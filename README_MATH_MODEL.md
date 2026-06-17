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



### 13.3 Interleaved RGB / assisted Y-linear solve

An additional planned solve family is an interleaved candidate-selection mode.
It treats a no-W RGB or chromatic-only solve as a valid candidate alongside the
selected assisted model:

```text
candidate A: RGB / chromatic-only direct solve
candidate B: strict RGBW, WX, RGB+CCT, multi-emitter strict, or overdrive solve
selection:   choose the candidate that best tracks xy/Y and useful Y granularity
```

The motivation is Y quantization and channel granularity. In many RGBW strips,
the W emitter is far brighter than the R/G/B emitters. A W-assisted solve can be
more power efficient and can reach higher Y, but a one-code or one-response-step
change in W may move luminance farther than the target ladder wants. A no-W RGB
solve uses weaker emitters and may fill intermediate Y levels that sit between
W-assisted steps.

This remains a constrained model. It is not an unconstrained RGBW optimizer. Each candidate is solved through its own legal topology, and the final node records which candidate family won.

#### Candidate set

For a target `X_t`, `xy_t`, and requested luminance `Y_t`, generate:

```text
C_rgb:
    direct RGB / chromatic-only candidate when xy_t lies inside that candidate
    hull and the solve is physically valid

C_assisted:
    selected RGBW/CCT/5+ model candidate, such as strict sub_gamut,
    wx_radial_virtual, wx_virtual_axis_maxbright, wx_lp_legacy,
    strict_multi_emitter_subgamut, or multi_emitter_overdrive
```

For RGBW:

```text
C_rgb = solve_xyz([R, G, B], X_t)
C_assisted = selected RGBW strict/WX candidate
```

For multi-emitter packages, the chromatic-only candidate can be profile-defined:

```text
RGB
RGBY
RGBV
RGBCMY
outer-hull-only candidate set
other no-inner/no-W chromatic simplexes declared by the emitter profile
```

The chromatic-only candidate is valid only where it can solve the target xy and
Y with acceptable residual or a profile-defined projection. If the target falls
outside the chromatic-only hull, this mode falls back to the assisted candidate.

#### Y-step / granularity metric

Each candidate should expose an estimated local Y step. A simple first metric is
based on active-channel full-drive Y and output quantization:

```math
\Delta Y_{q,i} \approx \frac{P_{i,Y}}{Q_i}
```

where `Q_i` is the effective code depth for channel `i` after any TemporalBFI,
True16, or chipset response mapping.

For a candidate output tuple `f`, the local luminance quantum can be estimated
from the active channels:

```text
candidate_y_step = min useful local Y step from active channels
```

or from a response provider:

```text
candidate_y_step = local derivative / inverse-response step around f
```

W, CW, and WW often have much larger `P_Y` than R/B and usually larger than G.
So a W-assisted candidate may have better efficiency but worse fine Y spacing.
The RGB/chromatic-only candidate may have lower maximum Y but better intermediate
Y placement.

#### Selection score

A first profile score can be:

```text
score(candidate) =
    w_xy      * xy_error(candidate, target)
  + w_Y       * abs(Y_candidate - Y_t)
  + w_step    * candidate_y_step
  + w_headroom* clipping_or_headroom_penalty
  + w_switch  * topology_switch_penalty
  + w_trust   * measured_response_uncertainty
```

Lower score wins. Useful policies:

```text
y_linear_first:
    prioritize target-Y tracking and local Y-step quality.

efficiency_first:
    prefer W-assisted output unless RGB/chromatic-only materially improves Y
    placement or avoids a visible luminance jump.

hysteresis_locked:
    require a minimum improvement before switching candidate family, preventing
    alternating RGB/W-assisted choices across neighboring LUT nodes.

measured_response_first:
    prefer the candidate family with better verifier/pass/fail or response-curve
    evidence for the current hue/Y bucket.
```

#### Algorithm

```text
function solve_interleaved_y_linear(source_rgb, profile, assisted_mode, policy):
    X_t  = source_rgb_to_led_absolute_XYZ(source_rgb)
    xy_t = XYZ_to_xy(X_t)
    Y_t  = X_t.Y

    candidates = []

    rgb_candidate = solve_chromatic_only_candidate(X_t, xy_t, profile)
    if rgb_candidate is valid:
        candidates.append(rgb_candidate.with_family("chromatic_only"))

    assisted_candidate = solve_selected_assisted_model(X_t, xy_t,
                                                       assisted_mode,
                                                       profile)
    if assisted_candidate is valid:
        candidates.append(assisted_candidate.with_family("assisted"))

    for candidate in candidates:
        candidate.xy_error = compute_projected_xy_error(candidate, target)
        candidate.Y_error = abs(candidate.predicted_Y - Y_t)
        candidate.y_step = estimate_local_Y_quantum(candidate, response_provider)
        candidate.headroom = estimate_channel_headroom(candidate)
        candidate.trust = lookup_response_trust(candidate.family, hue_Y_bucket)

    selected = rank_candidates(candidates, policy)
    selected = apply_endpoint_luminance_policy_if_direct(selected, policy)
    return quantize(selected.output_tuple), selected.metadata
```

#### Metadata

```text
model_family = interleaved_rgb_assisted_y_linear
assisted_model_family = strict_subgamut | wx_radial_virtual | wx_virtual_axis_maxbright | wx_lp_legacy | ...
chromatic_candidate_family = rgb_only | rgby | rgbv | rgbcmy | profile-defined
selected_candidate_family = chromatic_only | assisted
candidate_y_error
candidate_xy_error
candidate_y_step_estimate
candidate_headroom
interleave_selection_policy
interleave_hysteresis_state
```

Verifier and correction dictionaries must preserve this metadata. The same input
RGB can be solved by two different physical families, and those measurements are
not interchangeable.

#### Practical RGBW interpretation

For a neutral or low-saturation color, W-assisted output may be the most power
efficient and brightest result. For an edge or hue where the W-assisted path
jumps from `Y_1` to `Y_5`, the RGB-only candidate may be able to land near
`Y_2`, `Y_3`, or `Y_4` because it uses weaker emitters. This can remain useful
even with TemporalBFI or true 16-bit chipsets, because the physical Y per code is
still different for each emitter.

Green usually has more Y headroom than red or blue in common RGBW packages, so
RGB-only granularity is not perfectly uniform. The mode should therefore score
actual response curves rather than assuming all RGB channels are equally weak.


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
