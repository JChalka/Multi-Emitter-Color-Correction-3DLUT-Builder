# RGBW LUT Builder Roadmap

A model-guided, measurement-corrected LUT builder for mapping linear RGB color into calibrated LED output values.

This document is the **roadmap / integration README**. It explains the overall project direction, how the currently spread-out tools should be unified, and the phases needed to turn the current model-only experiments into a standalone measured RGB/RGBW/multi-emitter LUT builder.

For the actual solve equations and per-mode algorithms, see:

```text
README_MATH_MODEL.md
```

---

## Central design rule

```text
math model = physical/topological prediction axis
patch captures = real-world correction field
pass/fail dictionary = measured truth override
local triangle/simplex solve = shared primitive for prediction and correction
multi-emitter packages = layered simplex composition, not unconstrained N-channel solving
```

The mathematical model should be good enough to build a usable initial LUT and to define expected topology. It is not the final source of truth. Measurements correct the model for real hardware, optics, wall/diffuser response, camera/colorimeter behavior, temporal rendering behavior, and LED package non-idealities.

The important recent insight is that the WX / white-overdrive solver and the future capture-cloud correction solver share the same primitive:

```text
known point = XYZxyY + output channel tuple + trust/source metadata
solve target inside a valid line/triangle/simplex
expand weights back into output channels
score expected dE / dY / topology / headroom
```

---

## Pipeline scope

This builder is for the **LED-output mapping stage**, not the upstream video-grabber tone-mapping stage.

```text
upstream video pipeline / HyperHDR / tone mapping / gamut selection
    ↓
linear RGB in a declared output gamut
    ↓
LED output LUT
    ↓
RGB, RGBW, TemporalBFI, APA102/HD108, RGBCCT, RGBY/W, or other device-specific output
```

In a HyperHDR-style system, the video LUT and LED LUT are separate responsibilities:

```text
HyperHDR / video cube:
    video RGB/YUV/P010/HDR/DV source
    → tone-mapped RGB in Rec.709 / Rec.2020 / native / working gamut

LED cube:
    linear RGB in selected gamut
    → calibrated device drive values
```

This separation prevents double-applying tone curves. The LED cube should normally solve **linear-light RGB values** in the selected gamut; optional baked transfer curves can exist for testing or legacy workflows, but should not be the default.

---

## Current codebase status

The future standalone repository should preserve and refactor existing infrastructure rather than rebuilding everything from scratch.

### Already available in the current GUI

```text
interactive LUT builder GUI
display/profile configuration
reference-white controls
color-space / gamut clamp selection
RGBW and RGB channel modes
LP Solver / Delaunay mode selection
worker count configuration
output directory and verifier diagnostic directory handling
header export
True16 calibration header export
binary cube export
HyperHDR JSON export
CIE chart and visualization panels
persistent GUI config
```

The GUI already has a typed settings model containing output mode, worker count, build mode, display profile, verifier feedback settings, and memory-related candidate options. It also already defines standard color-space primary sets such as Rec.709/sRGB, DCI-P3, BT.2020, and Adobe RGB.

### Already available in the legacy Delaunay builder

The legacy Delaunay builder is not the solver direction we want to keep as the main path, but it contains important reusable plumbing:

```text
capture loading and deduplication
XYZ/RGBW point-cloud data handling
full-grid direct LUT build path
multiprocessing worker support
memory-aware candidate sizing for large capture sets
NumPy memmap output
summary JSON / diagnostics CSVs / utilization CSVs
verifier failure dictionary generation
display-profile-scoped feedback banks
active exact-key verifier feedback candidate override support
```

The old Delaunay architecture can remain as a legacy/reference mode, but the new measured builder should use the math model as the topology and prediction axis, then apply measured corrections.

### Already available in the model-only builder

```text
native / Rec.709 / Rec.2020 / P3 / Adobe RGB target gamuts
linear input-transfer default
optional baked transfer behavior
RGBW sub-gamut selection
strict RGBW topology rules
single-channel Y ramp support
single-channel xyY ramp support
primary+W boundary handling
normalized float internal math
parallel LUT generation
Windows-console-safe runtime output
tetrahedral interpolation expectation
WX white-overdrive / extraction model family
radial virtual-primary WX mode with target-position policy control
virtual-axis max-brightness WX mode
LP max-white / legacy WX reference mode
interactive WX geometry explorer for target-xy probing
```

### Already available in the host calibration GUI

```text
serial control of the LED device
UDP request/reply capture server
RGBW16 render/capture requests
spotread preset support
XYZxy, Lab, LCh, and Luv parsing
structured UDP replies
local JSON capture traces
model-matched out-of-hull expected-xy projection in the verifier
```

---

## Solver families to unify

The future builder should treat each output family as a profile-selected model, not as unrelated one-off programs.

### RGB-only LEDs

RGB output is the simple three-primary model:

```text
linear RGB in selected source gamut
    ↓
project/map into measured device RGB triangle
    ↓
RGB output values
```

This is the correct mode for standard RGB LED strips and SPI chipsets such as APA102 or HD108 when no W diode is present.

### RGBW strict sub-gamut

For RGBW LEDs, W sits inside the RGB triangle and divides it into:

```text
RGW
RBW
BGW
```

The strict mode remains the default correctness path. Legal topologies are constrained to one physical sub-gamut or its edge/vertex reductions:

```text
black
R, G, B, W
RG, RB, BG
RW, GW, BW
RGW, RBW, BGW
```

### WX / white-overdrive modes

The WX family is a separate white-overdrive / white-extraction family. It should be explicit because it changes physical RGBW utilization.

```text
strict_subgamut              default topology-safe RGBW solve
wx_radial_virtual            radial virtual-primary white-overdrive model
wx_virtual_axis_maxbright    virtual-axis max-brightness / high-W model
wx_lp_legacy                 direct LP max-white endpoint / reference model
```

Verifier results can promote a WX mode from experimental to a functional model family for a display profile when residuals are predictable and correctable. The pre-radial WX verification session was based on the virtual-axis max-brightness model, not the radial model, and already suggests that high-W regions can be stable while many failures are common wall/setup/capture residuals rather than topology failures. The old name `wx_legacy_virtual_axis` should be treated as a deprecated alias for `wx_virtual_axis_maxbright`, not as a diagnostic-only or obsolete mode.

Recommended naming:

```text
wx_virtual_axis_maxbright:
    first-class functional WX model
    independently chooses high-W / high-Y virtual points per sub-gamut
    can reach very high brightness on supported hardware
    less geometrically constrained than wx_radial_virtual

wx_legacy_virtual_axis:
    deprecated compatibility alias only
    should not be used in new metadata except to read old files/configs
```

### Multi-emitter layered simplex

Packages with more than four emitters should not be solved as unconstrained N-channel optimizations. They should be decomposed into layered simplex solves.

```text
RGBCCT        RGB + cool white + warm white
RGBWWCW       RGB + warm white + cool white
RGBY          RGB + yellow / amber
RGBV          RGB + violet
RGBYW         RGB + yellow + white
RGB+CCT+Y     RGB + cool/warm white + yellow or amber
```

Emitter classification:

```text
outer emitter:
    expands or defines the measured device hull
    becomes a hull vertex / sub-gamut-creating point

inner emitter:
    lives inside the measured hull
    becomes an alternate inner anchor / white-axis model

edge emitter:
    lies on or near an existing hull edge
    treated as a hull refinement, edge anchor, or configurable ambiguous point
```

Examples:

```text
RGBCCT:
    solve RGB + warm-white as one inner-anchor model
    solve RGB + cool-white as another inner-anchor model
    solve/blend between those solved outputs

RGBY+W:
    yellow expands the outer hull
    white remains an inner anchor
    build a measured outer-hull fan against W and solve in the containing triangle
```


### Degenerate inner-anchor line fallback

Multi-emitter overdrive models should also handle the rare case where additional
inner emitters do **not** form a useful final inner-emitter triangle/simplex.
This is unlikely in normal commercial packages, but it is a useful constraint for
lab emitters, unusual CCT stacks, or future profiles where several inner anchors
fall along a near-line.

Rule:

```text
if inner anchors form a valid final triangle/simplex around the target:
    solve them normally using the layered-simplex rule
else:
    reduce the inner-anchor chain through adjacent line solves
```

For a three-point inner line between two outer/hull references:

```text
OuterA --- Inner --- OuterB

1. Solve between OuterA and Inner      → SolveOAI
2. Solve between Inner and OuterB      → SolveIOB
3. Solve between SolveOAI and SolveIOB → final virtual result
```

For a four-point line, use the same recursive reduction:

```text
OuterA --- InnerA --- InnerB --- OuterB

1. Solve OuterA ↔ InnerA
2. Solve InnerB ↔ OuterB
3. Solve those two solved virtual points against each other
```

Longer line chains reduce the same way until they become a 3-line or 2-line
problem already covered by the same rule.

This fallback applies to **overdrive / virtual prediction models** where the
builder is intentionally creating virtual inner-anchor results before the final
solve. The strict sub-gamut model should remain strict: direct edge/hull lines
are solved only between the actual legal emitter endpoints for that topology.

---

## Input gamuts and transfer handling

The builder should support at least:

```text
native
Rec.709 / sRGB
Rec.2020
Display P3
Adobe RGB
```

Named gamuts should default to linear component interpretation for LED LUT generation:

```text
source RGB values are already linear-light inside the selected gamut
```

Optional transfer modes can be provided:

```text
linear             default for the LED LUT
native/gamut EOTF  optional legacy or test mode
custom curve       optional future extension
```

---

## Out-of-hull projection contract

For named gamuts, some source colors are physically unreachable on the measured LED primary set. The expected verifier target must not remain the impossible raw Rec.709 or Rec.2020 xy coordinate.

Expected targets should be computed as:

```text
raw target xy/XYZ from selected gamut
if target is inside measured device hull:
    expected = raw target
else:
    expected = model-projected target inside measured hull
```

Projection must be shared between builder and verifier. A simple nearest point on the xy triangle is not always equivalent to the model’s XYZ residual projection.

---

---

## Virtual reference hull and response-learning roadmap

The CIE virtual-hull idea should be implemented first as a **profile preprocessing / virtual-emitter remapping layer**, not as an expensive per-LUT-node solve.

A practical first implementation should start conservatively:

```text
measured physical hull
→ slightly expanded virtual reference hull
→ solve/profile emitters into that reference space
→ let verifier/correction decide where expansion helps or hurts
```

This gives the builder permission to test small W / inner-emitter contributions near physical edges without hard-locking those edges to zero-W behavior. For example, a yellow or yellow-green target on a measured `RG` boundary may benefit from a small W contribution in a wall/diffuser setup, while a saturated red/green/blue edge may be dragged inward too much by W and should be backed off by correction.

The key architectural rule is:

```text
once per display/emitter profile:
    measured emitter xyY / XYZ
    → reference-hull projection / remapping
    → stored virtual emitter xy / XYZ / metadata

then for every LUT node:
    solve against stored virtual emitters
    expand back to physical emitter channels
    apply measured correction
```

So strict sub-gamut, WX, multi-emitter, and correction solves can consume the same `KnownPoint` abstraction:

```text
KnownPoint:
    expected/model xyY
    measured xyY when available
    virtual/reference xy or XYZ used for solving
    physical output tuple used for expansion
    active channel family
    source/trust metadata
    correction-session metadata
```

This keeps the solver geometry domain separate from the physical output domain:

```text
solver geometry domain:
    virtual/reference chromaticity positions

physical output domain:
    measured channel response, Y ramps, RGBW16/channels16/TemporalBFI output
```

### Learning response model

The pass/fail dictionary should evolve into a richer **learning response model**, not just a hint log. The builder should learn how each active channel family behaves as captures accumulate.

A useful correction-session artifact:

```text
CorrectionResponseProfile:
    display_profile
    emitter_profile
    model_family
    active_channel_family       # RG, RW, RGW, RGBW, radial-WX, RGBCCT, etc.
    drive_path_signature        # ratios / dominant channel / W participation
    expected_xyY_curve
    measured_xyY_curve
    residual_vectors
    dE / dY trend
    headroom limits
    known good region
    known bad region
    recommended next probes
```

Instead of only asking whether one patch passed, the builder should ask:

```text
For this channel combination and drive trajectory,
what chromaticity curve do we actually observe?
```

That lets correction answer questions such as:

```text
Does adding W pull yellow toward the target or away from it?
Does this RG edge need a little W?
Does this orange family need less R, more G, or some W?
Does a blue/purple failure come from weak B, wall drift, or bad W introduction?
Does this package behave differently at low Y than high Y?
```

The richer learned layer can be represented as:

```text
ObservedResponseCurve:
    set of KnownPoints along a drive family
    fitted xyY trajectory
    confidence
    correction direction
    blocked regions
    recommended next capture
```

The correction loop then becomes:

```text
1. Generate candidate from model.
2. Compare to learned response curve for that active channel family.
3. Predict whether adding/removing a channel improves the measured trajectory.
4. Choose the smallest useful probe.
5. Update the response curve from the new capture.
6. Repeat only where uncertainty remains high.
```

Initial staged implementation:

```text
Phase A:
    slightly expanded virtual hull
    per-family verifier residual summaries
    active-channel-family grouping
    W/no-W comparison around edges

Phase B:
    fit local chromaticity curves per channel family
    record whether W or an inner/outer emitter helps or hurts by hue/Y bucket
    use this to bias correction candidates

Phase C:
    live adaptive probing
    choose captures based on model uncertainty and curve disagreement
```

This is the "work smart, not hard" calibration path: the model gives topology, captures teach the actual optical/diode response, and the correction engine learns which channel introductions are physically useful for the current setup.


## Interpolation and MCU runtime contract

Generated LUTs should be consumed with tetrahedral interpolation by default:

```text
input RGB16 / normalized float
→ find cube cell
→ choose tetrahedron from fractional RGB ordering
→ interpolate RGB / RGBW / multi-emitter output vector
```

Tetrahedral interpolation matters because trilinear interpolation can blend all eight cell corners and synthesize illegal channel participation between individually legal vertices.

Runtime/storage modes:

```text
vertex_tetra:
    store output values at cube vertices
    runtime fetches selected tetrahedron vertices
    lowest storage, more runtime math/fetches

coefficient_tetra:
    precompute per-cell tetrahedral affine coefficients
    runtime evaluates a short dot product
    higher storage, faster MCU/SBC path
```

Approximate uncompressed storage for RGBW16 outputs:

```text
vertex_tetra size ≈ grid_size^3 * 4 channels * 2 bytes
coefficient_tetra size ≈ (grid_size - 1)^3 * 6 tetra * 4 terms * 4 channels * bytes_per_coeff
```

For MCU targets with 8 / 16 / 32 MB PSRAM, useful starting profiles are:

```text
17^3 or 25^3 vertex LUTs for very small targets
17^3 or 33^3 coefficient LUTs when speed is more important than size
65^3+ vertex LUTs mainly for host / SBC / large-memory consumers
```

---

## Capture and correction data sources

The measured builder should support multiple data sources rather than requiring one giant capture set.

### Minimal anchors

```text
black / near-black floor
R single-channel ramp
G single-channel ramp
B single-channel ramp
optional W single-channel ramp for RGBW
optional warm/cool/Y/amber/violet ramps for multi-emitter profiles
D65 / neutral ramp
```

Ramp granularity should be selectable:

```text
minimal: 0, 512, 1024, 2048, 4096, 8192, 16384, 32768, 49152, 65535
medium: dense low end + 5–10% mid/high spacing
dense: near-full measured ladder where feasible
```

### Edge and boundary captures

```text
RG edge
RB edge
BG edge
RW boundary
GW boundary
BW boundary
neutral/D65 line
warm/cool near-whites
outer-hull edges for RGBY/RGBV/mixed packages
inner-anchor transition lines for RGBCCT-style packages
```

### Mixed HSV / patch sweeps

```text
hues:        16 / 24 / 32+
saturation: configurable levels
value:      dense low end + sparse mid/high
```

Targeted probes:

```text
yellows and oranges
blues and purples
skin tones
near-white off-axis points
high-saturation low-Y points
known verifier failures
WX high-W regions
multi-emitter inner-anchor blend regions
```

### Capture-cloud simplex correction

The measured correction primitive should rank local measured triangles/simplexes instead of blindly using the first enclosing set.

Ranking inputs:

```text
proximity to expected xy
whether expected xy is inside the measured triangle
expected Y / nits agreement
source trust: exact capture, verifier pass, interpolated, model-only
same topology / compatible topology family
channel headroom and clipping risk
age / profile match / capture session quality
nearby known-fail exclusions
expected dE / dY from measured triangle prediction
triangle conditioning / area / skinny-simplex penalty
```

Correction ladder:

```text
exact verifier pass
→ measured local triangle/simplex correction
→ measured edge/pair correction
→ measured channel-ramp correction
→ math model prediction
→ hardcoded fallback
```

---

## Pass/fail dictionary

The pass/fail dictionary should become a first-class artifact, and over time it should feed the richer correction response model described above. Exact pass/fail records remain important, but the builder should also aggregate them into per-family response curves so it can learn whether a channel introduction helps or hurts a given region.

It should store:

```text
input RGB / gamut / transfer
expected raw xy/XYZ
expected projected xy/XYZ
rendered output value
measured XYZxyY / Lab / LCh / Luv
error metrics
pass/fail status
source verifier/capture session
display profile
capture timestamp
model family / wx mode / multi-emitter mode
correction triangle/simplex id
active channel family
drive path signature
observed response curve id
virtual reference hull id
virtual emitter profile id
```

Priority:

```text
known measured pass/fail
> measured correction field
> measured channel response
> math model prediction
> hardcoded fallback
```

Known measured pass should override the model. Known measured fail should block candidate reuse even if the model predicts it is good.

---

## TemporalBFI dense response dataset

TemporalBFI data should be treated as an optional high-quality channel-response backend.

Current dataset characteristics:

```text
~186k captures
8-bit upper value
8-bit lower value
blend-frame count
full XYZxyY measurements
dense low-end ramps
sparser high-end ramps
full lowest-floor ramp
full upper 8-bit ramp spine
additional rib captures
pruned + interpolated monotonic ladder
~15.5-bit effective 16-bit mapping
```

This dataset is especially useful for:

```text
low-end channel response
low-end xy drift
effective 16-bit mapping
floor / upper / blended-frame behavior
monotonicity constraints
channel response interpolation
```

Large-data strategies:

```text
Parquet + DuckDB
SQLite with channel/effective_q16 indexes
Zarr / HDF5 chunked arrays
NumPy memmap for monotonic ladders
```

---

## Channel response providers

The builder should hide channel response data behind a common interface.

Proposed providers:

```text
HardcodedRampResponseProvider
Fill16CaptureRampResponseProvider
TemporalBFIResponseProvider
HybridResponseProvider
RGBIdentityResponseProvider
MultiEmitterProfileResponseProvider
```

Responsibilities:

```text
lookup channel XYZxyY at requested q16
invert channel Y response to drive q16
return trust/source metadata
support chunked/cache-backed reads
expose monotonicity diagnostics
support RGB-only, RGBW, RGBCCT, RGBY/RGBV/mixed emitter targets
```

---

## Builder modes

### Model-only

```text
--mode model-only
```

Generates a LUT from the mathematical model and available response curves only. Useful for initial bring-up, regression tests, baseline LUT generation, and new LED type sanity checks.

### Offline measured

```text
--mode offline-measured
```

Loads captures and verifier dictionaries from disk, builds correction fields, generates a LUT, then expects a separate verification run.

Offline mode should load:

```text
single-channel ramps
mixed patch captures
verification reports
pass/fail dictionaries
TemporalBFI response datasets
existing display profiles
emitter profiles for multi-emitter targets
```

### Live measured

```text
--mode live-measured
```

Uses the host calibration GUI UDP capture server to request captures while building/refining calibration data.

Live mode can:

```text
build a prediction dictionary
capture model candidates
compute xyY / Lab / LCh / Luv error
retry corrected candidates
update pass/fail dictionaries before final LUT generation
request adaptive sparse captures
```

### Legacy Delaunay / reference

```text
--mode legacy-delaunay
```

The old Delaunay solver can remain available as a diagnostic/reference mode. It should not be the default path for the new measured builder.

---

## Live UDP capture protocol

The host calibration GUI acts as a capture server:

```text
rgbw_lut_builder sends JSON request over UDP
host GUI renders requested RGB/RGBW/multi-emitter value over serial
host GUI runs spotread
host GUI replies with full measurement data
```

Example RGBW request:

```json
{
  "type": "capture_rgbw16",
  "request_id": "probe_0001",
  "name": "neutral_probe",
  "rgbw16": [155, 0, 465, 2014],
  "measurement_format": "xyzxy"
}
```

Example RGB-only request:

```json
{
  "type": "capture_rgb16",
  "request_id": "probe_0002",
  "name": "rgb_probe",
  "rgb16": [12000, 24000, 32000],
  "measurement_format": "xyzxy"
}
```

Example generic multi-emitter request:

```json
{
  "type": "capture_channels16",
  "request_id": "probe_0003",
  "name": "rgbcct_probe",
  "channel_order": ["R", "G", "B", "WW", "CW"],
  "channels16": [8000, 12000, 2000, 18000, 6000],
  "measurement_format": "xyzxy"
}
```

Spotread presets:

```text
XYZxy: spotread -x -O
Lab:   spotread -O
LCh:   spotread -h -O
Luv:   spotread -u -O
```

`XYZxy` should remain the default because XYZxyY is the main model and verifier data family.

---

## LUT formats and outputs

Recommended output families:

```text
RGB8
RGB16
RGBW8
RGBW16
RGBCCT16 / generic channels16
TemporalBFI effective q16 + encoder metadata
chipset-specific packed output
```

Every LUT file or sidecar summary should record:

```text
cube_size
input_gamut
input_transfer
output_family
output_channels
output_bit_depth
sample_scale
emitter_set_id
emitter_classification
outer_hull_id
inner_anchor_policy
multi_emitter_mode
interpolation
interpolation_runtime
coefficient_layout
coefficient_format
coefficient_q_format
channel_order
projection_method
display_profile
build_mode
response_provider
wx_mode
wx_target_position
correction_field_id
correction_simplex_policy
pass_fail_dictionary_id
```

Embedded paired exports:

```text
*.lutvtx  / vertex_tetra binary cube
*.lutcoef / coefficient_tetra binary cube
*.json    / sidecar metadata, coefficient layout, q-format, channel order
*.h       / optional generated C/C++ header for small grids
```

---

## Verifier requirements

Verifier outputs must include enough metadata to keep correction families separate:

```text
verification_gamut
input_transfer
interpolation
interpolation_runtime
coefficient_layout
expected_raw_x
expected_raw_y
expected_projected_x
expected_projected_y
expected_projected_to_hull
expected_projection_method
output_family
emitter_set_id
emitter_classification
outer_hull_id
inner_anchor_policy
multi_emitter_mode
wx_mode
wx_target_position
correction_triangle_id
measurement_format
spotread_command
display_profile
```

This prevents Rec.709, Rec.2020, native, linear, baked-transfer, strict, WX, and multi-emitter results from being conflated in the correction dictionary.

---

## Suggested repository layout

```text
rgbw_lut_builder/
  README.md                      # roadmap README
  README_MATH_MODEL.md           # solve equations and algorithms
  pyproject.toml

  rgbw_lut_builder/
    __init__.py

    gui/
      rgbw_lut_gui.py

    model/
      gamuts.py
      rgb_model.py
      rgbw_model.py
      topology.py
      projection.py
      virtual_reference_hull.py
      virtual_emitter_profile.py
      emitter_classification.py
      layered_simplex.py
      wx_modes.py
      simplex.py
      interpolation/
        __init__.py
        tetrahedral.py
        tetra_coefficients.py
        fixed_point.py

    response/
      base.py
      hardcoded_ramps.py
      fill16_ramps.py
      temporal_bfi.py
      hybrid.py
      multi_emitter_profile.py

    captures/
      schemas.py
      loaders.py
      validators.py
      spotread_protocol.py
      udp_client.py

    correction/
      residuals.py
      correction_field.py
      measured_simplex.py
      triangle_ranker.py
      response_profiles.py
      observed_response_curve.py
      multi_emitter_correction.py
      pass_fail_dictionary.py
      live_retry.py

    build/
      model_only.py
      offline_measured.py
      live_measured.py
      lut_writer.py
      diagnostics.py

    verify/
      verifier.py
      metrics.py
      reports.py

    output/
      rgb8.py
      rgb16.py
      rgbw8.py
      rgbw16.py
      channels16.py
      temporal_bfi_encoder.py
      apa102_encoder.py
      hd108_encoder.py
      hyperhdr_export.py
      c_header_export.py
      binary_cube_export.py
      coefficient_cube_export.py
      mcu_header_export.py

    runtime/
      tetra_sampler_c_reference.c
      tetra_sampler_cpp.hpp
      tetra_sampler_arduino.hpp

    legacy/
      delaunay_builder.py
      lp_solver_adapter.py

  tools/
    build_lut.py
    verify_lut.py
    run_live_capture.py
    convert_temporal_bfi_dataset.py
    generate_capture_plan.py

  docs/
    capture_protocol.md
    temporal_bfi_dataset.md
    lut_format.md
    migration_from_temporalbfi.md
```

---

## Migration plan from current tools

### Carry forward directly

```text
rgbw_lut_gui display/profile/settings plumbing
colorspace definitions and UI selection
output mode and export UI
worker configuration
existing config directory structure
capture CSV loading conventions
verifier feedback/pass-fail bank structure
Delaunay builder memory and worker auto-sizing utilities
NumPy memmap LUT write helpers
binary cube export patterns for vertex LUTs
summary JSON / diagnostics writer patterns
host_calibration_gui UDP capture protocol
```

### Refactor / replace

```text
replace Delaunay as default solver with math-model measured builder
split RGB-only and RGBW topology models cleanly
replace hardcoded ramp arrays with response providers
make pass/fail dictionary first-class
add WX mode taxonomy: strict_subgamut, wx_radial_virtual, wx_virtual_axis_maxbright, wx_lp_legacy
add emitter classification for inner / outer / edge emitters
add layered simplex solving for RGBCCT, RGBY, RGBV, and mixed emitter packages
add virtual reference hull / virtual-emitter remapping profile preprocessing
add learning response profiles and observed chromaticity curves for correction sessions
make out-of-hull projection shared between builder and verifier
add local measured triangle/simplex correction as first-class correction primitive
unify LUT metadata across all exporters
add tetrahedral coefficient LUT export for MCU/runtime speed paths
move TemporalBFI dense response handling behind indexed/chunked backend
```

### Keep as legacy/reference

```text
Delaunay point-cloud solver
LP measured-white solver / wx_lp_legacy reference mode
deprecated wx_legacy_virtual_axis name as compatibility alias for wx_virtual_axis_maxbright
legacy LUT builder-specific GUI toggles that no longer apply
```

---

## Example CLI targets

Model-only RGBW Rec.2020 LUT:

```bash
rgbw-lut-build \
  --mode model-only \
  --output-family rgbw16 \
  --gamut rec2020 \
  --input-transfer linear \
  --cube-size 256 \
  --interpolation tetrahedral
```

Model-only RGBW Rec.2020 LUT using radial WX white overdrive:

```bash
rgbw-lut-build \
  --mode model-only \
  --output-family rgbw16 \
  --gamut rec2020 \
  --input-transfer linear \
  --rgbw-mode wx_radial_virtual \
  --wx-target-position 0.70 \
  --cube-size 256 \
  --interpolation tetrahedral
```

Model-only RGBW Rec.2020 LUT using virtual-axis max-brightness WX:

```bash
rgbw-lut-build \
  --mode model-only \
  --output-family rgbw16 \
  --gamut rec2020 \
  --input-transfer linear \
  --rgbw-mode wx_virtual_axis_maxbright \
  --cube-size 256 \
  --interpolation tetrahedral
```

Model-only RGBW Rec.2020 LUT using radial WX and MCU-oriented coefficient tetra export:

```bash
rgbw-lut-build \
  --mode model-only \
  --output-family rgbw16 \
  --gamut rec2020 \
  --input-transfer linear \
  --rgbw-mode wx_radial_virtual \
  --wx-target-position 0.70 \
  --cube-size 33 \
  --interpolation tetrahedral \
  --interpolation-runtime coefficient_tetra \
  --coefficient-format int32 \
  --emit-size-report
```

Model-only RGBCCT LUT using layered warm/cool inner-anchor solving:

```bash
rgbw-lut-build \
  --mode model-only \
  --output-family rgbcct16 \
  --gamut rec709 \
  --input-transfer linear \
  --emitter-profile profiles/rgbcct_strip_a.json \
  --multi-emitter-mode layered_simplex \
  --inner-anchor-policy cct_matched \
  --cube-size 65 \
  --interpolation tetrahedral
```

Model-only RGBY+W LUT with yellow treated as an outer-hull emitter:

```bash
rgbw-lut-build \
  --mode model-only \
  --output-family rgbyw16 \
  --gamut native \
  --input-transfer linear \
  --emitter-profile profiles/rgbyw_strip_a.json \
  --multi-emitter-mode layered_simplex \
  --outer-hull-policy measured_hull \
  --inner-anchor-policy max_white \
  --cube-size 65 \
  --interpolation tetrahedral
```

Live measured calibration:

```bash
rgbw-lut-build \
  --mode live-measured \
  --output-family rgbw16 \
  --gamut rec709 \
  --input-transfer linear \
  --host 192.168.1.50 \
  --port 19446 \
  --capture-plan plans/rec709_sparse_hsv.csv \
  --max-retries 4
```

---

## Roadmap phases

## Phase 1 status snapshot

```text
completed now:
  split roadmap and math-model documentation
  copied rgbw_lut_gui and the current measured/delaunay support modules into the standalone package
  fixed standalone package-local imports for the transitioned GUI modules
  added shared standalone project/config path defaults under rgbw_lut_builder.paths
  moved GUI state/config defaults under repo-scoped config/ instead of beside package source files
  added pyproject.toml and console entrypoints for the currently transitioned surfaces
  added tools/build_lut.py as a single front-door dispatcher for gui / measured / delaunay / analyze

still pending inside phase 1:
  move more reusable Delaunay/worker/memory/export utilities out of gui-era modules and into their target packages
  separate legacy/reference solver surfaces from the future build/ package API
  standardize WX metadata names outside the currently transitioned GUI path
```

### Phase 1: repository split and cleanup

```text
move rgbw_lut_gui into standalone repo
move reusable Delaunay/worker/memory/export utilities
move math-model builder into package modules
separate legacy solver modes from new model-measured mode
standardize WX mode names and metadata
standardize metadata and config paths
split roadmap and math-model documentation
```

### Phase 2: RGB and RGBW model unification

```text
add explicit RGB-only model path
keep RGBW strict sub-gamut model path
add explicit WX radial virtual-primary model path
keep LP max-white as wx_lp_legacy reference path
add wx_virtual_axis_maxbright as a first-class high-brightness WX path
share gamut transforms and hull projection
share tetrahedral LUT sampling assumptions
add output-family metadata everywhere
```

### Phase 3: measured response providers

```text
implement ChannelResponseProvider API
load fill16 channel ramps
load hardcoded fallback ramps
add TemporalBFI dense response backend with chunked/indexed lookup
add HybridResponseProvider source precedence
```

### Phase 4: model-vs-capture diagnostics

```text
load patch captures
compute model prediction for each capture
compare strict_subgamut, wx_radial_virtual, wx_virtual_axis_maxbright, and wx_lp_legacy residuals
write model_vs_capture_report.csv
separate results by gamut, transfer, output family, topology, and Y bucket
reuse existing verifier/pass-fail dictionary structure
```

### Phase 5: virtual reference hull and response learning

```text
implement slightly-expanded virtual reference hull generation
project/remap measured emitters into stored virtual emitter profiles
separate solver geometry coordinates from physical output channel tuples
add active-channel-family grouping to verifier reports
aggregate pass/fail records into CorrectionResponseProfile artifacts
fit simple ObservedResponseCurve summaries for W/no-W edge comparisons
use learned response direction to bias correction candidates before live probing
write diagnostics showing where virtual expansion helps, hurts, or remains uncertain
```

### Phase 6: multi-emitter layered simplex support

```text
load emitter profiles with arbitrary channel counts
classify emitters by measured chromaticity relative to the device hull
build outer-hull triangle fans for each inner anchor
solve RGBCCT-style warm/cool inner-anchor layers
solve RGBY/RGBV-style outer-hull-expanded packages
share known-point / simplex expansion logic with capture-cloud correction
write diagnostics for hull classification, ambiguous edge emitters, and inner-anchor blends
add degenerate inner-anchor line fallback for overdrive prediction models
ensure strict sub-gamut mode continues to solve only direct legal edge/hull pairs
```

### Phase 7: offline correction field

```text
fit conservative residual correction maps
build/rank local measured triangle/simplex candidates
apply corrections to model candidates
use pass/fail dictionary as final override/block
write before/after diagnostics
```

### Phase 8: live UDP active calibration

```text
builder sends capture requests to host_calibration_gui
receive full spotread measurement payloads
update pass/fail dictionary during calibration
retry candidate corrections until pass or retry budget exhausted
save live_capture_session.jsonl and live_retry_trace.csv
```

### Phase 9: output backend generalization

```text
RGB8 / RGB16
RGBW8 / RGBW16
generic channels16 outputs
TemporalBFI encoder
APA102 encoder
HD108 encoder
HyperHDR export
C header export
binary cube export
coefficient tetrahedral cube export
MCU/SBC size-report tooling for 8 / 16 / 32 MB PSRAM targets
reference fixed-point tetrahedral samplers
```

### Phase 10: adaptive capture planning

```text
use model confidence and correction uncertainty to choose new probes
support sparse capture sets for normal users
support dense research datasets for advanced calibration
stop capturing once each region has enough support
```

### Later exploration: physical-solution policy axis

Strict sub-gamut and WX are different slices of a larger physical-solution space. A future post-core-builder path could expose an additional input/policy axis:

```text
RGB + extraction/overdrive axis
RGB + desired physical utilization axis
RGB + scene/headroom axis
```

Practical forms:

```text
two or three 3D LUTs + runtime blend coefficient
base cube + WX delta cube
sparse 4D LUT with a small mode-axis grid
analytical mode selection feeding paired LUTs
```

This is intentionally deferred until the core builder is stable.

---

## Performance goals

Requirements:

```text
parallel LUT generation
bounded-memory worker scheduling
chunked reads for TemporalBFI datasets
memory-mapped LUT output
indexed capture lookups
indexed local-triangle/simplex candidate lookup
indexed observed-response-curve lookup by active channel family
cache channel-response windows
avoid 8-bit internal staging
optional tetrahedral coefficient precompute/export
LUT size estimator for vertex and coefficient runtime formats
reuse existing worker/memory utilities where practical
```

Runtime consumers should prefer:

```text
tetrahedral interpolation
fixed-point coordinate mapping where possible
axis index/fraction precompute tables for 10/12/16-bit inputs
coefficient_tetra runtime format when memory budget allows
vertex_tetra runtime format when storage is more constrained
read-only LUT sharing across worker threads
```

---

## Design status

```text
GUI/display/export plumbing: mostly available, with standalone package imports/path defaults now in place for the transitioned GUI slice
capture loading and memory-aware build plumbing: available in legacy builder
pass/fail verifier feedback dictionary: mostly available
host GUI live capture protocol: available
math-model LUT builder: available as model-only path
WX radial / virtual-axis max-brightness / LP white-overdrive model family: available as functional experimental path
multi-emitter layered simplex model: design documented, including degenerate inner-anchor line fallback, needs implementation
RGB-only device model: needs explicit branch
ChannelResponseProvider abstraction: needs implementation
TemporalBFI response backend: needs implementation
measured correction field: needs implementation
local measured triangle/simplex correction: needs implementation
tetrahedral coefficient LUT export/runtime sampler: needs implementation
live active correction loop: needs implementation
```

The near-term target is to turn the current model-only builder into the primary model-measured builder, using the existing GUI, display profile, output, worker, and dictionary infrastructure wherever possible.
