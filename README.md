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
| [`README_SPECTROPHOTOMETER_CCXXMAKE.md`](README_SPECTROPHOTOMETER_CCXXMAKE.md) | Spectrophotometer / colorimeter correction workflow, Argyll `ccxxmake` / CCXX artifact plan, and host GUI patch relay design. |
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


### Future physical-solution policy axis

A future research direction may allow more than one legal physical solution for
the same source RGB input. This is the refined version of the earlier
"interleaved RGB / assisted Y-linear" idea.

The important contract is emitted-Y, not region ownership:

```text
candidate LUTs:
    chromatic / no-inner candidate
    strict assisted candidate
    optional overdrive assisted candidate

runtime decision:
    preserve the source-domain input request
    compare the emitted-Y actually available from each candidate family
    choose the single candidate that gives the closest achievable Y / best local
    Y bracket
    sample only that selected candidate LUT
```

Candidate LUTs do not have to pretend that their own storage spans a local
`0..1` input domain. The builder may export each candidate as a partial
source-domain grid with nodes labelled by their actual input RGB position. For
example, RGB-only could cover low/mid source values, strict-assisted could cover
a partially overlapping mid/high range, and overdrive could begin only where it
has useful high-Y participation. Runtime still receives the normal `0..1` input
request, uses emitted-Y to choose the eligible candidate, and samples that
candidate using source-domain coordinates.

Black remains a shared early exit rather than a required node in every candidate
cube. Direct single-channel and dual-channel cases remain separate direct-family
storage because they cannot be overdriven by the same three-channel candidate
axis unless a profile explicitly redirects them into a virtual model.

This should not be implemented as one mixed-family 3D LUT, a coarse selector LUT,
or a device-space re-solve that rewrites the input RGB before sampling. Candidate
Y is a required runtime contract for this future mode because it defines the real
luminance values each candidate can emit.

The detailed theory and proposed storage/runtime format live in
[`README_MATH_MODEL.md`](README_MATH_MODEL.md) section 16. The roadmap keeps this
as future implementation / research rather than a current core-builder target.

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
    default-friendly cached split policy for ambiguous outer-edge regions with
    two possible inner anchors. Build a line or curve across the local trapezoid
    where both inner choices have equal distance/fit score, then choose the
    direct OuterA+OuterB+Inner simplex on the target's side of that boundary.
    Ties fall through to the configured inner-emitter preference, hysteresis,
    measured evidence, efficiency, or deterministic profile order.

virtual_inner_anchor:
    optionally create a constrained virtual inner anchor for a missing hue-side
    region, such as a magenta-side RB bridge built from CW+WW behavior. This is
    technically overdrive, not strict sub_gamut, but it can be exposed as a
    controlled virtual-gamut/virtual-primary policy.
```

Distance-based inner fit remains a strict policy: it only decides which one
legal direct simplex owns the ambiguous region. It does not solve both inner
anchors and blend the results.

The useful implementation form is a cached split boundary across the ambiguous
outer-edge trapezoid. For each local pair `OuterA/OuterB + InnerA/InnerB`, the
profile can precompute the equal-fit boundary once, then classify targets by
which side of that cached line/curve they fall on. A small MCU/runtime profile
may store a direct two-point line or a three-point polyline; a PC LUT builder can
cache a dense multi-point curve before candidate generation. If the computed
boundary is effectively straight, the profile should collapse it back to the
smallest line representation instead of carrying unnecessary cache points.

Borders can still tie, so the profile should record a deterministic tie-break
policy, including which inner diode is preferred on exact/effective equality,
and the verifier should report where that tie-break was used.

For the virtual-inner-anchor policy, the virtual point should not be introduced
alone. A single RB-side virtual primary can make that sub-gamut brighter than the
others. The profile should build a balanced set of sibling virtual primaries
across the outer sectors, such as `RBCWWW`, `RGCWWW`, and `BGCWWW` virtual-primary equivalents,
so runtime behavior resembles a coherent expanded virtual primary set rather
than a one-off brightness spike.

---

## Display profiling, instrument correction, and spectral reports

The measured builder treats capture quality as part of the display profile. The
short version is:

```text
spectrophotometer reference when available
→ Argyll ccxxmake .ccmx / .ccss artifact
→ spotread -X during colorimeter capture
→ raw + corrected XYZxyY stored in capture rows
→ builder/verifier consume corrected measurements by default
```

The detailed plan now lives in
[`README_SPECTROPHOTOMETER_CCXXMAKE.md`](README_SPECTROPHOTOMETER_CCXXMAKE.md).
That companion document covers the host_calibration_gui / `ccxxmake` bridge,
including `-d dummy`, the `-C` patch relay command, 8-bit and normalized-float
RGB patch arguments, high-bit-depth/TemporalBFI rendering, correction metadata,
validation, and later spectral reports.

Keep the artifact boundaries clear:

```text
InstrumentProfile:
    colorimeter / spectrophotometer identity and spotread options

ArgyllCorrectionProfile:
    .ccmx / .ccss path, kind, instruments, geometry, validation, and -X command

DisplayProfile:
    emitter profile, correction profile, geometry, reference white,
    and raw/corrected measurement policy
```

Spectral/lighting-quality reports remain optional diagnostics. Corrected XYZxyY
is the normal LUT solve input; SPD-dependent reports such as CRI, TM-30, TLCI,
SSI, and LM-79-style summaries are generated only when spectrophotometer data is
available.

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
run or review the host GUI / ccxxmake patch-relay workflow
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
host_calibration_gui-driven ccxxmake correction sessions
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
