from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = SCRIPT_DIR.parents[3]  # rgbw_lut_builder → tools → TemporalBFI → lib → project root

DEFAULT_INPUT_DIR = _PROJECT_ROOT / "tools" / "patch_captures"
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "tools" / "rgbw_capture_analysis" / "outputs"


@dataclass(frozen=True)
class ReferenceWhite:
    x: float
    y: float
    Y: float

    @property
    def xyz(self) -> tuple[float, float, float]:
        X = (self.x * self.Y) / self.y
        Z = ((1.0 - self.x - self.y) * self.Y) / self.y
        return X, self.Y, Z


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze measured RGBW patch captures in XYZ/Lab/LCh space.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--white-x", type=float, default=0.3309)
    parser.add_argument("--white-y", type=float, default=0.3590)
    parser.add_argument("--white-Y", type=float, default=100.0)
    parser.add_argument("--min-measured-y", type=float, default=0.0)
    parser.add_argument("--min-white-share-total", type=float, default=0.0)
    parser.add_argument("--top-family-count", type=int, default=8)
    parser.add_argument("--chroma-bins", type=int, default=18)
    parser.add_argument("--hue-bins", type=int, default=24)
    return parser.parse_args()


def safe_int(value: str | None) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def safe_float(value: str | None) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return float("nan")


def is_ok(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def xyz_to_lab(X: float, Y: float, Z: float, reference_white: ReferenceWhite) -> tuple[float, float, float]:
    Xn, Yn, Zn = reference_white.xyz

    def f_component(value: float) -> float:
        delta = 6.0 / 29.0
        if value > delta ** 3:
            return value ** (1.0 / 3.0)
        return value / (3.0 * delta * delta) + 4.0 / 29.0

    fx = f_component(X / Xn) if Xn > 0 else 0.0
    fy = f_component(Y / Yn) if Yn > 0 else 0.0
    fz = f_component(Z / Zn) if Zn > 0 else 0.0

    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    return L, a, b


def lab_to_lch(L: float, a: float, b: float) -> tuple[float, float, float]:
    C = math.hypot(a, b)
    h = math.degrees(math.atan2(b, a)) % 360.0
    return L, C, h


def family_name(name: str) -> str:
    return re.sub(r"_w[^_]+$", "", name)


def white_sweep_rank(name: str, w16: int) -> tuple[int, int]:
    match = re.search(r"_w(\d+)$", name)
    if match:
        return 0, int(match.group(1))
    return 1, w16


def load_rows(input_dir: Path, reference_white: ReferenceWhite, min_measured_y: float, min_white_share_total: float) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for csv_path in sorted(input_dir.glob("*.csv")):
        with csv_path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            for raw in csv.DictReader(handle):
                if not is_ok(raw.get("ok")):
                    continue

                X = safe_float(raw.get("X"))
                Y = safe_float(raw.get("Y"))
                Z = safe_float(raw.get("Z"))
                if not np.isfinite([X, Y, Z]).all():
                    continue
                if Y < min_measured_y:
                    continue

                r16 = safe_int(raw.get("r16"))
                g16 = safe_int(raw.get("g16"))
                b16 = safe_int(raw.get("b16"))
                w16 = safe_int(raw.get("w16"))
                rgb_sum = r16 + g16 + b16
                channel_sum = rgb_sum + w16
                min_rgb = min(r16, g16, b16)
                max_rgb = max(r16, g16, b16)

                white_share_total = (w16 / channel_sum) if channel_sum > 0 else 0.0
                if white_share_total < min_white_share_total:
                    continue

                white_share_rgb = (w16 / rgb_sum) if rgb_sum > 0 else math.inf
                white_over_min = (w16 / min_rgb) if min_rgb > 0 else math.inf
                white_over_max = (w16 / max_rgb) if max_rgb > 0 else math.inf
                L, a, b = xyz_to_lab(X, Y, Z, reference_white)
                L, C, h = lab_to_lch(L, a, b)
                x = safe_float(raw.get("x"))
                y = safe_float(raw.get("y"))

                rows.append(
                    {
                        "source_file": csv_path.name,
                        "name": raw.get("name", ""),
                        "family": family_name(raw.get("name", "")),
                        "solver_mode": raw.get("solver_mode", ""),
                        "r16": r16,
                        "g16": g16,
                        "b16": b16,
                        "w16": w16,
                        "rgb_sum": rgb_sum,
                        "channel_sum": channel_sum,
                        "min_rgb": min_rgb,
                        "max_rgb": max_rgb,
                        "white_share_total": white_share_total,
                        "white_share_rgb": white_share_rgb,
                        "white_over_min": white_over_min,
                        "white_over_max": white_over_max,
                        "X": X,
                        "Y": Y,
                        "Z": Z,
                        "x": x,
                        "y": y,
                        "L": L,
                        "a": a,
                        "b": b,
                        "C": C,
                        "h": h,
                    }
                )
    return rows


def write_metrics_csv(rows: list[dict[str, object]], output_path: Path) -> None:
    fieldnames = [
        "source_file",
        "name",
        "family",
        "solver_mode",
        "r16",
        "g16",
        "b16",
        "w16",
        "rgb_sum",
        "channel_sum",
        "min_rgb",
        "max_rgb",
        "white_share_total",
        "white_share_rgb",
        "white_over_min",
        "white_over_max",
        "X",
        "Y",
        "Z",
        "x",
        "y",
        "L",
        "a",
        "b",
        "C",
        "h",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize_rows(rows: list[dict[str, object]], reference_white: ReferenceWhite) -> dict[str, object]:
    white_active = [row for row in rows if row["w16"] > 0]
    white_and_rgb = [row for row in white_active if row["rgb_sum"] > 0]
    positive_min = [row for row in white_and_rgb if row["min_rgb"] > 0]
    white_above_min = [row for row in positive_min if row["white_over_min"] > 1.0]

    chroma_values = sorted(float(row["C"]) for row in white_and_rgb)
    white_share_values = sorted(float(row["white_share_total"]) for row in white_and_rgb)
    white_over_min_values = sorted(float(row["white_over_min"]) for row in positive_min)

    def quantiles(values: list[float]) -> dict[str, float]:
        if not values:
            return {}
        return {
            "p10": float(np.quantile(values, 0.10)),
            "p25": float(np.quantile(values, 0.25)),
            "p50": float(np.quantile(values, 0.50)),
            "p75": float(np.quantile(values, 0.75)),
            "p90": float(np.quantile(values, 0.90)),
            "p95": float(np.quantile(values, 0.95)),
            "p99": float(np.quantile(values, 0.99)),
        }

    return {
        "reference_white": {"x": reference_white.x, "y": reference_white.y, "Y": reference_white.Y},
        "row_counts": {
            "total": len(rows),
            "white_active": len(white_active),
            "white_and_rgb": len(white_and_rgb),
            "positive_min_rgb": len(positive_min),
            "white_above_min_rgb": len(white_above_min),
        },
        "fractions": {
            "white_above_min_rgb_fraction": float(len(white_above_min) / len(positive_min)) if positive_min else 0.0,
        },
        "white_share_total_quantiles": quantiles(white_share_values),
        "white_over_min_quantiles": quantiles(white_over_min_values),
        "measured_chroma_quantiles": quantiles(chroma_values),
    }


def plot_xy_scatter(rows: list[dict[str, object]], output_path: Path, reference_white: ReferenceWhite) -> None:
    x = np.array([row["x"] for row in rows], dtype=float)
    y = np.array([row["y"] for row in rows], dtype=float)
    white_share = np.array([row["white_share_total"] for row in rows], dtype=float)

    fig, ax = plt.subplots(figsize=(10, 8))
    scatter = ax.scatter(x, y, c=white_share, s=10, cmap="viridis", alpha=0.55, edgecolors="none")
    ax.scatter([reference_white.x], [reference_white.y], color="red", marker="x", s=80, label="Reference white")
    ax.set_title("Measured xy chromaticity colored by total white share")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.2)
    fig.colorbar(scatter, ax=ax, label="W / (R+G+B+W)")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_chroma_vs_white(rows: list[dict[str, object]], output_path: Path) -> None:
    chroma = np.array([row["C"] for row in rows], dtype=float)
    white_share = np.array([row["white_share_total"] for row in rows], dtype=float)
    hue = np.array([row["h"] for row in rows], dtype=float)

    fig, ax = plt.subplots(figsize=(10, 8))
    scatter = ax.scatter(chroma, white_share, c=hue, s=10, cmap="hsv", alpha=0.45, edgecolors="none")
    ax.set_title("Measured LCh chroma versus total white share")
    ax.set_xlabel("C*ab")
    ax.set_ylabel("W / (R+G+B+W)")
    ax.grid(True, alpha=0.2)
    fig.colorbar(scatter, ax=ax, label="Hue angle")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def build_envelope(rows: list[dict[str, object]], chroma_bins: int, hue_bins: int) -> list[dict[str, object]]:
    white_rows = [row for row in rows if row["w16"] > 0 and row["rgb_sum"] > 0]
    if not white_rows:
        return []

    max_chroma = max(float(row["C"]) for row in white_rows)
    chroma_edges = np.linspace(0.0, max(1.0, max_chroma), chroma_bins + 1)
    hue_edges = np.linspace(0.0, 360.0, hue_bins + 1)

    buckets: dict[tuple[int, int], list[float]] = defaultdict(list)
    for row in white_rows:
        chroma = float(row["C"])
        hue = float(row["h"])
        white_share = float(row["white_share_total"])
        chroma_index = min(chroma_bins - 1, int(np.searchsorted(chroma_edges, chroma, side="right") - 1))
        hue_index = min(hue_bins - 1, int(np.searchsorted(hue_edges, hue, side="right") - 1))
        chroma_index = max(0, chroma_index)
        hue_index = max(0, hue_index)
        buckets[(hue_index, chroma_index)].append(white_share)

    envelope_rows: list[dict[str, object]] = []
    for hue_index in range(hue_bins):
        for chroma_index in range(chroma_bins):
            values = buckets.get((hue_index, chroma_index), [])
            envelope_rows.append(
                {
                    "hue_bin": hue_index,
                    "hue_start": float(hue_edges[hue_index]),
                    "hue_end": float(hue_edges[hue_index + 1]),
                    "chroma_bin": chroma_index,
                    "chroma_start": float(chroma_edges[chroma_index]),
                    "chroma_end": float(chroma_edges[chroma_index + 1]),
                    "sample_count": len(values),
                    "white_share_p50": float(np.quantile(values, 0.50)) if values else 0.0,
                    "white_share_p90": float(np.quantile(values, 0.90)) if values else 0.0,
                    "white_share_p95": float(np.quantile(values, 0.95)) if values else 0.0,
                    "white_share_max": float(max(values)) if values else 0.0,
                }
            )
    return envelope_rows


def write_envelope_csv(envelope_rows: list[dict[str, object]], output_path: Path) -> None:
    if not envelope_rows:
        return
    fieldnames = list(envelope_rows[0].keys())
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(envelope_rows)


def plot_envelope_heatmap(envelope_rows: list[dict[str, object]], value_key: str, output_path: Path, title: str) -> None:
    if not envelope_rows:
        return
    hue_bins = max(int(row["hue_bin"]) for row in envelope_rows) + 1
    chroma_bins = max(int(row["chroma_bin"]) for row in envelope_rows) + 1
    matrix = np.zeros((hue_bins, chroma_bins), dtype=float)
    counts = np.zeros((hue_bins, chroma_bins), dtype=float)
    for row in envelope_rows:
        h = int(row["hue_bin"])
        c = int(row["chroma_bin"])
        matrix[h, c] = float(row[value_key])
        counts[h, c] = float(row["sample_count"])

    fig, ax = plt.subplots(figsize=(12, 8))
    image = ax.imshow(matrix, aspect="auto", origin="lower", cmap="magma")
    ax.set_title(title)
    ax.set_xlabel("Chroma bin")
    ax.set_ylabel("Hue bin")
    fig.colorbar(image, ax=ax, label=value_key)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(12, 8))
    count_image = ax.imshow(counts, aspect="auto", origin="lower", cmap="cividis")
    ax.set_title(title + " sample density")
    ax.set_xlabel("Chroma bin")
    ax.set_ylabel("Hue bin")
    fig.colorbar(count_image, ax=ax, label="sample_count")
    fig.tight_layout()
    fig.savefig(output_path.with_name(output_path.stem + "_counts.png"), dpi=160)
    plt.close(fig)


def plot_family_sweeps(rows: list[dict[str, object]], output_dir: Path, top_family_count: int) -> None:
    families: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        if row["w16"] > 0 and row["rgb_sum"] > 0:
            families[str(row["family"])].append(row)

    ranked = sorted(
        (
            (
                family,
                items,
                len({int(item["w16"]) for item in items}),
            )
            for family, items in families.items()
        ),
        key=lambda item: (-item[2], -len(item[1]), item[0]),
    )

    for family, items, distinct_w in ranked[:top_family_count]:
        if distinct_w < 4:
            continue

        ordered = sorted(items, key=lambda item: white_sweep_rank(str(item["name"]), int(item["w16"])))
        white_share = np.array([item["white_share_total"] for item in ordered], dtype=float)
        chroma = np.array([item["C"] for item in ordered], dtype=float)
        hue = np.array([item["h"] for item in ordered], dtype=float)
        x = np.array([item["x"] for item in ordered], dtype=float)
        y = np.array([item["y"] for item in ordered], dtype=float)

        fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
        axes[0].plot(white_share, chroma, marker="o")
        axes[0].set_title("Chroma vs white share")
        axes[0].set_xlabel("W / total")
        axes[0].set_ylabel("C*ab")
        axes[0].grid(True, alpha=0.2)

        axes[1].plot(white_share, hue, marker="o")
        axes[1].set_title("Hue vs white share")
        axes[1].set_xlabel("W / total")
        axes[1].set_ylabel("Hue angle")
        axes[1].grid(True, alpha=0.2)

        axes[2].plot(x, y, marker="o")
        axes[2].set_title("xy path across W sweep")
        axes[2].set_xlabel("x")
        axes[2].set_ylabel("y")
        axes[2].grid(True, alpha=0.2)

        fig.suptitle(f"Family sweep: {family}")
        fig.tight_layout()
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", family)[:100]
        fig.savefig(output_dir / f"family_{safe_name}.png", dpi=160)
        plt.close(fig)


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    reference_white = ReferenceWhite(args.white_x, args.white_y, args.white_Y)
    rows = load_rows(args.input_dir, reference_white, args.min_measured_y, args.min_white_share_total)
    if not rows:
        raise SystemExit("No valid rows were loaded. Check the input directory and filters.")

    write_metrics_csv(rows, output_dir / "capture_metrics.csv")

    summary = summarize_rows(rows, reference_white)
    with (output_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    envelope_rows = build_envelope(rows, args.chroma_bins, args.hue_bins)
    write_envelope_csv(envelope_rows, output_dir / "lch_white_envelope.csv")

    plot_xy_scatter(rows, output_dir / "xy_white_share.png", reference_white)
    plot_chroma_vs_white(rows, output_dir / "lch_chroma_vs_white_share.png")
    plot_envelope_heatmap(envelope_rows, "white_share_p95", output_dir / "lch_white_share_p95_heatmap.png", "Empirical p95 white share by hue/chroma bin")
    plot_envelope_heatmap(envelope_rows, "white_share_max", output_dir / "lch_white_share_max_heatmap.png", "Empirical max white share by hue/chroma bin")
    plot_family_sweeps(rows, output_dir, args.top_family_count)

    print(f"Loaded {len(rows)} valid rows from {args.input_dir}")
    print(f"Wrote metrics to {output_dir / 'capture_metrics.csv'}")
    print(f"Wrote summary to {output_dir / 'summary.json'}")
    print(f"Wrote envelope to {output_dir / 'lch_white_envelope.csv'}")
    print(f"Plots written under {output_dir}")


if __name__ == "__main__":
    main()