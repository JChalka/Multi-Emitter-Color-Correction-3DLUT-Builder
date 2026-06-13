# Project Function Tree

Generated from the current workspace by `python tools/generate_function_tree.py`.

Use this file as the lookup layer behind the roadmap:

- find the roadmap item
- see the target package modules
- inspect the current legacy or transitional source surfaces
- jump to candidate functions that still need to move or be reused

JSON inventory: [docs/project_function_tree.json](../docs/project_function_tree.json)

## Current module tree

### FILES_FOR_TRANSITION

| Module | Functions | State |
| --- | ---: | --- |
| [FILES_FOR_TRANSITION/analyze_rgbw_captures.py](../FILES_FOR_TRANSITION/analyze_rgbw_captures.py) | 18 | implemented |
| [FILES_FOR_TRANSITION/build_delaunay_rgbw_lut.py](../FILES_FOR_TRANSITION/build_delaunay_rgbw_lut.py) | 97 | implemented |
| [FILES_FOR_TRANSITION/build_measured_rgbw_lut.py](../FILES_FOR_TRANSITION/build_measured_rgbw_lut.py) | 19 | implemented |
| [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py) | 21 | implemented |
| [FILES_FOR_TRANSITION/prototype_measured_white_solver.py](../FILES_FOR_TRANSITION/prototype_measured_white_solver.py) | 27 | implemented |
| [FILES_FOR_TRANSITION/rgbw_lut_gui.py](../FILES_FOR_TRANSITION/rgbw_lut_gui.py) | 9 | implemented |
| [FILES_FOR_TRANSITION/wx_virtual_primary_gui.py](../FILES_FOR_TRANSITION/wx_virtual_primary_gui.py) | 12 | implemented |
| [FILES_FOR_TRANSITION/xy_target_rgbw_model.py](../FILES_FOR_TRANSITION/xy_target_rgbw_model.py) | 102 | implemented |

### rgbw_lut_builder

| Module | Functions | State |
| --- | ---: | --- |
| [rgbw_lut_builder/__init__.py](../rgbw_lut_builder/__init__.py) | 0 | implemented |
| [rgbw_lut_builder/captures/loaders.py](../rgbw_lut_builder/captures/loaders.py) | 2 | implemented |
| [rgbw_lut_builder/captures/schemas.py](../rgbw_lut_builder/captures/schemas.py) | 0 | placeholder |
| [rgbw_lut_builder/captures/spotread_protocol.py](../rgbw_lut_builder/captures/spotread_protocol.py) | 0 | placeholder |
| [rgbw_lut_builder/captures/udp_client.py](../rgbw_lut_builder/captures/udp_client.py) | 0 | placeholder |
| [rgbw_lut_builder/captures/validators.py](../rgbw_lut_builder/captures/validators.py) | 3 | implemented |
| [rgbw_lut_builder/correction/correction_field.py](../rgbw_lut_builder/correction/correction_field.py) | 0 | placeholder |
| [rgbw_lut_builder/correction/live_retry.py](../rgbw_lut_builder/correction/live_retry.py) | 0 | placeholder |
| [rgbw_lut_builder/correction/measured_simplex.py](../rgbw_lut_builder/correction/measured_simplex.py) | 0 | placeholder |
| [rgbw_lut_builder/correction/multi_emitter_correction.py](../rgbw_lut_builder/correction/multi_emitter_correction.py) | 0 | placeholder |
| [rgbw_lut_builder/correction/pass_fail_dictionary.py](../rgbw_lut_builder/correction/pass_fail_dictionary.py) | 9 | implemented |
| [rgbw_lut_builder/correction/residuals.py](../rgbw_lut_builder/correction/residuals.py) | 0 | placeholder |
| [rgbw_lut_builder/correction/triangle_ranker.py](../rgbw_lut_builder/correction/triangle_ranker.py) | 0 | placeholder |
| [rgbw_lut_builder/gui/__init__.py](../rgbw_lut_builder/gui/__init__.py) | 0 | implemented |
| [rgbw_lut_builder/gui/analyze_rgbw_captures.py](../rgbw_lut_builder/gui/analyze_rgbw_captures.py) | 18 | implemented |
| [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py) | 91 | implemented |
| [rgbw_lut_builder/gui/build_measured_rgbw_lut.py](../rgbw_lut_builder/gui/build_measured_rgbw_lut.py) | 19 | implemented |
| [rgbw_lut_builder/gui/prototype_measured_white_solver.py](../rgbw_lut_builder/gui/prototype_measured_white_solver.py) | 27 | implemented |
| [rgbw_lut_builder/gui/rgbw_lut_gui.py](../rgbw_lut_builder/gui/rgbw_lut_gui.py) | 9 | implemented |
| [rgbw_lut_builder/legacy/__init__.py](../rgbw_lut_builder/legacy/__init__.py) | 0 | implemented |
| [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py) | 102 | implemented |
| [rgbw_lut_builder/model/__init__.py](../rgbw_lut_builder/model/__init__.py) | 0 | implemented |
| [rgbw_lut_builder/model/emitter_classification.py](../rgbw_lut_builder/model/emitter_classification.py) | 0 | placeholder |
| [rgbw_lut_builder/model/gamuts.py](../rgbw_lut_builder/model/gamuts.py) | 11 | implemented |
| [rgbw_lut_builder/model/interpolation/__init__.py](../rgbw_lut_builder/model/interpolation/__init__.py) | 0 | placeholder |
| [rgbw_lut_builder/model/interpolation/fixed_point.py](../rgbw_lut_builder/model/interpolation/fixed_point.py) | 0 | placeholder |
| [rgbw_lut_builder/model/interpolation/tetra_coefficients.py](../rgbw_lut_builder/model/interpolation/tetra_coefficients.py) | 0 | placeholder |
| [rgbw_lut_builder/model/interpolation/tetrahedral.py](../rgbw_lut_builder/model/interpolation/tetrahedral.py) | 0 | placeholder |
| [rgbw_lut_builder/model/layered_simplex.py](../rgbw_lut_builder/model/layered_simplex.py) | 0 | placeholder |
| [rgbw_lut_builder/model/projection.py](../rgbw_lut_builder/model/projection.py) | 1 | implemented |
| [rgbw_lut_builder/model/rgb_model.py](../rgbw_lut_builder/model/rgb_model.py) | 2 | implemented |
| [rgbw_lut_builder/model/rgbw_model.py](../rgbw_lut_builder/model/rgbw_model.py) | 4 | implemented |
| [rgbw_lut_builder/model/simplex.py](../rgbw_lut_builder/model/simplex.py) | 6 | implemented |
| [rgbw_lut_builder/model/topology.py](../rgbw_lut_builder/model/topology.py) | 4 | implemented |
| [rgbw_lut_builder/model/wx_modes.py](../rgbw_lut_builder/model/wx_modes.py) | 3 | implemented |
| [rgbw_lut_builder/output/1d_greyscale_export.py](../rgbw_lut_builder/output/1d_greyscale_export.py) | 0 | placeholder |
| [rgbw_lut_builder/output/apa102_encoder.py](../rgbw_lut_builder/output/apa102_encoder.py) | 0 | placeholder |
| [rgbw_lut_builder/output/binary_cube_export.py](../rgbw_lut_builder/output/binary_cube_export.py) | 0 | placeholder |
| [rgbw_lut_builder/output/c_header_export.py](../rgbw_lut_builder/output/c_header_export.py) | 0 | placeholder |
| [rgbw_lut_builder/output/channels16.py](../rgbw_lut_builder/output/channels16.py) | 0 | placeholder |
| [rgbw_lut_builder/output/coefficient_cube_export.py](../rgbw_lut_builder/output/coefficient_cube_export.py) | 0 | placeholder |
| [rgbw_lut_builder/output/hd108_encoder.py](../rgbw_lut_builder/output/hd108_encoder.py) | 0 | placeholder |
| [rgbw_lut_builder/output/hyperhdr_export.py](../rgbw_lut_builder/output/hyperhdr_export.py) | 0 | placeholder |
| [rgbw_lut_builder/output/mcu_header_export.py](../rgbw_lut_builder/output/mcu_header_export.py) | 0 | placeholder |
| [rgbw_lut_builder/output/rgb16.py](../rgbw_lut_builder/output/rgb16.py) | 0 | placeholder |
| [rgbw_lut_builder/output/rgb8.py](../rgbw_lut_builder/output/rgb8.py) | 0 | placeholder |
| [rgbw_lut_builder/output/rgbw16.py](../rgbw_lut_builder/output/rgbw16.py) | 0 | placeholder |
| [rgbw_lut_builder/output/rgbw8.py](../rgbw_lut_builder/output/rgbw8.py) | 0 | placeholder |
| [rgbw_lut_builder/output/temporal_bfi_encoder.py](../rgbw_lut_builder/output/temporal_bfi_encoder.py) | 0 | placeholder |
| [rgbw_lut_builder/paths.py](../rgbw_lut_builder/paths.py) | 0 | implemented |
| [rgbw_lut_builder/response/base.py](../rgbw_lut_builder/response/base.py) | 0 | placeholder |
| [rgbw_lut_builder/response/fill16_ramps.py](../rgbw_lut_builder/response/fill16_ramps.py) | 0 | placeholder |
| [rgbw_lut_builder/response/hardcoded_ramps.py](../rgbw_lut_builder/response/hardcoded_ramps.py) | 0 | placeholder |
| [rgbw_lut_builder/response/hybrid.py](../rgbw_lut_builder/response/hybrid.py) | 0 | placeholder |
| [rgbw_lut_builder/response/multi_emitter_profile.py](../rgbw_lut_builder/response/multi_emitter_profile.py) | 0 | placeholder |
| [rgbw_lut_builder/response/temporal_bfi.py](../rgbw_lut_builder/response/temporal_bfi.py) | 0 | placeholder |
| [rgbw_lut_builder/verify/metrics.py](../rgbw_lut_builder/verify/metrics.py) | 0 | placeholder |
| [rgbw_lut_builder/verify/reports.py](../rgbw_lut_builder/verify/reports.py) | 0 | placeholder |
| [rgbw_lut_builder/verify/verifier.py](../rgbw_lut_builder/verify/verifier.py) | 0 | placeholder |

### tools

| Module | Functions | State |
| --- | ---: | --- |
| [tools/build_lut.py](../tools/build_lut.py) | 2 | implemented |
| [tools/convert_temporal_bfi_dataset.py](../tools/convert_temporal_bfi_dataset.py) | 0 | placeholder |
| [tools/generate_capture_plan.py](../tools/generate_capture_plan.py) | 0 | placeholder |
| [tools/generate_function_tree.py](../tools/generate_function_tree.py) | 10 | implemented |
| [tools/install_dependencies.py](../tools/install_dependencies.py) | 0 | placeholder |
| [tools/run_live_capture.py](../tools/run_live_capture.py) | 0 | placeholder |
| [tools/verify_lut.py](../tools/verify_lut.py) | 0 | placeholder |

## Roadmap move map

### Phase 1

#### move rgbw_lut_gui into standalone repo

- Status: `done`
- Target modules: [rgbw_lut_builder/gui/rgbw_lut_gui.py](../rgbw_lut_builder/gui/rgbw_lut_gui.py)<br>[tools/build_lut.py](../tools/build_lut.py)
- Current source surfaces: [FILES_FOR_TRANSITION/rgbw_lut_gui.py](../FILES_FOR_TRANSITION/rgbw_lut_gui.py)
- Math / prototype refs: none
- Candidate functions:
  - [FILES_FOR_TRANSITION/rgbw_lut_gui.py:main](../FILES_FOR_TRANSITION/rgbw_lut_gui.py#L1906)

#### move reusable Delaunay/worker/memory/export utilities

- Status: `done`
- Target modules: [rgbw_lut_builder/build/diagnostics.py](../rgbw_lut_builder/build/diagnostics.py)<br>[rgbw_lut_builder/build/live_measured.py](../rgbw_lut_builder/build/live_measured.py)<br>[rgbw_lut_builder/build/lut_writer.py](../rgbw_lut_builder/build/lut_writer.py)<br>[rgbw_lut_builder/captures/loaders.py](../rgbw_lut_builder/captures/loaders.py)<br>[rgbw_lut_builder/correction/pass_fail_dictionary.py](../rgbw_lut_builder/correction/pass_fail_dictionary.py)
- Current source surfaces: [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: none
- Candidate functions:
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_available_memory_bytes](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L1395)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_worker_init](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L3921)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_probe_debug_csv](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4796)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_comparison_csv](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5400)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:load_or_create_display_profile](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5443)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_feedback_bank](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5988)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_failure_dictionary](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6016) - Legacy verifier failure aggregation/report writer.
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_utilization_csv](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6318)


### Phase 2

#### add explicit RGB-only model path

- Status: `done`
- Target modules: [rgbw_lut_builder/model/rgb_model.py](../rgbw_lut_builder/model/rgb_model.py)<br>[rgbw_lut_builder/build/model_only.py](../rgbw_lut_builder/build/model_only.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: [RGB-only model](../README_MATH_MODEL.md#5-rgb-only-model), [Out-of-hull projection](../README_MATH_MODEL.md#3-out-of-hull-projection)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:input_linear_to_XYZ](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L348) - Source-gamut RGB to LED-space XYZ transform.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_xyz](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L419) - Shared 3-emitter XYZ linear solve primitive.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_strict_project_target_xyz_to_led_hull](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2148) - Project out-of-hull targets back onto the reachable LED hull.

#### keep RGBW strict sub-gamut model path

- Status: `done`
- Target modules: [rgbw_lut_builder/model/topology.py](../rgbw_lut_builder/model/topology.py)<br>[rgbw_lut_builder/model/rgbw_model.py](../rgbw_lut_builder/model/rgbw_model.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: [Strict RGBW sub-gamut model](../README_MATH_MODEL.md#6-strict-rgbw-sub-gamut-model), [Common simplex solve](../README_MATH_MODEL.md#2-common-simplex-solve)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_find_sub_gamut](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L407) - Find the containing strict RGBW sub-gamut in xy space.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_xyz](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L419) - Shared 3-emitter XYZ linear solve primitive.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:rgb_to_rgbw_subgamut](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L495)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_subgamut_fraction_from_linear](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2820)

#### add explicit WX radial virtual-primary model path

- Status: `done`
- Target modules: [rgbw_lut_builder/model/wx_modes.py](../rgbw_lut_builder/model/wx_modes.py)<br>[rgbw_lut_builder/model/rgbw_model.py](../rgbw_lut_builder/model/rgbw_model.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: [WX family](../README_MATH_MODEL.md#7-wx--white-overdrive-model-family), [WX common structure](../README_MATH_MODEL.md#8-wx-common-virtual-primary-structure), [Preferred wx_radial_virtual](../README_MATH_MODEL.md#9-preferred-wx-mode-wx_radial_virtual)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_wx_radial_ccw_span](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2241)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_wx_radial_angle_from_w_to_xy](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2245)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_wx_radial_sector_for_subgamut](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2254)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_wx_radial_hull_point_for_sector_position](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2262)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_wx_radial_solve_virtual_primary_at_xy](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2302)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_wx_radial_virtual_primary_state](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2327)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_select_wx_radial_virtual_primary_set](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2412)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_wx_radial_virtual_fraction_for_xy](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2428)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_wx_radial_virtual_fraction_from_xyz](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2450) - Radial virtual-primary WX solve from target XYZ.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_wx_fraction_from_linear](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2862) - Dispatch WX solve from linear RGB into a constrained RGBW tuple.

#### keep LP max-white as wx_lp_legacy reference path

- Status: `done`
- Target modules: [rgbw_lut_builder/model/wx_modes.py](../rgbw_lut_builder/model/wx_modes.py)<br>[rgbw_lut_builder/model/rgbw_model.py](../rgbw_lut_builder/model/rgbw_model.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: [Reference wx_lp_legacy](../README_MATH_MODEL.md#10-reference-wx-mode-wx_lp_legacy)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:rgb_to_rgbw_wx_legacy](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L564)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_wx_lp_legacy_fraction_from_xyz](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2696) - Reference LP/max-white WX solve from target XYZ.

#### add wx_virtual_axis_maxbright as a first-class high-brightness WX path

- Status: `done`
- Target modules: [rgbw_lut_builder/model/wx_modes.py](../rgbw_lut_builder/model/wx_modes.py)<br>[rgbw_lut_builder/model/rgbw_model.py](../rgbw_lut_builder/model/rgbw_model.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: [Max-brightness wx_virtual_axis_maxbright](../README_MATH_MODEL.md#11-max-brightness-wx-mode-wx_virtual_axis_maxbright)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_wx_axis_candidates_for_subgamut](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2469)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_select_wx_virtual_axis_primary_set](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2543)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_select_wx_virtual_axis_primary_for_subgamut](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2614)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_wx_virtual_axis_maxbright_fraction_for_xy](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2660)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_wx_virtual_axis_maxbright_fraction_from_xyz](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2683) - Virtual-axis max-brightness WX solve from target XYZ.

#### share gamut transforms and hull projection

- Status: `done`
- Target modules: [rgbw_lut_builder/model/gamuts.py](../rgbw_lut_builder/model/gamuts.py)<br>[rgbw_lut_builder/model/projection.py](../rgbw_lut_builder/model/projection.py)<br>[rgbw_lut_builder/model/simplex.py](../rgbw_lut_builder/model/simplex.py)<br>[rgbw_lut_builder/model/topology.py](../rgbw_lut_builder/model/topology.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: [Source gamut conversion](../README_MATH_MODEL.md#source-gamut-conversion), [Common simplex solve](../README_MATH_MODEL.md#2-common-simplex-solve), [Out-of-hull projection](../README_MATH_MODEL.md#3-out-of-hull-projection)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:XYZ_to_xy](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L114)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_build_gamut_matrix](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L134)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:input_linear_to_XYZ](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L348) - Source-gamut RGB to LED-space XYZ transform.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_barycentric_2d](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L380)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_xy_in_triangle](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L398)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_strict_project_target_xyz_to_led_hull](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2148) - Project out-of-hull targets back onto the reachable LED hull.

#### share tetrahedral LUT sampling assumptions

- Status: `planned`
- Target modules: [rgbw_lut_builder/model/interpolation/](../rgbw_lut_builder/model/interpolation/)<br>[rgbw_lut_builder/output/coefficient_cube_export.py](../rgbw_lut_builder/output/coefficient_cube_export.py)<br>[rgbw_lut_builder/runtime/](../rgbw_lut_builder/runtime/)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)<br>[rgbw_lut_builder/runtime/](../rgbw_lut_builder/runtime/)
- Math / prototype refs: [Tetrahedral LUT interpolation](../README_MATH_MODEL.md#14-tetrahedral-lut-interpolation)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:write_lut_header](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L3467)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:build_rgbw_lut_cube](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L3701) - Legacy full cube builder for model-only RGBW LUTs.

#### add output-family metadata everywhere

- Status: `active`
- Target modules: [rgbw_lut_builder/build/model_only.py](../rgbw_lut_builder/build/model_only.py)<br>[rgbw_lut_builder/output/](../rgbw_lut_builder/output/)<br>[rgbw_lut_builder/verify/reports.py](../rgbw_lut_builder/verify/reports.py)
- Current source surfaces: [rgbw_lut_builder/build/model_only.py](../rgbw_lut_builder/build/model_only.py)<br>[rgbw_lut_builder/gui/build_measured_rgbw_lut.py](../rgbw_lut_builder/gui/build_measured_rgbw_lut.py)<br>[rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: none
- Candidate functions:
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_rgbw_header](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6330)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:save_lut_npy](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6340)
  - [rgbw_lut_builder/gui/build_measured_rgbw_lut.py:write_rgbw_header](../rgbw_lut_builder/gui/build_measured_rgbw_lut.py#L311)


### Phase 3

#### implement ChannelResponseProvider API

- Status: `planned`
- Target modules: [rgbw_lut_builder/response/base.py](../rgbw_lut_builder/response/base.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: [Correction response profiles and observed response curves](../README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_y_fraction_from_drive](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1367) - Single-channel Y ramp lookup from drive level.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_drive_from_channel_y_fraction](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1383) - Inverse single-channel Y ramp lookup.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_decode_source_rgb16_to_linear](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1395)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_encode_linear_to_model_code](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1406)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_xy_from_drive](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1479) - Single-channel xy lookup from drive level.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_basis_xyz_for_fraction](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1491)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_xyz_curve](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1548) - Per-channel XYZ curve synthesized from measured ramps.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_y_abs_from_drive](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1576)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_drive_from_channel_y_abs](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1590)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_xyz_from_drive](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1632)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_refine_rgbw_fraction_with_channel_xy](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1754)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_y_curve_strict](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L3449)

#### load fill16 channel ramps

- Status: `planned`
- Target modules: [rgbw_lut_builder/response/fill16_ramps.py](../rgbw_lut_builder/response/fill16_ramps.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)<br>[rgbw_lut_builder/captures/loaders.py](../rgbw_lut_builder/captures/loaders.py)
- Math / prototype refs: none
- Candidate functions:
  - [rgbw_lut_builder/captures/loaders.py:load_captures](../rgbw_lut_builder/captures/loaders.py#L30) - Load and normalize measured patch capture rows.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_y_fraction_from_drive](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1367) - Single-channel Y ramp lookup from drive level.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_xy_from_drive](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L1479) - Single-channel xy lookup from drive level.

#### load hardcoded fallback ramps

- Status: `planned`
- Target modules: [rgbw_lut_builder/response/hardcoded_ramps.py](../rgbw_lut_builder/response/hardcoded_ramps.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: none
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_max_y_virtual_primary_for_subgamut](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2019)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_channel_y_curve_strict](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L3449)

#### add TemporalBFI dense response backend with chunked/indexed lookup

- Status: `planned`
- Target modules: [rgbw_lut_builder/response/temporal_bfi.py](../rgbw_lut_builder/response/temporal_bfi.py)
- Current source surfaces: [tools/convert_temporal_bfi_dataset.py](../tools/convert_temporal_bfi_dataset.py)<br>[FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py)
- Math / prototype refs: none
- Candidate functions:
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_spotread_command_with_one_shot](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L108)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_generate_verifier_patches](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L176)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_lut_axis_position](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L239)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_clamp_lut_result](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L253)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_trilinear_lut_lookup](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L259)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_tetrahedral_lut_lookup](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L287)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_lut_lookup](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L334)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_xy_to_xyz1_tuple](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L374)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_build_rgb_to_xyz_matrix](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L379)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_apply_transfer_normalized](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L396)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_expected_xy_for_named_gamut](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L416)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_xyz_to_xy_tuple](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L433)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_point_in_triangle_xy](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L442)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_closest_point_on_segment_xy](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L460)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_project_xy_to_hull](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L474)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_xyY_to_XYZ](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L509)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_nnls_small](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L519)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_compute_model_scale_k](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L554)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_model_project_xy_for_named_gamut](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L578)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_xy_chroma_de](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L642)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:main](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L2877)

#### add HybridResponseProvider source precedence

- Status: `planned`
- Target modules: [rgbw_lut_builder/response/hybrid.py](../rgbw_lut_builder/response/hybrid.py)
- Current source surfaces: [rgbw_lut_builder/response/base.py](../rgbw_lut_builder/response/base.py)<br>[rgbw_lut_builder/response/fill16_ramps.py](../rgbw_lut_builder/response/fill16_ramps.py)<br>[rgbw_lut_builder/response/hardcoded_ramps.py](../rgbw_lut_builder/response/hardcoded_ramps.py)<br>[rgbw_lut_builder/response/temporal_bfi.py](../rgbw_lut_builder/response/temporal_bfi.py)
- Math / prototype refs: none
- Candidate functions: none pinned yet


### Phase 4

#### load patch captures

- Status: `planned`
- Target modules: [rgbw_lut_builder/verify/verifier.py](../rgbw_lut_builder/verify/verifier.py)<br>[rgbw_lut_builder/captures/loaders.py](../rgbw_lut_builder/captures/loaders.py)
- Current source surfaces: [rgbw_lut_builder/captures/loaders.py](../rgbw_lut_builder/captures/loaders.py)<br>[rgbw_lut_builder/gui/analyze_rgbw_captures.py](../rgbw_lut_builder/gui/analyze_rgbw_captures.py)
- Math / prototype refs: [Capture-cloud simplex correction](../README_MATH_MODEL.md#12-capture-cloud-simplex-correction)
- Candidate functions:
  - [rgbw_lut_builder/captures/loaders.py:load_captures](../rgbw_lut_builder/captures/loaders.py#L30) - Load and normalize measured patch capture rows.
  - [rgbw_lut_builder/captures/loaders.py:build_family_capture_sets](../rgbw_lut_builder/captures/loaders.py#L83) - Group captures into family-specific lookup sets.

#### compute model prediction for each capture

- Status: `planned`
- Target modules: [rgbw_lut_builder/verify/verifier.py](../rgbw_lut_builder/verify/verifier.py)<br>[rgbw_lut_builder/build/model_only.py](../rgbw_lut_builder/build/model_only.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)<br>[rgbw_lut_builder/build/model_only.py](../rgbw_lut_builder/build/model_only.py)
- Math / prototype refs: [RGB-only model](../README_MATH_MODEL.md#5-rgb-only-model), [Strict RGBW sub-gamut model](../README_MATH_MODEL.md#6-strict-rgbw-sub-gamut-model), [WX family](../README_MATH_MODEL.md#7-wx--white-overdrive-model-family)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_predict_xyz_from_rgbw16](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L866)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:verify_captures](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L906) - Legacy model-vs-capture verification/report path.

#### compare strict_subgamut, wx_radial_virtual, wx_virtual_axis_maxbright, and wx_lp_legacy residuals

- Status: `planned`
- Target modules: [rgbw_lut_builder/verify/metrics.py](../rgbw_lut_builder/verify/metrics.py)<br>[rgbw_lut_builder/verify/reports.py](../rgbw_lut_builder/verify/reports.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: [Strict RGBW sub-gamut model](../README_MATH_MODEL.md#6-strict-rgbw-sub-gamut-model), [Preferred wx_radial_virtual](../README_MATH_MODEL.md#9-preferred-wx-mode-wx_radial_virtual), [Reference wx_lp_legacy](../README_MATH_MODEL.md#10-reference-wx-mode-wx_lp_legacy), [Max-brightness wx_virtual_axis_maxbright](../README_MATH_MODEL.md#11-max-brightness-wx-mode-wx_virtual_axis_maxbright)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:rgb_to_rgbw_subgamut](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L495)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:rgb_to_rgbw_wx_legacy](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L564)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:rgb_to_rgbw_wx](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L630)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:verify_captures](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L906) - Legacy model-vs-capture verification/report path.

#### write model_vs_capture_report.csv

- Status: `planned`
- Target modules: [rgbw_lut_builder/verify/reports.py](../rgbw_lut_builder/verify/reports.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)<br>[rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: none
- Candidate functions:
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_probe_debug_csv](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4796)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_comparison_csv](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5400)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_failure_dictionary](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6016) - Legacy verifier failure aggregation/report writer.
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_utilization_csv](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6318)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:write_csv](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L825)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:verify_captures](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L906) - Legacy model-vs-capture verification/report path.

#### separate results by gamut, transfer, output family, topology, and Y bucket

- Status: `planned`
- Target modules: [rgbw_lut_builder/verify/metrics.py](../rgbw_lut_builder/verify/metrics.py)<br>[rgbw_lut_builder/verify/reports.py](../rgbw_lut_builder/verify/reports.py)
- Current source surfaces: [rgbw_lut_builder/build/model_only.py](../rgbw_lut_builder/build/model_only.py)<br>[rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: none
- Candidate functions:
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_failure_dictionary](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6016) - Legacy verifier failure aggregation/report writer.

#### reuse existing verifier/pass-fail dictionary structure

- Status: `planned`
- Target modules: [rgbw_lut_builder/verify/reports.py](../rgbw_lut_builder/verify/reports.py)<br>[rgbw_lut_builder/correction/pass_fail_dictionary.py](../rgbw_lut_builder/correction/pass_fail_dictionary.py)
- Current source surfaces: [rgbw_lut_builder/correction/pass_fail_dictionary.py](../rgbw_lut_builder/correction/pass_fail_dictionary.py)<br>[rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: [Capture-cloud simplex correction](../README_MATH_MODEL.md#12-capture-cloud-simplex-correction)
- Candidate functions:
  - [rgbw_lut_builder/correction/pass_fail_dictionary.py:load_feedback_candidate_model_for_args](../rgbw_lut_builder/correction/pass_fail_dictionary.py#L134)
  - [rgbw_lut_builder/correction/pass_fail_dictionary.py:write_verifier_feedback_bank](../rgbw_lut_builder/correction/pass_fail_dictionary.py#L186)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:load_feedback_candidate_model_for_args](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4536)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_parse_verifier_feedback_rows](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5571) - Collect verifier CSV rows and target-match candidates.
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_feedback_bank](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5988)


### Phase 5

#### implement slightly-expanded virtual reference hull generation

- Status: `planned`
- Target modules: [rgbw_lut_builder/model/projection.py](../rgbw_lut_builder/model/projection.py)<br>[rgbw_lut_builder/model/emitter_classification.py](../rgbw_lut_builder/model/emitter_classification.py)
- Current source surfaces: none
- Math / prototype refs: [Profile-space virtual reference hull](../README_MATH_MODEL.md#4-profile-space-virtual-reference-hull)
- Candidate functions: none pinned yet

#### project/remap measured emitters into stored virtual emitter profiles

- Status: `planned`
- Target modules: [rgbw_lut_builder/model/projection.py](../rgbw_lut_builder/model/projection.py)<br>[rgbw_lut_builder/response/multi_emitter_profile.py](../rgbw_lut_builder/response/multi_emitter_profile.py)
- Current source surfaces: none
- Math / prototype refs: [Physical and virtual emitter records](../README_MATH_MODEL.md#physical-and-virtual-emitter-records), [Solve using virtual geometry, expand through physical channels](../README_MATH_MODEL.md#solve-using-virtual-geometry-expand-through-physical-channels)
- Candidate functions: none pinned yet

#### separate solver geometry coordinates from physical output channel tuples

- Status: `planned`
- Target modules: [rgbw_lut_builder/model/simplex.py](../rgbw_lut_builder/model/simplex.py)<br>[rgbw_lut_builder/response/multi_emitter_profile.py](../rgbw_lut_builder/response/multi_emitter_profile.py)
- Current source surfaces: none
- Math / prototype refs: [Solve using virtual geometry, expand through physical channels](../README_MATH_MODEL.md#solve-using-virtual-geometry-expand-through-physical-channels)
- Candidate functions: none pinned yet

#### add active-channel-family grouping to verifier reports

- Status: `planned`
- Target modules: [rgbw_lut_builder/verify/reports.py](../rgbw_lut_builder/verify/reports.py)
- Current source surfaces: [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: [Correction response profiles and observed response curves](../README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves)
- Candidate functions:
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_rgb_key_from_rgb](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4342)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_rgbw_family_index](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4347)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_observation_sort_value](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4362)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_ok_value](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4378)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_obs_from_verifier_row](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4387)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_iter_feedback_bank_observations](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4425)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:build_feedback_candidate_model_from_observations](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4470)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:load_feedback_candidate_model_for_args](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4536)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_apply_feedback_candidate_overrides](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4546)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_bank_paths](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5454)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_parse_verifier_feedback_rows](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5571) - Collect verifier CSV rows and target-match candidates.
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_result_id](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5695)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_target_xyz](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5721)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_measured_xyz](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5731)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_capture_delta](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5775)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_legacy_feedback_observations](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5827)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_merge_feedback_observation](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5867)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_build_feedback_entry_stats](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5906)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_feedback_bank](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5988)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_failure_dictionary](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6016) - Legacy verifier failure aggregation/report writer.

#### aggregate pass/fail records into CorrectionResponseProfile artifacts

- Status: `planned`
- Target modules: [rgbw_lut_builder/response/multi_emitter_profile.py](../rgbw_lut_builder/response/multi_emitter_profile.py)<br>[rgbw_lut_builder/correction/residuals.py](../rgbw_lut_builder/correction/residuals.py)
- Current source surfaces: [rgbw_lut_builder/correction/pass_fail_dictionary.py](../rgbw_lut_builder/correction/pass_fail_dictionary.py)<br>[rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: [Correction response profiles and observed response curves](../README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves)
- Candidate functions:
  - [rgbw_lut_builder/correction/pass_fail_dictionary.py:write_verifier_feedback_bank](../rgbw_lut_builder/correction/pass_fail_dictionary.py#L186)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_legacy_feedback_observations](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5827)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_build_feedback_entry_stats](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5906)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_feedback_bank](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5988)

#### fit simple ObservedResponseCurve summaries for W/no-W edge comparisons

- Status: `planned`
- Target modules: [rgbw_lut_builder/correction/residuals.py](../rgbw_lut_builder/correction/residuals.py)<br>[rgbw_lut_builder/response/multi_emitter_profile.py](../rgbw_lut_builder/response/multi_emitter_profile.py)
- Current source surfaces: [rgbw_lut_builder/correction/pass_fail_dictionary.py](../rgbw_lut_builder/correction/pass_fail_dictionary.py)
- Math / prototype refs: [Correction response profiles and observed response curves](../README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves)
- Candidate functions:
  - [rgbw_lut_builder/correction/pass_fail_dictionary.py:load_feedback_candidate_model_for_args](../rgbw_lut_builder/correction/pass_fail_dictionary.py#L134)
  - [rgbw_lut_builder/correction/pass_fail_dictionary.py:write_verifier_feedback_bank](../rgbw_lut_builder/correction/pass_fail_dictionary.py#L186)

#### use learned response direction to bias correction candidates before live probing

- Status: `planned`
- Target modules: [rgbw_lut_builder/correction/triangle_ranker.py](../rgbw_lut_builder/correction/triangle_ranker.py)<br>[rgbw_lut_builder/correction/live_retry.py](../rgbw_lut_builder/correction/live_retry.py)
- Current source surfaces: [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: [Capture-cloud simplex correction](../README_MATH_MODEL.md#12-capture-cloud-simplex-correction)
- Candidate functions:
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_capture_delta](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5775)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_resolve_target_match_candidates](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5791)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_merge_feedback_observation](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5867)

#### write diagnostics showing where virtual expansion helps, hurts, or remains uncertain

- Status: `planned`
- Target modules: [rgbw_lut_builder/verify/reports.py](../rgbw_lut_builder/verify/reports.py)<br>[rgbw_lut_builder/verify/metrics.py](../rgbw_lut_builder/verify/metrics.py)
- Current source surfaces: [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)<br>[rgbw_lut_builder/gui/analyze_rgbw_captures.py](../rgbw_lut_builder/gui/analyze_rgbw_captures.py)
- Math / prototype refs: [Why this helps edge colors](../README_MATH_MODEL.md#why-this-helps-edge-colors)
- Candidate functions:
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:summarize_rows](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L213)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_failure_dictionary](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6016) - Legacy verifier failure aggregation/report writer.
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:summarize_build](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6344)


### Phase 6

#### load emitter profiles with arbitrary channel counts

- Status: `planned`
- Target modules: [rgbw_lut_builder/response/multi_emitter_profile.py](../rgbw_lut_builder/response/multi_emitter_profile.py)<br>[rgbw_lut_builder/model/emitter_classification.py](../rgbw_lut_builder/model/emitter_classification.py)
- Current source surfaces: none
- Math / prototype refs: [Multi-emitter layered simplex model](../README_MATH_MODEL.md#13-multi-emitter-layered-simplex-model)
- Candidate functions: none pinned yet

#### classify emitters by measured chromaticity relative to the device hull

- Status: `planned`
- Target modules: [rgbw_lut_builder/model/emitter_classification.py](../rgbw_lut_builder/model/emitter_classification.py)
- Current source surfaces: none
- Math / prototype refs: [Emitter classification](../README_MATH_MODEL.md#emitter-classification)
- Candidate functions: none pinned yet

#### build outer-hull triangle fans for each inner anchor

- Status: `planned`
- Target modules: [rgbw_lut_builder/model/layered_simplex.py](../rgbw_lut_builder/model/layered_simplex.py)<br>[rgbw_lut_builder/model/simplex.py](../rgbw_lut_builder/model/simplex.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: [General algorithm](../README_MATH_MODEL.md#general-algorithm)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_find_sub_gamut](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L407) - Find the containing strict RGBW sub-gamut in xy space.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_xyz](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L419) - Shared 3-emitter XYZ linear solve primitive.

#### solve RGBCCT-style warm/cool inner-anchor layers

- Status: `planned`
- Target modules: [rgbw_lut_builder/model/layered_simplex.py](../rgbw_lut_builder/model/layered_simplex.py)
- Current source surfaces: none
- Math / prototype refs: [RGBCCT / warm-cool inner-anchor model](../README_MATH_MODEL.md#rgbcct--warm-cool-inner-anchor-model)
- Candidate functions: none pinned yet

#### solve RGBY/RGBV-style outer-hull-expanded packages

- Status: `planned`
- Target modules: [rgbw_lut_builder/model/layered_simplex.py](../rgbw_lut_builder/model/layered_simplex.py)
- Current source surfaces: none
- Math / prototype refs: [RGBY / RGBV / outer-hull expansion](../README_MATH_MODEL.md#rgby--rgbv--outer-hull-expansion)
- Candidate functions: none pinned yet

#### share known-point / simplex expansion logic with capture-cloud correction

- Status: `planned`
- Target modules: [rgbw_lut_builder/model/simplex.py](../rgbw_lut_builder/model/simplex.py)<br>[rgbw_lut_builder/correction/measured_simplex.py](../rgbw_lut_builder/correction/measured_simplex.py)
- Current source surfaces: [rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)<br>[rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: [Common simplex solve](../README_MATH_MODEL.md#2-common-simplex-solve), [Capture-cloud simplex correction](../README_MATH_MODEL.md#12-capture-cloud-simplex-correction)
- Candidate functions:
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_resolve_target_match_candidates](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5791)
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_xyz](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L419) - Shared 3-emitter XYZ linear solve primitive.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_nnls_solve](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L699)

#### write diagnostics for hull classification, ambiguous edge emitters, and inner-anchor blends

- Status: `planned`
- Target modules: [rgbw_lut_builder/verify/reports.py](../rgbw_lut_builder/verify/reports.py)<br>[rgbw_lut_builder/verify/metrics.py](../rgbw_lut_builder/verify/metrics.py)
- Current source surfaces: [rgbw_lut_builder/gui/analyze_rgbw_captures.py](../rgbw_lut_builder/gui/analyze_rgbw_captures.py)
- Math / prototype refs: [Emitter classification](../README_MATH_MODEL.md#emitter-classification)
- Candidate functions:
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:parse_args](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L39)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:safe_int](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L54)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:safe_float](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L61)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:is_ok](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L68)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:xyz_to_lab](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L72)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:lab_to_lch](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L91)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:family_name](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L97)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:white_sweep_rank](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L101)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:load_rows](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L108)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:write_metrics_csv](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L178)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:summarize_rows](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L213)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:plot_xy_scatter](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L254)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:plot_chroma_vs_white](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L273)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:build_envelope](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L290)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:write_envelope_csv](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L332)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:plot_envelope_heatmap](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L342)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:plot_family_sweeps](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L376)
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:main](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L431)

#### add degenerate inner-anchor line fallback for overdrive prediction models

- Status: `planned`
- Target modules: [rgbw_lut_builder/model/layered_simplex.py](../rgbw_lut_builder/model/layered_simplex.py)<br>[rgbw_lut_builder/model/simplex.py](../rgbw_lut_builder/model/simplex.py)
- Current source surfaces: none
- Math / prototype refs: [Degenerate inner-anchor line fallback](../README_MATH_MODEL.md#degenerate-inner-anchor-line-fallback)
- Candidate functions: none pinned yet

#### ensure strict sub-gamut mode continues to solve only direct legal edge/hull pairs

- Status: `planned`
- Target modules: [rgbw_lut_builder/model/topology.py](../rgbw_lut_builder/model/topology.py)<br>[rgbw_lut_builder/model/layered_simplex.py](../rgbw_lut_builder/model/layered_simplex.py)
- Current source surfaces: [rgbw_lut_builder/model/topology.py](../rgbw_lut_builder/model/topology.py)<br>[rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: [Strict RGBW sub-gamut model](../README_MATH_MODEL.md#6-strict-rgbw-sub-gamut-model)
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_find_sub_gamut](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L407) - Find the containing strict RGBW sub-gamut in xy space.
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:_solve_subgamut_fraction_from_linear](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L2820)


### Phase 7

#### fit conservative residual correction maps

- Status: `planned`
- Target modules: [rgbw_lut_builder/correction/correction_field.py](../rgbw_lut_builder/correction/correction_field.py)<br>[rgbw_lut_builder/correction/residuals.py](../rgbw_lut_builder/correction/residuals.py)
- Current source surfaces: [rgbw_lut_builder/gui/analyze_rgbw_captures.py](../rgbw_lut_builder/gui/analyze_rgbw_captures.py)<br>[rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: [Capture-cloud simplex correction](../README_MATH_MODEL.md#12-capture-cloud-simplex-correction)
- Candidate functions:
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_rgb_key_from_rgb](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4342)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_rgbw_family_index](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4347)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_observation_sort_value](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4362)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_ok_value](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4378)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_obs_from_verifier_row](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4387)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_iter_feedback_bank_observations](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4425)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:build_feedback_candidate_model_from_observations](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4470)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:load_feedback_candidate_model_for_args](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4536)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_apply_feedback_candidate_overrides](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L4546)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_bank_paths](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5454)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_parse_verifier_feedback_rows](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5571) - Collect verifier CSV rows and target-match candidates.
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_result_id](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5695)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_target_xyz](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5721)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_measured_xyz](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5731)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_feedback_capture_delta](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5775)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_legacy_feedback_observations](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5827)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_merge_feedback_observation](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5867)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_build_feedback_entry_stats](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5906)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_feedback_bank](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5988)

#### build/rank local measured triangle/simplex candidates

- Status: `planned`
- Target modules: [rgbw_lut_builder/correction/measured_simplex.py](../rgbw_lut_builder/correction/measured_simplex.py)<br>[rgbw_lut_builder/correction/triangle_ranker.py](../rgbw_lut_builder/correction/triangle_ranker.py)
- Current source surfaces: [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: [Common simplex solve](../README_MATH_MODEL.md#2-common-simplex-solve), [Capture-cloud simplex correction](../README_MATH_MODEL.md#12-capture-cloud-simplex-correction)
- Candidate functions:
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_parse_target_match_row](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5469)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_resolve_best_capture_xyz](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5523)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_resolve_target_match_candidates](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5791)

#### apply corrections to model candidates

- Status: `planned`
- Target modules: [rgbw_lut_builder/correction/correction_field.py](../rgbw_lut_builder/correction/correction_field.py)<br>[rgbw_lut_builder/correction/residuals.py](../rgbw_lut_builder/correction/residuals.py)<br>[rgbw_lut_builder/build/offline_measured.py](../rgbw_lut_builder/build/offline_measured.py)
- Current source surfaces: [rgbw_lut_builder/build/model_only.py](../rgbw_lut_builder/build/model_only.py)<br>[rgbw_lut_builder/gui/build_measured_rgbw_lut.py](../rgbw_lut_builder/gui/build_measured_rgbw_lut.py)
- Math / prototype refs: [Capture-cloud simplex correction](../README_MATH_MODEL.md#12-capture-cloud-simplex-correction)
- Candidate functions:
  - [rgbw_lut_builder/gui/build_measured_rgbw_lut.py:summarize](../rgbw_lut_builder/gui/build_measured_rgbw_lut.py#L367)

#### use pass/fail dictionary as final override/block

- Status: `planned`
- Target modules: [rgbw_lut_builder/correction/pass_fail_dictionary.py](../rgbw_lut_builder/correction/pass_fail_dictionary.py)<br>[rgbw_lut_builder/correction/correction_field.py](../rgbw_lut_builder/correction/correction_field.py)
- Current source surfaces: [rgbw_lut_builder/correction/pass_fail_dictionary.py](../rgbw_lut_builder/correction/pass_fail_dictionary.py)
- Math / prototype refs: [Correction response profiles and observed response curves](../README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves)
- Candidate functions:
  - [rgbw_lut_builder/correction/pass_fail_dictionary.py:load_feedback_candidate_model_for_args](../rgbw_lut_builder/correction/pass_fail_dictionary.py#L134)
  - [rgbw_lut_builder/correction/pass_fail_dictionary.py:write_verifier_feedback_bank](../rgbw_lut_builder/correction/pass_fail_dictionary.py#L186)

#### write before/after diagnostics

- Status: `planned`
- Target modules: [rgbw_lut_builder/verify/reports.py](../rgbw_lut_builder/verify/reports.py)<br>[rgbw_lut_builder/verify/metrics.py](../rgbw_lut_builder/verify/metrics.py)
- Current source surfaces: [rgbw_lut_builder/gui/analyze_rgbw_captures.py](../rgbw_lut_builder/gui/analyze_rgbw_captures.py)<br>[rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: none
- Candidate functions:
  - [rgbw_lut_builder/gui/analyze_rgbw_captures.py:summarize_rows](../rgbw_lut_builder/gui/analyze_rgbw_captures.py#L213)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_failure_dictionary](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6016) - Legacy verifier failure aggregation/report writer.
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:summarize_build](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L6344)


### Phase 8

#### builder sends capture requests to host_calibration_gui

- Status: `planned`
- Target modules: [rgbw_lut_builder/captures/udp_client.py](../rgbw_lut_builder/captures/udp_client.py)<br>[rgbw_lut_builder/build/live_measured.py](../rgbw_lut_builder/build/live_measured.py)
- Current source surfaces: [tools/run_live_capture.py](../tools/run_live_capture.py)<br>[rgbw_lut_builder/captures/udp_client.py](../rgbw_lut_builder/captures/udp_client.py)<br>[FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py)
- Math / prototype refs: none
- Candidate functions:
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_spotread_command_with_one_shot](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L108)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_generate_verifier_patches](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L176)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_lut_axis_position](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L239)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_clamp_lut_result](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L253)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_trilinear_lut_lookup](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L259)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_tetrahedral_lut_lookup](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L287)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_lut_lookup](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L334)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_xy_to_xyz1_tuple](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L374)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_build_rgb_to_xyz_matrix](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L379)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_apply_transfer_normalized](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L396)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_expected_xy_for_named_gamut](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L416)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_xyz_to_xy_tuple](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L433)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_point_in_triangle_xy](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L442)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_closest_point_on_segment_xy](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L460)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_project_xy_to_hull](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L474)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_xyY_to_XYZ](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L509)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_nnls_small](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L519)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_compute_model_scale_k](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L554)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_model_project_xy_for_named_gamut](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L578)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_xy_chroma_de](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L642)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:main](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L2877)

#### receive full spotread measurement payloads

- Status: `planned`
- Target modules: [rgbw_lut_builder/captures/spotread_protocol.py](../rgbw_lut_builder/captures/spotread_protocol.py)<br>[rgbw_lut_builder/build/live_measured.py](../rgbw_lut_builder/build/live_measured.py)
- Current source surfaces: [rgbw_lut_builder/captures/spotread_protocol.py](../rgbw_lut_builder/captures/spotread_protocol.py)<br>[FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py)
- Math / prototype refs: none
- Candidate functions:
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_spotread_command_with_one_shot](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L108)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_generate_verifier_patches](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L176)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_lut_axis_position](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L239)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_clamp_lut_result](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L253)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_trilinear_lut_lookup](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L259)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_tetrahedral_lut_lookup](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L287)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_lut_lookup](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L334)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_xy_to_xyz1_tuple](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L374)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_build_rgb_to_xyz_matrix](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L379)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_apply_transfer_normalized](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L396)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_expected_xy_for_named_gamut](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L416)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_xyz_to_xy_tuple](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L433)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_point_in_triangle_xy](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L442)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_closest_point_on_segment_xy](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L460)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_project_xy_to_hull](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L474)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_xyY_to_XYZ](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L509)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_nnls_small](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L519)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_compute_model_scale_k](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L554)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_verifier_model_project_xy_for_named_gamut](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L578)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:_xy_chroma_de](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L642)
  - [FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py:main](../FILES_FOR_TRANSITION/host_calibration_gui_pipeline_ready_udp_capture_spotread_protocol_model_projection.py#L2877)

#### update pass/fail dictionary during calibration

- Status: `planned`
- Target modules: [rgbw_lut_builder/correction/pass_fail_dictionary.py](../rgbw_lut_builder/correction/pass_fail_dictionary.py)<br>[rgbw_lut_builder/correction/live_retry.py](../rgbw_lut_builder/correction/live_retry.py)
- Current source surfaces: [rgbw_lut_builder/correction/pass_fail_dictionary.py](../rgbw_lut_builder/correction/pass_fail_dictionary.py)<br>[rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: [Capture-cloud simplex correction](../README_MATH_MODEL.md#12-capture-cloud-simplex-correction)
- Candidate functions:
  - [rgbw_lut_builder/correction/pass_fail_dictionary.py:write_verifier_feedback_bank](../rgbw_lut_builder/correction/pass_fail_dictionary.py#L186)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_merge_feedback_observation](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5867)
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:write_verifier_feedback_bank](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5988)

#### retry candidate corrections until pass or retry budget exhausted

- Status: `planned`
- Target modules: [rgbw_lut_builder/correction/live_retry.py](../rgbw_lut_builder/correction/live_retry.py)<br>[rgbw_lut_builder/build/live_measured.py](../rgbw_lut_builder/build/live_measured.py)
- Current source surfaces: [tools/run_live_capture.py](../tools/run_live_capture.py)<br>[rgbw_lut_builder/gui/prototype_measured_white_solver.py](../rgbw_lut_builder/gui/prototype_measured_white_solver.py)
- Math / prototype refs: [Capture-cloud simplex correction](../README_MATH_MODEL.md#12-capture-cloud-simplex-correction)
- Candidate functions: none pinned yet

#### save live_capture_session.jsonl and live_retry_trace.csv

- Status: `planned`
- Target modules: [rgbw_lut_builder/build/live_measured.py](../rgbw_lut_builder/build/live_measured.py)<br>[rgbw_lut_builder/verify/reports.py](../rgbw_lut_builder/verify/reports.py)
- Current source surfaces: [tools/run_live_capture.py](../tools/run_live_capture.py)<br>[rgbw_lut_builder/gui/prototype_measured_white_solver.py](../rgbw_lut_builder/gui/prototype_measured_white_solver.py)
- Math / prototype refs: none
- Candidate functions: none pinned yet


### Phase 9

#### RGB8 / RGB16

- Status: `active`
- Target modules: [rgbw_lut_builder/output/rgb8.py](../rgbw_lut_builder/output/rgb8.py)<br>[rgbw_lut_builder/output/rgb16.py](../rgbw_lut_builder/output/rgb16.py)
- Current source surfaces: [rgbw_lut_builder/build/model_only.py](../rgbw_lut_builder/build/model_only.py)<br>[rgbw_lut_builder/build/lut_writer.py](../rgbw_lut_builder/build/lut_writer.py)
- Math / prototype refs: none
- Candidate functions: none pinned yet

#### RGBW8 / RGBW16

- Status: `active`
- Target modules: [rgbw_lut_builder/output/rgbw8.py](../rgbw_lut_builder/output/rgbw8.py)<br>[rgbw_lut_builder/output/rgbw16.py](../rgbw_lut_builder/output/rgbw16.py)
- Current source surfaces: [rgbw_lut_builder/build/model_only.py](../rgbw_lut_builder/build/model_only.py)<br>[rgbw_lut_builder/build/lut_writer.py](../rgbw_lut_builder/build/lut_writer.py)
- Math / prototype refs: none
- Candidate functions: none pinned yet

#### generic channels16 outputs

- Status: `planned`
- Target modules: [rgbw_lut_builder/output/channels16.py](../rgbw_lut_builder/output/channels16.py)
- Current source surfaces: [rgbw_lut_builder/build/lut_writer.py](../rgbw_lut_builder/build/lut_writer.py)
- Math / prototype refs: none
- Candidate functions: none pinned yet

#### TemporalBFI encoder

- Status: `planned`
- Target modules: [rgbw_lut_builder/output/temporal_bfi_encoder.py](../rgbw_lut_builder/output/temporal_bfi_encoder.py)
- Current source surfaces: [tools/convert_temporal_bfi_dataset.py](../tools/convert_temporal_bfi_dataset.py)
- Math / prototype refs: none
- Candidate functions: none pinned yet

#### APA102 encoder

- Status: `planned`
- Target modules: [rgbw_lut_builder/output/apa102_encoder.py](../rgbw_lut_builder/output/apa102_encoder.py)
- Current source surfaces: none
- Math / prototype refs: none
- Candidate functions: none pinned yet

#### HD108 encoder

- Status: `planned`
- Target modules: [rgbw_lut_builder/output/hd108_encoder.py](../rgbw_lut_builder/output/hd108_encoder.py)
- Current source surfaces: none
- Math / prototype refs: none
- Candidate functions: none pinned yet

#### HyperHDR export

- Status: `planned`
- Target modules: [rgbw_lut_builder/output/hyperhdr_export.py](../rgbw_lut_builder/output/hyperhdr_export.py)
- Current source surfaces: [rgbw_lut_builder/gui/rgbw_lut_gui.py](../rgbw_lut_builder/gui/rgbw_lut_gui.py)
- Math / prototype refs: none
- Candidate functions: none pinned yet

#### C header export

- Status: `active`
- Target modules: [rgbw_lut_builder/output/c_header_export.py](../rgbw_lut_builder/output/c_header_export.py)<br>[rgbw_lut_builder/output/mcu_header_export.py](../rgbw_lut_builder/output/mcu_header_export.py)
- Current source surfaces: [rgbw_lut_builder/build/lut_writer.py](../rgbw_lut_builder/build/lut_writer.py)<br>[rgbw_lut_builder/legacy/xy_target_rgbw_model.py](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py)
- Math / prototype refs: none
- Candidate functions:
  - [rgbw_lut_builder/legacy/xy_target_rgbw_model.py:write_lut_header](../rgbw_lut_builder/legacy/xy_target_rgbw_model.py#L3467)

#### binary cube export

- Status: `active`
- Target modules: [rgbw_lut_builder/output/binary_cube_export.py](../rgbw_lut_builder/output/binary_cube_export.py)
- Current source surfaces: [rgbw_lut_builder/build/lut_writer.py](../rgbw_lut_builder/build/lut_writer.py)
- Math / prototype refs: none
- Candidate functions: none pinned yet

#### coefficient tetrahedral cube export

- Status: `planned`
- Target modules: [rgbw_lut_builder/output/coefficient_cube_export.py](../rgbw_lut_builder/output/coefficient_cube_export.py)
- Current source surfaces: [rgbw_lut_builder/output/binary_cube_export.py](../rgbw_lut_builder/output/binary_cube_export.py)<br>[rgbw_lut_builder/runtime/](../rgbw_lut_builder/runtime/)
- Math / prototype refs: [Tetrahedral LUT interpolation](../README_MATH_MODEL.md#14-tetrahedral-lut-interpolation)
- Candidate functions: none pinned yet

#### MCU/SBC size-report tooling for 8 / 16 / 32 MB PSRAM targets

- Status: `planned`
- Target modules: [rgbw_lut_builder/output/mcu_header_export.py](../rgbw_lut_builder/output/mcu_header_export.py)<br>[rgbw_lut_builder/output/coefficient_cube_export.py](../rgbw_lut_builder/output/coefficient_cube_export.py)
- Current source surfaces: none
- Math / prototype refs: none
- Candidate functions: none pinned yet

#### reference fixed-point tetrahedral samplers

- Status: `planned`
- Target modules: [rgbw_lut_builder/runtime/tetra_sampler_c_reference.c](../rgbw_lut_builder/runtime/tetra_sampler_c_reference.c)<br>[rgbw_lut_builder/runtime/tetra_sampler_cpp.hpp](../rgbw_lut_builder/runtime/tetra_sampler_cpp.hpp)<br>[rgbw_lut_builder/runtime/tetra_sampler_arduino.hpp](../rgbw_lut_builder/runtime/tetra_sampler_arduino.hpp)
- Current source surfaces: [rgbw_lut_builder/runtime/](../rgbw_lut_builder/runtime/)
- Math / prototype refs: [Tetrahedral LUT interpolation](../README_MATH_MODEL.md#14-tetrahedral-lut-interpolation)
- Candidate functions: none pinned yet


### Phase 10

#### use model confidence and correction uncertainty to choose new probes

- Status: `planned`
- Target modules: [tools/generate_capture_plan.py](../tools/generate_capture_plan.py)<br>[rgbw_lut_builder/verify/metrics.py](../rgbw_lut_builder/verify/metrics.py)
- Current source surfaces: [tools/generate_capture_plan.py](../tools/generate_capture_plan.py)<br>[rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: [Capture-cloud simplex correction](../README_MATH_MODEL.md#12-capture-cloud-simplex-correction), [Correction response profiles and observed response curves](../README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves)
- Candidate functions:
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_build_feedback_entry_stats](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5906)

#### support sparse capture sets for normal users

- Status: `planned`
- Target modules: [tools/generate_capture_plan.py](../tools/generate_capture_plan.py)
- Current source surfaces: [tools/generate_capture_plan.py](../tools/generate_capture_plan.py)
- Math / prototype refs: none
- Candidate functions: none pinned yet

#### support dense research datasets for advanced calibration

- Status: `planned`
- Target modules: [tools/generate_capture_plan.py](../tools/generate_capture_plan.py)<br>[rgbw_lut_builder/response/temporal_bfi.py](../rgbw_lut_builder/response/temporal_bfi.py)
- Current source surfaces: [tools/generate_capture_plan.py](../tools/generate_capture_plan.py)<br>[tools/convert_temporal_bfi_dataset.py](../tools/convert_temporal_bfi_dataset.py)
- Math / prototype refs: none
- Candidate functions: none pinned yet

#### stop capturing once each region has enough support

- Status: `planned`
- Target modules: [tools/generate_capture_plan.py](../tools/generate_capture_plan.py)<br>[rgbw_lut_builder/verify/metrics.py](../rgbw_lut_builder/verify/metrics.py)
- Current source surfaces: [tools/generate_capture_plan.py](../tools/generate_capture_plan.py)<br>[rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py)
- Math / prototype refs: [Capture-cloud simplex correction](../README_MATH_MODEL.md#12-capture-cloud-simplex-correction)
- Candidate functions:
  - [rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py:_build_feedback_entry_stats](../rgbw_lut_builder/gui/build_delaunay_rgbw_lut.py#L5906)

