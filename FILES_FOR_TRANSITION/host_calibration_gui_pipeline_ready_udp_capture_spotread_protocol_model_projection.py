#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import os
import queue
import re
import shlex
import socket
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

import serial
import serial.tools.list_ports
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "Temporal RGBW Calibration Host v7.5.2"
DEFAULT_SERIAL_BAUD = 30000000
DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parent / "captures"
FRAME_HEADER = b"TCAL"
FRAME_MAX_PAYLOAD = 128

KIND_HELLO_REQ = 0x01
KIND_HELLO_RSP = 0x81
KIND_PING_REQ = 0x02
KIND_PING_RSP = 0x82
KIND_CAL_REQ = 0x30
KIND_CAL_RSP = 0xB0
KIND_LOG = 0x90

OP_GET_STATE = 0x00
OP_SET_RENDER_ENABLED = 0x20
OP_SET_FILL = 0x21
OP_CLEAR = 0x23
OP_SET_PHASE = 0x24
OP_COMMIT = 0x26
OP_SET_PHASE_MODE = 0x28
OP_SET_SOLVER_ENABLED = 0x29
OP_SET_TEMPORAL_BLEND = 0x2A
OP_SET_FILL16 = 0x2B

PHASE_MODE_AUTO = 0
PHASE_MODE_MANUAL = 1

MAX_BFI = 4
BLEND_CYCLE_LENGTH = MAX_BFI + 1
MAX_BLEND_CYCLE_LENGTH = 60
PHASE_CONTROL_MAX = MAX_BLEND_CYCLE_LENGTH - 1

GENERIC_PLAN_FIELDS = [
    "name",
    "mode",
    "repeats",
    "r",
    "g",
    "b",
    "w",
    "lower_r",
    "lower_g",
    "lower_b",
    "lower_w",
    "upper_r",
    "upper_g",
    "upper_b",
    "upper_w",
    "r16",
    "g16",
    "b16",
    "w16",
    "bfi_r",
    "bfi_g",
    "bfi_b",
    "bfi_w",
    "use_fill16",
]

# ---------------------------------------------------------------------------
# Argyll spotread presets / measurement protocol
# ---------------------------------------------------------------------------
# -O is intentionally included in every preset because it performs one
# calibration/measurement and exits, which is what the host GUI needs for both
# local plan captures and UDP-triggered remote sparse captures.
SPOTREAD_PRESETS: dict[str, str] = {
    "XYZxy (-x -O)": "spotread -x -O",
    "Lab (-O)": "spotread -O",
    "LCh (-h -O)": "spotread -h -O",
    "Luv (-u -O)": "spotread -u -O",
}
SPOTREAD_FORMAT_TO_PRESET: dict[str, str] = {
    "xyzxy": "XYZxy (-x -O)",
    "xyy": "XYZxy (-x -O)",
    "lab": "Lab (-O)",
    "lch": "LCh (-h -O)",
    "luv": "Luv (-u -O)",
}
DEFAULT_SPOTREAD_PRESET = "XYZxy (-x -O)"
DEFAULT_SPOTREAD_COMMAND = SPOTREAD_PRESETS[DEFAULT_SPOTREAD_PRESET]


def _spotread_command_with_one_shot(command: str) -> str:
    """Ensure scripted spotread captures keep the required one-shot -O flag."""
    command = (command or DEFAULT_SPOTREAD_COMMAND).strip() or DEFAULT_SPOTREAD_COMMAND
    try:
        args = shlex.split(command, posix=False)
    except Exception:
        return command
    if args and Path(args[0]).name.lower() in {"spotread", "spotread.exe"}:
        has_one_shot = any(a == "-O" or a.startswith("-O") for a in args[1:])
        if not has_one_shot:
            command = command + " -O"
    return command


# ---------------------------------------------------------------------------
# LUT Verifier — built-in named patch set + programmatic grid generator
# ---------------------------------------------------------------------------
_VERIFIER_NAMED_PATCHES: list[tuple[str, int, int, int]] = [
    # Neutral ramp
    ("black",          0,      0,      0),
    ("near_black",     2000,   2000,   2000),
    ("neutral_6pct",   3932,   3932,   3932),
    ("neutral_25pct",  16384,  16384,  16384),
    ("neutral_50pct",  32768,  32768,  32768),
    ("neutral_75pct",  49152,  49152,  49152),
    ("white",          65535,  65535,  65535),
    # Pure primaries / secondaries at full drive
    ("red",            65535,  0,      0),
    ("green",          0,      65535,  0),
    ("blue",           0,      0,      65535),
    ("cyan",           0,      65535,  65535),
    ("magenta",        65535,  0,      65535),
    ("yellow",         65535,  65535,  0),
    # Primaries / secondaries at half drive
    ("red_half",       32768,  0,      0),
    ("green_half",     0,      32768,  0),
    ("blue_half",      0,      0,      32768),
    ("cyan_half",      0,      32768,  32768),
    ("magenta_half",   32768,  0,      32768),
    ("yellow_half",    32768,  32768,  0),
    # 50% desaturated primaries (stress-tests W extraction near-neutral)
    ("red_desat",      65535,  32768,  32768),
    ("green_desat",    32768,  65535,  32768),
    ("blue_desat",     32768,  32768,  65535),
    # Tertiary hues
    ("orange",         65535,  32768,  0),
    ("chartreuse",     32768,  65535,  0),
    ("spring",         0,      65535,  32768),
    ("azure",          0,      32768,  65535),
    ("violet",         32768,  0,      65535),
    ("rose",           65535,  0,      32768),
    # Skin tones
    ("skin_light",     65535,  45875,  37632),
    ("skin_mid",       52429,  34078,  26214),
    ("skin_dark",      29490,  17825,  12451),
    # Warm / cool near-whites
    ("warm_white",     65535,  58000,  44000),
    ("cool_white",     50000,  58000,  65535),
    # Dark saturated (test for W=0 constraint)
    ("dark_red",       12000,  0,      0),
    ("dark_green",     0,      12000,  0),
    ("dark_blue",      0,      0,      12000),
]

# Keep the original name accessible for any code that still uses it.
VERIFIER_PATCHES = _VERIFIER_NAMED_PATCHES


def _generate_verifier_patches(preset: str = "quick") -> list[tuple[str, int, int, int]]:
    """Return a patch list for the given preset.

    Presets
    -------
    quick  —   36 hand-picked named patches (fast sanity check)
    medium —  ~300 patches (named set + coarse HSV grid)
    full   — ~2000 patches (named set + dense HSV grid)
    """
    if preset == "quick":
        return list(_VERIFIER_NAMED_PATCHES)

    def _hsv_to_rgb16(h_deg: float, s: float, v: float) -> tuple[int, int, int]:
        h = h_deg / 60.0
        i = int(h) % 6
        f = h - int(h)
        p = v * (1.0 - s)
        q = v * (1.0 - f * s)
        t = v * (1.0 - (1.0 - f) * s)
        channels = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
        return (round(channels[0] * 65535), round(channels[1] * 65535), round(channels[2] * 65535))

    seen: set[tuple[int, int, int]] = {(r, g, b) for _, r, g, b in _VERIFIER_NAMED_PATCHES}
    result: list[tuple[str, int, int, int]] = list(_VERIFIER_NAMED_PATCHES)

    def _add(name: str, r16: int, g16: int, b16: int) -> None:
        key = (r16, g16, b16)
        if key not in seen:
            seen.add(key)
            result.append((name, r16, g16, b16))

    if preset == "medium":
        # Neutral ramp — 28 additional steps (log-ish spacing)
        neutral_steps = 32
        for i in range(1, neutral_steps):
            v16 = round(i / neutral_steps * 65535)
            _add(f"neutral_r{i:02d}", v16, v16, v16)
        # HSV grid: 16 hues × 4 saturations × 5 values = 320 generated
        hue_steps   = 16
        sat_levels  = [0.35, 0.6, 0.85, 1.0]
        val_levels  = [0.15, 0.35, 0.55, 0.75, 1.0]
    else:  # full
        # Neutral ramp — 62 additional steps
        neutral_steps = 64
        for i in range(1, neutral_steps):
            v16 = round(i / neutral_steps * 65535)
            _add(f"neutral_r{i:02d}", v16, v16, v16)
        # HSV grid: 32 hues × 7 saturations × 8 values = 1792 generated
        hue_steps   = 32
        sat_levels  = [0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 1.0]
        val_levels  = [0.08, 0.18, 0.3, 0.45, 0.6, 0.75, 0.9, 1.0]

    for hi in range(hue_steps):
        h = hi * 360.0 / hue_steps
        for s in sat_levels:
            for v in val_levels:
                r16, g16, b16 = _hsv_to_rgb16(h, s, v)
                _add(f"h{round(h):03d}_s{round(s * 100):03d}_v{round(v * 100):03d}",
                     r16, g16, b16)

    return result


def _lut_axis_position(v16: int, grid_size: int) -> tuple[int, int, float]:
    """Return lower index, upper index, and fractional position for a Q16 LUT axis."""
    if grid_size <= 1:
        return 0, 0, 0.0
    vf = max(0.0, min(65535.0, float(v16))) / 65535.0 * (grid_size - 1)
    lo = int(math.floor(vf))
    if lo >= grid_size - 1:
        lo = grid_size - 2
        frac = 1.0
    else:
        frac = vf - lo
    return lo, lo + 1, frac


def _clamp_lut_result(values: np.ndarray) -> tuple[int, int, int, int]:
    arr = np.asarray(values, dtype=float)
    arr = np.clip(np.rint(arr), 0.0, 65535.0).astype(np.uint16)
    return int(arr[0]), int(arr[1]), int(arr[2]), int(arr[3])


def _trilinear_lut_lookup(
    cube: np.ndarray, r16: int, g16: int, b16: int
) -> tuple[int, int, int, int]:
    """Trilinear interpolation in an (N,N,N,4) uint16 LUT cube (axis 0..65535)."""
    N = cube.shape[0]
    r0, r1, fr = _lut_axis_position(r16, N)
    g0, g1, fg = _lut_axis_position(g16, N)
    b0, b1, fb = _lut_axis_position(b16, N)
    w = [
        (1 - fr) * (1 - fg) * (1 - fb),
        (1 - fr) * (1 - fg) * fb,
        (1 - fr) * fg       * (1 - fb),
        (1 - fr) * fg       * fb,
        fr       * (1 - fg) * (1 - fb),
        fr       * (1 - fg) * fb,
        fr       * fg       * (1 - fb),
        fr       * fg       * fb,
    ]
    corners = [
        cube[r0, g0, b0], cube[r0, g0, b1],
        cube[r0, g1, b0], cube[r0, g1, b1],
        cube[r1, g0, b0], cube[r1, g0, b1],
        cube[r1, g1, b0], cube[r1, g1, b1],
    ]
    result = sum(w[i] * corners[i].astype(float) for i in range(8))
    return _clamp_lut_result(result)


def _tetrahedral_lut_lookup(
    cube: np.ndarray, r16: int, g16: int, b16: int
) -> tuple[int, int, int, int]:
    """Tetrahedral interpolation in an (N,N,N,4) uint16 LUT cube.

    This follows the standard six-tetrahedra decomposition of a cube.  On the
    neutral diagonal, equal channel fractions use only the V000->V111 diagonal
    vertices, avoiding the off-diagonal mixed-topology corners that ordinary
    trilinear interpolation blends in.
    """
    N = cube.shape[0]
    r0, r1, fr = _lut_axis_position(r16, N)
    g0, g1, fg = _lut_axis_position(g16, N)
    b0, b1, fb = _lut_axis_position(b16, N)

    c000 = cube[r0, g0, b0].astype(float)
    c100 = cube[r1, g0, b0].astype(float)
    c010 = cube[r0, g1, b0].astype(float)
    c001 = cube[r0, g0, b1].astype(float)
    c110 = cube[r1, g1, b0].astype(float)
    c101 = cube[r1, g0, b1].astype(float)
    c011 = cube[r0, g1, b1].astype(float)
    c111 = cube[r1, g1, b1].astype(float)

    if fr >= fg:
        if fg >= fb:      # fr >= fg >= fb
            result = ((1 - fr) * c000 + (fr - fg) * c100 +
                      (fg - fb) * c110 + fb * c111)
        elif fr >= fb:    # fr >= fb >= fg
            result = ((1 - fr) * c000 + (fr - fb) * c100 +
                      (fb - fg) * c101 + fg * c111)
        else:             # fb >= fr >= fg
            result = ((1 - fb) * c000 + (fb - fr) * c001 +
                      (fr - fg) * c101 + fg * c111)
    else:
        if fr >= fb:      # fg >= fr >= fb
            result = ((1 - fg) * c000 + (fg - fr) * c010 +
                      (fr - fb) * c110 + fb * c111)
        elif fg >= fb:    # fg >= fb >= fr
            result = ((1 - fg) * c000 + (fg - fb) * c010 +
                      (fb - fr) * c011 + fr * c111)
        else:             # fb >= fg >= fr
            result = ((1 - fb) * c000 + (fb - fg) * c001 +
                      (fg - fr) * c011 + fr * c111)
    return _clamp_lut_result(result)


def _lut_lookup(
    cube: np.ndarray, r16: int, g16: int, b16: int, interpolation: str = "tetrahedral"
) -> tuple[int, int, int, int]:
    mode = (interpolation or "tetrahedral").strip().lower()
    if mode == "trilinear":
        return _trilinear_lut_lookup(cube, r16, g16, b16)
    if mode == "tetrahedral":
        return _tetrahedral_lut_lookup(cube, r16, g16, b16)
    raise ValueError(f"Unsupported LUT interpolation mode: {interpolation!r}")



_VERIFIER_D65_XY = (0.3127, 0.3290)
_VERIFIER_GAMUT_PRIMARIES: dict[str, dict[str, tuple[float, float]]] = {
    "rec709": {
        "R": (0.6400, 0.3300),
        "G": (0.3000, 0.6000),
        "B": (0.1500, 0.0600),
    },
    "rec2020": {
        "R": (0.7080, 0.2920),
        "G": (0.1700, 0.7970),
        "B": (0.1310, 0.0460),
    },
    "dci-p3": {
        "R": (0.6800, 0.3200),
        "G": (0.2650, 0.6900),
        "B": (0.1500, 0.0600),
    },
    "adobe-rgb": {
        "R": (0.6400, 0.3300),
        "G": (0.2100, 0.7100),
        "B": (0.1500, 0.0600),
    },
}
_VERIFIER_GAMUT_CHOICES = ["summary/native", "rec709", "rec2020", "dci-p3", "adobe-rgb"]
_VERIFIER_TRANSFER_CHOICES = ["linear", "gamut"]
_VERIFIER_INTERPOLATION_CHOICES = ["tetrahedral", "trilinear"]


def _xy_to_xyz1_tuple(xy: tuple[float, float]) -> np.ndarray:
    x, y = float(xy[0]), float(xy[1])
    return np.array([x / y, 1.0, (1.0 - x - y) / y], dtype=float)


def _verifier_build_rgb_to_xyz_matrix(
    primaries: dict[str, tuple[float, float]],
    white_xy: tuple[float, float] = _VERIFIER_D65_XY,
) -> np.ndarray:
    """Build a normalised linear-RGB -> XYZ matrix with white Y=1."""
    M_prim = np.column_stack([_xy_to_xyz1_tuple(primaries[ch]) for ch in "RGB"])
    XYZ_w = _xy_to_xyz1_tuple(white_xy)
    scales = np.linalg.solve(M_prim, XYZ_w)
    return M_prim * scales


_VERIFIER_GAMUT_MATRICES = {
    name: _verifier_build_rgb_to_xyz_matrix(primaries)
    for name, primaries in _VERIFIER_GAMUT_PRIMARIES.items()
}


def _verifier_apply_transfer_normalized(rgb: np.ndarray, gamut: str, transfer: str) -> np.ndarray:
    """Decode normalised source RGB to linear light for expected-xy calculation."""
    v = np.clip(np.asarray(rgb, dtype=float), 0.0, 1.0)
    transfer = (transfer or "linear").strip().lower()
    if transfer == "linear":
        return v
    if transfer != "gamut":
        raise ValueError(f"Unsupported verifier transfer mode: {transfer!r}")
    gamut = (gamut or "summary/native").strip().lower()
    if gamut == "rec709":
        return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    if gamut == "rec2020":
        return v ** 2.4
    if gamut == "dci-p3":
        return v ** 2.6
    if gamut == "adobe-rgb":
        return v ** 2.2
    return v


def _verifier_expected_xy_for_named_gamut(
    r16: int, g16: int, b16: int, gamut: str, transfer: str = "linear"
) -> tuple[float, float] | None:
    """Expected source xy for a Q16 RGB input in a named colour space."""
    gamut = (gamut or "").strip().lower()
    if gamut not in _VERIFIER_GAMUT_MATRICES:
        return None
    rgb = np.array([r16, g16, b16], dtype=float) / 65535.0
    linear = _verifier_apply_transfer_normalized(rgb, gamut, transfer)
    xyz = _VERIFIER_GAMUT_MATRICES[gamut] @ linear
    s = float(np.sum(xyz))
    if s < 1e-12:
        return None
    return float(xyz[0] / s), float(xyz[1] / s)



def _verifier_xyz_to_xy_tuple(xyz: np.ndarray) -> tuple[float, float] | None:
    """Convert XYZ to xy tuple, returning None for black/invalid vectors."""
    arr = np.asarray(xyz, dtype=float)
    s = float(np.sum(arr))
    if not np.isfinite(s) or s <= 1e-12:
        return None
    return float(arr[0] / s), float(arr[1] / s)


def _verifier_point_in_triangle_xy(
    p: tuple[float, float],
    tri_xy: np.ndarray,
    eps: float = 1e-9,
) -> bool:
    """2-D barycentric inside-triangle test for CIE xy points."""
    P = np.asarray(p, dtype=float)
    A, B, C = [np.asarray(v, dtype=float) for v in tri_xy]
    M = np.array([[A[0] - C[0], B[0] - C[0]],
                  [A[1] - C[1], B[1] - C[1]]], dtype=float)
    try:
        uv = np.linalg.solve(M, P - C)
    except np.linalg.LinAlgError:
        return False
    w = np.array([uv[0], uv[1], 1.0 - uv[0] - uv[1]], dtype=float)
    return bool(np.all(w >= -eps))


def _verifier_closest_point_on_segment_xy(
    p: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
) -> tuple[np.ndarray, float]:
    """Closest point on a 2-D xy segment and its segment parameter."""
    ab = b - a
    denom = float(np.dot(ab, ab))
    if denom <= 1e-18:
        return a.copy(), 0.0
    t = float(np.clip(np.dot(p - a, ab) / denom, 0.0, 1.0))
    return a + t * ab, t


def _verifier_project_xy_to_hull(
    xy: tuple[float, float],
    hull_xy: np.ndarray,
) -> tuple[tuple[float, float], bool, str]:
    """Project xy into the measured RGB-primary hull if it falls outside.

    The verifier is chromaticity-only, so this uses the nearest point on the
    measured diode RGB triangle in CIE xy space.  That keeps named-gamut checks
    from judging against unreachable Rec.2020/Rec.709 coordinates when the
    loaded LUT/display summary says the measured diode hull cannot reproduce
    that chromaticity.
    """
    tri = np.asarray(hull_xy, dtype=float).reshape(3, 2)
    p = np.asarray(xy, dtype=float)
    if _verifier_point_in_triangle_xy((float(p[0]), float(p[1])), tri):
        return (float(p[0]), float(p[1])), False, "inside"

    labels = ("RG", "GB", "BR")
    edges = ((0, 1), (1, 2), (2, 0))
    best_q: np.ndarray | None = None
    best_d2 = float("inf")
    best_edge = "unknown"
    for label, (i, j) in zip(labels, edges):
        q, _t = _verifier_closest_point_on_segment_xy(p, tri[i], tri[j])
        d2 = float(np.dot(p - q, p - q))
        if d2 < best_d2:
            best_d2 = d2
            best_q = q
            best_edge = label
    if best_q is None:
        return (float(p[0]), float(p[1])), False, "projection_failed"
    return (float(best_q[0]), float(best_q[1])), True, best_edge



def _verifier_xyY_to_XYZ(xy: tuple[float, float], Y: float) -> np.ndarray:
    """Convert xy + absolute/relative Y to XYZ."""
    x, y = float(xy[0]), float(xy[1])
    if y <= 1e-12:
        return np.zeros(3, dtype=float)
    return np.array([x / y * float(Y),
                     float(Y),
                     (1.0 - x - y) / y * float(Y)], dtype=float)


def _verifier_nnls_small(M: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, float]:
    """Tiny dependency-free NNLS for 3xN verifier projection systems.

    Enumerates every active-set subset, solves least squares, rejects negative
    coefficient sets, and returns the lowest residual nonnegative solution.
    This mirrors the model builder's NNLS fallback without requiring scipy in
    the GUI.
    """
    A_full = np.asarray(M, dtype=float)
    b = np.asarray(b, dtype=float)
    n = int(A_full.shape[1])
    best = np.zeros(n, dtype=float)
    best_res = float("inf")
    for mask in range(1, 1 << n):
        idx = [i for i in range(n) if mask & (1 << i)]
        A = A_full[:, idx]
        try:
            t_sub, _residuals, _rank, _s = np.linalg.lstsq(A, b, rcond=None)
        except np.linalg.LinAlgError:
            continue
        t_sub = np.asarray(t_sub, dtype=float)
        if np.any(t_sub < -1e-9):
            continue
        cand = np.zeros(n, dtype=float)
        for j, i in enumerate(idx):
            cand[i] = max(0.0, float(t_sub[j]))
        res = float(np.linalg.norm(A_full @ cand - b))
        if res < best_res:
            best = cand
            best_res = res
    if not np.isfinite(best_res):
        return np.zeros(n, dtype=float), float(np.linalg.norm(b))
    return best, best_res


def _verifier_compute_model_scale_k(
    basis_xyz: dict[str, np.ndarray],
    white_xy: tuple[float, float] = _VERIFIER_D65_XY,
) -> float:
    """Recompute the model builder's named-gamut absolute-Y scale factor.

    The RGBW model scales normalized named-gamut XYZ so D65 white reaches the
    brightest available channel in the D65-containing sub-gamut.  Matching that
    scale is important because the model's out-of-hull projection is an XYZ
    NNLS/gamut-clip operation, not a nearest-point-in-xy operation.
    """
    target = _verifier_xyY_to_XYZ(white_xy, 1.0)
    for tri in (("R", "G", "W"), ("R", "B", "W"), ("B", "G", "W")):
        M = np.column_stack([basis_xyz[ch] for ch in tri])
        try:
            t = np.linalg.solve(M, target)
        except np.linalg.LinAlgError:
            continue
        if np.all(t >= -1e-9):
            max_t = float(np.max(t))
            return 1.0 / max(max_t, 1e-12)
    return 1.0


def _verifier_model_project_xy_for_named_gamut(
    r16: int,
    g16: int,
    b16: int,
    gamut: str,
    transfer: str,
    basis_xyz: dict[str, np.ndarray],
    white_xy: tuple[float, float] = _VERIFIER_D65_XY,
) -> tuple[tuple[float, float] | None, bool, str]:
    """Project named-gamut target xy using the RGBW model's fallback policy.

    This intentionally differs from nearest CIE-xy projection.  The model
    builder converts source RGB to absolute XYZ, then if the chromaticity is
    outside the measured LED RGB hull it tries RGW/RBW/BGW nonnegative solves
    and chooses the topology with the smallest XYZ residual.  If a solved
    channel exceeds full scale, the whole vector is normalized by max(t), and
    the expected xy is taken from that clipped XYZ state.
    """
    gamut = (gamut or "").strip().lower()
    if gamut not in _VERIFIER_GAMUT_MATRICES:
        return None, False, "unsupported_gamut"

    rgb = np.array([r16, g16, b16], dtype=float) / 65535.0
    linear = _verifier_apply_transfer_normalized(rgb, gamut, transfer)
    xyz_norm = _VERIFIER_GAMUT_MATRICES[gamut] @ linear
    if float(np.sum(xyz_norm)) <= 1e-12:
        return None, False, "black"

    target_xyz = xyz_norm * _verifier_compute_model_scale_k(basis_xyz, white_xy)
    target_xy = _verifier_xyz_to_xy_tuple(target_xyz)
    if target_xy is None:
        return None, False, "black"

    hull_xy = np.asarray([_verifier_xyz_to_xy_tuple(basis_xyz[ch]) for ch in "RGB"], dtype=float)
    if _verifier_point_in_triangle_xy(target_xy, hull_xy):
        return target_xy, False, "inside"

    best_tri: tuple[str, str, str] | None = None
    best_t: np.ndarray | None = None
    best_res = float("inf")
    for tri in (("R", "G", "W"), ("R", "B", "W"), ("B", "G", "W")):
        M = np.column_stack([basis_xyz[ch] for ch in tri])
        t, res = _verifier_nnls_small(M, target_xyz)
        if res < best_res:
            best_res = res
            best_tri = tri
            best_t = t

    if best_tri is None or best_t is None:
        return _verifier_project_xy_to_hull(target_xy, hull_xy)

    max_t = float(np.max(best_t)) if best_t.size else 0.0
    if max_t > 1.0:
        best_t = best_t / max_t

    M = np.column_stack([basis_xyz[ch] for ch in best_tri])
    projected_xyz = M @ best_t
    projected_xy = _verifier_xyz_to_xy_tuple(projected_xyz)
    if projected_xy is None:
        return _verifier_project_xy_to_hull(target_xy, hull_xy)
    return projected_xy, True, "model_" + "".join(best_tri)



def _xy_chroma_de(
    meas_x: float, meas_y: float,
    exp_x: float,  exp_y: float,
    ref_x: float,  ref_y: float,
) -> float:
    """Chromaticity-only ΔE: a*b* Euclidean distance with both points normalised to Y=100."""
    def xy_to_ab(x: float, y: float) -> tuple[float, float]:
        if y < 1e-10:
            return 0.0, 0.0
        X = x * 100.0 / y
        Z = (1.0 - x - y) * 100.0 / y
        rX = ref_x * 100.0 / ref_y
        rZ = (1.0 - ref_x - ref_y) * 100.0 / ref_y

        def f(t: float) -> float:
            d = 6.0 / 29.0
            return t ** (1.0 / 3.0) if t > d ** 3 else t / (3.0 * d * d) + 4.0 / 29.0

        a = 500.0 * (f(X / rX) - f(1.0))   # Y/rY = 100/100 = 1
        b = 200.0 * (f(1.0) - f(Z / rZ))
        return a, b

    ma, mb = xy_to_ab(meas_x, meas_y)
    ea, eb = xy_to_ab(exp_x, exp_y)
    return math.sqrt((ma - ea) ** 2 + (mb - eb) ** 2)


@dataclass
class MeasurementPlanRow:
    name: str
    r: int
    g: int
    b: int
    w: int
    bfi_r: int
    bfi_g: int
    bfi_b: int
    bfi_w: int
    repeats: int
    lower_r: int = 0
    lower_g: int = 0
    lower_b: int = 0
    lower_w: int = 0
    upper_r: int = 0
    upper_g: int = 0
    upper_b: int = 0
    upper_w: int = 0
    r16: int = 0
    g16: int = 0
    b16: int = 0
    w16: int = 0
    use_fill16: bool = False
    mode: str = "fill8"

    def normalized_mode(self) -> str:
        if self.mode == "blend8":
            return "blend8"
        if self.use_fill16 or self.mode == "fill16":
            return "fill16"
        return "fill8"

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["mode"] = self.normalized_mode()
        data["use_fill16"] = int(self.normalized_mode() == "fill16")
        return data


class DirectSerialClient:
    def __init__(self, log_queue, noise_filter=None):
        self.log_queue = log_queue
        self.noise_filter = noise_filter
        self.serial_port = None
        self.rx_thread = None
        self.stop_event = threading.Event()
        self.packet_handler = None
        self.write_lock = threading.Lock()

    def _log(self, line: str):
        if self.noise_filter and self.noise_filter(line):
            return
        self.log_queue.put(line)

    def start(self, port: str, baud: int):
        self.stop()
        self.serial_port = serial.Serial(port=port, baudrate=baud, timeout=0.05, write_timeout=1.0)
        self.stop_event.clear()
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx_thread.start()
        self._log(f"[serial] connected to {port} @ {baud}")

    def stop(self):
        self.stop_event.set()
        if self.serial_port is not None:
            try:
                self.serial_port.close()
            except Exception:
                pass
        self.serial_port = None

    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def send_frame(self, kind: int, payload: bytes = b""):
        if not self.is_connected():
            raise RuntimeError("Transport is not connected")
        if len(payload) > FRAME_MAX_PAYLOAD:
            raise ValueError(f"Payload too large: {len(payload)} > {FRAME_MAX_PAYLOAD}")
        frame = bytearray(FRAME_HEADER)
        frame.append(kind & 0xFF)
        frame.append((len(payload) >> 8) & 0xFF)
        frame.append(len(payload) & 0xFF)
        frame.extend(payload)
        crc = 0
        for value in frame[4:]:
            crc ^= value
        frame.append(crc)
        with self.write_lock:
            if self.serial_port is None:
                raise RuntimeError("Serial device is not connected")
            self.serial_port.write(frame)
            self.serial_port.flush()
            self._log(f"[tx] kind=0x{kind:02X} payload={payload.hex()}")

    def _rx_loop(self):
        buffer = bytearray()
        while not self.stop_event.is_set():
            if self.serial_port is None:
                return
            try:
                chunk = self.serial_port.read(256)
            except serial.SerialException as exc:
                self._log(f"[serial] read error: {exc}")
                return
            if not chunk:
                continue
            buffer.extend(chunk)
            self._consume_frames(buffer)

    def _consume_frames(self, buffer: bytearray):
        while True:
            idx = buffer.find(FRAME_HEADER)
            if idx < 0:
                if len(buffer) > len(FRAME_HEADER):
                    del buffer[:-len(FRAME_HEADER)]
                return
            if idx > 0:
                del buffer[:idx]
            if len(buffer) < 8:
                return
            payload_len = (buffer[5] << 8) | buffer[6]
            if payload_len > FRAME_MAX_PAYLOAD:
                self._log(f"[rx] dropping oversized payload {payload_len}")
                del buffer[0]
                continue
            frame_len = 4 + 1 + 2 + payload_len + 1
            if len(buffer) < frame_len:
                return
            frame = bytes(buffer[:frame_len])
            del buffer[:frame_len]
            crc = 0
            for value in frame[4:-1]:
                crc ^= value
            if crc != frame[-1]:
                self._log("[rx] crc mismatch, dropping frame")
                continue
            kind = frame[4]
            payload = frame[7:-1]
            self._handle_frame(kind, payload)

    def _handle_frame(self, kind: int, payload: bytes):
        if kind == KIND_LOG:
            text = payload.decode("utf-8", errors="replace")
            self._log(f"[device] {text}")
            msg = {"type": "device_log", "text": text}
        elif kind == KIND_HELLO_RSP:
            text = payload.decode("utf-8", errors="replace")
            msg = {"type": "hello", "text": text}
            self._log(f"[rx] hello={text}")
        elif kind == KIND_PING_RSP:
            text = payload.decode("utf-8", errors="replace")
            msg = {"type": "ping", "text": text}
            self._log(f"[rx] ping={text}")
        elif kind == KIND_CAL_RSP:
            msg = {
                "type": "cal_response",
                "op": payload[0] if len(payload) > 0 else None,
                "status": payload[1] if len(payload) > 1 else None,
                "render_enabled": payload[2] if len(payload) > 2 else None,
                "manual_phase_mode": payload[3] if len(payload) > 3 else None,
                "phase": payload[4] if len(payload) > 4 else None,
                "payload_hex": payload.hex(),
            }
            if msg["op"] is not None and msg["status"] is not None:
                self._log(f"[rx] cal op=0x{msg['op']:02X} status=0x{msg['status']:02X} phase={msg['phase']}")
            else:
                self._log(f"[rx] cal payload={payload.hex()}")
        else:
            msg = {"type": "frame", "kind": kind, "payload_hex": payload.hex()}
            self._log(f"[rx] kind=0x{kind:02X} payload={payload.hex()}")
        if self.packet_handler is not None:
            try:
                self.packet_handler(msg)
            except Exception as exc:
                self._log(f"[rx] packet handler error: {exc}")

    @staticmethod
    def available_ports() -> list[str]:
        return [port.device for port in serial.tools.list_ports.comports()]


class ArgyllRunner:
    NUMBER = r"([0-9.+\-eE]+)"
    XYZ_RE = re.compile(r"XYZ:\s*" + NUMBER + r"\s+" + NUMBER + r"\s+" + NUMBER, re.I)
    YXY_RE = re.compile(r"Yxy:\s*" + NUMBER + r"\s+" + NUMBER + r"\s+" + NUMBER, re.I)
    LAB_RE = re.compile(r"(?:Lab|L\*a\*b\*)\s*:\s*" + NUMBER + r"\s+" + NUMBER + r"\s+" + NUMBER, re.I)
    LCH_RE = re.compile(r"(?:LCh|L\*C\*h|LCh\(ab\))\s*:\s*" + NUMBER + r"\s+" + NUMBER + r"\s+" + NUMBER, re.I)
    LUV_RE = re.compile(r"(?:Luv|L\*u\*v\*)\s*:\s*" + NUMBER + r"\s+" + NUMBER + r"\s+" + NUMBER, re.I)

    def __init__(self, log_queue):
        self.log_queue = log_queue
        self.active_proc = None
        self.lock = threading.Lock()

    def cleanup_stale_processes(self):
        self.log_queue.put("[argyll] cleaning stale spotread")
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/IM", "spotread.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["pkill", "-f", "spotread"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.75)

    def abort_active(self):
        with self.lock:
            proc = self.active_proc
            self.active_proc = None
        if proc is None:
            self.log_queue.put("[argyll] no active process")
            return
        self.log_queue.put(f"[argyll] aborting pid={proc.pid}")
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        time.sleep(0.75)

    @staticmethod
    def _float_groups(match: re.Match | None) -> tuple[float, ...] | None:
        if match is None:
            return None
        try:
            return tuple(float(match.group(i)) for i in range(1, match.lastindex + 1))
        except Exception:
            return None

    @staticmethod
    def _infer_measurement_format(args: list[str], result: dict[str, object]) -> str:
        lower = [a.lower() for a in args]
        if any(a == "-x" for a in lower) or "xyY" in result:
            return "XYZxy"
        if any(a == "-h" for a in lower) or "LCh" in result:
            return "XYZ_LCh"
        if any(a == "-u" for a in lower) or "Luv" in result:
            return "XYZ_Luv"
        if "Lab" in result:
            return "XYZ_Lab"
        if "XYZ" in result:
            return "XYZ"
        return "unknown"

    def run_spotread(self, command, timeout_s=45.0, send_trigger_newline=True, cleanup_first=True):
        command = _spotread_command_with_one_shot(str(command or DEFAULT_SPOTREAD_COMMAND))
        if cleanup_first:
            self.cleanup_stale_processes()
        args = shlex.split(command, posix=False)
        self.log_queue.put(f"[argyll] running: {args!r}")
        started = time.time()
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=creationflags)
        with self.lock:
            self.active_proc = proc
        stdout = ""
        stderr = ""
        timed_out = False
        try:
            if send_trigger_newline and proc.stdin is not None:
                time.sleep(0.2)
                proc.stdin.write("\n")
                proc.stdin.flush()
                self.log_queue.put("[argyll] sent newline trigger")
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            self.log_queue.put("[argyll] timeout expired, terminating")
            try:
                proc.terminate()
                stdout, stderr = proc.communicate(timeout=3)
            except Exception:
                try:
                    proc.kill()
                    stdout, stderr = proc.communicate(timeout=3)
                except Exception:
                    pass
        finally:
            with self.lock:
                if self.active_proc is proc:
                    self.active_proc = None

        result: dict[str, object] = {
            "ok": (proc.returncode == 0) and (not timed_out),
            "returncode": proc.returncode,
            "elapsed_s": time.time() - started,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "timed_out": timed_out,
            "pid": proc.pid,
            "command": args,
            "command_string": command,
            "source": "spotread",
        }
        out_text = result["stdout"]

        xyz = self._float_groups(self.XYZ_RE.search(out_text))
        if xyz is not None:
            X, Y_xyz, Z = xyz
            result["XYZ"] = {"X": X, "Y": Y_xyz, "Z": Z}
            result["X"] = X
            result["Y_from_XYZ"] = Y_xyz
            result["Z"] = Z

        yxy = self._float_groups(self.YXY_RE.search(out_text))
        if yxy is not None:
            Y, x, y = yxy
            result["xyY"] = {"Y": Y, "x": x, "y": y}
            result["Y"] = Y
            result["x"] = x
            result["y"] = y

        lab = self._float_groups(self.LAB_RE.search(out_text))
        if lab is not None:
            L, a, b = lab
            result["Lab"] = {"L": L, "a": a, "b": b}
            result["Lab_L"] = L
            result["Lab_a"] = a
            result["Lab_b"] = b

        lch = self._float_groups(self.LCH_RE.search(out_text))
        if lch is not None:
            L, C, h = lch
            result["LCh"] = {"L": L, "C": C, "h": h}
            result["LCh_L"] = L
            result["LCh_C"] = C
            result["LCh_h"] = h

        luv = self._float_groups(self.LUV_RE.search(out_text))
        if luv is not None:
            L, u, v = luv
            result["Luv"] = {"L": L, "u": u, "v": v}
            result["Luv_L"] = L
            result["Luv_u"] = u
            result["Luv_v"] = v

        result["measurement_format"] = self._infer_measurement_format(args, result)
        columns = ["ok", "returncode", "elapsed_s", "timed_out"]
        if "XYZ" in result:
            columns += ["XYZ.X", "XYZ.Y", "XYZ.Z"]
        if "xyY" in result:
            columns += ["xyY.Y", "xyY.x", "xyY.y"]
        if "Lab" in result:
            columns += ["Lab.L", "Lab.a", "Lab.b"]
        if "LCh" in result:
            columns += ["LCh.L", "LCh.C", "LCh.h"]
        if "Luv" in result:
            columns += ["Luv.L", "Luv.u", "Luv.v"]
        result["measurement_columns"] = columns

        self.log_queue.put(f"[argyll] done rc={proc.returncode} timeout={timed_out} format={result.get('measurement_format')}")
        time.sleep(0.75)
        return result

class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1440x920")
        self.root.minsize(1240, 760)
        self.log_queue = queue.Queue()
        self.show_transport_spam_var = tk.BooleanVar(value=False)
        self.device = DirectSerialClient(self.log_queue, noise_filter=self.should_filter_transport_log)
        self.device.packet_handler = self.on_device_packet
        self.argyll = ArgyllRunner(self.log_queue)
        self.current_status = {}
        self.measurement_rows: list[MeasurementPlanRow] = []
        self.capture_dir = DEFAULT_ARTIFACT_DIR
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.last_measurement = None
        self.running_plan = False
        self.plan_pause_event = threading.Event()
        self.plan_stop_event = threading.Event()
        self.plan_report_path: Path | None = None
        self.resume_capture_path: Path | None = None
        self.plan_source_path: Path | None = None
        # --- UDP capture bridge state ---
        self.udp_capture_socket: socket.socket | None = None
        self.udp_capture_thread: threading.Thread | None = None
        self.udp_capture_stop_event = threading.Event()
        self.udp_capture_lock = threading.Lock()
        # --- LUT verifier state ---
        self.verifier_lut: np.ndarray | None = None
        self.verifier_summary: dict = {}
        self.verifier_results: list[dict] = []
        self.verifier_running: bool = False
        self.verifier_stop_event = threading.Event()
        self._verifier_preset_var = tk.StringVar(value="quick")
        self._verifier_interp_var = tk.StringVar(value="tetrahedral")
        self._verifier_gamut_var = tk.StringVar(value="summary/native")
        self._verifier_transfer_var = tk.StringVar(value="linear")
        self._verifier_project_hull_var = tk.BooleanVar(value=True)
        self.build_ui()
        self.refresh_serial_ports()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._start_log_pump()

    def should_filter_transport_log(self, line: str) -> bool:
        if self.show_transport_spam_var.get():
            return False
        return (line.startswith("[tx]") or line.startswith("[rx]") or
                line.startswith("[udp capture]") or line.startswith("[serial]"))

    def build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="both", expand=True)

        controls = ttk.LabelFrame(top, text="Connection")
        controls.pack(fill="x")

        self.serial_port_var = tk.StringVar(value="")
        self.serial_baud_var = tk.StringVar(value=str(DEFAULT_SERIAL_BAUD))
        self.udp_capture_host_var = tk.StringVar(value="0.0.0.0")
        self.udp_capture_port_var = tk.StringVar(value="19446")
        self.udp_capture_status_text = tk.StringVar(value="udp capture: stopped")
        self.argyll_cmd_var = tk.StringVar(value=DEFAULT_SPOTREAD_COMMAND)
        self.argyll_preset_var = tk.StringVar(value=DEFAULT_SPOTREAD_PRESET)
        self.phase_mode_var = tk.IntVar(value=PHASE_MODE_AUTO)
        self.phase_var = tk.IntVar(value=0)
        self.settle_delay_var = tk.DoubleVar(value=0.25)
        self.timeout_var = tk.DoubleVar(value=45.0)
        self.cleanup_first_var = tk.BooleanVar(value=True)
        self.send_newline_var = tk.BooleanVar(value=True)
        self.use_fill16_var = tk.BooleanVar(value=False)
        self.plan_use_solver_var = tk.BooleanVar(value=False)
        self.resume_row_var = tk.IntVar(value=0)
        self.resume_repeat_var = tk.IntVar(value=0)
        self.resume_report_text = tk.StringVar(value="resume: none")

        row = ttk.Frame(controls)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Serial port").pack(side="left")
        self.serial_port_combo = ttk.Combobox(row, textvariable=self.serial_port_var, width=24)
        self.serial_port_combo.pack(side="left", padx=4)
        ttk.Button(row, text="Refresh", command=self.refresh_serial_ports).pack(side="left", padx=4)
        ttk.Label(row, text="Baud").pack(side="left", padx=(12, 0))
        ttk.Entry(row, textvariable=self.serial_baud_var, width=12).pack(side="left", padx=4)
        ttk.Button(row, text="Connect Serial", command=self.connect_device).pack(side="left", padx=8)
        ttk.Button(row, text="Hello", command=self.send_hello).pack(side="left", padx=4)
        ttk.Button(row, text="Ping", command=self.send_ping).pack(side="left", padx=4)
        ttk.Button(row, text="Get State", command=self.get_state).pack(side="left", padx=4)

        udp_row = ttk.Frame(controls)
        udp_row.pack(fill="x", padx=8, pady=6)
        ttk.Label(udp_row, text="UDP capture server").pack(side="left")
        ttk.Entry(udp_row, textvariable=self.udp_capture_host_var, width=14).pack(side="left", padx=4)
        ttk.Label(udp_row, text=":").pack(side="left")
        ttk.Entry(udp_row, textvariable=self.udp_capture_port_var, width=7).pack(side="left", padx=2)
        ttk.Button(udp_row, text="Start UDP capture", command=self.start_udp_capture_server).pack(side="left", padx=8)
        ttk.Button(udp_row, text="Stop UDP capture", command=self.stop_udp_capture_server).pack(side="left", padx=4)
        ttk.Label(udp_row, textvariable=self.udp_capture_status_text).pack(side="left", padx=8)

        cmdrow = ttk.Frame(controls)
        cmdrow.pack(fill="x", padx=8, pady=6)
        ttk.Label(cmdrow, text="Argyll preset").pack(side="left")
        preset_combo = ttk.Combobox(cmdrow, textvariable=self.argyll_preset_var, values=list(SPOTREAD_PRESETS.keys()), width=17, state="readonly")
        preset_combo.pack(side="left", padx=4)
        preset_combo.bind("<<ComboboxSelected>>", lambda _evt: self.apply_argyll_preset())
        ttk.Button(cmdrow, text="Apply", command=self.apply_argyll_preset).pack(side="left", padx=2)
        ttk.Label(cmdrow, text="Argyll command").pack(side="left", padx=(10, 0))
        ttk.Entry(cmdrow, textvariable=self.argyll_cmd_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(cmdrow, text="Capture dir", command=self.choose_capture_dir).pack(side="left", padx=4)

        opts = ttk.Frame(controls)
        opts.pack(fill="x", padx=8, pady=6)
        ttk.Label(opts, text="Settle s").pack(side="left")
        ttk.Entry(opts, textvariable=self.settle_delay_var, width=8).pack(side="left", padx=4)
        ttk.Label(opts, text="Timeout s").pack(side="left")
        ttk.Entry(opts, textvariable=self.timeout_var, width=8).pack(side="left", padx=4)
        ttk.Checkbutton(opts, text="Cleanup stale before read", variable=self.cleanup_first_var).pack(side="left", padx=8)
        ttk.Checkbutton(opts, text="Send newline trigger", variable=self.send_newline_var).pack(side="left", padx=8)
        ttk.Checkbutton(opts, text="Show transport spam", variable=self.show_transport_spam_var).pack(side="left", padx=8)
        ttk.Checkbutton(opts, text="Plan uses solver mode", variable=self.plan_use_solver_var).pack(side="left", padx=8)
        ttk.Button(opts, text="Kill stale spotread", command=self.kill_stale).pack(side="left", padx=8)
        ttk.Button(opts, text="Abort Measurement", command=self.abort_measurement).pack(side="left", padx=4)

        resume = ttk.Frame(controls)
        resume.pack(fill="x", padx=8, pady=6)
        ttk.Label(resume, text="Resume row").pack(side="left")
        ttk.Entry(resume, textvariable=self.resume_row_var, width=8).pack(side="left", padx=4)
        ttk.Label(resume, text="Resume repeat").pack(side="left")
        ttk.Entry(resume, textvariable=self.resume_repeat_var, width=8).pack(side="left", padx=4)
        ttk.Button(resume, text="Load report", command=self.load_progress_report).pack(side="left", padx=8)
        ttk.Label(resume, textvariable=self.resume_report_text).pack(side="left", padx=8)

        mid = ttk.PanedWindow(top, orient="horizontal")
        mid.pack(fill="both", expand=True, pady=8)

        left = ttk.Frame(mid)
        right = ttk.Frame(mid)
        mid.add(left, weight=1)
        mid.add(right, weight=4)

        self.build_render_panel(left)

        right_split = ttk.PanedWindow(right, orient="vertical")
        right_split.pack(fill="both", expand=True)
        tabs_container = ttk.Frame(right_split)
        log_frame = ttk.Frame(right_split)
        right_split.add(tabs_container, weight=3)
        right_split.add(log_frame, weight=1)

        right_tabs = ttk.Notebook(tabs_container)
        right_tabs.pack(fill="both", expand=True)
        plan_frame = ttk.Frame(right_tabs)
        verifier_frame = ttk.Frame(right_tabs)
        right_tabs.add(plan_frame, text="Measurement Plan")
        right_tabs.add(verifier_frame, text="LUT Verifier")

        self.build_plan_panel(plan_frame)
        self.build_verifier_panel(verifier_frame)
        self.build_log_panel(log_frame)

    def refresh_serial_ports(self):
        ports = DirectSerialClient.available_ports()
        self.serial_port_combo["values"] = ports
        if not self.serial_port_var.get() and ports:
            self.serial_port_var.set(ports[0])
        self.log_queue.put(f"[serial] found ports: {ports}")

    def _make_int_scale(self, parent, label, variable, maxv, length=220):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=2)
        ttk.Label(row, text=label, width=8).pack(side="left")
        scale = tk.Scale(row, from_=0, to=maxv, variable=variable, orient="horizontal", resolution=1, showvalue=False, command=lambda _v: self._round_var(variable))
        scale.configure(length=length)
        scale.pack(side="left", fill="x", expand=True)
        ttk.Entry(row, textvariable=variable, width=8).pack(side="left", padx=4)

    def _round_var(self, var):
        try:
            var.set(int(round(float(var.get()))))
        except Exception:
            pass
        self.update_preview()

    def build_render_panel(self, parent):
        box = ttk.LabelFrame(parent, text="Render / Manual Control")
        box.pack(fill="x", pady=6)

        self.manual_mode_var = tk.StringVar(value="fill8")
        self.r_var = tk.IntVar(value=0)
        self.g_var = tk.IntVar(value=0)
        self.b_var = tk.IntVar(value=0)
        self.w_var = tk.IntVar(value=0)
        self.lower_r_var = tk.IntVar(value=0)
        self.lower_g_var = tk.IntVar(value=0)
        self.lower_b_var = tk.IntVar(value=0)
        self.lower_w_var = tk.IntVar(value=0)
        self.r16_var = tk.IntVar(value=0)
        self.g16_var = tk.IntVar(value=0)
        self.b16_var = tk.IntVar(value=0)
        self.w16_var = tk.IntVar(value=0)
        self.bfi_r_var = tk.IntVar(value=0)
        self.bfi_g_var = tk.IntVar(value=0)
        self.bfi_b_var = tk.IntVar(value=0)
        self.bfi_w_var = tk.IntVar(value=0)

        mode_row = ttk.Frame(box)
        mode_row.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(mode_row, text="Mode").pack(side="left")
        ttk.Radiobutton(mode_row, text="Fill8", variable=self.manual_mode_var, value="fill8", command=self._on_manual_mode_changed).pack(side="left", padx=6)
        ttk.Radiobutton(mode_row, text="Blend8", variable=self.manual_mode_var, value="blend8", command=self._on_manual_mode_changed).pack(side="left", padx=6)
        ttk.Radiobutton(mode_row, text="Fill16", variable=self.manual_mode_var, value="fill16", command=self._on_manual_mode_changed).pack(side="left", padx=6)

        tabs = ttk.Notebook(box)
        tabs.pack(fill="x", padx=8, pady=4)
        base_tab = ttk.Frame(tabs)
        true16_tab = ttk.Frame(tabs)
        tabs.add(base_tab, text="8-bit / Blend8")
        tabs.add(true16_tab, text="True16")

        for label, var, maxv in [("R", self.r_var, 255), ("G", self.g_var, 255), ("B", self.b_var, 255), ("W", self.w_var, 255)]:
            self._make_int_scale(base_tab, label, var, maxv, length=180)

        blend8 = ttk.LabelFrame(base_tab, text="Blend8 lower / previous value")
        blend8.pack(fill="x", padx=8, pady=6)
        for label, var in [("Floor R", self.lower_r_var), ("Floor G", self.lower_g_var), ("Floor B", self.lower_b_var), ("Floor W", self.lower_w_var)]:
            self._make_int_scale(blend8, label, var, 255, length=180)

        bfi_box = ttk.LabelFrame(base_tab, text="BFI insertion counts")
        bfi_box.pack(fill="x", padx=8, pady=6)
        for label, var, maxv in [("BFI R", self.bfi_r_var, MAX_BFI), ("BFI G", self.bfi_g_var, MAX_BFI), ("BFI B", self.bfi_b_var, MAX_BFI), ("BFI W", self.bfi_w_var, MAX_BFI)]:
            self._make_int_scale(bfi_box, label, var, maxv, length=180)

        fill16 = ttk.LabelFrame(true16_tab, text="True 16-bit patch values")
        fill16.pack(fill="x", padx=8, pady=6)
        for txt, var in [("R16", self.r16_var), ("G16", self.g16_var), ("B16", self.b16_var), ("W16", self.w16_var)]:
            row = ttk.Frame(fill16)
            row.pack(fill="x", padx=4, pady=2)
            ttk.Label(row, text=txt, width=8).pack(side="left")
            scale = tk.Scale(row, from_=0, to=65535, variable=var, orient="horizontal", resolution=1, showvalue=False, command=lambda _v: self._sync_preview_from_16())
            scale.configure(length=180)
            scale.pack(side="left", fill="x", expand=True)
            ttk.Entry(row, textvariable=var, width=10).pack(side="left", padx=4)

        phase_box = ttk.Frame(box)
        phase_box.pack(fill="x", padx=8, pady=6)
        ttk.Label(phase_box, text="Phase mode").pack(side="left")
        ttk.Radiobutton(phase_box, text="Auto", variable=self.phase_mode_var, value=PHASE_MODE_AUTO, command=self.send_phase_mode).pack(side="left", padx=4)
        ttk.Radiobutton(phase_box, text="Manual", variable=self.phase_mode_var, value=PHASE_MODE_MANUAL, command=self.send_phase_mode).pack(side="left", padx=4)
        ttk.Label(phase_box, text="Phase index").pack(side="left", padx=(16, 4))
        tk.Scale(phase_box, from_=0, to=PHASE_CONTROL_MAX, variable=self.phase_var, orient="horizontal", resolution=1, showvalue=False, command=lambda _v: self._round_var(self.phase_var)).pack(side="left", fill="x", expand=True)
        ttk.Entry(phase_box, textvariable=self.phase_var, width=8).pack(side="left", padx=4)
        ttk.Button(phase_box, text="Apply phase", command=self.send_phase).pack(side="left", padx=4)

        btns = ttk.Frame(box)
        btns.pack(fill="x", padx=8, pady=8)
        ttk.Button(btns, text="Send State", command=self.send_fill).pack(side="left", padx=4)
        ttk.Button(btns, text="Commit", command=self.commit).pack(side="left", padx=4)
        ttk.Button(btns, text="Clear", command=self.clear).pack(side="left", padx=4)
        ttk.Button(btns, text="Measure Once", command=self.measure_once).pack(side="left", padx=12)

        pv = ttk.Frame(box)
        pv.pack(fill="x", padx=8, pady=8)
        current_box = ttk.Frame(pv)
        current_box.pack(side="left")
        ttk.Label(current_box, text="Upper / current").pack(anchor="w")
        self.preview_canvas = tk.Canvas(current_box, width=90, height=48, bg="#000000", highlightthickness=1, highlightbackground="#999")
        self.preview_canvas.pack(side="left")
        lower_box = ttk.Frame(pv)
        lower_box.pack(side="left", padx=(12, 0))
        ttk.Label(lower_box, text="Lower / previous").pack(anchor="w")
        self.lower_preview_canvas = tk.Canvas(lower_box, width=90, height=48, bg="#000000", highlightthickness=1, highlightbackground="#999")
        self.lower_preview_canvas.pack(side="left")
        self.preview_text = tk.StringVar(value="Preview RGB")
        ttk.Label(pv, textvariable=self.preview_text).pack(side="left", padx=12)

        status = ttk.Frame(box)
        status.pack(fill="x", padx=8, pady=6)
        self.status_text = tk.StringVar(value="status: idle")
        ttk.Label(status, textvariable=self.status_text).pack(side="left")
        self.measurement_text = tk.StringVar(value="last measurement: none")
        ttk.Label(status, textvariable=self.measurement_text).pack(side="left", padx=16)
        self.update_preview()

    def build_log_panel(self, parent):
        box = ttk.LabelFrame(parent, text="Logs")
        box.pack(fill="both", expand=True, pady=6)
        self.log = tk.Text(box, height=10, wrap="word")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    def build_plan_panel(self, parent):
        box = ttk.LabelFrame(parent, text="Measurement Plan")
        box.pack(fill="both", expand=True, pady=6)
        toolbar = ttk.Frame(box)
        toolbar.pack(fill="x", padx=6, pady=6)
        ttk.Button(toolbar, text="Add current", command=self.add_current_to_plan).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Import plan CSV", command=self.import_plan_csv).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Clear plan", command=self.clear_plan).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Delete selected", command=self.delete_selected_plan_rows).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Run plan", command=self.run_plan).pack(side="left", padx=8)
        ttk.Button(toolbar, text="Pause/Resume", command=self.toggle_pause_plan).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Stop", command=self.stop_plan).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Save plan CSV", command=self.save_plan_csv).pack(side="left", padx=4)
        cols = ("name", "mode", "rgbw", "rgbw16", "bfi", "lower", "upper", "timing", "repeats")
        tree_frame = ttk.Frame(box)
        tree_frame.pack(fill="both", expand=True, padx=6, pady=6)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=16, selectmode="extended")
        widths = {"name": 200, "mode": 78, "rgbw": 110, "rgbw16": 180, "bfi": 90, "lower": 130, "upper": 130, "timing": 100, "repeats": 60}
        for col in cols:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=widths.get(col, 70), stretch=(col in {"name", "rgbw16", "lower", "upper"}), anchor="center")
        self.tree.column("name", anchor="w")
        self.tree.bind("<<TreeviewSelect>>", self.on_plan_selection)

        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

    def _start_log_pump(self):
        def inner():
            try:
                while True:
                    self.log.insert("end", self.log_queue.get_nowait() + "\n")
                    self.log.see("end")
            except queue.Empty:
                pass
            self.root.after(100, inner)

        inner()


    def apply_argyll_preset(self):
        preset = self.argyll_preset_var.get().strip()
        command = SPOTREAD_PRESETS.get(preset)
        if command:
            self.argyll_cmd_var.set(command)
            self.log_queue.put(f"[argyll] preset {preset} -> {command}")

    def _format_measurement_status(self, result: dict[str, object]) -> str:
        fmt = str(result.get("measurement_format", "unknown"))
        xyY = result.get("xyY")
        if isinstance(xyY, dict):
            return f"last measurement: {fmt} Y={xyY.get('Y')} x={xyY.get('x')} y={xyY.get('y')}"
        xyz = result.get("XYZ")
        if isinstance(xyz, dict):
            base = f"last measurement: {fmt} X={xyz.get('X')} Y={xyz.get('Y')} Z={xyz.get('Z')}"
            lch = result.get("LCh")
            if isinstance(lch, dict):
                return base + f" LCh=({lch.get('L')},{lch.get('C')},{lch.get('h')})"
            lab = result.get("Lab")
            if isinstance(lab, dict):
                return base + f" Lab=({lab.get('L')},{lab.get('a')},{lab.get('b')})"
            luv = result.get("Luv")
            if isinstance(luv, dict):
                return base + f" Luv=({luv.get('L')},{luv.get('u')},{luv.get('v')})"
            return base
        return f"last measurement: {fmt} ok={result.get('ok')} rc={result.get('returncode')}"

    def _spotread_command_for_request(self, request: dict[str, object] | None = None) -> str:
        if request:
            raw_cmd = request.get("spotread_command", request.get("argyll_command"))
            if raw_cmd:
                return _spotread_command_with_one_shot(str(raw_cmd))
            raw_preset = request.get("spotread_preset", request.get("measurement_preset"))
            if raw_preset:
                preset = str(raw_preset).strip()
                if preset in SPOTREAD_PRESETS:
                    return SPOTREAD_PRESETS[preset]
                key = SPOTREAD_FORMAT_TO_PRESET.get(preset.lower())
                if key:
                    return SPOTREAD_PRESETS[key]
                raise ValueError(f"unsupported spotread preset/format: {preset!r}")
            raw_fmt = request.get("measurement_format", request.get("measurement_mode"))
            if raw_fmt:
                key = SPOTREAD_FORMAT_TO_PRESET.get(str(raw_fmt).strip().lower())
                if not key:
                    raise ValueError(f"unsupported measurement_format: {raw_fmt!r}")
                return SPOTREAD_PRESETS[key]
        return _spotread_command_with_one_shot(self.argyll_cmd_var.get().strip())

    @staticmethod
    def _measurement_value(result: dict[str, object], dotted_key: str):
        if "." in dotted_key:
            head, tail = dotted_key.split(".", 1)
            node = result.get(head)
            if isinstance(node, dict):
                return node.get(tail)
            return None
        return result.get(dotted_key)

    def _plan_capture_header(self) -> list[str]:
        return [
            "name", "mode", "use_fill16", "r", "g", "b", "w",
            "lower_r", "lower_g", "lower_b", "lower_w",
            "upper_r", "upper_g", "upper_b", "upper_w",
            "r16", "g16", "b16", "w16", "bfi_r", "bfi_g", "bfi_b", "bfi_w",
            "repeat_index", "solver_mode", "measurement_format", "spotread_command",
            "ok", "returncode", "elapsed_s", "timed_out",
            "XYZ_X", "XYZ_Y", "XYZ_Z", "xyY_Y", "xyY_x", "xyY_y",
            "Lab_L", "Lab_a", "Lab_b", "LCh_L", "LCh_C", "LCh_h", "Luv_L", "Luv_u", "Luv_v",
            "stdout", "stderr",
        ]

    def _plan_capture_row(self, row: MeasurementPlanRow, rep: int, solver_mode: int, result: dict[str, object]) -> list[object]:
        return [
            row.name, row.normalized_mode(), int(row.normalized_mode() == "fill16"), row.r, row.g, row.b, row.w,
            row.lower_r, row.lower_g, row.lower_b, row.lower_w,
            row.upper_r, row.upper_g, row.upper_b, row.upper_w,
            row.r16, row.g16, row.b16, row.w16, row.bfi_r, row.bfi_g, row.bfi_b, row.bfi_w,
            rep, solver_mode, result.get("measurement_format"), result.get("command_string"),
            result.get("ok"), result.get("returncode"), result.get("elapsed_s"), result.get("timed_out"),
            self._measurement_value(result, "XYZ.X"), self._measurement_value(result, "XYZ.Y"), self._measurement_value(result, "XYZ.Z"),
            self._measurement_value(result, "xyY.Y"), self._measurement_value(result, "xyY.x"), self._measurement_value(result, "xyY.y"),
            self._measurement_value(result, "Lab.L"), self._measurement_value(result, "Lab.a"), self._measurement_value(result, "Lab.b"),
            self._measurement_value(result, "LCh.L"), self._measurement_value(result, "LCh.C"), self._measurement_value(result, "LCh.h"),
            self._measurement_value(result, "Luv.L"), self._measurement_value(result, "Luv.u"), self._measurement_value(result, "Luv.v"),
            result.get("stdout", ""), result.get("stderr", ""),
        ]

    def start_udp_capture_server(self):
        """Start a UDP request server for remote sparse-capture orchestration.

        This is intentionally *not* a device transport. The LED device still
        receives TCAL commands over the normal serial connection. UDP is only a
        host-side RPC channel for tools such as rgbw_lut_builder to request a
        patch render/capture and receive the colorimeter result.
        """
        try:
            self.stop_udp_capture_server()
            host = self.udp_capture_host_var.get().strip() or "0.0.0.0"
            port = int(self.udp_capture_port_var.get().strip())
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.1)
            sock.bind((host, port))
            self.udp_capture_socket = sock
            self.udp_capture_stop_event.clear()
            self.udp_capture_thread = threading.Thread(
                target=self._udp_capture_rx_loop,
                name="udp-capture-rx",
                daemon=True,
            )
            self.udp_capture_thread.start()
            bound = sock.getsockname()
            self.udp_capture_status_text.set(f"udp capture: listening {bound[0]}:{bound[1]}")
            self.log_queue.put(f"[udp capture] listening on {bound[0]}:{bound[1]}")
        except Exception as exc:
            self.udp_capture_status_text.set("udp capture: stopped")
            self.log_queue.put(f"[udp capture] start failed: {exc}")
            messagebox.showerror("UDP capture server failed", str(exc))

    def stop_udp_capture_server(self):
        self.udp_capture_stop_event.set()
        sock = self.udp_capture_socket
        self.udp_capture_socket = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        self.udp_capture_status_text.set("udp capture: stopped")

    def _udp_capture_rx_loop(self):
        while not self.udp_capture_stop_event.is_set():
            sock = self.udp_capture_socket
            if sock is None:
                return
            try:
                payload, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self.udp_capture_stop_event.is_set():
                    self.log_queue.put(f"[udp capture] receive error: {exc}")
                return
            threading.Thread(
                target=self._handle_udp_capture_payload,
                args=(payload, addr),
                daemon=True,
            ).start()

    def _udp_send_json(self, addr: tuple[str, int], payload: dict[str, object]):
        sock = self.udp_capture_socket
        if sock is None:
            return
        try:
            data = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except Exception:
            data = json.dumps({"ok": False, "error": "failed to encode response"}).encode("utf-8")
        try:
            sock.sendto(data, addr)
        except OSError as exc:
            self.log_queue.put(f"[udp capture] send error to {addr}: {exc}")

    def _handle_udp_capture_payload(self, payload: bytes, addr: tuple[str, int]):
        try:
            request = json.loads(payload.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("UDP request must be a JSON object")
        except Exception as exc:
            self._udp_send_json(addr, {"ok": False, "error": f"bad request: {exc}"})
            return

        reply_addr = addr
        try:
            if "reply_host" in request or "reply_port" in request:
                reply_addr = (
                    str(request.get("reply_host", addr[0])),
                    int(request.get("reply_port", addr[1])),
                )
        except Exception:
            reply_addr = addr

        request_id = request.get("request_id", request.get("id", ""))
        command = str(request.get("type", request.get("cmd", "capture_rgbw16"))).strip().lower()
        if command in {"ping", "status"}:
            self._udp_send_json(reply_addr, {
                "ok": True,
                "request_id": request_id,
                "type": "status",
                "serial_connected": self.device.is_connected(),
                "running_plan": self.running_plan,
                "verifier_running": self.verifier_running,
                "protocol": "rgbw_lut_capture_udp_v1",
                "supported_request_types": ["capture_rgbw16", "render", "set", "set_rgbw16", "display_rgbw16", "status", "ping"],
                "supported_measurement_formats": sorted(SPOTREAD_FORMAT_TO_PRESET.keys()),
                "default_measurement_format": "XYZxy",
                "default_spotread_command": DEFAULT_SPOTREAD_COMMAND,
            })
            return

        # Measurement/rendering is serialized because both the serial LED device
        # and the colorimeter are single shared resources.
        with self.udp_capture_lock:
            try:
                response = self._run_udp_capture_request(request, command)
            except Exception as exc:
                response = {
                    "ok": False,
                    "request_id": request_id,
                    "type": command,
                    "error": str(exc),
                }
        self._udp_send_json(reply_addr, response)

    def _coerce_rgbw16_from_udp_request(self, request: dict[str, object]) -> tuple[int, int, int, int]:
        if "rgbw16" in request:
            vals = request["rgbw16"]
            if not isinstance(vals, (list, tuple)) or len(vals) != 4:
                raise ValueError("rgbw16 must be a 4-element list")
            return tuple(max(0, min(65535, int(round(float(v))))) for v in vals)  # type: ignore[return-value]
        keys16 = ["r16", "g16", "b16", "w16"]
        if any(k in request for k in keys16):
            return tuple(max(0, min(65535, int(round(float(request.get(k, 0)))))) for k in keys16)  # type: ignore[return-value]
        if "rgbw8" in request:
            vals = request["rgbw8"]
            if not isinstance(vals, (list, tuple)) or len(vals) != 4:
                raise ValueError("rgbw8 must be a 4-element list")
            return tuple(max(0, min(255, int(round(float(v))))) * 257 for v in vals)  # type: ignore[return-value]
        keys8 = ["r", "g", "b", "w"]
        if any(k in request for k in keys8):
            return tuple(max(0, min(255, int(round(float(request.get(k, 0)))))) * 257 for k in keys8)  # type: ignore[return-value]
        raise ValueError("request must include rgbw16, r16/g16/b16/w16, rgbw8, or r/g/b/w")

    def _run_udp_capture_request(self, request: dict[str, object], command: str) -> dict[str, object]:
        request_id = request.get("request_id", request.get("id", ""))
        if not self.device.is_connected():
            raise RuntimeError("serial device is not connected")
        r16, g16, b16, w16 = self._coerce_rgbw16_from_udp_request(request)
        name = str(request.get("name", request_id or "udp_capture"))
        row = MeasurementPlanRow(
            name=name,
            r=self._q16_to_u8(r16),
            g=self._q16_to_u8(g16),
            b=self._q16_to_u8(b16),
            w=self._q16_to_u8(w16),
            bfi_r=int(request.get("bfi_r", 0) or 0),
            bfi_g=int(request.get("bfi_g", 0) or 0),
            bfi_b=int(request.get("bfi_b", 0) or 0),
            bfi_w=int(request.get("bfi_w", 0) or 0),
            repeats=1,
            r16=r16,
            g16=g16,
            b16=b16,
            w16=w16,
            use_fill16=True,
            mode="fill16",
        )

        self.log_queue.put(f"[udp capture] {command} {name}: RGBW16={r16}/{g16}/{b16}/{w16}")
        self._render_plan_row(row)

        response: dict[str, object] = {
            "ok": True,
            "protocol": "rgbw_lut_capture_udp_v1",
            "request_id": request_id,
            "type": command,
            "name": name,
            "render": row.to_dict(),
            "ts": time.time(),
        }
        if command in {"render", "set", "set_rgbw16", "display_rgbw16"}:
            return response

        spotread_command = self._spotread_command_for_request(request)
        measurement = self._run_measurement(command_override=spotread_command)
        self.last_measurement = measurement
        self.measurement_text.set(self._format_measurement_status(measurement))
        response["measurement"] = measurement
        response["measurement_protocol"] = {
            "format": measurement.get("measurement_format"),
            "columns": measurement.get("measurement_columns", []),
            "spotread_command": measurement.get("command_string", spotread_command),
        }

        # Keep a local JSON trace so network-triggered captures are not
        # ephemeral if the requester crashes before persisting the reply.
        trace = {
            "ts": response["ts"],
            "source": "udp_capture",
            "request": request,
            "render": row.to_dict(),
            "measurement": measurement,
        }
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "udp_capture"
        path = self.capture_dir / f"udp_capture_{int(time.time())}_{safe_name}.json"
        try:
            path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
            response["local_trace"] = str(path)
        except Exception as exc:
            response["local_trace_error"] = str(exc)
        return response

    def on_close(self):
        self.stop_udp_capture_server()
        try:
            self.device.stop()
        except Exception:
            pass
        self.root.destroy()

    def connect_device(self):
        try:
            port = self.serial_port_var.get().strip()
            if not port:
                raise ValueError("Select a serial port first")
            self.device.start(port, int(self.serial_baud_var.get().strip()))
        except Exception as exc:
            messagebox.showerror("Connect failed", str(exc))

    def _on_manual_mode_changed(self):
        self.use_fill16_var.set(self.manual_mode_var.get() == "fill16")
        self.update_preview()

    def set_preview_values(self, r, g, b, w, bfi_r, bfi_g, bfi_b, bfi_w, r16=None, g16=None, b16=None, w16=None, mode="fill8", lower_r=0, lower_g=0, lower_b=0, lower_w=0):
        self.manual_mode_var.set(mode)
        self.use_fill16_var.set(mode == "fill16")
        self.r_var.set(int(r))
        self.g_var.set(int(g))
        self.b_var.set(int(b))
        self.w_var.set(int(w))
        self.lower_r_var.set(int(lower_r))
        self.lower_g_var.set(int(lower_g))
        self.lower_b_var.set(int(lower_b))
        self.lower_w_var.set(int(lower_w))
        self.r16_var.set(int(r16) if r16 is not None else int(r) * 257)
        self.g16_var.set(int(g16) if g16 is not None else int(g) * 257)
        self.b16_var.set(int(b16) if b16 is not None else int(b) * 257)
        self.w16_var.set(int(w16) if w16 is not None else int(w) * 257)
        self.bfi_r_var.set(int(bfi_r))
        self.bfi_g_var.set(int(bfi_g))
        self.bfi_b_var.set(int(bfi_b))
        self.bfi_w_var.set(int(bfi_w))
        self.update_preview()

    def _sync_preview_from_16(self):
        self.r_var.set(int((self.r16_var.get() * 255 + 32767) // 65535))
        self.g_var.set(int((self.g16_var.get() * 255 + 32767) // 65535))
        self.b_var.set(int((self.b16_var.get() * 255 + 32767) // 65535))
        self.w_var.set(int((self.w16_var.get() * 255 + 32767) // 65535))
        self.update_preview()

    @staticmethod
    def _preview_rgb_with_white(r, g, b, w):
        r8 = max(0, min(255, int(r) + int(w)))
        g8 = max(0, min(255, int(g) + int(w)))
        b8 = max(0, min(255, int(b) + int(w)))
        return r8, g8, b8

    def update_preview(self):
        r = int(self.r_var.get())
        g = int(self.g_var.get())
        b = int(self.b_var.get())
        w = int(self.w_var.get())
        lower_r = int(self.lower_r_var.get())
        lower_g = int(self.lower_g_var.get())
        lower_b = int(self.lower_b_var.get())
        lower_w = int(self.lower_w_var.get())
        mode = self.manual_mode_var.get()
        preview_r, preview_g, preview_b = self._preview_rgb_with_white(r, g, b, w)
        lower_preview_r, lower_preview_g, lower_preview_b = self._preview_rgb_with_white(lower_r, lower_g, lower_b, lower_w)
        self.preview_canvas.configure(bg=f"#{preview_r:02x}{preview_g:02x}{preview_b:02x}")
        self.lower_preview_canvas.configure(bg=f"#{lower_preview_r:02x}{lower_preview_g:02x}{lower_preview_b:02x}")
        if mode == "blend8":
            self.preview_text.set(f"BLEND8 upper=({r},{g},{b},{self.w_var.get()}) lower=({self.lower_r_var.get()},{self.lower_g_var.get()},{self.lower_b_var.get()},{self.lower_w_var.get()}) BFI=({self.bfi_r_var.get()},{self.bfi_g_var.get()},{self.bfi_b_var.get()},{self.bfi_w_var.get()})")
        elif mode == "fill16":
            self.preview_text.set(f"FILL16 RGBW16=({self.r16_var.get()},{self.g16_var.get()},{self.b16_var.get()},{self.w16_var.get()}) RGBW8=({r},{g},{b},{self.w_var.get()})")
        else:
            self.preview_text.set(f"FILL8 RGBW=({r},{g},{b},{self.w_var.get()}) BFI=({self.bfi_r_var.get()},{self.bfi_g_var.get()},{self.bfi_b_var.get()},{self.bfi_w_var.get()})")

    def send_hello(self):
        self.device.send_frame(KIND_HELLO_REQ, b"")

    def send_ping(self):
        self.device.send_frame(KIND_PING_REQ, b"host-ping")

    def get_state(self):
        self.device.send_frame(KIND_CAL_REQ, bytes([OP_GET_STATE]))

    def send_phase_mode(self):
        self.device.send_frame(KIND_CAL_REQ, bytes([OP_SET_PHASE_MODE, self.phase_mode_var.get() & 0xFF]))

    def send_phase(self):
        phase = max(0, min(PHASE_CONTROL_MAX, int(self.phase_var.get())))
        self.phase_var.set(phase)
        self.device.send_frame(KIND_CAL_REQ, bytes([OP_SET_PHASE, phase]))

    def _pack_u16(self, value: int) -> bytes:
        value = max(0, min(65535, int(value)))
        return bytes([(value >> 8) & 0xFF, value & 0xFF])

    def _build_fill_payload(self, row: MeasurementPlanRow | None = None) -> bytes:
        if row is None:
            mode = self.manual_mode_var.get()
            if mode == "blend8":
                return self._build_blend8_payload(self._build_manual_row())
            if mode == "fill16":
                payload = bytearray([OP_SET_FILL16])
                for value in [self.r16_var.get(), self.g16_var.get(), self.b16_var.get(), self.w16_var.get()]:
                    payload.extend(self._pack_u16(value))
                return bytes(payload)
            return bytes([OP_SET_FILL, self.r_var.get() & 0xFF, self.g_var.get() & 0xFF, self.b_var.get() & 0xFF, self.w_var.get() & 0xFF, self.bfi_r_var.get() & 0xFF, self.bfi_g_var.get() & 0xFF, self.bfi_b_var.get() & 0xFF, self.bfi_w_var.get() & 0xFF])
        mode = row.normalized_mode()
        if mode == "blend8":
            return self._build_blend8_payload(row)
        if mode == "fill16":
            payload = bytearray([OP_SET_FILL16])
            for value in [row.r16, row.g16, row.b16, row.w16]:
                payload.extend(self._pack_u16(value))
            return bytes(payload)
        return bytes([OP_SET_FILL, row.r & 0xFF, row.g & 0xFF, row.b & 0xFF, row.w & 0xFF, row.bfi_r & 0xFF, row.bfi_g & 0xFF, row.bfi_b & 0xFF, row.bfi_w & 0xFF])

    def _build_blend8_payload(self, row: MeasurementPlanRow) -> bytes:
        return bytes([
            OP_SET_TEMPORAL_BLEND,
            row.lower_r & 0xFF,
            row.lower_g & 0xFF,
            row.lower_b & 0xFF,
            row.lower_w & 0xFF,
            row.upper_r & 0xFF,
            row.upper_g & 0xFF,
            row.upper_b & 0xFF,
            row.upper_w & 0xFF,
            row.bfi_r & 0xFF,
            row.bfi_g & 0xFF,
            row.bfi_b & 0xFF,
            row.bfi_w & 0xFF,
        ])

    def _build_manual_row(self) -> MeasurementPlanRow:
        mode = self.manual_mode_var.get()
        r = int(self.r_var.get())
        g = int(self.g_var.get())
        b = int(self.b_var.get())
        w = int(self.w_var.get())
        r16 = int(self.r16_var.get()) if mode == "fill16" else (r * 257)
        g16 = int(self.g16_var.get()) if mode == "fill16" else (g * 257)
        b16 = int(self.b16_var.get()) if mode == "fill16" else (b * 257)
        w16 = int(self.w16_var.get()) if mode == "fill16" else (w * 257)
        return MeasurementPlanRow(
            name="manual",
            r=r,
            g=g,
            b=b,
            w=w,
            bfi_r=int(self.bfi_r_var.get()),
            bfi_g=int(self.bfi_g_var.get()),
            bfi_b=int(self.bfi_b_var.get()),
            bfi_w=int(self.bfi_w_var.get()),
            repeats=1,
            lower_r=int(self.lower_r_var.get()),
            lower_g=int(self.lower_g_var.get()),
            lower_b=int(self.lower_b_var.get()),
            lower_w=int(self.lower_w_var.get()),
            upper_r=r,
            upper_g=g,
            upper_b=b,
            upper_w=w,
            r16=r16,
            g16=g16,
            b16=b16,
            w16=w16,
            use_fill16=(mode == "fill16"),
            mode=mode,
        )

    def send_fill(self):
        self.device.send_frame(KIND_CAL_REQ, self._build_fill_payload())

    def clear(self):
        self.device.send_frame(KIND_CAL_REQ, bytes([OP_CLEAR]))

    def commit(self):
        self.device.send_frame(KIND_CAL_REQ, bytes([OP_COMMIT]))

    def kill_stale(self):
        threading.Thread(target=self.argyll.cleanup_stale_processes, daemon=True).start()

    def abort_measurement(self):
        threading.Thread(target=self.argyll.abort_active, daemon=True).start()

    def choose_capture_dir(self):
        chosen = filedialog.askdirectory(initialdir=str(self.capture_dir))
        if chosen:
            self.capture_dir = Path(chosen)
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            self.log_queue.put(f"[fs] capture dir = {self.capture_dir}")

    def on_device_packet(self, msg):
        self.current_status = msg
        self.status_text.set(f"status: {msg.get('type')}")

    def infer_repeats(self, r, g, b, w):
        y = 0.2126 * (r / 255.0) + 0.7152 * (g / 255.0) + 0.0722 * (b / 255.0) + 1.0 * (w / 255.0)
        if y > 0.5:
            return 1
        if y > 0.15:
            return 2
        if y > 0.03:
            return 4
        return 8

    def _q16_to_u8(self, value):
        q16 = max(0, min(65535, int(value)))
        return int((q16 * 255 + 32767) // 65535)

    def _parse_bool(self, value):
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _normalize_mode(self, rec: dict[str, object]) -> str:
        mode = str(rec.get("mode", "")).strip().lower()
        if mode and mode not in {"blend8", "fill16", "fill8"}:
            raise ValueError(f"unsupported mode '{mode}'")
        if mode in {"blend8", "fill16", "fill8"}:
            return mode
        if any(str(rec.get(field, "")).strip() for field in ["lower_r", "upper_r", "lower_g", "upper_g", "lower_b", "upper_b", "lower_w", "upper_w"]):
            return "blend8"
        if self._parse_bool(rec.get("use_fill16", "0")):
            return "fill16"
        return "fill8"

    def _tree_values_for_row(self, row: MeasurementPlanRow):
        lower = ""
        upper = ""
        timing = ""
        if row.normalized_mode() == "blend8":
            lower = f"{row.lower_r}/{row.lower_g}/{row.lower_b}/{row.lower_w}"
            upper = f"{row.upper_r}/{row.upper_g}/{row.upper_b}/{row.upper_w}"
            timing = f"{row.bfi_r}/{row.bfi_g}/{row.bfi_b}/{row.bfi_w}"
        return (
            row.name,
            row.normalized_mode(),
            f"{row.r}/{row.g}/{row.b}/{row.w}",
            f"{row.r16}/{row.g16}/{row.b16}/{row.w16}",
            f"{row.bfi_r}/{row.bfi_g}/{row.bfi_b}/{row.bfi_w}",
            lower,
            upper,
            timing,
            row.repeats,
        )

    def add_plan_row(self, row: MeasurementPlanRow):
        self.measurement_rows.append(row)
        self.tree.insert("", "end", values=self._tree_values_for_row(row))

    def add_current_to_plan(self):
        row = self._build_manual_row()
        row.name = f"state_{len(self.measurement_rows):04d}"
        row.repeats = self.infer_repeats(row.r, row.g, row.b, row.w)
        self.add_plan_row(row)

    def on_plan_selection(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        try:
            idx = self.tree.index(selected[0])
        except Exception:
            return
        if not (0 <= idx < len(self.measurement_rows)):
            return
        row = self.measurement_rows[idx]
        self.set_preview_values(
            row.r,
            row.g,
            row.b,
            row.w,
            row.bfi_r,
            row.bfi_g,
            row.bfi_b,
            row.bfi_w,
            r16=row.r16,
            g16=row.g16,
            b16=row.b16,
            w16=row.w16,
            mode=row.normalized_mode(),
            lower_r=row.lower_r,
            lower_g=row.lower_g,
            lower_b=row.lower_b,
            lower_w=row.lower_w,
        )

    def _clear_plan_rows(self):
        self.measurement_rows.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _reset_resume_state(self, report_label: str = "resume: none"):
        self.resume_row_var.set(0)
        self.resume_repeat_var.set(0)
        self.resume_capture_path = None
        self.plan_report_path = None
        self.resume_report_text.set(report_label)

    def clear_plan(self):
        if not self.measurement_rows:
            return
        if not messagebox.askyesno("Clear plan", "Remove all plan entries?"):
            return
        self._clear_plan_rows()
        self._reset_resume_state()
        self.log_queue.put("[plan] cleared all entries")

    def delete_selected_plan_rows(self):
        selected = list(self.tree.selection())
        if not selected:
            return
        indices = []
        for iid in selected:
            try:
                indices.append(self.tree.index(iid))
            except Exception:
                pass
        for iid in selected:
            self.tree.delete(iid)
        if indices:
            remove_set = set(indices)
            self.measurement_rows = [row for i, row in enumerate(self.measurement_rows) if i not in remove_set]
            self.log_queue.put(f"[plan] deleted {len(remove_set)} selected entries")

    def _dict_int(self, rec, key, default=0):
        value = rec.get(key, default)
        if value in (None, ""):
            return int(default)
        return int(value)

    def _row_from_record(self, rec: dict[str, str]) -> MeasurementPlanRow:
        mode = self._normalize_mode(rec)
        lower_r = self._dict_int(rec, "lower_r", 0)
        lower_g = self._dict_int(rec, "lower_g", 0)
        lower_b = self._dict_int(rec, "lower_b", 0)
        lower_w = self._dict_int(rec, "lower_w", 0)
        upper_r = self._dict_int(rec, "upper_r", self._dict_int(rec, "r", 0))
        upper_g = self._dict_int(rec, "upper_g", self._dict_int(rec, "g", 0))
        upper_b = self._dict_int(rec, "upper_b", self._dict_int(rec, "b", 0))
        upper_w = self._dict_int(rec, "upper_w", self._dict_int(rec, "w", 0))
        r16 = self._dict_int(rec, "r16", self._dict_int(rec, "r", 0) * 257)
        g16 = self._dict_int(rec, "g16", self._dict_int(rec, "g", 0) * 257)
        b16 = self._dict_int(rec, "b16", self._dict_int(rec, "b", 0) * 257)
        w16 = self._dict_int(rec, "w16", self._dict_int(rec, "w", 0) * 257)
        if mode == "blend8":
            r = upper_r
            g = upper_g
            b = upper_b
            w = upper_w
            r16 = upper_r * 257
            g16 = upper_g * 257
            b16 = upper_b * 257
            w16 = upper_w * 257
        else:
            r = self._dict_int(rec, "r", self._q16_to_u8(r16))
            g = self._dict_int(rec, "g", self._q16_to_u8(g16))
            b = self._dict_int(rec, "b", self._q16_to_u8(b16))
            w = self._dict_int(rec, "w", self._q16_to_u8(w16))
        return MeasurementPlanRow(name=str(rec.get("name", f"state_{len(self.measurement_rows):04d}")), r=r, g=g, b=b, w=w, bfi_r=self._dict_int(rec, "bfi_r", 0), bfi_g=self._dict_int(rec, "bfi_g", 0), bfi_b=self._dict_int(rec, "bfi_b", 0), bfi_w=self._dict_int(rec, "bfi_w", 0), repeats=max(1, self._dict_int(rec, "repeats", 1)), lower_r=lower_r, lower_g=lower_g, lower_b=lower_b, lower_w=lower_w, upper_r=upper_r, upper_g=upper_g, upper_b=upper_b, upper_w=upper_w, r16=r16, g16=g16, b16=b16, w16=w16, use_fill16=(mode == "fill16"), mode=mode)

    def _import_plan_csv_path(self, path: str | Path, *, confirm_replace: bool = True) -> bool:
        path = Path(path)
        if self.measurement_rows and confirm_replace and not messagebox.askyesno("Replace plan", "Replace the current plan and clear any saved resume progress?"):
            return False
        self._clear_plan_rows()
        self._reset_resume_state()
        imported = 0
        imported_true16 = 0
        imported_legacy = 0
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            required_legacy = ["name", "r", "g", "b", "w", "bfi_r", "bfi_g", "bfi_b", "bfi_w", "repeats"]
            required_blend8 = ["name", "lower_r", "lower_g", "lower_b", "lower_w", "upper_r", "upper_g", "upper_b", "upper_w"]
            required_true16 = ["name", "r16", "g16", "b16", "w16"]
            has_supported = any(all(key in fieldnames for key in req) for req in [required_legacy, required_blend8, required_true16]) or ("mode" in fieldnames)
            if not has_supported:
                messagebox.showerror("Import failed", "Unsupported CSV schema.\n\nAccepted schemas:\n1) Legacy fill8\n2) Raw temporal blend8 with lower_*/upper_* columns\n3) True16 fill16")
                return False
            for rec in reader:
                try:
                    row = self._row_from_record(rec)
                    self.add_plan_row(row)
                    imported += 1
                    if row.normalized_mode() == "fill16":
                        imported_true16 += 1
                    else:
                        imported_legacy += 1
                except Exception as exc:
                    self.log_queue.put(f"[plan] skipped bad row during import: {exc}")
        if imported_true16 > 0:
            self.use_fill16_var.set(True)
        self.plan_source_path = path
        self.log_queue.put(f"[plan] imported {imported} entries from {path} (legacy/blend8={imported_legacy}, fill16={imported_true16})")
        return True

    def _resolve_plan_csv_for_report(self, report_path: Path, plan_source_csv: str) -> Path | None:
        plan_source_csv = plan_source_csv.strip()
        if plan_source_csv:
            candidate = Path(plan_source_csv)
            if candidate.exists():
                return candidate
        initialdir = str(report_path.parent)
        initialfile = Path(plan_source_csv).name if plan_source_csv else ""
        prompt = "Select the original plan CSV to resume from this progress report."
        if plan_source_csv:
            prompt += f"\n\nSaved path:\n{plan_source_csv}"
        messagebox.showinfo("Locate plan CSV", prompt)
        selected = filedialog.askopenfilename(initialdir=initialdir, initialfile=initialfile, filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        return Path(selected) if selected else None

    def import_plan_csv(self):
        path = filedialog.askopenfilename(initialdir=str(self.capture_dir), filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        self._import_plan_csv_path(path, confirm_replace=True)

    def highlight_plan_index(self, idx: int):
        children = self.tree.get_children()
        if 0 <= idx < len(children):
            iid = children[idx]
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)

    def _render_current_state(self):
        self.send_fill()
        time.sleep(self.settle_delay_var.get())
        self.commit()
        time.sleep(self.settle_delay_var.get())

    def _render_plan_row(self, row: MeasurementPlanRow):
        self.device.send_frame(KIND_CAL_REQ, self._build_fill_payload(row))
        time.sleep(self.settle_delay_var.get())
        self.device.send_frame(KIND_CAL_REQ, bytes([OP_COMMIT]))
        time.sleep(self.settle_delay_var.get())

    def _run_measurement(self, command_override: str | None = None):
        command = command_override or self._spotread_command_for_request(None)
        return self.argyll.run_spotread(command, timeout_s=float(self.timeout_var.get()), send_trigger_newline=bool(self.send_newline_var.get()), cleanup_first=bool(self.cleanup_first_var.get()))

    def measure_once(self):
        def worker():
            try:
                self._render_current_state()
                result = self._run_measurement()
                self.last_measurement = result
                self.measurement_text.set(self._format_measurement_status(result))
                out = {"ts": time.time(), "render": {"mode": self.manual_mode_var.get(), "r": self.r_var.get(), "g": self.g_var.get(), "b": self.b_var.get(), "w": self.w_var.get(), "lower_r": self.lower_r_var.get(), "lower_g": self.lower_g_var.get(), "lower_b": self.lower_b_var.get(), "lower_w": self.lower_w_var.get(), "r16": self.r16_var.get(), "g16": self.g16_var.get(), "b16": self.b16_var.get(), "w16": self.w16_var.get(), "bfi_r": self.bfi_r_var.get(), "bfi_g": self.bfi_g_var.get(), "bfi_b": self.bfi_b_var.get(), "bfi_w": self.bfi_w_var.get(), "use_fill16": self.use_fill16_var.get(), "phase_mode": self.phase_mode_var.get(), "phase": self.phase_var.get()}, "measurement": result}
                path = self.capture_dir / f"single_measure_{int(time.time())}.json"
                path.write_text(json.dumps(out, indent=2), encoding="utf-8")
                self.log_queue.put(f"[measure] wrote {path}")
            except Exception as exc:
                self.log_queue.put(f"[measure] error: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _step_offset(self, row_index: int, repeat_index: int) -> int:
        done = 0
        for idx, row in enumerate(self.measurement_rows):
            repeats = max(1, row.repeats)
            if idx < row_index:
                done += repeats
            elif idx == row_index:
                done += max(0, repeat_index)
                break
        return done

    def _wait_if_paused(self):
        while self.plan_pause_event.is_set() and not self.plan_stop_event.is_set():
            time.sleep(0.1)

    def _progress_payload(self, **kwargs):
        return {"app": APP_TITLE, "updated_ts": time.time(), "capture_csv": str(kwargs["capture_csv"]), "row_count": len(self.measurement_rows), "total_steps": kwargs["total_steps"], "completed_steps": kwargs["completed_steps"], "status": kwargs["status"], "solver_mode": kwargs["solver_mode"], "next_row_index": kwargs["next_row_index"], "next_repeat_index": kwargs["next_repeat_index"], "plan_source_csv": str(self.plan_source_path) if self.plan_source_path else ""}

    def _write_progress_report(self, report_path: Path, **kwargs):
        report_path.write_text(json.dumps(self._progress_payload(**kwargs), indent=2), encoding="utf-8")
        self.plan_report_path = report_path
        self.resume_report_text.set(f"resume: {report_path.name}")

    def load_progress_report(self):
        path = filedialog.askopenfilename(initialdir=str(self.capture_dir), filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        report_path = Path(path)
        data = json.loads(report_path.read_text(encoding="utf-8"))
        plan_rows = data.get("plan_rows") or []
        if plan_rows:
            self._clear_plan_rows()
            self._reset_resume_state()
            for rec in plan_rows:
                self.add_plan_row(self._row_from_record(rec))
        elif not self.measurement_rows:
            plan_source_csv = str(data.get("plan_source_csv", "") or "").strip()
            plan_path = self._resolve_plan_csv_for_report(report_path, plan_source_csv)
            if plan_path is None:
                self.log_queue.put(f"[plan] report load cancelled for {path}: no plan CSV selected")
                return
            if not self._import_plan_csv_path(plan_path, confirm_replace=False):
                return
        status = str(data.get("status", "")).strip().lower()
        next_row_index = int(data.get("next_row_index", 0))
        next_repeat_index = int(data.get("next_repeat_index", 0))
        if status == "completed":
            next_row_index = 0
            next_repeat_index = 0
        self.resume_row_var.set(next_row_index)
        self.resume_repeat_var.set(next_repeat_index)
        capture_csv = data.get("capture_csv")
        self.resume_capture_path = Path(capture_csv) if capture_csv else None
        self.plan_use_solver_var.set(bool(int(data.get("solver_mode", 0))))
        plan_source_csv = str(data.get("plan_source_csv", "") or "").strip()
        if plan_source_csv:
            self.plan_source_path = Path(plan_source_csv)
        self.plan_report_path = report_path
        self.resume_report_text.set(f"resume: {report_path.name}")
        self.log_queue.put(f"[plan] loaded progress report {path}")

    def toggle_pause_plan(self):
        if not self.running_plan:
            return
        if self.plan_pause_event.is_set():
            self.plan_pause_event.clear()
            self.log_queue.put("[plan] resumed")
        else:
            self.plan_pause_event.set()
            self.log_queue.put("[plan] paused")

    def stop_plan(self):
        if not self.running_plan:
            return
        self.plan_stop_event.set()
        self.plan_pause_event.clear()
        self.log_queue.put("[plan] stop requested")

    def run_plan(self):
        if not self.measurement_rows:
            messagebox.showinfo("Plan", "No plan rows added yet.")
            return
        if self.running_plan:
            messagebox.showinfo("Plan", "A plan is already running.")
            return
        if not self.device.is_connected():
            messagebox.showerror("Plan", "Connect the Teensy serial device first.")
            return

        def worker():
            self.running_plan = True
            self.plan_pause_event.clear()
            self.plan_stop_event.clear()
            plan_has_advanced = any(row.normalized_mode() != "fill8" for row in self.measurement_rows)
            timestamp = int(time.time())
            capture_path = self.resume_capture_path or (self.capture_dir / (f"plan_capture_advanced_{timestamp}.csv" if plan_has_advanced else f"plan_capture_{timestamp}.csv"))
            report_path = self.plan_report_path or capture_path.with_suffix(".progress.json")
            start_row = max(0, int(self.resume_row_var.get()))
            start_repeat = max(0, int(self.resume_repeat_var.get()))
            if start_row >= len(self.measurement_rows):
                self.log_queue.put(f"[plan] resume row {start_row} is outside current plan; restarting from row 0")
                start_row = 0
                start_repeat = 0
                capture_path = self.capture_dir / (f"plan_capture_advanced_{timestamp}.csv" if plan_has_advanced else f"plan_capture_{timestamp}.csv")
                report_path = capture_path.with_suffix(".progress.json")
                self.resume_capture_path = None
                self.plan_report_path = None
                self.resume_row_var.set(0)
                self.resume_repeat_var.set(0)
            solver_mode = 1 if self.plan_use_solver_var.get() else 0
            total_steps = sum(max(1, row.repeats) for row in self.measurement_rows)
            if start_repeat >= max(1, self.measurement_rows[start_row].repeats):
                self.log_queue.put(f"[plan] resume repeat {start_repeat} is outside row {start_row}; restarting that row")
                start_repeat = 0
                self.resume_repeat_var.set(0)
            completed_steps = self._step_offset(start_row, start_repeat)
            next_row_index = start_row
            next_repeat_index = start_repeat
            stopped = False
            try:
                self.log_queue.put(f"[plan] setting solver mode = {solver_mode} before plan run")
                self.device.send_frame(KIND_CAL_REQ, bytes([OP_SET_SOLVER_ENABLED, solver_mode]))
                time.sleep(self.settle_delay_var.get())

                capture_exists = capture_path.exists()
                file_mode = "a" if capture_exists and completed_steps > 0 else "w"
                with capture_path.open(file_mode, newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    if file_mode == "w":
                        writer.writerow(self._plan_capture_header())
                    start_ts = time.time()
                    for idx, row in enumerate(self.measurement_rows):
                        if idx < start_row:
                            continue
                        self._wait_if_paused()
                        if self.plan_stop_event.is_set():
                            stopped = True
                            next_row_index = idx
                            next_repeat_index = start_repeat if idx == start_row else 0
                            break
                        self.root.after(0, lambda i=idx: self.highlight_plan_index(i))
                        self.root.after(0, lambda r=row: self.set_preview_values(r.r, r.g, r.b, r.w, r.bfi_r, r.bfi_g, r.bfi_b, r.bfi_w, r16=r.r16, g16=r.g16, b16=r.b16, w16=r.w16, mode=r.normalized_mode(), lower_r=r.lower_r, lower_g=r.lower_g, lower_b=r.lower_b, lower_w=r.lower_w))
                        self._render_plan_row(row)
                        repeat_start = start_repeat if idx == start_row else 0
                        for rep in range(repeat_start, max(1, row.repeats)):
                            self._wait_if_paused()
                            if self.plan_stop_event.is_set():
                                stopped = True
                                next_row_index = idx
                                next_repeat_index = rep
                                break
                            self._write_progress_report(report_path, capture_csv=capture_path, total_steps=total_steps, completed_steps=completed_steps, status="running", solver_mode=solver_mode, next_row_index=idx, next_repeat_index=rep)
                            result = self._run_measurement()
                            self.last_measurement = result
                            self.root.after(0, lambda res=result: self.measurement_text.set(self._format_measurement_status(res)))
                            writer.writerow(self._plan_capture_row(row, rep, solver_mode, result))
                            f.flush()
                            completed_steps += 1
                            next_repeat_index = rep + 1
                            next_row_index = idx
                            if next_repeat_index >= max(1, row.repeats):
                                next_row_index = idx + 1
                                next_repeat_index = 0
                            processed_steps = max(1, completed_steps - self._step_offset(start_row, start_repeat))
                            elapsed = time.time() - start_ts
                            eta = (elapsed / processed_steps) * (total_steps - completed_steps)
                            self.log_queue.put(f"[plan] {completed_steps}/{total_steps} complete, eta ~ {eta/60.0:.1f} min")
                            self._write_progress_report(report_path, capture_csv=capture_path, total_steps=total_steps, completed_steps=completed_steps, status="running", solver_mode=solver_mode, next_row_index=next_row_index, next_repeat_index=next_repeat_index)
                        if stopped:
                            break
                        start_repeat = 0
                final_status = "stopped" if stopped else "completed"
                self._write_progress_report(report_path, capture_csv=capture_path, total_steps=total_steps, completed_steps=completed_steps, status=final_status, solver_mode=solver_mode, next_row_index=next_row_index, next_repeat_index=next_repeat_index)
                self.resume_capture_path = capture_path
                self.plan_report_path = report_path
                self.resume_row_var.set(next_row_index)
                self.resume_repeat_var.set(next_repeat_index)
                self.log_queue.put(f"[plan] wrote {capture_path}")
                self.log_queue.put(f"[plan] progress report {report_path} ({final_status})")
            finally:
                self.running_plan = False

        threading.Thread(target=worker, daemon=True).start()

    def save_plan_csv(self):
        if not self.measurement_rows:
            messagebox.showinfo("Plan", "No plan rows to save.")
            return
        path = filedialog.asksaveasfilename(initialdir=str(self.capture_dir), defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=GENERIC_PLAN_FIELDS)
            writer.writeheader()
            for row in self.measurement_rows:
                writer.writerow(row.to_dict())
        self.log_queue.put(f"[plan] saved {path}")

    # -----------------------------------------------------------------------
    # LUT Verifier
    # -----------------------------------------------------------------------

    def build_verifier_panel(self, parent: ttk.Frame) -> None:
        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True, padx=4, pady=4)

        # ── Load row ────────────────────────────────────────────────────────
        lut_row = ttk.Frame(box)
        lut_row.pack(fill="x", pady=2)
        ttk.Button(lut_row, text="Load LUT (.npy)", command=self._load_verifier_lut).pack(side="left", padx=4)
        self._verifier_lut_label = tk.StringVar(value="no LUT loaded")
        ttk.Label(lut_row, textvariable=self._verifier_lut_label, anchor="w").pack(side="left", padx=4, fill="x", expand=True)

        sum_row = ttk.Frame(box)
        sum_row.pack(fill="x", pady=2)
        ttk.Button(sum_row, text="Load Summary (.json)", command=self._load_verifier_summary).pack(side="left", padx=4)
        self._verifier_summary_label = tk.StringVar(value="no summary loaded  (expected xy unavailable)")
        ttk.Label(sum_row, textvariable=self._verifier_summary_label, anchor="w").pack(side="left", padx=4, fill="x", expand=True)

        # ── Options / actions ───────────────────────────────────────────────
        opt_row = ttk.Frame(box)
        opt_row.pack(fill="x", pady=4)
        ttk.Label(opt_row, text="dE threshold:").pack(side="left", padx=(4, 2))
        self._verifier_de_var = tk.DoubleVar(value=2.0)
        ttk.Entry(opt_row, textvariable=self._verifier_de_var, width=6).pack(side="left", padx=2)
        ttk.Label(opt_row, text="Patch set:").pack(side="left", padx=(12, 2))
        preset_cb = ttk.Combobox(
            opt_row, textvariable=self._verifier_preset_var,
            values=["quick", "medium", "full"],
            state="readonly", width=8,
        )
        preset_cb.pack(side="left", padx=2)
        self._verifier_patch_count_var = tk.StringVar(value="36 patches")
        ttk.Label(opt_row, textvariable=self._verifier_patch_count_var, width=12, anchor="w").pack(side="left", padx=2)
        ttk.Label(opt_row, text="Interp:").pack(side="left", padx=(12, 2))
        interp_cb = ttk.Combobox(
            opt_row, textvariable=self._verifier_interp_var,
            values=_VERIFIER_INTERPOLATION_CHOICES,
            state="readonly", width=11,
        )
        interp_cb.pack(side="left", padx=2)
        ttk.Label(opt_row, text="Target gamut:").pack(side="left", padx=(12, 2))
        gamut_cb = ttk.Combobox(
            opt_row, textvariable=self._verifier_gamut_var,
            values=_VERIFIER_GAMUT_CHOICES,
            state="readonly", width=14,
        )
        gamut_cb.pack(side="left", padx=2)
        ttk.Label(opt_row, text="Transfer:").pack(side="left", padx=(12, 2))
        transfer_cb = ttk.Combobox(
            opt_row, textvariable=self._verifier_transfer_var,
            values=_VERIFIER_TRANSFER_CHOICES,
            state="readonly", width=7,
        )
        transfer_cb.pack(side="left", padx=2)
        ttk.Checkbutton(opt_row, text="Project OOH xy", variable=self._verifier_project_hull_var).pack(side="left", padx=(10, 2))
        def _update_patch_count(*_):
            patches = _generate_verifier_patches(self._verifier_preset_var.get())
            self._verifier_patch_count_var.set(f"{len(patches)} patches")
        self._verifier_preset_var.trace_add("write", _update_patch_count)
        ttk.Button(opt_row, text="Run Verification", command=self.run_verification).pack(side="left", padx=8)
        ttk.Button(opt_row, text="Stop", command=self.stop_verification).pack(side="left", padx=4)
        ttk.Button(opt_row, text="Export CSV", command=self.export_verifier_csv).pack(side="left", padx=4)
        self._verifier_status_var = tk.StringVar(value="idle")
        ttk.Label(opt_row, textvariable=self._verifier_status_var, width=32, anchor="w").pack(side="left", padx=12)

        # ── Results table ────────────────────────────────────────────────────
        vcols = ("patch", "input_rgb16", "lut_rgbw16", "w_pct",
                 "meas_x", "meas_y", "meas_Y",
                 "exp_x", "exp_y", "proj", "dE", "ok")
        tbl_frame = ttk.Frame(box)
        tbl_frame.pack(fill="both", expand=True, pady=4)
        tbl_frame.columnconfigure(0, weight=1)
        tbl_frame.rowconfigure(0, weight=1)
        self._vtree = ttk.Treeview(tbl_frame, columns=vcols, show="headings", height=20)
        col_cfg = {
            "patch":       (130, "w",      True),
            "input_rgb16": (165, "center", True),
            "lut_rgbw16":  (215, "center", True),
            "w_pct":       ( 58, "center", False),
            "meas_x":      ( 68, "center", False),
            "meas_y":      ( 68, "center", False),
            "meas_Y":      ( 70, "center", False),
            "exp_x":       ( 68, "center", False),
            "exp_y":       ( 68, "center", False),
            "proj":        ( 52, "center", False),
            "dE":          ( 60, "center", False),
            "ok":          ( 36, "center", False),
        }
        for col in vcols:
            w, anchor, stretch = col_cfg[col]
            self._vtree.heading(col, text=col)
            self._vtree.column(col, width=w, anchor=anchor, stretch=stretch)
        self._vtree.tag_configure("pass",    foreground="#1a7a1a")
        self._vtree.tag_configure("fail",    foreground="#cc2200")
        self._vtree.tag_configure("noref",   foreground="#555555")
        self._vtree.tag_configure("running", foreground="#0055aa")
        vscroll = ttk.Scrollbar(tbl_frame, orient="vertical", command=self._vtree.yview)
        hscroll = ttk.Scrollbar(tbl_frame, orient="horizontal", command=self._vtree.xview)
        self._vtree.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
        self._vtree.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")

        # ── Summary line ─────────────────────────────────────────────────────
        self._verifier_summary_line = tk.StringVar(value="")
        ttk.Label(box, textvariable=self._verifier_summary_line, anchor="w").pack(fill="x", padx=4, pady=2)

    def _load_verifier_lut(self) -> None:
        path = filedialog.askopenfilename(
            title="Load RGBW LUT cube (.npy)",
            filetypes=[("NumPy arrays", "*.npy"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            cube = np.load(path)
            if cube.ndim != 4 or cube.shape[3] != 4:
                raise ValueError(f"Expected shape (N,N,N,4), got {cube.shape}")
            if not (cube.shape[0] == cube.shape[1] == cube.shape[2]):
                raise ValueError(f"LUT cube must be cubic, got {cube.shape[:3]}")
            self.verifier_lut = cube.astype(np.uint16)
            N = cube.shape[0]
            self._verifier_lut_label.set(f"{Path(path).name}  ({N}³ cube)")
            self.log_queue.put(f"[verifier] loaded LUT: {path}  shape={cube.shape}  dtype={cube.dtype}")
        except Exception as exc:
            messagebox.showerror("Load LUT failed", str(exc))

    def _load_verifier_summary(self) -> None:
        path = filedialog.askopenfilename(
            title="Load lut_summary.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.verifier_summary = json.loads(Path(path).read_text(encoding="utf-8"))
            sanity = self.verifier_summary.get("basis_sanity", {})
            ref_xy = sanity.get("reference_white_xy", [None, None])
            mode = self.verifier_summary.get("settings", {}).get("target_white_balance_mode", "?")
            if ref_xy[0] is not None:
                label = f"{Path(path).name}  ref_white=({ref_xy[0]:.4f},{ref_xy[1]:.4f})  mode={mode}"
            else:
                label = f"{Path(path).name}  (no reference_white_xy found)"
            self._verifier_summary_label.set(label)
            settings = self.verifier_summary.get("settings", {}) if isinstance(self.verifier_summary, dict) else {}
            summary_gamut = str(
                settings.get("gamut")
                or settings.get("input_gamut")
                or self.verifier_summary.get("gamut", "")
                or ""
            ).strip().lower()
            if summary_gamut in _VERIFIER_GAMUT_MATRICES:
                self._verifier_gamut_var.set(summary_gamut)
            summary_transfer = str(
                settings.get("input_transfer")
                or self.verifier_summary.get("input_transfer", "")
                or ""
            ).strip().lower()
            if summary_transfer in _VERIFIER_TRANSFER_CHOICES:
                self._verifier_transfer_var.set(summary_transfer)
            summary_interp = str(
                settings.get("recommended_interpolation")
                or self.verifier_summary.get("recommended_interpolation", "")
                or ""
            ).strip().lower()
            if summary_interp in _VERIFIER_INTERPOLATION_CHOICES:
                self._verifier_interp_var.set(summary_interp)
            self.log_queue.put(
                f"[verifier] loaded summary: {path}  "
                f"gamut={self._verifier_gamut_var.get()}  "
                f"transfer={self._verifier_transfer_var.get()}  "
                f"interp={self._verifier_interp_var.get()}"
            )
        except Exception as exc:
            messagebox.showerror("Load summary failed", str(exc))

    def _verifier_ref_white(self) -> tuple[float, float]:
        """Reference white (x, y) from the loaded summary, or D65 fallback."""
        ref_xy = self.verifier_summary.get("basis_sanity", {}).get("reference_white_xy", [0.3127, 0.3290])
        return float(ref_xy[0]), float(ref_xy[1])

    def _verifier_expected_xy_from_summary(self, r16: int, g16: int, b16: int) -> tuple[float, float] | None:
        """Legacy/native expected xy derived from lut_summary basis."""
        basis_data = self.verifier_summary.get("basis_xyz_per_q16", {})
        if not all(k in basis_data for k in ("r16", "g16", "b16")):
            return None
        r_b = np.array(basis_data["r16"], dtype=float)
        g_b = np.array(basis_data["g16"], dtype=float)
        b_b = np.array(basis_data["b16"], dtype=float)
        rgb_basis = np.column_stack([r_b, g_b, b_b])

        mode = self.verifier_summary.get("settings", {}).get("target_white_balance_mode", "raw")
        if mode == "reference-white":
            rx, ry = self._verifier_ref_white()
            eq_Y = float((r_b + g_b + b_b)[1])
            Kx = rx / ry
            Kz = (1.0 - rx - ry) / ry
            tgt_white = np.array([Kx * eq_Y, eq_Y, Kz * eq_Y])
            try:
                scales = np.linalg.solve(rgb_basis, tgt_white)
            except np.linalg.LinAlgError:
                return None
            target_basis = rgb_basis @ np.diag(scales)
        else:
            target_basis = rgb_basis

        xyz = target_basis @ np.array([r16, g16, b16], dtype=float)
        s = float(np.sum(xyz))
        if s < 1e-10:
            return None
        return float(xyz[0] / s), float(xyz[1] / s)

    def _verifier_led_hull_xy(self) -> np.ndarray | None:
        """Measured diode RGB hull from lut_summary basis, as [[Rxy],[Gxy],[Bxy]]."""
        basis_data = self.verifier_summary.get("basis_xyz_per_q16", {})
        if not all(k in basis_data for k in ("r16", "g16", "b16")):
            return None
        pts: list[tuple[float, float]] = []
        for key in ("r16", "g16", "b16"):
            xy = _verifier_xyz_to_xy_tuple(np.array(basis_data[key], dtype=float))
            if xy is None:
                return None
            pts.append(xy)
        return np.asarray(pts, dtype=float)

    def _verifier_model_projection_basis_xyz(self) -> dict[str, np.ndarray] | None:
        """Return RGBW basis XYZ for model-style expected-xy projection.

        Preferred source is basis_xyz_per_q16 when it includes w16.  Model LUT
        summaries may instead provide primaries_xy + max_Y, so support that too.
        If only RGB basis data exists, fall back to the older nearest-xy hull
        projection because the RGBW model's RGW/RBW/BGW NNLS cannot be matched
        without W.
        """
        basis_data = self.verifier_summary.get("basis_xyz_per_q16", {})
        key_map = {"R": "r16", "G": "g16", "B": "b16", "W": "w16"}
        if all(k in basis_data for k in key_map.values()):
            try:
                return {
                    ch: np.array(basis_data[key], dtype=float)
                    for ch, key in key_map.items()
                }
            except Exception:
                return None

        prim_xy = self.verifier_summary.get("primaries_xy", {})
        max_y = self.verifier_summary.get("max_Y", {})
        if all(ch in prim_xy and ch in max_y for ch in "RGBW"):
            try:
                return {
                    ch: _verifier_xyY_to_XYZ(tuple(prim_xy[ch]), float(max_y[ch]))
                    for ch in "RGBW"
                }
            except Exception:
                return None

        return None

    def _verifier_expected_xy_info(self, r16: int, g16: int, b16: int) -> dict[str, object]:
        """Expected xy plus gamut/transfer/projection metadata for verifier rows."""
        gamut = self._verifier_gamut_var.get().strip().lower()
        transfer = self._verifier_transfer_var.get().strip().lower()
        project_enabled = bool(self._verifier_project_hull_var.get())

        if gamut == "summary/native":
            raw_xy = self._verifier_expected_xy_from_summary(r16, g16, b16)
            return {
                "xy": raw_xy,
                "raw_xy": raw_xy,
                "projected": False,
                "projection_edge": "native",
                "hull_xy": None,
                "gamut": gamut,
                "transfer": transfer,
                "project_hull_enabled": project_enabled,
            }

        raw_xy = _verifier_expected_xy_for_named_gamut(r16, g16, b16, gamut, transfer)
        projected = False
        edge = "none"
        final_xy = raw_xy
        hull_xy = self._verifier_led_hull_xy()

        if raw_xy is not None and project_enabled:
            model_basis = self._verifier_model_projection_basis_xyz()
            if model_basis is not None:
                model_xy, model_projected, model_edge = _verifier_model_project_xy_for_named_gamut(
                    r16,
                    g16,
                    b16,
                    gamut,
                    transfer,
                    model_basis,
                    self._verifier_ref_white(),
                )
                if model_xy is not None:
                    final_xy = model_xy
                    projected = model_projected
                    edge = model_edge
                elif hull_xy is not None:
                    final_xy, projected, edge = _verifier_project_xy_to_hull(raw_xy, hull_xy)
                    edge = "xy_" + edge
                else:
                    edge = "model_projection_failed"
            elif hull_xy is not None:
                final_xy, projected, edge = _verifier_project_xy_to_hull(raw_xy, hull_xy)
                edge = "xy_" + edge
            else:
                edge = "no_projection_basis"
        elif raw_xy is not None:
            edge = "projection_disabled"

        return {
            "xy": final_xy,
            "raw_xy": raw_xy,
            "projected": projected,
            "projection_edge": edge,
            "hull_xy": hull_xy.tolist() if hull_xy is not None else None,
            "gamut": gamut,
            "transfer": transfer,
            "project_hull_enabled": project_enabled,
        }

    def _verifier_expected_xy(self, r16: int, g16: int, b16: int) -> tuple[float, float] | None:
        """Compatibility wrapper returning the verifier's final expected xy."""
        info = self._verifier_expected_xy_info(r16, g16, b16)
        return info.get("xy")  # type: ignore[return-value]

    def run_verification(self) -> None:
        if self.verifier_lut is None:
            messagebox.showerror("LUT Verifier", "Load a LUT .npy file first.")
            return
        if not self.device.is_connected():
            messagebox.showerror("LUT Verifier", "Connect the Teensy device first.")
            return
        if self.verifier_running:
            messagebox.showinfo("LUT Verifier", "Verification is already running.")
            return

        # Reset results display
        self.verifier_results.clear()
        for iid in self._vtree.get_children():
            self._vtree.delete(iid)
        self._verifier_summary_line.set("")
        self.verifier_stop_event.clear()

        def worker() -> None:
            self.verifier_running = True
            de_threshold = float(self._verifier_de_var.get())
            ref_x, ref_y = self._verifier_ref_white()
            patches = _generate_verifier_patches(self._verifier_preset_var.get())
            total = len(patches)
            try:
                for idx, (name, r16, g16, b16) in enumerate(patches):
                    if self.verifier_stop_event.is_set():
                        break

                    self.root.after(0, lambda s=f"running {idx + 1}/{total}: {name}":
                                    self._verifier_status_var.set(s))

                    # --- Apply LUT ---
                    lr, lg, lb, lw = _lut_lookup(self.verifier_lut, r16, g16, b16, self._verifier_interp_var.get())
                    w_total = lr + lg + lb + lw
                    w_pct = (lw / w_total * 100.0) if w_total > 0 else 0.0

                    # Insert placeholder row while measuring
                    iid = f"vrow_{idx}"
                    self.root.after(0, lambda i=iid, n=name, r=r16, g=g16, b=b16,
                                    lr_=lr, lg_=lg, lb_=lb, lw_=lw, wp=w_pct:
                                    self._vtree.insert(
                                        "", "end", iid=i,
                                        values=(n, f"{r}/{g}/{b}",
                                                f"{lr_}/{lg_}/{lb_}/{lw_}",
                                                f"{wp:.1f}%",
                                                "...", "...", "...",
                                                "-", "-", "-", "-", "⏳"),
                                        tags=("running",),
                                    ))
                    self.root.after(0, lambda i=iid: self._vtree.see(i))

                    # --- Send RGBW fill16 ---
                    payload = bytearray([OP_SET_FILL16])
                    for v in [lr, lg, lb, lw]:
                        payload.extend(self._pack_u16(v))
                    self.device.send_frame(KIND_CAL_REQ, bytes(payload))
                    time.sleep(self.settle_delay_var.get())
                    self.device.send_frame(KIND_CAL_REQ, bytes([OP_COMMIT]))
                    time.sleep(self.settle_delay_var.get())

                    # --- Measure ---
                    result = self.argyll.run_spotread(
                        self.argyll_cmd_var.get().strip(),
                        timeout_s=float(self.timeout_var.get()),
                        send_trigger_newline=bool(self.send_newline_var.get()),
                        cleanup_first=bool(self.cleanup_first_var.get()),
                    )
                    mx = result.get("x")
                    my = result.get("y")
                    mY = result.get("Y")

                    # --- Expected xy from target basis / named gamut, with optional hull projection ---
                    exp_info = self._verifier_expected_xy_info(r16, g16, b16)
                    exp_xy = exp_info.get("xy")  # final verifier target xy
                    exp_raw_xy = exp_info.get("raw_xy")

                    # --- dE ---
                    de_val: float | None = None
                    if exp_xy is not None and mx is not None and my is not None:
                        try:
                            ex, ey = exp_xy  # type: ignore[misc]
                            de_val = _xy_chroma_de(float(mx), float(my),
                                                   float(ex), float(ey),
                                                   ref_x, ref_y)
                        except Exception:
                            pass

                    # --- Classify ---
                    if de_val is None:
                        tag, ok_str = "noref", "—"
                    elif de_val <= de_threshold:
                        tag, ok_str = "pass", "✓"
                    else:
                        tag, ok_str = "fail", "✗"

                    row_data = {
                        "patch":    name,
                        "r16": r16, "g16": g16, "b16": b16,
                        "lut_r16": lr, "lut_g16": lg, "lut_b16": lb, "lut_w16": lw,
                        "w_pct":    w_pct,
                        "meas_x":   mx,  "meas_y": my, "meas_Y": mY,
                        "exp_raw_x": exp_raw_xy[0] if exp_raw_xy else None,  # type: ignore[index]
                        "exp_raw_y": exp_raw_xy[1] if exp_raw_xy else None,  # type: ignore[index]
                        "exp_x":    exp_xy[0] if exp_xy else None,           # type: ignore[index]
                        "exp_y":    exp_xy[1] if exp_xy else None,           # type: ignore[index]
                        "exp_projected_to_hull": bool(exp_info.get("projected", False)),
                        "exp_projection_edge": str(exp_info.get("projection_edge", "")),
                        "exp_project_hull_enabled": bool(exp_info.get("project_hull_enabled", False)),
                        "expected_hull_xy": json.dumps(exp_info.get("hull_xy")) if exp_info.get("hull_xy") is not None else "",
                        "dE":       de_val,
                        "ok":       ok_str,
                        "tag":      tag,
                        "interpolation": self._verifier_interp_var.get(),
                        "expected_gamut": self._verifier_gamut_var.get(),
                        "verification_gamut": self._verifier_gamut_var.get(),
                        "input_transfer": self._verifier_transfer_var.get(),
                    }
                    self.verifier_results.append(row_data)
                    self.last_measurement = result
                    self.root.after(0, lambda res=result:
                                    self.measurement_text.set(
                                        f"last measurement: Y={res.get('Y')} "
                                        f"x={res.get('x')} y={res.get('y')}"))

                    def _update_row(i=iid, rd=row_data, tg=tag) -> None:
                        mx_ = f"{rd['meas_x']:.4f}" if rd['meas_x'] is not None else "err"
                        my_ = f"{rd['meas_y']:.4f}" if rd['meas_y'] is not None else "err"
                        mY_ = f"{rd['meas_Y']:.2f}" if rd['meas_Y'] is not None else "err"
                        ex_ = f"{rd['exp_x']:.4f}" if rd['exp_x'] is not None else "-"
                        ey_ = f"{rd['exp_y']:.4f}" if rd['exp_y'] is not None else "-"
                        de_ = f"{rd['dE']:.3f}"    if rd['dE']   is not None else "-"
                        proj_ = str(rd.get("exp_projection_edge", "")) if rd.get("exp_projected_to_hull") else ""
                        self._vtree.item(i, values=(
                            rd["patch"],
                            f"{rd['r16']}/{rd['g16']}/{rd['b16']}",
                            f"{rd['lut_r16']}/{rd['lut_g16']}/{rd['lut_b16']}/{rd['lut_w16']}",
                            f"{rd['w_pct']:.1f}%",
                            mx_, my_, mY_, ex_, ey_, proj_, de_, rd["ok"],
                        ), tags=(tg,))

                    self.root.after(0, _update_row)

                    xy_str = (f"xy=({mx:.4f},{my:.4f})" if mx is not None else "xy=err")
                    if de_val is not None and exp_xy is not None:
                        ex, ey = exp_xy  # type: ignore[misc]
                        proj_tag = ""
                        if bool(exp_info.get("projected", False)):
                            proj_tag = f" projected={exp_info.get('projection_edge')}"
                        de_str = f"  exp=({float(ex):.4f},{float(ey):.4f}){proj_tag}  dE={de_val:.3f}"
                    else:
                        de_str = ""
                    self.log_queue.put(
                        f"[verifier] {idx + 1}/{total} {name}: "
                        f"interp={self._verifier_interp_var.get()} "
                        f"gamut={self._verifier_gamut_var.get()} "
                        f"transfer={self._verifier_transfer_var.get()} "
                        f"project_hull={int(self._verifier_project_hull_var.get())} "
                        f"W={lw} ({w_pct:.1f}%)  {xy_str}{de_str}  [{ok_str}]"
                    )

            finally:
                self.verifier_running = False

                def _finalize() -> None:
                    n_pass = sum(1 for r in self.verifier_results if r["tag"] == "pass")
                    n_fail = sum(1 for r in self.verifier_results if r["tag"] == "fail")
                    n_ref  = n_pass + n_fail
                    max_de = max((r["dE"] for r in self.verifier_results if r["dE"] is not None),
                                 default=None)
                    n_projected = sum(1 for r in self.verifier_results if r.get("exp_projected_to_hull"))
                    summary = (f"{len(self.verifier_results)} patches measured"
                               f"  |  {self._verifier_interp_var.get()}  "
                               f"{self._verifier_gamut_var.get()}  "
                               f"{self._verifier_transfer_var.get()}  "
                               f"project_hull={int(self._verifier_project_hull_var.get())}"
                               f" projected={n_projected}")
                    if n_ref > 0:
                        summary += (f"  |  {n_pass} pass / {n_fail} fail"
                                    f"  (dE ≤ {self._verifier_de_var.get():.1f})")
                        if max_de is not None:
                            summary += f"  |  max dE = {max_de:.3f}"
                    self._verifier_summary_line.set(summary)
                    status = "stopped" if self.verifier_stop_event.is_set() else "done"
                    self._verifier_status_var.set(status)

                self.root.after(0, _finalize)

        threading.Thread(target=worker, daemon=True).start()

    def stop_verification(self) -> None:
        if not self.verifier_running:
            return
        self.verifier_stop_event.set()
        self.log_queue.put("[verifier] stop requested")

    def export_verifier_csv(self) -> None:
        if not self.verifier_results:
            messagebox.showinfo("LUT Verifier", "No results to export yet.")
            return
        path = filedialog.asksaveasfilename(
            initialdir=str(self.capture_dir),
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        fieldnames = [
            "patch", "r16", "g16", "b16",
            "lut_r16", "lut_g16", "lut_b16", "lut_w16", "w_pct",
            "meas_x", "meas_y", "meas_Y",
            "exp_raw_x", "exp_raw_y",
            "exp_x", "exp_y",
            "exp_projected_to_hull", "exp_projection_edge",
            "exp_project_hull_enabled", "expected_hull_xy",
            "dE", "ok",
            "interpolation", "expected_gamut", "verification_gamut", "input_transfer",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.verifier_results)
        self.log_queue.put(f"[verifier] exported {len(self.verifier_results)} rows to {path}")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()