"""xy_target_rgbw.py - Analytical RGB -> RGBW mapping via CIE xy chromaticity.

Input-gamut modes
─────────────────
  native   (default) - Input RGB values address the LED’s own R, G, B
           primary chromaticities, with D65 as the reference white.
           (255,255,255) -> D65.  Linear EOTF (no encoding curve).
           This is the widest gamut the LED system can produce while
           keeping neutrals on the D65 locus.  Exact linearity; no OOG
           for any colour within the LED RGB triangle.

  rec709   - sRGB / Rec.709 primaries, D65 white.
  rec2020  - Rec.2020 wide-gamut primaries, D65 white.
  dci-p3   - Display P3 primaries (D65-adapted), D65 white.
  adobe-rgb - Adobe RGB primaries, D65 white.

  Named gamuts default to linear RGB component interpretation in LUT mode so
  transfer/tonemap curves can live elsewhere in the render pipeline.  Use
  --input-transfer=gamut to explicitly bake the named EOTF into the LUT.

For named gamuts, input XYZ (normalised at Y=1 for white) is scaled by
SCALE_K to match the LED's absolute luminance space.

LED centroids (from calibration)
─────────────────────────────────
  R  xy=(0.6853, 0.3147)  maxY= 154.67
  G  xy=(0.1379, 0.7480)  maxY= 566.27
  B  xy=(0.1295, 0.0663)  maxY= 129.64
  W  xy=(0.3299, 0.3582)  maxY=1543.64

Sub-gamut structure (all modes)
───────────────────────────────
W sits inside the LED RGB triangle and divides it into three sub-triangles:
  RGW | RBW | BGW
For each input the correct sub-triangle is found by xy containment and a
3x3 XYZ linear system is solved.  If any channel exceeds 1.0 the whole
vector is proportionally scaled (preserving chromaticity at max brightness).

Whiteness-extraction variant  (rgb_to_rgbw_wx)
───────────────────────────────────────────────
Maximises the W channel while preserving chromaticity, then fills the
residual with R+G+B.  All four channels may fire simultaneously.

Usage
─────
  python xy_target_rgbw.py                       # native gamut, table + plot
  python xy_target_rgbw.py --no-plot             # table only
  python xy_target_rgbw.py --gamut rec709        # sRGB / Rec.709 input
  python xy_target_rgbw.py --gamut rec2020       # Rec.2020 wide-gamut input
  python xy_target_rgbw.py --rgb 255 0 0         # single colour, both methods
"""

from __future__ import annotations

import argparse
import colorsys
import csv
import glob
import os
import sys

# Keep CLI output safe on Windows terminals that default to cp1252.
# The script contains unicode in documentation/comments, but runtime logging
# should never crash a long LUT build because the console cannot encode a glyph.
def _configure_stdio_for_console() -> None:
    for _stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
        try:
            if _stream is not None and hasattr(_stream, "reconfigure"):
                _stream.reconfigure(errors="replace")
        except Exception:
            pass

_configure_stdio_for_console()
from dataclasses import dataclass, field
from typing import Literal

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

# ── LED primary parameters ────────────────────────────────────────────────────

#  Chromaticity centroids (CIE 1931 xy)
PRIMARIES_XY: dict[str, np.ndarray] = {
    "R": np.array([0.6853, 0.3147]),
    "G": np.array([0.1379, 0.7480]),
    "B": np.array([0.1295, 0.0663]),
    "W": np.array([0.3299, 0.3582]),
}

#  Peak luminance (cd/m^2 or relative Y - units are internally consistent)
MAX_Y: dict[str, float] = {
    "R": 149.658631,
    "G": 563.961804,
    "B": 129.540105,
    "W": 1511.803150,
}

#  D65 reference white chromaticity  (CIE 1931 xy)
#  x = X/(X+Y+Z) = 0.95047/3.0393 = 0.3127
#  y = Y/(X+Y+Z) = 1.00000/3.0393 = 0.3290
D65_xy = np.array([0.3127, 0.3290])

# ── Colour-math helpers ───────────────────────────────────────────────────────

def xy_Y_to_XYZ(xy: np.ndarray, Y: float) -> np.ndarray:
    """Convert CIE xy chromaticity + luminance Y to CIE XYZ."""
    x, y = float(xy[0]), float(xy[1])
    return np.array([(x / y) * Y,
                     Y,
                     ((1.0 - x - y) / y) * Y], dtype=float)


def XYZ_to_xy(XYZ: np.ndarray) -> np.ndarray:
    """CIE XYZ -> xy chromaticity.  Returns D65 for near-black inputs."""
    s = float(XYZ.sum())
    if s < 1e-12:
        return D65_xy.copy()
    return XYZ[:2] / s


# ── Named-gamut input system ──────────────────────────────────────────────────
#
#  Each named gamut defines:
#    primaries_xy  - {R, G, B} chromaticities
#    white_xy      - reference white chromaticity
#    eotf          - callable  u8_array -> linear [0,1]  (None = already linear)
#
#  The RGB->XYZ matrix is derived analytically from primaries + white so that
#  white=(1,1,1) maps to Y=1.  All named-gamut XYZ are normalised (Y=1 for
#  white) and must be scaled by SCALE_K to enter LED absolute units.


def _build_gamut_matrix(primaries_xy: dict[str, np.ndarray],
                        white_xy: np.ndarray) -> np.ndarray:
    """
    Derive the 3x3 linear-RGB -> CIE XYZ matrix from primary chromaticities
    and white point (standard Bradford-free method).
    Returns M such that M @ [r,g,b] = XYZ  with white=(1,1,1) -> Y=1.
    """
    def _xy_to_xyz1(xy: np.ndarray) -> np.ndarray:
        x, y = float(xy[0]), float(xy[1])
        return np.array([x / y, 1.0, (1.0 - x - y) / y])

    M_prim = np.column_stack([_xy_to_xyz1(primaries_xy[c]) for c in "RGB"])
    XYZ_w  = _xy_to_xyz1(white_xy)
    S      = np.linalg.solve(M_prim, XYZ_w)   # per-primary scale
    return M_prim * S                          # broadcast column-wise


def _eotf_srgb(u8: np.ndarray) -> np.ndarray:
    v = u8 / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)

def _eotf_gamma(gamma: float):
    def _f(u8: np.ndarray) -> np.ndarray:
        return (u8 / 255.0) ** gamma
    return _f


#  Standard primary chromaticities (CIE 1931 xy)
_P_REC709    = {"R": np.array([0.6400, 0.3300]),
                "G": np.array([0.3000, 0.6000]),
                "B": np.array([0.1500, 0.0600])}
_P_REC2020   = {"R": np.array([0.7080, 0.2920]),
                "G": np.array([0.1700, 0.7970]),
                "B": np.array([0.1310, 0.0460])}
_P_DCI_P3    = {"R": np.array([0.6800, 0.3200]),   # Display P3 (D65-adapted)
                "G": np.array([0.2650, 0.6900]),
                "B": np.array([0.1500, 0.0600])}
_P_ADOBE_RGB = {"R": np.array([0.6400, 0.3300]),
                "G": np.array([0.2100, 0.7100]),
                "B": np.array([0.1500, 0.0600])}

# gamut_name -> (M_rgb_to_xyz_norm, eotf_fn, description)
#
#  'native' uses the actual LED R/G/B primary chromaticities with D65 as the
#  reference white.  (255,255,255) -> D65.  Linear EOTF (no encoding curve).
#  This is the widest physically-addressable gamut that stays D65-neutral.
_NAMED_GAMUTS: dict[str, tuple[np.ndarray, object, str]] = {
    "native":    (_build_gamut_matrix(PRIMARIES_XY,   D65_xy), _eotf_gamma(1.0), "Native LED primaries, D65 white, linear"),
    "rec709":    (_build_gamut_matrix(_P_REC709,    D65_xy), _eotf_srgb,         "Rec.709/sRGB primaries, D65"),
    "rec2020":   (_build_gamut_matrix(_P_REC2020,   D65_xy), _eotf_gamma(2.4),   "Rec.2020 primaries, D65"),
    "dci-p3":    (_build_gamut_matrix(_P_DCI_P3,    D65_xy), _eotf_gamma(2.6),   "Display P3 primaries, D65"),
    "adobe-rgb": (_build_gamut_matrix(_P_ADOBE_RGB, D65_xy), _eotf_gamma(2.2),   "Adobe RGB primaries, D65"),
}

VALID_GAMUTS = list(_NAMED_GAMUTS.keys())


# ── WX mode taxonomy ──────────────────────────────────────────────────────────
#
# ``method='wx'`` is now a family selector rather than a single hard-coded
# solver.  Use ``--wx-mode`` in the LUT builder to choose which white-extraction
# model writes the cube.  Direct method aliases are accepted for one-shot calls
# and tests, but the canonical build form is:
#
#   --method wx --wx-mode wx_radial_virtual
#   --method wx --wx-mode wx_virtual_axis_maxbright
#   --method wx --wx-mode wx_lp_legacy
#
# The default is the radial virtual-primary implementation so a plain
# ``--method wx`` build follows the preferred/current model.  Use
# ``--wx-mode wx_virtual_axis_maxbright`` to reproduce the pre-radial
# high-brightness/virtual-axis build explicitly.
DEFAULT_WX_MODE = "wx_radial_virtual"
VALID_WX_MODES = [
    "wx_radial_virtual",
    "wx_virtual_axis_maxbright",
    "wx_lp_legacy",
]
_WX_MODE_ALIASES = {
    "wx": DEFAULT_WX_MODE,
    "radial": "wx_radial_virtual",
    "wx_radial": "wx_radial_virtual",
    "wx_radial_virtual": "wx_radial_virtual",
    "radial_virtual": "wx_radial_virtual",
    "virtual_axis": "wx_virtual_axis_maxbright",
    "maxbright": "wx_virtual_axis_maxbright",
    "max_bright": "wx_virtual_axis_maxbright",
    "brightness": "wx_virtual_axis_maxbright",
    "wx_virtual_axis": "wx_virtual_axis_maxbright",
    "wx_virtual_axis_maxbright": "wx_virtual_axis_maxbright",
    "lp": "wx_lp_legacy",
    "wx_lp": "wx_lp_legacy",
    "lp_legacy": "wx_lp_legacy",
    "lp_maxwhite": "wx_lp_legacy",
    "wx_lp_legacy": "wx_lp_legacy",
    "wx_lp_maxwhite": "wx_lp_legacy",
}
VALID_LUT_METHODS = [
    "sub_gamut",
    "wx",
    "wx_radial_virtual",
    "wx_virtual_axis_maxbright",
    "wx_lp_legacy",
]


def _normalize_wx_mode(wx_mode: str | None) -> str:
    key = str(wx_mode or DEFAULT_WX_MODE).strip().lower().replace("-", "_")
    if key not in _WX_MODE_ALIASES:
        raise ValueError(
            f"Unsupported wx_mode={wx_mode!r}; expected one of {VALID_WX_MODES} "
            f"or aliases {sorted(_WX_MODE_ALIASES)}"
        )
    return _WX_MODE_ALIASES[key]


def _normalize_lut_method(method: str) -> str:
    m = str(method).strip().lower().replace("-", "_")
    if m == "both":
        return "sub_gamut"
    if m in {"sub", "subgamut", "sub_gamut", "strict", "strict_subgamut"}:
        return "sub_gamut"
    if m in _WX_MODE_ALIASES or m == "wx":
        return "wx"
    raise ValueError(
        f"Unsupported method={method!r}; expected 'sub_gamut', 'wx', 'both', "
        f"or a direct WX mode alias: {VALID_WX_MODES}"
    )


def _method_to_wx_mode(method: str, wx_mode: str | None = None) -> str:
    m = str(method).strip().lower().replace("-", "_")
    if m in _WX_MODE_ALIASES and m != "wx":
        return _normalize_wx_mode(m)
    return _normalize_wx_mode(wx_mode)


def input_to_XYZ(rgb_u8, gamut: str = "native", input_transfer: str = "gamut") -> np.ndarray:
    """
    Convert an (R, G, B) 0-255 triplet to CIE XYZ in LED absolute units.

    Path:
      1. Interpret the input RGB components either linearly or through the
         named gamut's EOTF.  The LUT builder defaults to linear so external
         transfer/tonemap stages are not double-applied.
      2. Apply the gamut’s RGB->XYZ matrix (normalised so white -> Y=1).
      3. Scale by SCALE_K so D65 white lands on the brightest LED channel’s
         full drive, mapping the result into LED absolute luminance units.

    input_transfer:
      "linear" - component values are already linear light.
      "gamut"  - apply the named gamut EOTF (legacy/baked-transfer behavior).

    native always behaves linearly because its stored EOTF is gamma=1.
    """
    rgb = np.asarray(rgb_u8, dtype=float)
    M_norm, eotf, _ = _NAMED_GAMUTS[gamut]
    if input_transfer == "linear":
        linear = np.clip(rgb / 255.0, 0.0, 1.0)
    elif input_transfer == "gamut":
        linear = eotf(rgb)                     # type: ignore[operator]
    else:
        raise ValueError(f"Unknown input_transfer: {input_transfer!r}")
    return (M_norm @ linear) * SCALE_K    # scale to LED absolute units


def _apply_input_transfer_normalized(v: np.ndarray, gamut: str, input_transfer: str = "linear") -> np.ndarray:
    """Return normalized linear-light RGB from normalized source components.

    This is the LUT-safe high-precision path.  Values stay in normalized
    float64 space throughout the model; no 0..255 staging is used for LUT/rgb16
    solves.  ``input_transfer='gamut'`` intentionally applies the named
    transfer curve to normalized components for legacy/baked-transfer tests.
    """
    v = np.clip(np.asarray(v, dtype=np.float64), 0.0, 1.0)
    if input_transfer == "linear":
        return v
    if input_transfer != "gamut":
        raise ValueError(f"Unknown input_transfer: {input_transfer!r}")
    if gamut == "rec709":
        return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    if gamut == "rec2020":
        return v ** 2.4
    if gamut == "dci-p3":
        return v ** 2.6
    if gamut == "adobe-rgb":
        return v ** 2.2
    # native remains linear; its stored transfer is gamma=1.
    return v


def _encode_linear_to_normalized_source(linear: np.ndarray, gamut: str, input_transfer: str = "linear") -> np.ndarray:
    """Encode normalized linear-light RGB to normalized source components.

    This is only needed when preserving the old ``input_transfer='gamut'``
    behavior for chroma/value solves.  The default LUT path uses linear and is
    therefore identity.  The return value is still normalized float64, not 8-bit.
    """
    v = np.clip(np.asarray(linear, dtype=np.float64), 0.0, 1.0)
    if input_transfer == "linear":
        return v
    if input_transfer != "gamut":
        raise ValueError(f"Unknown input_transfer: {input_transfer!r}")
    if gamut == "rec709":
        return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1.0 / 2.4) - 0.055)
    if gamut == "rec2020":
        return np.power(v, 1.0 / 2.4)
    if gamut == "dci-p3":
        return np.power(v, 1.0 / 2.6)
    if gamut == "adobe-rgb":
        return np.power(v, 1.0 / 2.2)
    return v


def input_linear_to_XYZ(linear_rgb: np.ndarray, gamut: str = "native") -> np.ndarray:
    """Convert normalized linear-light RGB components to absolute LED XYZ.

    Unlike ``input_to_XYZ()``, this accepts already-linear 0..1 float values and
    is used by the 16-bit LUT/rgb16 path so the internal solve is never staged
    through 8-bit/model-code coordinates.
    """
    linear = np.clip(np.asarray(linear_rgb, dtype=np.float64), 0.0, 1.0)
    M_norm, _eotf, _desc = _NAMED_GAMUTS[gamut]
    return (M_norm @ linear) * SCALE_K


# ── Full-intensity XYZ primaries ──────────────────────────────────────────────

#  P[ch] = XYZ produced by channel ch at 100% intensity (t = 1.0)
PRIMARY_XYZ: dict[str, np.ndarray] = {
    ch: xy_Y_to_XYZ(xy, MAX_Y[ch]) for ch, xy in PRIMARIES_XY.items()
}

# ── Sub-gamut definitions ─────────────────────────────────────────────────────

#  W is inside the RGB triangle.  It subdivides it into three triangles
#  (named by the three LED channels that form each triangle's vertices).
#  Order matters for the signed-area / barycentric tests below.
SUB_GAMUTS: list[tuple[str, str, str]] = [
    ("R", "G", "W"),
    ("R", "B", "W"),
    ("B", "G", "W"),
]

# ── Chromaticity geometry helpers ─────────────────────────────────────────────

def _barycentric_2d(P: np.ndarray,
                    A: np.ndarray,
                    B: np.ndarray,
                    C: np.ndarray) -> np.ndarray | None:
    """
    Solve P = lA*A + lB*B + lC*C (lA+lB+lC=1) in 2-D.
    Returns [lA, lB, lC] or None if degenerate.
    """
    T = np.array([[A[0] - C[0], B[0] - C[0]],
                  [A[1] - C[1], B[1] - C[1]]], dtype=float)
    rhs = P - C
    try:
        lam = np.linalg.solve(T, rhs)
    except np.linalg.LinAlgError:
        return None
    return np.array([lam[0], lam[1], 1.0 - lam[0] - lam[1]], dtype=float)


def _xy_in_triangle(P: np.ndarray,
                    A: np.ndarray,
                    B: np.ndarray,
                    C: np.ndarray,
                    eps: float = 1e-6) -> bool:
    w = _barycentric_2d(P, A, B, C)
    return w is not None and bool(np.all(w >= -eps))


def _find_sub_gamut(xy: np.ndarray) -> tuple[str, str, str] | None:
    """Return the first sub-gamut triangle whose xy region contains *xy*."""
    for g in SUB_GAMUTS:
        xy_A = PRIMARIES_XY[g[0]]
        xy_B = PRIMARIES_XY[g[1]]
        xy_C = PRIMARIES_XY[g[2]]
        if _xy_in_triangle(xy, xy_A, xy_B, xy_C):
            return g
    return None

# ── 3x3 XYZ linear solve for a sub-gamut ─────────────────────────────────────

def _solve_xyz(ch_A: str, ch_B: str, ch_C: str,
               XYZ_target: np.ndarray) -> np.ndarray | None:
    """
    Solve [P_A | P_B | P_C] / [tA, tB, tC]T = XYZ_target.

    Columns are full-intensity XYZ of each channel (absolute Y units).
    Returns channel intensities [tA, tB, tC] (>=0) or None if the colour
    is outside this sub-gamut (any intensity goes negative).
    """
    M = np.column_stack([PRIMARY_XYZ[ch_A],
                         PRIMARY_XYZ[ch_B],
                         PRIMARY_XYZ[ch_C]])
    try:
        t = np.linalg.solve(M, XYZ_target)
    except np.linalg.LinAlgError:
        return None
    if np.any(t < -1e-6):
        return None
    return np.maximum(t, 0.0)

# ── Reference-white scale factor K (named gamuts only) ───────────────────────
#
#  Named-gamut inputs produce normalised XYZ (Y_white = 1.0).  SCALE_K maps
#  them to LED absolute-Y units so the sub-gamut solvers receive the same
#  magnitude as native mode.
#
#  K = 1 / max(t_white) where t is the sub-gamut solve for D65 at Y_norm=1.
#  The dominant LED channel reaches exactly full drive for D65 white input.
#
#  Native mode does NOT use SCALE_K: input_to_XYZ returns absolute LED units.

def _compute_scale_factor() -> tuple[float, tuple[str, str, str]]:
    """Return (K, white_sub_gamut) by solving D65 at normalised Y=1."""
    XYZ_d65_norm = xy_Y_to_XYZ(D65_xy, 1.0)
    for g in SUB_GAMUTS:
        t = _solve_xyz(g[0], g[1], g[2], XYZ_d65_norm)
        if t is not None:
            K = 1.0 / t.max() if t.max() > 0 else 1.0
            return K, g
    raise RuntimeError(
        "D65 white point is not within any LED sub-gamut - check primaries.")


SCALE_K, WHITE_SUB_GAMUT = _compute_scale_factor()

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class RGBWResult:
    R: int
    G: int
    B: int
    W: int
    method:      str
    sub_gamut:   tuple[str, str, str] | None
    xy_input:    np.ndarray
    Y_input:     float               # normalised (0-1 for sRGB)
    t_raw:       dict[str, float]    # channel intensities (post-gamut-clip, pre 0-255 scale)
    gamut_clipped: bool = False      # True when colour was outside LED gamut -> brightness reduced

    @property
    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.R, self.G, self.B, self.W

    def __str__(self) -> str:
        r, g, b, w = self.R, self.G, self.B, self.W
        xy = self.xy_input
        gname = "/".join(self.sub_gamut) if self.sub_gamut else "n/a"
        clip_tag = " OOG" if self.gamut_clipped else "    "
        return (f"RGBW=({r:5d},{g:5d},{b:5d},{w:5d})  "
                f"xy=({xy[0]:.4f},{xy[1]:.4f})  Y={self.Y_input:.4f}  "
                f"[{self.method}/{gname}]{clip_tag}")


# ── Primary algorithm: sub-gamut 3-channel solve ──────────────────────────────

def rgb_to_rgbw_subgamut(r: int, g: int, b: int,
                          gamut: str = "native", input_transfer: str = "gamut") -> RGBWResult:
    """
    Convert RGB (0-255) to RGBW (0-255) using the sub-gamut XYZ solve.

    Input space is governed by *gamut*:
      native   - r/g/b are linear drive fractions for the LED R/G/B primaries.
                 Covers the full LED gamut with exact linearity.  No EOTF.
      rec709 ... - input is decoded via the named gamut’s EOTF then converted
                 to XYZ via the standard gamut matrix.

    The input xy chromaticity selects one of three LED sub-triangles
    (RGW / RBW / BGW).  A 3x3 XYZ linear system is solved.  If any channel
    exceeds 1.0 the whole vector is proportionally scaled (preserving
    chromaticity at maximum achievable brightness).
    """
    XYZ = input_to_XYZ([r, g, b], gamut, input_transfer=input_transfer)
    Y   = float(XYZ[1])
    xy  = XYZ_to_xy(XYZ)

    # Black shortcut
    if Y < 1e-10:
        return RGBWResult(0, 0, 0, 0, "sub_gamut", None, xy, 0.0,
                          {"R": 0.0, "G": 0.0, "B": 0.0, "W": 0.0})

    XYZ_abs = XYZ   # already in absolute LED units from input_to_XYZ

    # 1. Find sub-gamut by xy containment; attempt exact solve
    gamut_tri = _find_sub_gamut(xy)
    t_vec: np.ndarray | None = None

    if gamut_tri is not None:
        t_vec = _solve_xyz(gamut_tri[0], gamut_tri[1], gamut_tri[2], XYZ_abs)
        if t_vec is None:
            gamut_tri = None   # floating-point edge case - fall through to NNLS

    # 2. Fallback for out-of-gamut or edge-case colours:
    #    Try NNLS on every sub-gamut; pick the one with smallest XYZ residual.
    if gamut_tri is None:
        best_residual = np.inf
        for g_cand in SUB_GAMUTS:
            M = np.column_stack([PRIMARY_XYZ[c] for c in g_cand])
            t_nnls, res = _nnls_solve(M, XYZ_abs)
            if res < best_residual:
                best_residual = res
                gamut_tri = g_cand
                t_vec = t_nnls

    # Gamut-boundary normalisation: if any channel would exceed full drive,
    # scale all proportionally so the hottest channel hits exactly 1.0.
    # This preserves xy chromaticity at the maximum brightness achievable
    # within the LED gamut.  Independent per-channel clipping (t->255) would
    # destroy the xy relationship - the fundamental flaw this step fixes.
    max_t = float(t_vec.max())      # type: ignore[union-attr]
    clipped = max_t > 1.0
    if clipped:
        t_vec = t_vec / max_t       # type: ignore[operator]

    ch_map = {c: 0.0 for c in "RGBW"}
    for ch, tv in zip(gamut_tri, t_vec):    # type: ignore[arg-type]
        ch_map[ch] = float(tv)

    out = _to_u16(ch_map)
    return RGBWResult(out["R"], out["G"], out["B"], out["W"],
                      "sub_gamut", gamut_tri, xy, Y, ch_map, clipped)


# ── Alternative algorithm: whiteness extraction (4-channel) ───────────────────

def rgb_to_rgbw_wx_legacy(r: int, g: int, b: int,
                          gamut: str = "native", input_transfer: str = "gamut") -> RGBWResult:
    """
    Convert RGB (0-255) to RGBW (0-255) using the legacy whiteness extraction.

    Maximises the W channel: finds the largest t_W ∈ [0,1] such that
    XYZ_target - t_W/P_W is still reproducible with non-negative R+G+B.
    All four channels may fire simultaneously.

    Input space is governed by *gamut* (same as rgb_to_rgbw_subgamut).
    """
    XYZ = input_to_XYZ([r, g, b], gamut, input_transfer=input_transfer)
    Y   = float(XYZ[1])
    xy  = XYZ_to_xy(XYZ)

    if Y < 1e-10:
        return RGBWResult(0, 0, 0, 0, "wx", None, xy, 0.0,
                          {"R": 0.0, "G": 0.0, "B": 0.0, "W": 0.0})

    XYZ_abs = XYZ   # already in absolute LED units

    P_W = PRIMARY_XYZ["W"]
    M_RGB = np.column_stack([PRIMARY_XYZ["R"],
                             PRIMARY_XYZ["G"],
                             PRIMARY_XYZ["B"]])
    M_RGB_inv = np.linalg.inv(M_RGB)

    # t_RGB(t_W) = a - t_W / d
    a = M_RGB_inv @ XYZ_abs   # RGB intensities at t_W = 0
    d = M_RGB_inv @ P_W       # ∂t_RGB / ∂t_W

    # Maximum t_W before any RGB channel goes negative (d[i] > 0 is the binding constraint)
    t_W_max = 1.0
    for i in range(3):
        if d[i] > 1e-12 and a[i] >= 0:
            t_W_max = min(t_W_max, a[i] / d[i])

    t_W = max(0.0, min(1.0, t_W_max))
    t_rgb = np.maximum(a - t_W * d, 0.0)

    # Gamut-boundary normalisation (same logic as sub-gamut solver).
    # a = M_RGB_inv @ XYZ_scaled may have components > 1 when the input
    # colour is outside the LED gamut.  Scale all four channels together
    # so the hottest hits exactly 1.0 and chromaticity is preserved.
    t_all = np.array([t_rgb[0], t_rgb[1], t_rgb[2], t_W])
    max_t = float(t_all.max())
    clipped = max_t > 1.0
    if clipped:
        t_all = t_all / max_t
        t_rgb = t_all[:3]
        t_W = float(t_all[3])

    ch_map = {
        "R": float(t_rgb[0]),
        "G": float(t_rgb[1]),
        "B": float(t_rgb[2]),
        "W": float(t_W),
    }

    out = _to_u16(ch_map)
    # Determine sub-gamut label by xy location
    sg_label = _find_sub_gamut(xy)
    return RGBWResult(out["R"], out["G"], out["B"], out["W"],
                      "wx_legacy", sg_label, xy, Y, ch_map, clipped)


def rgb_to_rgbw_wx(r: int, g: int, b: int,
                   gamut: str = "native", input_transfer: str = "gamut",
                   wx_mode: str = DEFAULT_WX_MODE,
                   wx_radial_target_position: float | None = None) -> RGBWResult:
    """Convert RGB (0-255) to RGBW using a selected WX extraction model.

    ``method='wx'`` is now a mode family.  ``wx_mode`` selects the concrete
    white-extraction model:

      wx_virtual_axis_maxbright - pre-radial virtual-axis max-brightness model
      wx_radial_virtual         - radial virtual-primary model
      wx_lp_legacy              - direct LP max-white reference model

    Native single-channel and native outer-edge dual-channel inputs keep exact
    identity / edge-lock behavior for every WX mode.
    """
    wx_mode = _normalize_wx_mode(wx_mode)
    XYZ = input_to_XYZ([r, g, b], gamut, input_transfer=input_transfer)
    Y = float(XYZ[1])
    xy = XYZ_to_xy(XYZ)

    if Y < 1e-10:
        return RGBWResult(0, 0, 0, 0, wx_mode, None, xy, 0.0,
                          {"R": 0.0, "G": 0.0, "B": 0.0, "W": 0.0})

    # Native LED coordinates retain exact single-axis and outer-edge behavior.
    if gamut == "native":
        src = np.asarray([r, g, b], dtype=np.float64)
        active = [i for i, v in enumerate(src) if float(v) > 1e-9]
        if len(active) in (1, 2):
            frac = _solve_fraction_for_fixed_topology_from_xyz(XYZ, active)
            ch_map = {c: 0.0 for c in "RGBW"}
            for i in active:
                ch_map["RGBW"[i]] = float(frac[i])
            out = _to_u16(ch_map)
            label = tuple("RGBW"[i] for i in active)
            return RGBWResult(out["R"], out["G"], out["B"], out["W"],
                              wx_mode, label, xy, Y, ch_map,
                              bool(float(np.max(frac)) >= 1.0 - 1e-12))

    src_norm = np.asarray([r, g, b], dtype=np.float64) / 255.0
    linear = _apply_input_transfer_normalized(src_norm, gamut, input_transfer=input_transfer)
    value_scale = float(np.max(linear))
    if value_scale <= 1e-12:
        frac = np.zeros(4, dtype=np.float64)
    else:
        chroma_linear = linear / value_scale
        frac = _solve_wx_fraction_from_linear(
            chroma_linear,
            gamut=gamut,
            wx_mode=wx_mode,
            wx_radial_target_position=wx_radial_target_position,
        ) * value_scale
    frac = np.clip(frac, 0.0, 1.0)

    ch_map = {ch: float(frac[i]) for i, ch in enumerate("RGBW")}
    out = _to_u16(ch_map)
    sg_label = _find_sub_gamut(xy)
    return RGBWResult(out["R"], out["G"], out["B"], out["W"],
                      wx_mode, sg_label, xy, Y, ch_map,
                      bool(float(np.max(frac)) >= 1.0 - 1e-12))

# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_u16(ch_map: dict[str, float]) -> dict[str, int]:
    return {ch: int(np.clip(round(v * 65535), 0, 65535))
            for ch, v in ch_map.items()}


def _nnls_solve(M: np.ndarray,
                b: np.ndarray) -> tuple[np.ndarray, float]:
    """Non-negative least squares fallback (manual active-set for 3-D)."""
    # Use scipy if available, else gradient projection
    try:
        from scipy.optimize import nnls
        return nnls(M, b)
    except ImportError:
        pass
    # Simple projected-gradient (sufficient for 3x3)
    t = np.zeros(M.shape[1])
    for _ in range(500):
        grad = M.T @ (M @ t - b)
        t = np.maximum(t - 0.01 * grad, 0.0)
    return t, float(np.linalg.norm(M @ t - b))


# ── Verification: round-trip xy check ────────────────────────────────────────

def verify_xy(result: RGBWResult) -> tuple[float, float]:
    """
    Re-synthesise xy from the RGBW output and compare with the input xy.
    Returns (dx, dy) chromaticity error.
    """
    XYZ_out = np.zeros(3)
    for ch, val in zip("RGBW", result.as_tuple):
        XYZ_out += (val / 65535.0) * PRIMARY_XYZ[ch]
    xy_out = XYZ_to_xy(XYZ_out)
    return float(xy_out[0] - result.xy_input[0]), float(xy_out[1] - result.xy_input[1])


# ── Test suite ────────────────────────────────────────────────────────────────

_TEST_COLORS: list[tuple[str, tuple[int, int, int]]] = [
    # sRGB primaries
    ("Red",        (255,   0,   0)),
    ("Green",      (  0, 255,   0)),
    ("Blue",       (  0,   0, 255)),
    # sRGB secondaries
    ("Yellow",     (255, 255,   0)),
    ("Cyan",       (  0, 255, 255)),
    ("Magenta",    (255,   0, 255)),
    # Neutrals
    ("White",      (255, 255, 255)),
    ("Mid-grey",   (128, 128, 128)),
    ("Dark-grey",  ( 64,  64,  64)),
    # Near-D65 warm/cool neutrals
    ("Warm-white", (255, 240, 220)),
    ("Cool-white", (220, 230, 255)),
    # Mixed
    ("Orange",     (255, 140,   0)),
    ("Sky-blue",   ( 70, 130, 180)),
    ("Lime",       (160, 220,  60)),
]


def run_tests(method: Literal["sub_gamut", "wx", "both"] = "both",
              gamut: str = "native") -> None:
    """Print a formatted comparison table for all test colours."""
    if method == "both":
        methods = [rgb_to_rgbw_subgamut, rgb_to_rgbw_wx]
    elif _normalize_lut_method(method) == "sub_gamut":
        methods = [rgb_to_rgbw_subgamut]
    else:
        wx_mode = _method_to_wx_mode(method, DEFAULT_WX_MODE)
        methods = [lambda r, g, b, gamut="native": rgb_to_rgbw_wx(r, g, b, gamut, wx_mode=wx_mode)]

    gamut_desc = "native (LED primaries, linear)" if gamut == "native" \
                 else _NAMED_GAMUTS[gamut][2]
    print(f"\n  Input gamut: {gamut_desc}")

    print("\n" + "═" * 118)
    print(f"  {'Colour':<14}  {'Input RGB':>13}  "
          f"{'R':>5} {'G':>5} {'B':>5} {'W':>5}  "
          f"{'xy input':>16}  {'dxy (verify)':>14}  {'OOG':<3}  Method/Gamut")
    print("─" * 118)

    for name, rgb in _TEST_COLORS:
        for fn in methods:
            res = fn(*rgb, gamut)
            dx, dy = verify_xy(res)
            r_in, g_in, b_in = rgb
            gname = "/".join(res.sub_gamut) if res.sub_gamut else "---"
            oog = "*" if res.gamut_clipped else " "
            print(f"  {name:<14}  ({r_in:3d},{g_in:3d},{b_in:3d})  "
                  f"{res.R:5d} {res.G:5d} {res.B:5d} {res.W:5d}  "
                  f"({res.xy_input[0]:.4f},{res.xy_input[1]:.4f})  "
                  f"d({dx:+.4f},{dy:+.4f})  {oog:<3}  "
                  f"{res.method}/{gname}")

    print("═" * 118)
    print(f"\n  Scale factor K = {SCALE_K:.4f}  "
          f"(white sub-gamut: {'/'.join(WHITE_SUB_GAMUT)})")
    print(f"  D65 white point: xy=({D65_xy[0]:.4f},{D65_xy[1]:.4f})\n")


# ── HSV sample generation ────────────────────────────────────────────────────

def _generate_hsv_samples() -> list[tuple[int, int, int]]:
    """
    Generate ~1800 unique RGB (0-255) samples on a uniform HSV grid.

    Grid: 72 hues (every 5 deg) x 5 saturations (0.20-1.00) x 5 values (0.20-1.00)
    = 1800 combinations before deduplication.  Near-black and near-neutral
    cells collapse to the same u8 triplet and are deduplicated.
    """
    seen: set[tuple[int, int, int]] = set()
    out:  list[tuple[int, int, int]] = []
    hues = np.linspace(0.0, 1.0, 73)[:-1]   # 72 evenly-spaced hues, 0 deg - 355 deg
    sats = np.linspace(0.2, 1.0, 5)          # 0.20, 0.40, 0.60, 0.80, 1.00
    vals = np.linspace(0.2, 1.0, 5)          # 0.20, 0.40, 0.60, 0.80, 1.00
    for h in hues:
        for s in sats:
            for v in vals:
                rf, gf, bf = colorsys.hsv_to_rgb(float(h), float(s), float(v))
                rgb = (int(round(rf * 255)),
                       int(round(gf * 255)),
                       int(round(bf * 255)))
                if rgb not in seen:
                    seen.add(rgb)
                    out.append(rgb)
    return out


# ── CSV output ────────────────────────────────────────────────────────────────

def write_csv(samples: list[tuple[int, int, int]],
              method: str = "sub_gamut",
              gamut: str = "native",
              filepath: str = "rgbw_samples.csv") -> int:
    """
    Solve every sample and write results to *filepath* as CSV.

    Columns:
      R_in, G_in, B_in          - input RGB (0-255, 8-bit)
      R_out, G_out, B_out, W_out - output RGBW (0-65535, 16-bit)
      xy_x, xy_y                 - input CIE xy chromaticity
      dx, dy                     - verify_xy chromaticity error
      gamut_clipped              - 1 if brightness was reduced to fit LED gamut
      sub_gamut                  - sub-gamut triangle used (e.g. R/G/W)

    Returns the number of rows written.
    """
    if _normalize_lut_method(method) == "wx":
        _wx_mode_for_csv = _method_to_wx_mode(method, DEFAULT_WX_MODE)
        fn = lambda r, g, b, gamut="native": rgb_to_rgbw_wx(r, g, b, gamut, wx_mode=_wx_mode_for_csv)
    else:
        fn = rgb_to_rgbw_subgamut
    with open(filepath, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["R_in", "G_in", "B_in",
                    "R_out", "G_out", "B_out", "W_out",
                    "xy_x", "xy_y", "dx", "dy",
                    "gamut_clipped", "sub_gamut"])
        for r, g, b in samples:
            res = fn(r, g, b, gamut)
            dx, dy = verify_xy(res)
            sg = "/".join(res.sub_gamut) if res.sub_gamut else "---"
            w.writerow([r, g, b,
                        res.R, res.G, res.B, res.W,
                        f"{res.xy_input[0]:.6f}", f"{res.xy_input[1]:.6f}",
                        f"{dx:.6f}", f"{dy:.6f}",
                        int(res.gamut_clipped), sg])
    return len(samples)

# ── Capture verification ───────────────────────────────────────────────────────────────────────────────

def _predict_xyz_from_rgbw16(r16: int, g16: int, b16: int, w16: int) -> np.ndarray:
    """Predict CIE XYZ from 16-bit RGBW drives using calibrated LED primaries."""
    return (r16 / 65535.0 * PRIMARY_XYZ["R"] +
            g16 / 65535.0 * PRIMARY_XYZ["G"] +
            b16 / 65535.0 * PRIMARY_XYZ["B"] +
            w16 / 65535.0 * PRIMARY_XYZ["W"])


def _mccamy_cct(x: float, y: float) -> float | None:
    """McCamy (1992) CCT approximation from CIE xy. Returns None if ill-conditioned."""
    denom = y - 0.1858
    if abs(denom) < 1e-9:
        return None
    n = (x - 0.3320) / denom
    cct = -449.0 * n**3 + 3525.0 * n**2 - 6823.3 * n + 5520.33
    return cct if 500.0 < cct < 25000.0 else None


def _white_label(x: float, y: float) -> str:
    """
    Classify an xy chromaticity as a named illuminant / CCT range or 'chromatic'.
    Distance threshold from D65 of 0.08 - anything outside is flagged chromatic.
    """
    dx = x - float(D65_xy[0])
    dy = y - float(D65_xy[1])
    if (dx * dx + dy * dy) ** 0.5 > 0.08:
        return "chromatic"
    cct = _mccamy_cct(x, y)
    if cct is None:
        return "near-white"
    if   cct < 2700:  return f"warm-WW ({cct:.0f}K)"
    elif cct < 3200:  return f"WW ({cct:.0f}K)"
    elif cct < 4200:  return f"NW ({cct:.0f}K)"
    elif cct < 5200:  return f"N ({cct:.0f}K)"
    elif cct < 6200:  return f"D ({cct:.0f}K)"
    elif cct < 6800:  return f"D65 ({cct:.0f}K)"
    elif cct < 7500:  return f"CW ({cct:.0f}K)"
    else:             return f"vCW ({cct:.0f}K)"


def verify_captures(captures_dir: str,
                    output_csv: str = "verify_report.csv") -> None:
    """
    Read every patch-capture CSV in *captures_dir*, compute the model’s predicted
    XYZ from the 16-bit RGBW drives, compare against the instrument-measured XYZ,
    and write a per-patch error report to *output_csv*.

    Required CSV columns (comma-separated):
      name, r16, g16, b16, w16, X, Y, Z
    Optional (recomputed from XYZ if absent):
      x, y
    Other columns are ignored.  Rows where ok==False, timed_out==True, or Y<=0
    are skipped.

    Output columns:
      name                     - patch name
      r16,g16,b16,w16          - 16-bit drives sent to LEDs
      X_meas,Y_meas,Z_meas     - instrument-measured XYZ
      x_meas,y_meas            - measured CIE xy
      X_pred,Y_pred,Z_pred     - model-predicted XYZ
      x_pred,y_pred            - predicted CIE xy
      dx,dy                    - chromaticity error (pred - meas)
      dE_xy                    - Euclidean xy error magnitude
      dY_abs                   - luminance error (pred - meas, cd/m^2)
      dY_rel_pct               - luminance error relative to measured (%)
      scale_factor             - Y_meas / Y_pred (systematic gain ratio)
      white_label              - CCT-based white-point classification
      source_file              - originating CSV filename
    """
    csv_files = sorted(glob.glob(os.path.join(captures_dir, "*.csv")))
    if not csv_files:
        print(f"  No CSV files found in {captures_dir!r}")
        return
    print(f"  Found {len(csv_files)} capture file(s) in {captures_dir!r}")

    rows_written = 0
    rows_skipped = 0

    # Accumulators for summary statistics
    _dx_all: list[float] = []
    _dy_all: list[float] = []
    _dE_all: list[float] = []
    _dY_rel_all: list[float] = []
    _scale_all: list[float] = []

    with open(output_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "name",
            "r16", "g16", "b16", "w16",
            "X_meas", "Y_meas", "Z_meas", "x_meas", "y_meas",
            "X_pred", "Y_pred", "Z_pred", "x_pred", "y_pred",
            "dx", "dy", "dE_xy",
            "dY_abs", "dY_rel_pct",
            "scale_factor",
            "white_label",
            "source_file",
        ])

        for csv_path in csv_files:
            fname = os.path.basename(csv_path)
            with open(csv_path, newline="", encoding="utf-8",
                      errors="replace") as rf:
                reader = csv.DictReader(rf)
                for row in reader:
                    # Skip failed / timed-out captures
                    if str(row.get("ok", "True")).strip().lower() == "false":
                        rows_skipped += 1
                        continue
                    if str(row.get("timed_out", "False")).strip().lower() == "true":
                        rows_skipped += 1
                        continue

                    # Parse required columns
                    try:
                        r16 = int(float(row["r16"]))
                        g16 = int(float(row["g16"]))
                        b16 = int(float(row["b16"]))
                        w16 = int(float(row["w16"]))
                        X_m = float(row["X"])
                        Y_m = float(row["Y"])
                        Z_m = float(row["Z"])
                    except (KeyError, ValueError):
                        rows_skipped += 1
                        continue

                    if Y_m <= 0:
                        rows_skipped += 1
                        continue

                    # Recompute xy from XYZ (more reliable than stored x/y)
                    xyz_sum_m = X_m + Y_m + Z_m
                    x_m = X_m / xyz_sum_m if xyz_sum_m > 1e-9 else float(D65_xy[0])
                    y_m = Y_m / xyz_sum_m if xyz_sum_m > 1e-9 else float(D65_xy[1])

                    # Model prediction
                    XYZ_p   = _predict_xyz_from_rgbw16(r16, g16, b16, w16)
                    X_p, Y_p, Z_p = float(XYZ_p[0]), float(XYZ_p[1]), float(XYZ_p[2])
                    xyz_sum_p = X_p + Y_p + Z_p
                    x_p = X_p / xyz_sum_p if xyz_sum_p > 1e-9 else float(D65_xy[0])
                    y_p = Y_p / xyz_sum_p if xyz_sum_p > 1e-9 else float(D65_xy[1])

                    dx    = x_p - x_m
                    dy    = y_p - y_m
                    dE_xy = (dx * dx + dy * dy) ** 0.5
                    dY_a  = Y_p - Y_m
                    dY_r  = dY_a / Y_m * 100.0
                    scale = Y_m / Y_p if Y_p > 1e-9 else 0.0

                    _dx_all.append(dx)
                    _dy_all.append(dy)
                    _dE_all.append(dE_xy)
                    _dY_rel_all.append(dY_r)
                    _scale_all.append(scale)

                    w.writerow([
                        row.get("name", ""),
                        r16, g16, b16, w16,
                        f"{X_m:.6f}", f"{Y_m:.6f}", f"{Z_m:.6f}",
                        f"{x_m:.6f}", f"{y_m:.6f}",
                        f"{X_p:.6f}", f"{Y_p:.6f}", f"{Z_p:.6f}",
                        f"{x_p:.6f}", f"{y_p:.6f}",
                        f"{dx:.6f}", f"{dy:.6f}", f"{dE_xy:.6f}",
                        f"{dY_a:.6f}", f"{dY_r:.4f}",
                        f"{scale:.6f}",
                        _white_label(x_m, y_m),
                        fname,
                    ])
                    rows_written += 1

    if rows_written == 0:
        print("  No valid rows found - check column names in capture CSVs.")
        return

    arr_dE   = np.array(_dE_all)
    arr_dx   = np.array(_dx_all)
    arr_dy   = np.array(_dy_all)
    arr_dYr  = np.array(_dY_rel_all)
    arr_scl  = np.array(_scale_all)

    print(f"  Verified {rows_written:,} patches ({rows_skipped:,} skipped) -> {output_csv}")
    print()
    print("  ─── Error summary " + "─" * 70)
    print()
    print(f"  {'Metric':<22}  {'Mean':>10}  {'Median':>10}  {'P95':>10}  {'Max':>10}")
    print(f"  {'-'*22}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}")

    def _row(label, arr):
        print(f"  {label:<22}  {np.mean(arr):>10.5f}  {np.median(arr):>10.5f}"
              f"  {np.percentile(arr,95):>10.5f}  {np.max(arr):>10.5f}")

    _row("dE_xy",         arr_dE)
    _row("|dx|",          np.abs(arr_dx))
    _row("|dy|",          np.abs(arr_dy))
    _row("|dY_rel| (%)",  np.abs(arr_dYr))
    _row("scale_factor",  arr_scl)
    print()

# ── CIE xy diagram ────────────────────────────────────────────────────────────

#  Simplified CIE 1931 spectral locus (5 nm steps, 380-700 nm)
#  Source: CIE 2 deg standard observer, rounded to 4 decimal places
_SPECTRAL_LOCUS_XY = np.array([
    # (wavelength, x, y)  - xy only used below
    [0.1741, 0.0050],  # 380
    [0.1740, 0.0050],  # 385
    [0.1738, 0.0049],  # 390
    [0.1736, 0.0049],  # 395
    [0.1733, 0.0048],  # 400
    [0.1720, 0.0048],  # 405
    [0.1714, 0.0051],  # 410
    [0.1689, 0.0069],  # 415
    [0.1644, 0.0109],  # 420
    [0.1566, 0.0177],  # 425
    [0.1440, 0.0297],  # 430
    [0.1241, 0.0578],  # 435
    [0.0913, 0.1327],  # 440
    [0.0454, 0.2950],  # 445
    [0.0082, 0.5384],  # 450 (short-wavelength green boundary)
    [0.0039, 0.6548],  # 455
    [0.0139, 0.7502],  # 460
    [0.0389, 0.8120],  # 465
    [0.0743, 0.8338],  # 470
    [0.1142, 0.8262],  # 475
    [0.1547, 0.8059],  # 480
    [0.1929, 0.7816],  # 485
    [0.2296, 0.7543],  # 490
    [0.2658, 0.7243],  # 495
    [0.3016, 0.6923],  # 500
    [0.3373, 0.6589],  # 505
    [0.3731, 0.6243],  # 510
    [0.4087, 0.5896],  # 515
    [0.4441, 0.5547],  # 520
    [0.4788, 0.5202],  # 525
    [0.5125, 0.4866],  # 530
    [0.5448, 0.4544],  # 535
    [0.5752, 0.4242],  # 540
    [0.6029, 0.3965],  # 545
    [0.6270, 0.3725],  # 550
    [0.6482, 0.3514],  # 555
    [0.6658, 0.3340],  # 560
    [0.6801, 0.3197],  # 565
    [0.6915, 0.3083],  # 570
    [0.7006, 0.2993],  # 575
    [0.7079, 0.2920],  # 580
    [0.7140, 0.2859],  # 585
    [0.7190, 0.2809],  # 590
    [0.7230, 0.2770],  # 595
    [0.7260, 0.2740],  # 600
    [0.7283, 0.2717],  # 605
    [0.7300, 0.2700],  # 610
    [0.7311, 0.2689],  # 615
    [0.7320, 0.2680],  # 620
    [0.7327, 0.2673],  # 625
    [0.7334, 0.2666],  # 630
    [0.7340, 0.2660],  # 635
    [0.7344, 0.2656],  # 640
    [0.7346, 0.2654],  # 645
    [0.7347, 0.2653],  # 650 (monochromatic red limit)
    [0.7347, 0.2653],  # 680
    [0.7347, 0.2653],  # 700
])


def _srgb_gamut_xy() -> np.ndarray:
    """Return the three sRGB primary xy chromaticities (IEC 61966-2-1)."""
    return np.array([
        [0.6400, 0.3300],  # sRGB R
        [0.3000, 0.6000],  # sRGB G
        [0.1500, 0.0600],  # sRGB B
    ])


def plot_cie_diagram(show_wx: bool = True, input_gamut: str = "native",
                     samples: list[tuple[int, int, int]] | None = None,
                     save_path: str | None = None,
                     show: bool = True) -> None:
    """
    Plot the CIE 1931 xy diagram with:
      *  Spectral locus + purple line
      *  sRGB gamut
      *  LED gamut (R-G-B triangle + W centroid)
      *  Three LED sub-gamut triangles (RGW / RBW / BGW)
      *  Sample points: HSV ramp (when *samples* provided) or named test colours
      *  D65 white point
    """
    fig, ax = plt.subplots(figsize=(9, 8))
    ax.set_facecolor("#f8f8f8")

    # --- Spectral locus ---
    locus = _SPECTRAL_LOCUS_XY
    # Close with purple line (700->380)
    locus_closed = np.vstack([locus, locus[[0]]])
    ax.plot(locus_closed[:, 0], locus_closed[:, 1],
            color="black", lw=1.2, label="Spectral locus")
    ax.fill(locus_closed[:, 0], locus_closed[:, 1],
            color="white", alpha=0.5, zorder=0)

    # --- sRGB gamut ---
    srgb = _srgb_gamut_xy()
    srgb_closed = np.vstack([srgb, srgb[[0]]])
    ax.plot(srgb_closed[:, 0], srgb_closed[:, 1],
            "--", color="grey", lw=1.0, label="sRGB gamut")

    # --- LED sub-gamut triangles ---
    sub_colours = {"RGW": "#ffcccc", "RBW": "#ccccff", "BGW": "#ccffcc"}
    edge_colours = {"RGW": "#cc4444", "RBW": "#4444cc", "BGW": "#44aa44"}
    for g in SUB_GAMUTS:
        key = "".join(g)
        verts = np.array([PRIMARIES_XY[c] for c in g])
        poly = plt.Polygon(verts, closed=True,
                           facecolor=sub_colours.get(key, "#eeeeee"),
                           edgecolor=edge_colours.get(key, "#888888"),
                           lw=1.4, alpha=0.35)
        ax.add_patch(poly)
        # Label centroid of triangle
        cx, cy = verts.mean(axis=0)
        ax.text(cx, cy, key, ha="center", va="center",
                fontsize=8, color=edge_colours.get(key, "#888888"),
                fontweight="bold")

    # --- LED gamut outline ---
    led_rgb = np.array([PRIMARIES_XY["R"],
                        PRIMARIES_XY["G"],
                        PRIMARIES_XY["B"]])
    led_closed = np.vstack([led_rgb, led_rgb[[0]]])
    ax.plot(led_closed[:, 0], led_closed[:, 1],
            "-", color="#222222", lw=1.8, label="LED gamut (R-G-B)")

    # --- LED primaries ---
    marker_style = dict(zorder=6, s=80)
    for ch, color in zip("RGB", ["#ee2020", "#20bb20", "#2020ee"]):
        xy = PRIMARIES_XY[ch]
        ax.scatter(*xy, color=color, **marker_style)
        ax.annotate(f"LED {ch}", xy, xytext=(xy[0] + 0.01, xy[1] + 0.008),
                    fontsize=9, color=color, fontweight="bold")

    # W centroid
    xy_W = PRIMARIES_XY["W"]
    ax.scatter(*xy_W, color="#886600", marker="D", s=90, zorder=6)
    ax.annotate("LED W", xy_W, xytext=(xy_W[0] + 0.012, xy_W[1] + 0.008),
                fontsize=9, color="#886600", fontweight="bold")

    # D65 white point
    ax.scatter(*D65_xy, color="black", marker="+", s=130, lw=2.0, zorder=7,
               label=f"D65 ({D65_xy[0]:.4f},{D65_xy[1]:.4f})")
    ax.annotate("D65", D65_xy, xytext=(D65_xy[0] + 0.012, D65_xy[1] - 0.015),
                fontsize=8, color="black")

    # --- Sample points (HSV ramp if provided, else named test colours) ---
    scatter_src = samples if samples is not None else [rgb for _, rgb in _TEST_COLORS]
    use_labels  = samples is None
    scatter_x, scatter_y, scatter_colors = [], [], []
    for i, rgb_pt in enumerate(scatter_src):
        r, g, b = rgb_pt
        res = rgb_to_rgbw_subgamut(r, g, b, input_gamut)
        scatter_x.append(float(res.xy_input[0]))
        scatter_y.append(float(res.xy_input[1]))
        scatter_colors.append(np.clip([r / 255.0, g / 255.0, b / 255.0], 0.0, 1.0))
        if use_labels:
            name = _TEST_COLORS[i][0]
            ax.annotate(name, (float(res.xy_input[0]), float(res.xy_input[1])),
                        textcoords="offset points", xytext=(4, 4),
                        fontsize=7, color="#333333")

    pt_size  = 12 if samples is not None else 55
    pt_label = (f"HSV samples ({len(scatter_src)})" if samples is not None
                else "Test colours")
    ax.scatter(scatter_x, scatter_y,
               c=scatter_colors, s=pt_size, zorder=7,
               edgecolors="none" if samples is not None else "black",
               linewidths=0.0 if samples is not None else 0.5,
               label=pt_label)

    ax.set_xlim(-0.05, 0.82)
    ax.set_ylim(-0.05, 0.90)
    ax.set_xlabel("CIE x", fontsize=11)
    ax.set_ylabel("CIE y", fontsize=11)
    ax.set_title("CIE 1931 xy  -  LED sub-gamut structure & test colours",
                 fontsize=12)
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, lw=0.4, alpha=0.4)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"  Saved CIE diagram -> {save_path}")
    if show:
        plt.show()
    else:
        plt.close()


# ── CLI entry point ───────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Analytical RGB->RGBW mapper via CIE xy sub-gamut solve.")
    p.add_argument("--no-plot", action="store_true",
                   help="Skip the interactive CIE diagram window (PNG is still saved).")
    p.add_argument("--no-csv", action="store_true",
                   help="Skip writing the CSV file.")
    p.add_argument("--method", choices=["sub_gamut", "wx", "both", "wx_radial_virtual", "wx_virtual_axis_maxbright", "wx_lp_legacy"],
                   default="both",
                   help="Mapping family. For WX, use --wx-mode or a direct WX mode alias.")
    p.add_argument("--wx-mode", choices=VALID_WX_MODES, default=DEFAULT_WX_MODE,
                   help="Concrete WX model when --method wx is used.")
    p.add_argument("--wx-radial-target-position", type=float, default=WX_RADIAL_TARGET_POSITION,
                   help="Radial WX target-position policy knob used only by wx_radial_virtual (default: %(default)s).")
    p.add_argument("--gamut", choices=VALID_GAMUTS, default="native",
                   help="Input colour space (default: native LED primaries, linear).")
    p.add_argument("--rgb", nargs=3, type=int, metavar=("R", "G", "B"),
                   help="Convert a single RGB triplet and exit.")
    p.add_argument("--csv", metavar="FILE",
                   help="Override CSV output path (default: rgbw_<gamut>_<method>.csv).")
    p.add_argument("--png", metavar="FILE",
                   help="Override PNG output path (default: rgbw_<gamut>.png).")
    p.add_argument("--verify", metavar="DIR",
                   help="Scan patch-capture CSVs in DIR and write a model-vs-measured report.")
    p.add_argument("--verify-output", metavar="FILE", default="verify_report.csv",
                   help="Output CSV for --verify (default: verify_report.csv).")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    print(f"\nLED primaries (absolute Y units):")
    for ch in "RGBW":
        xy = PRIMARIES_XY[ch]
        P  = PRIMARY_XYZ[ch]
        print(f"  {ch}: xy=({xy[0]:.4f},{xy[1]:.4f})  maxY={MAX_Y[ch]:.2f}"
              f"  XYZ=({P[0]:.2f},{P[1]:.2f},{P[2]:.2f})")

    if args.verify:
        verify_captures(args.verify, args.verify_output)
        return

    if args.rgb:
        r, g, b = args.rgb
        sg = rgb_to_rgbw_subgamut(r, g, b, args.gamut)
        wx = rgb_to_rgbw_wx(r, g, b, args.gamut, wx_mode=args.wx_mode, wx_radial_target_position=args.wx_radial_target_position)
        gamut_desc = _NAMED_GAMUTS[args.gamut][2]
        print(f"\n  Input ({r},{g},{b})  [{gamut_desc}]")
        print(f"  Sub-gamut -> {sg}")
        dx, dy = verify_xy(sg)
        print(f"             xy error: dx={dx:+.5f}  dy={dy:+.5f}")
        print(f"  Whiteness -> {wx}")
        dx, dy = verify_xy(wx)
        print(f"             xy error: dx={dx:+.5f}  dy={dy:+.5f}")
        return

    run_tests(args.method, args.gamut)

    # Always generate HSV ramp samples - used for both CSV and CIE diagram
    csv_method = args.method if args.method != "both" else "sub_gamut"
    hsv_samples = _generate_hsv_samples()

    if not args.no_csv:
        csv_path = args.csv if args.csv else f"rgbw_{args.gamut}_{csv_method}.csv"
        n = write_csv(hsv_samples, csv_method, args.gamut, csv_path)
        print(f"  Wrote {n} rows \u2192 {csv_path}")

    png_path = args.png if args.png else f"rgbw_{args.gamut}.png"
    plot_cie_diagram(input_gamut=args.gamut, samples=hsv_samples,
                     save_path=png_path, show=not args.no_plot)




# ── 16-bit 3D LUT builder ────────────────────────────────────────────────────
#
# This section intentionally reuses rgb_to_rgbw_subgamut() / rgb_to_rgbw_wx()
# above.  The analytical model itself is left untouched; the LUT builder only
# maps 16-bit grid-node coordinates into normalized float color space and writes
# the solved RGBW outputs as a Delaunay-style cube at the requested bit depth.

from numpy.lib.format import open_memmap
from pathlib import Path as _Path
import json as _json
import time as _time
import math as _math
import ctypes as _ctypes
from concurrent.futures import ProcessPoolExecutor as _ProcessPoolExecutor
from concurrent.futures import wait as _wait, FIRST_COMPLETED as _FIRST_COMPLETED


def _axis_values_u16(grid_size: int, sample_scale: float = 65535.0) -> np.ndarray:
    return np.linspace(0.0, float(sample_scale), int(grid_size), dtype=np.float64)


def _select_lut_solver(method: str, wx_mode: str = DEFAULT_WX_MODE):
    method_norm = _normalize_lut_method(method)
    if method_norm == "wx":
        mode = _method_to_wx_mode(method, wx_mode)
        return lambda r, g, b, gamut="native", input_transfer="gamut": rgb_to_rgbw_wx(
            r, g, b, gamut=gamut, input_transfer=input_transfer, wx_mode=mode
        )
    return rgb_to_rgbw_subgamut



def _channel_y_fraction_from_drive(ch: str, drive: float, sample_scale: float = 65535.0) -> float:
    """Measured single-channel Y fraction for a drive code.

    The hardcoded ramp is stored on a 0..65535 axis.  sample_scale lets the
    surrounding LUT builder keep its existing axis controls while still using
    the same measured response shape.
    """
    ch = ch.upper()
    if ch not in _CHANNEL_Y_RESPONSE:
        return float(np.clip(drive / max(float(sample_scale), 1e-12), 0.0, 1.0))
    drive_65535 = float(drive) * (65535.0 / max(float(sample_scale), 1e-12))
    y = float(np.interp(np.clip(drive_65535, 0.0, 65535.0), _CHANNEL_Y_DRIVE, _channel_y_curve_strict(ch)))
    y_max = max(float(_channel_y_curve_strict(ch)[-1]), 1e-12)
    return float(np.clip(y / y_max, 0.0, 1.0))


def _drive_from_channel_y_fraction(ch: str, frac: float, sample_scale: float = 65535.0) -> float:
    """Inverse of _channel_y_fraction_from_drive()."""
    ch = ch.upper()
    f = float(np.clip(frac, 0.0, 1.0))
    if ch not in _CHANNEL_Y_RESPONSE:
        return f * float(sample_scale)
    y_curve = _channel_y_curve_strict(ch)
    target_y = f * max(float(y_curve[-1]), 1e-12)
    drive_65535 = float(np.interp(target_y, y_curve, _CHANNEL_Y_DRIVE))
    return float(np.clip(drive_65535 * (float(sample_scale) / 65535.0), 0.0, float(sample_scale)))


def _decode_source_rgb16_to_linear(rgb16: np.ndarray, gamut: str, sample_scale: float, input_transfer: str = "linear") -> np.ndarray:
    """Decode a 16-bit source RGB node to normalized linear-light components.

    The LUT path keeps source coordinates in normalized float64.  No internal
    8-bit staging is used; ``sample_scale`` only maps the external input range
    to 0..1.
    """
    normalized = np.clip(np.asarray(rgb16, dtype=np.float64) / max(float(sample_scale), 1e-12), 0.0, 1.0)
    return _apply_input_transfer_normalized(normalized, gamut, input_transfer=input_transfer)


def _encode_linear_to_model_code(linear: np.ndarray, gamut: str, input_transfer: str = "linear") -> np.ndarray:
    """Compatibility wrapper returning normalized source components.

    Older builder revisions encoded full-chroma values into a 0..255
    ``xy_target`` model domain.  The current LUT/rgb16 path is normalized-float
    end-to-end, so this function deliberately returns normalized float values.
    The name is retained to keep external patches from breaking.
    """
    return _encode_linear_to_normalized_source(linear, gamut, input_transfer=input_transfer)


def _rgb16_to_lut_chroma_and_value(
    rgb16: np.ndarray,
    *,
    gamut: str,
    sample_scale: float,
    channel_y_model: str,
    input_transfer: str = "linear",
) -> tuple[np.ndarray, float]:
    """Return (normalized full-chroma source RGB, normalized value_scale).

    LUT input coordinates are source/color-space coordinates, not measured LED
    response coordinates.  Therefore they stay normalized float64 by default
    for every gamut, including native.  The measured Y/xy ramp models are
    applied only on the output-emitter side when converting solved RGBW
    fractions back into LED drive values.
    """
    rgb = np.asarray(rgb16, dtype=np.float64)
    linear = _decode_source_rgb16_to_linear(
        rgb, gamut, sample_scale, input_transfer=input_transfer
    )

    value_scale = float(np.max(linear))
    if value_scale <= 1e-12:
        return np.zeros(3, dtype=np.float64), 0.0
    chroma_linear = linear / value_scale
    # When input_transfer='gamut', preserve the old semantic of solving the
    # full-chroma source coordinate before the named EOTF is applied, but keep it
    # normalized-float rather than encoding through 8-bit model code.
    chroma_source = _encode_linear_to_normalized_source(
        chroma_linear, gamut, input_transfer=input_transfer
    )
    chroma_linear_for_solve = _apply_input_transfer_normalized(
        chroma_source, gamut, input_transfer=input_transfer
    )
    return chroma_linear_for_solve, value_scale

def _rgbw_fraction_to_drive(
    rgbw_fraction: np.ndarray,
    *,
    sample_scale: float,
    channel_y_model: str,
    allowed_idx: list[int] | None = None,
) -> np.ndarray:
    """Map normalized emitter fractions to RGBW drive codes.

    ``allowed_idx`` is part of the solve topology, not a post-hoc color fix. It
    ensures final drive conversion is only evaluated for channels that exist in
    the chosen linear system. Inactive channels are never passed through the
    inverse Y ramp and therefore cannot acquire black-floor/duplicate-anchor
    residue.
    """
    frac = np.clip(np.asarray(rgbw_fraction, dtype=np.float64), 0.0, 1.0)
    out = np.zeros(4, dtype=np.float64)
    idx = list(range(4)) if allowed_idx is None else sorted(set(int(i) for i in allowed_idx if 0 <= int(i) < 4))
    if channel_y_model != "ramp":
        out[idx] = frac[idx] * float(sample_scale)
        return out
    for i in idx:
        out[i] = _drive_from_channel_y_fraction("RGBW"[i], float(frac[i]), sample_scale)
    return out


def _channel_xy_from_drive(ch: str, drive: float, sample_scale: float = 65535.0) -> np.ndarray:
    """Measured single-channel xy chromaticity for a drive code."""
    ch = ch.upper()
    if ch not in _CHANNEL_XY_RESPONSE:
        return np.asarray(PRIMARIES_XY[ch], dtype=np.float64)
    drive_65535 = float(drive) * (65535.0 / max(float(sample_scale), 1e-12))
    xy_curve = np.asarray(_CHANNEL_XY_RESPONSE[ch], dtype=np.float64)
    x = float(np.interp(np.clip(drive_65535, 0.0, 65535.0), _CHANNEL_Y_DRIVE, xy_curve[:, 0]))
    y = float(np.interp(np.clip(drive_65535, 0.0, 65535.0), _CHANNEL_Y_DRIVE, xy_curve[:, 1]))
    return np.array([x, y], dtype=np.float64)


def _channel_basis_xyz_for_fraction(
    ch: str,
    frac: float,
    *,
    sample_scale: float,
    channel_y_model: str,
    channel_xy_model: str,
) -> np.ndarray:
    """XYZ basis column for a normalized emitter fraction using drive-dependent xy."""
    f = float(np.clip(frac, 0.0, 1.0))
    if channel_xy_model != "ramp":
        return PRIMARY_XYZ[ch.upper()].copy()
    if channel_y_model == "ramp":
        drive = _drive_from_channel_y_fraction(ch, f, sample_scale)
    else:
        drive = f * float(sample_scale)
    xy = _channel_xy_from_drive(ch, drive, sample_scale)
    return xy_Y_to_XYZ(xy, MAX_Y[ch.upper()])


def _solve_closest_to_prior_equality(A: np.ndarray, target_xyz: np.ndarray, prior: np.ndarray) -> np.ndarray:
    """Closest vector to prior subject to A @ t == target_xyz, then nonnegative-safe."""
    A = np.asarray(A, dtype=np.float64)
    b = np.asarray(target_xyz, dtype=np.float64)
    p = np.asarray(prior, dtype=np.float64)
    try:
        t = p + A.T @ np.linalg.solve(A @ A.T, b - A @ p)
    except np.linalg.LinAlgError:
        t, _res = _nnls_solve(A, b)
        return np.maximum(t, 0.0)

    # Use the 1-D nullspace for a 3x4 system to pull the solution into the
    # nonnegative interval when possible without changing A @ t.
    try:
        _u, _s, vh = np.linalg.svd(A)
        n = vh[-1]
        n_norm = float(np.linalg.norm(n))
        if n_norm > 1e-12:
            n = n / n_norm
            lo, hi = -np.inf, np.inf
            for ti, ni in zip(t, n):
                if ni > 1e-12:
                    lo = max(lo, -float(ti) / float(ni))
                elif ni < -1e-12:
                    hi = min(hi, -float(ti) / float(ni))
                elif ti < 0.0:
                    lo, hi = 1.0, 0.0
                    break
            if lo <= hi:
                t = t + float(np.clip(0.0, lo, hi)) * n
    except np.linalg.LinAlgError:
        pass
    if np.any(t < -1e-6):
        t, _res = _nnls_solve(A, b)
    return np.maximum(t, 0.0)


def _channel_xyz_curve(ch: str, *, channel_y_model: str, channel_xy_model: str) -> np.ndarray:
    """Return the measured single-channel XYZ ramp sampled at _CHANNEL_Y_DRIVE.

    The capture gives correlated xyY samples.  Do not interpolate xy and Y as
    independent signals and then reconstruct XYZ; that invents chromaticities
    between black and the first valid colour sample.  Instead convert each
    captured xyY anchor to XYZ once, with drive=0 as the zero vector, and linearly
    interpolate XYZ components.  This preserves the actual measured xyY state
    correlation along the drive ramp.
    """
    ch = ch.upper()
    if channel_y_model == "ramp" and ch in _CHANNEL_Y_RESPONSE:
        Y_curve = _channel_y_curve_strict(ch)
    else:
        Y_curve = (np.asarray(_CHANNEL_Y_DRIVE, dtype=np.float64) / 65535.0) * float(MAX_Y[ch])

    if channel_xy_model == "ramp" and ch in _CHANNEL_XY_RESPONSE:
        xy_curve = np.asarray(_CHANNEL_XY_RESPONSE[ch], dtype=np.float64)
    else:
        xy_curve = np.repeat(np.asarray(PRIMARIES_XY[ch], dtype=np.float64)[None, :], len(_CHANNEL_Y_DRIVE), axis=0)

    out = np.zeros((len(_CHANNEL_Y_DRIVE), 3), dtype=np.float64)
    for i, (xy, Y) in enumerate(zip(xy_curve, Y_curve)):
        if float(Y) > 1e-12:
            out[i] = xy_Y_to_XYZ(np.asarray(xy, dtype=np.float64), float(Y))
    return out


def _channel_y_abs_from_drive(ch: str, drive: float, sample_scale: float = 65535.0) -> float:
    """Measured absolute Y for one emitter drive."""
    ch = ch.upper()
    drive = float(np.clip(drive, 0.0, float(sample_scale)))
    if ch in _CHANNEL_Y_RESPONSE:
        drive_65535 = drive * (65535.0 / max(float(sample_scale), 1e-12))
        return float(np.interp(
            np.clip(drive_65535, 0.0, 65535.0),
            _CHANNEL_Y_DRIVE,
            _channel_y_curve_strict(ch),
        ))
    return (drive / max(float(sample_scale), 1e-12)) * float(MAX_Y[ch])


def _drive_from_channel_y_abs(ch: str, Y_abs: float, sample_scale: float = 65535.0) -> float:
    """Inverse measured absolute-Y ramp for one emitter."""
    ch = ch.upper()
    Y = float(max(0.0, Y_abs))
    if ch not in _CHANNEL_Y_RESPONSE:
        return float(np.clip((Y / max(float(MAX_Y[ch]), 1e-12)) * float(sample_scale), 0.0, float(sample_scale)))
    y_curve = _channel_y_curve_strict(ch)
    drive_65535 = float(np.interp(np.clip(Y, 0.0, float(y_curve[-1])), y_curve, _CHANNEL_Y_DRIVE))
    return float(np.clip(drive_65535 * (float(sample_scale) / 65535.0), 0.0, float(sample_scale)))


def _scale_topology_drive_by_y(
    full_chroma_drive: np.ndarray,
    value_scale: float,
    allowed_idx: list[int],
    *,
    sample_scale: float,
    channel_y_model: str,
) -> np.ndarray:
    """Scale a full-chroma topology solution by measured per-channel Y.

    This is the LUT-safe value ramp.  The full-chroma solve establishes the
    topology and chromatic ratio.  Lower input values are produced by scaling
    each active emitter's *measured Y contribution* and then inverting that
    same channel's Y ramp back to drive.  This preserves solve(k*RGB)=k*solve(RGB)
    in luminance-contribution space and prevents low-drive xy noise from
    re-solving the colour into a different ratio.
    """
    d_full = np.clip(np.asarray(full_chroma_drive, dtype=np.float64), 0.0, float(sample_scale))
    out = np.zeros(4, dtype=np.float64)
    idx = sorted(set(int(i) for i in allowed_idx if 0 <= int(i) < 4))
    v = float(np.clip(value_scale, 0.0, 1.0))
    for i in idx:
        ch = "RGBW"[i]
        if channel_y_model == "ramp":
            y_full = _channel_y_abs_from_drive(ch, float(d_full[i]), sample_scale)
            out[i] = _drive_from_channel_y_abs(ch, y_full * v, sample_scale)
        else:
            out[i] = d_full[i] * v
    return np.clip(out, 0.0, float(sample_scale))


def _channel_xyz_from_drive(
    ch: str,
    drive: float,
    *,
    sample_scale: float,
    channel_y_model: str,
    channel_xy_model: str,
) -> np.ndarray:
    """Interpolate the measured single-channel XYZ ramp at an output drive.

    xyY ramp captures are correlated measurements of one state.  We therefore
    interpolate XYZ components between captured xyY anchors, not xy and Y
    independently.  At drive=0 the XYZ state is exactly black, so the fake
    chromaticity stored at the zero row cannot contaminate low-drive colours.
    """
    ch = ch.upper()
    drive = float(np.clip(drive, 0.0, float(sample_scale)))
    drive_65535 = drive * (65535.0 / max(float(sample_scale), 1e-12))
    curve = _channel_xyz_curve(ch, channel_y_model=channel_y_model, channel_xy_model=channel_xy_model)
    return np.array([
        np.interp(np.clip(drive_65535, 0.0, 65535.0), _CHANNEL_Y_DRIVE, curve[:, k])
        for k in range(3)
    ], dtype=np.float64)


def _basis_from_ramp_state(
    ch: str,
    frac_hint: float,
    *,
    sample_scale: float,
    channel_y_model: str,
    channel_xy_model: str,
) -> np.ndarray:
    """Return a local normalized XYZ basis from the measured ramp state.

    The solve variable remains a normalized channel fraction.  To build its
    column, evaluate the channel's interpolated xyY ramp at the hinted fraction
    and divide the measured XYZ by that fraction.  If the hint is nearly zero,
    use a tiny positive probe so an allowed-but-currently-zero channel can still
    be considered without creating a singular all-zero column.
    """
    f = float(np.clip(frac_hint, 0.0, 1.0))
    probe_f = max(f, 1e-6)
    if channel_y_model == "ramp":
        drive = _drive_from_channel_y_fraction(ch, probe_f, sample_scale)
    else:
        drive = probe_f * float(sample_scale)
    return _channel_xyz_from_drive(
        ch,
        drive,
        sample_scale=sample_scale,
        channel_y_model=channel_y_model,
        channel_xy_model=channel_xy_model,
    ) / probe_f


def _solve_fraction_in_fixed_topology_with_ramps(
    target_xyz: np.ndarray,
    initial_frac: np.ndarray,
    allowed_idx: list[int],
    *,
    sample_scale: float,
    channel_y_model: str,
    channel_xy_model: str,
) -> np.ndarray:
    """Solve target XYZ inside an explicit legal topology using ramped xyY states.

    No channel outside *allowed_idx* can enter the result.  For the measured xy
    ramp mode, basis columns are built directly from the interpolated single-
    channel ramp states rather than by iterating a centroid solve.  This keeps
    the model within the intended topology set:
        RGW / RBW / BGW / R / G / B / W / RG / RB / BG / RW / GW / BW.
    """
    initial_frac = np.clip(np.asarray(initial_frac, dtype=np.float64), 0.0, 1.0)
    target_xyz = np.asarray(target_xyz, dtype=np.float64)
    allowed_idx = sorted(set(int(i) for i in allowed_idx if 0 <= int(i) < 4))

    out = np.zeros(4, dtype=np.float64)
    if not allowed_idx:
        return out

    # With constant xy, the existing centroid solution is already the desired
    # linear basis solve.  Only zero inactive channels and renormalize if needed.
    if channel_xy_model != "ramp":
        out[allowed_idx] = initial_frac[allowed_idx]
        max_out = float(np.max(out))
        if max_out > 1.0:
            out = out / max_out
        return np.clip(out, 0.0, 1.0)

    ch_order = "RGBW"
    A = np.column_stack([
        _basis_from_ramp_state(
            ch_order[i],
            float(initial_frac[i]),
            sample_scale=sample_scale,
            channel_y_model=channel_y_model,
            channel_xy_model=channel_xy_model,
        )
        for i in allowed_idx
    ])

    try:
        cand, _residuals, _rank, _s = np.linalg.lstsq(A, target_xyz, rcond=None)
    except np.linalg.LinAlgError:
        cand, _res = _nnls_solve(A, target_xyz)

    cand = np.asarray(cand, dtype=np.float64)
    if np.any(cand < -1e-6):
        cand, _res = _nnls_solve(A, target_xyz)
    cand = np.maximum(cand, 0.0)

    max_t = float(np.max(cand)) if cand.size else 0.0
    if max_t > 1.0:
        cand = cand / max_t

    for j, i in enumerate(allowed_idx):
        out[i] = float(cand[j])

    return np.clip(out, 0.0, 1.0)


def _refine_rgbw_fraction_with_channel_xy(
    rgb_model: np.ndarray,
    initial_frac: np.ndarray,
    *,
    value_scale: float,
    gamut: str,
    sample_scale: float,
    channel_y_model: str,
    channel_xy_model: str,
    iterations: int = 4,
    allowed_idx: list[int] | None = None,
) -> np.ndarray:
    """Topology-preserving measured-ramp solve.

    This replaces the earlier fixed-point xy-ramp refinement.  The input
    fraction from the analytical solve defines the intended target XYZ after
    chroma/value scaling.  The final solve is performed directly inside the
    explicit allowed topology using interpolated single-channel xyY ramp states.
    """
    t_all = np.clip(np.asarray(initial_frac, dtype=np.float64), 0.0, 1.0)
    if not np.any(t_all > 1e-12):
        return t_all

    target_xyz = np.zeros(3, dtype=np.float64)
    for _ch, _frac in zip("RGBW", t_all):
        target_xyz += float(_frac) * PRIMARY_XYZ[_ch]

    if allowed_idx is None:
        allowed_idx = [i for i, v in enumerate(t_all) if float(v) > 1e-10]

    return _solve_fraction_in_fixed_topology_with_ramps(
        target_xyz,
        t_all,
        allowed_idx,
        sample_scale=sample_scale,
        channel_y_model=channel_y_model,
        channel_xy_model=channel_xy_model,
    )


# Internal sub-gamut boundary handling
# -----------------------------------
# Normal sub-gamut solves select one of RGW/RBW/BGW to make decomposition
# unique.  On the *internal* W-primary boundaries (R-W, G-W, B-W), the correct
# legal topology is not the union of both adjacent triangles; it is the actual
# boundary segment itself: primary + W only.  This is intentionally very tight
# and only applies to all-three-channel chroma values that land directly on an
# internal W-primary boundary.  Native single/dual edges keep their usual
# identity/edge-lock behavior.  Keep the default eps very tight: D65 is visually
# near the B-W divider, but it is not on the exact B-W line and must remain a
# normal sub-gamut solve.
_BOUNDARY_PRIMARY_ORDER = ("R", "G", "B")


def _point_line_segment_distance(P: np.ndarray, A: np.ndarray, B: np.ndarray) -> tuple[float, float]:
    """Return perpendicular xy distance from P to segment A-B and segment parameter t."""
    P = np.asarray(P, dtype=np.float64)
    A = np.asarray(A, dtype=np.float64)
    B = np.asarray(B, dtype=np.float64)
    v = B - A
    vv = float(np.dot(v, v))
    if vv <= 1e-24:
        return float(np.linalg.norm(P - A)), 0.0
    t = float(np.dot(P - A, v) / vv)
    proj = A + np.clip(t, 0.0, 1.0) * v
    return float(np.linalg.norm(P - proj)), t


def _internal_subgamut_boundary_label(xy: np.ndarray, eps: float = 5e-6) -> str | None:
    """Return 'RW', 'GW', or 'BW' when xy is on an internal W-primary boundary."""
    best_label: str | None = None
    best_dist = float("inf")
    Wxy = PRIMARIES_XY["W"]
    for ch in _BOUNDARY_PRIMARY_ORDER:
        dist, t = _point_line_segment_distance(xy, Wxy, PRIMARIES_XY[ch])
        # t==0 is the W centroid, t==1 is the primary endpoint.  Exclude the
        # endpoints so pure channels/white do not become artificial boundary
        # cases.
        if dist <= eps and (eps < t < 1.0 - eps) and dist < best_dist:
            best_dist = dist
            best_label = ch + "W"
    return best_label


def _model_code_to_linear(rgb_model: np.ndarray, gamut: str, input_transfer: str = "linear") -> np.ndarray:
    """Return normalized linear-light components for the LUT model path.

    The name is kept for compatibility with earlier revisions, but LUT/rgb16
    solves now pass normalized float RGB, not 0..255 model code.
    """
    return np.clip(np.asarray(rgb_model, dtype=np.float64), 0.0, 1.0)



def _solve_fraction_for_fixed_topology_from_xyz(
    target_xyz: np.ndarray,
    allowed_idx: list[int],
) -> np.ndarray:
    """Solve target XYZ using exactly the supplied topology.

    This is the topology-contract solve used by the LUT/rgb16 sub-gamut path.
    The matrix columns are built only from ``allowed_idx``. Therefore the solve
    has no mathematical route to produce an inactive channel. This replaces the
    older classify-then-wide-solve-then-mask flow.

    For 3-channel sub-gamuts (RGW/RBW/BGW), A is 3x3 and the result is the
    exact XYZ linear solve when the target lies in that triangle.

    For exact 2-channel edges (RG/RB/BG/RW/GW/BW), A is 3x2 and the target is
    expected to lie on that line; least-squares plus non-negative projection is
    used only for tiny numeric/ramp drift. For single-channel topologies, the
    scalar projection onto that channel is used.
    """
    idx = sorted(set(int(i) for i in allowed_idx if 0 <= int(i) < 4))
    out = np.zeros(4, dtype=np.float64)
    if not idx:
        return out

    ch_order = "RGBW"
    A = np.column_stack([PRIMARY_XYZ[ch_order[i]] for i in idx])
    b = np.asarray(target_xyz, dtype=np.float64)

    if len(idx) == 1:
        col = A[:, 0]
        denom = float(np.dot(col, col))
        t = np.array([0.0 if denom <= 1e-24 else float(np.dot(col, b) / denom)], dtype=np.float64)
    elif len(idx) == 3:
        try:
            t = np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            t, _res = _nnls_solve(A, b)
    else:
        try:
            t, _residuals, _rank, _s = np.linalg.lstsq(A, b, rcond=None)
        except np.linalg.LinAlgError:
            t, _res = _nnls_solve(A, b)

    t = np.asarray(t, dtype=np.float64)
    if np.any(t < -1e-6):
        t, _res = _nnls_solve(A, b)
    t = np.maximum(t, 0.0)

    max_t = float(np.max(t)) if t.size else 0.0
    if max_t > 1.0:
        t = t / max_t

    for local_i, global_i in enumerate(idx):
        out[global_i] = float(t[local_i])
    return np.clip(out, 0.0, 1.0)



def _xyz_from_rgbw_fraction(frac: np.ndarray) -> np.ndarray:
    """Predict absolute XYZ from normalized RGBW emitter fractions."""
    f = np.clip(np.asarray(frac, dtype=np.float64), 0.0, None)
    xyz = np.zeros(3, dtype=np.float64)
    for i, ch in enumerate("RGBW"):
        xyz += float(f[i]) * PRIMARY_XYZ[ch]
    return xyz


def _solve_wx_combo_weights(
    candidate_xyz: np.ndarray,
    target_xyz: np.ndarray,
) -> np.ndarray:
    """Final constrained solve between three sub-gamut candidate primaries.

    ``candidate_xyz`` is shape (3, 3): one XYZ column-like row for each of
    RGW/RBW/BGW.  The final solve is still a three-variable constrained solve;
    the variables are sub-gamut candidate weights, not direct RGBW channel
    intensities.  This keeps the experimental WX path from becoming a generic
    unconstrained 4-channel NNLS.
    """
    C = np.asarray(candidate_xyz, dtype=np.float64).reshape(3, 3)
    b = np.asarray(target_xyz, dtype=np.float64)
    valid = np.isfinite(C).all(axis=1) & (np.linalg.norm(C, axis=1) > 1e-12)
    if not np.any(valid):
        return np.zeros(3, dtype=np.float64)

    A = C[valid].T
    w_valid, _res = _nnls_solve(A, b)
    w = np.zeros(3, dtype=np.float64)
    w[np.where(valid)[0]] = np.maximum(w_valid, 0.0)
    return w


def _ray_interval_for_subgamut_virtual_primary(
    target_xy: np.ndarray,
    tri: tuple[str, str, str],
) -> tuple[str, np.ndarray, np.ndarray, float, float] | None:
    """Ray/triangle interval used by the WX virtual-primary construction.

    For a sub-gamut such as RGW, the missing native primary is B.  The ray from
    B through the requested target xy intersects the RGW triangle in a segment.
    Points on that segment are the RGW-side colours that can participate in a
    later virtual-primary mix to reconstruct the requested target.
    """
    outer = [ch for ch in tri if ch != "W"]
    missing_list = [ch for ch in "RGB" if ch not in outer]
    if len(missing_list) != 1:
        return None
    missing = missing_list[0]
    origin = np.asarray(PRIMARIES_XY[missing], dtype=np.float64)
    target = np.asarray(target_xy, dtype=np.float64)
    direction = target - origin
    if not np.isfinite(direction).all() or float(np.dot(direction, direction)) <= 1e-24:
        return None

    A, B, C = [np.asarray(PRIMARIES_XY[ch], dtype=np.float64) for ch in tri]
    M = np.array([[A[0] - C[0], B[0] - C[0]],
                  [A[1] - C[1], B[1] - C[1]]], dtype=np.float64)
    try:
        Minv = np.linalg.inv(M)
    except np.linalg.LinAlgError:
        return None

    def _bary(q: np.ndarray) -> np.ndarray:
        uv = Minv @ (q - C)
        return np.array([uv[0], uv[1], 1.0 - uv[0] - uv[1]], dtype=np.float64)

    lam0 = _bary(origin)
    lam1 = _bary(origin + direction)
    dlam = lam1 - lam0
    lo = -np.inf
    hi = np.inf
    eps = 1e-10
    for a, b in zip(lam0, dlam):
        # a + b*t >= -eps
        if abs(float(b)) <= 1e-15:
            if float(a) < -eps:
                return None
        elif b > 0:
            lo = max(lo, float((-eps - a) / b))
        else:
            hi = min(hi, float((-eps - a) / b))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi < lo:
        return None
    return missing, origin, direction, lo, hi


def _eval_wx_virtual_primary_at_t(
    origin: np.ndarray,
    direction: np.ndarray,
    t: float,
    tri: tuple[str, str, str],
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray] | None:
    """Evaluate one possible max-normalised sub-gamut virtual primary."""
    q_xy = origin + float(t) * direction
    if not np.isfinite(q_xy).all():
        return None
    t_unit = _solve_xyz(tri[0], tri[1], tri[2], xy_Y_to_XYZ(q_xy, 1.0))
    if t_unit is None or not np.isfinite(t_unit).all():
        return None
    t_unit = np.maximum(np.asarray(t_unit, dtype=np.float64), 0.0)
    max_unit = float(np.max(t_unit))
    if max_unit <= 1e-12:
        return None

    frac = np.zeros(4, dtype=np.float64)
    for ch, tv in zip(tri, t_unit / max_unit):
        frac["RGBW".index(ch)] = float(tv)
    xyz = _xyz_from_rgbw_fraction(frac)
    return float(xyz[1]), frac, xyz, q_xy


def _max_y_virtual_primary_for_subgamut(
    target_xy: np.ndarray,
    tri: tuple[str, str, str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return the max-Y virtual primary for one W sub-gamut.

    This replaces the earlier explicit ray-projection construction with the
    equivalent constrained sub-gamut system.  For a sub-gamut such as RGW, the
    missing native primary is B.  The virtual primary is solved as the brightest
    legal RGW state which, when mixed with the missing B primary, can reproduce
    the requested target chromaticity.

    Variables
    ---------
      t0,t1,t2 : emitter fractions for the supplied sub-gamut, bounded [0,1]
      u        : non-negative amount of the missing native primary used only to
                 enforce the virtual-primary topology relation

    Constraint
    ----------
      xy( A_sub @ t + u * P_missing ) == target_xy

    Objective
    ---------
      maximise Y( A_sub @ t )

    The returned virtual primary is only ``A_sub @ t``; the missing-primary
    helper variable is discarded.  This keeps the WX path aligned with the
    regular mathematical model: every virtual primary is still produced from a
    constrained three-emitter sub-gamut solve.  No generic direct four-column
    RGBW solve is used.
    """
    target_xy = np.asarray(target_xy, dtype=np.float64)
    out = np.zeros(4, dtype=np.float64)

    outer = [ch for ch in tri if ch != "W"]
    missing_list = [ch for ch in "RGB" if ch not in outer]
    if len(missing_list) != 1:
        return out, np.zeros(3, dtype=np.float64), D65_xy.copy()
    missing = missing_list[0]

    try:
        from scipy.optimize import linprog as _linprog

        cols = [PRIMARY_XYZ[ch] for ch in tri] + [PRIMARY_XYZ[missing]]
        M = np.column_stack(cols)  # 3 x 4, variables: sub-gamut t0..t2 + missing helper u
        x, y = float(target_xy[0]), float(target_xy[1])
        Aeq = np.vstack([
            (1.0 - x) * M[0] - x * M[1] - x * M[2],
            -y * M[0] + (1.0 - y) * M[1] - y * M[2],
        ])
        beq = np.zeros(2, dtype=np.float64)

        # Maximise sub-gamut Y only.  The missing-primary helper participates in
        # the chromaticity constraint, but is not part of the virtual primary and
        # therefore receives zero objective weight.
        c = np.array([-PRIMARY_XYZ[ch][1] for ch in tri] + [0.0], dtype=np.float64)
        res = _linprog(
            c,
            A_eq=Aeq,
            b_eq=beq,
            bounds=[(0.0, 1.0), (0.0, 1.0), (0.0, 1.0), (0.0, None)],
            method="highs",
        )
        if res.success and np.isfinite(res.x).all():
            t = np.maximum(np.asarray(res.x[:3], dtype=np.float64), 0.0)
            # Numerical guard: the LP objective should naturally drive at least
            # one sub-gamut emitter to full scale, but keep the virtual primary
            # max-normalized if a backend returns a slightly sub-unit solution.
            max_t = float(np.max(t))
            if max_t > 1e-12:
                if max_t < 1.0 - 1e-9:
                    t = t / max_t
                for ch, tv in zip(tri, t):
                    out["RGBW".index(ch)] = float(tv)
                xyz = _xyz_from_rgbw_fraction(out)
                return np.clip(out, 0.0, 1.0), xyz, XYZ_to_xy(xyz)
    except Exception:
        pass

    # Robust fallback: use the old geometric interval helper only when the
    # constrained LP path is unavailable.  The normal path above is the intended
    # math-model-aligned virtual-primary solve.
    interval = _ray_interval_for_subgamut_virtual_primary(target_xy, tri)
    if interval is not None:
        _missing, origin, direction, lo, hi = interval
        ts = np.linspace(float(lo), float(hi), 33, dtype=np.float64)
        best: tuple[float, np.ndarray, np.ndarray, np.ndarray] | None = None
        for tv in ts:
            ev = _eval_wx_virtual_primary_at_t(origin, direction, float(tv), tri)
            if ev is None:
                continue
            yv, frac, xyz, q_xy = ev
            if best is None or yv > best[0]:
                best = (yv, frac, xyz, q_xy)
        if best is not None:
            return best[1], best[2], best[3]

    # Last-resort fallback: target solve inside this sub-gamut and normalize.
    frac = _solve_fraction_for_fixed_topology_from_xyz(
        xy_Y_to_XYZ(np.asarray(target_xy, dtype=np.float64), 1.0),
        ["RGBW".index(ch) for ch in tri],
    )
    max_f = float(np.max(frac))
    if max_f > 1e-12:
        frac = frac / max_f
    xyz = _xyz_from_rgbw_fraction(frac)
    return frac, xyz, XYZ_to_xy(xyz)


def _chromaticity_constraint_rows_for_columns(xy: np.ndarray, M: np.ndarray) -> np.ndarray:
    """Return linear xy chromaticity equality rows for emitter columns.

    For any candidate vector ``f`` with XYZ = M @ f, CIE xy requires:
        X - x*(X+Y+Z) = 0
        Y - y*(X+Y+Z) = 0
    which expands to two linear constraints in the emitter fractions.  Earlier
    WX experiments accidentally omitted the Z terms; keeping this helper shared
    prevents that class of drift from returning.
    """
    xy = np.asarray(xy, dtype=np.float64)
    M = np.asarray(M, dtype=np.float64)
    x, y = float(xy[0]), float(xy[1])
    return np.vstack([
        (1.0 - x) * M[0] - x * M[1] - x * M[2],
        -y * M[0] + (1.0 - y) * M[1] - y * M[2],
    ])


def _strict_project_target_xyz_to_led_hull(target_xyz: np.ndarray) -> tuple[np.ndarray, bool, np.ndarray]:
    """Project impossible target xy through the proven strict sub-gamut model.

    Named-gamut targets such as Rec.2020 green or Rec.709 blue can lie outside
    the measured LED RGB hull.  The verifier expects those values to compare
    against the model-projected achievable xy, not the raw named-gamut xy.  For
    WX we therefore reuse the strict sub-gamut projection as the target axis
    before attempting a four-channel-capable extraction.

    Returns ``(projected_xyz, projected, strict_frac)``.  The projected XYZ uses
    the strict model's output chromaticity with unit Y; WX then builds its own
    full-chroma endpoint around that achievable xy.
    """
    target_xyz = np.asarray(target_xyz, dtype=np.float64)
    out_zero = np.zeros(4, dtype=np.float64)
    if not np.isfinite(target_xyz).all() or float(target_xyz[1]) <= 1e-12:
        return target_xyz, False, out_zero

    target_xy = XYZ_to_xy(target_xyz)
    in_hull = _xy_in_triangle(
        target_xy,
        PRIMARIES_XY["R"], PRIMARIES_XY["G"], PRIMARIES_XY["B"],
        eps=1e-9,
    )
    if in_hull:
        return target_xyz, False, out_zero

    # Strict projection: choose the same restricted 3-emitter topology that the
    # normal mathematical model would use for this impossible target, then use
    # its achieved xy as the expected target for WX.  This prevents live/offline
    # verification from chasing raw Rec.2020/Rec.709 points the LEDs cannot hit.
    best_frac = np.zeros(4, dtype=np.float64)
    best_xyz = np.zeros(3, dtype=np.float64)
    best_residual = np.inf
    for tri in SUB_GAMUTS:
        M = np.column_stack([PRIMARY_XYZ[c] for c in tri])
        t, res = _nnls_solve(M, target_xyz)
        t = np.maximum(np.asarray(t, dtype=np.float64), 0.0)
        max_t = float(np.max(t)) if t.size else 0.0
        if max_t > 1.0:
            t = t / max_t
        frac = np.zeros(4, dtype=np.float64)
        for ch, tv in zip(tri, t):
            frac["RGBW".index(ch)] = float(tv)
        xyz = _xyz_from_rgbw_fraction(frac)
        if not np.isfinite(xyz).all() or float(xyz[1]) <= 1e-12:
            continue
        # Match the strict model's fallback selection: choose the sub-gamut
        # with the smallest NNLS residual *before* full-drive normalization.
        # Selecting after clipping can incorrectly prefer W-heavy points for
        # impossible named-gamut primaries such as Rec.2020 red.
        residual = float(res) if np.isfinite(res) else float("inf")
        if residual < best_residual:
            best_residual = residual
            best_frac = frac
            best_xyz = xyz

    if float(best_xyz[1]) <= 1e-12:
        return target_xyz, False, out_zero
    projected_xy = XYZ_to_xy(best_xyz)
    return xy_Y_to_XYZ(projected_xy, 1.0), True, best_frac





# ── WX radial virtual-primary geometry ────────────────────────────────────────
#
# This is the "description image" implementation.  The active physical
# sub-gamut containing the requested/projected xy defines a polar coordinate
# around the W diode:
#   * angular_position: where the target ray falls between that sub-gamut's two
#     RGB hull vertices;
#   * hull_fraction: how far from W toward the RGB hull the active virtual
#     primary should live.
# The same angular_position and hull_fraction are then applied to RGW/RBW/BGW.
# The three virtual primaries therefore stay visually consistent: the virtual
# triangle rotates with hue and scales toward/away from W instead of letting each
# sub-gamut independently choose a different max-W endpoint.

WX_RADIAL_TARGET_POSITION = 0.72
WX_RADIAL_MIN_TARGET_POSITION = 0.05
WX_RADIAL_MAX_TARGET_POSITION = 0.999999

# Canonical CCW sector order around W.  SUB_GAMUTS keeps the solver channel
# order, while this mapping keeps the angular geometry stable.
_WX_RADIAL_SECTOR_BY_OUTER: dict[frozenset[str], tuple[str, str]] = {
    frozenset(("R", "G")): ("R", "G"),
    frozenset(("G", "B")): ("G", "B"),
    frozenset(("R", "B")): ("B", "R"),
}


def _wx_radial_ccw_span(a: float, b: float) -> float:
    return float((float(b) - float(a)) % (2.0 * np.pi))


def _wx_radial_angle_from_w_to_xy(xy: np.ndarray) -> float:
    wxy = np.asarray(PRIMARIES_XY["W"], dtype=np.float64)
    v = np.asarray(xy, dtype=np.float64) - wxy
    a = float(np.arctan2(v[1], v[0]))
    if a < 0.0:
        a += float(2.0 * np.pi)
    return a


def _wx_radial_sector_for_subgamut(tri: tuple[str, str, str]) -> tuple[str, str]:
    outer = frozenset(ch for ch in tri if ch != "W")
    try:
        return _WX_RADIAL_SECTOR_BY_OUTER[outer]
    except KeyError as exc:
        raise ValueError(f"Unsupported WX radial sub-gamut: {tri!r}") from exc


def _wx_radial_hull_point_for_sector_position(
    tri: tuple[str, str, str],
    angular_position: float,
) -> tuple[np.ndarray, float, float]:
    """Return the RGB-hull point for a normalized sector angle.

    Returns ``(xy, radial_distance_from_W, theta)``.  ``angular_position`` is
    0..1 between the canonical sector's first and second RGB vertex.
    """
    wxy = np.asarray(PRIMARIES_XY["W"], dtype=np.float64)
    c0, c1 = _wx_radial_sector_for_subgamut(tri)
    a0 = _wx_radial_angle_from_w_to_xy(PRIMARIES_XY[c0])
    a1 = _wx_radial_angle_from_w_to_xy(PRIMARIES_XY[c1])
    span = _wx_radial_ccw_span(a0, a1)
    u = float(np.clip(angular_position, 0.0, 1.0))
    theta = a0 + u * span
    direction = np.array([np.cos(theta), np.sin(theta)], dtype=np.float64)

    p0 = np.asarray(PRIMARIES_XY[c0], dtype=np.float64)
    p1 = np.asarray(PRIMARIES_XY[c1], dtype=np.float64)
    edge = p1 - p0
    M = np.column_stack([direction, -edge])
    try:
        r, s = np.linalg.solve(M, p0 - wxy)
    except np.linalg.LinAlgError:
        # Degenerate fallback should never happen with real RGB primaries.
        q = (p0 + p1) * 0.5
        return q, float(np.linalg.norm(q - wxy)), theta

    # Clamp only as a numerical guard.  The canonical sector ray should hit the
    # matching RGB edge with r>=0 and s in [0,1].
    r = max(0.0, float(r))
    q = wxy + r * direction
    if s < -1e-6 or s > 1.0 + 1e-6:
        s = float(np.clip(s, 0.0, 1.0))
        q = p0 + s * edge
        r = float(np.linalg.norm(q - wxy))
    return np.asarray(q, dtype=np.float64), r, theta


def _wx_radial_solve_virtual_primary_at_xy(
    q_xy: np.ndarray,
    tri: tuple[str, str, str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Solve one radial virtual primary in its physical W sub-gamut."""
    q_xy = np.asarray(q_xy, dtype=np.float64)
    t_unit = _solve_xyz(tri[0], tri[1], tri[2], xy_Y_to_XYZ(q_xy, 1.0))
    if t_unit is None or not np.isfinite(t_unit).all():
        frac = _solve_fraction_for_fixed_topology_from_xyz(
            xy_Y_to_XYZ(q_xy, 1.0),
            ["RGBW".index(ch) for ch in tri],
        )
    else:
        t_unit = np.maximum(np.asarray(t_unit, dtype=np.float64), 0.0)
        max_t = float(np.max(t_unit))
        frac = np.zeros(4, dtype=np.float64)
        if max_t > 1e-12:
            for ch, tv in zip(tri, t_unit / max_t):
                frac["RGBW".index(ch)] = float(tv)
    frac = np.clip(frac, 0.0, 1.0)
    xyz = _xyz_from_rgbw_fraction(frac)
    xy = XYZ_to_xy(xyz) if float(xyz[1]) > 1e-12 else q_xy.copy()
    return frac, xyz, xy


def _wx_radial_virtual_primary_state(
    target_xy: np.ndarray,
    *,
    target_position: float = WX_RADIAL_TARGET_POSITION,
) -> dict[str, object] | None:
    """Build the consistent radial virtual-primary triangle for ``target_xy``.

    ``target_position`` describes where the target should sit on the active
    W→virtual-primary radial axis.  For example, 0.72 means the active virtual
    primary is chosen so the target sits 72% of the way from W to that virtual
    point, unless that would push the virtual point beyond the RGB hull.  Values
    closer to 1.0 keep the triangle closer to the target/W and increase W usage;
    smaller values push the virtual primaries toward the RGB hull.
    """
    target_xy = np.asarray(target_xy, dtype=np.float64)
    if not np.isfinite(target_xy).all():
        return None

    active_tri = _find_sub_gamut(target_xy)
    projected = False
    if active_tri is None:
        projected_xyz, projected, _strict_frac = _strict_project_target_xyz_to_led_hull(
            xy_Y_to_XYZ(target_xy, 1.0)
        )
        target_xy = XYZ_to_xy(projected_xyz)
        active_tri = _find_sub_gamut(target_xy)
    if active_tri is None:
        return None

    wxy = np.asarray(PRIMARIES_XY["W"], dtype=np.float64)
    target_vec = target_xy - wxy
    target_radius = float(np.linalg.norm(target_vec))

    c0, c1 = _wx_radial_sector_for_subgamut(active_tri)
    a0 = _wx_radial_angle_from_w_to_xy(PRIMARIES_XY[c0])
    a1 = _wx_radial_angle_from_w_to_xy(PRIMARIES_XY[c1])
    span = _wx_radial_ccw_span(a0, a1)
    if target_radius <= 1e-12 or span <= 1e-12:
        angular_position = 0.5
    else:
        target_theta = _wx_radial_angle_from_w_to_xy(target_xy)
        angular_position = float(np.clip(_wx_radial_ccw_span(a0, target_theta) / span, 0.0, 1.0))

    active_hull_xy, active_hull_radius, _active_theta = _wx_radial_hull_point_for_sector_position(
        active_tri, angular_position
    )
    target_position = float(np.clip(target_position, WX_RADIAL_MIN_TARGET_POSITION, WX_RADIAL_MAX_TARGET_POSITION))
    if active_hull_radius <= 1e-12:
        hull_fraction = 0.0
    else:
        desired_virtual_radius = target_radius / target_position
        hull_fraction = float(np.clip(desired_virtual_radius / active_hull_radius, 0.0, 1.0))

    virtual_fracs: list[np.ndarray] = []
    virtual_xyz: list[np.ndarray] = []
    virtual_xy: list[np.ndarray] = []
    hull_xy: list[np.ndarray] = []
    theta_by_tri: list[float] = []

    for tri in SUB_GAMUTS:
        hxy, hr, theta = _wx_radial_hull_point_for_sector_position(tri, angular_position)
        q_xy = wxy + hull_fraction * (hxy - wxy)
        frac, xyz, xy = _wx_radial_solve_virtual_primary_at_xy(q_xy, tri)
        virtual_fracs.append(frac)
        virtual_xyz.append(xyz)
        virtual_xy.append(xy)
        hull_xy.append(hxy)
        theta_by_tri.append(float(theta))

    return {
        "target_xy": target_xy,
        "projected": bool(projected),
        "active_subgamut": active_tri,
        "angular_position": float(angular_position),
        "target_position": float(target_position),
        "hull_fraction": float(hull_fraction),
        "active_hull_xy": np.asarray(active_hull_xy, dtype=np.float64),
        "virtual_fracs": virtual_fracs,
        "virtual_xyz": virtual_xyz,
        "virtual_xy": virtual_xy,
        "hull_xy": hull_xy,
        "theta_by_tri": theta_by_tri,
    }


def _select_wx_radial_virtual_primary_set(
    target_xy: np.ndarray,
    *,
    target_position: float = WX_RADIAL_TARGET_POSITION,
) -> tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray]] | None:
    """Compatibility wrapper returning the radial virtual-primary set."""
    state = _wx_radial_virtual_primary_state(target_xy, target_position=target_position)
    if state is None:
        return None
    return (
        list(state["virtual_fracs"]),  # type: ignore[arg-type]
        list(state["virtual_xyz"]),    # type: ignore[arg-type]
        list(state["virtual_xy"]),     # type: ignore[arg-type]
    )


def _solve_wx_radial_virtual_fraction_for_xy(
    target_xy: np.ndarray,
    *,
    target_position: float = WX_RADIAL_TARGET_POSITION,
) -> np.ndarray:
    """Solve the final RGBW endpoint via the radial virtual-primary triangle."""
    state = _wx_radial_virtual_primary_state(target_xy, target_position=target_position)
    if state is None:
        return np.zeros(4, dtype=np.float64)
    virtual_fracs = list(state["virtual_fracs"])  # type: ignore[arg-type]
    virtual_xyz = list(state["virtual_xyz"])      # type: ignore[arg-type]
    solve_xy = np.asarray(state["target_xy"], dtype=np.float64)
    weights = _solve_virtual_primary_triangle_weights(solve_xy, np.stack(virtual_xyz, axis=0))
    combined = np.zeros(4, dtype=np.float64)
    for wi, frac in zip(weights, virtual_fracs):
        combined += float(wi) * np.asarray(frac, dtype=np.float64)
    max_c = float(np.max(combined))
    if max_c > 1e-12:
        combined = combined / max_c
    return np.clip(combined, 0.0, 1.0)


def _solve_wx_radial_virtual_fraction_from_xyz(
    target_xyz: np.ndarray,
    *,
    target_position: float = WX_RADIAL_TARGET_POSITION,
) -> np.ndarray:
    """Projected-target-safe radial virtual-primary WX endpoint."""
    target_xyz = np.asarray(target_xyz, dtype=np.float64)
    if not np.isfinite(target_xyz).all() or float(target_xyz[1]) <= 1e-12:
        return np.zeros(4, dtype=np.float64)
    projected_xyz, _projected, strict_frac = _strict_project_target_xyz_to_led_hull(target_xyz)
    target_xy = XYZ_to_xy(projected_xyz)
    frac = _solve_wx_radial_virtual_fraction_for_xy(target_xy, target_position=target_position)
    if float(np.max(frac)) <= 1e-12 and float(np.max(strict_frac)) > 1e-12:
        return np.clip(strict_frac / float(np.max(strict_frac)), 0.0, 1.0)
    return np.clip(frac, 0.0, 1.0)




def _wx_axis_candidates_for_subgamut(
    target_xy: np.ndarray,
    tri: tuple[str, str, str],
    *,
    min_active_floor: float = 1.0 / 1024.0,
    samples: int = 513,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, tuple[float, float, float, float]]]:
    """Return candidate virtual primaries on one bounded WX axis.

    Candidates are sorted from most desirable to least desirable.  The primary
    sort key is W utilisation, not non-degeneracy.  A tiny active-emitter floor
    is used only as an anti-collapse preference; if the axis cannot support it,
    zero-floor candidates remain available.
    """
    target_xy = np.asarray(target_xy, dtype=np.float64)
    interval = _ray_interval_for_subgamut_virtual_primary(target_xy, tri)
    candidates: list[tuple[np.ndarray, np.ndarray, np.ndarray, tuple[float, float, float, float]]] = []

    if interval is not None:
        _missing, origin, direction, lo, hi = interval
        if np.isfinite(lo) and np.isfinite(hi) and hi >= lo:
            ts = np.linspace(float(lo), float(hi), max(33, int(samples)), dtype=np.float64)
            tri_idx = ["RGBW".index(ch) for ch in tri]
            for tv in ts:
                ev = _eval_wx_virtual_primary_at_t(origin, direction, float(tv), tri)
                if ev is None:
                    continue
                yv, frac, xyz, q_xy = ev
                if float(np.max(frac)) <= 1e-12 or not np.isfinite(xyz).all() or float(xyz[1]) <= 1e-12:
                    continue
                active = np.asarray([frac[i] for i in tri_idx], dtype=np.float64)
                min_active = float(np.min(active)) if active.size else 0.0
                w_value = float(frac[3])
                # Prefer satisfying the anti-collapse floor, but never ahead of
                # W usage by a large margin.  The floor flag only breaks close
                # cases and avoids exact edge collapse where possible.
                floor_ok = 1.0 if min_active + 1e-12 >= float(min_active_floor) else 0.0
                dist_to_target = float(np.linalg.norm(np.asarray(q_xy) - target_xy))
                score = (w_value, floor_ok, min_active, float(yv) - 1e-6 * dist_to_target)
                candidates.append((np.clip(frac, 0.0, 1.0), xyz, np.asarray(q_xy, dtype=np.float64), score))

    if not candidates:
        frac, xyz, q_xy = _max_y_virtual_primary_for_subgamut(target_xy, tri)
        if float(np.max(frac)) > 1e-12 and np.isfinite(xyz).all() and float(xyz[1]) > 1e-12:
            active = frac[["RGBW".index(ch) for ch in tri]]
            score = (float(frac[3]), 0.0, float(np.min(active)), float(xyz[1]))
            candidates.append((np.clip(frac, 0.0, 1.0), xyz, q_xy, score))

    # If this axis can support the tiny anti-collapse floor at all, discard the
    # exact-edge alternatives for this virtual primary.  This is still a
    # max-white selection inside the feasible non-collapsed set; the floor is
    # only relaxed when the geometry genuinely cannot support it.
    if any(item[3][1] >= 0.5 for item in candidates):
        candidates = [item for item in candidates if item[3][1] >= 0.5]

    # Deduplicate near-identical xy candidates, then sort by max-white goal.
    dedup: dict[tuple[int, int], tuple[np.ndarray, np.ndarray, np.ndarray, tuple[float, float, float, float]]] = {}
    for item in candidates:
        q = item[2]
        key = (int(round(float(q[0]) * 1_000_000)), int(round(float(q[1]) * 1_000_000)))
        if key not in dedup or item[3] > dedup[key][3]:
            dedup[key] = item
    return sorted(dedup.values(), key=lambda item: item[3], reverse=True)


def _barycentric_in_virtual_xy_triangle(
    target_xy: np.ndarray,
    virtual_xy: list[np.ndarray],
) -> np.ndarray | None:
    P = np.asarray(target_xy, dtype=np.float64)
    A, B, C = [np.asarray(v, dtype=np.float64) for v in virtual_xy]
    return _barycentric_2d(P, A, B, C)


def _select_wx_virtual_axis_primary_set(
    target_xy: np.ndarray,
) -> tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray]] | None:
    """Select max-white virtual primaries that actually enclose target_xy.

    The naive max-W endpoint of each axis can produce a virtual triangle that no
    longer contains the target.  When that happens, the offending vertex is
    backed away from the W-heavy endpoint just enough to restore containment.
    This keeps W as the primary objective while preventing the final virtual
    solve from falling back to NNLS/chromaticity drift.
    """
    cand_sets = [
        _wx_axis_candidates_for_subgamut(target_xy, tri)
        for tri in SUB_GAMUTS
    ]
    if any(len(c) == 0 for c in cand_sets):
        return None

    idx = [0, 0, 0]

    def _current():
        fracs = [cand_sets[i][idx[i]][0] for i in range(3)]
        xyzs = [cand_sets[i][idx[i]][1] for i in range(3)]
        xys = [cand_sets[i][idx[i]][2] for i in range(3)]
        return fracs, xyzs, xys

    best_state: tuple[float, list[int]] | None = None
    for _ in range(24):
        _fracs, _xyzs, xys = _current()
        bary = _barycentric_in_virtual_xy_triangle(target_xy, xys)
        if bary is not None:
            min_b = float(np.min(bary))
            if min_b >= -1e-7:
                return _current()
            if best_state is None or min_b > best_state[0]:
                best_state = (min_b, idx.copy())
            bad_i = int(np.argmin(bary))
        else:
            bad_i = 0

        # Move the bad vertex down its W-sorted candidate list until containment
        # improves.  This is a local bounded search, not a post-channel fix-up:
        # it changes the virtual-primary selection before the final solve.
        old_i = idx[bad_i]
        best_local = (best_state[0] if best_state is not None else -1e9, old_i)
        found = False
        for j in range(old_i + 1, len(cand_sets[bad_i])):
            trial = idx.copy()
            trial[bad_i] = j
            trial_xys = [cand_sets[i][trial[i]][2] for i in range(3)]
            b = _barycentric_in_virtual_xy_triangle(target_xy, trial_xys)
            if b is None:
                continue
            min_b = float(np.min(b))
            if min_b > best_local[0]:
                best_local = (min_b, j)
            if min_b >= -1e-7:
                idx[bad_i] = j
                found = True
                break
        if not found:
            if best_local[1] == old_i:
                break
            idx[bad_i] = best_local[1]

    if best_state is not None:
        idx = best_state[1]
        return _current()
    return _current()


def _select_wx_virtual_axis_primary_for_subgamut(
    target_xy: np.ndarray,
    tri: tuple[str, str, str],
    *,
    min_active_floor: float = 1.0 / 1024.0,
    samples: int = 513,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compatibility wrapper returning the best single-axis virtual primary."""
    candidates = _wx_axis_candidates_for_subgamut(
        target_xy, tri, min_active_floor=min_active_floor, samples=samples
    )
    if candidates:
        return candidates[0][0], candidates[0][1], candidates[0][2]
    return _max_y_virtual_primary_for_subgamut(target_xy, tri)


def _solve_virtual_primary_triangle_weights(
    target_xy: np.ndarray,
    virtual_xyz_rows: np.ndarray,
) -> np.ndarray:
    """Sub-gamut-style solve inside the virtual-primary triangle.

    This is the same mathematical operation as the normal RGB/RGW/RBW/BGW
    solve, except the columns are the three solved virtual primaries rather than
    the physical LED primaries.  The target is unit-Y at the requested/projected
    chromaticity; the caller normalizes the expanded RGBW endpoint afterwards.
    """
    C = np.asarray(virtual_xyz_rows, dtype=np.float64).reshape(3, 3).T
    target = xy_Y_to_XYZ(np.asarray(target_xy, dtype=np.float64), 1.0)
    try:
        w = np.linalg.solve(C, target)
    except np.linalg.LinAlgError:
        w, _res = _nnls_solve(C, target)
    w = np.asarray(w, dtype=np.float64)
    if np.any(w < -1e-7) or not np.isfinite(w).all():
        w, _res = _nnls_solve(C, target)
    w = np.maximum(w, 0.0)
    max_w = float(np.max(w)) if w.size else 0.0
    if max_w > 1.0:
        w = w / max_w
    return w





def _solve_wx_virtual_axis_maxbright_fraction_for_xy(target_xy: np.ndarray) -> np.ndarray:
    """Solve WX using the pre-radial virtual-axis max-brightness model.

    This is the model used by the earlier high-brightness verifier session.  It
    chooses a max-W / max-brightness virtual primary on each RGW/RBW/BGW axis,
    backs off only enough to keep the target inside the virtual triangle, solves
    the target inside that virtual triangle, then expands the result back into
    physical RGBW.
    """
    selected = _select_wx_virtual_axis_primary_set(target_xy)
    if selected is None:
        return np.zeros(4, dtype=np.float64)
    virtual_fracs, virtual_xyz, _virtual_xy = selected
    weights = _solve_virtual_primary_triangle_weights(target_xy, np.stack(virtual_xyz, axis=0))
    combined = np.zeros(4, dtype=np.float64)
    for wi, frac in zip(weights, virtual_fracs):
        combined += float(wi) * np.asarray(frac, dtype=np.float64)
    max_f = float(np.max(combined))
    if max_f > 1e-12:
        combined = combined / max_f
    return np.clip(combined, 0.0, 1.0)


def _solve_wx_virtual_axis_maxbright_fraction_from_xyz(target_xyz: np.ndarray) -> np.ndarray:
    """Projected-target-safe virtual-axis max-brightness WX endpoint."""
    target_xyz = np.asarray(target_xyz, dtype=np.float64)
    if not np.isfinite(target_xyz).all() or float(target_xyz[1]) <= 1e-12:
        return np.zeros(4, dtype=np.float64)
    projected_xyz, _projected, strict_frac = _strict_project_target_xyz_to_led_hull(target_xyz)
    target_xy = XYZ_to_xy(projected_xyz)
    frac = _solve_wx_virtual_axis_maxbright_fraction_for_xy(target_xy)
    if float(np.max(frac)) <= 1e-12 and float(np.max(strict_frac)) > 1e-12:
        return np.clip(strict_frac / float(np.max(strict_frac)), 0.0, 1.0)
    return np.clip(frac, 0.0, 1.0)


def _solve_wx_lp_legacy_fraction_from_xyz(target_xyz: np.ndarray) -> np.ndarray:
    """Projected-target-safe direct LP max-white WX endpoint."""
    return _solve_wx_combo_fraction_from_xyz(target_xyz)


def _solve_wx_endpoint_fraction_from_xyz(
    target_xyz: np.ndarray,
    *,
    wx_mode: str = DEFAULT_WX_MODE,
    wx_radial_target_position: float | None = None,
) -> np.ndarray:
    """Dispatch one full-chroma WX endpoint solve by concrete WX mode."""
    mode = _normalize_wx_mode(wx_mode)
    if mode == "wx_virtual_axis_maxbright":
        return _solve_wx_virtual_axis_maxbright_fraction_from_xyz(target_xyz)
    if mode == "wx_radial_virtual":
        pos = WX_RADIAL_TARGET_POSITION if wx_radial_target_position is None else float(wx_radial_target_position)
        return _solve_wx_radial_virtual_fraction_from_xyz(target_xyz, target_position=pos)
    if mode == "wx_lp_legacy":
        return _solve_wx_lp_legacy_fraction_from_xyz(target_xyz)
    raise ValueError(f"Unsupported wx_mode={wx_mode!r}")

def _solve_wx_balanced_fraction_for_xy(target_xy: np.ndarray) -> np.ndarray:
    """Solve the WX endpoint with W as the primary objective.

    This experimental branch is explicitly the four-channel / white-extraction
    model, so the objective must be max-W, not max-nondegeneracy.  The direct
    equality constraints are the same chromaticity constraints used elsewhere:

        X - x(X+Y+Z) = 0
        Y - y(X+Y+Z) = 0

    A tiny lower bound is applied to all four physical channels only when it is
    feasible, so interior colours do not collapse to a 3-channel answer while a
    valid four-channel max-W solution exists.  Native single-axis and native
    outer-edge duals are still locked by the caller before this function runs.

    The virtual-primary helpers above remain useful as diagnostics for the more
    nuanced construction, but this reference path is the bounded max-white solve
    that keeps chromaticity exact for the current test points.
    """
    target_xy = np.asarray(target_xy, dtype=np.float64)
    out = np.zeros(4, dtype=np.float64)
    if not np.isfinite(target_xy).all():
        return out
    try:
        from scipy.optimize import linprog as _linprog
    except Exception:
        # No scipy LP backend: fall back to the virtual-axis approximation.
        selected = _select_wx_virtual_axis_primary_set(target_xy)
        if selected is None:
            return out
        virtual_fracs, virtual_xyz, _virtual_xy = selected
        weights = _solve_virtual_primary_triangle_weights(target_xy, np.stack(virtual_xyz, axis=0))
        combined = np.zeros(4, dtype=np.float64)
        for wi, frac in zip(weights, virtual_fracs):
            combined += float(wi) * frac
        max_f = float(np.max(combined))
        return np.clip(combined / max_f, 0.0, 1.0) if max_f > 1e-12 else out

    M = np.column_stack([PRIMARY_XYZ[ch] for ch in "RGBW"])
    A_eq = _chromaticity_constraint_rows_for_columns(target_xy, M)
    b_eq = np.zeros(2, dtype=np.float64)

    # Maximize W first.  A tiny Y term provides deterministic tie-breaking
    # without letting luminance override white extraction.
    y_weights = np.array([PRIMARY_XYZ[ch][1] for ch in "RGBW"], dtype=np.float64)
    y_weights = y_weights / max(float(np.max(y_weights)), 1e-12)
    c = -1e-6 * y_weights
    c[3] += -1.0

    # Try a small four-channel floor first.  If impossible for the requested xy,
    # relax it progressively.  This is not a non-degeneracy objective; it is a
    # feasibility constraint for the explicit four-channel WX mode.
    for floor in (1.0 / 1024.0, 1.0 / 2048.0, 1.0 / 4096.0, 0.0):
        res = _linprog(
            c,
            A_eq=A_eq,
            b_eq=b_eq,
            bounds=[(floor, 1.0), (floor, 1.0), (floor, 1.0), (floor, 1.0)],
            method="highs",
        )
        if res.success and res.x is not None and np.isfinite(res.x).all():
            frac = np.maximum(np.asarray(res.x, dtype=np.float64), 0.0)
            max_f = float(np.max(frac))
            if max_f > 1e-12:
                return np.clip(frac / max_f, 0.0, 1.0)
    return out


def _solve_wx_combo_fraction_from_xyz(target_xyz: np.ndarray) -> np.ndarray:
    """Four-channel-capable WX solve with projected-target safety.

    The previous virtual-primary prototypes could degenerate when one physical
    sub-gamut already contained the target xy: that sub-gamut vertex became the
    target itself, so the final virtual solve selected it and produced an RGW /
    RBW / BGW output instead of a WX/four-channel output.

    This version fixes the actual failure mode:
      * impossible named-gamut targets are first projected through the strict
        sub-gamut model, matching verifier expectations;
      * the final WX endpoint is solved with the correct linear xy constraints;
      * the LP objective explicitly avoids interior colours collapsing back to a
        single sub-gamut vertex, while native single/dual edge locks remain in
        the caller.

    The caller applies input value scaling afterwards.
    """
    target_xyz = np.asarray(target_xyz, dtype=np.float64)
    if not np.isfinite(target_xyz).all() or float(target_xyz[1]) <= 1e-12:
        return np.zeros(4, dtype=np.float64)

    projected_xyz, _projected, strict_frac = _strict_project_target_xyz_to_led_hull(target_xyz)
    target_xy = XYZ_to_xy(projected_xyz)

    frac = _solve_wx_balanced_fraction_for_xy(target_xy)
    if float(np.max(frac)) <= 1e-12:
        # Edge/degenerate fallback: use the strict projected endpoint if the
        # balanced four-channel manifold cannot represent the requested xy.
        if float(np.max(strict_frac)) > 1e-12:
            return np.clip(strict_frac / float(np.max(strict_frac)), 0.0, 1.0)
        return _solve_subgamut_fraction_from_linear(np.array([0.0, 0.0, 0.0]), gamut="native")
    return np.clip(frac, 0.0, 1.0)

def _solve_subgamut_fraction_from_linear(
    rgb_linear: np.ndarray,
    *,
    gamut: str,
) -> np.ndarray:
    """Solve normalized linear-light RGB to RGBW fractions without quantization."""
    target_xyz = input_linear_to_XYZ(rgb_linear, gamut)
    target_xy = XYZ_to_xy(target_xyz)

    tri = _find_sub_gamut(target_xy)
    t_vec: np.ndarray | None = None
    if tri is not None:
        t_vec = _solve_xyz(tri[0], tri[1], tri[2], target_xyz)
        if t_vec is None:
            tri = None

    if tri is None:
        best_residual = np.inf
        best_tri: tuple[str, str, str] | None = None
        best_t: np.ndarray | None = None
        for g_cand in SUB_GAMUTS:
            M = np.column_stack([PRIMARY_XYZ[c] for c in g_cand])
            t_nnls, res = _nnls_solve(M, target_xyz)
            if res < best_residual:
                best_residual = res
                best_tri = g_cand
                best_t = t_nnls
        tri = best_tri
        t_vec = best_t

    out = np.zeros(4, dtype=np.float64)
    if tri is None or t_vec is None:
        return out

    max_t = float(np.max(t_vec))
    if max_t > 1.0:
        t_vec = t_vec / max_t
    for ch, tv in zip(tri, t_vec):
        out["RGBW".index(ch)] = float(tv)
    return np.clip(out, 0.0, 1.0)


def _solve_wx_fraction_from_linear(
    rgb_linear: np.ndarray,
    *,
    gamut: str,
    wx_mode: str = DEFAULT_WX_MODE,
    wx_radial_target_position: float | None = None,
) -> np.ndarray:
    """WX solve from normalized linear-light RGB with value preserved.

    The full-chroma endpoint is selected by ``wx_mode``; the source value is
    applied afterwards so all WX modes share the same value-preservation path.
    """
    rgb_linear = np.clip(np.asarray(rgb_linear, dtype=np.float64), 0.0, 1.0)
    value_scale = float(np.max(rgb_linear))
    if value_scale <= 1e-12:
        return np.zeros(4, dtype=np.float64)
    chroma_linear = rgb_linear / value_scale
    target_xyz = input_linear_to_XYZ(chroma_linear, gamut)
    return np.clip(
        _solve_wx_endpoint_fraction_from_xyz(
            target_xyz,
            wx_mode=wx_mode,
            wx_radial_target_position=wx_radial_target_position,
        ) * value_scale,
        0.0,
        1.0,
    )

def _solve_internal_boundary_primary_w(
    rgb_model: np.ndarray,
    *,
    gamut: str,
    boundary_label: str,
    input_transfer: str = "linear",
) -> np.ndarray:
    """Solve an exact internal boundary as a strict primary+W pair."""
    rgb_linear = np.clip(np.asarray(rgb_model, dtype=np.float64), 0.0, 1.0)
    target_xyz = input_linear_to_XYZ(rgb_linear, gamut)
    primary = boundary_label[0].upper()
    M = np.column_stack([PRIMARY_XYZ[primary], PRIMARY_XYZ["W"]])

    try:
        t_pair, _residuals, _rank, _s = np.linalg.lstsq(M, target_xyz, rcond=None)
    except np.linalg.LinAlgError:
        return _solve_subgamut_fraction_from_linear(rgb_linear, gamut=gamut)

    t_pair = np.maximum(np.asarray(t_pair, dtype=np.float64), 0.0)
    max_t = float(np.max(t_pair))
    if max_t > 1.0:
        t_pair = t_pair / max_t

    out = np.zeros(4, dtype=np.float64)
    out["RGBW".index(primary)] = float(t_pair[0])
    out[3] = float(t_pair[1])
    return np.clip(out, 0.0, 1.0)


def _allowed_indices_for_model_chroma(
    rgb_model: np.ndarray,
    *,
    gamut: str,
    boundary_eps: float,
    input_transfer: str = "linear",
) -> tuple[list[int], str | None]:
    """Return the legal output-channel topology for a normalized target."""
    rgb_linear = np.clip(np.asarray(rgb_model, dtype=np.float64), 0.0, 1.0)
    target_xyz = input_linear_to_XYZ(rgb_linear, gamut)
    target_xy = XYZ_to_xy(target_xyz)
    source_linear = _model_code_to_linear(rgb_linear, gamut, input_transfer=input_transfer)

    # Native input coordinates are LED-native axes; exact single/dual native
    # edges keep their edge-lock behavior.  Named gamuts are decided purely by
    # transformed target chromaticity.
    if gamut == "native":
        active_rgb = [i for i, v in enumerate(source_linear) if float(v) > 1e-9]
        if len(active_rgb) == 1:
            return active_rgb, "native_single"
        if len(active_rgb) == 2:
            return active_rgb, "native_edge_" + "".join("RGB"[i] for i in active_rgb)

    label = _internal_subgamut_boundary_label(target_xy, eps=boundary_eps)
    if label is not None:
        primary = label[0].upper()
        return ["RGBW".index(primary), 3], label

    tri = _find_sub_gamut(target_xy)
    if tri is not None:
        return ["RGBW".index(ch) for ch in tri], "".join(tri)

    # Out-of-gamut fallback: keep the same restricted topology style by choosing
    # the sub-gamut whose 3-channel NNLS residual is smallest.
    best_tri: tuple[str, str, str] | None = None
    best_residual = np.inf
    for g_cand in SUB_GAMUTS:
        M = np.column_stack([PRIMARY_XYZ[c] for c in g_cand])
        _t_nnls, res = _nnls_solve(M, target_xyz)
        if res < best_residual:
            best_residual = res
            best_tri = g_cand
    if best_tri is None:
        return [0, 1, 2], "RGB"
    return ["RGBW".index(ch) for ch in best_tri], "".join(best_tri)


def _boundary_aware_full_chroma_fraction(
    rgb_model: np.ndarray,
    *,
    method: str,
    gamut: str,
    boundary_eps: float,
    input_transfer: str = "linear",
    wx_mode: str = DEFAULT_WX_MODE,
    wx_radial_target_position: float | None = None,
) -> tuple[np.ndarray, str | None, list[int]]:
    """Return RGBW fractions, topology label, and legal channel indices.

    For the LUT/rgb16 path the sub-gamut method is topology-constrained by
    construction: once the target chromaticity selects RGW/RBW/BGW or an exact
    edge pair, the linear solve is performed *only* with those columns.

    There is intentionally no 4-channel NNLS/refinement route in this function.
    If ``method='wx'`` is requested from the LUT path, it is rejected because WX
    is an explicit four-channel extraction model and violates the sub-gamut
    topology contract.
    """
    rgb_linear = np.clip(np.asarray(rgb_model, dtype=np.float64), 0.0, 1.0)
    target_xyz = input_linear_to_XYZ(rgb_linear, gamut)

    if method == "sub_gamut":
        allowed_idx, label = _allowed_indices_for_model_chroma(
            rgb_linear,
            gamut=gamut,
            boundary_eps=boundary_eps,
            input_transfer=input_transfer,
        )
        frac = _solve_fraction_for_fixed_topology_from_xyz(target_xyz, allowed_idx)
        return frac, label, allowed_idx

    method_norm = _normalize_lut_method(method)
    effective_wx_mode = _method_to_wx_mode(method, wx_mode)

    if method_norm == "wx":
        # WX is explicitly four-channel-capable, but native single-axis and
        # native outer-edge dual-channel inputs still keep exact identity /
        # edge-lock behavior.  Internal W-primary boundaries intentionally use
        # the selected WX extraction model rather than pair-locking.
        if gamut == "native":
            active_rgb = [i for i, v in enumerate(rgb_linear) if float(v) > 1e-9]
            if len(active_rgb) in (1, 2):
                frac = _solve_fraction_for_fixed_topology_from_xyz(target_xyz, active_rgb)
                return frac, "native_edge_" + "".join("RGB"[i] for i in active_rgb), active_rgb
        frac = _solve_wx_fraction_from_linear(
            rgb_linear,
            gamut=gamut,
            wx_mode=effective_wx_mode,
            wx_radial_target_position=wx_radial_target_position,
        )
        allowed_idx = [i for i, v in enumerate(frac) if float(v) > 1e-10]
        if not allowed_idx:
            allowed_idx = [0, 1, 2, 3]
        return frac, "WX_" + effective_wx_mode, allowed_idx

    raise ValueError(f"Unsupported method={method!r}; expected 'sub_gamut', 'wx', or direct WX mode aliases.")

def solve_rgb16_for_lut(
    r16: float,
    g16: float,
    b16: float,
    *,
    method: str = "sub_gamut",
    gamut: str = "native",
    sample_scale: float = 65535.0,
    scaling_mode: str = "chroma_value",
    channel_y_model: str = "ramp",
    channel_xy_model: str = "ramp",
    boundary_eps: float = 5e-6,
    input_transfer: str = "linear",
    output_bit_depth: int = 16,
    wx_mode: str = DEFAULT_WX_MODE,
    wx_radial_target_position: float | None = None,
) -> tuple[int, int, int, int]:
    """Solve one 16-bit RGB LUT node using the existing xy_target_rgbw model.

    scaling_mode="chroma_value" is the LUT-safe path: solve full-scale
    chromaticity with the existing analytical function, then apply the node's
    16-bit value separately.  With channel_y_model="ramp", the value step uses
    the hardcoded single-channel Y ramps as a forward/inverse output-emitter
    response model instead of assuming Y is exactly proportional to drive.
    With channel_xy_model="ramp", the full-chroma topology endpoint can be
    corrected from measured xyY ramp states, but value scaling remains locked
    to measured per-channel Y contribution ratios.

    scaling_mode="direct" calls the existing analytical function directly after
    solving the full 16-bit node directly in normalized-float color space.
    This is useful for debugging one-shot xy_target-style clamping without
    introducing an internal 8-bit/model-code staging step.
    """
    rgb = np.array([float(r16), float(g16), float(b16)], dtype=np.float64)
    if not np.isfinite(rgb).all() or float(np.max(rgb)) <= 0.0:
        return 0, 0, 0, 0

    method_norm = _normalize_lut_method(method)
    effective_wx_mode = _method_to_wx_mode(method, wx_mode) if method_norm == "wx" else _normalize_wx_mode(wx_mode)

    output_bit_depth = int(output_bit_depth)
    if output_bit_depth < 1 or output_bit_depth > 16:
        raise ValueError(f"Unsupported output_bit_depth={output_bit_depth}; expected 1..16")
    output_scale = float((1 << output_bit_depth) - 1)

    if scaling_mode == "direct":
        # Direct mode solves the full node in normalized float color space and
        # then quantizes only at the final requested output bit depth.
        rgb_linear = _decode_source_rgb16_to_linear(
            rgb, gamut, sample_scale, input_transfer=input_transfer
        )
        frac, _label, allowed_idx = _boundary_aware_full_chroma_fraction(
            rgb_linear, method=method_norm, gamut=gamut,
            boundary_eps=boundary_eps, input_transfer=input_transfer,
            wx_mode=effective_wx_mode,
            wx_radial_target_position=wx_radial_target_position,
        )
        # Full-node/direct mode: solve the legal topology once, then convert
        # that topology solution to drive.  No low-drive xy re-solve is allowed.
        out_drive = _rgbw_fraction_to_drive(frac, sample_scale=sample_scale, channel_y_model=channel_y_model, allowed_idx=allowed_idx)
        out_q = np.clip(np.round(out_drive * (output_scale / max(float(sample_scale), 1e-12))), 0.0, output_scale)
        return tuple(int(x) for x in out_q)

    if scaling_mode != "chroma_value":
        raise ValueError(f"Unknown LUT scaling mode: {scaling_mode!r}")

    # Native single-axis nodes are exact LED-axis coordinates. Keep them as
    # identity endpoints regardless of the D65-normalized matrix math used for
    # mixed chromaticities.
    if gamut == "native":
        active = rgb > 0.0
        if int(np.count_nonzero(active)) == 1:
            out_single = np.zeros(4, dtype=np.float64)
            out_single[:3] = rgb
            out_q = np.clip(np.round(out_single * (output_scale / max(float(sample_scale), 1e-12))), 0.0, output_scale)
            return tuple(int(x) for x in out_q)

    rgb_model, value_scale = _rgb16_to_lut_chroma_and_value(
        rgb,
        gamut=gamut,
        sample_scale=sample_scale,
        channel_y_model=channel_y_model,
        input_transfer=input_transfer,
    )
    if value_scale <= 1e-12:
        return 0, 0, 0, 0

    # xy_target's model functions always return RGBW as 0..65535, independent
    # of the surrounding LUT sample scale.  Exact internal sub-gamut boundaries
    # are special: they sit on the primary-W segment itself, so force a tight
    # two-channel primary+W solve instead of allowing a wider 3/4-channel mix.
    full_chroma_frac, _boundary_label, _allowed_idx = _boundary_aware_full_chroma_fraction(
        rgb_model, method=method_norm, gamut=gamut, boundary_eps=boundary_eps, input_transfer=input_transfer,
        wx_mode=effective_wx_mode,
        wx_radial_target_position=wx_radial_target_position,
    )
    # Solve the full-chroma topology once.  When xy ramp mode is enabled,
    # use the measured xyY ramp only to establish that full-chroma topology
    # endpoint; lower values are generated by scaling each active channel's
    # measured Y contribution and inverting the same Y ramp.  This removes the
    # previous low-drive cross-channel re-solve that could collapse red toward
    # zero for neutral RBW values.
    full_chroma_drive = _rgbw_fraction_to_drive(
        full_chroma_frac,
        sample_scale=sample_scale,
        channel_y_model=channel_y_model,
        allowed_idx=_allowed_idx,
    )
    if channel_xy_model == "ramp" and method_norm == "sub_gamut":
        full_chroma_refined = _refine_rgbw_fraction_with_channel_xy(
            rgb_model,
            full_chroma_frac,
            value_scale=1.0,
            gamut=gamut,
            sample_scale=sample_scale,
            channel_y_model=channel_y_model,
            channel_xy_model=channel_xy_model,
            allowed_idx=_allowed_idx,
        )
        full_chroma_drive = _rgbw_fraction_to_drive(
            full_chroma_refined,
            sample_scale=sample_scale,
            channel_y_model=channel_y_model,
            allowed_idx=_allowed_idx,
        )
    out = _scale_topology_drive_by_y(
        full_chroma_drive,
        value_scale,
        _allowed_idx,
        sample_scale=sample_scale,
        channel_y_model=channel_y_model,
    )
    out_q = np.clip(np.round(out * (output_scale / max(float(sample_scale), 1e-12))), 0.0, output_scale)
    return tuple(int(x) for x in out_q)



# ── Measured single-channel Y response ramps ────────────────────────────────
#
# Derived from plan_capture_advanced_1776188116(1).csv single-emitter 16-bit
# ramps.  The raw measurements contain small local capture noise, so these Y
# arrays are an isotonic monotonic fit of the per-channel measured Y values.
# Chromaticities remain the centroid constants above; only channel Y-vs-drive
# response is used by the LUT builder when --channel-y-model=ramp.
_CHANNEL_Y_DRIVE = np.array([0, 263, 526, 789, 1052, 1315, 1579, 1842, 2105, 2368, 2631, 2895, 3158, 3421, 3684, 3947, 4211, 4474, 4737, 5000, 5263, 5527, 5790, 6053, 6316, 6579, 6843, 7106, 7369, 7632, 7895, 8158, 8422, 8685, 8948, 9211, 9474, 9738, 10001, 10264, 10527, 10790, 11054, 11317, 11580, 11843, 12106, 12370, 12633, 12896, 13159, 13422, 13686, 13949, 14212, 14475, 14738, 15001, 15265, 15528, 15791, 16054, 16317, 16581, 16844, 17107, 17370, 17633, 17897, 18160, 18423, 18686, 18949, 19213, 19476, 19739, 20002, 20265, 20529, 20792, 21055, 21318, 21581, 21845, 22108, 22371, 22634, 22897, 23160, 23424, 23687, 23950, 24213, 24476, 24740, 25003, 25266, 25529, 25792, 26056, 26319, 26582, 26845, 27108, 27372, 27635, 27898, 28161, 28424, 28688, 28951, 29214, 29477, 29740, 30003, 30267, 30530, 30793, 31056, 31319, 31583, 31846, 32109, 32372, 32635, 32899, 33162, 33425, 33688, 33951, 34215, 34478, 34741, 35004, 35267, 35531, 35794, 36057, 36320, 36583, 36846, 37110, 37373, 37636, 37899, 38162, 38426, 38689, 38952, 39215, 39478, 39742, 40005, 40268, 40531, 40794, 41058, 41321, 41584, 41847, 42110, 42374, 42637, 42900, 43163, 43426, 43690, 43953, 44216, 44479, 44742, 45005, 45269, 45532, 45795, 46058, 46321, 46585, 46848, 47111, 47374, 47637, 47901, 48164, 48427, 48690, 48953, 49217, 49480, 49743, 50006, 50269, 50533, 50796, 51059, 51322, 51585, 51848, 52112, 52375, 52638, 52901, 53164, 53428, 53691, 53954, 54217, 54480, 54744, 55007, 55270, 55533, 55796, 56060, 56323, 56586, 56849, 57112, 57376, 57639, 57902, 58165, 58428, 58691, 58955, 59218, 59481, 59744, 60007, 60271, 60534, 60797, 61060, 61323, 61587, 61850, 62113, 62376, 62639, 62903, 63166, 63429, 63692, 63955, 64219, 64482, 64745, 65008, 65271, 65535], dtype=np.float64)
_CHANNEL_Y_RESPONSE: dict[str, np.ndarray] = {
    "R": np.array([0.000000, 0.465299, 0.945131, 1.493649, 1.977446, 2.578653, 3.089845, 3.625377, 4.159147, 4.815454, 5.347448, 5.885821, 6.785631, 7.107245, 7.657777, 8.327050, 8.873092, 9.701718, 10.375673, 10.679040, 11.362120, 12.055480, 12.996916, 13.156947, 14.104330, 14.357268, 15.292710, 15.737705, 16.315323, 16.881392, 17.964968, 18.249560, 19.197070, 19.873651, 20.568478, 20.972421, 21.796445, 22.525999, 22.573005, 23.805032, 24.078456, 25.047446, 25.047446, 25.717682, 26.135029, 27.466292, 27.466292, 28.991713, 29.196283, 29.196283, 30.596076, 31.605182, 31.605182, 32.677622, 32.677622, 32.677622, 33.214953, 35.546714, 35.546714, 35.546714, 36.635767, 37.484048, 37.484048, 39.448100, 39.448100, 39.740864, 39.749702, 40.923142, 40.923142, 43.280649, 43.280649, 43.280649, 43.365245, 44.925101, 45.244754, 45.244754, 45.407223, 47.053950, 47.053950, 48.554561, 48.554561, 48.945091, 48.968358, 49.860699, 52.750559, 52.750559, 52.750559, 53.316835, 53.316835, 54.987358, 54.987358, 55.845779, 56.019967, 56.231445, 57.249936, 57.906691, 57.906691, 59.903917, 60.847564, 60.847564, 60.847564, 61.292860, 61.791541, 62.172369, 63.749196, 65.639279, 65.782666, 66.288419, 67.891746, 68.422922, 68.719450, 68.719450, 68.719450, 68.735978, 69.061686, 69.061686, 69.591236, 73.721316, 73.721316, 73.721316, 73.721316, 73.721316, 73.740671, 73.740671, 75.767965, 75.767965, 76.142450, 77.234748, 77.234748, 77.777478, 79.652785, 79.954567, 79.954567, 81.023798, 81.023798, 81.091502, 82.051808, 82.380571, 85.164846, 85.164846, 85.164846, 85.164846, 87.159516, 87.737016, 87.737016, 87.737016, 87.827303, 89.499661, 89.499661, 89.784476, 92.015221, 92.015221, 93.564641, 93.564641, 93.564641, 94.626331, 94.626331, 94.626331, 95.202032, 97.374445, 97.374445, 97.374445, 98.459326, 99.967840, 100.131284, 100.131284, 100.131284, 101.013595, 102.207910, 102.207910, 102.658166, 102.980867, 102.980867, 104.761878, 104.761878, 105.845149, 108.196163, 108.196163, 108.321414, 108.321414, 110.724947, 110.724947, 110.847540, 111.273884, 111.273884, 111.364011, 111.799762, 113.985443, 113.985443, 114.434262, 114.434262, 115.272419, 115.678221, 116.227305, 118.872527, 118.872527, 118.872527, 118.872527, 119.526904, 121.268280, 121.362518, 121.362518, 122.492507, 122.804233, 122.804233, 124.642736, 124.642736, 125.876817, 127.410894, 127.410894, 127.410894, 129.394430, 129.394430, 129.394430, 129.394430, 130.029976, 130.093013, 130.615609, 131.965541, 131.965541, 132.821616, 134.221902, 134.221902, 134.717798, 134.800128, 135.522414, 136.112780, 136.848422, 137.099999, 138.127341, 138.633965, 139.097843, 140.014067, 140.495832, 141.505793, 141.665072, 142.359962, 142.931866, 143.396415, 143.998823, 144.567772, 145.139596, 145.828501, 146.290691, 147.078085, 147.760259, 148.218731, 148.574348, 149.501645, 149.658631], dtype=np.float64),
    "G": np.array([0.000000, 2.274274, 4.682426, 6.926496, 9.179290, 11.442873, 13.701483, 15.970634, 18.220894, 20.493162, 22.757731, 25.014992, 27.280035, 29.993730, 32.175271, 34.066486, 36.692043, 39.047443, 41.325177, 43.414478, 45.804659, 48.093016, 50.342896, 52.646267, 54.897523, 57.393476, 59.443544, 61.592368, 63.911761, 66.190611, 68.473844, 70.710904, 73.076635, 75.737023, 78.011943, 79.818242, 82.686447, 84.659868, 87.501976, 89.330826, 91.652244, 94.212639, 96.030171, 98.621939, 100.381820, 102.626620, 105.015534, 108.193079, 109.121165, 111.346100, 114.004706, 115.686304, 118.502722, 120.908758, 123.399417, 124.528203, 126.745179, 128.789315, 132.853196, 133.520381, 135.722857, 139.757054, 142.143885, 142.593834, 146.932095, 149.253426, 149.253426, 153.602123, 155.909117, 157.005760, 159.601560, 162.658879, 164.724931, 166.918234, 168.214604, 170.064208, 171.953140, 176.883709, 177.548427, 180.107801, 182.092470, 183.489358, 186.403975, 187.745520, 191.504625, 192.268327, 194.781040, 199.635904, 200.213653, 204.210986, 204.210986, 206.412493, 208.919138, 210.734950, 213.658642, 215.216675, 217.652000, 219.653843, 222.299416, 225.426184, 228.547896, 231.274043, 231.274043, 233.509917, 236.052280, 238.123003, 240.772543, 243.137773, 245.963993, 247.776977, 250.880407, 252.801226, 254.918175, 256.557276, 259.018528, 259.983038, 263.183093, 265.405629, 267.198530, 269.672649, 272.650477, 273.589859, 276.641238, 278.396743, 280.660347, 283.262017, 286.761644, 288.970741, 290.046487, 293.443962, 295.344684, 297.611376, 299.708786, 300.969695, 304.354125, 305.493099, 308.830706, 312.284688, 313.008804, 314.713140, 317.955203, 320.726671, 321.611177, 323.749287, 327.887264, 330.099816, 331.818468, 333.761439, 335.705696, 337.424461, 339.591637, 344.387665, 344.387665, 349.070948, 349.070948, 353.642128, 357.977766, 358.774465, 360.319729, 360.319729, 363.110103, 365.177155, 367.693905, 369.961205, 372.826242, 376.363896, 376.712183, 378.879359, 382.866710, 383.787490, 385.955953, 388.123129, 392.009070, 393.727835, 394.525819, 398.511884, 399.882362, 401.601127, 405.936765, 406.285052, 409.024721, 411.317266, 413.708647, 416.797890, 417.246301, 419.190558, 423.648992, 424.547100, 426.490071, 428.781328, 429.927600, 432.667270, 435.408225, 437.351195, 440.540562, 441.686834, 444.078215, 447.841361, 450.131332, 451.727302, 453.097779, 455.389037, 458.129992, 460.010279, 460.010279, 468.477387, 468.477387, 468.477387, 470.810006, 473.775167, 477.188739, 479.255791, 480.726392, 484.737701, 484.837825, 488.723766, 489.072053, 492.037214, 494.204390, 495.923155, 498.315823, 501.055492, 502.998463, 505.515212, 507.334101, 509.849564, 513.163013, 515.331475, 516.826034, 521.834289, 521.958371, 524.125547, 525.844312, 528.459899, 530.404156, 533.019743, 535.884780, 537.255258, 539.994927, 541.465528, 544.206484, 546.149454, 549.238697, 551.754160, 553.124638, 554.719321, 558.481180, 562.018834, 562.940900, 563.961804], dtype=np.float64),
    "B": np.array([0.000000, 0.549655, 1.066667, 1.602463, 2.111306, 2.659707, 3.209097, 3.745881, 4.254952, 4.806256, 5.346323, 5.865368, 6.372777, 6.878726, 7.483568, 8.005433, 8.505180, 9.026662, 9.534199, 10.155801, 10.657628, 11.167558, 11.675963, 12.183636, 12.698129, 13.208309, 13.804095, 14.319556, 14.837018, 15.356807, 15.850027, 16.367300, 17.091754, 17.491022, 17.981271, 18.708174, 19.022297, 19.426048, 20.133745, 20.653451, 21.148608, 21.770332, 22.140312, 22.785551, 22.995183, 23.752525, 24.015149, 25.161309, 25.437281, 25.943407, 26.156592, 26.949546, 27.172575, 27.944208, 28.189955, 28.698883, 29.415289, 30.317532, 30.369768, 30.841666, 31.450863, 32.236920, 32.961270, 32.961270, 33.374551, 34.267813, 34.487378, 35.001427, 35.462934, 36.004467, 36.532880, 37.252607, 37.567416, 38.053604, 39.153277, 39.276265, 40.253353, 40.714396, 40.714396, 41.227356, 42.040853, 42.352955, 43.302353, 43.356407, 44.465907, 44.761248, 44.881263, 45.945595, 46.196871, 46.790370, 47.096440, 47.597011, 48.593318, 48.593318, 49.113539, 50.061809, 50.115244, 50.555566, 51.314342, 51.894499, 52.184306, 52.982987, 53.607123, 53.676443, 54.327291, 54.685431, 55.787224, 56.149501, 56.324453, 56.876284, 57.352007, 57.897408, 58.409446, 58.951117, 60.185238, 60.233251, 60.606774, 61.113255, 61.501928, 62.153139, 62.912599, 63.108575, 64.217544, 64.356043, 64.743181, 65.187134, 65.832656, 66.609342, 66.741486, 67.215387, 67.772646, 68.436049, 69.061004, 69.498138, 69.902018, 70.642545, 71.154579, 71.450511, 71.939011, 72.664911, 73.126999, 73.854849, 74.013925, 74.538807, 75.397687, 76.081984, 76.081984, 76.890789, 77.703803, 77.703803, 78.630129, 78.630129, 79.477792, 79.860840, 80.346124, 80.761683, 81.457332, 82.037263, 82.201023, 82.901041, 83.441786, 83.924522, 84.800871, 85.247836, 86.033231, 86.195671, 86.730627, 87.243112, 87.676722, 88.212073, 88.579936, 89.028121, 89.765709, 90.343212, 90.722831, 91.207186, 91.780526, 92.183235, 92.962608, 93.465506, 94.289547, 94.718434, 94.931759, 95.347328, 95.985736, 96.630338, 97.176236, 98.114592, 98.114592, 98.606389, 99.080874, 99.779656, 100.384018, 100.587899, 101.200552, 101.668112, 102.386364, 102.889209, 103.380442, 103.663467, 104.159234, 104.909725, 105.311070, 105.953112, 106.340160, 107.138771, 107.278586, 107.954467, 108.364556, 108.860192, 109.612515, 110.112327, 110.468223, 110.961921, 111.527510, 112.073179, 112.627759, 113.467131, 113.878594, 114.311303, 114.774685, 115.323001, 115.714110, 117.639047, 117.639047, 117.639047, 117.856079, 118.886630, 118.886630, 119.453238, 120.109910, 120.466846, 120.985745, 121.462273, 122.148134, 122.536596, 122.941798, 123.351654, 124.011335, 124.691177, 125.229315, 125.643730, 126.202668, 126.783167, 127.626884, 127.917567, 128.641675, 128.918712, 129.368762, 129.540105], dtype=np.float64),
    "W": np.array([0.000000, 6.214984, 12.529614, 18.767706, 25.021958, 31.241618, 38.098598, 43.902058, 50.069158, 54.949855, 61.490834, 69.483602, 74.470097, 79.356469, 87.123745, 93.790093, 99.451777, 105.440363, 112.432326, 120.099478, 123.307284, 132.753126, 136.186423, 143.728208, 148.063472, 154.276264, 161.493719, 167.707797, 173.045152, 179.810337, 186.475398, 194.795068, 199.229170, 205.444534, 211.332996, 216.770475, 221.656847, 232.080821, 237.967997, 241.977646, 252.176129, 256.184492, 262.499980, 265.156678, 273.600429, 281.368991, 287.358864, 291.241859, 297.906920, 306.903064, 310.285657, 310.285657, 321.436812, 326.097692, 338.200232, 344.314186, 351.857257, 351.857257, 358.071334, 364.510904, 369.948383, 375.060246, 385.484221, 392.150568, 403.301080, 403.301080, 407.135299, 413.474745, 419.237839, 426.004311, 433.671463, 440.337810, 444.647831, 448.984381, 458.079363, 465.298104, 468.077598, 477.851628, 485.618904, 486.846486, 494.390843, 500.379429, 503.488397, 516.918644, 526.692030, 526.692030, 530.802446, 538.344230, 547.892768, 547.892768, 553.554453, 560.771907, 571.198454, 577.763392, 579.418000, 583.853388, 592.624041, 597.284921, 603.174669, 613.498520, 622.596074, 623.272550, 628.485823, 640.363514, 640.363514, 649.134810, 652.141083, 659.459947, 667.127099, 673.218382, 677.553646, 684.444199, 690.434072, 694.645254, 704.643489, 712.526084, 712.526084, 718.077595, 728.401445, 734.842301, 741.057665, 749.377334, 750.606203, 763.875571, 763.875571, 765.593506, 773.035166, 788.899192, 794.463325, 795.565540, 809.010337, 809.010337, 809.449342, 815.439214, 826.316745, 834.486871, 834.486871, 844.535811, 851.078077, 852.080168, 861.854197, 863.183190, 872.955933, 885.613439, 891.420591, 897.104118, 897.104118, 903.156031, 920.361671, 920.361671, 925.135297, 929.144946, 940.122600, 941.902576, 942.680461, 956.791041, 957.666478, 966.663909, 972.002550, 975.437134, 985.211163, 990.198945, 990.424437, 999.423153, 1010.299397, 1016.965745, 1022.955617, 1022.958190, 1035.676451, 1035.676451, 1046.165038, 1058.044659, 1059.046750, 1062.708112, 1064.261309, 1074.913348, 1082.971372, 1082.971372, 1093.133701, 1102.682239, 1104.238009, 1112.947907, 1112.947907, 1119.452090, 1136.106623, 1136.106623, 1139.779321, 1141.884912, 1152.311459, 1155.644632, 1170.081472, 1170.081472, 1176.849872, 1189.794267, 1189.794267, 1199.732391, 1199.732391, 1205.172442, 1218.505138, 1226.663929, 1226.663929, 1232.216725, 1232.216725, 1245.049018, 1246.930404, 1257.255541, 1261.592092, 1269.363226, 1275.027483, 1275.579876, 1289.791223, 1289.791223, 1294.679524, 1305.005947, 1312.725734, 1312.725734, 1326.887019, 1326.887019, 1329.220031, 1344.207334, 1344.885096, 1347.543080, 1359.085750, 1359.085750, 1367.971721, 1376.643536, 1380.854719, 1384.187892, 1390.304419, 1395.293487, 1403.615728, 1413.503790, 1413.503790, 1419.280793, 1428.730493, 1439.884863, 1439.884863, 1442.719564, 1449.712813, 1453.599666, 1461.819212, 1469.917248, 1473.925611, 1482.924328, 1489.141621, 1489.141621, 1496.813275, 1507.465313, 1509.346699, 1511.803150], dtype=np.float64),
}



# ── Measured single-channel xy chromaticity response ramps ─────────────────
#
# Derived from plan_capture_advanced_1776188116(2).csv single-emitter 16-bit
# ramps.  Each row is [x, y] at the corresponding _CHANNEL_Y_DRIVE code.
# Drive=0 uses the first nonzero measured chromaticity because black has no
# defined chromaticity; zero-output paths still return black before this is used.
_CHANNEL_XY_RESPONSE: dict[str, np.ndarray] = {
    "R": np.array([
        [0.688662, 0.311338], [0.688662, 0.311338], [0.688052, 0.311948], [0.687738, 0.312262],
        [0.687571, 0.312429], [0.687353, 0.312647], [0.687174, 0.312826], [0.686894, 0.313106],
        [0.686661, 0.313339], [0.686483, 0.313517], [0.686370, 0.313630], [0.686224, 0.313776],
        [0.686098, 0.313902], [0.686009, 0.313991], [0.685921, 0.314079], [0.685854, 0.314146],
        [0.685747, 0.314253], [0.685637, 0.314363], [0.685623, 0.314377], [0.685496, 0.314504],
        [0.685438, 0.314562], [0.685371, 0.314629], [0.685342, 0.314658], [0.685311, 0.314689],
        [0.685260, 0.314740], [0.685211, 0.314789], [0.685198, 0.314802], [0.685154, 0.314846],
        [0.685114, 0.314886], [0.685106, 0.314894], [0.685061, 0.314939], [0.685042, 0.314958],
        [0.685018, 0.314982], [0.685002, 0.314998], [0.684982, 0.315018], [0.684958, 0.315042],
        [0.684950, 0.315050], [0.684981, 0.315019], [0.684947, 0.315053], [0.684934, 0.315066],
        [0.684918, 0.315082], [0.684916, 0.315084], [0.684897, 0.315103], [0.684900, 0.315100],
        [0.684907, 0.315093], [0.684893, 0.315107], [0.684892, 0.315108], [0.684921, 0.315079],
        [0.684905, 0.315095], [0.684908, 0.315092], [0.684961, 0.315039], [0.684925, 0.315075],
        [0.684916, 0.315084], [0.684935, 0.315065], [0.684913, 0.315087], [0.684918, 0.315082],
        [0.684921, 0.315079], [0.685007, 0.314993], [0.684909, 0.315091], [0.684916, 0.315084],
        [0.684924, 0.315076], [0.684950, 0.315050], [0.684941, 0.315059], [0.684991, 0.315009],
        [0.684964, 0.315036], [0.684978, 0.315022], [0.684966, 0.315034], [0.684979, 0.315021],
        [0.684980, 0.315020], [0.685053, 0.314947], [0.685013, 0.314987], [0.685016, 0.314984],
        [0.685017, 0.314983], [0.685039, 0.314961], [0.685066, 0.314934], [0.685056, 0.314944],
        [0.685059, 0.314941], [0.685072, 0.314928], [0.685070, 0.314930], [0.685105, 0.314895],
        [0.685093, 0.314907], [0.685119, 0.314881], [0.685113, 0.314887], [0.685130, 0.314870],
        [0.685182, 0.314818], [0.685157, 0.314843], [0.685164, 0.314836], [0.685177, 0.314823],
        [0.685180, 0.314820], [0.685236, 0.314764], [0.685206, 0.314794], [0.685215, 0.314785],
        [0.685221, 0.314779], [0.685227, 0.314773], [0.685243, 0.314757], [0.685280, 0.314720],
        [0.685263, 0.314737], [0.685305, 0.314695], [0.685308, 0.314692], [0.685303, 0.314697],
        [0.685311, 0.314689], [0.685331, 0.314669], [0.685335, 0.314665], [0.685351, 0.314649],
        [0.685367, 0.314633], [0.685399, 0.314601], [0.685409, 0.314591], [0.685414, 0.314586],
        [0.685444, 0.314556], [0.685451, 0.314549], [0.685502, 0.314498], [0.685445, 0.314555],
        [0.685443, 0.314557], [0.685468, 0.314532], [0.685471, 0.314529], [0.685486, 0.314514],
        [0.685475, 0.314525], [0.685543, 0.314457], [0.685540, 0.314460], [0.685565, 0.314435],
        [0.685539, 0.314461], [0.685541, 0.314459], [0.685549, 0.314451], [0.685552, 0.314448],
        [0.685589, 0.314411], [0.685582, 0.314418], [0.685592, 0.314408], [0.685611, 0.314389],
        [0.685610, 0.314390], [0.685622, 0.314378], [0.685643, 0.314357], [0.685660, 0.314340],
        [0.685654, 0.314346], [0.685679, 0.314321], [0.685675, 0.314325], [0.685682, 0.314318],
        [0.685697, 0.314303], [0.685708, 0.314292], [0.685756, 0.314244], [0.685754, 0.314246],
        [0.685751, 0.314249], [0.685758, 0.314242], [0.685795, 0.314205], [0.685813, 0.314187],
        [0.685800, 0.314200], [0.685804, 0.314196], [0.685816, 0.314184], [0.685847, 0.314153],
        [0.685844, 0.314156], [0.685858, 0.314142], [0.685900, 0.314100], [0.685882, 0.314118],
        [0.685929, 0.314071], [0.685932, 0.314068], [0.685925, 0.314075], [0.685989, 0.314011],
        [0.685951, 0.314049], [0.685960, 0.314040], [0.685974, 0.314026], [0.686025, 0.313975],
        [0.686004, 0.313996], [0.686016, 0.313984], [0.686040, 0.313960], [0.686081, 0.313919],
        [0.686091, 0.313909], [0.686071, 0.313929], [0.686083, 0.313917], [0.686102, 0.313898],
        [0.686127, 0.313873], [0.686120, 0.313880], [0.686134, 0.313866], [0.686161, 0.313839],
        [0.686149, 0.313851], [0.686173, 0.313827], [0.686181, 0.313819], [0.686201, 0.313799],
        [0.686240, 0.313760], [0.686230, 0.313770], [0.686256, 0.313744], [0.686243, 0.313757],
        [0.686294, 0.313706], [0.686290, 0.313710], [0.686303, 0.313697], [0.686321, 0.313679],
        [0.686312, 0.313688], [0.686325, 0.313675], [0.686333, 0.313667], [0.686360, 0.313640],
        [0.686365, 0.313635], [0.686378, 0.313622], [0.686384, 0.313616], [0.686401, 0.313599],
        [0.686409, 0.313591], [0.686422, 0.313578], [0.686472, 0.313528], [0.686451, 0.313549],
        [0.686462, 0.313538], [0.686481, 0.313519], [0.686480, 0.313520], [0.686512, 0.313488],
        [0.686525, 0.313475], [0.686521, 0.313479], [0.686545, 0.313455], [0.686558, 0.313442],
        [0.686563, 0.313437], [0.686592, 0.313408], [0.686584, 0.313416], [0.686608, 0.313392],
        [0.686650, 0.313350], [0.686634, 0.313366], [0.686642, 0.313358], [0.686675, 0.313325],
        [0.686682, 0.313318], [0.686691, 0.313309], [0.686692, 0.313308], [0.686706, 0.313294],
        [0.686716, 0.313284], [0.686727, 0.313273], [0.686745, 0.313255], [0.686749, 0.313251],
        [0.686769, 0.313231], [0.686792, 0.313208], [0.686804, 0.313196], [0.686815, 0.313185],
        [0.686812, 0.313188], [0.686825, 0.313175], [0.686842, 0.313158], [0.686857, 0.313143],
        [0.686868, 0.313132], [0.686886, 0.313114], [0.686899, 0.313101], [0.686907, 0.313093],
        [0.686925, 0.313075], [0.686943, 0.313057], [0.686958, 0.313042], [0.686967, 0.313033],
        [0.686981, 0.313019], [0.686996, 0.313004], [0.687011, 0.312989], [0.687024, 0.312976],
        [0.687036, 0.312964], [0.687052, 0.312948], [0.687061, 0.312939], [0.687082, 0.312918],
        [0.687095, 0.312905], [0.687112, 0.312888], [0.687123, 0.312877], [0.687140, 0.312860],
        [0.687154, 0.312846], [0.687168, 0.312832]
    ], dtype=np.float64),
    "G": np.array([
        [0.140818, 0.747966], [0.140818, 0.747966], [0.140129, 0.747584], [0.139583, 0.747440],
        [0.138985, 0.747617], [0.138777, 0.747527], [0.138476, 0.747695], [0.138081, 0.747906],
        [0.137710, 0.748100], [0.137721, 0.748082], [0.137612, 0.748156], [0.137405, 0.748245],
        [0.137352, 0.748255], [0.137349, 0.748263], [0.137264, 0.748182], [0.137146, 0.748363],
        [0.137224, 0.748276], [0.137266, 0.748188], [0.136961, 0.748444], [0.137037, 0.748318],
        [0.136062, 0.749202], [0.136872, 0.748377], [0.136903, 0.748423], [0.136549, 0.748760],
        [0.136333, 0.748892], [0.136868, 0.748373], [0.136713, 0.748626], [0.136832, 0.748479],
        [0.136528, 0.748652], [0.136677, 0.748599], [0.136962, 0.748305], [0.136529, 0.748621],
        [0.136574, 0.748707], [0.136487, 0.748718], [0.136447, 0.748773], [0.136820, 0.748438],
        [0.136686, 0.748678], [0.136397, 0.748788], [0.136625, 0.748665], [0.136486, 0.748745],
        [0.136666, 0.748593], [0.136339, 0.749197], [0.136833, 0.748448], [0.136847, 0.748167],
        [0.136648, 0.748452], [0.136488, 0.748698], [0.136875, 0.748332], [0.136958, 0.748377],
        [0.136552, 0.748642], [0.136501, 0.748659], [0.136837, 0.748366], [0.136972, 0.748391],
        [0.136685, 0.748434], [0.136783, 0.748462], [0.136784, 0.748436], [0.136682, 0.748506],
        [0.136840, 0.748312], [0.136923, 0.748378], [0.136776, 0.748611], [0.136975, 0.748321],
        [0.136979, 0.748258], [0.136977, 0.748280], [0.136629, 0.748572], [0.136689, 0.748624],
        [0.136978, 0.748269], [0.136669, 0.748779], [0.136589, 0.748634], [0.136786, 0.748399],
        [0.137137, 0.748110], [0.136998, 0.748587], [0.137170, 0.748147], [0.136941, 0.748296],
        [0.136869, 0.748455], [0.137014, 0.748257], [0.136922, 0.748385], [0.137056, 0.748415],
        [0.137361, 0.748036], [0.137313, 0.747993], [0.137120, 0.748131], [0.137009, 0.748355],
        [0.137010, 0.748348], [0.137034, 0.748446], [0.137302, 0.748223], [0.137025, 0.748324],
        [0.137360, 0.748058], [0.137164, 0.748273], [0.137219, 0.748176], [0.137575, 0.747795],
        [0.137106, 0.748426], [0.137341, 0.748117], [0.137452, 0.747908], [0.137451, 0.747926],
        [0.137424, 0.748144], [0.137388, 0.748197], [0.137430, 0.748021], [0.137209, 0.748388],
        [0.137497, 0.748038], [0.137461, 0.748078], [0.137304, 0.748183], [0.137675, 0.747826],
        [0.137557, 0.748181], [0.137633, 0.748026], [0.137672, 0.747904], [0.137548, 0.748043],
        [0.137807, 0.747892], [0.137390, 0.748142], [0.137497, 0.748038], [0.137582, 0.748019],
        [0.137611, 0.748116], [0.137668, 0.748371], [0.137789, 0.747861], [0.137409, 0.748454],
        [0.137544, 0.748457], [0.137724, 0.747888], [0.137664, 0.748073], [0.137632, 0.748041],
        [0.138110, 0.747599], [0.137774, 0.747903], [0.137492, 0.748149], [0.138140, 0.747618],
        [0.137891, 0.747881], [0.137718, 0.747996], [0.138248, 0.747540], [0.137859, 0.747884],
        [0.138192, 0.747621], [0.138331, 0.747517], [0.138192, 0.747627], [0.138375, 0.747695],
        [0.138243, 0.747645], [0.137998, 0.747788], [0.138212, 0.747638], [0.138134, 0.747741],
        [0.138017, 0.747816], [0.138353, 0.747524], [0.138465, 0.747535], [0.138151, 0.747839],
        [0.138608, 0.747334], [0.138553, 0.747389], [0.138466, 0.747514], [0.138607, 0.747353],
        [0.138750, 0.747158], [0.138132, 0.747794], [0.138522, 0.747432], [0.138465, 0.747519],
        [0.138847, 0.747345], [0.138795, 0.747332], [0.138628, 0.747526], [0.138600, 0.747622],
        [0.138592, 0.747292], [0.138431, 0.747446], [0.138537, 0.747484], [0.138707, 0.747333],
        [0.138576, 0.747389], [0.138555, 0.747481], [0.138782, 0.747464], [0.138630, 0.747609],
        [0.138849, 0.747281], [0.138917, 0.747408], [0.139033, 0.747049], [0.138696, 0.747425],
        [0.139129, 0.747087], [0.138798, 0.747460], [0.138734, 0.747336], [0.139251, 0.747040],
        [0.139009, 0.747146], [0.139130, 0.747097], [0.138955, 0.747321], [0.139048, 0.747356],
        [0.139419, 0.746832], [0.139193, 0.747221], [0.139300, 0.746887], [0.139388, 0.746923],
        [0.139333, 0.747093], [0.139192, 0.747226], [0.139270, 0.746980], [0.139608, 0.746838],
        [0.139632, 0.746759], [0.139492, 0.746891], [0.139675, 0.746609], [0.139513, 0.746817],
        [0.139545, 0.747011], [0.139374, 0.746949], [0.139566, 0.746937], [0.139455, 0.746987],
        [0.139670, 0.746894], [0.139658, 0.746634], [0.139556, 0.746948], [0.139994, 0.746431],
        [0.139966, 0.746511], [0.139784, 0.746787], [0.139702, 0.746758], [0.139731, 0.746942],
        [0.139775, 0.746796], [0.139748, 0.746873], [0.139995, 0.746642], [0.139914, 0.746615],
        [0.140089, 0.746605], [0.140296, 0.746207], [0.140108, 0.746788], [0.140241, 0.746361],
        [0.140260, 0.746292], [0.140088, 0.746553], [0.140127, 0.746417], [0.140295, 0.746409],
        [0.140133, 0.746659], [0.140297, 0.746356], [0.140488, 0.746275], [0.140342, 0.746217],
        [0.140131, 0.746608], [0.140263, 0.746436], [0.140580, 0.746186], [0.140322, 0.746474],
        [0.140663, 0.746155], [0.140394, 0.746218], [0.140713, 0.745965], [0.140659, 0.746109],
        [0.140524, 0.746281], [0.140649, 0.746118], [0.140712, 0.746149], [0.140596, 0.746258],
        [0.140761, 0.745965], [0.140781, 0.746128], [0.140754, 0.746197], [0.140697, 0.746113],
        [0.140889, 0.745978], [0.140820, 0.746175], [0.140809, 0.746183], [0.140882, 0.745937],
        [0.140683, 0.746079], [0.141071, 0.745758], [0.140860, 0.745956], [0.140919, 0.745987],
        [0.140808, 0.746089], [0.141035, 0.746047], [0.141021, 0.745845], [0.141245, 0.745805],
        [0.141066, 0.745885], [0.141081, 0.745829], [0.141097, 0.745981], [0.141397, 0.745699],
        [0.141423, 0.745589], [0.141396, 0.745654], [0.141302, 0.745699], [0.141237, 0.745882],
        [0.141250, 0.745828], [0.141343, 0.745739], [0.141489, 0.745681], [0.141554, 0.745658],
        [0.141409, 0.745672], [0.141528, 0.745721]
    ], dtype=np.float64),
    "B": np.array([
        [0.129182, 0.066810], [0.129182, 0.066810], [0.129120, 0.066715], [0.129390, 0.066101],
        [0.129455, 0.065968], [0.129455, 0.065920], [0.129444, 0.065911], [0.129535, 0.065726],
        [0.129556, 0.065671], [0.129571, 0.065629], [0.129583, 0.065639], [0.129614, 0.065573],
        [0.129625, 0.065519], [0.129661, 0.065470], [0.129647, 0.065470], [0.129641, 0.065434],
        [0.129677, 0.065376], [0.129664, 0.065371], [0.129655, 0.065369], [0.129709, 0.065288],
        [0.129685, 0.065322], [0.129701, 0.065282], [0.129672, 0.065307], [0.129707, 0.065267],
        [0.129699, 0.065267], [0.129703, 0.065261], [0.129715, 0.065263], [0.129729, 0.065209],
        [0.129714, 0.065239], [0.129691, 0.065247], [0.129727, 0.065228], [0.129724, 0.065220],
        [0.129705, 0.065240], [0.129714, 0.065230], [0.129732, 0.065216], [0.129699, 0.065232],
        [0.129727, 0.065218], [0.129720, 0.065228], [0.129701, 0.065245], [0.129694, 0.065242],
        [0.129732, 0.065219], [0.129709, 0.065231], [0.129741, 0.065202], [0.129714, 0.065234],
        [0.129726, 0.065225], [0.129722, 0.065280], [0.129702, 0.065250], [0.129788, 0.065227],
        [0.129684, 0.065253], [0.129700, 0.065265], [0.129695, 0.065268], [0.129691, 0.065287],
        [0.129698, 0.065269], [0.129773, 0.065233], [0.129671, 0.065301], [0.129695, 0.065284],
        [0.129671, 0.065302], [0.129674, 0.065327], [0.129681, 0.065343], [0.129678, 0.065325],
        [0.129677, 0.065320], [0.129676, 0.065327], [0.129708, 0.065315], [0.129642, 0.065369],
        [0.129682, 0.065330], [0.129665, 0.065361], [0.129627, 0.065360], [0.129640, 0.065395],
        [0.129677, 0.065376], [0.129681, 0.065364], [0.129631, 0.065426], [0.129645, 0.065418],
        [0.129615, 0.065436], [0.129651, 0.065424], [0.129611, 0.065449], [0.129604, 0.065435],
        [0.129590, 0.065449], [0.129633, 0.065447], [0.129639, 0.065443], [0.129591, 0.065488],
        [0.129592, 0.065480], [0.129621, 0.065464], [0.129569, 0.065496], [0.129600, 0.065536],
        [0.129619, 0.065496], [0.129616, 0.065520], [0.129589, 0.065541], [0.129569, 0.065544],
        [0.129593, 0.065537], [0.129578, 0.065567], [0.129601, 0.065549], [0.129549, 0.065578],
        [0.129545, 0.065573], [0.129579, 0.065586], [0.129597, 0.065590], [0.129557, 0.065592],
        [0.129551, 0.065600], [0.129557, 0.065620], [0.129590, 0.065631], [0.129569, 0.065640],
        [0.129525, 0.065671], [0.129524, 0.065691], [0.129520, 0.065707], [0.129555, 0.065671],
        [0.129505, 0.065717], [0.129543, 0.065694], [0.129434, 0.065712], [0.129512, 0.065752],
        [0.129547, 0.065712], [0.129495, 0.065749], [0.129516, 0.065768], [0.129537, 0.065744],
        [0.129463, 0.065798], [0.129514, 0.065771], [0.129523, 0.065794], [0.129464, 0.065818],
        [0.129483, 0.065797], [0.129471, 0.065827], [0.129471, 0.065827], [0.129453, 0.065844],
        [0.129496, 0.065836], [0.129469, 0.065841], [0.129402, 0.065907], [0.129433, 0.065868],
        [0.129450, 0.065915], [0.129487, 0.065893], [0.129444, 0.065916], [0.129457, 0.065918],
        [0.129417, 0.065931], [0.129448, 0.065916], [0.129440, 0.065939], [0.129407, 0.065988],
        [0.129453, 0.065954], [0.129397, 0.065994], [0.129419, 0.065986], [0.129403, 0.066017],
        [0.129381, 0.066027], [0.129374, 0.066051], [0.129413, 0.066027], [0.129407, 0.066043],
        [0.129359, 0.066089], [0.129377, 0.066078], [0.129388, 0.066046], [0.129373, 0.066094],
        [0.129316, 0.066121], [0.129389, 0.066110], [0.129344, 0.066139], [0.129352, 0.066151],
        [0.129317, 0.066147], [0.129332, 0.066170], [0.129356, 0.066171], [0.129341, 0.066174],
        [0.129349, 0.066185], [0.129310, 0.066217], [0.129303, 0.066205], [0.129307, 0.066215],
        [0.129299, 0.066267], [0.129302, 0.066223], [0.129339, 0.066227], [0.129305, 0.066261],
        [0.129294, 0.066293], [0.129288, 0.066290], [0.129323, 0.066276], [0.129322, 0.066284],
        [0.129281, 0.066343], [0.129255, 0.066297], [0.129259, 0.066353], [0.129276, 0.066359],
        [0.129273, 0.066358], [0.129257, 0.066380], [0.129236, 0.066418], [0.129236, 0.066409],
        [0.129266, 0.066411], [0.129209, 0.066418], [0.129243, 0.066430], [0.129268, 0.066411],
        [0.129221, 0.066468], [0.129237, 0.066464], [0.129196, 0.066496], [0.129184, 0.066519],
        [0.129194, 0.066513], [0.129241, 0.066475], [0.129235, 0.066501], [0.129220, 0.066514],
        [0.129213, 0.066530], [0.129202, 0.066544], [0.129204, 0.066563], [0.129165, 0.066596],
        [0.129188, 0.066576], [0.129163, 0.066613], [0.129200, 0.066580], [0.129165, 0.066624],
        [0.129137, 0.066662], [0.129158, 0.066630], [0.129127, 0.066658], [0.129117, 0.066663],
        [0.129133, 0.066686], [0.129184, 0.066658], [0.129119, 0.066710], [0.129178, 0.066666],
        [0.129176, 0.066684], [0.129139, 0.066717], [0.129164, 0.066716], [0.129115, 0.066746],
        [0.129090, 0.066774], [0.129081, 0.066799], [0.129126, 0.066777], [0.129094, 0.066794],
        [0.129088, 0.066811], [0.129128, 0.066786], [0.129079, 0.066835], [0.129016, 0.066831],
        [0.129095, 0.066832], [0.129081, 0.066866], [0.129100, 0.066853], [0.129061, 0.066886],
        [0.129027, 0.066920], [0.129066, 0.066904], [0.129073, 0.066918], [0.129024, 0.066947],
        [0.129030, 0.066949], [0.129019, 0.066976], [0.129046, 0.066946], [0.129900, 0.068302],
        [0.129032, 0.066980], [0.129051, 0.066968], [0.129040, 0.066992], [0.128989, 0.066992],
        [0.129033, 0.067008], [0.128987, 0.067057], [0.129029, 0.067034], [0.128981, 0.067083],
        [0.128995, 0.067079], [0.128984, 0.067050], [0.129011, 0.067077], [0.128958, 0.067132],
        [0.129016, 0.067091], [0.129009, 0.067104], [0.128974, 0.067138], [0.128981, 0.067143],
        [0.128948, 0.067179], [0.128938, 0.067191], [0.128933, 0.067208], [0.128967, 0.067187],
        [0.128943, 0.067214], [0.128934, 0.067232], [0.128933, 0.067240], [0.128908, 0.067267],
        [0.128916, 0.067271], [0.128941, 0.067254]
    ], dtype=np.float64),
    "W": np.array([
        [0.330471, 0.358761], [0.330471, 0.358761], [0.330544, 0.358978], [0.330372, 0.359135],
        [0.330427, 0.358780], [0.330538, 0.359111], [0.330517, 0.359150], [0.333542, 0.358586],
        [0.332098, 0.357256], [0.329918, 0.359624], [0.330437, 0.358978], [0.330411, 0.359204],
        [0.330913, 0.359035], [0.330717, 0.359009], [0.330854, 0.359133], [0.330420, 0.358897],
        [0.330463, 0.359257], [0.330692, 0.359218], [0.330647, 0.358971], [0.330330, 0.359144],
        [0.330717, 0.358938], [0.330347, 0.359198], [0.330581, 0.359044], [0.330782, 0.359084],
        [0.330556, 0.359071], [0.330700, 0.359294], [0.330569, 0.359148], [0.330614, 0.359155],
        [0.330349, 0.359193], [0.330503, 0.359196], [0.330375, 0.359251], [0.330472, 0.359126],
        [0.330637, 0.359231], [0.330601, 0.359075], [0.330472, 0.359107], [0.330497, 0.359094],
        [0.330436, 0.359083], [0.330620, 0.359020], [0.330566, 0.359187], [0.330289, 0.359198],
        [0.330528, 0.359117], [0.330323, 0.359256], [0.330495, 0.359097], [0.330602, 0.359003],
        [0.330475, 0.359094], [0.330472, 0.359013], [0.330505, 0.358890], [0.330553, 0.358967],
        [0.330472, 0.359006], [0.330376, 0.358979], [0.330423, 0.359050], [0.330418, 0.358915],
        [0.330461, 0.358918], [0.330469, 0.358899], [0.330415, 0.358930], [0.330293, 0.358965],
        [0.330304, 0.358917], [0.330377, 0.358865], [0.330365, 0.358899], [0.330346, 0.358919],
        [0.330362, 0.358914], [0.330286, 0.358923], [0.330401, 0.358889], [0.330305, 0.358837],
        [0.330336, 0.358805], [0.330382, 0.358856], [0.330297, 0.358817], [0.330159, 0.358859],
        [0.330259, 0.358844], [0.330291, 0.358774], [0.330210, 0.358826], [0.330127, 0.358780],
        [0.330321, 0.358725], [0.330219, 0.358651], [0.330301, 0.358691], [0.330231, 0.358579],
        [0.330213, 0.358699], [0.330132, 0.358629], [0.330166, 0.358657], [0.330108, 0.358686],
        [0.330115, 0.358571], [0.330167, 0.358571], [0.330134, 0.358479], [0.330149, 0.358518],
        [0.330074, 0.358529], [0.330106, 0.358449], [0.330049, 0.358451], [0.330110, 0.358470],
        [0.330018, 0.358430], [0.330122, 0.358378], [0.330081, 0.358473], [0.330052, 0.358442],
        [0.330083, 0.358314], [0.329959, 0.358358], [0.330033, 0.358302], [0.330067, 0.358288],
        [0.330050, 0.358277], [0.330058, 0.358272], [0.329994, 0.358237], [0.329989, 0.358243],
        [0.330006, 0.358173], [0.329930, 0.358196], [0.329945, 0.358140], [0.329927, 0.358171],
        [0.329953, 0.358105], [0.329915, 0.358105], [0.329858, 0.358150], [0.329890, 0.358063],
        [0.329842, 0.358105], [0.329915, 0.357998], [0.329871, 0.358002], [0.329819, 0.358036],
        [0.329839, 0.357993], [0.329871, 0.357930], [0.329819, 0.357946], [0.329815, 0.357896],
        [0.329774, 0.357874], [0.329776, 0.357849], [0.329775, 0.357859], [0.329751, 0.357834],
        [0.329748, 0.357803], [0.329780, 0.357787], [0.329724, 0.357764], [0.329666, 0.357723],
        [0.329714, 0.357761], [0.329693, 0.357689], [0.329674, 0.357722], [0.329714, 0.357645],
        [0.329624, 0.357629], [0.329672, 0.357630], [0.329585, 0.357627], [0.329597, 0.357621],
        [0.329615, 0.357601], [0.329633, 0.357568], [0.329622, 0.357503], [0.329536, 0.357521],
        [0.329571, 0.357458], [0.329535, 0.357484], [0.329558, 0.357416], [0.329544, 0.357428],
        [0.329507, 0.357404], [0.329517, 0.357373], [0.329497, 0.357387], [0.329462, 0.357310],
        [0.329333, 0.357548], [0.329470, 0.357279], [0.329424, 0.357282], [0.329436, 0.357279],
        [0.329429, 0.357192], [0.329411, 0.357219], [0.329420, 0.357210], [0.329353, 0.357222],
        [0.329400, 0.357159], [0.329376, 0.357142], [0.329364, 0.357114], [0.329289, 0.357068],
        [0.329361, 0.357098], [0.329326, 0.357074], [0.329271, 0.357059], [0.329293, 0.357012],
        [0.329263, 0.356995], [0.329292, 0.356961], [0.329276, 0.356966], [0.329229, 0.356911],
        [0.329238, 0.356897], [0.329209, 0.356890], [0.329226, 0.356869], [0.329198, 0.356805],
        [0.329205, 0.356779], [0.329118, 0.356823], [0.329162, 0.356770], [0.329148, 0.356738],
        [0.329137, 0.356749], [0.329129, 0.356681], [0.329136, 0.356693], [0.329147, 0.356647],
        [0.329103, 0.356634], [0.329093, 0.356617], [0.329075, 0.356617], [0.329064, 0.356600],
        [0.329045, 0.356553], [0.329064, 0.356521], [0.329045, 0.356500], [0.329007, 0.356494],
        [0.328978, 0.356468], [0.328989, 0.356423], [0.328939, 0.356423], [0.328950, 0.356406],
        [0.328975, 0.356362], [0.328963, 0.356361], [0.328912, 0.356332], [0.328950, 0.356298],
        [0.328932, 0.356264], [0.328873, 0.356263], [0.328895, 0.356249], [0.328869, 0.356225],
        [0.328862, 0.356215], [0.328853, 0.356177], [0.328809, 0.356174], [0.328814, 0.356158],
        [0.328792, 0.356116], [0.328799, 0.356111], [0.328772, 0.356084], [0.328741, 0.356078],
        [0.328753, 0.356033], [0.328749, 0.356029], [0.328719, 0.356012], [0.328707, 0.355963],
        [0.328695, 0.355953], [0.328699, 0.355929], [0.328689, 0.355891], [0.328680, 0.355885],
        [0.328664, 0.355861], [0.328651, 0.355834], [0.328650, 0.355811], [0.328612, 0.355818],
        [0.328603, 0.355784], [0.328594, 0.355777], [0.328581, 0.355735], [0.328577, 0.355716],
        [0.328532, 0.355704], [0.328546, 0.355669], [0.328541, 0.355626], [0.328552, 0.355622],
        [0.328523, 0.355591], [0.328478, 0.355587], [0.328499, 0.355563], [0.328490, 0.355564],
        [0.328448, 0.355541], [0.328461, 0.355499], [0.328465, 0.355458], [0.328433, 0.355443],
        [0.328437, 0.355430], [0.328420, 0.355432], [0.328371, 0.355411], [0.328384, 0.355379],
        [0.328359, 0.355358], [0.328347, 0.355321], [0.328345, 0.355299], [0.328331, 0.355257],
        [0.328321, 0.355269], [0.328327, 0.355207], [0.328298, 0.355241], [0.328272, 0.355215],
        [0.328284, 0.355170], [0.328241, 0.355187], [0.328216, 0.355136], [0.328231, 0.355115],
        [0.328241, 0.355080], [0.328197, 0.355082]
    ], dtype=np.float64),
}

def _channel_y_curve_strict(ch: str) -> np.ndarray:
    """Return a numerically strict monotonic copy of a measured Y curve."""
    y = np.asarray(_CHANNEL_Y_RESPONSE[ch.upper()], dtype=np.float64)
    # The isotonic fit intentionally creates flat runs where capture noise made
    # the local slope ambiguous. Add an imperceptibly tiny ramp so forward+inverse
    # interpolation preserves drive identity for single-channel native LUT nodes.
    return y + np.linspace(0.0, 1e-6, len(y), dtype=np.float64)


def _format_header_array(values: np.ndarray, values_per_line: int = 12) -> str:
    vals = np.asarray(values).reshape(-1)
    lines = []
    for start in range(0, vals.size, values_per_line):
        chunk = vals[start:start + values_per_line]
        lines.append("    " + ", ".join(str(int(v)) for v in chunk))
    return ",\n".join(lines)


def write_lut_header(cube: np.ndarray, output_path: _Path, *, array_prefix: str, summary: dict) -> None:
    cube = np.asarray(cube)
    flat = cube.reshape(-1, 4)
    guard = f"{output_path.stem.upper()}_H".replace("-", "_").replace(".", "_")
    lines = [
        "// Auto-generated by xy_target_rgbw_lut.py",
        "// RGBW 3D LUT built from xy_target_rgbw analytical model.",
        f"// gamut={summary.get('gamut')} method={summary.get('method')} scaling_mode={summary.get('scaling_mode')}",
        f"// grid_size={summary.get('grid_size')} sample_scale={summary.get('sample_scale')}",
        "",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <stdint.h>",
        "",
        "#ifndef PROGMEM",
        "#define PROGMEM",
        "#endif",
        "",
        f"static const uint32_t {array_prefix.upper()}_GRID_SIZE = {cube.shape[0]};",
        f"static const uint32_t {array_prefix.upper()}_ENTRY_COUNT = {flat.shape[0]};",
        "",
    ]
    for ci, ch in enumerate("RGBW"):
        c_type = "uint8_t" if cube.dtype == np.uint8 else "uint16_t"
        lines.append(f"static const {c_type} {array_prefix}_{ch}[{flat.shape[0]}] PROGMEM = {{")
        lines.append(_format_header_array(flat[:, ci]))
        lines.append("};")
        lines.append("")
    lines.extend([f"#endif  // {guard}", ""])
    output_path.write_text("\n".join(lines), encoding="utf-8")




def _available_memory_bytes() -> int | None:
    """Best-effort available-RAM query without external dependencies."""
    # Prefer psutil when present, but keep the script dependency-free.
    try:
        import psutil  # type: ignore
        return int(psutil.virtual_memory().available)
    except Exception:
        pass

    if os.name == "nt":
        try:
            class _MEMORYSTATUSEX(_ctypes.Structure):
                _fields_ = [
                    ("dwLength", _ctypes.c_ulong),
                    ("dwMemoryLoad", _ctypes.c_ulong),
                    ("ullTotalPhys", _ctypes.c_ulonglong),
                    ("ullAvailPhys", _ctypes.c_ulonglong),
                    ("ullTotalPageFile", _ctypes.c_ulonglong),
                    ("ullAvailPageFile", _ctypes.c_ulonglong),
                    ("ullTotalVirtual", _ctypes.c_ulonglong),
                    ("ullAvailVirtual", _ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", _ctypes.c_ulonglong),
                ]
            stat = _MEMORYSTATUSEX()
            stat.dwLength = _ctypes.sizeof(_MEMORYSTATUSEX)
            if _ctypes.windll.kernel32.GlobalMemoryStatusEx(_ctypes.byref(stat)):
                return int(stat.ullAvailPhys)
        except Exception:
            return None

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        avail_pages = os.sysconf("SC_AVPHYS_PAGES")
        return int(page_size * avail_pages)
    except Exception:
        return None


def _estimate_lut_build_memory(
    *,
    grid_size: int,
    workers: int,
    chunk_slices: int,
    output_dtype: np.dtype = np.dtype(np.uint16),
) -> dict[str, int]:
    """Estimate transient memory for the parallel LUT build.

    The .npy memmap itself is mostly file-backed and is not counted as fully
    resident.  The important transient cost is worker interpreter overhead plus
    result blocks existing briefly in both child and parent processes.
    """
    grid = int(grid_size)
    workers = max(1, int(workers))
    chunk_slices = max(1, int(chunk_slices))
    dtype = np.dtype(output_dtype)
    slice_bytes = grid * grid * 4 * dtype.itemsize
    chunk_bytes = slice_bytes * chunk_slices
    cube_bytes = grid * grid * grid * 4 * dtype.itemsize

    # Conservative process overhead: Python + NumPy + this script's hardcoded
    # ramp arrays.  This is intentionally pessimistic for Windows spawn mode.
    per_worker_overhead = 160 * 1024 * 1024
    # Result data can exist in child, IPC pickle buffer, and parent at once.
    per_worker_transient = per_worker_overhead + (4 * chunk_bytes)
    parent_overhead = 96 * 1024 * 1024 + (2 * chunk_bytes)
    transient_total = parent_overhead + workers * per_worker_transient
    return {
        "slice_bytes": int(slice_bytes),
        "chunk_bytes": int(chunk_bytes),
        "cube_bytes": int(cube_bytes),
        "per_worker_overhead_bytes": int(per_worker_overhead),
        "per_worker_transient_bytes": int(per_worker_transient),
        "parent_overhead_bytes": int(parent_overhead),
        "transient_total_bytes": int(transient_total),
    }


def _auto_lut_parallel_settings(
    *,
    grid_size: int,
    requested_workers: int,
    requested_chunk_slices: int,
    max_memory_mb: float,
    output_dtype: np.dtype = np.dtype(np.uint16),
) -> tuple[int, int, dict]:
    """Choose worker and chunk counts from CPU/RAM constraints."""
    cpu_count = os.cpu_count() or 1
    grid_size = int(grid_size)

    # Keep chunks small by default.  For a 256^3 uint16 RGBW cube, one R-slice
    # is only 512 KiB; 4 slices/task gives good IPC amortization without large
    # result buffers.  Smaller grids get slightly larger chunks.
    if requested_chunk_slices and requested_chunk_slices > 0:
        chunk_slices = max(1, min(grid_size, int(requested_chunk_slices)))
    else:
        if grid_size >= 192:
            chunk_slices = 4
        elif grid_size >= 96:
            chunk_slices = 8
        else:
            chunk_slices = max(1, min(grid_size, 16))

    avail_bytes = _available_memory_bytes()
    if max_memory_mb and max_memory_mb > 0:
        budget_bytes = int(float(max_memory_mb) * 1024 * 1024)
        budget_source = "--lut-max-memory-mb"
    elif avail_bytes is not None:
        # Use at most half of currently available RAM and leave room for the OS,
        # GUI, verifier, and file cache.  The memmap is file-backed, but writes
        # can still pressure the page cache.
        budget_bytes = int(avail_bytes * 0.50)
        budget_source = "50% available RAM"
    else:
        # Conservative fallback when RAM cannot be queried.
        budget_bytes = 1024 * 1024 * 1024
        budget_source = "fallback 1GiB"

    if requested_workers and requested_workers > 0:
        workers = max(1, min(grid_size, int(requested_workers)))
    else:
        # Start from CPU count, then reduce until the estimate fits.  Cap auto
        # mode at 12 workers so high-core machines do not accidentally spawn a
        # huge number of heavy Python processes; explicit --lut-workers can go
        # higher if desired and memory allows.
        workers = max(1, min(cpu_count, grid_size, 12))
        while workers > 1:
            est = _estimate_lut_build_memory(
                grid_size=grid_size, workers=workers, chunk_slices=chunk_slices,
                output_dtype=output_dtype,
            )
            if est["transient_total_bytes"] <= budget_bytes:
                break
            workers -= 1

    # If the caller explicitly requested more workers than the budget allows,
    # reduce them rather than risking a crash.  They can raise the budget with
    # --lut-max-memory-mb if they really want to push harder.
    while workers > 1:
        est = _estimate_lut_build_memory(
            grid_size=grid_size, workers=workers, chunk_slices=chunk_slices,
            output_dtype=output_dtype,
        )
        if est["transient_total_bytes"] <= budget_bytes:
            break
        workers -= 1

    est = _estimate_lut_build_memory(
        grid_size=grid_size, workers=workers, chunk_slices=chunk_slices,
        output_dtype=output_dtype,
    )
    est.update({
        "cpu_count": int(cpu_count),
        "available_memory_bytes": int(avail_bytes) if avail_bytes is not None else None,
        "memory_budget_bytes": int(budget_bytes),
        "memory_budget_source": budget_source,
        "requested_workers": int(requested_workers),
        "requested_chunk_slices": int(requested_chunk_slices),
        "selected_workers": int(workers),
        "selected_chunk_slices": int(chunk_slices),
    })
    return int(workers), int(chunk_slices), est


def _solve_lut_r_slice_range_worker(job: dict) -> tuple[int, int, np.ndarray]:
    """Worker process: solve a contiguous R-slice block and return LUT data."""
    r_start = int(job["r_start"])
    r_stop = int(job["r_stop"])
    grid_size = int(job["grid_size"])
    sample_scale = float(job["sample_scale"])
    axis = _axis_values_u16(grid_size, sample_scale)

    output_bit_depth = int(job.get("output_bit_depth", 16))
    output_dtype = np.uint8 if output_bit_depth <= 8 else np.uint16
    block = np.empty((r_stop - r_start, grid_size, grid_size, 4), dtype=output_dtype)
    for local_ri, ri in enumerate(range(r_start, r_stop)):
        r16 = axis[ri]
        for gi, g16 in enumerate(axis):
            for bi, b16 in enumerate(axis):
                block[local_ri, gi, bi, :] = solve_rgb16_for_lut(
                    r16, g16, b16,
                    method=job["method"],
                    gamut=job["gamut"],
                    sample_scale=sample_scale,
                    scaling_mode=job["scaling_mode"],
                    channel_y_model=job["channel_y_model"],
                    channel_xy_model=job["channel_xy_model"],
                    boundary_eps=float(job["boundary_eps"]),
                    input_transfer=job["input_transfer"],
                    output_bit_depth=output_bit_depth,
                    wx_mode=job.get("wx_mode", DEFAULT_WX_MODE),
                    wx_radial_target_position=job.get("wx_radial_target_position"),
                )
    return r_start, r_stop, block


def _iter_slice_ranges(grid_size: int, chunk_slices: int):
    for start in range(0, int(grid_size), int(chunk_slices)):
        yield start, min(int(grid_size), start + int(chunk_slices))

def build_rgbw_lut_cube(
    *,
    output_dir: _Path,
    grid_size: int = 256,
    method: str = "sub_gamut",
    gamut: str = "native",
    sample_scale: float = 65535.0,
    scaling_mode: str = "chroma_value",
    basename: str | None = None,
    write_header: bool = False,
    header_grid_size: int = 0,
    channel_y_model: str = "ramp",
    channel_xy_model: str = "ramp",
    boundary_eps: float = 5e-6,
    input_transfer: str = "linear",
    lut_workers: int = 0,
    lut_chunk_slices: int = 0,
    lut_max_memory_mb: float = 0.0,
    output_bit_depth: int = 16,
    wx_mode: str = DEFAULT_WX_MODE,
    wx_radial_target_position: float | None = None,
) -> dict:
    """Build an RGBW LUT cube shaped (R,G,B,4) at the requested output bit depth."""
    output_dir = _Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    method_norm = _normalize_lut_method(method)
    effective_wx_mode = _method_to_wx_mode(method, wx_mode) if method_norm == "wx" else _normalize_wx_mode(wx_mode)
    grid_size = int(grid_size)
    axis = _axis_values_u16(grid_size, sample_scale)
    output_bit_depth = int(output_bit_depth)
    if output_bit_depth < 1 or output_bit_depth > 16:
        raise ValueError(f"Unsupported output_bit_depth={output_bit_depth}; expected 1..16")
    output_dtype = np.uint8 if output_bit_depth <= 8 else np.uint16
    output_max = int((1 << output_bit_depth) - 1)
    if basename is None:
        wx_tag = f"_{effective_wx_mode}" if method_norm == "wx" else ""
        basename = f"xy_target_rgbw_{gamut}_{method_norm}{wx_tag}_{scaling_mode}_xfer{input_transfer}_Y{channel_y_model}_xy{channel_xy_model}_{grid_size}_{output_bit_depth}bit"

    npy_path = output_dir / f"{basename}.npy"
    csv_path = output_dir / f"{basename}_probes.csv"
    summary_path = output_dir / f"{basename}_summary.json"

    cube = open_memmap(npy_path, mode="w+", dtype=output_dtype, shape=(grid_size, grid_size, grid_size, 4))
    t0 = _time.time()
    progress_every = max(1, grid_size // 16)

    workers, chunk_slices, parallel_info = _auto_lut_parallel_settings(
        grid_size=grid_size,
        requested_workers=int(lut_workers),
        requested_chunk_slices=int(lut_chunk_slices),
        max_memory_mb=float(lut_max_memory_mb),
    )
    est = parallel_info
    print(
        "  LUT build memory estimate: "
        f"cube={est['cube_bytes'] / (1024**2):.1f} MiB file-backed, "
        f"transient~{est['transient_total_bytes'] / (1024**2):.1f} MiB, "
        f"budget={est['memory_budget_bytes'] / (1024**2):.1f} MiB "
        f"({est['memory_budget_source']})",
        flush=True,
    )
    print(
        f"  parallel build: workers={workers} chunk_slices={chunk_slices} "
        f"cpu_count={est['cpu_count']}",
        flush=True,
    )
    if est["transient_total_bytes"] > est["memory_budget_bytes"]:
        print(
            "  warning: estimated minimum transient memory exceeds the selected budget; "
            "using sequential build to minimize peak memory. Raise --lut-max-memory-mb "
            "only if the machine has enough free RAM.",
            flush=True,
        )

    if workers <= 1:
        for ri, r16 in enumerate(axis):
            # Keep this intentionally simple and faithful: each node calls the
            # existing xy_target solver through solve_rgb16_for_lut().
            sl = np.empty((grid_size, grid_size, 4), dtype=output_dtype)
            for gi, g16 in enumerate(axis):
                for bi, b16 in enumerate(axis):
                    sl[gi, bi, :] = solve_rgb16_for_lut(
                        r16, g16, b16,
                        method=method_norm,
                        gamut=gamut,
                        sample_scale=sample_scale,
                        scaling_mode=scaling_mode,
                        channel_y_model=channel_y_model,
                        channel_xy_model=channel_xy_model,
                        boundary_eps=boundary_eps,
                        input_transfer=input_transfer,
                        output_bit_depth=output_bit_depth,
                        wx_mode=effective_wx_mode,
                        wx_radial_target_position=wx_radial_target_position,
                    )
            cube[ri, :, :, :] = sl
            if ri == 0 or (ri + 1) % progress_every == 0 or (ri + 1) == grid_size:
                elapsed = _time.time() - t0
                print(f"  solved R slice {ri + 1}/{grid_size}  elapsed={elapsed:.1f}s", flush=True)
    else:
        ranges = list(_iter_slice_ranges(grid_size, chunk_slices))
        range_iter = iter(ranges)
        pending = {}
        completed_slices = 0
        max_in_flight = max(1, workers * 2)

        def _submit_next(pool) -> bool:
            try:
                r_start, r_stop = next(range_iter)
            except StopIteration:
                return False
            job = {
                "r_start": r_start,
                "r_stop": r_stop,
                "grid_size": grid_size,
                "sample_scale": sample_scale,
                "method": method_norm,
                "wx_mode": effective_wx_mode,
                "wx_radial_target_position": wx_radial_target_position,
                "gamut": gamut,
                "scaling_mode": scaling_mode,
                "channel_y_model": channel_y_model,
                "channel_xy_model": channel_xy_model,
                "boundary_eps": float(boundary_eps),
                "input_transfer": input_transfer,
                "output_bit_depth": output_bit_depth,
            }
            fut = pool.submit(_solve_lut_r_slice_range_worker, job)
            pending[fut] = (r_start, r_stop)
            return True

        with _ProcessPoolExecutor(max_workers=workers) as pool:
            for _ in range(min(max_in_flight, len(ranges))):
                _submit_next(pool)

            while pending:
                done, _not_done = _wait(pending.keys(), return_when=_FIRST_COMPLETED)
                for fut in done:
                    r_expected = pending.pop(fut)
                    r_start, r_stop, block = fut.result()
                    if (r_start, r_stop) != r_expected:
                        raise RuntimeError(
                            f"Worker returned unexpected slice range {(r_start, r_stop)} != {r_expected}"
                        )
                    cube[r_start:r_stop, :, :, :] = block
                    completed_slices += (r_stop - r_start)
                    del block

                    while len(pending) < max_in_flight and _submit_next(pool):
                        pass

                    if (completed_slices == grid_size or
                        completed_slices <= chunk_slices or
                        completed_slices % progress_every == 0 or
                        (completed_slices - (r_stop - r_start)) // progress_every != completed_slices // progress_every):
                        elapsed = _time.time() - t0
                        print(
                            f"  solved R slices {completed_slices}/{grid_size}  elapsed={elapsed:.1f}s",
                            flush=True,
                        )
    cube.flush()

    # Probe rows use the same 16-bit solver path and make scale bugs obvious.
    probes = [
        ("black", 0, 0, 0),
        ("red", sample_scale, 0, 0),
        ("red_half", sample_scale / 2, 0, 0),
        ("green", 0, sample_scale, 0),
        ("green_half", 0, sample_scale / 2, 0),
        ("blue", 0, 0, sample_scale),
        ("blue_half", 0, 0, sample_scale / 2),
        ("yellow", sample_scale, sample_scale, 0),
        ("yellow_half", sample_scale / 2, sample_scale / 2, 0),
        ("cyan", 0, sample_scale, sample_scale),
        ("cyan_half", 0, sample_scale / 2, sample_scale / 2),
        ("magenta", sample_scale, 0, sample_scale),
        ("magenta_half", sample_scale / 2, 0, sample_scale / 2),
        ("white", sample_scale, sample_scale, sample_scale),
        ("white_half", sample_scale / 2, sample_scale / 2, sample_scale / 2),
        ("orange", sample_scale, sample_scale * 0.532, sample_scale * 0.028),
    ]
    probe_rows = []
    for name, r16, g16, b16 in probes:
        out = solve_rgb16_for_lut(r16, g16, b16, method=method_norm, gamut=gamut,
                                  sample_scale=sample_scale, scaling_mode=scaling_mode,
                                  channel_y_model=channel_y_model,
                                  channel_xy_model=channel_xy_model,
                                  boundary_eps=boundary_eps,
                                  input_transfer=input_transfer,
                                  output_bit_depth=output_bit_depth,
                                  wx_mode=effective_wx_mode,
                                  wx_radial_target_position=wx_radial_target_position)
        probe_rows.append({
            "name": name,
            "r16": int(round(r16)), "g16": int(round(g16)), "b16": int(round(b16)),
            "out_r": out[0], "out_g": out[1], "out_b": out[2], "out_w": out[3],
        })
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(probe_rows[0].keys()))
        w.writeheader(); w.writerows(probe_rows)

    summary = {
        "script": "xy_target_rgbw_lut.py",
        "source_model": "xy_target_rgbw.py functions: rgb_to_rgbw_subgamut/rgb_to_rgbw_wx with selectable wx_mode",
        "grid_size": grid_size,
        "shape": [grid_size, grid_size, grid_size, 4],
        "dtype": str(np.dtype(output_dtype)),
        "output_bit_depth": int(output_bit_depth),
        "output_max": int(output_max),
        "axis_order": "R,G,B,RGBW",
        "axis_min": 0,
        "axis_max": int(round(sample_scale)),
        "sample_scale": float(sample_scale),
        "gamut": gamut,
        "method": method_norm,
        "wx_mode": effective_wx_mode if method_norm == "wx" else None,
        "wx_radial_target_position": wx_radial_target_position if method_norm == "wx" else None,
        "scaling_mode": scaling_mode,
        "input_transfer": input_transfer,
        "channel_y_model": channel_y_model,
        "channel_xy_model": channel_xy_model,
        "parallel": parallel_info,
        "subgamut_boundary_eps": float(boundary_eps),
        "npy_path": str(npy_path),
        "probe_csv_path": str(csv_path),
        "elapsed_seconds": float(_time.time() - t0),
        "primaries_xy": {ch: [float(v[0]), float(v[1])] for ch, v in PRIMARIES_XY.items()},
        "max_Y": {ch: float(v) for ch, v in MAX_Y.items()},
        "channel_y_response_source": "plan_capture_advanced_1776188116(2).csv single-channel 16-bit ramps, isotonic monotonic Y fit",
        "channel_xy_response_source": "plan_capture_advanced_1776188116(2).csv single-channel 16-bit ramps, raw measured xy interpolation",
        "channel_y_response_points": int(len(_CHANNEL_Y_DRIVE)),
        "channel_xy_response_points": int(len(_CHANNEL_Y_DRIVE)),
        "D65_xy": [float(D65_xy[0]), float(D65_xy[1])],
    }
    summary_path.write_text(_json.dumps(summary, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)

    del cube

    if write_header:
        h_grid = int(header_grid_size or grid_size)
        if h_grid == grid_size:
            header_cube = np.load(npy_path, mmap_mode="r")
        else:
            h_axis = _axis_values_u16(h_grid, sample_scale)
            header_cube = np.empty((h_grid, h_grid, h_grid, 4), dtype=output_dtype)
            for ri, r16 in enumerate(h_axis):
                for gi, g16 in enumerate(h_axis):
                    for bi, b16 in enumerate(h_axis):
                        header_cube[ri, gi, bi, :] = solve_rgb16_for_lut(
                            r16, g16, b16,
                            method=method,
                            gamut=gamut,
                            sample_scale=sample_scale,
                            scaling_mode=scaling_mode,
                            channel_y_model=channel_y_model,
                            channel_xy_model=channel_xy_model,
                            boundary_eps=boundary_eps,
                            input_transfer=input_transfer,
                            output_bit_depth=output_bit_depth,
                        )
        h_path = output_dir / f"{basename}_{h_grid}.h"
        h_summary = dict(summary)
        h_summary["grid_size"] = h_grid
        write_lut_header(np.asarray(header_cube), h_path, array_prefix="RGBW_LUT", summary=h_summary)
        summary["header_path"] = str(h_path)

    print(f"  wrote {npy_path}")
    print(f"  wrote {csv_path}")
    print(f"  wrote {summary_path}")
    return summary


def _parse_args_lut() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Analytical RGB->RGBW mapper via CIE xy sub-gamut solve, with optional 16-bit LUT build."
    )
    p.add_argument("--no-plot", action="store_true",
                   help="Skip the interactive CIE diagram window (PNG is still saved).")
    p.add_argument("--no-csv", action="store_true",
                   help="Skip writing the CSV file.")
    p.add_argument("--method", choices=["sub_gamut", "wx", "both", "wx_radial_virtual", "wx_virtual_axis_maxbright", "wx_lp_legacy"],
                   default="both",
                   help="Mapping family. For --build-lut, 'both' means sub_gamut; wx uses --wx-mode. Direct WX mode aliases are also accepted.")
    p.add_argument("--wx-mode", choices=VALID_WX_MODES, default=DEFAULT_WX_MODE,
                   help="Concrete WX model when --method wx is used: wx_radial_virtual, wx_virtual_axis_maxbright, or wx_lp_legacy. Default is the current radial virtual-primary model; pass wx_virtual_axis_maxbright to reproduce the pre-radial high-brightness model.")
    p.add_argument("--wx-radial-target-position", type=float, default=WX_RADIAL_TARGET_POSITION,
                   help="Radial WX target-position policy knob used only by wx_radial_virtual (default: %(default)s).")
    p.add_argument("--gamut", choices=VALID_GAMUTS, default="native",
                   help="Input colour space (default: native LED primaries, linear).")
    p.add_argument("--rgb", nargs=3, type=float, metavar=("R", "G", "B"),
                   help="Convert a single RGB triplet and exit. Existing xy_target path expects model-scale values unless --rgb16 is set.")
    p.add_argument("--rgb16", action="store_true",
                   help="Interpret --rgb values as 16-bit LUT input nodes and solve through solve_rgb16_for_lut().")
    p.add_argument("--csv", metavar="FILE",
                   help="Override CSV output path (default: rgbw_<gamut>_<method>.csv).")
    p.add_argument("--png", metavar="FILE",
                   help="Override PNG output path (default: rgbw_<gamut>.png).")
    p.add_argument("--verify", metavar="DIR",
                   help="Scan patch-capture CSVs in DIR and write a model-vs-measured report.")
    p.add_argument("--verify-output", metavar="FILE", default="verify_report.csv",
                   help="Output CSV for --verify (default: verify_report.csv).")

    p.add_argument("--build-lut", action="store_true",
                   help="Build a uint16 RGBW 3D LUT cube from this analytical model.")
    p.add_argument("--lut-grid-size", type=int, default=256,
                   help="3D LUT grid size per RGB axis (default: 256).")
    p.add_argument("--lut-output-dir", type=_Path, default=_Path("xy_target_lut_outputs"),
                   help="Output directory for LUT files (default: ./xy_target_lut_outputs).")
    p.add_argument("--lut-basename", type=str, default="",
                   help="Optional basename for generated LUT files.")
    p.add_argument("--sample-scale", type=float, default=65535.0,
                   help="16-bit LUT input/output maximum (default: 65535).")
    p.add_argument("--lut-scaling-mode", choices=["chroma_value", "direct"], default="chroma_value",
                   help="chroma_value preserves 16-bit LUT scaling; direct reproduces one-shot xy_target clamping.")
    p.add_argument("--input-transfer", choices=["linear", "gamut"], default="linear",
                   help="Input component transfer for named gamuts in LUT/rgb16 mode: linear maps LUT nodes linearly within gamut boundaries; gamut bakes the named EOTF/gamma into the LUT.")
    p.add_argument("--channel-y-model", choices=["ramp", "linear"], default="ramp",
                   help="Output-emitter Y response model for 16-bit LUT mode: ramp uses hardcoded single-channel capture ramps; linear uses max-Y only.")
    p.add_argument("--channel-xy-model", choices=["ramp", "constant"], default="ramp",
                   help="Output-emitter chromaticity model for 16-bit LUT mode: ramp uses measured per-drive single-channel xy; constant uses fixed centroids.")
    p.add_argument("--subgamut-boundary-eps", type=float, default=5e-6,
                   help="CIE xy tolerance for exact internal W-primary sub-gamut boundary detection in LUT mode (default: 5e-6).")
    p.add_argument("--lut-workers", type=int, default=0,
                   help="Worker processes for LUT build. 0 = auto from CPU/RAM; 1 = sequential (default: 0).")
    p.add_argument("--lut-chunk-slices", type=int, default=0,
                   help="R-axis slices per worker task. 0 = auto; lower values reduce transient memory (default: 0).")
    p.add_argument("--lut-max-memory-mb", type=float, default=0.0,
                   help="Maximum estimated transient memory for the LUT builder. 0 = auto budget from available RAM (default: 0).")
    p.add_argument("--lut-output-bit-depth", type=int, default=16, choices=list(range(1, 17)),
                   help="Final LUT output bit depth. Internal math remains normalized float64; default 16, use 8 for uint8 output.")
    p.add_argument("--write-header", action="store_true",
                   help="Also write a split-channel C header. Use --header-grid-size for a smaller embedded header.")
    p.add_argument("--header-grid-size", type=int, default=0,
                   help="Grid size for optional C header (0 = same as LUT grid).")
    return p.parse_args()


def main_lut() -> None:
    args = _parse_args_lut()

    print(f"\nLED primaries (absolute Y units):")
    for ch in "RGBW":
        xy = PRIMARIES_XY[ch]
        P  = PRIMARY_XYZ[ch]
        print(f"  {ch}: xy=({xy[0]:.4f},{xy[1]:.4f})  maxY={MAX_Y[ch]:.2f}"
              f"  XYZ=({P[0]:.2f},{P[1]:.2f},{P[2]:.2f})")

    if args.build_lut:
        method = _normalize_lut_method(args.method)
        effective_wx_mode = _method_to_wx_mode(args.method, args.wx_mode) if method == "wx" else args.wx_mode
        build_rgbw_lut_cube(
            output_dir=args.lut_output_dir,
            grid_size=args.lut_grid_size,
            method=method,
            wx_mode=effective_wx_mode,
            wx_radial_target_position=args.wx_radial_target_position,
            gamut=args.gamut,
            sample_scale=args.sample_scale,
            scaling_mode=args.lut_scaling_mode,
            basename=(args.lut_basename or None),
            write_header=args.write_header,
            header_grid_size=args.header_grid_size,
            channel_y_model=args.channel_y_model,
            channel_xy_model=args.channel_xy_model,
            boundary_eps=args.subgamut_boundary_eps,
            input_transfer=args.input_transfer,
            lut_workers=args.lut_workers,
            lut_chunk_slices=args.lut_chunk_slices,
            lut_max_memory_mb=args.lut_max_memory_mb,
            output_bit_depth=args.lut_output_bit_depth,
        )
        return

    if args.verify:
        verify_captures(args.verify, args.verify_output)
        return

    if args.rgb:
        r, g, b = args.rgb
        if args.rgb16:
            method = _normalize_lut_method(args.method)
            effective_wx_mode = _method_to_wx_mode(args.method, args.wx_mode) if method == "wx" else args.wx_mode
            out = solve_rgb16_for_lut(r, g, b, method=method, gamut=args.gamut,
                                      sample_scale=args.sample_scale,
                                      scaling_mode=args.lut_scaling_mode,
                                      channel_y_model=args.channel_y_model,
                                      channel_xy_model=args.channel_xy_model,
                                      boundary_eps=args.subgamut_boundary_eps,
                                      input_transfer=args.input_transfer,
                                      output_bit_depth=args.lut_output_bit_depth,
                                      wx_mode=effective_wx_mode,
                                      wx_radial_target_position=args.wx_radial_target_position)
            print(f"\n  RGB16 input ({int(round(r))},{int(round(g))},{int(round(b))})"
                  f"  [{_NAMED_GAMUTS[args.gamut][2]}]  method={method} wx_mode={effective_wx_mode if method == 'wx' else None}"
                  f"  scaling={args.lut_scaling_mode} input_transfer={args.input_transfer} channel_y_model={args.channel_y_model} channel_xy_model={args.channel_xy_model} boundary_eps={args.subgamut_boundary_eps}")
            print(f"  LUT RGBW{args.lut_output_bit_depth} -> ({out[0]},{out[1]},{out[2]},{out[3]})")
            return
        r_i, g_i, b_i = int(round(r)), int(round(g)), int(round(b))
        sg = rgb_to_rgbw_subgamut(r_i, g_i, b_i, args.gamut, input_transfer=args.input_transfer)
        wx = rgb_to_rgbw_wx(r_i, g_i, b_i, args.gamut, input_transfer=args.input_transfer,
                            wx_mode=_method_to_wx_mode(args.method, args.wx_mode),
                            wx_radial_target_position=args.wx_radial_target_position)
        gamut_desc = _NAMED_GAMUTS[args.gamut][2]
        print(f"\n  Input ({r_i},{g_i},{b_i})  [{gamut_desc}]")
        print(f"  Sub-gamut -> {sg}")
        dx, dy = verify_xy(sg)
        print(f"             xy error: dx={dx:+.5f}  dy={dy:+.5f}")
        print(f"  Whiteness -> {wx}")
        dx, dy = verify_xy(wx)
        print(f"             xy error: dx={dx:+.5f}  dy={dy:+.5f}")
        return

    run_tests(args.method, args.gamut)

    csv_method = args.method if args.method != "both" else "sub_gamut"
    hsv_samples = _generate_hsv_samples()

    if not args.no_csv:
        csv_path = args.csv if args.csv else f"rgbw_{args.gamut}_{csv_method}.csv"
        n = write_csv(hsv_samples, csv_method, args.gamut, csv_path)
        print(f"  Wrote {n} rows -> {csv_path}")

    png_path = args.png if args.png else f"rgbw_{args.gamut}.png"
    plot_cie_diagram(input_gamut=args.gamut, samples=hsv_samples,
                     save_path=png_path, show=not args.no_plot)


if __name__ == "__main__":
    main_lut()
