"""
Interactive GUI for RGBW LUT generation, visualization, and export.

Uses tkinter for the UI framework + matplotlib for embedded charts.
Imports the existing solver and LUT-builder machinery.
"""
from __future__ import annotations

import csv
import json
import math
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.patches import Polygon

try:
    from ..paths import DEFAULT_CONFIG_DIR, DEFAULT_GUI_CONFIG_PATH
    from .prototype_measured_white_solver import (
        DEFAULT_INPUT_DIR,
        MeasuredPriorDataset,
        ReferenceWhite,
        build_target_rgb_basis,
        fit_basis_from_pure_sweeps,
        fit_basis_from_all_families,
        lab_to_lch,
        load_measured_prior_dataset,
        solve_measured_white,
        xyz_to_lab,
    )
    from .build_measured_rgbw_lut import (
        axis_values,
        build_classic_cube,
        build_classic_sample,
        build_measured_cube,
        evaluate_rgbw_sample,
        neutral_classic_blend_factor,
        trilinear_expand_cube,
        write_comparison_csv,
        write_rgbw_header,
    )
    from .build_delaunay_rgbw_lut import (
        build_delaunay_cube,
        build_family_capture_sets,
        compute_y_scale as compute_delaunay_y_scale,
        load_captures as load_delaunay_captures,
        save_lut_npy as save_delaunay_lut_npy,
        summarize_build as delaunay_summarize_build,
        write_probe_debug_csv as write_delaunay_probe_debug_csv,
        write_utilization_csv as write_delaunay_utilization_csv,
        write_verifier_failure_dictionary,
        write_verifier_feedback_bank,
        load_or_create_display_profile,
    )
except ImportError:
    from prototype_measured_white_solver import (
        DEFAULT_INPUT_DIR,
        MeasuredPriorDataset,
        ReferenceWhite,
        build_target_rgb_basis,
        fit_basis_from_pure_sweeps,
        fit_basis_from_all_families,
        lab_to_lch,
        load_measured_prior_dataset,
        solve_measured_white,
        xyz_to_lab,
    )
    from build_measured_rgbw_lut import (
        axis_values,
        build_classic_cube,
        build_classic_sample,
        build_measured_cube,
        evaluate_rgbw_sample,
        neutral_classic_blend_factor,
        trilinear_expand_cube,
        write_comparison_csv,
        write_rgbw_header,
    )
    from build_delaunay_rgbw_lut import (
        build_delaunay_cube,
        build_family_capture_sets,
        compute_y_scale as compute_delaunay_y_scale,
        load_captures as load_delaunay_captures,
        save_lut_npy as save_delaunay_lut_npy,
        summarize_build as delaunay_summarize_build,
        write_probe_debug_csv as write_delaunay_probe_debug_csv,
        write_utilization_csv as write_delaunay_utilization_csv,
        write_verifier_failure_dictionary,
        write_verifier_feedback_bank,
        load_or_create_display_profile,
    )

    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"
    DEFAULT_GUI_CONFIG_PATH = DEFAULT_CONFIG_DIR / "gui" / "rgbw_lut_gui_config.json"

# ---------------------------------------------------------------------------
# Standard colorspace primaries in CIE 1931 xy
# ---------------------------------------------------------------------------

COLORSPACES: dict[str, dict[str, tuple[float, float]]] = {
    "sRGB / Rec.709": {
        "R": (0.6400, 0.3300),
        "G": (0.3000, 0.6000),
        "B": (0.1500, 0.0600),
        "W": (0.3127, 0.3290),
    },
    "DCI-P3": {
        "R": (0.6800, 0.3200),
        "G": (0.2650, 0.6900),
        "B": (0.1500, 0.0600),
        "W": (0.3140, 0.3510),
    },
    "BT.2020": {
        "R": (0.7080, 0.2920),
        "G": (0.1700, 0.7970),
        "B": (0.1310, 0.0460),
        "W": (0.3127, 0.3290),
    },
    "Adobe RGB": {
        "R": (0.6400, 0.3300),
        "G": (0.2100, 0.7100),
        "B": (0.1500, 0.0600),
        "W": (0.3127, 0.3290),
    },
}

# CIE 1931 spectral locus (approximate boundary, sampled every ~5 nm)
CIE_LOCUS_XY = [
    (0.1741, 0.0050), (0.1740, 0.0050), (0.1738, 0.0049), (0.1736, 0.0049),
    (0.1733, 0.0048), (0.1726, 0.0048), (0.1714, 0.0051), (0.1689, 0.0069),
    (0.1644, 0.0109), (0.1566, 0.0177), (0.1440, 0.0297), (0.1241, 0.0578),
    (0.0913, 0.1327), (0.0687, 0.2007), (0.0454, 0.2950), (0.0235, 0.4127),
    (0.0082, 0.5384), (0.0039, 0.6548), (0.0139, 0.7502), (0.0389, 0.8120),
    (0.0743, 0.8338), (0.1142, 0.8262), (0.1547, 0.8059), (0.1929, 0.7816),
    (0.2296, 0.7543), (0.2658, 0.7243), (0.3016, 0.6923), (0.3373, 0.6589),
    (0.3731, 0.6245), (0.4087, 0.5896), (0.4441, 0.5547), (0.4788, 0.5202),
    (0.5125, 0.4866), (0.5448, 0.4544), (0.5752, 0.4242), (0.6029, 0.3965),
    (0.6270, 0.3725), (0.6482, 0.3514), (0.6658, 0.3340), (0.6801, 0.3197),
    (0.6915, 0.3083), (0.7006, 0.2993), (0.7079, 0.2920), (0.7140, 0.2859),
    (0.7190, 0.2809), (0.7230, 0.2770), (0.7260, 0.2740), (0.7283, 0.2717),
    (0.7300, 0.2700), (0.7311, 0.2689), (0.7320, 0.2680), (0.7327, 0.2673),
    (0.7334, 0.2666), (0.7340, 0.2660), (0.7344, 0.2656), (0.7346, 0.2654),
]

# ---------------------------------------------------------------------------
# Helper: xy → XYZ (Y=1 normalization)
# ---------------------------------------------------------------------------

def xy_to_xyz_unit(x: float, y: float) -> np.ndarray:
    """Convert CIE xy chromaticity to XYZ with Y=1."""
    if y < 1e-12:
        return np.array([0.0, 0.0, 0.0])
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def xyz_to_xy(xyz: np.ndarray) -> tuple[float, float]:
    s = float(np.sum(xyz))
    if abs(s) < 1e-12:
        return (float("nan"), float("nan"))
    return (float(xyz[0] / s), float(xyz[1] / s))


def point_in_triangle(px: float, py: float, verts: list[tuple[float, float]]) -> bool:
    """Barycentric test — is (px, py) inside the triangle defined by verts?"""
    (x1, y1), (x2, y2), (x3, y3) = verts
    d = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    if abs(d) < 1e-15:
        return False
    a = ((y2 - y3) * (px - x3) + (x3 - x2) * (py - y3)) / d
    b = ((y3 - y1) * (px - x3) + (x1 - x3) * (py - y3)) / d
    c = 1.0 - a - b
    return a >= -1e-9 and b >= -1e-9 and c >= -1e-9


# ---------------------------------------------------------------------------
# Solver settings dataclass (mirrors argparse.Namespace but typed)
# ---------------------------------------------------------------------------

@dataclass
class SolverSettings:
    input_dir: Path = DEFAULT_INPUT_DIR
    output_dir: Path = Path(r".\lut_outputs")
    white_x: float = 0.3309
    white_y: float = 0.3590
    white_Y: float = 100.0
    max_delta_e: float = 4.0
    max_hue_shift: float = 4.0
    ignore_hue_below_chroma: float = 8.0
    target_white_balance_mode: str = "reference-white"
    neutral_classic_chroma: float = 8.0
    neutral_classic_fade_width: float = 10.0
    measured_prior_mode: str = "family"
    measured_prior_neighbors: int = 0
    measured_family_count: int = 0
    measured_prior_strength: float = 0.35
    nondegenerate_regularization: float = 0.01
    max_luminance_ratio: float = 2.0
    sample_scale: float = 65535.0
    coarse_grid_size: int = 17
    full_grid_size: int = 256
    header_name: str = "measured_rgbw_lut"
    header_grid_size: int = 0
    true16_lut_size: int = 0
    gamut_clamp_colorspace: str = "none"
    skip_full_lut: bool = False
    skip_header: bool = False
    emit_classic_header: bool = False
    channel_mode: str = "RGBW"
    workers: int = 0  # 0 = use all logical CPUs
    build_mode: str = "LP Solver"  # "LP Solver" or "Delaunay"
    hull_fallback_k: int = 8
    verifier_diagnostics_dir: str = ""
    skip_failure_dictionary: bool = False
    config_dir: Path = DEFAULT_CONFIG_DIR
    display_profile: str = "default_display"
    display_id: str = ""
    feedback_mode: str = "diagnostic"
    feedback_bank: str = "auto"
    feedback_trust_pass_dE: float = 2.5
    # Measured-anchor candidate auto-sizing.  These are passed through to the
    # Delaunay builder via argparse.Namespace; no GUI controls are required for
    # the normal auto mode.
    knn_max_candidate_axis: int = 160
    knn_min_candidate_axis: int = 32
    knn_memory_fraction: float = 0.50
    knn_bytes_per_row_candidate: float = 112.0
    delta_e_tiebreak: float = 2.0   # full Lab \u0394E tiebreak budget for W preference (Mode-2)
    chroma_gate: float = 15.0       # CIELAB C* above which W reward \u2192 0 (Mode-2)


# ---------------------------------------------------------------------------
# Persistent config
# ---------------------------------------------------------------------------

CONFIG_PATH = DEFAULT_GUI_CONFIG_PATH


def _settings_to_dict(s: SolverSettings) -> dict:
    """Serialize SolverSettings to a JSON-safe dict."""
    d = vars(s).copy()
    for k, v in d.items():
        if isinstance(v, Path):
            d[k] = str(v)
    return d


def _dict_to_settings(d: dict) -> SolverSettings:
    """Deserialize a dict back into SolverSettings."""
    s = SolverSettings()
    for k, v in d.items():
        if not hasattr(s, k):
            continue
        current = getattr(s, k)
        if isinstance(current, Path):
            setattr(s, k, Path(v))
        elif isinstance(current, bool):
            setattr(s, k, bool(v))
        elif isinstance(current, int):
            setattr(s, k, int(v))
        elif isinstance(current, float):
            setattr(s, k, float(v))
        else:
            setattr(s, k, v)
    return s


def _load_config() -> SolverSettings:
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                return _dict_to_settings(json.load(f))
        except Exception:
            pass
    return SolverSettings()


def _save_config(s: SolverSettings) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(_settings_to_dict(s), f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main GUI Application
# ---------------------------------------------------------------------------

class RGBWLutApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("RGBW Measured LUT Builder")
        self.geometry("1400x900")
        self.minsize(1100, 700)

        self.settings = _load_config()

        # Loaded data (populated on load)
        self.basis: dict[str, np.ndarray] | None = None
        self.measured_prior: MeasuredPriorDataset | None = None
        self.rgb_basis: np.ndarray | None = None
        self.target_rgb_basis: np.ndarray | None = None
        self.target_rgb_basis_info: dict | None = None
        self.white_basis: np.ndarray | None = None
        self.reference_white: ReferenceWhite | None = None
        self.family_bases: dict = {}
        self.wb_scales: np.ndarray = np.ones(3, dtype=np.float64)

        # Build results
        self.classic_cube: np.ndarray | None = None
        self.measured_cube: np.ndarray | None = None
        self.comparison_rows: list[dict[str, float]] | None = None

        # Guard against concurrent builds
        self._build_active: bool = False
        self._build_btn: ttk.Button | None = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Top-level paned: left panel (controls) + right panel (notebook)
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # --- Left: scrollable control panel ---
        ctrl_frame = ttk.Frame(paned, width=340)
        paned.add(ctrl_frame, weight=0)

        canvas = tk.Canvas(ctrl_frame, width=320, highlightthickness=0)
        scrollbar = ttk.Scrollbar(ctrl_frame, orient=tk.VERTICAL, command=canvas.yview)
        self._ctrl_inner = ttk.Frame(canvas)
        self._ctrl_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._ctrl_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Mousewheel scrolling
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta / 120), "units"))

        self._build_controls(self._ctrl_inner)

        # --- Right: tabbed notebook for views ---
        self.notebook = ttk.Notebook(paned)
        paned.add(self.notebook, weight=1)

        # Tab: CIE Chart
        self._cie_frame = ttk.Frame(self.notebook)
        self.notebook.add(self._cie_frame, text="CIE 1931 Chart")
        self._build_cie_tab(self._cie_frame)

        # Tab: White Gain Histogram
        self._hist_frame = ttk.Frame(self.notebook)
        self.notebook.add(self._hist_frame, text="White Gain")

        # Tab: White Slices
        self._slice_frame = ttk.Frame(self.notebook)
        self.notebook.add(self._slice_frame, text="White Slices")

        # Tab: Comparison Table
        self._table_frame = ttk.Frame(self.notebook)
        self.notebook.add(self._table_frame, text="Comparison")

        # Tab: 3D LUT Viewer
        self._lut3d_frame = ttk.Frame(self.notebook)
        self.notebook.add(self._lut3d_frame, text="3D LUT")

        # Status bar + progress bar (progress sits just above status)
        self._progress_var = tk.IntVar(value=0)
        self._progress_bar = ttk.Progressbar(
            self, variable=self._progress_var, maximum=100, mode="determinate"
        )
        self._progress_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=2, pady=(0, 1))
        self._progress_bar.pack_forget()  # hidden until a build starts

        self._status_var = tk.StringVar(value="Ready. Load data to begin.")
        ttk.Label(self, textvariable=self._status_var, relief=tk.SUNKEN, anchor=tk.W).pack(
            fill=tk.X, side=tk.BOTTOM, padx=2, pady=2
        )

    def _build_controls(self, parent: ttk.Frame) -> None:
        row = 0

        def section(label: str) -> int:
            nonlocal row
            ttk.Separator(parent, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 2))
            row += 1
            ttk.Label(parent, text=label, font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky="w", padx=4)
            row += 1
            return row

        def entry_row(label: str, var: tk.Variable, width: int = 12) -> int:
            nonlocal row
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(8, 2), pady=1)
            ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, columnspan=2, sticky="ew", padx=2, pady=1)
            row += 1
            return row

        def combo_row(label: str, var: tk.StringVar, values: list[str], width: int = 18) -> int:
            nonlocal row
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(8, 2), pady=1)
            ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width).grid(
                row=row, column=1, columnspan=2, sticky="ew", padx=2, pady=1
            )
            row += 1
            return row

        # --- Data paths ---
        section("Data Paths")
        self._input_dir_var = tk.StringVar(value=str(self.settings.input_dir))
        ttk.Label(parent, text="Input dir").grid(row=row, column=0, sticky="w", padx=(8, 2))
        ttk.Entry(parent, textvariable=self._input_dir_var, width=24).grid(row=row, column=1, sticky="ew", padx=2)
        ttk.Button(parent, text="...", width=3, command=self._browse_input_dir).grid(row=row, column=2, padx=2)
        row += 1

        self._output_dir_var = tk.StringVar(value=str(self.settings.output_dir))
        ttk.Label(parent, text="Output dir").grid(row=row, column=0, sticky="w", padx=(8, 2))
        ttk.Entry(parent, textvariable=self._output_dir_var, width=24).grid(row=row, column=1, sticky="ew", padx=2)
        ttk.Button(parent, text="...", width=3, command=self._browse_output_dir).grid(row=row, column=2, padx=2)
        row += 1

        self._verifier_diag_dir_var = tk.StringVar(value=str(getattr(self.settings, "verifier_diagnostics_dir", "") or ""))
        ttk.Label(parent, text="Verifier CSV dir").grid(row=row, column=0, sticky="w", padx=(8, 2))
        ttk.Entry(parent, textvariable=self._verifier_diag_dir_var, width=24).grid(row=row, column=1, sticky="ew", padx=2)
        ttk.Button(parent, text="...", width=3, command=self._browse_verifier_diag_dir).grid(row=row, column=2, padx=2)
        row += 1

        # --- Display feedback / profile ---
        section("Display Feedback")
        self._config_dir_var = tk.StringVar(value=str(getattr(self.settings, "config_dir", DEFAULT_CONFIG_DIR)))
        ttk.Label(parent, text="Config dir").grid(row=row, column=0, sticky="w", padx=(8, 2))
        ttk.Entry(parent, textvariable=self._config_dir_var, width=24).grid(row=row, column=1, sticky="ew", padx=2)
        ttk.Button(parent, text="...", width=3, command=self._browse_config_dir).grid(row=row, column=2, padx=2)
        row += 1

        self._display_profile_var = tk.StringVar(value=str(getattr(self.settings, "display_profile", "default_display") or "default_display"))
        entry_row("Display profile", self._display_profile_var, width=20)

        self._display_id_var = tk.StringVar(value=str(getattr(self.settings, "display_id", "") or ""))
        entry_row("Display id override", self._display_id_var, width=20)

        self._feedback_mode_var = tk.StringVar(value=str(getattr(self.settings, "feedback_mode", "diagnostic") or "diagnostic"))
        combo_row("Feedback mode", self._feedback_mode_var, ["off", "diagnostic", "candidate", "penalty", "reevaluate"], width=18)

        self._feedback_bank_var = tk.StringVar(value=str(getattr(self.settings, "feedback_bank", "auto") or "auto"))
        entry_row("Feedback bank", self._feedback_bank_var, width=20)

        self._feedback_pass_de_var = tk.StringVar(value=str(getattr(self.settings, "feedback_trust_pass_dE", 2.5)))
        entry_row("Pass/fail dE", self._feedback_pass_de_var)

        # --- Reference white ---
        section("Reference White")
        self._white_x_var = tk.StringVar(value=str(self.settings.white_x))
        self._white_y_var = tk.StringVar(value=str(self.settings.white_y))
        self._white_Y_var = tk.StringVar(value=str(self.settings.white_Y))
        entry_row("White x", self._white_x_var)
        entry_row("White y", self._white_y_var)
        entry_row("White Y", self._white_Y_var)

        # --- Color constraints ---
        section("Color Constraints")
        self._max_de_var = tk.StringVar(value=str(self.settings.max_delta_e))
        self._max_hue_var = tk.StringVar(value=str(self.settings.max_hue_shift))
        self._ignore_hue_chroma_var = tk.StringVar(value=str(self.settings.ignore_hue_below_chroma))
        entry_row("Max Delta E", self._max_de_var)
        entry_row("Max Hue Shift", self._max_hue_var)
        entry_row("Ignore hue < chroma", self._ignore_hue_chroma_var)

        # --- White balance ---
        section("White Balance")
        self._wb_mode_var = tk.StringVar(value=self.settings.target_white_balance_mode)
        combo_row("Target WB mode", self._wb_mode_var, ["raw", "reference-white"])

        # --- Neutral region ---
        section("Neutral Region")
        self._neutral_chroma_var = tk.StringVar(value=str(self.settings.neutral_classic_chroma))
        self._neutral_fade_var = tk.StringVar(value=str(self.settings.neutral_classic_fade_width))
        entry_row("Classic chroma", self._neutral_chroma_var)
        entry_row("Fade width", self._neutral_fade_var)

        # --- Measured prior ---
        section("Measured Prior")
        self._prior_mode_var = tk.StringVar(value=self.settings.measured_prior_mode)
        self._prior_neighbors_var = tk.StringVar(value=str(self.settings.measured_prior_neighbors))
        self._prior_family_count_var = tk.StringVar(value=str(self.settings.measured_family_count))
        self._prior_strength_var = tk.StringVar(value=str(self.settings.measured_prior_strength))
        self._regularization_var = tk.StringVar(value=str(self.settings.nondegenerate_regularization))
        self._max_luminance_ratio_var = tk.StringVar(value=str(self.settings.max_luminance_ratio))
        combo_row("Prior mode", self._prior_mode_var, ["row", "family"])
        entry_row("Prior neighbors", self._prior_neighbors_var)
        entry_row("Family count", self._prior_family_count_var)
        entry_row("Prior strength", self._prior_strength_var)
        entry_row("Regularization", self._regularization_var)
        entry_row("Max lum. ratio", self._max_luminance_ratio_var)

        # --- Grid ---
        section("Grid & Export")
        self._coarse_grid_var = tk.StringVar(value=str(self.settings.coarse_grid_size))
        self._full_grid_var = tk.StringVar(value=str(self.settings.full_grid_size))
        self._header_grid_var = tk.StringVar(value=str(self.settings.header_grid_size))
        self._header_name_var = tk.StringVar(value=self.settings.header_name)
        self._true16_lut_size_var = tk.StringVar(value=str(self.settings.true16_lut_size))
        entry_row("Coarse grid", self._coarse_grid_var)
        entry_row("Full grid", self._full_grid_var)
        entry_row("Header grid", self._header_grid_var)
        entry_row("Header name", self._header_name_var)
        entry_row("True16 LUT size", self._true16_lut_size_var)

        self._workers_var = tk.StringVar(value=str(self.settings.workers))
        entry_row("Workers (0=auto)", self._workers_var)

        # --- Channel mode ---
        section("Output Mode")
        self._channel_mode_var = tk.StringVar(value=self.settings.channel_mode)
        combo_row("Channel mode", self._channel_mode_var, ["RGBW", "RGB"])

        # --- Build mode ---
        section("Build Mode")
        self._build_mode_var = tk.StringVar(value=self.settings.build_mode)
        combo_row("Solver mode", self._build_mode_var, ["LP Solver", "Delaunay"])

        # --- Delaunay settings ---
        section("Delaunay Settings")
        self._hull_fallback_k_var = tk.StringVar(value=str(self.settings.hull_fallback_k))
        entry_row("Hull fallback K", self._hull_fallback_k_var)

        # --- Gamut clamping ---
        section("Gamut Clamping")
        self._gamut_clamp_var = tk.StringVar(value="none")
        clamp_choices = ["none"] + list(COLORSPACES.keys())
        combo_row("Clamp to colorspace", self._gamut_clamp_var, clamp_choices)

        # --- Checkboxes ---
        section("Options")
        self._skip_full_var = tk.BooleanVar(value=False)
        self._skip_header_var = tk.BooleanVar(value=False)
        self._emit_classic_var = tk.BooleanVar(value=False)
        self._skip_failure_dict_var = tk.BooleanVar(value=getattr(self.settings, "skip_failure_dictionary", False))
        ttk.Checkbutton(parent, text="Skip full LUT", variable=self._skip_full_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8
        )
        row += 1
        ttk.Checkbutton(parent, text="Skip header", variable=self._skip_header_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8
        )
        row += 1
        ttk.Checkbutton(parent, text="Emit classic header", variable=self._emit_classic_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8
        )
        row += 1
        ttk.Checkbutton(parent, text="Skip failure dictionary", variable=self._skip_failure_dict_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8
        )
        row += 1

        # --- Action buttons ---
        section("Actions")
        ttk.Button(parent, text="Load Data", command=self._load_data).grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=2
        )
        row += 1
        ttk.Button(parent, text="Load Output", command=self._load_output).grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=2
        )
        row += 1
        self._build_btn = ttk.Button(parent, text="Build LUT", command=self._build_lut_async)
        self._build_btn.grid(row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=2)
        row += 1
        ttk.Button(parent, text="Fix LUT (force black at 0,0,0)", command=self._fix_lut_black).grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=2
        )
        row += 1
        ttk.Button(parent, text="Export Header", command=self._export_header).grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=2
        )
        row += 1
        ttk.Button(parent, text="Export True16 Cal Header", command=self._export_1d_header).grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=2
        )
        row += 1
        ttk.Button(parent, text="Export Binary Cube", command=self._export_binary_cube).grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=2
        )
        row += 1
        ttk.Button(parent, text="Export HyperHDR JSON", command=self._export_hyperhdr_json).grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=2
        )
        row += 1

        parent.columnconfigure(1, weight=1)

    # ------------------------------------------------------------------
    # CIE 1931 chart tab
    # ------------------------------------------------------------------

    def _build_cie_tab(self, parent: ttk.Frame) -> None:
        self._cie_fig = Figure(figsize=(7, 6), dpi=100)
        self._cie_ax = self._cie_fig.add_subplot(111)
        self._cie_canvas = FigureCanvasTkAgg(self._cie_fig, master=parent)
        toolbar = NavigationToolbar2Tk(self._cie_canvas, parent)
        toolbar.update()
        self._cie_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Overlay checkboxes
        overlay_frame = ttk.Frame(parent)
        overlay_frame.pack(fill=tk.X, padx=4, pady=2)
        self._cie_show_709 = tk.BooleanVar(value=True)
        self._cie_show_p3 = tk.BooleanVar(value=False)
        self._cie_show_2020 = tk.BooleanVar(value=True)
        self._cie_show_led = tk.BooleanVar(value=True)
        self._cie_show_captures = tk.BooleanVar(value=True)
        self._cie_show_solved = tk.BooleanVar(value=True)
        for text, var in [
            ("Rec.709", self._cie_show_709),
            ("DCI-P3", self._cie_show_p3),
            ("BT.2020", self._cie_show_2020),
            ("LED gamut", self._cie_show_led),
            ("Captures", self._cie_show_captures),
            ("Solved pts", self._cie_show_solved),
        ]:
            ttk.Checkbutton(overlay_frame, text=text, variable=var, command=self._update_cie_chart).pack(side=tk.LEFT, padx=4)

        self._draw_cie_base()

    def _draw_cie_base(self) -> None:
        """Draw the static CIE 1931 background."""
        ax = self._cie_ax
        ax.clear()
        ax.set_xlim(0.0, 0.8)
        ax.set_ylim(0.0, 0.9)
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        ax.set_title("CIE 1931 xy")
        ax.set_aspect("equal")
        ax.grid(True, alpha=0.15)

        # Spectral locus
        locus_x = [p[0] for p in CIE_LOCUS_XY]
        locus_y = [p[1] for p in CIE_LOCUS_XY]
        ax.plot(locus_x + [locus_x[0]], locus_y + [locus_y[0]], color="black", linewidth=0.8, alpha=0.5)

        self._cie_canvas.draw()

    def _update_cie_chart(self) -> None:
        ax = self._cie_ax
        ax.clear()
        self._draw_cie_base()

        def _draw_gamut(name: str, color: str, linestyle: str = "--") -> None:
            cs = COLORSPACES[name]
            verts = [cs["R"], cs["G"], cs["B"], cs["R"]]
            xs = [v[0] for v in verts]
            ys = [v[1] for v in verts]
            ax.plot(xs, ys, color=color, linestyle=linestyle, linewidth=1.2, label=name)

        if self._cie_show_709.get():
            _draw_gamut("sRGB / Rec.709", "#d44", "--")
        if self._cie_show_p3.get():
            _draw_gamut("DCI-P3", "#c84", "-.")
        if self._cie_show_2020.get():
            _draw_gamut("BT.2020", "#48c", "--")

        # LED gamut from measured basis
        if self._cie_show_led.get() and self.basis is not None:
            led_xy = {}
            for ch in ("r16", "g16", "b16"):
                led_xy[ch] = xyz_to_xy(self.basis[ch])
            verts = [led_xy["r16"], led_xy["g16"], led_xy["b16"], led_xy["r16"]]
            ax.plot([v[0] for v in verts], [v[1] for v in verts], "k-", linewidth=1.8, label="LED gamut")
            # White channel
            w_xy = xyz_to_xy(self.basis["w16"])
            ax.plot(w_xy[0], w_xy[1], "D", color="gray", markersize=7, label=f"W {w_xy[0]:.3f}, {w_xy[1]:.3f}")

        # Capture data sparse points
        if self._cie_show_captures.get() and self.measured_prior is not None:
            ref = self.reference_white
            if ref is not None:
                # Show a sparse subsample of capture data in xy
                self._draw_capture_points(ax)

        # Solved LUT sample points (before/after)
        if self._cie_show_solved.get() and self.comparison_rows is not None and self.basis is not None:
            self._draw_solved_points(ax)

        ax.legend(fontsize=7, loc="upper right")
        self._cie_canvas.draw()

    def _draw_capture_points(self, ax) -> None:
        """Plot sparse measured capture chromaticities from the prior dataset."""
        if self.measured_prior is None or self.rgb_basis is None or self.white_basis is None:
            return
        # We don't have raw xy stored in MeasuredPriorDataset, but we can reconstruct
        # from Lab → XYZ (approximate — sufficient for visualization)
        ref = self.reference_white
        prior = self.measured_prior
        # Subsample to max 500 points for speed
        n = prior.lab.shape[0]
        step = max(1, n // 500)
        for i in range(0, n, step):
            lab = prior.lab[i]
            xyz = _lab_to_xyz(lab, ref)
            x, y = xyz_to_xy(xyz)
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            ax.plot(x, y, ".", color="#888888", markersize=2, alpha=0.3)

    def _draw_solved_points(self, ax) -> None:
        """Draw before (classic) and after (measured) xy for a subsample of solved grid points."""
        if self.comparison_rows is None or self.basis is None:
            return
        rgb_basis = self.rgb_basis
        white_basis = self.white_basis
        target_rgb_basis = self.target_rgb_basis
        if rgb_basis is None or white_basis is None or target_rgb_basis is None:
            return

        n = len(self.comparison_rows)
        step = max(1, n // 200)  # Show at most ~200 points
        classic_xs, classic_ys = [], []
        solved_xs, solved_ys = [], []
        for i in range(0, n, step):
            row = self.comparison_rows[i]
            rgb_target = np.array([row["target_r"], row["target_g"], row["target_b"]])
            # Classic: just target RGB through target basis
            target_xyz = target_rgb_basis @ rgb_target
            cx, cy = xyz_to_xy(target_xyz)
            if math.isfinite(cx) and math.isfinite(cy):
                classic_xs.append(cx)
                classic_ys.append(cy)

            # Solved: RGBW through actual measured basis
            classic_rgbw = build_classic_sample(rgb_target)
            nb = neutral_classic_blend_factor(row.get("target_C", 0.0), self._settings_as_namespace())
            proposed_r = row["target_r"] - row.get("proposed_w", 0.0) if "proposed_w" in row else classic_rgbw[0]
            # Use the actual measured cube values
            idx = (int(row.get("r_index", 0)), int(row.get("g_index", 0)), int(row.get("b_index", 0)))
            if self.measured_cube is not None and all(0 <= j < self.measured_cube.shape[k] for k, j in enumerate(idx)):
                rgbw = self.measured_cube[idx[0], idx[1], idx[2]]
                solved_xyz = rgb_basis @ rgbw[:3].astype(float) + white_basis * float(rgbw[3])
                sx, sy = xyz_to_xy(solved_xyz)
                if math.isfinite(sx) and math.isfinite(sy):
                    solved_xs.append(sx)
                    solved_ys.append(sy)

        if classic_xs:
            ax.scatter(classic_xs, classic_ys, s=4, c="#cc4444", alpha=0.35, zorder=3, label="Target (classic)")
        if solved_xs:
            ax.scatter(solved_xs, solved_ys, s=4, c="#4488cc", alpha=0.35, zorder=3, label="Solved RGBW")

    # ------------------------------------------------------------------
    # Settings sync
    # ------------------------------------------------------------------

    def _sync_settings(self) -> None:
        """Read GUI fields into self.settings."""
        s = self.settings
        s.input_dir = Path(self._input_dir_var.get())
        s.output_dir = Path(self._output_dir_var.get())
        s.verifier_diagnostics_dir = self._verifier_diag_dir_var.get().strip()
        s.config_dir = Path(self._config_dir_var.get())
        s.display_profile = self._display_profile_var.get().strip() or "default_display"
        s.display_id = self._display_id_var.get().strip()
        s.feedback_mode = self._feedback_mode_var.get()
        s.feedback_bank = self._feedback_bank_var.get().strip() or "auto"
        s.feedback_trust_pass_dE = float(self._feedback_pass_de_var.get())
        s.white_x = float(self._white_x_var.get())
        s.white_y = float(self._white_y_var.get())
        s.white_Y = float(self._white_Y_var.get())
        s.max_delta_e = float(self._max_de_var.get())
        s.max_hue_shift = float(self._max_hue_var.get())
        s.ignore_hue_below_chroma = float(self._ignore_hue_chroma_var.get())
        s.target_white_balance_mode = self._wb_mode_var.get()
        s.neutral_classic_chroma = float(self._neutral_chroma_var.get())
        s.neutral_classic_fade_width = float(self._neutral_fade_var.get())
        s.measured_prior_mode = self._prior_mode_var.get()
        s.measured_prior_neighbors = int(self._prior_neighbors_var.get())
        s.measured_family_count = int(self._prior_family_count_var.get())
        s.measured_prior_strength = float(self._prior_strength_var.get())
        s.nondegenerate_regularization = float(self._regularization_var.get())
        s.max_luminance_ratio = float(self._max_luminance_ratio_var.get())
        s.sample_scale = 65535.0
        s.coarse_grid_size = int(self._coarse_grid_var.get())
        s.full_grid_size = int(self._full_grid_var.get())
        s.header_grid_size = int(self._header_grid_var.get())
        s.header_name = self._header_name_var.get()
        s.true16_lut_size = int(self._true16_lut_size_var.get())
        s.gamut_clamp_colorspace = self._gamut_clamp_var.get()
        s.skip_full_lut = self._skip_full_var.get()
        s.skip_header = self._skip_header_var.get()
        s.emit_classic_header = self._emit_classic_var.get()
        s.skip_failure_dictionary = self._skip_failure_dict_var.get()
        s.channel_mode = self._channel_mode_var.get()
        s.workers = int(self._workers_var.get())
        s.build_mode = self._build_mode_var.get()
        s.hull_fallback_k = int(self._hull_fallback_k_var.get())

    def _settings_as_namespace(self) -> Any:
        """Return settings as an argparse.Namespace-like object for existing functions."""
        self._sync_settings()
        import argparse
        ns = argparse.Namespace(**vars(self.settings))
        if not getattr(ns, "verifier_diagnostics_dir", ""):
            ns.verifier_diagnostics_dir = None
        else:
            ns.verifier_diagnostics_dir = Path(ns.verifier_diagnostics_dir)
        ns.config_dir = Path(getattr(ns, "config_dir", DEFAULT_CONFIG_DIR))
        if not getattr(ns, "feedback_bank", ""):
            ns.feedback_bank = "auto"
        return ns

    # ------------------------------------------------------------------
    # File dialogs
    # ------------------------------------------------------------------

    def _browse_input_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self._input_dir_var.get(), title="Select input captures directory")
        if d:
            self._input_dir_var.set(d)

    def _browse_output_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self._output_dir_var.get(), title="Select output directory")
        if d:
            self._output_dir_var.set(d)

    def _browse_verifier_diag_dir(self) -> None:
        initial = self._verifier_diag_dir_var.get() or self._output_dir_var.get()
        d = filedialog.askdirectory(initialdir=initial, title="Select verifier/target-match CSV directory")
        if d:
            self._verifier_diag_dir_var.set(d)

    def _browse_config_dir(self) -> None:
        initial = self._config_dir_var.get() or str(DEFAULT_CONFIG_DIR)
        d = filedialog.askdirectory(initialdir=initial, title="Select config directory")
        if d:
            self._config_dir_var.set(d)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_data(self) -> None:
        self._sync_settings()
        self._status("Loading capture data...")
        try:
            self.reference_white = ReferenceWhite(self.settings.white_x, self.settings.white_y, self.settings.white_Y)
            self.basis = fit_basis_from_pure_sweeps(self.settings.input_dir)
            self.measured_prior = load_measured_prior_dataset(self.settings.input_dir, self.reference_white)
            self.rgb_basis = np.column_stack([self.basis["r16"], self.basis["g16"], self.basis["b16"]])
            self.white_basis = self.basis["w16"]
            self.target_rgb_basis, self.target_rgb_basis_info = build_target_rgb_basis(
                self.rgb_basis, self.reference_white, self.settings.target_white_balance_mode,
                white_basis=self.white_basis,
            )
            self.wb_scales = np.array(
                self.target_rgb_basis_info.get("channel_scales", [1.0, 1.0, 1.0]), dtype=np.float64
            )
            self.family_bases = fit_basis_from_all_families(self.settings.input_dir)
            equal_rgb_xy = xyz_to_xy(self.basis["r16"] + self.basis["g16"] + self.basis["b16"])
            w_xy = xyz_to_xy(self.white_basis)
            n_priors = self.measured_prior.lab.shape[0] if self.measured_prior else 0
            self._status(
                f"Loaded: {n_priors} prior rows, equal-RGB xy=({equal_rgb_xy[0]:.4f},{equal_rgb_xy[1]:.4f}), "
                f"W xy=({w_xy[0]:.4f},{w_xy[1]:.4f})"
            )
            self._update_cie_chart()
        except Exception as exc:
            self._status(f"Load error: {exc}")
            messagebox.showerror("Load Error", str(exc))

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        self._sync_settings()
        _save_config(self.settings)
        self.destroy()

    def _save_config_now(self) -> None:
        """Save current GUI state to config file."""
        self._sync_settings()
        _save_config(self.settings)

    # ------------------------------------------------------------------
    # Restore settings from lut_summary.json
    # ------------------------------------------------------------------

    def _apply_summary_settings(self, saved: dict) -> None:
        """Push settings from a lut_summary.json 'settings' block into GUI variables."""
        mapping = {
            "max_delta_e": self._max_de_var,
            "max_hue_shift": self._max_hue_var,
            "ignore_hue_below_chroma": self._ignore_hue_chroma_var,
            "target_white_balance_mode": self._wb_mode_var,
            "neutral_classic_chroma": self._neutral_chroma_var,
            "neutral_classic_fade_width": self._neutral_fade_var,
            "measured_prior_mode": self._prior_mode_var,
            "measured_prior_neighbors": self._prior_neighbors_var,
            "measured_family_count": self._prior_family_count_var,
            "measured_prior_strength": self._prior_strength_var,
            "nondegenerate_regularization": self._regularization_var,
            "coarse_grid_size": self._coarse_grid_var,
            "full_grid_size": self._full_grid_var,
            "white_x": self._white_x_var,
            "white_y": self._white_y_var,
            "white_Y": self._white_Y_var,
            "gamut_clamp_colorspace": self._gamut_clamp_var,
            "true16_lut_size": self._true16_lut_size_var,
            "channel_mode": self._channel_mode_var,
            "input_dir": self._input_dir_var,
            "build_mode": self._build_mode_var,
            "hull_fallback_k": self._hull_fallback_k_var,
        }
        for key, var in mapping.items():
            if key in saved:
                var.set(str(saved[key]))

    # ------------------------------------------------------------------
    # Load previously generated output
    # ------------------------------------------------------------------

    def _load_output(self) -> None:
        """Reload cubes and comparison CSV from the output directory."""
        self._sync_settings()
        out = self.settings.output_dir

        # Load the summary that matches the selected build mode.  Delaunay runs
        # intentionally write delaunay_lut_summary.json and delaunay_rgbw_coarse_*.npy;
        # the older generic lut_summary/measured_rgbw names belong to the LP/measured
        # builder.  Prefer the mode-specific file first so pressing “Load LUT” while
        # Delaunay is selected does not silently restore stale measured-mode settings.
        prefer_delaunay = str(self.settings.build_mode).lower() == "delaunay"
        summary_candidates = (
            [out / "delaunay_lut_summary.json", out / "lut_summary.json"]
            if prefer_delaunay else
            [out / "lut_summary.json", out / "delaunay_lut_summary.json"]
        )
        summary_path = next((p for p in summary_candidates if p.exists()), summary_candidates[0])
        if summary_path.exists():
            try:
                with summary_path.open("r", encoding="utf-8") as f:
                    summary = json.load(f)
                saved = summary.get("settings", {})
                if saved:
                    self._apply_summary_settings(saved)
                    self._sync_settings()  # re-read vars after update
                    prefer_delaunay = str(self.settings.build_mode).lower() == "delaunay"
            except Exception as exc:
                self._status(f"Warning: could not read summary: {exc}")

        gs = self.settings.coarse_grid_size
        cube_prefix = "delaunay_rgbw_coarse" if prefer_delaunay else "measured_rgbw_coarse"
        measured_path = out / f"{cube_prefix}_{gs}.npy"
        classic_path = out / f"classic_rgbw_coarse_{gs}.npy"
        csv_path = out / "coarse_lut_comparison.csv"

        if not measured_path.exists():
            # Try to find a cube matching the active mode first, then fall back to
            # either cube type so old output folders remain loadable.  Prefer mtime
            # over lexicographic order because 17/33/256 grid names do not sort by age.
            candidates = sorted(out.glob(f"{cube_prefix}_*.npy"), key=lambda p: p.stat().st_mtime)
            if not candidates:
                candidates = sorted(
                    list(out.glob("delaunay_rgbw_coarse_*.npy")) + list(out.glob("measured_rgbw_coarse_*.npy")),
                    key=lambda p: p.stat().st_mtime,
                )
            if candidates:
                measured_path = candidates[-1]
                found_gs = measured_path.stem.split("_")[-1]
                try:
                    gs = int(found_gs)
                    self._coarse_grid_var.set(str(gs))
                    self._sync_settings()
                    classic_path = out / f"classic_rgbw_coarse_{gs}.npy"
                except ValueError:
                    pass
            if not measured_path.exists():
                mode_label = "Delaunay" if prefer_delaunay else "measured"
                messagebox.showwarning("Not Found", f"No {mode_label} cube found in:\n{out}")
                return

        try:
            self.measured_cube = np.load(measured_path).astype(np.float32)
            self._status(f"Loaded measured cube: {measured_path.name}")

            if classic_path.exists():
                self.classic_cube = np.load(classic_path).astype(np.float32)

            if csv_path.exists():
                with csv_path.open("r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    rows: list[dict[str, Any]] = []
                    for raw_row in reader:
                        typed: dict[str, Any] = {}
                        for k, v in raw_row.items():
                            try:
                                typed[k] = float(v)
                            except (ValueError, TypeError):
                                typed[k] = v
                        rows.append(typed)
                    self.comparison_rows = rows

            # Also load basis data if not yet loaded
            if self.basis is None:
                self._load_data()

            self._on_build_complete()
            self._save_config_now()
        except Exception as exc:
            self._status(f"Load output error: {exc}")
            messagebox.showerror("Load Error", str(exc))

    # ------------------------------------------------------------------
    # LUT building (threaded)
    # ------------------------------------------------------------------

    def _build_lut_async(self) -> None:
        if self._build_active:
            messagebox.showwarning("Build in Progress", "A build is already running. Wait for it to finish.")
            return
        if self.basis is None:
            messagebox.showwarning("No Data", "Load data first.")
            return
        self._build_active = True
        if self._build_btn is not None:
            self._build_btn.config(state=tk.DISABLED)
        self._sync_settings()
        self._status("Building coarse LUT... (this may take a while)")

        # Show and reset progress bar.
        self._progress_var.set(0)
        self._progress_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=2, pady=(0, 1))

        def _on_progress(completed: int, total: int) -> None:
            """Called from the worker thread; schedules a main-thread update."""
            pct = int(100 * completed / total)
            self.after(0, lambda p=pct, c=completed, t=total: (
                self._progress_var.set(p),
                self._status_var.set(f"Building coarse LUT\u2026  {c}/{t} R-slices  ({p}%)")
            ))

        def _worker() -> None:
            try:
                args = self._settings_as_namespace()
                out = self.settings.output_dir
                out.mkdir(parents=True, exist_ok=True)
                gs = self.settings.coarse_grid_size
                coarse_axis = axis_values(gs, self.settings.sample_scale)

                if self.settings.build_mode == "Delaunay":
                    # ---- Mode 2: family-hull ΔE solver ----
                    xyz_points, rgbw_points, raw_count, meta = load_delaunay_captures(
                        self.settings.input_dir
                    )
                    family_capture_sets = build_family_capture_sets(
                        xyz_points, rgbw_points
                    )
                    y_scale = compute_delaunay_y_scale(
                        xyz_points, self.target_rgb_basis, self.settings.sample_scale,
                        reference_white=self.reference_white, rgbw_points=rgbw_points,
                    )
                    measured, rows, used_anchors = build_delaunay_cube(
                        coarse_axis, xyz_points, rgbw_points,
                        self.target_rgb_basis, self.reference_white, args,
                        build_comparison=True,
                        progress_callback=_on_progress,
                        family_bases=self.family_bases,
                        family_capture_sets=family_capture_sets,
                        y_scale=y_scale,
                        raw_rgb_basis=self.rgb_basis,
                        wb_scales=self.wb_scales,
                    )
                    classic = build_classic_cube(coarse_axis)

                    self.classic_cube = classic
                    self.measured_cube = measured
                    self.comparison_rows = rows

                    save_delaunay_lut_npy(measured, out / f"delaunay_rgbw_coarse_{gs}.npy")
                    np.save(
                        out / f"classic_rgbw_coarse_{gs}.npy",
                        np.clip(np.round(classic), 0, 65535).astype(np.uint16),
                    )
                    write_comparison_csv(rows, out / "coarse_lut_comparison.csv")
                    write_delaunay_utilization_csv(
                        meta, used_anchors, out / "delaunay_utilization.csv"
                    )
                    equal_rgb_xy = xyz_to_xy(
                        self.basis["r16"] + self.basis["g16"] + self.basis["b16"]
                    )
                    summary = delaunay_summarize_build(
                        rows, xyz_points, rgbw_points, used_anchors, raw_count, args,
                        self.target_rgb_basis,
                        xyz_to_xy(self.white_basis),
                        equal_rgb_xy,
                        y_scale=y_scale,
                    )
                    with (out / "delaunay_lut_summary.json").open("w", encoding="utf-8") as fh:
                        json.dump(summary, fh, indent=2)

                    # Compact named-patch diagnostic CSV.  This is intentionally
                    # tiny compared with coarse_lut_comparison.csv and is the
                    # fastest way to debug neutral/dual-channel/skin-tone failures.
                    write_delaunay_probe_debug_csv(
                        out / "delaunay_probe_debug.csv",
                        xyz_points,
                        rgbw_points,
                        self.target_rgb_basis,
                        self.rgb_basis,
                        self.white_basis,
                        self.reference_white,
                        args,
                        self.family_bases,
                        family_capture_sets,
                        y_scale,
                    )

                    if getattr(args, "verifier_diagnostics_dir", None) is not None and not getattr(args, "skip_failure_dictionary", False):
                        display_profile, _profile_path = load_or_create_display_profile(args, self.reference_white)
                        write_verifier_failure_dictionary(
                            Path(args.verifier_diagnostics_dir),
                            out,
                            dE_threshold=float(getattr(args, "feedback_trust_pass_dE", 2.5)),
                        )
                        if str(getattr(args, "feedback_mode", "diagnostic")) != "off":
                            write_verifier_feedback_bank(
                                Path(args.verifier_diagnostics_dir),
                                out,
                                args,
                                display_profile,
                                dE_threshold=float(getattr(args, "feedback_trust_pass_dE", 2.5)),
                            )

                else:
                    # ---- Mode 1: LP Solver (original) ----
                    classic = build_classic_cube(coarse_axis)
                    measured, rows = build_measured_cube(
                        coarse_axis,
                        self.rgb_basis,
                        self.target_rgb_basis,
                        self.white_basis,
                        self.reference_white,
                        self.measured_prior,
                        args,
                        progress_callback=_on_progress,
                        w_dominant_y_per_unit=float(
                            self.target_rgb_basis_info.get("w_dominant_y_per_unit", 0.0)
                        ),
                    )

                    if self.settings.gamut_clamp_colorspace != "none":
                        measured = self._apply_gamut_clamp(measured, coarse_axis)

                    self.classic_cube = classic
                    self.measured_cube = measured
                    self.comparison_rows = rows

                    np.save(
                        out / f"classic_rgbw_coarse_{gs}.npy",
                        np.clip(np.round(classic), 0, 65535).astype(np.uint16),
                    )
                    np.save(
                        out / f"measured_rgbw_coarse_{gs}.npy",
                        np.clip(np.round(measured), 0, 65535).astype(np.uint16),
                    )
                    write_comparison_csv(rows, out / "coarse_lut_comparison.csv")
                    self._write_lut_summary(out, rows)

                # Update views on main thread
                self.after(0, self._on_build_complete)
                self.after(0, self._save_config_now)
            except Exception as exc:
                _exc_msg = str(exc)
                self.after(0, lambda: self._progress_bar.pack_forget())
                self.after(0, lambda m=_exc_msg: self._status(f"Build error: {m}"))
                self.after(0, lambda m=_exc_msg: messagebox.showerror("Build Error", m))
            finally:
                self.after(0, self._on_build_finished)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_build_complete(self) -> None:
        self._progress_bar.pack_forget()
        self._status(f"Build complete. {len(self.comparison_rows or [])} coarse points solved.")
        self._update_cie_chart()
        self._update_histogram()
        self._update_white_slices()
        self._update_comparison_table()
        self._update_3d_viewer()

    def _on_build_finished(self) -> None:
        """Always called after a build attempt (success or error) to re-enable the button."""
        self._build_active = False
        if self._build_btn is not None:
            self._build_btn.config(state=tk.NORMAL)

    def _fix_lut_black(self) -> None:
        """Force RGBW = (0,0,0,0) at grid index [0,0,0] in both classic and measured cubes."""
        changed = []
        for name, cube in (("measured", self.measured_cube), ("classic", self.classic_cube)):
            if cube is None:
                continue
            if np.any(cube[0, 0, 0] != 0):
                cube[0, 0, 0] = 0.0
                changed.append(name)

        if not changed:
            self._status("Fix LUT: [0,0,0] is already zero in all loaded cubes.")
            return

        # Patch comparison_rows entry for (0,0,0) if present.
        if self.comparison_rows:
            for row in self.comparison_rows:
                if row.get("r_index") == 0 and row.get("g_index") == 0 and row.get("b_index") == 0:
                    row["proposed_w"] = 0.0
                    row["raw_proposed_w"] = 0.0
                    row["white_gain_abs"] = 0.0
                    row["raw_white_gain_abs"] = 0.0
                    break

        # Re-save patched cubes to disk.
        out = self.settings.output_dir
        gs = self.settings.coarse_grid_size
        if self.measured_cube is not None:
            np.save(out / f"measured_rgbw_coarse_{gs}.npy",
                    np.clip(np.round(self.measured_cube), 0, 65535).astype(np.uint16))
        if self.classic_cube is not None:
            np.save(out / f"classic_rgbw_coarse_{gs}.npy",
                    np.clip(np.round(self.classic_cube), 0, 65535).astype(np.uint16))
        if self.comparison_rows:
            write_comparison_csv(self.comparison_rows, out / "coarse_lut_comparison.csv")

        # Refresh all views.
        self._update_cie_chart()
        self._update_histogram()
        self._update_white_slices()
        self._update_comparison_table()
        self._update_3d_viewer()
        self._status(f"Fix LUT: zeroed [0,0,0] in {', '.join(changed)} cube(s) and re-saved.")

    # ------------------------------------------------------------------
    # LUT summary generation
    # ------------------------------------------------------------------

    def _write_lut_summary(self, out: Path, rows: list[dict]) -> None:
        """Write lut_summary.json capturing settings, basis, counts, quantiles, and extremes."""
        s = self.settings
        summary: dict[str, Any] = {}

        # Settings block
        summary["settings"] = {
            "max_delta_e": s.max_delta_e,
            "max_hue_shift": s.max_hue_shift,
            "ignore_hue_below_chroma": s.ignore_hue_below_chroma,
            "target_white_balance_mode": s.target_white_balance_mode,
            "neutral_classic_chroma": s.neutral_classic_chroma,
            "neutral_classic_fade_width": s.neutral_classic_fade_width,
            "measured_prior_mode": s.measured_prior_mode,
            "measured_prior_neighbors": s.measured_prior_neighbors,
            "measured_family_count": s.measured_family_count,
            "measured_prior_strength": s.measured_prior_strength,
            "nondegenerate_regularization": s.nondegenerate_regularization,
            "max_luminance_ratio": s.max_luminance_ratio,
            "sample_scale": s.sample_scale,
            "coarse_grid_size": s.coarse_grid_size,
            "full_grid_size": s.full_grid_size,
            "gamut_clamp_colorspace": s.gamut_clamp_colorspace,
            "white_x": s.white_x,
            "white_y": s.white_y,
            "white_Y": s.white_Y,
            "input_dir": str(s.input_dir),
        }

        # Basis vectors
        if self.basis is not None:
            summary["basis_xyz_per_q16"] = {
                k: [float(x) for x in v] for k, v in self.basis.items()
            }
            rgb_sum = self.basis["r16"] + self.basis["g16"] + self.basis["b16"]
            eq_xy = xyz_to_xy(rgb_sum)
            w_xy = xyz_to_xy(self.basis["w16"])
            summary["basis_sanity"] = {
                "equal_rgb_neutral_xyz_per_q16": [float(x) for x in rgb_sum],
                "equal_rgb_neutral_xy": [float(eq_xy[0]), float(eq_xy[1])],
                "pure_white_channel_xy": [float(w_xy[0]), float(w_xy[1])],
                "reference_white_xy": [s.white_x, s.white_y],
                "equal_rgb_vs_white_dx": float(eq_xy[0] - w_xy[0]),
                "equal_rgb_vs_white_dy": float(eq_xy[1] - w_xy[1]),
                "white_channel_vs_reference_dx": float(w_xy[0] - s.white_x),
                "white_channel_vs_reference_dy": float(w_xy[1] - s.white_y),
            }

        # Counts
        if rows:
            gains = np.array([r["white_gain_abs"] for r in rows], dtype=float)
            summary["counts"] = {
                "coarse_samples": len(rows),
                "proposed_more_white_than_classic": int(np.sum(gains > 0)),
                "proposed_less_white_than_classic": int(np.sum(gains < 0)),
                "neutral_classic_bias_samples": int(
                    sum(1 for r in rows if float(r.get("neutral_classic_blend", 0)) > 0)
                ),
            }
            summary["white_gain_quantiles"] = {
                "p01": float(np.percentile(gains, 1)),
                "p10": float(np.percentile(gains, 10)),
                "p50": float(np.percentile(gains, 50)),
                "p90": float(np.percentile(gains, 90)),
                "p99": float(np.percentile(gains, 99)),
            }

            sorted_by_gain = sorted(rows, key=lambda r: r["white_gain_abs"])
            summary["top_increases"] = sorted_by_gain[-20:][::-1]
            summary["top_decreases"] = sorted_by_gain[:20]

        path = out / "lut_summary.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)

    # ------------------------------------------------------------------
    # Gamut clamping
    # ------------------------------------------------------------------

    def _apply_gamut_clamp(self, cube: np.ndarray, axis: np.ndarray) -> np.ndarray:
        """Clamp solved RGBW values so that resulting xy stays within the target gamut triangle."""
        cs_name = self.settings.gamut_clamp_colorspace
        cs = COLORSPACES.get(cs_name)
        if cs is None or self.rgb_basis is None or self.white_basis is None:
            return cube

        gamut_verts = [cs["R"], cs["G"], cs["B"]]
        rgb_basis = self.rgb_basis
        white_basis = self.white_basis
        clamped = cube.copy()
        grid_size = cube.shape[0]

        for ri in range(grid_size):
            for gi in range(grid_size):
                for bi in range(grid_size):
                    rgbw = cube[ri, gi, bi].astype(np.float64)
                    result_xyz = rgb_basis @ rgbw[:3] + white_basis * rgbw[3]
                    x, y = xyz_to_xy(result_xyz)
                    if not (math.isfinite(x) and math.isfinite(y)):
                        continue
                    if not point_in_triangle(x, y, gamut_verts):
                        # Fall back to classic min(rgb) which stays closer to the input colorspace
                        rgb_target = np.array([axis[ri], axis[gi], axis[bi]])
                        w = float(min(rgb_target))
                        clamped[ri, gi, bi] = np.array([rgb_target[0] - w, rgb_target[1] - w, rgb_target[2] - w, w], dtype=np.float32)

        return clamped

    # ------------------------------------------------------------------
    # RGBW → RGB cube conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _fold_w_into_rgb(cube: np.ndarray) -> np.ndarray:
        """Collapse an RGBW cube (N×N×N×4) to RGB (N×N×N×3) by adding W back into each color channel."""
        rgb = cube[..., :3].astype(np.float64) + cube[..., 3:4].astype(np.float64)
        return np.clip(np.round(rgb), 0, 65535).astype(np.uint16)

    @property
    def _is_rgb_mode(self) -> bool:
        return self.settings.channel_mode == "RGB"

    # ------------------------------------------------------------------
    # Histogram tab
    # ------------------------------------------------------------------

    def _update_histogram(self) -> None:
        for w in self._hist_frame.winfo_children():
            w.destroy()

        if not self.comparison_rows:
            return

        fig = Figure(figsize=(8, 5), dpi=100)
        ax = fig.add_subplot(111)
        gains = np.array([r["white_gain_abs"] for r in self.comparison_rows], dtype=float)
        ax.hist(gains, bins=80, color="#4477aa", alpha=0.85)
        ax.axvline(0.0, color="black", linewidth=1.0, alpha=0.5)
        ax.set_title("White gain over classic min(RGB)")
        ax.set_xlabel("Proposed W - Classic W")
        ax.set_ylabel("Count")
        ax.grid(True, alpha=0.2)
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=self._hist_frame)
        NavigationToolbar2Tk(canvas, self._hist_frame).update()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        canvas.draw()

    # ------------------------------------------------------------------
    # White slices tab
    # ------------------------------------------------------------------

    def _update_white_slices(self) -> None:
        for w in self._slice_frame.winfo_children():
            w.destroy()

        if self.classic_cube is None or self.measured_cube is None:
            return

        if self._is_rgb_mode:
            lbl = ttk.Label(self._slice_frame, text="White slices not applicable in RGB mode.")
            lbl.pack(pady=20)
            return

        axis = axis_values(self.settings.coarse_grid_size, self.settings.sample_scale)
        gs = axis.size
        slice_indices = [0, gs // 4, gs // 2, (3 * gs) // 4, gs - 1]
        n_slices = len(slice_indices)

        fig = Figure(figsize=(3.5 * n_slices, 7), dpi=90)

        for col, b_idx in enumerate(slice_indices):
            ax_c = fig.add_subplot(2, n_slices, col + 1)
            ax_m = fig.add_subplot(2, n_slices, n_slices + col + 1)
            ax_c.imshow(self.classic_cube[:, :, b_idx, 3], origin="lower", cmap="magma")
            ax_m.imshow(self.measured_cube[:, :, b_idx, 3], origin="lower", cmap="magma")
            ax_c.set_title(f"Classic W\nB={axis[b_idx]:.0f}", fontsize=8)
            ax_m.set_title(f"Measured W\nB={axis[b_idx]:.0f}", fontsize=8)
            ax_c.tick_params(labelsize=6)
            ax_m.tick_params(labelsize=6)

        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=self._slice_frame)
        NavigationToolbar2Tk(canvas, self._slice_frame).update()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        canvas.draw()

    # ------------------------------------------------------------------
    # Comparison table tab
    # ------------------------------------------------------------------

    def _update_comparison_table(self) -> None:
        for w in self._table_frame.winfo_children():
            w.destroy()

        if not self.comparison_rows:
            return

        # Build column list from whichever keys are present, in a preferred display order
        preferred_order = [
            "r_index", "g_index", "b_index",
            "target_L", "target_C", "target_h",
            "classic_w", "proposed_w", "out_w", "white_gain_abs", "w_pct",
            "proposed_delta_e", "proposed_hue_shift",
            "neutral_classic_blend", "prior_mode",
            "in_hull", "projected", "bary_min",
        ]
        available = set(self.comparison_rows[0].keys())
        columns = [c for c in preferred_order if c in available]
        for c in self.comparison_rows[0].keys():
            if c not in columns:
                columns.append(c)

        tree = ttk.Treeview(self._table_frame, columns=columns, show="headings", height=25)
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=85, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(self._table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)

        for row in self.comparison_rows:
            vals = []
            for col in columns:
                v = row.get(col, "")
                if isinstance(v, float):
                    vals.append(f"{v:.3f}")
                else:
                    vals.append(str(v))
            tree.insert("", tk.END, values=vals)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

    # ------------------------------------------------------------------
    # 3D LUT Viewer tab
    # ------------------------------------------------------------------

    def _update_3d_viewer(self) -> None:
        for w in self._lut3d_frame.winfo_children():
            w.destroy()

        if self.measured_cube is None:
            return

        from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 — registers 3D projection

        cube = self.measured_cube
        gs = cube.shape[0]

        # Controls at top
        ctrl = ttk.Frame(self._lut3d_frame)
        ctrl.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(ctrl, text="Subsample step:").pack(side=tk.LEFT, padx=2)
        self._lut3d_step_var = tk.StringVar(value=str(max(1, gs // 9)))
        step_spin = ttk.Spinbox(ctrl, from_=1, to=gs, textvariable=self._lut3d_step_var, width=5,
                                command=self._redraw_3d)
        step_spin.pack(side=tk.LEFT, padx=2)

        self._lut3d_channel_var = tk.StringVar(value="W" if not self._is_rgb_mode else "R")
        ch_labels = ("R", "G", "B") if self._is_rgb_mode else ("R", "G", "B", "W")
        for ch_label in ch_labels:
            ttk.Radiobutton(ctrl, text=ch_label, variable=self._lut3d_channel_var, value=ch_label,
                            command=self._redraw_3d).pack(side=tk.LEFT, padx=4)

        self._lut3d_show_classic = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctrl, text="Show classic", variable=self._lut3d_show_classic,
                        command=self._redraw_3d).pack(side=tk.LEFT, padx=6)

        fig = Figure(figsize=(7, 6), dpi=90)
        self._lut3d_fig = fig
        self._lut3d_canvas_widget = FigureCanvasTkAgg(fig, master=self._lut3d_frame)
        NavigationToolbar2Tk(self._lut3d_canvas_widget, self._lut3d_frame).update()
        self._lut3d_canvas_widget.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self._redraw_3d()

    def _redraw_3d(self) -> None:
        if self.measured_cube is None or not hasattr(self, "_lut3d_fig"):
            return

        from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

        fig = self._lut3d_fig
        fig.clear()

        ch_map = {"R": 0, "G": 1, "B": 2, "W": 3}
        ch_idx = ch_map[self._lut3d_channel_var.get()]
        ch_name = self._lut3d_channel_var.get()

        cube = self.measured_cube
        gs = cube.shape[0]
        step = max(1, int(self._lut3d_step_var.get()))
        axis = axis_values(gs, self.settings.sample_scale)

        ri = np.arange(0, gs, step)
        gi = np.arange(0, gs, step)
        bi = np.arange(0, gs, step)
        rr, gg, bb = np.meshgrid(ri, gi, bi, indexing="ij")
        rr_flat = rr.ravel()
        gg_flat = gg.ravel()
        bb_flat = bb.ravel()

        vals = cube[rr_flat, gg_flat, bb_flat, ch_idx].astype(float)
        norm_vals = vals / max(float(vals.max()), 1.0)

        # Map to colours for the scatter
        if ch_idx == 0:
            colors = np.column_stack([norm_vals, np.zeros(len(norm_vals)), np.zeros(len(norm_vals)), np.full(len(norm_vals), 0.4)])
        elif ch_idx == 1:
            colors = np.column_stack([np.zeros(len(norm_vals)), norm_vals, np.zeros(len(norm_vals)), np.full(len(norm_vals), 0.4)])
        elif ch_idx == 2:
            colors = np.column_stack([np.zeros(len(norm_vals)), np.zeros(len(norm_vals)), norm_vals, np.full(len(norm_vals), 0.4)])
        else:
            colors = np.column_stack([norm_vals * 0.8, norm_vals * 0.8, norm_vals, np.full(len(norm_vals), 0.4)])

        ax = fig.add_subplot(111, projection="3d")
        x_vals = axis[rr_flat]
        y_vals = axis[gg_flat]
        z_vals = axis[bb_flat]
        sizes = 6.0 + 20.0 * norm_vals
        ax.scatter(x_vals, y_vals, z_vals, c=colors, s=sizes, depthshade=True)

        if self._lut3d_show_classic.get() and self.classic_cube is not None:
            c_vals = self.classic_cube[rr_flat, gg_flat, bb_flat, ch_idx].astype(float)
            c_norm = c_vals / max(float(c_vals.max()), 1.0)
            c_colors = np.column_stack([c_norm * 0.5, c_norm * 0.5, c_norm * 0.5, np.full(len(c_norm), 0.15)])
            c_sizes = 3.0 + 8.0 * c_norm
            ax.scatter(x_vals, y_vals, z_vals, c=c_colors, s=c_sizes, depthshade=True, marker="x")

        ax.set_xlabel("R (input)")
        ax.set_ylabel("G (input)")
        ax.set_zlabel("B (input)")
        ax.set_title(f"3D LUT — {ch_name} channel output  (step={step})")

        fig.tight_layout()
        self._lut3d_canvas_widget.draw()

    # ------------------------------------------------------------------
    # Header export
    # ------------------------------------------------------------------

    def _export_header(self) -> None:
        if self.measured_cube is None:
            messagebox.showwarning("No LUT", "Build a LUT first.")
            return
        self._sync_settings()
        args = self._settings_as_namespace()
        out = self.settings.output_dir
        out.mkdir(parents=True, exist_ok=True)
        header_grid = self.settings.header_grid_size if self.settings.header_grid_size > 0 else self.settings.coarse_grid_size
        expanded = trilinear_expand_cube(self.measured_cube, header_grid)

        # In RGB mode, fold W back into RGB and zero out W channel
        if self._is_rgb_mode:
            exp_f = expanded.astype(np.float64)
            exp_f[..., :3] += exp_f[..., 3:4]
            exp_f[..., 3] = 0.0
            expanded = np.clip(np.round(exp_f), 0, 65535).astype(np.uint16)

        path = out / f"{self.settings.header_name}_grid_{header_grid}_from_{self.settings.coarse_grid_size}.h"
        write_rgbw_header(expanded, path, self.settings.header_name, args, self.settings.coarse_grid_size)
        self._status(f"Exported header: {path}")

        if self.settings.emit_classic_header and self.classic_cube is not None:
            classic_expanded = trilinear_expand_cube(self.classic_cube, header_grid)
            if self._is_rgb_mode:
                c_f = classic_expanded.astype(np.float64)
                c_f[..., :3] += c_f[..., 3:4]
                c_f[..., 3] = 0.0
                classic_expanded = np.clip(np.round(c_f), 0, 65535).astype(np.uint16)
            cpath = out / f"classic_rgbw_lut_grid_{header_grid}_from_{self.settings.coarse_grid_size}.h"
            write_rgbw_header(classic_expanded, cpath, "classic_rgbw_lut", args, self.settings.coarse_grid_size)
            self._status(f"Exported classic + measured headers under {out}")

    # ------------------------------------------------------------------
    # True16 Calibration header export
    # ------------------------------------------------------------------

    def _export_1d_header(self) -> None:
        """Export a per-channel Q16→Q16 calibration LUT in True16 format from the solved cube.

        Uses 1D interpolation from the coarse cube sweeps rather than
        expanding the full 3D cube, so the target LUT size can be
        arbitrarily large (e.g. 16k or 50k to match ladder state counts)
        without blowing up memory.
        """
        if self.measured_cube is None:
            messagebox.showwarning("No LUT", "Build a LUT first.")
            return
        self._sync_settings()
        out = self.settings.output_dir
        out.mkdir(parents=True, exist_ok=True)

        # Determine target LUT size (0 = use full_grid_size for backward compat)
        target_size = self.settings.true16_lut_size
        if target_size <= 0:
            target_size = self.settings.full_grid_size

        # Extract 1D sweeps from the coarse cube (shape: coarse³ × 4)
        coarse = self.measured_cube.astype(np.float64)
        gs = coarse.shape[0]  # coarse grid size
        coarse_x = np.linspace(0, 65535, gs)  # Q16 knot positions
        target_x = np.linspace(0, 65535, target_size)  # Q16 target positions

        # Pure-channel sweeps at coarse resolution
        coarse_r = coarse[:, 0, 0, 0]
        coarse_g = coarse[0, :, 0, 1]
        coarse_b = coarse[0, 0, :, 2]
        coarse_w = np.array([coarse[i, i, i, 3] for i in range(gs)])

        # In RGB mode, fold W back into each pure-channel sweep
        rgb_mode = self._is_rgb_mode
        if rgb_mode:
            coarse_r = coarse_r + np.array([coarse[i, 0, 0, 3] for i in range(gs)])
            coarse_g = coarse_g + np.array([coarse[0, i, 0, 3] for i in range(gs)])
            coarse_b = coarse_b + np.array([coarse[0, 0, i, 3] for i in range(gs)])

        # Interpolate each sweep to target_size using piecewise linear
        lut_r = np.clip(np.round(np.interp(target_x, coarse_x, coarse_r)), 0, 65535).astype(np.uint16)
        lut_g = np.clip(np.round(np.interp(target_x, coarse_x, coarse_g)), 0, 65535).astype(np.uint16)
        lut_b = np.clip(np.round(np.interp(target_x, coarse_x, coarse_b)), 0, 65535).astype(np.uint16)
        lut_w = np.clip(np.round(np.interp(target_x, coarse_x, coarse_w)), 0, 65535).astype(np.uint16)

        lut_size = target_size
        if rgb_mode:
            channels = [("R", lut_r), ("G", lut_g), ("B", lut_b)]
            mode_label = "RGB"
        else:
            channels = [("R", lut_r), ("G", lut_g), ("B", lut_b), ("W", lut_w)]
            mode_label = "RGBW"

        # Compute per-channel metadata
        def _channel_meta(arr: np.ndarray) -> dict:
            max_val = int(arr.max())
            nonzero = arr[arr > 0]
            measurement_points = int(len(nonzero))
            sample_count = int(len(arr))
            # Largest gap between consecutive output values
            diffs = np.diff(arr.astype(np.int32))
            largest_gap = int(np.max(np.abs(diffs))) if len(diffs) > 0 else 0
            return {
                "max_y_x1000": int(round(max_val / 65535.0 * 1000000)),
                "measurement_points": measurement_points,
                "sample_count": sample_count,
                "largest_gap_q16": largest_gap,
            }

        ch_meta = {name: _channel_meta(arr) for name, arr in channels}

        path = out / f"True16_Calibration_{mode_label}_{lut_size}.h"
        lines = [
            f"// Auto-generated True16 calibration header from RGBW LUT GUI ({mode_label} mode)",
            "// 16-bit Q16 input -> 16-bit Q16 output LUTs derived from measured RGBW cube",
            f"// Coarse grid: {self.settings.coarse_grid_size}, expanded to: {lut_size}, channels: {mode_label}",
            "#pragma once",
            "#include <TemporalBFI.h>",
            "",
            "namespace TemporalBFICalibrationTrue16 {",
            "",
        ]

        # Emit per-channel LUT arrays
        for ch_name, arr in channels:
            lines.append(f"static const uint16_t LUT_{ch_name}_16_TO_16[{lut_size}] = {{")
            for i in range(0, lut_size, 8):
                chunk = ", ".join(str(int(v)) for v in arr[i : i + 8])
                trailing = "," if i + 8 < lut_size else ","
                lines.append(f"    {chunk}{trailing}")
            lines.append("};")
            lines.append("")

        # LUT statistics
        lines.append("// LUT statistics and configuration")
        lines.append(f"static const uint16_t LUT_SIZE = {lut_size};")
        lines.append(f"static const uint8_t LUT_CHANNEL_COUNT = {len(channels)};")
        if not rgb_mode:
            lines.append("static const uint16_t WHITE_SHAPE_SCALE_Q16 = 65535;")
            lines.append("static const uint16_t WHITE_SHAPE_GAMMA_X1000 = 1000;")
            lines.append("static const uint8_t WHITE_AUTO_SCALE_ENABLED = 0;")
            lines.append("static const uint16_t WHITE_AUTO_RECOMMENDED_SCALE_Q16 = 65535;")
            lines.append("static const uint16_t WHITE_AUTO_PAIR_COUNT = 0;")
        lines.append("")

        # Per-channel metadata
        for ch_name, _ in channels:
            m = ch_meta[ch_name]
            lines.append(f"// Channel {ch_name}")
            lines.append(f"static const uint32_t {ch_name}_MAX_Y_X1000 = {m['max_y_x1000']};")
            lines.append(f"static const uint16_t {ch_name}_MEASUREMENT_POINTS = {m['measurement_points']};")
            lines.append(f"static const uint16_t {ch_name}_SAMPLE_COUNT = {m['sample_count']};")
            lines.append(f"static const uint16_t {ch_name}_LARGEST_GAP_Q16 = {m['largest_gap_q16']};")
            lines.append("")

        # Global fit metadata
        lines.append("// Global fit metadata")
        lines.append("static const uint32_t BLACK_LEVEL_Y_X1000 = 0;")
        lines.append("static const uint8_t BLACK_LEVEL_COMPENSATION_ENABLED = 0;")
        lines.append("static const uint16_t PROFILE_TARGET_GAMMA_X1000 = 1000;")
        profile_mode = "rgb-cube-derived" if rgb_mode else "rgbw-cube-derived"
        lines.append(f'static constexpr const char* PROFILE_TARGET_MODE = "{profile_mode}";')
        lines.append("")

        # Mixed-patch correction (not applicable for RGBW cube extractions)
        lines.append("// Mixed-patch correction metadata")
        lines.append("static const uint8_t MIXED_PATCH_CORRECTION_ENABLED = 0;")
        lines.append("static const uint8_t MIXED_PATCH_CORRECTION_APPLIED = 0;")
        lines.append("static const uint16_t MIXED_PATCH_CORRECTION_STRENGTH_X1000 = 0;")
        lines.append("static const uint16_t MIXED_PATCH_CORRECTION_ROWS_USED = 0;")
        lines.append("")

        # Global mixed-patch fit (not applicable)
        lines.append("// Optional global mixed-patch fit metadata")
        lines.append("static const uint8_t GLOBAL_MIXED_FIT_ENABLED = 0;")
        lines.append("static const uint8_t GLOBAL_MIXED_FIT_APPLIED = 0;")
        lines.append("static const uint16_t GLOBAL_MIXED_FIT_PEAK_PRESERVE_STRENGTH_X1000 = 0;")
        lines.append("static const uint16_t GLOBAL_MIXED_FIT_GAMMA_X1000 = 1000;")
        lines.append("static const uint16_t GLOBAL_MIXED_FIT_SCALE_R_Q16 = 65535;")
        lines.append("static const uint16_t GLOBAL_MIXED_FIT_SCALE_G_Q16 = 65535;")
        lines.append("static const uint16_t GLOBAL_MIXED_FIT_SCALE_B_Q16 = 65535;")
        if not rgb_mode:
            lines.append("static const uint16_t GLOBAL_MIXED_FIT_SCALE_W_Q16 = 65535;")
        lines.append("")

        # Combined accessor struct
        lines.append("// Combined accessor for runtime use")
        lines.append("struct True16LUTSet {")
        lines.append("    static const uint16_t* lutForChannel(uint8_t channel) {")
        lines.append("        switch (channel) {")
        lines.append("            case 0: return LUT_G_16_TO_16;")
        lines.append("            case 1: return LUT_R_16_TO_16;")
        if rgb_mode:
            lines.append("            default: return LUT_B_16_TO_16;")
        else:
            lines.append("            case 2: return LUT_B_16_TO_16;")
            lines.append("            default: return LUT_W_16_TO_16;")
        lines.append("        }")
        lines.append("    }")
        lines.append(f"    static constexpr size_t lutSize() {{ return LUT_SIZE; }}")
        lines.append("};")
        lines.append("")
        lines.append("} // namespace TemporalBFICalibrationTrue16")
        lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        self._status(f"Exported True16 calibration header: {path}")

    # ------------------------------------------------------------------
    # Binary cube export (SD / QSPI flash → PSRAM at boot)
    # ------------------------------------------------------------------

    def _export_binary_cube(self) -> None:
        """Export the measured cube as a raw binary file for SD-card or QSPI flash loading.

        File layout (little-endian):
            Bytes 0–1:   grid size N  (uint16)
            Bytes 2–3:   channels C   (uint16) — 4 for RGBW, 3 for RGB
            Bytes 4…:    N×N×N×C uint16 values in R,G,B row-major order
                         i.e. cube[r][g][b] = {ch0, ch1, ...}
        Total payload = 4 + N³ × C × 2  bytes.
        """
        if self.measured_cube is None:
            messagebox.showwarning("No LUT", "Build a LUT first.")
            return
        self._sync_settings()
        out = self.settings.output_dir
        out.mkdir(parents=True, exist_ok=True)

        rgb_mode = self._is_rgb_mode
        if rgb_mode:
            cube = self._fold_w_into_rgb(self.measured_cube)
            n_ch = 3
            mode_label = "rgb"
        else:
            cube = np.clip(np.round(self.measured_cube), 0, 65535).astype(np.uint16)
            n_ch = 4
            mode_label = "rgbw"

        gs = cube.shape[0]
        path = out / f"measured_{mode_label}_cube_{gs}.bin"

        header = np.array([gs, n_ch], dtype=np.uint16)
        with open(path, "wb") as f:
            f.write(header.tobytes())
            f.write(cube.tobytes())

        size_mb = path.stat().st_size / (1024 * 1024)
        self._status(f"Exported binary cube: {path}  ({gs}³×{n_ch} uint16, {size_mb:.2f} MB)")

    # ------------------------------------------------------------------
    # HyperHDR JSON export
    # ------------------------------------------------------------------

    def _export_hyperhdr_json(self) -> None:
        """Export the LUT as a HyperHDR-compatible JSON file.

        Produces the same schema that loadCustomRgbwLutProfile() in
        DriverOtherRawHid.cpp expects, so the file can be dropped
        directly into ~/.hyperhdr/rgbw_lut_headers/ on the Pi.
        """
        if self.measured_cube is None:
            messagebox.showwarning("No LUT", "Build a LUT first.")
            return
        self._sync_settings()
        out = self.settings.output_dir
        out.mkdir(parents=True, exist_ok=True)

        header_grid = (
            self.settings.header_grid_size
            if self.settings.header_grid_size > 0
            else self.settings.coarse_grid_size
        )
        expanded = trilinear_expand_cube(self.measured_cube, header_grid)

        if self._is_rgb_mode:
            exp_f = expanded.astype(np.float64)
            exp_f[..., :3] += exp_f[..., 3:4]
            exp_f[..., 3] = 0.0
            expanded = np.clip(np.round(exp_f), 0, 65535).astype(np.uint16)

        quantized = np.clip(np.round(expanded), 0, 65535).astype(np.uint16)
        flat = quantized.reshape(-1, 4)
        gs = int(quantized.shape[0])
        entry_count = int(flat.shape[0])
        slug = f"{self.settings.header_name}_grid_{gs}_from_{self.settings.coarse_grid_size}"

        payload = {
            "name": slug,
            "slug": slug,
            "gridSize": gs,
            "sourceGridSize": self.settings.coarse_grid_size,
            "entryCount": entry_count,
            "axisMin": 0,
            "axisMax": int(round(self.settings.sample_scale)),
            "requires3dInterpolation": gs < 257,
            "channels": {
                "red": flat[:, 0].tolist(),
                "green": flat[:, 1].tolist(),
                "blue": flat[:, 2].tolist(),
                "white": flat[:, 3].tolist(),
            },
        }

        path = out / f"{slug}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

        size_kb = path.stat().st_size / 1024
        self._status(f"Exported HyperHDR JSON: {path}  ({gs}³×4, {size_kb:.0f} KB)")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _status(self, msg: str) -> None:
        self._status_var.set(msg)
        self.update_idletasks()


# ---------------------------------------------------------------------------
# Helper: Lab → XYZ (inverse of xyz_to_lab)
# ---------------------------------------------------------------------------

def _lab_to_xyz(lab: np.ndarray, ref: ReferenceWhite) -> np.ndarray:
    """Convert CIE L*a*b* back to XYZ (approximate inverse for visualization)."""
    L, a, b = float(lab[0]), float(lab[1]), float(lab[2])
    fy = (L + 16.0) / 116.0
    fx = a / 500.0 + fy
    fz = fy - b / 200.0
    delta = 6.0 / 29.0
    delta3 = delta ** 3

    def finv(t: float) -> float:
        if t > delta:
            return t ** 3
        return 3.0 * delta * delta * (t - 4.0 / 29.0)

    ref_xyz = ref.xyz
    X = ref_xyz[0] * finv(fx)
    Y = ref_xyz[1] * finv(fy)
    Z = ref_xyz[2] * finv(fz)
    return np.array([X, Y, Z])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = RGBWLutApp()
    app.mainloop()


if __name__ == "__main__":
    main()
