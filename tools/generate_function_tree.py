from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = PROJECT_ROOT / "docs"
MARKDOWN_OUTPUT = DOCS_DIR / "project_function_tree.md"
JSON_OUTPUT = DOCS_DIR / "project_function_tree.json"

SCAN_ROOTS = [
	PROJECT_ROOT / "rgbw_lut_builder",
	PROJECT_ROOT / "tools",
	PROJECT_ROOT / "FILES_FOR_TRANSITION",
]

EXCLUDED_PARTS = {"__pycache__", ".git", ".venv", "venv", "build", "dist"}


@dataclass(frozen=True)
class FunctionInfo:
	module_path: str
	name: str
	lineno: int
	end_lineno: int
	description: str = ""

	@property
	def link(self) -> str:
		return f"../{self.module_path}#L{self.lineno}"


def math_ref(label: str, anchor: str) -> dict[str, str]:
	return {"label": label, "href": f"../README_MATH_MODEL.md{anchor}"}


FUNCTION_DESCRIPTIONS: dict[str, str] = {
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:input_linear_to_XYZ": "Source-gamut RGB to LED-space XYZ transform.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_find_sub_gamut": "Find the containing strict RGBW sub-gamut in xy space.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_xyz": "Shared 3-emitter XYZ linear solve primitive.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_strict_project_target_xyz_to_led_hull": "Project out-of-hull targets back onto the reachable LED hull.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_wx_fraction_from_linear": "Dispatch WX solve from linear RGB into a constrained RGBW tuple.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_wx_radial_virtual_fraction_from_xyz": "Radial virtual-primary WX solve from target XYZ.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_wx_virtual_axis_maxbright_fraction_from_xyz": "Virtual-axis max-brightness WX solve from target XYZ.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_wx_lp_legacy_fraction_from_xyz": "Reference LP/max-white WX solve from target XYZ.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_y_fraction_from_drive": "Single-channel Y ramp lookup from drive level.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_drive_from_channel_y_fraction": "Inverse single-channel Y ramp lookup.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_xy_from_drive": "Single-channel xy lookup from drive level.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_xyz_curve": "Per-channel XYZ curve synthesized from measured ramps.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:verify_captures": "Legacy model-vs-capture verification/report path.",
	"rgbw_lut_builder/legacy/xy_target_rgbw_model.py:build_rgbw_lut_cube": "Legacy full cube builder for model-only RGBW LUTs.",
	"rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_parse_verifier_feedback_rows": "Collect verifier CSV rows and target-match candidates.",
	"rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_failure_dictionary": "Legacy verifier failure aggregation/report writer.",
	"rgbw_lut_builder/captures/loaders.py:load_captures": "Load and normalize measured patch capture rows.",
	"rgbw_lut_builder/captures/loaders.py:build_family_capture_sets": "Group captures into family-specific lookup sets.",
	"rgbw_lut_builder/build/model_only.py:solve_model_node": "Package-owned single-node model solve entrypoint.",
	"rgbw_lut_builder/build/model_only.py:build_model_lut_cube": "Package-owned RGB/RGBW model cube build entrypoint.",
}


MOVE_PLAN: list[dict[str, object]] = [
	{
		"phase": "Phase 1",
		"item": "move rgbw_lut_gui into standalone repo",
		"status": "done",
		"targets": ["rgbw_lut_builder/gui/rgbw_lut_gui.py", "tools/build_lut.py"],
		"sources": ["FILES_FOR_TRANSITION/rgbw_lut_gui.py"],
		"patterns": [r"main"],
		"math_refs": [],
	},
	{
		"phase": "Phase 1",
		"item": "move reusable Delaunay/worker/memory/export utilities",
		"status": "done",
		"targets": [
			"rgbw_lut_builder/build/diagnostics.py",
			"rgbw_lut_builder/build/live_measured.py",
			"rgbw_lut_builder/build/lut_writer.py",
			"rgbw_lut_builder/captures/loaders.py",
			"rgbw_lut_builder/correction/pass_fail_dictionary.py",
		],
		"sources": ["rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"_worker_init", r"load_or_create_display_profile", r"write_verifier_.*", r"write_.*csv", r"available_memory_bytes"],
		"math_refs": [],
	},
	{
		"phase": "Phase 2",
		"item": "add explicit RGB-only model path",
		"status": "done",
		"targets": ["rgbw_lut_builder/model/rgb_model.py", "rgbw_lut_builder/build/model_only.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"input_linear_to_XYZ", r"_strict_project_target_xyz_to_led_hull", r"_solve_xyz"],
		"math_refs": [
			math_ref("RGB-only model", "#5-rgb-only-model"),
			math_ref("Out-of-hull projection", "#3-out-of-hull-projection"),
		],
	},
	{
		"phase": "Phase 2",
		"item": "keep RGBW strict sub-gamut model path",
		"status": "done",
		"targets": ["rgbw_lut_builder/model/topology.py", "rgbw_lut_builder/model/rgbw_model.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"_find_sub_gamut", r"_solve_xyz", r"_solve_subgamut_fraction_from_linear", r"rgb_to_rgbw_subgamut"],
		"math_refs": [
			math_ref("Strict RGBW sub-gamut model", "#6-strict-rgbw-sub-gamut-model"),
			math_ref("Common simplex solve", "#2-common-simplex-solve"),
		],
	},
	{
		"phase": "Phase 2",
		"item": "add explicit WX radial virtual-primary model path",
		"status": "done",
		"targets": ["rgbw_lut_builder/model/wx_modes.py", "rgbw_lut_builder/model/rgbw_model.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"_solve_wx_radial_.*", r"_wx_radial_.*", r"_solve_wx_fraction_from_linear"],
		"math_refs": [
			math_ref("WX family", "#7-wx--white-overdrive-model-family"),
			math_ref("WX common structure", "#8-wx-common-virtual-primary-structure"),
			math_ref("Preferred wx_radial_virtual", "#9-preferred-wx-mode-wx_radial_virtual"),
		],
	},
	{
		"phase": "Phase 2",
		"item": "keep LP max-white as wx_lp_legacy reference path",
		"status": "done",
		"targets": ["rgbw_lut_builder/model/wx_modes.py", "rgbw_lut_builder/model/rgbw_model.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"_solve_wx_lp_legacy_fraction_from_xyz", r"rgb_to_rgbw_wx_legacy"],
		"math_refs": [math_ref("Reference wx_lp_legacy", "#10-reference-wx-mode-wx_lp_legacy")],
	},
	{
		"phase": "Phase 2",
		"item": "add wx_virtual_axis_maxbright as a first-class high-brightness WX path",
		"status": "done",
		"targets": ["rgbw_lut_builder/model/wx_modes.py", "rgbw_lut_builder/model/rgbw_model.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"_solve_wx_virtual_axis_maxbright_.*", r"_select_wx_virtual_axis_.*", r"_wx_axis_candidates_for_subgamut"],
		"math_refs": [math_ref("Max-brightness wx_virtual_axis_maxbright", "#11-max-brightness-wx-mode-wx_virtual_axis_maxbright")],
	},
	{
		"phase": "Phase 2",
		"item": "share gamut transforms and hull projection",
		"status": "done",
		"targets": ["rgbw_lut_builder/model/gamuts.py", "rgbw_lut_builder/model/projection.py", "rgbw_lut_builder/model/simplex.py", "rgbw_lut_builder/model/topology.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"_build_gamut_matrix", r"input_linear_to_XYZ", r"XYZ_to_xy", r"_strict_project_target_xyz_to_led_hull", r"_barycentric_2d", r"_xy_in_triangle"],
		"math_refs": [
			math_ref("Source gamut conversion", "#source-gamut-conversion"),
			math_ref("Common simplex solve", "#2-common-simplex-solve"),
			math_ref("Out-of-hull projection", "#3-out-of-hull-projection"),
		],
	},
	{
		"phase": "Phase 2",
		"item": "share tetrahedral LUT sampling assumptions",
		"status": "planned",
		"targets": ["rgbw_lut_builder/model/interpolation/", "rgbw_lut_builder/output/coefficient_cube_export.py", "rgbw_lut_builder/runtime/"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py", "rgbw_lut_builder/runtime/"],
		"patterns": [r"write_lut_header", r"build_rgbw_lut_cube"],
		"math_refs": [math_ref("Tetrahedral LUT interpolation", "#14-tetrahedral-lut-interpolation")],
	},
	{
		"phase": "Phase 2",
		"item": "add output-family metadata everywhere",
		"status": "active",
		"targets": ["rgbw_lut_builder/build/model_only.py", "rgbw_lut_builder/output/", "rgbw_lut_builder/verify/reports.py"],
		"sources": ["rgbw_lut_builder/build/model_only.py", "rgbw_lut_builder/gui/build_measured_rgbw_lut.py", "rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"build_model_lut_cube", r"write_.*header", r"save_lut_npy", r"summary"],
		"math_refs": [],
	},
	{
		"phase": "Phase 3",
		"item": "implement ChannelResponseProvider API",
		"status": "planned",
		"targets": ["rgbw_lut_builder/response/base.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"_channel_.*", r"_drive_from_channel_.*", r"_decode_source_rgb16_to_linear", r"_encode_linear_to_model_code"],
		"math_refs": [math_ref("Correction response profiles and observed response curves", "#correction-response-profiles-and-observed-response-curves")],
	},
	{
		"phase": "Phase 3",
		"item": "load fill16 channel ramps",
		"status": "planned",
		"targets": ["rgbw_lut_builder/response/fill16_ramps.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py", "rgbw_lut_builder/captures/loaders.py"],
		"patterns": [r"_channel_y_fraction_from_drive", r"_channel_xy_from_drive", r"load_captures"],
		"math_refs": [],
	},
	{
		"phase": "Phase 3",
		"item": "load hardcoded fallback ramps",
		"status": "planned",
		"targets": ["rgbw_lut_builder/response/hardcoded_ramps.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"MAX_Y", r"PRIMARY_XYZ", r"_channel_y_curve_strict"],
		"math_refs": [],
	},
	{
		"phase": "Phase 3",
		"item": "add TemporalBFI dense response backend with chunked/indexed lookup",
		"status": "planned",
		"targets": ["rgbw_lut_builder/response/temporal_bfi.py"],
		"sources": ["tools/convert_temporal_bfi_dataset.py", "FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py"],
		"patterns": [r".*"],
		"math_refs": [],
	},
	{
		"phase": "Phase 3",
		"item": "add HybridResponseProvider source precedence",
		"status": "planned",
		"targets": ["rgbw_lut_builder/response/hybrid.py"],
		"sources": ["rgbw_lut_builder/response/base.py", "rgbw_lut_builder/response/fill16_ramps.py", "rgbw_lut_builder/response/hardcoded_ramps.py", "rgbw_lut_builder/response/temporal_bfi.py"],
		"patterns": [],
		"math_refs": [],
	},
	{
		"phase": "Phase 4",
		"item": "load patch captures",
		"status": "planned",
		"targets": ["rgbw_lut_builder/verify/verifier.py", "rgbw_lut_builder/captures/loaders.py"],
		"sources": ["rgbw_lut_builder/captures/loaders.py", "rgbw_lut_builder/gui/analyze_rgbw_captures.py"],
		"patterns": [r"load_captures", r"build_family_capture_sets"],
		"math_refs": [math_ref("Capture-cloud simplex correction", "#12-capture-cloud-simplex-correction")],
	},
	{
		"phase": "Phase 4",
		"item": "compute model prediction for each capture",
		"status": "planned",
		"targets": ["rgbw_lut_builder/verify/verifier.py", "rgbw_lut_builder/build/model_only.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py", "rgbw_lut_builder/build/model_only.py"],
		"patterns": [r"verify_captures", r"_predict_xyz_from_rgbw16", r"solve_model_node"],
		"math_refs": [
			math_ref("RGB-only model", "#5-rgb-only-model"),
			math_ref("Strict RGBW sub-gamut model", "#6-strict-rgbw-sub-gamut-model"),
			math_ref("WX family", "#7-wx--white-overdrive-model-family"),
		],
	},
	{
		"phase": "Phase 4",
		"item": "compare strict_subgamut, wx_radial_virtual, wx_virtual_axis_maxbright, and wx_lp_legacy residuals",
		"status": "planned",
		"targets": ["rgbw_lut_builder/verify/metrics.py", "rgbw_lut_builder/verify/reports.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"verify_captures", r"rgb_to_rgbw_wx.*", r"rgb_to_rgbw_subgamut"],
		"math_refs": [
			math_ref("Strict RGBW sub-gamut model", "#6-strict-rgbw-sub-gamut-model"),
			math_ref("Preferred wx_radial_virtual", "#9-preferred-wx-mode-wx_radial_virtual"),
			math_ref("Reference wx_lp_legacy", "#10-reference-wx-mode-wx_lp_legacy"),
			math_ref("Max-brightness wx_virtual_axis_maxbright", "#11-max-brightness-wx-mode-wx_virtual_axis_maxbright"),
		],
	},
	{
		"phase": "Phase 4",
		"item": "write model_vs_capture_report.csv",
		"status": "planned",
		"targets": ["rgbw_lut_builder/verify/reports.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py", "rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"verify_captures", r"write_verifier_failure_dictionary", r"write_.*csv"],
		"math_refs": [],
	},
	{
		"phase": "Phase 4",
		"item": "separate results by gamut, transfer, output family, topology, and Y bucket",
		"status": "planned",
		"targets": ["rgbw_lut_builder/verify/metrics.py", "rgbw_lut_builder/verify/reports.py"],
		"sources": ["rgbw_lut_builder/build/model_only.py", "rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"summary", r"write_verifier_failure_dictionary"],
		"math_refs": [],
	},
	{
		"phase": "Phase 4",
		"item": "reuse existing verifier/pass-fail dictionary structure",
		"status": "planned",
		"targets": ["rgbw_lut_builder/verify/reports.py", "rgbw_lut_builder/correction/pass_fail_dictionary.py"],
		"sources": ["rgbw_lut_builder/correction/pass_fail_dictionary.py", "rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"write_verifier_feedback_bank", r"load_feedback_candidate_model_for_args", r"_parse_verifier_feedback_rows"],
		"math_refs": [math_ref("Capture-cloud simplex correction", "#12-capture-cloud-simplex-correction")],
	},
	{
		"phase": "Phase 5",
		"item": "implement slightly-expanded virtual reference hull generation",
		"status": "planned",
		"targets": ["rgbw_lut_builder/model/projection.py", "rgbw_lut_builder/model/emitter_classification.py"],
		"sources": [],
		"patterns": [],
		"math_refs": [math_ref("Profile-space virtual reference hull", "#4-profile-space-virtual-reference-hull")],
	},
	{
		"phase": "Phase 5",
		"item": "project/remap measured emitters into stored virtual emitter profiles",
		"status": "planned",
		"targets": ["rgbw_lut_builder/model/projection.py", "rgbw_lut_builder/response/multi_emitter_profile.py"],
		"sources": [],
		"patterns": [],
		"math_refs": [
			math_ref("Physical and virtual emitter records", "#physical-and-virtual-emitter-records"),
			math_ref("Solve using virtual geometry, expand through physical channels", "#solve-using-virtual-geometry-expand-through-physical-channels"),
		],
	},
	{
		"phase": "Phase 5",
		"item": "separate solver geometry coordinates from physical output channel tuples",
		"status": "planned",
		"targets": ["rgbw_lut_builder/model/simplex.py", "rgbw_lut_builder/response/multi_emitter_profile.py"],
		"sources": [],
		"patterns": [],
		"math_refs": [math_ref("Solve using virtual geometry, expand through physical channels", "#solve-using-virtual-geometry-expand-through-physical-channels")],
	},
	{
		"phase": "Phase 5",
		"item": "add active-channel-family grouping to verifier reports",
		"status": "planned",
		"targets": ["rgbw_lut_builder/verify/reports.py"],
		"sources": ["rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"write_verifier_failure_dictionary", r"_feedback_.*"],
		"math_refs": [math_ref("Correction response profiles and observed response curves", "#correction-response-profiles-and-observed-response-curves")],
	},
	{
		"phase": "Phase 5",
		"item": "aggregate pass/fail records into CorrectionResponseProfile artifacts",
		"status": "planned",
		"targets": ["rgbw_lut_builder/response/multi_emitter_profile.py", "rgbw_lut_builder/correction/residuals.py"],
		"sources": ["rgbw_lut_builder/correction/pass_fail_dictionary.py", "rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"write_verifier_feedback_bank", r"_build_feedback_entry_stats", r"_legacy_feedback_observations"],
		"math_refs": [math_ref("Correction response profiles and observed response curves", "#correction-response-profiles-and-observed-response-curves")],
	},
	{
		"phase": "Phase 5",
		"item": "fit simple ObservedResponseCurve summaries for W/no-W edge comparisons",
		"status": "planned",
		"targets": ["rgbw_lut_builder/correction/residuals.py", "rgbw_lut_builder/response/multi_emitter_profile.py"],
		"sources": ["rgbw_lut_builder/correction/pass_fail_dictionary.py"],
		"patterns": [r"write_verifier_feedback_bank", r"load_feedback_candidate_model_for_args"],
		"math_refs": [math_ref("Correction response profiles and observed response curves", "#correction-response-profiles-and-observed-response-curves")],
	},
	{
		"phase": "Phase 5",
		"item": "use learned response direction to bias correction candidates before live probing",
		"status": "planned",
		"targets": ["rgbw_lut_builder/correction/triangle_ranker.py", "rgbw_lut_builder/correction/live_retry.py"],
		"sources": ["rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"_resolve_target_match_candidates", r"_feedback_capture_delta", r"_merge_feedback_observation"],
		"math_refs": [math_ref("Capture-cloud simplex correction", "#12-capture-cloud-simplex-correction")],
	},
	{
		"phase": "Phase 5",
		"item": "write diagnostics showing where virtual expansion helps, hurts, or remains uncertain",
		"status": "planned",
		"targets": ["rgbw_lut_builder/verify/reports.py", "rgbw_lut_builder/verify/metrics.py"],
		"sources": ["rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py", "rgbw_lut_builder/gui/analyze_rgbw_captures.py"],
		"patterns": [r"write_verifier_failure_dictionary", r"summarize"],
		"math_refs": [math_ref("Why this helps edge colors", "#why-this-helps-edge-colors")],
	},
	{
		"phase": "Phase 6",
		"item": "load emitter profiles with arbitrary channel counts",
		"status": "planned",
		"targets": ["rgbw_lut_builder/response/multi_emitter_profile.py", "rgbw_lut_builder/model/emitter_classification.py"],
		"sources": [],
		"patterns": [],
		"math_refs": [math_ref("Multi-emitter layered simplex model", "#13-multi-emitter-layered-simplex-model")],
	},
	{
		"phase": "Phase 6",
		"item": "classify emitters by measured chromaticity relative to the device hull",
		"status": "planned",
		"targets": ["rgbw_lut_builder/model/emitter_classification.py"],
		"sources": [],
		"patterns": [],
		"math_refs": [math_ref("Emitter classification", "#emitter-classification")],
	},
	{
		"phase": "Phase 6",
		"item": "build outer-hull triangle fans for each inner anchor",
		"status": "planned",
		"targets": ["rgbw_lut_builder/model/layered_simplex.py", "rgbw_lut_builder/model/simplex.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"_find_sub_gamut", r"_solve_xyz"],
		"math_refs": [math_ref("General algorithm", "#general-algorithm")],
	},
	{
		"phase": "Phase 6",
		"item": "solve RGBCCT-style warm/cool inner-anchor layers",
		"status": "planned",
		"targets": ["rgbw_lut_builder/model/layered_simplex.py"],
		"sources": [],
		"patterns": [],
		"math_refs": [math_ref("RGBCCT / warm-cool inner-anchor model", "#rgbcct--warm-cool-inner-anchor-model")],
	},
	{
		"phase": "Phase 6",
		"item": "solve RGBY/RGBV-style outer-hull-expanded packages",
		"status": "planned",
		"targets": ["rgbw_lut_builder/model/layered_simplex.py"],
		"sources": [],
		"patterns": [],
		"math_refs": [math_ref("RGBY / RGBV / outer-hull expansion", "#rgby--rgbv--outer-hull-expansion")],
	},
	{
		"phase": "Phase 6",
		"item": "share known-point / simplex expansion logic with capture-cloud correction",
		"status": "planned",
		"targets": ["rgbw_lut_builder/model/simplex.py", "rgbw_lut_builder/correction/measured_simplex.py"],
		"sources": ["rgbw_lut_builder/legacy/xy_target_rgbw_model.py", "rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"_solve_xyz", r"_nnls_solve", r"_resolve_target_match_candidates"],
		"math_refs": [
			math_ref("Common simplex solve", "#2-common-simplex-solve"),
			math_ref("Capture-cloud simplex correction", "#12-capture-cloud-simplex-correction"),
		],
	},
	{
		"phase": "Phase 6",
		"item": "write diagnostics for hull classification, ambiguous edge emitters, and inner-anchor blends",
		"status": "planned",
		"targets": ["rgbw_lut_builder/verify/reports.py", "rgbw_lut_builder/verify/metrics.py"],
		"sources": ["rgbw_lut_builder/gui/analyze_rgbw_captures.py"],
		"patterns": [r".*"],
		"math_refs": [math_ref("Emitter classification", "#emitter-classification")],
	},
	{
		"phase": "Phase 6",
		"item": "add degenerate inner-anchor line fallback for overdrive prediction models",
		"status": "planned",
		"targets": ["rgbw_lut_builder/model/layered_simplex.py", "rgbw_lut_builder/model/simplex.py"],
		"sources": [],
		"patterns": [],
		"math_refs": [math_ref("Degenerate inner-anchor line fallback", "#degenerate-inner-anchor-line-fallback")],
	},
	{
		"phase": "Phase 6",
		"item": "ensure strict sub-gamut mode continues to solve only direct legal edge/hull pairs",
		"status": "planned",
		"targets": ["rgbw_lut_builder/model/topology.py", "rgbw_lut_builder/model/layered_simplex.py"],
		"sources": ["rgbw_lut_builder/model/topology.py", "rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"_find_sub_gamut", r"_solve_subgamut_fraction_from_linear"],
		"math_refs": [math_ref("Strict RGBW sub-gamut model", "#6-strict-rgbw-sub-gamut-model")],
	},
	{
		"phase": "Phase 7",
		"item": "fit conservative residual correction maps",
		"status": "planned",
		"targets": ["rgbw_lut_builder/correction/correction_field.py", "rgbw_lut_builder/correction/residuals.py"],
		"sources": ["rgbw_lut_builder/gui/analyze_rgbw_captures.py", "rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r".*residual.*", r"_feedback_.*"],
		"math_refs": [math_ref("Capture-cloud simplex correction", "#12-capture-cloud-simplex-correction")],
	},
	{
		"phase": "Phase 7",
		"item": "build/rank local measured triangle/simplex candidates",
		"status": "planned",
		"targets": ["rgbw_lut_builder/correction/measured_simplex.py", "rgbw_lut_builder/correction/triangle_ranker.py"],
		"sources": ["rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"_resolve_target_match_candidates", r"_parse_target_match_row", r"_resolve_best_capture_xyz"],
		"math_refs": [
			math_ref("Common simplex solve", "#2-common-simplex-solve"),
			math_ref("Capture-cloud simplex correction", "#12-capture-cloud-simplex-correction"),
		],
	},
	{
		"phase": "Phase 7",
		"item": "apply corrections to model candidates",
		"status": "planned",
		"targets": ["rgbw_lut_builder/correction/correction_field.py", "rgbw_lut_builder/correction/residuals.py", "rgbw_lut_builder/build/offline_measured.py"],
		"sources": ["rgbw_lut_builder/build/model_only.py", "rgbw_lut_builder/gui/build_measured_rgbw_lut.py"],
		"patterns": [r"build_model_lut_cube", r"summarize"],
		"math_refs": [math_ref("Capture-cloud simplex correction", "#12-capture-cloud-simplex-correction")],
	},
	{
		"phase": "Phase 7",
		"item": "use pass/fail dictionary as final override/block",
		"status": "planned",
		"targets": ["rgbw_lut_builder/correction/pass_fail_dictionary.py", "rgbw_lut_builder/correction/correction_field.py"],
		"sources": ["rgbw_lut_builder/correction/pass_fail_dictionary.py"],
		"patterns": [r"write_verifier_feedback_bank", r"load_feedback_candidate_model_for_args"],
		"math_refs": [math_ref("Correction response profiles and observed response curves", "#correction-response-profiles-and-observed-response-curves")],
	},
	{
		"phase": "Phase 7",
		"item": "write before/after diagnostics",
		"status": "planned",
		"targets": ["rgbw_lut_builder/verify/reports.py", "rgbw_lut_builder/verify/metrics.py"],
		"sources": ["rgbw_lut_builder/gui/analyze_rgbw_captures.py", "rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"summarize", r"write_verifier_failure_dictionary"],
		"math_refs": [],
	},
	{
		"phase": "Phase 8",
		"item": "builder sends capture requests to host_calibration_gui",
		"status": "planned",
		"targets": ["rgbw_lut_builder/captures/udp_client.py", "rgbw_lut_builder/build/live_measured.py"],
		"sources": ["tools/run_live_capture.py", "rgbw_lut_builder/captures/udp_client.py", "FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py"],
		"patterns": [r".*"],
		"math_refs": [],
	},
	{
		"phase": "Phase 8",
		"item": "receive full spotread measurement payloads",
		"status": "planned",
		"targets": ["rgbw_lut_builder/captures/spotread_protocol.py", "rgbw_lut_builder/build/live_measured.py"],
		"sources": ["rgbw_lut_builder/captures/spotread_protocol.py", "FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py"],
		"patterns": [r".*"],
		"math_refs": [],
	},
	{
		"phase": "Phase 8",
		"item": "update pass/fail dictionary during calibration",
		"status": "planned",
		"targets": ["rgbw_lut_builder/correction/pass_fail_dictionary.py", "rgbw_lut_builder/correction/live_retry.py"],
		"sources": ["rgbw_lut_builder/correction/pass_fail_dictionary.py", "rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r"write_verifier_feedback_bank", r"_merge_feedback_observation"],
		"math_refs": [math_ref("Capture-cloud simplex correction", "#12-capture-cloud-simplex-correction")],
	},
	{
		"phase": "Phase 8",
		"item": "retry candidate corrections until pass or retry budget exhausted",
		"status": "planned",
		"targets": ["rgbw_lut_builder/correction/live_retry.py", "rgbw_lut_builder/build/live_measured.py"],
		"sources": ["tools/run_live_capture.py", "rgbw_lut_builder/gui/prototype_measured_white_solver.py"],
		"patterns": [r".*retry.*", r".*probe.*"],
		"math_refs": [math_ref("Capture-cloud simplex correction", "#12-capture-cloud-simplex-correction")],
	},
	{
		"phase": "Phase 8",
		"item": "save live_capture_session.jsonl and live_retry_trace.csv",
		"status": "planned",
		"targets": ["rgbw_lut_builder/build/live_measured.py", "rgbw_lut_builder/verify/reports.py"],
		"sources": ["tools/run_live_capture.py", "rgbw_lut_builder/gui/prototype_measured_white_solver.py"],
		"patterns": [r".*trace.*", r".*session.*"],
		"math_refs": [],
	},
	{
		"phase": "Phase 9",
		"item": "RGB8 / RGB16",
		"status": "active",
		"targets": ["rgbw_lut_builder/output/rgb8.py", "rgbw_lut_builder/output/rgb16.py"],
		"sources": ["rgbw_lut_builder/build/model_only.py", "rgbw_lut_builder/build/lut_writer.py"],
		"patterns": [r"build_model_lut_cube", r"save_lut_npy"],
		"math_refs": [],
	},
	{
		"phase": "Phase 9",
		"item": "RGBW8 / RGBW16",
		"status": "active",
		"targets": ["rgbw_lut_builder/output/rgbw8.py", "rgbw_lut_builder/output/rgbw16.py"],
		"sources": ["rgbw_lut_builder/build/model_only.py", "rgbw_lut_builder/build/lut_writer.py"],
		"patterns": [r"build_model_lut_cube", r"write_rgbw_header", r"save_lut_npy"],
		"math_refs": [],
	},
	{
		"phase": "Phase 9",
		"item": "generic channels16 outputs",
		"status": "planned",
		"targets": ["rgbw_lut_builder/output/channels16.py"],
		"sources": ["rgbw_lut_builder/build/lut_writer.py"],
		"patterns": [r"write_rgbw_header", r"save_lut_npy"],
		"math_refs": [],
	},
	{
		"phase": "Phase 9",
		"item": "TemporalBFI encoder",
		"status": "planned",
		"targets": ["rgbw_lut_builder/output/temporal_bfi_encoder.py"],
		"sources": ["tools/convert_temporal_bfi_dataset.py"],
		"patterns": [r".*"],
		"math_refs": [],
	},
	{
		"phase": "Phase 9",
		"item": "APA102 encoder",
		"status": "planned",
		"targets": ["rgbw_lut_builder/output/apa102_encoder.py"],
		"sources": [],
		"patterns": [],
		"math_refs": [],
	},
	{
		"phase": "Phase 9",
		"item": "HD108 encoder",
		"status": "planned",
		"targets": ["rgbw_lut_builder/output/hd108_encoder.py"],
		"sources": [],
		"patterns": [],
		"math_refs": [],
	},
	{
		"phase": "Phase 9",
		"item": "HyperHDR export",
		"status": "planned",
		"targets": ["rgbw_lut_builder/output/hyperhdr_export.py"],
		"sources": ["rgbw_lut_builder/gui/rgbw_lut_gui.py"],
		"patterns": [r".*hyperhdr.*"],
		"math_refs": [],
	},
	{
		"phase": "Phase 9",
		"item": "C header export",
		"status": "active",
		"targets": ["rgbw_lut_builder/output/c_header_export.py", "rgbw_lut_builder/output/mcu_header_export.py"],
		"sources": ["rgbw_lut_builder/build/lut_writer.py", "rgbw_lut_builder/legacy/xy_target_rgbw_model.py"],
		"patterns": [r"write_rgbw_header", r"write_lut_header"],
		"math_refs": [],
	},
	{
		"phase": "Phase 9",
		"item": "binary cube export",
		"status": "active",
		"targets": ["rgbw_lut_builder/output/binary_cube_export.py"],
		"sources": ["rgbw_lut_builder/build/lut_writer.py"],
		"patterns": [r"save_lut_npy"],
		"math_refs": [],
	},
	{
		"phase": "Phase 9",
		"item": "coefficient tetrahedral cube export",
		"status": "planned",
		"targets": ["rgbw_lut_builder/output/coefficient_cube_export.py"],
		"sources": ["rgbw_lut_builder/output/binary_cube_export.py", "rgbw_lut_builder/runtime/"],
		"patterns": [],
		"math_refs": [math_ref("Tetrahedral LUT interpolation", "#14-tetrahedral-lut-interpolation")],
	},
	{
		"phase": "Phase 9",
		"item": "MCU/SBC size-report tooling for 8 / 16 / 32 MB PSRAM targets",
		"status": "planned",
		"targets": ["rgbw_lut_builder/output/mcu_header_export.py", "rgbw_lut_builder/output/coefficient_cube_export.py"],
		"sources": [],
		"patterns": [],
		"math_refs": [],
	},
	{
		"phase": "Phase 9",
		"item": "reference fixed-point tetrahedral samplers",
		"status": "planned",
		"targets": ["rgbw_lut_builder/runtime/tetra_sampler_c_reference.c", "rgbw_lut_builder/runtime/tetra_sampler_cpp.hpp", "rgbw_lut_builder/runtime/tetra_sampler_arduino.hpp"],
		"sources": ["rgbw_lut_builder/runtime/"],
		"patterns": [],
		"math_refs": [math_ref("Tetrahedral LUT interpolation", "#14-tetrahedral-lut-interpolation")],
	},
	{
		"phase": "Phase 10",
		"item": "use model confidence and correction uncertainty to choose new probes",
		"status": "planned",
		"targets": ["tools/generate_capture_plan.py", "rgbw_lut_builder/verify/metrics.py"],
		"sources": ["tools/generate_capture_plan.py", "rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r".*plan.*", r"_build_feedback_entry_stats"],
		"math_refs": [
			math_ref("Capture-cloud simplex correction", "#12-capture-cloud-simplex-correction"),
			math_ref("Correction response profiles and observed response curves", "#correction-response-profiles-and-observed-response-curves"),
		],
	},
	{
		"phase": "Phase 10",
		"item": "support sparse capture sets for normal users",
		"status": "planned",
		"targets": ["tools/generate_capture_plan.py"],
		"sources": ["tools/generate_capture_plan.py"],
		"patterns": [r".*plan.*"],
		"math_refs": [],
	},
	{
		"phase": "Phase 10",
		"item": "support dense research datasets for advanced calibration",
		"status": "planned",
		"targets": ["tools/generate_capture_plan.py", "rgbw_lut_builder/response/temporal_bfi.py"],
		"sources": ["tools/generate_capture_plan.py", "tools/convert_temporal_bfi_dataset.py"],
		"patterns": [r".*plan.*"],
		"math_refs": [],
	},
	{
		"phase": "Phase 10",
		"item": "stop capturing once each region has enough support",
		"status": "planned",
		"targets": ["tools/generate_capture_plan.py", "rgbw_lut_builder/verify/metrics.py"],
		"sources": ["tools/generate_capture_plan.py", "rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py"],
		"patterns": [r".*plan.*", r"_build_feedback_entry_stats"],
		"math_refs": [math_ref("Capture-cloud simplex correction", "#12-capture-cloud-simplex-correction")],
	},
]


def normalize_path(path: Path) -> str:
	return path.relative_to(PROJECT_ROOT).as_posix()


def scan_python_functions() -> tuple[dict[str, list[FunctionInfo]], dict[str, int]]:
	module_index: dict[str, list[FunctionInfo]] = {}
	empty_modules: dict[str, int] = {}
	for root in SCAN_ROOTS:
		if not root.exists():
			continue
		for path in sorted(root.rglob("*.py")):
			if any(part in EXCLUDED_PARTS for part in path.parts):
				continue
			module_path = normalize_path(path)
			text = path.read_text(encoding="utf-8")
			if not text.strip():
				module_index[module_path] = []
				empty_modules[module_path] = 0
				continue
			try:
				tree = ast.parse(text, filename=str(path))
			except SyntaxError:
				module_index[module_path] = []
				continue
			functions: list[FunctionInfo] = []
			for node in tree.body:
				if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
					key = f"{module_path}:{node.name}"
					functions.append(
						FunctionInfo(
							module_path=module_path,
							name=node.name,
							lineno=node.lineno,
							end_lineno=getattr(node, "end_lineno", node.lineno),
							description=FUNCTION_DESCRIPTIONS.get(key, ""),
						)
					)
			module_index[module_path] = functions
	return module_index, empty_modules


def module_tree(module_index: dict[str, list[FunctionInfo]], empty_modules: dict[str, int]) -> dict[str, list[dict[str, object]]]:
	tree: dict[str, list[dict[str, object]]] = defaultdict(list)
	for module_path in sorted(module_index):
		root = module_path.split("/", 1)[0]
		tree[root].append(
			{
				"module_path": module_path,
				"function_count": len(module_index[module_path]),
				"is_placeholder": module_path in empty_modules,
			}
		)
	return dict(tree)


def find_candidate_functions(
	module_index: dict[str, list[FunctionInfo]],
	source_modules: list[str],
	patterns: list[str],
) -> list[FunctionInfo]:
	results: list[FunctionInfo] = []
	seen: set[tuple[str, str]] = set()
	regexes = [re.compile(pattern, flags=re.IGNORECASE) for pattern in patterns if pattern]
	for module_path in source_modules:
		for function in module_index.get(module_path, []):
			if regexes and not any(regex.search(function.name) for regex in regexes):
				continue
			key = (function.module_path, function.name)
			if key in seen:
				continue
			seen.add(key)
			results.append(function)
	return sorted(results, key=lambda item: (item.module_path, item.lineno, item.name))


def render_module_tree_markdown(tree: dict[str, list[dict[str, object]]]) -> list[str]:
	lines = ["## Current module tree", ""]
	for root, entries in sorted(tree.items()):
		lines.append(f"### {root}")
		lines.append("")
		lines.append("| Module | Functions | State |")
		lines.append("| --- | ---: | --- |")
		for entry in entries:
			state = "placeholder" if entry["is_placeholder"] else "implemented"
			lines.append(
				f"| [{entry['module_path']}](../{entry['module_path']}) | {entry['function_count']} | {state} |"
			)
		lines.append("")
	return lines


def render_move_plan_markdown(planned_items: list[dict[str, object]]) -> list[str]:
	lines = ["## Roadmap move map", ""]
	current_phase = None
	for item in planned_items:
		phase = str(item["phase"])
		if phase != current_phase:
			if current_phase is not None:
				lines.append("")
			lines.append(f"### {phase}")
			lines.append("")
			current_phase = phase
		math_refs = item.get("math_refs", [])
		math_text = ", ".join(f"[{ref['label']}]({ref['href']})" for ref in math_refs) if math_refs else "none"
		target_text = "<br>".join(f"[{path}](../{path})" for path in item.get("targets", [])) or "none"
		source_text = "<br>".join(f"[{path}](../{path})" for path in item.get("sources", [])) or "none"
		lines.append(f"#### {item['item']}")
		lines.append("")
		lines.append(f"- Status: `{item['status']}`")
		lines.append(f"- Target modules: {target_text}")
		lines.append(f"- Current source surfaces: {source_text}")
		lines.append(f"- Math / prototype refs: {math_text}")
		candidates: list[FunctionInfo] = item.get("candidate_functions", [])  # type: ignore[assignment]
		if candidates:
			lines.append("- Candidate functions:")
			for function in candidates:
				description = f" - {function.description}" if function.description else ""
				lines.append(
					f"  - [{function.module_path}:{function.name}](../{function.module_path}#L{function.lineno}){description}"
				)
		else:
			lines.append("- Candidate functions: none pinned yet")
		lines.append("")
	return lines


def build_inventory() -> dict[str, object]:
	module_index, empty_modules = scan_python_functions()
	tree = module_tree(module_index, empty_modules)
	move_items: list[dict[str, object]] = []
	for item in MOVE_PLAN:
		move_item = dict(item)
		move_item["candidate_functions"] = [
			asdict(candidate)
			for candidate in find_candidate_functions(
				module_index,
				list(move_item.get("sources", [])),
				list(move_item.get("patterns", [])),
			)
		]
		move_items.append(move_item)
	return {
		"generated_by": "python tools/generate_function_tree.py",
		"module_tree": tree,
		"move_plan": move_items,
	}


def write_outputs(inventory: dict[str, object]) -> None:
	DOCS_DIR.mkdir(parents=True, exist_ok=True)
	JSON_OUTPUT.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
	markdown_lines = [
		"# Project Function Tree",
		"",
		"Generated from the current workspace by `python tools/generate_function_tree.py`.",
		"",
		"Use this file as the lookup layer behind the roadmap:",
		"",
		"- find the roadmap item",
		"- see the target package modules",
		"- inspect the current legacy or transitional source surfaces",
		"- jump to candidate functions that still need to move or be reused",
		"",
		f"JSON inventory: [{JSON_OUTPUT.relative_to(PROJECT_ROOT).as_posix()}](../{JSON_OUTPUT.relative_to(PROJECT_ROOT).as_posix()})",
		"",
	]
	markdown_lines.extend(render_module_tree_markdown(inventory["module_tree"]))
	move_items = []
	for item in inventory["move_plan"]:  # type: ignore[index]
		copy = dict(item)
		copy["candidate_functions"] = [FunctionInfo(**candidate) for candidate in item["candidate_functions"]]
		move_items.append(copy)
	markdown_lines.extend(render_move_plan_markdown(move_items))
	MARKDOWN_OUTPUT.write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")


def main() -> None:
	inventory = build_inventory()
	write_outputs(inventory)
	print(f"wrote {MARKDOWN_OUTPUT.relative_to(PROJECT_ROOT)}")
	print(f"wrote {JSON_OUTPUT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
	main()