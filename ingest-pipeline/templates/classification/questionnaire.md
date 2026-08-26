# Campaign Classification Questionnaire

Fill this before freezing taxonomy. Completed answers produce `taxonomy.yaml`, `doc_types.yaml`, `glossary.yaml`, and `policy.yaml`. Freeze requires checked boxes at the end.

## 1. Domain / subdomain enumeration

- [ ] List all subdomains (5-50). For 20+, group into domains first.
- [ ] For each subdomain: 2-3 line definition, include list, exclude list.
- [ ] Overlap map: which subdomain pairs are commonly confused? Which campaign owns each overlap first? (backbone Order rule)
- [ ] Domains field: `domains:` multi-valued, list allowed domains.

## 2. Implicit-term harvesting

- [ ] List 30-50 surface forms that imply a subdomain without naming it (tests, procedures, acronyms, eponyms). Example: `HbA1c → diabetes_monitoring`.
- [ ] For each: maps_to, notes, source. These seed `glossary.yaml` and the judge's `domain_knowledge` header.
- [ ] Source for each term: which 2-3 real docs contain it? (proves harvest, not invention)

## 3. Example diversity (per subdomain)

- [ ] 3-8 examples per subdomain, each covering distinct variance:
  - [ ] short vs long doc
  - [ ] lexically explicit vs lexically thin (no keywords)
  - [ ] edge near neighbor subdomain (the confusing pair)
  - [ ] implicit-reference example (uses glossary term, not subdomain name)
- [ ] No near-duplicate examples. Each `covers:` label is distinct.

## 4. Comparison / relationship docs

- [ ] Policy choice: `primary_plus_secondary` (recommended) vs `single_plus_comparison_label`
- [ ] `relation_type` allowed values confirmed: `none, comparison, relationship, progression`
- [ ] Define "primary": doc's main purpose, not first mentioned. Give 2 examples of A-vs-B docs with your chosen primary.

## 5. Document shape → chunk policy

- [ ] Are docs front-loaded (title + first 1-2 paragraphs carry the subdomain signal)? If yes, `first_window` 1200-1500 is correct. Show 3 doc openings that prove it.
- [ ] If heading-dense: confirm `header_aware` and show a header outline example.
- [ ] If signal scattered: justify `full` and note cost.
- [ ] Window chosen: ___ tokens. Header outline included? yes/no.

## 6. Retrieval policy

- [ ] `top_k` chosen: ___ (default 4, 1-10). Rationale for k given N subdomains.
- [ ] Embedding model: ___ . Endpoint env: ___
- [ ] Judge model: ___ . Endpoint env: ___
- [ ] `gap_threshold` chosen: ___ (default 0.08). Docs where top1-top2 < gap go to NEEDS_HUMAN_VALIDATION.

## 7. Confidence buckets (rubric anchors)

- [ ] `SURE` anchor approved: "explicit + primary focus, no comparator / metadata consistent"
- [ ] `NEEDS_HUMAN_VALIDATION` anchor approved: "implicit or comparison or close runner-up / metadata ambiguous"
- [ ] `I_GUESSED` anchor approved: "thin/generic evidence"
- [ ] One anchored example per bucket written (reuse `taxonomy.yaml` examples for thin/generic).

## 8. Review & calibration

- [ ] `stratified_per_subdomain`: ___ (default 8)
- [ ] `stratified_per_doctype`: ___ (default 8)
- [ ] `spot_check_sure`: ___ (default 20), target `SURE` accuracy ___ (default 0.90)
- [ ] `singleton_audit`: ___ (default 10) — audit sample of singleton-pruned docs
- [ ] Gold sample: ~100 docs stratified + hard types (implicit, comparison) labeled by expert, stored as `campaigns/<campaign>/gold/`.

## 9. Document types (Part E — reading posture)

- [ ] Enumerate document types starting from the 14 defaults in `doc_types.yaml`. For each kept type: 2-3 line definition, include/exclude, reading_rule, ephemeral (true=>low ingest priority).
- [ ] Hide types you don't have; add any missing. Deletion is encouraged — defaults are starter, not mandate. Typical internal docs (training, task management, meeting minutes, anomaly reports, internal updates) are internal + Hebrew; external specs/standards are external + English — adjust if your corpus differs.
- [ ] For each kept type: set typical_languages/typical_sources (internal→he, external/spec→en are defaults — adjust if not). Set hard gates only if truly impossible: allowed_original_languages (e.g., anomaly_report: [he]), allowed_extensions (e.g., anomaly_report: [xlsx, xls, csv]).
- [ ] Overlap map for easily confused types: meeting_minutes vs task_list, anomaly_report vs trend_analysis, onboarding_q_with_answers vs onboarding_q_without_answers, announcement vs logistics. Which type owns each overlap first?
- [ ] Per-type examples: 3 diverse per type (short vs long, lexically thin vs explicit, extension signal vs prose). For onboarding Q±A, include one explicit Q+A and one Q-without-A.
- [ ] Confirm ephemeral types (logistics, announcement, task_list) ingest_priority: low is correct or adjust.
- [ ] Note partial-doc rules: meeting_minutes — only Decisions/Action Items are durable.

## Freeze checklist (all must be yes)

- [ ] `taxonomy.yaml` version frozen, N matches `policy.yaml` expectations, every subdomain has diverse examples
- [ ] `doc_types.yaml` version frozen, 14 types (or your edited count) validated, every type has diverse examples, internal→he / external→en defaults reviewed
- [ ] `glossary.yaml` version frozen, keys unique, every implicit example resolves via glossary
- [ ] `policy.yaml` version frozen, `top_k` and `window` and `gap_threshold` and `routing_defaults` justified by corpus sample
- [ ] Questionnaire answers committed; hash of four YAMLs recorded in ledger `taxonomy_frozen` / `doc_types_frozen` event

> New scenario: `cp -r payload/templates/classification campaigns/<campaign>/ && fill this file → freeze`
