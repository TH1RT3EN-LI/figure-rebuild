# Verify source fidelity from the command line

```sh
figure-rebuild verify-source-fidelity \
  --source-descriptor /data/source-descriptor.json \
  --manifest /job/manifest.json \
  --resolved-scene /job/resolved-scene.json \
  --asset-root /job/assets \
  --pptx /job/actual.pptx \
  --evidence-dir /job/new-source-fidelity-evidence
```

The descriptor is a UTF-8 JSON object:

```json
{
  "pdf_path": "source.pdf",
  "pdf_sha256": "<64 lowercase hexadecimal digits>",
  "page": 3,
  "region": [46, 70, 300, 239],
  "scale": 2.0
}
```

`page` is one-based; `region` is `[x0,y0,x1,y1]` in PDF points. `scale` is
uniform PDF-to-canvas scale and defaults to 2. Optional `reference_png_path`
and `reference_png_sha256` must be supplied together; a fresh pristine render
must match those reference bytes. Source-region selection remains a caller
declaration, not proof of the intended figure.

All command-line filesystem paths resolve from the current working directory.
The PDF and optional reference paths inside the descriptor resolve from the
descriptor file's resolved parent directory. Absolute paths remain absolute;
`~` is expanded. The asset root is passed explicitly and is not inferred from
the manifest. Asset-relative paths remain subject to the API's confinement rules.

Optional `--policy /path/policy.json` and `--limits /path/limits.json` accept
partial JSON objects with exactly the field names of `SourceReplayPolicy`
and `ReplayLimits`; omitted fields retain their documented API defaults.
For example, `{"max_clip_overhang":0}` selects zero overhang tolerance and
`{"timeout_seconds":60,"max_commands":100000}` tightens two resource limits.
Every supplied value is checked by the typed API. Unknown profiles or explicit
transformations without implemented replay proof remain UNRESOLVED.

Descriptor, policy and limits files are each limited to 1 MiB. Unknown or
duplicate fields, malformed/non-object JSON, non-finite numbers, integers too
large for the finite numeric range, invalid types and empty paths are rejected.
Boolean values are not accepted as numeric arguments. This strict request-file
parser does not claim to validate arbitrary JSON content inside submitted scenes.

Exit status **0** means only **VERIFIED_IN_DECLARED_SCOPE**. FAIL, UNRESOLVED,
NOT_PROVIDED or an unknown status return **1**; invalid command syntax returns
argparse's **2**. Validation/I/O errors return nonzero with a stderr message.
Successful API return values, including non-verified reports, are printed as JSON
to stdout and preserved at `evidence-dir/source-fidelity.json`. The evidence
directory must not already exist and is never overwritten. Report-write failure
cannot produce a successful exit or a printed VERIFIED report.

The command replays the original PDF and checks the submitted manifest, resolved
scene and actual PPTX. It neither edits those artifacts nor changes output-review,
semantic, visual or user-acceptance records. `semantic_recognition` remains
NOT_PROVIDED, `visual_acceptance` NOT_EVALUATED, and user acceptance pending.
Missing optional source dependencies produce UNRESOLVED; the command does not
install them. See [the API scope and budgets](source-fidelity.md) for the limited
supported source/OOXML profile and the evidence that must accompany a report.
