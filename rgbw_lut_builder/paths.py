from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

DEFAULT_CAPTURE_INPUT_DIR = PROJECT_ROOT / "patch_captures"
DEFAULT_LUT_OUTPUT_DIR = PROJECT_ROOT / "lut_outputs"
DEFAULT_ANALYSIS_OUTPUT_DIR = PROJECT_ROOT / "analysis_outputs"
DEFAULT_SOLVER_OUTPUT_DIR = PROJECT_ROOT / "solver_outputs"
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_PROFILE_DIR = DEFAULT_CONFIG_DIR / "profiles"
DEFAULT_DICTIONARY_DIR = DEFAULT_CONFIG_DIR / "dictionaries"
DEFAULT_GUI_STATE_DIR = DEFAULT_CONFIG_DIR / "gui"
DEFAULT_GUI_CONFIG_PATH = DEFAULT_GUI_STATE_DIR / "rgbw_lut_gui_config.json"