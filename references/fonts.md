# Font fidelity from author PDFs

## Per-object native fonts

The existing runtime font profile remains the default. Add explicitly supplied
profiles in `fonts.additional:[{family,regular,bold,italic?,boldItalic?}, ...]`; every face uses its
absolute file path, SHA256 and registered `face_index`. A text object may set
`font_family` to one of those exact families. Family identity and weight are
verified from the file, and glyph coverage is checked only against the objects
that use that face. Unknown families fail before output, without system fallback.
`regular` and `bold` remain required. Register actual static `italic` and
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
For `anchor` text, the anchor is the first content baseline; asymmetric insets
do not move that baseline or horizontal alignment. The build report retains
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
