from __future__ import annotations

from .rgb_model import RGBModelConfig, solve_rgb16, solve_rgb_only
from .rgbw_model import RGBWModelConfig, solve_rgbw16, solve_rgbw_model, solve_strict_rgbw, solve_wx_rgbw
from .wx_modes import (
    DEFAULT_WX_MODE,
    VALID_WX_MODES,
    normalize_lut_method,
    normalize_wx_mode,
    resolve_wx_mode,
)

__all__ = [
    "DEFAULT_WX_MODE",
    "RGBModelConfig",
    "RGBWModelConfig",
    "VALID_WX_MODES",
    "normalize_lut_method",
    "normalize_wx_mode",
    "resolve_wx_mode",
    "solve_rgb16",
    "solve_rgb_only",
    "solve_rgbw16",
    "solve_rgbw_model",
    "solve_strict_rgbw",
    "solve_wx_rgbw",
]