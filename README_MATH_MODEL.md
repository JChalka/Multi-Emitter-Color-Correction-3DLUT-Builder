# RGBW LUT Builder Math Model

This document is the **math-model README**. It gives the solve architecture and algorithms for the current RGB, strict RGBW sub-gamut, WX / white-overdrive, capture-cloud correction, and multi-emitter layered-simplex models.

For the project migration plan, repository split, tooling phases, and implementation roadmap, see:

```text
README_ROADMAP.md
```

---

## 1. Core notation

### Emitters

Each physical emitter `i` has measured or configured chromaticity and peak luminance:

```text
p_i.xy = (x_i, y_i)
p_i.Y  = peak luminance for full drive
```

Convert each emitter to full-drive CIE XYZ:

```math
P_i = xyY_to_XYZ(x_i, y_i, Y_i)
```

with:

```math
xyY\_to\_XYZ(x, y, Y)
= \begin{bmatrix}
  X \\
  Y \\
  Z
\end{bmatrix}
= \begin{bmatrix}
  \frac{x}{y}Y \\
  Y \\
  \frac{1-x-y}{y}Y
\end{bmatrix}
```

The inverse chromaticity projection is:

```math
XYZ\_to\_xy(X,Y,Z)
= \begin{bmatrix}
  \frac{X}{X+Y+Z} \\
  \frac{Y}{X+Y+Z}
\end{bmatrix}
```

For near-black values where `X + Y + Z` is effectively zero, return the reference white xy as a safe placeholder.

### Channel tuple

For RGBW:

```math
f = \begin{bmatrix} f_R & f_G & f_B & f_W \end{bmatrix}^T
```

where each channel fraction is normalized:

```math
0 \le f_i \le 1
```

The synthesized XYZ for a channel tuple is:

```math
X_{out} = \sum_i f_i P_i
```

For RGBW16 quantization:

```math
q_i = round(clip(f_i, 0, 1) \cdot 65535)
```

The same notation generalizes to arbitrary channel sets:

```math
f = \begin{bmatrix} f_0 & f_1 & \dots & f_{N-1} \end{bmatrix}^T
```

### Source gamut conversion

Let normalized source RGB be:

```math
s = \begin{bmatrix} r & g & b \end{bmatrix}^T, \quad 0 \le r,g,b \le 1
```

Let `M_g` be the selected source gamut RGB→XYZ matrix, normalized so that the selected white has `Y = 1`.

The target XYZ in LED absolute units is:

```math
X_t = K \cdot M_g \cdot EOTF_g(s)
```

For the normal LUT path:

```math
EOTF_g(s) = s
```

because upstream tone mapping / transfer handling should already have produced linear-light source values.

`K` maps named-gamut normalized white into the LED absolute luminance domain. In native mode, the source gamut primaries are the measured LED RGB primaries and the transform is already aligned with the physical device model.

---

## 2. Common simplex solve

The shared primitive is a line/triangle/simplex solve over known points.

A known point stores:

```text
XYZ / xyY
output tuple f
source metadata
trust metadata
```

Examples of known points:

```text
physical LED primaries
strict sub-gamut vertices
WX virtual primaries
warm-white / cool-white inner-anchor solutions
measured capture patches
verifier-approved exact candidates
```

### 2D barycentric containment

For a target chromaticity `T` and triangle vertices `A`, `B`, `C` in xy:

```math
T = \lambda_A A + \lambda_B B + \lambda_C C
```

with:

```math
\lambda_A + \lambda_B + \lambda_C = 1
```

Solve:

```math
\begin{bmatrix}
A_x - C_x & B_x - C_x \\
A_y - C_y & B_y - C_y
\end{bmatrix}
\begin{bmatrix}
\lambda_A \\
\lambda_B
\end{bmatrix}
=
\begin{bmatrix}
T_x - C_x \\
T_y - C_y
\end{bmatrix}
```

then:

```math
\lambda_C = 1 - \lambda_A - \lambda_B
```

The target is inside the triangle when:

```math
\lambda_A, \lambda_B, \lambda_C \ge -\epsilon
```

### 3D XYZ solve

For a physical 3-emitter set `S = (a,b,c)`:

```math
P_S = \begin{bmatrix} P_a & P_b & P_c \end{bmatrix}
```

Solve:

```math
P_S \cdot t_S = X_t
```

or:

```math
t_S = P_S^{-1}X_t
```

The solve is physically valid when:

```math
t_a,t_b,t_c \ge -\epsilon
```

The output tuple is initialized as zero, then the solved components are inserted at their physical channel positions.

### Brightness normalization

If any solved channel exceeds full drive:

```math
m = \max_i(t_i)
```

and:

```math
m > 1
```

then normalize all participating channels:

```math
t_i' = \frac{t_i}{m}
```

This preserves target chromaticity while reducing luminance to the maximum achievable value inside the selected physical topology.

---

## 3. Out-of-hull projection

For named gamuts, some source colors are physically unreachable.

The model must project the target before solver/verifier comparison:

```text
raw target XYZ/xy from selected source gamut
if target is inside measured device hull:
    use raw target
else:
    project target into model-reachable hull
```

A robust projection can be expressed as a constrained residual solve:

```math
f^* = \arg\min_{f \ge 0} \left\|P f - X_t\right\|^2
```

where `P` is the matrix of reachable physical or model-defined primary XYZ columns.

The projected target is then:

```math
X_{proj} = P f^*
```

and:

```math
xy_{proj} = XYZ\_to\_xy(X_{proj})
```

If `max(f*) > 1`, the same proportional normalization can be applied for a maximum-brightness in-hull chromaticity-preserving result.

Builder and verifier must use the same projection method.

---

---

## 4. Profile-space virtual reference hull

The virtual reference hull is a profile preprocessing layer. It should not require solving every LUT node against a giant CIE-wide reference.

The practical first version should be:

```text
measured physical hull
→ slightly expanded virtual reference hull
→ measured emitters remapped into reference-space virtual emitters
→ normal strict/WX/multi-emitter solves use the stored virtual emitters
→ final output still expands back to physical channel tuples
```

### Physical and virtual emitter records

For each physical emitter `i`, keep the measured physical data:

```math
P_i = xyY\_to\_XYZ(x_i, y_i, Y_i)
```

and define a reference-space virtual emitter:

```math
\tilde{P}_i = \Phi(P_i, H_{phys}, H_{ref})
```

where:

```text
H_phys = measured device hull
H_ref  = slightly expanded virtual reference hull
Φ      = profile-defined emitter remapping / projection function
```

The exact `Φ` can start conservatively. For example, for an outer emitter, project its direction from the reference white or inner anchor toward the slightly expanded hull while preserving hull order. For an inner emitter, keep it as an inner anchor but store its reference-space relation to the expanded hull.

A known point should carry both coordinate systems:

```text
KnownPoint:
    virtual/reference xy or XYZ used for solving
    physical measured xyY / XYZ when available
    physical output tuple used for channel expansion
    active channel family
    trust/source metadata
```

### Solve using virtual geometry, expand through physical channels

For a strict sub-gamut-style solve, the solver may use virtual columns for geometry/topology:

```math
\tilde{P}_S =
\begin{bmatrix}
\tilde{P}_a & \tilde{P}_b & \tilde{P}_c
\end{bmatrix}
```

and solve:

```math
\tilde{P}_S t_S = \tilde{X}_t
```

but the output tuple still activates the physical channels:

```math
f_a = t_a,\quad f_b = t_b,\quad f_c = t_c
```

The measured physical response remains:

```math
X_{physical} = \sum_i f_i P_i
```

This separates the solver's reference geometry from the actual emitter response measured by the calibration system.

### Why this helps edge colors

A strict physical `RG` boundary rule says:

```text
target on measured RG edge → no W
```

But in an optical setup with wall/diffuser/camera drift, a tiny W contribution may pull a yellow or orange target closer to the measured expected chromaticity. A slightly expanded virtual reference hull lets the model test that possibility without hardcoding that every edge must use W.

Correction then decides:

```text
if W helps this edge/hue/Y region:
    keep or increase the virtual expansion / W introduction

if W drags the target inward too much:
    back off the virtual expansion or block that channel introduction
```

### Profile preprocessing algorithm

```text
function build_virtual_emitter_profile(emitter_profile, expansion_policy):
    physical_emitters = load measured xyY / XYZ / response metadata
    H_phys = build measured physical hull from outer emitters
    H_ref  = expand_hull(H_phys, expansion_policy)

    virtual_emitters = []
    for emitter in physical_emitters:
        classification = classify inner / outer / edge relative to H_phys
        virtual_xy_or_XYZ = remap_emitter_to_reference_hull(
            emitter,
            H_phys,
            H_ref,
            classification,
            expansion_policy
        )
        virtual_emitters.append({
            physical_emitter_id,
            physical_XYZ,
            virtual_XYZ,
            virtual_xy,
            output_basis_tuple,
            classification,
            trust,
            projection_metadata
        })

    return VirtualEmitterProfile(H_phys, H_ref, virtual_emitters)
```

The normal LUT builder then consumes `VirtualEmitterProfile` rather than raw emitter xy alone.

This makes the later CIE-wide reference idea a drop-in extension: replace the conservative `H_ref` with a larger reference hull or CIE-spanning hull, while keeping the same virtual-emitter API.

## 5. RGB-only model

For RGB-only devices, there is exactly one physical triangle.

```text
outer hull = R-G-B
inner emitters = none
```

Algorithm:

```text
function solve_rgb_only(source_rgb):
    X_t = source_rgb_to_led_absolute_XYZ(source_rgb)
    X_t = project_to_RGB_hull_if_needed(X_t)
    t = solve_xyz([P_R, P_G, P_B], X_t)
    t = normalize_if_any_channel_exceeds_one(t)
    return [R=t_R, G=t_G, B=t_B]
```

Mathematically:

```math
\begin{bmatrix} f_R \\ f_G \\ f_B \end{bmatrix}
=
\begin{bmatrix} P_R & P_G & P_B \end{bmatrix}^{-1} X_t
```

This is effectively the strict sub-gamut solve with only one triangle.

---

## 6. Strict RGBW sub-gamut model

For RGBW, the W diode sits inside the RGB triangle and divides the physical hull into three sub-gamuts:

```text
RGW
RBW
BGW
```

Legal strict topologies:

```text
black
R, G, B, W
RG, RB, BG
RW, GW, BW
RGW, RBW, BGW
```

### Algorithm

```text
function solve_strict_rgbw(source_rgb):
    X_t  = source_rgb_to_led_absolute_XYZ(source_rgb)
    xy_t = XYZ_to_xy(X_t)

    if near_black(X_t):
        return [0,0,0,0]

    if xy_t is outside the reachable hull:
        X_t, xy_t = project_to_model_hull(X_t)

    for subgamut in [RGW, RBW, BGW]:
        if xy_t is inside triangle(subgamut.xy):
            t = solve_xyz(subgamut.P, X_t)
            if t is non-negative:
                return expand_and_normalize(t, subgamut.channels)

    # fallback for numerical edge cases or out-of-hull residual projection
    best = argmin_over_subgamuts(||P_subgamut * nnls(P_subgamut, X_t) - X_t||)
    return expand_and_normalize(best.t, best.channels)
```

### Function form

Let:

```math
\mathcal{G}_{RGBW} = \{RGW, RBW, BGW\}
```

The containing sub-gamut is:

```math
g^* = \{g \in \mathcal{G}_{RGBW} : xy_t \in triangle(xy_g)\}
```

The strict solve is:

```math
t_{g^*} = P_{g^*}^{-1} X_t
```

and the physical RGBW tuple is:

```math
f_i =
\begin{cases}
\frac{t_i}{\max(1, \max_j t_j)}, & i \in g^* \\
0, & i \notin g^*
\end{cases}
```

---

## 7. WX / white-overdrive model family

WX is not a free-form unconstrained four-channel solve. It is a controlled family of models that intentionally increases W participation while preserving chromaticity.

Named modes:

```text
strict_subgamut              baseline, no arbitrary four-channel output
wx_radial_virtual            radial virtual-primary white-overdrive model
wx_virtual_axis_maxbright    virtual-axis max-brightness / high-W model
wx_lp_legacy                 direct LP max-white endpoint / reference model
```

`wx_legacy_virtual_axis` is a deprecated alias for `wx_virtual_axis_maxbright`; it is not an obsolete or diagnostic-only model.

All WX modes should preserve these invariants when requested:

```text
native R / G / B identity
native outer RG / RB / BG edge locking
low-Y collapse to fewer channels
out-of-hull projection before solve
verifier-known fail/pass override behavior
```

---

## 8. WX common virtual-primary structure

WX virtual-primary modes use a repeated 3-primary solve:

```text
Step 1: construct / choose three virtual primaries
Step 2: solve target inside triangle(V_RGW, V_RBW, V_BGW)
Step 3: expand virtual-primary weights back into physical RGBW
```

Each virtual primary stores:

```text
V_j.xy / V_j.XYZ
V_j.f = physical RGBW tuple that produces that virtual primary
```

For the three RGBW sub-gamuts:

```text
j ∈ {RGW, RBW, BGW}
```

we construct:

```math
V_j = \{X_j, xy_j, f_j\}
```

where:

```math
f_j = solve\_strict\_in\_physical\_subgamut(j, X_j)
```

Then solve target chromaticity inside the virtual triangle:

```math
xy_t = \omega_{RGW}xy_{RGW} + \omega_{RBW}xy_{RBW} + \omega_{BGW}xy_{BGW}
```

with:

```math
\omega_{RGW}+\omega_{RBW}+\omega_{BGW}=1
```

and:

```math
\omega_j \ge 0
```

Expand the physical tuple:

```math
f = \sum_j \omega_j f_j
```

Normalize by the limiting physical channel:

```math
f' = \frac{f}{\max(1, \max_i f_i)}
```

This produces a constrained four-channel-capable result without doing an unconstrained 4-emitter optimization.

---

## 9. Preferred WX mode: `wx_radial_virtual`

`wx_radial_virtual` implements the radial mental model directly.

### Geometric setup

Let:

```text
W = LED W chromaticity
T = target chromaticity after projection
H = intersection of ray W→T with the outer RGB hull
```

The ray is:

```math
R(\alpha) = W + \alpha(T-W)
```

where:

```math
R(0) = W,
R(1) = T,
R(\alpha_H) = H,
\alpha_H > 1
```

Let the target-position policy parameter be:

```math
\rho \in [0,1]
```

The active virtual primary position is chosen between the target and the hull:

```math
V_{active}.xy = W + \left(1 + \rho(\alpha_H - 1)\right)(T-W)
```

Equivalently:

```math
V_{active}.xy = (1-\rho)T + \rho H
```

Interpretation:

```text
ρ = 0  → virtual primary stays at target, smaller virtual triangle, lower W overdrive
ρ = 1  → virtual primary sits on/near hull, larger virtual triangle, higher W overdrive
```

### Consistent pose for the other sub-gamuts

The active sub-gamut determines a radial pose around W:

```text
active sub-gamut = physical sub-gamut containing target xy
pose = normalized angular position inside active W→outer-edge wedge
hull distance = target-to-hull fraction controlled by ρ
```

For each other sub-gamut:

```text
1. Recreate the same normalized angular position inside that sub-gamut's W→outer-edge wedge.
2. Cast the corresponding ray from W to that sub-gamut's outer edge / hull.
3. Place the virtual primary at the same target-position fraction ρ along that ray.
4. Solve that virtual point inside its physical sub-gamut.
```

This keeps `V_RGW`, `V_RBW`, and `V_BGW` posed consistently rather than independently maximizing each one.

### Algorithm

```text
function solve_wx_radial_virtual(source_rgb, ρ):
    X_t  = source_rgb_to_led_absolute_XYZ(source_rgb)
    xy_t = XYZ_to_xy(X_t)

    if near_black(X_t):
        return [0,0,0,0]

    if native_identity_or_outer_edge_lock_applies(source_rgb):
        return strict_identity_or_edge_solution(source_rgb)

    X_t, xy_t = project_if_out_of_hull(X_t)

    active = find_subgamut_containing(xy_t)
    pose   = compute_radial_pose(W.xy, xy_t, active.outer_edge)

    virtuals = []
    for subgamut in [RGW, RBW, BGW]:
        xy_v = recreate_pose_in_subgamut(pose, subgamut, ρ)
        X_v  = choose_virtual_XYZ_for_xy(xy_v)
        f_v  = solve_strict_in_subgamut(subgamut, X_v)
        virtuals.append({xy_v, X_v, f_v})

    ω = barycentric_weights(xy_t, [v.xy for v in virtuals])
    f = Σ_j ω_j * virtuals[j].f
    f = normalize_by_limiting_channel(f)
    f = apply_value_scale_if_direct_RGB_wrapper(source_rgb, f)
    return quantize(f)
```

### Target-position policy

`ρ` can be a scalar or profile-defined curve:

```text
wx_target_position = 0.70
wx_target_position_curve = optional saturation/Y/hue-aware curve
```

Potential policies:

```text
white only in a controlled central region
white overdrive across most of the native gamut
strong W near neutral but weaker W near saturated edges
full overdrive except at the native RGB triangle boundary
measured residual minimization by hue / saturation / value
```

---

## 10. Reference WX mode: `wx_lp_legacy`

`wx_lp_legacy` is the direct max-white endpoint model.

It solves for the maximum feasible W contribution while preserving target chromaticity, then fills the residual with RGB.

### Derivation

Let:

```math
P_{RGB} = \begin{bmatrix} P_R & P_G & P_B \end{bmatrix}
```

First solve the target with RGB only:

```math
a = P_{RGB}^{-1}X_t
```

Compute how much RGB would be displaced by one unit of W:

```math
d = P_{RGB}^{-1}P_W
```

For a W amount `w`, the residual RGB is:

```math
t_{RGB}(w) = a - w d
```

The maximum feasible W before any RGB component goes negative is:

```math
w^* = \min\left(1,\ \min_{i:d_i>0} \frac{a_i}{d_i}\right)
```

Then:

```math
t_{RGB}^* = max(a - w^*d, 0)
```

and:

```math
f = \begin{bmatrix}
t_R^* \\
t_G^* \\
t_B^* \\
w^*
\end{bmatrix}
```

Normalize if needed:

```math
f' = \frac{f}{\max(1,\max_i f_i)}
```

### Algorithm

```text
function solve_wx_lp_legacy(source_rgb):
    X_t = source_rgb_to_led_absolute_XYZ(source_rgb)

    if near_black(X_t):
        return [0,0,0,0]

    X_t = project_if_out_of_hull(X_t)

    a = inverse(P_RGB) * X_t
    d = inverse(P_RGB) * P_W

    w = 1
    for each RGB component i:
        if d[i] > eps and a[i] >= 0:
            w = min(w, a[i] / d[i])

    w = clamp(w, 0, 1)
    rgb = max(a - w*d, 0)
    f = [rgb.R, rgb.G, rgb.B, w]
    f = normalize_by_limiting_channel(f)
    return quantize(f)
```

This mode is valuable as a functional reference endpoint, even though it does not express the radial virtual-primary geometry.

---

## 11. Max-brightness WX mode: `wx_virtual_axis_maxbright`

`wx_virtual_axis_maxbright` is a first-class white-extraction / brightness-maximizing WX model.  The earlier name `wx_legacy_virtual_axis` should be treated only as a deprecated compatibility alias.

It follows the same common WX virtual-primary structure, but each sub-gamut's virtual primary is chosen independently to favor high W / high Y behavior:

```text
for each subgamut:
    select a subgamut-local max-W / max-Y virtual point
    solve that point in the physical sub-gamut
solve target inside triangle(selected virtual points)
expand virtual weights back into RGBW
```

Compared with `wx_radial_virtual`, this model is less geometrically constrained because the secondary and tertiary virtual primaries are not posed from the active sub-gamut's shared radial relationship to W and the hull. That is a policy difference, not a reason to demote it to diagnostic status.

The pre-radial verifier session used this model. Its high-W regions were stable enough to treat it as a functional model family for suitable display profiles, and its purpose is better described as **maximize brightness / maximize white participation** than as a legacy visualization mode. On hardware/display profiles where it reaches higher luminance safely, it can be selected intentionally as a high-brightness extraction policy.

Algorithmically:

```text
function solve_wx_virtual_axis_maxbright(source_rgb):
    X_t = source_rgb_to_led_absolute_XYZ(source_rgb)

    if near_black(X_t):
        return [0,0,0,0]

    X_t = project_if_out_of_hull(X_t)
    xy_t = xy(X_t)

    for each subgamut g in [RGW, RBW, BGW]:
        V_g.xy = choose_subgamut_local_high_W_high_Y_axis_point(g, xy_t)
        V_g.XYZ = xyY_to_XYZ(V_g.xy, selected_virtual_Y_policy)
        V_g.f = solve_physical_subgamut(g, V_g.XYZ)
        V_g.f = normalize_by_limiting_channel(V_g.f)

    alpha = solve_barycentric_xy(xy_t, [V_RGW.xy, V_RBW.xy, V_BGW.xy])
    f = alpha_RGW*V_RGW.f + alpha_RBW*V_RBW.f + alpha_BGW*V_BGW.f
    f = normalize_by_limiting_channel(f)
    return quantize(f)
```

New metadata should use `wx_virtual_axis_maxbright`. Readers may accept `wx_legacy_virtual_axis` as an alias when loading older files.

---

## 12. Capture-cloud simplex correction

Capture correction uses the same known-point simplex primitive as WX.

For measured correction, known points are real captures:

```text
capture A: measured XYZxyY + known RGBW16/RGB16/channels16 output
capture B: measured XYZxyY + known RGBW16/RGB16/channels16 output
capture C: measured XYZxyY + known RGBW16/RGB16/channels16 output
```

If the expected target lies inside the measured triangle, solve:

```math
xy_t = \lambda_Axy_A + \lambda_Bxy_B + \lambda_Cxy_C
```

with:

```math
\lambda_A+\lambda_B+\lambda_C=1
```

Then expand the output tuple:

```math
f_{corr} = \lambda_A f_A + \lambda_B f_B + \lambda_C f_C
```

Predict the measurement:

```math
X_{pred} = \lambda_A X_A + \lambda_B X_B + \lambda_C X_C
```

Score the candidate using:

```text
expected xy error
expected Y / nits error
dE / dY estimate
source trust
topology compatibility
headroom / clipping risk
triangle conditioning
known pass/fail exclusions
```

Algorithm:

```text
function solve_capture_cloud_correction(target, captures):
    candidates = find_triangles_near_target(captures, target.xy)
    scored = []

    for tri in candidates:
        if target.xy not inside tri.xy:
            continue
        if topology_incompatible(tri, target):
            continue
        if known_fail_nearby(tri, target):
            continue

        λ = barycentric_weights(target.xy, tri.xy)
        f = Σ_i λ_i * tri[i].output_tuple
        X = Σ_i λ_i * tri[i].measured_XYZ
        score = score_expected_error_and_trust(X, f, target, tri)
        scored.append((score, f, tri))

    if scored has good candidate:
        return best(scored)
    else:
        return fallback_to_edge_ramp_or_model(target)
```

This is why WX and capture-cloud correction should share a `simplex.py` implementation.

---

### Correction response profiles and observed response curves

The pass/fail dictionary should feed an aggregate response model. A single patch can say "pass" or "fail", but a sequence of patches along the same drive family can reveal the actual chromaticity curve of the hardware/optics setup.

Group captures by active channel family and drive path signature:

```text
active_channel_family:
    RG, RW, RGW, RGBW, radial-WX, virtual-axis-WX, RGBCCT, etc.

drive_path_signature:
    channel ratios
    dominant channel
    W / inner-emitter participation
    Y bucket
    hue / saturation bucket
```

A correction response profile stores:

```text
CorrectionResponseProfile:
    display_profile
    emitter_profile
    model_family
    active_channel_family
    drive_path_signature
    expected_xyY_curve
    measured_xyY_curve
    residual_vectors
    dE / dY trend
    headroom limits
    known good region
    known bad region
    recommended next probes
```

The learned curve is:

```text
ObservedResponseCurve:
    points = KnownPoints along a drive family
    fitted xyY trajectory
    confidence
    correction direction
    blocked regions
    recommended next capture
```

A simple first fit can be piecewise linear in xyY over ordered drive states:

```math
C_{meas}(\tau) =
\sum_k \beta_k(\tau) X_{meas,k}
```

where `τ` is a path coordinate such as value, W participation, inner-emitter blend amount, or a normalized ratio along an edge.

The correction loop can then use the curve before choosing the next candidate:

```text
function choose_corrected_candidate(target, model_candidate, response_profile):
    family = classify_active_channel_family(model_candidate)
    curve  = lookup_observed_response_curve(family, target hue/Y bucket)

    if curve has enough confidence:
        direction = estimate_residual_direction(curve, target)
        candidate = adjust_candidate_along_learned_direction(model_candidate, direction)
    else:
        candidate = conservative_model_or_simplex_candidate(model_candidate)

    if uncertainty remains high:
        request the smallest useful probe
    return candidate
```

This lets the builder learn whether introducing W or another emitter helps a region instead of blindly recapturing dense grids.


## 13. Multi-emitter layered simplex model

The RGBW model generalizes to packages with arbitrary emitter counts by classifying emitters and solving them in layers.

### Emitter classification

```text
outer emitter:
    expands or defines the device hull
    becomes a hull vertex / sub-gamut-creating point
    participates in physical triangle construction

inner emitter:
    lives inside the device hull
    does not expand gamut
    becomes an alternate inner anchor / white-axis model
    is solved as a separate inner-anchor layer

edge emitter:
    lies on or near an existing hull edge
    may be treated as a hull refinement, edge anchor, or constrained outer point
    classification should be profile-configurable when measurements are ambiguous
```

### General algorithm

```text
function solve_layered_simplex(source_rgb, emitter_profile):
    X_t  = source_rgb_to_led_absolute_XYZ(source_rgb)
    xy_t = XYZ_to_xy(X_t)

    emitters = load_emitter_profile()
    outer, inner, edge = classify_emitters_by_xy_hull(emitters)
    hull = build_measured_outer_hull(outer, edge_policy=edge.policy)

    X_t, xy_t = project_to_hull_if_needed(X_t, hull)

    virtual_points = []
    for anchor in inner:
        fan = build_triangle_fan(hull, anchor)
        tri = find_containing_triangle(fan, xy_t)
        f_anchor = solve_xyz(tri.P, X_t)
        f_anchor = expand_to_full_channel_tuple(f_anchor, tri.channels)
        X_anchor = synthesize_XYZ(f_anchor)
        virtual_points.append({
            xy: XYZ_to_xy(X_anchor),
            XYZ: X_anchor,
            f: f_anchor,
            anchor: anchor,
            trust: model_anchor
        })

    if len(virtual_points) == 1:
        return virtual_points[0].f

    if virtual_points_form_valid_simplex(virtual_points, xy_t):
        weights = solve_between_virtual_points(virtual_points, target_policy)
        f = Σ_j weights[j] * virtual_points[j].f
    else:
        f = reduce_degenerate_inner_anchor_line_chain(virtual_points, target_policy)

    f = normalize_and_apply_policy(f)
    return quantize(f)
```

### RGBCCT / warm-cool inner-anchor model

Warm white and cool white are both inner anchors.

```text
1. Solve target using RGB + WW as one RGBW-like model.
2. Solve target using RGB + CW as another RGBW-like model.
3. Treat both solved outputs as virtual points with known XYZxyY and channel tuples.
4. Blend / solve between those points using CCT, Y, dE, or profile policy.
5. Expand final weights back into R/G/B/WW/CW.
```

For two inner anchors, the final stage can be a line solve:

```math
f = (1-\eta) f_{WW} + \eta f_{CW}
```

where `η` can be chosen by:

```text
CCT target match
maximum Y
minimum RGB residual
best measured dE
smooth WW↔CW transition
profile-specific warm/cool preference
```

### RGBY / RGBV / outer-hull expansion

When an added emitter expands the outer hull, it becomes a hull vertex.

Examples:

```text
RGB + yellow/amber outside RG edge:
    old hull: R-G-B
    new hull: R-Y-G-B
    possible white-anchored sub-gamuts: RYW, YGW, GBW, BRW

RGB + violet outside BR edge:
    old hull: R-G-B
    new hull: R-G-B-V
    possible white-anchored sub-gamuts: RGW, GBW, BVW, VRW
```

The triangulation should be derived from measured hull order, not hardcoded emitter names.

For each inner anchor:

```text
outer hull vertices + one inner anchor
→ physical triangle fan
→ solve target in containing triangle
→ produce one solved virtual output for that inner anchor
```

Then if multiple inner anchors exist, the final stage solves between the solved inner-anchor outputs.


### Degenerate inner-anchor line fallback

A rare edge case occurs when additional inner emitters do not form a valid final
inner-emitter triangle/simplex for the residual inner-anchor solve. This can
happen when inner anchors are collinear, nearly collinear, or arranged such that
the target cannot be enclosed by the inner-anchor virtual points.

For overdrive prediction models, do not promote this to an unconstrained
N-channel solve. Keep reducing the problem through known-point line solves.

#### Three-point inner line

Given:

```text
OuterA --- Inner --- OuterB
```

First run the normal full virtual solve for each relevant inner/outer-adjacent
segment, producing known points:

```text
SolveOAI = solve_line_or_pair(OuterA, Inner, target_policy)
SolveIOB = solve_line_or_pair(Inner, OuterB, target_policy)
```

Then solve between those solved virtual points:

```text
Final = solve_line_or_pair(SolveOAI, SolveIOB, target_policy)
```

In output-tuple form, each solved point is still a known point:

```math
K_{OAI} = \{xy_{OAI}, X_{OAI}, f_{OAI}\}
```

```math
K_{IOB} = \{xy_{IOB}, X_{IOB}, f_{IOB}\}
```

The final blend is:

```math
f = (1-\eta) f_{OAI} + \eta f_{IOB}
```

where `η` is chosen by the same line-solve policy used elsewhere:

```text
target xy projection onto the line
CCT / Y / dE policy
measured correction preference
profile-specific overdrive policy
```

#### Four-point and longer inner lines

For a four-point line:

```text
OuterA --- InnerA --- InnerB --- OuterB
```

reduce it by adjacent pair solves:

```text
K_left  = solve_line_or_pair(OuterA, InnerA, target_policy)
K_right = solve_line_or_pair(InnerB, OuterB, target_policy)
Final   = solve_line_or_pair(K_left, K_right, target_policy)
```

For longer line chains, apply the same recursive reduction until the problem
collapses to an already-supported 3-line or 2-line solve.

#### Constraint

This fallback is for WX / overdrive / virtual prediction models. It is not a
reason to loosen strict sub-gamut topology. In strict mode, direct line or hull
edges should remain direct legal emitter-pair solves only:

```text
strict_subgamut:
    solve between actual legal edge/hull endpoints

overdrive / layered virtual models:
    may recursively reduce virtual inner-anchor line chains
```

If an added inner emitter **does** create a valid new triangle/sub-gamut, use the
normal layered-simplex triangle solve instead of the degenerate line fallback.

---

## 14. Tetrahedral LUT interpolation

The LUT runtime should use tetrahedral interpolation by default.

For input coordinates inside a cube cell:

```math
\Delta_r, \Delta_g, \Delta_b \in [0,1]
```

The selected tetrahedron is determined by the ordering of the fractional components.

One deterministic policy:

```text
if Δb > Δr > Δg: t1
if Δb > Δg > Δr: t2
if Δg > Δb > Δr: t3
if Δr > Δb > Δg: t4
if Δr > Δg > Δb: t5
else:             t6
```

Tie handling should be deterministic, especially on neutral diagonals.

### Vertex tetra runtime

The vertex runtime fetches four selected tetrahedron vertices and evaluates a weighted sum.

```text
cell = floor(input * (grid_size - 1))
Δ = fract(input * (grid_size - 1))
tetra = select_tetrahedron(Δ)
vertices = fetch_four_vertices(cell, tetra)
weights = tetra_barycentric_weights(Δ, tetra)
out = Σ_i weights[i] * vertices[i]
```

### Coefficient tetra runtime

The coefficient runtime stores affine coefficients per cell and tetrahedron:

```math
\Delta = \begin{bmatrix}1 & \Delta_b & \Delta_r & \Delta_g\end{bmatrix}^T
```

For each output channel:

```math
out_c = C_{tetra,c}^T \Delta
```

Equivalent fixed-point form:

```math
out_c = c_0 + c_B\Delta_b + c_R\Delta_r + c_G\Delta_g
```

The coefficients are generated offline from the vertex cube. Runtime code should not reconstruct tetrahedron deltas from the eight surrounding vertices when using `coefficient_tetra`.

Metadata:

```text
interpolation = tetrahedral
interpolation_runtime = vertex_tetra | coefficient_tetra
coefficient_layout = per_cell_6tetra_affine | none
coefficient_order = [1, db, dr, dg]
tetrahedron_order_policy = sorted_fraction_rgb_deterministic
fraction_bits = 8 | 10 | 12 | 16
coefficient_format = int16_delta | int24_delta | int32 | float32
coefficient_q_format = q0.16 | q8.16 | q16.16 | profile-defined
coefficient_scale = integer scale used for stored coefficients
cell_index_order = r_major | g_major | b_major
```

Approximate RGBW16 storage:

```text
vertex_tetra size ≈ grid_size^3 * 4 channels * 2 bytes
coefficient_tetra size ≈ (grid_size - 1)^3 * 6 tetra * 4 terms * 4 channels * bytes_per_coeff
```

---

## 15. Future physical-solution policy axis

Strict sub-gamut and WX are not mutually exclusive models. They are different slices of a larger physical-solution space.

A future post-core-builder direction is to add an extra source-side policy axis:

```text
RGB + extraction/overdrive axis
RGB + desired physical utilization axis
RGB + scene/headroom axis
```

Practical representations:

```text
two or three 3D LUTs + runtime blend coefficient
base cube + WX delta cube
sparse 4D LUT with a small mode-axis grid
analytical mode selection feeding paired LUTs
```

This would allow one input RGB target to address multiple valid physical RGBW solutions:

```text
strict_subgamut result
wx_radial_virtual result
wx_lp_legacy / overdrive result
measured-corrected high-W result
```

TemporalBFI or RGBW16 output can remain 16-bit/effective-q16 while the source-side addressing carries the extra policy information.

This should remain a later exploration after the main builder is stable.
