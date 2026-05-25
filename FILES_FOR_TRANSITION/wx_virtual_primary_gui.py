#!/usr/bin/env python3
"""Interactive WX virtual-primary / extraction-mode model viewer.

Drop this file next to the current xy_target_rgbw*.py model builder, then run:

    python wx_virtual_primary_gui.py

Or point it at a specific model file:

    python wx_virtual_primary_gui.py --model-path path/to/xy_target_rgbw_model.py

The GUI deliberately imports the model module instead of copying the solver
math.  It uses the model's own primaries, sub-gamut definitions, strict
projection helper, WX virtual-axis helpers, and current WX endpoint
solver, then visualizes the geometry around a draggable target xy.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import os
from pathlib import Path
import sys
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any

import numpy as np

# Matplotlib backend must be selected before importing pyplot.
import matplotlib
# Use Agg for --self-test so the non-GUI sanity check works on headless build hosts.
# Normal interactive runs use TkAgg.
matplotlib.use("Agg" if "--self-test" in sys.argv else "TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.patches import Polygon


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_GLOBS = (
    "xy_target_rgbw_model.py",
    "xy_target_rgbw_lut*.py",
    "xy_target_rgbw*.py",
)

SUBGAMUT_FILL = {
    "RGW": "#ffcc66",
    "RBW": "#99aaff",
    "BGW": "#9ee6a4",
}
CHANNEL_COLOR = {
    "R": "#ff2020",
    "G": "#25c940",
    "B": "#2050ff",
    "W": "#8a7600",
}
VIRTUAL_COLOR = {
    "RGW": "#e0b000",
    "RBW": "#4f77ff",
    "BGW": "#1b9b55",
}

PRESETS = {
    "D65": (0.3127, 0.3290),
    "LED W": (0.3299, 0.3582),
    "Orange": (0.531, 0.436),
    "Yellow": (0.445, 0.504),
    "Chartreuse": (0.358, 0.573),
    "Spring": (0.151, 0.457),
    "Rose": (0.436, 0.252),
    "Warm near-white": (0.380, 0.380),
    "Cool near-white": (0.285, 0.300),
}


def _load_model_from_path(path: Path) -> Any:
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".py":
        raise ValueError(f"Model path must be a Python file: {path}")

    # Dataclasses need the module to be present in sys.modules before exec.
    module_name = "rgbw_wx_model_loaded"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not create import spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _find_default_model() -> Path | None:
    candidates: list[Path] = []
    for pattern in DEFAULT_MODEL_GLOBS:
        candidates.extend(SCRIPT_DIR.glob(pattern))
        candidates.extend(Path.cwd().glob(pattern))
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in candidates:
        try:
            rp = p.resolve()
        except Exception:
            continue
        if rp == Path(__file__).resolve() or rp in seen:
            continue
        seen.add(rp)
        unique.append(rp)
    if not unique:
        return None
    # Prefer a short import-friendly bundled model name, otherwise the newest.
    for p in unique:
        if p.name == "xy_target_rgbw_model.py":
            return p
    return max(unique, key=lambda p: p.stat().st_mtime)


def _safe_float_pair(x: float, y: float) -> np.ndarray:
    x = float(np.clip(x, 0.001, 0.84))
    y = float(np.clip(y, 0.001, 0.86))
    # Keep xy inside the legal xy half-plane.  This does not clamp to the LED
    # hull; out-of-hull points are still useful because the model projection can
    # be shown explicitly.
    if x + y >= 0.995:
        over = x + y - 0.995
        y = max(0.001, y - over)
    return np.array([x, y], dtype=np.float64)


def _xy_from_xyz(model: Any, xyz: np.ndarray) -> np.ndarray:
    return np.asarray(model.XYZ_to_xy(np.asarray(xyz, dtype=np.float64)), dtype=np.float64)


def _xyz_from_fraction(model: Any, frac: np.ndarray) -> np.ndarray:
    f = np.asarray(frac, dtype=np.float64)
    xyz = np.zeros(3, dtype=np.float64)
    for i, ch in enumerate("RGBW"):
        xyz += float(f[i]) * np.asarray(model.PRIMARY_XYZ[ch], dtype=np.float64)
    return xyz


def _u16_tuple(frac: np.ndarray) -> tuple[int, int, int, int]:
    f = np.clip(np.asarray(frac, dtype=np.float64), 0.0, 1.0)
    return tuple(int(np.clip(round(v * 65535.0), 0, 65535)) for v in f)  # type: ignore[return-value]


def _fmt_xy(xy: np.ndarray) -> str:
    return f"({float(xy[0]):.5f}, {float(xy[1]):.5f})"


def _fmt_frac(frac: np.ndarray) -> str:
    return " ".join(f"{ch}:{float(v):.4f}" for ch, v in zip("RGBW", frac))


def _fmt_u16(frac: np.ndarray) -> str:
    r, g, b, w = _u16_tuple(frac)
    return f"R:{r:5d}  G:{g:5d}  B:{b:5d}  W:{w:5d}"


class WXVirtualPrimaryGUI(tk.Tk):
    def __init__(self, model: Any, model_path: Path) -> None:
        super().__init__()
        self.title("WX virtual-primary / extraction-mode explorer")
        self.geometry("1420x900")
        self.minsize(1100, 720)
        self.model = model
        self.model_path = model_path
        self.target_xy = np.asarray(getattr(model, "D65_xy", np.array([0.3127, 0.3290])), dtype=np.float64)
        self.dragging = False
        self._updating_entries = False

        self.show_raw_target = tk.BooleanVar(value=True)
        self.show_projected_target = tk.BooleanVar(value=True)
        self.show_virtual_triangle = tk.BooleanVar(value=True)
        self.show_missing_axes = tk.BooleanVar(value=True)
        self.show_w_lines = tk.BooleanVar(value=True)
        self.show_direct_lp = tk.BooleanVar(value=True)
        self.clamp_to_rgb_hull = tk.BooleanVar(value=False)
        self.wx_geometry_mode = tk.StringVar(value=str(getattr(model, "DEFAULT_WX_MODE", "wx_radial_virtual")))
        self.radial_target_position = tk.DoubleVar(value=float(getattr(model, "WX_RADIAL_TARGET_POSITION", 0.72)))
        self.value_scale = tk.DoubleVar(value=1.0)
        self.x_var = tk.StringVar(value=f"{self.target_xy[0]:.5f}")
        self.y_var = tk.StringVar(value=f"{self.target_xy[1]:.5f}")
        self.status_var = tk.StringVar(value="")

        self._build_widgets()
        self._connect_events()
        self.update_plot()

    # ------------------------------------------------------------------ UI ----
    def _build_widgets(self) -> None:
        root = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        root.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(root)
        right = ttk.Frame(root, padding=(10, 8))
        root.add(left, weight=5)
        root.add(right, weight=2)

        self.fig = Figure(figsize=(8.8, 7.4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.fig.subplots_adjust(left=0.08, right=0.98, top=0.94, bottom=0.08)
        self.canvas = FigureCanvasTkAgg(self.fig, master=left)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar = NavigationToolbar2Tk(self.canvas, left)
        toolbar.update()

        # Right panel controls
        ttk.Label(right, text="Target xy", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        xy_frame = ttk.Frame(right)
        xy_frame.pack(fill=tk.X, pady=(4, 6))
        ttk.Label(xy_frame, text="x").grid(row=0, column=0, sticky="w")
        ttk.Entry(xy_frame, textvariable=self.x_var, width=10).grid(row=0, column=1, padx=(4, 10))
        ttk.Label(xy_frame, text="y").grid(row=0, column=2, sticky="w")
        ttk.Entry(xy_frame, textvariable=self.y_var, width=10).grid(row=0, column=3, padx=(4, 8))
        ttk.Button(xy_frame, text="Set", command=self.set_from_entries).grid(row=0, column=4)

        ttk.Label(right, text="Drag the red target point on the CIE plot.  Out-of-hull targets stay visible; the model-projected target is shown separately.", wraplength=380).pack(anchor="w", pady=(0, 10))

        preset_box = ttk.LabelFrame(right, text="Presets")
        preset_box.pack(fill=tk.X, pady=(0, 8))
        for i, (name, xy) in enumerate(PRESETS.items()):
            b = ttk.Button(preset_box, text=name, command=lambda xy=xy: self.set_target_xy(np.array(xy, dtype=np.float64)))
            b.grid(row=i // 3, column=i % 3, sticky="ew", padx=2, pady=2)
        for col in range(3):
            preset_box.columnconfigure(col, weight=1)

        mode_box = ttk.LabelFrame(right, text="WX geometry mode")
        mode_box.pack(fill=tk.X, pady=(0, 8))
        ttk.Radiobutton(mode_box, text="wx_radial_virtual — constrained radial triangle", value="wx_radial_virtual", variable=self.wx_geometry_mode, command=self.update_plot).grid(row=0, column=0, sticky="w", padx=4, pady=1)
        ttk.Radiobutton(mode_box, text="wx_virtual_axis_maxbright — maximize-brightness axes", value="wx_virtual_axis_maxbright", variable=self.wx_geometry_mode, command=self.update_plot).grid(row=1, column=0, sticky="w", padx=4, pady=1)
        ttk.Radiobutton(mode_box, text="wx_lp_legacy — direct LP max-white endpoint", value="wx_lp_legacy", variable=self.wx_geometry_mode, command=self.update_plot).grid(row=2, column=0, sticky="w", padx=4, pady=1)
        ttk.Label(mode_box, text="Target position on active W→virtual radial axis").grid(row=3, column=0, sticky="w", padx=4, pady=(6, 0))
        ttk.Scale(mode_box, from_=0.50, to=0.98, orient=tk.HORIZONTAL, variable=self.radial_target_position, command=lambda _v: self.update_plot()).grid(row=4, column=0, sticky="ew", padx=4, pady=(0, 4))
        mode_box.columnconfigure(0, weight=1)

        opt_box = ttk.LabelFrame(right, text="Display")
        opt_box.pack(fill=tk.X, pady=(0, 8))
        checks = [
            ("Raw target", self.show_raw_target),
            ("Projected target", self.show_projected_target),
            ("Virtual triangle", self.show_virtual_triangle),
            ("Missing-primary axes", self.show_missing_axes),
            ("Virtual-to-W lines", self.show_w_lines),
            ("Current LP max-white endpoint", self.show_direct_lp),
            ("Clamp drag to LED RGB hull", self.clamp_to_rgb_hull),
        ]
        for i, (label, var) in enumerate(checks):
            ttk.Checkbutton(opt_box, text=label, variable=var, command=self.update_plot).grid(row=i, column=0, sticky="w", padx=4, pady=1)

        scale_box = ttk.LabelFrame(right, text="Output scale")
        scale_box.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(scale_box, text="Value scale applied to final RGBW output display only").pack(anchor="w", padx=4, pady=(4, 0))
        ttk.Scale(scale_box, from_=0.0, to=1.0, orient=tk.HORIZONTAL, variable=self.value_scale, command=lambda _v: self.update_plot()).pack(fill=tk.X, padx=4, pady=4)

        action_box = ttk.Frame(right)
        action_box.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(action_box, text="Load different model...", command=self.load_different_model).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_box, text="Copy report", command=self.copy_report).pack(side=tk.LEFT)

        ttk.Separator(right).pack(fill=tk.X, pady=(4, 8))
        ttk.Label(right, text="Live solve", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        self.report = tk.Text(right, height=24, wrap="none", font=("Consolas", 9))
        self.report.pack(fill=tk.BOTH, expand=True, pady=(4, 6))
        self.report.configure(state="disabled")

        ttk.Label(right, textvariable=self.status_var, foreground="#555555", wraplength=390).pack(anchor="w", pady=(4, 0))

    def _connect_events(self) -> None:
        self.canvas.mpl_connect("button_press_event", self.on_button_press)
        self.canvas.mpl_connect("button_release_event", self.on_button_release)
        self.canvas.mpl_connect("motion_notify_event", self.on_motion)

    # ------------------------------------------------------------- model calc ----
    def _solve_selected_endpoint(self, projected_xyz: np.ndarray, solve_xy: np.ndarray, mode: str, combined: np.ndarray, lp_frac: np.ndarray) -> np.ndarray:
        """Return the endpoint that matches the model builder's concrete WX mode.

        New model files expose _solve_wx_endpoint_fraction_from_xyz(); older
        files only expose the individual geometry helpers, so keep fallbacks to
        avoid breaking older exploratory scripts.
        """
        m = self.model
        target_pos = float(self.radial_target_position.get())
        try:
            if hasattr(m, "_solve_wx_endpoint_fraction_from_xyz"):
                return np.asarray(m._solve_wx_endpoint_fraction_from_xyz(
                    projected_xyz,
                    wx_mode=mode,
                    wx_radial_target_position=target_pos,
                ), dtype=np.float64)
        except TypeError:
            # Older interim patch used target_position instead of wx_radial_target_position.
            if mode == "wx_radial_virtual" and hasattr(m, "_solve_wx_radial_virtual_fraction_from_xyz"):
                return np.asarray(m._solve_wx_radial_virtual_fraction_from_xyz(projected_xyz, target_position=target_pos), dtype=np.float64)
            raise

        if mode == "wx_lp_legacy":
            return np.asarray(lp_frac, dtype=np.float64)
        if mode == "wx_radial_virtual" and hasattr(m, "_solve_wx_radial_virtual_fraction_from_xyz"):
            return np.asarray(m._solve_wx_radial_virtual_fraction_from_xyz(projected_xyz, target_position=target_pos), dtype=np.float64)
        if mode == "wx_virtual_axis_maxbright" and hasattr(m, "_solve_wx_virtual_axis_maxbright_fraction_from_xyz"):
            return np.asarray(m._solve_wx_virtual_axis_maxbright_fraction_from_xyz(projected_xyz), dtype=np.float64)
        return np.asarray(combined, dtype=np.float64)

    def compute_state(self) -> dict[str, Any]:
        m = self.model
        raw_xy = self.target_xy.copy()
        target_xyz = m.xy_Y_to_XYZ(raw_xy, 1.0)
        projected_xyz, projected, strict_frac = m._strict_project_target_xyz_to_led_hull(target_xyz)
        projected_xy = _xy_from_xyz(m, projected_xyz)
        solve_xy = projected_xy

        mode = self.wx_geometry_mode.get()
        radial_meta = None
        selected = None
        if mode == "wx_radial_virtual" and hasattr(m, "_select_wx_radial_virtual_primary_set"):
            target_pos = float(self.radial_target_position.get())
            selected = m._select_wx_radial_virtual_primary_set(solve_xy, target_position=target_pos)
            if hasattr(m, "_wx_radial_virtual_primary_state"):
                radial_meta = m._wx_radial_virtual_primary_state(solve_xy, target_position=target_pos)
        elif mode == "wx_virtual_axis_maxbright" and hasattr(m, "_select_wx_virtual_axis_primary_set"):
            selected = m._select_wx_virtual_axis_primary_set(solve_xy)
        elif mode == "wx_lp_legacy":
            selected = None
        elif hasattr(m, "_select_wx_virtual_axis_primary_set"):
            selected = m._select_wx_virtual_axis_primary_set(solve_xy)

        virtual_fracs: list[np.ndarray] = []
        virtual_xyz: list[np.ndarray] = []
        virtual_xy: list[np.ndarray] = []
        weights = np.zeros(3, dtype=np.float64)
        combined = np.zeros(4, dtype=np.float64)
        virtual_ok = False
        if selected is not None:
            virtual_fracs, virtual_xyz, virtual_xy = selected
            virtual_fracs = [np.asarray(v, dtype=np.float64) for v in virtual_fracs]
            virtual_xyz = [np.asarray(v, dtype=np.float64) for v in virtual_xyz]
            virtual_xy = [np.asarray(v, dtype=np.float64) for v in virtual_xy]
            weights = np.asarray(m._solve_virtual_primary_triangle_weights(solve_xy, np.stack(virtual_xyz, axis=0)), dtype=np.float64)
            for wi, frac in zip(weights, virtual_fracs):
                combined += float(wi) * frac
            max_c = float(np.max(combined))
            if max_c > 1e-12:
                combined = np.clip(combined / max_c, 0.0, 1.0)
                virtual_ok = True

        lp_frac = np.asarray(m._solve_wx_balanced_fraction_for_xy(solve_xy), dtype=np.float64)
        primary_frac = self._solve_selected_endpoint(projected_xyz, solve_xy, mode, combined, lp_frac)
        primary_ok = bool(float(np.max(primary_frac)) > 1e-12)
        strict_endpoint = np.asarray(strict_frac, dtype=np.float64)
        if float(np.max(strict_endpoint)) > 1e-12:
            strict_endpoint = strict_endpoint / float(np.max(strict_endpoint))

        sg = m._find_sub_gamut(solve_xy)
        rgb_in_hull = m._xy_in_triangle(solve_xy, m.PRIMARIES_XY["R"], m.PRIMARIES_XY["G"], m.PRIMARIES_XY["B"], eps=1e-8)
        value_scale = float(self.value_scale.get())

        return {
            "raw_xy": raw_xy,
            "target_xyz": target_xyz,
            "projected": bool(projected),
            "projected_xy": projected_xy,
            "projected_xyz": projected_xyz,
            "strict_frac": strict_endpoint,
            "solve_xy": solve_xy,
            "sub_gamut": sg,
            "rgb_in_hull": bool(rgb_in_hull),
            "mode": mode,
            "radial_meta": radial_meta,
            "virtual_ok": virtual_ok,
            "virtual_fracs": virtual_fracs,
            "virtual_xyz": virtual_xyz,
            "virtual_xy": virtual_xy,
            "weights": weights,
            "combined": combined,
            "primary_frac": np.clip(primary_frac, 0.0, 1.0),
            "primary_ok": primary_ok,
            "lp_frac": np.clip(lp_frac, 0.0, 1.0),
            "value_scale": value_scale,
        }

    # --------------------------------------------------------------- plotting ----
    def update_plot(self) -> None:
        try:
            state = self.compute_state()
            self._draw_state(state)
            self._update_report(state)
            self._update_entries()
        except Exception as exc:
            self.status_var.set(f"Solve error: {exc}")
            traceback.print_exc()

    def _draw_state(self, s: dict[str, Any]) -> None:
        m = self.model
        ax = self.ax
        ax.clear()
        ax.set_title(f"WX extraction geometry — {s.get('mode', 'unknown')}")
        ax.set_xlabel("CIE x")
        ax.set_ylabel("CIE y")
        ax.set_xlim(0.04, 0.76)
        ax.set_ylim(0.02, 0.84)
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, alpha=0.25, linewidth=0.7)

        locus = getattr(m, "_SPECTRAL_LOCUS_XY", None)
        if locus is not None:
            locus = np.asarray(locus, dtype=np.float64)
            if locus.ndim == 2 and locus.shape[1] >= 2:
                ax.plot(locus[:, 0], locus[:, 1], color="#555555", linewidth=1.2, alpha=0.65, label="CIE locus")
                ax.plot([locus[-1, 0], locus[0, 0]], [locus[-1, 1], locus[0, 1]], color="#555555", linewidth=1.0, alpha=0.45)

        # Physical RGB triangle and W-subgamut regions.
        rgb_pts = np.array([m.PRIMARIES_XY[ch] for ch in "RGB"], dtype=np.float64)
        ax.add_patch(Polygon(rgb_pts, closed=True, fill=False, edgecolor="#111111", linewidth=2.4, zorder=1, label="LED RGB hull"))
        for tri in m.SUB_GAMUTS:
            key = "".join(tri)
            pts = np.array([m.PRIMARIES_XY[ch] for ch in tri], dtype=np.float64)
            ax.add_patch(Polygon(pts, closed=True, facecolor=SUBGAMUT_FILL.get(key, "#dddddd"), edgecolor="#333333", linewidth=0.8, alpha=0.18, zorder=0))
            centroid = pts.mean(axis=0)
            ax.text(centroid[0], centroid[1], key, color=VIRTUAL_COLOR.get(key, "#444444"), fontsize=10, weight="bold", ha="center", va="center", alpha=0.85)

        # Physical primary points.
        for ch in "RGBW":
            xy = np.asarray(m.PRIMARIES_XY[ch], dtype=np.float64)
            ax.scatter([xy[0]], [xy[1]], s=70, color=CHANNEL_COLOR[ch], edgecolor="black", linewidth=0.6, zorder=5)
            ax.text(xy[0] + 0.006, xy[1] + 0.005, f"LED {ch}", color=CHANNEL_COLOR[ch], fontsize=10, weight="bold", zorder=6)

        raw_xy = s["raw_xy"]
        projected_xy = s["projected_xy"]
        solve_xy = s["solve_xy"]

        if self.show_raw_target.get():
            ax.scatter([raw_xy[0]], [raw_xy[1]], s=95, color="#ff3030", edgecolor="white", linewidth=1.2, zorder=12, label="raw target")
            ax.text(raw_xy[0] + 0.008, raw_xy[1] + 0.008, "target xy", color="#cc0000", fontsize=10, weight="bold", zorder=13)

        if self.show_projected_target.get() and (s["projected"] or np.linalg.norm(projected_xy - raw_xy) > 1e-5):
            ax.scatter([projected_xy[0]], [projected_xy[1]], s=82, marker="D", color="#222222", edgecolor="white", linewidth=1.0, zorder=12, label="projected target")
            ax.plot([raw_xy[0], projected_xy[0]], [raw_xy[1], projected_xy[1]], color="#222222", linewidth=1.0, linestyle=":", alpha=0.8, zorder=7)
            ax.text(projected_xy[0] + 0.008, projected_xy[1] - 0.018, "projected", color="#222222", fontsize=9, zorder=13)

        # Virtual primaries and triangle.
        virtual_xy = s["virtual_xy"]
        virtual_fracs = s["virtual_fracs"]
        tri_names = ["".join(t) for t in m.SUB_GAMUTS]
        if s["virtual_ok"] and len(virtual_xy) == 3:
            vxy_arr = np.array(virtual_xy, dtype=np.float64)
            if self.show_virtual_triangle.get():
                ax.add_patch(Polygon(vxy_arr, closed=True, fill=False, edgecolor="#000000", linewidth=2.0, linestyle="--", alpha=0.92, zorder=8, label="virtual gamut"))

            for i, (name, xy, frac) in enumerate(zip(tri_names, virtual_xy, virtual_fracs)):
                color = VIRTUAL_COLOR.get(name, "#000000")
                ax.scatter([xy[0]], [xy[1]], s=80, color=color, edgecolor="black", linewidth=0.7, zorder=10)
                label = f"V_{name}"
                ax.text(xy[0] + 0.006, xy[1] + (0.012 if i != 1 else -0.020), label, color=color, fontsize=10, weight="bold", zorder=11)

                if self.show_w_lines.get():
                    wxy = np.asarray(m.PRIMARIES_XY["W"], dtype=np.float64)
                    ax.plot([xy[0], wxy[0]], [xy[1], wxy[1]], color=color, linewidth=1.0, linestyle="-.", alpha=0.55, zorder=4)

                if self.show_missing_axes.get():
                    if s.get("mode") == "wx_radial_virtual" and s.get("radial_meta") is not None:
                        meta = s["radial_meta"]
                        try:
                            hxy = np.asarray(meta["hull_xy"][i], dtype=np.float64)
                            wxy = np.asarray(m.PRIMARIES_XY["W"], dtype=np.float64)
                            ax.plot([wxy[0], hxy[0]], [wxy[1], hxy[1]], color=color, linewidth=0.9, linestyle=":", alpha=0.65, zorder=3)
                        except Exception:
                            pass
                    else:
                        outer = [ch for ch in m.SUB_GAMUTS[i] if ch != "W"]
                        missing = [ch for ch in "RGB" if ch not in outer]
                        if missing:
                            oxy = np.asarray(m.PRIMARIES_XY[missing[0]], dtype=np.float64)
                            ax.plot([oxy[0], solve_xy[0], xy[0]], [oxy[1], solve_xy[1], xy[1]], color=color, linewidth=0.9, linestyle=":", alpha=0.75, zorder=3)

            if self.show_missing_axes.get() and s.get("mode") == "wx_radial_virtual" and s.get("radial_meta") is not None:
                try:
                    meta = s["radial_meta"]
                    active_hull = np.asarray(meta["active_hull_xy"], dtype=np.float64)
                    wxy = np.asarray(m.PRIMARIES_XY["W"], dtype=np.float64)
                    ax.plot([wxy[0], solve_xy[0], active_hull[0]], [wxy[1], solve_xy[1], active_hull[1]], color="#cc9900", linewidth=1.2, linestyle="-", alpha=0.65, zorder=6, label="active W→target→hull")
                except Exception:
                    pass

        if self.show_direct_lp.get():
            lp_frac = s["lp_frac"]
            if float(np.max(lp_frac)) > 1e-12:
                lp_xyz = _xyz_from_fraction(m, lp_frac)
                lp_xy = _xy_from_xyz(m, lp_xyz)
                ax.scatter([lp_xy[0]], [lp_xy[1]], s=110, marker="*", color="#000000", edgecolor="white", linewidth=0.8, zorder=14, label="LP endpoint xy")
                ax.text(lp_xy[0] + 0.008, lp_xy[1] + 0.010, "LP max-W", color="#000000", fontsize=9, weight="bold", zorder=14)

        # Make the active solve point visible even when raw and projected overlap.
        ax.scatter([solve_xy[0]], [solve_xy[1]], s=26, color="#ffffff", edgecolor="#000000", linewidth=0.8, zorder=15)

        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(loc="lower right", fontsize=8, framealpha=0.86)
        self.canvas.draw_idle()

    def _update_report(self, s: dict[str, Any]) -> None:
        m = self.model
        lines: list[str] = []
        lines.append(f"model: {self.model_path.name}")
        lines.append(f"geometry mode       {s.get('mode', 'unknown')}")
        lines.append(f"raw target xy       {_fmt_xy(s['raw_xy'])}")
        lines.append(f"projected target   {_fmt_xy(s['projected_xy'])}  projected={s['projected']}")
        sg = "/".join(s["sub_gamut"]) if s["sub_gamut"] else "none / out of sub-gamuts"
        lines.append(f"active sub-gamut    {sg}")
        lines.append(f"inside LED RGB hull {s['rgb_in_hull']}")
        if s.get("radial_meta") is not None:
            meta = s["radial_meta"]
            lines.append(f"radial angular pos  {float(meta['angular_position']):.5f}")
            lines.append(f"radial hull frac    {float(meta['hull_fraction']):.5f}")
            lines.append(f"target position     {float(meta['target_position']):.5f}")
        lines.append("")
        if s.get("mode") == "wx_radial_virtual":
            lines.append("virtual primaries selected from model._select_wx_radial_virtual_primary_set():")
        elif s.get("mode") == "wx_virtual_axis_maxbright":
            lines.append("virtual primaries selected from model._select_wx_virtual_axis_primary_set() [max-brightness axes]:")
        else:
            lines.append("virtual primaries: skipped for direct LP max-white mode")
        tri_names = ["".join(t) for t in m.SUB_GAMUTS]
        if s["virtual_ok"]:
            for name, xy, frac, xyz in zip(tri_names, s["virtual_xy"], s["virtual_fracs"], s["virtual_xyz"]):
                lines.append(f"  V_{name:<3} xy={_fmt_xy(xy)}  Y={float(xyz[1]):9.4f}")
                lines.append(f"        {_fmt_frac(frac)}")
            lines.append(f"  weights  " + " ".join(f"{n}:{float(w):.5f}" for n, w in zip(tri_names, s["weights"])))
            combined = np.clip(s["combined"] * s["value_scale"], 0.0, 1.0)
            combined_xyz = _xyz_from_fraction(m, combined)
            combined_xy = _xy_from_xyz(m, combined_xyz) if float(combined_xyz[1]) > 1e-12 else np.asarray(getattr(m, "D65_xy", [0.3127, 0.3290]))
            lines.append("")
            lines.append("expanded virtual-primary result, normalized by limiting RGBW channel:")
            lines.append(f"  frac    {_fmt_frac(combined)}")
            lines.append(f"  u16     {_fmt_u16(combined)}")
            lines.append(f"  xy/Y    {_fmt_xy(combined_xy)}  Y={float(combined_xyz[1]):.4f}")
        else:
            lines.append("  no valid virtual primary set returned")

        lines.append("")
        primary = np.clip(s["primary_frac"] * s["value_scale"], 0.0, 1.0)
        primary_xyz = _xyz_from_fraction(m, primary)
        primary_xy = _xy_from_xyz(m, primary_xyz) if float(primary_xyz[1]) > 1e-12 else np.asarray(getattr(m, "D65_xy", [0.3127, 0.3290]))
        lines.append("selected-mode output:")
        lines.append(f"  frac    {_fmt_frac(primary)}")
        lines.append(f"  u16     {_fmt_u16(primary)}")
        lines.append(f"  xy/Y    {_fmt_xy(primary_xy)}  Y={float(primary_xyz[1]):.4f}")

        lines.append("")
        lp = np.clip(s["lp_frac"] * s["value_scale"], 0.0, 1.0)
        lp_xyz = _xyz_from_fraction(m, lp)
        lp_xy = _xy_from_xyz(m, lp_xyz) if float(lp_xyz[1]) > 1e-12 else np.asarray(getattr(m, "D65_xy", [0.3127, 0.3290]))
        lines.append("current LP max-white endpoint:")
        lines.append(f"  frac    {_fmt_frac(lp)}")
        lines.append(f"  u16     {_fmt_u16(lp)}")
        lines.append(f"  xy/Y    {_fmt_xy(lp_xy)}  Y={float(lp_xyz[1]):.4f}")

        if s["projected"]:
            strict = np.clip(s["strict_frac"] * s["value_scale"], 0.0, 1.0)
            lines.append("")
            lines.append("strict projected endpoint used to choose achievable expected xy:")
            lines.append(f"  frac    {_fmt_frac(strict)}")
            lines.append(f"  u16     {_fmt_u16(strict)}")

        self.report.configure(state="normal")
        self.report.delete("1.0", tk.END)
        self.report.insert("1.0", "\n".join(lines))
        self.report.configure(state="disabled")
        self.status_var.set("Tip: wx_radial_virtual uses active W→target→hull to set a shared angular/radial coordinate for all three virtual primaries.")

    def _update_entries(self) -> None:
        self._updating_entries = True
        try:
            self.x_var.set(f"{self.target_xy[0]:.5f}")
            self.y_var.set(f"{self.target_xy[1]:.5f}")
        finally:
            self._updating_entries = False

    # ----------------------------------------------------------- interactions ----
    def set_from_entries(self) -> None:
        try:
            xy = _safe_float_pair(float(self.x_var.get()), float(self.y_var.get()))
        except ValueError:
            messagebox.showerror("Invalid xy", "x and y must be numeric.")
            return
        self.set_target_xy(xy)

    def set_target_xy(self, xy: np.ndarray) -> None:
        xy = _safe_float_pair(float(xy[0]), float(xy[1]))
        if self.clamp_to_rgb_hull.get():
            xy = self._clamp_xy_to_rgb_hull(xy)
        self.target_xy = xy
        self.update_plot()

    def _clamp_xy_to_rgb_hull(self, xy: np.ndarray) -> np.ndarray:
        m = self.model
        A = np.asarray(m.PRIMARIES_XY["R"], dtype=np.float64)
        B = np.asarray(m.PRIMARIES_XY["G"], dtype=np.float64)
        C = np.asarray(m.PRIMARIES_XY["B"], dtype=np.float64)
        if m._xy_in_triangle(xy, A, B, C, eps=1e-12):
            return xy
        pts = np.array([A, B, C], dtype=np.float64)
        best = pts[0]
        best_d2 = float("inf")
        for p0, p1 in ((A, B), (B, C), (C, A)):
            v = p1 - p0
            denom = float(np.dot(v, v))
            if denom <= 1e-24:
                continue
            t = float(np.clip(np.dot(xy - p0, v) / denom, 0.0, 1.0))
            q = p0 + t * v
            d2 = float(np.dot(q - xy, q - xy))
            if d2 < best_d2:
                best_d2 = d2
                best = q
        return np.asarray(best, dtype=np.float64)

    def on_button_press(self, event: Any) -> None:
        if event.inaxes is not self.ax or event.xdata is None or event.ydata is None:
            return
        self.dragging = True
        self.set_target_xy(np.array([event.xdata, event.ydata], dtype=np.float64))

    def on_button_release(self, event: Any) -> None:
        self.dragging = False

    def on_motion(self, event: Any) -> None:
        if not self.dragging or event.inaxes is not self.ax or event.xdata is None or event.ydata is None:
            return
        self.set_target_xy(np.array([event.xdata, event.ydata], dtype=np.float64))

    def load_different_model(self) -> None:
        initial = str(self.model_path.parent if self.model_path else SCRIPT_DIR)
        filename = filedialog.askopenfilename(
            title="Load RGBW model Python file",
            initialdir=initial,
            filetypes=(("Python files", "*.py"), ("All files", "*.*")),
        )
        if not filename:
            return
        try:
            new_path = Path(filename)
            new_model = _load_model_from_path(new_path)
            # Basic compatibility check.
            for attr in ("PRIMARIES_XY", "PRIMARY_XYZ", "SUB_GAMUTS", "xy_Y_to_XYZ", "XYZ_to_xy"):
                if not hasattr(new_model, attr):
                    raise AttributeError(f"missing required model attribute: {attr}")
            self.model = new_model
            self.model_path = new_path
            self.radial_target_position.set(float(getattr(new_model, "WX_RADIAL_TARGET_POSITION", self.radial_target_position.get())))
            self.wx_geometry_mode.set(str(getattr(new_model, "DEFAULT_WX_MODE", self.wx_geometry_mode.get())))
            self.update_plot()
        except Exception as exc:
            messagebox.showerror("Could not load model", f"{exc}\n\n{traceback.format_exc(limit=3)}")

    def copy_report(self) -> None:
        text = self.report.get("1.0", tk.END)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set("Copied live solve report to clipboard.")


# --------------------------------------------------------------------- main ----
def _self_test(model: Any, model_path: Path) -> None:
    required = [
        "PRIMARIES_XY", "PRIMARY_XYZ", "SUB_GAMUTS", "D65_xy",
        "xy_Y_to_XYZ", "XYZ_to_xy", "_strict_project_target_xyz_to_led_hull",
        "_select_wx_virtual_axis_primary_set", "_solve_virtual_primary_triangle_weights",
        "_solve_wx_balanced_fraction_for_xy",
    ]
    missing = [name for name in required if not hasattr(model, name)]
    if missing:
        raise RuntimeError("Model missing required attributes: " + ", ".join(missing))
    xy = np.asarray(model.D65_xy, dtype=np.float64)
    selected = model._select_wx_virtual_axis_primary_set(xy)
    radial = model._select_wx_radial_virtual_primary_set(xy) if hasattr(model, "_select_wx_radial_virtual_primary_set") else None
    lp = model._solve_wx_balanced_fraction_for_xy(xy)
    print(f"Loaded model: {model_path}")
    print(f"D65 virtual-axis/maxbright selected set: {selected is not None}")
    print(f"D65 radial virtual selected set: {radial is not None}")
    print(f"D65 LP fraction: {_fmt_frac(lp)}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive WX virtual-primary / extraction-mode GUI")
    parser.add_argument("--model-path", type=Path, default=None, help="Path to the current xy_target_rgbw*.py model file")
    parser.add_argument("--self-test", action="store_true", help="Load the model and print a small non-GUI sanity check")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    model_path = args.model_path or _find_default_model()
    if model_path is None:
        print("Could not find xy_target_rgbw*.py next to this script or in the current directory.", file=sys.stderr)
        print("Run with --model-path path/to/xy_target_rgbw_model.py", file=sys.stderr)
        return 2
    try:
        model = _load_model_from_path(model_path)
        if args.self_test:
            _self_test(model, model_path)
            return 0
        app = WXVirtualPrimaryGUI(model, model_path)
        app.mainloop()
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
