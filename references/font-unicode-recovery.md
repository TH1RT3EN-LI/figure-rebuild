# Exact candidate Unicode recovery

When a source TrueType subset has no useful Unicode cmap, its PDF ToUnicode may
describe the visible glyph incorrectly. The explicit `font_unicode` helper
searches a hash-bound candidate face for each independently bound source GID.
It accepts a proposal only when exactly one candidate glyph and one Unicode
match the complete decomposed pen operations, units per em, horizontal advance
and side bearing. It uses no coordinate tolerance, source ToUnicode, expected
label string, font-family name or nearest bounding box.

```python
from figure_rebuild.font_unicode import recover_glyph_unicode, verify_glyph_unicode

receipt = recover_glyph_unicode(source_definition, candidate_definition, source_gids)
verify_glyph_unicode(source_definition, candidate_definition, source_gids, receipt)
```

Each definition contains an absolute `path` and actual `sha256`. A TTC collection
requires an explicit `face_index`. `source_gids` is a nonempty list of unique
positive integers from an independently established actual source font handle.
The helper itself does not establish the PDF handle/program/GID binding.

Candidate Unicode cmaps supply direct aliases. For a MATH face, the helper can
also follow the candidate's `ssty` single or alternate substitutions, including
one GSUB extension wrapper. [OpenType's `ssty` definition](https://learn.microsoft.com/en-us/typography/opentype/spec/features_pt#tag-ssty)
assigns these variants to first- and second-level math scripts; actual scale and
position belong to the layout engine. The [GSUB extension specification](https://learn.microsoft.com/en-us/typography/opentype/spec/gsub#lookup-type-7-subtable-substitution-subtable-extension)
requires a non-extension effective lookup type. The helper rejects context
flags, unsupported lookup types, nested or inconsistent extensions and script
aliases without MATH. It does not follow arbitrary stylistic or ligature rules.
`include_script_alternates=False` restricts recovery to direct Unicode cmaps.

Ambiguous glyphs, multiple Unicode aliases and missing shapes remain
`UNRESOLVED`; the overall result is `REVIEW`. The complete candidate repertoire
is searched, so an unencoded duplicate contour also prevents choosing a match.
The full typed receipt is independently recomputed: extra claims, altered
aliases, budgets, numbers, script levels or identities fail verification.

Defaults bound each font to 32 MiB, source selection to 8192 GIDs, candidate
repertoire to 16384 glyphs, emitted work to 500000 operations and 2000000 control
points, component depth to 32 and alias processing to 131072 records. These
bounds are separate from existing used-glyph comparison and PDF source-control
policies. They do not bound all font-parser memory. Only static TrueType `glyf`
faces are supported; CFF, variable faces and `.notdef` cannot establish proposals.

The standalone command reads a closed input schema and writes a new receipt:

```bash
python -m figure_rebuild.font_unicode --spec glyphs.json --output unicode-receipt.json
```

The JSON spec contains `source`, `candidate`, `source_gids` and optionally
`include_script_alternates`. Duplicate JSON keys and existing output paths are
rejected. Exit status is nonzero for unresolved proposals.

Recovery is conditional on the supplied candidate. It establishes neither
original font identity nor hinting, full-family equality, arbitrary shaping,
formula AST, subscript/superscript placement, diagram relations, native output
appearance, pixel equivalence or user acceptance. Keep the actual source and
candidate bytes, native font-handle proof, all unresolved cases and separate
layout/visual review before selecting a reconstruction.
