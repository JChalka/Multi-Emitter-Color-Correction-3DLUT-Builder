# RGBW LUT Builder Math Model

This document is the **math-model README**. It gives the solve architecture and algorithms for the current RGB, strict RGBW sub-gamut, WX / white-overdrive, capture-cloud correction, and multi-emitter strict sub-gamut and overdrive/layered-simplex models.

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

### Brightness / endpoint policy

A simplex solve may request a participating channel above full drive. Define:

```math
m = \max_i(t_i)
```

The builder must not treat the response to `m > 1` as a fixed physical law. It is a **profile policy** because different applications may prefer different tradeoffs between target-Y correctness, chromaticity preservation, and post-clip channel granularity.

Supported policy family:

```text
y_correct_clip:
    keep the absolute target-Y solve while the direct topology has physical
    headroom. Once any participating channel exceeds full drive, clamp the
    whole tuple proportionally to the physical endpoint. This avoids independent
    per-channel clipping and keeps the selected topology's chromaticity/ratio
    contract.

rolloff_after_clip:
    follow the y_correct_clip behavior near the endpoint but introduce a smooth
    knee after the clipping point. The goal is to avoid a hard plateau while
    still tracking the physical clipping boundary.

scale_to_full_endpoint:
    legacy/current behavior. First derive the chromaticity-preserving endpoint
    tuple, then scale that endpoint by the source/value axis. This preserves
    smooth channel granularity but changes the target-Y contract.
```

For an unclipped solve:

```math
m \le 1 \quad \Rightarrow \quad f_i = t_i
```

For the legacy endpoint-scaling behavior:

```math
\hat{t}_i = \frac{t_i}{\max(1,m)}
```

then, for a source/value scale `v`:

```math
f_i = v\hat{t}_i
```

So a half-scale yellow whose chromaticity endpoint is:

```text
R = 65535, G = 28335
```

becomes approximately:

```text
R = 32767, G = 14167
```

under `scale_to_full_endpoint`.

For a Y-correct clipping policy, first form the requested-Y direct solve:

```math
t_i^{node} = v t_i
```

If that node still has headroom, emit it directly. If it exceeds the physical endpoint, clamp proportionally:

```math
f_i =
\begin{cases}
t_i^{node}, & \max_j(t_j^{node}) \le 1 \\
\frac{t_i^{node}}{\max_j(t_j^{node})}, & \max_j(t_j^{node}) > 1
\end{cases}
```

This records the luminance residual as real clipping without independently clipping channels or applying the legacy endpoint-scale-by-value behavior.

A rolloff policy should be represented as a smooth blend/compression between those two endpoints:

```math
\alpha = smoothstep(knee_{start}, knee_{end}, m)
```

```math
f_i = (1-\alpha)f_{clip,i} + \alpha f_{endpoint,i}
```

or an equivalent monotonic compression curve. The exact curve is profile-defined, but it must preserve deterministic ordering and avoid discontinuities at the knee.

Metadata should record:

```text
endpoint_luminance_policy:
    y_correct_clip | rolloff_after_clip | scale_to_full_endpoint

endpoint_rolloff_knee:
    start/end or equivalent curve parameters when rolloff is used

endpoint_scale_axis:
    source value / max input channel / explicit Y scale, depending on profile
```

Builder, verifier, and correction reports must use the same endpoint policy. Otherwise a verifier can incorrectly mark a LUT as failing only because expected Y/chroma was computed under a different endpoint contract.

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
    t = apply_endpoint_policy(t, source_rgb, endpoint_policy)
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
                return expand_and_apply_endpoint_policy(t, subgamut.channels, endpoint_policy)

    # fallback for numerical edge cases or out-of-hull residual projection
    best = argmin_over_subgamuts(||P_subgamut * nnls(P_subgamut, X_t) - X_t||)
    return expand_and_apply_endpoint_policy(best.t, best.channels, endpoint_policy)
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

and the physical RGBW tuple is policy-dependent.

For the legacy `scale_to_full_endpoint` policy:

```math
f_i =
\begin{cases}
v\frac{t_i}{\max(1, \max_j t_j)}, & i \in g^* \\
0, & i \notin g^*
\end{cases}
```

where `v` is the selected source/value scale.

For `y_correct_clip`, let `t_i^{node} = v t_i` for the requested source/value scale. Then:

```math
f_i =
\begin{cases}
t_i^{node}, & i \in g^*, \max_j(t_j^{node}) \le 1 \\
\frac{t_i^{node}}{\max_j(t_j^{node})}, & i \in g^*, \max_j(t_j^{node}) > 1 \\
0, & i \notin g^*
\end{cases}
```

`rolloff_after_clip` uses the same direct topology but replaces the hard transition into the proportional endpoint with a smooth knee between the requested-Y path and the endpoint-scaled path.

The strict invariant is the topology, not a single endpoint-luminance behavior:

```text
strict mode = one legal local topology
endpoint behavior = profile-selected Y / clipping / granularity policy
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


## 13. Multi-emitter sub-gamut and overdrive models

The RGBW model generalizes to packages with arbitrary emitter counts, but the
multi-emitter family has two separate solve classes:

```text
strict multi-emitter sub_gamut:
    direct legal topology solve
    one containing physical/virtual line/triangle/simplex at a time
    includes all legal direct candidates implied by the emitter topology
    no solve-both-inner-anchors-then-blend behavior

multi-emitter overdrive / layered simplex:
    opt-in virtual prediction solve
    solves multiple inner-anchor layers or virtual candidates
    then solves/blends between those solved known points
```

The earlier shorthand "multi-emitter layered simplex" mostly described the
second class. It should be treated as the **overdrive / virtual prediction**
model for 5+ emitter packages. Strict `sub_gamut` also applies to 5+ emitters,
but it follows the same direct-topology rule as RGBW strict mode.

### Emitter classification

```text
outer emitter:
    expands or defines the device hull
    becomes a hull vertex / sub-gamut-creating point
    participates in physical line/triangle/simplex construction

inner emitter:
    lives inside the device hull
    does not expand gamut
    becomes an inner anchor for strict fans / bridge simplexes or overdrive layers

edge emitter:
    lies on or near an existing hull edge
    may be treated as a hull refinement, edge anchor, or constrained outer point
    classification should be profile-configurable when measurements are ambiguous
```

Classification is shared by strict and overdrive modes, but the classified
points are consumed differently.

---

### 13.1 Strict multi-emitter `sub_gamut` model

Strict multi-emitter `sub_gamut` is the 5+ emitter equivalent of strict RGBW
sub-gamut solving. It uses measured or virtualized emitter geometry to find a
**single legal local line/triangle/simplex** that contains the target, then
solves only that local topology.

It does **not** solve every inner-emitter layer and then blend those solved
layers. That behavior belongs to the overdrive model.

#### General strict topology construction

Let:

```text
O = ordered outer-hull emitters
I = inner emitters
E = edge emitters after profile policy/classification
```

For strict mode, build legal direct candidates from the topology:

```text
single candidates:
    every emitter: O_i, I_j, E_k

dual candidates:
    adjacent outer hull edges O_i + O_{i+1}
    outer + inner lines O_i + I_j
    inner + inner lines I_j + I_k
    edge-refined direct lines allowed by the profile

3-channel candidates:
    outer edge + one inner: O_i + O_{i+1} + I_j
    one outer + two inners: O_i + I_j + I_k
    edge-refined direct triangles allowed by the profile
    optional inner-only triangles I_j + I_k + I_l when three inner emitters form
        a valid inner simplex and the profile allows it
```

For RGBW, `I = {W}`, so this reduces to the familiar `RGW`, `GBW`, `BRW`
triangle fan. For 2+ inner emitters, the extra `outer + inner + inner` bridge
triangles are part of strict sub-gamut topology. They are direct simplexes, not
overdrive blends.

#### RGB+CCT strict topology example

For an `RGB + CW + WW` profile with outer hull `R,G,B` and two inner emitters
`CW,WW`, strict candidates include:

```text
Black

Singles:
    R, G, B, CW, WW

Duals:
    outer edges:       RG, RB, BG
    outer + CW:        R+CW, G+CW, B+CW
    outer + WW:        R+WW, G+WW, B+WW
    inner bridge:      CW+WW

3-channel direct strict candidates:
    outer edge + CW:   RG+CW, RB+CW, BG+CW
    outer edge + WW:   RG+WW, RB+WW, BG+WW
    outer + CW + WW:   R+CW+WW, G+CW+WW, B+CW+WW
```

The `outer + CW + WW` triangles are required for the correct 2-inner topology.
They cover the bridge between the two inner anchors and each outer vertex. They
should not be conflated with the overdrive behavior that solves a whole CW fan
and a whole WW fan, then blends those solved outputs.

#### Strict candidate overlap policy

With multiple inner emitters, strict candidates can overlap in xy. The overlap
may be tiny for common CCT geometries, but it must still have an explicit
policy. The default should be **emitter/power efficiency**, because strict
sub-gamut selection is primarily about choosing the most efficient legal local
basis for the requested target.

The overlap decision is user-facing profile behavior, not a hidden numerical
accident. Profiles should record the selected policy and verifier/correction
reports should record which policy selected each ambiguous candidate.

##### Policy: power efficiency

Power efficiency is the default strict-overlap policy.

Goal:

```text
choose the valid direct simplex that reaches the requested chromaticity/Y with
the lowest estimated current or power
```

Equivalent scoring directions:

```text
minimize:
    estimated_current(candidate, target_Y)

or maximize:
    emitted_Y(candidate) / estimated_current(candidate)
```

This is the closest generalization of RGBW strict sub-gamut behavior. Strict
mode chooses a local legal basis not because it is the only possible basis, but
because it should be the most efficient physical basis for that region of the
gamut.

Useful inputs:

```text
per-channel current model
per-channel Y response
thermal/current headroom
measured efficiency by active channel family
power limits / ABL policy
```

##### Policy: channel resolution

Channel-resolution policy chooses the valid direct solve that preserves the most
useful drive granularity while still solving the requested chromaticity.

This is useful when two candidates both match xy, but one of them requires a
channel to sit near the floor or pushes one channel into a hard limiting state.
Those cases can create coarse quantization, visible stepping, or poor temporal
precision even if the chromaticity solve is valid.

Goal:

```text
choose the valid direct simplex whose solved channel vector has the best usable
resolution near its limiting / maximum channel
```

Useful scoring signals:

```text
prefer:
    largest minimum participating-channel drive above the quantization floor
    strongest limiting-channel headroom before clipping
    smooth derivative of output tuple versus input RGB
    best effective-q16 / TemporalBFI precision in the active channels

penalize:
    almost-zero participating channels
    channels pinned at exactly full scale over a wide region
    candidates with large output jumps across neighboring input values
    candidates whose Y response curve is poorly sampled or poorly conditioned
```

This policy is especially relevant when a bridge candidate such as
`R+CW+WW` or `B+CW+WW` solves chromaticity but would place one inner emitter so
low that runtime granularity becomes worse than an `RB+CW` or `RB+WW` choice.

##### Policy: Y-preserving split gamut

Y-preserving split policy handles adjacent ambiguous regions where the policy
boundary itself can create luminance discontinuity.

The motivating case is an `RB` side without a magenta-side inner emitter:

```text
candidate A: RB+CW, closer to blue/cyan-side behavior
candidate B: RB+WW, closer to red/yellow-side behavior
candidate C: R+CW+WW or B+CW+WW bridge triangle
```

All candidates may be chromatically plausible in a small overlap region, but one
side can end up brighter than the other if the split is chosen only by xy error
or current draw.

Goal:

```text
choose or blend the policy boundary so solved Y and max-achievable Y remain
similar across neighboring input values
```

Useful scoring signals:

```text
penalize:
    sudden Y jump across the RBCW/RBWW decision boundary
    large difference in max-achievable Y for adjacent candidate families
    abrupt change in limiting channel or active channel family

prefer:
    candidate families with similar Y range at matched xy/value
    smooth Y derivative across the split
    continuity with neighboring hue/value samples
    measured pass/fail or response-curve evidence that preserves perceived Y
```

This does not mean strict mode should blend two solved layers. It means the
strict selection policy should account for Y continuity when it chooses which
single direct simplex owns an overlap region.

##### Policy: distance-based inner-emitter fit

Distance-based inner-emitter fit is a strict overlap policy for ambiguous
5+ emitter regions where the same outer edge can be paired with more than one
inner emitter.

The motivating shape is an outer edge with two plausible inner anchors:

```text
OuterA -------- OuterB
   \            /
    \          /
     InnerA  InnerB
```

Candidate strict simplexes may include:

```text
S_A = OuterA + OuterB + InnerA
S_B = OuterA + OuterB + InnerB
```

If only one candidate contains the target xy, strict containment selects it. The
distance policy applies when both candidates are valid, both are near-valid, or
profile geometry/capture data marks the region as ambiguous.

For a target chromaticity `T`, compute xy distances to the local four-point
neighborhood:

```math
d_{OA} = \lVert T - O_A \rVert
```

```math
d_{OB} = \lVert T - O_B \rVert
```

```math
d_{IA} = \lVert T - I_A \rVert
```

```math
d_{IB} = \lVert T - I_B \rVert
```

The outer distances are shared by both candidate triangles, so they mostly
provide local scale and endpoint context. The inner distances decide which inner
anchor better fits the target in the ambiguous band.

A simple first score is:

```text
edge_scale = max(distance(OuterA, OuterB), epsilon)
inner_scale_A = max(distance(InnerA, OuterA), distance(InnerA, OuterB), epsilon)
inner_scale_B = max(distance(InnerB, OuterA), distance(InnerB, OuterB), epsilon)

score_A = distance(T, InnerA) / inner_scale_A
score_B = distance(T, InnerB) / inner_scale_B
```

A slightly richer score can include the triangle projection residual so that
near-boundary candidates remain stable when neither simplex contains the target
cleanly:

```text
score_A = w_inner * normalized_distance(T, InnerA)
        + w_resid * distance(T, project_to_triangle(T, OuterA, OuterB, InnerA)) / edge_scale

score_B = w_inner * normalized_distance(T, InnerB)
        + w_resid * distance(T, project_to_triangle(T, OuterA, OuterB, InnerB)) / edge_scale
```

Then choose:

```text
if score_A + tie_epsilon < score_B:
    choose OuterA + OuterB + InnerA
elif score_B + tie_epsilon < score_A:
    choose OuterA + OuterB + InnerB
else:
    apply the configured tie-break policy
```

Useful tie-break order:

```text
1. previous/hysteresis owner, if this is a neighboring LUT node or temporal path
2. measured pass/fail or response-curve evidence for the active family
3. power_efficiency
4. channel_resolution
5. deterministic profile order
```

This lowers ambiguity for inner-emitter split regions without silently becoming
overdrive. The solver still selects one direct strict simplex and solves that
simplex directly. It does not solve both inner-anchor layers and blend the
outputs afterward.

###### Cached split boundary form

`distance_inner_fit` is a good default candidate because the expensive part can
be computed once per emitter profile, not per LUT node. The ambiguous local
shape is usually a trapezoid or near-trapezoid bounded by:

```text
OuterA -------- OuterB
   \            /
    \          /
     InnerA  InnerB
```

The profile can precompute the split boundary where the two inner choices have
equal fit score:

```text
score_A(T) = score_B(T)
```

For the simplest Euclidean inner-distance policy this is close to the
perpendicular bisector between `InnerA` and `InnerB`, clipped to the ambiguous
outer-edge region. For the more useful normalized four-neighborhood policy, the
boundary may curve because the score also includes local scale, outer-edge
projection residual, measured response evidence, or power/channel-resolution
weights.

The cached boundary then becomes a cheap side-of-boundary test:

```text
if T is on InnerA side of cached split:
    choose OuterA + OuterB + InnerA
elif T is on InnerB side of cached split:
    choose OuterA + OuterB + InnerB
else:
    apply configured tie-break policy
```

Cache granularities should be profile/runtime selectable:

```text
line_2pt:
    store only the clipped start/end of the split. Lowest memory and fastest
    runtime path; good when the split is effectively straight.

curve_3pt:
    store start/mid/end. Useful MCU default because it captures basic curvature
    while keeping cache size very small.

curve_multipoint:
    store a small polyline with configurable point count. Useful for richer
    embedded runtimes or profiles where the split bends noticeably.

curve_full:
    PC/LUT-builder cache. Precompute the full high-granularity split curve before
    candidate generation, then optionally simplify it for export.
```

If the full curve fits a straight line within the profile tolerance, the builder
should collapse it to `line_2pt`. If a three-point approximation is within
tolerance, export `curve_3pt`; otherwise keep the requested multipoint/full
representation for host-side builds.

Tie-break metadata remains required because exact boundaries and numerical
near-ties still exist:

```text
inner_split_tie_break_policy:
    previous_owner | preferred_inner | measured_evidence | power_efficiency |
    channel_resolution | deterministic_profile_order

inner_split_tie_preferred_inner:
    InnerA or InnerB for exact/effective equality when no stronger evidence is
    available
```

This cache shape is also useful for diagnostics: verifier reports can overlay
the split line/curve on the CIE plot, show which side selected each candidate,
and identify whether a mismatch came from the split policy or from the direct
simplex solve itself.

##### Policy: constrained virtual inner anchor

A missing hue-side inner emitter can leave an awkward strict overlap decision.
For example, `RGB + CW + WW` may have inner anchors biased toward cyan/green-blue
and yellow/red, but no inner anchor near magenta. In that case the `RB` side may
not have an obviously efficient strict inner candidate.

One optional policy is to create a constrained virtual inner anchor for that
missing region:

```text
build a virtual RB-side inner point from measured/solved CW+WW behavior
use it as a virtual primary / KnownPoint for the ambiguous region
solve against that virtual point at runtime
```

This is **technically overdrive**, not strict sub_gamut, because the virtual
point is a solved/generated target rather than a physical direct simplex.
However, it can be exposed as a constrained policy because runtime behavior is
similar to the virtual-gamut / virtual-primary idea: the solver sees a coherent
virtual point and expands it back to physical channels.

The important guardrail is that a virtual point should not be introduced alone.
If only one RB-side virtual primary such as an `RB+CW+WW` virtual point is
created, then the `R-B-V_RB` region can become brighter than the neighboring
regions simply because that virtual point has more physical brightness headroom.

To keep the virtual geometry balanced, create a sibling virtual-primary set:

```text
V_RBCWWW:
    magenta-side / RB bridge virtual primary built from R/B/CW/WW behavior

V_RGCWWW:
    red-green / warm-side sibling virtual primary

V_BGCWWW:
    blue-green / cool-side sibling virtual primary
```

In the CCT-style case, the RB-side virtual primary should be balanced by
`RGCWWW` and `BGCWWW` siblings rather than replacing only one region. These
siblings can act as virtual replacements/augmentations for the neighboring WW
and CW anchors so the resulting virtual fan has comparable brightness behavior
across the outer sectors.

The exact names can be profile-defined, but the policy should preserve this
rule:

```text
do not add one isolated high-Y virtual primary;
add a coherent set of virtual primaries that replaces or augments the relevant
inner anchors across the outer sectors
```

This keeps the virtual policy closer to the existing WX/radial virtual-primary
structure and prevents a one-off virtual primary from creating an unintended
brightness island.

##### Example policy consequences

```text
CW close to cyan/green-blue:
    G+BCW or GBCW-style regions may rank efficiently near cyan/blue-green.

WW close to yellow/red:
    RGWW or red/yellow-side regions may rank efficiently near yellow/orange.

No magenta-side inner emitter:
    RB-side targets may be ambiguous. Power policy might choose the lowest
    current of RB+CW, RB+WW, R+CW+WW, or B+CW+WW. Distance-based
    inner-emitter fit might choose the inner anchor whose OuterA+OuterB+Inner
    triangle is closer to the target xy in the local four-point neighborhood.
    Channel-resolution policy might choose the candidate with better active-channel
    precision. Y-preserving policy might move the split boundary so the two sides
    have similar Y. Virtual-inner-anchor policy might create a constrained RB-side
    virtual anchor, but only alongside balanced RG/BG sibling virtual primaries.
```

#### Strict algorithm

```text
function solve_strict_multi_emitter(source_rgb, emitter_profile, strict_policy):
    X_t  = source_rgb_to_led_absolute_XYZ(source_rgb)
    xy_t = XYZ_to_xy(X_t)

    if near_black(X_t):
        return zero tuple

    emitters = load_emitter_profile()
    outer, inner, edge = classify_emitters_by_xy_hull(emitters)
    hull = build_ordered_outer_hull(outer, edge_policy=edge.policy)

    X_t, xy_t = project_to_hull_if_needed(X_t, hull)

    legal = build_strict_candidate_set(
        outer_hull=hull,
        inner_emitters=inner,
        edge_emitters=edge,
        policy=strict_policy,
    )

    candidates = []
    for simplex in legal:
        if xy_t lies on/inside simplex.xy geometry:
            candidates.append(solve_direct_simplex(simplex, X_t))

    if candidates is empty:
        candidates = direct_nnls_projection_candidates(legal, X_t)

    best = rank_strict_candidates(candidates,
                                  residual,
                                  efficiency,
                                  distance_inner_fit,
                                  headroom,
                                  pass_fail_dictionary,
                                  smoothness_hysteresis,
                                  user_policy)
    return normalize_and_quantize(best.f)
```

In function form, a strict candidate simplex `S` is solved directly:

```math
P_S t_S = X_t
```

and expanded into the full output tuple:

```math
f_i =
\begin{cases}
\dfrac{t_i}{\max(1, \max_{j \in S} t_j)}, & i \in S \\
0, & i \notin S
\end{cases}
```

This preserves the core strict invariant:

```text
strict mode = one direct local topology only
```

---

### 13.2 Multi-emitter overdrive / layered simplex model

The layered simplex behavior previously described as "the multi-emitter model"
should be understood as the **overdrive / virtual prediction** model for 5+
emitter packages.

It intentionally creates solved known points first, then solves between those
known points:

```text
1. Solve target against each selected inner-anchor or virtual sub-model.
2. Store each result as a KnownPoint with XYZxyY + physical output tuple.
3. Solve/blend between those known points using the overdrive policy.
4. Expand the final weights back into physical channels.
```

This is analogous to RGBW WX modes: it is constrained and reproducible, but it
is not the same thing as strict direct sub-gamut solving.

For RGBCCT, overdrive may do this:

```text
solve RGB+WW fan result  → KnownPoint_WW
solve RGB+CW fan result  → KnownPoint_CW
solve between KnownPoint_WW and KnownPoint_CW by CCT / Y / dE / policy
```

That is valid overdrive behavior, but it should not be documented as the strict
5-emitter sub_gamut topology.

#### Overdrive algorithm

```text
function solve_multi_emitter_overdrive(source_rgb, emitter_profile, overdrive_policy):
    X_t  = source_rgb_to_led_absolute_XYZ(source_rgb)
    xy_t = XYZ_to_xy(X_t)

    if near_black(X_t):
        return zero tuple

    emitters = load_emitter_profile()
    outer, inner, edge = classify_emitters_by_xy_hull(emitters)
    X_t, xy_t = project_to_profile_hull_if_needed(X_t, outer, edge)

    known_points = []

    for layer in overdrive_policy.selected_layers(inner, edge):
        layer_result = solve_layer_or_virtual_model(X_t, xy_t, layer)
        known_points.append(KnownPoint(
            XYZ=layer_result.expected_XYZ,
            xyY=layer_result.expected_xyY,
            output_tuple=layer_result.physical_channels,
            source="multi_emitter_overdrive_layer",
            trust=layer_result.trust,
        ))

    final_weights = solve_target_inside_known_points(xy_t, known_points,
                                                     overdrive_policy)
    f = sum(weight_j * known_points[j].output_tuple for j in final_weights)
    f = normalize_by_limiting_channel(f)
    return quantize(f)
```

Overdrive ranking can intentionally optimize objectives that strict mode should
not optimize directly:

```text
maximize useful Y / HDR wallwash brightness
choose a CCT or tint path between inner anchors
blend toward measured lower-dE known points
increase W/CW/WW participation when verifier says it is stable
respect thermal/current budgets and user brightness policy
```



## 14. Tetrahedral LUT interpolation

The LUT runtime should use tetrahedral interpolation by default.

For interleaved candidate-LUT sets, tetrahedral interpolation applies inside each
candidate LUT. Candidate selection, split-surface lookup, optional inter-family
blend, and input-coordinate precision are separate parts of the interleaved
runtime contract and should not be hidden inside one ordinary mixed-family cube
unless the export is explicitly marked as a fully baked/debug LUT.

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


## 15. Spectral characterization and color-rendition reports

Spectral reporting is a diagnostic/profile layer. It should not be required for
the core LUT solve, but it becomes available when a spectrophotometer capture
provides a spectral power distribution rather than only XYZxyY.

Important separation:

```text
XYZxyY / Lab / LCh / Luv:
    usable from colorimeter or corrected colorimeter measurements
    normal builder/verifier correction path

SPD:
    requires spectrophotometer data
    required for CRI, TM-30, TLCI, SSI, and deeper emitter analysis
```

### Spectral measurement record

A spectral record should be tied to the same output tuple and active family
metadata used elsewhere:

```text
SpectralMeasurement:
    measurement_id
    display_profile_id
    emitter_profile_id
    instrument_id
    instrument_correction_id
    geometry_id
    output_tuple
    active_channel_family
    model_family
    wavelength_nm[]
    spectral_power[]
    integration_time / spotread options
    raw_or_corrected_policy
    derived_XYZxyY
    derived_CCT_Duv when meaningful
```

Single-emitter records should be captured for every physical channel:

```text
R, G, B, W, CW, WW, amber, violet, yellow, etc.
```

Mixed-family records should be optional but useful:

```text
neutral ramp
strict sub_gamut white / near-white families
WX / overdrive high-W families
RGB+CCT inner-anchor blends
known verifier pass/fail stress patches
representative user-selected output tuples
```

### Per-emitter spectral statistics

Basic per-emitter statistics:

```text
peak wavelength
dominant wavelength
centroid wavelength
FWHM
spectral bandwidth / shape notes
chromaticity x/y and u'/v'
CCT and Duv when the emitter or mix is white-like enough
relative or absolute Y / luminous flux proxy when calibrated
```

These values help explain why two emitters with similar xy can behave
differently under a colorimeter, why an Argyll CCXX correction is needed, and why
some multi-emitter regions may have different measured residuals even when the
topology solve is numerically valid.

### CRI / CIE 13.3 report

When an SPD is available, the report layer may compute CIE CRI-style values:

```text
CRIReport:
    Ra
    Ri[1..N]
    R9 and other commonly inspected special indices
    reference illuminant / CCT region
    warning flags for low-CCT/high-CCT/low-Y/out-of-scope use
```

CRI remains useful because it is widely recognized, but it should not be the only
quality metric for LED and multi-emitter systems.

### ANSI/IES TM-30 report

TM-30 should be treated as the preferred richer color-rendition report when a
compliant implementation is available:

```text
TM30Report:
    Rf
    Rg
    color vector graphic data
    hue-bin fidelity
    hue-bin chroma shift
    hue-bin hue shift
    local chroma/fidelity tables
```

For this project, TM-30 is useful both per emitter and for mixed outputs:

```text
single white / CW / WW channels
strict neutral and near-white solves
WX / maxbright high-W outputs
RGB+CCT blend paths
user-selected ambient/wallwash operating points
```

### Optional report families

Additional report adapters can be added behind optional dependencies:

```text
CIE 224-style fidelity:
    optional CIE color-fidelity metric for scientific comparison.

TLCI:
    useful for camera/video-oriented lighting and wallwash setups.

SSI:
    useful when comparing an emitter/mix against a selected reference spectrum.

LM-79-style summary:
    useful report framing for SSL-style photometric/colorimetric measurements,
    without claiming formal accredited LM-79 compliance.
```

### Report artifacts

Spectral reports should be exported separately from LUT metadata but linkable
from display/emitter profiles and verifier rows:

```text
SpectralReport:
    report_id
    display_profile_id
    emitter_profile_id
    geometry_id
    instrument_id
    spectral_measurement_ids
    standards: cri | tm30 | tlci | ssi | lm79_summary | custom
    per_emitter_results
    mixed_family_results
    warnings
    generated_at
```

The builder may use these reports for human diagnostics and profile metadata,
but the normal correction path should continue to operate on corrected XYZxyY,
measured response providers, pass/fail dictionaries, and capture-cloud residuals.

---


## 16. Future physical-solution policy axis

The earlier `interleaved_rgb_assisted_y_linear` idea should be treated as a
future physical-solution policy axis, not as a separate detailed section in the
multi-emitter chapter.

The problem it tries to solve is not:

```text
which region of input RGB should use RGB, strict RGBW, WX, or overdrive?
```

The problem is:

```text
given the input value domain and the emitted-Y values available from several
legal candidate LUTs, which precise physical output value can the runtime
actually achieve?
```

That makes emitted-Y part of the normative runtime contract. Without the
candidate Y contract, the model can only guess from topology, emitter strength,
or policy preference. That is not sufficient for this solve family.

This section is future implementation / research. It may be experimented with
before every other roadmap item is complete, but it should remain documented as a
future policy axis until the core builder and measured response contracts are
stable.

---

### 16.1 Canonical source-domain rule

Candidate LUTs in this family are still normal calibrated source-domain LUTs.
They already answer:

```text
source RGB input → calibrated output tuple for this model family
```

Therefore the runtime must not do a second device-space solve such as:

```text
source RGB
→ target XYZxyY
→ search/solve device response
→ synthesize a replacement RGB coordinate
→ sample a candidate LUT
```

That would risk calibrating a calibration. The candidate LUTs were built for the
canonical source RGB domain, so their sampling coordinate should remain tied to
that same source-domain request.

The allowed runtime decomposition is source-domain only:

```text
source RGB input
→ source ray / value decomposition
→ candidate emitted-Y comparison
→ choose one candidate family
→ sample that candidate LUT using source-domain coordinates
```

The important refinement is that each candidate LUT does **not** have to pretend
its own storage covers a normalized `0..1` local input domain. Because these
cubes are generated by the builder, each candidate can store the canonical input
RGB tuple associated with every node, or can declare a source-domain coverage
range for its grid.

For example, along a given source ray a profile might export candidate coverage
like this:

```text
chromatic / RGB-only candidate:
    source-value coverage ≈ 0.000 .. 0.557

strict-assisted candidate:
    source-value coverage ≈ 0.270 .. 0.880

overdrive-assisted candidate:
    source-value coverage ≈ 0.370 .. 1.000
```

Those numbers are not local LUT-normalized coordinates. They are positions in
the actual requested input domain. If the runtime receives `rgb_in = d * 0.42`,
then every eligible candidate is addressed at that same source-domain position
rather than first remapping `0.42` into `0..1` inside that candidate's cube.

A candidate may still use an internal index transform to find cells quickly:

```text
source value / RGB coordinate → candidate grid index
```

but that is an addressing detail, not a semantic remap of the input request.
The semantic coordinate remains the canonical source-domain RGB value.

Black should remain an early exit:

```text
rgb_in = 0 → zero output tuple
```

A strict-assisted or overdrive candidate does not need to waste a `0,0,0` node
only to represent a direct request for no emitted light. Each candidate's first
stored node may instead be its lowest useful / lowest measurable emitted-Y state
for that source ray or grid region.

Single-channel and direct dual-channel families remain separate direct-topology
storage. They are not part of the three-channel candidate interleave because a
single physical emitter or direct two-emitter line cannot be overdriven by the
same RGB/strict/overdrive choice without an explicit virtual redirection policy.

For the default source-domain contract, the final lookup coordinate is the
original input RGB, interpreted through the selected candidate's declared
source-domain grid:

```text
coord = rgb_in
```

A future family-local inverse-Y coordinate contract may be added, but it must be
explicitly marked as a different contract and must preserve the same source
chromatic ray:

```text
rgb_in = d * v_req
coord_k = d * v_k
```

where `v_k` is chosen from candidate `k`'s own emitted-Y contract. It must not be
an arbitrary RGB replacement found by a new XYZ/device solve.

---

### 16.2 Candidate families

The policy axis exposes multiple legal physical candidates for the same source
RGB request.

For RGBW, useful candidates are:

```text
C_chromatic:
    RGB-only / no-W calibrated LUT

C_strict_assisted:
    strict RGBW sub-gamut calibrated LUT

C_overdrive:
    optional WX / virtual-primary / max-white calibrated LUT
```

For RGB+CCT or larger multi-emitter profiles, the chromatic candidate can be a
profile-declared no-inner / no-white hull:

```text
RGB
RGBY
RGBV
RGBCMY
outer-hull-only profile candidate
```

The strict-assisted candidate is the selected strict local-topology solve. The
overdrive candidate is explicitly a virtual/layered/WX policy and must keep its
own provenance.

Single-channel and direct dual-channel requests are direct-topology exceptions.
They should be stored once in a shared direct-family path rather than duplicated
inside every candidate tier. A single physical emitter or direct two-emitter line
has no meaningful inter-family overdrive alternative unless the profile
explicitly redirects it into a virtual model.

---

### 16.3 Required emitted-Y contract

For every candidate family `k`, the artifact must expose both:

```text
L_k(rgb): calibrated output tuple for candidate k
Y_k(rgb): emitted-Y produced by L_k(rgb)
```

`Y_k` is required for this policy axis. It is not optional debug data.

A candidate record should distinguish:

```text
Y_ideal:
    continuous model-predicted Y before output quantization or encoding

Y_emit:
    predicted/measured emitted Y after the actual output tuple, quantization,
    TemporalBFI encoding, chipset depth, or response backend
```

Runtime candidate selection must use `Y_emit` by default because that is the
value the hardware can actually produce. `Y_ideal` is useful for diagnostics,
error reporting, and builder QA, but it cannot be the final selection truth if
quantization or temporal encoding changes the emitted luminance.

The Y field must use the same interpolation contract as the candidate output
LUT, or must declare its own compatible contract. For a vertex-tetra candidate
LUT, the simplest representation is:

```text
node:
    output tuple
    Y_emit
    optional Y_ideal
    optional confidence / measurement provenance
```

For a coefficient-tetra runtime, Y may be stored as an additional interpolated
channel or as a separate affine field.

---

### 16.4 Union-of-Y-contracts model

For a source-domain chromatic ray:

```math
rgb = d \, v
```

where:

```math
d = \frac{rgb}{\max(r,g,b)}
```

and `v` is the source-domain value scalar, each candidate family exposes a Y
curve along that same ray over its own valid source-domain coverage interval:

```math
Y_{chromatic}(d,v), \quad v \in [v_{min,chromatic}(d), v_{max,chromatic}(d)]
```

```math
Y_{strict}(d,v), \quad v \in [v_{min,strict}(d), v_{max,strict}(d)]
```

```math
Y_{overdrive}(d,v), \quad v \in [v_{min,overdrive}(d), v_{max,overdrive}(d)]
```

The coverage interval is expressed in the canonical input domain, not in a
candidate-local `0..1` coordinate system. A candidate can therefore start at the
lowest Y it can usefully emit and end at the highest source-domain request it is
intended to serve.

The runtime sees the available emitted luminance set as the union of candidate
contracts at the requested source-domain position:

```math
\mathcal{Y}_{available}(d,v_{req})
= \{Y_k(d,v_{req}) \mid k \in \mathcal{C},\ v_{req} \in coverage_k(d)\}
```

The selection goal is:

```text
find the candidate whose emitted-Y contract gives the closest achievable output
for this exact source-domain request
```

This is why the mode is not a policy-overloaded region map. A family wins only
because its emitted-Y contract provides a better achievable value or local
bracket for the current request. The candidate cube is then sampled at the same
source-domain input position, not at a remapped local 0..1 coordinate.

---

### 16.5 Runtime decision by Y bracket

For each candidate `k`, runtime should first test whether the requested
source-domain coordinate is inside that candidate's declared coverage:

```text
v_req ∈ coverage_k(d)
```

If the candidate is eligible, the runtime evaluates the candidate's emitted-Y
contract at the same source-domain request:

```text
Y_hit_k = Y_k(d, v_req)
```

The local Y bracket / step is still important, but it is measured around the
candidate's source-domain neighborhood rather than by mapping the input into a
candidate-local `0..1` axis:

```text
Y_lower_k <= Y_hit_k <= Y_upper_k
bracket_width_k = Y_upper_k - Y_lower_k
nearest_error_k = abs(Y_hit_k - Y_req)
```

`Y_lower_k` and `Y_upper_k` may come from adjacent source-domain nodes, a
monotonic envelope, or a local derivative field. They describe what the selected
candidate can actually emit near the requested input value.

The primary selection terms are:

```text
1. candidate covers this source-domain request
2. nearest emitted-Y error at this request
3. local bracket width / Y step around this request
4. invalid or nonmonotonic-region penalties
```

Tie-breaks may exist, but they are secondary:

```text
lower estimated power
previous candidate family for temporal stability
strict before overdrive
higher measured confidence
profile-preferred candidate family
```

Tie-breaks must not override a materially better emitted-Y value. They only
resolve near-equal cases.

A minimal runtime selection form:

```text
function lookup_physical_solution_axis(rgb_in):
    if near_black(rgb_in):
        return zero tuple

    if direct_single_or_dual_exception_applies(rgb_in):
        return direct_family_lookup(rgb_in)

    d, v_req = decompose_source_ray(rgb_in)
    Y_req    = source_luminance_request(rgb_in)

    best = none

    for candidate k in enabled_candidates:
        if not candidate_covers_source_value(k, d, v_req):
            continue

        Y_hit = sample_or_probe_Y_emit(k.Y_contract, rgb_in)
        step  = estimate_local_Y_step(k.Y_contract, d, v_req)
        err   = abs(Y_hit - Y_req)

        score = err
              + w_step * step
              + nonmonotonic_penalty(k, d, v_req)
              + confidence_penalty(k, d, v_req)
              + tie_break_penalty(k)

        if score < best.score:
            best = {candidate: k, score: score}

    return tetra_sample(best.candidate.output_lut, rgb_in)
```

The default contract is therefore:

```text
lookup coordinate = original source-domain RGB input
```

A future explicit inverse-Y coordinate contract may allow:

```text
coord = d * v_k
```

but that is a separate mode. It should not be assumed by the canonical
source-domain candidate-LUT format.

---

### 16.6 Y bracket / local-step accelerator

Runtime cannot scan an entire 3D LUT ray for every pixel. The emitted-Y contract
therefore needs an accelerator, but the accelerator is not the source of truth.
The source of truth remains `Y_emit`.

For the canonical source-domain candidate-LUT contract, the accelerator is not
primarily used to find a replacement source value. It is used to answer cheap
questions at the current source-domain coordinate:

```text
is this candidate valid at rgb_in?
what is Y_emit at rgb_in?
what is the local Y step / bracket width near rgb_in?
is this region monotonic and trusted?
```

Useful accelerator forms:

```text
source_domain_coverage:
    per candidate and direction/key, store the valid source-value interval such
    as 0.000..0.557, 0.270..0.880, or 0.370..1.000

ray_value_table:
    for each direction key and candidate, store source-domain Y knots along the
    source value axis

monotonic_envelope:
    fitted or corrected monotonic curve used for local step/bracket estimates
    while preserving flags for raw nonmonotonic regions

direct_index_table:
    optional coarse map from source value or Y bucket to a nearby local bracket

slope_field:
    local derivative / Y-step estimate around each knot for faster scoring
```

The accelerator may be coarse as long as final scoring verifies against the
candidate's actual interpolated `Y_emit` field at or near `rgb_in`.

A practical runtime sequence:

```text
1. reject candidates whose source-domain coverage does not include rgb_in
2. sample or probe Y_emit for each remaining candidate at rgb_in
3. use the accelerator to estimate local Y step / bracket width around rgb_in
4. choose the candidate with the closest achievable emitted-Y and useful step
5. sample only the selected candidate output LUT at rgb_in
```

This preserves the precise Y contract without doing broad repeated cube lookups
or remapping the request into a candidate-local normalized domain.

---

### 16.7 Nonmonotonic and quantized Y behavior

The builder must inspect each candidate's emitted-Y curve along source rays.
Ideally:

```text
as source value increases, Y_emit increases
```

In practice, measurement noise, endpoint policies, quantization, TemporalBFI
state changes, chipset bit depth, or interpolation can create local reversals.
The artifact should record:

```text
raw Y_emit samples
monotonic inverse envelope
nonmonotonic region flags
blocked / low-trust ranges
local bracket width
local nearest achievable Y error
```

Runtime inverse lookup should use the monotonic envelope for fast bracketing, but
candidate scoring should verify against raw/interpolated `Y_emit` near the chosen
value. Badly nonmonotonic regions should either be blocked, forced to a safer
candidate, or require a denser measured/corrected profile.

---

### 16.8 Storage contract

The canonical future artifact is:

```text
PhysicalSolutionPolicySet:
    model_family = future_physical_solution_policy_axis
    source_domain = canonical_source_rgb
    coordinate_contract = source_domain_embedded_grid | source_domain_original | source_ray_inverse_y

    CandidateLUTSet:
        chromatic_candidate_lut
        strict_assisted_candidate_lut
        overdrive_candidate_lut optional
        per-candidate source-domain coverage
        per-node canonical input RGB tuple or declared source-domain grid
        no requirement that each candidate maps its own storage to 0..1

    CandidateYContractSet:
        Y_emit field for each candidate
        Y_ideal optional
        interpolation contract
        source-domain coverage interval / coverage surface
        bracket / local-step accelerator
        monotonic envelope
        nonmonotonic flags
        measurement/prediction confidence

    SharedDirectFamilyTable:
        black
        single physical emitters
        legal dual-emitter direct lines

    SelectionContract:
        emitted-Y nearest/bracket scoring
        local Y-step metric
        invalidity handling
        tie-break policy
        temporal hysteresis policy optional

    RuntimeFormula:
        candidate Y lookup
        bracket / inverse lookup
        candidate scoring
        one selected candidate LUT sample
        output quantization / packing
```

A candidate LUT may be stored as a partial source-domain grid. The grid can skip
black, begin at the candidate's lowest useful Y, and stop where that candidate no
longer participates. Runtime lookup should therefore treat the candidate's grid
as embedded in the canonical input domain:

```text
candidate grid coordinate labels = source RGB values
candidate eligibility = rgb_in lies inside declared source-domain coverage
candidate lookup = interpolate using rgb_in against that source-domain grid
```

A selector ownership LUT is not canonical for this policy axis. It may be useful
as a debug export or as a cache for a specific constrained runtime, but it cannot
replace the emitted-Y contract because it loses the exact available-Y
information the mode is designed to preserve.

A fully baked single output LUT is also diagnostic/export-only unless the grid is
dense enough to be effectively 1:1 with the desired input space. A normal mixed
3D LUT can interpolate across unrelated candidate families and synthesize output
tuples the policy never selected.

---

### 16.9 Input precision and output precision

Input precision and output precision are separate contracts.

The input coordinate expresses the requested source-domain color and Y intent:

```text
input: q16 | q32 | float | double
```

The output expresses the physical drive payload:

```text
output: RGB8 | RGBW8 | RGBW16 | channels16 | TemporalBFI state | chipset packet
```

A value such as `256,256,256` in a 16-bit input domain is a low requested
neutral luminance. It is not equivalent to requesting `255,255,255` out of an
8-bit output range. The emitted-Y contract determines which candidate can best
represent that low neutral request with the available physical output granularity.

Higher precision input forms such as `q32`, `float`, or `double` may be useful
for this future policy axis because the candidate Y bracket can carry meaningful
source-side granularity even when the final emitted channels remain 8-bit,
16-bit, or TemporalBFI encoded.

---

### 16.10 Metadata

Required or recommended metadata:

```text
model_family = future_physical_solution_policy_axis
candidate_tiers = chromatic | strict_assisted | overdrive_assisted
chromatic_candidate_family = rgb_only | rgby | rgbv | rgbcmy | profile-defined
strict_assisted_candidate_family
overdrive_candidate_family
candidate_lut_ids
candidate_y_contract_ids
candidate_Y_lookup_layout
candidate_Y_interpolation_contract
candidate_Y_inverse_accelerator
candidate_source_domain_coverage
candidate_source_domain_grid_layout
candidate_node_input_rgb_tuple optional
candidate_lowest_useful_Y
candidate_first_stored_source_value
candidate_Y_monotonic_envelope
candidate_Y_nonmonotonic_flags
candidate_Y_emit_units
candidate_Y_ideal_units optional
selected_candidate_family
selected_candidate_source_value
candidate_y_error
candidate_y_bracket_width
candidate_y_step_estimate
candidate_xy_error
candidate_headroom
candidate_confidence
shared_direct_family_id
selection_contract = emitted_y_nearest_bracket
coordinate_contract = source_domain_embedded_grid | source_domain_original | source_ray_inverse_y
input_coordinate_type = q16 | q32 | float | double
output_channel_contract = RGB8 | RGBW8 | RGBW16 | channels16 | TemporalBFI | profile-defined
physical_solution_tie_break_policy
physical_solution_hysteresis_policy optional
```

Verifier and correction dictionaries must preserve candidate provenance. An
RGB/no-W pass is not strict-assisted evidence, a strict-assisted fail is not an
overdrive fail, and an overdrive measurement should not poison a chromatic-only
candidate that emits a different physical tuple for the same source RGB request.

---

### 16.11 Practical RGBW interpretation

For common RGBW packages, W often has far higher Y per code than R and B, and is
frequently stronger than G. A strict-assisted output can be more efficient and
brighter, but its emitted-Y ladder may skip over low or mid luminance values that
an RGB-only tuple can hit with weaker emitters.

For a neutral or low-saturation source request, the runtime should not select a
candidate because a policy says "neutral uses W" or "low values use RGB." It
should select the candidate whose `Y_emit` contract lands closest to the
requested luminance with the smallest useful bracket.

At higher values, strict-assisted may become the only candidate with enough
headroom. Above that, an explicitly enabled overdrive candidate may add useful
Y range or finer high-Y placement. The same emitted-Y contract decides these
transitions.

Green-heavy directions need special care because RGB-only granularity is not
uniform: green can have much more Y headroom than red or blue. The model should
therefore use the actual candidate `Y_emit` curves rather than assuming the RGB
candidate is always the fine-granularity option.

---

### 16.12 Effective input bit resolution for mixed candidate sets

The input-coordinate discussion above says which numeric representation a runtime
may use (`q16`, `q32`, `float`, `double`). A separate question is how much input
resolution is actually useful once several candidate LUTs expose measurably
different emitted-Y values for the same source-domain request.

For a single ordinary RGB→RGB model, the answer is effectively one-to-one with
the output device contract:

```text
RGB8 output  → RGB8-style source axes are normally sufficient
RGB16 output → RGB16-style source axes are normally sufficient
```

There is no inter-family Y selection to preserve. The source coordinate only has
to address the one calibrated output model.

For the future physical-solution policy axis, the source RGB value also acts as a
requested emitted-Y scalar along a chromatic ray. If multiple candidate LUTs are
available, each candidate contributes its own distinct emitted-Y ladder:

```text
RGB / chromatic-only candidate
strict-assisted candidate
overdrive-assisted candidate, optional
```

The useful input precision is then governed by the union of emitted-Y values that
are measurably different after the final output contract:

```text
Y_emit after RGB8 / RGBW8 quantization
Y_emit after RGBW16 / channels16 quantization
Y_emit after TemporalBFI encoding / monotonic ladder selection
Y_emit after chipset-specific output packing
```

The builder should compute this from the actual `Y_emit` contracts when possible:

```text
For each source ray d:
    collect every candidate's emitted-Y samples over its source-domain coverage
    merge values that are within the profile's measurement / visibility tolerance
    count the remaining distinct emitted-Y opportunities
    derive the input scalar bits needed to address that union
```

In notation:

```math
N_{union}(d) = \left|\bigcup_k \{Y_{emit,k}(d,v)\}\right|_{\epsilon_Y}
```

```math
B_{input,useful}(d) \approx \lceil \log_2(1 + N_{union}(d)) \rceil
```

where `ε_Y` is the profile's merge tolerance for values that are not measurably
or visibly different.

When the full `Y_emit` scan is not available, a planning approximation can use
the effective output bit depth, active channel-event count, and source-domain
coverage width.

Let:

```text
B_out_eff = effective output luminance precision
            examples: 8, 16, 15.5 for a TemporalBFI ladder with ~15.5 useful bits

Q_eff     = 2^B_out_eff - 1

m_k       = effective active channel-event count for candidate k along the ray
            RGB-only path: usually 3
            strict RGBW/RGBWW direct path: usually 3, because strict output is
                still one direct 3-emitter simplex at a time
            RGBW overdrive path: up to 4 when the overdrive tuple can use all
                RGBW channels distinctly
            RGBWW / RGBCCT overdrive path: up to 5 when the overdrive tuple can
                use all RGBWW/CCT channels distinctly

w_k(d)    = source-domain coverage width for candidate k along ray d
            for example 0.000..0.557 has width 0.557
```

A local density estimate is:

```math
D(d,v) \approx \sum_{k \in eligible(d,v)} \frac{m_k \cdot Q_{eff}}{w_k(d)}
```

and the useful source scalar precision for the densest overlap is:

```math
B_{input,useful} \approx \left\lceil \log_2(1 + \max_{d,v} D(d,v)) \right\rceil
```

This is not a replacement for the real `Y_emit` contract. It is only a planning
estimate for choosing `q16`, `q32`, `float`, or `double` input coordinates and
for deciding whether a candidate-set experiment is worth storing at a given
precision.

#### Full-range planning examples

If every candidate covers the whole `0..1` source-value range and its emitted-Y
ladder is measurably distinct from the others, the estimate reduces to:

```math
B_{input,useful} \approx \left\lceil B_{out,eff} + \log_2\left(\sum_k m_k\right) \right\rceil
```

Approximate examples:

```text
Candidate set                                  Σm_k    useful bits @ 8-bit   @ 15.5-bit TemporalBFI   @ 16-bit
RGB + strict RGBW/RGBWW                         6          11                     19                  19
RGB + strict + RGBW overdrive                  10          12                     19                  20
RGB + strict + RGBWW/RGBCCT overdrive          11          12                     19                  20
```

The strict RGBW/RGBWW row stays at `3` active channel-events for the strict
candidate because strict mode still emits one legal local 3-emitter topology at a
time. The overdrive row can increase to `4` or `5` because virtual/layered
outputs may use more physical emitters at once.

These values should be read as useful source-scalar planning targets, not as a
claim that an ordinary single-family RGB LUT requires more than its normal source
precision.

#### Source-domain coverage compression

Candidate LUTs in this policy axis should not be treated as independent local
`0..1` domains. Each candidate can declare where its nodes live in the canonical
input domain. This changes the useful input precision because emitted-Y events
can be packed into only part of the source range.

Using the illustrative source-domain coverage ranges:

```text
RGB-only:   0.000 .. 0.557  width 0.557
strict:     0.270 .. 0.880  width 0.610
overdrive:  0.370 .. 1.000  width 0.630
```

For an RGBW overdrive-capable TemporalBFI profile with `B_out_eff ≈ 15.5`:

```text
RGB + strict overlap:
    density factor ≈ 3/0.557 + 3/0.610 ≈ 10.30
    useful input bits ≈ ceil(15.5 + log2(10.30)) = 19

RGB + strict + RGBW overdrive overlap:
    density factor ≈ 3/0.557 + 3/0.610 + 4/0.630 ≈ 16.65
    useful input bits ≈ ceil(15.5 + log2(16.65)) = 20
```

For the same coverage shape with true 16-bit output, the dense three-candidate
RGBW case rounds up to roughly `21` useful source-scalar bits. For 8-bit output,
the same shape is closer to `13` useful source-scalar bits.

The practical consequence is that a `q16` input coordinate may be adequate for a
single 15.5-bit or 16-bit output family, but it can truncate some useful
candidate-selection headroom when RGB, strict-assisted, and overdrive-assisted
families all produce distinct emitted-Y ladders in overlapping source-domain
intervals. `q20`/`q21` fixed-point source coordinates, or `float`, are more
credible targets for a high-precision interleaved experiment.

#### Low-end D65 probe interpretation

Manual TemporalBFI `channels16` probes against the monotonic ladder showed the
expected pattern on the wall-wash bench: RGB-only could land near D65 at roughly
the low-nit floor, while strict RBW/RGW/BGW and overdrive RGBW-style candidates
first approached D65 at substantially higher luminance. In the observed bench
sample, RGB-only near-D65 appeared around `~2 nits`, strict-assisted near-D65
states appeared closer to `~6.5–8 nits`, and overdrive-style near-D65 states
were around `~7 nits`.

That supports two storage rules:

```text
1. Black remains a shared early exit. Candidate cubes do not need to spend their
   first node on 0,0,0 when that request always means no emitted light.

2. A candidate cube's first stored node should be close to that family’s lowest
   useful / lowest measurable source-domain state, such as the first near-D65
   point for a neutral path, not an artificial local-domain zero.
```

On the installed HyperHDR wall-wash setup, distance and spread may push the same
lowest useful D65 floor lower than the bench box measurement, so this threshold
belongs in the emitted-Y/profile contract rather than being hardcoded.

The same analysis applies to RGBWW/RGBCCT. Adding more emitters can create finer
control once the family is active, but it can also raise the lowest useful
near-neutral floor because more participating emitters usually means a brighter
minimum measurable state.

---

### 16.13 Implementation status

This policy axis should remain future/research until the following are stable:

```text
candidate LUT generation for chromatic, strict-assisted, and optional overdrive
candidate emitted-Y sidecars from predicted and/or measured response providers
candidate provenance in verifier and pass/fail dictionaries
monotonic Y-envelope generation and nonmonotonic diagnostics
runtime/export format for candidate LUT sets and Y contracts
```

Possible future modules:

```text
rgbw_lut_builder/model/physical_solution_policy.py
    source-domain candidate selection from emitted-Y contracts

rgbw_lut_builder/model/candidate_y_contract.py
    Y_emit fields, inverse/bracket accelerators, monotonic envelopes,
    nonmonotonic flags, and local Y-step metrics

rgbw_lut_builder/output/interleaved_candidate_set.py
    candidate LUT set export, Y contract export, runtime formula metadata, and
    optional fully baked debug LUT export
```

The core invariant for future implementation is:

```text
no emitted-Y contract, no physical-solution policy axis
```
