# Font fidelity from author PDFs

## Per-object native fonts

The existing runtime font profile remains the default. Add explicitly supplied
profiles in `fonts.additional:[{family,regular,bold,italic?,boldItalic?}, ...]`; every face uses its
absolute file path, SHA256 and registered `face_index`. A text object may set
`font_family` to one of those exact families. Family identity and weight are
verified from the file, and glyph coverage is checked only against the objects
that use that face. Unknown families fail before output, without system fallback.
The default family requires `regular` and `bold`; its regular face also renders
the comparison captions. An additional family needs at least one real face,
and only its explicitly used roles must exist. This permits a supplied PDF
subset with only a regular, bold or italic face. Comparison-caption characters
are checked against the default family, not against every subset. A subset
still rejects any label containing missing characters, and no missing style is
synthesized. Keep its character repertoire explicit in the delivery notes.
Register actual static `italic` and
`boldItalic` files whenever the scene requests those styles. The face-selection
table is exact:

| Text flags | Required profile role |
| --- | --- |
| neither | `regular` |
| `bold:true` | `bold` |
| `italic:true` | `italic` |
| both true | `boldItalic` |

A two-face profile still works for upright text. An italic request without the
matching real file fails before export; Canvas, SVG and PPT authoring must not
synthesize a slant or weight or silently use a system face. Each role is checked
against the binary's family names, OS/2 weight, slant flags/italic angle, hash,
collection face index and the glyphs used by that role's objects. A genuine
registered oblique face is classified as `oblique` in the audit. Audit entries
retain style and PostScript names, units/em, hhea/OS/2 ascent, descent and leading,
and checked object IDs. These font metrics are evidence, not automatic guarantees
of identical application rendering.

For example, within a runtime `fonts` record:

```json
{
  "family": "Times New Roman",
  "regular": {"path": "/caller/fonts/times-regular.ttf", "face_index": 0},
  "bold": {"path": "/caller/fonts/times-bold.ttf", "face_index": 0},
  "italic": {"path": "/caller/fonts/times-italic.ttf", "face_index": 0},
  "boldItalic": {"path": "/caller/fonts/times-bold-italic.ttf", "face_index": 0}
}
```

These are illustrative paths; supply caller-owned registered files. The runtime
profile resolver records SHA256 hashes and rejects a provided mismatch. TTC/OTC
members require their explicit registered `face_index` rather than choosing the
first member implicitly.

To use a job-specific profile without changing the installed default, set
`FIGURE_REBUILD_FONT_PROFILE=/absolute/path/profile.json` for that build.

Preserve a matched font's em and baseline as source measurements. Native text
baseline placement accounts for the font ascent and PPT leading; it does not
use the glyph ink ascent as the text-frame baseline. Check the actual reimport
preview because font hinting and application rendering can still differ.

## Explicit native text calibration

Text may set `line_height` (positive pixels), `baseline_offset` (nonnegative
pixels from the content frame's top to its first baseline), and
`insets:{left,right,top,bottom}` (nonnegative pixels, omitted sides default to
zero). These values are measured source/calibration inputs. Without them, the
source frame retains its registered-font default baseline and leading contract.
Wrapping and overflow checks use the frame after subtracting all insets.
For `anchor` text, the anchor is the first content baseline, including when
`rotation` is set; the text frame rotates around that fixed source point.
Asymmetric insets do not move that baseline or horizontal alignment. The build report retains
the content frame, insets, resolved baseline and whether leading/baseline came
from explicit measurements. Explicit leading is saved as native DrawingML point
spacing. The same audited font bytes are registered with the measurer and the
presentation renderer. The first-baseline correction uses the renderer's actual
metrics, its default/exact-spacing branch, paint adjustment and physical PPT
point rounding. It increases the native top inset and decreases the bottom
inset by the same amount, preserving content height, vertical alignment and
the rotation center. Derived native insets may be signed; source insets remain
nonnegative. Missing renderer metrics or inconsistent mapped scale fail clearly.
This renderer model is verified with Artifact Tool 2.8.59; other backend versions
need the actual rendering regression, not just API-name compatibility.
Verify actual exported PPT rendering before treating
the calibrated values as an accepted source match.

The opt-in regression covers 160 exported cases, including explicit/default
spacing, multiline text, placement scaling, rotation and vertical alignment:

```bash
FIGURE_REBUILD_RENDER_TESTS=1 python -m unittest discover -s tests -p test_text_baseline_render.py
```

Set `FIGURE_REBUILD_RENDER_EVIDENCE_ROOT` to a fresh directory to retain all
references, PPTs, previews and measurements. Default test discovery skips it.

## Read author metadata

### Prove the used SFNT glyphs

After independently binding a source PDF font program and its native glyph IDs,
compare its used glyphs with a supplied static Unicode face. The read-only
`figure_rebuild.source_font` module checks exact decomposed contours, horizontal
advance/side-bearing values and units per em. A matching family name is neither
required nor sufficient. Input font bytes are bound by SHA256; collections
require an explicit face index. `.notdef`, variable fonts, raw Type1/CFF,
ambiguous Unicode bindings and exceeded byte/contour/component budgets fail.
These budgets do not claim to bound all font parser memory.

The spec has `source` and `candidate` definitions containing absolute `path`,
`sha256` and optional `face_index`, and `bindings` containing unique pairs such
as `{"unicode":65,"source_gid":17}`. Source GIDs must come from independently
bound native paint records. The helper does not infer them from a font name,
nearby box or the candidate's cmap. Negative GIDs used for ligature components
need a separate source sequence/shaping proof and are not ordinary glyphs.

```sh
python -m figure_rebuild.source_font \
  --spec /caller/job/source-font-spec.json \
  --output /caller/job/source-font-proof-001.json
```

Exit status 0 means the recorded glyphs match, 1 means a supported comparison
differs, and 2 rejects an invalid input. Existing reports are never overwritten.
A PASS proves only the listed contours and horizontal metrics. It does not
prove whole-family identity, font style roles, hinting, ligature shaping,
baseline placement, automatic semantics or final PPT appearance. Keep the
source native context binding and actual exported-PPT review alongside it.

If a source subset is explicitly re-encoded under a job-local alias, preserve
the original program and derivation proof. Record its limited Unicode coverage
and retain the exact profile/fonts with the job. A correct local rendering
does not establish font embedding, extended editing repertoire or reopening
in PowerPoint/WPS without those dependencies.

Different jobs can carry different subsets under the same family and style
name. Installing those together can make an office application select another
job's subset and visibly substitute its missing characters. Keep font selection
isolated per job, or derive explicitly audited per-job family aliases. Preserve
prior glyph contours/metrics and permission bits when deriving an alias; a
new family name is not a new source-font identity proof. Verify the actual
application's selected program and painted glyphs, not only its reported names.
An existing cmap entry can also have an empty letter component intended for
GSUB ligature shaping. A nominal cmap/outline comparison may pass while an
application that does not use that ligature omits letters. Review complete
rendered labels and shaped pairs separately from nominal glyph coverage.

For explicitly declared donor components, bind the exact donor binary, its
copyright notice and its release license sidecar. Scope added metadata to each
face's imported characters; a notice from another font version is not evidence
for this donor. Preserve original records and permissions, and keep retained
source-subset rights separate from the new components' license. A metadata
correction should have a new frozen successor and unchanged glyph/metric tables.

### Embed the registered fonts in an existing figure

`embed-fonts` reads the final slide's explicit family/style runs and the build's
hash-bound `font-audit.json`. It embeds only used roles as uncompressed EOT
font parts, preserving the complete prepared SFNT bytes, Unicode repertoire,
permission bits and every slide/media/layout payload. It does not subset again
or require the source-font importer's 16-family single-build policy for an
already assembled presentation. The command requires a new output and receipt:

```sh
python -m figure_rebuild embed-fonts \
  --input /caller/job/build/run-001/validated-output/reconstruction.pptx \
  --font-audit /caller/job/build/run-001/font-audit.json \
  --output /caller/delivery/embedded-preview.pptx \
  --receipt /caller/delivery/embedded-preview.font-receipt.json
```

Restricted, bitmap-only, contradictory or unknown embedding rights, variable
fonts, signature/outline mismatch, changed font bytes, missing used glyphs,
inherited/theme text faces, fields and existing embeddings are refused.
Reachable slide layouts and masters are checked recursively: inherited live
text or fields require a separate policy and are refused, while empty figure
scaffolding is preserved. Valid family aliases for the same registered face
share one font part and content-type declaration, with separate family entries;
collisions with original package parts or declarations are refused.
`fsType=4` allows preview/print embedding and is recorded as lacking editing
permission. Such an embedded file may open read-only in an application; retain
a separately named native editing version and its original font dependencies.
Do not change rights bits to make the file appear editable. A complete registered
subset still provides only its existing repertoire, not the original family.

Independently parse each actual EOT header and compare the full recovered SFNT
payload and rights. Check presentation relationships, unique used families and
the `regular → bold → italic → boldItalic` schema order, plus unchanged slide
bytes. For a native portability check, use a fresh application profile and an
isolated font directory with only one unrelated bootstrap face: LibreOffice
cannot initialize with zero system fonts. Compare actual exported font names
with the name-ID-6 alternatives in the embedded bytes, requiring an observed
name for each used role; an audit's alternative names need not all occur.
PowerPoint/WPS reopening, editing and playback remain separate checks.
In the remaining-four audit, clean-font Linux WPS imports substituted fonts
despite the embedded EOT parts. That application still needs its own verified
font provisioning and actual save/reopen checks; a successful LibreOffice
import cannot establish WPS portability. Windows PowerPoint and macOS checks
remain pending.

The container format follows [Microsoft's PowerPoint font-part notes](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oe376/1663dabc-5d98-463f-889e-bcd9b77c3d34)
and the [EOT structure specification](https://www.w3.org/submissions/EOT/).
Implementation: `src/figure_rebuild/font_embedding.py`; regression controls:
`tests/test_font_embedding.py`.

For a separately audited raw-CFF wrapper, an OpenType `CFF ` font must use the
`OTTO` SFNT signature; a TrueType signature can make the native font loader
reject otherwise preserved contours. The used-SFNT helper rejects signature
and outline-table mismatches. Preserve original Type2 charstring bytes before
any lazy parser rewrites them, then compare the derived used contours and
advances independently. Names and a successfully loaded font are not identity
proofs. Raw CFF/Type1 conversion and source native handle binding remain outside
the used-SFNT helper's supported input contract.

Use the author's vector PDF when available. The normal text in a diagram can
have several real font families; source reconstruction must not silently apply
the slide template's default font. Font names, em size and baseline are separate
from visible ink bounds.

Install the optional source dependency with the same configured Python runtime:

```sh
python -m pip install -r requirements/source.txt
```

Run the read-only helper from any current directory:

```sh
python /path/to/figure-rebuild/scripts/analyze_pdf_fonts.py \
  --pdf /path/to/author-paper.pdf --page 4 \
  --region 157.5 72 454.524 195.366 \
  --font-registry /path/to/fonts/manifest.json \
  --scale 4 --offset -683 -240 \
  --output /path/to/job/diagnostics/font-analysis-001.json
```

`--page` is one-based. `--region` uses PDF points in the page coordinate system.
`--scale` and `--offset` must be explicitly supplied together. For a measured
affine mapping use `--matrix A B TX C D TY` instead. The helper never estimates
registration or writes a scene. Omit both transform options to report PDF
coordinates only. It refuses to overwrite an existing report.

The font registry has a `fonts` list with `id`, `path`, `family`, `style`,
`face_index` and optional `sha256`. Relative paths resolve against the registry
directory. TTC/OTC fonts require the registered face index. The helper verifies
the file hash when given, opens that exact face with fontTools, and matches
actual PostScript names or full names to the PDF. Similar fonts, family aliases,
and unverified entries never become a confident match. Exact verified names are
reported as `verified_match`; multiple candidates are `ambiguous` with their
stable registry IDs, and absent candidates remain `unresolved`. These are
metadata-match categories, not a guessed percentage confidence or image-glyph
recognition. Font binaries are supplied by the caller and are not
redistributed by this skill.

Each text span retains `pdf_fontname`, size, baseline origin, line direction,
font metric bbox, and its verified registry match. `source_*` values exist only
when a transform was explicitly supplied. A metric bbox can extend above or
below actual glyph ink; place live text from its baseline and em, and then check
the final export visually. Do not treat the metric bbox as a tight image crop.

Some mathematical labels are already converted to vector outlines in the PDF.
They are absent from text spans; a page's font-resource list does not establish
which font produced those paths. The report gives drawing counts and explicitly
leaves outline fonts unknown. Human shape comparisons may support an inferred
family, but that inference must be labeled and recorded separately. When the
user requires retypeset formulas, transcribe and verify the expression, render
it with an actual TeX engine, retain its source and vector PDF, and insert a
transparent high-resolution asset. Do not use source-text screenshots as the
formula asset.

For manually transcribed ordinary labels with no source text layer or font
programme, an explicit source-specific driver may derive used Unicode glyphs
from the original visible paths. Bind the complete native paint order, original
contours, fill rule, colors and clip containment before partitioning. Similar
font outlines may suggest a partition, but their diagnostic matching tolerance
is separate from the fixed source-control guard. Preserve every source glyph
contour and independently compare all occurrences to the actual saved font.
Record manual Unicode, spaces, synthetic metrics and limited glyph coverage;
this is neither original-font identity nor automatic text/formula recognition.

Use shared glyphs only after verifying their control points and physical line
origins. Keep measured text frames inside the source canvas and retain the
authoring runtime's font-family limit. The renderer's `Mg` metric probe also
needs explicit glyph coverage: any supplemental metric-only glyphs must be
identified, and actual current labels must be independently proven not to use
them. Font delivery and editing with unseen characters remain separate checks.

Inspect the actual application's exported font programme as well as the input
font. In the audited [LibreOffice 26.2.5.2 CFF converter](https://github.com/LibreOffice/core/blob/libreoffice-26.2.5.2/vcl/source/fontsubset/cff.cxx#L1300-L1317),
the optional final-axis operand of compact `hvcurveto`/`vhcurveto` becomes an
integer. An explicitly derived font can retain the same target controls while
encoding full move/line/cubic operators. Verify saved input contours, actual
subset operands and advances, actual native font handles, and matched-scale
layout again; do not widen a source-fidelity tolerance to accept truncation.

Raw exported Type1 hashes can also change with the exporter's `UniqueID`. Keep
both actual programmes and the observed values. Before treating this as a
metadata change, compare every other font/private dictionary field, encoding,
all glyph and subroutine operands, and the used native-handle controls and
advances. Matching names or a removed `UniqueID` alone is insufficient. Such a
comparison preserves a verified parent result; it does not establish identity
with the original source font programme.
