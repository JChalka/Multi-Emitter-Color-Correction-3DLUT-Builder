# RGBW LUT Builder Roadmap

A model-guided, measurement-corrected LUT builder for mapping linear RGB color into calibrated LED output values.

This document is the **roadmap / integration README**. It is intentionally focused on repository transition state, ownership, migration phases, and implementation sequencing rather than repeating every solver detail.

Use the companion docs for deeper descriptions:

```text
README.md             project overview and detailed feature framing
README_MATH_MODEL.md  solve equations, algorithms, and policy examples
```

---

## Central design rule

```text
math model = physical/topological prediction axis
instrument/spectral profile = measurement-truth normalization layer
patch captures = real-world correction field
pass/fail dictionary = measured truth override
local triangle/simplex solve = shared primitive for prediction and correction
multi-emitter packages = strict sub-gamut or layered overdrive composition, not unconstrained N-channel solving
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

## Display profiling and instrument correction

The roadmap should track the repository work needed to make measurement quality
part of the display profile. The detailed instrument-correction design lives in
`README.md`; this file only needs the implementation-oriented summary.

Target behavior:

```text
spectrophotometer reference when available
→ Argyll CCXX / CCMX / CCSS correction artifact
→ spotread -X correction during colorimeter capture
→ raw + corrected XYZxyY stored in capture rows
→ builder/verifier consume corrected measurements by default
```

Keep these as separate artifacts:

```text
InstrumentProfile:
    identifies the colorimeter / spectro and spotread options

ArgyllCorrectionProfile:
    wraps .ccmx / .ccss path, kind, source instruments, geometry, and validation

DisplayProfile:
    references emitter profile, instrument profile, correction profile,
    geometry, reference white, and measurement policy
```

Fallback/internal matrix correction can remain as a diagnostic path:

```text
XYZ_reference ≈ M · XYZ_colorimeter
```

Roadmap ownership:

```text
rgbw_lut_builder/response/
    instrument and display/emitter profile records

rgbw_lut_builder/captures/
    raw/corrected XYZxyY capture schemas and spotread command integration

rgbw_lut_builder/verify/
    correction validation summaries and raw-vs-corrected report views

host calibration GUI:
    correction profile loading, spotread -X wiring, paired capture helpers,
    and raw/corrected display toggles
```

Rebuild or revalidate the correction when the LED package, wall/diffuser,
measurement geometry, instrument, spotread mode, or high-W/multi-emitter
spectral content changes enough that the old correction may no longer describe
the current setup.

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
verifier reference-white override controls
fallback diode-basis measurement / DiodeProfile fetch path
```

---

## Solver families to unify

This roadmap should only summarize the model families and their migration
ownership. The detailed solve equations, candidate lists, and policy examples
belong in `README_MATH_MODEL.md`; the top-level `README.md` carries the higher
level project explanation.

Target solver family taxonomy:

```text
rgb_only:
    measured three-primary solve for RGB LED/SPI devices.

strict_rgbw_subgamut:
    default RGBW correctness path using only legal R/G/B/W, RGB edge,
    W edge, and RGW/RBW/BGW families.

wx_radial_virtual:
    opt-in radial virtual-primary white-overdrive model.

wx_virtual_axis_maxbright:
    opt-in high-W / high-Y virtual-axis max-brightness model.

wx_lp_legacy:
    LP max-white reference behavior; compatibility/reference mode.

strict_multi_emitter_subgamut:
    5+ emitter direct topology solve. Build legal lines/triangles/simplexes from
    outer, edge, and inner emitters; choose one direct candidate at a time.

multi_emitter_overdrive_layered_simplex:
    opt-in virtual/layered solve. Solve inner-anchor or virtual layers first,
    then solve/blend between those solved KnownPoints.
```

Strict 5+ emitter work should remain separate from overdrive work:

```text
strict sub_gamut:
    one legal direct simplex owns the output
    examples: outer-edge+inner, outer+inner+inner, inner bridge lines
    no solved-layer blending

overdrive / layered simplex:
    generated or solved KnownPoints are allowed
    blending between solved layers is explicit metadata
    residuals are reported separately from strict topology residuals
```

Shorthand strict-overlap policy list:

```text
power_efficiency:
    default; lowest estimated current / highest Y per current.

channel_resolution:
    prefer candidates with better usable channel precision/headroom.

y_preserving_split:
    place ambiguous split boundaries where solved/max Y stays continuous.

distance_inner_fit:
    for ambiguous inner-emitter regions, compare target xy distance to the local
    InnerA / InnerB / OuterA / OuterB neighborhood and select the direct
    OuterA+OuterB+Inner simplex with the closer inner-anchor fit.

virtual_inner_anchor:
    constrained overdrive/virtual-primary policy for missing hue-side inner
    anchors; requires balanced sibling virtual primaries, not one isolated point.
```

Current transition targets:

```text
rgbw_lut_builder/model/simplex.py
    shared line/triangle/simplex solve and expansion primitive

rgbw_lut_builder/model/topology.py
    legal candidate generation, strict-vs-overdrive guards, overlap policy owner

rgbw_lut_builder/model/layered_simplex.py
    explicit overdrive / virtual KnownPoint layer composition

rgbw_lut_builder/model/emitter_classification.py
    outer / inner / edge classification and ambiguous-edge handling

rgbw_lut_builder/response/multi_emitter_profile.py
    arbitrary channel-count emitter profiles, current/Y metadata, policy knobs

rgbw_lut_builder/verify/reports.py
    diagnostics for selected topology, overlap policy, tie-breaks, and residuals
```

Implementation rule:

```text
strict and overdrive outputs must not be mixed in the same feedback bucket;
verifier reports and correction dictionaries must preserve model_family and
active_channel_family metadata.
```

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

### Instrument correction captures

Before large LED-response captures, the builder should be able to run a small paired instrument profile:

```text
spectrophotometer reference patches
colorimeter matched patches
Argyll ccxxmake CCMX/CCSS generation
optional internal matrix/spectral correction fit
holdout validation
instrument correction profile JSON wrapper export
```

Recommended first paired patch set:

```text
black / near-black
R, G, B, W full-drive
R, G, B, W mid-drive
neutral ramp
RG/RB/GB edges
RW/GW/BW boundaries
yellow/orange/rose/blue/magenta stress patches
WX high-W probes for the active overdrive model
```

This profile is not a replacement for LED correction data. It is the measurement-normalization layer that should be applied before the builder learns emitter ramps, pass/fail dictionaries, or capture-cloud response curves.

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
raw instrument XYZxyY
corrected instrument XYZxyY
instrument profile id
instrument correction profile id
measurement correction applied flag
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
InstrumentCorrectedCaptureResponseProvider
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
instrument correction profiles
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
build or load an instrument correction profile
build a prediction dictionary
capture model candidates
compute raw and corrected xyY / Lab / LCh / Luv error
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

When an Argyll correction artifact is attached, the GUI/capture client should add the correction file with `-X`:

```bash
spotread -x -O -X profiles/instrument_corrections/wallwash.ccmx
spotread -x -O -X profiles/instrument_corrections/wallwash.ccss
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
instrument_profile_id
instrument_correction_profile_id
measurement_correction_policy
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
instrument_id
instrument_correction_id
measurement_correction_applied
raw_XYZxyY_available
corrected_XYZxyY_available
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

    profiling/
      instruments.py
      argyll_ccxx.py
      spectral_correction.py
      matrix_correction.py
      paired_capture.py
      validation.py
      display_profile.py

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
    profile_instrument.py
    make_argyll_ccxx.py
    validate_instrument_profile.py
    run_live_capture.py
    convert_temporal_bfi_dataset.py
    generate_capture_plan.py

  docs/
    capture_protocol.md
    display_profiling.md
    instrument_correction.md
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
host_calibration_gui spotread raw measurement parsing
```

### Refactor / replace

```text
replace Delaunay as default solver with math-model measured builder
split RGB-only and RGBW topology models cleanly
replace hardcoded ramp arrays with response providers
add display/instrument profiling and spectro-derived colorimeter correction artifacts
prefer Argyll ccxxmake-generated CCMX/CCSS artifacts before introducing custom correction formats
make pass/fail dictionary first-class
add WX mode taxonomy: strict_subgamut, wx_radial_virtual, wx_virtual_axis_maxbright, wx_lp_legacy
add emitter classification for inner / outer / edge emitters
add strict multi-emitter sub_gamut candidate generation for 5+ emitter packages
add overdrive/layered-simplex solving for RGBCCT, RGBY, RGBV, and mixed emitter packages
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

Instrument correction profile from paired spectro/colorimeter captures, preferring Argyll CCXX output:

```bash
rgbw-lut-profile-instrument \
  --display-profile profiles/wallwash_tv.json \
  --reference-instrument efi-es-3000 \
  --target-instrument colorimeter-primary \
  --capture-plan plans/instrument_profile_rgbw_sparse.csv \
  --correction-type argyll-ccmx \
  --ccxxmake \
  --holdout-ratio 0.25 \
  --output profiles/instrument_corrections/tv_wallwash_es3000_to_colorimeter.json
```

Example corrected `spotread` invocation recorded by that profile:

```bash
spotread -v -x -O -X profiles/instrument_corrections/tv_wallwash.ccmx
```

Measured RGBW build using an instrument correction profile:

```bash
rgbw-lut-build \
  --mode offline-measured \
  --output-family rgbw16 \
  --gamut rec2020 \
  --input-transfer linear \
  --display-profile profiles/wallwash_tv.json \
  --instrument-correction profiles/instrument_corrections/tv_wallwash_es3000_to_colorimeter.json \
  --spotread-correction profiles/instrument_corrections/tv_wallwash.ccmx \
  --rgbw-mode wx_virtual_axis_maxbright \
  --cube-size 256 \
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

Use the tracker below as the canonical progress view.

Status meanings:

| Status | Meaning |
| --- | --- |
| done | The roadmap item has landed in repo-owned code and the pinned work below is the current ownership/evidence. |
| active | Some of the intended ownership is landed, but the roadmap item is not fully closed yet. |
| planned | The roadmap item is still design/backlog work with no pinned implementation yet. |

Rule for updates:

| What changes | How to update this roadmap |
| --- | --- |
| A roadmap item lands code | Update the row for that exact roadmap item, not a separate snapshot block. |
| Work is partial | Mark the row `active` and pin the exact files/commands already landed. |
| Work is complete | Mark the row `done` and keep the concrete owner modules in the same row. |

Detailed move map and function inventory:

| Lookup surface | Purpose |
| --- | --- |
| [docs/project_function_tree.md](docs/project_function_tree.md) | Phase-by-phase move map with target modules, current source surfaces, and candidate functions that still need to move or be reused. |
| [docs/project_function_tree.json](docs/project_function_tree.json) | Machine-readable inventory of the same module tree and roadmap move plan. |

Regenerate the inventory with:

```text
python tools/generate_function_tree.py
```

Use the roadmap rows below for task-level status and ownership, then use the function tree doc when you need to answer "which function moves from where into what file next?"

### Phase 1: repository split and cleanup

| Roadmap item | Status | Move target / source | Math / prototype | Pinned work |
| --- | --- | --- | --- | --- |
| move rgbw_lut_gui into standalone repo | done | `FILES_FOR_TRANSITION/rgbw_lut_gui.py -> rgbw_lut_builder/gui/rgbw_lut_gui.py`; entry surface `tools/build_lut.py` | n/a | `rgbw_lut_builder/gui/rgbw_lut_gui.py` is running from the standalone package and `tools/build_lut.py` exposes `gui` as a front door. |
| move reusable Delaunay/worker/memory/export utilities | done | Transitional/legacy Delaunay plumbing -> `rgbw_lut_builder/build/{diagnostics,live_measured,lut_writer}.py`, `rgbw_lut_builder/captures/{loaders,validators}.py`, `rgbw_lut_builder/correction/pass_fail_dictionary.py` | n/a | Shared ownership moved into those package modules; deprecated `gui/build_delaunay_rgbw_lut.py` now delegates those slices. |
| move math-model builder into package modules | done | `FILES_FOR_TRANSITION/xy_target_rgbw_model.py -> rgbw_lut_builder/legacy/xy_target_rgbw_model.py`, then staged into `rgbw_lut_builder/model/*` and `rgbw_lut_builder/build/model_only.py` | [README_MATH_MODEL.md](README_MATH_MODEL.md) | Legacy handoff lives in `rgbw_lut_builder/legacy/xy_target_rgbw_model.py`, with package-owned model/build entrypoints in `rgbw_lut_builder/model/*` and `rgbw_lut_builder/build/model_only.py`. |
| separate legacy solver modes from new model-measured mode | done | Split package front doors between `tools/build_lut.py` surfaces and `rgbw_lut_builder/build/model_only.py legacy-cli` passthrough | n/a | `tools/build_lut.py` exposes distinct surfaces and `rgbw_lut_builder/build/model_only.py` separates package-owned model entrypoints from `legacy-cli` passthrough. |
| standardize WX mode names and metadata | done | Legacy WX aliases/naming -> `rgbw_lut_builder/model/wx_modes.py` | [WX / white-overdrive model family](README_MATH_MODEL.md#7-wx--white-overdrive-model-family) | Canonical WX naming and alias normalization now live in `rgbw_lut_builder/model/wx_modes.py`. |
| standardize metadata and config paths | done | Source-adjacent config/output writes -> `rgbw_lut_builder/paths.py` and repo-scoped `config/` | n/a | Standalone defaults now live in `rgbw_lut_builder/paths.py` and GUI state/config writes are repo-scoped under `config/`. |
| split roadmap and math-model documentation | done | Integration tracking stays here; equations/prototypes stay in `README_MATH_MODEL.md` | [README_MATH_MODEL.md](README_MATH_MODEL.md) | Integration tracking remains in this file and model equations/details stay in `README_MATH_MODEL.md`. |

### Phase 2: RGB and RGBW model unification

| Roadmap item | Status | Move target / source | Math / prototype | Pinned work |
| --- | --- | --- | --- | --- |
| add explicit RGB-only model path | done | Legacy RGB-only transform/projection/3-point solve -> `rgbw_lut_builder/model/rgb_model.py` and `rgbw_lut_builder/build/model_only.py` | [RGB-only model](README_MATH_MODEL.md#5-rgb-only-model), [Out-of-hull projection](README_MATH_MODEL.md#3-out-of-hull-projection) | `rgbw_lut_builder/model/rgb_model.py` owns the RGB-only solve path and `rgbw_lut_builder/build/model_only.py build-cube --model-family rgb` now emits package-owned RGB16 LUTs. |
| keep RGBW strict sub-gamut model path | done | Legacy strict sub-gamut helpers -> `rgbw_lut_builder/model/topology.py` and `rgbw_lut_builder/model/rgbw_model.py` | [Strict RGBW sub-gamut model](README_MATH_MODEL.md#6-strict-rgbw-sub-gamut-model), [Common simplex solve](README_MATH_MODEL.md#2-common-simplex-solve) | `rgbw_lut_builder/model/rgbw_model.py` owns the strict RGBW solve path and `build-cube --model-family rgbw --method strict_subgamut` is the package-owned cube route. |
| add explicit WX radial virtual-primary model path | done | Legacy `_wx_radial_*` / virtual-primary solve surfaces -> `rgbw_lut_builder/model/wx_modes.py` and `rgbw_lut_builder/model/rgbw_model.py` | [WX family](README_MATH_MODEL.md#7-wx--white-overdrive-model-family), [WX common structure](README_MATH_MODEL.md#8-wx-common-virtual-primary-structure), [Preferred wx_radial_virtual](README_MATH_MODEL.md#9-preferred-wx-mode-wx_radial_virtual) | `rgbw_lut_builder/model/wx_modes.py` and `rgbw_lut_builder/model/rgbw_model.py` expose the canonical `wx_radial_virtual` route through the package model API. |
| keep LP max-white as wx_lp_legacy reference path | done | Legacy LP/max-white WX solve -> `rgbw_lut_builder/model/wx_modes.py` and `rgbw_lut_builder/model/rgbw_model.py` | [Reference wx_lp_legacy](README_MATH_MODEL.md#10-reference-wx-mode-wx_lp_legacy) | `wx_lp_legacy` remains a canonical selectable mode through `rgbw_lut_builder/model/wx_modes.py` and the package RGBW model surface. |
| add wx_virtual_axis_maxbright as a first-class high-brightness WX path | done | Legacy virtual-axis WX solve -> `rgbw_lut_builder/model/wx_modes.py` and `rgbw_lut_builder/model/rgbw_model.py` | [Max-brightness wx_virtual_axis_maxbright](README_MATH_MODEL.md#11-max-brightness-wx-mode-wx_virtual_axis_maxbright) | `wx_virtual_axis_maxbright` is normalized as a first-class mode and smoke-validated through `tools/build_lut.py model-only build-cube --model-family rgbw --method wx --wx-mode wx_virtual_axis_maxbright`. |
| share gamut transforms and hull projection | done | Legacy conversion/projection/barycentric helpers -> `rgbw_lut_builder/model/{gamuts,projection,simplex,topology}.py` | [Source gamut conversion](README_MATH_MODEL.md#source-gamut-conversion), [Common simplex solve](README_MATH_MODEL.md#2-common-simplex-solve), [Out-of-hull projection](README_MATH_MODEL.md#3-out-of-hull-projection) | `rgbw_lut_builder/model/projection.py` now owns strict LED-hull projection, `rgbw_lut_builder/model/simplex.py` owns the shared barycentric/NNLS/chromaticity primitives, and the package RGB/RGBW solvers use those owners directly. |
| share tetrahedral LUT sampling assumptions | planned | Planned owner is `rgbw_lut_builder/model/interpolation/*` plus `rgbw_lut_builder/output/coefficient_cube_export.py` and runtime samplers under `rgbw_lut_builder/runtime/` | [Tetrahedral LUT interpolation](README_MATH_MODEL.md#14-tetrahedral-lut-interpolation) | The package targets exist as placeholders; candidate source/build surfaces are tracked in `docs/project_function_tree.md`. |
| add output-family metadata everywhere | active | Current summary metadata in `rgbw_lut_builder/build/model_only.py` -> propagate into `rgbw_lut_builder/output/*` and `rgbw_lut_builder/verify/reports.py` | n/a | Package-owned model cube summaries now write `output_family` and `output_channels` in `rgbw_lut_builder/build/model_only.py`, but the same metadata still needs to be propagated consistently across all builders/verifiers/exports. |

### Phase 3: measured response providers

| Roadmap item | Status | Move target / source | Math / prototype | Pinned work |
| --- | --- | --- | --- | --- |
| implement ChannelResponseProvider API | planned | Legacy channel-response/ramp helpers from `rgbw_lut_builder/legacy/xy_target_rgbw_model.py` -> `rgbw_lut_builder/response/base.py` | [Correction response profiles and observed response curves](README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves) | `rgbw_lut_builder/response/base.py` exists as the target owner; detailed candidate helper functions are pinned in `docs/project_function_tree.md`. |
| load fill16 channel ramps | planned | Legacy per-channel ramp lookup plus capture loaders -> `rgbw_lut_builder/response/fill16_ramps.py` | n/a | `rgbw_lut_builder/response/fill16_ramps.py` exists as the target owner; candidate load/lookup functions are pinned in `docs/project_function_tree.md`. |
| load hardcoded fallback ramps | planned | Legacy strict Y/XYZ fallback curves and constants -> `rgbw_lut_builder/response/hardcoded_ramps.py` | n/a | `rgbw_lut_builder/response/hardcoded_ramps.py` exists as the target owner; candidate fallback helpers are pinned in `docs/project_function_tree.md`. |
| add TemporalBFI dense response backend with chunked/indexed lookup | planned | TemporalBFI conversion/host-capture surfaces -> `rgbw_lut_builder/response/temporal_bfi.py` | n/a | `rgbw_lut_builder/response/temporal_bfi.py` exists as the target owner; source surfaces are tracked in `docs/project_function_tree.md`. |
| add HybridResponseProvider source precedence | planned | Response-provider composition -> `rgbw_lut_builder/response/hybrid.py` | n/a | `rgbw_lut_builder/response/hybrid.py` exists as the target owner and will compose `base`, `fill16_ramps`, `hardcoded_ramps`, and `temporal_bfi`. |


### Phase 3A: display profiling and instrument correction

| Roadmap item | Status | Move target / source | Math / prototype | Pinned work |
| --- | --- | --- | --- | --- |
| add instrument/display profile schema | planned | New profile artifacts -> `rgbw_lut_builder/profiling/instruments.py` and `rgbw_lut_builder/profiling/display_profile.py` | [Display profiling and instrument correction](#display-profiling-and-instrument-correction) | Target modules should record colorimeter, spectro, geometry, raw/corrected measurement policy, and correction ids. |
| implement paired spectro/colorimeter capture workflow | planned | Host calibration GUI + UDP capture protocol + `tools/profile_instrument.py` -> `rgbw_lut_builder/profiling/paired_capture.py` | [Display profiling and instrument correction](#display-profiling-and-instrument-correction) | First version should render the same sparse RGBW/WX patch plan for the spectrophotometer and colorimeter and store paired raw XYZxyY. |
| integrate Argyll `ccxxmake` / CCXX artifacts | planned | Argyll wrapper + profile metadata -> `rgbw_lut_builder/profiling/argyll_ccxx.py`, `tools/make_argyll_ccxx.py`, and host GUI spotread command handling | [Preferred first implementation: Argyll CCXX / `ccxxmake`](#preferred-first-implementation-argyll-ccxx--ccxxmake) | Prefer `.ccmx` / `.ccss` output and feed it back into measurements via `spotread -X`; repo JSON should wrap the Argyll artifact with ids, geometry, validation, and raw/corrected policy. |
| fit 3x3 XYZ correction matrix | planned | New correction fitter -> `rgbw_lut_builder/profiling/matrix_correction.py` | [Display profiling and instrument correction](#fallbackinternal-implementation-paired-spectrocolorimeter-matrix-correction) | Keep this as fallback/debug/cross-check against Argyll `.ccmx`; fit `XYZ_reference ≈ M · XYZ_colorimeter`, with low-Y/outlier handling and a holdout validation report. |
| support spectral / CCSS-style correction metadata | planned | Future spectral artifact loader/exporter -> `rgbw_lut_builder/profiling/spectral_correction.py` | [Display profiling and instrument correction](#correction-artifacts) | Prefer Argyll `.ccss` when available; metadata should allow spectro-derived spectral sample corrections without changing capture schemas. |
| apply instrument correction in capture loaders | planned | Capture loaders and response providers -> `rgbw_lut_builder/captures/loaders.py`, `rgbw_lut_builder/response/*` | [Display profiling and instrument correction](#host-gui--capture-workflow) | Raw XYZxyY must be preserved; corrected XYZxyY becomes the default measurement used by builder/verifier when a valid correction profile is attached. |
| add verifier raw-vs-corrected diagnostics | planned | Verifier/report surfaces -> `rgbw_lut_builder/verify/reports.py` and host calibration GUI verifier | [Display profiling and instrument correction](#host-gui--capture-workflow) | Reports should show whether correction was applied and allow before/after error summaries for the correction profile. |

### Phase 4: model-vs-capture diagnostics

| Roadmap item | Status | Move target / source | Math / prototype | Pinned work |
| --- | --- | --- | --- | --- |
| load patch captures | planned | Capture loaders plus analyze surfaces -> `rgbw_lut_builder/verify/verifier.py` and `rgbw_lut_builder/captures/loaders.py` | [Capture-cloud simplex correction](README_MATH_MODEL.md#12-capture-cloud-simplex-correction) | `rgbw_lut_builder/verify/verifier.py` exists as the target owner; capture-loading candidates are pinned in `docs/project_function_tree.md`. |
| compute model prediction for each capture | planned | Legacy `verify_captures` / `_predict_xyz_from_rgbw16` plus package model entrypoints -> `rgbw_lut_builder/verify/verifier.py` and `rgbw_lut_builder/build/model_only.py` | [RGB-only model](README_MATH_MODEL.md#5-rgb-only-model), [Strict RGBW sub-gamut model](README_MATH_MODEL.md#6-strict-rgbw-sub-gamut-model), [WX family](README_MATH_MODEL.md#7-wx--white-overdrive-model-family) | The target owners exist; source candidate functions are pinned in `docs/project_function_tree.md`. |
| compare strict_subgamut, wx_radial_virtual, wx_virtual_axis_maxbright, and wx_lp_legacy residuals | planned | Legacy verification/report logic -> `rgbw_lut_builder/verify/{metrics,reports}.py` | [Strict RGBW sub-gamut model](README_MATH_MODEL.md#6-strict-rgbw-sub-gamut-model), [Preferred wx_radial_virtual](README_MATH_MODEL.md#9-preferred-wx-mode-wx_radial_virtual), [Reference wx_lp_legacy](README_MATH_MODEL.md#10-reference-wx-mode-wx_lp_legacy), [Max-brightness wx_virtual_axis_maxbright](README_MATH_MODEL.md#11-max-brightness-wx-mode-wx_virtual_axis_maxbright) | `rgbw_lut_builder/verify/metrics.py` and `rgbw_lut_builder/verify/reports.py` exist as targets; source mode-comparison functions are pinned in `docs/project_function_tree.md`. |
| write model_vs_capture_report.csv | planned | Legacy verifier CSV/report writers -> `rgbw_lut_builder/verify/reports.py` | n/a | `rgbw_lut_builder/verify/reports.py` exists as the target owner; source report writers are pinned in `docs/project_function_tree.md`. |
| separate results by gamut, transfer, output family, topology, and Y bucket | planned | Current summary/report logic -> `rgbw_lut_builder/verify/{metrics,reports}.py` | n/a | The target owners exist; the remaining work is to propagate model/output metadata through those reporting layers. |
| reuse existing verifier/pass-fail dictionary structure | planned | Existing dictionary ownership in `rgbw_lut_builder/correction/pass_fail_dictionary.py` -> reporting integration in `rgbw_lut_builder/verify/reports.py` | [Capture-cloud simplex correction](README_MATH_MODEL.md#12-capture-cloud-simplex-correction) | Existing dictionary ownership is in `rgbw_lut_builder/correction/pass_fail_dictionary.py`, but this phase has not wired it into model-vs-capture reporting yet. |

### Phase 5: virtual reference hull and response learning

| Roadmap item | Status | Move target / source | Math / prototype | Pinned work |
| --- | --- | --- | --- | --- |
| implement slightly-expanded virtual reference hull generation | planned | Math-model prototype -> `rgbw_lut_builder/model/{projection,emitter_classification}.py` | [Profile-space virtual reference hull](README_MATH_MODEL.md#4-profile-space-virtual-reference-hull) | `rgbw_lut_builder/model/projection.py` and `rgbw_lut_builder/model/emitter_classification.py` exist as target anchors. |
| project/remap measured emitters into stored virtual emitter profiles | planned | Math-model prototype -> `rgbw_lut_builder/model/projection.py` and `rgbw_lut_builder/response/multi_emitter_profile.py` | [Physical and virtual emitter records](README_MATH_MODEL.md#physical-and-virtual-emitter-records), [Solve using virtual geometry, expand through physical channels](README_MATH_MODEL.md#solve-using-virtual-geometry-expand-through-physical-channels) | Those target owners now exist as anchors; the function-level move map lives in `docs/project_function_tree.md`. |
| separate solver geometry coordinates from physical output channel tuples | planned | Virtual-geometry/profile separation -> `rgbw_lut_builder/model/simplex.py` and `rgbw_lut_builder/response/multi_emitter_profile.py` | [Solve using virtual geometry, expand through physical channels](README_MATH_MODEL.md#solve-using-virtual-geometry-expand-through-physical-channels) | Those target owners now exist as anchors. |
| add active-channel-family grouping to verifier reports | planned | Existing verifier feedback parsing/reporting -> `rgbw_lut_builder/verify/reports.py` | [Correction response profiles and observed response curves](README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves) | `rgbw_lut_builder/verify/reports.py` exists as the target owner; source feedback/report helpers are pinned in `docs/project_function_tree.md`. |
| aggregate pass/fail records into CorrectionResponseProfile artifacts | planned | Existing feedback-bank/report surfaces -> `rgbw_lut_builder/response/multi_emitter_profile.py` and `rgbw_lut_builder/correction/residuals.py` | [Correction response profiles and observed response curves](README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves) | Those target owners exist; current aggregation candidates are pinned in `docs/project_function_tree.md`. |
| fit simple ObservedResponseCurve summaries for W/no-W edge comparisons | planned | Existing feedback-bank surfaces -> `rgbw_lut_builder/correction/residuals.py` and `rgbw_lut_builder/response/multi_emitter_profile.py` | [Correction response profiles and observed response curves](README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves) | Those target owners exist; current summary candidates are pinned in `docs/project_function_tree.md`. |
| use learned response direction to bias correction candidates before live probing | planned | Existing candidate-ranking and retry hints -> `rgbw_lut_builder/correction/{triangle_ranker,live_retry}.py` | [Capture-cloud simplex correction](README_MATH_MODEL.md#12-capture-cloud-simplex-correction) | `rgbw_lut_builder/correction/triangle_ranker.py` and `rgbw_lut_builder/correction/live_retry.py` exist as target owners; source hint-merging functions are pinned in `docs/project_function_tree.md`. |
| write diagnostics showing where virtual expansion helps, hurts, or remains uncertain | planned | Existing analysis/report paths -> `rgbw_lut_builder/verify/{reports,metrics}.py` | [Why this helps edge colors](README_MATH_MODEL.md#why-this-helps-edge-colors) | `rgbw_lut_builder/verify/reports.py` and `rgbw_lut_builder/verify/metrics.py` exist as target owners; source diagnostics are pinned in `docs/project_function_tree.md`. |

### Phase 6: multi-emitter sub-gamut and overdrive support

| Roadmap item | Status | Move target / source | Math / prototype | Pinned work |
| --- | --- | --- | --- | --- |
| load emitter profiles with arbitrary channel counts | planned | New emitter-profile loader -> `rgbw_lut_builder/response/multi_emitter_profile.py` and `rgbw_lut_builder/model/emitter_classification.py` | [Multi-emitter sub-gamut and overdrive models](README_MATH_MODEL.md#13-multi-emitter-sub-gamut-and-overdrive-models) | Those target owners exist as anchors. |
| classify emitters by measured chromaticity relative to the device hull | planned | New emitter classification logic -> `rgbw_lut_builder/model/emitter_classification.py` | [Emitter classification](README_MATH_MODEL.md#emitter-classification) | `rgbw_lut_builder/model/emitter_classification.py` exists as the target owner. |
| build strict 5+ emitter sub_gamut candidate set | planned | Strict/simplex primitives plus new layered-simplex/topology owners -> `rgbw_lut_builder/model/{layered_simplex,simplex,topology}.py` | [Strict multi-emitter sub_gamut model](README_MATH_MODEL.md#131-strict-multi-emitter-sub_gamut-model) | Generate direct legal candidates including outer-edge+inner fans, outer+inner-pair bridge triangles, inner-inner lines, and overlap-policy ranking. |
| implement strict-overlap policy selector | planned | Policy/ranking helpers -> `rgbw_lut_builder/model/topology.py`, `rgbw_lut_builder/model/simplex.py`, and verifier metadata -> `rgbw_lut_builder/verify/reports.py` | [Strict candidate overlap policy](README_MATH_MODEL.md#strict-candidate-overlap-policy) | Add selectable policies for power efficiency, channel resolution, Y-preserving split decisions, distance-based inner-emitter fit, and user/profile hue/CCT bias. |
| implement distance-based inner-emitter fit policy | planned | Ambiguous-region ranking helper -> `rgbw_lut_builder/model/topology.py` with diagnostics in `rgbw_lut_builder/verify/reports.py` | [Policy: distance-based inner-emitter fit](README_MATH_MODEL.md#policy-distance-based-inner-emitter-fit) | For an ambiguous outer edge with InnerA/InnerB alternatives, rank direct OuterA+OuterB+Inner candidates by normalized target-xy distance/projection residual, then use hysteresis/measured evidence/efficiency as tie-breaks. |
| add constrained virtual-inner-anchor policy | planned | Virtual-primary/overdrive policy -> `rgbw_lut_builder/model/layered_simplex.py` and virtual profile metadata -> `rgbw_lut_builder/response/multi_emitter_profile.py` | [Policy: constrained virtual inner anchor](README_MATH_MODEL.md#policy-constrained-virtual-inner-anchor) | Treat missing-hue virtual anchors as constrained overdrive, not strict topology; require balanced sibling virtual primaries rather than a single isolated high-Y virtual point. |
| solve RGBCCT-style warm/cool overdrive layers | planned | New layered-simplex owner -> `rgbw_lut_builder/model/layered_simplex.py` | [RGBCCT / warm-cool overdrive model](README_MATH_MODEL.md#rgbcct--warm-cool-overdrive-model) | `rgbw_lut_builder/model/layered_simplex.py` exists as the target owner. |
| solve RGBY/RGBV-style outer-hull-expanded packages | planned | New layered-simplex owner -> `rgbw_lut_builder/model/layered_simplex.py` | [RGBY / RGBV / outer-hull expansion](README_MATH_MODEL.md#rgby--rgbv--outer-hull-expansion) | `rgbw_lut_builder/model/layered_simplex.py` exists as the target owner. |
| share known-point / simplex expansion logic with capture-cloud correction | planned | Shared simplex ownership -> `rgbw_lut_builder/model/simplex.py` and `rgbw_lut_builder/correction/measured_simplex.py` | [Common simplex solve](README_MATH_MODEL.md#2-common-simplex-solve), [Capture-cloud simplex correction](README_MATH_MODEL.md#12-capture-cloud-simplex-correction) | Those target owners exist; reusable solve/candidate functions are pinned in `docs/project_function_tree.md`. |
| write diagnostics for hull classification, overlap policy, ambiguous edge emitters, and inner-anchor blends | planned | New diagnostics/report owners -> `rgbw_lut_builder/verify/{reports,metrics}.py` | [Emitter classification](README_MATH_MODEL.md#emitter-classification) | Diagnostics should show which strict candidates overlapped, which policy selected the output, and where overdrive layers differ from strict direct topology. |
| add degenerate inner-anchor line fallback for overdrive prediction models | planned | New layered-simplex/simplex owner -> `rgbw_lut_builder/model/{layered_simplex,simplex}.py` | [Degenerate inner-anchor line fallback](README_MATH_MODEL.md#degenerate-inner-anchor-line-fallback) | Design is documented in the math model, and the package target owners already exist as anchors. |
| preserve strict-vs-overdrive policy metadata | planned | Guard strict topology in `rgbw_lut_builder/model/topology.py` while direct multi-emitter fan/bridge selection lands in `rgbw_lut_builder/model/layered_simplex.py` / `simplex.py` | [Strict multi-emitter sub_gamut model](README_MATH_MODEL.md#131-strict-multi-emitter-sub_gamut-model) | Store chosen overlap policy, tie-break source, active channel family, and strict/overdrive model family so correction data is not mixed. |

### Phase 7: offline correction field

| Roadmap item | Status | Move target / source | Math / prototype | Pinned work |
| --- | --- | --- | --- | --- |
| fit conservative residual correction maps | planned | Existing analysis/feedback surfaces -> `rgbw_lut_builder/correction/{correction_field,residuals}.py` | [Capture-cloud simplex correction](README_MATH_MODEL.md#12-capture-cloud-simplex-correction) | `rgbw_lut_builder/correction/correction_field.py` and `rgbw_lut_builder/correction/residuals.py` exist as target owners. |
| build/rank local measured triangle/simplex candidates | planned | Existing capture-match/candidate helpers -> `rgbw_lut_builder/correction/{measured_simplex,triangle_ranker}.py` | [Common simplex solve](README_MATH_MODEL.md#2-common-simplex-solve), [Capture-cloud simplex correction](README_MATH_MODEL.md#12-capture-cloud-simplex-correction) | Those target owners exist; source candidate ranking helpers are pinned in `docs/project_function_tree.md`. |
| apply corrections to model candidates | planned | Correction-field ownership plus offline builder integration -> `rgbw_lut_builder/correction/{correction_field,residuals}.py` and `rgbw_lut_builder/build/offline_measured.py` | [Capture-cloud simplex correction](README_MATH_MODEL.md#12-capture-cloud-simplex-correction) | Those target owners exist as anchors. |
| use pass/fail dictionary as final override/block | planned | Existing dictionary ownership in `rgbw_lut_builder/correction/pass_fail_dictionary.py` -> correction-field integration in `rgbw_lut_builder/correction/correction_field.py` | [Correction response profiles and observed response curves](README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves) | Dictionary ownership exists in `rgbw_lut_builder/correction/pass_fail_dictionary.py`, but correction-field integration is not pinned yet. |
| write before/after diagnostics | planned | New correction diagnostics/report owners -> `rgbw_lut_builder/verify/{reports,metrics}.py` | n/a | Those target owners exist as anchors; source report writers are pinned in `docs/project_function_tree.md`. |

### Phase 8: live UDP active calibration

| Roadmap item | Status | Move target / source | Math / prototype | Pinned work |
| --- | --- | --- | --- | --- |
| builder sends capture requests to host_calibration_gui | planned | Host GUI/live capture surfaces -> `rgbw_lut_builder/captures/udp_client.py` and `rgbw_lut_builder/build/live_measured.py` | n/a | Host-side protocol support exists, but builder-side active calibration wiring is not pinned yet. |
| receive full spotread measurement payloads | planned | Spotread protocol parsing -> `rgbw_lut_builder/captures/spotread_protocol.py` and `rgbw_lut_builder/build/live_measured.py` | n/a | Host-side protocol support exists, but builder-side active calibration wiring is not pinned yet. |
| update pass/fail dictionary during calibration | planned | Existing feedback-bank ownership -> `rgbw_lut_builder/correction/pass_fail_dictionary.py` plus live integration in `rgbw_lut_builder/correction/live_retry.py` | [Capture-cloud simplex correction](README_MATH_MODEL.md#12-capture-cloud-simplex-correction) | Dictionary ownership exists in `rgbw_lut_builder/correction/pass_fail_dictionary.py`, but live calibration integration is not pinned yet. |
| retry candidate corrections until pass or retry budget exhausted | planned | Live retry/probe orchestration -> `rgbw_lut_builder/correction/live_retry.py` and `rgbw_lut_builder/build/live_measured.py` | [Capture-cloud simplex correction](README_MATH_MODEL.md#12-capture-cloud-simplex-correction) | Those target owners exist as anchors; retry/probe source surfaces are pinned in `docs/project_function_tree.md`. |
| save live_capture_session.jsonl and live_retry_trace.csv | planned | Live session/report persistence -> `rgbw_lut_builder/build/live_measured.py` and `rgbw_lut_builder/verify/reports.py` | n/a | Those target owners exist as anchors. |

### Phase 9: output backend generalization

| Roadmap item | Status | Move target / source | Math / prototype | Pinned work |
| --- | --- | --- | --- | --- |
| RGB8 / RGB16 | active | Current model-cube export -> `rgbw_lut_builder/output/{rgb8,rgb16}.py` | n/a | Package-owned model cube output currently writes RGB16 in `rgbw_lut_builder/build/model_only.py`; broader backend generalization is still pending. |
| RGBW8 / RGBW16 | active | Current model-cube/header export -> `rgbw_lut_builder/output/{rgbw8,rgbw16}.py` | n/a | Package-owned model cube output currently writes RGBW16 in `rgbw_lut_builder/build/model_only.py`; broader backend generalization is still pending. |
| generic channels16 outputs | planned | Shared cube/header logic -> `rgbw_lut_builder/output/channels16.py` | n/a | `rgbw_lut_builder/output/channels16.py` exists as the target owner. |
| TemporalBFI encoder | planned | TemporalBFI conversion/export surfaces -> `rgbw_lut_builder/output/temporal_bfi_encoder.py` | n/a | `rgbw_lut_builder/output/temporal_bfi_encoder.py` exists as the target owner. |
| APA102 encoder | planned | New chipset backend -> `rgbw_lut_builder/output/apa102_encoder.py` | n/a | `rgbw_lut_builder/output/apa102_encoder.py` exists as the target owner. |
| HD108 encoder | planned | New chipset backend -> `rgbw_lut_builder/output/hd108_encoder.py` | n/a | `rgbw_lut_builder/output/hd108_encoder.py` exists as the target owner. |
| HyperHDR export | planned | GUI/export surfaces -> `rgbw_lut_builder/output/hyperhdr_export.py` | n/a | `rgbw_lut_builder/output/hyperhdr_export.py` exists as the target owner. |
| C header export | active | Shared header writers -> `rgbw_lut_builder/output/{c_header_export,mcu_header_export}.py` | n/a | Shared RGBW header writing exists in `rgbw_lut_builder/build/lut_writer.py`, but generalized output-backend ownership is still pending. |
| binary cube export | active | Shared `.npy` export -> `rgbw_lut_builder/output/binary_cube_export.py` | n/a | Shared `.npy` cube writing exists in `rgbw_lut_builder/build/lut_writer.py`, but generalized output-backend ownership is still pending. |
| coefficient tetrahedral cube export | planned | New coefficient exporter -> `rgbw_lut_builder/output/coefficient_cube_export.py` | [Tetrahedral LUT interpolation](README_MATH_MODEL.md#14-tetrahedral-lut-interpolation) | `rgbw_lut_builder/output/coefficient_cube_export.py` exists as the target owner. |
| MCU/SBC size-report tooling for 8 / 16 / 32 MB PSRAM targets | planned | MCU/export sizing surfaces -> `rgbw_lut_builder/output/{mcu_header_export,coefficient_cube_export}.py` | n/a | Those target owners exist as anchors. |
| reference fixed-point tetrahedral samplers | planned | Runtime sampler surfaces -> `rgbw_lut_builder/runtime/*` | [Tetrahedral LUT interpolation](README_MATH_MODEL.md#14-tetrahedral-lut-interpolation) | The runtime sampler reference files already exist as anchors under `rgbw_lut_builder/runtime/`. |

### Phase 10: adaptive capture planning

| Roadmap item | Status | Move target / source | Math / prototype | Pinned work |
| --- | --- | --- | --- | --- |
| use model confidence and correction uncertainty to choose new probes | planned | Planner/report heuristics -> `tools/generate_capture_plan.py` and `rgbw_lut_builder/verify/metrics.py` | [Capture-cloud simplex correction](README_MATH_MODEL.md#12-capture-cloud-simplex-correction), [Correction response profiles and observed response curves](README_MATH_MODEL.md#correction-response-profiles-and-observed-response-curves) | `tools/generate_capture_plan.py` and `rgbw_lut_builder/verify/metrics.py` exist as target owners; candidate scoring functions are pinned in `docs/project_function_tree.md`. |
| support sparse capture sets for normal users | planned | Planner policies -> `tools/generate_capture_plan.py` | n/a | `tools/generate_capture_plan.py` exists as the target owner. |
| support dense research datasets for advanced calibration | planned | Planner plus TemporalBFI response surfaces -> `tools/generate_capture_plan.py` and `rgbw_lut_builder/response/temporal_bfi.py` | n/a | Those target owners exist as anchors. |
| stop capturing once each region has enough support | planned | Planner stop conditions plus metrics -> `tools/generate_capture_plan.py` and `rgbw_lut_builder/verify/metrics.py` | [Capture-cloud simplex correction](README_MATH_MODEL.md#12-capture-cloud-simplex-correction) | Those target owners exist as anchors; candidate support/feedback functions are pinned in `docs/project_function_tree.md`. |

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
math-model LUT builder: copied legacy path still available, with package-owned model APIs plus initial package-owned RGB16/RGBW16 cube build path now landed for phase 2
WX radial / virtual-axis max-brightness / LP white-overdrive model family: available as functional experimental path
multi-emitter layered simplex model: design documented, including degenerate inner-anchor line fallback, needs implementation
RGB-only device model: package API plus initial package-owned LUT-build integration now available
display/instrument profiling and spectro-derived colorimeter correction: design documented, needs implementation
ChannelResponseProvider abstraction: needs implementation
TemporalBFI response backend: needs implementation
measured correction field: needs implementation
local measured triangle/simplex correction: needs implementation
tetrahedral coefficient LUT export/runtime sampler: needs implementation
live active correction loop: needs implementation
```

The near-term target is to turn the current model-only builder into the primary model-measured builder, using the existing GUI, display profile, output, worker, and dictionary infrastructure wherever possible.
