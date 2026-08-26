# Dual Classification — Subdomain + Document Type (Design)

**Date:** 2026-08-18
**Status:** Draft (grill-me locked, 2026-08-18)
**Parent:** `2026-08-15-generic-subdomain-classification-design.md` (Stage 6), `2026-08-02-client-ingest-pipeline-design.md` (backbone)
**Scope:** Add document-type classification as a second pipeline alongside subdomain classification. Both run after Stage 5 translation on the post-translation set (translated + English-bypass), share structure but diverge on metadata, constraints, and vocabulary ownership. Closes the deferred item: "pipeline only has subdomain classification."

## Purpose

Assign every document that survived filtering and translation **two orthogonal labels**:

- **Subdomain** — topical *what is this about* (existing, per-campaign, `taxonomy.yaml`, N=5-50).
- **Document type** — structural *what kind of artifact is this* (new, largely shared across vaults, ~13 types, single-label).

Document type decides *how a document is read* — what assumptions to make, how literally to take it, how much of it matters, and whether it is durable knowledge or ephemeral coordination. It also depends on file-level metadata (original extension, original language, filename) that subdomain ignores.

Every decision is recorded with its evidence so both classifiers can be analyzed, calibrated, and re-run.

## Non-goals

- No translation. Consumes Stage 5 English md + sidecar metadata; does not re-translate.
- No corpus-wide filtering. Doc-type never rejects for "belongs at all" — only classifies. Ephemeral types (`logistics`, `announcement`) may be deprioritized at ingest but not dropped in Stage 6.
- No new vault three-layer. Reuses `raw/`→`raw_md/`→store, `wiki/`, ledger, `.qmd`.
- No model hosting. Same OpenAI-compatible endpoint as subdomain (`CLASSIFY_LLM_BASE_URL` / `QMD_OPENAI_*`).

## Vocabulary

### Document types — default list (ships, editable)

Single-label. List is a **framework-owned default** templated from `payload/templates/classification/doc_types.yaml` into vaults via `manifest.json`. Vault owners are expected to edit it; questionnaire and docs explicitly encourage this. Pipeline reads the vault's copy (or `campaigns/<campaign>/doc_types.yaml` campaign overlay when multi-campaign).

| Type | Reading posture | Typical source | Typical language | Ephemeral | Notes |
|------|----------------|----------------|------------------|-----------|-------|
| `domain_intro_presentation` | Introductory deck, high-level framing | internal | he | false | `.pptx`/`.pdf` heavy |
| `spec_standard` | Normative spec/standard, read literally | external/vendor | en | false | external authority |
| `official_research_summary` | Synthesized research, rigorous but interpretive | internal | he | false | |
| `logistics` | Coordination, scheduling | internal | he | **true** | low ingest priority |
| `announcement` | Broadcast notice (subtypes: general / policy / event) | internal | he | **true** | may split into 2-3 subtypes per vault |
| `anomaly_report` | Structured anomaly snapshot (often xlsx) | internal | he | false | `allowed_languages: [he]`, `allowed_extensions: [xlsx,xls,csv]` |
| `anomaly_drill_down` | Deep-dive on an anomaly | internal | he | false | `allowed_languages: [he]` |
| `trend_analysis` | Analytical trend / longitudinal review | internal | he | false | `allowed_languages: [he]` |
| `meeting_minutes` | Minutes; only decisions + action items are durable | internal | he | false | Part E3: partial-doc matters |
| `intermediate_results` | Mid-stream results, provisional | internal | he | false | |
| `official_internal_update` | Authoritative internal memo/update | internal | he | false | |
| `task_list` | Checklist / task inventory | internal | he | **true** | structure over prose |
| `onboarding_q_with_answers` | Training Q with approved A | internal | he | false | durable as canonical answer |
| `onboarding_q_without_answers` | Training Q without A (exercise/assessment) | internal | he | false | prompt, not claim |

Internal ↔ Hebrew / external ↔ English is a **default routing hint**, not a hard guarantee. Each type carries:

```yaml
onboarding_q_with_answers:
  definition: "Training/onboarding Q&A where both question and approved answer are present."
  include: ["Q: ... A: ... paired", "approved answer"]
  exclude: ["Q without answer", "free-form exposition without Q"]
  reading_rule: "Read A: as durable canonical answer."
  ephemeral: false
  typical_languages: [he]
  typical_sources: [internal]
  allowed_original_languages: []   # empty = no hard gate
  allowed_extensions: []           # empty = no hard gate
  examples:
    - text: "Q: How to ...? A: ..."
      source: "doc_0912.md"
      covers: "explicit Q+A"
```

Hard gates (`allowed_original_languages`, `allowed_extensions`) prune the candidate enum before retrieval. Soft hints (`typical_languages`, `typical_sources`, `ephemeral`, `reading_rule`, `ingest_priority`) are injected into the prompt and available for downstream routing, but do not prune.

`announcement` may be materialized as `announcement_general`, `announcement_policy`, `announcement_event` if the vault needs subtypes — questionnaire decides.

Questionnaire Part E (E1/E2/E3) is expanded to cover doc-types with same rigor as subdomains: enumeration, overlap map (`meeting_minutes` vs `task_list`, `anomaly_report` vs `trend_analysis`), durable-vs-ephemeral, partial-doc rules, and diverse examples per type.

### Subdomain vocabulary

Unchanged: `taxonomy.yaml` per-campaign, `glossary.yaml`, include/exclude, glossary_keys, 3-8 diverse examples per subdomain.

## Decision model

Same categorical buckets as subdomain, enforced via `guided_json` enum — numeric scores impossible.

| Bucket | When either judge emits it | Human effect |
|--------|---------------------------|--------------|
| `SURE` | Signal explicit + primary focus, metadata consistent, no close runner-up | Auto-accept; spot-check audit |
| `NEEDS_HUMAN_VALIDATION` | Ambiguous type, close runner-up (gap < threshold), or implicit cue | Review queue (stratified) |
| `I_GUESSED` | Thin/generic evidence, least-bad pick | Quarantine; never auto-accept; taxonomy fix signal |

Doc-type is single-label — output is `doc_type` (singular), no `secondary_*` or `relation_type`.

Singleton case: if language/extension pruning leaves exactly one candidate, auto-assign `SURE` with `reason: singleton_constraint`, log `singleton_pruned` warning, skip LLM call. Few-shot examples are drawn **dynamically from the pruned candidate set**, not the full 13.

## Order of operations (per campaign)

1. **Questionnaire → freeze.** Expert fills expanded `questionnaire.md` (subdomain + doc-type Parts B/E); `taxonomy.yaml` + `doc_types.yaml` + `glossary.yaml` + `policy.yaml` freeze. Freeze hashes into ledger; change invalidates downstream decisions.
2. **Chunk (once).** Single `first_window 1500` + header outline (same as subdomain). Writes content-addressed `store/ab/abcdef….md` with frontmatter `source_doc_id, source_hash, chunk_policy_version, source_metadata (filename, ext, original_language)`. Doc-type does not need a second window — 1.5k is sufficient; metadata + header outline are prepended in the prompt instead.
3. **Embed once per task.** Batch `/v1/embeddings`. Two centroid sets: per-subdomain (from `taxonomy.yaml` examples) and per-doc-type (from `doc_types.yaml` examples, mean embedding per type).
4. **Top-k retrieval (per task).** `policy.retrieval.top_k` (default 4, clamped 1-10) cosine candidates against centroids. Doc-type retrieval runs **after pruning** by `allowed_original_languages` / `allowed_extensions` using sidecar `source_metadata.json` — pruned types never enter retrieval.
5. **Judge (per task, always).** Closed-choice on pruned top-k definitions + dynamically chosen few-shots (only from pruned candidates) + chunk text + headers + metadata line (`Source metadata: filename=... ext=xlsx lang=he`). `reasoning_brief` first, then `doc_type` / `primary_subdomain`, then `confidence_bucket`. Temp 0, `guided_json` schema.
6. **Label Studio export (per task, separate).** Two projects/views: `subdomain` and `doctype`. Doctype view renders `<HyperText>` md preview, confidence-colored box, reasoning_brief, `Source metadata:` line, `pruned_candidates` note, and `<Choices>` for doc-types. Third small `singleton_audit` view for singleton-auto-assigned docs (not in main queue). Review queue: stratified `NEEDS_HUMAN_VALIDATION` (~8 per doc-type) + `I_GUESSED` + disagreement-band close-calls + ~20 `SURE` spot-checks. Typical doctype queue ~108.
7. **Calibration (per task).** Per-bucket accuracy, confusion matrix (e.g., `task_list`↔`meeting_minutes`, `anomaly_report`↔`trend_analysis`), `constraint_miss` rate (pruned type overridden by reviewer), glossary/type-miss report. Patch → version bump → re-run exactly affected docs (ledger query: "which docs used doc_types v3?").
8. **Ledger + frontmatter patch.** Append-only JSONL; derived frontmatter `doc_type: <single>` (singular) and `domains: [...]` on store copies (atomic temp→rename → ledger). Current state is projection over the log.

Subdomain and doc-type classify in **parallel after translation** — same chunked English md as input. The post-translation set is `translated_he→en` ∪ `already_en` (English-bypass docs that skipped translation still get chunked and classified).

## Config contracts

### `payload/templates/classification/doc_types.yaml` (new, framework-owned)

Global default ~13 types as above. Each type:

```yaml
version: 1
doc_types:
  spec_standard:
    definition: "..."
    include: ["..."]
    exclude: ["..."]
    reading_rule: "Read literally as normative spec."
    ephemeral: false
    typical_languages: [en]
    typical_sources: [external]
    allowed_original_languages: []   # hard gate, empty = any
    allowed_extensions: []           # hard gate, empty = any
    ingest_priority: normal          # high | normal | low
    examples:
      - {text: "...", source: "doc_0123.md", covers: "short spec, explicit"}
```

`allowed_original_languages` / `allowed_extensions` are closed vocabularies — at classify time `effective_candidates = all_doc_types ∩ language_allowed ∩ extension_allowed`. `typical_*` / `ephemeral` / `ingest_priority` are soft, prompt-visible, not gating.

`logistics` / `announcement` / `task_list` default `ephemeral: true, ingest_priority: low`.

### `campaigns/<campaign>/doc_types.yaml` (overlay, per-campaign when needed)

Adds/hides types, adds constraints. Merge: `effective = (global ∪ additions) − hidden`. Cannot redefine a global type's definition — only add/hide/constrain. Campaign overlay is optional; single-vault deploys edit the vault's copy directly.

### `policy.yaml` (extended)

```yaml
version: 1
chunk: {mode: first_window, window: 1500, header_outline: true}
retrieval: {top_k: 4, embed_model: "embeddinggemma-300M"}
judge: {model: "minimax-m2.7", temperature: 0.0, few_shot_per_topk: 3, gap_threshold: 0.08}
confidence: {buckets: [SURE, NEEDS_HUMAN_VALIDATION, I_GUESSED]}
relation: {mode: primary_plus_secondary, allowed: [none, comparison, relationship, progression]}
review: {stratified_per_subdomain: 8, stratified_per_doctype: 8, disagreement_band: 0.10, spot_check_sure: 20, singleton_audit: 10}
routing_defaults:
  internal_hebrew: {languages: [he], sources: [internal]}
  external_english: {languages: [en], sources: [external]}
```

`gap_threshold` drives `NEEDS_HUMAN_VALIDATION` vs `SURE`; reused per-task.

### `questionnaire.md` (expanded)

Part E now walks the user through: doc-type enumeration (start from 13 defaults — hide/add), per-type include/exclude, overlap ownership, durable-vs-ephemeral (E2), partial-doc rules (E3), language/source defaults, implicit-type harvesting for structural cues, example diversity (short/long, lexically thin/rich, extension signal, onboarding Q±A). Freeze checklist gates both taxonomies.

## Module layout

```
payload/templates/classification/
  taxonomy.yaml            # existing (subdomain, toy 5)
  doc_types.yaml           # NEW: 13-type global default
  glossary.yaml
  policy.yaml              # extended per above
  questionnaire.md         # expanded Part E
  label_studio/
    view_subdomain.xml
    view_doctype.xml       # NEW
    view_singleton_audit.xml  # NEW

ingest-pipeline/scripts/classify/
  classify_common.py       # NEW: shared lib (embed, centroid, chunk helpers, ledger, calibrate/export helpers)
  classify_subdomain.py    # thin wrapper — taxonomy, glossary, prompt template A
  classify_doctype.py      # thin wrapper — doc_types, constraints, prompt template B (+ metadata line)
  chunk.py                 # unchanged (single store)
  taxonomy.py              # + doc-type parser
  validate.py              # + doc_type closed-vocab check
  ledger.py                # + task field, singleton_pruned event
  export_label_studio.py   # + doctype + singleton views
  calibrate.py             # + per-doctype metrics
```

`classify_common.py` is the shared library; CLIs are separate (no `--task` flag). Wrappers differ only in YAML source, candidate pruning, and system-prompt template.

`manifest.json` adds `payload/templates/classification/doc_types.yaml` (and updated `questionnaire.md`/`policy.yaml`) to `owned_paths`; `vault upgrade` lays them into vaults.

## Prompt contracts

**Subdomain (unchanged):** System = role + glossary `domain_knowledge` + top-k definitions + diverse examples + bucket rubric. User = chunk + header outline + title.

**Doc-type (new):** System = role + "classify document type (single label)" + allowed doc-type definitions (only pruned candidates) + their dynamically chosen few-shots + bucket rubric (explicit structural signal + metadata consistency anchors, annotated `singleton_constraint` example, `onboarding Q±A` pair example). Prepended:

```
Source metadata: filename=Q3_anomaly_drilldown.xlsx ext=xlsx original_language=he
Typical mapping: internal→he, external→en (defaults, editable)
Headers outline:
# Q3 review
## Anomalies
## Drill-down
```

User = chunk text + title (same 1.5k). Output:

```json
{
  "reasoning_brief": "2-3 sentences, evidence → doc_type via metadata/headers/content, no length limit",
  "doc_type": "anomaly_report",
  "confidence_bucket": "SURE"
}
```

Schema enforces `doc_type ∈ effective_candidates` (pruned). Unparseable → `I_GUESSED`.

Few-shot selection: sample 1-3 examples per pruned candidate, preferring `covers:` diversity (short/long, thin/rich, extension signal).

## Ledger & storage

- **Store:** Single content-addressed `store/ab/abcdef….md`; frontmatter `doc_type: <single>` (doctype) and `domains: [...]` (subdomain) are patched independently, both atomic temp→rename → ledger.
- **Events:**
  - `doc_types_frozen`, `taxonomy_frozen`, `glossary_version`
  - `chunk_written`, `embed_batch` (per task, with task + model id)
  - `retrieval_topk` (per task, with pruned list)
  - `singleton_pruned` (warning, with `original_lang, ext, pruned_to`)
  - `judge_completed` (per task, with `task, doc_type|primary_subdomain, confidence_bucket, reasoning_brief, candidates_pruned, model id, taxonomy/doc_types versions`)
  - `review_decision` (per task)
  - `frontmatter_patched` (per task)
- **Projection:** Current state per doc is ledger projection per task; version bump re-runs exactly affected docs.

## Human review (sample-efficient)

- Queue `doctype` = stratified `NEEDS_HUMAN_VALIDATION` (~8 per type) + `I_GUESSED` all + disagreement-band close calls + `I_GUESSED` + `SURE` spot-check 20.
- Queue `subdomain` unchanged.
- Queue `singleton_audit` = 10 sampled singleton-auto-assigned docs per batch, separate view, for catching bad constraints without queue flood.
- Batches ordered by bucket uncertainty — least-confident first.
- Disposition: accept, override `doc_type`, or flag `doc_types.yaml` patch (definition/constraint/example). Patch → version bump → re-run affected slice.

## Calibration

Per-task:
- Per-bucket accuracy, confusion matrix (doc-type: pairwise errors like `task_list`↔`meeting_minutes`, `anomaly_report`↔`trend_analysis`, `onboarding_q_with_answers`↔`onboarding_q_without_answers`).
- `constraint_miss` rate: reviewer overrides a pruned singleton or pruned-out candidate.
- `I_GUESSED` correct <40% is healthy (genuinely hard).
Phase 0 gold sample: ~100 docs stratified across doc-types + hard types (onboarding Q±A pair, xlsx-shaped vs text-shaped same content) + language/extension edge cases. Agreement sets `SURE` trust and spot-check size.

## Testing

- **Vocab sync:** `doc_types.yaml` types == judge schema enum (pruned); `allowed_*` values valid; `ephemeral`/`typical_*` present; questionnaire→YAML coverage. Fails build on drift.
- **Constraints:** language/extension pruning, singleton auto-assign + warning, dynamic few-shot pruned-set.
- **Chunk:** single store consumed by both tasks; metadata line correctness; atomic write.
- **Retrieval stubs:** stub `/v1/embeddings`, toy 3-type + 5-subdomain taxonomies — top-k recall, clamping, empty-embedding fail-closed, pruned candidates not retrieved.
- **Judge stubs:** schema ordering (reasoning_brief first), enum enforcement, `onboarding Q±A` distinction, singleton fallback.
- **Label Studio:** views render for N=13 and N=20, task-specific metadata/pruned note, HTML preview.
- **Ledger:** append/projection idempotency per task, re-run after version bump touches exactly correct docs, singleton events auditable.
- **Gold regression:** standing gold sample vs both judges — per-bucket accuracy floor, false-SURE ceiling.
- **Determinism:** Same chunk + taxonomy/doc_types versions + model id + metadata → same projection.
- **Internal/external defaults:** doc asserts `internal` types default `he` and `spec_standard` defaults `en` are present and editable.

## Success criteria (pilot)

- Same campaign with toy N=5 subdomain + 13 doc-types completes `chunk → retrieve (×2, parallel) → judge (×2, parallel) → export (×2+audit) → calibrate (×2)` end-to-end with stubbed embeddings/LLM.
- `doc_types.yaml` edit without code change (add type, hide type, add constraint) works by swapping YAML.
- Label Studio imports succeed for doctype + singleton_audit; per-task calibration reports are producible.
- Ledger answers "what decided doc X's doc_type, with which doc_types version/model, why (reasoning_brief), and was it singleton-pruned" for every doc.
- Re-run after doc_types patch re-processes exactly affected docs, atomically.
- `vault check example_vault` still passes; `vault upgrade example_vault` layers new `doc_types.yaml` with drift backup.
- English-bypass docs (no translation) are classified for both tasks.

## Open inputs

- `announcement` subtypes: keep single `announcement` vs split into `announcement_general|policy|event` — questionnaire decides per vault, default single.
- `top_k` / `gap_threshold` per task: spec ships shared `top_k: 4, gap_threshold: 0.08`; per-task overrides in `policy.yaml` are allowed but not required for pilot.
- `onboarding_q_*` reading rules for downstream ingest (how to split Q and A, grading semantics) — deferred to ingest curriculum stage.

## Decisions locked (grill-me 2026-08-18)

- Doc-type is **single-label**, **13-type shared default** (editable, framework-owned), covering onboarding Q±A and internal( he)/external( en) split.
- **Language + extension constraints** are hard gates in `doc_types.yaml`; `typical_*`/`ephemeral`/`priority` are soft editable defaults.
- **Singleton-pruned** auto-assigns `SURE` with warning, skipping LLM; dynamic few-shot from pruned set.
- **Same retrieve→judge pipeline** for both tasks (always LLM, not embedding-only).
- **After translation, parallel**, but includes **English-bypass** docs.
- **Shared library, separate CLIs** (`classify_common.py` + two wrappers).
- **Single 1.5k chunk** for both; metadata/headers injected in doctype prompt.
- **Same 3 buckets**, singular `doc_type:` frontmatter, shared ledger with `task`.
- **Separate Label Studio queues** + singleton audit.
- **Framework-owned `doc_types.yaml`** default + per-campaign overlay pattern.
