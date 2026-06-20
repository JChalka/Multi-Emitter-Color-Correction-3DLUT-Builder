# Spectrophotometer / Argyll CCXX Workflow

This companion README owns the spectrophotometer, colorimeter-correction, and
Argyll `ccxxmake` integration plan for the RGBW / multi-emitter LUT builder.
The roadmap should only keep a compact status entry for this work and link here
for the detailed implementation contract.

The goal is to let the project use a spectrophotometer as the slower reference
instrument and a colorimeter as the faster dense-capture instrument, while
keeping the artifact format compatible with ArgyllCMS:

```text
host_calibration_gui renders LED patches
ccxxmake drives the patch sequence through -C
spectrophotometer and/or colorimeter measure the same emitted patches
ccxxmake writes .ccmx or .ccss
future spotread captures use -X correction_file
builder/verifier consume corrected XYZxyY by default
```

---

## 1. Why this exists

RGBW and 5+ emitter wallwash systems are not normal LCD/OLED displays. The LED
package, wall, diffuser, distance, viewing geometry, and selected model family
all change the spectrum seen by the meter. A colorimeter can be fast and stable,
but without a matching correction it can report biased XYZxyY values.

That bias can look like a solver problem:

```text
primary xy appears shifted
white-diode xy appears shifted
yellow/orange residuals look worse than they are
WX / high-W regions are mis-ranked
multi-emitter inner-anchor choices are scored against the wrong measurement
```

The intended order is therefore:

```text
1. Correct / qualify the measurement instrument.
2. Capture LED response using the corrected measurement path.
3. Let the builder learn the actual LED + wall/diffuser behavior.
```

---

## 2. Argyll primitives used by this project

The first implementation should not invent a custom correction-file format.
ArgyllCMS already has the useful correction artifacts:

```text
.ccmx    Colorimeter Correction Matrix
.ccss    Colorimeter Calibration Spectral Sample
```

`ccxxmake` can create those artifacts, and `spotread` can apply them later:

```bash
spotread -x -O -X profiles/instrument_corrections/wallwash.ccmx
spotread -x -O -X profiles/instrument_corrections/wallwash.ccss
```

Important `ccxxmake` options for this project:

```text
-S
    Create CCSS instead of CCMX.

-d dummy
    Do not rely on an Argyll test window. This is useful when the actual patch
    display is handled by host_calibration_gui and the LED controller.

-C "command"
    Invoke command every time ccxxmake sets a color. Argyll appends six values:
    R8 G8 B8 Rf Gf Bf
    where R8/G8/B8 are 0..255 integers and Rf/Gf/Bf are 0.0..1.0 floats.

-s steps
    Select patch-combination density. Higher values produce more patches.

-o observer
    Select the observer used when deriving tristimulus values from spectra for
    CCMX spectral data.

-f ref.ti3[,targ.ti3]
    Future/offline path for creating corrections from pre-measured Argyll .ti3
    files instead of an interactive measurement run.
```

The `-C` path is the key to integrating `ccxxmake` with the LED calibration
stack. It lets Argyll own the patch sequence and instrument workflow while the
host GUI owns how that RGB patch is rendered on the LED system.

---

## 3. CCMX vs CCSS policy

Use both artifact types, but keep their purpose distinct.

```text
CCMX:
    Best default when the user has both the reference spectrophotometer and the
    target colorimeter available on the same LED/wall/diffuser setup. It is a
    matrix correction for a specific colorimeter + display/geometry pairing.

CCSS:
    Useful when the goal is to describe the display/emitter spectral sample for
    compatible colorimeters, or when sharing a correction family with another
    user/instrument. It is generated from spectrophotometer spectral readings.
```

Do not treat CCMX and CCSS as additive correction layers. They are alternate
ways to let the colorimeter reach the corrected measurement basis. The profile
should record which one is active.

---

## 4. Host GUI / ccxxmake integration model

`ccxxmake` should be able to drive the host GUI through a small relay command:

```text
ccxxmake
  -d dummy
  -C "python tools/ccxxmake_patch_relay.py --host 127.0.0.1 --port 19447 ..."
  output.ccmx
```

For every patch, Argyll appends six patch arguments to the command:

```text
R8 G8 B8 Rf Gf Bf
```

The relay command should:

```text
1. Parse the six appended arguments.
2. Send a render request to host_calibration_gui.
3. Wait for the GUI/device acknowledgment.
4. Wait the configured settle time.
5. Exit 0 when the LED patch is displayed and stable.
6. Exit non-zero if the GUI/device cannot display the patch.
```

The host GUI then renders the patch through the selected output path:

```text
rgb8:
    Use the 0..255 integer values directly.

rgb16:
    Use the float values and scale to 0..65535, preserving more precision than
    the integer-only path where possible.

TemporalBFI / True16 / arbitrary effective bit depth:
    Treat the floats as normalized source RGB and use the selected transfer,
    response provider, and temporal encoder to display the requested patch at
    the highest available precision.

channels16 / multi-emitter:
    Treat the floats as normalized source RGB, solve through the selected model
    or LUT, and render the resulting channel tuple.
```

The `-C` RGB8 integers are still important because they are Argyll's canonical
patch identity. The float values are the preferred source for high-bit-depth LED
rendering because they preserve normalized intent independent of the eventual
output depth.

---

## 5. Patch relay request schema

The relay should send a JSON request similar to:

```json
{
  "type": "ccxxmake_render_patch",
  "session_id": "wallwash_ccmx_2026_06_20",
  "patch_index": 12,
  "rgb8": [255, 128, 0],
  "rgb_float": [1.0, 0.5019608, 0.0],
  "render_mode": "temporal_bfi",
  "source_gamut": "rec709",
  "input_transfer": "linear",
  "output_family": "rgbw16",
  "settle_ms": 500,
  "black_insert_ms": 0
}
```

The GUI response should include enough data to audit what was actually shown:

```json
{
  "ok": true,
  "session_id": "wallwash_ccmx_2026_06_20",
  "patch_index": 12,
  "displayed_rgb8": [255, 128, 0],
  "displayed_rgb_float": [1.0, 0.5019608, 0.0],
  "rendered_tuple": [65535, 32768, 0, 0],
  "channel_order": ["R", "G", "B", "W"],
  "render_mode": "rgbw16",
  "device_ack": true,
  "settled": true
}
```

Every session should emit a sidecar patch log:

```text
CCXXPatchRecord:
    session_id
    patch_index
    argyll_rgb8
    argyll_rgb_float
    render_mode
    source_gamut
    input_transfer
    output_family
    output_bit_depth / effective_bit_depth
    solved_output_tuple
    channel_order
    settle_ms
    displayed_at
    ack_status
```

This patch log is not a replacement for the Argyll `.ccmx` / `.ccss`; it is the
repo-side audit trail that explains how the Argyll patch values were rendered on
the LED system.

---

## 6. Host GUI UI scope

Add a small `Spectro / CCXX` panel or dialog to `host_calibration_gui`.

Minimum controls:

```text
correction kind:
    CCMX | CCSS

output file:
    profiles/instrument_corrections/<name>.ccmx or .ccss

Argyll display mode:
    default to -d dummy

ccxxmake display technology:
    default to unknown / user-selected -t value

patch density:
    -s steps

observer:
    default 1931_2, optional advanced selection

render backend:
    rgb8 | rgb16 | temporal_bfi | channels16 | current LUT/model path

source gamut / transfer:
    rec709 linear by default unless the profile says otherwise

settle / black insertion:
    per-patch settle delay and optional black frame between patches

instrument descriptors:
    reference instrument, target colorimeter, serial ids where available

geometry descriptors:
    off-wall / diffuser / installed wallwash / bench distance / ambient notes
```

The UI should show the generated command before running it:

```bash
ccxxmake -v -d dummy -t u -s 3 \
  -C "python tools/ccxxmake_patch_relay.py --host 127.0.0.1 --port 19447 --session profiles/instrument_corrections/wallwash.session.json --render-mode temporal_bfi --settle-ms 500" \
  profiles/instrument_corrections/wallwash.ccmx
```

For CCSS:

```bash
ccxxmake -v -S -d dummy -t u -s 3 \
  -C "python tools/ccxxmake_patch_relay.py --host 127.0.0.1 --port 19447 --session profiles/instrument_corrections/wallwash.session.json --render-mode temporal_bfi --settle-ms 500" \
  profiles/instrument_corrections/wallwash.ccss
```

The GUI can either run `ccxxmake` itself or copy the command for manual terminal
execution. Running it inside the GUI is useful later, but copy/run is acceptable
for the first implementation as long as the relay server is reliable.

---

## 7. Correction artifact metadata

The project should wrap the Argyll artifact with metadata rather than replacing
it:

```text
ArgyllCorrectionProfile:
    correction_id
    correction_kind: ccmx | ccss
    argyll_ccxx_path
    argyll_command
    argyll_version when available
    generated_by: ccxxmake
    patch_relay_session_id
    reference_instrument_id
    target_instrument_id
    display_profile_id
    emitter_profile_id
    geometry_id
    render_mode
    source_gamut
    input_transfer
    output_family
    output_bit_depth / effective_bit_depth
    training_patch_set
    validation_patch_set
    holdout_error_stats
    spotread_correction_arg: -X path/to/file.ccmx_or_ccss
    raw_vs_corrected_policy
    created_at
```

Display profiles should reference the active correction:

```text
DisplayProfile:
    emitter_profile_id
    instrument_profile_id
    argyll_correction_profile_id
    geometry_id
    reference_white_xy
    measurement_units
```

---

## 8. Applying the correction later

Once a correction is accepted, normal capture paths should add `-X` to the
`spotread` command:

```bash
spotread -x -O -X profiles/instrument_corrections/wallwash.ccmx
```

Capture rows should preserve both raw and corrected fields when possible:

```text
X_raw, Y_raw, Z_raw, x_raw, y_raw
X_corr, Y_corr, Z_corr, x_corr, y_corr
instrument_id
instrument_correction_id
correction_applied: true | false
spotread_command
```

Corrected XYZxyY should be the default builder/verifier input when a valid
correction profile is attached. Raw-only capture should remain available for
bring-up, debugging, and correction validation.

---

## 9. Validation path

A correction should not become the default silently. Validate it first.

Recommended validation:

```text
1. Reserve a holdout patch set not used by ccxxmake.
2. Measure holdout patches without -X.
3. Measure the same patches with -X correction.
4. Compare expected vs measured residuals and raw-vs-corrected drift.
5. Save a validation report next to the correction profile.
```

Useful report fields:

```text
before/after mean dE
before/after median dE
P90 / P95 / max dE
neutral-axis residuals
primary residuals
W / CW / WW residuals
WX high-W residuals
low-Y stability
per-active-channel-family residuals
```

The report should be stored as JSON and optionally rendered as HTML.

---

## 10. Spectral and lighting-quality reports

The same spectrophotometer capture path should also support non-correction
reports when SPD data is available:

```text
per-emitter SPD records
CRI / CIE 13.3 summaries
ANSI/IES TM-30 summaries
TLCI / SSI diagnostics where useful
LM-79-style summary fields for SSL-style reports
```

These are profile diagnostics, not required LUT inputs. The normal LUT builder
still consumes corrected XYZxyY. SPD reports explain the emitter/wallwash system
and help diagnose why a correction or model family behaves the way it does.

---

## 11. Suggested files and ownership

```text
host_calibration_gui:
    CCXX panel, patch-relay server endpoint, render-mode selection,
    ccxxmake command preview, correction registration, validation UI

tools/ccxxmake_patch_relay.py:
    command invoked by ccxxmake -C; parses appended RGB args and relays patches
    to the host GUI

tools/make_argyll_ccxx.py:
    optional wrapper that launches ccxxmake with repo metadata paths

tools/validate_instrument_profile.py:
    holdout validation and raw-vs-corrected reporting

rgbw_lut_builder/profiling/argyll_ccxx.py:
    profile metadata schema, command builders, artifact registration

rgbw_lut_builder/profiling/paired_capture.py:
    session records, patch logs, validation set definitions

rgbw_lut_builder/captures/spotread_protocol.py:
    spotread command construction, -X injection, raw/corrected field mapping

rgbw_lut_builder/profiling/spectral_reports.py:
    SPD-driven report adapters and HTML/CSV/JSON output
```

---

## 12. Implementation phases

```text
Phase A - patch relay skeleton:
    implement tools/ccxxmake_patch_relay.py
    add host GUI endpoint for ccxxmake_render_patch
    log every rendered patch and output tuple

Phase B - manual ccxxmake workflow:
    GUI generates copy/paste ccxxmake command
    user runs ccxxmake externally
    generated .ccmx/.ccss is registered in profile metadata

Phase C - managed ccxxmake workflow:
    GUI launches ccxxmake, streams logs, handles aborts/timeouts,
    and stores patch relay session metadata automatically

Phase D - validation and spotread -X wiring:
    validate correction on holdout patches
    add -X to future spotread captures
    preserve raw/corrected capture fields

Phase E - spectral reporting:
    parse/store SPD captures where available
    generate CRI/TM-30/TLCI/SSI/summary reports
```

---

## 13. Guardrails

```text
Do not discard raw measurements.
Do not mix corrected and uncorrected captures in one feedback bucket.
Do not assume ccxxmake RGB8 arguments are the final LED output bit depth.
Do not make the spectrophotometer path mandatory for basic LUT bring-up.
Do not treat CCMX and CCSS as additive corrections.
Do not reuse a correction after geometry, wall/diffuser, LED package, or major
model/output path changes without revalidation.
```
