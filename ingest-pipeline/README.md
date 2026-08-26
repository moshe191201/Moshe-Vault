# ingest-pipeline

A four-stage Hebrew document pipeline. One of the two products in this monorepo; the other
is `second_brain_vault_framework` at the repo root.

**Windows only.** **Not pure stdlib.** Runs inside an air gap on a ~3,800-page Hebrew
Confluence corpus.

```
raw/ --[3] convert--> raw_md/ --[4] terms--> glossary --[5] translate--> English --[6] classify--> domains
```

| Stage | Entry point | Purpose |
|---|---|---|
| 3 | `scripts/convert/convert_to_md.py` | PDF/DOCX/PPTX/HTML/email/VSDX/OneNote → markdown, Hebrew OCR-reversal fix, dedup |
| 4 | `scripts/extract_domain_terms.py` | deterministic domain-term extraction (wordfreq ratios + YAP roots) |
| 5 | `scripts/translate/translate.py` | Hebrew→English with deterministic glossary masking + chunk checkpointing |
| 6 | `scripts/classify/` | subdomain classification against a frozen taxonomy |

Run conversion as `python -m scripts.convert.convert_to_md` or `python scripts/convert/convert_to_md.py` (package mode), and translation as `python -m scripts.translate.translate` or `python scripts/translate/translate.py`.

## The rule that matters

**This directory never imports `second_brain_vault_framework`.**

The framework is pure stdlib, cross-platform, and ships a payload into user-owned vaults.
This pipeline is none of those things. The framework's `manifest.json` contains no path from
here, and a vault owner never sees any of it. Integration will be designed later; until then
the boundary is one-way and absolute.

`scripts/classify/validate.py` was `core.cmd_classify` in the framework until that cut — it
carries its own copy of `parse_frontmatter` rather than importing one.

## Running the tests

```bash
pip install -r requirements.txt
```

```bash
cd ingest-pipeline && python -m unittest discover -s tests
```

Expect `OK (skipped=2)` — the two skips are YAP-dependent and skip cleanly when `YAP_DIR` is
unset. Without the requirements installed, six modules fail to *import* and 67 tests never
run while the summary still reports a plausible-looking count; see `HANDOFF.md` §6.

A line reading `FAILED: 1 docs still invalid after 3 fix rounds` is stderr from a **passing**
test that deliberately drives a document to `qa_failed`. It is not a failure.

## Layout

```
scripts/
  convert/       stage 3 — document conversion to markdown (docling, pandoc, hebrew_fix, vsdx, OneNote)
  translate/     stage 5 — Hebrew→English translation (masking, chunking, QA, checkpointing, LLM)
  classify/      stage 6 — subdomain classification against a frozen taxonomy
  extract_domain_terms.py  stage 4
  hebrew_yap_stemmer.py    shared YAP helper
  hot_words.py / review_queue.py  shared tooling
tests/           the suite + fixtures
templates/
  planning/      campaign questionnaire (assess → filtering → … → success criteria)
  classification/  taxonomy, glossary, policy, Label Studio view
skills/          vault-classify (not shipped in the framework payload)
data/            glossary, curated person names, translation policy + prompt
requirements.txt single dependency surface for the whole pipeline — build the air-gap bundle from this, on Windows
```

State, open work, and gotchas: `../HANDOFF.md`.
