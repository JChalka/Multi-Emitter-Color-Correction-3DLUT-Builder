"""build_delaunay_rgbw_lut.py  —  Mode 2 RGBW LUT builder.

Architecture
------------
Each ~40 k physical capture is a directly measured RGBW state:
    (r16, g16, b16, w16)  →  (X, Y, Z)

The ~40 k XYZ vectors form a point cloud that spans the physical gamut of the
display.  A 3-D Delaunay triangulation decomposes that cloud into tetrahedra.

For every LUT grid node  (input RGB  →  target XYZ  via the calibrated basis):

  1. Find the enclosing tetrahedron with  scipy.spatial.Delaunay.find_simplex.
  2. Compute barycentric weights  α, β, γ, δ  (sum = 1, all ≥ 0).
  3. Output RGBW = α·RGBW₁ + β·RGBW₂ + γ·RGBW₃ + δ·RGBW₄.

This is exact:  RGBW linearity means the same barycentric combination that
reaches target XYZ in the capture space produces target XYZ on the display.

Out-of-gamut nodes (high-saturation corners whose target XYZ lies outside the
capture convex hull) fall back to a constrained NNLS on the K nearest captures,
yielding the closest achievable XYZ with some white contribution wherever the
physics allows it.

The full LUT (--full-grid-size) is solved DIRECTLY — not by coarse-grid
trilinear expansion — so no inter-node interpolation artefacts are introduced.

Outputs (.npy, comparison CSV, summary JSON, utilization CSV) are compatible
with the existing verifier pipeline.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
from datetime import datetime, timezone
import itertools
import json
import math
import os
from pathlib import Path

import numpy as np
from numpy.lib.format import open_memmap
from scipy.optimize import nnls as scipy_nnls, least_squares
from scipy.spatial import ConvexHull, Delaunay, cKDTree

try:
    from ..paths import DEFAULT_CONFIG_DIR, DEFAULT_LUT_OUTPUT_DIR
    from .prototype_measured_white_solver import (
        DEFAULT_INPUT_DIR,
        ReferenceWhite,
        build_target_rgb_basis,
        fit_basis_from_pure_sweeps,
        fit_basis_from_all_families,
        lab_to_lch,
        xyz_to_lab,
        safe_float,
        safe_int,
        is_ok,
    )
except ImportError:
    from prototype_measured_white_solver import (
        DEFAULT_INPUT_DIR,
        ReferenceWhite,
        build_target_rgb_basis,
        fit_basis_from_pure_sweeps,
        fit_basis_from_all_families,
        lab_to_lch,
        xyz_to_lab,
        safe_float,
        safe_int,
        is_ok,
    )

    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"
    DEFAULT_LUT_OUTPUT_DIR = _PROJECT_ROOT / "lut_outputs"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = DEFAULT_LUT_OUTPUT_DIR

# ---------------------------------------------------------------------------
# Module-level worker state (populated once per worker process)
# ---------------------------------------------------------------------------

_worker_state: dict = {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build RGBW LUT via Delaunay tetrahedralization of physical captures (Mode 2)."
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR,
        help="Directory containing capture CSV files")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Output directory for LUT files")
    parser.add_argument("--white-x", type=float, default=0.3127,
        help="Reference white CIE x")
    parser.add_argument("--white-y", type=float, default=0.3290,
        help="Reference white CIE y")
    parser.add_argument("--white-Y", type=float, default=100.0,
        help="Reference white Y luminance")
    parser.add_argument("--target-white-balance-mode",
        choices=["raw", "reference-white"], default="reference-white",
        help="How to map input RGB to target XYZ")
    parser.add_argument("--sample-scale", type=float, default=65535.0,
        help="Maximum drive value (default: 65535 for 16-bit)")
    parser.add_argument("--coarse-grid-size", type=int, default=17,
        help="Grid size for the coarse diagnostic LUT (default: 17)")
    parser.add_argument("--full-grid-size", type=int, default=256,
        help="Grid size for the full LUT, solved directly (default: 256)")
    parser.add_argument("--skip-full-lut", action="store_true",
        help="Skip building the full-resolution LUT")
    parser.add_argument("--hull-fallback-k", type=int, default=8,
        help="(legacy, unused) K nearest captures for out-of-convex-hull fallback")
    parser.add_argument("--knn-max-candidate-axis", type=int, default=160,
        help="Maximum raw+scaled measured-anchor candidate axis before memory auto-capping (default: 160)")
    parser.add_argument("--knn-min-candidate-axis", type=int, default=32,
        help="Preferred minimum measured-anchor candidate axis when memory allows (default: 32)")
    parser.add_argument("--knn-memory-fraction", type=float, default=0.50,
        help="Fraction of currently available RAM that all workers may use for measured-anchor candidate temporaries (default: 0.50)")
    parser.add_argument("--knn-bytes-per-row-candidate", type=float, default=112.0,
        help="Estimated temporary bytes per row per measured-anchor candidate for auto-capping (default: 112)")
    parser.add_argument("--workers", "-j", type=int, default=0,
        help="Worker processes (0 = all logical CPUs)")
    parser.add_argument("--delta-e-tiebreak", type=float, default=2.0,
        help="Full Lab ΔE budget by which W preference can override a better-colour "
             "family for near-neutral inputs (default: 2.0).  0 = no W preference.")
    parser.add_argument("--chroma-gate", type=float, default=15.0,
        help="CIELAB C* above which the W reward is fully suppressed (default: 15). "
             "Pure primaries have C*>50 so W reward is zero for them.")
    parser.add_argument("--skip-header", action="store_true",
        help="Skip writing the C header file")
    parser.add_argument("--skip-probe-debug", action="store_true",
        help="Skip writing the small named probe debug CSV")
    parser.add_argument("--verifier-diagnostics-dir", type=Path, default=None,
        help="Optional folder containing verifier CSVs and lut_target_match CSVs to build an informational failure dictionary")
    parser.add_argument("--skip-failure-dictionary", action="store_true",
        help="Skip writing verifier_failure_dictionary.{json,csv} even when verifier-diagnostics-dir is set")
    parser.add_argument("--config-dir", type=Path, default=SCRIPT_DIR / "config",
        help="Local config directory for display profiles and verifier feedback banks")
    parser.add_argument("--display-profile", type=str, default="default_display",
        help="Display profile id or JSON path used to scope verifier feedback dictionaries")
    parser.add_argument("--display-id", type=str, default="",
        help="Optional explicit display id override for the active display profile")
    parser.add_argument("--feedback-mode",
        choices=["off", "diagnostic", "candidate", "penalty", "reevaluate"], default="diagnostic",
        help="Verifier feedback usage mode. Only off/diagnostic write dictionaries in this pass; candidate/penalty/reevaluate are reserved.")
    parser.add_argument("--feedback-bank", type=str, default="auto",
        help="Verifier feedback bank path, or 'auto' for config/dictionaries/<display_id>/verifier_feedback_bank.json")
    parser.add_argument("--feedback-trust-pass-dE", type=float, default=2.5,
        help="dE threshold used to classify verifier pass/fail feedback entries")
    parser.add_argument("--disable-output-guardrails", action="store_true",
        help="Diagnostic mode: skip post-solve RGBW guardrails except pure-primary endpoint authority")
    parser.add_argument("--measured-candidate-solver",
        choices=["off", "diagnostic", "active"], default="active",
        help="Use physical patch captures as an active measured-candidate solver after family/W-axis scoring (default: active)")
    parser.add_argument("--measured-candidate-top-k", type=int, default=768,
        help="XY-nearest captures per legal family pool to evaluate in the measured-candidate solver (default: 768)")
    parser.add_argument("--measured-candidate-de-threshold", type=float, default=100.0,
        help="Predicted Lab dE gate for replacing normal solver output with measured-candidate output (default: 100.0)")
    parser.add_argument("--header-name", type=str, default="delaunay_rgbw_lut",
        help="C header array name prefix")
    parser.add_argument("--header-grid-size", type=int, default=0,
        help="Grid size to embed in the C header (0 = coarse-grid-size)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Capture loading and deduplication
# ---------------------------------------------------------------------------

def load_captures(input_dir: Path) -> tuple[np.ndarray, np.ndarray, int, list[dict]]:
    """Load all ok=True captures from input_dir.

    Deduplicates rows with identical (r16, g16, b16, w16) drive tuples by
    averaging their XYZ measurements.  Independent captures with distinct
    drive values are kept as separate points.

    Returns
    -------
    xyz_points  : (N, 3) float64  — XYZ of each unique drive state
    rgbw_points : (N, 4) float64  — RGBW drive values [0..65535]
    raw_count   : int             — total ok rows before deduplication
    meta        : list[dict]      — per-point metadata for utilization report
    """
    channels = ("r16", "g16", "b16", "w16")
    buckets: dict[tuple[int, int, int, int], list[np.ndarray]] = {}
    bucket_names: dict[tuple[int, int, int, int], list[str]] = {}
    raw_count = 0

    for csv_path in sorted(input_dir.glob("*.csv")):
        with csv_path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                if not is_ok(row.get("ok")):
                    continue
                drives = tuple(safe_int(row.get(c)) for c in channels)
                total_drive = sum(drives)
                if total_drive <= 0:
                    continue
                xyz = np.array(
                    [safe_float(row.get("X")), safe_float(row.get("Y")), safe_float(row.get("Z"))],
                    dtype=np.float64,
                )
                if not np.isfinite(xyz).all() or xyz[1] <= 0.0:
                    continue
                raw_count += 1
                buckets.setdefault(drives, []).append(xyz)
                bucket_names.setdefault(drives, []).append(str(row.get("name", "")))

    if not buckets:
        raise RuntimeError(f"No valid captures found in {input_dir}")

    keys = sorted(buckets.keys())
    xyz_list: list[np.ndarray] = []
    rgbw_list: list[np.ndarray] = []
    meta: list[dict] = []

    for drives in keys:
        xyz_stack = np.stack(buckets[drives], axis=0)
        xyz_mean = xyz_stack.mean(axis=0)
        n_avg = len(buckets[drives])
        xyz_list.append(xyz_mean)
        rgbw_list.append(np.array(drives, dtype=np.float64))
        meta.append({
            "r16": drives[0], "g16": drives[1], "b16": drives[2], "w16": drives[3],
            "X": float(xyz_mean[0]), "Y": float(xyz_mean[1]), "Z": float(xyz_mean[2]),
            "n_averaged": n_avg,
            "example_name": bucket_names[drives][0],
        })

    xyz_points = np.array(xyz_list, dtype=np.float64)
    rgbw_points = np.array(rgbw_list, dtype=np.float64)
    return xyz_points, rgbw_points, raw_count, meta


# ---------------------------------------------------------------------------
# Constrained barycentric NNLS (out-of-hull fallback)
# ---------------------------------------------------------------------------

def fit_constrained_bary(
    anchor_xyz: np.ndarray,
    target_xyz: np.ndarray,
    anchor_rgbw: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Closest point in convex hull of anchors via augmented NNLS.

    Finds non-negative weights w (sum = 1) minimising
        || anchor_xyz.T @ w  -  target_xyz ||²
    by appending a heavily-weighted equality row.  Applies same weights to
    anchor_rgbw and returns (rgbw_out, weights).
    """
    k = len(anchor_xyz)
    # Scale the sum constraint to dominate the XYZ fit
    scale = max(float(np.max(np.abs(anchor_xyz))), 1.0) * 1e3
    A = np.vstack([anchor_xyz.T, scale * np.ones((1, k))])   # (4, k)
    b_vec = np.append(target_xyz, scale)                       # (4,)
    w, _ = scipy_nnls(A, b_vec)
    w_sum = float(w.sum())
    if w_sum > 1e-12:
        w = w / w_sum
    else:
        w = np.ones(k, dtype=np.float64) / k
    return w @ anchor_rgbw, w


# ---------------------------------------------------------------------------
# Chromaticity-preserving hull boundary projection
# ---------------------------------------------------------------------------

def project_to_hull_batch(
    xyz_out: np.ndarray,
    tri: Delaunay,
    n_steps: int = 40,
) -> tuple[np.ndarray, np.ndarray]:
    """Project out-of-hull XYZ points to the convex hull boundary along their
    chromaticity rays (uniform scale toward origin).

    For each point i, binary-searches the maximum scale factor t ∈ (0, 1] such
    that  t * xyz_out[i]  lies inside the convex hull.  Preserves CIE
    chromaticity (x, y) while clamping luminance to the maximum the display
    can physically reproduce for that chromaticity.

    This is the primary out-of-hull strategy.  Bright-neutral targets (e.g.
    full-white input) whose additive-RGB target Y slightly exceeds the hull
    boundary will be projected to the W=max rim of the hull, so the Delaunay
    interpolation naturally selects W-dominant captures as vertices.

    Returns
    -------
    projected : (P, 3) float64  — projected XYZ; equals xyz_out for failures
    success   : (P,)  bool      — True where a valid interior was found on the ray
    """
    P = len(xyz_out)
    scale_lo = np.zeros(P, dtype=np.float64)
    has_interior = np.zeros(P, dtype=bool)

    # Progressive scan: find a guaranteed in-hull point on each chromaticity ray
    for s in (0.95, 0.9, 0.8, 0.7, 0.5, 0.3, 0.1, 0.05, 0.01, 0.001):
        remaining_idx = np.where(~has_interior)[0]
        if len(remaining_idx) == 0:
            break
        candidates = xyz_out[remaining_idx] * s
        found_mask = tri.find_simplex(candidates) >= 0
        found_global = remaining_idx[found_mask]
        scale_lo[found_global] = s
        has_interior[found_global] = True

    scale_hi = np.ones(P, dtype=np.float64)

    # Vectorised binary search: converge scale_lo → boundary
    for _ in range(n_steps):
        if not has_interior.any():
            break
        s_mid = (scale_lo + scale_hi) / 2.0              # (P,)
        candidates = xyz_out * s_mid[:, None]             # (P, 3)
        in_hull = tri.find_simplex(candidates) >= 0       # (P,)
        # Only update bounds for points that have a known interior (active)
        update = has_interior
        scale_lo = np.where(update & in_hull,  s_mid, scale_lo)
        scale_hi = np.where(update & ~in_hull, s_mid, scale_hi)

    # Apply a tiny inward margin to avoid floating-point boundary glitches
    final_scale = np.where(has_interior, scale_lo * 0.9999, 1.0)
    projected = xyz_out * final_scale[:, None]
    return projected, has_interior


def project_to_hull_boundary(
    xyz_targets: np.ndarray,
    tri: Delaunay,
    n_steps: int = 44,
) -> tuple[np.ndarray, np.ndarray]:
    """Project ANY XYZ targets (in-hull OR out-of-hull) to the convex hull surface.

    Finds max t s.t. ``t * xyz`` is inside the hull, for t ∈ (0, ∞).

    - In-hull targets (t=1 already inside): binary-searches upward to find the
      OUTWARD hull boundary (t > 1).  This is the key path that pushes targets
      toward W-dominant hull-surface captures and drives near-100% utilisation.
    - Out-of-hull targets (t=1 outside): binary-searches inward (t < 1), same
      as the legacy ``project_to_hull_batch``.

    Chromaticity (CIE x, y) is exactly preserved; only luminance changes.

    Returns
    -------
    projected : (P, 3) float64
    success   : (P,)  bool  — False only when the hull has no interior on that ray
    """
    P = len(xyz_targets)

    # Classify each target
    at_t1 = tri.find_simplex(xyz_targets) >= 0  # (P,) — in-hull at t=1?

    scale_lo = np.zeros(P, dtype=np.float64)
    scale_hi = np.ones(P, dtype=np.float64)
    has_lo = np.zeros(P, dtype=bool)   # known in-hull scale
    has_hi = np.zeros(P, dtype=bool)   # known out-of-hull scale

    # --- In-hull targets: lo=1 is inside, find hi > 1 (exponential scan up) ---
    in_idx = np.where(at_t1)[0]
    if len(in_idx):
        scale_lo[in_idx] = 1.0
        has_lo[in_idx] = True
        # scale_hi will be set once we find an out-of-hull upper bound
        t_test = 2.0
        still_searching = in_idx.copy()
        for _ in range(7):   # 2, 4, 8, 16, 32, 64, 128 × original
            if len(still_searching) == 0:
                break
            candidates = xyz_targets[still_searching] * t_test
            outside = still_searching[tri.find_simplex(candidates) < 0]
            inside  = still_searching[tri.find_simplex(candidates) >= 0]
            # Those now outside: hi found
            scale_hi[outside] = t_test
            has_hi[outside] = True
            # Those still inside: update lo, keep searching
            scale_lo[inside] = t_test
            still_searching = inside
            t_test *= 2.0
        # Any still inside after 128× are essentially unbounded → treat as failure
        # (won't happen for a finite hull, but guard anyway)

    # --- Out-of-hull targets: hi=1 is outside, find lo < 1 (scan down) ---
    out_idx = np.where(~at_t1)[0]
    if len(out_idx):
        has_hi[out_idx] = True   # scale_hi already 1.0
        for s in (0.95, 0.9, 0.8, 0.7, 0.5, 0.3, 0.1, 0.05, 0.01, 0.001):
            need_lo = np.where(~at_t1 & ~has_lo)[0]
            if len(need_lo) == 0:
                break
            candidates = xyz_targets[need_lo] * s
            found = need_lo[tri.find_simplex(candidates) >= 0]
            scale_lo[found] = s
            has_lo[found] = True

    # --- Binary search for all points where both bounds are known ---
    active = has_lo & has_hi
    for _ in range(n_steps):
        if not active.any():
            break
        s_mid = (scale_lo + scale_hi) / 2.0
        in_hull = tri.find_simplex(xyz_targets * s_mid[:, None]) >= 0
        scale_lo = np.where(active & in_hull,  s_mid, scale_lo)
        scale_hi = np.where(active & ~in_hull, s_mid, scale_hi)

    success = has_lo & has_hi
    final_scale = np.where(success, scale_lo * 0.9999, 1.0)
    projected = xyz_targets * final_scale[:, None]
    return projected, success


# ---------------------------------------------------------------------------
# Luminance scale: map full-white RGB input to RGBW hull maximum
# ---------------------------------------------------------------------------

def compute_y_scale(
    xyz_points: np.ndarray,
    target_rgb_basis: np.ndarray,
    sample_scale: float,
    tri=None,
    n_steps: int = 60,
    reference_white: ReferenceWhite | None = None,
    rgbw_points: np.ndarray | None = None,
) -> float:
    """Neutral-axis luminance scale.

    Earlier versions used the highest measured Y on the D65 ray, which pushes
    full white toward the brightest W-inclusive hull point (~2k Y here).  That
    is not the desired endpoint: full white should target the best measured
    D65-ish state with W at/near 65535, then use RGB only as residual
    correction.  If reference/capture data are available, choose that measured
    endpoint and scale neutral targets to it.  Otherwise fall back to the legacy
    hull-boundary search.
    """
    white_xyz = target_rgb_basis @ np.full(3, sample_scale)

    if reference_white is not None and rgbw_points is not None and len(xyz_points):
        base_Y = float(white_xyz[1])
        if base_Y > 1e-9:
            denom = np.maximum(xyz_points.sum(axis=1), 1e-9)
            x = xyz_points[:, 0] / denom
            y = xyz_points[:, 1] / denom
            xy_dist = np.sqrt((x - reference_white.x) ** 2 + (y - reference_white.y) ** 2)
            # Full-white endpoint: require absolute W near max, then pick closest
            # chromaticity to the requested reference white.  This avoids chasing
            # the brightest W-inclusive point when it is not actually D65.
            w = rgbw_points[:, 3]
            valid = (w >= sample_scale * 0.98) & np.isfinite(xy_dist) & (xyz_points[:, 1] > 0)
            if valid.any():
                # Prefer closest xy first; use Y only as a weak tie-break.
                cand = np.where(valid)[0]
                score = xy_dist[cand] * 1000.0 - (xyz_points[cand, 1] / max(float(xyz_points[cand, 1].max()), 1.0)) * 0.01
                best = cand[int(np.argmin(score))]
                return float(xyz_points[best, 1] / base_Y)

    # Legacy fallback: find hull boundary on reference-white ray.
    _own_tri = tri is None
    if _own_tri:
        tri = Delaunay(xyz_points)

    in_hull = tri.find_simplex(white_xyz[None])[0] >= 0

    if not in_hull:
        # white_xyz is outside the capture hull — project inward to boundary
        proj, success = project_to_hull_batch(white_xyz[None], tri, n_steps=n_steps)
        if _own_tri:
            del tri
        if success[0] and float(white_xyz[1]) > 1e-6:
            return float(proj[0, 1] / white_xyz[1]) * 0.9999
        return 1.0

    # white_xyz is inside the hull — binary-search OUTWARD for the hull boundary.
    # project_to_hull_batch only searches t ∈ (0, 1], so we need to do this
    # separately with t > 1.
    white_norm = float(np.linalg.norm(white_xyz))
    if white_norm < 1e-12:
        if _own_tri:
            del tri
        return 1.0

    white_unit = white_xyz / white_norm
    # Upper bound: project all captures onto the white direction, take max.
    # Multiply by 1.05 to guarantee at least one step beyond the hull.
    dots = xyz_points @ white_unit          # (N,) — scalar projections
    t_hi = float(dots.max()) / white_norm * 1.05

    t_lo = 1.0   # guaranteed inside (verified above)

    for _ in range(n_steps):
        t_mid = (t_lo + t_hi) / 2.0
        if tri.find_simplex((white_xyz * t_mid)[None])[0] >= 0:
            t_lo = t_mid
        else:
            t_hi = t_mid

    if _own_tri:
        del tri

    return float(t_lo * 0.9999)   # tiny inward margin to avoid boundary glitches


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def axis_values(grid_size: int, sample_scale: float) -> np.ndarray:
    return np.linspace(0.0, sample_scale, grid_size, dtype=np.float64)


def xyz_to_xy(xyz: np.ndarray) -> tuple[float, float]:
    denom = float(np.sum(xyz))
    if abs(denom) < 1e-12:
        return float("nan"), float("nan")
    return float(xyz[0] / denom), float(xyz[1] / denom)


def _xyY_from_chroma_and_Y(chroma_xyz: np.ndarray, target_Y: np.ndarray) -> np.ndarray:
    """Return XYZ with chromaticity from ``chroma_xyz`` and luminance ``target_Y``.

    This is the key target-construction distinction for Mode 2:
      * chromaticity should come from the calibrated/reference RGB target basis,
      * Y should come from the physical/raw drive level so primaries and edges
        keep their usable luminance/resolution instead of being globally WB-scaled.

    Without this, dual-channel and skin/warm/cool targets inherit raw measured
    same-drive chromaticity, which is exactly why cyan/magenta/yellow kept
    landing on equal-drive states instead of the known calibrated anchors.
    """
    chroma_xyz = np.asarray(chroma_xyz, dtype=np.float64)
    Y = np.asarray(target_Y, dtype=np.float64)
    denom = np.maximum(chroma_xyz.sum(axis=1), 1e-12)
    x = chroma_xyz[:, 0] / denom
    y = chroma_xyz[:, 1] / denom
    out = np.zeros_like(chroma_xyz, dtype=np.float64)
    valid = y > 1e-12
    out[valid, 0] = (x[valid] * Y[valid]) / y[valid]
    out[valid, 1] = Y[valid]
    out[valid, 2] = ((1.0 - x[valid] - y[valid]) * Y[valid]) / y[valid]
    return out


def _xyY_from_xy_and_Y(chroma_xy: np.ndarray, target_Y: np.ndarray) -> np.ndarray:
    """Return XYZ with chromaticity from direct xy coordinates and luminance Y.

    This is used by the target-space RGB transform.  The fitted transform maps
    source linear RGB into measured-primary *xy barycentric* coordinates, so the
    predicted xy must be used directly.  Converting those weights through a
    unit-Y XYZ primary basis is a different model and was the cause of the large
    yellow/orange/spring/rose target skew.
    """
    xy = np.asarray(chroma_xy, dtype=np.float64)
    Y = np.asarray(target_Y, dtype=np.float64)
    out = np.zeros((len(xy), 3), dtype=np.float64)
    x = xy[:, 0]
    y = xy[:, 1]
    valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(Y) & (y > 1e-12) & (Y > 0.0)
    out[valid, 0] = (x[valid] * Y[valid]) / y[valid]
    out[valid, 1] = Y[valid]
    out[valid, 2] = ((1.0 - x[valid] - y[valid]) * Y[valid]) / y[valid]
    return out



# ---------------------------------------------------------------------------
# Target RGB -> measured-primary chromaticity model
# ---------------------------------------------------------------------------

# Semantic colour anchors used to derive the source-RGB -> measured-primary
# barycentric transform.  These are target-space coordinates, not measured LED
# centroids.  The transform is fitted at build time against the *current*
# measured primary centroids, so it adapts when the LED/wall/diffuser gamut
# changes.  No gamma/transfer curve is applied here; input RGB is treated as
# already linear-light, matching the HyperHDR pipeline before this LUT.
_TARGET_XY_ANCHORS: list[tuple[str, tuple[int, int, int], tuple[float, float]]] = [
    ("yellow",     (65535, 60395, 3855), (0.445, 0.504)),
    ("orange",     (65535, 33924, 1799), (0.531, 0.436)),
    ("chartreuse", (40349, 65535, 4626), (0.358, 0.573)),
    ("spring",     (3084, 65535, 50115), (0.151, 0.457)),
    ("rose",       (65535, 20560, 50886), (0.436, 0.252)),
]


def _primary_xy_from_basis(rgb_basis: np.ndarray) -> np.ndarray:
    """Return measured primary xy rows [R,G,B] from a 3x3 XYZ basis."""
    basis = np.asarray(rgb_basis, dtype=np.float64)
    out = np.zeros((3, 2), dtype=np.float64)
    for i in range(3):
        xyz = basis[:, i]
        denom = max(float(np.sum(xyz)), 1e-12)
        out[i, 0] = float(xyz[0] / denom)
        out[i, 1] = float(xyz[1] / denom)
    return out


def _unit_y_basis_from_primary_xy(primary_xy: np.ndarray) -> np.ndarray:
    """Build a 3x3 XYZ(Y=1) basis whose columns are primary chromaticities."""
    cols: list[np.ndarray] = []
    for x, y in np.asarray(primary_xy, dtype=np.float64):
        if y <= 1e-12:
            cols.append(np.array([0.0, 1.0, 0.0], dtype=np.float64))
        else:
            cols.append(np.array([x / y, 1.0, (1.0 - x - y) / y], dtype=np.float64))
    return np.stack(cols, axis=1)


def fit_rgb_to_effective_bary_matrix(
    raw_rgb_basis: np.ndarray,
    sample_scale: float = 65535.0,
    anchors: list[tuple[str, tuple[int, int, int], tuple[float, float]]] | None = None,
) -> np.ndarray:
    """Fit a linear RGB->effective-measured-primary transform.

    The solver needs target xy for the source RGB values it receives.  Directly
    mixing the measured LED centroids by the input RGB tuple is wrong for the
    current source RGB coordinate system: e.g. the RGB tuple for semantic
    orange/yellow/rose is not the same as an LED-drive barycentric mix.

    We therefore fit a signed 3x3 target-space matrix A such that:

        q = linear_rgb @ A
        target_xy = weighted_mix(measured_primary_xy, q)

    This is still linear-light RGB.  It is a coloursystem/barycentric transform,
    not a gamma or transfer curve.  The measured primary centroids are taken from
    ``raw_rgb_basis`` at runtime, while the semantic anchor xy values define the
    source colourspace convention we want the LUT to target.
    """
    anchors = anchors or _TARGET_XY_ANCHORS
    primary_xy = _primary_xy_from_basis(raw_rgb_basis)
    rgbs = [np.asarray(rgb, dtype=np.float64) / float(sample_scale) for _name, rgb, _xy in anchors]
    xys = [np.asarray(xy, dtype=np.float64) for _name, _rgb, xy in anchors]

    def pred_xy_from_A(A_flat: np.ndarray, rgb01: np.ndarray) -> np.ndarray:
        A = A_flat.reshape(3, 3)
        q = rgb01 @ A
        denom = float(np.sum(q))
        if abs(denom) <= 1e-12:
            return np.array([0.3127, 0.3290], dtype=np.float64)
        return (q @ primary_xy) / denom

    def residual(A_flat: np.ndarray) -> np.ndarray:
        res = []
        for rgb01, expected_xy in zip(rgbs, xys):
            res.extend((pred_xy_from_A(A_flat, rgb01) - expected_xy) * 100.0)
        return np.asarray(res, dtype=np.float64)

    sol = least_squares(residual, np.eye(3, dtype=np.float64).ravel(), max_nfev=200000)
    A = sol.x.reshape(3, 3).astype(np.float64)

    # The direct-xy barycentric model is invariant to a uniform scale applied to
    # all coefficients.  Normalize the matrix so debug ``effective_r/g/b``
    # values stay human-readable and comparable across builds.
    diag_scale = float(np.mean(np.abs(np.diag(A))))
    if np.isfinite(diag_scale) and diag_scale > 1e-12:
        A = A / diag_scale
    return A


def _target_xyz_from_effective_rgb_model(
    rgb_flat: np.ndarray,
    raw_rgb_basis: np.ndarray,
    target_rgb_basis: np.ndarray,
    y_scale: float,
    neutral_weight: np.ndarray,
    sample_scale: float,
    target_transform_matrix: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Construct target XYZ for the family solver.

    The LUT verifier computes expected chromaticity directly from the
    reference-white-balanced RGB basis stored in ``delaunay_lut_summary.json``:

        xyz_expected = target_rgb_basis @ [R, G, B]
        exp_xy       = xy(xyz_expected)

    The solver must aim at that same chromaticity.  Earlier iterations routed
    non-neutral colours through a fitted measured-primary xy/barycentric model,
    while the verifier still used ``target_rgb_basis``.  That made the solver
    and verifier disagree by huge amounts for HSV ramps, skin tones, warm/cool
    whites, and dual-channel colours.

    This function now uses the verifier-compatible basis for *all* RGB target
    chromaticity.  For non-neutral colours, luminance is still taken from the
    raw physical RGB basis so channel resolution/usable Y are not globally
    white-balance-scaled.  Equal/nearly-equal RGB values still route through
    ``xyz_neutral`` so the dedicated neutral solver can use the W-heavy measured
    neutral path.

    ``target_transform_matrix`` is intentionally ignored and kept only for API
    compatibility with older worker/debug code.  No gamma/transfer curve is
    applied here.

    Returns ``(xyz_target, xyz_colour, xyz_raw, xyz_neutral)`` for probe
    diagnostics.
    """
    rgb_flat = np.asarray(rgb_flat, dtype=np.float64)

    # Physical/raw RGB luminance basis.  This supplies Y for coloured targets.
    xyz_raw = rgb_flat @ raw_rgb_basis.T

    # Verifier-compatible chromaticity basis.  This is what
    # host_calibration_gui._verifier_expected_xy() derives from lut_summary.
    xyz_chroma = rgb_flat @ target_rgb_basis.T

    # Neutral path uses the same target basis, scaled to the measured W/D65
    # neutral endpoint selected by compute_y_scale().  xy is unchanged by this
    # scale, but Y is what the neutral W solver should chase.
    xyz_neutral = xyz_chroma * float(y_scale)

    # Non-neutral colour Y should not be globally scaled to the full neutral/W
    # endpoint.  Exact primaries and duals need raw physical edge Y so the solver
    # does not chase impossible brightness by inflating the stronger diode.  For
    # mixed min(rgb)>0 rows, blend toward common-neutral + residual Y so W-axis
    # candidates have enough luminance authority without pinning dual-channel
    # edges to a white-scaled target.
    target_Y_colour = np.maximum(xyz_raw[:, 1].copy(), 0.0)
    if len(rgb_flat):
        _ch_max = np.maximum(np.max(rgb_flat, axis=1), 1.0)
        _common = np.minimum.reduce([rgb_flat[:, 0], rgb_flat[:, 1], rgb_flat[:, 2]])
        _common_frac = np.clip(_common / _ch_max, 0.0, 1.0)
        _all3 = (rgb_flat[:, 0] > 0.0) & (rgb_flat[:, 1] > 0.0) & (rgb_flat[:, 2] > 0.0) & (_common > 0.0)
        _common_y_gate = np.clip((_common_frac - 0.035) / 0.285, 0.0, 1.0) * _all3.astype(np.float64)
        if np.any(_common_y_gate > 0.0):
            _common_rgb = np.column_stack([_common, _common, _common])
            _resid_rgb = np.maximum(rgb_flat - _common[:, None], 0.0)
            _common_neutral_Y = (_common_rgb @ target_rgb_basis.T)[:, 1] * float(y_scale)
            _resid_raw_Y = (_resid_rgb @ raw_rgb_basis.T)[:, 1]
            _mixed_Y = np.maximum(_resid_raw_Y + _common_neutral_Y, 0.0)
            target_Y_colour = target_Y_colour * (1.0 - _common_y_gate) + _mixed_Y * _common_y_gate

    xyz_colour = _xyY_from_chroma_and_Y(xyz_chroma, target_Y_colour)

    bad_colour = (~np.isfinite(xyz_colour).all(axis=1)) | (xyz_colour[:, 1] <= 0.0)
    if np.any(bad_colour):
        xyz_colour[bad_colour] = xyz_raw[bad_colour]

    nw = np.asarray(neutral_weight, dtype=np.float64)
    xyz_target = xyz_colour * (1.0 - nw[:, None]) + xyz_neutral * nw[:, None]
    return xyz_target, xyz_colour, xyz_raw, xyz_neutral

# ---------------------------------------------------------------------------
# Multi-family target XYZ computation
# ---------------------------------------------------------------------------

# Family definitions: (family_key, rgb_mask, uses_white)
# rgb_mask: which of the 3 input channels are active in this family (R=bit0, G=bit1, B=bit2)
# This controls which input drives map to which family basis columns.
_FAMILY_DEFS: list[tuple[str, int, bool]] = [
    # single channels
    ("r",    0b001, False),
    ("g",    0b010, False),
    ("b",    0b100, False),
    ("w",    0b000, True),    # never selected for RGB query — W=0 always
    # dual RGB channels
    ("rg",   0b011, False),
    ("rb",   0b101, False),
    ("gb",   0b110, False),
    # single RGB + white
    ("rw",   0b001, True),
    ("gw",   0b010, True),
    ("bw",   0b100, True),
    # triple / full
    ("rgb",  0b111, False),
    ("rgw",  0b011, True),
    ("rbw",  0b101, True),
    ("gbw",  0b110, True),
    ("rgbw", 0b111, True),
]


def build_xyz_from_families_vectorised(
    rgb_flat: np.ndarray,
    family_bases: dict[str, np.ndarray],
    reference_white: ReferenceWhite,
    sample_scale: float,
    wb_scales: np.ndarray,
) -> np.ndarray:
    """Compute per-node target XYZ using multi-family measured bases.

    For each node (row in rgb_flat):

    1. Identify the dominant channel set (which of R, G, B are non-zero).
    2. For each active family that spans that channel set (and optionally W):
       a. Compute ``xyz_no_w`` using the RGB channels of the family basis
          (with white-balance scales applied, matching target_rgb_basis).
       b. Add a W contribution scaled so that the family's combined output
          lands at the reference-white chromaticity.  W is added only when the
          family includes W and the reference-white mode is active.
    3. Among all matching families, pick the one that produces the **maximum Y**
       (highest RGBW utilisation for that input).

    Parameters
    ----------
    rgb_flat     : (N, 3) — input R/G/B drives in [0, sample_scale]
    family_bases : output of ``fit_basis_from_all_families``
    reference_white : target white for chromaticity correction
    sample_scale : 65535.0
    wb_scales    : (3,) white-balance per-channel scales (from build_target_rgb_basis)

    Returns
    -------
    xyz_flat : (N, 3) target XYZ per node
    """
    N = len(rgb_flat)
    out_xyz = np.zeros((N, 3), dtype=np.float64)

    # Process nodes grouped by their non-zero RGB channel mask
    # rgb_mask values: bit0=R, bit1=G, bit2=B
    r_nz = rgb_flat[:, 0] > 0
    g_nz = rgb_flat[:, 1] > 0
    b_nz = rgb_flat[:, 2] > 0
    masks = (r_nz.astype(np.int32)
             | (g_nz.astype(np.int32) << 1)
             | (b_nz.astype(np.int32) << 2))   # (N,) int 0..7

    for mask_val in range(0, 8):
        node_idx = np.where(masks == mask_val)[0]
        if len(node_idx) == 0:
            continue

        drives_sub = rgb_flat[node_idx]  # (M, 3)

        # If all channels zero → black, leave xyz=(0,0,0)
        if mask_val == 0:
            continue

        # Collect candidate XYZ from each family that covers this mask
        best_xyz = np.zeros((len(node_idx), 3), dtype=np.float64)
        best_Y   = np.full(len(node_idx), -1.0, dtype=np.float64)

        for fk, fam_rgb_mask, fam_uses_w in _FAMILY_DEFS:
            # Family must cover ALL active channels of this node (no less)
            if (fam_rgb_mask & mask_val) != mask_val:
                continue
            # Skip W-containing families here: forcing ref-white chromaticity for
            # coloured inputs collapses all targets toward neutral white, which
            # causes every simplex lookup to land in the same bright-white region
            # of the hull and destroys utilisation.  Grey/neutral inputs get their
            # W-boost through the neutral-blend path in _solve_r_slice instead.
            if fam_uses_w:
                continue
            if fk not in family_bases:
                continue

            basis = family_bases[fk]  # (3, n_ch)
            # Determine column order: family key letters, channels: r=0,g=1,b=2,w=3
            fk_ch = [{"r": 0, "g": 1, "b": 2, "w": 3}[c] for c in fk]
            n_ch = len(fk_ch)

            # Build drive matrix for this family (M, n_ch) — RGB channels only
            drive_cols = [drives_sub[:, ci] * wb_scales[ci] for ci in fk_ch]
            drive_mat = np.column_stack(drive_cols)  # (M, n_ch)
            xyz_candidate = drive_mat @ basis.T       # (M, 3)

            # Keep whichever family gives higher Y for each node
            better = xyz_candidate[:, 1] > best_Y
            best_Y   = np.where(better, xyz_candidate[:, 1], best_Y)
            best_xyz = np.where(better[:, None], xyz_candidate, best_xyz)

        out_xyz[node_idx] = best_xyz

    return out_xyz


# ---------------------------------------------------------------------------
# Map family key characters to RGBW column indices
_CH_IDX: dict[str, int] = {"r": 0, "g": 1, "b": 2, "w": 3}

# Dual-channel pair tables used for calibrated target construction
# (R=bit 1, G=bit 2, B=bit 4)
_DUAL_PAIR_CHANNELS: dict[str, tuple[int, int]] = {
    "rg": (0, 1),   # R and G channel indices in 4-ch RGBW array
    "rb": (0, 2),   # R and B
    "gb": (1, 2),   # G and B
}
_DUAL_PAIR_MASKS: dict[str, int] = {
    "rg": 1 | 2,    # 3  — exactly R+G active
    "rb": 1 | 4,    # 5  — exactly R+B active
    "gb": 2 | 4,    # 6  — exactly G+B active
}

# W-axis sub-gamut preference for non-neutral min(rgb)>0 rows.  The shared
# RGB component is solved by the same measured W-axis mechanics as neutrals,
# but the endpoint/residual family is tried in the residual+W sub-gamut before
# full RGBW is allowed.  This keeps W tracking common while letting measured
# RGW/RBW/GBW/RW/GW/BW captures provide the residual ratios.
_W_AXIS_SUBGAMUT_FAMILIES: tuple[str, ...] = ("rw", "gw", "bw", "rgw", "rbw", "gbw")
_W_AXIS_RESIDUAL_FAMILY_BY_MASK: dict[int, str] = {
    1: "rw",       # residual R + W
    2: "gw",       # residual G + W
    4: "bw",       # residual B + W
    1 | 2: "rgw",  # residual R/G + W
    1 | 4: "rbw",  # residual R/B + W
    2 | 4: "gbw",  # residual G/B + W
}
_FAMILY_INDEX_BY_NAME: dict[str, int] = {name: i for i, (name, _m, _w) in enumerate(_FAMILY_DEFS)}
_FAMILY_RGB_MASK_BY_NAME: dict[str, int] = {name: mask for name, mask, _w in _FAMILY_DEFS}


def _mixed_common_primary_w_families(
    input_rgb: np.ndarray,
    sample_scale: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (primary_family_name, residual_mask) for min(rgb)>0 W-axis rows.

    The residual mask is computed after removing common=min(R,G,B).  It maps
    directly to the W sub-gamut that should be attempted first:
    R→RW, G→GW, B→BW, RG→RGW, RB→RBW, GB→GBW.  Degenerate rows fall back to
    RGBW by leaving the primary family empty.
    """
    rgb = np.asarray(input_rgb, dtype=np.float64)
    if rgb.ndim != 2 or rgb.shape[1] != 3:
        return np.full(0, "", dtype=object), np.zeros(0, dtype=np.int32)
    common = np.minimum.reduce([rgb[:, 0], rgb[:, 1], rgb[:, 2]])
    residual = np.maximum(rgb - common[:, None], 0.0)
    eps = max(1.0, 1e-5 * float(sample_scale))
    mask = (
        (residual[:, 0] > eps).astype(np.int32) * 1 |
        (residual[:, 1] > eps).astype(np.int32) * 2 |
        (residual[:, 2] > eps).astype(np.int32) * 4
    )
    primary = np.array([_W_AXIS_RESIDUAL_FAMILY_BY_MASK.get(int(m), "") for m in mask], dtype=object)
    return primary, mask



# ---------------------------------------------------------------------------
# Measured-capture candidate solver (physical patch-capture source of truth)

def _family_mask_from_rgbw_vectorised(rgbw: np.ndarray, sample_scale: float) -> np.ndarray:
    vals = np.asarray(rgbw, dtype=np.float64)
    eps = max(1.0, 1e-5 * float(sample_scale))
    return ((vals[:, 0] > eps).astype(np.int32) * 1 |
            (vals[:, 1] > eps).astype(np.int32) * 2 |
            (vals[:, 2] > eps).astype(np.int32) * 4 |
            (vals[:, 3] > eps).astype(np.int32) * 8)


def _expected_drive_prior_for_rgb(
    input_rgb: np.ndarray,
    raw_rgb_basis: np.ndarray | None,
    sample_scale: float,
) -> np.ndarray:
    """Physical drive-ratio prior used only as a weak tie-break.

    The measured candidate solver is colorimetry-first.  This prior encodes a
    display-independent fact the patch captures confirm: for exact duals, the
    weaker-emitter channel can remain high while stronger emitters usually need
    to be reduced; for mixed min(rgb)>0 rows, W carries the common component and
    RGB residuals are free to follow measured chromaticity.
    """
    rgb = np.asarray(input_rgb, dtype=np.float64).reshape(3)
    out = np.zeros(4, dtype=np.float64)
    imask = int(rgb[0] > 0.0) | (int(rgb[1] > 0.0) << 1) | (int(rgb[2] > 0.0) << 2)
    if raw_rgb_basis is not None:
        y_slopes = np.maximum(np.asarray(raw_rgb_basis, dtype=np.float64)[1, :3], 1e-12)
    else:
        y_slopes = np.ones(3, dtype=np.float64)

    if imask in (1, 2, 4):
        out[:3] = rgb
        return np.clip(out, 0.0, sample_scale)

    if imask in (3, 5, 6):
        active = rgb > 0.0
        weak = float(np.min(y_slopes[active])) if np.any(active) else 1.0
        out[:3] = np.where(active, rgb * weak / y_slopes, 0.0)
        return np.clip(out, 0.0, sample_scale)

    if imask == 7:
        common = float(np.min(rgb))
        residual = np.maximum(rgb - common, 0.0)
        if np.max(rgb) <= np.min(rgb) + max(1.0, 1e-5 * sample_scale):
            # Neutral residual direction: W dominant, with modest R/B correction
            # and little G because the measured W channel is green/yellow leaning.
            out[:] = (0.12 * common, 0.03 * common, 0.25 * common, common)
            return np.clip(out, 0.0, sample_scale)
        out[3] = common
        active = residual > 1.0
        if np.any(active):
            weak = float(np.min(y_slopes[active]))
            out[:3] = np.where(active, residual * weak / y_slopes, 0.0)
        return np.clip(out, 0.0, sample_scale)

    return out


def _allowed_measured_family_masks_for_input(input_rgb: np.ndarray, sample_scale: float) -> list[int]:
    rgb = np.asarray(input_rgb, dtype=np.float64).reshape(3)
    eps = max(1.0, 1e-5 * float(sample_scale))
    imask = int(rgb[0] > eps) | (int(rgb[1] > eps) << 1) | (int(rgb[2] > eps) << 2)
    if imask == 0:
        return [0]
    if imask in (1, 2, 4):
        return [imask]
    if imask in (3, 5, 6):
        # Exact duals should stay in measured pair-family space.  This preserves
        # the weak-vs-strong diode ratio learned from captures instead of letting
        # W/RGBW leak into saturated pair colours.
        return [imask]
    if imask == 7 and float(np.max(rgb) - np.min(rgb)) <= eps:
        return [15]

    common = float(np.min(rgb))
    residual = np.maximum(rgb - common, 0.0)
    rmask = int(residual[0] > eps) | (int(residual[1] > eps) << 1) | (int(residual[2] > eps) << 2)
    primary = _W_AXIS_RESIDUAL_FAMILY_BY_MASK.get(rmask, "rgbw")
    primary_mask = {"rw": 9, "gw": 10, "bw": 12, "rgw": 11, "rbw": 13, "gbw": 14, "rgbw": 15}.get(primary, 15)
    allowed: list[int] = [primary_mask]
    # Legal residual subsets before full RGBW fallback.  This keeps the W-axis
    # enabled while preventing off-residual/common channels from winning first.
    if primary_mask == 13:      # RBW
        allowed += [9, 12]
    elif primary_mask == 11:    # RGW
        allowed += [9, 10]
    elif primary_mask == 14:    # GBW
        allowed += [10, 12]
    allowed += [15, 7]          # emergency full RGBW / RGB fallback
    return list(dict.fromkeys(allowed))


def _measured_candidate_solver_vectorised(
    rgb_flat: np.ndarray,
    xyz_targets: np.ndarray,
    family_xyz: dict,
    family_rgbw: dict,
    white_xyz_ref: np.ndarray,
    sample_scale: float,
    input_masks: np.ndarray,
    neutral_weights: np.ndarray,
    raw_rgb_basis: np.ndarray | None = None,
    top_k: int = 768,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Select RGBW from physical patch captures by measured forward accuracy.

    This is a solver candidate source, not a verifier dictionary.  It searches
    the actual patch-capture point cloud in legal topology/family pools, scales
    measured RGBW/XYZ uniformly, then scores candidates by predicted Lab dE to
    the target, Y error, topology legality, and a weak physical drive prior.
    """
    rgb = np.asarray(rgb_flat, dtype=np.float64)
    target = np.asarray(xyz_targets, dtype=np.float64)
    N = len(rgb)
    out_rgbw = np.zeros((N, 4), dtype=np.float64)
    out_fam = np.full(N, -1, dtype=np.int32)
    out_ok = np.zeros(N, dtype=bool)
    out_de = np.full(N, np.inf, dtype=np.float64)
    out_score = np.full(N, np.inf, dtype=np.float64)
    if N == 0:
        return out_rgbw, out_fam, out_ok, out_de, out_score

    # Flatten family dictionaries into family-mask pools.  Duplicates are fine;
    # all captures represent physical measured states.
    mask_to_xyz: dict[int, list[np.ndarray]] = {}
    mask_to_rgbw: dict[int, list[np.ndarray]] = {}
    for fk, _xyz in family_xyz.items():
        _rgbw = family_rgbw.get(fk)
        if _xyz is None or _rgbw is None or len(_xyz) == 0:
            continue
        fam_mask = 0
        for ch in fk:
            fam_mask |= {"r": 1, "g": 2, "b": 4, "w": 8}.get(ch, 0)
        mask_to_xyz.setdefault(fam_mask, []).append(np.asarray(_xyz, dtype=np.float64))
        mask_to_rgbw.setdefault(fam_mask, []).append(np.asarray(_rgbw, dtype=np.float64))
    pool_xyz = {m: np.vstack(v) for m, v in mask_to_xyz.items()}
    pool_rgbw = {m: np.vstack(mask_to_rgbw[m]) for m in mask_to_xyz}

    t_sum = np.maximum(target.sum(axis=1), 1e-9)
    t_xy = np.column_stack([target[:, 0] / t_sum, target[:, 1] / t_sum])
    t_Y = np.maximum(target[:, 1], 1e-9)
    t_lab = _xyz_to_lab_vectorised(np.maximum(target, 0.0), white_xyz_ref)

    scale_names_count = 8
    top = max(16, int(top_k))
    fam_name_to_index = _FAMILY_INDEX_BY_NAME

    for i in range(N):
        if np.sum(rgb[i]) <= 0.0:
            out_ok[i] = True
            out_de[i] = 0.0
            out_score[i] = 0.0
            continue
        # Keep exact neutrals on the dedicated neutral-axis solver.  The measured
        # candidate path is used for non-neutral topology/ranking failures.
        if neutral_weights[i] >= 0.98:
            continue

        allowed = _allowed_measured_family_masks_for_input(rgb[i], sample_scale)
        cand_xyz_list = [pool_xyz[m] for m in allowed if m in pool_xyz]
        cand_rgbw_list = [pool_rgbw[m] for m in allowed if m in pool_rgbw]
        cand_mask_list = [np.full(len(pool_rgbw[m]), m, dtype=np.int32) for m in allowed if m in pool_rgbw]
        if not cand_xyz_list:
            continue
        cxyz_all = np.vstack(cand_xyz_list)
        crgbw_all = np.vstack(cand_rgbw_list)
        cmask_all = np.concatenate(cand_mask_list)
        c_sum = np.maximum(cxyz_all.sum(axis=1), 1e-9)
        c_xy = np.column_stack([cxyz_all[:, 0] / c_sum, cxyz_all[:, 1] / c_sum])
        xy_dist = np.hypot(c_xy[:, 0] - t_xy[i, 0], c_xy[:, 1] - t_xy[i, 1])
        k_eff = min(top, len(cxyz_all))
        if k_eff < len(cxyz_all):
            nn = np.argpartition(xy_dist, k_eff - 1)[:k_eff]
        else:
            nn = np.arange(len(cxyz_all))
        base_xyz = cxyz_all[nn]
        base_rgbw = crgbw_all[nn]
        base_mask = cmask_all[nn]
        base_Y = np.maximum(base_xyz[:, 1], 1e-9)
        inmax = max(float(np.max(rgb[i])), 1.0)
        common = max(float(np.min(rgb[i])), 1.0)
        base_rgbmax = np.maximum(np.max(base_rgbw[:, :3], axis=1), 1.0)
        base_total = np.maximum(np.sum(base_rgbw[:, :3], axis=1) + base_rgbw[:, 3], 1.0)
        sy = t_Y[i] / base_Y
        sm = inmax / base_rgbmax
        ss = (float(np.sum(rgb[i])) + common) / base_total
        scales = [np.ones(len(nn)), sy, sm, ss, np.sqrt(np.maximum(sy, 1e-9) * np.maximum(sm, 1e-9))]
        if input_masks[i] == 7:
            sw = common / np.maximum(base_rgbw[:, 3], 1.0)
            scales += [sw,
                       np.sqrt(np.maximum(sy, 1e-9) * np.maximum(sw, 1e-9)),
                       np.sqrt(np.maximum(sm, 1e-9) * np.maximum(sw, 1e-9))]
        S = np.stack([np.clip(s, 0.0, sample_scale / np.maximum(np.max(base_rgbw, axis=1), 1.0)) for s in scales], axis=1)
        cand_rgbw = base_rgbw[:, None, :] * S[:, :, None]
        cand_xyz = base_xyz[:, None, :] * S[:, :, None]
        cand_lab = _xyz_to_lab_vectorised(cand_xyz.reshape(-1, 3), white_xyz_ref).reshape(cand_xyz.shape)
        de = np.linalg.norm(cand_lab - t_lab[i], axis=2)
        y_log = np.abs(np.log(np.maximum(cand_xyz[:, :, 1], 1e-9) / t_Y[i]))
        expected = _expected_drive_prior_for_rgb(rgb[i], raw_rgb_basis, sample_scale)
        exp_norm = 4096.0 + 0.10 * np.maximum(cand_rgbw, expected[None, None, :])
        exp_dist = np.linalg.norm((cand_rgbw - expected[None, None, :]) / exp_norm, axis=2)

        score = de + 0.05 * y_log + 0.18 * exp_dist
        imask = int(input_masks[i])
        if imask in (3, 5, 6):
            active = rgb[i] > 0.0
            off = ~active
            if np.any(off):
                score += np.sum((cand_rgbw[:, :, :3][:, :, off] / sample_scale) ** 2, axis=2) * 2000.0
            score += (cand_rgbw[:, :, 3] / sample_scale) ** 2 * 2000.0
        elif imask == 7:
            cm = float(np.min(rgb[i]))
            residual = np.maximum(rgb[i] - cm, 0.0)
            forbidden = residual <= max(1.0, 1e-5 * sample_scale)
            if np.any(forbidden) and cm > 1.0:
                leak = np.max(cand_rgbw[:, :, :3][:, :, forbidden], axis=2)
                score += (leak / max(cm, 1.0)) ** 2 * 80.0
            score += np.abs(np.log((cand_rgbw[:, :, 3] + 1.0) / (cm + 1.0))) * 0.02

        flat_best = int(np.argmin(score))
        bi, si = np.unravel_index(flat_best, score.shape)
        out_rgbw[i] = cand_rgbw[bi, si]
        out_de[i] = float(de[bi, si])
        out_score[i] = float(score[bi, si])
        fam_mask = int(base_mask[bi])
        # Convert bitmask to builder family index.
        fam_name = {1: "r", 2: "g", 4: "b", 8: "w", 3: "rg", 5: "rb", 6: "gb",
                    9: "rw", 10: "gw", 12: "bw", 7: "rgb", 11: "rgw", 13: "rbw",
                    14: "gbw", 15: "rgbw"}.get(fam_mask, "rgbw")
        out_fam[i] = int(fam_name_to_index.get(fam_name, 14))
        # Candidate is usable when it is genuinely close in measured forward-model
        # space.  Rows above the gate keep the existing family/W-axis solver result.
        out_ok[i] = np.isfinite(out_de[i])
    return np.clip(out_rgbw, 0.0, sample_scale), out_fam, out_ok, out_de, out_score

# ---------------------------------------------------------------------------
# Item 3: Full CIELAB (L*, a*, b*) vectorised — L* included for luminance gate

def _xyz_to_lab_vectorised(
    xyz: np.ndarray,       # (N, 3)
    white_xyz: np.ndarray, # (3,) or (N, 3)
) -> np.ndarray:           # (N, 3) — L*, a*, b*
    """Full vectorised CIELAB.

    ``white_xyz`` may be either one global reference white ``(3,)`` or a
    per-row axis/reference white ``(N, 3)``.  The per-row form is important for
    target-aware W-axis scoring: exact neutrals use the configured reference
    white, while mixed ``min(rgb)>0`` rows can score candidates relative to the
    row's own target xy instead of silently using D65/reference white for every
    Lab comparison.
    """
    xyz = np.asarray(xyz, dtype=np.float64)
    white = np.asarray(white_xyz, dtype=np.float64)
    if white.ndim == 1:
        white_use = white[None, :]
    elif white.shape == xyz.shape:
        white_use = white
    else:
        # Keep older callers safe if a malformed white array arrives.
        white_use = np.reshape(white, (-1, 3))[0][None, :]

    delta  = 6.0 / 29.0
    delta3 = delta ** 3
    coeff  = 1.0 / (3.0 * delta * delta)
    xyz_n  = xyz / np.maximum(white_use, 1e-12)   # (N, 3)

    def _f(t: np.ndarray) -> np.ndarray:
        return np.where(t > delta3, np.cbrt(t), coeff * t + (4.0 / 29.0))

    fx, fy, fz = _f(xyz_n[:, 0]), _f(xyz_n[:, 1]), _f(xyz_n[:, 2])
    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    return np.stack([L, a, b], axis=1)  # (N, 3)


def _axis_reference_white_from_targets(
    xyz_targets: np.ndarray,
    white_xyz_ref: np.ndarray,
    target_xy: np.ndarray | None = None,
) -> np.ndarray:
    """Build a per-row Lab reference/axis white for target-aware W-axis scoring.

    For exact neutral rows the target xy is the configured reference white, so
    this returns the same chromaticity as ``white_xyz_ref`` and preserves the
    previous neutral-axis behaviour.  For mixed RGB rows, the reference
    chromaticity follows the row's passed-in target xyY, while reference Y stays
    at the configured white Y so L* remains on the same scale as the rest of the
    solver.
    """
    target = np.asarray(xyz_targets, dtype=np.float64)
    fallback = np.asarray(white_xyz_ref, dtype=np.float64).reshape(3)
    out = np.repeat(fallback[None, :], len(target), axis=0)

    if target_xy is not None:
        xy = np.asarray(target_xy, dtype=np.float64)
        if xy.shape == (len(target), 2):
            x = xy[:, 0]
            y = xy[:, 1]
        else:
            xy = None
    else:
        xy = None

    if xy is None:
        denom = np.maximum(target.sum(axis=1), 1e-9)
        x = target[:, 0] / denom
        y = target[:, 1] / denom

    ref_Y = max(float(fallback[1]), 1e-9)
    valid = np.isfinite(x) & np.isfinite(y) & (y > 1e-9)
    out[valid, 0] = (x[valid] * ref_Y) / y[valid]
    out[valid, 1] = ref_Y
    out[valid, 2] = ((1.0 - x[valid] - y[valid]) * ref_Y) / y[valid]
    return out


# ---------------------------------------------------------------------------
# Item 2: 3^n bounded active-set NNLS — handles saturate-at-max channel states

def _bounded_active_set_vectorised(
    basis: np.ndarray,        # (3, n_ch)
    xyz_targets: np.ndarray,  # (N, 3)
    upper_bound: float,
) -> np.ndarray:              # (N, n_ch) drives
    """Bounded active-set solve enumerating 3^n channel states per family.

    Each channel is in one of three states:
      0 — fixed at zero        (excluded from this family combination)
      1 — free (LSQ-solved)    (included, within bounds)
      2 — saturated at upper_bound  (NEW — the missing state from 2^n)

    The saturation state (2) is what was missing in the old 2^n subset
    enumeration.  Without it, a target that needs R=65535 would be rejected
    as infeasible, letting W-only or other wrong families win.

    For n_ch=1: 3 cases.   n_ch=2: 9.   n_ch=3: 27.   n_ch=4: 81.
    All are fast to enumerate.
    """
    N, n_ch = len(xyz_targets), basis.shape[1]
    best_drives = np.zeros((N, n_ch), dtype=np.float64)
    best_err    = np.full(N, np.inf, dtype=np.float64)

    for state in itertools.product(range(3), repeat=n_ch):
        # Channels fixed at upper_bound contribute a constant XYZ offset.
        fixed_xyz = np.zeros(3, dtype=np.float64)
        free_idx: list[int] = []
        sat_idx:  list[int] = []
        for ch_i, s in enumerate(state):
            if s == 2:
                fixed_xyz += basis[:, ch_i] * upper_bound
                sat_idx.append(ch_i)
            elif s == 1:
                free_idx.append(ch_i)
            # s == 0: contributes nothing

        # Residual after removing fixed-channel contribution
        residual = xyz_targets - fixed_xyz[None, :]  # (N, 3)

        if not free_idx:
            # All channels fixed (saturated or zero) — compute error directly.
            drives_cand = np.zeros((N, n_ch), dtype=np.float64)
            for ch_i in sat_idx:
                drives_cand[:, ch_i] = upper_bound
            xyz_ach = (basis @ drives_cand.T).T
            err = np.linalg.norm(xyz_targets - xyz_ach, axis=1)
            update = err < best_err
            best_drives = np.where(update[:, None], drives_cand, best_drives)
            best_err    = np.where(update, err, best_err)
            continue

        # Least-squares solve for free channels
        A_free   = basis[:, free_idx]                          # (3, n_free)
        pinv_A   = np.linalg.pinv(A_free)                     # (n_free, 3)
        X_free   = (pinv_A @ residual.T).T                    # (N, n_free)

        # Accept only nodes where free channels stay within [0, upper_bound]
        feasible = np.all(
            (X_free >= -1e-8) & (X_free <= upper_bound + 1e-8), axis=1
        )
        if not np.any(feasible):
            continue

        X_free_cl = np.clip(X_free, 0.0, upper_bound)
        drives_cand = np.zeros((N, n_ch), dtype=np.float64)
        for ch_i in sat_idx:
            drives_cand[:, ch_i] = upper_bound
        for local_i, ch_i in enumerate(free_idx):
            drives_cand[:, ch_i] = X_free_cl[:, local_i]

        xyz_ach = (basis @ drives_cand.T).T
        err     = np.linalg.norm(xyz_targets - xyz_ach, axis=1)

        update      = feasible & (err < best_err)
        best_drives = np.where(update[:, None], drives_cand, best_drives)
        best_err    = np.where(update, err, best_err)

    return best_drives


# ---------------------------------------------------------------------------
# Items 4 + 5: Per-family Delaunay hull lookup (physical forward model)

def build_family_capture_sets(
    xyz_points: np.ndarray,   # (N, 3)  — all deduplicated capture XYZ
    rgbw_points: np.ndarray,  # (N, 4)  — corresponding RGBW drives
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Partition the capture cloud by emitter family.

    A capture belongs to family 'rg' when R>0, G>0, B==0, W==0, etc.
    Exact-zero thresholding is used since drive values are integer counts.

    Returns dict mapping family_key → (xyz_sub, rgbw_sub).  Families with
    fewer than 5 captures (insufficient for 3-D Delaunay) are still included;
    the worker-side builder falls back to cKDTree for those.
    """
    R, G, B, W = (rgbw_points[:, i] for i in range(4))
    sets: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for fk, rgb_mask, uses_w in _FAMILY_DEFS:
        r_on = bool(rgb_mask & 0b001)
        g_on = bool(rgb_mask & 0b010)
        b_on = bool(rgb_mask & 0b100)
        m = np.ones(len(xyz_points), dtype=bool)
        m &= (R > 0) if r_on else (R == 0)
        m &= (G > 0) if g_on else (G == 0)
        m &= (B > 0) if b_on else (B == 0)
        m &= (W > 0) if uses_w else (W == 0)
        if m.any():
            sets[fk] = (xyz_points[m], rgbw_points[m])
    return sets


def _bary_interp_vectorised(
    tri: Delaunay,
    xyz_batch: np.ndarray,     # (N, 3)
    family_xyz:  np.ndarray,   # (M, 3)
    family_rgbw: np.ndarray,   # (M, 4)
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised barycentric interpolation of RGBW and XYZ for in-hull nodes.

    Returns (rgbw_out, xyz_ach, in_hull_mask) where in_hull_mask[i] is True
    when xyz_batch[i] was inside the triangulation.  Out-of-hull entries
    are left as zeros and handled by the caller.
    """
    N = len(xyz_batch)
    simplices = tri.find_simplex(xyz_batch)   # (N,) — -1 if out of hull
    in_hull   = simplices >= 0                # (N,) bool
    sims_ih   = simplices[in_hull]            # (n_ih,)

    rgbw_out = np.zeros((N, 4), dtype=np.float64)
    xyz_ach  = np.zeros((N, 3), dtype=np.float64)

    if not in_hull.any():
        return rgbw_out, xyz_ach, in_hull

    # tri.transform: (n_simplices, ndim+1, ndim) = (n_simplices, 4, 3)
    T       = tri.transform[sims_ih]            # (n_ih, 4, 3)
    pts_ih  = xyz_batch[in_hull]                # (n_ih, 3)
    delta   = pts_ih - T[:, 3, :]              # (n_ih, 3)
    b3      = np.einsum("ijk,ik->ij", T[:, :3, :], delta)  # (n_ih, 3)
    bary    = np.concatenate(
        [b3, 1.0 - b3.sum(axis=1, keepdims=True)], axis=1
    )                                           # (n_ih, 4)
    verts   = tri.simplices[sims_ih]            # (n_ih, 4)

    rgbw_out[in_hull] = np.einsum("ij,ijk->ik", bary, family_rgbw[verts])  # (n_ih, 4)
    xyz_ach[in_hull]  = np.einsum("ij,ijk->ik", bary, family_xyz[verts])   # (n_ih, 3)
    return rgbw_out, xyz_ach, in_hull


def _knn_interp_vectorised(
    tree: cKDTree,
    xyz_batch: np.ndarray,     # (N, 3)
    family_xyz:  np.ndarray,   # (M, 3)
    family_rgbw: np.ndarray,   # (M, 4)
    k: int = 6,
) -> tuple[np.ndarray, np.ndarray]:
    """Inverse-distance-weighted KNN interpolation as Delaunay fallback.

    Returns (rgbw_out, xyz_ach) for the given batch.
    """
    k_eff = min(k, len(family_xyz))
    dists, idx = tree.query(xyz_batch, k=k_eff)   # (N, k_eff)
    if k_eff == 1:
        dists = dists[:, None]
        idx   = idx[:, None]
    weights = 1.0 / np.maximum(dists, 1e-12)       # (N, k_eff)
    weights /= weights.sum(axis=1, keepdims=True)
    rgbw_out = np.einsum("ij,ijk->ik", weights, family_rgbw[idx])  # (N, 4)
    xyz_ach  = np.einsum("ij,ijk->ik", weights, family_xyz[idx])   # (N, 3)
    return rgbw_out, xyz_ach


def _target_space_key(xyz: np.ndarray, white_xyz_ref: np.ndarray) -> np.ndarray:
    """Metric key for target-space measured-neighbour lookup.

    The key intentionally emphasizes chromaticity/perceptual a*b* more than Y.
    This prevents saturated dual-channel targets from selecting equal-drive
    high-Y captures when the chromatically correct measured state is an
    unequal-ratio lower-Y capture.
    """
    xyz = np.maximum(np.asarray(xyz, dtype=np.float64), 0.0)
    s = np.maximum(xyz.sum(axis=1), 1e-9)
    x = xyz[:, 0] / s
    y = xyz[:, 1] / s
    lab = _xyz_to_lab_vectorised(xyz, white_xyz_ref)
    logY = np.log(np.maximum(xyz[:, 1], 1e-6))
    return np.column_stack([
        x * 120.0,
        y * 120.0,
        lab[:, 0] * 0.03,
        lab[:, 1],
        lab[:, 2],
        logY * 0.20,
    ])


def _knn_constrained_bary_vectorised(
    tree: "cKDTree",
    xyz_batch: np.ndarray,      # (N, 3)
    family_xyz: np.ndarray,     # (M, 3)
    family_rgbw: np.ndarray,    # (M, 4)
    k: int = 5,
) -> tuple[np.ndarray, np.ndarray]:
    """KNN-anchored constrained barycentric fit for out-of-hull nodes.

    For each target, finds k nearest measured captures then calls
    fit_constrained_bary() to find the best convex combination, which is more
    accurate than raw IDW because it solves the closest-point-in-convex-hull
    problem rather than simple distance-weighted averaging.
    """
    k_eff = min(k, len(family_xyz))
    _, idx = tree.query(xyz_batch, k=k_eff)   # (N, k_eff)
    if k_eff == 1:
        idx = idx[:, None]
    N = len(xyz_batch)
    rgbw_out = np.zeros((N, 4), dtype=np.float64)
    xyz_ach  = np.zeros((N, 3), dtype=np.float64)
    for i in range(N):
        anchors_xyz  = family_xyz[idx[i]]
        anchors_rgbw = family_rgbw[idx[i]]
        r_i, w_i = fit_constrained_bary(anchors_xyz, xyz_batch[i], anchors_rgbw)
        rgbw_out[i] = r_i
        xyz_ach[i]  = w_i @ anchors_xyz
    return rgbw_out, xyz_ach



def _knn_constrained_bary_keyed_vectorised(
    key_tree: "cKDTree",
    xyz_batch: np.ndarray,
    family_xyz: np.ndarray,
    family_rgbw: np.ndarray,
    white_xyz_ref: np.ndarray,
    k: int = 8,
) -> tuple[np.ndarray, np.ndarray]:
    """Target-space KNN anchors + constrained barycentric fit.

    Queries neighbours in the perceptual/chromaticity key space, then solves the
    convex combination in XYZ.  This is the preferred fallback for out-of-hull
    targets and sparse family hulls.
    """
    k_eff = min(k, len(family_xyz))
    qkey = _target_space_key(xyz_batch, white_xyz_ref)
    _, idx = key_tree.query(qkey, k=k_eff)
    if k_eff == 1:
        idx = idx[:, None]
    N = len(xyz_batch)
    rgbw_out = np.zeros((N, 4), dtype=np.float64)
    xyz_ach = np.zeros((N, 3), dtype=np.float64)
    for i in range(N):
        anchors_xyz = family_xyz[idx[i]]
        anchors_rgbw = family_rgbw[idx[i]]
        r_i, w_i = fit_constrained_bary(anchors_xyz, xyz_batch[i], anchors_rgbw)
        rgbw_out[i] = r_i
        xyz_ach[i] = w_i @ anchors_xyz
    return rgbw_out, xyz_ach



def _mixed_local_expected_w_vectorised(
    key_tree: "cKDTree | None",
    xyz_batch: np.ndarray,
    mixed_xyz: np.ndarray | None,
    mixed_rgbw: np.ndarray | None,
    white_xyz_ref: np.ndarray,
    input_rgb: np.ndarray | None,
    input_masks: np.ndarray | None,
    common_gate: np.ndarray,
    fallback_expected_w: np.ndarray,
    sample_scale: float,
    k: int = 96,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Derive an off-axis-white W target from the local measured W-active cloud.

    This is deliberately a *measured prior*, not a generic minRGB rule.  For
    mixed RGB values, the shared component says "W should be considered", but
    the measured optical system tells us how much W actually belongs at that
    target chromaticity.  Warm/cool whites may want W near the shared component;
    skin-like warm saturated points may have a stable lower-W RGBW ratio.

    Returns (effective_expected_w, local_valid, local_xy_dist).
    """
    exp = np.asarray(fallback_expected_w, dtype=np.float64).copy()
    valid = np.zeros(len(xyz_batch), dtype=bool)
    best_xy_out = np.full(len(xyz_batch), np.inf, dtype=np.float64)

    if (key_tree is None or mixed_xyz is None or mixed_rgbw is None or
            input_rgb is None or input_masks is None or len(mixed_xyz) == 0):
        return exp, valid, best_xy_out

    gate = np.clip(np.asarray(common_gate, dtype=np.float64), 0.0, 1.0)
    active = (np.asarray(input_masks, dtype=np.int32) == 7) & (gate > 0.05) & (exp > 0.0)
    if not np.any(active):
        return exp, valid, best_xy_out

    idx_active = np.where(active)[0]
    qkey = _target_space_key(xyz_batch[idx_active], white_xyz_ref)
    k_eff = min(max(1, k), len(mixed_xyz))
    _, nn = key_tree.query(qkey, k=k_eff)
    if k_eff == 1:
        nn = nn[:, None]

    cand_xyz = mixed_xyz[nn]
    cand_rgbw = mixed_rgbw[nn]

    tgt = xyz_batch[idx_active]
    tgt_sum = np.maximum(tgt.sum(axis=1), 1e-9)
    tgt_x = tgt[:, 0] / tgt_sum
    tgt_y = tgt[:, 1] / tgt_sum
    cand_sum = np.maximum(cand_xyz.sum(axis=2), 1e-9)
    cand_x = cand_xyz[:, :, 0] / cand_sum
    cand_y = cand_xyz[:, :, 1] / cand_sum
    xy_dist = np.sqrt((cand_x - tgt_x[:, None]) ** 2 + (cand_y - tgt_y[:, None]) ** 2)

    tgt_Y = np.maximum(tgt[:, 1], 1e-9)
    cand_Y = np.maximum(cand_xyz[:, :, 1], 1e-9)
    y_log = np.abs(np.log(cand_Y / tgt_Y[:, None]))

    inp = np.asarray(input_rgb, dtype=np.float64)[idx_active]
    inp_max = np.maximum(np.max(inp, axis=1), 1.0)
    inp_common = np.maximum(np.minimum.reduce([inp[:, 0], inp[:, 1], inp[:, 2]]), 1.0)

    # Preserve strong active-channel topology when it is meaningful.  Missing
    # strong channels are not forbidden (warm white may legitimately use RW),
    # but they must earn it chromatically.
    cand_on = cand_rgbw[:, :, :3] > (0.001 * sample_scale)
    inp_strength = inp / inp_max[:, None]
    missing_strength = (
        (~cand_on[:, :, 0]).astype(np.float64) * (inp_strength[:, None, 0] ** 2) +
        (~cand_on[:, :, 1]).astype(np.float64) * (inp_strength[:, None, 1] ** 2) +
        (~cand_on[:, :, 2]).astype(np.float64) * (inp_strength[:, None, 2] ** 2)
    )

    cand_w = np.clip(cand_rgbw[:, :, 3], 0.0, sample_scale)
    cand_rgb_max = np.maximum(np.max(cand_rgbw[:, :, :3], axis=2), 1.0)
    # Prefer a candidate at a sane drive scale, but keep this weak; the purpose
    # is to reject tiny noisy anchors, not to force endpoint scale.
    scale_dist = np.abs(np.log((cand_rgb_max + cand_w + 1.0) / (inp_max[:, None] + inp_common[:, None] + 1.0)))

    # Off-axis-white candidate score: xy dominates, Y is a secondary gate, and
    # topology/scale are weak priors.  This mirrors target_match but avoids
    # picking impossible single-channel structures unless they are clearly best.
    cand_score = xy_dist + 0.0012 * y_log + 0.0018 * missing_strength + 0.0008 * scale_dist
    cand_score = np.where(cand_w > 0.0, cand_score, np.inf)
    best_j = np.argmin(cand_score, axis=1)
    rows = np.arange(len(idx_active))
    best_xy = xy_dist[rows, best_j]
    best_ylog = y_log[rows, best_j]
    best_w = cand_w[rows, best_j]

    # Valid local priors need a clearly relevant measured neighbor.  Low-chroma
    # white-like colours can tolerate a looser xy distance because the measured
    # W-dominant path is often the desired power/color tradeoff; saturated skin
    # and off-axis colours need tighter xy.
    local_ok = (
        (best_w > 0.0) &
        (best_ylog <= (1.05 + 0.35 * gate[idx_active])) &
        (best_xy <= (0.0065 + 0.0040 * gate[idx_active]))
    )

    # Use the measured local W as the effective target, with light smoothing
    # toward fallback so sparse isolated captures don't become a hard command.
    # If the capture says W should be much lower than minRGB (skin), lower it.
    # If it says W should be much higher (warm/cool white), raise it.
    blend = np.clip(0.55 + 0.35 * gate[idx_active], 0.0, 0.90)
    new_exp = (1.0 - blend) * exp[idx_active] + blend * best_w
    new_exp = np.clip(new_exp, 0.0, sample_scale)

    exp[idx_active] = np.where(local_ok, new_exp, exp[idx_active])
    valid[idx_active] = local_ok
    best_xy_out[idx_active] = best_xy
    return exp, valid, best_xy_out


def _available_memory_bytes() -> int | None:
    """Return currently available system RAM in bytes, or None if unknown.

    Used only to size measured-anchor candidate batches.  Prefer psutil when
    present, but keep a ctypes/sysconf fallback so the builder stays dependency
    free on Windows and Linux.
    """
    try:
        import psutil  # type: ignore
        return int(psutil.virtual_memory().available)
    except Exception:
        pass

    if os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullAvailPhys)
        except Exception:
            return None

    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size)
    except Exception:
        return None


def _auto_measured_candidate_axis_cap(
    n_rows: int,
    requested_axis: int,
    want_scaled_candidates: bool,
) -> int:
    """Choose measured-anchor candidate axis from available RAM and workers.

    v20.4 fixed the OOM by hard-capping raw+scaled candidates to 64.  That was
    safe but unnecessarily conservative on high-RAM systems or when using fewer
    workers.  This keeps the same memory safety goal, but lets the cap rise up
    toward ``--knn-max-candidate-axis`` when the machine has enough free RAM.

    The cap is the final candidate axis after raw+scaled concatenation.  When
    scaled candidates are enabled, the caller queries approximately half this
    number of raw anchors and then appends their scaled versions.
    """
    requested_axis = max(1, int(requested_axis))
    if n_rows <= 0:
        return requested_axis

    args = _worker_state.get("args") if isinstance(_worker_state, dict) else None
    hard_cap = int(getattr(args, "knn_max_candidate_axis", 160))
    min_axis = int(getattr(args, "knn_min_candidate_axis", 32))
    mem_fraction = float(getattr(args, "knn_memory_fraction", 0.50))
    bytes_per = float(getattr(args, "knn_bytes_per_row_candidate", 112.0))
    workers = int(_worker_state.get("worker_count", 0) or getattr(args, "workers", 0) or (os.cpu_count() or 1))

    hard_cap = max(1, hard_cap)
    min_axis = max(1, min_axis)
    mem_fraction = float(np.clip(mem_fraction, 0.05, 0.90))
    bytes_per = max(32.0, bytes_per)
    workers = max(1, workers)

    base_cap = min(requested_axis, hard_cap)
    avail = _available_memory_bytes()
    if avail is None or avail <= 0:
        return max(1, min(base_cap, max(min_axis, base_cap)))

    # Divide the shared RAM budget by the number of simultaneously active
    # workers.  Keep at least a small per-worker budget so low-memory readings
    # do not collapse K to 1 unless the machine is truly under pressure.
    per_worker_budget = max(1.0, (float(avail) * mem_fraction) / float(workers))
    mem_cap = int(per_worker_budget // (float(n_rows) * bytes_per))
    mem_cap = max(1, mem_cap)

    cap = min(base_cap, mem_cap)
    if mem_cap >= min_axis and base_cap >= min_axis:
        cap = max(cap, min_axis)
    return max(1, cap)


def _knn_best_measured_keyed_vectorised(
    key_tree: "cKDTree",
    xyz_batch: np.ndarray,
    family_xyz: np.ndarray,
    family_rgbw: np.ndarray,
    white_xyz_ref: np.ndarray,
    k: int = 128,
    y_weight: float = 0.0006,
    input_rgb: np.ndarray | None = None,
    input_masks: np.ndarray | None = None,
    family_rgb_mask: int = 0,
    exact_dual_drive_weight: float = 0.0260,
    exact_dual_rg_ratio_weight: float = 0.0060,
    expected_w: np.ndarray | None = None,
    common_w_gate: np.ndarray | None = None,
    common_w_anchor_weight: float = 0.0350,
    sample_scale: float = 65535.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return the best *measured* candidate near each target in target space.

    This deliberately does not run a constrained XYZ fit.  For saturated
    dual-channel and skin/off-axis regions, the measured capture set already
    contains anchors that match the requested chromaticity very closely, while
    a full-XYZ barycentric fit often walks away from that anchor to recover
    luminance.  That is exactly what caused magenta to choose B≈31783 instead
    of the measured B≈20480 anchor.

    Candidate ranking mirrors the target-match diagnostics: chromaticity/xy is
    primary, log-luminance is a weak tie-break.
    """
    # Scaled-anchor mode doubles the candidate list (raw anchor + scaled anchor).
    # Size the final raw+scaled candidate axis from available RAM and active
    # worker count instead of using a fixed hard cap.  This avoids the v20.1
    # memory blow-up on dense 256² slices, while allowing high-RAM / low-worker
    # systems to use a larger K than the conservative v20.4 fixed cap.
    want_scaled_candidates = (input_rgb is not None and input_masks is not None)
    k_eff_requested = min(max(1, k), len(family_xyz))
    k_total = _auto_measured_candidate_axis_cap(
        n_rows=len(xyz_batch),
        requested_axis=k_eff_requested,
        want_scaled_candidates=want_scaled_candidates,
    )
    k_total = min(k_eff_requested, max(1, k_total))
    k_query = max(1, k_total // 2) if want_scaled_candidates else k_total

    # Query the measured cloud at multiple Y scales while preserving xy.  The
    # previous lookup was centred near target Y, so it could miss lower/higher
    # measured anchors with excellent chromaticity that should simply be scaled
    # to this input level.  This is especially important for sparse high-value
    # duals and W sub-gamuts where only a low/mid capture passes dE.
    if k_query > 4:
        y_probe_scales = (0.22, 0.40, 0.70, 1.00, 1.55, 2.40)
        k_per = max(1, int(math.ceil(k_query / float(len(y_probe_scales)))))
        idx_parts: list[np.ndarray] = []
        for ys in y_probe_scales:
            qkey = _target_space_key(xyz_batch * float(ys), white_xyz_ref)
            _, idx_part = key_tree.query(qkey, k=min(k_per, len(family_xyz)))
            if idx_part.ndim == 1:
                idx_part = idx_part[:, None]
            idx_parts.append(idx_part)
        idx = np.concatenate(idx_parts, axis=1)
    else:
        qkey = _target_space_key(xyz_batch, white_xyz_ref)
        _, idx = key_tree.query(qkey, k=k_query)
        if k_query == 1:
            idx = idx[:, None]

    cand_xyz = family_xyz[idx]      # (N, k_candidates, 3)
    cand_rgbw = family_rgbw[idx]    # (N, k_candidates, 4)

    # Scaled-anchor candidates -------------------------------------------------
    # Treat target-space measured captures as local manifolds, not only absolute
    # drive states.  A measured anchor with the right chromaticity can often be
    # scaled linearly to the requested drive/Y level.  This prevents half-scale
    # duals and HSV value ramps from snapping to the same full-scale anchor and
    # losing granularity/monotonicity.
    if input_rgb is not None and input_masks is not None:
        inp = np.asarray(input_rgb, dtype=np.float64)
        masks = np.asarray(input_masks, dtype=np.int32)
        if len(inp) == len(xyz_batch) and len(cand_rgbw):
            active_cols = [i for i, bit in enumerate((1, 2, 4)) if (family_rgb_mask & bit)]
            if active_cols:
                inp_active = inp[:, active_cols]
                cand_active = cand_rgbw[:, :, active_cols]
            else:
                inp_active = inp
                cand_active = cand_rgbw[:, :, :3]

            inp_max = np.maximum(np.max(inp_active, axis=1), 1.0)
            cand_max = np.maximum(np.max(cand_active, axis=2), 1.0)
            cand_Y0 = np.maximum(cand_xyz[:, :, 1], 1e-9)
            tgt_Y0 = np.maximum(xyz_batch[:, 1], 1e-9)

            scale_drive = inp_max[:, None] / cand_max
            scale_y = tgt_Y0[:, None] / cand_Y0

            exact_dual_scale = (masks == int(family_rgb_mask)) & (int(family_rgb_mask) in (3, 5, 6))
            all3_scale = masks == 7
            # Exact duals should mostly follow requested drive scale; mixed RGB
            # gets more Y influence because W/common-component decomposition can
            # legitimately change RGB residual scale.
            alpha = np.where(exact_dual_scale, 0.88, np.where(all3_scale, 0.55, 0.72))
            scale = np.exp(
                alpha[:, None] * np.log(np.maximum(scale_drive, 1e-6)) +
                (1.0 - alpha[:, None]) * np.log(np.maximum(scale_y, 1e-6))
            )

            # Stronger exact-dual overdrive cap: low/mid dual inputs should not
            # jump to full-scale measured branches unless the endpoint itself is
            # near full scale.  This is applied to the scaled candidate before it
            # enters the same measured-anchor scoring path as raw anchors.
            endpoint = np.clip(inp_max / float(sample_scale), 0.0, 1.0)
            dual_cap = 1.12 + 0.43 * endpoint * endpoint
            max_scale_dual = (dual_cap[:, None] * inp_max[:, None]) / cand_max
            scale = np.where(exact_dual_scale[:, None], np.minimum(scale, max_scale_dual), scale)

            # Avoid channel clipping changing chromaticity; cap uniform scale so
            # all RGBW channels remain inside the physical drive range.
            cand_abs_max = np.maximum(np.max(cand_rgbw, axis=2), 1.0)
            scale_cap = float(sample_scale) / cand_abs_max
            scale = np.clip(scale, 0.035, np.minimum(scale_cap, 3.25))

            scaled_rgbw = np.clip(cand_rgbw * scale[:, :, None], 0.0, float(sample_scale))
            scaled_xyz = cand_xyz * scale[:, :, None]
            cand_rgbw = np.concatenate([cand_rgbw, scaled_rgbw], axis=1)
            cand_xyz = np.concatenate([cand_xyz, scaled_xyz], axis=1)

    tgt_sum = np.maximum(xyz_batch.sum(axis=1), 1e-9)
    tgt_x = xyz_batch[:, 0] / tgt_sum
    tgt_y = xyz_batch[:, 1] / tgt_sum
    cand_sum = np.maximum(cand_xyz.sum(axis=2), 1e-9)
    cand_x = cand_xyz[:, :, 0] / cand_sum
    cand_y = cand_xyz[:, :, 1] / cand_sum
    xy_dist = np.sqrt((cand_x - tgt_x[:, None]) ** 2 + (cand_y - tgt_y[:, None]) ** 2)

    tgt_Y = np.maximum(xyz_batch[:, 1], 1e-9)
    cand_Y = np.maximum(cand_xyz[:, :, 1], 1e-9)
    y_log_ratio = np.abs(np.log(cand_Y / tgt_Y[:, None]))

    # Make luminance only a very weak tie-break.  The latest verifier passes
    # showed the remaining secondary misses are mostly caused by selecting a
    # scaled lower-drive capture with slightly better xy even when the input is
    # full-scale, or by selecting a higher-drive capture for half-scale inputs.
    # For exact dual-channel edges, add a measured-data/topology term that keeps
    # the active-drive *scale* near the requested input scale.  This is not a
    # colour trim: it prevents the inverse lookup from throwing away usable
    # endpoint resolution when multiple captures lie on the same measured
    # chromaticity curve.
    score = xy_dist + y_weight * y_log_ratio

    if input_rgb is not None and input_masks is not None:
        inp = np.asarray(input_rgb, dtype=np.float64)
        masks = np.asarray(input_masks, dtype=np.int32)
        if len(inp) == len(xyz_batch):
            active_cols_all = [i for i, bit in enumerate((1, 2, 4)) if (family_rgb_mask & bit)]
            if active_cols_all:
                cand_active_all = np.maximum(cand_rgbw[:, :, active_cols_all], 0.0)
                inp_max_all = np.maximum(np.max(inp, axis=1), 1.0)
                cand_max_all = np.maximum(np.max(cand_active_all, axis=2), 1.0)
                scale_dist = np.abs(np.log((cand_max_all + 1.0) / (inp_max_all[:, None] + 1.0)))
                score += 0.0065 * scale_dist

            if family_rgb_mask in (3, 5, 6):
                active_cols = [i for i, bit in enumerate((1, 2, 4)) if (family_rgb_mask & bit)]
                if len(active_cols) == 2:
                    inp_act = inp[:, active_cols]                         # (N, 2)
                    cand_act = cand_rgbw[:, :, active_cols]                # (N, k, 2)
                    inp_max = np.maximum(np.max(inp_act, axis=1), 1.0)
                    cand_max = np.maximum(np.max(cand_act, axis=2), 1.0)
                    exact_dual = masks == int(family_rgb_mask)
                    max_drive_dist = np.abs(np.log((cand_max + 1.0) / (inp_max[:, None] + 1.0)))

                    # Drive-scale affinity is useful for symmetric full/half duals
                    # (e.g. magenta where R=65535+B≈20480 is a known good high-scale
                    # measured point), but it was too strong for asymmetric duals
                    # like orange/spring/rose.  In those cases the measured curve
                    # often has a lower absolute drive state with better xy, and
                    # forcing active-max equality just moves to a brighter but less
                    # correct anchor.  Keep the term, but fade it for asymmetric
                    # exact-dual inputs instead of applying a post-solve color trim.
                    inp_ratio = np.minimum(inp_act[:, 0], inp_act[:, 1]) / np.maximum(inp_max, 1.0)
                    cand_ratio = np.minimum(cand_act[:, :, 0], cand_act[:, :, 1]) / np.maximum(cand_max, 1.0)
                    asymmetric = exact_dual & (inp_ratio < 0.85)
                    drive_weight_vec = exact_dual_drive_weight * np.where(asymmetric, 0.04, 1.0)
                    score += exact_dual[:, None] * drive_weight_vec[:, None] * max_drive_dist

                    # RG off-axis colors such as orange/chartreuse are especially
                    # sensitive to selecting a same-chromaticity capture from the
                    # wrong branch of the measured RG curve.  Ratio continuity only
                    # applies to asymmetric RG; symmetric yellow/yellow_half remain
                    # free to choose calibrated unequal measured R:G ratios.
                    if family_rgb_mask == 3:
                        ratio_dist = np.abs(np.log((cand_ratio + 1e-4) / (inp_ratio[:, None] + 1e-4)))
                        score += asymmetric[:, None] * (exact_dual_rg_ratio_weight * 0.45) * ratio_dist

                    # Prevent exact dual-channel lookup from jumping to a much
                    # higher-drive measured branch just because xy is slightly
                    # closer.  This keeps low/mid RB/RG/GB inputs from snapping
                    # prematurely to R=65535/G=65535 anchors while still allowing
                    # endpoint authority when the input itself is near endpoint.
                    endpoint_frac = np.clip(inp_max / float(sample_scale), 0.0, 1.0)
                    dual_over_cap = 1.12 + 0.43 * endpoint_frac * endpoint_frac
                    over_ratio = np.maximum(0.0, np.log((cand_max + 1.0) / (inp_max[:, None] * dual_over_cap[:, None] + 1.0)))
                    over_weight = 1.15 - 0.55 * endpoint_frac
                    score += exact_dual[:, None] * over_weight[:, None] * over_ratio * over_ratio

    # For 3-channel mixed RGB, measured W-family anchors should prefer useful
    # W, but the target W must be *measured-cluster aware*.  Skin_light is the
    # canonical example: the target-space neighborhood contains a stable
    # R:G:B:W≈1:0.25:0.12:0.22 cluster, so forcing W toward min(R,G,B) is
    # wrong.  Derive a local W expectation from the nearest measured anchors
    # whenever the neighborhood is chromatically tight and ratio-consistent;
    # otherwise fall back to the shared-RGB common-W preference.
    if expected_w is not None:
        exp_w = np.asarray(expected_w, dtype=np.float64)
        if len(exp_w) == len(xyz_batch):
            gate = np.ones(len(xyz_batch), dtype=np.float64)
            if common_w_gate is not None:
                gate = np.asarray(common_w_gate, dtype=np.float64)
            gate = np.clip(gate, 0.0, 1.0)
            cand_w = np.clip(cand_rgbw[:, :, 3], 0.0, 65535.0)
            exp_w_eff = exp_w.copy()

            if input_rgb is not None and input_masks is not None:
                inp = np.asarray(input_rgb, dtype=np.float64)
                masks = np.asarray(input_masks, dtype=np.int32)
                if len(inp) == len(xyz_batch):
                    inp_max_rgb = np.maximum(np.max(inp, axis=1), 1.0)
                    cand_rgb_max = np.maximum(np.max(cand_rgbw[:, :, :3], axis=2), 1.0)
                    cand_w_ratio = cand_w / cand_rgb_max
                    best_xy = np.min(xy_dist, axis=1)
                    local = (
                        (masks == 7)[:, None]
                        & (gate[:, None] > 0.05)
                        & (cand_w > 0.0)
                        & (xy_dist <= np.minimum(0.010, best_xy[:, None] + 0.0038))
                        & (y_log_ratio <= 1.15)
                    )
                    ratio_vals = np.where(local, cand_w_ratio, np.nan)
                    count = np.sum(local, axis=1)
                    med_ratio = np.full(len(xyz_batch), np.nan, dtype=np.float64)
                    mad_ratio = np.full(len(xyz_batch), np.nan, dtype=np.float64)
                    valid_ratio_rows = count > 0
                    if np.any(valid_ratio_rows):
                        # Avoid repeated All-NaN slice warnings on rows whose
                        # measured-neighbor set has no local W-active candidate.
                        with np.errstate(all="ignore"):
                            med_ratio[valid_ratio_rows] = np.nanmedian(ratio_vals[valid_ratio_rows], axis=1)
                    valid_mad_rows = count > 0
                    if np.any(valid_mad_rows):
                        with np.errstate(all="ignore"):
                            mad_ratio[valid_mad_rows] = np.nanmedian(
                                np.abs(ratio_vals[valid_mad_rows] - med_ratio[valid_mad_rows, None]),
                                axis=1,
                            )
                    stable = (count >= 2) & np.isfinite(med_ratio) & np.isfinite(mad_ratio) & (mad_ratio <= 0.075)
                    # Slight headroom keeps the measured cluster from becoming a hard cap.
                    local_exp = np.clip(med_ratio * inp_max_rgb * 1.10, 0.0, 65535.0)
                    exp_w_eff = np.where(stable, np.minimum(exp_w_eff, local_exp), exp_w_eff)

            exp = np.maximum(exp_w_eff[:, None], 1.0)
            # Bounded preference band, not a hard target.  Common-W is now an
            # upper-bias: inside the visually comparable set we prefer useful W,
            # but measured local ratios can legitimately sit well below minRGB.
            lo = (0.30 + 0.30 * gate[:, None]) * exp   # 0.30→0.60×effective expected W
            hi = 1.22 * exp + 512.0
            under = np.maximum(0.0, lo - cand_w) / exp
            over = np.maximum(0.0, cand_w - hi) / exp
            centre = np.abs(cand_w - exp) / exp
            w_loss = 2.2 * under * under + 0.8 * over * over + 0.05 * centre
            score += common_w_anchor_weight * gate[:, None] * w_loss

    best = np.argmin(score, axis=1)
    rows = np.arange(len(xyz_batch))
    return cand_rgbw[rows, best], cand_xyz[rows, best]


def _xy_error_vectorised(xyz_ach: np.ndarray, xyz_tgt: np.ndarray) -> np.ndarray:
    ach_sum = np.maximum(xyz_ach.sum(axis=1), 1e-9)
    tgt_sum = np.maximum(xyz_tgt.sum(axis=1), 1e-9)
    ax = xyz_ach[:, 0] / ach_sum
    ay = xyz_ach[:, 1] / ach_sum
    tx = xyz_tgt[:, 0] / tgt_sum
    ty = xyz_tgt[:, 1] / tgt_sum
    return np.sqrt((ax - tx) ** 2 + (ay - ty) ** 2)




def _ratio_anchor_tile_shape(
    n_rows: int,
    requested_k: int,
) -> tuple[int, int, int]:
    """Choose row/K tile sizes for streaming ratio-anchor scoring.

    ``requested_k`` is search coverage and is never reduced here.  Available
    memory only controls how many rows and candidate neighbours are evaluated
    at once.  This avoids making candidate quality depend on worker count while
    still respecting the current system RAM budget and ``--knn-memory-fraction``.
    """
    n_rows = max(1, int(n_rows))
    requested_k = max(1, int(requested_k))

    args = _worker_state.get("args") if isinstance(_worker_state, dict) else None
    mem_fraction = float(getattr(args, "knn_memory_fraction", 0.50))
    workers = int(_worker_state.get("worker_count", 0) or getattr(args, "workers", 0) or (os.cpu_count() or 1))
    mem_fraction = float(np.clip(mem_fraction, 0.05, 0.90))
    workers = max(1, workers)

    avail = _available_memory_bytes()
    if avail is None or avail <= 0:
        # If the platform cannot report available memory, keep full search
        # coverage and rely on modest streaming tiles rather than reducing K.
        return min(n_rows, max(1, int(math.ceil(n_rows / float(workers))))), requested_k, -1

    budget = max(1.0, (float(avail) * mem_fraction) / float(workers))

    f8 = np.dtype(np.float64).itemsize
    i8 = np.dtype(np.intp).itemsize
    b1 = np.dtype(np.bool_).itemsize

    # Persistent per-row output/best arrays in _find_scaled_ratio_anchor_vectorised.
    # This includes RGBW/XYZ/source/score/diagnostic arrays and some overhead for
    # temporary row vectors.  It is intentionally estimated from dtype sizes, not
    # a fixed MiB floor.
    row_state_bytes = (
        (4 + 3 + 4 + 3) * f8 +      # best rgbw/xyz/source rgbw/source xyz
        (7 * f8) +                  # score, scale, de, xy, yerr, resid, leak
        (3 * f8) +                  # leak ratio/excess plus scratch
        (3 * np.dtype(np.int32).itemsize) +
        (2 * b1)
    )

    # Per row x requested_k neighbour-index matrix for the row tile.  We keep
    # this separate from candidate scoring so each row still sees the full K.
    index_bytes_per_row = requested_k * i8

    # Approximate largest simultaneous per row-candidate footprint inside one
    # candidate tile.  This accounts for gathered anchors, scaled RGBW/XYZ, Lab,
    # score/error terms, scale caps, finite masks and leakage terms.
    bytes_per_row_k_tile = (
        (3 + 4) * f8 +              # gathered xyz/rgbw anchors
        (3 + 4) * f8 +              # scaled xyz/rgbw candidates
        (3 * f8) +                  # Lab candidate
        (9 * f8) +                  # xy/yerr/de/resid/score/scale/temp terms
        (2 * b1) + i8               # finite/bool masks and index scratch
    )

    def estimate(row_tile: int, k_tile: int) -> float:
        return (
            row_tile * row_state_bytes +
            row_tile * index_bytes_per_row +
            row_tile * k_tile * bytes_per_row_k_tile
        )

    # Prefer larger row tiles for fewer KD queries, then stream candidate-K
    # windows inside each row tile.  Shrink rows only when the full neighbour
    # index matrix plus a single candidate column cannot fit.
    row_tile = n_rows
    while row_tile > 1 and estimate(row_tile, 1) > budget:
        row_tile = max(1, row_tile // 2)

    remaining = budget - (row_tile * row_state_bytes + row_tile * index_bytes_per_row)
    if remaining <= 0.0:
        k_tile = 1
    else:
        k_tile = int(remaining // max(1.0, row_tile * bytes_per_row_k_tile))
        k_tile = max(1, min(requested_k, k_tile))

    # If the chosen row tile leaves enough memory for more candidate columns,
    # use as many as possible up to requested_k.  This affects runtime only.
    while k_tile < requested_k and estimate(row_tile, min(requested_k, k_tile * 2)) <= budget:
        k_tile = min(requested_k, k_tile * 2)

    return max(1, row_tile), max(1, k_tile), int(budget)


def _find_scaled_ratio_anchor_vectorised(
    xyz_targets: np.ndarray,
    family_xyz: np.ndarray,
    family_rgbw: np.ndarray,
    white_xyz_ref: np.ndarray,
    sample_scale: float,
    *,
    input_rgb: np.ndarray | None = None,
    input_masks: np.ndarray | None = None,
    family_rgb_mask: int = 0,
    role: str = "mixed_w_axis",
    axis_drive: np.ndarray | None = None,
    forbidden_rgb_mask: np.ndarray | None = None,
    k: int = 96,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Find a measured ratio anchor by xy, then scale it linearly to target.

    This streams the full requested candidate count in row/K tiles.  Memory
    limits therefore reduce tile size and increase runtime, but they do not
    reduce the number of anchors searched for each row.
    """
    target = np.asarray(xyz_targets, dtype=np.float64)
    N = len(target)
    out_rgbw = np.zeros((N, 4), dtype=np.float64)
    out_xyz = np.zeros((N, 3), dtype=np.float64)
    ok = np.zeros(N, dtype=bool)

    diag = {
        "w_axis_endpoint_r": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_g": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_b": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_w": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_x": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_y": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_Y": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_r": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_g": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_b": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_w": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_x": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_y": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_Y": np.full(N, np.nan, dtype=np.float64),
        "w_axis_deltaE": np.full(N, np.inf, dtype=np.float64),
        "w_axis_xy_err": np.full(N, np.inf, dtype=np.float64),
        "w_axis_y_err": np.full(N, np.inf, dtype=np.float64),
        "w_axis_residual_energy": np.full(N, np.inf, dtype=np.float64),
        "w_axis_w_to_common": np.full(N, np.nan, dtype=np.float64),
        "w_axis_source": np.full(N, 3, dtype=np.int32),
        "w_axis_common_leak_max": np.zeros(N, dtype=np.float64),
        "w_axis_common_leak_ratio": np.zeros(N, dtype=np.float64),
        "w_axis_common_leak_excess": np.zeros(N, dtype=np.float64),
        "w_axis_selected": np.zeros(N, dtype=bool),
        "scaled_anchor_role": np.full(N, 1 if role == "exact_dual" else 2, dtype=np.int32),
        "scaled_anchor_family_mask": np.full(N, int(family_rgb_mask), dtype=np.int32),
        "scaled_anchor_source_r": np.full(N, np.nan, dtype=np.float64),
        "scaled_anchor_source_g": np.full(N, np.nan, dtype=np.float64),
        "scaled_anchor_source_b": np.full(N, np.nan, dtype=np.float64),
        "scaled_anchor_source_w": np.full(N, np.nan, dtype=np.float64),
        "scaled_anchor_source_x": np.full(N, np.nan, dtype=np.float64),
        "scaled_anchor_source_y": np.full(N, np.nan, dtype=np.float64),
        "scaled_anchor_source_Y": np.full(N, np.nan, dtype=np.float64),
        "scaled_anchor_scale": np.full(N, np.nan, dtype=np.float64),
        "scaled_anchor_scale_mode": np.zeros(N, dtype=np.int32),
        "scaled_anchor_xy_err": np.full(N, np.inf, dtype=np.float64),
        "scaled_anchor_y_log_err": np.full(N, np.inf, dtype=np.float64),
        "scaled_anchor_deltaE": np.full(N, np.inf, dtype=np.float64),
        "scaled_anchor_selected": np.zeros(N, dtype=bool),
        "scaled_anchor_blocked_reason": np.zeros(N, dtype=np.int32),
    }

    if N == 0 or family_xyz is None or family_rgbw is None or len(family_xyz) == 0:
        return out_rgbw, out_xyz, ok, diag

    fxyz = np.asarray(family_xyz, dtype=np.float64)
    frgbw = np.asarray(family_rgbw, dtype=np.float64)
    valid_family = np.isfinite(fxyz).all(axis=1) & (fxyz[:, 1] > 0.0) & np.isfinite(frgbw).all(axis=1)
    if not np.any(valid_family):
        return out_rgbw, out_xyz, ok, diag
    fxyz_v = fxyz[valid_family]
    frgbw_v = frgbw[valid_family]

    tgt_sum = np.maximum(target.sum(axis=1), 1e-9)
    tgt_x = target[:, 0] / tgt_sum
    tgt_y = target[:, 1] / tgt_sum
    tgt_Y = np.maximum(target[:, 1], 1e-9)

    fsum = np.maximum(fxyz_v.sum(axis=1), 1e-9)
    fx = fxyz_v[:, 0] / fsum
    fy = fxyz_v[:, 1] / fsum
    xy_tree = cKDTree(np.column_stack([fx * 420.0, fy * 420.0]))
    requested_k = min(max(1, int(k)), len(fxyz_v))
    row_tile_n, k_tile_n, _budget = _ratio_anchor_tile_shape(N, requested_k)

    if input_rgb is not None and len(input_rgb) == N:
        inp = np.asarray(input_rgb, dtype=np.float64)
    else:
        inp = np.zeros((N, 3), dtype=np.float64)
    masks = np.asarray(input_masks, dtype=np.int32) if input_masks is not None and len(input_masks) == N else np.zeros(N, dtype=np.int32)
    active_cols = [i for i, bit in enumerate((1, 2, 4)) if (int(family_rgb_mask) & bit)]
    if not active_cols:
        active_cols = [0, 1, 2]

    forbid_all = np.zeros((N, 3), dtype=bool)
    if forbidden_rgb_mask is not None and len(forbidden_rgb_mask) == N:
        fm = np.asarray(forbidden_rgb_mask, dtype=np.int32)
        forbid_all[:, 0] = (fm & 1) != 0
        forbid_all[:, 1] = (fm & 2) != 0
        forbid_all[:, 2] = (fm & 4) != 0

    lab_tgt_all = _xyz_to_lab_vectorised(np.maximum(target, 0.0), white_xyz_ref)

    best_score = np.full(N, np.inf, dtype=np.float64)
    best_rgbw = np.zeros((N, 4), dtype=np.float64)
    best_xyz = np.zeros((N, 3), dtype=np.float64)
    best_anchor_rgbw = np.zeros((N, 4), dtype=np.float64)
    best_anchor_xyz = np.zeros((N, 3), dtype=np.float64)
    best_scale = np.full(N, np.nan, dtype=np.float64)
    best_mode = np.zeros(N, dtype=np.int32)
    best_de = np.full(N, np.inf, dtype=np.float64)
    best_xy = np.full(N, np.inf, dtype=np.float64)
    best_yerr = np.full(N, np.inf, dtype=np.float64)
    best_resid = np.full(N, np.inf, dtype=np.float64)
    best_leak = np.zeros(N, dtype=np.float64)
    best_leak_ratio = np.zeros(N, dtype=np.float64)
    best_leak_excess = np.zeros(N, dtype=np.float64)

    for r0 in range(0, N, row_tile_n):
        r1 = min(N, r0 + row_tile_n)
        rows_global = np.arange(r0, r1)
        M = r1 - r0
        target_t = target[r0:r1]
        tgt_x_t = tgt_x[r0:r1]
        tgt_y_t = tgt_y[r0:r1]
        tgt_Y_t = tgt_Y[r0:r1]
        inp_t = inp[r0:r1]
        forbid_t = forbid_all[r0:r1]
        lab_tgt_t = lab_tgt_all[r0:r1]

        _, idx_full = xy_tree.query(np.column_stack([tgt_x_t * 420.0, tgt_y_t * 420.0]), k=requested_k)
        if requested_k == 1:
            idx_full = idx_full[:, None]

        inp_active = np.maximum(inp_t[:, active_cols], 0.0)
        inp_active_max = np.maximum(np.max(inp_active, axis=1), 1.0)
        inp_active_min = np.maximum(np.min(np.where(inp_active > 0.0, inp_active, np.nan), axis=1), 1.0)
        inp_active_min = np.where(np.isfinite(inp_active_min), inp_active_min, 1.0)

        axis_t = None
        if axis_drive is not None and len(axis_drive) == N:
            axis_t = np.asarray(axis_drive, dtype=np.float64)[r0:r1]
        elif role != "exact_dual":
            axis_t = np.minimum.reduce([inp_t[:, 0], inp_t[:, 1], inp_t[:, 2]])
        if axis_t is not None:
            axis_t = np.maximum(axis_t, 1.0)

        for k0 in range(0, requested_k, k_tile_n):
            k1 = min(requested_k, k0 + k_tile_n)
            idx = idx_full[:, k0:k1]
            kk = k1 - k0
            cand_xyz0 = fxyz_v[idx]
            cand_rgbw0 = frgbw_v[idx]

            cand_active = np.maximum(cand_rgbw0[:, :, active_cols], 0.0)
            cand_active_max = np.maximum(np.max(cand_active, axis=2), 1.0)
            cand_active_min = np.maximum(np.min(np.where(cand_active > 0.0, cand_active, np.nan), axis=2), 1.0)
            cand_active_min = np.where(np.isfinite(cand_active_min), cand_active_min, 1.0)

            cand_Y0 = np.maximum(cand_xyz0[:, :, 1], 1e-9)
            scale_y = tgt_Y_t[:, None] / cand_Y0
            scale_drive = inp_active_max[:, None] / cand_active_max
            scale_min = inp_active_min[:, None] / cand_active_min

            scale_modes: list[tuple[int, np.ndarray]] = []
            if role == "exact_dual":
                scale_modes.append((1, scale_drive))
                scale_modes.append((2, scale_y))
                for code, a in ((11, 0.75), (12, 0.50), (13, 0.25)):
                    scale_modes.append((code, np.exp(a * np.log(np.maximum(scale_drive, 1e-6)) + (1.0 - a) * np.log(np.maximum(scale_y, 1e-6)))))
            else:
                axis_use = axis_t if axis_t is not None else np.ones(M, dtype=np.float64)
                scale_w = axis_use[:, None] / np.maximum(cand_rgbw0[:, :, 3], 1.0)
                scale_modes.append((3, scale_w))
                scale_modes.append((2, scale_y))
                scale_modes.append((1, scale_drive))
                scale_modes.append((4, np.exp(0.55 * np.log(np.maximum(scale_w, 1e-6)) + 0.45 * np.log(np.maximum(scale_y, 1e-6)))))
                scale_modes.append((5, np.exp(0.35 * np.log(np.maximum(scale_drive, 1e-6)) + 0.65 * np.log(np.maximum(scale_y, 1e-6)))))
                scale_modes.append((6, np.exp(0.50 * np.log(np.maximum(scale_drive, 1e-6)) + 0.50 * np.log(np.maximum(scale_w, 1e-6)))))

            cand_abs_max = np.maximum(np.max(cand_rgbw0, axis=2), 1.0)
            scale_cap = float(sample_scale) / cand_abs_max
            endpoint = np.clip(inp_active_max / float(sample_scale), 0.0, 1.0)
            active_headroom = (1.035 + 0.170 * endpoint * endpoint)[:, None]
            active_cap = np.full_like(scale_cap, np.inf, dtype=np.float64)
            for ch in active_cols:
                denom = np.maximum(cand_rgbw0[:, :, ch], 1.0)
                cap_ch = (np.maximum(inp_t[:, ch], 1.0)[:, None] * active_headroom + 64.0) / denom
                active_cap = np.minimum(active_cap, cap_ch)
            scale_cap = np.minimum(scale_cap, active_cap)

            if role != "exact_dual":
                axis_use = axis_t if axis_t is not None else np.ones(M, dtype=np.float64)
                w_cap = (axis_use[:, None] * 1.25 + 512.0) / np.maximum(cand_rgbw0[:, :, 3], 1.0)
                scale_cap = np.minimum(scale_cap, w_cap)

            for mode_code, scale_base in scale_modes:
                for sm in (0.94, 1.00, 1.06):
                    sc = np.clip(scale_base * sm, 0.0, scale_cap)
                    finite = np.isfinite(sc) & (sc > 0.0)
                    cand_rgbw = np.clip(cand_rgbw0 * sc[:, :, None], 0.0, float(sample_scale))
                    cand_xyz = np.maximum(cand_xyz0 * sc[:, :, None], 0.0)
                    csum = np.maximum(cand_xyz.sum(axis=2), 1e-9)
                    cx = cand_xyz[:, :, 0] / csum
                    cy = cand_xyz[:, :, 1] / csum
                    xy = np.sqrt((cx - tgt_x_t[:, None]) ** 2 + (cy - tgt_y_t[:, None]) ** 2)
                    yerr = np.abs(np.log(np.maximum(cand_xyz[:, :, 1], 1e-9) / tgt_Y_t[:, None]))
                    lab = _xyz_to_lab_vectorised(cand_xyz.reshape(-1, 3), white_xyz_ref).reshape(M, kk, 3)
                    de = np.linalg.norm(lab - lab_tgt_t[:, None, :], axis=2)
                    rgb_norm = cand_rgbw[:, :, :3] / float(sample_scale)
                    resid = rgb_norm[:, :, 0] ** 2 + 4.0 * rgb_norm[:, :, 1] ** 2 + rgb_norm[:, :, 2] ** 2
                    score = 1280.0 * xy + 0.08 * de + 0.46 * yerr + 0.34 * resid
                    if role == "exact_dual":
                        out_act = np.maximum(np.max(cand_rgbw[:, :, active_cols], axis=2), 1.0)
                        drive_dist = np.abs(np.log((out_act + 1.0) / (inp_active_max[:, None] + 1.0)))
                        score += 0.55 * drive_dist
                        leak = np.zeros_like(score)
                        leak_excess = np.zeros_like(score)
                    else:
                        axis_use = axis_t if axis_t is not None else np.ones(M, dtype=np.float64)
                        w = cand_rgbw[:, :, 3]
                        w_close = np.abs(w - axis_use[:, None]) / np.maximum(axis_use[:, None], 512.0)
                        score += 0.28 * w_close
                        if np.any(forbid_t):
                            leak = np.max(np.where(forbid_t[:, None, :], cand_rgbw[:, :, :3], 0.0), axis=2)
                            leak_den = np.maximum(axis_use[:, None], 512.0)
                            leak_tol = 48.0 + 0.0040 * leak_den
                            leak_excess = np.maximum(0.0, leak - leak_tol) / leak_den
                            score += 2600.0 * leak_excess + 180.0 * leak_excess * leak_excess
                        else:
                            leak = np.zeros_like(score)
                            leak_excess = np.zeros_like(score)
                    score = np.where(finite, score, np.inf)
                    j = np.argmin(score, axis=1)
                    local_rows = np.arange(M)
                    local_score = score[local_rows, j]
                    better = local_score < best_score[rows_global]
                    if not np.any(better):
                        continue
                    lb = local_rows[better]
                    jb = j[better]
                    gb = rows_global[better]
                    best_score[gb] = local_score[better]
                    best_rgbw[gb] = cand_rgbw[lb, jb]
                    best_xyz[gb] = cand_xyz[lb, jb]
                    best_anchor_rgbw[gb] = cand_rgbw0[lb, jb]
                    best_anchor_xyz[gb] = cand_xyz0[lb, jb]
                    best_scale[gb] = sc[lb, jb]
                    best_mode[gb] = int(mode_code)
                    best_de[gb] = de[lb, jb]
                    best_xy[gb] = xy[lb, jb]
                    best_yerr[gb] = yerr[lb, jb]
                    best_resid[gb] = resid[lb, jb]
                    if role != "exact_dual":
                        axis_use = axis_t if axis_t is not None else np.ones(M, dtype=np.float64)
                        leak_den_1d = np.maximum(axis_use[lb], 512.0)
                        leak_tol_1d = 48.0 + 0.0040 * leak_den_1d
                        leak_sel = leak[lb, jb]
                        best_leak[gb] = leak_sel
                        best_leak_ratio[gb] = leak_sel / leak_den_1d
                        best_leak_excess[gb] = np.maximum(0.0, leak_sel - leak_tol_1d) / leak_den_1d

            # Explicitly release large candidate-tile arrays before the next K window.
            del cand_xyz0, cand_rgbw0

    finite_best = np.isfinite(best_score)
    if role == "exact_dual":
        ok = finite_best & (
            ((best_xy <= 0.0120) & (best_yerr <= 1.60)) |
            ((best_xy <= 0.0180) & (best_de <= 8.0) & (best_yerr <= 1.25)) |
            (best_xy <= 0.0065)
        )
    else:
        ok = finite_best & (
            ((best_xy <= 0.0140) & (best_yerr <= 2.35)) |
            ((best_xy <= 0.0200) & (best_de <= 9.0) & (best_yerr <= 1.65)) |
            (best_xy <= 0.0075)
        )

    out_rgbw = np.where(ok[:, None], best_rgbw, out_rgbw)
    out_xyz = np.where(ok[:, None], best_xyz, out_xyz)
    ssum = np.maximum(out_xyz.sum(axis=1), 1e-9)
    asum = np.maximum(best_anchor_xyz.sum(axis=1), 1e-9)
    diag["w_axis_endpoint_r"] = best_anchor_rgbw[:, 0]
    diag["w_axis_endpoint_g"] = best_anchor_rgbw[:, 1]
    diag["w_axis_endpoint_b"] = best_anchor_rgbw[:, 2]
    diag["w_axis_endpoint_w"] = best_anchor_rgbw[:, 3]
    diag["w_axis_endpoint_x"] = best_anchor_xyz[:, 0] / asum
    diag["w_axis_endpoint_y"] = best_anchor_xyz[:, 1] / asum
    diag["w_axis_endpoint_Y"] = best_anchor_xyz[:, 1]
    diag["w_axis_scaled_r"] = best_rgbw[:, 0]
    diag["w_axis_scaled_g"] = best_rgbw[:, 1]
    diag["w_axis_scaled_b"] = best_rgbw[:, 2]
    diag["w_axis_scaled_w"] = best_rgbw[:, 3]
    diag["w_axis_scaled_x"] = out_xyz[:, 0] / ssum
    diag["w_axis_scaled_y"] = out_xyz[:, 1] / ssum
    diag["w_axis_scaled_Y"] = out_xyz[:, 1]
    diag["w_axis_deltaE"] = best_de
    diag["w_axis_xy_err"] = best_xy
    diag["w_axis_y_err"] = best_yerr
    diag["w_axis_residual_energy"] = best_resid
    if axis_drive is not None and len(axis_drive) == N:
        diag["w_axis_w_to_common"] = best_rgbw[:, 3] / np.maximum(np.asarray(axis_drive, dtype=np.float64), 1.0)
    diag["w_axis_common_leak_max"] = best_leak
    diag["w_axis_common_leak_ratio"] = best_leak_ratio
    diag["w_axis_common_leak_excess"] = best_leak_excess
    diag["w_axis_selected"] = ok
    diag["scaled_anchor_source_r"] = best_anchor_rgbw[:, 0]
    diag["scaled_anchor_source_g"] = best_anchor_rgbw[:, 1]
    diag["scaled_anchor_source_b"] = best_anchor_rgbw[:, 2]
    diag["scaled_anchor_source_w"] = best_anchor_rgbw[:, 3]
    diag["scaled_anchor_source_x"] = best_anchor_xyz[:, 0] / asum
    diag["scaled_anchor_source_y"] = best_anchor_xyz[:, 1] / asum
    diag["scaled_anchor_source_Y"] = best_anchor_xyz[:, 1]
    diag["scaled_anchor_scale"] = best_scale
    diag["scaled_anchor_scale_mode"] = best_mode
    diag["scaled_anchor_xy_err"] = best_xy
    diag["scaled_anchor_y_log_err"] = best_yerr
    diag["scaled_anchor_deltaE"] = best_de
    diag["scaled_anchor_selected"] = ok
    diag["scaled_anchor_blocked_reason"] = np.where(finite_best, np.where(ok, 0, 2), 1)
    return out_rgbw, out_xyz, ok, diag


# ---------------------------------------------------------------------------
# Neutral-axis dedicated solver
# ---------------------------------------------------------------------------


def solve_w_axis_measured(
    xyz_targets: np.ndarray,
    axis_drive: np.ndarray,
    white_xyz_ref: np.ndarray,
    sample_scale: float,
    *,
    w_key_tree: "cKDTree | None" = None,
    w_xyz: np.ndarray | None = None,
    w_rgbw: np.ndarray | None = None,
    target_xy: np.ndarray | None = None,
    endpoint_mode: str = "target_xy",
    rgbw_basis: np.ndarray | None = None,
    residual_weights: np.ndarray | None = None,
    input_rgb: np.ndarray | None = None,
    forbidden_rgb_mask: np.ndarray | None = None,
    strict_residual_mask: bool = False,
    endpoint_min_w_abs: float | None = None,
    endpoint_min_w_dominance: float | None = None,
    endpoint_scale_modes: tuple[str, ...] = ("w",),
    k: int = 64,
    allow_fixed_w_fallback: bool = True,
    return_diagnostics: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Shared W-axis solver used by both neutral and mixed common-RGB routes.

    This is the structural core that the exact neutral axis has been proving out:
    choose a W-dominant correction endpoint, scale the *whole measured RGBW
    endpoint vector* by the requested axis drive, then score the resulting XYZ
    against the target.  For exact neutrals the target xy is the configured
    reference white; for mixed RGB with min(rgb)>0 the target xy is the row's
    full verifier-aligned target xy.

    Fixed-W residual solving is kept only as a fallback/augmentation: it may
    replace the scaled endpoint only when it is materially more accurate.  That
    prevents the old failure mode where free RGB residual channels overdrive to
    50k/65k while the input common/max is much lower.
    """
    N = len(xyz_targets)
    out_rgbw = np.zeros((N, 4), dtype=np.float64)
    out_xyz = np.zeros((N, 3), dtype=np.float64)
    active = np.zeros(N, dtype=bool)

    diag = {
        "w_axis_endpoint_r": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_g": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_b": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_w": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_x": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_y": np.full(N, np.nan, dtype=np.float64),
        "w_axis_endpoint_Y": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_r": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_g": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_b": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_w": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_x": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_y": np.full(N, np.nan, dtype=np.float64),
        "w_axis_scaled_Y": np.full(N, np.nan, dtype=np.float64),
        "w_axis_deltaE": np.full(N, np.inf, dtype=np.float64),
        "w_axis_xy_err": np.full(N, np.inf, dtype=np.float64),
        "w_axis_y_err": np.full(N, np.inf, dtype=np.float64),
        "w_axis_residual_energy": np.full(N, np.inf, dtype=np.float64),
        "w_axis_w_to_common": np.full(N, np.nan, dtype=np.float64),
        "w_axis_source": np.zeros(N, dtype=np.int32),  # 0 none, 1 scaled endpoint, 2 fixed-W fallback
        "w_axis_common_leak_max": np.zeros(N, dtype=np.float64),
        "w_axis_common_leak_ratio": np.zeros(N, dtype=np.float64),
        "w_axis_common_leak_excess": np.zeros(N, dtype=np.float64),
        "w_axis_selected": np.zeros(N, dtype=bool),
    }

    if N == 0:
        return (out_rgbw, out_xyz, active, diag) if return_diagnostics else (out_rgbw, out_xyz, active)

    target = np.asarray(xyz_targets, dtype=np.float64)
    axis = np.clip(np.asarray(axis_drive, dtype=np.float64), 0.0, float(sample_scale))
    valid = np.isfinite(target).all(axis=1) & (target[:, 1] > 0.0) & (axis > 0.0)

    if residual_weights is None:
        rw = np.array([1.0, 4.0, 1.0], dtype=np.float64)
    else:
        rw = np.asarray(residual_weights, dtype=np.float64)
        if rw.shape != (3,):
            rw = np.array([1.0, 4.0, 1.0], dtype=np.float64)

    if input_rgb is not None and len(input_rgb) == N:
        inp_rgb = np.asarray(input_rgb, dtype=np.float64)
        inp_max = np.maximum(np.max(inp_rgb, axis=1), 1.0)
    else:
        inp_rgb = None
        inp_max = np.maximum(axis, 1.0)

    # Mixed-common rows need a residual-mask contract: RGB channels that are
    # merely part of common=min(R,G,B) should not suddenly become large free
    # residuals when the full RGBW fallback is tried.  Keep this optional so the
    # exact-neutral path and broad family fallback retain their existing behavior.
    forbidden_rgb = np.zeros((N, 3), dtype=bool)
    if strict_residual_mask and inp_rgb is not None:
        if forbidden_rgb_mask is not None:
            fm = np.asarray(forbidden_rgb_mask, dtype=np.int32)
            if len(fm) == N:
                forbidden_rgb[:, 0] = (fm & 1) != 0
                forbidden_rgb[:, 1] = (fm & 2) != 0
                forbidden_rgb[:, 2] = (fm & 4) != 0
        else:
            common = np.minimum.reduce([inp_rgb[:, 0], inp_rgb[:, 1], inp_rgb[:, 2]])
            contrast = np.max(inp_rgb, axis=1) - common
            eps_resid = np.maximum(64.0, 0.0035 * np.maximum(np.max(inp_rgb, axis=1), 1.0))
            has_real_residual = contrast > eps_resid
            forbidden_rgb = (inp_rgb <= (common[:, None] + eps_resid[:, None])) & has_real_residual[:, None]

    target_sum = np.maximum(target.sum(axis=1), 1e-9)
    tgt_x = target[:, 0] / target_sum
    tgt_y = target[:, 1] / target_sum
    target_Y = np.maximum(target[:, 1], 1e-9)

    # Lab/ΔE in this shared W-axis solver must be target-axis aware.  In
    # reference-white mode this is exactly ``white_xyz_ref``.  In target-xy mode
    # it follows the passed-in target xyY, so rows like 10,55,30000 are not
    # scored as if their correction endpoint were D65/reference white.
    if endpoint_mode == "target_xy":
        axis_white_xyz = _axis_reference_white_from_targets(target, white_xyz_ref, target_xy)
    else:
        axis_white_xyz = np.asarray(white_xyz_ref, dtype=np.float64)
    lab_tgt = _xyz_to_lab_vectorised(np.maximum(target, 0.0), axis_white_xyz)

    def _metrics(c_rgbw: np.ndarray, c_xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        c_xyz = np.maximum(np.asarray(c_xyz, dtype=np.float64), 0.0)
        c_rgbw = np.clip(np.asarray(c_rgbw, dtype=np.float64), 0.0, float(sample_scale))
        lab = _xyz_to_lab_vectorised(c_xyz, axis_white_xyz)
        de = np.linalg.norm(lab - lab_tgt, axis=1)
        xy = _xy_error_vectorised(c_xyz, target)
        y_log = np.abs(np.log(np.maximum(c_xyz[:, 1], 1e-9) / target_Y))
        rel_y = np.abs(np.maximum(c_xyz[:, 1], 1e-9) - target_Y) / target_Y
        rgb_norm = c_rgbw[:, :3] / float(sample_scale)
        resid = rw[0] * rgb_norm[:, 0] ** 2 + rw[1] * rgb_norm[:, 1] ** 2 + rw[2] * rgb_norm[:, 2] ** 2
        out_max = np.maximum(np.max(c_rgbw[:, :3], axis=1), 1.0)
        # Axis solve should inherit neutral-axis scale discipline: RGB residuals
        # can correct chromaticity, but should not jump far above the input max.
        endpoint = np.clip(inp_max / float(sample_scale), 0.0, 1.0)
        cap = 1.04 + 0.16 * endpoint * endpoint
        over = np.maximum(0.0, (out_max / np.maximum(inp_max, 1.0)) - cap)

        if strict_residual_mask:
            leak = np.max(np.where(forbidden_rgb, c_rgbw[:, :3], 0.0), axis=1)
            leak_den = np.maximum(axis, 512.0)
            # Small absolute/common-relative leakage is allowed for quantisation and
            # measured white correction.  Larger leakage gets expensive so the
            # solver does not switch from RBW/RGW/GBW into a full RGBW branch that
            # treats a common channel as a new residual axis.
            leak_tol = 48.0 + 0.0040 * leak_den
            leak_excess = np.maximum(0.0, leak - leak_tol) / leak_den
            leak_ratio = leak / leak_den
        else:
            leak = np.zeros(N, dtype=np.float64)
            leak_ratio = np.zeros(N, dtype=np.float64)
            leak_excess = np.zeros(N, dtype=np.float64)
        return de, xy, y_log + 0.5 * rel_y, resid, over * over, leak, leak_ratio, leak_excess

    def _score(c_rgbw: np.ndarray, c_xyz: np.ndarray, source_bias: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        de, xy, yerr, resid, over, leak, leak_ratio, leak_excess = _metrics(c_rgbw, c_xyz)
        w_den = np.maximum(axis, 512.0)
        w_close = np.abs(c_rgbw[:, 3] - axis) / w_den
        w_under = np.maximum(0.0, (0.88 * axis - c_rgbw[:, 3]) / w_den)
        # xy dominates; ΔE/Y are gates; residual/scale preserve neutral-like behaviour.
        score = (
            1250.0 * xy
            + 0.11 * de
            + 0.52 * yerr
            + 0.34 * w_close
            + 1.25 * w_under * w_under
            + 0.72 * resid
            + 8.0 * over
            + 2600.0 * leak_excess
            + 180.0 * leak_excess * leak_excess
            + float(source_bias)
        )
        score = np.where(valid, score, np.inf)
        return score, de, xy, yerr, resid, over, leak, leak_ratio, leak_excess

    endpoint_score = np.full(N, np.inf, dtype=np.float64)
    endpoint_rgbw = np.zeros((N, 4), dtype=np.float64)
    endpoint_xyz = np.zeros((N, 3), dtype=np.float64)
    endpoint_de = np.full(N, np.inf, dtype=np.float64)
    endpoint_xy = np.full(N, np.inf, dtype=np.float64)
    endpoint_yerr = np.full(N, np.inf, dtype=np.float64)
    endpoint_resid = np.full(N, np.inf, dtype=np.float64)

    def _consider_endpoint(a_rgbw: np.ndarray, a_xyz: np.ndarray, scale: np.ndarray) -> None:
        nonlocal endpoint_score, endpoint_rgbw, endpoint_xyz, endpoint_de, endpoint_xy, endpoint_yerr, endpoint_resid
        a_rgbw = np.asarray(a_rgbw, dtype=np.float64)
        a_xyz = np.asarray(a_xyz, dtype=np.float64)
        scale = np.asarray(scale, dtype=np.float64)
        finite_anchor = (a_rgbw[:, 3] > 0.0) & (a_xyz[:, 1] > 0.0) & np.isfinite(scale) & (scale > 0.0)
        c_rgbw = np.clip(a_rgbw * scale[:, None], 0.0, float(sample_scale))
        c_xyz = np.maximum(a_xyz * scale[:, None], 0.0)
        sc, de, xy, yerr, resid, _over, leak, leak_ratio, leak_excess = _score(c_rgbw, c_xyz, source_bias=0.0)
        sc = np.where(finite_anchor, sc, np.inf)
        better = sc < endpoint_score
        endpoint_score = np.where(better, sc, endpoint_score)
        endpoint_rgbw = np.where(better[:, None], c_rgbw, endpoint_rgbw)
        endpoint_xyz = np.where(better[:, None], c_xyz, endpoint_xyz)
        endpoint_de = np.where(better, de, endpoint_de)
        endpoint_xy = np.where(better, xy, endpoint_xy)
        endpoint_yerr = np.where(better, yerr, endpoint_yerr)
        endpoint_resid = np.where(better, resid, endpoint_resid)
        # Keep endpoint diagnostics for the winning scaled endpoint.
        diag["w_axis_endpoint_r"][:] = np.where(better, a_rgbw[:, 0], diag["w_axis_endpoint_r"])
        diag["w_axis_endpoint_g"][:] = np.where(better, a_rgbw[:, 1], diag["w_axis_endpoint_g"])
        diag["w_axis_endpoint_b"][:] = np.where(better, a_rgbw[:, 2], diag["w_axis_endpoint_b"])
        diag["w_axis_endpoint_w"][:] = np.where(better, a_rgbw[:, 3], diag["w_axis_endpoint_w"])
        _s = np.maximum(a_xyz.sum(axis=1), 1e-9)
        diag["w_axis_endpoint_x"][:] = np.where(better, a_xyz[:, 0] / _s, diag["w_axis_endpoint_x"])
        diag["w_axis_endpoint_y"][:] = np.where(better, a_xyz[:, 1] / _s, diag["w_axis_endpoint_y"])
        diag["w_axis_endpoint_Y"][:] = np.where(better, a_xyz[:, 1], diag["w_axis_endpoint_Y"])

    # Endpoint-mode 1: exact neutral/reference-white path.  Use the same single
    # full-W correction endpoint for every row, preserving the old neutral curve.
    if endpoint_mode == "reference_white" and w_xyz is not None and w_rgbw is not None and len(w_xyz) > 0:
        _sum = np.maximum(w_xyz.sum(axis=1), 1e-9)
        _rx = white_xyz_ref[0] / max(float(white_xyz_ref.sum()), 1e-9)
        _ry = white_xyz_ref[1] / max(float(white_xyz_ref.sum()), 1e-9)
        _x = w_xyz[:, 0] / _sum
        _y = w_xyz[:, 1] / _sum
        _full = w_rgbw[:, 3] >= (float(sample_scale) * 0.98)
        if np.any(_full):
            _cand = np.where(_full)[0]
            _j = _cand[int(np.argmin((_x[_cand] - _rx) ** 2 + (_y[_cand] - _ry) ** 2))]
            a_rgbw = np.repeat(w_rgbw[_j][None, :], N, axis=0).astype(np.float64)
            a_xyz = np.repeat(w_xyz[_j][None, :], N, axis=0).astype(np.float64)
            scale = np.clip(axis / np.maximum(a_rgbw[:, 3], 1.0), 0.0, float(sample_scale) / np.maximum(np.max(a_rgbw, axis=1), 1.0))
            _consider_endpoint(a_rgbw, a_xyz, scale)

    # Endpoint-mode 2: row-target xy.  Query W-active measured endpoints nearest
    # the full target xyY and scale the whole endpoint so W tracks min(rgb).
    elif w_key_tree is not None and w_xyz is not None and w_rgbw is not None and len(w_xyz) > 0:
        k_req = min(max(1, int(k)), len(w_xyz))
        k_eff = _auto_measured_candidate_axis_cap(N, k_req, False)
        k_eff = max(1, min(k_req, k_eff))
        # The tree itself was built with the global target-space key, so query it
        # in that same coordinate system for candidate recall.  Candidate scoring
        # below is target-axis aware via ``axis_white_xyz`` and exact xy/Y error.
        _, idx = w_key_tree.query(_target_space_key(target, white_xyz_ref), k=k_eff)
        if k_eff == 1:
            idx = idx[:, None]
        for j in range(k_eff):
            a_rgbw = np.asarray(w_rgbw[idx[:, j]], dtype=np.float64)
            a_xyz = np.asarray(w_xyz[idx[:, j]], dtype=np.float64)
            a_w = np.maximum(a_rgbw[:, 3], 1.0)
            a_max = np.maximum(np.max(a_rgbw, axis=1), 1.0)
            # Treat W-axis endpoints as measured curves, not arbitrary anchors.  In
            # the full RGBW pool we still prefer high-W/dominant endpoints, but the
            # residual+W sub-gamut pass must allow low/mid-W anchors: saturated
            # colors often have the correct RGB residual ratios with W well below
            # 18% of full scale.
            w_dominance = a_rgbw[:, 3] / a_max
            min_w_abs = 0.18 * float(sample_scale) if endpoint_min_w_abs is None else float(endpoint_min_w_abs)
            min_w_dom = 0.35 if endpoint_min_w_dominance is None else float(endpoint_min_w_dominance)
            endpoint_like = (a_rgbw[:, 3] >= min_w_abs) & (w_dominance >= min_w_dom)
            upper = float(sample_scale) / a_max

            scale_candidates: list[np.ndarray] = []
            modes = tuple(endpoint_scale_modes or ("w",))
            if "w" in modes:
                scale_candidates.append(axis / a_w)
            if "y" in modes:
                scale_candidates.append(target_Y / np.maximum(a_xyz[:, 1], 1e-9))
            if "drive" in modes:
                scale_candidates.append(inp_max / a_max)
            if "blend" in modes:
                sw = axis / a_w
                sy = target_Y / np.maximum(a_xyz[:, 1], 1e-9)
                sd = inp_max / a_max
                scale_candidates.append(np.exp(0.45 * np.log(np.maximum(sw, 1e-6)) + 0.55 * np.log(np.maximum(sy, 1e-6))))
                scale_candidates.append(np.exp(0.35 * np.log(np.maximum(sd, 1e-6)) + 0.65 * np.log(np.maximum(sy, 1e-6))))
            if not scale_candidates:
                scale_candidates.append(axis / a_w)

            for base_scale in scale_candidates:
                base_scale = np.clip(base_scale, 0.0, upper)
                for sm in (0.94, 1.00, 1.06):
                    sc = np.clip(base_scale * sm, 0.0, upper)
                    if np.any(endpoint_like):
                        _consider_endpoint(np.where(endpoint_like[:, None], a_rgbw, 0.0), np.where(endpoint_like[:, None], a_xyz, 0.0), sc)

    endpoint_ok = valid & np.isfinite(endpoint_score)
    # A W-axis endpoint is "good" if it is close enough to preserve chromaticity
    # without obviously missing Y.  These are intentionally loose; family/fixed-W
    # fallback is for truly bad endpoint curves, not for small tiebreaks.
    endpoint_good = endpoint_ok & (
        (endpoint_xy <= 0.010)
        | ((endpoint_xy <= 0.020) & (endpoint_de <= 10.0))
        | ((endpoint_xy <= 0.030) & (endpoint_de <= 6.0))
    )

    best_rgbw = endpoint_rgbw.copy()
    best_xyz = endpoint_xyz.copy()
    best_score = endpoint_score.copy()
    best_source = np.where(endpoint_ok, 1, 0)

    # Fixed-W residual fallback.  It may replace the scaled endpoint only if the
    # endpoint is not usable or the residual solve is materially better in colour.
    if allow_fixed_w_fallback and rgbw_basis is not None:
        basis = np.asarray(rgbw_basis, dtype=np.float64)
        if basis.ndim == 2 and basis.shape[0] == 3 and basis.shape[1] >= 4:
            rgb_basis = basis[:, :3]
            w_col = basis[:, 3]
            fixed_best_score = np.full(N, np.inf, dtype=np.float64)
            fixed_best_rgbw = np.zeros((N, 4), dtype=np.float64)
            fixed_best_xyz = np.zeros((N, 3), dtype=np.float64)
            fixed_de = np.full(N, np.inf, dtype=np.float64)
            fixed_xy = np.full(N, np.inf, dtype=np.float64)
            fixed_yerr = np.full(N, np.inf, dtype=np.float64)
            fixed_resid = np.full(N, np.inf, dtype=np.float64)
            for mult in (0.90, 0.98, 1.00, 1.04, 1.12):
                w = np.clip(axis * mult, 0.0, float(sample_scale))
                rem_xyz = target - w[:, None] * w_col[None, :]
                rgb_drive = _bounded_active_set_vectorised(rgb_basis, rem_xyz, sample_scale)
                cand_rgbw = np.zeros((N, 4), dtype=np.float64)
                cand_rgbw[:, :3] = rgb_drive
                cand_rgbw[:, 3] = w
                cand_xyz = cand_rgbw @ basis.T
                sc, de, xy, yerr, resid, _over, leak, leak_ratio, leak_excess = _score(cand_rgbw, cand_xyz, source_bias=2.25)
                better = sc < fixed_best_score
                fixed_best_score = np.where(better, sc, fixed_best_score)
                fixed_best_rgbw = np.where(better[:, None], cand_rgbw, fixed_best_rgbw)
                fixed_best_xyz = np.where(better[:, None], cand_xyz, fixed_best_xyz)
                fixed_de = np.where(better, de, fixed_de)
                fixed_xy = np.where(better, xy, fixed_xy)
                fixed_yerr = np.where(better, yerr, fixed_yerr)
                fixed_resid = np.where(better, resid, fixed_resid)

            fixed_valid = valid & np.isfinite(fixed_best_score)
            # Fallback if no endpoint, or if the fixed-W residual solve is
            # materially better.  Small score wins do not dislodge the smooth
            # endpoint axis because that is what preserves granularity.
            replace = fixed_valid & (
                ~endpoint_good
                | ((fixed_xy + 0.004 < endpoint_xy) & (fixed_de + 1.25 < endpoint_de))
                | ((fixed_xy <= 0.006) & (endpoint_xy > 0.018))
            )
            best_rgbw = np.where(replace[:, None], fixed_best_rgbw, best_rgbw)
            best_xyz = np.where(replace[:, None], fixed_best_xyz, best_xyz)
            best_score = np.where(replace, fixed_best_score, best_score)
            best_source = np.where(replace, 2, best_source)
            endpoint_de = np.where(replace, fixed_de, endpoint_de)
            endpoint_xy = np.where(replace, fixed_xy, endpoint_xy)
            endpoint_yerr = np.where(replace, fixed_yerr, endpoint_yerr)
            endpoint_resid = np.where(replace, fixed_resid, endpoint_resid)

    ok = valid & np.isfinite(best_score)
    out_rgbw = np.where(ok[:, None], np.clip(best_rgbw, 0.0, float(sample_scale)), out_rgbw)
    out_xyz = np.where(ok[:, None], np.maximum(best_xyz, 0.0), out_xyz)
    active = ok

    ssum = np.maximum(out_xyz.sum(axis=1), 1e-9)
    diag["w_axis_scaled_r"] = out_rgbw[:, 0]
    diag["w_axis_scaled_g"] = out_rgbw[:, 1]
    diag["w_axis_scaled_b"] = out_rgbw[:, 2]
    diag["w_axis_scaled_w"] = out_rgbw[:, 3]
    diag["w_axis_scaled_x"] = out_xyz[:, 0] / ssum
    diag["w_axis_scaled_y"] = out_xyz[:, 1] / ssum
    diag["w_axis_scaled_Y"] = out_xyz[:, 1]
    diag["w_axis_deltaE"] = endpoint_de
    diag["w_axis_xy_err"] = endpoint_xy
    diag["w_axis_y_err"] = endpoint_yerr
    diag["w_axis_residual_energy"] = endpoint_resid
    diag["w_axis_w_to_common"] = out_rgbw[:, 3] / np.maximum(axis, 1.0)
    if strict_residual_mask:
        _de, _xy, _yerr, _resid, _over, _leak, _leak_ratio, _leak_excess = _metrics(out_rgbw, out_xyz)
        diag["w_axis_common_leak_max"] = _leak
        diag["w_axis_common_leak_ratio"] = _leak_ratio
        diag["w_axis_common_leak_excess"] = _leak_excess
    diag["w_axis_source"] = best_source
    diag["w_axis_selected"] = active

    return (out_rgbw, out_xyz, active, diag) if return_diagnostics else (out_rgbw, out_xyz, active)



def solve_neutral_axis_measured(
    xyz_targets: np.ndarray,          # (N, 3) — neutral node targets
    neutral_key_tree: "cKDTree",      # KD-tree over neutral Lab/xy/logY keys
    neutral_xyz:  np.ndarray,         # (M, 3) measured XYZ
    neutral_rgbw: np.ndarray,         # (M, 4) measured RGBW
    white_xyz_ref: np.ndarray,        # (3,)
    sample_scale: float,
    k: int = 32,
    residual_weights: np.ndarray | None = None,
    neutral_drive: np.ndarray | None = None,
) -> np.ndarray:                      # (N, 4) RGBW
    """Dedicated W-dominant solver for exact/near neutral-axis nodes.

    This is intentionally *not* the generic family score.  Neutral targets need
    a lexicographic preference:
      1) stay close to D65 / target Lab,
      2) stay close in Y,
      3) maximize absolute W drive,
      4) minimize RGB residual, with G residual penalized most strongly.

    The previous implementation queried only by log(Y) and soft-blended the
    nearest captures.  That could pick similarly-bright but chromatically poor
    W states, or blend unrelated correction strategies into a non-measured
    residual.  Here we query in a target-space key and choose the best measured
    candidate using a continuous score.
    """
    N = len(xyz_targets)
    if len(neutral_xyz) == 0:
        return np.zeros((N, 4), dtype=np.float64)

    # Prefer the shared W-axis core over nearest-neighbor measured selection.
    # In reference-white mode this exactly preserves the old proven neutral
    # behaviour: choose the full-W endpoint closest to the configured white point
    # and scale that full RGBW correction vector by neutral_drive.
    if neutral_drive is not None and len(neutral_drive) == N:
        _axis_rgbw, _axis_xyz, _axis_ok = solve_w_axis_measured(
            xyz_targets,
            np.asarray(neutral_drive, dtype=np.float64),
            white_xyz_ref,
            sample_scale,
            w_key_tree=neutral_key_tree,
            w_xyz=neutral_xyz,
            w_rgbw=neutral_rgbw,
            endpoint_mode="reference_white",
            residual_weights=residual_weights,
            k=k,
            allow_fixed_w_fallback=False,
        )
        if np.all(_axis_ok):
            return np.clip(_axis_rgbw, 0.0, sample_scale)

    lab_tgt = _xyz_to_lab_vectorised(np.maximum(xyz_targets, 0.0), white_xyz_ref)
    tgt_Y = np.maximum(xyz_targets[:, 1], 1e-9)
    tgt_sum = np.maximum(xyz_targets.sum(axis=1), 1e-9)
    tgt_x = xyz_targets[:, 0] / tgt_sum
    tgt_y = xyz_targets[:, 1] / tgt_sum
    tgt_logY = np.log(tgt_Y)

    # Same metric used by worker neutral tree construction.
    qkey = np.column_stack([
        tgt_x * 120.0,
        tgt_y * 120.0,
        lab_tgt[:, 0] * 0.03,
        lab_tgt[:, 1],
        lab_tgt[:, 2],
        tgt_logY * 0.20,
    ])

    k_eff = min(max(1, k), len(neutral_xyz))
    _, idx = neutral_key_tree.query(qkey, k=k_eff)
    if k_eff == 1:
        idx = idx[:, None]

    cand_xyz = neutral_xyz[idx]       # (N, k, 3)
    cand_rgbw = neutral_rgbw[idx]     # (N, k, 4)

    lab_cand = _xyz_to_lab_vectorised(
        cand_xyz.reshape(-1, 3), white_xyz_ref
    ).reshape(N, k_eff, 3)
    de = np.linalg.norm(lab_cand - lab_tgt[:, None, :], axis=2)

    cand_Y = np.maximum(cand_xyz[:, :, 1], 1e-9)
    rel_Y = np.abs(cand_Y - tgt_Y[:, None]) / tgt_Y[:, None]

    r_n = cand_rgbw[:, :, 0] / sample_scale
    g_n = cand_rgbw[:, :, 1] / sample_scale
    b_n = cand_rgbw[:, :, 2] / sample_scale
    w_n = cand_rgbw[:, :, 3] / sample_scale

    if residual_weights is None:
        residual_weights = np.array([1.0, 4.0, 1.0], dtype=np.float64)
    residual_weights = np.asarray(residual_weights, dtype=np.float64)
    if residual_weights.shape != (3,):
        residual_weights = np.array([1.0, 4.0, 1.0], dtype=np.float64)

    # Continuous ΔE term: dE below 3 is not "free" because the goal is average
    # dE <= 2.  Residual penalties are derived from the measured W→D65
    # correction endpoint rather than hardcoded to this particular W diode.
    # Low-neutral interpolation from the dense LUT was still under-using W
    # even when the exact probe selected the desired W-heavy capture.  Nudge
    # the neutral curve toward the W-dominant measured path more strongly while
    # keeping continuous dE/Y as the primary constraints.
    score = (
        140.0 * de
        + 220.0 * rel_Y
        -  85.0 * w_n
        +  16.0 * (
            residual_weights[0] * r_n * r_n +
            residual_weights[1] * g_n * g_n +
            residual_weights[2] * b_n * b_n
        )
    )

    best = np.argmin(score, axis=1)
    out = cand_rgbw[np.arange(N), best]
    return np.clip(out, 0.0, sample_scale)


def solve_w_dominant_target_axis(
    xyz_targets: np.ndarray,
    preferred_w_drive: np.ndarray,
    white_xyz_ref: np.ndarray,
    sample_scale: float,
    rgbw_basis: np.ndarray | None = None,
    w_key_tree: "cKDTree | None" = None,
    w_xyz: np.ndarray | None = None,
    w_rgbw: np.ndarray | None = None,
    residual_weights: np.ndarray | None = None,
    input_rgb: np.ndarray | None = None,
    family_key_tree: dict[str, "cKDTree | None"] | None = None,
    family_xyz: dict[str, np.ndarray] | None = None,
    family_rgbw: dict[str, np.ndarray] | None = None,
    family_bases: dict[str, np.ndarray] | None = None,
    k: int = 48,
    return_diagnostics: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Target-aware mixed RGB W-axis route.

    This is the shared neutral/W-axis solver for non-neutral min(rgb)>0 rows.
    It keeps the neutral-axis mechanics — measured W endpoint, whole-vector
    scaling, W tracking common, and fixed-W residual fallback — but tries the
    residual+W sub-gamut first.  For example, after subtracting common:

        residual R  -> RW
        residual RB -> RBW

    Only rows that cannot satisfy the target xy/Y in RW/GW/BW/RGW/RBW/GBW fall
    through to the full RGBW endpoint/fixed-W solve.  This prevents the full
    RGBW candidate from deleting required residual channels simply because a
    free four-channel solve can hit Y.
    """
    target = np.asarray(xyz_targets, dtype=np.float64)
    N = len(target)

    def _call_core(rows: np.ndarray | None, *,
                   key_tree, xyz, rgbw, basis, allow_fixed: bool,
                   strict_mask: bool = False,
                   endpoint_min_w_abs: float | None = None,
                   endpoint_min_w_dominance: float | None = None,
                   endpoint_scale_modes: tuple[str, ...] = ("w",),
                   ret_diag: bool = True):
        if rows is None:
            t = target
            axis = preferred_w_drive
            inp = input_rgb
        else:
            t = target[rows]
            axis = np.asarray(preferred_w_drive, dtype=np.float64)[rows]
            inp = None if input_rgb is None else np.asarray(input_rgb, dtype=np.float64)[rows]
        forbid_mask = None
        if strict_mask and inp is not None:
            _primary, _rm = _mixed_common_primary_w_families(inp, sample_scale)
            forbid_mask = (7 & (~_rm)).astype(np.int32)
        return solve_w_axis_measured(
            t,
            axis,
            white_xyz_ref,
            sample_scale,
            w_key_tree=key_tree,
            w_xyz=xyz,
            w_rgbw=rgbw,
            endpoint_mode="target_xy",
            rgbw_basis=basis,
            residual_weights=residual_weights,
            input_rgb=inp,
            forbidden_rgb_mask=forbid_mask,
            strict_residual_mask=strict_mask,
            endpoint_min_w_abs=endpoint_min_w_abs,
            endpoint_min_w_dominance=endpoint_min_w_dominance,
            endpoint_scale_modes=endpoint_scale_modes,
            k=k,
            allow_fixed_w_fallback=allow_fixed,
            return_diagnostics=ret_diag,
        )

    # No family information: preserve the previous full-RGBW behaviour.
    have_family_pools = (
        input_rgb is not None and family_key_tree is not None and
        family_xyz is not None and family_rgbw is not None
    )
    if not have_family_pools:
        return _call_core(None, key_tree=w_key_tree, xyz=w_xyz, rgbw=w_rgbw,
                          basis=rgbw_basis, allow_fixed=True,
                          ret_diag=return_diagnostics)

    inp = np.asarray(input_rgb, dtype=np.float64)
    primary_family, residual_mask = _mixed_common_primary_w_families(inp, sample_scale)

    out_rgbw = np.zeros((N, 4), dtype=np.float64)
    out_xyz = np.zeros((N, 3), dtype=np.float64)
    out_ok = np.zeros(N, dtype=bool)
    out_score = np.full(N, np.inf, dtype=np.float64)
    out_family = np.full(N, -1, dtype=np.int32)
    out_rank = np.zeros(N, dtype=np.int32)  # 1 primary sub-gamut, 2 secondary sub-gamut, 9 full RGBW
    out_diag: dict[str, np.ndarray] = {}

    # Best sub-gamut candidate even when it misses the normal acceptance gate.
    # This prevents a row with a close RBW/RGW/GBW measured curve from jumping to
    # full RGBW simply because the sub-gamut was slightly outside a loose Y gate.
    reserve_rgbw = np.zeros((N, 4), dtype=np.float64)
    reserve_xyz = np.zeros((N, 3), dtype=np.float64)
    reserve_score = np.full(N, np.inf, dtype=np.float64)
    reserve_xy = np.full(N, np.inf, dtype=np.float64)
    reserve_de = np.full(N, np.inf, dtype=np.float64)
    reserve_yerr = np.full(N, np.inf, dtype=np.float64)
    reserve_family = np.full(N, -1, dtype=np.int32)
    reserve_rank = np.zeros(N, dtype=np.int32)
    reserve_diag: dict[str, np.ndarray] = {}

    def _diag_fill_for_key(dk: str, dv: np.ndarray):
        """Default full-row diagnostic array matching a candidate diagnostic.

        Different W-axis candidate sources expose different diagnostic keys.
        The first stored candidate may be the normal endpoint solve while a
        later ratio-ridge candidate adds ``scaled_anchor_*`` fields.  The diag
        accumulator therefore has to merge keys incrementally rather than
        freezing to the first candidate's schema.
        """
        if dv.dtype.kind == "f":
            fill = np.nan
            # These are numeric source/category codes, not measured source
            # coordinates, so zero is the safest missing value.
            if dk in {"w_axis_source", "scaled_anchor_scale_mode", "scaled_anchor_blocked_reason"}:
                fill = 0.0
            return np.full(N, fill, dtype=dv.dtype)
        if dv.dtype.kind in "iu":
            return np.zeros(N, dtype=dv.dtype)
        if dv.dtype.kind == "b":
            return np.zeros(N, dtype=bool)
        return np.zeros(N, dtype=dv.dtype)

    def _ensure_diag(template: dict[str, np.ndarray]) -> None:
        nonlocal out_diag
        for dk, dv in template.items():
            if dk not in out_diag:
                out_diag[dk] = _diag_fill_for_key(dk, dv)
        if "w_axis_family" not in out_diag:
            out_diag["w_axis_family"] = np.full(N, -1, dtype=np.int32)
        if "w_axis_subgamut_rank" not in out_diag:
            out_diag["w_axis_subgamut_rank"] = np.zeros(N, dtype=np.int32)

    def _cand_score(diag: dict[str, np.ndarray], family_bias: np.ndarray) -> np.ndarray:
        xy = np.asarray(diag.get("w_axis_xy_err"), dtype=np.float64)
        de = np.asarray(diag.get("w_axis_deltaE"), dtype=np.float64)
        yerr = np.asarray(diag.get("w_axis_y_err"), dtype=np.float64)
        resid = np.asarray(diag.get("w_axis_residual_energy"), dtype=np.float64)
        # xy dominates; Y must be sane; family_bias keeps the residual-derived
        # family ahead of broader or mismatched sub-gamuts when colour is close.
        return 1250.0 * xy + 0.10 * de + 0.55 * yerr + 0.42 * resid + family_bias

    def _cand_good(diag: dict[str, np.ndarray], ok: np.ndarray, *, secondary: bool = False) -> np.ndarray:
        xy = np.asarray(diag.get("w_axis_xy_err"), dtype=np.float64)
        de = np.asarray(diag.get("w_axis_deltaE"), dtype=np.float64)
        yerr = np.asarray(diag.get("w_axis_y_err"), dtype=np.float64)
        if secondary:
            return ok & (
                ((xy <= 0.0075) & (yerr <= 1.55)) |
                ((xy <= 0.0120) & (de <= 6.0) & (yerr <= 1.05))
            )
        return ok & (
            ((xy <= 0.0100) & (yerr <= 1.45)) |
            ((xy <= 0.0065) & (yerr <= 2.05)) |
            ((xy <= 0.0180) & (de <= 6.0) & (yerr <= 1.10))
        )

    def _store(rows: np.ndarray, local_sel: np.ndarray, cand_rgbw: np.ndarray,
               cand_xyz: np.ndarray, cand_diag: dict[str, np.ndarray],
               family_name: str, rank: int, score: np.ndarray) -> None:
        nonlocal out_rgbw, out_xyz, out_ok, out_score, out_family, out_rank
        if not np.any(local_sel):
            return
        _ensure_diag(cand_diag)
        g_rows = rows[local_sel]
        l_rows = np.where(local_sel)[0]
        out_rgbw[g_rows] = cand_rgbw[l_rows]
        out_xyz[g_rows] = cand_xyz[l_rows]
        out_ok[g_rows] = True
        out_score[g_rows] = score[l_rows]
        fam_i = _FAMILY_INDEX_BY_NAME.get(family_name, -1)
        out_family[g_rows] = fam_i
        out_rank[g_rows] = rank
        for dk, dv in cand_diag.items():
            out_diag[dk][g_rows] = dv[l_rows]
        out_diag["w_axis_family"][g_rows] = fam_i
        out_diag["w_axis_subgamut_rank"][g_rows] = rank

    def _reserve(rows: np.ndarray, local_sel: np.ndarray, cand_rgbw: np.ndarray,
                 cand_xyz: np.ndarray, cand_diag: dict[str, np.ndarray],
                 family_name: str, rank: int, score: np.ndarray) -> None:
        nonlocal reserve_rgbw, reserve_xyz, reserve_score, reserve_xy, reserve_de, reserve_yerr, reserve_family, reserve_rank, reserve_diag
        if not np.any(local_sel):
            return
        for dk, dv in cand_diag.items():
            if dk not in reserve_diag:
                reserve_diag[dk] = _diag_fill_for_key(dk, dv)
        g_rows = rows[local_sel]
        l_rows = np.where(local_sel)[0]
        better = score[l_rows] < reserve_score[g_rows]
        if not np.any(better):
            return
        g_b = g_rows[better]
        l_b = l_rows[better]
        reserve_rgbw[g_b] = cand_rgbw[l_b]
        reserve_xyz[g_b] = cand_xyz[l_b]
        reserve_score[g_b] = score[l_b]
        reserve_xy[g_b] = np.asarray(cand_diag.get("w_axis_xy_err"), dtype=np.float64)[l_b]
        reserve_de[g_b] = np.asarray(cand_diag.get("w_axis_deltaE"), dtype=np.float64)[l_b]
        reserve_yerr[g_b] = np.asarray(cand_diag.get("w_axis_y_err"), dtype=np.float64)[l_b]
        reserve_family[g_b] = _FAMILY_INDEX_BY_NAME.get(family_name, -1)
        reserve_rank[g_b] = rank
        for dk, dv in cand_diag.items():
            reserve_diag[dk][g_b] = dv[l_b]

    # Primary residual+W family pass.  This is the important structural change:
    # do not let full RGBW compete until the natural sub-gamut has failed.
    for fk in _W_AXIS_SUBGAMUT_FAMILIES:
        rows = np.where(primary_family == fk)[0]
        if len(rows) == 0:
            continue
        if family_key_tree.get(fk) is None or fk not in family_xyz or fk not in family_rgbw or len(family_xyz[fk]) == 0:
            continue
        cand_rgbw, cand_xyz, cand_ok, cand_diag = _call_core(
            rows,
            key_tree=family_key_tree.get(fk),
            xyz=family_xyz[fk],
            rgbw=family_rgbw[fk],
            basis=None,
            allow_fixed=False,
            strict_mask=True,
            endpoint_min_w_abs=1.0,
            endpoint_min_w_dominance=0.0,
            endpoint_scale_modes=("w", "y", "drive", "blend"),
            ret_diag=True,
        )
        fam_bias = np.zeros(len(rows), dtype=np.float64)
        sc = _cand_score(cand_diag, fam_bias)
        good = _cand_good(cand_diag, cand_ok, secondary=False)
        _reserve(rows, cand_ok & np.isfinite(sc), cand_rgbw, cand_xyz, cand_diag, fk, 1, sc)
        _store(rows, good, cand_rgbw, cand_xyz, cand_diag, fk, 1, sc)

        # Ratio-ridge search: if the correct residual+W family has a measured
        # low/mid-Y capture with the right xy, scale that whole RGBW vector
        # linearly before declaring the sub-gamut failed.  This is the direct
        # fix for h281/h090-style value ramps where the correct RBW/RGW/GBW
        # ratio exists at lower Y but the normal target-space KNN query misses it.
        forbid = (7 & (~residual_mask[rows])).astype(np.int32)
        rr_rgbw, rr_xyz, rr_ok, rr_diag = _find_scaled_ratio_anchor_vectorised(
            target[rows], family_xyz[fk], family_rgbw[fk], white_xyz_ref, sample_scale,
            input_rgb=inp[rows], input_masks=np.full(len(rows), 7, dtype=np.int32),
            family_rgb_mask=_FAMILY_RGB_MASK_BY_NAME.get(fk, 0),
            role="mixed_w_axis", axis_drive=np.asarray(preferred_w_drive, dtype=np.float64)[rows],
            forbidden_rgb_mask=forbid, k=max(96, k * 2),
        )
        rr_sc = _cand_score(rr_diag, np.full(len(rows), -0.10, dtype=np.float64))
        # v33 recovery: keep ratio-ridge anchors as diagnostics only.  The replay
        # verifier showed they were over-selected and replaced many passing RGBW
        # rows with bad scaled sub-gamut rows.  Once replay scoring is stable,
        # these can become candidates again under pass/fail feedback control.
        if False:
            rr_good = rr_ok & _cand_good(rr_diag, rr_ok, secondary=False)
            _reserve(rows, rr_ok & np.isfinite(rr_sc), rr_rgbw, rr_xyz, rr_diag, fk, 3, rr_sc)
            rr_better = rr_good & (rr_sc < out_score[rows])
            _store(rows, rr_better, rr_rgbw, rr_xyz, rr_diag, fk, 3, rr_sc)

    # Secondary sub-gamut pass only for rows whose primary family could not hit
    # target xy/Y.  This is a sparse-data recovery path; thresholds are tighter
    # than the primary pass so an unrelated family cannot displace full RGBW.
    remaining = np.where(~out_ok)[0]
    if len(remaining):
        for fk in _W_AXIS_SUBGAMUT_FAMILIES:
            if family_key_tree.get(fk) is None or fk not in family_xyz or fk not in family_rgbw or len(family_xyz[fk]) == 0:
                continue
            fam_mask = _FAMILY_RGB_MASK_BY_NAME.get(fk, 0)
            rm_all = residual_mask[remaining]
            # Secondary W families are now residual-mask legal only.  For a
            # residual RB row, allow RBW/RW/BW; do not allow RGW/GW/GBW to turn
            # the common G channel into a fake residual.  Full RGBW remains the
            # explicit emergency fallback below.
            legal = ((fam_mask & ~rm_all) == 0) & (fam_mask != 0)
            rows = remaining[legal]
            if len(rows) == 0:
                continue
            cand_rgbw, cand_xyz, cand_ok, cand_diag = _call_core(
                rows,
                key_tree=family_key_tree.get(fk),
                xyz=family_xyz[fk],
                rgbw=family_rgbw[fk],
                basis=None,
                allow_fixed=False,
                strict_mask=True,
                endpoint_min_w_abs=1.0,
                endpoint_min_w_dominance=0.0,
                endpoint_scale_modes=("w", "y", "drive", "blend"),
                ret_diag=True,
            )
            rm = residual_mask[rows]
            exact = primary_family[rows] == fk
            covers = (fam_mask & rm) == rm
            subset = (fam_mask & rm) == fam_mask
            fam_bias = np.where(exact, 0.02, np.where(covers, 0.16, np.where(subset, 0.30, 0.85)))
            sc = _cand_score(cand_diag, fam_bias)
            good = _cand_good(cand_diag, cand_ok, secondary=True)
            _reserve(rows, cand_ok & np.isfinite(sc), cand_rgbw, cand_xyz, cand_diag, fk, 2, sc)
            better = good & (sc < out_score[rows])
            _store(rows, better, cand_rgbw, cand_xyz, cand_diag, fk, 2, sc)

            forbid = (7 & (~residual_mask[rows])).astype(np.int32)
            rr_rgbw, rr_xyz, rr_ok, rr_diag = _find_scaled_ratio_anchor_vectorised(
                target[rows], family_xyz[fk], family_rgbw[fk], white_xyz_ref, sample_scale,
                input_rgb=inp[rows], input_masks=np.full(len(rows), 7, dtype=np.int32),
                family_rgb_mask=fam_mask, role="mixed_w_axis",
                axis_drive=np.asarray(preferred_w_drive, dtype=np.float64)[rows],
                forbidden_rgb_mask=forbid, k=max(96, k * 2),
            )
            rr_sc = _cand_score(rr_diag, fam_bias - 0.05)
            # v33 recovery: diagnostic-only secondary ratio-ridge anchors.
            if False:
                rr_good = rr_ok & _cand_good(rr_diag, rr_ok, secondary=True)
                _reserve(rows, rr_ok & np.isfinite(rr_sc), rr_rgbw, rr_xyz, rr_diag, fk, 4, rr_sc)
                rr_better = rr_good & (rr_sc < out_score[rows])
                _store(rows, rr_better, rr_rgbw, rr_xyz, rr_diag, fk, 4, rr_sc)
        remaining = np.where(~out_ok)[0]

    # Full RGBW is the final fallback.  Its fixed-W residual solve remains useful
    # when no sub-gamut has enough Y or chromatic reach.
    if len(remaining):
        full_rgbw, full_xyz, full_ok, full_diag = _call_core(
            remaining,
            key_tree=w_key_tree,
            xyz=w_xyz,
            rgbw=w_rgbw,
            basis=rgbw_basis,
            allow_fixed=True,
            strict_mask=True,
            endpoint_min_w_abs=0.08 * float(sample_scale),
            endpoint_min_w_dominance=0.12,
            endpoint_scale_modes=("w", "y", "blend"),
            ret_diag=True,
        )
        _ensure_diag(full_diag)
        good = full_ok
        # If full RGBW injects a common/min channel but a close sub-gamut reserve
        # exists, keep the sub-gamut curve.  This targets the h281_s075_v045→v060
        # style branch flip: the measured RBW/RGW/GBW ratios are correct, but a
        # full RGBW residual solve starts treating the min channel as an active
        # residual and the value ramp becomes nonlinear.
        full_leak_ratio = np.asarray(full_diag.get("w_axis_common_leak_ratio", np.zeros(len(remaining))), dtype=np.float64)
        full_leak_excess = np.asarray(full_diag.get("w_axis_common_leak_excess", np.zeros(len(remaining))), dtype=np.float64)
        r_rows = remaining
        reserve_available = np.isfinite(reserve_score[r_rows])
        reserve_goodish = reserve_available & (
            ((reserve_xy[r_rows] <= 0.014) & (reserve_yerr[r_rows] <= 2.40)) |
            ((reserve_xy[r_rows] <= 0.020) & (reserve_de[r_rows] <= 9.0) & (reserve_yerr[r_rows] <= 1.60))
        )
        full_leaky = (full_leak_ratio > 0.018) | (full_leak_excess > 0.0045)
        use_reserve = good & reserve_goodish & full_leaky
        if np.any(use_reserve):
            g_res = remaining[use_reserve]
            out_rgbw[g_res] = reserve_rgbw[g_res]
            out_xyz[g_res] = reserve_xyz[g_res]
            out_ok[g_res] = True
            out_family[g_res] = reserve_family[g_res]
            out_rank[g_res] = reserve_rank[g_res]
            if reserve_diag:
                _ensure_diag(reserve_diag)
                for dk, dv in reserve_diag.items():
                    out_diag[dk][g_res] = dv[g_res]
                out_diag["w_axis_family"][g_res] = reserve_family[g_res]
                out_diag["w_axis_subgamut_rank"][g_res] = reserve_rank[g_res]
        good_full = good & ~use_reserve
        g_rows = remaining[good_full]
        l_rows = np.where(good_full)[0]
        out_rgbw[g_rows] = full_rgbw[l_rows]
        out_xyz[g_rows] = full_xyz[l_rows]
        out_ok[g_rows] = True
        out_family[g_rows] = _FAMILY_INDEX_BY_NAME.get("rgbw", 14)
        out_rank[g_rows] = 9
        for dk, dv in full_diag.items():
            out_diag[dk][g_rows] = dv[l_rows]
        out_diag["w_axis_family"][g_rows] = _FAMILY_INDEX_BY_NAME.get("rgbw", 14)
        out_diag["w_axis_subgamut_rank"][g_rows] = 9

    if not out_diag:
        # Preserve diagnostics shape for all-invalid calls.
        _dummy_rgbw, _dummy_xyz, _dummy_ok, _dummy_diag = _call_core(
            None, key_tree=w_key_tree, xyz=w_xyz, rgbw=w_rgbw,
            basis=rgbw_basis, allow_fixed=True, ret_diag=True,
        )
        _ensure_diag(_dummy_diag)

    out_diag["w_axis_family"] = out_family
    out_diag["w_axis_subgamut_rank"] = out_rank
    out_diag["w_axis_selected"] = out_ok

    return (out_rgbw, out_xyz, out_ok, out_diag) if return_diagnostics else (out_rgbw, out_xyz, out_ok)


# ---------------------------------------------------------------------------
# Common-min RGB decomposition candidate
# ---------------------------------------------------------------------------

def _build_common_min_candidate_vectorised(
    rgb_flat: np.ndarray,
    xyz_targets: np.ndarray,
    raw_rgb_basis: np.ndarray | None,
    target_rgb_basis: np.ndarray | None,
    y_scale: float,
    family_bases: dict,
    family_key_tree: dict[str, cKDTree | None],
    family_xyz: dict[str, np.ndarray],
    family_rgbw: dict[str, np.ndarray],
    white_xyz_ref: np.ndarray,
    sample_scale: float,
    neutral_key_tree: cKDTree | None,
    neutral_xyz: np.ndarray | None,
    neutral_rgbw: np.ndarray | None,
    neutral_residual_weights: np.ndarray | None = None,
    mixed_w_key_tree: cKDTree | None = None,
    mixed_w_xyz: np.ndarray | None = None,
    mixed_w_rgbw: np.ndarray | None = None,
    target_transform_matrix: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Solve mixed RGB as a target-aware W-dominant axis candidate.

    For inputs with a real shared RGB component ``common=min(R,G,B)``, this is
    the generalized neutral-axis route: solve the *full* target XYZ/xyY in one
    pass while treating W near the shared component as the preferred luminance
    carrier and RGB as residual chromatic correction.  It is intentionally not
    the old split path:

        common neutral solve + independent residual solve

    and it is not merely a weak W bonus on the normal family solver.  The W band
    is centred on the actual common component so the resulting LUT keeps the same
    monotonic/granular behaviour as the proven neutral-axis sweep, while the RGB
    residuals remain free to move as needed to hit the full off-axis target.

    Exact R=G=B neutrals are routed elsewhere through ``solve_neutral_axis_measured``
    against the configured reference white.  This function is for non-neutral
    all-RGB values where min(rgb)>0.
    """
    N = len(rgb_flat)
    out_rgbw = np.zeros((N, 4), dtype=np.float64)
    out_xyz = np.zeros((N, 3), dtype=np.float64)
    active = np.zeros(N, dtype=bool)
    source = np.zeros(N, dtype=np.int32)  # 0 none, 4 target-aware W-dominant axis

    if N == 0 or raw_rgb_basis is None or target_rgb_basis is None:
        return out_rgbw, out_xyz, active, source
    if "rgbw" not in family_bases:
        return out_rgbw, out_xyz, active, source

    basis = np.asarray(family_bases["rgbw"], dtype=np.float64)
    if basis.shape[1] < 4:
        return out_rgbw, out_xyz, active, source

    rgb = np.asarray(rgb_flat, dtype=np.float64)
    ch_max = np.maximum(np.max(rgb, axis=1), 1.0)
    common = np.minimum.reduce([rgb[:, 0], rgb[:, 1], rgb[:, 2]])
    common_frac = np.clip(common / ch_max, 0.0, 1.0)
    spread = ch_max - common
    # This candidate should be available for every non-neutral all-RGB value
    # containing a shared component.  Very tiny common components get a weak gate
    # but still generate a candidate for diagnostics/competition.
    all3 = (rgb[:, 0] > 0.0) & (rgb[:, 1] > 0.0) & (rgb[:, 2] > 0.0)
    non_neutralish = spread > max(1e-6, 0.002 * float(sample_scale))
    gate = np.clip((common_frac - 0.010) / 0.340, 0.0, 1.0)
    active = all3 & non_neutralish & (common > 0.0)
    if not np.any(active):
        return out_rgbw, out_xyz, active, source

    # Route through the generalized neutral/W axis solver directly.  This is
    # intentionally the same decomposition model as the exact neutral path, but
    # with the target xyY supplied by the current RGB input instead of hardcoded
    # to the configured reference white.  W is anchored to min(R,G,B); RGB is the
    # residual correction needed to hit the full target.
    common_rgbw, common_xyz, common_ok = solve_w_dominant_target_axis(
        xyz_targets,
        common,
        white_xyz_ref,
        sample_scale,
        rgbw_basis=family_bases.get("rgbw"),
        w_key_tree=mixed_w_key_tree,
        w_xyz=mixed_w_xyz,
        w_rgbw=mixed_w_rgbw,
        residual_weights=neutral_residual_weights,
        input_rgb=rgb,
        family_key_tree=family_key_tree,
        family_xyz=family_xyz,
        family_rgbw=family_rgbw,
        family_bases=family_bases,
        k=64,
    )

    ok = active & common_ok
    out_rgbw = np.where(ok[:, None], np.clip(common_rgbw, 0.0, float(sample_scale)), out_rgbw)
    out_xyz = np.where(ok[:, None], np.maximum(common_xyz, 0.0), out_xyz)
    source = np.where(ok, 5, source)  # 5 = generalized neutral/W target axis
    active = ok
    return out_rgbw, out_xyz, active, source

# ---------------------------------------------------------------------------
# Main solver: Items 1–5 combined

def solve_rgbw_by_family_hull(
    xyz_targets: np.ndarray,           # (N, 3) — neutral-blended, y_scale applied
    family_bases: dict[str, np.ndarray],
    family_tri:   dict[str, Delaunay | None],  # per-family Delaunay (None if < 5 pts)
    family_tree:  dict[str, cKDTree  | None],  # per-family XYZ cKDTree
    family_key_tree: dict[str, cKDTree | None], # per-family target-space cKDTree
    family_xyz:   dict[str, np.ndarray],       # per-family measured XYZ
    family_rgbw:  dict[str, np.ndarray],       # per-family measured RGBW
    white_xyz_ref: np.ndarray,         # (3,)
    sample_scale: float,
    delta_e_tiebreak: float = 2.0,
    chroma_gate: float = 15.0,
    zero_mask: np.ndarray | None = None,
    input_masks: np.ndarray | None = None,     # (N,) int32 — R/G/B active bits
    input_rgb: np.ndarray | None = None,       # (N, 3) original input drives
    neutral_weights: np.ndarray | None = None, # (N,) float — 1=neutral, 0=sat
    neutral_residual_weights: np.ndarray | None = None,
    mixed_w_key_tree: cKDTree | None = None,
    mixed_w_xyz: np.ndarray | None = None,
    mixed_w_rgbw: np.ndarray | None = None,
    raw_rgb_basis: np.ndarray | None = None,
    target_rgb_basis: np.ndarray | None = None,
    y_scale: float = 1.0,
    target_transform_matrix: np.ndarray | None = None,
    neutral_key_tree: cKDTree | None = None,
    neutral_xyz: np.ndarray | None = None,
    neutral_rgbw: np.ndarray | None = None,
) -> np.ndarray:                       # (N, 4) RGBW
    """Physically-correct RGBW decomposition over measured emitter sub-gamuts.

    For each of the up-to-15 measured emitter families:

    **Stage 1 — Sub-gamut hull lookup (README items 4 + 5):**
      a. Attempt barycentric interpolation inside the family's Delaunay
         triangulation of its measured captures.  This is the physical forward
         model — the LUT is a cached lookup into measured RGBW manifolds.
      b. Out-of-hull nodes fall back to inverse-distance-weighted KNN on the
         same capture subset.  KNN also acts as the fallback when the family
         has fewer than 5 points for triangulation.

    **Stage 2 — Bounded active-set NNLS (README item 2):**
      Used when both Delaunay and KNN produce drives outside [0, sample_scale],
      and as a parallel cross-check for families where no measured captures
      exist (uses the fitted linear family basis instead).

      Unlike the old 2^n subset enumeration, 3^n active-set enumeration
      includes a "saturate at 65535" state.  This prevents gamut-boundary
      primaries (red, yellow, …) from being rejected as infeasible when the
      correct solution is R=65535 or similar.

    **Stage 3 — Scoring (README item 3):**
      Primary: full CIELAB ΔE (L*, a*, b*).  Luminance error is included —
      a dimmer or brighter candidate cannot hide behind good chromaticity.

      W preference gate: W reward applies only when the target chromaticity
      is within `chroma_gate` CIELAB C* units of neutral.  For saturated
      primaries/secondaries the reward is zero or close to zero, so W cannot
      override the correct primary family.

      Score = full_ΔE  −  delta_e_tiebreak × w_frac × w_reward_factor

    **Item 1 (luminance scaling)** is applied upstream in ``_solve_r_slice``
    by multiplying xyz_targets by y_scale before calling this function.
    """
    N      = len(xyz_targets)
    lab_tgt = _xyz_to_lab_vectorised(xyz_targets, white_xyz_ref)  # (N, 3)
    C_tgt   = np.linalg.norm(lab_tgt[:, 1:], axis=1)              # (N,) chroma
    # W reward factor: 1.0 for neutral (C→0), 0.0 at chroma_gate and above
    w_reward = np.clip(1.0 - C_tgt / max(chroma_gate, 1e-6), 0.0, 1.0)  # (N,)

    # Topology gate constants
    R_BIT, G_BIT, B_BIT = 1, 2, 4
    # Topology penalty for families whose RGB channels are not a subset of the
    # active input channels.  A large but finite penalty keeps such families as
    # last-resort fallbacks rather than hard exclusions.
    TOPO_PENALTY = 500.0

    # Inactive-channel weight: how strongly to penalise driving an RGB channel
    # that the input did not request.  Expressed as equivalent ΔE units for a
    # fully-driven inactive channel (normalised 0→1).
    INACTIVE_WEIGHT = 10.0
    # Relative luminance error coefficient (fraction, e.g. 0.1 = 10% Y error)
    Y_ERR_WEIGHT = 5.0

    target_Y = np.maximum(xyz_targets[:, 1], 1e-9)               # (N,)
    chroma_priority = np.clip((C_tgt - 8.0) / 28.0, 0.0, 1.0)   # high C: xy dominates

    def _candidate_colour_term(
        xyz_ach_cand: np.ndarray,
        rows: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return colour/Y terms for either the full slice or a row subset.

        v23 introduced an exact-dual rescue pass that scores only the subset of
        rows belonging to RG/RB/GB.  The original closure always compared against
        full-slice target arrays, which caused broadcasting failures such as
        (254, 3) vs (65529,).  Passing ``rows`` keeps all target-side arrays on
        the same subset as the candidate XYZ.
        """
        if rows is None:
            lab_tgt_use = lab_tgt
            xyz_tgt_use = xyz_targets
            target_Y_use = target_Y
            chroma_priority_use = chroma_priority
        else:
            rows = np.asarray(rows)
            lab_tgt_use = lab_tgt[rows]
            xyz_tgt_use = xyz_targets[rows]
            target_Y_use = target_Y[rows]
            chroma_priority_use = chroma_priority[rows]

        lab_ach_cand = _xyz_to_lab_vectorised(xyz_ach_cand, white_xyz_ref)
        de_cand = np.linalg.norm(lab_ach_cand - lab_tgt_use, axis=1)
        xy_err_cand = _xy_error_vectorised(xyz_ach_cand, xyz_tgt_use)
        ach_Y_cand = np.maximum(xyz_ach_cand[:, 1], 1e-9)
        rel_Y_cand = np.abs(ach_Y_cand - target_Y_use) / target_Y_use
        # For saturated/off-axis colors, measured target-match data shows that
        # matching xy is much more important than forcing full target Y.
        # Otherwise the solver walks away from known-good anchors (magenta,
        # yellow, cyan) to recover luminance.
        # High-chroma/off-axis candidate selection should be dominated by xy.
        # Luminance remains only a weak continuity/tie-break term so exact dual
        # and HSV ramps do not jump to wrong-scale measured branches just to
        # recover raw Y.
        xy_metric = 1000.0 * xy_err_cand + 0.045 * np.abs(np.log(ach_Y_cand / target_Y_use))
        colour = (1.0 - chroma_priority_use) * de_cand + chroma_priority_use * xy_metric
        return colour, de_cand, rel_Y_cand

    best_score  = np.full(N, np.inf, dtype=np.float64)
    best_rgbw   = np.zeros((N, 4), dtype=np.float64)
    best_xyz_ach = np.zeros_like(xyz_targets, dtype=np.float64)
    best_family = np.full(N, -1, dtype=np.int32)   # index into _FAMILY_DEFS

    # Lexicographic helpers for common-component W preference.  RGB-only can
    # still win when it is materially more accurate, but if a W-family candidate
    # is within a visually-near-equivalent error band, prefer W near the shared
    # RGB floor and lower residual RGB energy.
    best_colour_term = np.full(N, np.inf, dtype=np.float64)
    best_delta_e     = np.full(N, np.inf, dtype=np.float64)
    best_rel_Y_err   = np.full(N, np.inf, dtype=np.float64)
    best_common_loss = np.full(N, np.inf, dtype=np.float64)

    if input_rgb is not None and input_masks is not None:
        _in_common_src = np.asarray(input_rgb, dtype=np.float64)
        _mx_common = np.maximum(np.max(_in_common_src, axis=1), 1.0)
        _mn_common = np.minimum.reduce([_in_common_src[:, 0], _in_common_src[:, 1], _in_common_src[:, 2]])
        expected_common_w = np.clip(_mn_common, 0.0, sample_scale)
        common_frac = np.clip(_mn_common / _mx_common, 0.0, 1.0)
        all3_mask = (input_masks == (R_BIT | G_BIT | B_BIT))
        # Common-RGB W extraction should only become forceful when the shared
        # component is genuinely meaningful.  The earlier 0.10→0.42 gate was
        # too eager for highly saturated red/orange HSV samples with tiny G/B
        # residuals, while still too weak for skin/warm/cool whites.  Start
        # later and reach full strength around skin/white-like common ratios.
        common_gate_global = np.clip((common_frac - 0.055) / 0.300, 0.0, 1.0) * all3_mask.astype(np.float64)
        common_active_global = common_gate_global > 0.08
        # Measured off-axis-white prior: if the W-active capture cloud has a
        # stable nearby solution, use its W level as the effective common-W
        # expectation.  This is what lets warm/cool whites become W-dominant
        # while allowing skin-like colours to use a lower, measured W ratio.
        expected_common_w, local_common_w_valid, local_common_w_xy = _mixed_local_expected_w_vectorised(
            mixed_w_key_tree, xyz_targets, mixed_w_xyz, mixed_w_rgbw,
            white_xyz_ref, input_rgb, input_masks, common_gate_global,
            expected_common_w, sample_scale, k=128,
        )
    else:
        expected_common_w = np.zeros(N, dtype=np.float64)
        common_gate_global = np.zeros(N, dtype=np.float64)
        common_active_global = np.zeros(N, dtype=bool)
        local_common_w_valid = np.zeros(N, dtype=bool)
        local_common_w_xy = np.full(N, np.inf, dtype=np.float64)

    # Endpoint used by the explicit common-min RGB candidate.  This is the
    # measured full-W state nearest the reference white, scaled linearly from
    # black for the shared RGB component.  It mirrors the hard neutral-axis
    # solver but is used only as an additional candidate for non-neutral all-RGB
    # inputs.
    common_neutral_endpoint_rgbw = None
    common_neutral_endpoint_xyz = None
    try:
        _ref_sum = max(float(np.sum(white_xyz_ref)), 1e-12)
        _ref_x = float(white_xyz_ref[0] / _ref_sum)
        _ref_y = float(white_xyz_ref[1] / _ref_sum)
        _best_score = np.inf
        for _fk_n, _xyz_n in family_xyz.items():
            _rgbw_n = family_rgbw.get(_fk_n)
            if _rgbw_n is None or len(_xyz_n) == 0:
                continue
            _keep = (_rgbw_n[:, 3] >= float(sample_scale) * 0.98) & (_xyz_n[:, 1] > 0.0)
            if not np.any(_keep):
                continue
            _cand_idx = np.where(_keep)[0]
            _sum_n = np.maximum(_xyz_n[_cand_idx].sum(axis=1), 1e-9)
            _x_n = _xyz_n[_cand_idx, 0] / _sum_n
            _y_n = _xyz_n[_cand_idx, 1] / _sum_n
            _score_n = (_x_n - _ref_x) ** 2 + (_y_n - _ref_y) ** 2
            _j = int(np.argmin(_score_n))
            if float(_score_n[_j]) < _best_score:
                _best_score = float(_score_n[_j])
                common_neutral_endpoint_xyz = _xyz_n[_cand_idx[_j]].astype(np.float64)
                common_neutral_endpoint_rgbw = _rgbw_n[_cand_idx[_j]].astype(np.float64)
    except Exception:
        common_neutral_endpoint_rgbw = None
        common_neutral_endpoint_xyz = None

    for fk_idx, (fk, fk_rgb_mask, fam_uses_w) in enumerate(_FAMILY_DEFS):
        # ---------------------------------------------------------------
        # Topology gate: family's RGB mask must be a SUBSET of input mask.
        # e.g. R-only family (mask=0b001) is blocked for G-only input (0b010).
        # W-only family (rgb_mask=0b000) passes the subset rule but is further
        # penalised unless the input is essentially neutral (w_reward > 0).
        # ---------------------------------------------------------------
        if input_masks is not None:
            # Subset check: no family RGB bit may be set if input bit is clear
            blocked = ((fk_rgb_mask & ~input_masks) != 0)            # (N,)
            if fam_uses_w and fk_rgb_mask == 0:
                # W-only family: also block for high-chroma / active-RGB inputs
                blocked |= (input_masks != 0)
            topo_penalty = np.where(blocked, TOPO_PENALTY, 0.0)      # (N,)
        else:
            topo_penalty = np.zeros(N, dtype=np.float64)

        # ---- Stage 1: Delaunay / KNN on measured captures ----
        if fk in family_xyz:
            fxyz  = family_xyz[fk]
            frgbw = family_rgbw[fk]
            tri_f = family_tri.get(fk)
            tree_f = family_tree.get(fk)
            key_tree_f = family_key_tree.get(fk) if family_key_tree is not None else None

            if tri_f is not None:
                rgbw_cand, xyz_ach, in_hull = _bary_interp_vectorised(
                    tri_f, xyz_targets, fxyz, frgbw
                )
                # Out-of-hull: use nearest single vertex (k=1) to preserve
                # boundary authority instead of IDW-averaging towards interior.
                oh = ~in_hull
                if oh.any() and (key_tree_f is not None or tree_f is not None):
                    if key_tree_f is not None:
                        r_oh, x_oh = _knn_constrained_bary_keyed_vectorised(
                            key_tree_f, xyz_targets[oh], fxyz, frgbw, white_xyz_ref, k=8
                        )
                    else:
                        r_oh, x_oh = _knn_constrained_bary_vectorised(
                            tree_f, xyz_targets[oh], fxyz, frgbw, k=8
                        )
                    rgbw_cand[oh] = r_oh
                    xyz_ach[oh]   = x_oh
            elif key_tree_f is not None or tree_f is not None:
                # Too few points for Delaunay — use target-space constrained bary on KNN anchors
                if key_tree_f is not None:
                    rgbw_cand, xyz_ach = _knn_constrained_bary_keyed_vectorised(
                        key_tree_f, xyz_targets, fxyz, frgbw, white_xyz_ref, k=8
                    )
                else:
                    rgbw_cand, xyz_ach = _knn_constrained_bary_vectorised(
                        tree_f, xyz_targets, fxyz, frgbw, k=8
                    )
            else:
                continue

            # Clamp to valid drive range (KNN/bary may slightly exceed bounds)
            rgbw_cand = np.clip(rgbw_cand, 0.0, sample_scale)

            # Additional target-space measured-anchor candidate.  Delaunay /
            # constrained barycentric fitting can solve the full XYZ target by
            # moving away from the chromatically correct measured anchor.  Keep
            # the best measured candidate in competition and select it whenever
            # its chromaticity-priority quality is better.
            if key_tree_f is not None and len(fxyz) > 0:
                rgbw_key, xyz_key = _knn_best_measured_keyed_vectorised(
                    key_tree_f, xyz_targets, fxyz, frgbw, white_xyz_ref, k=160,
                    input_rgb=input_rgb, input_masks=input_masks, family_rgb_mask=fk_rgb_mask,
                    expected_w=(expected_common_w if fam_uses_w else None),
                    common_w_gate=(common_gate_global if fam_uses_w else None),
                    sample_scale=sample_scale,
                )
                q_cur, _, _ = _candidate_colour_term(xyz_ach)
                q_key, _, _ = _candidate_colour_term(xyz_key)
                xy_cur = _xy_error_vectorised(xyz_ach, xyz_targets)
                xy_key = _xy_error_vectorised(xyz_key, xyz_targets)

                # Keep measured anchors as first-class candidates.  The latest
                # verifier showed yellow/orange/chartreuse and skin tones still
                # missing mostly because Delaunay/barycentric fits chased Y and
                # stepped away from known-good measured chromaticity anchors.
                # For high-chroma/off-axis colors, allow the measured anchor to
                # win whenever it is materially closer in xy, even if full XYZ
                # dE or Y is worse.  Luminance is a weak secondary constraint for
                # these regions; visual hue/chroma correctness is dominant.
                high_chroma = chroma_priority > 0.35
                exact_dual = (input_masks == fk_rgb_mask) if input_masks is not None and fk in ("rg", "rb", "gb") else np.zeros(N, dtype=bool)
                measured_good_xy = xy_key < 0.0080
                materially_better_xy = (xy_key + 0.00025) < xy_cur
                dual_better_xy = exact_dual & ((xy_key + 0.0012) < xy_cur)
                high_chroma_anchor = high_chroma & measured_good_xy & ((xy_key + 0.00065) < xy_cur)

                # Exact dual-channel edges are the region where measured target-space
                # anchors are most trustworthy: the capture set directly records the
                # calibrated unequal-drive RG/RB/GB ratios.  Do not let a Delaunay or
                # constrained-bary candidate walk away from a close measured anchor
                # solely to recover Y.  This is a topology/measurement rule, not a
                # setup-specific post-hoc correction.
                y_key = np.maximum(xyz_key[:, 1], 1e-9)
                y_tgt = np.maximum(xyz_targets[:, 1], 1e-9)
                dual_anchor_good = (
                    exact_dual
                    & measured_good_xy
                    & (np.abs(np.log(y_key / y_tgt)) < 1.35)
                )

                mixed_common_anchor = np.zeros(N, dtype=bool)
                if input_masks is not None and fam_uses_w:
                    _gate = common_gate_global if 'common_gate_global' in locals() else np.zeros(N, dtype=np.float64)
                    _y_key = np.maximum(xyz_key[:, 1], 1e-9)
                    _y_tgt = np.maximum(xyz_targets[:, 1], 1e-9)
                    mixed_common_anchor = (
                        (input_masks == (1 | 2 | 4))
                        & (_gate > 0.08)
                        & (xy_key < 0.0105)
                        & (np.abs(np.log(_y_key / _y_tgt)) < 1.45)
                        & ((xy_key + 0.0015 < xy_cur) | (q_key <= q_cur + 2.5))
                    )

                key_better = (
                    (q_key < q_cur)
                    | materially_better_xy
                    | dual_better_xy
                    | high_chroma_anchor
                    | dual_anchor_good
                    | mixed_common_anchor
                )
                rgbw_cand = np.where(key_better[:, None], rgbw_key, rgbw_cand)
                xyz_ach = np.where(key_better[:, None], xyz_key, xyz_ach)

            # Explicit split common+residual candidate removed in v23.
            # The old path solved the shared component and residual as independent
            # colours, then added them.  That produced repeated mixed-RGB outputs
            # and bad residual ratios.  The replacement is the full-target fixed-W
            # candidate generated after the family loop.

        elif fk in family_bases:
            # ---- Stage 2 fallback: 3^n bounded NNLS on linear family model ----
            basis  = family_bases[fk]                    # (3, n_ch)
            fk_ch  = [_CH_IDX[c] for c in fk]
            drives = _bounded_active_set_vectorised(
                basis, xyz_targets, sample_scale
            )                                            # (N, n_ch)
            xyz_ach   = (basis @ drives.T).T             # (N, 3)
            rgbw_cand = np.zeros((N, 4), dtype=np.float64)
            for local_i, ch_i in enumerate(fk_ch):
                rgbw_cand[:, ch_i] = drives[:, local_i]
        else:
            continue

        # ---- Stage 3: colorimetric scoring ----
        colour_term, delta_e, _rel_Y_for_colour = _candidate_colour_term(xyz_ach)

        if fam_uses_w:
            w_frac = rgbw_cand[:, 3] / np.maximum(
                rgbw_cand.sum(axis=1), 1.0
            )                                                       # (N,)
        else:
            w_frac = np.zeros(N, dtype=np.float64)

        # Relative luminance error
        ach_Y = np.maximum(xyz_ach[:, 1], 1e-9)
        rel_Y_err = np.abs(ach_Y - target_Y) / target_Y            # (N,)

        # Inactive RGB channel penalty: penalise non-zero drive on channels
        # the input did not activate (e.g. G>0 for a pure-R input).
        if input_masks is not None:
            r_off = (~(input_masks & R_BIT).astype(bool)).astype(np.float64)
            g_off = (~(input_masks & G_BIT).astype(bool)).astype(np.float64)
            b_off = (~(input_masks & B_BIT).astype(bool)).astype(np.float64)
            inactive_energy = (
                r_off * (rgbw_cand[:, 0] / sample_scale) ** 2 +
                g_off * (rgbw_cand[:, 1] / sample_scale) ** 2 +
                b_off * (rgbw_cand[:, 2] / sample_scale) ** 2
            )
        else:
            inactive_energy = np.zeros(N, dtype=np.float64)

        # Coverage penalty: high-chroma nodes expect the winner family to
        # cover all active input RGB channels.  Without this, rbw / gbw
        # families win for skin tones (which strongly activate G) by simply
        # ignoring the active channel.
        COVERAGE_WEIGHT = 750.0
        if input_masks is not None:
            # Coverage is important for high-chroma primaries/secondaries, but
            # should not block low-chroma warm/cool whites where the best measured
            # solution may legitimately be RW/BW/GBW etc.  Use target chroma, not
            # exact-neutral weight, so warm/cool whites are allowed to choose the
            # measured low-chroma W families from target_match_results.
            #
            # Weight missing coverage by the strength of the omitted input
            # channel.  A small HSV residual channel should not block an RGW/RBW
            # measured anchor when the capture set says that anchor is the right
            # optical correction; strong missing channels remain protected.
            coverage_required = np.clip((C_tgt - chroma_gate) / max(chroma_gate, 1e-6), 0.0, 1.0)
            missing_rgb_bits  = input_masks & ~fk_rgb_mask         # (N,) int32
            if input_rgb is not None:
                _in = np.asarray(input_rgb, dtype=np.float64)
                _mx = np.maximum(np.max(_in, axis=1), 1.0)
                missing_strength = np.zeros(N, dtype=np.float64)
                for _bit, _ch in ((R_BIT, 0), (G_BIT, 1), (B_BIT, 2)):
                    missing_strength += (((missing_rgb_bits & _bit) != 0).astype(np.float64)
                                         * (_in[:, _ch] / _mx) ** 2)
                missing_strength = np.clip(missing_strength, 0.0, 1.0)
            else:
                missing_strength = (missing_rgb_bits != 0).astype(np.float64)
            coverage_penalty  = COVERAGE_WEIGHT * coverage_required * missing_strength
        else:
            coverage_penalty = np.zeros(N, dtype=np.float64)

        # Neutral completion penalty (Fix 3 supplement): for near-neutral nodes,
        # penalise families that cannot provide both R and B correction channels.
        # The W emitter is green-leaning (x≈0.33, y≈0.36); D65 correction needs
        # R and B residual.  Families like bw/gw (missing R or B) get penalised
        # proportional to neutral weight, preventing bw from winning on neutrals.
        NEUTRAL_COMPLETION_WEIGHT = 15.0
        _rb_bits  = R_BIT | B_BIT                                   # 0b101
        _rb_and_w = (int(fk_rgb_mask & _rb_bits) == _rb_bits) and fam_uses_w
        if neutral_weights is not None and not _rb_and_w:
            neutral_completion_pen = NEUTRAL_COMPLETION_WEIGHT * neutral_weights
        else:
            neutral_completion_pen = np.zeros(N, dtype=np.float64)

        # Neutral residual penalty: weights are derived from the measured
        # W=65535→reference-white correction endpoint.  This removes the old
        # hardcoded "green is always bad" assumption and adapts if the W diode
        # chromaticity changes.
        NEUTRAL_RESIDUAL_WEIGHT = 25.0
        if neutral_residual_weights is None:
            _nrw = np.array([1.0, 4.0, 1.0], dtype=np.float64)
        else:
            _nrw = np.asarray(neutral_residual_weights, dtype=np.float64)
            if _nrw.shape != (3,):
                _nrw = np.array([1.0, 4.0, 1.0], dtype=np.float64)
        if neutral_weights is not None:
            neutral_residual = neutral_weights * (
                _nrw[0] * (rgbw_cand[:, 0] / sample_scale) ** 2 +
                _nrw[1] * (rgbw_cand[:, 1] / sample_scale) ** 2 +
                _nrw[2] * (rgbw_cand[:, 2] / sample_scale) ** 2
            )
        else:
            neutral_residual = np.zeros(N, dtype=np.float64)

        # Drive-scale sanity for high-chroma colors.  Because high-chroma scoring
        # intentionally downweights Y, otherwise a very dim measured anchor with
        # good xy can beat a physically appropriate bright solution.  This is a
        # smooth measured-topology penalty, not a channel trim: it only discourages
        # gross scale mismatch relative to the requested active RGB level.
        if input_rgb is not None:
            _inp_max = np.maximum(np.max(input_rgb, axis=1), 1.0)
            _out_max = np.maximum(np.max(rgbw_cand[:, :3], axis=1), 1.0)
            _ratio = _out_max / _inp_max
            _under = np.maximum(0.0, np.log(0.35 / np.maximum(_ratio, 1e-6)))
            _over  = np.maximum(0.0, np.log(np.maximum(_ratio, 1e-6) / 1.35))
            drive_scale_penalty = 18.0 * chroma_priority * (_under * _under + 0.65 * _over * _over)
            if input_masks is not None:
                _exact_dual_node = ((input_masks == int(fk_rgb_mask)) & np.isin(input_masks, [3, 5, 6])).astype(np.float64)
                _endpoint = np.clip(_inp_max / float(sample_scale), 0.0, 1.0)
                _dual_cap = 1.04 + 0.22 * _endpoint * _endpoint
                _dual_over = np.maximum(0.0, np.log(np.maximum(_ratio, 1e-6) / _dual_cap))
                drive_scale_penalty += _exact_dual_node * (220.0 - 80.0 * _endpoint) * _dual_over * _dual_over
                # Mixed all-RGB nodes should not normally require an RGB residual
                # channel above the input's own active max once W is available.
                # Keep this as a soft topology penalty, not a hard clamp; exact
                # dual edges still use their measured RG/RB/GB branches freely.
                _all3 = (input_masks == (R_BIT | G_BIT | B_BIT)).astype(np.float64)
                _cap = 1.04 + 0.12 * (1.0 - common_gate_global)
                _over_all3 = np.maximum(0.0, _ratio - _cap)
                drive_scale_penalty += _all3 * common_gate_global * 120.0 * _over_all3 * _over_all3
        else:
            drive_scale_penalty = np.zeros(N, dtype=np.float64)

        # Common-component W preference for non-neutral RGB mixtures.
        # The hard neutral path now handles R=G=B smoothly, but moderately
        # coloured RGB inputs with all three channels active should still treat
        # their shared RGB component as a white-emitter opportunity: use W for
        # the common part, then RGB residuals for chromatic correction.  This is
        # a soft penalty, not a forced extraction, so high-chroma edge cases can
        # still choose RGB-only if it is materially better.
        if input_rgb is not None and input_masks is not None:
            _target_w_abs = np.clip(expected_common_w, 0.0, sample_scale)
            _target_w = np.clip(_target_w_abs / sample_scale, 0.0, 1.0)
            _w_norm = np.clip(rgbw_cand[:, 3] / sample_scale, 0.0, 1.0)
            # Bounded preference band: W may sit well below minRGB if the
            # measured neighborhood proves that is the correct local ratio.  The
            # scalar score should only prevent W=0 from beating a comparable
            # W-floor candidate; target-space measured anchors carry the stronger
            # local-ratio prior.
            _band_lo = (0.32 + 0.28 * common_gate_global) * _target_w
            _band_hi = np.minimum(1.0, 1.22 * _target_w + 512.0 / sample_scale)
            _under_w = np.maximum(0.0, _band_lo - _w_norm)
            _over_w = np.maximum(0.0, _w_norm - _band_hi)
            _centre_w = np.abs(_w_norm - _target_w)
            common_w_loss = (2.2 * _under_w * _under_w + 0.8 * _over_w * _over_w + 0.05 * _centre_w)
            common_w_penalty = common_gate_global * 80.0 * common_w_loss
        else:
            common_w_loss = np.zeros(N, dtype=np.float64)
            common_w_penalty = np.zeros(N, dtype=np.float64)

        # Exact dual-channel topology protection.  For RG/RB/GB inputs, an
        # inactive RGB channel or a large W channel is almost always a topology
        # error unless it is overwhelmingly better chromatically.  This is a
        # score penalty, not a hard ban; the post-loop rescue below still makes
        # the final measured RGB-only comparison.
        if input_masks is not None and input_rgb is not None:
            _exact_dual_node_bool = np.isin(input_masks, [R_BIT | G_BIT, R_BIT | B_BIT, G_BIT | B_BIT])
            if np.any(_exact_dual_node_bool):
                _inmax_dual = np.maximum(np.max(input_rgb, axis=1), 1.0)
                _w_norm_dual = rgbw_cand[:, 3] / _inmax_dual
                _dual_w_penalty = _exact_dual_node_bool.astype(np.float64) * chroma_priority * 220.0 * _w_norm_dual * _w_norm_dual
                _dual_inactive_penalty = _exact_dual_node_bool.astype(np.float64) * chroma_priority * 1800.0 * inactive_energy
            else:
                _dual_w_penalty = np.zeros(N, dtype=np.float64)
                _dual_inactive_penalty = np.zeros(N, dtype=np.float64)
        else:
            _dual_w_penalty = np.zeros(N, dtype=np.float64)
            _dual_inactive_penalty = np.zeros(N, dtype=np.float64)

        # Score: lower = better.  W reward is zero for saturated targets.
        # Luminance weight is reduced for high-chroma/off-axis colors so the
        # solver does not sacrifice xy correctness for a brighter but wrong
        # same-drive mixture.
        y_weight_eff = Y_ERR_WEIGHT * (1.0 - 0.985 * chroma_priority)
        if input_masks is not None:
            _exact_dual_y = ((input_masks == int(fk_rgb_mask)) & np.isin(input_masks, [3, 5, 6])).astype(np.float64)
            y_weight_eff = y_weight_eff * (1.0 - 0.70 * _exact_dual_y * chroma_priority)
        score = (
            colour_term
            + y_weight_eff * rel_Y_err
            + INACTIVE_WEIGHT * inactive_energy
            + topo_penalty
            + coverage_penalty
            + neutral_completion_pen
            + drive_scale_penalty
            + common_w_penalty
            + _dual_w_penalty
            + _dual_inactive_penalty
            + NEUTRAL_RESIDUAL_WEIGHT * neutral_residual
            - delta_e_tiebreak * w_frac * w_reward
        )

        score_better = score < best_score

        # Common-component lexicographic preference.  Once a W-floor candidate
        # is close enough in colour/Y to the current best, prefer it over RGB-only
        # if it has a meaningfully better common-W loss.  Conversely, allow a
        # lower-W/RGB-only solution to win when it is materially more accurate.
        if input_rgb is not None and input_masks is not None:
            valid_best = np.isfinite(best_colour_term)
            # Tolerances are intentionally broad in high-chroma xy-priority zones
            # and tighter near neutral/pastel regions.
            colour_tol = 0.55 + 1.25 * chroma_priority
            de_tol = 0.55 + 0.65 * chroma_priority
            y_tol = 0.22 + 0.35 * chroma_priority

            materially_more_accurate = (
                (colour_term + 0.35 < best_colour_term)
                | (delta_e + 0.45 < best_delta_e)
            )
            comparable = (
                valid_best
                & common_active_global
                & (colour_term <= best_colour_term + colour_tol)
                & (delta_e <= best_delta_e + de_tol)
                & (rel_Y_err <= best_rel_Y_err + y_tol)
            )
            common_loss_better = comparable & ((common_w_loss + 0.003) < best_common_loss)
            # Also prefer actual W-family candidates over RGB-only if the current
            # best has essentially no W and the candidate is comparable.
            w_floor_better = comparable & (rgbw_cand[:, 3] >= (0.32 + 0.28 * common_gate_global) * expected_common_w) & (best_rgbw[:, 3] < 0.18 * expected_common_w)
            common_better = (common_loss_better | w_floor_better) & ~materially_more_accurate
            better = score_better | common_better | materially_more_accurate
        else:
            better = score_better

        best_score = np.where(better, score, best_score)
        best_colour_term = np.where(better, colour_term, best_colour_term)
        best_delta_e = np.where(better, delta_e, best_delta_e)
        best_rel_Y_err = np.where(better, rel_Y_err, best_rel_Y_err)
        best_common_loss = np.where(better, common_w_loss, best_common_loss)
        best_rgbw  = np.where(better[:, None], rgbw_cand, best_rgbw)
        best_xyz_ach = np.where(better[:, None], xyz_ach, best_xyz_ach)
        best_family = np.where(better, fk_idx, best_family)

    # First-class mixed-RGB common candidate: solve the *full* target with W
    # fixed near the shared component, then let RGB residuals move together as
    # needed.  This replaces the v22 split common+residual candidate.
    common_candidate_rgbw = np.zeros_like(best_rgbw)
    common_candidate_xyz = np.zeros_like(xyz_targets)
    common_candidate_active = np.zeros(N, dtype=bool)
    common_candidate_source = np.zeros(N, dtype=np.int32)
    if (input_rgb is not None and input_masks is not None and raw_rgb_basis is not None
            and target_rgb_basis is not None):
        common_rgbw, common_xyz, common_active, common_source = _build_common_min_candidate_vectorised(
            input_rgb, xyz_targets,
            raw_rgb_basis, target_rgb_basis, y_scale,
            family_bases, family_key_tree, family_xyz, family_rgbw,
            white_xyz_ref, sample_scale,
            neutral_key_tree, neutral_xyz, neutral_rgbw,
            neutral_residual_weights=neutral_residual_weights,
            mixed_w_key_tree=mixed_w_key_tree,
            mixed_w_xyz=mixed_w_xyz,
            mixed_w_rgbw=mixed_w_rgbw,
            target_transform_matrix=target_transform_matrix,
        )
        common_candidate_rgbw = common_rgbw
        common_candidate_xyz = common_xyz
        common_candidate_active = common_active
        common_candidate_source = common_source
        if np.any(common_active):
            q_com, de_com, rel_com = _candidate_colour_term(common_xyz)
            q_best = best_colour_term
            de_best = best_delta_e
            rel_best = best_rel_Y_err

            target_w = np.maximum(expected_common_w, 512.0)
            best_w_loss = np.abs(best_rgbw[:, 3] - expected_common_w) / target_w
            common_w_loss2 = np.abs(common_rgbw[:, 3] - expected_common_w) / target_w

            if neutral_residual_weights is None:
                _rw = np.array([1.0, 4.0, 1.0], dtype=np.float64)
            else:
                _rw = np.asarray(neutral_residual_weights, dtype=np.float64)
                if _rw.shape != (3,):
                    _rw = np.array([1.0, 4.0, 1.0], dtype=np.float64)
            best_resid = (
                _rw[0] * (best_rgbw[:, 0] / sample_scale) ** 2 +
                _rw[1] * (best_rgbw[:, 1] / sample_scale) ** 2 +
                _rw[2] * (best_rgbw[:, 2] / sample_scale) ** 2
            )
            common_resid = (
                _rw[0] * (common_rgbw[:, 0] / sample_scale) ** 2 +
                _rw[1] * (common_rgbw[:, 1] / sample_scale) ** 2 +
                _rw[2] * (common_rgbw[:, 2] / sample_scale) ** 2
            )

            # v25: for min(rgb)>0, the W-dominant full-target solve is the primary
            # decomposition model.  Normal family candidates are fallbacks when they
            # are materially more accurate, not the default that W must beat by a
            # narrow tiebreak.  This mirrors the proven neutral-axis behaviour while
            # still protecting saturated/off-axis cases that the W route cannot hit.
            gate = np.clip(common_gate_global, 0.0, 1.0)
            strong_common = common_active & (gate > 0.12)
            common_visual_ok = (
                common_active
                & (q_com <= q_best + 1.75 + 2.75 * gate)
                & (de_com <= de_best + 2.25 + 3.25 * gate)
                & (rel_com <= rel_best + 0.45 + 0.55 * gate)
            )
            normal_materially_better = (
                common_active
                & (q_best + 1.10 + 0.85 * gate < q_com)
                & (de_best + 1.60 + 0.90 * gate < de_com)
                & (best_w_loss <= common_w_loss2 + 0.20)
            )
            decomposition_better = (
                (common_w_loss2 + 0.14 < best_w_loss)
                | (common_resid + 0.012 < 0.80 * best_resid)
                | (strong_common & (common_rgbw[:, 3] >= 0.72 * expected_common_w))
            )
            materially_better = common_active & ((q_com + 0.25 < q_best) | (de_com + 0.60 < de_best))
            choose_common = materially_better | (common_visual_ok & decomposition_better & ~normal_materially_better)
            best_rgbw = np.where(choose_common[:, None], common_rgbw, best_rgbw)
            best_xyz_ach = np.where(choose_common[:, None], common_xyz, best_xyz_ach)
            best_family = np.where(choose_common, 14, best_family)
            best_colour_term = np.where(choose_common, q_com, best_colour_term)
            best_delta_e = np.where(choose_common, de_com, best_delta_e)
            best_rel_Y_err = np.where(choose_common, rel_com, best_rel_Y_err)

    # Exact dual-channel rescue.  v22 exposed a regression where RB/RG/GB HSV
    # samples could be pulled into W-containing families with catastrophic dE.
    # For exact dual inputs, the measured RGB-only pair family is the topology
    # baseline.  Allow W-family dual solutions only when they are materially more
    # accurate; otherwise fall back to the pair-family measured candidate.
    if input_rgb is not None and input_masks is not None:
        for _mask_val, _fk_pair in ((R_BIT | G_BIT, "rg"), (R_BIT | B_BIT, "rb"), (G_BIT | B_BIT, "gb")):
            _idx = np.where(input_masks == _mask_val)[0]
            if len(_idx) == 0:
                continue
            _kt = family_key_tree.get(_fk_pair) if family_key_tree is not None else None
            _fx = family_xyz.get(_fk_pair)
            _fr = family_rgbw.get(_fk_pair)
            if _kt is None or _fx is None or _fr is None or len(_fx) == 0:
                continue
            _rgbw_pair, _xyz_pair = _knn_best_measured_keyed_vectorised(
                _kt, xyz_targets[_idx], _fx, _fr, white_xyz_ref, k=160,
                input_rgb=input_rgb[_idx],
                input_masks=input_masks[_idx],
                family_rgb_mask=_mask_val,
                sample_scale=sample_scale,
            )
            # Exact-dual ratio ridge: orange/yellow/chartreuse failures often
            # have a lower-Y measured RG/RB/GB anchor with the right xy that the
            # normal target-space KNN does not retrieve for the higher-Y target.
            # Search pair-family captures by xy only, scale the whole measured
            # RGBW vector, and let it compete with the normal pair rescue.
            _rr_rgbw_pair, _rr_xyz_pair, _rr_ok_pair, _rr_diag_pair = _find_scaled_ratio_anchor_vectorised(
                xyz_targets[_idx], _fx, _fr, white_xyz_ref, sample_scale,
                input_rgb=input_rgb[_idx], input_masks=input_masks[_idx],
                family_rgb_mask=_mask_val, role="exact_dual", k=160,
            )
            _pair_xy0 = _xy_error_vectorised(_xyz_pair, xyz_targets[_idx])
            _rr_xy = np.asarray(_rr_diag_pair.get("scaled_anchor_xy_err"), dtype=np.float64)
            _rr_yerr = np.asarray(_rr_diag_pair.get("scaled_anchor_y_log_err"), dtype=np.float64)
            _use_rr_pair = _rr_ok_pair & (
                (_rr_xy + 0.00040 < _pair_xy0) |
                ((_rr_xy <= 0.0105) & (_rr_yerr <= 1.45)) |
                ((_pair_xy0 > 0.0160) & (_rr_xy <= 0.0180))
            )
            if np.any(_use_rr_pair):
                _rgbw_pair = np.where(_use_rr_pair[:, None], _rr_rgbw_pair, _rgbw_pair)
                _xyz_pair = np.where(_use_rr_pair[:, None], _rr_xyz_pair, _xyz_pair)
            _q_pair, _de_pair, _rel_pair = _candidate_colour_term(_xyz_pair, _idx)
            _q_best = best_colour_term[_idx]
            _de_best = best_delta_e[_idx]
            _rel_best = best_rel_Y_err[_idx]
            _best_w = best_rgbw[_idx, 3]
            _inp_max = np.maximum(np.max(input_rgb[_idx], axis=1), 1.0)
            _pair_xy = _xy_error_vectorised(_xyz_pair, xyz_targets[_idx])
            _best_xy = _xy_error_vectorised(best_xyz_ach[_idx], xyz_targets[_idx])

            if _mask_val == (R_BIT | G_BIT):
                _inactive_best = best_rgbw[_idx, 2]
            elif _mask_val == (R_BIT | B_BIT):
                _inactive_best = best_rgbw[_idx, 1]
            else:
                _inactive_best = best_rgbw[_idx, 0]

            _pair_family_idx = [fd[0] for fd in _FAMILY_DEFS].index(_fk_pair)
            _topology_problem = (
                (_best_w > np.maximum(384.0, 0.012 * _inp_max))
                | (_inactive_best > np.maximum(96.0, 0.0025 * _inp_max))
                | (best_family[_idx] != _pair_family_idx)
            )
            # For exact duals, measured RGB-only pair anchors define the intended
            # topology.  Prefer them whenever they are chromatically plausible,
            # even if their Y is lower than a wrong W/RGBW branch.  This directly
            # prevents RB/GB HSV ramps from mapping into 4-channel white-biased
            # solutions with huge chromaticity dE.
            _pair_plausible = (_pair_xy <= 0.018) | (_q_pair <= 20.0)
            _pair_chroma_better = (
                (_pair_xy + 0.0020 < _best_xy)
                | (_q_pair + 1.0 < _q_best)
                | (_de_pair + 3.0 < _de_best)
                | (_pair_xy <= 0.0065)
            )
            _y_not_absurd = _rel_pair <= (_rel_best + 4.0)
            _use_pair = _topology_problem & _pair_plausible & _pair_chroma_better & _y_not_absurd
            if np.any(_use_pair):
                _global = _idx[_use_pair]
                best_rgbw[_global] = _rgbw_pair[_use_pair]
                best_xyz_ach[_global] = _xyz_pair[_use_pair]
                best_family[_global] = [fd[0] for fd in _FAMILY_DEFS].index(_fk_pair)
                best_colour_term[_global] = _q_pair[_use_pair]
                best_delta_e[_global] = _de_pair[_use_pair]
                best_rel_Y_err[_global] = _rel_pair[_use_pair]

    if zero_mask is not None:
        best_rgbw[zero_mask] = 0.0
    return best_rgbw, best_family


# ---------------------------------------------------------------------------
# Neutral weight helper
# ---------------------------------------------------------------------------

def compute_neutral_weight(rgb_flat: np.ndarray, sample_scale: float = 65535.0) -> np.ndarray:
    """Per-node neutral-axis blend weight.

    This intentionally combines two tests:

    1. Relative spread, for normal/mid/high brightness values.
       spread = (max(R,G,B) - min(R,G,B)) / max(R,G,B)

    2. Absolute spread, for very low values and 256³ LUT interpolation.
       A neutral input like (2000,2000,2000) is trilinearly interpolated from
       cube corners whose channels differ by one grid step (~257 counts).  A
       purely relative 8% neutral window marks those corners as coloured, so
       interpolated near-black greys lose W.  The absolute window keeps
       low-level diagonal neighborhoods W-dominant without pulling saturated
       dark primaries into the neutral path.

    The returned weight is the max of the relative and absolute neutral weights.
    """
    r, g, b = rgb_flat[:, 0], rgb_flat[:, 1], rgb_flat[:, 2]
    ch_max = np.maximum(np.maximum(r, g), b)
    ch_min = np.minimum(np.minimum(r, g), b)
    spread_abs = ch_max - ch_min

    spread_rel = np.zeros_like(ch_max, dtype=np.float64)
    np.divide(spread_abs, np.maximum(ch_max, 1e-9), out=spread_rel, where=ch_max > 0)

    # Relative test: strict enough that skin/warm/cool/off-axis colours do not
    # route through the neutral solver.
    rel_start, rel_end = 0.015, 0.08
    nw_rel = np.clip(1.0 - (spread_rel - rel_start) / (rel_end - rel_start), 0.0, 1.0)

    # Absolute test: tied to the natural 8-bit-index grid step for a 16-bit LUT.
    # This mainly protects low-level greys from trilinear interpolation through
    # off-diagonal cube corners.
    grid_step = float(sample_scale) / 255.0
    abs_start = 1.5 * grid_step
    abs_end = 4.0 * grid_step
    nw_abs = np.clip(1.0 - (spread_abs - abs_start) / (abs_end - abs_start), 0.0, 1.0)

    # Avoid marking true black specially here; zero_mask handles black output.
    return np.maximum(nw_rel, nw_abs)


def _apply_dual_channel_xyz_targets(
    xyz_out: np.ndarray,           # (N, 3) — modified for dual-channel nodes
    rgb_flat: np.ndarray,          # (N, 3) input drives (normalised 0..sample_scale)
    input_mask: np.ndarray,        # (N,) int32 active-channel bitmask
    family_drive_tree: dict,       # fk -> cKDTree in 2D normalised drive space
    family_drive_xyz:  dict,       # fk -> (M, 3) measured XYZ for that family
    sample_scale: float,
    k: int = 5,
) -> np.ndarray:
    """Replace raw-basis XYZ targets for dual-channel (rg/rb/gb) grid nodes
    with IDW-interpolated targets from the measured dual-channel captures.

    The raw physical basis mix (rgb @ raw_rgb_basis.T) is incorrect for
    dual-channel edges because display primaries interact non-linearly at
    unequal drives.  Measured captures show, for example:
      cyan    (gb): correct target at G:B ≈ 1.00:0.60 — not equal drives
      magenta (rb): correct target at R:B ≈ 1.00:0.31
      yellow  (rg): correct target at R:G ≈ 1.00:0.50

    For each dual-channel grid node we find the k nearest measured captures
    in 2D normalised (ch_a / scale, ch_b / scale) drive space and IDW-
    interpolate their measured XYZ.  This gives a calibrated target that
    reflects the actual display behaviour rather than a linear model guess.
    """
    for fk, bit_mask in _DUAL_PAIR_MASKS.items():
        if fk not in family_drive_tree:
            continue
        dual_nodes = (input_mask == bit_mask)  # exactly these two bits set
        if not dual_nodes.any():
            continue
        ch_a, ch_b = _DUAL_PAIR_CHANNELS[fk]
        q_drives = np.column_stack([
            rgb_flat[dual_nodes, ch_a] / sample_scale,
            rgb_flat[dual_nodes, ch_b] / sample_scale,
        ])                                              # (M, 2)
        tree = family_drive_tree[fk]
        fxyz = family_drive_xyz[fk]
        n_neighbors = min(k, len(fxyz))
        dists, idxs = tree.query(q_drives, k=n_neighbors)
        dists = np.maximum(dists, 1e-12)
        if n_neighbors == 1 or dists.ndim == 1:
            xyz_interp = fxyz[idxs]
        else:
            weights = 1.0 / dists
            weights /= weights.sum(axis=1, keepdims=True)
            xyz_interp = (weights[:, :, None] * fxyz[idxs]).sum(axis=1)
        xyz_out[dual_nodes] = xyz_interp
    return xyz_out


# ---------------------------------------------------------------------------
# Multiprocessing worker
# ---------------------------------------------------------------------------

def _worker_init(state: dict) -> None:
    """Initialise per-worker state.

    Receives raw capture arrays (family_capture_xyz / family_capture_rgbw) and
    builds per-family Delaunay triangulations + cKDTrees here \u2014 in the worker
    process \u2014 so the Delaunay objects are never sent across the process boundary.
    """
    global _worker_state
    s = state.copy()

    raw_xyz  = s.pop("family_capture_xyz",  {})
    raw_rgbw = s.pop("family_capture_rgbw", {})

    f_tri:  dict[str, Delaunay | None] = {}
    f_tree: dict[str, cKDTree  | None] = {}
    f_key_tree: dict[str, cKDTree | None] = {}
    f_xyz:  dict[str, np.ndarray]       = {}
    f_rgbw: dict[str, np.ndarray]       = {}

    for fk in raw_xyz:
        fxyz  = raw_xyz[fk]
        frgbw = raw_rgbw[fk]
        f_xyz[fk]  = fxyz
        f_rgbw[fk] = frgbw
        f_key_tree[fk] = cKDTree(_target_space_key(fxyz, s["white_xyz_ref"])) if len(fxyz) > 0 else None
        if len(fxyz) >= 5:
            try:
                f_tri[fk]  = Delaunay(fxyz)
                f_tree[fk] = cKDTree(fxyz)
            except Exception:
                f_tri[fk]  = None
                f_tree[fk] = cKDTree(fxyz) if len(fxyz) > 0 else None
        else:
            f_tri[fk]  = None
            f_tree[fk] = cKDTree(fxyz) if len(fxyz) > 0 else None

    s["family_tri"]  = f_tri
    s["family_tree"] = f_tree
    s["family_key_tree"] = f_key_tree
    s["family_xyz"]  = f_xyz
    s["family_rgbw"] = f_rgbw

    # Global W-active target-space cloud used only as a measured prior for
    # non-neutral mixed RGB / off-axis-white decomposition.  It does not replace
    # the family solver; it gives the scorer a local measured W expectation.
    _mw_xyz_list: list[np.ndarray] = []
    _mw_rgbw_list: list[np.ndarray] = []
    for _mfk, _mxyz in f_xyz.items():
        _mrgbw = f_rgbw[_mfk]
        _keep = _mrgbw[:, 3] > 0
        if np.any(_keep):
            _mw_xyz_list.append(_mxyz[_keep])
            _mw_rgbw_list.append(_mrgbw[_keep])
    if _mw_xyz_list:
        _mw_xyz = np.concatenate(_mw_xyz_list, axis=0)
        _mw_rgbw = np.concatenate(_mw_rgbw_list, axis=0)
        s["mixed_w_xyz"] = _mw_xyz
        s["mixed_w_rgbw"] = _mw_rgbw
        s["mixed_w_key_tree"] = cKDTree(_target_space_key(_mw_xyz, s["white_xyz_ref"]))
    else:
        s["mixed_w_xyz"] = None
        s["mixed_w_rgbw"] = None
        s["mixed_w_key_tree"] = None

    # Build target-space Lab/logY KD-trees for dual-channel families (rg/rb/gb).
    # Queried in _solve_r_slice by the raw-basis target XYZ to find measured
    # captures whose reproduced XYZ is closest to the target.  This replaces
    # the old drive-space trees which matched by input drive ratio (wrong for
    # primaries that interact non-linearly, e.g. cyan best at G:B ≈ 1:0.60).
    _white_ref_dc = s["white_xyz_ref"]
    _L_W_dc, _Y_W_dc = 0.5, 2.0      # Lab L* weight, logY weight in search space
    f_target_tree: dict = {}
    f_target_xyz:  dict = {}
    f_target_rgbw: dict = {}
    for _fk in _DUAL_PAIR_CHANNELS:
        if _fk not in raw_xyz or len(raw_xyz[_fk]) == 0:
            continue
        _fxyz  = raw_xyz[_fk]
        _frgbw = raw_rgbw[_fk]
        _flab  = _xyz_to_lab_vectorised(np.maximum(_fxyz, 0.0), _white_ref_dc)
        _flogY = np.log(np.maximum(_fxyz[:, 1], 1e-6)).reshape(-1, 1)
        _fkey  = np.hstack(
            [_flab[:, 0:1] * _L_W_dc, _flab[:, 1:3], _flogY * _Y_W_dc]
        )                                                          # (M, 4)
        f_target_tree[_fk] = cKDTree(_fkey)
        f_target_xyz[_fk]  = _fxyz
        f_target_rgbw[_fk] = _frgbw
    s["family_target_tree"] = f_target_tree
    s["family_target_xyz"]  = f_target_xyz
    s["family_target_rgbw"] = f_target_rgbw

    # Build neutral measured candidate set for the dedicated neutral-axis solver.
    # Do not query by luminance only: W-only and W+blue states can be bright but
    # chromatically poor.  Keep a broad set of W-active, near-reference captures
    # and build a target-space key using xy, Lab, and a weak logY term.
    _ref_w   = s["white_xyz_ref"]                               # (3,) XYZ
    _ref_sum = float(_ref_w.sum())
    _REF_x   = float(_ref_w[0]) / max(_ref_sum, 1e-9)
    _REF_y   = float(_ref_w[1]) / max(_ref_sum, 1e-9)
    _NEUTRAL_XY_TOL = 0.060     # broad; objective scoring performs final ranking
    _W_FAM_SET = {"w", "rw", "gw", "bw", "rgw", "rbw", "gbw", "rgbw"}
    _n_xyz_list:  list[np.ndarray] = []
    _n_rgbw_list: list[np.ndarray] = []
    for _nfk in _W_FAM_SET:
        if _nfk not in raw_xyz or len(raw_xyz[_nfk]) == 0:
            continue
        _nx = raw_xyz[_nfk]
        _nr = raw_rgbw[_nfk]
        _sum = _nx.sum(axis=1)
        _safe = _sum > 1e-9
        _x = np.where(_safe, _nx[:, 0] / np.maximum(_sum, 1e-9), 0.0)
        _y = np.where(_safe, _nx[:, 1] / np.maximum(_sum, 1e-9), 0.0)
        _xyd = np.sqrt((_x - _REF_x) ** 2 + (_y - _REF_y) ** 2)
        _w_active = _nr[:, 3] > 0
        _keep = _safe & _w_active & (_xyd <= _NEUTRAL_XY_TOL)
        if _keep.any():
            _n_xyz_list.append(_nx[_keep])
            _n_rgbw_list.append(_nr[_keep])
    if _n_xyz_list:
        _nxa = np.concatenate(_n_xyz_list, axis=0)
        _nra = np.concatenate(_n_rgbw_list, axis=0)
        _sum = np.maximum(_nxa.sum(axis=1), 1e-9)
        _x = _nxa[:, 0] / _sum
        _y = _nxa[:, 1] / _sum
        _lab = _xyz_to_lab_vectorised(np.maximum(_nxa, 0.0), _ref_w)
        _logY = np.log(np.maximum(_nxa[:, 1], 1e-6))
        _nkey = np.column_stack([
            _x * 120.0,
            _y * 120.0,
            _lab[:, 0] * 0.03,
            _lab[:, 1],
            _lab[:, 2],
            _logY * 0.20,
        ])
        s["neutral_tree"] = cKDTree(_nkey)
        s["neutral_xyz"]  = _nxa
        s["neutral_rgbw"] = _nra

        # Derive neutral residual penalties from the measured best full-W
        # reference-white correction, instead of hardcoding a green penalty.
        _scale = float(getattr(s["args"], "sample_scale", 65535.0))
        _full_w = _nra[:, 3] >= (_scale * 0.98)
        if np.any(_full_w):
            _cand = np.where(_full_w)[0]
            _xyd = np.sqrt((_x[_cand] - _REF_x) ** 2 + (_y[_cand] - _REF_y) ** 2)
            _best = _cand[int(np.argmin(_xyd))]
            _res = np.maximum(_nra[_best, :3] / max(_scale, 1.0), 0.0)
            _mx = float(np.max(_res))
            if _mx > 1e-6:
                _weights = (_mx + 0.02) / (_res + 0.02)
                _weights = np.clip(_weights, 1.0, 8.0)
                _weights /= max(float(np.min(_weights)), 1.0)
                s["neutral_residual_weights"] = _weights.astype(np.float64)
            else:
                s["neutral_residual_weights"] = np.array([1.0, 4.0, 1.0], dtype=np.float64)
        else:
            s["neutral_residual_weights"] = np.array([1.0, 4.0, 1.0], dtype=np.float64)
    else:
        s["neutral_tree"] = None
        s["neutral_xyz"]  = None
        s["neutral_rgbw"] = None
        s["neutral_residual_weights"] = np.array([1.0, 4.0, 1.0], dtype=np.float64)

    # raw_rgb_basis: physical (unscaled) basis — fallback to target_rgb_basis if absent
    if "raw_rgb_basis" not in s:
        s["raw_rgb_basis"] = s["target_rgb_basis"]
    _worker_state = s


# ---------------------------------------------------------------------------
# Exact probe solve + compact diagnostics
# ---------------------------------------------------------------------------

def _u8_to_u16(v: int) -> int:
    """Round an 8-bit channel value to the solver's 16-bit domain."""
    return int(round((float(v) / 255.0) * 65535.0))


# Compact probe definitions.
#
# Important distinction:
#   * *_edge probes are topology probes: exact RGB cube edges/corners.
#   * semantic hue probes use the RGB tuple produced by the intended wide-gamut
#     colour pipeline for that named hue.  Do not verify a semantic colour name
#     against a simple edge tuple like (65535, 0, 32768); that asks the solver to
#     hit the wrong xyY for the input value.
_PROBE_DEFS: list[tuple[str, tuple[int, int, int], str, str]] = [
    # Neutral axis
    ("near_black",    (2000, 2000, 2000),     "neutral", "low neutral"),
    ("neutral_6pct",  (3932, 3932, 3932),     "neutral", "6 percent neutral"),
    ("neutral_25pct", (16384, 16384, 16384),  "neutral", "25 percent neutral"),
    ("neutral_50pct", (32768, 32768, 32768),  "neutral", "50 percent neutral"),
    ("neutral_75pct", (49152, 49152, 49152),  "neutral", "75 percent neutral"),
    ("white",         (65535, 65535, 65535),  "neutral", "full reference white"),

    # Pure primaries
    ("red",           (65535, 0, 0),          "primary", "pure red axis"),
    ("green",         (0, 65535, 0),          "primary", "pure green axis"),
    ("blue",          (0, 0, 65535),          "primary", "pure blue axis"),

    # Topology / exact-edge probes.  These are not semantic hue names.
    ("cyan_edge",       (0, 65535, 65535),    "topology_edge", "exact GB full edge"),
    ("magenta_edge",    (65535, 0, 65535),    "topology_edge", "exact RB full edge"),
    ("yellow_edge",     (65535, 65535, 0),    "topology_edge", "exact RG full edge"),
    ("orange_edge",     (65535, 32768, 0),    "topology_edge", "exact asymmetric RG edge"),
    ("chartreuse_edge", (32768, 65535, 0),    "topology_edge", "exact asymmetric RG edge"),
    ("spring_edge",     (0, 65535, 32768),    "topology_edge", "exact asymmetric GB edge"),
    ("azure_edge",      (0, 32768, 65535),    "topology_edge", "exact asymmetric GB edge"),
    ("violet_edge",     (32768, 0, 65535),    "topology_edge", "exact asymmetric RB edge"),
    ("rose_edge",       (65535, 0, 32768),    "topology_edge", "exact asymmetric RB edge"),

    # Semantic hue probes.  These RGB tuples should match the source wide-gamut
    # colour pipeline, so their computed xyY target is aligned with the colour
    # name being verified.
    ("yellow",     (65535, 60395, 3855),                    "semantic_hue", "wide-gamut yellow"),
    ("orange",     (65535, 33924, 1799),                    "semantic_hue", "wide-gamut orange"),
    ("chartreuse", (40349, 65535, 4626),                    "semantic_hue", "wide-gamut chartreuse"),
    ("spring",     (_u8_to_u16(12), _u8_to_u16(255), _u8_to_u16(195)),  "semantic_hue", "wide-gamut spring"),
    ("rose",       (_u8_to_u16(255), _u8_to_u16(80), _u8_to_u16(198)),  "semantic_hue", "wide-gamut rose"),

    # Mixed / off-axis white and skin probes
    ("red_desat",      (65535, 32768, 32768), "mixed_rgb", "red plus shared RGB floor"),
    ("green_desat",    (32768, 65535, 32768), "mixed_rgb", "green plus shared RGB floor"),
    ("blue_desat",     (32768, 32768, 65535), "mixed_rgb", "blue plus shared RGB floor"),
    ("skin_light",    (65535, 45875, 37632),  "mixed_rgb", "light skin target"),
    ("skin_mid",      (52429, 34078, 26214),  "mixed_rgb", "mid skin target"),
    ("skin_dark",     (29490, 17825, 12451),  "mixed_rgb", "dark skin target"),
    ("warm_white",    (65535, 58000, 45000),  "off_axis_white", "warm non-D65 white"),
    ("cool_white",    (45000, 52000, 65535),  "off_axis_white", "cool non-D65 white"),

    # Representative non-neutral RGB mix: should prefer useful W for the
    # common RGB component, then residual RGB for chromatic correction.
    ("mixed_purple_wcommon", (34406, 19661, 49151), "mixed_rgb", "purple with common RGB floor"),
]


def _hsv_probe_defs() -> list[tuple[str, tuple[int, int, int], str, str]]:
    """Representative HSV verifier sweep probes for compact debug output.

    This does not include every verifier row; it samples hue/saturation/value
    classes that have repeatedly exposed scaling and residual-mask failures.
    """
    vals: list[tuple[str, tuple[int, int, int], str, str]] = []
    import colorsys as _colorsys
    hue_steps = list(range(0, 360, 15))
    sat_vals = (0.45, 0.60, 0.75, 0.90)
    val_vals = (0.08, 0.18, 0.30, 0.45, 0.60, 0.75, 0.90, 1.00)
    # cap to 24*4*8 = 768? too many; sample alternating combinations to ~192
    for hi, h in enumerate(hue_steps):
        for si, s in enumerate(sat_vals):
            for vi, v in enumerate(val_vals):
                if ((hi + si * 2 + vi) % 4) != 0:
                    continue
                r, g, b = _colorsys.hsv_to_rgb(h / 360.0, s, v)
                rgb = (int(round(r * 65535.0)), int(round(g * 65535.0)), int(round(b * 65535.0)))
                vals.append((f"hsv_h{h:03d}_s{int(s*100):03d}_v{int(v*100):03d}", rgb, "hsv_sweep", "sampled HSV verifier-style probe"))
    return vals

_PROBE_DEFS.extend(_hsv_probe_defs())

_PROBE_RGBS: dict[str, tuple[int, int, int]] = {name: rgb for name, rgb, _cls, _note in _PROBE_DEFS}
_PROBE_META: dict[str, tuple[str, str]] = {name: (_cls, _note) for name, _rgb, _cls, _note in _PROBE_DEFS}



def _apply_output_guardrails(
    rgb_flat: np.ndarray,
    rgbw_flat: np.ndarray,
    family_flat: np.ndarray,
    input_mask: np.ndarray,
    neutral_weight: np.ndarray,
    sample_scale: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Topology-preserving endpoint rule.

    Keep only the generic, measurement-invariant part of the previous guardrail
    pass: exact pure-primary axes preserve channel authority at all non-zero levels.

    This is not a colour-correction bandaid.  For a pure R/G/B input, the
    requested channel itself defines the measured primary axis, and the solver
    should not trade away endpoint resolution for a microscopically closer
    lower-drive measured anchor.  RG/RB/skin/mixed correction is left entirely
    to measured target-space candidate selection and family scoring.
    """
    out = np.asarray(rgbw_flat, dtype=np.float64).copy()
    fam = np.asarray(family_flat, dtype=np.int32).copy()
    scale = float(sample_scale)
    if len(out) == 0:
        return out, fam

    R_BIT, G_BIT, B_BIT = 1, 2, 4
    fam_index = {name: i for i, (name, _mask, _w) in enumerate(_FAMILY_DEFS)}

    # Pure primary axis authority at all non-zero levels.
    nonzero = np.max(rgb_flat, axis=1) > 0.0
    for bit, ch, fname in ((R_BIT, 0, "r"), (G_BIT, 1, "g"), (B_BIT, 2, "b")):
        m = (input_mask == bit) & nonzero
        if np.any(m):
            out[m, :] = 0.0
            out[m, ch] = rgb_flat[m, ch]
            fam[m] = fam_index.get(fname, fam[m])

    # v33 recovery: guardrails after this point are disabled by default.
    # Pure primary endpoint authority above is topology-invariant; dual/mixed
    # post-solve scaling/capping hides solver errors and made replay diagnosis
    # ambiguous.  Re-enable only after the solver itself selects measured-correct
    # candidates.
    if True or bool(getattr(_worker_state.get("args", object()), "disable_output_guardrails", False)):
        return np.clip(out, 0.0, scale), fam

    # Exact dual-channel scale continuity.  The measured-anchor solver may find
    # a good high-drive RG/RB/GB point for a lower-value input and preserve its
    # chromaticity, but that destroys value-ramp granularity and Y monotonicity
    # (for example half-yellow selecting a ~56k red branch).  Uniformly scaling
    # the already-selected RGBW state preserves chromaticity while keeping the
    # output active max near the requested active max.  Endpoint inputs retain
    # enough headroom for calibrated asymmetric measured branches.
    for mask_val in (R_BIT | G_BIT, R_BIT | B_BIT, G_BIT | B_BIT):
        m = (input_mask == mask_val) & nonzero
        if not np.any(m):
            continue
        in_max = np.maximum(np.max(rgb_flat[m, :3], axis=1), 1.0)
        out_max = np.maximum(np.max(out[m, :3], axis=1), 1.0)
        endpoint = np.clip(in_max / scale, 0.0, 1.0)
        max_ratio = 1.025 + 0.175 * endpoint * endpoint
        target_max = in_max * max_ratio
        over = out_max > target_max
        if np.any(over):
            idx = np.where(m)[0][over]
            factor = target_max[over] / np.maximum(out_max[over], 1.0)
            out[idx, :] *= factor[:, None]

    # Mixed all-RGB linear envelope.
    #
    # v28 used one uniform RGBW scale whenever *any* mixed RGB channel exceeded
    # its corresponding input component.  That preserved the selected ratios,
    # but it also allowed a small shared-channel cap (for example G=minRGB on a
    # saturated red row) to shrink the dominant channel and W by 5-8x.  The
    # verifier showed this as non-linear value ramps where increasing input V
    # produced *lower* dominant RGB output and huge dE swings.
    #
    # Keep the useful part of the envelope, but make it dominance preserving:
    #   1. apply a uniform scale only for true global overdrive, where the RGB
    #      output maximum is above the input maximum envelope;
    #   2. cap per-channel RGB overages locally instead of scaling unrelated
    #      channels down;
    #   3. cap W to the shared/common RGB drive with small quantisation headroom.
    #
    # This keeps the LUT roughly linear for the upstream transfer curve without
    # letting a low residual-channel cap collapse the full RGBW solution.
    mixed3 = (input_mask == (R_BIT | G_BIT | B_BIT)) & nonzero & (neutral_weight < 0.98)
    if np.any(mixed3):
        idx_all = np.where(mixed3)[0]
        inp = np.maximum(rgb_flat[idx_all, :3], 0.0)
        vals = np.maximum(out[idx_all, :4], 0.0)

        in_max = np.maximum(np.max(inp, axis=1), 1.0)
        out_rgb_max = np.maximum(np.max(vals[:, :3], axis=1), 1.0)
        endpoint = np.clip(in_max / scale, 0.0, 1.0)
        global_headroom = 1.025 + 0.175 * endpoint * endpoint
        global_cap = in_max * global_headroom
        global_over = out_rgb_max > global_cap
        if np.any(global_over):
            gidx = idx_all[global_over]
            factor = global_cap[global_over] / np.maximum(out_rgb_max[global_over], 1.0)
            out[gidx, :] *= factor[:, None]

        # Re-read after possible global scale.
        vals = np.maximum(out[idx_all, :4], 0.0)

        # Per-channel active RGB envelope.  Do not use these low-channel caps as
        # a row-wide scale factor; clamp only the offending channel.  This is the
        # behaviour intended by examples such as 65535,1000,32000 -> keep the
        # dominant channel authority while preventing impossible low-channel
        # residual inflation.
        rgb_cap = inp * 1.010 + 64.0
        out[idx_all, :3] = np.minimum(vals[:, :3], rgb_cap)

        # W should track the common/shared RGB component for this path.  Give a
        # small absolute and relative headroom so quantisation and sparse capture
        # anchors do not create hard discontinuities.
        common = np.minimum.reduce([inp[:, 0], inp[:, 1], inp[:, 2]])
        w_cap = common * 1.025 + 64.0
        out[idx_all, 3] = np.minimum(np.maximum(out[idx_all, 3], 0.0), w_cap)

        # W-axis residual-mask envelope.  Once a mixed min(rgb)>0 solution has
        # selected a W-using family, channels equal to the common/min input are
        # no longer free RGB residuals; W is carrying that shared component.
        # The old mixed envelope allowed a common channel to remain as high as
        # the input value, which caused h090-style failures where G stayed near
        # input G even though the target needed an RBW solution and the white
        # diode already leans green/yellow.  Keep this only for W-using mixed
        # rows so RGB-only fallback remains available when the W-axis route did
        # not win.
        w_family_ids = np.array([
            fam_index.get("rw", -99), fam_index.get("gw", -99), fam_index.get("bw", -99),
            fam_index.get("rgw", -99), fam_index.get("rbw", -99), fam_index.get("gbw", -99),
            fam_index.get("rgbw", -99),
        ], dtype=np.int32)
        fam_sel = fam[idx_all]
        w_using = np.isin(fam_sel, w_family_ids) | (out[idx_all, 3] > np.maximum(96.0, 0.08 * common))
        if np.any(w_using):
            local_rows = idx_all[w_using]
            local_inp = inp[w_using]
            local_common = common[w_using]
            local_out = np.maximum(out[local_rows, :3], 0.0)
            local_inmax = np.maximum(np.max(local_inp, axis=1), 1.0)
            residual = np.maximum(local_inp - local_common[:, None], 0.0)
            eps_resid = np.maximum(64.0, 0.0035 * local_inmax)
            forbidden = residual <= eps_resid[:, None]
            leak_cap = 48.0 + 0.0040 * np.maximum(local_common, 1.0)
            capped = np.where(forbidden, np.minimum(local_out, leak_cap[:, None]), local_out)
            out[local_rows, :3] = capped

    return np.clip(out, 0.0, scale), fam


# ---------------------------------------------------------------------------
# Verifier feedback candidate model (active measured correction layer)
# ---------------------------------------------------------------------------

def _feedback_rgb_key_from_rgb(rgb: np.ndarray | list | tuple) -> str:
    vals = [int(round(float(v))) for v in np.asarray(rgb, dtype=np.float64).reshape(-1)[:3]]
    return f"{vals[0]},{vals[1]},{vals[2]}"


def _feedback_rgbw_family_index(rgbw: np.ndarray | list | tuple, sample_scale: float = 65535.0) -> int:
    vals = np.asarray(rgbw, dtype=np.float64).reshape(-1)
    eps = max(1.0, 1e-5 * float(sample_scale))
    key = ""
    if len(vals) >= 1 and vals[0] > eps:
        key += "r"
    if len(vals) >= 2 and vals[1] > eps:
        key += "g"
    if len(vals) >= 3 and vals[2] > eps:
        key += "b"
    if len(vals) >= 4 and vals[3] > eps:
        key += "w"
    return int(_FAMILY_INDEX_BY_NAME.get(key, -1))


def _feedback_observation_sort_value(obs: dict) -> tuple[float, float, int]:
    try:
        de = float(obs.get("dE", obs.get("verifier_dE", np.inf)))
    except Exception:
        de = float("inf")
    try:
        y = float((obs.get("measured_xyY") or [None, None, np.inf])[2])
    except Exception:
        y = float("inf")
    try:
        seen = int(obs.get("seen_count", 1))
    except Exception:
        seen = 1
    return (de if np.isfinite(de) else float("inf"), abs(y) if np.isfinite(y) else float("inf"), -seen)


def _feedback_ok_value(value: object, dE: float | None, threshold: float) -> bool:
    txt = str(value or "").strip().lower()
    if txt in {"✓", "true", "pass", "ok", "yes", "1"}:
        return True
    if txt in {"x", "✗", "false", "fail", "no", "0"}:
        return False
    return bool(dE is not None and np.isfinite(dE) and dE <= float(threshold))


def _feedback_obs_from_verifier_row(row: dict, source_file: str, dE_threshold: float) -> dict | None:
    try:
        rgb = [safe_int(row.get("r16")), safe_int(row.get("g16")), safe_int(row.get("b16"))]
        rgbw = [
            safe_int(row.get("lut_r16")),
            safe_int(row.get("lut_g16")),
            safe_int(row.get("lut_b16")),
            safe_int(row.get("lut_w16")),
        ]
    except Exception:
        return None
    if sum(rgb) <= 0:
        return None
    de = safe_float(row.get("dE"))
    if not np.isfinite(de):
        return None
    mx = safe_float(row.get("meas_x"))
    my = safe_float(row.get("meas_y"))
    mY = safe_float(row.get("meas_Y"))
    ex = safe_float(row.get("exp_x"))
    ey = safe_float(row.get("exp_y"))
    status_ok = _feedback_ok_value(row.get("ok"), de, dE_threshold)
    return {
        "rgb_key": _feedback_rgb_key_from_rgb(rgb),
        "input_rgb": rgb,
        "lut_rgbw": rgbw,
        "dE": float(de),
        "status": "pass" if status_ok else "fail",
        "ok": bool(status_ok),
        "measured_xyY": [float(mx), float(my), float(mY)] if np.isfinite([mx, my, mY]).all() else None,
        "target": {"x": float(ex) if np.isfinite(ex) else None, "y": float(ey) if np.isfinite(ey) else None},
        "patch": str(row.get("patch", "")),
        "source_file": str(source_file),
        "selected_family": str(row.get("selected_family", "")),
        "selected_route": str(row.get("selected_route", "")),
    }


def _iter_feedback_bank_observations(bank: dict) -> list[dict]:
    out: list[dict] = []
    entries = bank.get("entries", {}) if isinstance(bank, dict) else {}
    if not isinstance(entries, dict):
        return out
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        observations = entry.get("observations")
        if isinstance(observations, list):
            for obs in observations:
                if not isinstance(obs, dict):
                    continue
                rgb = obs.get("input_rgb") or entry.get("input_rgb")
                rgbw = obs.get("lut_rgbw")
                if rgb is None or rgbw is None:
                    continue
                o = dict(obs)
                o["rgb_key"] = str(key)
                o["input_rgb"] = [int(round(float(v))) for v in rgb[:3]]
                o["lut_rgbw"] = [int(round(float(v))) for v in rgbw[:4]]
                if "ok" not in o:
                    o["ok"] = str(o.get("status", "")).lower() == "pass"
                if "status" not in o:
                    o["status"] = "pass" if o.get("ok") else "fail"
                out.append(o)
        else:
            latest = entry.get("latest_result") if isinstance(entry.get("latest_result"), dict) else None
            rgb = entry.get("input_rgb")
            rgbw = latest.get("lut_rgbw") if latest else None
            if rgb is not None and rgbw is not None:
                out.append({
                    "rgb_key": str(key),
                    "input_rgb": [int(round(float(v))) for v in rgb[:3]],
                    "lut_rgbw": [int(round(float(v))) for v in rgbw[:4]],
                    "dE": latest.get("dE"),
                    "status": latest.get("status", "pass" if latest.get("ok") else "fail"),
                    "ok": bool(latest.get("ok", latest.get("status") == "pass")),
                    "measured_xyY": latest.get("measured_xyY"),
                    "patch": latest.get("patch", ""),
                    "source_file": latest.get("source_file", ""),
                })
    return out


def build_feedback_candidate_model_from_observations(
    observations: list[dict],
    *,
    dE_threshold: float = 2.5,
) -> dict:
    """Build an exact-key measured feedback candidate model.

    This is intentionally conservative for the first active correction pass:
    exact RGB keys with at least one verifier-passing observation can inject the
    best measured-passing RGBW state as a solver candidate/override.  Failing
    observations are preserved for diagnostics/next-pass penalty work, but are
    not used as replacements.
    """
    by_key: dict[str, list[dict]] = {}
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        key = obs.get("rgb_key") or _feedback_rgb_key_from_rgb(obs.get("input_rgb", [0, 0, 0]))
        rgbw = obs.get("lut_rgbw")
        if not key or rgbw is None:
            continue
        o = dict(obs)
        try:
            o["lut_rgbw"] = [int(round(float(v))) for v in rgbw[:4]]
        except Exception:
            continue
        try:
            de = float(o.get("dE", o.get("verifier_dE", np.inf)))
        except Exception:
            de = float("inf")
        o["dE"] = de
        ok = bool(o.get("ok", False)) or str(o.get("status", "")).lower() == "pass"
        if not ok and np.isfinite(de) and de <= float(dE_threshold):
            ok = True
        o["ok"] = bool(ok)
        o["status"] = "pass" if ok else "fail"
        by_key.setdefault(str(key), []).append(o)

    exact_pass: dict[str, dict] = {}
    exact_best: dict[str, dict] = {}
    pass_counts: dict[str, int] = {}
    fail_counts: dict[str, int] = {}
    for key, obs_list in by_key.items():
        sorted_all = sorted(obs_list, key=_feedback_observation_sort_value)
        exact_best[key] = sorted_all[0]
        pass_list = [o for o in sorted_all if o.get("ok")]
        fail_list = [o for o in sorted_all if not o.get("ok")]
        pass_counts[key] = len(pass_list)
        fail_counts[key] = len(fail_list)
        if pass_list:
            exact_pass[key] = pass_list[0]

    return {
        "schema_version": 3,
        "mode": "exact_pass_candidate",
        "dE_threshold": float(dE_threshold),
        "exact_pass": exact_pass,
        "exact_best": exact_best,
        "pass_counts": pass_counts,
        "fail_counts": fail_counts,
        "unique_rgb": len(by_key),
        "known_pass_rgb": len(exact_pass),
        "observation_count": sum(len(v) for v in by_key.values()),
    }


def load_feedback_candidate_model_for_args(args: argparse.Namespace) -> dict | None:
    """Load active measured feedback candidates from a v2 bank or verifier CSVs.

    The model is only loaded for feedback-mode candidate/reevaluate.  It is safe
    to pass through worker state because it contains only compact exact-key
    dictionaries, not the full verifier CSVs.
    """
    mode = str(getattr(args, "feedback_mode", "diagnostic") or "diagnostic").lower()
    if mode not in {"candidate", "reevaluate"}:
        return None

    dE_threshold = float(getattr(args, "feedback_trust_pass_dE", 2.5))
    observations: list[dict] = []

    # Prefer an explicit/non-auto bank if present.  Auto banks are still used if
    # they exist under config/dictionaries/<display_id>/.
    bank_arg = str(getattr(args, "feedback_bank", "auto") or "auto")
    bank_paths: list[Path] = []
    if bank_arg and bank_arg.lower() != "auto":
        bank_paths.append(Path(bank_arg))
    else:
        display_id = _safe_profile_id(getattr(args, "display_id", "") or getattr(args, "display_profile", "default_display") or "default_display")
        try:
            bank_paths.append(_feedback_bank_paths(args, display_id)[0])
        except Exception:
            pass

    for bp in bank_paths:
        try:
            if bp.exists():
                observations.extend(_iter_feedback_bank_observations(json.loads(bp.read_text(encoding="utf-8"))))
        except Exception:
            pass

    # Also allow direct verifier CSV directories.  This lets the replay harness
    # and early correction work without requiring a prebuilt bank.
    verifier_dir = getattr(args, "verifier_diagnostics_dir", None)
    if verifier_dir:
        try:
            vd = Path(verifier_dir)
            if vd.exists() and vd.is_dir():
                for csv_path in sorted(vd.glob("family_hull_latest_quick_verify*.csv")):
                    with csv_path.open("r", newline="", encoding="utf-8", errors="replace") as fh:
                        for row in csv.DictReader(fh):
                            obs = _feedback_obs_from_verifier_row(row, csv_path.name, dE_threshold)
                            if obs is not None:
                                observations.append(obs)
        except Exception:
            pass

    if not observations:
        return None
    return build_feedback_candidate_model_from_observations(observations, dE_threshold=dE_threshold)


def _apply_feedback_candidate_overrides(
    rgb_flat: np.ndarray,
    rgbw_flat: np.ndarray,
    family_flat: np.ndarray,
    args: argparse.Namespace,
    model: dict | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply exact verifier-passing measured candidates.

    This is the first active pass/fail correction mode.  It is deliberately
    exact-key only: if this display profile has a measured verifier-passing
    RGBW output for the same RGB input, use that measured-correct state.  This
    avoids reusing known failing solver branches while keeping interpolation and
    nearest-neighbor generalization out of the first correction pass.
    """
    mode = str(getattr(args, "feedback_mode", "diagnostic") or "diagnostic").lower()
    N = len(rgb_flat)
    hit = np.zeros(N, dtype=bool)
    if mode not in {"candidate", "reevaluate"}:
        return rgbw_flat, family_flat, hit
    if model is None:
        model = _worker_state.get("feedback_candidate_model")
    if not isinstance(model, dict):
        return rgbw_flat, family_flat, hit
    exact_pass = model.get("exact_pass", {})
    if not isinstance(exact_pass, dict) or not exact_pass:
        return rgbw_flat, family_flat, hit

    out = np.array(rgbw_flat, dtype=np.float64, copy=True)
    fam = np.array(family_flat, dtype=np.int32, copy=True)
    sample_scale = float(getattr(args, "sample_scale", 65535.0))
    for i in range(N):
        key = _feedback_rgb_key_from_rgb(rgb_flat[i])
        obs = exact_pass.get(key)
        if not isinstance(obs, dict):
            continue
        rgbw = obs.get("lut_rgbw")
        if rgbw is None:
            continue
        try:
            vals = np.asarray(rgbw, dtype=np.float64).reshape(4)
        except Exception:
            continue
        out[i] = np.clip(vals, 0.0, sample_scale)
        fam_i = _feedback_rgbw_family_index(out[i], sample_scale)
        if fam_i >= 0:
            fam[i] = fam_i
        hit[i] = True
    return out, fam, hit


def _solve_rgb_flat_points(rgb_flat: np.ndarray) -> dict[str, np.ndarray]:
    """Solve arbitrary RGB points with the same worker-state path used by the cube.

    This is used for exact named probes so we can debug representative points
    without generating or opening a multi-GB comparison CSV.
    """
    s = _worker_state
    target_rgb_basis: np.ndarray = s["target_rgb_basis"]
    raw_rgb_basis: np.ndarray = s["raw_rgb_basis"]
    family_bases: dict = s["family_bases"]
    family_tri: dict = s["family_tri"]
    family_tree: dict = s["family_tree"]
    family_xyz: dict = s["family_xyz"]
    family_rgbw: dict = s["family_rgbw"]
    white_xyz_ref: np.ndarray = s["white_xyz_ref"]
    y_scale: float = s["y_scale"]
    args = s["args"]

    zero_mask = rgb_flat.sum(axis=1) <= 0.0
    eps_topo = 1e-3 * args.sample_scale
    R_BIT, G_BIT, B_BIT = 1, 2, 4
    input_mask = (
        (rgb_flat[:, 0] > eps_topo).astype(np.int32) * R_BIT |
        (rgb_flat[:, 1] > eps_topo).astype(np.int32) * G_BIT |
        (rgb_flat[:, 2] > eps_topo).astype(np.int32) * B_BIT
    )

    nw = compute_neutral_weight(rgb_flat, args.sample_scale)
    xyz_flat, xyz_colour, xyz_raw, xyz_neutral = _target_xyz_from_effective_rgb_model(
        rgb_flat, raw_rgb_basis, target_rgb_basis, y_scale, nw,
        args.sample_scale, s.get("target_transform_matrix"),
    )

    tiebreak = getattr(args, "delta_e_tiebreak", 2.0)
    chroma_gate = getattr(args, "chroma_gate", 15.0)
    rgbw_flat = np.zeros((len(rgb_flat), 4), dtype=np.float64)
    family_flat = np.full(len(rgb_flat), -1, dtype=np.int32)
    w_axis_diag = {}

    neutral_mask = (nw >= 0.98) & ~zero_mask
    _n_tree = s.get("neutral_tree")
    _n_xyz = s.get("neutral_xyz")
    _n_rgbw = s.get("neutral_rgbw")
    if neutral_mask.any() and _n_tree is not None and _n_xyz is not None and _n_rgbw is not None:
        rgbw_flat[neutral_mask] = solve_neutral_axis_measured(
            xyz_flat[neutral_mask], _n_tree, _n_xyz, _n_rgbw,
            white_xyz_ref, args.sample_scale,
            residual_weights=s.get("neutral_residual_weights"),
            neutral_drive=np.mean(rgb_flat[neutral_mask], axis=1),
        )
        family_flat[neutral_mask] = 14  # rgbw label for hard neutral route

    # Non-neutral all-RGB values with a shared RGB component are now routed
    # through the generalized neutral/W target-axis solver as the primary path,
    # rather than as an optional family-hull tiebreaker.  Exact equal-RGB values
    # stay on the D65/reference neutral route above; exact duals/primaries stay
    # in family_hull below.
    common_min = np.minimum.reduce([rgb_flat[:, 0], rgb_flat[:, 1], rgb_flat[:, 2]])
    mixed_common_mask = (
        (~neutral_mask) & (~zero_mask) & (input_mask == (R_BIT | G_BIT | B_BIT)) &
        (common_min > 0.0)
    )
    if mixed_common_mask.any():
        _wc_rgbw, _wc_xyz, _wc_ok, _wc_diag = solve_w_dominant_target_axis(
            # Keep v27 target construction/neutral handling intact, but make the
            # mixed-common W-axis endpoint chase the row's verifier-compatible
            # colour xyY instead of any neutral-weight blended target.
            xyz_colour[mixed_common_mask],
            common_min[mixed_common_mask],
            white_xyz_ref,
            args.sample_scale,
            rgbw_basis=family_bases.get("rgbw"),
            w_key_tree=s.get("mixed_w_key_tree"),
            w_xyz=s.get("mixed_w_xyz"),
            w_rgbw=s.get("mixed_w_rgbw"),
            residual_weights=s.get("neutral_residual_weights"),
            input_rgb=rgb_flat[mixed_common_mask],
            family_key_tree=s.get("family_key_tree", {}),
            family_xyz=family_xyz,
            family_rgbw=family_rgbw,
            family_bases=family_bases,
            k=64,
            return_diagnostics=True,
        )
        _mixed_idx = np.where(mixed_common_mask)[0]
        # Store diagnostics for every mixed-common row, including rows that fall
        # back to family_hull.
        w_axis_diag = {k: np.full(len(rgb_flat), np.nan if v.dtype.kind == "f" else 0, dtype=v.dtype) for k, v in _wc_diag.items()}
        for _dk, _dv in _wc_diag.items():
            w_axis_diag[_dk][_mixed_idx] = _dv
        # Diagnostic-only in v33 recovery: do not hard-accept the mixed-common
        # W-axis pre-route before the family solver has a chance to compare the
        # measured RGB/RGBW/sub-gamut candidates.  The v30/v32 verifier showed
        # that this hard pre-route replaced many previously passing rows with
        # bad sub-gamut anchors.  The same W-axis candidate is still generated
        # and scored inside solve_rgbw_by_family_hull(), where colour/Y accuracy
        # can beat decomposition preference.
        if False and np.any(_wc_ok):
            _ok_idx = _mixed_idx[_wc_ok]
            rgbw_flat[_ok_idx] = _wc_rgbw[_wc_ok]
            _fam_from_axis = _wc_diag.get("w_axis_family", np.full(len(_wc_ok), 14, dtype=np.int32))
            family_flat[_ok_idx] = np.where(_fam_from_axis[_wc_ok] >= 0, _fam_from_axis[_wc_ok], 14)
            mixed_common_mask[_ok_idx] = False

    measured_candidate_hit = np.zeros(len(rgb_flat), dtype=bool)
    measured_candidate_de = np.full(len(rgb_flat), np.nan, dtype=np.float64)
    measured_candidate_score = np.full(len(rgb_flat), np.nan, dtype=np.float64)
    if str(getattr(args, "measured_candidate_solver", "active") or "active").lower() != "off":
        _mc_rgbw, _mc_fam, _mc_ok, _mc_de, _mc_score = _measured_candidate_solver_vectorised(
            rgb_flat, xyz_flat, family_xyz, family_rgbw, white_xyz_ref, args.sample_scale,
            input_mask, nw, raw_rgb_basis=raw_rgb_basis,
            top_k=int(getattr(args, "measured_candidate_top_k", 768)),
        )
        measured_candidate_de = _mc_de
        measured_candidate_score = _mc_score
        _mc_mode = str(getattr(args, "measured_candidate_solver", "active") or "active").lower()
        _mc_gate = float(getattr(args, "measured_candidate_de_threshold", 5.0))
        measured_candidate_hit = _mc_ok & (_mc_de <= _mc_gate)
        # Apply before generic family scoring so measured-correct physical capture
        # candidates are not overwritten by weak branch/scoring choices.  Neutral
        # rows stay on the dedicated neutral-axis solver above.
        if _mc_mode == "active" and np.any(measured_candidate_hit):
            _mc_apply = measured_candidate_hit & (~neutral_mask)
            rgbw_flat[_mc_apply] = _mc_rgbw[_mc_apply]
            family_flat[_mc_apply] = _mc_fam[_mc_apply]

    non_neutral = ~(neutral_mask | (family_flat >= 0))
    if non_neutral.any():
        _nn_rgbw, _nn_fam = solve_rgbw_by_family_hull(
            xyz_flat[non_neutral], family_bases, family_tri, family_tree,
            s.get("family_key_tree", {}), family_xyz, family_rgbw, white_xyz_ref, args.sample_scale,
            delta_e_tiebreak=tiebreak, chroma_gate=chroma_gate,
            zero_mask=zero_mask[non_neutral], input_masks=input_mask[non_neutral],
            input_rgb=rgb_flat[non_neutral],
            neutral_weights=nw[non_neutral],
            neutral_residual_weights=s.get("neutral_residual_weights"),
            mixed_w_key_tree=s.get("mixed_w_key_tree"),
            mixed_w_xyz=s.get("mixed_w_xyz"),
            mixed_w_rgbw=s.get("mixed_w_rgbw"),
            raw_rgb_basis=raw_rgb_basis,
            target_rgb_basis=target_rgb_basis,
            y_scale=y_scale,
            target_transform_matrix=s.get("target_transform_matrix"),
            neutral_key_tree=s.get("neutral_tree"),
            neutral_xyz=s.get("neutral_xyz"),
            neutral_rgbw=s.get("neutral_rgbw"),
        )
        rgbw_flat[non_neutral] = _nn_rgbw
        family_flat[non_neutral] = _nn_fam

    rgbw_flat, family_flat = _apply_output_guardrails(
        rgb_flat, rgbw_flat, family_flat, input_mask, nw, args.sample_scale
    )

    # Active verifier-feedback candidate correction.  This is applied after the
    # normal solver/guardrail path so a measured-passing state from the display
    # profile remains the final source of truth for exact RGB keys.
    rgbw_flat, family_flat, feedback_candidate_hit = _apply_feedback_candidate_overrides(
        rgb_flat, rgbw_flat, family_flat, args, s.get("feedback_candidate_model")
    )

    common_candidate_rgbw, common_candidate_xyz, common_candidate_active, common_candidate_source = _build_common_min_candidate_vectorised(
        # Diagnostics for the mixed-common axis candidate should use the same
        # row-colour target as the primary mixed-common route.  This does not
        # alter v27 target construction, dual handling, or soft neutral scoring.
        rgb_flat, xyz_colour,
        raw_rgb_basis, target_rgb_basis, y_scale,
        family_bases, s.get("family_key_tree", {}), family_xyz, family_rgbw,
        white_xyz_ref, args.sample_scale,
        s.get("neutral_tree"), s.get("neutral_xyz"), s.get("neutral_rgbw"),
        neutral_residual_weights=s.get("neutral_residual_weights"),
        mixed_w_key_tree=s.get("mixed_w_key_tree"),
        mixed_w_xyz=s.get("mixed_w_xyz"),
        mixed_w_rgbw=s.get("mixed_w_rgbw"),
        target_transform_matrix=s.get("target_transform_matrix"),
    )

    return {
        "rgbw": np.clip(rgbw_flat, 0.0, args.sample_scale),
        "family": family_flat,
        "target_xyz": xyz_flat,
        "target_colour_xyz": xyz_colour,
        "target_raw_xyz": xyz_raw,
        "target_neutral_xyz": xyz_neutral,
        "neutral_weight": nw,
        "input_mask": input_mask,
        "zero_mask": zero_mask,
        "common_candidate_rgbw": common_candidate_rgbw,
        "common_candidate_xyz": common_candidate_xyz,
        "common_candidate_active": common_candidate_active,
        "common_candidate_source": common_candidate_source,
        "w_axis_diag": w_axis_diag,
        "feedback_candidate_hit": feedback_candidate_hit,
        "measured_candidate_hit": measured_candidate_hit,
        "measured_candidate_de": measured_candidate_de,
        "measured_candidate_score": measured_candidate_score,
    }


def write_probe_debug_csv(
    output_path: Path,
    xyz_points: np.ndarray,
    rgbw_points: np.ndarray,
    target_rgb_basis: np.ndarray,
    raw_rgb_basis: np.ndarray,
    white_basis: np.ndarray,
    reference_white: ReferenceWhite,
    args: argparse.Namespace,
    family_bases: dict,
    family_capture_sets: dict,
    y_scale: float,
) -> None:
    """Write a compact exact-probe diagnostic CSV.

    This intentionally avoids the giant full comparison CSV.  The rows show the
    exact solver target, neutral routing, selected family, output RGBW, a simple
    linear predicted xy/Y, and nearest measured state to the target.  The nearest
    measured columns are diagnostic only; they help identify whether the capture
    set already contains a good solution near the target.
    """
    target_transform_matrix = None

    state = {
        "axis": np.array([], dtype=np.float64),
        "target_rgb_basis": target_rgb_basis,
        "raw_rgb_basis": raw_rgb_basis,
        "target_transform_matrix": target_transform_matrix,
        "family_bases": family_bases,
        "family_capture_xyz": {fk: v[0] for fk, v in family_capture_sets.items()},
        "family_capture_rgbw": {fk: v[1] for fk, v in family_capture_sets.items()},
        "white_xyz_ref": reference_white.xyz,
        "reference_white": reference_white,
        "y_scale": float(y_scale),
        "args": args,
        "feedback_candidate_model": load_feedback_candidate_model_for_args(args),
    }
    _worker_init(state)

    names = list(_PROBE_RGBS.keys())
    rgb_flat = np.array([_PROBE_RGBS[n] for n in names], dtype=np.float64)
    solved = _solve_rgb_flat_points(rgb_flat)
    rgbw = solved["rgbw"]
    target_xyz = solved["target_xyz"]
    target_colour_xyz = solved.get("target_colour_xyz", target_xyz)
    target_raw_xyz = solved.get("target_raw_xyz", target_xyz)
    target_neutral_xyz = solved.get("target_neutral_xyz", target_xyz)
    nw = solved["neutral_weight"]
    input_mask = solved["input_mask"]
    fam_idx = solved["family"]
    common_candidate_rgbw = solved.get("common_candidate_rgbw", np.zeros_like(rgbw))
    common_candidate_xyz = solved.get("common_candidate_xyz", np.zeros_like(target_xyz))
    common_candidate_active = solved.get("common_candidate_active", np.zeros(len(rgbw), dtype=bool))
    common_candidate_source = solved.get("common_candidate_source", np.zeros(len(rgbw), dtype=np.int32))
    w_axis_diag = solved.get("w_axis_diag", {})
    fam_names = [fd[0] for fd in _FAMILY_DEFS]

    # Target-space nearest measured diagnostic over all captures.
    cap_lab = _xyz_to_lab_vectorised(np.maximum(xyz_points, 0.0), reference_white.xyz)
    cap_logY = np.log(np.maximum(xyz_points[:, 1], 1e-6))
    cap_key = np.column_stack([cap_lab[:, 0] * 0.05, cap_lab[:, 1], cap_lab[:, 2], cap_logY * 0.20])
    cap_tree = cKDTree(cap_key)

    rows: list[dict] = []
    for i, name in enumerate(names):
        t_xyz = target_xyz[i]
        t_sum = max(float(t_xyz.sum()), 1e-9)
        t_x = float(t_xyz[0] / t_sum)
        t_y = float(t_xyz[1] / t_sum)
        t_lab = _xyz_to_lab_vectorised(t_xyz.reshape(1, 3), reference_white.xyz)[0]
        t_key = np.array([[t_lab[0] * 0.05, t_lab[1], t_lab[2], np.log(max(t_xyz[1], 1e-6)) * 0.20]])
        _, nn_idx_arr = cap_tree.query(t_key, k=1)
        nn_idx = int(np.ravel(nn_idx_arr)[0])
        nn_xyz = xyz_points[nn_idx]
        nn_rgbw = rgbw_points[nn_idx]
        nn_lab = cap_lab[nn_idx]
        nn_de = float(np.linalg.norm(nn_lab - t_lab))

        pred_xyz = raw_rgb_basis @ rgbw[i, :3] + white_basis * rgbw[i, 3]
        p_sum = max(float(pred_xyz.sum()), 1e-9)
        p_lab = _xyz_to_lab_vectorised(np.maximum(pred_xyz.reshape(1, 3), 0.0), reference_white.xyz)[0]
        p_de = float(np.linalg.norm(p_lab - t_lab))

        _inp_rgb = rgb_flat[i]
        _out_rgb = rgbw[i, :3]
        _active = _inp_rgb > (1e-3 * args.sample_scale)
        if np.any(_active):
            _inp_active = _inp_rgb[_active]
            _out_active = _out_rgb[_active]
            _input_active_max = float(np.max(_inp_active))
            _output_active_max = float(np.max(_out_active))
            _input_active_minmax_ratio = float(np.min(_inp_active) / max(np.max(_inp_active), 1.0))
            _output_active_minmax_ratio = float(np.min(_out_active) / max(np.max(_out_active), 1.0))
            _output_active_max_ratio = float(_output_active_max / max(_input_active_max, 1.0))
        else:
            _input_active_max = _output_active_max = 0.0
            _input_active_minmax_ratio = _output_active_minmax_ratio = _output_active_max_ratio = 0.0
        _input_common_min = float(np.min(_inp_rgb))
        _out_w_to_common_ratio = float(rgbw[i, 3] / max(_input_common_min, 1.0)) if _input_common_min > 0 else 0.0
        _expected_common_w = _input_common_min
        _common_w_deficit = float(max(0.0, _expected_common_w - rgbw[i, 3])) if _expected_common_w > 0 else 0.0
        _common_w_band_ok = bool((_expected_common_w <= 0.0) or (rgbw[i, 3] >= 0.50 * _expected_common_w and rgbw[i, 3] <= 1.20 * _expected_common_w + 512.0))

        _raw_t = target_raw_xyz[i]
        _raw_s = max(float(_raw_t.sum()), 1e-9)
        _colour_t = target_colour_xyz[i]
        _colour_s = max(float(_colour_t.sum()), 1e-9)
        _neu_t = target_neutral_xyz[i]
        _neu_s = max(float(_neu_t.sum()), 1e-9)
        _probe_class, _probe_note = _PROBE_META.get(name, ("unknown", ""))
        _A = _worker_state.get("target_transform_matrix")
        if _A is not None:
            _eff = (_inp_rgb / float(args.sample_scale)) @ np.asarray(_A, dtype=np.float64)
        else:
            _eff = np.array([float("nan"), float("nan"), float("nan")])

        _route = "neutral" if nw[i] >= 0.98 and rgb_flat[i].sum() > 0 else "family_hull"
        _w_axis_selected_i = bool(w_axis_diag.get("w_axis_selected", np.zeros(len(rgbw), dtype=bool))[i])
        if _route == "family_hull" and input_mask[i] == 7 and _input_common_min > 0 and _w_axis_selected_i:
            _route = "w_dominant_target_axis"

        rows.append({
            "patch": name,
            "probe_class": _probe_class,
            "probe_note": _probe_note,
            "input_r": int(rgb_flat[i, 0]),
            "input_g": int(rgb_flat[i, 1]),
            "input_b": int(rgb_flat[i, 2]),
            "target_x": t_x,
            "target_y": t_y,
            "target_Y": float(t_xyz[1]),
            "colour_target_x": float(_colour_t[0] / _colour_s),
            "colour_target_y": float(_colour_t[1] / _colour_s),
            "colour_target_Y": float(_colour_t[1]),
            "effective_r": float(_eff[0]),
            "effective_g": float(_eff[1]),
            "effective_b": float(_eff[2]),
            "raw_target_x": float(_raw_t[0] / _raw_s),
            "raw_target_y": float(_raw_t[1] / _raw_s),
            "raw_target_Y": float(_raw_t[1]),
            "neutral_target_x": float(_neu_t[0] / _neu_s),
            "neutral_target_y": float(_neu_t[1] / _neu_s),
            "neutral_target_Y": float(_neu_t[1]),
            "neutral_weight": float(nw[i]),
            "input_mask": int(input_mask[i]),
            "route": _route,
            "winner_family": fam_names[int(fam_idx[i])] if fam_idx[i] >= 0 else "none",
            "out_r": float(rgbw[i, 0]),
            "out_g": float(rgbw[i, 1]),
            "out_b": float(rgbw[i, 2]),
            "out_w": float(rgbw[i, 3]),
            "input_active_max": _input_active_max,
            "output_active_max": _output_active_max,
            "output_active_max_ratio": _output_active_max_ratio,
            "input_active_minmax_ratio": _input_active_minmax_ratio,
            "output_active_minmax_ratio": _output_active_minmax_ratio,
            "input_common_min": _input_common_min,
            "expected_common_w": _expected_common_w,
            "common_w_deficit": _common_w_deficit,
            "common_w_band_ok": _common_w_band_ok,
            "out_w_to_common_ratio": _out_w_to_common_ratio,
            "common_candidate_active": bool(common_candidate_active[i]),
            "common_candidate_source": int(common_candidate_source[i]),
            "common_candidate_r": float(common_candidate_rgbw[i, 0]),
            "common_candidate_g": float(common_candidate_rgbw[i, 1]),
            "common_candidate_b": float(common_candidate_rgbw[i, 2]),
            "common_candidate_w": float(common_candidate_rgbw[i, 3]),
            "common_candidate_pred_x": float(common_candidate_xyz[i, 0] / max(float(common_candidate_xyz[i].sum()), 1e-9)) if common_candidate_active[i] else float("nan"),
            "common_candidate_pred_y": float(common_candidate_xyz[i, 1] / max(float(common_candidate_xyz[i].sum()), 1e-9)) if common_candidate_active[i] else float("nan"),
            "common_candidate_pred_Y": float(common_candidate_xyz[i, 1]) if common_candidate_active[i] else float("nan"),
            "common_candidate_selected": bool(common_candidate_active[i] and np.allclose(rgbw[i], common_candidate_rgbw[i], rtol=0.0, atol=2.0)),
            "w_axis_active": bool(np.isfinite(w_axis_diag.get("w_axis_deltaE", np.full(len(rgbw), np.inf))[i])),
            "w_axis_endpoint_r": float(w_axis_diag.get("w_axis_endpoint_r", np.full(len(rgbw), np.nan))[i]),
            "w_axis_endpoint_g": float(w_axis_diag.get("w_axis_endpoint_g", np.full(len(rgbw), np.nan))[i]),
            "w_axis_endpoint_b": float(w_axis_diag.get("w_axis_endpoint_b", np.full(len(rgbw), np.nan))[i]),
            "w_axis_endpoint_w": float(w_axis_diag.get("w_axis_endpoint_w", np.full(len(rgbw), np.nan))[i]),
            "w_axis_endpoint_x": float(w_axis_diag.get("w_axis_endpoint_x", np.full(len(rgbw), np.nan))[i]),
            "w_axis_endpoint_y": float(w_axis_diag.get("w_axis_endpoint_y", np.full(len(rgbw), np.nan))[i]),
            "w_axis_endpoint_Y": float(w_axis_diag.get("w_axis_endpoint_Y", np.full(len(rgbw), np.nan))[i]),
            "w_axis_scaled_r": float(w_axis_diag.get("w_axis_scaled_r", np.full(len(rgbw), np.nan))[i]),
            "w_axis_scaled_g": float(w_axis_diag.get("w_axis_scaled_g", np.full(len(rgbw), np.nan))[i]),
            "w_axis_scaled_b": float(w_axis_diag.get("w_axis_scaled_b", np.full(len(rgbw), np.nan))[i]),
            "w_axis_scaled_w": float(w_axis_diag.get("w_axis_scaled_w", np.full(len(rgbw), np.nan))[i]),
            "w_axis_scaled_x": float(w_axis_diag.get("w_axis_scaled_x", np.full(len(rgbw), np.nan))[i]),
            "w_axis_scaled_y": float(w_axis_diag.get("w_axis_scaled_y", np.full(len(rgbw), np.nan))[i]),
            "w_axis_scaled_Y": float(w_axis_diag.get("w_axis_scaled_Y", np.full(len(rgbw), np.nan))[i]),
            "w_axis_deltaE": float(w_axis_diag.get("w_axis_deltaE", np.full(len(rgbw), np.inf))[i]),
            "w_axis_xy_err": float(w_axis_diag.get("w_axis_xy_err", np.full(len(rgbw), np.inf))[i]),
            "w_axis_y_err": float(w_axis_diag.get("w_axis_y_err", np.full(len(rgbw), np.inf))[i]),
            "w_axis_residual_energy": float(w_axis_diag.get("w_axis_residual_energy", np.full(len(rgbw), np.inf))[i]),
            "w_axis_w_to_common": float(w_axis_diag.get("w_axis_w_to_common", np.full(len(rgbw), np.nan))[i]),
            "w_axis_source": int(w_axis_diag.get("w_axis_source", np.zeros(len(rgbw), dtype=np.int32))[i]),
            "w_axis_family": int(w_axis_diag.get("w_axis_family", np.full(len(rgbw), -1, dtype=np.int32))[i]),
            "w_axis_family_name": fam_names[int(w_axis_diag.get("w_axis_family", np.full(len(rgbw), -1, dtype=np.int32))[i])] if int(w_axis_diag.get("w_axis_family", np.full(len(rgbw), -1, dtype=np.int32))[i]) >= 0 else "none",
            "w_axis_subgamut_rank": int(w_axis_diag.get("w_axis_subgamut_rank", np.zeros(len(rgbw), dtype=np.int32))[i]),
            "w_axis_selected": bool(w_axis_diag.get("w_axis_selected", np.zeros(len(rgbw), dtype=bool))[i]),
            "scaled_anchor_role": int(w_axis_diag.get("scaled_anchor_role", np.zeros(len(rgbw), dtype=np.int32))[i]),
            "scaled_anchor_family_mask": int(w_axis_diag.get("scaled_anchor_family_mask", np.zeros(len(rgbw), dtype=np.int32))[i]),
            "scaled_anchor_source_r": float(w_axis_diag.get("scaled_anchor_source_r", np.full(len(rgbw), np.nan))[i]),
            "scaled_anchor_source_g": float(w_axis_diag.get("scaled_anchor_source_g", np.full(len(rgbw), np.nan))[i]),
            "scaled_anchor_source_b": float(w_axis_diag.get("scaled_anchor_source_b", np.full(len(rgbw), np.nan))[i]),
            "scaled_anchor_source_w": float(w_axis_diag.get("scaled_anchor_source_w", np.full(len(rgbw), np.nan))[i]),
            "scaled_anchor_scale": float(w_axis_diag.get("scaled_anchor_scale", np.full(len(rgbw), np.nan))[i]),
            "scaled_anchor_scale_mode": int(w_axis_diag.get("scaled_anchor_scale_mode", np.zeros(len(rgbw), dtype=np.int32))[i]),
            "scaled_anchor_xy_err": float(w_axis_diag.get("scaled_anchor_xy_err", np.full(len(rgbw), np.inf))[i]),
            "scaled_anchor_y_log_err": float(w_axis_diag.get("scaled_anchor_y_log_err", np.full(len(rgbw), np.inf))[i]),
            "scaled_anchor_deltaE": float(w_axis_diag.get("scaled_anchor_deltaE", np.full(len(rgbw), np.inf))[i]),
            "scaled_anchor_selected": bool(w_axis_diag.get("scaled_anchor_selected", np.zeros(len(rgbw), dtype=bool))[i]),
            "scaled_anchor_blocked_reason": int(w_axis_diag.get("scaled_anchor_blocked_reason", np.zeros(len(rgbw), dtype=np.int32))[i]),
            "family_fallback_selected": bool(input_mask[i] == 7 and _input_common_min > 0 and not (fam_idx[i] == 14)),
            "pred_x_linear": float(pred_xyz[0] / p_sum),
            "pred_y_linear": float(pred_xyz[1] / p_sum),
            "pred_Y_linear": float(pred_xyz[1]),
            "pred_deltaE_linear": p_de,
            "nearest_measured_r": float(nn_rgbw[0]),
            "nearest_measured_g": float(nn_rgbw[1]),
            "nearest_measured_b": float(nn_rgbw[2]),
            "nearest_measured_w": float(nn_rgbw[3]),
            "nearest_measured_Y": float(nn_xyz[1]),
            "nearest_measured_deltaE": nn_de,
        })

    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _solve_r_slice(job: tuple) -> tuple:
    """Solve all (g, b) cells for one r-slice using the family-hull solver."""
    r_index, r_value, build_comparison = job
    s = _worker_state
    axis: np.ndarray                = s["axis"]
    target_rgb_basis: np.ndarray    = s["target_rgb_basis"]
    raw_rgb_basis: np.ndarray       = s["raw_rgb_basis"]
    family_bases: dict              = s["family_bases"]
    family_tri:  dict               = s["family_tri"]
    family_tree: dict               = s["family_tree"]
    family_xyz:  dict               = s["family_xyz"]
    family_rgbw: dict               = s["family_rgbw"]
    white_xyz_ref: np.ndarray       = s["white_xyz_ref"]    # (3,) reference white XYZ
    reference_white: ReferenceWhite = s["reference_white"]
    y_scale: float                  = s["y_scale"]          # Item 1: luminance scale
    args                            = s["args"]

    grid_size = axis.size

    # Build (r, g, b) grid for this r-slice
    g_vals, b_vals = np.meshgrid(axis, axis, indexing="ij")   # (G, B)
    rgb_flat = np.stack(
        [np.full((grid_size, grid_size), r_value), g_vals, b_vals], axis=-1
    ).reshape(-1, 3)                                            # (N, 3)

    zero_mask = rgb_flat.sum(axis=1) <= 0.0

    # Input topology mask — computed before xyz_flat so dual-channel target
    # calibration can use it to identify rg/rb/gb nodes.
    eps_topo = 1e-3 * args.sample_scale
    R_BIT, G_BIT, B_BIT = 1, 2, 4
    input_mask = (
        (rgb_flat[:, 0] > eps_topo).astype(np.int32) * R_BIT |
        (rgb_flat[:, 1] > eps_topo).astype(np.int32) * G_BIT |
        (rgb_flat[:, 2] > eps_topo).astype(np.int32) * B_BIT
    )                                                          # (N,) int32

    # -----------------------------------------------------------------------
    # Target XYZ construction
    # -----------------------------------------------------------------------
    # Non-neutral source RGB first passes through a fitted linear target-space
    # matrix into measured-primary barycentric coordinates.  This fixes the
    # systematic mismatch where semantic hue inputs such as yellow/orange/rose
    # were being paired with the wrong xyY target.  Equal/nearly-equal RGB values
    # still blend to the D65/reference-white neutral axis.
    nw = compute_neutral_weight(rgb_flat, args.sample_scale)  # (N,) in [0, 1]
    xyz_flat, xyz_colour, xyz_raw, xyz_neutral = _target_xyz_from_effective_rgb_model(
        rgb_flat, raw_rgb_basis, target_rgb_basis, y_scale, nw,
        args.sample_scale, s.get("target_transform_matrix"),
    )

    # Items 2\u20135: per-family Delaunay hull lookup + 3^n bounded NNLS fallback,
    # scored by full Lab \u0394E with chroma-gated W preference.
    # Items 2–5: RGBW solve with hard neutral partition.
    # Neutral-axis nodes (nw >= 0.98) are hard-routed to the dedicated
    # W-dominant measured curve so generic family scoring cannot override them.
    # All other nodes (saturated primaries, skin tones, dual-channel edges)
    # go through the standard family-hull competition.
    tiebreak    = getattr(args, "delta_e_tiebreak", 2.0)
    chroma_gate = getattr(args, "chroma_gate",      15.0)
    _RGBW_FAM_IDX = 14   # "rgbw" index in _FAMILY_DEFS
    _N = len(rgb_flat)
    rgbw_flat   = np.zeros((_N, 4), dtype=np.float64)
    family_flat = np.full(_N, -1, dtype=np.int32)

    neutral_mask = (nw >= 0.98) & ~zero_mask
    _n_tree  = s.get("neutral_tree")
    _n_xyz   = s.get("neutral_xyz")
    _n_rgbw  = s.get("neutral_rgbw")
    if neutral_mask.any() and _n_tree is not None and _n_xyz is not None and _n_rgbw is not None:
        rgbw_flat[neutral_mask] = solve_neutral_axis_measured(
            xyz_flat[neutral_mask],
            _n_tree, _n_xyz, _n_rgbw,
            white_xyz_ref, args.sample_scale,
            residual_weights=s.get("neutral_residual_weights"),
            neutral_drive=np.mean(rgb_flat[neutral_mask], axis=1),
        )
        family_flat[neutral_mask] = _RGBW_FAM_IDX

    common_min = np.minimum.reduce([rgb_flat[:, 0], rgb_flat[:, 1], rgb_flat[:, 2]])
    mixed_common_mask = (
        (~neutral_mask) & (~zero_mask) & (input_mask == (R_BIT | G_BIT | B_BIT)) &
        (common_min > 0.0)
    )
    if mixed_common_mask.any():
        _wc_rgbw, _wc_xyz, _wc_ok, _wc_diag = solve_w_dominant_target_axis(
            # Keep v27 target construction/neutral handling intact, but make the
            # mixed-common W-axis endpoint chase the row's verifier-compatible
            # colour xyY instead of any neutral-weight blended target.
            xyz_colour[mixed_common_mask],
            common_min[mixed_common_mask],
            white_xyz_ref,
            args.sample_scale,
            rgbw_basis=family_bases.get("rgbw"),
            w_key_tree=s.get("mixed_w_key_tree"),
            w_xyz=s.get("mixed_w_xyz"),
            w_rgbw=s.get("mixed_w_rgbw"),
            residual_weights=s.get("neutral_residual_weights"),
            input_rgb=rgb_flat[mixed_common_mask],
            family_key_tree=s.get("family_key_tree", {}),
            family_xyz=family_xyz,
            family_rgbw=family_rgbw,
            family_bases=family_bases,
            k=64,
            return_diagnostics=True,
        )
        _mixed_idx = np.where(mixed_common_mask)[0]
        # Diagnostic-only: do not hard-accept the mixed-common W-axis pre-route before
        # family scoring / measured feedback can arbitrate.
        if False and np.any(_wc_ok):
            _ok_idx = _mixed_idx[_wc_ok]
            rgbw_flat[_ok_idx] = _wc_rgbw[_wc_ok]
            _fam_from_axis = _wc_diag.get("w_axis_family", np.full(len(_wc_ok), _RGBW_FAM_IDX, dtype=np.int32))
            family_flat[_ok_idx] = np.where(_fam_from_axis[_wc_ok] >= 0, _fam_from_axis[_wc_ok], _RGBW_FAM_IDX)
            mixed_common_mask[_ok_idx] = False

    measured_candidate_hit = np.zeros(len(rgb_flat), dtype=bool)
    measured_candidate_de = np.full(len(rgb_flat), np.nan, dtype=np.float64)
    if str(getattr(args, "measured_candidate_solver", "active") or "active").lower() != "off":
        _mc_rgbw, _mc_fam, _mc_ok, _mc_de, _mc_score = _measured_candidate_solver_vectorised(
            rgb_flat, xyz_flat, family_xyz, family_rgbw, white_xyz_ref, args.sample_scale,
            input_mask, nw, raw_rgb_basis=raw_rgb_basis,
            top_k=int(getattr(args, "measured_candidate_top_k", 768)),
        )
        measured_candidate_de = _mc_de
        _mc_mode = str(getattr(args, "measured_candidate_solver", "active") or "active").lower()
        _mc_gate = float(getattr(args, "measured_candidate_de_threshold", 5.0))
        measured_candidate_hit = _mc_ok & (_mc_de <= _mc_gate)
        if _mc_mode == "active" and np.any(measured_candidate_hit):
            _mc_apply = measured_candidate_hit & (~neutral_mask)
            rgbw_flat[_mc_apply] = _mc_rgbw[_mc_apply]
            family_flat[_mc_apply] = _mc_fam[_mc_apply]

    non_neutral = ~(neutral_mask | (family_flat >= 0))
    if non_neutral.any():
        _nn_rgbw, _nn_fam = solve_rgbw_by_family_hull(
            xyz_flat[non_neutral],
            family_bases,
            family_tri,
            family_tree,
            s.get("family_key_tree", {}),
            family_xyz,
            family_rgbw,
            white_xyz_ref,
            args.sample_scale,
            delta_e_tiebreak=tiebreak,
            chroma_gate=chroma_gate,
            zero_mask=zero_mask[non_neutral],
            input_masks=input_mask[non_neutral],
            input_rgb=rgb_flat[non_neutral],
            neutral_weights=nw[non_neutral],
            neutral_residual_weights=s.get("neutral_residual_weights"),
            mixed_w_key_tree=s.get("mixed_w_key_tree"),
            mixed_w_xyz=s.get("mixed_w_xyz"),
            mixed_w_rgbw=s.get("mixed_w_rgbw"),
            raw_rgb_basis=raw_rgb_basis,
            target_rgb_basis=target_rgb_basis,
            y_scale=y_scale,
            target_transform_matrix=s.get("target_transform_matrix"),
            neutral_key_tree=s.get("neutral_tree"),
            neutral_xyz=s.get("neutral_xyz"),
            neutral_rgbw=s.get("neutral_rgbw"),
        )
        rgbw_flat[non_neutral]   = _nn_rgbw
        family_flat[non_neutral] = _nn_fam

    rgbw_flat, family_flat = _apply_output_guardrails(
        rgb_flat, rgbw_flat, family_flat, input_mask, nw, args.sample_scale
    )

    # Active verifier-feedback candidate correction.  Exact RGB keys with a
    # measured-passing observation in the display feedback model override the
    # ordinary solver output.  This keeps known-good measured states from being
    # replaced by newly introduced solver branches.
    rgbw_flat, family_flat, feedback_candidate_hit = _apply_feedback_candidate_overrides(
        rgb_flat, rgbw_flat, family_flat, args, s.get("feedback_candidate_model")
    )

    # Per-family node count for this slice (aggregated in build_delaunay_cube)
    fam_names  = [fd[0] for fd in _FAMILY_DEFS]
    fam_counts: dict[str, int] = {}
    for fi, fname in enumerate(fam_names):
        cnt = int(np.sum(family_flat == fi))
        if cnt:
            fam_counts[fname] = cnt

    slice_cube = np.clip(
        rgbw_flat.reshape(grid_size, grid_size, 4), 0.0, args.sample_scale
    ).astype(np.float32)

    # ------------------------------------------------------------------
    # Comparison rows (coarse builds only)
    # ------------------------------------------------------------------
    slice_rows: list[dict] = []
    if build_comparison:
        for g_i in range(grid_size):
            g_val = float(axis[g_i])
            for b_i in range(grid_size):
                b_val = float(axis[b_i])
                flat_i = g_i * grid_size + b_i
                rgbw = slice_cube[g_i, b_i].astype(np.float64)
                rgbw_sum = float(rgbw.sum())
                target_xyz_i = xyz_flat[flat_i]

                if target_xyz_i[1] > 0.0:
                    t_lab = xyz_to_lab(target_xyz_i, reference_white)
                    t_L, t_C, t_h = lab_to_lch(t_lab)
                else:
                    t_L, t_C, t_h = 0.0, 0.0, 0.0

                classic_w = float(min(r_value, g_val, b_val))

                slice_rows.append({
                    "r_index": r_index,
                    "g_index": g_i,
                    "b_index": b_i,
                    "target_r": float(r_value),
                    "target_g": g_val,
                    "target_b": b_val,
                    "target_L": float(t_L),
                    "target_C": float(t_C),
                    "target_h": float(t_h),
                    "classic_w": classic_w,
                    "out_r": float(rgbw[0]),
                    "out_g": float(rgbw[1]),
                    "out_b": float(rgbw[2]),
                    "out_w": float(rgbw[3]),
                    "w_pct": float(100.0 * rgbw[3] / rgbw_sum) if rgbw_sum > 0.0 else 0.0,
                    "white_gain_abs": float(rgbw[3] - classic_w),
                    "in_hull": True,
                    "projected": False,
                    "bary_min": 1.0,
                    "family": fam_names[int(family_flat[flat_i])] if family_flat[flat_i] >= 0 else "none",
                    "feedback_candidate_hit": bool(feedback_candidate_hit[flat_i]) if 'feedback_candidate_hit' in locals() else False,
                    "measured_candidate_hit": bool(measured_candidate_hit[flat_i]) if 'measured_candidate_hit' in locals() else False,
                    "measured_candidate_de": float(measured_candidate_de[flat_i]) if 'measured_candidate_de' in locals() else float('nan'),
                    "neutral_weight": float(nw[flat_i]),
                    "target_Y": float(target_xyz_i[1]),
                    "target_x": float(target_xyz_i[0] / max(float(target_xyz_i.sum()), 1e-9)),
                    "target_y": float(target_xyz_i[1] / max(float(target_xyz_i.sum()), 1e-9)),
                })

    return r_index, slice_cube, slice_rows, fam_counts


# ---------------------------------------------------------------------------
# LUT cube builder
# ---------------------------------------------------------------------------

def build_delaunay_cube(
    axis: np.ndarray,
    xyz_points: np.ndarray,
    rgbw_points: np.ndarray,
    target_rgb_basis: np.ndarray,
    reference_white: ReferenceWhite,
    args: argparse.Namespace,
    build_comparison: bool = True,
    progress_callback=None,
    y_scale: float = 1.0,
    raw_rgb_basis: np.ndarray | None = None,
    family_bases: dict | None = None,
    wb_scales: np.ndarray | None = None,
    family_capture_sets: dict | None = None,
) -> tuple[np.ndarray, list[dict], set[int]]:
    """Build an RGBW LUT cube using the full README architecture.

    Each LUT node is solved by:
      1. Scaling the target XYZ by y_scale (RGBW-inclusive luminance model).
      2. Per-family Delaunay hull lookup over measured captures (physical
         forward model + sub-gamut partitioning).
      3. Full Lab \u0394E scoring with chroma-gated W preference.

    ``raw_rgb_basis`` and ``wb_scales`` are retained for GUI signature
    compatibility and are not used here.
    """
    grid_size = axis.size
    n_workers = max(1, args.workers or (os.cpu_count() or 1))
    n_workers = min(n_workers, grid_size)

    if family_bases is None:
        family_bases = {}
    if family_capture_sets is None:
        family_capture_sets = {}

    white_xyz_ref = reference_white.xyz                          # (3,)

    _raw_basis_for_target = raw_rgb_basis if raw_rgb_basis is not None else target_rgb_basis
    target_transform_matrix = None

    feedback_candidate_model = load_feedback_candidate_model_for_args(args)
    if feedback_candidate_model is not None:
        print(
            f"  Feedback candidate model: {feedback_candidate_model.get('known_pass_rgb', 0)} known-pass RGB keys "
            f"from {feedback_candidate_model.get('observation_count', 0)} verifier observations",
            flush=True,
        )

    state = {
        "axis":                axis,
        "target_rgb_basis":    target_rgb_basis,
        "raw_rgb_basis":       _raw_basis_for_target,
        "target_transform_matrix": target_transform_matrix,
        "family_bases":        family_bases,
        "family_capture_xyz":  {fk: v[0] for fk, v in family_capture_sets.items()},
        "family_capture_rgbw": {fk: v[1] for fk, v in family_capture_sets.items()},
        "white_xyz_ref":       white_xyz_ref,
        "reference_white":     reference_white,
        "y_scale":             float(y_scale),
        "args":                args,
        "worker_count":        n_workers,
        "feedback_candidate_model": feedback_candidate_model,
    }
    jobs = [
        (r_index, float(axis[r_index]), build_comparison)
        for r_index in range(grid_size)
    ]

    cube = np.zeros((grid_size, grid_size, grid_size, 4), dtype=np.float32)
    comparison_slices: list[list[dict]] = [[] for _ in range(grid_size)]
    all_used: set[int] = set()
    agg_fam_counts: dict[str, int] = {}

    label       = "coarse" if build_comparison else "full"
    tiebreak    = getattr(args, "delta_e_tiebreak", 2.0)
    chroma_gate = getattr(args, "chroma_gate", 15.0)
    n_fam       = len(family_capture_sets) or len(family_bases)
    print(
        f"  Building {grid_size}\u00b3 {label} cube with {n_workers} workers "
        f"({n_fam} families, y_scale={y_scale:.3f}, tiebreak={tiebreak}, "
        f"chroma_gate={chroma_gate}) \u2026",
        flush=True,
    )

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=n_workers,
        initializer=_worker_init,
        initargs=(state,),
    ) as executor:
        futures = {executor.submit(_solve_r_slice, job): job[0] for job in jobs}
        completed = 0
        try:
            for future in concurrent.futures.as_completed(futures):
                r_index, slice_cube, slice_rows, slice_fam_counts = future.result()
                cube[r_index] = slice_cube
                comparison_slices[r_index] = slice_rows
                for fk, cnt in slice_fam_counts.items():
                    agg_fam_counts[fk] = agg_fam_counts.get(fk, 0) + cnt
                completed += 1
                pct = 100.0 * completed / grid_size
                print(
                    f"\r  [{completed:>{len(str(grid_size))}}/{grid_size}]  {pct:5.1f}%",
                    end="", flush=True,
                )
                if progress_callback is not None:
                    progress_callback(completed, grid_size)
        except Exception:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
    print(flush=True)

    # Family-usage diagnostic
    total_nodes = grid_size ** 3
    print(f"  Family usage ({grid_size}\u00b3 = {total_nodes} nodes):", flush=True)
    for fk, _m, _w in _FAMILY_DEFS:
        cnt = agg_fam_counts.get(fk, 0)
        if cnt:
            print(f"    {fk:>6}: {cnt:>8} nodes  ({100.0*cnt/total_nodes:5.1f}%)", flush=True)
    unresolved = total_nodes - sum(agg_fam_counts.values())
    if unresolved:
        print(f"  {'unresolved':>6}: {unresolved:>8} nodes  (zero/black)", flush=True)

    comparison_rows = [row for sl in comparison_slices for row in sl]
    return cube, comparison_rows, all_used


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def write_comparison_csv(rows: list[dict], output_path: Path) -> None:
    if not rows:
        return
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _csv_float(row: dict, *names: str, default: float = float("nan")) -> float:
    for name in names:
        if name in row and str(row.get(name, "")).strip() != "":
            try:
                return float(row.get(name))
            except Exception:
                pass
    return default


def _csv_int(row: dict, *names: str, default: int = 0) -> int:
    val = _csv_float(row, *names, default=float(default))
    if not np.isfinite(val):
        return default
    return int(round(val))


def _csv_bool(row: dict, *names: str) -> bool:
    for name in names:
        if name in row:
            s = str(row.get(name, "")).strip().lower()
            return s in {"1", "true", "yes", "ok", "pass", "✓", "y"}
    return False


def _read_csv_rows_safe(path: Path) -> list[dict]:
    try:
        with path.open("r", newline="", encoding="utf-8", errors="replace") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []



# ---------------------------------------------------------------------------
# Display-scoped verifier feedback bank (diagnostic pass/fail memory)
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_profile_id(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        text = "default_display"
    # Path-like profile arguments use the filename stem as the display id.
    text = Path(text).stem if any(sep in text for sep in ("/", "\\")) else text
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in text)
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or "default_display"


def _json_sanitize(value):
    """Recursively convert numpy scalars and NaN/Inf to JSON-safe values."""
    if isinstance(value, dict):
        return {str(k): _json_sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_sanitize(v) for v in value]
    if isinstance(value, set):
        return sorted(_json_sanitize(v) for v in value)
    if isinstance(value, np.generic):
        return _json_sanitize(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    return str(value)


def _xyY_to_XYZ(x: float, y: float, Y: float) -> tuple[float | None, float | None, float | None]:
    if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(Y)) or y <= 1e-12 or Y < 0.0:
        return None, None, None
    X = (x * Y) / y
    Z = ((1.0 - x - y) * Y) / y
    return float(X), float(Y), float(Z)


def _profile_path_from_args(args: argparse.Namespace) -> tuple[Path, str]:
    config_dir = Path(getattr(args, "config_dir", DEFAULT_CONFIG_DIR))
    profile_arg = str(getattr(args, "display_profile", "default_display") or "default_display")
    explicit_id = str(getattr(args, "display_id", "") or "").strip()

    maybe_path = Path(profile_arg)
    if maybe_path.suffix.lower() == ".json" or maybe_path.is_absolute() or any(sep in profile_arg for sep in ("/", "\\")):
        profile_path = maybe_path if maybe_path.is_absolute() else (config_dir / "profiles" / maybe_path)
        display_id = _safe_profile_id(explicit_id or profile_path.stem)
    else:
        display_id = _safe_profile_id(explicit_id or profile_arg)
        profile_path = config_dir / "profiles" / f"{display_id}.json"
    return profile_path, display_id


def load_or_create_display_profile(
    args: argparse.Namespace,
    reference_white: ReferenceWhite,
) -> tuple[dict, Path]:
    """Load or create the active display profile.

    The profile is intentionally lightweight in this first pass.  Its main job
    is to scope verifier feedback banks so pass/fail memory from one physical
    display does not silently affect another display later.
    """
    profile_path, display_id = _profile_path_from_args(args)
    now = _utc_now_iso()
    profile: dict = {}
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            profile = {}

    if not profile:
        profile = {
            "schema_version": 1,
            "display_id": display_id,
            "display_name": display_id,
            "created_at": now,
            "notes": "",
        }

    profile["schema_version"] = int(profile.get("schema_version", 1) or 1)
    profile["display_id"] = _safe_profile_id(str(profile.get("display_id") or display_id))
    profile["last_used_at"] = now
    profile.setdefault("display_name", profile["display_id"])
    profile["reference_white"] = {
        "x": float(reference_white.x),
        "y": float(reference_white.y),
        "Y": float(reference_white.Y),
    }
    profile["builder_profile"] = {
        "sample_scale": float(getattr(args, "sample_scale", 65535.0)),
        "target_white_balance_mode": str(getattr(args, "target_white_balance_mode", "reference-white")),
    }
    profile["paths"] = {
        "input_dir": str(getattr(args, "input_dir", "")),
        "config_dir": str(getattr(args, "config_dir", DEFAULT_CONFIG_DIR)),
    }

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(_json_sanitize(profile), indent=2), encoding="utf-8")
    setattr(args, "display_id", profile["display_id"])
    setattr(args, "display_profile_path", str(profile_path))
    return profile, profile_path


def _feedback_bank_paths(args: argparse.Namespace, display_id: str) -> tuple[Path, Path, Path, Path]:
    config_dir = Path(getattr(args, "config_dir", DEFAULT_CONFIG_DIR))
    bank_arg = str(getattr(args, "feedback_bank", "auto") or "auto")
    dict_dir = config_dir / "dictionaries" / _safe_profile_id(display_id)
    if bank_arg.lower() != "auto":
        bank_path = Path(bank_arg)
        if not bank_path.is_absolute():
            bank_path = dict_dir / bank_path
    else:
        bank_path = dict_dir / "verifier_feedback_bank.json"
    return bank_path, dict_dir / "verifier_pass_bank.json", dict_dir / "verifier_fail_bank.json", dict_dir / "sessions"


def _verifier_row_key(row: dict) -> tuple[int, int, int, str]:
    r = _csv_int(row, "verifier_r16", "input_r", "r16")
    g = _csv_int(row, "verifier_g16", "input_g", "g16")
    b = _csv_int(row, "verifier_b16", "input_b", "b16")
    return r, g, b, f"{r},{g},{b}"


def _parse_target_match_row(row: dict, source_file: str) -> dict:
    r, g, b, key = _verifier_row_key(row)
    return {
        "source_file": source_file,
        "patch": str(row.get("patch", "")).strip(),
        "rgb_key": key,
        "input_rgb": [r, g, b],
        "rank": _csv_int(row, "rank", default=999999),
        "score": _csv_float(row, "score", default=float("inf")),
        "xy_dist": _csv_float(row, "xy_dist", default=float("inf")),
        "Y_log_ratio": _csv_float(row, "Y_log_ratio", "y_log_ratio", default=float("inf")),
        "cap_x": _csv_float(row, "cap_x"),
        "cap_y": _csv_float(row, "cap_y"),
        "cap_Y": _csv_float(row, "cap_Y"),
        "cap_r16": _csv_int(row, "cap_r16"),
        "cap_g16": _csv_int(row, "cap_g16"),
        "cap_b16": _csv_int(row, "cap_b16"),
        "cap_w16": _csv_int(row, "cap_w16"),
        "cap_name": str(row.get("cap_name", "")),
        "cap_source_file": str(row.get("cap_source_file", "")),
    }


def _find_capture_file(cap_source_file: str, search_dirs: list[Path]) -> Path | None:
    name = str(cap_source_file or "").strip()
    if not name:
        return None
    direct = Path(name)
    if direct.exists():
        return direct
    for base in search_dirs:
        try:
            base = Path(base)
            if not base.exists():
                continue
            candidate = base / name
            if candidate.exists():
                return candidate
        except Exception:
            continue
    # Last resort: one recursive lookup in each plausible root.
    for base in search_dirs:
        try:
            base = Path(base)
            if not base.exists() or not base.is_dir():
                continue
            hits = list(base.rglob(name))
            if hits:
                return hits[0]
        except Exception:
            continue
    return None


def _resolve_best_capture_xyz(best: dict | None, search_dirs: list[Path]) -> dict | None:
    if not best:
        return None
    out = dict(best)
    cap_x = float(out.get("cap_x", float("nan")))
    cap_y = float(out.get("cap_y", float("nan")))
    cap_Y = float(out.get("cap_Y", float("nan")))
    X, Y, Z = _xyY_to_XYZ(cap_x, cap_y, cap_Y)
    out["cap_X"] = X
    out["cap_Z"] = Z
    out["XYZ_source"] = "reconstructed_from_xyY" if X is not None else "unavailable"

    cap_file = _find_capture_file(str(out.get("cap_source_file", "")), search_dirs)
    if cap_file is None:
        return out

    cap_name = str(out.get("cap_name", ""))
    want = (
        int(out.get("cap_r16", 0)),
        int(out.get("cap_g16", 0)),
        int(out.get("cap_b16", 0)),
        int(out.get("cap_w16", 0)),
    )
    for row in _read_csv_rows_safe(cap_file):
        drives = (
            _csv_int(row, "r16"),
            _csv_int(row, "g16"),
            _csv_int(row, "b16"),
            _csv_int(row, "w16"),
        )
        if drives != want:
            continue
        if cap_name and str(row.get("name", "")) != cap_name:
            # Same drive tuple may be duplicated; keep looking for exact name.
            continue
        Xr = _csv_float(row, "X")
        Yr = _csv_float(row, "Y")
        Zr = _csv_float(row, "Z")
        if np.isfinite(Xr) and np.isfinite(Yr) and np.isfinite(Zr):
            out["cap_X"] = float(Xr)
            out["cap_Y"] = float(Yr)
            out["cap_Z"] = float(Zr)
            out["XYZ_source"] = "patch_capture_file"
            out["resolved_capture_path"] = str(cap_file)
            return out
    return out


def _channel_direction_hints_from_rows(rows: list[dict], best_capture: dict | None = None) -> dict:
    hints = {"r": "hold", "g": "hold", "b": "hold", "w": "hold"}
    if best_capture is not None and rows:
        # Compare best capture to the most recent LUT output for an actionable
        # move direction.  This is intentionally diagnostic-only in this pass.
        latest = rows[-1]
        for ch, lut_name, cap_name in (
            ("r", "lut_r", "cap_r16"),
            ("g", "lut_g", "cap_g16"),
            ("b", "lut_b", "cap_b16"),
            ("w", "lut_w", "cap_w16"),
        ):
            cur = float(latest.get(lut_name, 0.0) or 0.0)
            tgt = float(best_capture.get(cap_name, cur) or cur)
            if tgt > cur + 512.0:
                hints[ch] = "raise"
            elif cur > tgt + 512.0:
                hints[ch] = "lower"
        return hints

    # Fallback to aggregate string flags when no best capture is available.
    counts: dict[str, dict[str, int]] = {c: {"raise": 0, "lower": 0} for c in hints}
    for row in rows:
        for token in str(row.get("channel_direction_hints", "")).split("|"):
            token = token.strip()
            if token.startswith("raise_") and token[-1:] in counts:
                counts[token[-1]]["raise"] += 1
            elif token.startswith("lower_") and token[-1:] in counts:
                counts[token[-1]]["lower"] += 1
    for ch, c in counts.items():
        if c["raise"] > c["lower"]:
            hints[ch] = "raise"
        elif c["lower"] > c["raise"]:
            hints[ch] = "lower"
    return hints


def _parse_verifier_feedback_rows(verifier_dir: Path, dE_threshold: float) -> tuple[list[dict], dict[str, list[dict]]]:
    verifier_rows: list[dict] = []
    target_match_by_key: dict[str, list[dict]] = {}

    for csv_path in sorted(Path(verifier_dir).glob("*.csv")):
        rows = _read_csv_rows_safe(csv_path)
        if not rows:
            continue
        headers = set(rows[0].keys())
        lower_name = csv_path.name.lower()
        is_target_match = (
            "target_match" in lower_name
            or {"rank", "cap_r16", "cap_g16", "cap_b16", "cap_w16"}.issubset(headers)
        )
        if is_target_match:
            for row in rows:
                tm = _parse_target_match_row(row, csv_path.name)
                target_match_by_key.setdefault(tm["rgb_key"], []).append(tm)

        looks_verifier = (
            "verifier_dE" in headers
            or "dE" in headers
            or "verifier_de" in {h.lower() for h in headers}
            or {"lut_r16", "lut_g16", "lut_b16", "lut_w16"}.issubset(headers)
        )
        if not looks_verifier:
            continue

        for row in rows:
            r, g, b, key = _verifier_row_key(row)
            lr = _csv_int(row, "lut_r16", "out_r", "lut_r")
            lg = _csv_int(row, "lut_g16", "out_g", "lut_g")
            lb = _csv_int(row, "lut_b16", "out_b", "lut_b")
            lw = _csv_int(row, "lut_w16", "out_w", "lut_w")
            de = _csv_float(row, "verifier_dE", "verifier_de", "dE", "deltaE", default=float("nan"))
            has_ok = ("verifier_ok" in row or "ok" in row or "pass" in row)
            ok_flag = _csv_bool(row, "verifier_ok", "ok", "pass")
            if not np.isfinite(de) and not has_ok:
                continue
            status = "pass" if ((np.isfinite(de) and de <= dE_threshold) and (ok_flag or not has_ok)) else "fail"
            if has_ok and not ok_flag:
                status = "fail"

            inp = np.array([r, g, b], dtype=np.float64)
            out = np.array([lr, lg, lb, lw], dtype=np.float64)
            active = inp > 0
            common = float(np.min(inp))
            inp_max = float(np.max(inp)) if np.max(inp) > 0 else 1.0
            out_rgb_max = float(np.max(out[:3]))
            mask = int(active[0]) | (int(active[1]) << 1) | (int(active[2]) << 2)

            exp_x = _csv_float(row, "exp_x", "target_x")
            exp_y = _csv_float(row, "exp_y", "target_y")
            meas_x = _csv_float(row, "verifier_meas_x", "measured_x", "meas_x")
            meas_y = _csv_float(row, "verifier_meas_y", "measured_y", "meas_y")
            meas_Y = _csv_float(row, "verifier_meas_Y", "measured_Y", "meas_Y")
            xy_dx = float(exp_x - meas_x) if np.isfinite(exp_x) and np.isfinite(meas_x) else float("nan")
            xy_dy = float(exp_y - meas_y) if np.isfinite(exp_y) and np.isfinite(meas_y) else float("nan")
            flags: list[str] = []
            if status == "fail":
                flags.append("dE_fail")
            if np.isfinite(xy_dx) and abs(xy_dx) > 0.0025:
                flags.append("xy_raise_x" if xy_dx > 0 else "xy_lower_x")
            if np.isfinite(xy_dy) and abs(xy_dy) > 0.0025:
                flags.append("xy_raise_y" if xy_dy > 0 else "xy_lower_y")

            channel_moves: list[str] = []
            for _name, _inp, _out in (("r", r, lr), ("g", g, lg), ("b", b, lb)):
                if _out > _inp * 1.05 + 512.0:
                    channel_moves.append(f"lower_{_name}")
                elif _inp > 2048.0 and _out < max(128.0, _inp * 0.035):
                    channel_moves.append(f"raise_{_name}")
            flags.extend(channel_moves)

            if mask == 7 and common > 0:
                if lw < 0.72 * common:
                    flags.append("under_w")
                if lw > 1.25 * common + 512.0:
                    flags.append("over_w")
                if out_rgb_max > 1.25 * inp_max:
                    flags.append("overdrive")
            if mask in (3, 5, 6):
                inactive_rgb = max(float(out[i]) for i in range(3) if not active[i])
                if inactive_rgb > 512.0:
                    flags.append("dual_inactive_rgb_leak")
                if lw > 512.0:
                    flags.append("dual_w_leak")

            verifier_rows.append({
                "source_file": csv_path.name,
                "patch": str(row.get("patch", "")).strip(),
                "rgb_key": key,
                "input_rgb": [r, g, b],
                "input_r": r, "input_g": g, "input_b": b,
                "lut_rgbw": [lr, lg, lb, lw],
                "lut_r": lr, "lut_g": lg, "lut_b": lb, "lut_w": lw,
                "target": {"x": exp_x, "y": exp_y, "Y": _csv_float(row, "exp_Y", "target_Y")},
                "measured": {"x": meas_x, "y": meas_y, "Y": meas_Y},
                "xy_dx": xy_dx,
                "xy_dy": xy_dy,
                "verifier_dE": de,
                "status": status,
                "ok": status == "pass",
                "input_mask": mask,
                "input_common_min": common,
                "input_max": inp_max,
                "out_rgb_max": out_rgb_max,
                "out_w_to_common": float(lw / max(common, 1.0)) if common > 0 else 0.0,
                "failure_flags": flags,
                "channel_direction_hints": "|".join(channel_moves),
                "selected_family": str(row.get("selected_family", row.get("family", ""))),
                "selected_route": str(row.get("selected_route", "")),
            })
    return verifier_rows, target_match_by_key


def _finite_or_none(value) -> float | None:
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def _feedback_result_id(row: dict) -> str:
    """Stable id for one unique verifier observation.

    Session id is intentionally excluded so re-importing the same verifier CSV
    does not inflate pass/fail counts.  Different LUT outputs, measured xyY, dE,
    route/family, or patch/source rows become distinct observations for the same
    RGB key.  This is the key difference from the old bank, which collapsed each
    RGB key down to one latest/best capture record.
    """
    payload = {
        "source_file": row.get("source_file", ""),
        "patch": row.get("patch", ""),
        "rgb_key": row.get("rgb_key", ""),
        "input_rgb": row.get("input_rgb", []),
        "lut_rgbw": row.get("lut_rgbw", []),
        "target": row.get("target", {}),
        "measured": row.get("measured", {}),
        "dE": None if _finite_or_none(row.get("verifier_dE")) is None else round(float(row.get("verifier_dE")), 6),
        "status": row.get("status", ""),
        "selected_family": row.get("selected_family", ""),
        "selected_route": row.get("selected_route", ""),
    }
    blob = json.dumps(_json_sanitize(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8", errors="replace")).hexdigest()[:20]


def _feedback_target_xyz(target: dict) -> list[float | None] | None:
    x = _finite_or_none(target.get("x"))
    y = _finite_or_none(target.get("y"))
    Y = _finite_or_none(target.get("Y"))
    if x is None or y is None or Y is None:
        return None
    X, Y2, Z = _xyY_to_XYZ(x, y, Y)
    return [X, Y2, Z] if X is not None else None


def _feedback_measured_xyz(measured: dict) -> list[float | None] | None:
    x = _finite_or_none(measured.get("x"))
    y = _finite_or_none(measured.get("y"))
    Y = _finite_or_none(measured.get("Y"))
    if x is None or y is None or Y is None:
        return None
    X, Y2, Z = _xyY_to_XYZ(x, y, Y)
    return [X, Y2, Z] if X is not None else None


def _channel_direction_hints_for_observation(row: dict, best_capture: dict | None = None) -> dict:
    """Per-observation channel correction hints.

    Do not aggregate here: a single RGB key can have one failed observation that
    overshoots and a later failed observation that undershoots.  Keeping both is
    what lets future correction logic steer toward the middle instead of losing
    contradictory evidence.
    """
    hints = {"r": "hold", "g": "hold", "b": "hold", "w": "hold"}
    if best_capture is not None:
        for ch, lut_name, cap_name in (
            ("r", "lut_r", "cap_r16"),
            ("g", "lut_g", "cap_g16"),
            ("b", "lut_b", "cap_b16"),
            ("w", "lut_w", "cap_w16"),
        ):
            cur = float(row.get(lut_name, 0.0) or 0.0)
            tgt = float(best_capture.get(cap_name, cur) or cur)
            if tgt > cur + 512.0:
                hints[ch] = "raise"
            elif cur > tgt + 512.0:
                hints[ch] = "lower"
        return hints

    # Fall back to row-local parsed flags.
    for token in str(row.get("channel_direction_hints", "")).split("|"):
        token = token.strip()
        if token.startswith("raise_") and token[-1:] in hints:
            hints[token[-1]] = "raise"
        elif token.startswith("lower_") and token[-1:] in hints:
            hints[token[-1]] = "lower"
    return hints


def _feedback_capture_delta(row: dict, best_capture: dict | None) -> dict | None:
    if best_capture is None:
        return None
    out = {}
    for ch, lut_name, cap_name in (
        ("r", "lut_r", "cap_r16"),
        ("g", "lut_g", "cap_g16"),
        ("b", "lut_b", "cap_b16"),
        ("w", "lut_w", "cap_w16"),
    ):
        cur = _finite_or_none(row.get(lut_name))
        tgt = _finite_or_none(best_capture.get(cap_name))
        out[ch] = None if cur is None or tgt is None else float(tgt - cur)
    return out


def _resolve_target_match_candidates(
    matches: list[dict],
    search_dirs: list[Path],
    *,
    max_candidates: int = 12,
) -> list[dict]:
    matches_sorted = sorted(
        matches,
        key=lambda m: (
            0 if (m["xy_dist"] <= 0.012 and abs(m["Y_log_ratio"]) <= 0.50) else 1,
            m["xy_dist"],
            abs(m["Y_log_ratio"]),
            m["rank"],
        ),
    )
    out: list[dict] = []
    seen: set[tuple[int, int, int, int, str]] = set()
    for m in matches_sorted:
        sig = (
            int(m.get("cap_r16", 0) or 0),
            int(m.get("cap_g16", 0) or 0),
            int(m.get("cap_b16", 0) or 0),
            int(m.get("cap_w16", 0) or 0),
            str(m.get("cap_name", "")),
        )
        if sig in seen:
            continue
        seen.add(sig)
        resolved = _resolve_best_capture_xyz(m, search_dirs)
        if resolved is not None:
            out.append(resolved)
        if len(out) >= max_candidates:
            break
    return out


def _legacy_feedback_observations(prev: dict, now: str) -> list[dict]:
    """Best-effort migration from schema v1 entries into schema v2 history."""
    if not isinstance(prev, dict):
        return []
    obs = prev.get("observations")
    if isinstance(obs, list):
        return [o for o in obs if isinstance(o, dict)]

    latest = prev.get("latest_result")
    if not isinstance(latest, dict):
        return []
    rgb_key = str(prev.get("rgb_key", ""))
    payload = {
        "observation_id": "legacy_" + hashlib.sha1(json.dumps(_json_sanitize(latest), sort_keys=True).encode("utf-8", errors="replace")).hexdigest()[:16],
        "schema_source": "legacy_v1_latest_result",
        "first_seen_at": prev.get("updated_at") or now,
        "last_seen_at": prev.get("updated_at") or now,
        "seen_count": 1,
        "sessions": [latest.get("session_id", "legacy")],
        "rgb_key": rgb_key,
        "input_rgb": prev.get("input_rgb"),
        "target": prev.get("target"),
        "target_XYZ": _feedback_target_xyz(prev.get("target", {})),
        "status": latest.get("status"),
        "dE": latest.get("dE"),
        "lut_rgbw": latest.get("lut_rgbw"),
        "measured_xyY": latest.get("verifier_xyY"),
        "measured_XYZ": None,
        "selected_family": latest.get("selected_family", ""),
        "selected_route": latest.get("selected_route", ""),
        "source_file": latest.get("source_file", ""),
        "patch": latest.get("patch", ""),
        "failure_flags": list(prev.get("fail_stats", {}).get("failure_flags", [])) if isinstance(prev.get("fail_stats"), dict) else [],
        "channel_direction_hints": prev.get("fail_stats", {}).get("channel_direction_hints", {}) if isinstance(prev.get("fail_stats"), dict) else {},
        "best_capture": prev.get("best_capture"),
        "capture_delta_rgbw": None,
    }
    return [payload]


def _merge_feedback_observation(existing: dict[str, dict], observation: dict, session_id: str, now: str) -> None:
    oid = str(observation.get("observation_id", ""))
    if not oid:
        return
    prev = existing.get(oid)
    if prev is None:
        observation.setdefault("first_seen_at", now)
        observation.setdefault("last_seen_at", now)
        observation.setdefault("seen_count", 1)
        sessions = observation.get("sessions")
        if not isinstance(sessions, list):
            sessions = []
        if session_id not in sessions:
            sessions.append(session_id)
        observation["sessions"] = sessions[-16:]
        existing[oid] = observation
        return

    prev["last_seen_at"] = now
    prev["seen_count"] = int(prev.get("seen_count", 1) or 1) + 1
    sessions = list(prev.get("sessions", [])) if isinstance(prev.get("sessions"), list) else []
    if session_id not in sessions:
        sessions.append(session_id)
    prev["sessions"] = sessions[-16:]
    # Keep the richer/latest copy of fields, but preserve first_seen_at/seen_count.
    first_seen = prev.get("first_seen_at")
    seen_count = prev.get("seen_count")
    observation.update({"first_seen_at": first_seen, "last_seen_at": now, "seen_count": seen_count, "sessions": prev["sessions"]})
    existing[oid] = observation


def _observation_sort_key(obs: dict) -> tuple[str, str, str]:
    return (
        str(obs.get("first_seen_at", "")),
        str(obs.get("source_file", "")),
        str(obs.get("observation_id", "")),
    )


def _build_feedback_entry_stats(observations: list[dict]) -> tuple[dict, dict, dict]:
    passes = [o for o in observations if o.get("status") == "pass"]
    fails = [o for o in observations if o.get("status") == "fail"]

    def de_of(o: dict, default: float) -> float:
        d = _finite_or_none(o.get("dE"))
        return default if d is None else d

    best_pass = min(passes, key=lambda o: de_of(o, float("inf"))) if passes else None
    worst_fail = max(fails, key=lambda o: de_of(o, -1.0)) if fails else None

    pass_stats = {
        "pass_count": len(passes),
        "pass_seen_count": int(sum(int(o.get("seen_count", 1) or 1) for o in passes)),
        "pass_observation_ids": [o.get("observation_id") for o in passes],
        "best_dE": best_pass.get("dE") if best_pass else None,
        "best_rgbw": best_pass.get("lut_rgbw") if best_pass else None,
        "best_measured_xyY": best_pass.get("measured_xyY") if best_pass else None,
        "best_measured_XYZ": best_pass.get("measured_XYZ") if best_pass else None,
        "best_source": "verifier_lut_output" if best_pass else None,
        "best_observation_id": best_pass.get("observation_id") if best_pass else None,
        "best_session_id": (best_pass.get("sessions") or [None])[-1] if best_pass else None,
    }

    recent_fail_de = [de_of(o, float("nan")) for o in fails if _finite_or_none(o.get("dE")) is not None]
    all_failure_flags = sorted({flag for o in fails for flag in o.get("failure_flags", [])})
    direction_counts: dict[str, dict[str, int]] = {c: {"raise": 0, "lower": 0, "hold": 0} for c in ("r", "g", "b", "w")}
    direction_examples: dict[str, dict[str, list[str]]] = {c: {"raise": [], "lower": [], "hold": []} for c in ("r", "g", "b", "w")}
    for o in fails:
        hints = o.get("channel_direction_hints", {})
        if not isinstance(hints, dict):
            continue
        oid = str(o.get("observation_id", ""))
        for ch in direction_counts:
            mv = str(hints.get(ch, "hold"))
            if mv not in direction_counts[ch]:
                mv = "hold"
            direction_counts[ch][mv] += 1
            if len(direction_examples[ch][mv]) < 6:
                direction_examples[ch][mv].append(oid)

    fail_stats = {
        "fail_count": len(fails),
        "fail_seen_count": int(sum(int(o.get("seen_count", 1) or 1) for o in fails)),
        "fail_observation_ids": [o.get("observation_id") for o in fails],
        "worst_dE": worst_fail.get("dE") if worst_fail else None,
        "worst_observation_id": worst_fail.get("observation_id") if worst_fail else None,
        "recent_dE": recent_fail_de[-24:],
        "failure_flags": all_failure_flags,
        "channel_direction_counts": direction_counts,
        "channel_direction_examples": direction_examples,
        "has_contradictory_channel_hints": {
            ch: direction_counts[ch]["raise"] > 0 and direction_counts[ch]["lower"] > 0
            for ch in direction_counts
        },
        "xy_error_vectors": [
            {
                "observation_id": o.get("observation_id"),
                "dx": o.get("xy_dx"),
                "dy": o.get("xy_dy"),
                "dE": o.get("dE"),
            }
            for o in fails[-24:]
        ],
    }

    latest = observations[-1] if observations else {}
    latest_result = {
        "status": latest.get("status"),
        "dE": latest.get("dE"),
        "verifier_xyY": latest.get("measured_xyY"),
        "lut_rgbw": latest.get("lut_rgbw"),
        "selected_family": latest.get("selected_family", ""),
        "selected_route": latest.get("selected_route", ""),
        "session_id": (latest.get("sessions") or [None])[-1] if latest else None,
        "source_file": latest.get("source_file", ""),
        "patch": latest.get("patch", ""),
        "observation_id": latest.get("observation_id"),
    }
    return pass_stats, fail_stats, latest_result


def write_verifier_feedback_bank(
    verifier_dir: Path,
    output_dir: Path,
    args: argparse.Namespace,
    display_profile: dict,
    *,
    dE_threshold: float = 2.5,
) -> dict | None:
    """Write/merge display-scoped pass/fail verifier feedback dictionaries.

    Schema v2 keeps every unique verifier observation per RGB key instead of
    collapsing the key into one latest/best capture.  That is required for the
    future correction pass because the same RGB key may have both overshooting
    and undershooting failures across sessions/builds.
    """
    verifier_dir = Path(verifier_dir)
    if not verifier_dir.exists() or not verifier_dir.is_dir():
        return None

    display_id = _safe_profile_id(display_profile.get("display_id", getattr(args, "display_id", "default_display")))
    bank_path, pass_path, fail_path, sessions_dir = _feedback_bank_paths(args, display_id)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    bank_path.parent.mkdir(parents=True, exist_ok=True)

    verifier_rows, target_match_by_key = _parse_verifier_feedback_rows(verifier_dir, dE_threshold)
    if not verifier_rows:
        return None

    search_dirs: list[Path] = []
    for p in (getattr(args, "input_dir", None), verifier_dir, verifier_dir.parent, output_dir):
        if p:
            try:
                search_dirs.append(Path(p))
            except Exception:
                pass

    # Keep multiple target-match candidates per RGB key.  The first entry is
    # still the best capture, but the full list gives the later correction code
    # alternate anchors across low/mid/high Y instead of one collapsed target.
    target_candidates_by_key: dict[str, list[dict]] = {
        key: _resolve_target_match_candidates(matches, search_dirs, max_candidates=12)
        for key, matches in target_match_by_key.items()
    }

    by_key: dict[str, list[dict]] = {}
    for row in verifier_rows:
        by_key.setdefault(row["rgb_key"], []).append(row)

    now = _utc_now_iso()
    session_id = now.replace("+00:00", "Z").replace(":", "").replace("-", "")
    session_summary = {
        "session_id": session_id,
        "created_at": now,
        "display_id": display_id,
        "verifier_dir": str(verifier_dir),
        "dE_threshold": dE_threshold,
        "rows": len(verifier_rows),
        "pass_rows": sum(1 for r in verifier_rows if r["status"] == "pass"),
        "fail_rows": sum(1 for r in verifier_rows if r["status"] == "fail"),
        "unique_rgb": len(by_key),
    }

    existing: dict = {}
    if bank_path.exists():
        try:
            existing = json.loads(bank_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    existing_entries = existing.get("entries", {}) if isinstance(existing, dict) else {}
    bank = {
        "schema_version": 2,
        "diagnostic_only": True,
        "display_id": display_id,
        "display_profile": display_profile,
        "created_at": existing.get("created_at", now) if isinstance(existing, dict) else now,
        "updated_at": now,
        "feedback_mode": str(getattr(args, "feedback_mode", "diagnostic")),
        "dE_threshold": dE_threshold,
        "sessions": list(existing.get("sessions", [])) if isinstance(existing, dict) else [],
        "entries": {},
    }
    bank["sessions"].append(session_summary)
    bank["sessions"] = bank["sessions"][-48:]

    detail_rows: list[dict] = []
    session_entries: dict[str, dict] = {}

    # Start by migrating/preserving existing entries, including schema v1.
    for key, prev in existing_entries.items() if isinstance(existing_entries, dict) else []:
        if not isinstance(prev, dict):
            continue
        observations = _legacy_feedback_observations(prev, now)
        observations = sorted(observations, key=_observation_sort_key)
        pass_stats, fail_stats, latest_result = _build_feedback_entry_stats(observations)
        bank["entries"][key] = {
            **{k: v for k, v in prev.items() if k not in ("pass_stats", "fail_stats", "latest_result", "observations")},
            "schema_version": 2,
            "rgb_key": key,
            "display_id": display_id,
            "observations": observations,
            "observation_count": len(observations),
            "pass_stats": pass_stats,
            "fail_stats": fail_stats,
            "latest_result": latest_result,
        }

    for key, rows_for_key in sorted(by_key.items()):
        input_rgb = rows_for_key[-1]["input_rgb"]
        prev_entry = bank["entries"].get(key, {})
        existing_obs_list = prev_entry.get("observations", []) if isinstance(prev_entry, dict) else []
        obs_by_id: dict[str, dict] = {
            str(o.get("observation_id")): dict(o)
            for o in existing_obs_list
            if isinstance(o, dict) and o.get("observation_id")
        }

        target_candidates = target_candidates_by_key.get(key, [])
        best_capture = target_candidates[0] if target_candidates else None
        session_obs_ids: list[str] = []

        for row in rows_for_key:
            oid = _feedback_result_id(row)
            hints = _channel_direction_hints_for_observation(row, best_capture)
            mx, my, mY = row["measured"].get("x"), row["measured"].get("y"), row["measured"].get("Y")
            observation = {
                "schema_version": 2,
                "observation_id": oid,
                "first_seen_at": now,
                "last_seen_at": now,
                "seen_count": 1,
                "sessions": [session_id],
                "rgb_key": key,
                "display_id": display_id,
                "input_rgb": row["input_rgb"],
                "input_mask": row.get("input_mask"),
                "input_common_min": row.get("input_common_min"),
                "input_max": row.get("input_max"),
                "target": row["target"],
                "target_XYZ": _feedback_target_xyz(row["target"]),
                "measured_xyY": [mx, my, mY],
                "measured_XYZ": _feedback_measured_xyz(row["measured"]),
                "xy_dx": row.get("xy_dx"),
                "xy_dy": row.get("xy_dy"),
                "dE": row.get("verifier_dE"),
                "status": row.get("status"),
                "ok": bool(row.get("ok")),
                "lut_rgbw": row.get("lut_rgbw"),
                "lut_r": row.get("lut_r"),
                "lut_g": row.get("lut_g"),
                "lut_b": row.get("lut_b"),
                "lut_w": row.get("lut_w"),
                "out_rgb_max": row.get("out_rgb_max"),
                "out_w_to_common": row.get("out_w_to_common"),
                "selected_family": row.get("selected_family", ""),
                "selected_route": row.get("selected_route", ""),
                "source_file": row.get("source_file", ""),
                "patch": row.get("patch", ""),
                "failure_flags": list(row.get("failure_flags", [])),
                "channel_direction_hints": hints,
                "capture_delta_rgbw": _feedback_capture_delta(row, best_capture),
                "best_capture": best_capture,
                "target_match_candidates": target_candidates,
            }
            _merge_feedback_observation(obs_by_id, observation, session_id, now)
            session_obs_ids.append(oid)

        observations = sorted(obs_by_id.values(), key=_observation_sort_key)
        pass_stats, fail_stats, latest_result = _build_feedback_entry_stats(observations)
        entry = {
            "schema_version": 2,
            "rgb_key": key,
            "display_id": display_id,
            "input_rgb": input_rgb,
            "target": rows_for_key[-1]["target"],
            "target_XYZ": _feedback_target_xyz(rows_for_key[-1]["target"]),
            "latest_result": latest_result,
            "pass_stats": pass_stats,
            "fail_stats": fail_stats,
            "best_capture": best_capture,
            "target_match_candidates": target_candidates,
            "observations": observations,
            "observation_count": len(observations),
        }
        bank["entries"][key] = entry
        session_entries[key] = {
            **entry,
            "observations": [obs_by_id[oid] for oid in session_obs_ids if oid in obs_by_id],
            "session_observation_ids": session_obs_ids,
        }

        for obs in session_entries[key]["observations"]:
            best = obs.get("best_capture") or {}
            hints = obs.get("channel_direction_hints") or {}
            detail_rows.append({
                "rgb_key": key,
                "observation_id": obs.get("observation_id"),
                "input_r": input_rgb[0],
                "input_g": input_rgb[1],
                "input_b": input_rgb[2],
                "status": obs.get("status"),
                "dE": obs.get("dE"),
                "lut_rgbw": ",".join(str(v) for v in (obs.get("lut_rgbw") or [])),
                "measured_xyY": ",".join(str(v) for v in (obs.get("measured_xyY") or [])),
                "xy_dx": obs.get("xy_dx"),
                "xy_dy": obs.get("xy_dy"),
                "pass_count": pass_stats.get("pass_count"),
                "fail_count": fail_stats.get("fail_count"),
                "best_pass_dE": pass_stats.get("best_dE"),
                "best_capture_available": bool(best),
                "best_capture_xy_dist": best.get("xy_dist", ""),
                "best_capture_Y_log_ratio": best.get("Y_log_ratio", ""),
                "best_capture_rgbw": ",".join(str(best.get(k, "")) for k in ("cap_r16", "cap_g16", "cap_b16", "cap_w16")) if best else "",
                "capture_delta_rgbw": json.dumps(_json_sanitize(obs.get("capture_delta_rgbw")), sort_keys=True),
                "channel_direction_hints": "|".join(f"{ch}:{mv}" for ch, mv in hints.items()),
                "failure_flags": "|".join(obs.get("failure_flags", [])),
                "seen_count": obs.get("seen_count"),
                "sessions": "|".join(str(s) for s in obs.get("sessions", [])),
            })

    # Split pass/fail views preserve observations; they are not collapsed views.
    pass_entries: dict[str, dict] = {}
    fail_entries: dict[str, dict] = {}
    for key, entry in bank["entries"].items():
        obs = entry.get("observations", []) if isinstance(entry, dict) else []
        pass_obs = [o for o in obs if o.get("status") == "pass"]
        fail_obs = [o for o in obs if o.get("status") == "fail"]
        if pass_obs:
            e = dict(entry)
            e["observations"] = pass_obs
            e["observation_count"] = len(pass_obs)
            pass_entries[key] = e
        if fail_obs:
            e = dict(entry)
            e["observations"] = fail_obs
            e["observation_count"] = len(fail_obs)
            fail_entries[key] = e

    bank_path.write_text(json.dumps(_json_sanitize(bank), indent=2), encoding="utf-8")
    pass_path.write_text(json.dumps(_json_sanitize({
        "schema_version": 2,
        "display_id": display_id,
        "entries": pass_entries,
    }), indent=2), encoding="utf-8")
    fail_path.write_text(json.dumps(_json_sanitize({
        "schema_version": 2,
        "display_id": display_id,
        "entries": fail_entries,
    }), indent=2), encoding="utf-8")

    session_path = sessions_dir / f"{session_id}_feedback_bank.json"
    session_csv = sessions_dir / f"{session_id}_feedback_detail.csv"
    session_path.write_text(json.dumps(_json_sanitize({
        "session": session_summary,
        "schema_version": 2,
        "entries": session_entries,
    }), indent=2), encoding="utf-8")
    if detail_rows:
        with session_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(detail_rows[0].keys()))
            writer.writeheader()
            writer.writerows(detail_rows)

    return {
        "bank_path": bank_path,
        "pass_path": pass_path,
        "fail_path": fail_path,
        "session_path": session_path,
        "session_csv": session_csv,
        "summary": session_summary,
    }

def write_verifier_failure_dictionary(
    verifier_dir: Path,
    output_dir: Path,
    *,
    dE_threshold: float = 2.5,
) -> tuple[Path, Path] | None:
    """Build an informational verifier failure dictionary from prior CSV outputs.

    This is diagnostic-only.  The builder does not alter LUT scores from this
    dictionary yet; it only writes JSON/CSV that describe repeated verifier
    failures, likely failure classes, and any target-match capture that appears
    close enough to have been a passing candidate.

    The input directory may contain any mix of verifier CSVs and
    ``lut_target_match`` CSVs from previous runs.
    """
    verifier_dir = Path(verifier_dir)
    if not verifier_dir.exists() or not verifier_dir.is_dir():
        return None

    verifier_rows: list[dict] = []
    target_match_by_key: dict[str, list[dict]] = {}

    for csv_path in sorted(verifier_dir.glob("*.csv")):
        rows = _read_csv_rows_safe(csv_path)
        if not rows:
            continue
        headers = set(rows[0].keys())
        lower_name = csv_path.name.lower()

        is_target_match = (
            "target_match" in lower_name
            or {"rank", "cap_r16", "cap_g16", "cap_b16", "cap_w16"}.issubset(headers)
        )
        if is_target_match:
            for row in rows:
                patch = str(row.get("patch", "")).strip()
                r = _csv_int(row, "verifier_r16", "input_r", "r16")
                g = _csv_int(row, "verifier_g16", "input_g", "g16")
                b = _csv_int(row, "verifier_b16", "input_b", "b16")
                key = f"{r},{g},{b}"
                tm = {
                    "source_file": csv_path.name,
                    "patch": patch,
                    "rgb_key": key,
                    "rank": _csv_int(row, "rank", default=999999),
                    "score": _csv_float(row, "score", default=float("inf")),
                    "xy_dist": _csv_float(row, "xy_dist", default=float("inf")),
                    "Y_log_ratio": _csv_float(row, "Y_log_ratio", "y_log_ratio", default=float("inf")),
                    "cap_x": _csv_float(row, "cap_x"),
                    "cap_y": _csv_float(row, "cap_y"),
                    "cap_Y": _csv_float(row, "cap_Y"),
                    "cap_r16": _csv_int(row, "cap_r16"),
                    "cap_g16": _csv_int(row, "cap_g16"),
                    "cap_b16": _csv_int(row, "cap_b16"),
                    "cap_w16": _csv_int(row, "cap_w16"),
                    "cap_name": str(row.get("cap_name", "")),
                    "cap_source_file": str(row.get("cap_source_file", "")),
                }
                target_match_by_key.setdefault(key, []).append(tm)
            continue

        # Verifier output: accept both GUI verifier and analysis CSV naming.
        looks_verifier = (
            "verifier_dE" in headers
            or "verifier_de" in {h.lower() for h in headers}
            or {"lut_r16", "lut_g16", "lut_b16", "lut_w16"}.issubset(headers)
        )
        if not looks_verifier:
            continue

        for row in rows:
            r = _csv_int(row, "verifier_r16", "input_r", "r16")
            g = _csv_int(row, "verifier_g16", "input_g", "g16")
            b = _csv_int(row, "verifier_b16", "input_b", "b16")
            lr = _csv_int(row, "lut_r16", "out_r", "lut_r")
            lg = _csv_int(row, "lut_g16", "out_g", "lut_g")
            lb = _csv_int(row, "lut_b16", "out_b", "lut_b")
            lw = _csv_int(row, "lut_w16", "out_w", "lut_w")
            de = _csv_float(row, "verifier_dE", "verifier_de", "dE", "deltaE", default=float("nan"))
            ok_flag = _csv_bool(row, "verifier_ok", "ok", "pass")
            fail = (np.isfinite(de) and de > dE_threshold) or (("verifier_ok" in row or "ok" in row) and not ok_flag)
            if not fail:
                continue

            inp = np.array([r, g, b], dtype=np.float64)
            out = np.array([lr, lg, lb, lw], dtype=np.float64)
            active = inp > 0
            common = float(np.min(inp))
            inp_max = float(np.max(inp)) if np.max(inp) > 0 else 1.0
            out_rgb_max = float(np.max(out[:3]))
            mask = int(active[0]) | (int(active[1]) << 1) | (int(active[2]) << 2)
            inactive_rgb = 0.0
            for idx_ch in range(3):
                if not active[idx_ch]:
                    inactive_rgb = max(inactive_rgb, float(out[idx_ch]))

            flags: list[str] = ["dE_fail"]
            exp_x = _csv_float(row, "exp_x", "target_x")
            exp_y = _csv_float(row, "exp_y", "target_y")
            meas_x = _csv_float(row, "verifier_meas_x", "measured_x", "meas_x")
            meas_y = _csv_float(row, "verifier_meas_y", "measured_y", "meas_y")
            xy_dx = float(exp_x - meas_x) if np.isfinite(exp_x) and np.isfinite(meas_x) else float("nan")
            xy_dy = float(exp_y - meas_y) if np.isfinite(exp_y) and np.isfinite(meas_y) else float("nan")
            if np.isfinite(xy_dx) and abs(xy_dx) > 0.0025:
                flags.append("xy_raise_x" if xy_dx > 0 else "xy_lower_x")
            if np.isfinite(xy_dy) and abs(xy_dy) > 0.0025:
                flags.append("xy_raise_y" if xy_dy > 0 else "xy_lower_y")
            channel_moves: list[str] = []
            for _name, _inp, _out in (("r", r, lr), ("g", g, lg), ("b", b, lb)):
                if _out > _inp * 1.05 + 512.0:
                    channel_moves.append(f"lower_{_name}")
                elif _inp > 2048.0 and _out < max(128.0, _inp * 0.035):
                    channel_moves.append(f"raise_{_name}")
            flags.extend(channel_moves)
            if mask == 7 and common > 0:
                if lw < 0.72 * common:
                    flags.append("under_w")
                if lw > 1.25 * common + 512.0:
                    flags.append("over_w")
                if out_rgb_max > 1.25 * inp_max:
                    flags.append("overdrive")
                if inp_max <= 32768.0 and out_rgb_max >= 50000.0:
                    flags.append("low_input_high_output")
            if mask in (3, 5, 6):
                if inactive_rgb > 512.0:
                    flags.append("dual_inactive_rgb_leak")
                if lw > 512.0:
                    flags.append("dual_w_leak")
            verifier_rows.append({
                "source_file": csv_path.name,
                "patch": str(row.get("patch", "")).strip(),
                "rgb_key": f"{r},{g},{b}",
                "input_r": r, "input_g": g, "input_b": b,
                "lut_r": lr, "lut_g": lg, "lut_b": lb, "lut_w": lw,
                "exp_x": exp_x,
                "exp_y": exp_y,
                "meas_x": meas_x,
                "meas_y": meas_y,
                "xy_dx": xy_dx,
                "xy_dy": xy_dy,
                "channel_direction_hints": "|".join(channel_moves),
                "meas_Y": _csv_float(row, "verifier_meas_Y", "measured_Y", "meas_Y"),
                "verifier_dE": de,
                "input_mask": mask,
                "input_common_min": common,
                "input_max": inp_max,
                "out_rgb_max": out_rgb_max,
                "out_w_to_common": float(lw / max(common, 1.0)) if common > 0 else 0.0,
                "failure_flags": flags,
            })

    # Add target-match summaries to each verifier failure and aggregate by RGB.
    agg: dict[str, dict] = {}
    detailed_rows: list[dict] = []
    for row in verifier_rows:
        key = row["rgb_key"]
        matches = target_match_by_key.get(key, [])
        best = None
        if matches:
            # Prefer plausible passing-ish candidates; otherwise smallest xy_dist.
            matches_sorted = sorted(
                matches,
                key=lambda m: (
                    0 if (m["xy_dist"] <= 0.012 and abs(m["Y_log_ratio"]) <= 0.50) else 1,
                    m["xy_dist"],
                    abs(m["Y_log_ratio"]),
                    m["rank"],
                ),
            )
            best = matches_sorted[0]

        rec = dict(row)
        if best is not None:
            rec.update({
                "best_capture_available": True,
                "best_capture_rank": best["rank"],
                "best_capture_xy_dist": best["xy_dist"],
                "best_capture_Y_log_ratio": best["Y_log_ratio"],
                "best_capture_r": best["cap_r16"],
                "best_capture_g": best["cap_g16"],
                "best_capture_b": best["cap_b16"],
                "best_capture_w": best["cap_w16"],
                "best_capture_name": best["cap_name"],
                "best_capture_source_file": best["cap_source_file"],
                "passing_capture_candidate": bool(best["xy_dist"] <= 0.012 and abs(best["Y_log_ratio"]) <= 0.50),
                "best_minus_lut_r": int(best["cap_r16"] - row["lut_r"]),
                "best_minus_lut_g": int(best["cap_g16"] - row["lut_g"]),
                "best_minus_lut_b": int(best["cap_b16"] - row["lut_b"]),
                "best_minus_lut_w": int(best["cap_w16"] - row["lut_w"]),
            })
        else:
            rec.update({
                "best_capture_available": False,
                "best_capture_rank": "",
                "best_capture_xy_dist": "",
                "best_capture_Y_log_ratio": "",
                "best_capture_r": "",
                "best_capture_g": "",
                "best_capture_b": "",
                "best_capture_w": "",
                "best_capture_name": "",
                "best_capture_source_file": "",
                "passing_capture_candidate": False,
                "best_minus_lut_r": "",
                "best_minus_lut_g": "",
                "best_minus_lut_b": "",
                "best_minus_lut_w": "",
            })
        detailed_rows.append(rec)

        a = agg.setdefault(key, {
            "rgb_key": key,
            "input_r": row["input_r"],
            "input_g": row["input_g"],
            "input_b": row["input_b"],
            "fail_count": 0,
            "max_dE": 0.0,
            "flags": set(),
            "patches": set(),
            "sources": set(),
            "best_capture": None,
        })
        a["fail_count"] += 1
        if np.isfinite(row["verifier_dE"]):
            a["max_dE"] = max(float(a["max_dE"]), float(row["verifier_dE"]))
        a["flags"].update(row["failure_flags"])
        if row["patch"]:
            a["patches"].add(row["patch"])
        a["sources"].add(row["source_file"])
        if best is not None:
            cur = a.get("best_capture")
            if cur is None or (best["xy_dist"], abs(best["Y_log_ratio"])) < (cur["xy_dist"], abs(cur["Y_log_ratio"])):
                a["best_capture"] = best

    json_dict = {
        "diagnostic_only": True,
        "verifier_dir": str(verifier_dir),
        "dE_threshold": dE_threshold,
        "failure_rows": len(detailed_rows),
        "unique_rgb_failures": len(agg),
        "entries": {},
    }
    aggregate_rows: list[dict] = []
    for key, a in sorted(agg.items(), key=lambda kv: (-kv[1]["fail_count"], kv[0])):
        best = a.get("best_capture") or {}
        entry = {
            "rgb_key": key,
            "input_rgb": [a["input_r"], a["input_g"], a["input_b"]],
            "fail_count": a["fail_count"],
            "max_dE": a["max_dE"],
            "failure_flags": sorted(a["flags"]),
            "patches": sorted(a["patches"]),
            "source_files": sorted(a["sources"]),
            "best_capture": best if best else None,
        }
        json_dict["entries"][key] = entry
        aggregate_rows.append({
            "rgb_key": key,
            "input_r": a["input_r"],
            "input_g": a["input_g"],
            "input_b": a["input_b"],
            "fail_count": a["fail_count"],
            "max_dE": a["max_dE"],
            "failure_flags": "|".join(sorted(a["flags"])),
            "patches": "|".join(sorted(a["patches"])),
            "best_capture_available": bool(best),
            "best_capture_xy_dist": best.get("xy_dist", "") if best else "",
            "best_capture_Y_log_ratio": best.get("Y_log_ratio", "") if best else "",
            "best_capture_rgbw": (
                f"{best.get('cap_r16', '')},{best.get('cap_g16', '')},{best.get('cap_b16', '')},{best.get('cap_w16', '')}"
                if best else ""
            ),
            "best_capture_name": best.get("cap_name", "") if best else "",
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "verifier_failure_dictionary.json"
    csv_path = output_dir / "verifier_failure_dictionary.csv"
    detail_path = output_dir / "verifier_failure_dictionary_detail.csv"
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(json_dict, fh, indent=2)
    if aggregate_rows:
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(aggregate_rows[0].keys()))
            writer.writeheader()
            writer.writerows(aggregate_rows)
    if detailed_rows:
        with detail_path.open("w", newline="", encoding="utf-8") as fh:
            # Flatten list flags for CSV.
            out_rows = []
            for r in detailed_rows:
                rr = dict(r)
                rr["failure_flags"] = "|".join(rr["failure_flags"])
                out_rows.append(rr)
            writer = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
            writer.writeheader()
            writer.writerows(out_rows)
    return json_path, csv_path



def write_utilization_csv(
    meta: list[dict],
    used_anchor_set: set[int],
    output_path: Path,
) -> None:
    """Write per-capture utilization report.

    Each row is one unique drive state.  The 'used' column indicates whether
    this capture was selected as a tetrahedron vertex (or KD-tree neighbour)
    for at least one LUT node during the full build.
    """
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = [
            "capture_index", "used",
            "r16", "g16", "b16", "w16",
            "X", "Y", "Z",
            "n_averaged", "example_name",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for idx, m in enumerate(meta):
            writer.writerow({
                "capture_index": idx,
                "used": idx in used_anchor_set,
                **m,
            })


def format_header_u16_entries(values: np.ndarray, values_per_line: int = 12) -> str:
    chunks: list[str] = []
    for start in range(0, values.size, values_per_line):
        end = min(start + values_per_line, values.size)
        line = ", ".join(str(int(v)) for v in values[start:end])
        chunks.append(f"    {line}")
    return ",\n".join(chunks)


def write_rgbw_header(
    cube: np.ndarray,
    output_path: Path,
    lut_name: str,
    args: argparse.Namespace,
    source_grid_size: int,
) -> None:
    quantized = np.clip(np.round(cube), 0, 65535).astype(np.uint16)
    flat = quantized.reshape(-1, 4)
    entry_count = int(flat.shape[0])
    guard = f"HYPERHDR_{output_path.stem.upper()}_H".replace("-", "_")

    lines = [
        "// Auto-generated by build_delaunay_rgbw_lut.py",
        "// Mode 2: Delaunay-tetrahedralized RGBW LUT from physical captures.",
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
        f"static const uint32_t RGBW_LUT_AXIS_MIN = 0;",
        f"static const uint32_t RGBW_LUT_AXIS_MAX = {int(round(args.sample_scale))};",
        "",
        f"static const uint16_t RGBW_LUT_R[{entry_count}] PROGMEM = {{",
        format_header_u16_entries(flat[:, 0]),
        "};",
        "",
        f"static const uint16_t RGBW_LUT_G[{entry_count}] PROGMEM = {{",
        format_header_u16_entries(flat[:, 1]),
        "};",
        "",
        f"static const uint16_t RGBW_LUT_B[{entry_count}] PROGMEM = {{",
        format_header_u16_entries(flat[:, 2]),
        "};",
        "",
        f"static const uint16_t RGBW_LUT_W[{entry_count}] PROGMEM = {{",
        format_header_u16_entries(flat[:, 3]),
        "};",
        "",
        f"#endif  // {guard}",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def save_lut_npy(cube: np.ndarray, output_path: Path) -> None:
    quantized = np.clip(np.round(cube), 0, 65535).astype(np.uint16)
    memmap = open_memmap(output_path, mode="w+", dtype=np.uint16, shape=quantized.shape)
    memmap[:] = quantized
    del memmap


def summarize_build(
    coarse_rows: list[dict],
    xyz_points: np.ndarray,
    rgbw_points: np.ndarray,
    used_anchor_set: set[int],
    raw_count: int,
    args: argparse.Namespace,
    target_rgb_basis: np.ndarray,
    white_channel_xy: tuple[float, float],
    equal_rgb_xy: tuple[float, float],
    y_scale: float = 1.0,
) -> dict:
    n_unique = len(xyz_points)

    gains = np.array([r["white_gain_abs"] for r in coarse_rows], dtype=float)

    return {
        "mode": "delaunay",
        "solver": "family_hull",
        "settings": {
            "input_dir": str(args.input_dir),
            "target_white_balance_mode": args.target_white_balance_mode,
            "sample_scale": args.sample_scale,
            "coarse_grid_size": args.coarse_grid_size,
            "full_grid_size": args.full_grid_size,
            "white_x": args.white_x,
            "white_y": args.white_y,
            "white_Y": args.white_Y,
            "y_scale": y_scale,
            "delta_e_tiebreak": getattr(args, "delta_e_tiebreak", 2.0),
            "chroma_gate": getattr(args, "chroma_gate", 15.0),
        },
        "build_mode": "delaunay",
        "basis_xyz_per_q16": {
            "r16": target_rgb_basis[:, 0].tolist(),
            "g16": target_rgb_basis[:, 1].tolist(),
            "b16": target_rgb_basis[:, 2].tolist(),
        },
        "capture_stats": {
            "raw_rows": raw_count,
            "unique_drive_states": n_unique,
            "duplicates_averaged": raw_count - n_unique,
        },
        "basis_sanity": {
            "equal_rgb_neutral_xy": list(equal_rgb_xy),
            "white_channel_xy": list(white_channel_xy),
            "reference_white_xy": [args.white_x, args.white_y],
        },
        "coarse_diagnostics": {
            "white_gain_p01": float(np.quantile(gains, 0.01)) if len(gains) > 0 else float("nan"),
            "white_gain_p10": float(np.quantile(gains, 0.10)) if len(gains) > 0 else float("nan"),
            "white_gain_p50": float(np.quantile(gains, 0.50)) if len(gains) > 0 else float("nan"),
            "white_gain_p90": float(np.quantile(gains, 0.90)) if len(gains) > 0 else float("nan"),
            "white_gain_p99": float(np.quantile(gains, 0.99)) if len(gains) > 0 else float("nan"),
        },
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    reference_white = ReferenceWhite(args.white_x, args.white_y, args.white_Y)
    display_profile, display_profile_path = load_or_create_display_profile(args, reference_white)
    feedback_mode = str(getattr(args, "feedback_mode", "diagnostic"))
    if feedback_mode in {"candidate", "reevaluate"}:
        print(
            f"  feedback-mode={feedback_mode!r}: exact verifier-passing feedback candidates are active.",
            flush=True,
        )
    elif feedback_mode == "penalty":
        print(
            "  feedback-mode='penalty' is not active yet; using diagnostic feedback bank only.",
            flush=True,
        )
    print(
        f"Using display profile {display_profile.get('display_id')} "
        f"({display_profile_path})",
        flush=True,
    )

    # ------------------------------------------------------------------
    # Load captures and deduplicate
    # ------------------------------------------------------------------
    print(f"Loading captures from {args.input_dir} …", flush=True)
    xyz_points, rgbw_points, raw_count, meta = load_captures(args.input_dir)
    n_unique = len(xyz_points)
    print(
        f"  {raw_count} ok rows  →  {n_unique} unique drive states "
        f"({raw_count - n_unique} averaged duplicates)",
        flush=True,
    )

    # ------------------------------------------------------------------
    # Fit the target RGB basis (white-balance correction for the input grid)
    # ------------------------------------------------------------------
    print("Fitting target RGB basis from pure-channel sweeps …", flush=True)
    basis = fit_basis_from_pure_sweeps(args.input_dir)
    rgb_basis = np.column_stack([basis["r16"], basis["g16"], basis["b16"]])
    white_basis = basis["w16"]
    target_rgb_basis, trb_info = build_target_rgb_basis(
        rgb_basis, reference_white, args.target_white_balance_mode,
        white_basis=white_basis,
    )
    wb_scales = np.array(trb_info.get("channel_scales", [1.0, 1.0, 1.0]), dtype=np.float64)
    equal_rgb_xy = xyz_to_xy(basis["r16"] + basis["g16"] + basis["b16"])
    white_channel_xy = xyz_to_xy(white_basis)

    # ------------------------------------------------------------------
    # Fit multi-family bases from ALL primary combinations in captures
    # ------------------------------------------------------------------
    print("Fitting multi-family bases (R, G, B, W, RG, RB, GB, RW, GW, BW, RGB, \u2026) \u2026", flush=True)
    family_bases = fit_basis_from_all_families(args.input_dir)
    fitted_families = sorted(family_bases.keys())
    print(f"  Fitted families: {', '.join(fitted_families)}", flush=True)

    # ------------------------------------------------------------------
    # Partition capture cloud by emitter family (Items 4 + 5)
    # ------------------------------------------------------------------
    print("Partitioning captures by emitter family …", flush=True)
    family_capture_sets = build_family_capture_sets(xyz_points, rgbw_points)
    for fk, (fxyz, _) in sorted(family_capture_sets.items()):
        print(f"  {fk:>6}: {len(fxyz)} captures", flush=True)

    # ------------------------------------------------------------------
    # Luminance scale: full-white RGB → RGBW-inclusive Y maximum (Item 1)
    # ------------------------------------------------------------------
    print("Computing RGBW-inclusive luminance scale …", flush=True)
    tri_all = Delaunay(xyz_points)
    y_scale = compute_y_scale(
        xyz_points, target_rgb_basis, args.sample_scale, tri=tri_all,
        reference_white=reference_white, rgbw_points=rgbw_points,
    )
    del tri_all
    print(f"  y_scale = {y_scale:.4f}  "
          f"(full-white target Y: {float((target_rgb_basis @ np.full(3, args.sample_scale))[1]):.1f}"
          f" → {float((target_rgb_basis @ np.full(3, args.sample_scale))[1] * y_scale):.1f})",
          flush=True)

    # ------------------------------------------------------------------
    # Coarse cube  (diagnostics + comparison CSV)
    # ------------------------------------------------------------------
    coarse_axis = axis_values(args.coarse_grid_size, args.sample_scale)
    coarse_cube, coarse_rows, coarse_used = build_delaunay_cube(
        coarse_axis, xyz_points, rgbw_points, target_rgb_basis, reference_white,
        args, build_comparison=True, family_bases=family_bases,
        y_scale=y_scale, family_capture_sets=family_capture_sets,
        raw_rgb_basis=rgb_basis,
    )

    coarse_npy = output_dir / f"delaunay_rgbw_coarse_{args.coarse_grid_size}.npy"
    save_lut_npy(coarse_cube, coarse_npy)
    write_comparison_csv(
        coarse_rows, output_dir / "delaunay_coarse_comparison.csv"
    )
    if not getattr(args, "skip_probe_debug", False):
        write_probe_debug_csv(
            output_dir / "delaunay_probe_debug.csv",
            xyz_points, rgbw_points, target_rgb_basis, rgb_basis, white_basis,
            reference_white, args, family_bases, family_capture_sets, y_scale,
        )
        print(f"  Probe debug CSV written to {output_dir / 'delaunay_probe_debug.csv'}", flush=True)

    if getattr(args, "verifier_diagnostics_dir", None) is not None and not getattr(args, "skip_failure_dictionary", False):
        result = write_verifier_failure_dictionary(
            Path(args.verifier_diagnostics_dir),
            output_dir,
            dE_threshold=float(getattr(args, "feedback_trust_pass_dE", 2.5)),
        )
        if result is not None:
            _json_path, _csv_path = result
            print(f"  Legacy failure dictionary written to {_json_path} and {_csv_path}", flush=True)

        if str(getattr(args, "feedback_mode", "diagnostic")) != "off":
            fb = write_verifier_feedback_bank(
                Path(args.verifier_diagnostics_dir),
                output_dir,
                args,
                display_profile,
                dE_threshold=float(getattr(args, "feedback_trust_pass_dE", 2.5)),
            )
            if fb is not None:
                print(
                    f"  Display feedback bank written to {fb['bank_path']} "
                    f"(pass={fb['summary']['pass_rows']}, fail={fb['summary']['fail_rows']})",
                    flush=True,
                )

    w_pcts = [r["w_pct"] for r in coarse_rows if r.get("w_pct", 0) > 0]
    print(
        f"  Coarse {args.coarse_grid_size}\u00b3: median W% = "
        f"{float(np.median(w_pcts)):.1f}%  max W% = {float(np.max(w_pcts)):.1f}%"
        if w_pcts else f"  Coarse {args.coarse_grid_size}\u00b3: (no rows)",
        flush=True,
    )

    # ------------------------------------------------------------------
    # Full cube  (solved directly \u2014 no trilinear expansion)
    # ------------------------------------------------------------------
    if not args.skip_full_lut:
        full_axis = axis_values(args.full_grid_size, args.sample_scale)
        full_cube, _, _ = build_delaunay_cube(
            full_axis, xyz_points, rgbw_points, target_rgb_basis, reference_white,
            args, build_comparison=False, family_bases=family_bases,
            y_scale=y_scale, family_capture_sets=family_capture_sets,
            raw_rgb_basis=rgb_basis,
        )

        full_npy = output_dir / f"delaunay_rgbw_full_{args.full_grid_size}.npy"
        save_lut_npy(full_cube, full_npy)
        print(f"  Full {args.full_grid_size}\u00b3 LUT written to {full_npy}", flush=True)

    # ------------------------------------------------------------------
    # Summary JSON
    # ------------------------------------------------------------------
    summary = summarize_build(
        coarse_rows, xyz_points, rgbw_points, set(), raw_count, args,
        target_rgb_basis, white_channel_xy, equal_rgb_xy, y_scale=y_scale,
    )
    summary["display_profile"] = {
        "display_id": display_profile.get("display_id"),
        "display_profile_path": str(display_profile_path),
        "feedback_mode": str(getattr(args, "feedback_mode", "diagnostic")),
        "feedback_bank": str(_feedback_bank_paths(args, display_profile.get("display_id", "default_display"))[0]),
    }
    with (output_dir / "delaunay_lut_summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    # ------------------------------------------------------------------
    # Optional C header
    # ------------------------------------------------------------------
    if not args.skip_header:
        header_grid_size = args.header_grid_size if args.header_grid_size > 0 else args.coarse_grid_size
        _full_cube_var = locals().get("full_cube", coarse_cube)
        header_cube = (
            coarse_cube if header_grid_size == args.coarse_grid_size
            else (_full_cube_var if not args.skip_full_lut and header_grid_size == args.full_grid_size
                  else coarse_cube)
        )
        header_path = output_dir / f"{args.header_name}_grid_{header_grid_size}.h"
        write_rgbw_header(header_cube, header_path, args.header_name, args, header_grid_size)
        print(f"  C header written to {header_path}", flush=True)

    # ------------------------------------------------------------------
    # Summary printout
    # ------------------------------------------------------------------
    print(flush=True)
    print(f"Target basis mode   : {args.target_white_balance_mode}")
    print(f"Luminance scale     : {y_scale:.4f}")
    print(f"\u0394E tiebreak         : {getattr(args, 'delta_e_tiebreak', 2.0)}")
    print(f"Chroma gate         : {getattr(args, 'chroma_gate', 15.0)} CIELAB C*")
    print(f"Equal-RGB neutral xy: ({equal_rgb_xy[0]:.4f}, {equal_rgb_xy[1]:.4f})")
    print(f"White-channel xy    : ({white_channel_xy[0]:.4f}, {white_channel_xy[1]:.4f})")
    print(f"Reference white xy  : ({args.white_x:.4f}, {args.white_y:.4f})")
    print(f"Capture points      : {raw_count} raw  \u2192  {n_unique} unique (all in model)")
    print(f"Fitted families     : {', '.join(sorted(family_bases.keys()))}")
    fam_counts = {fk: len(v[0]) for fk, v in sorted(family_capture_sets.items())}
    print(f"Family capture cts  : { {k: v for k, v in fam_counts.items()} }")
    print(f"Outputs written to  : {output_dir}")


if __name__ == "__main__":
    main()
