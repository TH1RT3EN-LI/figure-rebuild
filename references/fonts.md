# Font fidelity from author PDFs

## Per-object native fonts

The existing runtime font profile remains the default. Add explicitly supplied
profiles in `fonts.additional:[{family,regular,bold}, ...]`; every face uses its
absolute file path, SHA256 and registered `face_index`. A text object may set
`font_family` to one of those exact families. Family identity and weight are
verified from the file, and glyph coverage is checked only against the objects
that use that face. Unknown families fail before output, without system fallback.
To use a job-specific profile without changing the installed default, set
`FIGURE_REBUILD_FONT_PROFILE=/absolute/path/profile.json` for that build.

Preserve a matched font's em and baseline as source measurements. Native text
baseline placement accounts for the font ascent and PPT leading; it does not
use the glyph ink ascent as the text-frame baseline. Check the actual reimport
preview because font hinting and application rendering can still differ.

## Read author metadata

Use the author's vector PDF when available. The normal text in a diagram can
have several real font families; source reconstruction must not silently apply
the slide template's default font. Font names, em size and baseline are separate
from visible ink bounds.

Install the optional source dependency with the same configured Python runtime:

```sh
python -m pip install -r requirements-source.txt
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
and unverified entries never become a confident match. Missing or ambiguous
matches remain explicit. Font binaries are supplied by the caller and are not
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
