# CIE Virtual-Hull White Capacity Model

A reference note for a future RGBW / multi-emitter solver variant that treats white extraction as a **profile-space virtual-emitter remapping problem** instead of a per-target physical-hull rule.

This is not an implementation target for the current builder pass. It is a later research/design branch intended to make edge colors and multi-emitter packages easier to reason about once the strict sub-gamut, WX mode selection, and capture-cloud correction systems are stable.

The most important framing is:

```text
CIE virtual-hull projection is profile preprocessing,
not a per-LUT-node solve requirement.
```

The virtual reference solve only needs to run when building or updating a display/emitter profile. The result is a stored set of **virtual emitters / virtual primaries** that the normal sub-gamut, WX, layered-simplex, and capture-correction solvers can use for every LUT node.

---

## Motivation

The current strict RGBW sub-gamut model is intentionally conservative:

```text
R / G / B physical primaries define the outer LED hull.
W is an inner point.
W subdivides the RGB hull into RGW / RBW / BGW.
Targets on native outer RGB edges remain edge-locked.
```

That topology is clean and safe, but it can hide a useful fact: some colors that look like physical RGB edge colors may still benefit from a small white contribution after wall reflection, diffuser mixing, display-surface drift, channel response, and measured correction are considered.

A practical example is yellow. In strict sub-gamut mode, an RG boundary target is usually locked to R+G with no W. In the current wall/capture setup, however, a tiny W contribution may pull the measured result closer to the expected xy/Y target. The existing physical-hull rule has no way to discover that beforehand because it asks only:

```text
How much white can this measured physical LED hull produce directly?
```

The CIE virtual-hull model asks a higher-level reference question:

```text
How much white capacity does this emitter/region have
when viewed inside a larger reference color space?
```

Then the physical solver realizes that intent through calibrated emitters, WX policies, and measured capture-cloud correction.

---

## Core architecture

Define a large virtual reference hull in CIE xy:

```text
H_virtual = {V0, V1, V2}
```

This hull is not the physical LED hull. It is a reference construction used to remap measured emitters into a wider solver geometry.

Possible choices:

```text
CIE-covering triangle:
    a triangle chosen to contain most useful visible xy space

Rec.2020-plus triangle:
    a triangle based on, or padded beyond, a wide working gamut

profile-tuned virtual hull:
    a large hull tuned from verifier/capture behavior for a display profile
```

Keep a white/reference anchor:

```text
W_ref = measured W xyY / XYZ
```

or, for multi-emitter profiles:

```text
inner anchors = {W, WW, CW, A, ...} depending on emitter classification
```

The virtual hull can be subdivided by `W_ref` just like the physical RGBW model subdivides the measured RGB hull:

```text
V0 V1 W_ref
V1 V2 W_ref
V2 V0 W_ref
```

The key shift is that this reference solve does **not** have to be executed for every LUT node. It can be used once to remap each measured emitter into reference-space.

---

## Profile preprocessing: virtual emitter remapping

Instead of doing this:

```text
for every LUT node:
    solve target against large CIE reference hull
    derive white-capacity behavior
    solve physical output
```

use this:

```text
once per display/emitter profile:
    measured emitter xyY / XYZ
    → reference virtual-hull projection / remapping
    → stored virtual emitter xy / XYZ / classification metadata

then for every LUT node:
    solve against stored virtual emitters
    expand back to physical emitter channels
    apply measured correction
```

So strict RGBW sub-gamut becomes:

```text
physical emitters:
    R, G, B, W measured xyY / XYZ

profile preprocessing:
    R' = reference-space virtual position of measured R
    G' = reference-space virtual position of measured G
    B' = reference-space virtual position of measured B
    W' = reference-space virtual position of measured W

build/runtime solve:
    use R'/G'/B'/W' for topology and chromaticity solving
    use physical R/G/B/W channel tuples and response data for expansion/output
```

This separates the solver into two domains:

```text
solver geometry domain:
    virtual/reference chromaticity positions
    topology, containment, barycentric/simplex weights

physical output domain:
    measured channel response
    RGBW16 / TemporalBFI output
    capture-cloud corrections
    pass/fail overrides
```

The same idea applies to inner emitters:

```text
WW measured xy → WW' reference virtual anchor
CW measured xy → CW' reference virtual anchor
amber measured xy → A' inner or edge anchor, depending on classification
yellow/violet measured xy → Y'/V' outer or edge virtual hull point if gamut-expanding
```

The builder then operates on a common known-point abstraction.

---

## KnownPoint abstraction

Each stored profile point should preserve both its virtual solver position and its physical output meaning:

```text
KnownPoint:
    id
    physical_emitter_id or generated_virtual_id

    virtual_xy
    virtual_XYZ
    virtual_role: outer | inner | edge | generated | measured_capture

    physical_xyY / physical_XYZ
    physical_channel_tuple
    response_provider_metadata

    source: measured_primary | reference_projection | wx_virtual | capture | verifier_pass
    trust
    display_profile_id
    projection_policy
```

A physical primary, a CIE-remapped emitter, a radial-WX virtual primary, a warm-white layer solve, and a measured capture-cloud patch can all be represented as:

```text
known point = virtual/solver XYZxyY + physical output tuple + source/trust metadata
```

Then every downstream solver can reuse the same primitive:

```text
find a valid line/triangle/simplex around target
solve barycentric/simplex weights
expand weights back into output channel tuple
score expected dE / dY / topology / headroom / trust
```

---

## Reference remapping algorithm sketch

The virtual hull preprocessing can be expressed as:

```text
function build_virtual_emitter_profile(physical_emitters, reference_hull, inner_anchors, policy):
    classified = classify_emitters(physical_emitters, reference_hull, policy)

    virtual_points = []

    for emitter in physical_emitters:
        projected_xy = project_or_locate_in_reference_hull(emitter.xy)
        virtual_role = classify_relative_to_reference_hull(projected_xy, emitter, policy)

        # This may be identity, projection, radial remap, capacity remap,
        # or a profile-learned transform.
        virtual_xy, virtual_XYZ = remap_emitter_to_reference_space(
            emitter,
            projected_xy,
            reference_hull,
            inner_anchors,
            policy,
        )

        virtual_points.append(KnownPoint(
            virtual_xy=virtual_xy,
            virtual_XYZ=virtual_XYZ,
            virtual_role=virtual_role,
            physical_xyY=emitter.xyY,
            physical_channel_tuple=unit_tuple_for(emitter),
            source="reference_projection",
            trust=policy.default_projection_trust,
        ))

    return VirtualEmitterProfile(
        reference_hull=reference_hull,
        physical_emitters=physical_emitters,
        virtual_emitters=virtual_points,
        projection_policy=policy,
    )
```

Then strict sub-gamut and WX consume the virtual profile:

```text
function solve_node_with_virtual_profile(target, virtual_profile, output_policy):
    target_virtual = transform_target_to_virtual_domain(target, virtual_profile)

    base = strict_subgamut_solve(
        target_virtual,
        primaries=virtual_profile.virtual_emitters,
    )

    output = expand_virtual_weights_to_physical_channels(base)
    output = apply_response_provider(output)
    output = apply_capture_correction(output, target)
    return output
```

For a basic first pass, `transform_target_to_virtual_domain()` can be identity in xy/XYZ. The profile remapping is primarily applied to emitters.

---

## White capacity as a stored emitter/profile signal

The original white-capacity idea can still be preserved, but it becomes metadata rather than a repeated hot-path solve.

For an emitter or virtual region:

```text
white_capacity = reference-space barycentric weight toward W_ref
```

or:

```text
white_capacity_curve = sampled / fitted capacity as a function of hue, saturation, Y, or edge distance
```

This can be stored in the profile:

```text
VirtualEmitterProfile:
    reference_hull_id
    virtual_emitters
    white_capacity_policy
    white_capacity_curves
    edge_capacity_rules
```

WX modes can consume this signal in several ways:

```text
wx_radial_virtual:
    capacity shapes radial target-position / triangle size

wx_virtual_axis_maxbright:
    capacity gates how aggressively max-bright behavior is allowed

wx_lp_legacy:
    capacity gates or blends toward the LP endpoint

strict+WX blended candidate:
    capacity controls blend amount between calibrated strict and overdrive candidate
```

This avoids the hard-coded idea that native RGB edges always have zero W capacity.

---

## Relationship to strict sub-gamut

Strict sub-gamut currently asks:

```text
Which measured physical triangle contains target xy?
Solve only that triangle.
Keep outer RGB edges edge-locked.
```

With virtual emitter preprocessing, strict sub-gamut can instead ask:

```text
Which virtual/reference triangle contains target xy?
Solve that triangle.
Expand the virtual weights back into physical output channels.
```

This means a measured emitter can keep its physical channel identity while receiving a reference-space solver position.

A conservative profile can keep virtual emitters equal to measured emitters:

```text
R' = R
G' = G
B' = B
W' = W
```

A CIE virtual-hull profile can remap them:

```text
R' / G' / B' / W' = reference-space positions derived from large virtual hull
```

The same solver then works in both cases. The difference is only the profile’s virtual emitter table.

---

## Relationship to WX models

### `wx_radial_virtual`

Radial WX currently asks:

```text
Within the measured physical hull, how should the virtual triangle be posed around W?
```

With CIE virtual-hull preprocessing, radial WX can ask:

```text
Within the reference-space virtual emitter profile,
how should the virtual triangle be posed around W'?
```

The radial target-position policy can be shaped by stored capacity metadata:

```text
wx_target_position = f(white_capacity, saturation, Y, hue, edge_distance, profile)
```

### `wx_virtual_axis_maxbright`

Maxbright already finds high-brightness white-assisted solutions across a broad region. Reference-space capacity can provide a more explainable limiter:

```text
allow maxbright strongly where virtual white_capacity is high
reduce maxbright where the profile says the color should remain low-W
```

### `wx_lp_legacy`

LP legacy remains a direct feasibility/reference endpoint. The virtual-hull profile can gate when to choose or blend toward it.

---

## Relationship to multi-emitter sub-gamut and overdrive

The profile-preprocessing framing fits multi-emitter packages naturally, but it
should preserve the same distinction used by the main builder:

```text
strict multi-emitter sub_gamut:
    use virtual/reference emitter positions to choose one legal direct topology
    expand that direct solve back to physical channels

multi-emitter overdrive / layered simplex:
    create solved inner-anchor or virtual known points first
    then solve/blend between those known points
```

The virtual-reference hull can feed both families, but it should not make strict
mode silently behave like overdrive.

### Strict multi-emitter sub-gamut with virtual emitters

For RGBCCT:

```text
physical emitters:
    R, G, B, WW, CW

preprocessing:
    R'  = reference-space red
    G'  = reference-space green
    B'  = reference-space blue
    WW' = reference-space warm-white inner anchor
    CW' = reference-space cool-white inner anchor

strict solve:
    build direct triangle fans from R'-G'-B' to each inner anchor
    evaluate direct candidates such as RGWW, GBWW, BRWW, RGCW, GBCW, BRCW
    choose one direct legal candidate by residual / CCT / headroom / profile policy
    expand that one solve back into R/G/B/WW/CW physical channels
```

For RGBY+W or RGBV+W:

```text
physical emitters:
    R, G, B, Y, W

preprocessing:
    classify Y as outer / edge / inner based on reference and measured hull
    build virtual outer hull R'-Y'-G'-B' if Y expands the hull
    keep W' as inner anchor

strict solve:
    build triangle fan from the virtual outer hull to W'
    solve the containing direct triangle, e.g. RYW, YGW, GBW, or BRW
    expand that direct solve back into physical channels
```

Strict mode may choose between legal direct candidates, but it should not solve
multiple inner-anchor outputs and then blend them as a second stage.

### Multi-emitter overdrive with virtual emitters

The layered behavior previously described for RGBCCT belongs to the overdrive
family:

```text
RGBCCT overdrive:
    solve RGB+WW' layer
    solve RGB+CW' layer
    treat each result as a KnownPoint with XYZxyY + output tuple
    solve/blend between those known points
    expand back to R/G/B/WW/CW physical channels
```

For RGBY+W or RGBV+W, overdrive can similarly use the virtual outer hull and
inner anchors to create candidate known points before the final overdrive or
correction solve.

This keeps arbitrary emitter packages out of unconstrained N-channel solving:
extra emitters change the virtual point set and the allowed strict/overdrive
solve family, not the fundamental simplex primitive.


---

## Relationship to capture-cloud correction

Capture-cloud correction already wants the same abstraction:

```text
measured capture = known point with measured XYZxyY and known output tuple
```

The virtual emitter profile becomes just another source of known points. Candidate generation can combine:

```text
reference-projected emitters
calibrated strict-subgamut endpoints
WX-generated virtual primaries
inner-anchor layer outputs
measured capture patches
verifier-approved exact candidates
```

Correction flow:

```text
1. Solve model candidate using virtual emitter profile.
2. Find measured local triangle/simplex around expected target.
3. Solve target inside measured simplex.
4. Expand measured weights back into output tuples.
5. Score candidate by dE, dY, topology, headroom, trust, and known pass/fail data.
6. If model and measured candidates disagree strongly, request live capture.
```

This makes the CIE virtual hull a preprocessing/candidate-generation tool, not a competing correction system.

---

## Metadata / profile artifacts

Future profiles using this idea should record enough metadata to reproduce the remapping:

```text
emitter_profile_id
physical_emitters_xyY
physical_emitters_XYZ
reference_virtual_hull_id
reference_virtual_hull_vertices_xy
reference_virtual_hull_luminance_policy
white_reference_xyY
virtual_emitters_xy
virtual_emitters_XYZ
virtual_emitter_roles
emitter_projection_policy
white_capacity_model
white_capacity_curves
inner_anchor_map
outer_hull_map
edge_emitter_policy
projection_trust
calibrated_strict_base_profile_id
```

LUTs / verifier reports should record:

```text
virtual_emitter_profile_id
reference_virtual_hull_id
white_capacity_model
wx_mode
wx_target_position / wx_policy_curve
strict_base_profile_id
correction_simplex_policy
```

Different virtual hulls or remapping policies can produce different W behavior from the same physical emitters, so this metadata must be part of the reproducibility contract.

---

## Why this helps edge cases

In a physical RGBW topology, the RG edge is often treated as pure R+G. This is clean, but it assumes the physical edge is the correct conceptual boundary for deciding W participation.

In measurement practice, especially with wall reflection or diffuser mixing:

```text
small W additions can shift xy beneficially
W can improve Y/nits behavior
wall/reflection spectral drift may reward W participation
pure RGB edge solves can inherit channel-specific residuals
```

A reference-space virtual emitter profile lets the model represent nonzero W capacity near physical edges without turning every solve into an unconstrained 4-channel solve.

This is especially relevant for:

```text
yellow / orange near RG edge
cyan / spring near GB edge
magenta / rose near RB edge
near-edge colors that repeatedly fail in verifier sessions
high-Y colors where pure RGB channels are near limits
multi-emitter packages where added emitters change the conceptual hull
```

---

## Possible virtual hull choices

### Large CIE-enclosing triangle

Choose three synthetic points outside or near the visible locus so nearly all useful xy targets and measured emitters are inside.

Pros:

```text
simple
continuous over most targets
reveals broad theoretical W capacity
```

Cons:

```text
purely synthetic
white_capacity depends strongly on arbitrary hull choice
may overestimate W in saturated regions
```

### Rec.2020-plus triangle

Use a working gamut larger than Rec.2020 or a padded version of Rec.2020.

Pros:

```text
less arbitrary than a huge synthetic triangle
still wider than most LED/device hulls
works well with video/color pipeline concepts
```

Cons:

```text
may still clip some spectral-locus areas
white capacity is tied to a named-gamut assumption
```

### Profile-learned virtual hull

Fit or tune the virtual hull from verifier/capture results.

Pros:

```text
can match wall/diffuser/capture behavior
can be optimized for actual dE improvement
```

Cons:

```text
requires data
risk of overfitting
harder to reason about across profiles
```

---

## Implementation notes for later

A minimal implementation should not start with per-node white-capacity solves. It should start with a profile artifact:

```json
{
  "emitter_profile_id": "display_a_cie_virtual_v1",
  "reference_virtual_hull_id": "cie_large_triangle_v1",
  "physical_emitters": {
    "R":  {"xy": [0.6853, 0.3147], "Y": 149.66},
    "G":  {"xy": [0.1379, 0.7480], "Y": 563.96},
    "B":  {"xy": [0.1295, 0.0663], "Y": 129.54},
    "W":  {"xy": [0.3299, 0.3582], "Y": 1511.80}
  },
  "virtual_emitters": {
    "R":  {"xy": [0.0, 0.0], "role": "outer", "physical_channel": "R"},
    "G":  {"xy": [0.0, 0.0], "role": "outer", "physical_channel": "G"},
    "B":  {"xy": [0.0, 0.0], "role": "outer", "physical_channel": "B"},
    "W":  {"xy": [0.0, 0.0], "role": "inner", "physical_channel": "W"}
  },
  "projection_policy": "cie_virtual_hull_profile_preprocess_v1"
}
```

The placeholder virtual xy values above would be filled by the actual reference remapping tool.

Recommended implementation order:

```text
1. Add VirtualEmitterProfile data structure.
2. Allow strict_subgamut solver to use virtual emitter xy/XYZ while expanding to physical channels.
3. Add reference-hull projection/remapping tool that writes virtual_emitters.
4. Add diagnostics plotting physical emitters vs virtual emitters.
5. Compare model-only LUTs using measured emitters vs virtual emitters.
6. Only then experiment with white_capacity policy curves and WX integration.
```

---

## Open questions

```text
What virtual hull should be the default?
Should remapping be identity, projection, barycentric reference solve, radial remap, or learned transform?
Should the virtual hull use synthetic equal-Y vertices or tuned XYZ vertices?
Should W_ref be the measured W diode, D65, or a corrected/profile white?
Should capacity be computed in xy only, xyY, XYZ, Lab, or another space?
How should outer-edge identity rules interact with nonzero virtual W capacity?
Should capacity control radial target-position, blend amount, or candidate ranking only?
Can verifier data learn a stable capacity→WX policy curve?
How much does this change under direct-diode/diffuser capture vs wall-reflection capture?
```

---

## Design status

This is a future research branch, not a near-term blocker.

Recommended status:

```text
current priority:
    finish strict_subgamut / WX mode separation
    build calibrated strict_subgamut baseline
    implement capture-cloud correction
    validate radial and maxbright WX models

future branch:
    VirtualEmitterProfile abstraction
    CIE virtual-hull emitter remapping
    capacity-shaped WX policy curves
    strict+WX blended candidate generation
    verifier-learned white-capacity correction
```

The key takeaway is that the whole pipeline can be reinterpreted as layered virtual solves:

```text
large CIE virtual hull / reference profile preprocessing
→ stored virtual emitter table
→ calibrated physical sub-gamut base
→ WX / overdrive candidate generation
→ measured capture-cloud correction
→ final RGBW / multi-emitter output
```

This keeps the solver architecture consistent while opening the door to smarter W participation near physical hull edges without adding per-node CIE-reference solve cost.
