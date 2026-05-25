from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from numpy.lib.format import open_memmap

try:
    from ..paths import DEFAULT_LUT_OUTPUT_DIR
    from .prototype_measured_white_solver import (
        DEFAULT_INPUT_DIR,
        MeasuredPriorDataset,
        ReferenceWhite,
        build_target_rgb_basis,
        fit_basis_from_pure_sweeps,
        load_measured_prior_dataset,
        solve_measured_white,
        xyz_to_lab,
        lab_to_lch,
    )
except ImportError:
    from prototype_measured_white_solver import (
        DEFAULT_INPUT_DIR,
        MeasuredPriorDataset,
        ReferenceWhite,
        build_target_rgb_basis,
        fit_basis_from_pure_sweeps,
        load_measured_prior_dataset,
        solve_measured_white,
        xyz_to_lab,
        lab_to_lch,
    )

    DEFAULT_LUT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "lut_outputs"

DEFAULT_OUTPUT_DIR = DEFAULT_LUT_OUTPUT_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build coarse and dense RGBW LUTs from measured capture data.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--white-x", type=float, default=0.3309)
    parser.add_argument("--white-y", type=float, default=0.3590)
    parser.add_argument("--white-Y", type=float, default=100.0)
    parser.add_argument("--max-delta-e", type=float, default=4.0)
    parser.add_argument("--max-hue-shift", type=float, default=4.0)
    parser.add_argument("--ignore-hue-below-chroma", type=float, default=8.0)
    parser.add_argument("--target-white-balance-mode", choices=["raw", "reference-white"], default="reference-white")
    parser.add_argument("--neutral-classic-chroma", type=float, default=8.0)
    parser.add_argument("--neutral-classic-fade-width", type=float, default=10.0)
    parser.add_argument("--measured-prior-mode", choices=["row", "family"], default="family")
    parser.add_argument("--measured-prior-neighbors", type=int, default=0)
    parser.add_argument("--measured-family-count", type=int, default=0)
    parser.add_argument("--measured-prior-strength", type=float, default=0.35)
    parser.add_argument("--nondegenerate-regularization", type=float, default=0.01)
    parser.add_argument("--max-luminance-ratio", type=float, default=2.0,
        help="Max allowed output Y / target Y in the solver; prevents white blowup for dim near-neutrals (default: 2.0)")
    parser.add_argument("--sample-scale", type=float, default=65535.0)
    parser.add_argument("--coarse-grid-size", type=int, default=17)
    parser.add_argument("--full-grid-size", type=int, default=256)
    parser.add_argument("--skip-full-lut", action="store_true")
    parser.add_argument("--skip-header", action="store_true")
    parser.add_argument("--emit-classic-header", action="store_true")
    parser.add_argument("--header-name", type=str, default="measured_rgbw_lut")
    parser.add_argument("--header-grid-size", type=int, default=0)
    parser.add_argument(
        "--workers", "-j", type=int, default=0,
        help="Parallel worker processes for LUT build (0 = all logical CPUs, default: 0)",
    )
    return parser.parse_args()


def axis_values(grid_size: int, sample_scale: float) -> np.ndarray:
    return np.linspace(0.0, sample_scale, grid_size, dtype=np.float64)


def build_classic_cube(axis: np.ndarray) -> np.ndarray:
    # Fully vectorised — no Python loops needed.
    R, G, B = np.meshgrid(axis, axis, axis, indexing="ij")
    W = np.minimum(np.minimum(R, G), B)
    return np.stack([R - W, G - W, B - W, W], axis=-1).astype(np.float32)


def build_classic_sample(rgb_target: np.ndarray) -> np.ndarray:
    white = float(min(rgb_target))
    return np.array(
        [
            float(rgb_target[0] - white),
            float(rgb_target[1] - white),
            float(rgb_target[2] - white),
            white,
        ],
        dtype=np.float64,
    )


def neutral_classic_blend_factor(target_chroma: float, args: argparse.Namespace) -> float:
    threshold = max(0.0, float(args.neutral_classic_chroma))
    fade_width = max(0.0, float(args.neutral_classic_fade_width))
    if threshold <= 0.0:
        return 0.0
    if target_chroma <= threshold:
        return 1.0
    if fade_width <= 0.0:
        return 0.0
    if target_chroma >= threshold + fade_width:
        return 0.0
    return float(1.0 - ((target_chroma - threshold) / fade_width))


def evaluate_rgbw_sample(
    rgbw: np.ndarray,
    target_lab: np.ndarray,
    target_hue: float,
    target_chroma: float,
    rgb_basis: np.ndarray,
    white_basis: np.ndarray,
    reference_white: ReferenceWhite,
    args: argparse.Namespace,
) -> tuple[float, float]:
    candidate_xyz = rgb_basis @ rgbw[:3] + white_basis * rgbw[3]
    candidate_lab = xyz_to_lab(candidate_xyz, reference_white)
    _, candidate_chroma, candidate_hue = lab_to_lch(candidate_lab)
    candidate_delta_e = float(np.linalg.norm(target_lab - candidate_lab))
    if target_chroma < args.ignore_hue_below_chroma or candidate_chroma < args.ignore_hue_below_chroma:
        candidate_hue_shift = 0.0
    else:
        candidate_hue_shift = float(abs(((target_hue - candidate_hue + 180.0) % 360.0) - 180.0))
    return candidate_delta_e, candidate_hue_shift


def xyz_to_xy(xyz: np.ndarray) -> tuple[float, float]:
    denom = float(np.sum(xyz))
    if abs(denom) < 1e-12:
        return float("nan"), float("nan")
    return float(xyz[0] / denom), float(xyz[1] / denom)


# ---------------------------------------------------------------------------
# Multiprocessing worker
# ---------------------------------------------------------------------------

# Module-level state populated once per worker process by _worker_init.
# Avoids re-pickling large arrays (MeasuredPriorDataset) on every job.
_worker_state: dict = {}


def _worker_init(state: dict) -> None:
    global _worker_state
    _worker_state = state


def _solve_r_slice(job: tuple) -> tuple:
    """Process all (g, b) cells for one r-slice.  Runs in a worker process."""
    r_index, r_value = job
    s = _worker_state
    axis: np.ndarray          = s["axis"]
    rgb_basis: np.ndarray     = s["rgb_basis"]
    trb: np.ndarray           = s["target_rgb_basis"]
    white_basis: np.ndarray   = s["white_basis"]
    reference_white           = s["reference_white"]
    measured_prior            = s["measured_prior"]
    args                      = s["args"]
    w_dom_y: float            = s.get("w_dominant_y_per_unit", 0.0)

    grid_size = axis.size
    slice_cube = np.zeros((grid_size, grid_size, 4), dtype=np.float32)
    slice_rows: list[dict] = []

    for g_index, g_value in enumerate(axis):
        for b_index, b_value in enumerate(axis):
            rgb_target = np.array([r_value, g_value, b_value], dtype=float)
            classic_rgbw = build_classic_sample(rgb_target)
            classic_white = float(classic_rgbw[3])

            proposed = solve_measured_white(
                rgb_target, rgb_basis, white_basis, reference_white,
                args.max_delta_e, args.max_hue_shift, args.ignore_hue_below_chroma,
                args.sample_scale,
                target_rgb_basis=trb,
                measured_prior=measured_prior,
                measured_prior_neighbors=args.measured_prior_neighbors,
                measured_prior_mode=args.measured_prior_mode,
                measured_family_count=args.measured_family_count,
                measured_prior_strength=args.measured_prior_strength,
                nondegenerate_regularization=args.nondegenerate_regularization,
                max_luminance_ratio=args.max_luminance_ratio,
                w_dominant_y_per_unit=w_dom_y,
            )

            target_xyz = trb @ rgb_target
            target_lab = xyz_to_lab(target_xyz, reference_white)
            target_L, target_C, target_h = lab_to_lch(target_lab)

            final_rgbw = np.array(
                [float(proposed["rgb"][0]), float(proposed["rgb"][1]),
                 float(proposed["rgb"][2]), float(proposed["w"])],
                dtype=np.float64,
            )
            neutral_blend = neutral_classic_blend_factor(target_C, args)
            final_delta_e, final_hue_shift = evaluate_rgbw_sample(
                final_rgbw, target_lab, target_h, target_C,
                rgb_basis, white_basis, reference_white, args,
            )

            slice_cube[g_index, b_index] = np.asarray(final_rgbw, dtype=np.float32)
            slice_rows.append({
                "r_index": r_index,
                "g_index": g_index,
                "b_index": b_index,
                "target_r": float(r_value),
                "target_g": float(g_value),
                "target_b": float(b_value),
                "target_L": float(target_L),
                "target_C": float(target_C),
                "target_h": float(target_h),
                "classic_w": classic_white,
                "raw_proposed_w": float(proposed["w"]),
                "proposed_w": float(final_rgbw[3]),
                "neutral_classic_blend": neutral_blend,
                "raw_white_gain_abs": float(proposed["w"] - classic_white),
                "white_gain_abs": float(final_rgbw[3] - classic_white),
                "raw_proposed_delta_e": float(proposed["delta_e"]),
                "raw_proposed_hue_shift": float(proposed["hue_shift"]),
                "prior_mode": str(proposed.get("prior_mode", args.measured_prior_mode)),
                "prior_white_share": float(proposed.get("prior_white_share", 0.0)),
                "proposed_delta_e": final_delta_e,
                "proposed_hue_shift": final_hue_shift,
            })

    return r_index, slice_cube, slice_rows


def build_measured_cube(
    axis: np.ndarray,
    rgb_basis: np.ndarray,
    target_rgb_basis: np.ndarray,
    white_basis: np.ndarray,
    reference_white: ReferenceWhite,
    measured_prior: MeasuredPriorDataset | None,
    args: argparse.Namespace,
    progress_callback=None,  # optional: callable(completed: int, total: int)
    w_dominant_y_per_unit: float = 0.0,
) -> tuple[np.ndarray, list[dict[str, float]]]:
    grid_size = axis.size
    n_workers = max(1, getattr(args, "workers", 0) or (os.cpu_count() or 1))
    n_workers = min(n_workers, grid_size)  # no point spawning more than r-slices

    # Share large read-only arrays via the initializer so they are copied once
    # per worker process rather than once per job.
    state = {
        "axis": axis,
        "rgb_basis": rgb_basis,
        "target_rgb_basis": target_rgb_basis,
        "white_basis": white_basis,
        "reference_white": reference_white,
        "measured_prior": measured_prior,
        "args": args,
        "w_dominant_y_per_unit": w_dominant_y_per_unit,
    }
    jobs = [(r_index, float(r_value)) for r_index, r_value in enumerate(axis)]

    cube = np.zeros((grid_size, grid_size, grid_size, 4), dtype=np.float32)
    # Pre-allocate per-r-slice row lists in order so we can write futures out-of-order.
    comparison_slices: list[list[dict]] = [[] for _ in range(grid_size)]

    print(f"  Building {grid_size}\u00b3 cube with {n_workers} workers …", flush=True)
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=n_workers,
        initializer=_worker_init,
        initargs=(state,),
    ) as executor:
        futures = {executor.submit(_solve_r_slice, job): job[0] for job in jobs}
        completed = 0
        try:
            for future in concurrent.futures.as_completed(futures):
                r_index, slice_cube, slice_rows = future.result()
                cube[r_index] = slice_cube
                comparison_slices[r_index] = slice_rows
                completed += 1
                pct = 100.0 * completed / grid_size
                print(f"\r  [{completed:>{len(str(grid_size))}}/{grid_size}]  {pct:5.1f}%", end="", flush=True)
                if progress_callback is not None:
                    progress_callback(completed, grid_size)
        except Exception:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
    print(flush=True)

    comparison_rows = [row for slice_rows in comparison_slices for row in slice_rows]
    return cube, comparison_rows

def write_comparison_csv(rows: list[dict[str, float]], output_path: Path) -> None:
    if not rows:
        return
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def format_header_u16_entries(values: np.ndarray, values_per_line: int = 12) -> str:
    chunks: list[str] = []
    for start in range(0, values.size, values_per_line):
        end = min(start + values_per_line, values.size)
        line = ", ".join(str(int(value)) for value in values[start:end])
        chunks.append(f"    {line}")
    return ",\n".join(chunks)


def trilinear_expand_cube(coarse_cube: np.ndarray, full_grid_size: int) -> np.ndarray:
    coarse_size = coarse_cube.shape[0]
    if full_grid_size == coarse_size:
        return np.clip(np.round(coarse_cube), 0, 65535).astype(np.uint16)

    coords = np.linspace(0.0, coarse_size - 1.0, full_grid_size, dtype=np.float64)
    lower = np.floor(coords).astype(np.int32)
    upper = np.clip(lower + 1, 0, coarse_size - 1)
    frac = coords - lower

    expanded = np.empty((full_grid_size, full_grid_size, full_grid_size, 4), dtype=np.uint16)

    g0 = lower[:, None]
    g1 = upper[:, None]
    tg = frac[:, None, None]
    b0 = lower[None, :]
    b1 = upper[None, :]
    tb = frac[None, :, None]

    for r_index in range(full_grid_size):
        r0 = lower[r_index]
        r1 = upper[r_index]
        tr = frac[r_index]

        c000 = coarse_cube[r0, g0, b0]
        c001 = coarse_cube[r0, g0, b1]
        c010 = coarse_cube[r0, g1, b0]
        c011 = coarse_cube[r0, g1, b1]
        c100 = coarse_cube[r1, g0, b0]
        c101 = coarse_cube[r1, g0, b1]
        c110 = coarse_cube[r1, g1, b0]
        c111 = coarse_cube[r1, g1, b1]

        c00 = c000 * (1.0 - tb) + c001 * tb
        c01 = c010 * (1.0 - tb) + c011 * tb
        c10 = c100 * (1.0 - tb) + c101 * tb
        c11 = c110 * (1.0 - tb) + c111 * tb
        c0 = c00 * (1.0 - tg) + c01 * tg
        c1 = c10 * (1.0 - tg) + c11 * tg
        slab = c0 * (1.0 - tr) + c1 * tr

        expanded[r_index] = np.clip(np.round(slab), 0, 65535).astype(np.uint16)

    return expanded


def write_rgbw_header(
    cube: np.ndarray,
    output_path: Path,
    lut_name: str,
    args: argparse.Namespace,
    source_grid_size: int,
) -> None:
    quantized = np.clip(np.round(cube), 0, 65535).astype(np.uint16)
    flattened = quantized.reshape(-1, 4)
    entry_count = int(flattened.shape[0])
    guard = f"HYPERHDR_{output_path.stem.upper()}_H".replace("-", "_")

    lines = [
        "// Auto-generated by build_measured_rgbw_lut.py",
        "// Flattened RGB-major sampled RGBW LUT.",
        "// Entry index formula: ((r_index * RGBW_LUT_GRID_SIZE) + g_index) * RGBW_LUT_GRID_SIZE + b_index",
        f"// LUT name: {lut_name}",
        f"// Source solved grid size: {source_grid_size}",
        "",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <stdint.h>",
        "",
        "#ifdef __AVR__",
        "  #include <avr/pgmspace.h>",
        "#elif defined(ESP32) || defined(ESP8266)",
        "  #include <pgmspace.h>",
        "#elif !defined(PROGMEM)",
        "  #define PROGMEM",
        "#endif",
        "",
        f"static const uint32_t RGBW_LUT_FORMAT_VERSION = 1;",
        f"static const uint32_t RGBW_LUT_SOURCE_GRID_SIZE = {source_grid_size};",
        f"static const uint32_t RGBW_LUT_GRID_SIZE = {quantized.shape[0]};",
        f"static const uint32_t RGBW_LUT_ENTRY_COUNT = {entry_count};",
        f"static const uint8_t RGBW_LUT_IS_SAMPLED_3D_GRID = 1;",
        f"static const uint8_t RGBW_LUT_REQUIRES_3D_INTERPOLATION = {1 if quantized.shape[0] < 257 else 0};",
        f"static const uint32_t RGBW_LUT_AXIS_MIN = 0;",
        f"static const uint32_t RGBW_LUT_AXIS_MAX = {int(round(args.sample_scale))};",
        f"static const float RGBW_LUT_MAX_DELTA_E = {args.max_delta_e:.6f}f;",
        f"static const float RGBW_LUT_MAX_HUE_SHIFT = {args.max_hue_shift:.6f}f;",
        f"static const float RGBW_LUT_IGNORE_HUE_BELOW_CHROMA = {args.ignore_hue_below_chroma:.6f}f;",
        "",
        f"static const uint16_t RGBW_LUT_R[{entry_count}] PROGMEM = {{",
        format_header_u16_entries(flattened[:, 0]),
        "};",
        "",
        f"static const uint16_t RGBW_LUT_G[{entry_count}] PROGMEM = {{",
        format_header_u16_entries(flattened[:, 1]),
        "};",
        "",
        f"static const uint16_t RGBW_LUT_B[{entry_count}] PROGMEM = {{",
        format_header_u16_entries(flattened[:, 2]),
        "};",
        "",
        f"static const uint16_t RGBW_LUT_W[{entry_count}] PROGMEM = {{",
        format_header_u16_entries(flattened[:, 3]),
        "};",
        "",
        f"#endif  // {guard}",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def summarize(rows: list[dict[str, float]], args: argparse.Namespace, basis: dict[str, np.ndarray]) -> dict[str, object]:
    gains = np.array([row["white_gain_abs"] for row in rows], dtype=float)
    more = gains > 1.0
    less = gains < -1.0
    neutral_blend = np.array([row["neutral_classic_blend"] for row in rows], dtype=float)
    rgb_sum = basis["r16"] + basis["g16"] + basis["b16"]
    equal_rgb_xy = xyz_to_xy(rgb_sum)
    white_channel_xy = xyz_to_xy(basis["w16"])
    return {
        "settings": {
            "max_delta_e": args.max_delta_e,
            "max_hue_shift": args.max_hue_shift,
            "ignore_hue_below_chroma": args.ignore_hue_below_chroma,
            "target_white_balance_mode": args.target_white_balance_mode,
            "neutral_classic_chroma": args.neutral_classic_chroma,
            "neutral_classic_fade_width": args.neutral_classic_fade_width,
            "measured_prior_mode": args.measured_prior_mode,
            "measured_prior_neighbors": args.measured_prior_neighbors,
            "measured_family_count": args.measured_family_count,
            "measured_prior_strength": args.measured_prior_strength,
            "nondegenerate_regularization": args.nondegenerate_regularization,
            "max_luminance_ratio": args.max_luminance_ratio,
            "sample_scale": args.sample_scale,
            "coarse_grid_size": args.coarse_grid_size,
            "full_grid_size": args.full_grid_size,
        },
        "basis_xyz_per_q16": {key: value.tolist() for key, value in basis.items()},
        "basis_sanity": {
            "equal_rgb_neutral_xyz_per_q16": rgb_sum.tolist(),
            "equal_rgb_neutral_xy": list(equal_rgb_xy),
            "pure_white_channel_xy": list(white_channel_xy),
            "reference_white_xy": [args.white_x, args.white_y],
            "equal_rgb_vs_white_dx": float(equal_rgb_xy[0] - white_channel_xy[0]),
            "equal_rgb_vs_white_dy": float(equal_rgb_xy[1] - white_channel_xy[1]),
            "white_channel_vs_reference_dx": float(white_channel_xy[0] - args.white_x),
            "white_channel_vs_reference_dy": float(white_channel_xy[1] - args.white_y),
        },
        "counts": {
            "coarse_samples": len(rows),
            "proposed_more_white_than_classic": int(more.sum()),
            "proposed_less_white_than_classic": int(less.sum()),
            "neutral_classic_bias_samples": int(np.sum(neutral_blend > 0.0)),
        },
        "white_gain_quantiles": {
            "p01": float(np.quantile(gains, 0.01)),
            "p10": float(np.quantile(gains, 0.10)),
            "p50": float(np.quantile(gains, 0.50)),
            "p90": float(np.quantile(gains, 0.90)),
            "p99": float(np.quantile(gains, 0.99)),
        },
        "top_increases": sorted(rows, key=lambda row: row["white_gain_abs"], reverse=True)[:20],
        "top_decreases": sorted(rows, key=lambda row: row["white_gain_abs"])[:20],
    }


def trilinear_expand_uint16(coarse_cube: np.ndarray, full_grid_size: int, output_path: Path) -> Path:
    expanded = trilinear_expand_cube(coarse_cube, full_grid_size)
    memmap = open_memmap(output_path, mode="w+", dtype=np.uint16, shape=expanded.shape)
    memmap[:] = expanded
    del memmap
    return output_path


def plot_white_slices(classic_cube: np.ndarray, measured_cube: np.ndarray, axis: np.ndarray, output_path: Path) -> None:
    slice_indices = [0, axis.size // 4, axis.size // 2, (3 * axis.size) // 4, axis.size - 1]
    fig, axes = plt.subplots(2, len(slice_indices), figsize=(3.5 * len(slice_indices), 7), constrained_layout=True)

    for column, b_index in enumerate(slice_indices):
        classic = classic_cube[:, :, b_index, 3]
        measured = measured_cube[:, :, b_index, 3]
        image0 = axes[0, column].imshow(classic, origin="lower", cmap="magma")
        image1 = axes[1, column].imshow(measured, origin="lower", cmap="magma")
        axes[0, column].set_title(f"Classic W, B={axis[b_index]:.0f}")
        axes[1, column].set_title(f"Measured W, B={axis[b_index]:.0f}")
        axes[0, column].set_xlabel("G index")
        axes[1, column].set_xlabel("G index")
        axes[0, column].set_ylabel("R index")
        axes[1, column].set_ylabel("R index")

    fig.colorbar(image0, ax=axes[0, :], shrink=0.8, label="Classic white q16")
    fig.colorbar(image1, ax=axes[1, :], shrink=0.8, label="Measured white q16")
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_white_gain_histogram(rows: list[dict[str, float]], output_path: Path) -> None:
    gains = np.array([row["white_gain_abs"] for row in rows], dtype=float)
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(gains, bins=80, color="#4477aa", alpha=0.85)
    ax.axvline(0.0, color="black", linewidth=1.0, alpha=0.5)
    ax.set_title("Measured LUT white gain over classic min(rgb) on the coarse cube")
    ax.set_xlabel("Proposed W - classic W")
    ax.set_ylabel("Count")
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    reference_white = ReferenceWhite(args.white_x, args.white_y, args.white_Y)
    basis = fit_basis_from_pure_sweeps(args.input_dir)
    measured_prior = load_measured_prior_dataset(args.input_dir, reference_white)
    rgb_basis = np.column_stack([basis["r16"], basis["g16"], basis["b16"]])
    white_basis = basis["w16"]
    target_rgb_basis, target_rgb_basis_info = build_target_rgb_basis(rgb_basis, reference_white, args.target_white_balance_mode, white_basis=white_basis)
    equal_rgb_xy = xyz_to_xy(basis["r16"] + basis["g16"] + basis["b16"])
    white_channel_xy = xyz_to_xy(white_basis)

    coarse_axis = axis_values(args.coarse_grid_size, args.sample_scale)
    classic_cube = build_classic_cube(coarse_axis)
    w_dom_y = float(target_rgb_basis_info.get("w_dominant_y_per_unit", 0.0))
    measured_cube, comparison_rows = build_measured_cube(coarse_axis, rgb_basis, target_rgb_basis, white_basis, reference_white, measured_prior, args, w_dominant_y_per_unit=w_dom_y)

    np.save(output_dir / f"classic_rgbw_coarse_{args.coarse_grid_size}.npy", np.clip(np.round(classic_cube), 0, 65535).astype(np.uint16))
    np.save(output_dir / f"measured_rgbw_coarse_{args.coarse_grid_size}.npy", np.clip(np.round(measured_cube), 0, 65535).astype(np.uint16))
    write_comparison_csv(comparison_rows, output_dir / "coarse_lut_comparison.csv")

    summary = summarize(comparison_rows, args, basis)
    with (output_dir / "lut_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    plot_white_slices(classic_cube, measured_cube, coarse_axis, output_dir / "coarse_white_slices.png")
    plot_white_gain_histogram(comparison_rows, output_dir / "coarse_white_gain_histogram.png")

    if not args.skip_header:
        header_grid_size = args.header_grid_size if args.header_grid_size > 0 else args.coarse_grid_size
        measured_header_cube = trilinear_expand_cube(measured_cube, header_grid_size)
        measured_header_path = output_dir / f"{args.header_name}_grid_{header_grid_size}_from_{args.coarse_grid_size}.h"
        write_rgbw_header(measured_header_cube, measured_header_path, args.header_name, args, args.coarse_grid_size)

        if args.emit_classic_header:
            classic_header_cube = trilinear_expand_cube(classic_cube, header_grid_size)
            classic_header_path = output_dir / f"classic_rgbw_lut_grid_{header_grid_size}_from_{args.coarse_grid_size}.h"
            write_rgbw_header(classic_header_cube, classic_header_path, "classic_rgbw_lut", args, args.coarse_grid_size)

    if not args.skip_full_lut:
        trilinear_expand_uint16(measured_cube, args.full_grid_size, output_dir / f"measured_rgbw_full_{args.full_grid_size}.npy")
        trilinear_expand_uint16(classic_cube, args.full_grid_size, output_dir / f"classic_rgbw_full_{args.full_grid_size}.npy")

    print(f"Fitted basis from {args.input_dir}")
    print(f"Equal-RGB neutral xy: ({equal_rgb_xy[0]:.4f}, {equal_rgb_xy[1]:.4f})")
    print(f"White-channel xy    : ({white_channel_xy[0]:.4f}, {white_channel_xy[1]:.4f})")
    print(f"Reference white xy  : ({args.white_x:.4f}, {args.white_y:.4f})")
    print(f"Target basis mode   : {args.target_white_balance_mode}")
    print(f"Target equal-RGB xy : ({target_rgb_basis_info['equal_rgb_xy'][0]:.4f}, {target_rgb_basis_info['equal_rgb_xy'][1]:.4f})")
    print(f"Measured prior rows : {measured_prior.lab.shape[0]}")
    print(f"Measured families   : {len(measured_prior.family_names)}")
    print(f"Measured prior      : mode={args.measured_prior_mode}, neighbors={args.measured_prior_neighbors}, family_count={args.measured_family_count}, strength={args.measured_prior_strength:.3f}, regularization={args.nondegenerate_regularization:.4f}")
    print(f"Built coarse LUTs at {args.coarse_grid_size}^3")
    if not args.skip_header:
        header_grid_size = args.header_grid_size if args.header_grid_size > 0 else args.coarse_grid_size
        print(f"Wrote sampled LUT header(s) at {header_grid_size}^3 under {output_dir}")
    if not args.skip_full_lut:
        print(f"Expanded dense LUTs to {args.full_grid_size}^3")
    print(f"Outputs written under {output_dir}")


if __name__ == "__main__":
    main()