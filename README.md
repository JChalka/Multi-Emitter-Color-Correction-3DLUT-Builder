# Multi-Emitter Color Correction 3DLUT Builder

A model-guided, measurement-corrected LUT builder for mapping declared linear RGB input into calibrated LED output values.

The project began as an RGBW capture-analysis and measured white-extraction toolkit, but the roadmap has expanded into a more general display-correction style pipeline for RGB, RGBW, TemporalBFI, and arbitrary multi-emitter LED packages.

The core goal is:

```text
source RGB in a known gamut
→ topology-aware device model
→ measured/instrument-corrected emitter response
→ capture-cloud correction and verifier feedback
→ RGB / RGBW / channels16 / TemporalBFI output LUT
```

This repository is currently in transition. It contains the legacy/current RGBW capture-analysis builder, transitional sources, and the early standalone `rgbw_lut_builder` package structure. The detailed roadmap is the source of truth for what is already implemented, what has moved, and what remains planned.

---

## Documentation map

Start here for orientation, then use the deeper docs when implementing or reviewing solver behavior.

| Document | Purpose |
| --- | --- |
| [`README_ROADMAP.md`](README_ROADMAP.md) | Full project roadmap, implementation phases, migration plan, current status, and future builder direction. |
| [`README_MATH_MODEL.md`](README_MATH_MODEL.md) | Detailed solve architecture for RGB, strict RGBW sub-gamut, WX / white-overdrive, capture-cloud correction, and multi-emitter strict/overdrive models. |
| [`README_CIE_VIRTUAL_HULL_WHITE_CAPACITY_PROFILE_PREPROCESS.md`](README_CIE_VIRTUAL_HULL_WHITE_CAPACITY_PROFILE_PREPROCESS.md) | Future virtual-reference-hull / virtual-emitter profile preprocessing design. |
| [`docs/project_function_tree.md`](docs/project_function_tree.md) | Generated lookup layer mapping roadmap items to current modules, legacy sources, and candidate functions. |

---

## Current repository state

The current repository still carries forward the first-generation RGBW tooling:

```text
RGBW capture analysis
measured-basis white extraction experiments
interactive tkinter/matplotlib LUT GUI
legacy Delaunay / measured cube utilities
True16 calibration header export
binary RGB/RGBW cube export
verifier feedback/pass-fail plumbing
transitional standalone package modules
```

The newer standalone package direction is already partially represented in `rgbw_lut_builder/`, but not every roadmap item has landed yet. The current roadmap tracker marks repository split / cleanup and the main RGB/RGBW model-unification work as the earliest completed phases, while response providers, display profiling, measured correction, virtual-reference hulls, multi-emitter layered simplex support, output backends, and live adaptive calibration continue as later phases.

Use `docs/project_function_tree.md` when you need to answer questions like:

```text
which legacy function should move next?
which module owns this roadmap item?
which placeholder package is meant to receive this logic?
```

---

## What this builder is trying to achieve

The project is moving away from a narrowly scoped RGBW white-extraction LUT and toward a general measured color-correction system for LED emitters.

The central design rule is:

```text
math model = physical/topological prediction axis
patch captures = real-world correction field
pass/fail dictionary = measured truth override
local line/triangle/simplex solve = shared primitive for prediction and correction
multi-emitter packages = strict sub-gamut or layered overdrive composition, not unconstrained N-channel solving
```

The math model should produce a sane initial LUT and define legal device topology. Measurements then correct the model for real hardware and setup behavior:

```text
LED package spectra
wall / diffuser / optics response
bench vs installed geometry
channel response and low-end drift
TemporalBFI behavior
instrument bias
verifier pass/fail evidence
```

The builder is intended to behave more like a display profiling and correction tool than a one-off RGBW heuristic.

---

## What is changing compared with the original builder

The original builder family was centered around:

```text
measured RGBW captures
bounded-error white extraction
classic min(rgb) neutral bias
measured family priors
coarse cube solve
upsampled dense LUT export
```

That workflow was useful, but it did not fully encode the things the project now needs:

```text
named source gamuts as first-class targets
linear-light LED LUT contracts
strict RGB/RGBW topology legality
shared builder/verifier out-of-hull projection
explicit strict vs WX / overdrive model families
selectable endpoint / luminance-clipping policy
capture-cloud local simplex correction
instrument-corrected measurement profiles
arbitrary emitter packages and channel counts
tetrahedral / coefficient-tetra runtime LUT targets
```

The new direction is:

```text
declared linear source gamut
→ topology-aware model solve
→ measured response provider
→ instrument-corrected capture data
→ local measured correction field
→ pass/fail and response-learning override
→ tetrahedral RGB/RGBW/channels LUT
```

---

## Solver families

### RGB-only

RGB-only devices use a normal three-primary solve:

```text
linear RGB in selected source gamut
→ project/map into measured device RGB triangle
→ RGB output values
```

This is intended for ordinary RGB LED strips and RGB SPI devices such as APA102 or HD108 when no white diode is present.

### Strict RGBW sub-gamut

For RGBW packages, the W diode lives inside the RGB triangle and divides the physical hull into:

```text
RGW
RBW
BGW
```

Strict mode is the default correctness path. It only emits legal topology families:

```text
black
R, G, B, W
RG, RB, BG
RW, GW, BW
RGW, RBW, BGW
```

It intentionally avoids arbitrary four-channel RGBW output.

### Luminance endpoint / clipping policy

Strict topology defines which channels are allowed to participate. It does not, by itself, define what should happen when a target chromaticity and requested Y would force a participating channel past its available endpoint.

That behavior should be a profile-selected policy rather than an implicit physical rule:

```text
y_correct_clip:
    solve the requested Y directly. If the tuple still has physical headroom,
    emit that raw solve. Once any participating channel exceeds full drive,
    clamp proportionally to the physical endpoint instead of scaling that
    endpoint by the input/value axis.

rolloff_after_clip:
    follow the clipping/Y-correct result near the limit, then apply a smooth
    compression/rolloff so values after the clipping point still have usable
    channel granularity instead of flattening into a hard plateau.

scale_to_full_endpoint:
    legacy/current behavior. Treat the clipped full-endpoint tuple as the
    chromaticity anchor and scale it by the input/value axis. This preserves
    smooth channel granularity but is a profile choice, not a physically strict
    target-Y contract.
```

Example:

```text
half-scale yellow target
proportional physical endpoint:  R=65535, G≈34210

scale_to_full_endpoint:
    scales the endpoint by value: R≈32767, G≈17105

y_correct_clip:
    follows the raw requested-Y solve until it clips, then lands on the
    proportional endpoint:       R=65535, G≈34210

rolloff_after_clip:
    transitions between the raw requested-Y path and the endpoint-scaled path
    with a smooth knee so the clipped region retains usable gradation.
```

The builder should record this policy in LUT metadata, verifier reports, and correction dictionaries so measurements from Y-correct, rolloff, and endpoint-scaled cubes are not mixed.

### WX / white-overdrive

WX modes are opt-in white-extraction / brightness-overdrive models. They allow higher W participation through constrained virtual-primary solves rather than unconstrained RGBW optimization.

Current planned taxonomy:

```text
strict_subgamut              default topology-safe RGBW solve
wx_radial_virtual            radial virtual-primary white-overdrive model
wx_virtual_axis_maxbright    virtual-axis max-brightness / high-W model
wx_lp_legacy                 direct LP max-white endpoint / reference model
```

These modes are especially relevant for ambilight / wallwash / HDR-style usage where higher W participation and higher brightness may be useful, as long as verifier data shows the residuals are predictable and correctable.

### Multi-emitter sub-gamut and overdrive

The roadmap extends beyond RGBW. Future profiles can describe packages such as:

```text
RGBCCT
RGB + warm white + cool white
RGBY / RGBV
RGBY+W
RGB+CCT+Y
other arbitrary emitter sets
```

For 5+ emitter packages, the important split is:

```text
strict multi-emitter sub_gamut:
    direct legal topology solve
    build the legal hull / fan / bridge topology from measured emitters
    choose one containing line/triangle/simplex at a time
    rank overlaps by efficiency, residual, headroom, and profile policy
    do not solve multiple inner-anchor layers and then blend them

multi-emitter overdrive / layered simplex:
    opt-in virtual prediction model
    solve one or more inner-anchor or virtual layers first
    treat those solved layers as known points
    solve/blend between those known points for brightness, CCT, or measured-dE policy
```

A CCT-style `RGB + CW + WW` profile is the useful example. Strict sub-gamut is
not just "pick RGB+CW or RGB+WW". With three outer emitters and two inner
emitters, the legal topology includes direct outer-edge-to-inner triangles and
direct outer-to-inner-pair bridge triangles:

```text
Black

Singles:
    R, G, B, CW, WW

Duals:
    RG, RB, BG
    R+CW, G+CW, B+CW
    R+WW, G+WW, B+WW
    CW+WW

3-channel direct strict candidates:
    RG+CW, RB+CW, BG+CW
    RG+WW, RB+WW, BG+WW
    R+CW+WW, G+CW+WW, B+CW+WW
```

Those candidates are all still strict sub-gamut candidates because each one is a
single direct legal topology. The overdrive model is the separate mode that
solves a CW layer and a WW layer, then solves between the two solved results.

When multiple direct strict candidates overlap, the default policy should favor
emitter/power efficiency because that is the practical purpose of sub-gamut
selection. Additional selectable policies should be documented and surfaced
because the "best" strict candidate is not always the same for every setup:

```text
power_efficiency:
    choose the direct solve that reaches the target with the lowest estimated
    current / power, or highest Y per normalized current.

channel_resolution:
    choose the solve that preserves the most useful channel granularity near its
    limiting/max point while still solving the requested chromaticity. This can
    avoid candidates where one channel is almost unused, quantized too coarsely,
    or driven into a region with poor low-end precision.

y_preserving_split:
    when a split region can choose between candidates such as RB+CW and RB+WW,
    prefer the decision boundary that keeps solved Y / max-achievable Y similar
    across neighboring input values, so one side of the split does not become
    unintentionally brighter than the other.

distance_inner_fit:
    for an ambiguous outer-edge region with two possible inner anchors, compare
    the target xy distance to InnerA, InnerB, OuterA, and OuterB. Choose the
    direct OuterA+OuterB+Inner candidate whose inner-anchor fit is closer to the
    target xy, then fall through to hysteresis, measured evidence, efficiency,
    or deterministic profile order when the two fits are effectively tied.

virtual_inner_anchor:
    optionally create a constrained virtual inner anchor for a missing hue-side
    region, such as a magenta-side RB bridge built from CW+WW behavior. This is
    technically overdrive, not strict sub_gamut, but it can be exposed as a
    controlled virtual-gamut/virtual-primary policy.
```

Distance-based inner fit remains a strict policy: it only decides which one
legal direct simplex owns the ambiguous region. It does not solve both inner
anchors and blend the results. Borders can still tie, so the profile should
record a deterministic tie-break policy and the verifier should report where
that tie-break was used.

For the virtual-inner-anchor policy, the virtual point should not be introduced
alone. A single RB-side virtual primary can make that sub-gamut brighter than the
others. The profile should build a balanced set of sibling virtual primaries
across the outer sectors, such as `RBCWWW`, `RGCWWW`, and `BGCWWW` virtual-primary equivalents,
so runtime behavior resembles a coherent expanded virtual primary set rather
than a one-off brightness spike.

---

## Display profiling and instrument correction

The measured builder treats capture quality as part of the display profile.

Earlier captures assumed a colorimeter-based workflow. The roadmap now includes an explicit instrument-correction layer so a spectrophotometer reference, such as an EFI ES-3000-class instrument, can be used to correct the faster colorimeter used for large capture sweeps.

The intended separation is:

```text
instrument profile:
    how the measurement device should be corrected

display / emitter profile:
    what the LED + wall/diffuser/optics setup emits

correction field:
    how measured display behavior deviates from the math model
```

The first implementation target should lean on the existing ArgyllCMS correction workflow rather than inventing a repo-specific correction format first. Argyll `ccxxmake` can generate both matrix corrections (`.ccmx`) and spectral sample corrections (`.ccss`), and `spotread` can already consume those correction files directly:

```bash
spotread -v -X my_matrix.ccmx
spotread -v -X my_spectral.ccss
```

That means the builder can treat Argyll CCXX artifacts as the canonical first implementation path:

```text
paired spectro/colorimeter captures
→ ccxxmake-generated CCMX or CCSS
→ spotread -X correction during future captures
→ raw + corrected XYZxyY stored in capture/verifier data
→ builder consumes corrected measurements by default
```

Repo-side instrument profiles should therefore record the Argyll correction artifact path/type, validation stats, instrument ids, display geometry, and raw/corrected measurement policy. A small native JSON wrapper can reference or copy the `.ccmx` / `.ccss` file and store builder-specific metadata without replacing the Argyll format.

A fallback internal 3×3 representation can still exist for diagnostics or environments where applying `spotread -X` is not practical:

```text
XYZ_reference ≈ M · XYZ_colorimeter
```

Raw and corrected measurements should both be preserved. Corrected XYZxyY should become the default data used by the builder/verifier when a valid correction profile is attached.

---

## Spectral characterization and lighting-quality reports

Because the workflow now includes spectrophotometer readings in addition to faster corrected colorimeter captures, the project can expose more than XYZxyY correction data. Spectro captures should also be usable for deeper emitter and system characterization.

The builder should distinguish between:

```text
colorimeter / corrected XYZxyY:
    fast capture path for dense calibration, verifier runs, and LUT correction

spectrophotometer SPD:
    slower reference path for spectral correction, emitter analysis,
    CRI / TM-30 / TLCI-style reports, and deeper optical characterization
```

Per-emitter reports should be able to summarize each physical channel and each important mixed family:

```text
single emitters:
    R, G, B, W, CW, WW, amber, violet, yellow, etc.

mixed references:
    neutral ramp
    strict sub_gamut white / near-white families
    WX / overdrive high-W families
    RGB+CCT inner-anchor blends
    user-selected representative output tuples
```

Initial spectral metrics should include:

```text
basic SPD metadata:
    wavelength range, wavelength step, peak wavelength, dominant wavelength,
    centroid wavelength, FWHM, CCT/Duv where meaningful, x/y and u'/v'

CRI / CIE 13.3:
    Ra, individual Ri values, and common R9/R12-style red/blue-green checks

ANSI/IES TM-30:
    Rf, Rg, local hue-bin fidelity/chroma/hue shifts, and color-vector graphic data

additional optional report families:
    CIE 224-style fidelity metrics where useful
    TLCI / camera-lighting consistency for video-oriented setups
    SSI-style spectral similarity when a reference spectrum is selected
    LM-79-style photometric/colorimetric summary fields for SSL-style reporting
```

These reports are not required for building a LUT, but they are useful for understanding what the emitters and wall/diffuser system actually are. A user may want to know whether a high-W maxbright path is only bright, whether a custom RGB+CCT strip has useful rendering quality, or whether a wallwash setup has poor red fidelity even after the LUT is chromatically accurate.

The report path should preserve raw inputs and derived outputs:

```text
SpectralMeasurement:
    instrument_id
    correction_id / Argyll CCXX id when applicable
    emitter_profile_id
    geometry_id
    output_tuple / active_channel_family
    wavelength_nm[]
    spectral_power[]
    derived XYZxyY
    derived report metrics

SpectralReport:
    report_id
    display_profile_id
    emitter_profile_id
    report_standard: cri | tm30 | tlci | ssi | lm79_summary | custom
    per_emitter_results
    mixed_family_results
    validation_notes
    generated_at
```

The host GUI should eventually display these as HTML/CSV/JSON reports alongside the normal verifier tables. The standalone package should keep spectral analysis modular so LUT generation does not depend on CRI/TM-30 reporting libraries.

---

## Verification and correction direction

The verifier and correction layers are meant to evolve from isolated pass/fail hints into a learned response model.

Instead of only asking whether a single patch passed, the builder should learn:

```text
For this active channel family and drive trajectory,
what chromaticity / luminance curve do we actually observe?
```

That allows the correction engine to reason about questions such as:

```text
Does adding W help this yellow/orange edge region?
Does this blue or purple failure come from weak B, wall drift, or bad W introduction?
Does a package behave differently at low Y than high Y?
Does a warm/cool inner-anchor blend need a different layer split?
```

The correction ladder is intended to be:

```text
exact verifier pass
→ measured local triangle/simplex correction
→ measured edge/pair correction
→ measured channel-ramp correction
→ math model prediction
→ hardcoded fallback
```

---

## Interpolation and runtime targets

Generated LUTs should be consumed with tetrahedral interpolation by default.

Tetrahedral interpolation matters because trilinear interpolation blends all eight cell corners, which can synthesize illegal or unintended channel participation between individually legal RGBW/multi-emitter vertices.

Planned runtime/storage forms:

```text
vertex_tetra:
    store output values at cube vertices
    lowest storage
    more runtime math/fetches

coefficient_tetra:
    precompute per-cell tetrahedral affine coefficients
    higher storage
    faster MCU/SBC runtime path
```

Target hardware includes host/SBC consumers as well as MCU targets such as ESP32-S3, ESP32-P4, and Teensy 4.x where PSRAM size and lookup time matter.

---

## Current practical usage

Use the current repository when you want to:

```text
analyze existing RGBW captures
inspect white usage and measured chroma behavior
run the current interactive RGBW LUT GUI
build/export the existing coarse/dense LUTs
generate True16 calibration headers
generate binary RGB/RGBW cubes for device testing
track Argyll CCXX / CCMX / CCSS instrument-correction metadata
generate or review spectral characterization reports
inspect per-emitter CRI / TM-30 / TLCI-style diagnostics
review migration targets for the standalone builder
```

Expect the standalone builder direction to be the better target when you need:

```text
strict gamut-aware RGB/RGBW output
separate strict vs WX white-overdrive models
Rec.709 / Rec.2020 / P3 / native linear-light behavior
TV/display-primary-aware target gamuts
instrument-corrected display profiles
Argyll CCXX / CCMX / CCSS colorimeter correction through spotread -X
per-emitter spectral reports, CRI, TM-30, TLCI, and related diagnostics
capture-cloud correction
arbitrary emitter profiles
strict and overdrive multi-emitter modes
RGBCCT / RGBY / RGBV / RGBY+W support
tetrahedral coefficient runtime LUTs
live adaptive calibration
```

---

## Repository orientation

Approximate current/future layout:

```text
rgbw_lut_builder/
  model/          # RGB/RGBW/WX/simplex/topology/gamut logic
  response/       # channel response providers and emitter profiles
  captures/       # capture schemas/loaders/UDP/spotread protocol
  profiling/      # instruments, CCXX correction, SPD records, CRI/TM-30 reports
  correction/     # pass/fail dictionaries, residuals, measured simplex correction
  verify/         # model-vs-capture metrics and reports
  output/         # RGB/RGBW/channels/TemporalBFI/binary/coefficient exports
  gui/            # GUI surfaces carried forward from current tooling
  legacy/         # preserved reference/transition code

tools/            # CLI entry points and utilities
docs/             # generated function tree and implementation notes
FILES_FOR_TRANSITION/  # legacy/transitional scripts used during migration
```

Some modules are already implemented, while others are intentionally placeholders for the roadmap phases. Check the roadmap and generated function tree before moving code so the implementation remains aligned with the planned ownership model.

---

## Development status

This is an active research/buildout repository. The current code is useful, but the roadmap describes a broader architecture than the original hosted builder.

No ETA is attached to the roadmap. The current focus is to preserve the working legacy/capture/export tools while moving the project toward a standalone measured LUT builder with explicit models, instrument-corrected profiles, richer verifier feedback, and scalable multi-emitter support.
