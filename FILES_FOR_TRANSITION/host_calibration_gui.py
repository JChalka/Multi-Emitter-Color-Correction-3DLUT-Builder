#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import os
import queue
import re
import shlex
import shutil
import signal
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

APP_TITLE = "Temporal RGBW Calibration Host v7.6.0"
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
OP_SET_ANALYTICAL_RGB16 = 0x2C
OP_GET_DIODE_PROFILE = 0x2D
OP_SET_OUTPUT_MODE = 0x2E
# Temporal direct companion opcode for RGBWW direct requests.
# Temporal direct RGBW requests use OP_SET_FILL16.
OP_SET_DIRECT_RGBWW16 = 0x30
OP_SET_DIODE_PROFILE = 0x31
OP_GET_INPUT_GAMUT = 0x32
OP_SET_INPUT_GAMUT = 0x33

STATUS_OK = 0x00
STATUS_BAD_PAYLOAD = 0x01
STATUS_BAD_OPCODE = 0x02
STATUS_SOLVE_FAILED = 0x03
STATUS_UNSUPPORTED_OUTPUT_MODE = 0x04
STATUS_BAD_PROFILE = 0x05
STATUS_UNSUPPORTED_MODEL = 0x06
STATUS_UNSUPPORTED_GAMUT = 0x07
STATUS_NAMES = {
    STATUS_OK: "OK",
    STATUS_BAD_PAYLOAD: "BAD_PAYLOAD",
    STATUS_BAD_OPCODE: "BAD_OPCODE",
    STATUS_SOLVE_FAILED: "SOLVE_FAILED",
    STATUS_UNSUPPORTED_OUTPUT_MODE: "UNSUPPORTED_OUTPUT_MODE",
    STATUS_BAD_PROFILE: "BAD_PROFILE",
    STATUS_UNSUPPORTED_MODEL: "UNSUPPORTED_MODEL",
    STATUS_UNSUPPORTED_GAMUT: "UNSUPPORTED_GAMUT",
}

OUTPUT_MODE_RGB = 0x00
OUTPUT_MODE_RGBW = 0x01
OUTPUT_MODE_RGBWW = 0x02
OUTPUT_MODE_CHOICES = {
    "RGB": OUTPUT_MODE_RGB,
    "RGBW": OUTPUT_MODE_RGBW,
    "RGBWW/RGBCCT": OUTPUT_MODE_RGBWW,
}
OUTPUT_MODE_NAMES = {value: key for key, value in OUTPUT_MODE_CHOICES.items()}

PHASE_MODE_AUTO = 0
PHASE_MODE_MANUAL = 1

ANALYTICAL_MODEL_RGBW_STRICT_SUB_GAMUT = 0
ANALYTICAL_MODEL_RGBW_LP_LEGACY = 1
ANALYTICAL_MODEL_RGBWW_OVERDRIVE = 2
ANALYTICAL_MODEL_RGB_DIRECT_STUB = 3
ANALYTICAL_MODEL_RGBWW_STRICT_STUB = 4
ANALYTICAL_MODEL_CHOICES = {
    "rgbw_strict_sub_gamut": ANALYTICAL_MODEL_RGBW_STRICT_SUB_GAMUT,
    "rgbw_lp_legacy": ANALYTICAL_MODEL_RGBW_LP_LEGACY,
    "rgbww_overdrive": ANALYTICAL_MODEL_RGBWW_OVERDRIVE,
    "rgb_direct_stub": ANALYTICAL_MODEL_RGB_DIRECT_STUB,
    "rgbww_strict_stub": ANALYTICAL_MODEL_RGBWW_STRICT_STUB,
}
DUAL_EDGE_POLICY_Y_CORRECT_CLIP = 0
DUAL_EDGE_POLICY_ROLLOFF_AFTER_CLIP = 1
DUAL_EDGE_POLICY_SCALE_TO_FULL_ENDPOINT = 2
DUAL_EDGE_POLICY_CHOICES = {
    "y_correct_clip": DUAL_EDGE_POLICY_Y_CORRECT_CLIP,
    "rolloff_after_clip": DUAL_EDGE_POLICY_ROLLOFF_AFTER_CLIP,
    "scale_to_full_endpoint": DUAL_EDGE_POLICY_SCALE_TO_FULL_ENDPOINT,
}
DUAL_EDGE_POLICY_NAMES = {
    value: key for key, value in DUAL_EDGE_POLICY_CHOICES.items()
}
INPUT_GAMUT_NATIVE = 0
INPUT_GAMUT_REC709 = 1
INPUT_GAMUT_REC2020 = 2
INPUT_GAMUT_DCI_P3_D65 = 3
INPUT_GAMUT_DCI_P3_D60 = 4
FASTLED_INPUT_GAMUT_CHOICES = {
    "summary/native": INPUT_GAMUT_NATIVE,
    "rec709": INPUT_GAMUT_REC709,
    "rec2020": INPUT_GAMUT_REC2020,
    "dci-p3": INPUT_GAMUT_DCI_P3_D65,
    "dci-p3-d65": INPUT_GAMUT_DCI_P3_D65,
    "dci-p3-d60": INPUT_GAMUT_DCI_P3_D60,
}
FASTLED_INPUT_GAMUT_NAMES = {
    INPUT_GAMUT_NATIVE: "summary/native",
    INPUT_GAMUT_REC709: "rec709",
    INPUT_GAMUT_REC2020: "rec2020",
    INPUT_GAMUT_DCI_P3_D65: "dci-p3",
    INPUT_GAMUT_DCI_P3_D60: "dci-p3-d60",
}
ANALYTICAL_SOLVE_PATHS = {
    0: "none",
    1: "strict_sub_gamut",
    2: "lp_legacy",
    3: "strict_failed",
    4: "rgbww_overdrive",
    5: "unavailable",
}
VERIFIER_OUTPUT_SOURCES = ["3D LUT", "FastLED analytical MCU"]
STRIP_TYPE_CHOICES = {
    "RGB": 3,
    "RGBW": 4,
    "RGBWW/RGBCCT": 5,
}
CUBE_OUTPUT_TYPE_CHOICES = {
    "auto": 0,
    "RGB": 3,
    "RGBW": 4,
    "RGBWW/RGBCCT": 5,
}

MAX_BFI = 4
BLEND_CYCLE_LENGTH = MAX_BFI + 1
MAX_BLEND_CYCLE_LENGTH = 60
PHASE_CONTROL_MAX = MAX_BLEND_CYCLE_LENGTH - 1

GENERIC_PLAN_FIELDS = [
    "name", "mode", "channels", "repeats",
    "r", "g", "b", "w", "w1", "w2",
    "lower_r", "lower_g", "lower_b", "lower_w", "lower_w1", "lower_w2",
    "upper_r", "upper_g", "upper_b", "upper_w", "upper_w1", "upper_w2",
    "r16", "g16", "b16", "w16", "w1_16", "w2_16",
    "bfi_r", "bfi_g", "bfi_b", "bfi_w", "bfi_w1", "bfi_w2",
    "use_fill16",
]

PLAN_CHANNEL_LABELS = {
    3: "RGB",
    4: "RGBW",
    5: "RGBWW/RGBCCT",
}


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
PROCESS_SIGTERM = getattr(signal, "SIGTERM", signal.SIGINT)
PROCESS_SIGKILL = getattr(signal, "SIGKILL", PROCESS_SIGTERM)



SPOTREAD_EXECUTABLE_NAMES = {"spotread", "spotread.exe"}


def _split_command_for_platform(command: str) -> list[str]:
    """Split a user-entered command using host-appropriate quoting rules.

    The old Windows-tested path used ``posix=False`` unconditionally.  That is
    correct for quoted Windows paths, but on Linux it keeps quote characters as
    literal argv bytes.  Linux/macOS need POSIX shell quoting rules so entries
    such as ``"/usr/local/bin/spotread" -x -O`` turn into a usable argv list.
    """
    return shlex.split(command, posix=(os.name != "nt"))


def _is_spotread_executable(argv0: str) -> bool:
    exe_name = Path(str(argv0).strip().strip('"')).name.lower()
    return exe_name in SPOTREAD_EXECUTABLE_NAMES


def _spotread_args_with_one_shot(command: str) -> list[str]:
    """Return argv for spotread and ensure scripted reads use one-shot ``-O``."""
    command = (command or DEFAULT_SPOTREAD_COMMAND).strip() or DEFAULT_SPOTREAD_COMMAND
    args = _split_command_for_platform(command)
    if args and _is_spotread_executable(args[0]):
        has_one_shot = any(a == "-O" or a.startswith("-O") for a in args[1:])
        if not has_one_shot:
            args.append("-O")
    return args


def _format_command_for_log(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def _spotread_command_with_one_shot(command: str) -> str:
    """Ensure scripted spotread captures keep the required one-shot -O flag."""
    try:
        return _format_command_for_log(_spotread_args_with_one_shot(command))
    except Exception:
        return (command or DEFAULT_SPOTREAD_COMMAND).strip() or DEFAULT_SPOTREAD_COMMAND


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
    # Super-Dark primary (Test lowest possible code values)
    ("super_dark_red",     1,  0,      0),
    ("super_dark_green",   0,      1,  0),
    ("super_dark_blue",    0,      0,  1),
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


def _clamp_lut_result(values: np.ndarray) -> tuple[int, ...]:
    arr = np.asarray(values, dtype=float)
    arr = np.clip(np.rint(arr), 0.0, 65535.0).astype(np.uint16)
    return tuple(int(v) for v in arr.tolist())


def _trilinear_lut_lookup(
    cube: np.ndarray, r16: int, g16: int, b16: int
) -> tuple[int, ...]:
    """Trilinear interpolation in an (N,N,N,C) uint16 LUT cube (axis 0..65535)."""
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
) -> tuple[int, ...]:
    """Tetrahedral interpolation in an (N,N,N,C) uint16 LUT cube.

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
) -> tuple[int, ...]:
    mode = (interpolation or "tetrahedral").strip().lower()
    if mode == "trilinear":
        return _trilinear_lut_lookup(cube, r16, g16, b16)
    if mode == "tetrahedral":
        return _tetrahedral_lut_lookup(cube, r16, g16, b16)
    raise ValueError(f"Unsupported LUT interpolation mode: {interpolation!r}")



_VERIFIER_D65_XY = (0.3127, 0.3290)
_VERIFIER_D60_XY = (0.32168, 0.33767)
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
    "dci-p3-d60": {
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
_VERIFIER_GAMUT_WHITE_XY = {
    "dci-p3-d60": _VERIFIER_D60_XY,
}
_VERIFIER_GAMUT_CHOICES = ["summary/native", "rec709", "rec2020", "dci-p3", "dci-p3-d60", "adobe-rgb"]
_VERIFIER_TRANSFER_CHOICES = ["linear", "gamut"]
_VERIFIER_INTERPOLATION_CHOICES = ["tetrahedral", "trilinear"]

# Reference-white presets used by verifier Lab/xy chroma dE and model-style
# named-gamut projection. ``Summary/default`` uses the loaded LUT summary when
# available and falls back to D65 otherwise. ``Custom`` uses the editable x/y
# fields in the verifier UI.
_VERIFIER_REFERENCE_WHITE_PRESETS: dict[str, tuple[float, float] | None] = {
    "Summary/default": None,
    "D65": (0.3127, 0.3290),
    "DCI white": (0.3140, 0.3510),
    "E / equal-energy": (1.0 / 3.0, 1.0 / 3.0),
    "Custom": None,
}


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
    name: _verifier_build_rgb_to_xyz_matrix(
        primaries,
        _VERIFIER_GAMUT_WHITE_XY.get(name, _VERIFIER_D65_XY),
    )
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
    if gamut in {"dci-p3", "dci-p3-d60"}:
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


def _u32_be_from_payload(payload: bytes | bytearray, offset: int) -> int:
    return ((int(payload[offset]) << 24) |
            (int(payload[offset + 1]) << 16) |
            (int(payload[offset + 2]) << 8) |
            int(payload[offset + 3]))


def _pack_u32_be_q1e6(value: float) -> bytes:
    scaled = int(round(float(value) * 1000000.0))
    scaled = max(0, min(0xFFFFFFFF, scaled))
    return bytes([
        (scaled >> 24) & 0xFF,
        (scaled >> 16) & 0xFF,
        (scaled >> 8) & 0xFF,
        scaled & 0xFF,
    ])


def _status_name(status: object) -> str:
    try:
        value = int(status)
    except Exception:
        return str(status)
    return STATUS_NAMES.get(value, f"0x{value:02X}")


def _decode_diode_profile_payload(payload: bytes | bytearray) -> dict[str, object] | None:
    """Decode Teensy DiodeProfile payloads returned by OP_GET_DIODE_PROFILE.

    Supported layouts:
      compact:  [op,status,'D','P','R','F',version,format,12x u32be q1e6]
      extended: normal 51-byte cal response followed by the same DPRF block
    """
    data = bytes(payload)
    magic_offset: int | None = None
    for off in (2, 51):
        if len(data) >= off + 8 and data[off:off + 4] == b"DPRF":
            magic_offset = off
            break
    if magic_offset is None:
        return None
    version = int(data[magic_offset + 4])
    fmt = int(data[magic_offset + 5])
    if version != 1:
        return None
    if fmt == 1:
        channel_order = list("RGBW")
    elif fmt == 2:
        channel_order = ["R", "G", "B", "WW", "WC"]
    elif fmt == 3:
        channel_order = list("RGB")
    else:
        return None

    values_offset = magic_offset + 6
    required_bytes = 12 * len(channel_order)
    if len(data) < values_offset + required_bytes:
        return None

    primaries_xy: dict[str, list[float]] = {}
    relative_y: dict[str, float] = {}
    off = values_offset
    for ch in channel_order:
        x = _u32_be_from_payload(data, off) / 1000000.0; off += 4
        y = _u32_be_from_payload(data, off) / 1000000.0; off += 4
        rel_y = _u32_be_from_payload(data, off) / 1000000.0; off += 4
        primaries_xy[ch] = [float(x), float(y)]
        relative_y[ch] = float(rel_y)
    return {
        "version": version,
        "format": (
            "u32be_q1e6_rgbw" if fmt == 1
            else "u32be_q1e6_rgbww" if fmt == 2
            else "u32be_q1e6_rgb"
        ),
        "format_id": fmt,
        "primaries_xy": primaries_xy,
        "relative_y": relative_y,
        "channel_order": channel_order,
        "source": "teensy_diode_profile",
    }


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
    w2: int = 0
    bfi_w2: int = 0
    lower_r: int = 0
    lower_g: int = 0
    lower_b: int = 0
    lower_w: int = 0
    lower_w2: int = 0
    upper_r: int = 0
    upper_g: int = 0
    upper_b: int = 0
    upper_w: int = 0
    upper_w2: int = 0
    r16: int = 0
    g16: int = 0
    b16: int = 0
    w16: int = 0
    w2_16: int = 0
    use_fill16: bool = False
    mode: str = "fill8"
    channels: int = 4

    def normalized_mode(self) -> str:
        if self.mode == "blend8":
            return "blend8"
        if self.use_fill16 or self.mode == "fill16":
            return "fill16"
        return "fill8"

    def channel_count(self) -> int:
        try:
            count = int(self.channels)
        except Exception:
            count = 0
        if count in (3, 4, 5):
            return count
        if any(int(v) != 0 for v in (self.w2, self.bfi_w2, self.lower_w2, self.upper_w2, self.w2_16)):
            return 5
        if any(int(v) != 0 for v in (self.w, self.bfi_w, self.lower_w, self.upper_w, self.w16)):
            return 4
        return 3

    def channel_label(self) -> str:
        return PLAN_CHANNEL_LABELS.get(self.channel_count(), f"{self.channel_count()}ch")

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["mode"] = self.normalized_mode()
        data["channels"] = self.channel_count()
        data["use_fill16"] = int(self.normalized_mode() == "fill16")
        data["w1"] = self.w
        data["lower_w1"] = self.lower_w
        data["upper_w1"] = self.upper_w
        data["w1_16"] = self.w16
        data["bfi_w1"] = self.bfi_w
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
            payload_len = len(payload)
            msg["payload_len"] = payload_len
            op = msg.get("op")
            is_diode_profile_response = op in (OP_GET_DIODE_PROFILE, OP_SET_DIODE_PROFILE)
            is_input_gamut_response = op in (OP_GET_INPUT_GAMUT, OP_SET_INPUT_GAMUT)
            is_analytical_state = (payload_len >= 64) or (payload_len >= 51 and not is_diode_profile_response)
            if is_input_gamut_response and payload_len >= 4:
                msg["input_gamut"] = payload[2]
                msg["supported_input_gamut_bitmask"] = payload[3]
                msg["input_gamut_response_version"] = payload[4] if payload_len > 4 else None

            # FastLED analytical verifier response, old 51/52-byte layout or new 64-byte layout.
            if is_analytical_state and payload_len >= 26:
                msg["solved_rgbw16"] = [
                    (payload[18] << 8) | payload[19],
                    (payload[20] << 8) | payload[21],
                    (payload[22] << 8) | payload[23],
                    (payload[24] << 8) | payload[25],
                ]
            if is_analytical_state and payload_len >= 32:
                msg["input_rgb16"] = [
                    (payload[26] << 8) | payload[27],
                    (payload[28] << 8) | payload[29],
                    (payload[30] << 8) | payload[31],
                ]
            if is_analytical_state and payload_len >= 33:
                msg["analytical_model"] = payload[32]
            if is_analytical_state and payload_len >= 34:
                msg["analytical_solve_path"] = payload[33]
            if is_analytical_state and payload_len >= 51:
                msg["analytical_strict_ok"] = payload[34]
                msg["analytical_strict_rgbw16"] = [
                    (payload[35] << 8) | payload[36],
                    (payload[37] << 8) | payload[38],
                    (payload[39] << 8) | payload[40],
                    (payload[41] << 8) | payload[42],
                ]
                msg["analytical_lp_rgbw16"] = [
                    (payload[43] << 8) | payload[44],
                    (payload[45] << 8) | payload[46],
                    (payload[47] << 8) | payload[48],
                    (payload[49] << 8) | payload[50],
                ]
            if is_analytical_state and payload_len >= 52:
                msg["analytical_dual_edge_policy"] = payload[51]
            if is_analytical_state and payload_len >= 64:
                msg["active_output_mode"] = payload[52]
                msg["supported_output_mode_bitmask"] = payload[53]
                msg["physical_output_channel_count"] = payload[54]
                msg["active_logical_channel_count"] = payload[55]
                msg["last_input_w16"] = (payload[56] << 8) | payload[57]
                msg["last_input_w2_16"] = (payload[58] << 8) | payload[59]
                msg["last_solved_w2_16"] = (payload[60] << 8) | payload[61]
                solved = msg.get("solved_rgbw16")
                if isinstance(solved, list) and len(solved) == 4:
                    logical_channels = int(msg.get("active_logical_channel_count", 4))
                    solved_channels = list(solved)
                    if logical_channels >= 5:
                        solved_channels.append(int(msg["last_solved_w2_16"]))
                    msg["solved_channels16"] = solved_channels
                msg["fold_stub_flag"] = payload[62]
                msg["response_extension_version"] = payload[63]

            # Teensy temporal calibration companion Phase-8 state response.
            if (not is_analytical_state) and (not is_diode_profile_response) and (not is_input_gamut_response) and payload_len >= 30:
                msg["solver_enabled"] = payload[17] if payload_len > 17 else None
                msg["active_output_mode"] = payload[18]
                msg["supported_output_mode_bitmask"] = payload[19]
                msg["physical_output_channel_count"] = payload[20]
                msg["active_logical_channel_count"] = payload[21]
                msg["compile_time_target_output_mode"] = payload[22] if payload_len > 22 else None
                msg["fold_stub_flag"] = payload[23] if payload_len > 23 else None
                msg["response_extension_version"] = payload[29]

            if is_diode_profile_response:
                diode_profile = _decode_diode_profile_payload(payload)
                if diode_profile is not None:
                    msg["diode_profile"] = diode_profile
            if msg["op"] is not None and msg["status"] is not None:
                status_text = _status_name(msg["status"])
                mode_text = ""
                if "active_output_mode" in msg:
                    mode_name = OUTPUT_MODE_NAMES.get(int(msg["active_output_mode"]), f"mode{msg['active_output_mode']}")
                    supported = int(msg.get("supported_output_mode_bitmask", 0))
                    physical = msg.get("physical_output_channel_count")
                    logical = msg.get("active_logical_channel_count")
                    mode_text = f" mode={mode_name} supported=0x{supported:02X} phys={physical} logical={logical}"
                if is_input_gamut_response and "input_gamut" in msg:
                    gamut_name = FASTLED_INPUT_GAMUT_NAMES.get(int(msg["input_gamut"]), f"gamut{msg['input_gamut']}")
                    self._log(f"[rx] input gamut op=0x{msg['op']:02X} status={status_text} gamut={gamut_name}")
                elif is_diode_profile_response and "diode_profile" in msg:
                    self._log(f"[rx] diode profile op=0x{msg['op']:02X} status={status_text}{mode_text}")
                else:
                    self._log(f"[rx] cal op=0x{msg['op']:02X} status={status_text} phase={msg['phase']}{mode_text}")
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
            subprocess.run(
                ["taskkill", "/F", "/IM", "spotread.exe"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # Prefer exact process-name matching on POSIX targets.  The old
            # ``pkill -f spotread`` worked, but it was broader than necessary
            # and could match wrapper command lines.
            pkill = shutil.which("pkill")
            killall = shutil.which("killall")
            if pkill:
                subprocess.run([pkill, "-x", "spotread"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif killall:
                subprocess.run([killall, "spotread"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                self.log_queue.put("[argyll] cleanup skipped: pkill/killall not found")
        time.sleep(0.75)

    @staticmethod
    def _popen_kwargs() -> dict[str, object]:
        kwargs: dict[str, object] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "errors": "replace",
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            # Put spotread in its own process group so timeout/abort can cleanly
            # terminate the whole ArgyllCMS child tree on Linux/macOS.
            kwargs["start_new_session"] = True
        return kwargs

    @staticmethod
    def _signal_process_group(proc: subprocess.Popen, sig: int) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            if sig == PROCESS_SIGKILL:
                proc.kill()
            else:
                proc.terminate()
            return
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except ProcessLookupError:
            pass
        except Exception:
            if sig == PROCESS_SIGKILL:
                proc.kill()
            else:
                proc.terminate()

    def _terminate_process(self, proc: subprocess.Popen, timeout_s: float = 2.0) -> None:
        self._signal_process_group(proc, PROCESS_SIGTERM)
        try:
            proc.wait(timeout=timeout_s)
            return
        except Exception:
            pass
        self._signal_process_group(proc, PROCESS_SIGKILL)
        try:
            proc.wait(timeout=timeout_s)
        except Exception:
            pass

    def abort_active(self):
        with self.lock:
            proc = self.active_proc
            self.active_proc = None
        if proc is None:
            self.log_queue.put("[argyll] no active process")
            return
        self.log_queue.put(f"[argyll] aborting pid={proc.pid}")
        self._terminate_process(proc)
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
        started = time.time()
        stdout = ""
        stderr = ""
        timed_out = False
        args: list[str] = []
        command_string = str(command or DEFAULT_SPOTREAD_COMMAND)

        try:
            args = _spotread_args_with_one_shot(command_string)
            command_string = _format_command_for_log(args)
        except Exception as exc:
            stderr = f"Unable to parse spotread command {command_string!r}: {exc}"
            self.log_queue.put(f"[argyll] {stderr}")
            return {
                "ok": False,
                "returncode": None,
                "elapsed_s": time.time() - started,
                "stdout": "",
                "stderr": stderr,
                "timed_out": False,
                "pid": None,
                "command": [],
                "command_string": command_string,
                "source": "spotread",
                "measurement_format": "unknown",
                "measurement_columns": ["ok", "returncode", "elapsed_s", "timed_out"],
            }

        if cleanup_first:
            self.cleanup_stale_processes()

        self.log_queue.put(f"[argyll] running: {args!r}")
        if args and _is_spotread_executable(args[0]):
            exe = args[0]
            has_path = Path(exe).is_absolute() or os.sep in exe or (os.altsep is not None and os.altsep in exe)
            if not has_path and shutil.which(exe) is None:
                self.log_queue.put(f"[argyll] warning: {exe!r} not found on PATH")

        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen(args, **self._popen_kwargs())
        except FileNotFoundError as exc:
            stderr = str(exc)
            self.log_queue.put(f"[argyll] launch failed: {stderr}")
            return {
                "ok": False,
                "returncode": None,
                "elapsed_s": time.time() - started,
                "stdout": "",
                "stderr": stderr,
                "timed_out": False,
                "pid": None,
                "command": args,
                "command_string": command_string,
                "source": "spotread",
                "measurement_format": "unknown",
                "measurement_columns": ["ok", "returncode", "elapsed_s", "timed_out"],
            }
        except Exception as exc:
            stderr = f"{type(exc).__name__}: {exc}"
            self.log_queue.put(f"[argyll] launch failed: {stderr}")
            return {
                "ok": False,
                "returncode": None,
                "elapsed_s": time.time() - started,
                "stdout": "",
                "stderr": stderr,
                "timed_out": False,
                "pid": None,
                "command": args,
                "command_string": command_string,
                "source": "spotread",
                "measurement_format": "unknown",
                "measurement_columns": ["ok", "returncode", "elapsed_s", "timed_out"],
            }

        with self.lock:
            self.active_proc = proc

        try:
            if send_trigger_newline and proc.stdin is not None:
                time.sleep(0.2)
                try:
                    proc.stdin.write("\n")
                    proc.stdin.flush()
                    self.log_queue.put("[argyll] sent newline trigger")
                except (BrokenPipeError, OSError) as exc:
                    # Some spotread modes/platforms may exit before stdin is
                    # consumed.  Treat that as process output/status, not a GUI
                    # exception.
                    self.log_queue.put(f"[argyll] stdin trigger skipped: {exc}")
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            self.log_queue.put("[argyll] timeout expired, terminating")
            self._signal_process_group(proc, PROCESS_SIGTERM)
            try:
                stdout, stderr = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self._signal_process_group(proc, PROCESS_SIGKILL)
                try:
                    stdout, stderr = proc.communicate(timeout=3)
                except Exception:
                    stdout = stdout or ""
                    stderr = stderr or ""
            except Exception:
                stderr = (stderr or "") + "\n[host] communicate after terminate failed"
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
            "command_string": command_string,
            "source": "spotread",
        }

        # ArgyllCMS normally prints readings on stdout, but Linux builds and
        # wrappers can emit useful status/measurement text on stderr.  Parse both
        # while still preserving the original streams separately in the capture.
        out_text = "\n".join(part for part in (result["stdout"], result["stderr"]) if part)

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
        self.cal_response_queue: queue.Queue[dict[str, object]] = queue.Queue()
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
        self._last_teensy_diode_profile: dict[str, object] | None = None
        # --- UDP capture bridge state ---
        self.udp_capture_socket: socket.socket | None = None
        self.udp_capture_thread: threading.Thread | None = None
        self.udp_capture_stop_event = threading.Event()
        self.udp_capture_lock = threading.Lock()
        # --- LUT verifier state ---
        self.verifier_lut: np.ndarray | None = None
        self.verifier_lut_channel_count: int | None = None
        self.verifier_summary: dict = {}
        self.verifier_results: list[dict] = []
        self.verifier_running: bool = False
        self.verifier_stop_event = threading.Event()
        self._verifier_preset_var = tk.StringVar(value="quick")
        self._verifier_interp_var = tk.StringVar(value="tetrahedral")
        self._verifier_output_source_var = tk.StringVar(value=VERIFIER_OUTPUT_SOURCES[0])
        self._verifier_strip_type_var = tk.StringVar(value="RGBW")
        self._verifier_cube_output_type_var = tk.StringVar(value="auto")
        self._verifier_analytical_model_var = tk.StringVar(value="rgbw_strict_sub_gamut")
        self._verifier_dual_edge_policy_var = tk.StringVar(value="y_correct_clip")
        self._verifier_gamut_var = tk.StringVar(value="summary/native")
        self._verifier_transfer_var = tk.StringVar(value="linear")
        self._verifier_project_hull_var = tk.BooleanVar(value=True)
        self._verifier_ref_white_preset_var = tk.StringVar(value="D65")
        self._verifier_ref_white_x_var = tk.DoubleVar(value=_VERIFIER_D65_XY[0])
        self._verifier_ref_white_y_var = tk.DoubleVar(value=_VERIFIER_D65_XY[1])
        self._verifier_auto_measure_basis_var = tk.BooleanVar(value=True)
        self._device_output_mode_var = tk.StringVar(value="RGBW")
        self._device_output_mode_status_var = tk.StringVar(value="device mode: unknown")
        self._fastled_input_gamut_status_var = tk.StringVar(value="FastLED input gamut: unknown")
        self.w2_16_var = tk.IntVar(value=0)
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

        mode_ctl_row = ttk.Frame(controls)
        mode_ctl_row.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(mode_ctl_row, text="Device output mode").pack(side="left")
        mode_combo = ttk.Combobox(
            mode_ctl_row, textvariable=self._device_output_mode_var,
            values=list(OUTPUT_MODE_CHOICES.keys()), state="readonly", width=14,
        )
        mode_combo.pack(side="left", padx=4)
        ttk.Button(mode_ctl_row, text="Apply output mode", command=self.send_output_mode).pack(side="left", padx=4)
        ttk.Label(mode_ctl_row, textvariable=self._device_output_mode_status_var).pack(side="left", padx=8)

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
        mid.add(left, weight=0)
        mid.add(right, weight=1)
        self.root.after(300, lambda: self._safe_sashpos(mid, 0, 355))

        self.build_render_panel(left)

        right_split = ttk.PanedWindow(right, orient="vertical")
        right_split.pack(fill="both", expand=True)
        tabs_container = ttk.Frame(right_split)
        log_frame = ttk.Frame(right_split)
        right_split.add(tabs_container, weight=5)
        right_split.add(log_frame, weight=0)

        right_tabs = ttk.Notebook(tabs_container)
        right_tabs.pack(fill="both", expand=True)
        plan_frame = ttk.Frame(right_tabs)
        verifier_frame = ttk.Frame(right_tabs)
        right_tabs.add(plan_frame, text="Measurement Plan")
        right_tabs.add(verifier_frame, text="LUT Verifier")

        self.build_plan_panel(plan_frame)
        self.build_verifier_panel(verifier_frame)
        self.build_log_panel(log_frame)

    @staticmethod
    def _safe_sashpos(paned: ttk.PanedWindow, index: int, pos: int) -> None:
        try:
            paned.sashpos(index, pos)
        except Exception:
            pass

    def refresh_serial_ports(self):
        ports = DirectSerialClient.available_ports()
        self.serial_port_combo["values"] = ports
        if not self.serial_port_var.get() and ports:
            self.serial_port_var.set(ports[0])
        self.log_queue.put(f"[serial] found ports: {ports}")

    def _make_int_scale(self, parent, label, variable, maxv, length=220):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=6, pady=1)
        ttk.Label(row, text=label, width=7).pack(side="left")
        scale = tk.Scale(row, from_=0, to=maxv, variable=variable, orient="horizontal", resolution=1, showvalue=False, command=lambda _v: self._round_var(variable))
        scale.configure(length=length)
        scale.pack(side="left", fill="x", expand=True)
        ttk.Entry(row, textvariable=variable, width=6).pack(side="left", padx=3)

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
        self.w2_var = tk.IntVar(value=0)
        self.lower_r_var = tk.IntVar(value=0)
        self.lower_g_var = tk.IntVar(value=0)
        self.lower_b_var = tk.IntVar(value=0)
        self.lower_w_var = tk.IntVar(value=0)
        self.lower_w2_var = tk.IntVar(value=0)
        self.r16_var = tk.IntVar(value=0)
        self.g16_var = tk.IntVar(value=0)
        self.b16_var = tk.IntVar(value=0)
        self.w16_var = tk.IntVar(value=0)
        # W2 is only used by RGBWW/RGBCCT output paths and is intentionally
        # not folded into W on the host side.
        self.w2_16_var = getattr(self, "w2_16_var", tk.IntVar(value=0))
        self.bfi_r_var = tk.IntVar(value=0)
        self.bfi_g_var = tk.IntVar(value=0)
        self.bfi_b_var = tk.IntVar(value=0)
        self.bfi_w_var = tk.IntVar(value=0)
        self.bfi_w2_var = tk.IntVar(value=0)

        mode_row = ttk.Frame(box)
        mode_row.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(mode_row, text="Mode").pack(side="left")
        ttk.Radiobutton(mode_row, text="Fill8", variable=self.manual_mode_var, value="fill8", command=self._on_manual_mode_changed).pack(side="left", padx=6)
        ttk.Radiobutton(mode_row, text="Blend8", variable=self.manual_mode_var, value="blend8", command=self._on_manual_mode_changed).pack(side="left", padx=6)
        ttk.Radiobutton(mode_row, text="Fill16", variable=self.manual_mode_var, value="fill16", command=self._on_manual_mode_changed).pack(side="left", padx=6)

        summary = ttk.Frame(box)
        summary.pack(fill="x", padx=8, pady=(2, 4))
        swatches = ttk.Frame(summary)
        swatches.pack(side="left", anchor="n")
        ttk.Label(swatches, text="U").pack(side="left")
        self.preview_canvas = tk.Canvas(swatches, width=58, height=28, bg="#000000", highlightthickness=1, highlightbackground="#999")
        self.preview_canvas.pack(side="left", padx=(2, 8))
        ttk.Label(swatches, text="L").pack(side="left")
        self.lower_preview_canvas = tk.Canvas(swatches, width=58, height=28, bg="#000000", highlightthickness=1, highlightbackground="#999")
        self.lower_preview_canvas.pack(side="left", padx=(2, 0))
        text_box = ttk.Frame(summary)
        text_box.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.status_text = tk.StringVar(value="status: idle")
        ttk.Label(text_box, textvariable=self.status_text).pack(anchor="w")
        self.measurement_text = tk.StringVar(value="last measurement: none")
        ttk.Label(text_box, textvariable=self.measurement_text, wraplength=190, justify="left").pack(anchor="w")
        self.preview_text = tk.StringVar(value="Preview RGB")
        ttk.Label(text_box, textvariable=self.preview_text, wraplength=190, justify="left").pack(anchor="w")

        phase_box = ttk.Frame(box)
        phase_box.pack(fill="x", padx=8, pady=(2, 4))
        phase_mode_row = ttk.Frame(phase_box)
        phase_mode_row.pack(fill="x")
        ttk.Label(phase_mode_row, text="Phase mode").pack(side="left")
        ttk.Radiobutton(phase_mode_row, text="Auto", variable=self.phase_mode_var, value=PHASE_MODE_AUTO, command=self.send_phase_mode).pack(side="left", padx=4)
        ttk.Radiobutton(phase_mode_row, text="Manual", variable=self.phase_mode_var, value=PHASE_MODE_MANUAL, command=self.send_phase_mode).pack(side="left", padx=4)

        phase_index_row = ttk.Frame(phase_box)
        phase_index_row.pack(fill="x", pady=(1, 0))
        ttk.Label(phase_index_row, text="Phase index").pack(side="left")
        tk.Scale(phase_index_row, from_=0, to=PHASE_CONTROL_MAX, variable=self.phase_var, orient="horizontal", resolution=1, showvalue=False, command=lambda _v: self._round_var(self.phase_var)).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Entry(phase_index_row, textvariable=self.phase_var, width=6).pack(side="left", padx=4)
        ttk.Button(phase_index_row, text="Apply", command=self.send_phase).pack(side="left", padx=4)

        btns = ttk.Frame(box)
        btns.pack(fill="x", padx=8, pady=(4, 1))
        ttk.Button(btns, text="Send State", command=self.send_fill).pack(side="left", padx=3)
        ttk.Button(btns, text="Commit", command=self.commit).pack(side="left", padx=3)
        ttk.Button(btns, text="Clear", command=self.clear).pack(side="left", padx=3)
        ttk.Button(btns, text="Measure Once", command=self.measure_once).pack(side="left", padx=3)

        direct_btns = ttk.Frame(box)
        direct_btns.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Button(direct_btns, text="Direct RGBW16", command=self.send_direct_rgbw16).pack(side="left", padx=3)
        ttk.Button(direct_btns, text="Direct RGBWW16", command=self.send_direct_rgbww16).pack(side="left", padx=3)

        tabs = ttk.Notebook(box)
        tabs.pack(fill="x", padx=8, pady=(0, 4))
        base_tab = ttk.Frame(tabs)
        true16_tab = ttk.Frame(tabs)
        tabs.add(base_tab, text="8-bit / Blend8")
        tabs.add(true16_tab, text="True16")
        self.manual_tabs = tabs
        self.base_tab = base_tab
        self.true16_tab = true16_tab

        for label, var, maxv in [("R", self.r_var, 255), ("G", self.g_var, 255), ("B", self.b_var, 255), ("W", self.w_var, 255), ("W2", self.w2_var, 255)]:
            self._make_int_scale(base_tab, label, var, maxv, length=150)

        blend_extra = ttk.Notebook(base_tab)
        blend_extra.pack(fill="x", padx=6, pady=(4, 2))
        self.blend8_extra_tabs = blend_extra
        lower_tab = ttk.Frame(blend_extra)
        bfi_tab = ttk.Frame(blend_extra)
        blend_extra.add(lower_tab, text="Lower / previous")
        blend_extra.add(bfi_tab, text="BFI")

        blend8 = ttk.Frame(lower_tab)
        blend8.pack(fill="x", padx=2, pady=2)
        self.blend8_lower_box = blend8
        for label, var in [("Floor R", self.lower_r_var), ("Floor G", self.lower_g_var), ("Floor B", self.lower_b_var), ("Floor W", self.lower_w_var), ("Floor W2", self.lower_w2_var)]:
            self._make_int_scale(blend8, label, var, 255, length=130)

        bfi_box = ttk.Frame(bfi_tab)
        bfi_box.pack(fill="x", padx=2, pady=2)
        self.blend8_bfi_box = bfi_box
        for label, var, maxv in [("BFI R", self.bfi_r_var, MAX_BFI), ("BFI G", self.bfi_g_var, MAX_BFI), ("BFI B", self.bfi_b_var, MAX_BFI), ("BFI W", self.bfi_w_var, MAX_BFI), ("BFI W2", self.bfi_w2_var, MAX_BFI)]:
            self._make_int_scale(bfi_box, label, var, maxv, length=130)

        fill16 = ttk.LabelFrame(true16_tab, text="True 16-bit patch values")
        fill16.pack(fill="x", padx=6, pady=(4, 2))
        for txt, var in [("R16", self.r16_var), ("G16", self.g16_var), ("B16", self.b16_var), ("W16", self.w16_var), ("W2 16", self.w2_16_var)]:
            row = ttk.Frame(fill16)
            row.pack(fill="x", padx=4, pady=1)
            ttk.Label(row, text=txt, width=7).pack(side="left")
            scale = tk.Scale(row, from_=0, to=65535, variable=var, orient="horizontal", resolution=1, showvalue=False, command=lambda _v: self._sync_preview_from_16())
            scale.configure(length=140)
            scale.pack(side="left", fill="x", expand=True)
            ttk.Entry(row, textvariable=var, width=8).pack(side="left", padx=3)

        self._update_manual_control_visibility(select_tab=True)
        self.update_preview()

    def build_log_panel(self, parent):
        box = ttk.LabelFrame(parent, text="Logs")
        box.pack(fill="both", expand=True, pady=6)
        self.log = tk.Text(box, height=5, wrap="word")
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
        cols = ("name", "mode", "family", "values8", "values16", "bfi", "lower", "upper", "timing", "repeats")
        tree_frame = ttk.Frame(box)
        tree_frame.pack(fill="both", expand=True, padx=6, pady=6)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=16, selectmode="extended")
        widths = {"name": 200, "mode": 78, "family": 110, "values8": 140, "values16": 220, "bfi": 120, "lower": 150, "upper": 150, "timing": 120, "repeats": 60}
        for col in cols:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=widths.get(col, 70), stretch=(col in {"name", "values16", "lower", "upper"}), anchor="center")
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
            "name", "mode", "channels", "use_fill16", "r", "g", "b", "w", "w2",
            "lower_r", "lower_g", "lower_b", "lower_w", "lower_w2",
            "upper_r", "upper_g", "upper_b", "upper_w", "upper_w2",
            "r16", "g16", "b16", "w16", "w2_16",
            "bfi_r", "bfi_g", "bfi_b", "bfi_w", "bfi_w2",
            "repeat_index", "solver_mode", "measurement_format", "spotread_command",
            "ok", "returncode", "elapsed_s", "timed_out",
            "XYZ_X", "XYZ_Y", "XYZ_Z", "xyY_Y", "xyY_x", "xyY_y",
            "Lab_L", "Lab_a", "Lab_b", "LCh_L", "LCh_C", "LCh_h", "Luv_L", "Luv_u", "Luv_v",
            "stdout", "stderr",
        ]

    def _plan_capture_row(self, row: MeasurementPlanRow, rep: int, solver_mode: int, result: dict[str, object]) -> list[object]:
        return [
            row.name, row.normalized_mode(), row.channel_count(), int(row.normalized_mode() == "fill16"), row.r, row.g, row.b, row.w, row.w2,
            row.lower_r, row.lower_g, row.lower_b, row.lower_w, row.lower_w2,
            row.upper_r, row.upper_g, row.upper_b, row.upper_w, row.upper_w2,
            row.r16, row.g16, row.b16, row.w16, row.w2_16,
            row.bfi_r, row.bfi_g, row.bfi_b, row.bfi_w, row.bfi_w2,
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

    def _update_manual_control_visibility(self, *, select_tab: bool = False) -> None:
        """Keep the manual panel compact by selecting the mode-relevant tab."""
        mode = self.manual_mode_var.get()
        if select_tab and hasattr(self, "manual_tabs"):
            try:
                self.manual_tabs.select(self.true16_tab if mode == "fill16" else self.base_tab)
            except Exception:
                pass
        if hasattr(self, "blend8_extra_tabs"):
            try:
                self.blend8_extra_tabs.select(0 if mode == "blend8" else 1)
            except Exception:
                pass

    def _on_manual_mode_changed(self):
        self.use_fill16_var.set(self.manual_mode_var.get() == "fill16")
        self._update_manual_control_visibility(select_tab=True)
        self.update_preview()

    def set_preview_values(
        self,
        r,
        g,
        b,
        w,
        bfi_r,
        bfi_g,
        bfi_b,
        bfi_w,
        r16=None,
        g16=None,
        b16=None,
        w16=None,
        w2_16=None,
        mode="fill8",
        lower_r=0,
        lower_g=0,
        lower_b=0,
        lower_w=0,
        w2=0,
        lower_w2=0,
        bfi_w2=0,
    ):
        self.manual_mode_var.set(mode)
        self.use_fill16_var.set(mode == "fill16")
        self.r_var.set(int(r))
        self.g_var.set(int(g))
        self.b_var.set(int(b))
        self.w_var.set(int(w))
        self.w2_var.set(int(w2))
        self.lower_r_var.set(int(lower_r))
        self.lower_g_var.set(int(lower_g))
        self.lower_b_var.set(int(lower_b))
        self.lower_w_var.set(int(lower_w))
        self.lower_w2_var.set(int(lower_w2))
        self.r16_var.set(int(r16) if r16 is not None else int(r) * 257)
        self.g16_var.set(int(g16) if g16 is not None else int(g) * 257)
        self.b16_var.set(int(b16) if b16 is not None else int(b) * 257)
        self.w16_var.set(int(w16) if w16 is not None else int(w) * 257)
        self.w2_16_var.set(int(w2_16) if w2_16 is not None else int(w2) * 257)
        self.bfi_r_var.set(int(bfi_r))
        self.bfi_g_var.set(int(bfi_g))
        self.bfi_b_var.set(int(bfi_b))
        self.bfi_w_var.set(int(bfi_w))
        self.bfi_w2_var.set(int(bfi_w2))
        self._update_manual_control_visibility(select_tab=True)
        self.update_preview()

    def _sync_preview_from_16(self):
        self.r_var.set(int((self.r16_var.get() * 255 + 32767) // 65535))
        self.g_var.set(int((self.g16_var.get() * 255 + 32767) // 65535))
        self.b_var.set(int((self.b16_var.get() * 255 + 32767) // 65535))
        self.w_var.set(int((self.w16_var.get() * 255 + 32767) // 65535))
        self.w2_var.set(int((self.w2_16_var.get() * 255 + 32767) // 65535))
        self.update_preview()

    @staticmethod
    def _preview_rgb_with_white(r, g, b, w, w2=0):
        white = int(w) + int(w2)
        r8 = max(0, min(255, int(r) + white))
        g8 = max(0, min(255, int(g) + white))
        b8 = max(0, min(255, int(b) + white))
        return r8, g8, b8

    def update_preview(self):
        r = int(self.r_var.get())
        g = int(self.g_var.get())
        b = int(self.b_var.get())
        w = int(self.w_var.get())
        w2 = int(self.w2_var.get())
        lower_r = int(self.lower_r_var.get())
        lower_g = int(self.lower_g_var.get())
        lower_b = int(self.lower_b_var.get())
        lower_w = int(self.lower_w_var.get())
        lower_w2 = int(self.lower_w2_var.get())
        mode = self.manual_mode_var.get()
        preview_r, preview_g, preview_b = self._preview_rgb_with_white(r, g, b, w, w2)
        lower_preview_r, lower_preview_g, lower_preview_b = self._preview_rgb_with_white(lower_r, lower_g, lower_b, lower_w, lower_w2)
        self.preview_canvas.configure(bg=f"#{preview_r:02x}{preview_g:02x}{preview_b:02x}")
        self.lower_preview_canvas.configure(bg=f"#{lower_preview_r:02x}{lower_preview_g:02x}{lower_preview_b:02x}")
        if mode == "blend8":
            self.preview_text.set(
                f"BLEND8 U=({r},{g},{b},{w},{w2}) L=({lower_r},{lower_g},{lower_b},{lower_w},{lower_w2}) "
                f"BFI=({self.bfi_r_var.get()},{self.bfi_g_var.get()},{self.bfi_b_var.get()},"
                f"{self.bfi_w_var.get()},{self.bfi_w2_var.get()})"
            )
        elif mode == "fill16":
            self.preview_text.set(
                f"FILL16 16=({self.r16_var.get()},{self.g16_var.get()},"
                f"{self.b16_var.get()},{self.w16_var.get()},{self.w2_16_var.get()}) "
                f"8=({r},{g},{b},{w},{w2})"
            )
        else:
            self.preview_text.set(
                f"FILL8=({r},{g},{b},{w},{w2}) BFI=({self.bfi_r_var.get()},"
                f"{self.bfi_g_var.get()},{self.bfi_b_var.get()},{self.bfi_w_var.get()},"
                f"{self.bfi_w2_var.get()})"
            )

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
            row = self._build_manual_row()
        target_channels = self._selected_device_channel_count()
        row_channels = row.channel_count()
        if row_channels > target_channels:
            raise RuntimeError(
                f"row {row.name!r} is {self._channel_label(row_channels)}, "
                f"but selected device output mode is {self._channel_label(target_channels)}"
            )
        mode = row.normalized_mode()
        if mode == "blend8":
            return self._build_blend8_payload(row, output_channels=target_channels)
        if mode == "fill16":
            values = self._row_u16_channels(row, output_channels=target_channels)
            if target_channels >= 5:
                return self._build_direct_rgbww16_payload(values)
            return self._build_fill16_payload(values)
        return self._build_fill8_payload(row, output_channels=target_channels)

    def _row_u8_channels(
        self,
        row: MeasurementPlanRow,
        *,
        upper: bool = False,
        lower: bool = False,
        output_channels: int | None = None,
    ) -> list[int]:
        row_count = row.channel_count()
        out_count = output_channels or row_count
        if out_count < row_count:
            raise RuntimeError(
                f"row {row.name!r} is {self._channel_label(row_count)}, "
                f"but requested payload is {self._channel_label(out_count)}"
            )
        if lower:
            channels = [row.lower_r, row.lower_g, row.lower_b]
            w1 = row.lower_w if row_count >= 4 else 0
            w2 = row.lower_w2 if row_count >= 5 else 0
        elif upper:
            channels = [row.upper_r, row.upper_g, row.upper_b]
            w1 = row.upper_w if row_count >= 4 else 0
            w2 = row.upper_w2 if row_count >= 5 else 0
        else:
            channels = [row.r, row.g, row.b]
            w1 = row.w if row_count >= 4 else 0
            w2 = row.w2 if row_count >= 5 else 0
        if out_count >= 4:
            channels.append(w1)
        if out_count >= 5:
            channels.append(w2)
        return [max(0, min(255, int(v))) for v in channels]

    def _row_bfi_channels(self, row: MeasurementPlanRow, *, output_channels: int | None = None) -> list[int]:
        row_count = row.channel_count()
        out_count = output_channels or row_count
        if out_count < row_count:
            raise RuntimeError(
                f"row {row.name!r} is {self._channel_label(row_count)}, "
                f"but requested BFI payload is {self._channel_label(out_count)}"
            )
        channels = [row.bfi_r, row.bfi_g, row.bfi_b]
        if out_count >= 4:
            channels.append(row.bfi_w if row_count >= 4 else 0)
        if out_count >= 5:
            channels.append(row.bfi_w2 if row_count >= 5 else 0)
        return [max(0, min(MAX_BLEND_CYCLE_LENGTH - 1, int(v))) for v in channels]

    def _row_u16_channels(self, row: MeasurementPlanRow, *, output_channels: int | None = None) -> list[int]:
        row_count = row.channel_count()
        out_count = output_channels or row_count
        if out_count < row_count:
            raise RuntimeError(
                f"row {row.name!r} is {self._channel_label(row_count)}, "
                f"but requested Fill16 payload is {self._channel_label(out_count)}"
            )
        channels = [row.r16, row.g16, row.b16]
        if out_count >= 4:
            channels.append(row.w16 if row_count >= 4 else 0)
        if out_count >= 5:
            channels.append(row.w2_16 if row_count >= 5 else 0)
        return [max(0, min(65535, int(v))) for v in channels]

    def _build_fill8_payload(self, row: MeasurementPlanRow, *, output_channels: int | None = None) -> bytes:
        out_count = output_channels or self._selected_device_channel_count()
        channels = self._row_u8_channels(row, output_channels=out_count)
        bfi = self._row_bfi_channels(row, output_channels=out_count)
        return bytes([OP_SET_FILL, *channels, *bfi])

    def _build_blend8_payload(self, row: MeasurementPlanRow, *, output_channels: int | None = None) -> bytes:
        out_count = output_channels or self._selected_device_channel_count()
        lower = self._row_u8_channels(row, lower=True, output_channels=out_count)
        upper = self._row_u8_channels(row, upper=True, output_channels=out_count)
        bfi = self._row_bfi_channels(row, output_channels=out_count)
        return bytes([OP_SET_TEMPORAL_BLEND, *lower, *upper, *bfi])

    def _build_fill16_payload(self, values: list[int] | tuple[int, ...]) -> bytes:
        payload = bytearray([OP_SET_FILL16])
        for value in values:
            payload.extend(self._pack_u16(value))
        return bytes(payload)

    def _build_direct_rgbww16_payload(self, values: list[int] | tuple[int, ...]) -> bytes:
        payload = bytearray([OP_SET_DIRECT_RGBWW16])
        for value in values:
            payload.extend(self._pack_u16(value))
        return bytes(payload)

    def _selected_strip_channel_count(self) -> int:
        return STRIP_TYPE_CHOICES.get(self._verifier_strip_type_var.get(), 4)

    def _selected_device_channel_count(self) -> int:
        mode_name = self._device_output_mode_var.get()
        mode_id = OUTPUT_MODE_CHOICES.get(mode_name, OUTPUT_MODE_RGBW)
        if mode_id == OUTPUT_MODE_RGB:
            return 3
        if mode_id == OUTPUT_MODE_RGBWW:
            return 5
        return 4

    def _channel_label(self, count: int) -> str:
        return PLAN_CHANNEL_LABELS.get(int(count), f"{count}ch")

    def _validate_plan_row_compatible(self, row: MeasurementPlanRow, *, context: str = "plan") -> None:
        target_channels = self._selected_device_channel_count()
        row_channels = row.channel_count()
        if row_channels > target_channels:
            msg = (
                f"[{context}] row {row.name!r} is {self._channel_label(row_channels)}, "
                f"but selected device output mode is {self._channel_label(target_channels)}. "
                "Select a device/strip mode with enough channels; the host will not drop W/W2 channels."
            )
            self.log_queue.put(msg)
            raise RuntimeError(msg)

    def _selected_cube_output_channel_count(self) -> int:
        if self.verifier_lut is None or self.verifier_lut_channel_count is None:
            raise RuntimeError("Load a LUT before resolving cube output type.")
        selected = CUBE_OUTPUT_TYPE_CHOICES.get(self._verifier_cube_output_type_var.get(), 0)
        if selected == 0:
            return self.verifier_lut_channel_count
        if selected > self.verifier_lut_channel_count:
            raise RuntimeError(
                f"Selected cube output type requires {selected} channels, "
                f"but loaded cube only has {self.verifier_lut_channel_count}."
            )
        return selected

    def _validate_output_family(self, output_channels: int, strip_channels: int) -> None:
        if output_channels < 3:
            raise RuntimeError(f"Cube output type must have at least RGB channels, got {output_channels}.")
        if output_channels > 5:
            raise RuntimeError(
                f"Loaded cube has {output_channels} output channels. "
                "The GUI currently supports RGB, RGBW, and RGBWW/RGBCCT transport only."
            )
        if output_channels > strip_channels:
            raise RuntimeError(
                f"Selected strip type has {strip_channels} channels, but cube output has {output_channels}. "
                "Choose a strip type with enough output channels or load a lower-channel cube."
            )

    @staticmethod
    def _expand_output_channels(values: tuple[int, ...] | list[int], output_channels: int) -> tuple[int, int, int, int, int]:
        if len(values) < output_channels:
            raise RuntimeError(f"LUT lookup returned {len(values)} channels, expected {output_channels}.")
        channels = [max(0, min(65535, int(values[i]))) for i in range(output_channels)]
        while len(channels) < 5:
            channels.append(0)
        return channels[0], channels[1], channels[2], channels[3], channels[4]

    def _set_output_mode_for_strip_wait(self, strip_channels: int, timeout_s: float = 1.0) -> dict[str, object] | None:
        mode_id = OUTPUT_MODE_RGB if strip_channels == 3 else OUTPUT_MODE_RGBW if strip_channels == 4 else OUTPUT_MODE_RGBWW
        msg = self._send_cal_request_wait(bytes([OP_SET_OUTPUT_MODE, mode_id & 0xFF]), OP_SET_OUTPUT_MODE, timeout_s=timeout_s)
        if msg is None:
            raise RuntimeError("no response to OP_SET_OUTPUT_MODE")
        status = int(msg.get("status", 255))
        if status != STATUS_OK:
            raise RuntimeError(f"OP_SET_OUTPUT_MODE failed for selected strip type: {_status_name(status)}")
        self.root.after(0, lambda m=dict(msg): self._apply_device_mode_status(m))
        return msg

    def _send_output_channels_direct(self, values: tuple[int, int, int, int, int], strip_channels: int, timeout_s: float = 0.8) -> dict[str, object] | None:
        r16, g16, b16, w16, w2_16 = values
        if strip_channels >= 5:
            msg = self._send_cal_request_wait(self._build_direct_rgbww16_payload([r16, g16, b16, w16, w2_16]), OP_SET_DIRECT_RGBWW16, timeout_s=timeout_s)
            if isinstance(msg, dict) and int(msg.get("status", 255)) == STATUS_OK:
                return msg
            status = msg.get("status") if isinstance(msg, dict) else None
            raise RuntimeError(f"temporal direct RGBWW16 failed: {_status_name(status)}")
        return self._send_rgbw16_direct_or_fill16([r16, g16, b16, w16], timeout_s=timeout_s)

    def _build_output_mode_payload(self) -> bytes:
        mode_name = self._device_output_mode_var.get()
        mode_id = OUTPUT_MODE_CHOICES.get(mode_name, OUTPUT_MODE_RGBW)
        return bytes([OP_SET_OUTPUT_MODE, mode_id & 0xFF])

    def _fastled_input_gamut_id_for_selection(self) -> int:
        gamut_name = self._verifier_gamut_var.get().strip().lower()
        if gamut_name not in FASTLED_INPUT_GAMUT_CHOICES:
            raise RuntimeError(
                f"FastLED analytical input gamut does not support {gamut_name!r}; "
                "choose summary/native, rec709, rec2020, dci-p3, or dci-p3-d60."
            )
        return FASTLED_INPUT_GAMUT_CHOICES[gamut_name]

    def _apply_input_gamut_status(self, msg: dict[str, object]) -> None:
        if "input_gamut" not in msg:
            return
        try:
            gamut_id = int(msg.get("input_gamut", INPUT_GAMUT_NATIVE))
            gamut_name = FASTLED_INPUT_GAMUT_NAMES.get(gamut_id, f"gamut{gamut_id}")
            supported = int(msg.get("supported_input_gamut_bitmask", 0))
            self._fastled_input_gamut_status_var.set(
                f"FastLED input gamut: {gamut_name}  supported=0x{supported:02X}"
            )
        except Exception:
            pass

    def _set_fastled_input_gamut_wait(self, gamut_id: int, timeout_s: float = 1.0) -> dict[str, object]:
        msg = self._send_cal_request_wait(bytes([OP_SET_INPUT_GAMUT, gamut_id & 0xFF]), OP_SET_INPUT_GAMUT, timeout_s=timeout_s)
        if msg is None:
            raise RuntimeError("no response to OP_SET_INPUT_GAMUT")
        status = int(msg.get("status", 255))
        if status != STATUS_OK:
            raise RuntimeError(f"OP_SET_INPUT_GAMUT failed: {_status_name(status)}")
        self.root.after(0, lambda m=dict(msg): self._apply_input_gamut_status(m))
        return msg

    def fetch_fastled_input_gamut_async(self) -> None:
        if not self.device.is_connected():
            messagebox.showerror("FastLED input gamut", "Connect the Teensy device first.")
            return

        def worker() -> None:
            try:
                msg = self._send_cal_request_wait(bytes([OP_GET_INPUT_GAMUT]), OP_GET_INPUT_GAMUT, timeout_s=1.0)
                if msg is None:
                    raise RuntimeError("no response to OP_GET_INPUT_GAMUT")
                status = int(msg.get("status", 255))
                if status != STATUS_OK:
                    raise RuntimeError(f"OP_GET_INPUT_GAMUT failed: {_status_name(status)}")
                self.root.after(0, lambda m=dict(msg): self._apply_input_gamut_status(m))
            except Exception as exc:
                self.log_queue.put(f"[protocol] FastLED input gamut fetch failed: {exc}")
                self.root.after(0, lambda e=str(exc): messagebox.showerror("FastLED input gamut failed", e))

        threading.Thread(target=worker, daemon=True).start()

    def _send_rgbw16_direct_or_fill16(self, values: list[int] | tuple[int, int, int, int], timeout_s: float = 0.8) -> dict[str, object] | None:
        msg = self._send_cal_request_wait(self._build_fill16_payload(values), OP_SET_FILL16, timeout_s=timeout_s)
        if isinstance(msg, dict) and int(msg.get("status", 255)) == STATUS_OK:
            return msg
        status = msg.get("status") if isinstance(msg, dict) else None
        raise RuntimeError(f"temporal fill16 direct RGBW16 failed: {_status_name(status)}")

    def send_output_mode(self) -> None:
        if not self.device.is_connected():
            messagebox.showerror("Output mode", "Connect the Teensy device first.")
            return
        try:
            msg = self._send_cal_request_wait(self._build_output_mode_payload(), OP_SET_OUTPUT_MODE, timeout_s=1.0)
            if msg is None:
                raise RuntimeError("no response to OP_SET_OUTPUT_MODE")
            status = int(msg.get("status", 255))
            if status != STATUS_OK:
                raise RuntimeError(f"OP_SET_OUTPUT_MODE failed: {_status_name(status)}")
            self._apply_device_mode_status(msg)
        except Exception as exc:
            self.log_queue.put(f"[protocol] output mode failed: {exc}")
            messagebox.showerror("Output mode failed", str(exc))

    def send_direct_rgbw16(self) -> None:
        if not self.device.is_connected():
            messagebox.showerror("Temporal Fill16", "Connect the Teensy device first.")
            return
        try:
            values = [self.r16_var.get(), self.g16_var.get(), self.b16_var.get(), self.w16_var.get()]
            msg = self._send_cal_request_wait(self._build_fill16_payload(values), OP_SET_FILL16, timeout_s=1.0)
            if msg is None:
                raise RuntimeError("no response to OP_SET_FILL16")
            status = int(msg.get("status", 255))
            if status != STATUS_OK:
                raise RuntimeError(f"temporal fill16 direct RGBW16 failed: {_status_name(status)}")
            self._apply_device_mode_status(msg)
        except Exception as exc:
            self.log_queue.put(f"[protocol] temporal fill16 direct RGBW16 failed: {exc}")
            messagebox.showerror("Temporal Fill16 failed", str(exc))

    def send_direct_rgbww16(self) -> None:
        if not self.device.is_connected():
            messagebox.showerror("Temporal Direct RGBWW16", "Connect the Teensy device first.")
            return
        try:
            values = [self.r16_var.get(), self.g16_var.get(), self.b16_var.get(), self.w16_var.get(), self.w2_16_var.get()]
            msg = self._send_cal_request_wait(self._build_direct_rgbww16_payload(values), OP_SET_DIRECT_RGBWW16, timeout_s=1.0)
            if msg is None:
                raise RuntimeError("no response to OP_SET_DIRECT_RGBWW16")
            status = int(msg.get("status", 255))
            if status != STATUS_OK:
                raise RuntimeError(f"temporal direct RGBWW16 failed: {_status_name(status)}")
            self._apply_device_mode_status(msg)
        except Exception as exc:
            self.log_queue.put(f"[protocol] temporal direct RGBWW16 failed: {exc}")
            messagebox.showerror("Temporal Direct RGBWW16 failed", str(exc))

    def _build_manual_row(self) -> MeasurementPlanRow:
        mode = self.manual_mode_var.get()
        r = int(self.r_var.get())
        g = int(self.g_var.get())
        b = int(self.b_var.get())
        w = int(self.w_var.get())
        w2 = int(self.w2_var.get())
        lower_r = int(self.lower_r_var.get())
        lower_g = int(self.lower_g_var.get())
        lower_b = int(self.lower_b_var.get())
        lower_w = int(self.lower_w_var.get())
        lower_w2 = int(self.lower_w2_var.get())
        bfi_r = int(self.bfi_r_var.get())
        bfi_g = int(self.bfi_g_var.get())
        bfi_b = int(self.bfi_b_var.get())
        bfi_w = int(self.bfi_w_var.get())
        bfi_w2 = int(self.bfi_w2_var.get())

        r16 = int(self.r16_var.get()) if mode == "fill16" else (r * 257)
        g16 = int(self.g16_var.get()) if mode == "fill16" else (g * 257)
        b16 = int(self.b16_var.get()) if mode == "fill16" else (b * 257)
        w16 = int(self.w16_var.get()) if mode == "fill16" else (w * 257)
        w2_16 = int(self.w2_16_var.get()) if mode == "fill16" else (w2 * 257)

        selected_channels = self._selected_device_channel_count()
        required_channels = selected_channels
        needs_w = any((w, lower_w if mode == "blend8" else 0, bfi_w, w16 if mode == "fill16" else 0))
        needs_w2 = any((w2, lower_w2 if mode == "blend8" else 0, bfi_w2, w2_16 if mode == "fill16" else 0))
        if needs_w2:
            required_channels = max(required_channels, 5)
        elif selected_channels == 3 and needs_w:
            required_channels = 4

        manual_w2 = self._q16_to_u8(w2_16) if mode == "fill16" else w2
        manual_bfi_w2 = 0 if mode == "fill16" else bfi_w2
        return MeasurementPlanRow(
            name="manual",
            r=r,
            g=g,
            b=b,
            w=w if required_channels >= 4 else 0,
            w2=manual_w2 if required_channels >= 5 else 0,
            bfi_r=bfi_r,
            bfi_g=bfi_g,
            bfi_b=bfi_b,
            bfi_w=bfi_w if required_channels >= 4 else 0,
            bfi_w2=manual_bfi_w2 if required_channels >= 5 else 0,
            repeats=1,
            lower_r=lower_r,
            lower_g=lower_g,
            lower_b=lower_b,
            lower_w=lower_w if required_channels >= 4 else 0,
            lower_w2=lower_w2 if required_channels >= 5 else 0,
            upper_r=r,
            upper_g=g,
            upper_b=b,
            upper_w=w if required_channels >= 4 else 0,
            upper_w2=manual_w2 if required_channels >= 5 else 0,
            r16=r16,
            g16=g16,
            b16=b16,
            w16=w16 if required_channels >= 4 else 0,
            w2_16=w2_16 if required_channels >= 5 else 0,
            use_fill16=(mode == "fill16"),
            mode=mode,
            channels=required_channels,
        )

    def send_fill(self, *, show_error: bool = True):
        try:
            row = self._build_manual_row()
            self._validate_plan_row_compatible(row, context="manual")
            self.device.send_frame(KIND_CAL_REQ, self._build_fill_payload(row))
        except Exception as exc:
            self.log_queue.put(f"[manual] render rejected: {exc}")
            if show_error:
                messagebox.showerror("Render rejected", str(exc))
                return
            raise

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

    def _apply_device_mode_status(self, msg: dict[str, object]) -> None:
        if "active_output_mode" not in msg:
            return
        try:
            active = int(msg.get("active_output_mode", OUTPUT_MODE_RGBW))
            mode_name = OUTPUT_MODE_NAMES.get(active, f"mode{active}")
            supported = int(msg.get("supported_output_mode_bitmask", 0))
            physical = msg.get("physical_output_channel_count", "?")
            logical = msg.get("active_logical_channel_count", "?")
            self._device_output_mode_var.set(mode_name if mode_name in OUTPUT_MODE_CHOICES else self._device_output_mode_var.get())
            self._device_output_mode_status_var.set(
                f"device mode: {mode_name}  supported=0x{supported:02X}  phys={physical} logical={logical}"
            )
        except Exception:
            pass

    def on_device_packet(self, msg):
        self.current_status = msg
        if msg.get("type") == "cal_response":
            self.cal_response_queue.put(dict(msg))
            self._apply_device_mode_status(msg)
            self._apply_input_gamut_status(msg)
            status = _status_name(msg.get("status")) if msg.get("status") is not None else "?"
            self.status_text.set(f"status: cal_response op=0x{int(msg.get('op', 0)):02X} {status}")
        else:
            self.status_text.set(f"status: {msg.get('type')}")

    def _send_cal_request_wait(self, payload: bytes, op: int, timeout_s: float = 1.0) -> dict[str, object] | None:
        while True:
            try:
                self.cal_response_queue.get_nowait()
            except queue.Empty:
                break
        self.device.send_frame(KIND_CAL_REQ, payload)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                msg = self.cal_response_queue.get(timeout=max(0.01, min(0.1, deadline - time.time())))
            except queue.Empty:
                continue
            if msg.get("op") == op:
                return msg
        return None

    def infer_repeats(self, r, g, b, w=0, w2=0):
        y = (
            0.2126 * (r / 255.0) +
            0.7152 * (g / 255.0) +
            0.0722 * (b / 255.0) +
            1.0 * (w / 255.0) +
            1.0 * (w2 / 255.0)
        )
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

    def _canonical_record(self, rec: dict[str, object]) -> dict[str, object]:
        return {str(k).strip().lower(): v for k, v in rec.items() if k is not None}

    def _normalize_mode(self, rec: dict[str, object]) -> str:
        rec = self._canonical_record(rec)
        mode = str(rec.get("mode", "")).strip().lower()
        if mode and mode not in {"blend8", "fill16", "fill8"}:
            raise ValueError(f"unsupported mode '{mode}'")
        if mode in {"blend8", "fill16", "fill8"}:
            return mode
        blend_fields = [
            "lower_r", "upper_r", "lower_g", "upper_g", "lower_b", "upper_b",
            "lower_w", "upper_w", "lower_w1", "upper_w1", "lower_w2", "upper_w2",
        ]
        if any(str(rec.get(field, "")).strip() for field in blend_fields):
            return "blend8"
        fill16_fields = ["r16", "g16", "b16", "w16", "w1_16", "w2_16", "w216"]
        if self._parse_bool(rec.get("use_fill16", "0")) or any(str(rec.get(field, "")).strip() for field in fill16_fields):
            return "fill16"
        return "fill8"

    def _infer_record_channels(self, rec: dict[str, object], fieldnames: list[str] | None = None) -> int:
        rec = self._canonical_record(rec)
        if "channels" in rec and str(rec.get("channels", "")).strip():
            raw = str(rec.get("channels", "")).strip().lower()
            if raw.isdigit():
                parsed = int(raw)
            elif raw in {"rgb", "3", "3ch"}:
                parsed = 3
            elif raw in {"rgbw", "4", "4ch"}:
                parsed = 4
            elif raw in {"rgbww", "rgbcct", "rgbww/rgbcct", "5", "5ch"}:
                parsed = 5
            else:
                raise ValueError(f"unsupported channel family {raw!r}")
            if parsed not in (3, 4, 5):
                raise ValueError(f"unsupported channel count {parsed}")
            return parsed
        names = {str(f).strip().lower() for f in (fieldnames or rec.keys()) if f is not None}
        w2_keys = {"w2", "lower_w2", "upper_w2", "bfi_w2", "w2_16", "w216"}
        if names.intersection(w2_keys):
            return 5
        w_keys = {"w", "w1", "lower_w", "lower_w1", "upper_w", "upper_w1", "bfi_w", "bfi_w1", "w16", "w1_16"}
        if names.intersection(w_keys):
            return 4
        return 3

    def _tree_values_for_row(self, row: MeasurementPlanRow):
        lower = ""
        upper = ""
        timing = ""
        values8 = f"{row.r}/{row.g}/{row.b}"
        values16 = f"{row.r16}/{row.g16}/{row.b16}"
        bfi = f"{row.bfi_r}/{row.bfi_g}/{row.bfi_b}"
        if row.channel_count() >= 4:
            values8 += f"/{row.w}"
            values16 += f"/{row.w16}"
            bfi += f"/{row.bfi_w}"
        if row.channel_count() >= 5:
            values8 += f"/{row.w2}"
            values16 += f"/{row.w2_16}"
            bfi += f"/{row.bfi_w2}"
        if row.normalized_mode() == "blend8":
            lower = f"{row.lower_r}/{row.lower_g}/{row.lower_b}"
            upper = f"{row.upper_r}/{row.upper_g}/{row.upper_b}"
            timing = f"{row.bfi_r}/{row.bfi_g}/{row.bfi_b}"
            if row.channel_count() >= 4:
                lower += f"/{row.lower_w}"
                upper += f"/{row.upper_w}"
                timing += f"/{row.bfi_w}"
            if row.channel_count() >= 5:
                lower += f"/{row.lower_w2}"
                upper += f"/{row.upper_w2}"
                timing += f"/{row.bfi_w2}"
        return (
            row.name,
            row.normalized_mode(),
            row.channel_label(),
            values8,
            values16,
            bfi,
            lower,
            upper,
            timing,
            row.repeats,
        )

    def add_plan_row(self, row: MeasurementPlanRow):
        self.measurement_rows.append(row)
        self.tree.insert("", "end", values=self._tree_values_for_row(row))

    def add_current_to_plan(self):
        try:
            row = self._build_manual_row()
            self._validate_plan_row_compatible(row, context="plan")
        except Exception as exc:
            messagebox.showerror("Plan row rejected", str(exc))
            return
        row.name = f"state_{len(self.measurement_rows):04d}"
        row.repeats = self.infer_repeats(row.r, row.g, row.b, row.w, row.w2)
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
            w2_16=row.w2_16,
            mode=row.normalized_mode(),
            lower_r=row.lower_r,
            lower_g=row.lower_g,
            lower_b=row.lower_b,
            lower_w=row.lower_w,
            w2=row.w2,
            lower_w2=row.lower_w2,
            bfi_w2=row.bfi_w2,
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
        rec = self._canonical_record(rec)
        value = rec.get(str(key).strip().lower(), default)
        if value in (None, ""):
            return int(default)
        return int(value)

    def _dict_int_any(self, rec, keys: tuple[str, ...] | list[str], default=0):
        rec = self._canonical_record(rec)
        for key in keys:
            key_l = str(key).strip().lower()
            value = rec.get(key_l, None)
            if value not in (None, ""):
                return int(value)
        return int(default)

    def _row_from_record(self, rec: dict[str, str], fieldnames: list[str] | None = None) -> MeasurementPlanRow:
        rec = self._canonical_record(rec)
        mode = self._normalize_mode(rec)
        channels = self._infer_record_channels(rec, fieldnames)
        w = self._dict_int_any(rec, ("w", "w1"), 0)
        w2 = self._dict_int(rec, "w2", 0) if channels >= 5 else 0
        lower_r = self._dict_int(rec, "lower_r", 0)
        lower_g = self._dict_int(rec, "lower_g", 0)
        lower_b = self._dict_int(rec, "lower_b", 0)
        lower_w = self._dict_int_any(rec, ("lower_w", "lower_w1"), 0) if channels >= 4 else 0
        lower_w2 = self._dict_int(rec, "lower_w2", 0) if channels >= 5 else 0
        upper_r = self._dict_int(rec, "upper_r", self._dict_int(rec, "r", 0))
        upper_g = self._dict_int(rec, "upper_g", self._dict_int(rec, "g", 0))
        upper_b = self._dict_int(rec, "upper_b", self._dict_int(rec, "b", 0))
        upper_w = self._dict_int_any(rec, ("upper_w", "upper_w1"), w) if channels >= 4 else 0
        upper_w2 = self._dict_int(rec, "upper_w2", w2) if channels >= 5 else 0
        r16 = self._dict_int(rec, "r16", self._dict_int(rec, "r", 0) * 257)
        g16 = self._dict_int(rec, "g16", self._dict_int(rec, "g", 0) * 257)
        b16 = self._dict_int(rec, "b16", self._dict_int(rec, "b", 0) * 257)
        w16 = self._dict_int_any(rec, ("w16", "w1_16"), w * 257) if channels >= 4 else 0
        w2_16 = self._dict_int_any(rec, ("w2_16", "w216"), w2 * 257) if channels >= 5 else 0
        if mode == "blend8":
            r = upper_r
            g = upper_g
            b = upper_b
            w = upper_w if channels >= 4 else 0
            w2 = upper_w2 if channels >= 5 else 0
            r16 = upper_r * 257
            g16 = upper_g * 257
            b16 = upper_b * 257
            w16 = upper_w * 257 if channels >= 4 else 0
            w2_16 = upper_w2 * 257 if channels >= 5 else 0
        else:
            r = self._dict_int(rec, "r", self._q16_to_u8(r16))
            g = self._dict_int(rec, "g", self._q16_to_u8(g16))
            b = self._dict_int(rec, "b", self._q16_to_u8(b16))
            w = self._dict_int_any(rec, ("w", "w1"), self._q16_to_u8(w16)) if channels >= 4 else 0
            w2 = self._dict_int(rec, "w2", self._q16_to_u8(w2_16)) if channels >= 5 else 0
        return MeasurementPlanRow(
            name=str(rec.get("name", f"state_{len(self.measurement_rows):04d}")),
            r=r, g=g, b=b, w=w, w2=w2,
            bfi_r=self._dict_int(rec, "bfi_r", 0),
            bfi_g=self._dict_int(rec, "bfi_g", 0),
            bfi_b=self._dict_int(rec, "bfi_b", 0),
            bfi_w=self._dict_int_any(rec, ("bfi_w", "bfi_w1"), 0) if channels >= 4 else 0,
            bfi_w2=self._dict_int(rec, "bfi_w2", 0) if channels >= 5 else 0,
            repeats=max(1, self._dict_int(rec, "repeats", 1)),
            lower_r=lower_r, lower_g=lower_g, lower_b=lower_b, lower_w=lower_w, lower_w2=lower_w2,
            upper_r=upper_r, upper_g=upper_g, upper_b=upper_b, upper_w=upper_w, upper_w2=upper_w2,
            r16=r16, g16=g16, b16=b16, w16=w16, w2_16=w2_16,
            use_fill16=(mode == "fill16"), mode=mode, channels=channels,
        )

    def _csv_has_supported_plan_schema(self, fieldnames: list[str]) -> bool:
        names = {str(f).strip().lower() for f in fieldnames if f is not None}
        if "mode" in names:
            return True
        if {"name", "r", "g", "b"}.issubset(names):
            return True
        if {"name", "lower_r", "lower_g", "lower_b", "upper_r", "upper_g", "upper_b"}.issubset(names):
            return True
        if {"name", "r16", "g16", "b16"}.issubset(names):
            return True
        return False

    def _import_plan_csv_path(self, path: str | Path, *, confirm_replace: bool = True) -> bool:
        path = Path(path)
        if self.measurement_rows and confirm_replace and not messagebox.askyesno("Replace plan", "Replace the current plan and clear any saved resume progress?"):
            return False
        rows: list[MeasurementPlanRow] = []
        imported_true16 = 0
        imported_legacy = 0
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            if not self._csv_has_supported_plan_schema(fieldnames):
                messagebox.showerror(
                    "Import failed",
                    "Unsupported CSV schema. Accepted schemas: RGB, RGBW, or RGBWW/RGBCCT fill8; raw temporal blend8; True16 fill16; or generic rows with a mode column.",
                )
                return False
            for rec in reader:
                try:
                    row = self._row_from_record(rec, fieldnames=fieldnames)
                    self._validate_plan_row_compatible(row, context="plan import")
                    rows.append(row)
                    if row.normalized_mode() == "fill16":
                        imported_true16 += 1
                    else:
                        imported_legacy += 1
                except Exception as exc:
                    self.log_queue.put(f"[plan] import rejected row from {path}: {exc}")
                    messagebox.showerror("Import failed", f"Rejected row while importing {path.name}:\n\n{exc}")
                    return False
        self._clear_plan_rows()
        self._reset_resume_state()
        for row in rows:
            self.add_plan_row(row)
        if imported_true16 > 0:
            self.use_fill16_var.set(True)
        self.plan_source_path = path
        max_channels = max((row.channel_count() for row in rows), default=0)
        self.log_queue.put(
            f"[plan] imported {len(rows)} entries from {path} "
            f"(family={self._channel_label(max_channels) if max_channels else 'empty'}, legacy/blend8={imported_legacy}, fill16={imported_true16})"
        )
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
        self.send_fill(show_error=False)
        time.sleep(self.settle_delay_var.get())
        self.commit()
        time.sleep(self.settle_delay_var.get())

    def _render_plan_row(self, row: MeasurementPlanRow):
        self._validate_plan_row_compatible(row, context="plan render")
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
                out = {"ts": time.time(), "render": {"mode": self.manual_mode_var.get(), "r": self.r_var.get(), "g": self.g_var.get(), "b": self.b_var.get(), "w": self.w_var.get(), "w2": self.w2_var.get(), "lower_r": self.lower_r_var.get(), "lower_g": self.lower_g_var.get(), "lower_b": self.lower_b_var.get(), "lower_w": self.lower_w_var.get(), "lower_w2": self.lower_w2_var.get(), "r16": self.r16_var.get(), "g16": self.g16_var.get(), "b16": self.b16_var.get(), "w16": self.w16_var.get(), "w2_16": self.w2_16_var.get(), "bfi_r": self.bfi_r_var.get(), "bfi_g": self.bfi_g_var.get(), "bfi_b": self.bfi_b_var.get(), "bfi_w": self.bfi_w_var.get(), "bfi_w2": self.bfi_w2_var.get(), "use_fill16": self.use_fill16_var.get(), "phase_mode": self.phase_mode_var.get(), "phase": self.phase_var.get()}, "measurement": result}
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
        try:
            for row in self.measurement_rows:
                self._validate_plan_row_compatible(row, context="plan")
        except Exception as exc:
            messagebox.showerror("Plan rejected", str(exc))
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
                target_channels = self._selected_device_channel_count()
                self.log_queue.put(f"[plan] setting device output mode = {self._channel_label(target_channels)} before plan run")
                self._set_output_mode_for_strip_wait(target_channels, timeout_s=1.0)
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
                        self.root.after(0, lambda r=row: self.set_preview_values(r.r, r.g, r.b, r.w, r.bfi_r, r.bfi_g, r.bfi_b, r.bfi_w, r16=r.r16, g16=r.g16, b16=r.b16, w16=r.w16, w2_16=r.w2_16, mode=r.normalized_mode(), lower_r=r.lower_r, lower_g=r.lower_g, lower_b=r.lower_b, lower_w=r.lower_w, w2=r.w2, lower_w2=r.lower_w2, bfi_w2=r.bfi_w2))
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

    def _plan_save_fields(self) -> list[str]:
        max_channels = max((row.channel_count() for row in self.measurement_rows), default=4)
        fields = ["name", "mode", "channels", "repeats", "r", "g", "b"]
        if max_channels == 4:
            fields.append("w")
        elif max_channels >= 5:
            fields.extend(["w1", "w2"])
        fields.extend(["lower_r", "lower_g", "lower_b"])
        if max_channels == 4:
            fields.append("lower_w")
        elif max_channels >= 5:
            fields.extend(["lower_w1", "lower_w2"])
        fields.extend(["upper_r", "upper_g", "upper_b"])
        if max_channels == 4:
            fields.append("upper_w")
        elif max_channels >= 5:
            fields.extend(["upper_w1", "upper_w2"])
        fields.extend(["r16", "g16", "b16"])
        if max_channels == 4:
            fields.append("w16")
        elif max_channels >= 5:
            fields.extend(["w1_16", "w2_16"])
        fields.extend(["bfi_r", "bfi_g", "bfi_b"])
        if max_channels == 4:
            fields.append("bfi_w")
        elif max_channels >= 5:
            fields.extend(["bfi_w1", "bfi_w2"])
        fields.append("use_fill16")
        return fields

    def save_plan_csv(self):
        if not self.measurement_rows:
            messagebox.showinfo("Plan", "No plan rows to save.")
            return
        path = filedialog.asksaveasfilename(initialdir=str(self.capture_dir), defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        fields = self._plan_save_fields()
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in self.measurement_rows:
                data = row.to_dict()
                writer.writerow({field: data.get(field, "") for field in fields})
        max_channels = max(row.channel_count() for row in self.measurement_rows)
        self.log_queue.put(f"[plan] saved {path} ({self._channel_label(max_channels)})")

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
        self._verifier_summary_label = tk.StringVar(value="no summary loaded  (will auto-measure RGBW basis if enabled)")
        ttk.Label(sum_row, textvariable=self._verifier_summary_label, anchor="w").pack(side="left", padx=4, fill="x", expand=True)

        ref_row = ttk.Frame(box)
        ref_row.pack(fill="x", pady=2)
        ttk.Label(ref_row, text="Reference white:").pack(side="left", padx=(4, 2))
        ref_cb = ttk.Combobox(
            ref_row, textvariable=self._verifier_ref_white_preset_var,
            values=list(_VERIFIER_REFERENCE_WHITE_PRESETS.keys()),
            state="readonly", width=17,
        )
        ref_cb.pack(side="left", padx=2)
        ref_cb.bind("<<ComboboxSelected>>", lambda _evt: self._verifier_update_reference_white_fields())
        ttk.Label(ref_row, text="x").pack(side="left", padx=(10, 2))
        ttk.Entry(ref_row, textvariable=self._verifier_ref_white_x_var, width=8).pack(side="left", padx=2)
        ttk.Label(ref_row, text="y").pack(side="left", padx=(6, 2))
        ttk.Entry(ref_row, textvariable=self._verifier_ref_white_y_var, width=8).pack(side="left", padx=2)
        ttk.Button(ref_row, text="Apply preset", command=self._verifier_update_reference_white_fields).pack(side="left", padx=4)
        ttk.Checkbutton(ref_row, text="Auto-measure RGBW basis if summary missing", variable=self._verifier_auto_measure_basis_var).pack(side="left", padx=(12, 2))

        basis_row = ttk.Frame(box)
        basis_row.pack(fill="x", pady=2)
        ttk.Button(basis_row, text="Fetch Teensy DiodeProfile", command=self.fetch_teensy_diode_profile_async).pack(side="left", padx=4)
        ttk.Button(basis_row, text="Send DiodeProfile to Teensy", command=self.send_teensy_diode_profile_async).pack(side="left", padx=4)
        ttk.Button(basis_row, text="Measure diode basis now", command=self.measure_verifier_basis_async).pack(side="left", padx=4)

        # ── Options / actions ───────────────────────────────────────────────
        opt_row = ttk.Frame(box)
        opt_row.pack(fill="x", pady=(4, 2))
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
        ttk.Label(opt_row, text="Output:").pack(side="left", padx=(12, 2))
        source_cb = ttk.Combobox(
            opt_row, textvariable=self._verifier_output_source_var,
            values=VERIFIER_OUTPUT_SOURCES,
            state="readonly", width=18,
        )
        source_cb.pack(side="left", padx=2)
        ttk.Label(opt_row, text="Strip:").pack(side="left", padx=(12, 2))
        strip_cb = ttk.Combobox(
            opt_row, textvariable=self._verifier_strip_type_var,
            values=list(STRIP_TYPE_CHOICES.keys()),
            state="readonly", width=13,
        )
        strip_cb.pack(side="left", padx=2)
        ttk.Label(opt_row, text="Cube:").pack(side="left", padx=(12, 2))
        cube_cb = ttk.Combobox(
            opt_row, textvariable=self._verifier_cube_output_type_var,
            values=list(CUBE_OUTPUT_TYPE_CHOICES.keys()),
            state="readonly", width=13,
        )
        cube_cb.pack(side="left", padx=2)
        ttk.Label(opt_row, text="Model:").pack(side="left", padx=(12, 2))
        model_cb = ttk.Combobox(
            opt_row, textvariable=self._verifier_analytical_model_var,
            values=list(ANALYTICAL_MODEL_CHOICES.keys()),
            state="readonly", width=21,
        )
        model_cb.pack(side="left", padx=2)
        ttk.Label(opt_row, text="Dual edge:").pack(side="left", padx=(12, 2))
        dual_edge_cb = ttk.Combobox(
            opt_row, textvariable=self._verifier_dual_edge_policy_var,
            values=list(DUAL_EDGE_POLICY_CHOICES.keys()),
            state="readonly", width=20,
        )
        dual_edge_cb.pack(side="left", padx=2)

        opt_row2 = ttk.Frame(box)
        opt_row2.pack(fill="x", pady=(0, 2))
        ttk.Label(opt_row2, text="Target gamut:").pack(side="left", padx=(4, 2))
        gamut_cb = ttk.Combobox(
            opt_row2, textvariable=self._verifier_gamut_var,
            values=_VERIFIER_GAMUT_CHOICES,
            state="readonly", width=14,
        )
        gamut_cb.pack(side="left", padx=2)
        ttk.Label(opt_row2, text="Transfer:").pack(side="left", padx=(12, 2))
        transfer_cb = ttk.Combobox(
            opt_row2, textvariable=self._verifier_transfer_var,
            values=_VERIFIER_TRANSFER_CHOICES,
            state="readonly", width=7,
        )
        transfer_cb.pack(side="left", padx=2)
        ttk.Checkbutton(opt_row2, text="Project OOH xy", variable=self._verifier_project_hull_var).pack(side="left", padx=(10, 2))
        ttk.Button(opt_row2, text="Get FastLED gamut", command=self.fetch_fastled_input_gamut_async).pack(side="left", padx=(10, 2))
        ttk.Label(opt_row2, textvariable=self._fastled_input_gamut_status_var, anchor="w").pack(side="left", padx=(4, 2), fill="x", expand=True)

        self._verifier_mode_note_var = tk.StringVar(value="")
        note_row = ttk.Frame(box)
        note_row.pack(fill="x", pady=(0, 2))
        ttk.Label(
            note_row,
            textvariable=self._verifier_mode_note_var,
            anchor="w",
            justify="left",
            wraplength=1300,
        ).pack(side="left", padx=(4, 2), fill="x", expand=True)

        def _update_verifier_mode_note(*_):
            if self._verifier_output_source_var.get() == VERIFIER_OUTPUT_SOURCES[0]:
                self._verifier_mode_note_var.set(
                    "3D LUT output selected: interpolation and cube output type apply; analytical model and dual-edge controls are ignored. "
                    "Cube output channels must fit within the selected strip type."
                )
            else:
                self._verifier_mode_note_var.set(
                    "FastLED analytical MCU output selected: analytical model and dual-edge policy apply; interpolation is ignored. "
                    "Target gamut is sent to the Teensy as FastLED input gamut before verification."
                )

        source_cb.bind("<<ComboboxSelected>>", _update_verifier_mode_note)
        _update_verifier_mode_note()

        def _update_patch_count(*_):
            patches = _generate_verifier_patches(self._verifier_preset_var.get())
            self._verifier_patch_count_var.set(f"{len(patches)} patches")
        self._verifier_preset_var.trace_add("write", _update_patch_count)

        action_row = ttk.Frame(box)
        action_row.pack(fill="x", pady=(0, 4))
        ttk.Button(action_row, text="Run Verification", command=self.run_verification).pack(side="left", padx=(4, 4))
        ttk.Button(action_row, text="Stop", command=self.stop_verification).pack(side="left", padx=4)
        ttk.Button(action_row, text="Export CSV", command=self.export_verifier_csv).pack(side="left", padx=4)
        self._verifier_status_var = tk.StringVar(value="idle")
        ttk.Label(action_row, textvariable=self._verifier_status_var, anchor="w").pack(side="left", padx=12, fill="x", expand=True)

        # ── Results table ────────────────────────────────────────────────────
        vcols = ("patch", "input_rgb16", "lut_rgbw16", "path", "strict_rgbw16", "lp_rgbw16", "w_pct",
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
            "path":        (110, "center", False),
            "strict_rgbw16": (175, "center", True),
            "lp_rgbw16":    (175, "center", True),
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
        self._vtree.heading("lut_rgbw16", text="output_rgbw16")
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
            if cube.ndim != 4 or cube.shape[3] < 3:
                raise ValueError(f"Expected shape (N,N,N,C) with C>=3, got {cube.shape}")
            if cube.shape[3] > 5:
                raise ValueError(
                    f"Loaded cube has {cube.shape[3]} output channels. "
                    "This GUI currently supports RGB, RGBW, and RGBWW/RGBCCT cubes."
                )
            if not (cube.shape[0] == cube.shape[1] == cube.shape[2]):
                raise ValueError(f"LUT cube must be cubic, got {cube.shape[:3]}")
            self.verifier_lut = cube.astype(np.uint16)
            self.verifier_lut_channel_count = int(cube.shape[3])
            N = cube.shape[0]
            channels = self.verifier_lut_channel_count
            family = "RGB" if channels == 3 else "RGBW" if channels == 4 else "RGBWW/RGBCCT"
            self._verifier_lut_label.set(f"{Path(path).name}  ({N}³ {family} cube, {channels} channels)")
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
            self._verifier_update_reference_white_fields()
            self.log_queue.put(
                f"[verifier] loaded summary: {path}  "
                f"gamut={self._verifier_gamut_var.get()}  "
                f"transfer={self._verifier_transfer_var.get()}  "
                f"interp={self._verifier_interp_var.get()}  "
                f"ref_white=({self._verifier_ref_white()[0]:.4f},{self._verifier_ref_white()[1]:.4f})"
            )
        except Exception as exc:
            messagebox.showerror("Load summary failed", str(exc))

    def _summary_reference_white_xy(self) -> tuple[float, float] | None:
        """Reference white from loaded summary, or None when unavailable/invalid."""
        try:
            ref_xy = self.verifier_summary.get("basis_sanity", {}).get("reference_white_xy")
            if ref_xy is None:
                ref_xy = self.verifier_summary.get("reference_white_xy")
            if ref_xy is None or len(ref_xy) < 2:
                return None
            x = float(ref_xy[0])
            y = float(ref_xy[1])
            if not (math.isfinite(x) and math.isfinite(y) and x > 0 and y > 0 and x + y < 1.0):
                return None
            return x, y
        except Exception:
            return None

    def _verifier_update_reference_white_fields(self) -> None:
        """Apply selected reference-white preset to the editable xy fields."""
        preset = self._verifier_ref_white_preset_var.get()
        if preset == "Custom":
            return
        xy = None
        if preset == "Summary/default":
            xy = self._summary_reference_white_xy()
            if xy is None:
                xy = _VERIFIER_D65_XY
        else:
            xy = _VERIFIER_REFERENCE_WHITE_PRESETS.get(preset)
        if xy is None:
            xy = _VERIFIER_D65_XY
        try:
            self._verifier_ref_white_x_var.set(float(xy[0]))
            self._verifier_ref_white_y_var.set(float(xy[1]))
        except Exception:
            self._verifier_ref_white_x_var.set(_VERIFIER_D65_XY[0])
            self._verifier_ref_white_y_var.set(_VERIFIER_D65_XY[1])

    def _verifier_ref_white(self) -> tuple[float, float]:
        """Reference white (x, y) from verifier UI, summary, or D65 fallback."""
        preset = self._verifier_ref_white_preset_var.get()
        if preset == "Summary/default":
            summary_xy = self._summary_reference_white_xy()
            if summary_xy is not None:
                return summary_xy
        elif preset != "Custom":
            xy = _VERIFIER_REFERENCE_WHITE_PRESETS.get(preset)
            if xy is not None:
                return float(xy[0]), float(xy[1])
        try:
            x = float(self._verifier_ref_white_x_var.get())
            y = float(self._verifier_ref_white_y_var.get())
            if math.isfinite(x) and math.isfinite(y) and x > 0 and y > 0 and x + y < 1.0:
                return x, y
        except Exception:
            pass
        return _VERIFIER_D65_XY

    def _verifier_has_rgb_basis(self) -> bool:
        basis_data = self.verifier_summary.get("basis_xyz_per_q16", {}) if isinstance(self.verifier_summary, dict) else {}
        return all(k in basis_data for k in ("r16", "g16", "b16"))

    def _measurement_result_to_xyz(self, result: dict[str, object]) -> np.ndarray | None:
        """Extract XYZ from spotread result, accepting either XYZ or xyY output."""
        try:
            xyz_obj = result.get("XYZ")
            if isinstance(xyz_obj, dict):
                X = float(xyz_obj["X"])
                Y = float(xyz_obj["Y"])
                Z = float(xyz_obj["Z"])
                if all(math.isfinite(v) for v in (X, Y, Z)) and Y > 0:
                    return np.array([X, Y, Z], dtype=float)
            if all(k in result for k in ("X", "Z")) and ("Y_from_XYZ" in result or "Y" in result):
                X = float(result["X"])
                Y = float(result.get("Y_from_XYZ", result.get("Y")))
                Z = float(result["Z"])
                if all(math.isfinite(v) for v in (X, Y, Z)) and Y > 0:
                    return np.array([X, Y, Z], dtype=float)
            xyY_obj = result.get("xyY")
            if isinstance(xyY_obj, dict):
                x = float(xyY_obj["x"])
                y = float(xyY_obj["y"])
                Y = float(xyY_obj["Y"])
                xyz = _verifier_xyY_to_XYZ((x, y), Y)
                if float(xyz[1]) > 0:
                    return xyz
            if all(k in result for k in ("x", "y", "Y")):
                xyz = _verifier_xyY_to_XYZ((float(result["x"]), float(result["y"])), float(result["Y"]))
                if float(xyz[1]) > 0:
                    return xyz
        except Exception:
            return None
        return None

    def _render_verifier_basis_channel(self, ch: str) -> None:
        values = {"R": 0, "G": 0, "B": 0, "W": 0}
        values[ch] = 65535
        self._send_rgbw16_direct_or_fill16([values[c] for c in "RGBW"], timeout_s=0.8)
        time.sleep(float(self.settle_delay_var.get()))
        self.device.send_frame(KIND_CAL_REQ, bytes([OP_COMMIT]))
        time.sleep(float(self.settle_delay_var.get()))

    def fetch_teensy_diode_profile_async(self) -> None:
        if self.verifier_running:
            messagebox.showinfo("LUT Verifier", "Stop the active verification before fetching the Teensy DiodeProfile.")
            return
        if not self.device.is_connected():
            messagebox.showerror("LUT Verifier", "Connect the Teensy device first.")
            return

        def worker() -> None:
            try:
                self.root.after(0, lambda: self._verifier_status_var.set("fetching Teensy DiodeProfile"))
                self._fetch_teensy_diode_profile_basis()
                self.root.after(0, lambda: self._verifier_status_var.set("Teensy DiodeProfile loaded"))
            except Exception as exc:
                self.log_queue.put(f"[verifier] Teensy DiodeProfile fetch failed: {exc}")
                self.root.after(0, lambda e=str(exc): messagebox.showerror("Fetch Teensy DiodeProfile failed", e))
                self.root.after(0, lambda: self._verifier_status_var.set("DiodeProfile fetch failed"))

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_teensy_diode_profile_basis(self) -> dict[str, np.ndarray]:
        """Fetch the active Teensy profile and install it into verifier summary state."""
        msg = self._send_cal_request_wait(bytes([OP_GET_DIODE_PROFILE]), OP_GET_DIODE_PROFILE, timeout_s=2.0)
        if msg is None:
            raise RuntimeError("no response to OP_GET_DIODE_PROFILE")
        if int(msg.get("status", 255)) != 0:
            raise RuntimeError(f"Teensy returned {_status_name(msg.get('status'))} for OP_GET_DIODE_PROFILE")
        profile = msg.get("diode_profile")
        if not isinstance(profile, dict):
            raise RuntimeError("Teensy response did not include a DiodeProfile payload; update the FastLED verifier sketch")
        self._last_teensy_diode_profile = profile
        return self._install_diode_profile_summary(profile, "teensy_diode_profile")

    def _install_diode_profile_summary(self, profile: dict[str, object], source: str) -> dict[str, np.ndarray]:
        channel_order = profile.get("channel_order")
        if not isinstance(channel_order, list) or len(channel_order) < 3:
            raise RuntimeError(f"DiodeProfile channel order {channel_order} is invalid")

        primaries_xy = profile.get("primaries_xy")
        relative_y = profile.get("relative_y")
        if not isinstance(primaries_xy, dict) or not isinstance(relative_y, dict):
            raise RuntimeError("malformed DiodeProfile payload")

        basis: dict[str, np.ndarray] = {}
        clean_xy: dict[str, list[float]] = {}
        max_y: dict[str, float] = {}
        for ch in channel_order:
            if ch not in primaries_xy or ch not in relative_y:
                raise RuntimeError(f"DiodeProfile is missing channel data for {ch}")
            xy = primaries_xy[ch]
            x = float(xy[0])
            y = float(xy[1])
            rel_y = float(relative_y[ch])
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(rel_y) and x > 0.0 and y > 0.0 and x + y < 1.0 and rel_y > 0.0):
                raise RuntimeError(f"invalid DiodeProfile basis for {ch}: xy=({x},{y}) Y={rel_y}")
            clean_xy[ch] = [x, y]
            max_y[ch] = rel_y
            basis[ch] = _verifier_xyY_to_XYZ((x, y), rel_y)

        if not all(ch in basis for ch in ("R", "G", "B")):
            raise RuntimeError("DiodeProfile must include R,G,B channels")

        ref_x, ref_y = self._verifier_ref_white()
        basis_xyz_per_q16 = {
            "r16": basis["R"].tolist(),
            "g16": basis["G"].tolist(),
            "b16": basis["B"].tolist(),
        }
        if "W" in basis:
            basis_xyz_per_q16["w16"] = basis["W"].tolist()
        if "WW" in basis:
            basis_xyz_per_q16["ww16"] = basis["WW"].tolist()
        if "WC" in basis:
            basis_xyz_per_q16["wc16"] = basis["WC"].tolist()

        self.verifier_summary = {
            "basis_xyz_per_q16": basis_xyz_per_q16,
            "basis_sanity": {
                "reference_white_xy": [float(ref_x), float(ref_y)],
                "basis_source": source,
                "basis_units": "relative_Y",
                "channel_order": channel_order,
            },
            "settings": {
                "target_white_balance_mode": "reference-white",
                "input_transfer": self._verifier_transfer_var.get(),
                "gamut": self._verifier_gamut_var.get(),
            },
            "primaries_xy": clean_xy,
            "max_Y": max_y,
            "channel_order": channel_order,
            "reference_white_xy": [float(ref_x), float(ref_y)],
            "generated_by": f"host_calibration_gui_{source}",
            "generated_at": time.time(),
        }
        basis_label = f"{source} basis ({','.join(channel_order)})  " + " ".join(
            f"{ch}=({clean_xy[ch][0]:.4f},{clean_xy[ch][1]:.4f})" for ch in channel_order
        )
        self.root.after(0, lambda label=basis_label: self._verifier_summary_label.set(label))
        self.log_queue.put(
            f"[verifier] {source} basis loaded: "
            + " ".join(f"{ch}=({clean_xy[ch][0]:.5f},{clean_xy[ch][1]:.5f}) relY={max_y[ch]:.6f}" for ch in channel_order)
        )
        return basis

    def _extract_diode_profile_from_summary(self) -> dict[str, object]:
        basis = self._verifier_model_projection_basis_xyz()
        if basis is None:
            raise RuntimeError("no basis available; load a summary, fetch a profile, or measure the diode basis first")
        primaries_xy: dict[str, list[float]] = {}
        relative_y: dict[str, float] = {}
        for ch in "RGBW":
            xy = _verifier_xyz_to_xy_tuple(basis[ch])
            if xy is None:
                raise RuntimeError(f"invalid basis xy for {ch}")
            rel_y = float(basis[ch][1])
            if not (math.isfinite(xy[0]) and math.isfinite(xy[1]) and xy[0] > 0.0 and xy[1] > 0.0 and xy[0] + xy[1] < 1.0 and math.isfinite(rel_y) and rel_y > 0.0):
                raise RuntimeError(f"basis cannot be encoded as DiodeProfile for {ch}")
            primaries_xy[ch] = [float(xy[0]), float(xy[1])]
            relative_y[ch] = rel_y
        return {
            "version": 1,
            "format": "u32be_q1e6",
            "primaries_xy": primaries_xy,
            "relative_y": relative_y,
            "channel_order": list("RGBW"),
            "source": "host_summary",
        }

    def _encode_set_diode_profile_payload(self, profile: dict[str, object]) -> bytes:
        primaries_xy = profile.get("primaries_xy")
        relative_y = profile.get("relative_y")
        channel_order = profile.get("channel_order")
        if not isinstance(primaries_xy, dict) or not isinstance(relative_y, dict):
            raise RuntimeError("profile lacks primaries_xy/relative_y")

        if channel_order == ["R", "G", "B", "WW", "WC"]:
            format_id = 2
            channels = ["R", "G", "B", "WW", "WC"]
        else:
            format_id = 1
            channels = list("RGBW")

        payload = bytearray([OP_SET_DIODE_PROFILE])
        payload.extend(b"DPRF")
        payload.append(1)
        payload.append(format_id)
        for ch in channels:
            xy = primaries_xy[ch]
            x = float(xy[0])
            y = float(xy[1])
            rel_y = float(relative_y[ch])
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(rel_y) and x > 0.0 and y > 0.0 and x + y < 1.0 and rel_y > 0.0):
                raise RuntimeError(f"invalid DiodeProfile value for {ch}: xy=({x},{y}) Y={rel_y}")
            payload.extend(_pack_u32_be_q1e6(x))
            payload.extend(_pack_u32_be_q1e6(y))
            payload.extend(_pack_u32_be_q1e6(rel_y))
        return bytes(payload)

    def send_teensy_diode_profile_async(self) -> None:
        if self.verifier_running:
            messagebox.showinfo("LUT Verifier", "Stop the active verification before sending a DiodeProfile.")
            return
        if not self.device.is_connected():
            messagebox.showerror("LUT Verifier", "Connect the Teensy device first.")
            return

        def worker() -> None:
            try:
                self.root.after(0, lambda: self._verifier_status_var.set("sending DiodeProfile to Teensy"))
                selected_model = self._verifier_analytical_model_var.get()
                if selected_model == "rgbww_overdrive":
                    profile = self._last_teensy_diode_profile
                    if not isinstance(profile, dict) or profile.get("channel_order") != ["R", "G", "B", "WW", "WC"]:
                        raise RuntimeError(
                            "RGBWW model selected: no RGBWW DiodeProfile payload is available to send. "
                            "Fetch Teensy DiodeProfile while RGBWW mode is active, or keep using default FastLED RGBWW profile values."
                        )
                else:
                    profile = self._extract_diode_profile_from_summary()
                payload = self._encode_set_diode_profile_payload(profile)
                msg = self._send_cal_request_wait(payload, OP_SET_DIODE_PROFILE, timeout_s=2.0)
                if msg is None:
                    raise RuntimeError("no response to OP_SET_DIODE_PROFILE")
                status = int(msg.get("status", 255))
                if status != STATUS_OK:
                    raise RuntimeError(f"Teensy returned {_status_name(status)} for OP_SET_DIODE_PROFILE")
                response_profile = msg.get("diode_profile")
                if isinstance(response_profile, dict):
                    self._last_teensy_diode_profile = response_profile
                    self._install_diode_profile_summary(response_profile, "teensy_diode_profile_set_response")
                self.root.after(0, lambda: self._verifier_status_var.set("DiodeProfile sent"))
            except Exception as exc:
                self.log_queue.put(f"[verifier] DiodeProfile send failed: {exc}")
                self.root.after(0, lambda e=str(exc): messagebox.showerror("Send DiodeProfile failed", e))
                self.root.after(0, lambda: self._verifier_status_var.set("DiodeProfile send failed"))

        threading.Thread(target=worker, daemon=True).start()

    def measure_verifier_basis_async(self) -> None:
        if self.verifier_running:
            messagebox.showinfo("LUT Verifier", "Stop the active verification before measuring the diode basis.")
            return
        if not self.device.is_connected():
            messagebox.showerror("LUT Verifier", "Connect the Teensy device first.")
            return

        def worker() -> None:
            try:
                self.root.after(0, lambda: self._verifier_status_var.set("measuring RGBW diode basis"))
                self._measure_verifier_rgbw_basis()
                self.root.after(0, lambda: self._verifier_status_var.set("basis measured"))
            except Exception as exc:
                self.log_queue.put(f"[verifier] diode basis measurement failed: {exc}")
                self.root.after(0, lambda e=str(exc): messagebox.showerror("Measure diode basis failed", e))
                self.root.after(0, lambda: self._verifier_status_var.set("basis measurement failed"))

        threading.Thread(target=worker, daemon=True).start()

    def _measure_verifier_rgbw_basis(self) -> dict[str, np.ndarray]:
        """Measure full-drive R/G/B/W and install them as verifier fallback basis."""
        basis: dict[str, np.ndarray] = {}
        basis_xy: dict[str, list[float]] = {}
        max_y: dict[str, float] = {}
        command = self._spotread_command_for_request({"measurement_format": "xyzxy"})

        for ch in "RGBW":
            if self.verifier_stop_event.is_set():
                raise RuntimeError("basis measurement stopped")
            self.log_queue.put(f"[verifier] measuring fallback diode basis {ch}=65535")
            self.root.after(0, lambda c=ch: self._verifier_status_var.set(f"measuring diode basis {c}"))
            self._render_verifier_basis_channel(ch)
            result = self.argyll.run_spotread(
                command,
                timeout_s=float(self.timeout_var.get()),
                send_trigger_newline=bool(self.send_newline_var.get()),
                cleanup_first=bool(self.cleanup_first_var.get()),
            )
            if not result.get("ok"):
                raise RuntimeError(f"spotread failed while measuring {ch} basis")
            xyz = self._measurement_result_to_xyz(result)
            if xyz is None:
                raise RuntimeError(f"could not parse XYZ/xyY for {ch} basis")
            basis[ch] = xyz
            xy = _verifier_xyz_to_xy_tuple(xyz)
            if xy is None:
                raise RuntimeError(f"invalid xy for {ch} basis")
            basis_xy[ch] = [float(xy[0]), float(xy[1])]
            max_y[ch] = float(xyz[1])
            self.last_measurement = result
            self.root.after(0, lambda res=result: self.measurement_text.set(
                f"last basis measurement: Y={res.get('Y')} x={res.get('x')} y={res.get('y')}"
            ))

        ref_x, ref_y = self._verifier_ref_white()
        self.verifier_summary = {
            "basis_xyz_per_q16": {
                "r16": basis["R"].tolist(),
                "g16": basis["G"].tolist(),
                "b16": basis["B"].tolist(),
                "w16": basis["W"].tolist(),
            },
            "basis_sanity": {
                "reference_white_xy": [float(ref_x), float(ref_y)],
                "basis_source": "verifier_full_drive_spotread",
            },
            "settings": {
                "target_white_balance_mode": "reference-white",
                "input_transfer": self._verifier_transfer_var.get(),
                "gamut": self._verifier_gamut_var.get(),
            },
            "primaries_xy": basis_xy,
            "max_Y": max_y,
            "generated_by": "host_calibration_gui_verifier_fallback_basis",
            "generated_at": time.time(),
        }
        basis_label = (
            "measured fallback RGBW basis  "
            f"R=({basis_xy['R'][0]:.4f},{basis_xy['R'][1]:.4f}) "
            f"G=({basis_xy['G'][0]:.4f},{basis_xy['G'][1]:.4f}) "
            f"B=({basis_xy['B'][0]:.4f},{basis_xy['B'][1]:.4f}) "
            f"W=({basis_xy['W'][0]:.4f},{basis_xy['W'][1]:.4f})"
        )
        self.root.after(0, lambda label=basis_label: self._verifier_summary_label.set(label))
        self.log_queue.put(
            "[verifier] fallback basis measured: "
            + " ".join(f"{ch}=({basis_xy[ch][0]:.5f},{basis_xy[ch][1]:.5f}) Y={max_y[ch]:.3f}" for ch in "RGBW")
        )
        return basis

    def _ensure_verifier_basis_for_expected_xy(self) -> None:
        """Fetch or measure basis when no summary/basis is available."""
        if self._verifier_has_rgb_basis():
            return
        if self.device.is_connected():
            try:
                self.log_queue.put("[verifier] no summary basis available; fetching Teensy DiodeProfile")
                self._fetch_teensy_diode_profile_basis()
                return
            except Exception as exc:
                self.log_queue.put(f"[verifier] Teensy DiodeProfile fallback unavailable: {exc}")
        if not bool(self._verifier_auto_measure_basis_var.get()):
            return
        self.log_queue.put("[verifier] no summary/profile basis available; auto-measuring full-drive R/G/B/W")
        self._measure_verifier_rgbw_basis()

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
        """Return RGBW-like basis XYZ for model-style expected-xy projection.

        Preferred source is basis_xyz_per_q16 when it includes w16.  Model LUT
        summaries may instead provide primaries_xy + max_Y, so support that too.
        If only RGB basis data exists, fall back to the older nearest-xy hull
        projection because the RGBW model's RGW/RBW/BGW NNLS cannot be matched
        without W.
        """
        basis_data = self.verifier_summary.get("basis_xyz_per_q16", {})
        if not all(k in basis_data for k in ("r16", "g16", "b16")):
            return None

        key_map = {"R": "r16", "G": "g16", "B": "b16", "W": "w16"}
        if all(k in basis_data for k in key_map.values()):
            try:
                return {
                    ch: np.array(basis_data[key], dtype=float)
                    for ch, key in key_map.items()
                }
            except Exception:
                return None

        # RGBWW basis: synthesize an effective W as average of WW and WC for
        # RGBW-style model projection helpers.
        if all(k in basis_data for k in ("ww16", "wc16")):
            try:
                ww = np.array(basis_data["ww16"], dtype=float)
                wc = np.array(basis_data["wc16"], dtype=float)
                return {
                    "R": np.array(basis_data["r16"], dtype=float),
                    "G": np.array(basis_data["g16"], dtype=float),
                    "B": np.array(basis_data["b16"], dtype=float),
                    "W": 0.5 * (ww + wc),
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

        if all(ch in prim_xy and ch in max_y for ch in ("R", "G", "B", "WW", "WC")):
            try:
                ww = _verifier_xyY_to_XYZ(tuple(prim_xy["WW"]), float(max_y["WW"]))
                wc = _verifier_xyY_to_XYZ(tuple(prim_xy["WC"]), float(max_y["WC"]))
                return {
                    "R": _verifier_xyY_to_XYZ(tuple(prim_xy["R"]), float(max_y["R"])),
                    "G": _verifier_xyY_to_XYZ(tuple(prim_xy["G"]), float(max_y["G"])),
                    "B": _verifier_xyY_to_XYZ(tuple(prim_xy["B"]), float(max_y["B"])),
                    "W": 0.5 * (ww + wc),
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
        output_source = self._verifier_output_source_var.get()
        using_lut = output_source == VERIFIER_OUTPUT_SOURCES[0]
        if using_lut and self.verifier_lut is None:
            messagebox.showerror("LUT Verifier", "Load a LUT .npy file first.")
            return
        if not self.device.is_connected():
            messagebox.showerror("LUT Verifier", "Connect the Teensy device first.")
            return
        if self.verifier_running:
            messagebox.showinfo("LUT Verifier", "Verification is already running.")
            return
        strip_channels = self._selected_strip_channel_count()
        cube_output_channels: int | None = None
        if using_lut:
            try:
                cube_output_channels = self._selected_cube_output_channel_count()
                self._validate_output_family(cube_output_channels, strip_channels)
            except Exception as exc:
                messagebox.showerror("LUT output type", str(exc))
                return
        fastled_input_gamut_id: int | None = None
        if not using_lut:
            try:
                fastled_input_gamut_id = self._fastled_input_gamut_id_for_selection()
            except Exception as exc:
                messagebox.showerror("FastLED input gamut", str(exc))
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
            self._set_output_mode_for_strip_wait(strip_channels, timeout_s=1.0)
            if fastled_input_gamut_id is not None:
                msg = self._set_fastled_input_gamut_wait(fastled_input_gamut_id, timeout_s=1.0)
                gamut_name = FASTLED_INPUT_GAMUT_NAMES.get(int(msg.get("input_gamut", fastled_input_gamut_id)), str(fastled_input_gamut_id))
                self.log_queue.put(f"[verifier] FastLED input gamut set to {gamut_name}")
            self._ensure_verifier_basis_for_expected_xy()
            ref_x, ref_y = self._verifier_ref_white()
            patches = _generate_verifier_patches(self._verifier_preset_var.get())
            total = len(patches)
            try:
                for idx, (name, r16, g16, b16) in enumerate(patches):
                    if self.verifier_stop_event.is_set():
                        break

                    self.root.after(0, lambda s=f"running {idx + 1}/{total}: {name}":
                                    self._verifier_status_var.set(s))

                    output_source = self._verifier_output_source_var.get()
                    analytical_model = self._verifier_analytical_model_var.get()
                    analytical_dual_edge_policy = self._verifier_dual_edge_policy_var.get()
                    using_lut_row = output_source == VERIFIER_OUTPUT_SOURCES[0]
                    interpolation_mode = self._verifier_interp_var.get() if using_lut_row else "disabled"
                    cal_msg: dict[str, object] = {}
                    solve_path = ""
                    strict_tuple = None
                    lp_tuple = None

                    # --- Resolve output channels ---
                    if using_lut_row:
                        raw_output = _lut_lookup(self.verifier_lut, r16, g16, b16, self._verifier_interp_var.get())  # type: ignore[arg-type]
                        output_count = cube_output_channels if cube_output_channels is not None else len(raw_output)
                        lr, lg, lb, lw, lw2 = self._expand_output_channels(raw_output, output_count)
                    else:
                        model_id = ANALYTICAL_MODEL_CHOICES.get(analytical_model, ANALYTICAL_MODEL_RGBW_STRICT_SUB_GAMUT)
                        dual_edge_policy_id = DUAL_EDGE_POLICY_CHOICES.get(
                            analytical_dual_edge_policy,
                            DUAL_EDGE_POLICY_Y_CORRECT_CLIP,
                        )
                        payload = bytearray([OP_SET_ANALYTICAL_RGB16, model_id & 0xFF])
                        for v in [r16, g16, b16]:
                            payload.extend(self._pack_u16(v))
                        payload.append(dual_edge_policy_id & 0xFF)
                        cal_msg = self._send_cal_request_wait(bytes(payload), OP_SET_ANALYTICAL_RGB16, timeout_s=1.5)
                        if not isinstance(cal_msg, dict) or cal_msg.get("status") != 0:
                            status = cal_msg.get("status") if isinstance(cal_msg, dict) else None
                            raise RuntimeError(f"analytical MCU solve failed status={_status_name(status)}")
                        solved_channels = cal_msg.get("solved_channels16") if isinstance(cal_msg, dict) else None
                        solved = cal_msg.get("solved_rgbw16") if isinstance(cal_msg, dict) else None
                        if isinstance(solved_channels, list) and len(solved_channels) >= 4:
                            lr, lg, lb, lw = [int(v) for v in solved_channels[:4]]
                            lw2 = int(solved_channels[4]) if len(solved_channels) >= 5 else 0
                        elif isinstance(solved, list) and len(solved) == 4:
                            lr, lg, lb, lw = [int(v) for v in solved]
                            lw2 = int(cal_msg.get("last_solved_w2_16", 0)) if isinstance(cal_msg, dict) else 0
                        else:
                            raise RuntimeError("analytical MCU response did not include solved_rgbw16")
                        solve_path = ANALYTICAL_SOLVE_PATHS.get(int(cal_msg.get("analytical_solve_path", -1)), "unknown")
                        analytical_dual_edge_policy = DUAL_EDGE_POLICY_NAMES.get(
                            int(cal_msg.get("analytical_dual_edge_policy", dual_edge_policy_id)),
                            analytical_dual_edge_policy,
                        )
                        strict_tuple = cal_msg.get("analytical_strict_rgbw16")
                        lp_tuple = cal_msg.get("analytical_lp_rgbw16")
                    w_total = lr + lg + lb + lw + lw2
                    w_pct = ((lw + lw2) / w_total * 100.0) if w_total > 0 else 0.0

                    # Insert placeholder row while measuring
                    iid = f"vrow_{idx}"
                    self.root.after(0, lambda i=iid, n=name, r=r16, g=g16, b=b16,
                                    lr_=lr, lg_=lg, lb_=lb, lw_=lw, lw2_=lw2, wp=w_pct:
                                    self._vtree.insert(
                                        "", "end", iid=i,
                                        values=(n, f"{r}/{g}/{b}",
                                                f"{lr_}/{lg_}/{lb_}/{lw_}" + (f"/{lw2_}" if lw2_ > 0 else ""),
                                                "...",
                                            "...", "...",
                                                f"{wp:.1f}%",
                                                "...", "...", "...",
                                                "-", "-", "-", "-", "⏳"),
                                        tags=("running",),
                                    ))
                    self.root.after(0, lambda i=iid: self._vtree.see(i))

                    # --- Send RGBW fill16, unless the analytical opcode already rendered it ---
                    if using_lut_row:
                        self._send_output_channels_direct((lr, lg, lb, lw, lw2), strip_channels, timeout_s=0.8)
                        time.sleep(self.settle_delay_var.get())
                    else:
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
                        "lut_r16": lr, "lut_g16": lg, "lut_b16": lb, "lut_w16": lw, "lut_w2_16": lw2,
                        "output_source": output_source,
                        "active_output_mode": cal_msg.get("active_output_mode") if isinstance(cal_msg, dict) else None,
                        "supported_output_mode_bitmask": cal_msg.get("supported_output_mode_bitmask") if isinstance(cal_msg, dict) else None,
                        "physical_output_channel_count": cal_msg.get("physical_output_channel_count") if isinstance(cal_msg, dict) else None,
                        "active_logical_channel_count": cal_msg.get("active_logical_channel_count") if isinstance(cal_msg, dict) else None,
                        "selected_strip_type": self._verifier_strip_type_var.get(),
                        "selected_strip_channel_count": strip_channels,
                        "selected_cube_output_type": self._verifier_cube_output_type_var.get() if using_lut_row else "",
                        "selected_cube_output_channel_count": cube_output_channels if using_lut_row else None,
                        "last_input_w16": cal_msg.get("last_input_w16") if isinstance(cal_msg, dict) else None,
                        "last_input_w2_16": cal_msg.get("last_input_w2_16") if isinstance(cal_msg, dict) else None,
                        "last_solved_w2_16": cal_msg.get("last_solved_w2_16") if isinstance(cal_msg, dict) else None,
                        "fold_stub_flag": cal_msg.get("fold_stub_flag") if isinstance(cal_msg, dict) else None,
                        "response_extension_version": cal_msg.get("response_extension_version") if isinstance(cal_msg, dict) else None,
                        "analytical_model": analytical_model if not using_lut_row else "",
                        "analytical_dual_edge_policy": analytical_dual_edge_policy if not using_lut_row else "",
                        "analytical_solve_path": solve_path if not using_lut_row else "",
                        "analytical_strict_ok": cal_msg.get("analytical_strict_ok") if not using_lut_row else None,
                        "analytical_strict_rgbw16": "/".join(str(int(v)) for v in strict_tuple) if isinstance(strict_tuple, list) else "",
                        "analytical_lp_rgbw16": "/".join(str(int(v)) for v in lp_tuple) if isinstance(lp_tuple, list) else "",
                        "mcu_solved_r16": lr if not using_lut_row else None,
                        "mcu_solved_g16": lg if not using_lut_row else None,
                        "mcu_solved_b16": lb if not using_lut_row else None,
                        "mcu_solved_w16": lw if not using_lut_row else None,
                        "mcu_solved_w2_16": lw2 if not using_lut_row else None,
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
                        "interpolation": interpolation_mode,
                        "expected_gamut": self._verifier_gamut_var.get(),
                        "verification_gamut": self._verifier_gamut_var.get(),
                        "input_transfer": self._verifier_transfer_var.get(),
                        "reference_white_x": ref_x,
                        "reference_white_y": ref_y,
                        "reference_white_preset": self._verifier_ref_white_preset_var.get(),
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
                            f"{rd['lut_r16']}/{rd['lut_g16']}/{rd['lut_b16']}/{rd['lut_w16']}" + (f"/{rd['lut_w2_16']}" if int(rd.get('lut_w2_16') or 0) > 0 else ""),
                            rd.get("analytical_solve_path", ""),
                            rd.get("analytical_strict_rgbw16", ""),
                            rd.get("analytical_lp_rgbw16", ""),
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
                        f"source={output_source} "
                        f"model={analytical_model if not using_lut_row else 'lut'} "
                        f"dual_edge={analytical_dual_edge_policy if not using_lut_row else 'n/a'} "
                        f"path={row_data.get('analytical_solve_path', '') if not using_lut_row else 'lut'} "
                        f"strict={row_data.get('analytical_strict_rgbw16', '') if not using_lut_row else ''} "
                        f"lp={row_data.get('analytical_lp_rgbw16', '') if not using_lut_row else ''} "
                        f"interp={interpolation_mode} "
                        f"gamut={self._verifier_gamut_var.get()} "
                        f"transfer={self._verifier_transfer_var.get()} "
                        f"project_hull={int(self._verifier_project_hull_var.get())} "
                        f"ref=({ref_x:.4f},{ref_y:.4f}) "
                        f"W={(lw + lw2)} ({w_pct:.1f}%)  {xy_str}{de_str}  [{ok_str}]"
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
                               f"  |  {self._verifier_output_source_var.get()}  "
                               f"{self._verifier_analytical_model_var.get() if self._verifier_output_source_var.get() != VERIFIER_OUTPUT_SOURCES[0] else self._verifier_interp_var.get()}  "
                               f"{self._verifier_dual_edge_policy_var.get() if self._verifier_output_source_var.get() != VERIFIER_OUTPUT_SOURCES[0] else ''}  "
                               f"{'disabled' if self._verifier_output_source_var.get() != VERIFIER_OUTPUT_SOURCES[0] else ''}  "
                               f"{self._verifier_gamut_var.get()}  "
                               f"{self._verifier_transfer_var.get()}  "
                               f"project_hull={int(self._verifier_project_hull_var.get())} "
                               f"ref=({self._verifier_ref_white()[0]:.4f},{self._verifier_ref_white()[1]:.4f})"
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
            "lut_r16", "lut_g16", "lut_b16", "lut_w16", "lut_w2_16", "w_pct",
            "output_source", "active_output_mode", "supported_output_mode_bitmask",
            "physical_output_channel_count", "active_logical_channel_count",
            "selected_strip_type", "selected_strip_channel_count",
            "selected_cube_output_type", "selected_cube_output_channel_count",
            "last_input_w16", "last_input_w2_16", "last_solved_w2_16",
            "fold_stub_flag", "response_extension_version",
            "analytical_model",
            "analytical_dual_edge_policy",
            "analytical_solve_path",
            "analytical_strict_ok", "analytical_strict_rgbw16", "analytical_lp_rgbw16",
            "mcu_solved_r16", "mcu_solved_g16", "mcu_solved_b16", "mcu_solved_w16",
            "mcu_solved_w2_16",
            "meas_x", "meas_y", "meas_Y",
            "exp_raw_x", "exp_raw_y",
            "exp_x", "exp_y",
            "exp_projected_to_hull", "exp_projection_edge",
            "exp_project_hull_enabled", "expected_hull_xy",
            "dE", "ok",
            "interpolation", "expected_gamut", "verification_gamut", "input_transfer",
            "reference_white_x", "reference_white_y", "reference_white_preset",
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