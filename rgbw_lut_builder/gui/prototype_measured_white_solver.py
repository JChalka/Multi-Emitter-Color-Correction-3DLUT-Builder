from __future__ import annotations

import argparse
import colorsys
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import linprog

try:
    from ..paths import DEFAULT_CAPTURE_INPUT_DIR, DEFAULT_SOLVER_OUTPUT_DIR
except ImportError:
    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    DEFAULT_CAPTURE_INPUT_DIR = _PROJECT_ROOT / "patch_captures"
    DEFAULT_SOLVER_OUTPUT_DIR = _PROJECT_ROOT / "solver_outputs"

DEFAULT_INPUT_DIR = DEFAULT_CAPTURE_INPUT_DIR
DEFAULT_OUTPUT_DIR = DEFAULT_SOLVER_OUTPUT_DIR


@dataclass(frozen=True)
class ReferenceWhite:
    x: float
    y: float
    Y: float

    @property
    def xyz(self) -> np.ndarray:
        X = (self.x * self.Y) / self.y
        Z = ((1.0 - self.x - self.y) * self.Y) / self.y
        return np.array([X, self.Y, Z], dtype=float)


@dataclass(frozen=True)
class MeasuredPriorDataset:
    lab: np.ndarray
    white_share_total: np.ndarray
    rgb_ratio: np.ndarray
    family_index: np.ndarray
    family_names: tuple[str, ...]


@dataclass(frozen=True)
class PriorFamilyContribution:
    family_name: str
    family_weight: float
    min_distance: float
    white_share: float
    rgb_ratio: np.ndarray
    row_count: int


@dataclass(frozen=True)
class MeasuredPriorQueryResult:
    white_share: float
    rgb_ratio: np.ndarray
    family_contributions: tuple[PriorFamilyContribution, ...]
    neighbor_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype measured-basis RGBW extraction and compare against classic min(rgb).")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--white-x", type=float, default=0.3309)
    parser.add_argument("--white-y", type=float, default=0.3590)
    parser.add_argument("--white-Y", type=float, default=100.0)
    parser.add_argument("--max-delta-e", type=float, default=4.0)
    parser.add_argument("--max-hue-shift", type=float, default=4.0)
    parser.add_argument("--ignore-hue-below-chroma", type=float, default=8.0)
    parser.add_argument("--target-white-balance-mode", choices=["raw", "reference-white"], default="reference-white")
    parser.add_argument("--grid-size", type=int, default=17)
    parser.add_argument("--sample-scale", type=float, default=65535.0)
    parser.add_argument("--measured-prior-mode", choices=["row", "family"], default="family")
    parser.add_argument("--measured-family-count", type=int, default=0)
    parser.add_argument("--measured-prior-neighbors", type=int, default=0)
    parser.add_argument("--include-value-zero", action="store_true")
    parser.add_argument("--top-count", type=int, default=30)
    return parser.parse_args()


def measured_family_name(name: str | None) -> str:
    text = str(name or "").strip()
    if not text:
        return "unnamed"

    def is_parameter_token(token: str) -> bool:
        lower = token.lower()
        if lower in {"rgb", "rgbw", "q16"}:
            return False
        if re.fullmatch(r"(?:rgbw|rgb|[rgbw]{1,2})\d+(?:\.\d+)?(?:[rgbw]{1,2}\d+(?:\.\d+)?)*", lower):
            return True
        if re.fullmatch(r"(?:rgbw|rgb|q|w|sat|s)\d+(?:\.\d+)?", lower):
            return True
        if re.fullmatch(r"\d+(?:\.\d+)?", lower):
            return True
        return False

    tokens = [token for token in text.split("_") if token]
    semantic_tokens: list[str] = []
    for token in tokens:
        if is_parameter_token(token):
            break
        semantic_tokens.append(token)

    if semantic_tokens:
        return "_".join(semantic_tokens)
    return text


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


def xyz_to_lab(xyz: np.ndarray, reference_white: ReferenceWhite) -> np.ndarray:
    white_xyz = reference_white.xyz

    def f_component(value: float) -> float:
        delta = 6.0 / 29.0
        if value > delta ** 3:
            return value ** (1.0 / 3.0)
        return value / (3.0 * delta * delta) + 4.0 / 29.0

    fx = f_component(xyz[0] / white_xyz[0]) if white_xyz[0] > 0 else 0.0
    fy = f_component(xyz[1] / white_xyz[1]) if white_xyz[1] > 0 else 0.0
    fz = f_component(xyz[2] / white_xyz[2]) if white_xyz[2] > 0 else 0.0
    return np.array([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], dtype=float)


def lab_to_lch(lab: np.ndarray) -> tuple[float, float, float]:
    L, a, b = lab
    chroma = float(math.hypot(a, b))
    hue = float(math.degrees(math.atan2(b, a)) % 360.0)
    return float(L), chroma, hue


def circular_hue_distance_degrees(h1: float, h2: float) -> float:
    diff = abs(h1 - h2) % 360.0
    return min(diff, 360.0 - diff)


def fit_basis_from_pure_sweeps(input_dir: Path) -> dict[str, np.ndarray]:
    pure_rows: dict[str, list[tuple[float, np.ndarray]]] = {"r16": [], "g16": [], "b16": [], "w16": []}
    channels = ("r16", "g16", "b16", "w16")

    for csv_path in sorted(input_dir.glob("*.csv")):
        with csv_path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                if not is_ok(row.get("ok")):
                    continue

                values = [safe_int(row.get(channel)) for channel in channels]
                if sum(value > 0 for value in values) != 1:
                    continue

                for index, channel in enumerate(channels):
                    drive = values[index]
                    if drive <= 0:
                        continue
                    xyz = np.array([safe_float(row.get("X")), safe_float(row.get("Y")), safe_float(row.get("Z"))], dtype=float)
                    if np.isfinite(xyz).all():
                        pure_rows[channel].append((float(drive), xyz))

    basis: dict[str, np.ndarray] = {}
    for channel, items in pure_rows.items():
        if not items:
            raise RuntimeError(f"No pure-channel capture rows were found for {channel}")
        drives = np.array([item[0] for item in items], dtype=float)
        xyz = np.array([item[1] for item in items], dtype=float)
        slope = (drives[:, None] * xyz).sum(axis=0) / (drives[:, None] * drives[:, None]).sum(axis=0)
        basis[channel] = slope
    return basis


def fit_basis_from_all_families(
    input_dir: Path,
) -> dict[str, np.ndarray]:
    """Fit XYZ-per-unit-drive slopes for every RGBW primary family.

    For each of the 7 non-black primary subsets (R, G, B, W, RG, RB, GB, RW,
    GW, BW, RGB, RGW, RBW, GBW, RGBW) we collect all ok captures where ONLY
    the channels in that family are driven (all others are zero) and fit a
    per-family slope vector XYZ/unit-per-channel via least squares.

    Each entry in the returned dict maps a family key (e.g. ``"rgw"``) to a
    (3, n_channels) array whose columns are XYZ-per-unit for each active
    channel in family order.  The column order follows the family key: e.g.
    ``"rgw"`` columns are [R_xyz, G_xyz, W_xyz].

    Single-channel families (``"r"``, ``"g"``, ``"b"``, ``"w"``) are always
    fitted.  Multi-channel families are only included if at least 4 rows are
    available (too few rows produces unreliable slopes).
    """
    # All possible families: (key, active_channel_indices in rgbw order)
    _FAMILIES: list[tuple[str, tuple[int, ...]]] = [
        ("r",    (0,)),
        ("g",    (1,)),
        ("b",    (2,)),
        ("w",    (3,)),
        ("rg",   (0, 1)),
        ("rb",   (0, 2)),
        ("gb",   (1, 2)),
        ("rw",   (0, 3)),
        ("gw",   (1, 3)),
        ("bw",   (2, 3)),
        ("rgb",  (0, 1, 2)),
        ("rgw",  (0, 1, 3)),
        ("rbw",  (0, 2, 3)),
        ("gbw",  (1, 2, 3)),
        ("rgbw", (0, 1, 2, 3)),
    ]
    channels = ("r16", "g16", "b16", "w16")

    # Collect (drive_vector, xyz) for each family
    buckets: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {fk: [] for fk, _ in _FAMILIES}

    for csv_path in sorted(input_dir.glob("*.csv")):
        with csv_path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                if not is_ok(row.get("ok")):
                    continue
                drives = np.array([safe_int(row.get(c)) for c in channels], dtype=np.float64)
                if drives.sum() <= 0:
                    continue
                active_idx = tuple(int(i) for i in np.where(drives > 0)[0])
                # Find which family key matches this active set
                for fk, fam_idx in _FAMILIES:
                    if fam_idx == active_idx:
                        xyz = np.array(
                            [safe_float(row.get("X")), safe_float(row.get("Y")), safe_float(row.get("Z"))],
                            dtype=np.float64,
                        )
                        if np.isfinite(xyz).all() and xyz[1] > 0:
                            buckets[fk].append((drives[list(fam_idx)], xyz))
                        break

    result: dict[str, np.ndarray] = {}
    for fk, fam_idx in _FAMILIES:
        rows = buckets[fk]
        min_rows = 1 if len(fam_idx) == 1 else 4
        if len(rows) < min_rows:
            continue
        # Build design matrix: rows are (drive_0, drive_1, ..., drive_n) observations
        # Each xyz = A @ slopes_col  where slopes_col is (n_channels, 3)
        A = np.stack([r[0] for r in rows], axis=0)          # (M, n_ch)
        B = np.stack([r[1] for r in rows], axis=0)          # (M, 3)
        # Unconstrained least squares per XYZ component
        slopes, _, _, _ = np.linalg.lstsq(A, B, rcond=None)  # (n_ch, 3)
        result[fk] = slopes.T                                  # (3, n_ch) — columns = channels
    return result


def load_measured_prior_dataset(input_dir: Path, reference_white: ReferenceWhite) -> MeasuredPriorDataset:
    lab_rows: list[np.ndarray] = []
    white_share_rows: list[float] = []
    rgb_ratio_rows: list[np.ndarray] = []
    family_rows: list[str] = []
    channels = ("r16", "g16", "b16", "w16")

    for csv_path in sorted(input_dir.glob("*.csv")):
        with csv_path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                if not is_ok(row.get("ok")):
                    continue

                values = np.array([safe_int(row.get(channel)) for channel in channels], dtype=float)
                channel_sum = float(np.sum(values))
                rgb_sum = float(np.sum(values[:3]))
                if channel_sum <= 0.0:
                    continue

                xyz = np.array([safe_float(row.get("X")), safe_float(row.get("Y")), safe_float(row.get("Z"))], dtype=float)
                if not np.isfinite(xyz).all():
                    continue

                lab_rows.append(xyz_to_lab(xyz, reference_white))
                white_share_rows.append(float(values[3] / channel_sum))
                family_rows.append(measured_family_name(row.get("name")))
                if rgb_sum > 0.0:
                    rgb_ratio_rows.append(values[:3] / rgb_sum)
                else:
                    rgb_ratio_rows.append(np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float))

    if not lab_rows:
        raise RuntimeError("No valid measured capture rows were found for measured prior construction")

    family_names = tuple(sorted(set(family_rows)))
    family_lookup = {name: index for index, name in enumerate(family_names)}

    return MeasuredPriorDataset(
        lab=np.asarray(lab_rows, dtype=float),
        white_share_total=np.asarray(white_share_rows, dtype=float),
        rgb_ratio=np.asarray(rgb_ratio_rows, dtype=float),
        family_index=np.asarray([family_lookup[name] for name in family_rows], dtype=np.int32),
        family_names=family_names,
    )


def query_measured_prior_details(
    dataset: MeasuredPriorDataset,
    target_lab: np.ndarray,
    neighbor_count: int,
    mode: str = "family",
    family_count: int = 0,
) -> MeasuredPriorQueryResult:
    distances = np.linalg.norm(dataset.lab - target_lab[None, :], axis=1)
    if int(neighbor_count) <= 0:
        k = int(distances.size)
    else:
        k = max(1, min(int(neighbor_count), distances.size))
    indices = np.argpartition(distances, k - 1)[:k]
    selected_distances = distances[indices]
    selected_families = dataset.family_index[indices]

    if mode == "row":
        weights = 1.0 / np.maximum(selected_distances, 1e-6)
        weight_sum = float(np.sum(weights))
        if weight_sum <= 0.0:
            weights = np.full(k, 1.0 / k, dtype=float)
        else:
            weights = weights / weight_sum

        white_share = float(np.sum(dataset.white_share_total[indices] * weights))
        rgb_ratio = np.sum(dataset.rgb_ratio[indices] * weights[:, None], axis=0)
        rgb_ratio_sum = float(np.sum(rgb_ratio))
        if rgb_ratio_sum <= 0.0:
            rgb_ratio = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float)
        else:
            rgb_ratio = rgb_ratio / rgb_ratio_sum

        family_contributions: list[PriorFamilyContribution] = []
        grouped: dict[int, list[int]] = {}
        for local_index, family_index in enumerate(selected_families):
            grouped.setdefault(int(family_index), []).append(local_index)
        for family_index, local_indices in grouped.items():
            family_weight = float(np.sum(weights[local_indices]))
            if family_weight <= 0.0:
                continue
            normalized_family_weights = weights[local_indices] / family_weight
            family_white = float(np.sum(dataset.white_share_total[indices[local_indices]] * normalized_family_weights))
            family_ratio = np.sum(dataset.rgb_ratio[indices[local_indices]] * normalized_family_weights[:, None], axis=0)
            family_ratio_sum = float(np.sum(family_ratio))
            if family_ratio_sum <= 0.0:
                family_ratio = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float)
            else:
                family_ratio = family_ratio / family_ratio_sum
            family_contributions.append(
                PriorFamilyContribution(
                    family_name=dataset.family_names[family_index],
                    family_weight=family_weight,
                    min_distance=float(np.min(selected_distances[local_indices])),
                    white_share=family_white,
                    rgb_ratio=family_ratio,
                    row_count=len(local_indices),
                )
            )
        family_contributions.sort(key=lambda item: (-item.family_weight, item.min_distance, item.family_name))
        return MeasuredPriorQueryResult(
            white_share=white_share,
            rgb_ratio=rgb_ratio,
            family_contributions=tuple(family_contributions),
            neighbor_count=k,
        )

    grouped: dict[int, list[int]] = {}
    for local_index, family_index in enumerate(selected_families):
        grouped.setdefault(int(family_index), []).append(local_index)

    family_rows: list[tuple[int, float, float, np.ndarray, int]] = []
    for family_index, local_indices in grouped.items():
        family_distances = selected_distances[local_indices]
        family_weights = 1.0 / np.maximum(family_distances, 1e-6)
        family_weight_sum = float(np.sum(family_weights))
        if family_weight_sum <= 0.0:
            family_weights = np.full(len(local_indices), 1.0 / len(local_indices), dtype=float)
        else:
            family_weights = family_weights / family_weight_sum
        family_white = float(np.sum(dataset.white_share_total[indices[local_indices]] * family_weights))
        family_ratio = np.sum(dataset.rgb_ratio[indices[local_indices]] * family_weights[:, None], axis=0)
        family_ratio_sum = float(np.sum(family_ratio))
        if family_ratio_sum <= 0.0:
            family_ratio = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float)
        else:
            family_ratio = family_ratio / family_ratio_sum
        family_rows.append((int(family_index), float(np.min(family_distances)), family_white, family_ratio, len(local_indices)))

    family_rows.sort(key=lambda item: item[1])
    if int(family_count) <= 0:
        retained = family_rows
    else:
        retained = family_rows[: max(1, min(int(family_count), len(family_rows)))]
    family_weights = np.array([1.0 / max(item[1], 1e-6) for item in retained], dtype=float)
    family_weight_sum = float(np.sum(family_weights))
    if family_weight_sum <= 0.0:
        family_weights = np.full(len(retained), 1.0 / len(retained), dtype=float)
    else:
        family_weights = family_weights / family_weight_sum

    white_share = float(np.sum(np.array([item[2] for item in retained], dtype=float) * family_weights))
    rgb_ratio = np.sum(
        np.array([item[3] for item in retained], dtype=float) * family_weights[:, None],
        axis=0,
    )
    rgb_ratio_sum = float(np.sum(rgb_ratio))
    if rgb_ratio_sum <= 0.0:
        rgb_ratio = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float)
    else:
        rgb_ratio = rgb_ratio / rgb_ratio_sum

    family_contributions = tuple(
        PriorFamilyContribution(
            family_name=dataset.family_names[item[0]],
            family_weight=float(weight),
            min_distance=float(item[1]),
            white_share=float(item[2]),
            rgb_ratio=np.asarray(item[3], dtype=float),
            row_count=int(item[4]),
        )
        for item, weight in zip(retained, family_weights)
    )
    return MeasuredPriorQueryResult(
        white_share=white_share,
        rgb_ratio=rgb_ratio,
        family_contributions=family_contributions,
        neighbor_count=k,
    )


def query_measured_prior(
    dataset: MeasuredPriorDataset,
    target_lab: np.ndarray,
    neighbor_count: int,
    mode: str = "family",
    family_count: int = 0,
) -> tuple[float, np.ndarray]:
    result = query_measured_prior_details(
        dataset,
        target_lab,
        neighbor_count,
        mode=mode,
        family_count=family_count,
    )
    return result.white_share, result.rgb_ratio


def _compute_rgbw_max_ref_white_y(
    rgb_basis: np.ndarray,
    white_basis: np.ndarray,
    reference_white: ReferenceWhite,
) -> float:
    """Find the luminance of the W-dominant reference-white state.

    Fixes w = 1 (white channel at full per-unit drive) and solves for the
    *minimum* additional RGB luminance needed to bring the W+RGB mixture to
    the target reference-white chromaticity.  Returns W_Y + min_RGB_Y per
    unit drive.

    This sets the target_Y reference so that a neutral [255,255,255] input
    maps to the reference white at high W utilisation: at full drive the W LED
    alone produces most of the target Y, and a small RGB correction closes the
    remaining chromaticity gap without the binary search ever seeing a negative
    residual.  Using the unconstrained Y-maximising LP instead would over-shoot
    target_Y and break the solver for neutral inputs.

    Falls back to the RGB-only equal-drive Y if the LP does not converge.
    """
    Kx = reference_white.x / reference_white.y
    Kz = (1.0 - reference_white.x - reference_white.y) / reference_white.y

    # Chromaticity residuals that RGB must cover to make W+RGB land on the reference white:
    #   (W_X + RGB_X) − Kx·(W_Y + RGB_Y) = 0  →  c_x·rgb = Kx·W_Y − W_X
    #   (W_Z + RGB_Z) − Kz·(W_Y + RGB_Y) = 0  →  c_z·rgb = Kz·W_Y − W_Z
    b_eq_x = Kx * float(white_basis[1]) - float(white_basis[0])
    b_eq_z = Kz * float(white_basis[1]) - float(white_basis[2])

    # Per-channel chromaticity contribution of the RGB primaries
    c_x = rgb_basis[0, :] - Kx * rgb_basis[1, :]  # (X − Kx·Y) per unit of each channel
    c_z = rgb_basis[2, :] - Kz * rgb_basis[1, :]  # (Z − Kz·Y) per unit

    result = linprog(
        c=rgb_basis[1, :],               # minimise RGB_Y = Σ r16_Y·channel
        A_eq=np.vstack([c_x, c_z]),
        b_eq=np.array([b_eq_x, b_eq_z]),
        bounds=[(0.0, 1.0)] * 3,
        method="highs",
    )
    if result.success:
        return float(white_basis[1]) + float(result.fun)  # W_Y + min RGB correction Y
    return float(np.sum(rgb_basis, axis=1)[1])  # RGB-only fallback


def build_target_rgb_basis(
    rgb_basis: np.ndarray,
    reference_white: ReferenceWhite,
    mode: str,
    white_basis: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, object]]:
    """Build the white-balanced RGB target matrix.

    The returned matrix maps input [R,G,B] → target XYZ using the RGB-only
    equal-drive Y as the luminance reference.  This keeps the binary search in
    solve_measured_white monotone for all input colours.

    If white_basis is provided (reference-white mode), the W-dominant LP value
    is also computed and returned as 'w_dominant_y_per_unit' in the info dict.
    Pass that value to solve_measured_white as w_dominant_y_per_unit so the
    solver can use the high-W pre-check for near-full-scale neutral inputs.
    """
    if mode == "raw":
        return rgb_basis.copy(), {
            "mode": mode,
            "channel_scales": [1.0, 1.0, 1.0],
            "equal_rgb_xy": xyz_to_xy(np.sum(rgb_basis, axis=1)),
            "w_dominant_y_per_unit": 0.0,
        }

    measured_equal_rgb = np.sum(rgb_basis, axis=1)
    measured_equal_rgb_y = float(measured_equal_rgb[1])
    target_white_xyz = np.array(
        [
            (reference_white.x * measured_equal_rgb_y) / reference_white.y,
            measured_equal_rgb_y,
            ((1.0 - reference_white.x - reference_white.y) * measured_equal_rgb_y) / reference_white.y,
        ],
        dtype=float,
    )
    channel_scales = np.linalg.solve(rgb_basis, target_white_xyz)
    corrected = rgb_basis @ np.diag(channel_scales)

    # Optionally compute the W-dominant LP value for use in the solver pre-check.
    # This is NOT applied to the matrix itself — the matrix keeps the RGB-only Y
    # so the binary search stays monotone.  The caller passes w_dominant_y_per_unit
    # to solve_measured_white to enable the high-W pre-check for neutral inputs.
    w_dominant_y_per_unit = 0.0
    if white_basis is not None:
        w_dominant_y_per_unit = _compute_rgbw_max_ref_white_y(rgb_basis, white_basis, reference_white)

    return corrected, {
        "mode": mode,
        "channel_scales": channel_scales.tolist(),
        "equal_rgb_xy": xyz_to_xy(np.sum(corrected, axis=1)),
        "target_white_xyz": target_white_xyz.tolist(),
        "w_dominant_y_per_unit": w_dominant_y_per_unit,
    }


def xyz_to_xy(xyz: np.ndarray) -> tuple[float, float]:
    denom = float(np.sum(xyz))
    if abs(denom) < 1e-12:
        return float("nan"), float("nan")
    return float(xyz[0] / denom), float(xyz[1] / denom)


def nnls_3x3(A: np.ndarray, b: np.ndarray, upper_bound: float) -> tuple[np.ndarray, float]:
    best_x = np.zeros(3, dtype=float)
    best_error = float("inf")

    for mask_bits in range(1, 1 << 3):
        active = [index for index in range(3) if (mask_bits >> index) & 1]
        submatrix = A[:, active]
        solution, *_ = np.linalg.lstsq(submatrix, b, rcond=None)
        if np.any(solution < -1e-8) or np.any(solution > upper_bound + 1e-8):
            continue

        full = np.zeros(3, dtype=float)
        for active_index, value in zip(active, solution):
            full[active_index] = float(value)

        error = float(np.linalg.norm(A @ full - b))
        if error < best_error:
            best_error = error
            best_x = full

    return best_x, best_error


def regularized_nnls_3x3(
    A: np.ndarray,
    b: np.ndarray,
    upper_bound: float,
    preferred: np.ndarray,
    regularization: float,
) -> tuple[np.ndarray, float]:
    if regularization <= 0.0:
        return nnls_3x3(A, b, upper_bound)

    best_x = np.zeros(3, dtype=float)
    best_score = float("inf")

    for mask_bits in range(1, 1 << 3):
        active = [index for index in range(3) if (mask_bits >> index) & 1]
        submatrix = A[:, active]
        preferred_sub = preferred[active]
        lhs = (submatrix.T @ submatrix) + (np.eye(len(active)) * regularization)
        rhs = (submatrix.T @ b) + (regularization * preferred_sub)
        try:
            solution = np.linalg.solve(lhs, rhs)
        except np.linalg.LinAlgError:
            solution, *_ = np.linalg.lstsq(lhs, rhs, rcond=None)
        if np.any(solution < -1e-8) or np.any(solution > upper_bound + 1e-8):
            continue

        full = np.zeros(3, dtype=float)
        for active_index, value in zip(active, solution):
            full[active_index] = float(value)

        residual_error = float(np.linalg.norm(A @ full - b))
        regularized_error = residual_error + (regularization * float(np.linalg.norm(full - preferred)))
        if regularized_error < best_score:
            best_score = regularized_error
            best_x = full

    return best_x, best_score


def build_rgb_grid(grid_size: int, sample_scale: float, include_value_zero: bool) -> list[dict[str, float]]:
    grid: list[dict[str, float]] = []
    value_start = 0 if include_value_zero else 1
    for hue_index in range(grid_size):
        hue = hue_index / grid_size
        for sat_index in range(1, grid_size + 1):
            saturation = sat_index / grid_size
            for val_index in range(value_start, grid_size + 1):
                value = val_index / grid_size
                r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
                grid.append(
                    {
                        "hue_hsv_deg": hue * 360.0,
                        "saturation_hsv": saturation,
                        "value_hsv": value,
                        "r": r * sample_scale,
                        "g": g * sample_scale,
                        "b": b * sample_scale,
                    }
                )
    return grid


def solve_measured_white(
    rgb_target: np.ndarray,
    rgb_basis: np.ndarray,
    white_basis: np.ndarray,
    reference_white: ReferenceWhite,
    max_delta_e: float,
    max_hue_shift: float,
    ignore_hue_below_chroma: float,
    upper_bound: float,
    target_rgb_basis: np.ndarray | None = None,
    measured_prior: MeasuredPriorDataset | None = None,
    measured_prior_neighbors: int = 64,
    measured_prior_mode: str = "family",
    measured_family_count: int = 8,
    measured_prior_strength: float = 0.0,
    nondegenerate_regularization: float = 0.0,
    max_luminance_ratio: float = 2.0,
    w_dominant_y_per_unit: float = 0.0,
) -> dict[str, object]:
    target_basis = rgb_basis if target_rgb_basis is None else target_rgb_basis
    target_xyz = target_basis @ rgb_target
    target_lab = xyz_to_lab(target_xyz, reference_white)
    _, target_chroma, target_hue = lab_to_lch(target_lab)
    target_sum = float(np.sum(rgb_target))

    # True black must always be all-off — no LED driver should emit anything for
    # a zero-drive input.  Return immediately before the binary search can assign
    # a spurious white value from the prior or the residual math.
    if target_sum <= 0.0:
        return {
            "rgb": np.zeros(3, dtype=float),
            "w": 0.0,
            "delta_e": 0.0,
            "hue_shift": 0.0,
            "prior_mode": measured_prior_mode,
            "prior_white_share": 0.0,
        }

    if target_sum > 0.0:
        target_rgb_ratio = rgb_target / target_sum
    else:
        target_rgb_ratio = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float)

    best_white = 0.0
    best_rgb = rgb_target.copy()
    best_xyz = target_xyz.copy()
    best_delta_e = 0.0
    best_hue_shift = 0.0

    # --- Pre-check W = upper_bound ------------------------------------------------
    # For near-full-scale neutral inputs, the W-dominant LP shows that W=upper_bound
    # + a small RGB correction achieves the reference white at high W share.  The
    # binary search misses this: at W=upper_bound/2, the XYZ residual exceeds RGB
    # capacity, so the search sets high=upper_bound/2 and never tries higher again
    # (non-monotone feasibility with the higher LP target_Y).
    #
    # When w_dominant_y_per_unit > 0, we compute the reference-white target at the LP
    # luminance (scaled to input level), check if W=upper_bound + NNLS RGB achieves it
    # within dE constraints, and skip the binary search if it does.  The input-scaled
    # reference-white target naturally fails for warm/saturated inputs (residual Z goes
    # negative, pure W doesn't match their chromaticity), so the binary search still
    # runs for those and produces the correct W-moderate solution.
    _skip_binary_search = False
    if w_dominant_y_per_unit > 0.0 and target_sum > 0.0:
        _Kx = reference_white.x / reference_white.y
        _Kz = (1.0 - reference_white.x - reference_white.y) / reference_white.y
        # Reference-white XYZ at the LP luminance scaled to the per-channel input level.
        # Uses target_sum/3 so the target scales equally with input brightness and
        # naturally fails for warm/saturated inputs (less than full neutral drive).
        _pre_y = w_dominant_y_per_unit * (target_sum / 3.0)
        _pre_target = np.array([_Kx * _pre_y, _pre_y, _Kz * _pre_y], dtype=float)
        _pre_residual = _pre_target - white_basis * upper_bound
        _pre_rgb, _ = nnls_3x3(rgb_basis, _pre_residual, upper_bound)
        _pre_xyz = rgb_basis @ _pre_rgb + white_basis * upper_bound
        _pre_lab = xyz_to_lab(_pre_xyz, reference_white)
        _, _pre_chroma, _pre_hue = lab_to_lch(_pre_lab)
        _pre_dE = float(np.linalg.norm((target_lab - _pre_lab)[1:]))
        _pre_hue_shift = (
            0.0
            if target_chroma < ignore_hue_below_chroma or _pre_chroma < ignore_hue_below_chroma
            else circular_hue_distance_degrees(target_hue, _pre_hue)
        )
        _pre_lum_ok = (target_xyz[1] <= 0.0) or (_pre_xyz[1] <= target_xyz[1] * max_luminance_ratio)
        if _pre_dE <= max_delta_e and _pre_hue_shift <= max_hue_shift and _pre_lum_ok:
            best_white = upper_bound
            best_rgb = _pre_rgb
            best_xyz = _pre_xyz
            best_delta_e = _pre_dE
            best_hue_shift = _pre_hue_shift
            _skip_binary_search = True
    # ------------------------------------------------------------------------------

    low = 0.0
    high = upper_bound
    for _ in range(80):
        if _skip_binary_search:
            break
        candidate_white = (low + high) / 2.0
        residual_target = target_xyz - white_basis * candidate_white
        candidate_rgb, _ = nnls_3x3(rgb_basis, residual_target, upper_bound)
        candidate_xyz = rgb_basis @ candidate_rgb + white_basis * candidate_white
        candidate_lab = xyz_to_lab(candidate_xyz, reference_white)
        _, candidate_chroma, candidate_hue = lab_to_lch(candidate_lab)
        # Chromaticity-only delta-E (a*, b*): allows brightness to vary when white is used
        # without penalising near-reference-white high-W candidates for the luminance increase.
        candidate_delta_e = float(np.linalg.norm((target_lab - candidate_lab)[1:]))
        if target_chroma < ignore_hue_below_chroma or candidate_chroma < ignore_hue_below_chroma:
            candidate_hue_shift = 0.0
        else:
            candidate_hue_shift = circular_hue_distance_degrees(target_hue, candidate_hue)

        # Luminance guard: prevent W from blowing up luminance relative to target.
        # Chromaticity-only delta-E cannot catch this for near-neutral dim inputs where
        # adding white barely shifts a*/b* (e.g. W=21320 for a [1,1,1] grey → 150× Y).
        luminance_ok = (target_xyz[1] <= 0.0) or (
            candidate_xyz[1] <= target_xyz[1] * max_luminance_ratio
        )
        if candidate_delta_e <= max_delta_e and candidate_hue_shift <= max_hue_shift and luminance_ok:
            best_white = candidate_white
            best_rgb = candidate_rgb
            best_xyz = candidate_xyz
            best_delta_e = candidate_delta_e
            best_hue_shift = candidate_hue_shift
            low = candidate_white
        else:
            high = candidate_white

    prior_white_share = 0.0
    prior_rgb_ratio = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float)
    prior_target_white = 0.0
    if measured_prior is not None:
        prior_white_share, prior_rgb_ratio = query_measured_prior(
            measured_prior,
            target_lab,
            measured_prior_neighbors,
            mode=measured_prior_mode,
            family_count=measured_family_count,
        )
        if target_sum > 0.0:
            # Correct formula: white_share = w/(r+g+b+w), so w = share/(1-share) * (r+g+b).
            # The naive target_sum*share under-counts by 1/(1-share) — for 71% share that
            # is a 3.5x error (4679 vs the correct 16374).
            if prior_white_share >= 1.0 - 1e-9:
                prior_target_white = float(upper_bound)
            else:
                prior_target_white = float(np.clip(
                    target_sum * prior_white_share / (1.0 - prior_white_share),
                    0.0,
                    upper_bound,
                ))

    if measured_prior_strength > 0.0 or nondegenerate_regularization > 0.0:
        preferred_ratio = ((1.0 - measured_prior_strength) * target_rgb_ratio) + (measured_prior_strength * prior_rgb_ratio)
        preferred_ratio_sum = float(np.sum(preferred_ratio))
        if preferred_ratio_sum <= 0.0:
            preferred_ratio = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float)
        else:
            preferred_ratio = preferred_ratio / preferred_ratio_sum

        # Do NOT cap prior_target_white at best_white — that defeats the purpose.
        # best_white is the max white that keeps the same luminance as pure RGB;
        # the prior may (correctly) suggest much higher white for near-neutral colors.
        # The candidate loop uses lum_scale scaling so high-W solutions are reachable.
        white_candidates = {float(best_white)}
        if measured_prior_strength > 0.0:
            white_candidates.add(float(prior_target_white))
            lo = min(best_white, prior_target_white)
            hi = max(best_white, prior_target_white)
            if hi - lo > 1.0:
                for candidate in np.linspace(lo, hi, 9, dtype=float):
                    white_candidates.add(float(candidate))

        selected_score = float("inf")
        selected_solution: tuple[float, np.ndarray, np.ndarray, float, float] | None = None
        delta_limit = max(max_delta_e, 1e-6)
        hue_limit = max(max_hue_shift, 1e-6)
        sum_limit = max(target_sum, 1e-6)

        for candidate_white in sorted(white_candidates):
            w_y = float(white_basis[1] * candidate_white)
            if w_y > target_xyz[1] and target_xyz[1] > 0.0:
                # W already over-exposes the target luminance.  Scale the target
                # proportionally so that RGB acts as a chromaticity corrector on top
                # of white (additive), rather than trying to fill a negative residual.
                # The luminance_ok guard below still enforces the max_luminance_ratio cap.
                lum_scale = w_y / target_xyz[1]
                residual_target = target_xyz * lum_scale - white_basis * candidate_white
                residual_rgb_sum = max(0.0, target_sum * lum_scale - candidate_white)
            else:
                residual_target = target_xyz - white_basis * candidate_white
                residual_rgb_sum = max(0.0, target_sum - candidate_white)
            preferred_rgb = preferred_ratio * residual_rgb_sum
            candidate_rgb, _ = regularized_nnls_3x3(
                rgb_basis,
                residual_target,
                upper_bound,
                preferred=preferred_rgb,
                regularization=nondegenerate_regularization,
            )
            candidate_xyz = rgb_basis @ candidate_rgb + white_basis * candidate_white
            candidate_lab = xyz_to_lab(candidate_xyz, reference_white)
            _, candidate_chroma, candidate_hue = lab_to_lch(candidate_lab)
            # Chromaticity-only delta-E (a*, b*) — same basis as the binary search.
            candidate_delta_e = float(np.linalg.norm((target_lab - candidate_lab)[1:]))
            if target_chroma < ignore_hue_below_chroma or candidate_chroma < ignore_hue_below_chroma:
                candidate_hue_shift = 0.0
            else:
                candidate_hue_shift = circular_hue_distance_degrees(target_hue, candidate_hue)

            # Same luminance guard as the binary search — prevents the prior from
            # pushing white above a safe luminance multiple of the target.
            luminance_ok = (target_xyz[1] <= 0.0) or (
                candidate_xyz[1] <= target_xyz[1] * max_luminance_ratio
            )
            if candidate_delta_e > max_delta_e or candidate_hue_shift > max_hue_shift or not luminance_ok:
                continue

            candidate_rgb_sum = float(np.sum(candidate_rgb))
            if candidate_rgb_sum > 0.0:
                candidate_ratio = candidate_rgb / candidate_rgb_sum
            else:
                candidate_ratio = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float)

            # Penalise whites BELOW prior (we want more white), not above.
            # Whites above the prior are encouraged via white_reward.
            white_error = max(0.0, prior_target_white - candidate_white) / sum_limit
            ratio_error = float(np.linalg.norm(candidate_ratio - preferred_ratio))
            color_error = candidate_delta_e / delta_limit
            hue_error = candidate_hue_shift / hue_limit if max_hue_shift > 0.0 else 0.0
            white_reward = candidate_white / sum_limit
            candidate_score = (
                color_error
                + (0.25 * hue_error)
                + (measured_prior_strength * ((4.0 * white_error) + ratio_error))
                - (0.05 * white_reward)
            )

            if candidate_score < selected_score:
                selected_score = candidate_score
                selected_solution = (
                    float(candidate_white),
                    candidate_rgb,
                    candidate_xyz,
                    candidate_delta_e,
                    candidate_hue_shift,
                )

        if selected_solution is not None:
            best_white, best_rgb, best_xyz, best_delta_e, best_hue_shift = selected_solution

    return {
        "rgb": best_rgb,
        "w": best_white,
        "xyz": best_xyz,
        "delta_e": best_delta_e,
        "hue_shift": best_hue_shift,
        "prior_mode": measured_prior_mode,
        "prior_white_share": prior_white_share,
        "prior_rgb_ratio": prior_rgb_ratio,
        "prior_target_white": prior_target_white,
    }


def evaluate_grid(
    grid: list[dict[str, float]],
    rgb_basis: np.ndarray,
    white_basis: np.ndarray,
    reference_white: ReferenceWhite,
    max_delta_e: float,
    max_hue_shift: float,
    ignore_hue_below_chroma: float,
    upper_bound: float,
    target_rgb_basis: np.ndarray | None = None,
    w_dominant_y_per_unit: float = 0.0,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sample in grid:
        rgb_target = np.array([sample["r"], sample["g"], sample["b"]], dtype=float)
        target_basis = rgb_basis if target_rgb_basis is None else target_rgb_basis
        target_xyz = target_basis @ rgb_target
        target_lab = xyz_to_lab(target_xyz, reference_white)
        target_L, target_C, target_h = lab_to_lch(target_lab)

        classic_w = float(min(rgb_target))
        classic_rgb = rgb_target - classic_w
        classic_xyz = rgb_basis @ classic_rgb + white_basis * classic_w
        classic_lab = xyz_to_lab(classic_xyz, reference_white)
        _, classic_C, classic_h = lab_to_lch(classic_lab)
        classic_delta_e = float(np.linalg.norm(target_lab - classic_lab))
        classic_hue_shift = 0.0 if min(target_C, classic_C) < ignore_hue_below_chroma else circular_hue_distance_degrees(target_h, classic_h)

        proposed = solve_measured_white(
            rgb_target,
            rgb_basis,
            white_basis,
            reference_white,
            max_delta_e,
            max_hue_shift,
            ignore_hue_below_chroma,
            upper_bound,
            target_rgb_basis=target_rgb_basis,
            w_dominant_y_per_unit=w_dominant_y_per_unit,
        )

        target_sum = float(rgb_target.sum())
        rows.append(
            {
                "hue_hsv_deg": sample["hue_hsv_deg"],
                "saturation_hsv": sample["saturation_hsv"],
                "value_hsv": sample["value_hsv"],
                "target_r": float(rgb_target[0]),
                "target_g": float(rgb_target[1]),
                "target_b": float(rgb_target[2]),
                "target_L": target_L,
                "target_C": target_C,
                "target_h": target_h,
                "classic_r": float(classic_rgb[0]),
                "classic_g": float(classic_rgb[1]),
                "classic_b": float(classic_rgb[2]),
                "classic_w": classic_w,
                "classic_delta_e": classic_delta_e,
                "classic_hue_shift": classic_hue_shift,
                "classic_white_share": (classic_w / (classic_rgb.sum() + classic_w)) if (classic_rgb.sum() + classic_w) > 0 else 0.0,
                "proposed_r": float(proposed["rgb"][0]),
                "proposed_g": float(proposed["rgb"][1]),
                "proposed_b": float(proposed["rgb"][2]),
                "proposed_w": float(proposed["w"]),
                "proposed_delta_e": float(proposed["delta_e"]),
                "proposed_hue_shift": float(proposed["hue_shift"]),
                "proposed_white_share": (float(proposed["w"]) / (float(proposed["rgb"].sum()) + float(proposed["w"]))) if (float(proposed["rgb"].sum()) + float(proposed["w"])) > 0 else 0.0,
                "white_gain_abs": float(proposed["w"] - classic_w),
                "white_gain_rel_target_sum": float((proposed["w"] - classic_w) / target_sum) if target_sum > 0 else 0.0,
            }
        )
    return rows


def write_csv(rows: list[dict[str, object]], output_path: Path) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize_results(rows: list[dict[str, object]], basis: dict[str, np.ndarray], args: argparse.Namespace) -> dict[str, object]:
    white_greater = [row for row in rows if row["white_gain_abs"] > 1.0]
    white_lower = [row for row in rows if row["white_gain_abs"] < -1.0]
    exact_zero_classic = [row for row in rows if row["classic_w"] <= 1.0 and row["proposed_w"] > 1.0]

    top_increases = sorted(rows, key=lambda row: row["white_gain_abs"], reverse=True)[: args.top_count]
    top_decreases = sorted(rows, key=lambda row: row["white_gain_abs"])[: args.top_count]

    def export_rows(source_rows: list[dict[str, object]]) -> list[dict[str, object]]:
        result = []
        for row in source_rows:
            result.append(
                {
                    "hue_hsv_deg": row["hue_hsv_deg"],
                    "saturation_hsv": row["saturation_hsv"],
                    "value_hsv": row["value_hsv"],
                    "target_C": row["target_C"],
                    "classic_w": row["classic_w"],
                    "proposed_w": row["proposed_w"],
                    "white_gain_abs": row["white_gain_abs"],
                    "classic_delta_e": row["classic_delta_e"],
                    "proposed_delta_e": row["proposed_delta_e"],
                    "classic_hue_shift": row["classic_hue_shift"],
                    "proposed_hue_shift": row["proposed_hue_shift"],
                }
            )
        return result

    return {
        "settings": {
            "max_delta_e": args.max_delta_e,
            "max_hue_shift": args.max_hue_shift,
            "ignore_hue_below_chroma": args.ignore_hue_below_chroma,
            "target_white_balance_mode": args.target_white_balance_mode,
            "grid_size": args.grid_size,
            "sample_scale": args.sample_scale,
        },
        "basis_xyz_per_q16": {key: value.tolist() for key, value in basis.items()},
        "counts": {
            "total_samples": len(rows),
            "proposed_more_white_than_classic": len(white_greater),
            "proposed_less_white_than_classic": len(white_lower),
            "classic_zero_proposed_positive": len(exact_zero_classic),
        },
        "white_gain_stats": {
            "mean_abs": float(np.mean([row["white_gain_abs"] for row in rows])),
            "median_abs": float(np.median([row["white_gain_abs"] for row in rows])),
            "p90_abs": float(np.quantile([row["white_gain_abs"] for row in rows], 0.90)),
            "p10_abs": float(np.quantile([row["white_gain_abs"] for row in rows], 0.10)),
        },
        "top_increases": export_rows(top_increases),
        "top_decreases": export_rows(top_decreases),
    }


def plot_white_gain_vs_chroma(rows: list[dict[str, object]], output_path: Path) -> None:
    chroma = np.array([row["target_C"] for row in rows], dtype=float)
    white_gain = np.array([row["white_gain_abs"] for row in rows], dtype=float)
    hue = np.array([row["hue_hsv_deg"] for row in rows], dtype=float)

    fig, ax = plt.subplots(figsize=(10, 8))
    scatter = ax.scatter(chroma, white_gain, c=hue, cmap="hsv", s=12, alpha=0.45, edgecolors="none")
    ax.axhline(0.0, color="black", linewidth=1.0, alpha=0.4)
    ax.set_title("Measured-basis white gain over classic min(rgb)")
    ax.set_xlabel("Target C*ab")
    ax.set_ylabel("Proposed W - classic W")
    ax.grid(True, alpha=0.2)
    fig.colorbar(scatter, ax=ax, label="HSV hue angle")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_hue_vs_white_gain(rows: list[dict[str, object]], output_path: Path) -> None:
    hue = np.array([row["hue_hsv_deg"] for row in rows], dtype=float)
    white_gain = np.array([row["white_gain_abs"] for row in rows], dtype=float)
    chroma = np.array([row["target_C"] for row in rows], dtype=float)

    fig, ax = plt.subplots(figsize=(10, 8))
    scatter = ax.scatter(hue, white_gain, c=chroma, cmap="viridis", s=12, alpha=0.45, edgecolors="none")
    ax.axhline(0.0, color="black", linewidth=1.0, alpha=0.4)
    ax.set_title("White gain versus HSV hue")
    ax.set_xlabel("HSV hue angle")
    ax.set_ylabel("Proposed W - classic W")
    ax.grid(True, alpha=0.2)
    fig.colorbar(scatter, ax=ax, label="Target C*ab")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_classic_vs_proposed(rows: list[dict[str, object]], output_path: Path) -> None:
    classic = np.array([row["classic_w"] for row in rows], dtype=float)
    proposed = np.array([row["proposed_w"] for row in rows], dtype=float)
    value = np.array([row["value_hsv"] for row in rows], dtype=float)

    fig, ax = plt.subplots(figsize=(8, 8))
    scatter = ax.scatter(classic, proposed, c=value, cmap="plasma", s=12, alpha=0.45, edgecolors="none")
    limit = float(max(classic.max(initial=0.0), proposed.max(initial=0.0)))
    ax.plot([0.0, limit], [0.0, limit], linestyle="--", color="black", alpha=0.5)
    ax.set_title("Classic white versus measured-basis white")
    ax.set_xlabel("Classic W = min(R,G,B)")
    ax.set_ylabel("Proposed W")
    ax.grid(True, alpha=0.2)
    fig.colorbar(scatter, ax=ax, label="HSV value")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    reference_white = ReferenceWhite(args.white_x, args.white_y, args.white_Y)
    basis = fit_basis_from_pure_sweeps(args.input_dir)
    rgb_basis = np.column_stack([basis["r16"], basis["g16"], basis["b16"]])
    white_basis = basis["w16"]
    target_rgb_basis, target_basis_info = build_target_rgb_basis(rgb_basis, reference_white, args.target_white_balance_mode, white_basis=white_basis)
    w_dom_y = float(target_basis_info.get("w_dominant_y_per_unit", 0.0))

    grid = build_rgb_grid(args.grid_size, args.sample_scale, args.include_value_zero)
    rows = evaluate_grid(
        grid,
        rgb_basis,
        white_basis,
        reference_white,
        args.max_delta_e,
        args.max_hue_shift,
        args.ignore_hue_below_chroma,
        args.sample_scale,
        target_rgb_basis=target_rgb_basis,
        w_dominant_y_per_unit=w_dom_y,
    )

    write_csv(rows, output_dir / "solver_grid_comparison.csv")
    summary = summarize_results(rows, basis, args)
    summary["target_basis"] = target_basis_info
    with (output_dir / "solver_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    plot_white_gain_vs_chroma(rows, output_dir / "white_gain_vs_chroma.png")
    plot_hue_vs_white_gain(rows, output_dir / "white_gain_vs_hue.png")
    plot_classic_vs_proposed(rows, output_dir / "classic_vs_proposed_white.png")

    print(f"Fitted basis from {args.input_dir}")
    for key in ("r16", "g16", "b16", "w16"):
        print(f"  {key}: {basis[key]}")
    print(f"Target white-balance mode: {args.target_white_balance_mode}")
    print(f"Target equal-RGB xy: {target_basis_info['equal_rgb_xy']}")
    print(f"Evaluated {len(rows)} RGB samples")
    print(f"Wrote comparison CSV to {output_dir / 'solver_grid_comparison.csv'}")
    print(f"Wrote summary JSON to {output_dir / 'solver_summary.json'}")
    print(f"Plots written under {output_dir}")


if __name__ == "__main__":
    main()