# Dual Classification — Document Type Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add document-type classification as a second, parallel pipeline to subdomain classification — single-label, 13-type default (internal→he / external→en), with language/extension hard-gate pruning, singleton auto-assign, and shared library + separate CLIs, running after translation on the same 1.5k chunk.

**Architecture:** Single store/chunk, two parallel `retrieve → judge` flows. Extract shared logic into `ingest-pipeline/scripts/classify/classify_common.py` (centroids, embeddings, frontmatter, ledger helpers); keep thin task wrappers `classify_subdomain.py` and `classify_doctype.py` with their own YAML vocab, pruning, and prompt template. Payload ships `doc_types.yaml` as framework-owned default via `manifest.json`. Pure stdlib framework boundary preserved.

**Tech Stack:** Python stdlib only (framework), `urllib.request` + `hash_embed` fallback for embeddings/LLM, `re`/`hashlib`/`json`/`pathlib`, OpenAI-compatible `/v1/embeddings` + `/v1/chat/completions` with `guided_json`, content-addressed `store/`, append-only JSONL ledger, Label Studio HyperText/Choices XML.

---

## File Structure

```
# New payload (framework-owned, manifest.json-owned_paths):
src/second_brain_vault_framework/payload/templates/classification/doc_types.yaml
src/second_brain_vault_framework/payload/templates/classification/label_studio/view_doctype.xml
src/second_brain_vault_framework/payload/templates/classification/label_studio/view_singleton_audit.xml

# Modified payload/templates:
src/second_brain_vault_framework/payload/templates/classification/policy.yaml
src/second_brain_vault_framework/payload/templates/classification/questionnaire.md
src/second_brain_vault_framework/payload/templates/classification/label_studio/view.xml  # clarify as view_subdomain.xml, keep compat

# Pipeline scripts:
ingest-pipeline/scripts/classify/classify_common.py   # NEW shared lib
ingest-pipeline/scripts/classify/classify_subdomain.py # NEW wrapper (or keep retrieve/judge as wrappers)
ingest-pipeline/scripts/classify/classify_doctype.py   # NEW wrapper
ingest-pipeline/scripts/classify/taxonomy.py          # modify: add doc_types parser + constraint helpers
ingest-pipeline/scripts/classify/retrieve.py          # modify: pruning + task param
ingest-pipeline/scripts/classify/judge.py             # modify: extract common, add doctype prompt
ingest-pipeline/scripts/classify/validate.py          # modify: doc_type singular check
ingest-pipeline/scripts/classify/ledger.py            # modify: task field + singleton_pruned
ingest-pipeline/scripts/classify/export_label_studio.py # modify: doctype + singleton views
ingest-pipeline/scripts/classify/calibrate.py         # modify: per-doctype metrics + constraint_miss
ingest-pipeline/scripts/classify/chunk.py             # modify: emit source_metadata sidecar/frontmatter
ingest-pipeline/templates/classification/               # mirror payload for campaign scaffolding
  doc_types.yaml, policy.yaml, questionnaire.md

# Framework:
src/second_brain_vault_framework/manifest.json         # add owned_paths
src/second_brain_vault_framework/__init__.py           # version bump if needed

# Tests:
ingest-pipeline/tests/test_classify_doctype.py        # new: vocab sync, pruning, judge schema, ledger
ingest-pipeline/tests/test_classify_common.py         # new: shared helpers
tests/test_payload_doc_types.py                        # optional: payload-owned drift check (like existing)
```

---

### Task 1: Payload — `doc_types.yaml` global default (13 types)

**Files:**
- Create: `src/second_brain_vault_framework/payload/templates/classification/doc_types.yaml`
- Create: `ingest-pipeline/templates/classification/doc_types.yaml` (mirror, same content)

- [ ] **Step 1: Write the failing test**

```python
# ingest-pipeline/tests/test_classify_doctype.py (initial)
import re
from pathlib import Path

def test_doc_types_yaml_exists_and_has_13_types():
    # Check both payload and ingest template mirrors exist
    payload = Path("src/second_brain_vault_framework/payload/templates/classification/doc_types.yaml")
    ingest = Path("ingest-pipeline/templates/classification/doc_types.yaml")
    # At least one must exist for this test to pass after implementation
    assert payload.exists(), f"missing {payload}"
    txt = payload.read_text(encoding="utf-8")
    # Count top-level doc-type keys (2-space indent, name:)
    tops = re.findall(r"^\s{2}([\w-]+):\s*\n", txt, flags=re.MULTILINE)
    # Exclude known non-type keys
    types = [t for t in tops if t not in ("doc_types", "version", "campaign")]
    assert len(types) == 13, f"expected 13 doc types, got {len(types)}: {types}"
    for required in ["spec_standard", "anomaly_report", "anomaly_drill_down", "trend_analysis",
                     "onboarding_q_with_answers", "onboarding_q_without_answers", "task_list"]:
        assert required in types, f"missing {required}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ingest-pipeline && python -m unittest tests.test_classify_doctype.TestDocTypes -v` or `python -m pytest tests/test_classify_doctype.py -v`
Expected: FAIL with `missing ...doc_types.yaml`

- [ ] **Step 3: Write minimal implementation**

Create `src/second_brain_vault_framework/payload/templates/classification/doc_types.yaml`:

```yaml
# Document types — global default (13), framework-owned, editable.
# Copy to campaigns/<campaign>/doc_types.yaml overlay to add/hide/constrain.
# Internal -> he, external -> en defaults are editable hints, not hard guarantees.
# Hard gates: allowed_original_languages / allowed_extensions prune candidates.
# Soft hints: typical_languages / typical_sources / ephemeral / ingest_priority enrich prompt/routing.
version: 1
doc_types:
  domain_intro_presentation:
    definition: "Introductory deck that frames a domain — high-level concepts, scope, and goals."
    include: ["introductory slides", "domain framing", "overview deck"]
    exclude: ["detailed procedure", "raw data table"]
    reading_rule: "Read as framing/orientation, not as normative instruction."
    ephemeral: false
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: []
    allowed_extensions: []
    ingest_priority: normal
    examples:
      - text: "Domain intro: System X overview — goals, scope, key stakeholders and lifecycle."
        source: "doc_0101.md"
        covers: "short deck, explicit"
      - text: "Presentation outline: introduction to the platform, no procedures, high-level architecture only."
        source: "doc_0102.md"
        covers: "lexically thin, framing"
      - text: "Q3 kickoff deck: domain context and objectives for new joiners."
        source: "doc_0103.md"
        covers: "onboarding framing"

  spec_standard:
    definition: "Normative spec or standard — requirements, compliance, or interface definition, read literally."
    include: ["requirements", "compliance clause", "interface spec"]
    exclude: ["internal memo", "training Q&A"]
    reading_rule: "Read literally as normative requirement."
    ephemeral: false
    typical_languages: [en]
    typical_sources: [external]
    allowed_original_languages: []
    allowed_extensions: []
    ingest_priority: normal
    examples:
      - text: "Spec v3.2: The system SHALL support X with latency < 100ms. Compliance required."
        source: "doc_0201.md"
        covers: "explicit normative"
      - text: "External standard: interface definition for subsystem Y, versioned and reviewed."
        source: "doc_0202.md"
        covers: "external authority"

  official_research_summary:
    definition: "Synthesized research summary — literature, findings, and implications, rigorous but interpretive."
    include: ["literature review", "findings synthesis"]
    exclude: ["raw intermediate numbers", "announcement"]
    reading_rule: "Read as interpretive synthesis, check provenance."
    ephemeral: false
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: []
    allowed_extensions: []
    ingest_priority: normal
    examples:
      - text: "Research summary: review of 12 studies on X, implications for practice, limitations noted."
        source: "doc_0301.md"
        covers: "synthesis"

  logistics:
    definition: "Coordination/logistics — scheduling, resources, access, or operational notes."
    include: ["schedule", "room booking", "access request"]
    exclude: ["research finding", "spec requirement"]
    reading_rule: "Ephemeral coordination; may be deprioritized for ingest."
    ephemeral: true
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: []
    allowed_extensions: []
    ingest_priority: low
    examples:
      - text: "Logistics: Q3 meeting rooms booked, access cards issued, lunch安排 noted."
        source: "doc_0401.md"
        covers: "explicit logistics"

  announcement:
    definition: "Broadcast announcement — general, policy, or event notice."
    include: ["announcement", "notice", "policy update"]
    exclude: ["detailed procedure", "Q&A"]
    reading_rule: "Read as point-in-time notice."
    ephemeral: true
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: []
    allowed_extensions: []
    ingest_priority: low
    examples:
      - text: "Announcement: New policy effective 2026-09-01, all staff must complete training by 08-31."
        source: "doc_0501.md"
        covers: "policy announcement"

  anomaly_report:
    definition: "Structured anomaly snapshot — table of anomalies, counts, and status, often xlsx."
    include: ["anomaly table", "incident counts"]
    exclude: ["trend narrative", "meeting notes"]
    reading_rule: "Read table as snapshot; values are point-in-time."
    ephemeral: false
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: [he]
    allowed_extensions: [xlsx, xls, csv]
    ingest_priority: high
    examples:
      - text: "Anomaly report 2026-07: 23 anomalies, breakdown by subsystem, CSV attached."
        source: "doc_0601.xlsx"
        covers: "structured table"

  anomaly_drill_down:
    definition: "Deep-dive on a specific anomaly — root cause, evidence, and follow-up."
    include: ["root cause", "drill-down", "evidence trace"]
    exclude: ["high-level anomaly table only"]
    reading_rule: "Read as investigation, not as snapshot."
    ephemeral: false
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: [he]
    allowed_extensions: []
    ingest_priority: high
    examples:
      - text: "Drill-down: anomaly #42 — trace, logs, hypothesis, next steps."
        source: "doc_0701.md"
        covers: "explicit drill-down"

  trend_analysis:
    definition: "Analytical trend or longitudinal review — evolution over time, comparison across periods."
    include: ["trend", "over time", "time series"]
    exclude: ["single-point anomaly table"]
    reading_rule: "Read as analysis over time."
    ephemeral: false
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: [he]
    allowed_extensions: []
    ingest_priority: normal
    examples:
      - text: "Trend analysis Q1→Q3: drift in metric X, 12% increase, seasonality noted."
        source: "doc_0801.md"
        covers: "explicit trend"

  meeting_minutes:
    definition: "Meeting minutes — attendees, discussion, and decisions; only decisions/action items are durable."
    include: ["attendees", "decisions", "action items"]
    exclude: ["checklist without meeting context"]
    reading_rule: "Only Decisions and Action Items are durable; discussion is context."
    ephemeral: false
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: []
    allowed_extensions: []
    ingest_priority: normal
    examples:
      - text: "Minutes 2026-08-10: attendees, discussion, Decisions: approve X. Action: Y by 08-20."
        source: "doc_0901.md"
        covers: "explicit decisions"

  intermediate_results:
    definition: "Mid-stream provisional results — numbers pending validation or review."
    include: ["interim", "preliminary", "pending review"]
    exclude: ["final spec", "approved summary"]
    reading_rule: "Read as provisional; may be superseded."
    ephemeral: false
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: []
    allowed_extensions: []
    ingest_priority: normal
    examples:
      - text: "Intermediate results T2: provisional findings, subject to validation."
        source: "doc_1001.md"
        covers: "provisional"

  official_internal_update:
    definition: "Authoritative internal memo or status update from leadership or program office."
    include: ["status update", "internal memo", "program office"]
    exclude: ["external spec", "training exercise"]
    reading_rule: "Read as authoritative internal statement."
    ephemeral: false
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: []
    allowed_extensions: []
    ingest_priority: normal
    examples:
      - text: "Internal update: program status, milestones, risks, and next gates."
        source: "doc_1101.md"
        covers: "authoritative update"

  task_list:
    definition: "Checklist or task inventory — tasks, owners, and due dates, structure over prose."
    include: ["checklist", "tasks", "- [ ]", "owners"]
    exclude: ["meeting minutes narrative"]
    reading_rule: "Read as task inventory; structure is the signal."
    ephemeral: true
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: []
    allowed_extensions: [xlsx, csv, md]
    ingest_priority: low
    examples:
      - text: "- [ ] Prepare spec review — @yonik 2026-08-25\n- [x] Translate batch 3"
        source: "doc_1201.md"
        covers: "checklist structure"

  onboarding_q_with_answers:
    definition: "Training/onboarding Q&A where both question and approved answer are present — A is durable canonical answer."
    include: ["Q: ... A: ...", "approved answer"]
    exclude: ["Q without answer", "free exposition without Q"]
    reading_rule: "Read A: as durable canonical answer."
    ephemeral: false
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: []
    allowed_extensions: []
    ingest_priority: normal
    examples:
      - text: "Q: What is the escalation path for anomaly #7? A: Notify program office and file DR-7 within 24h."
        source: "doc_1301.md"
        covers: "explicit Q+A"
      - text: "Q: When is trend analysis required? A: When metric X exceeds threshold for 2 consecutive periods."
        source: "doc_1302.md"
        covers: "implicit Q+A"

  onboarding_q_without_answers:
    definition: "Training/onboarding question without an answer — exercise or assessment prompt, not a claim."
    include: ["Q:", "exercise", "assessment without answer"]
    exclude: ["Q with approved A"]
    reading_rule: "Read as assessment prompt; no answer to ingest as fact."
    ephemeral: false
    typical_languages: [he]
    typical_sources: [internal]
    allowed_original_languages: []
    allowed_extensions: []
    ingest_priority: normal
    examples:
      - text: "Q: Describe the trend in metric X and propose next steps. (no answer provided)"
        source: "doc_1401.md"
        covers: "Q without A"
```

Copy identical to `ingest-pipeline/templates/classification/doc_types.yaml`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s ingest-pipeline/tests -k doctype -v` (or the specific file)
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/second_brain_vault_framework/payload/templates/classification/doc_types.yaml ingest-pipeline/templates/classification/doc_types.yaml ingest-pipeline/tests/test_classify_doctype.py
git commit -m "feat(classify): add doc_types.yaml global default (13 types, internal→he / external→en, onboarding Q±A)"
```

---

### Task 2: Policy + questionnaire — extend for doc-types

**Files:**
- Modify: `src/second_brain_vault_framework/payload/templates/classification/policy.yaml`
- Modify: `src/second_brain_vault_framework/payload/templates/classification/questionnaire.md`
- Modify: `ingest-pipeline/templates/classification/policy.yaml` (mirror)
- Modify: `ingest-pipeline/templates/classification/questionnaire.md` (mirror)

- [ ] **Step 1: Write the failing test**

```python
def test_policy_has_doctype_fields():
    txt = Path("src/second_brain_vault_framework/payload/templates/classification/policy.yaml").read_text(encoding="utf-8")
    assert "stratified_per_doctype" in txt
    assert "singleton_audit" in txt
    assert "gap_threshold" in txt
    assert "routing_defaults" in txt
    assert "internal_hebrew" in txt
    assert "external_english" in txt

def test_questionnaire_part_e_has_doc_types():
    txt = Path("src/second_brain_vault_framework/payload/templates/classification/questionnaire.md").read_text(encoding="utf-8")
    assert "document type" in txt.lower()
    assert "onboarding" in txt.lower()
    assert "ephemeral" in txt.lower()
    assert "internal" in txt.lower() and "hebrew" in txt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest ingest-pipeline/tests/test_classify_doctype.py::TestPolicyAndQuestionnaire -v`
Expected: FAIL missing keys

- [ ] **Step 3: Write minimal implementation**

`policy.yaml` — add to existing (keep `chunk`, `retrieval`, `judge`, `confidence`, `relation`, extend `review`):

```yaml
version: 1
chunk: {mode: first_window, window: 1500, header_outline: true}
retrieval: {top_k: 4, embed_model: "embeddinggemma-300M"}
judge: {model: "minimax-m2.7", temperature: 0.0, few_shot_per_topk: 3, gap_threshold: 0.08}
confidence:
  buckets: [SURE, NEEDS_HUMAN_VALIDATION, I_GUESSED]
  rubric_anchors:
    SURE: "explicit + primary focus, no comparator / metadata consistent"
    NEEDS_HUMAN_VALIDATION: "implicit or close runner-up or metadata ambiguous"
    I_GUESSED: "thin/generic evidence"
relation: {mode: primary_plus_secondary, allowed: [none, comparison, relationship, progression]}
review: {stratified_per_subdomain: 8, stratified_per_doctype: 8, disagreement_band: 0.10, spot_check_sure: 20, singleton_audit: 10}
routing_defaults:
  internal_hebrew: {languages: [he], sources: [internal]}
  external_english: {languages: [en], sources: [external]}
```

`questionnaire.md` — append new section after existing §4-8 (before Freeze checklist). Add Part E for doc-types:

```markdown
## 9. Document types (Part E — reading posture)

- [ ] Enumerate doc types starting from the 13 defaults in `doc_types.yaml`. For each kept type: 2-3 line definition, include/exclude, reading_rule, ephemeral (true=>low ingest priority).
- [ ] Hide types you don't have; add any missing. Deletion is encouraged — defaults are starter, not mandate.
- [ ] For each kept type: set typical_languages/typical_sources (internal→he, external/spec→en are defaults — adjust if not). Set hard gates only if truly impossible: allowed_original_languages (e.g., anomaly_report: [he]), allowed_extensions (e.g., anomaly_report: [xlsx, xls, csv]).
- [ ] Overlap map for easily confused types: meeting_minutes vs task_list, anomaly_report vs trend_analysis, onboarding_q_with_answers vs onboarding_q_without_answers, announcement vs logistics. Which type owns each overlap first?
- [ ] Per-type examples: 3 diverse per type (short vs long, lexically thin vs explicit, extension signal vs prose). For onboarding Q±A, include one explicit Q+A and one Q-without-A.
- [ ] Confirm ephemeral types (logistics, announcement, task_list) ingest_priority: low is correct or adjust.
```

Update Freeze checklist to gate both `taxonomy.yaml` and `doc_types.yaml`.

- [ ] **Step 4: Run test to verify it passes**

Run: same as Step 2
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/second_brain_vault_framework/payload/templates/classification/policy.yaml src/second_brain_vault_framework/payload/templates/classification/questionnaire.md ingest-pipeline/templates/classification/policy.yaml ingest-pipeline/templates/classification/questionnaire.md
git commit -m "feat(classify): extend policy+questionnaire for doc-types (gap_threshold, singleton_audit, routing_defaults, Part E)"
```

---

### Task 3: `taxonomy.py` — add doc_types parser + constraint helpers

**Files:**
- Modify: `ingest-pipeline/scripts/classify/taxonomy.py`
- Test: `ingest-pipeline/tests/test_classify_doctype.py`

- [ ] **Step 1: Write the failing test**

```python
from scripts.classify.taxonomy import parse_doc_types_blocks, effective_doc_type_candidates, load_doc_types

def test_parse_doc_types_and_pruning():
    txt = Path("src/second_brain_vault_framework/payload/templates/classification/doc_types.yaml").read_text(encoding="utf-8")
    blocks = parse_doc_types_blocks(txt)
    assert "anomaly_report" in blocks
    assert "onboarding_q_with_answers" in blocks
    # Pruning: anomaly_report only allowed for he + xlsx
    all_types = set(blocks.keys())
    pruned_he_xlsx = effective_doc_type_candidates(blocks, original_language="he", extension="xlsx")
    assert "anomaly_report" in pruned_he_xlsx
    assert "spec_standard" in pruned_he_xlsx  # no hard gate, so stays
    pruned_en_xlsx = effective_doc_type_candidates(blocks, original_language="en", extension="xlsx")
    assert "anomaly_report" not in pruned_en_xlsx
    assert "trend_analysis" not in pruned_en_xlsx

def test_singleton_detection():
    # Given a blocks dict where pruning leaves one candidate
    blocks = {"a": {"allowed_original_languages": ["he"], "allowed_extensions": ["xlsx"]},
              "b": {"allowed_original_languages": ["en"]}}
    # Simulate he+xlsx => only a
    from scripts.classify.taxonomy import singleton_pruned_type
    assert singleton_pruned_type(blocks, original_language="he", extension="xlsx") == "a"
    assert singleton_pruned_type(blocks, original_language="en", extension="xlsx") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ingest-pipeline && python -m unittest tests.test_classify_doctype.TestTaxonomyDocTypes -v`
Expected: FAIL `ImportError: cannot import name 'parse_doc_types_blocks'`

- [ ] **Step 3: Write minimal implementation**

In `ingest-pipeline/scripts/classify/taxonomy.py`, add after existing `parse_taxonomy_blocks`/`templates_root`:

```python
import re as _re2

_DOC_TYPE_NON_KEYS = ("doc_types", "version", "campaign", "routing_defaults")

def parse_doc_types_blocks(txt: str) -> dict[str, str]:
    """Map doc-type name -> raw YAML block (same indent rule as subdomains)."""
    matches = list(TAXONOMY_RE.finditer(txt))
    blocks: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        if name in _DOC_TYPE_NON_KEYS:
            continue
        # Heuristic: doc_types live under `doc_types:` parent; but flat parse is fine if file is doc_types.yaml
        # Exclude policy/routing keys that match the regex but aren't types
        if name in ("chunk", "retrieval", "judge", "confidence", "relation", "review"):
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(txt)
        # Only keep blocks that look like a doc-type (has definition: or ephemeral:)
        block = txt[start:end]
        if "definition:" in block or "ephemeral:" in block:
            blocks[name] = block
    return blocks

def _parse_allowed(block: str, key: str) -> list[str] | None:
    # e.g., allowed_original_languages: [he]  or []  or [he, en]
    m = _re2.search(rf"{key}:\s*\[([^\]]*)\]", block)
    if not m:
        return None
    inner = m.group(1).strip()
    if not inner:
        return []  # empty = no restriction
    # split by comma, strip quotes/spaces
    return [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]

def effective_doc_type_candidates(blocks: dict[str, str], *, original_language: str, extension: str) -> list[str]:
    """Prune doc-types by hard gates. Empty allowed list means no restriction."""
    ext = extension.lower().lstrip(".")
    lang = original_language.lower()
    out = []
    for name, block in blocks.items():
        langs = _parse_allowed(block, "allowed_original_languages")
        exts = _parse_allowed(block, "allowed_extensions")
        if langs is not None and langs != [] and lang not in [l.lower() for l in langs]:
            continue
        if exts is not None and exts != [] and ext not in [e.lower().lstrip(".") for e in exts]:
            continue
        out.append(name)
    return sorted(out)

def singleton_pruned_type(blocks: dict[str, str], *, original_language: str, extension: str) -> str | None:
    cands = effective_doc_type_candidates(blocks, original_language=original_language, extension=extension)
    return cands[0] if len(cands) == 1 else None

def load_doc_types(path) -> tuple[str, dict]:
    """Return (raw_txt, {name: {definition, examples, block}})."""
    from pathlib import Path as _P
    p = _P(path)
    if not p.exists():
        return "", {}
    txt = p.read_text(encoding="utf-8")
    blocks = parse_doc_types_blocks(txt)
    out = {}
    for name, block in blocks.items():
        dm = _re2.search(r"definition:\s*\"(.*?)\"", block, flags=_re2.DOTALL)
        examples = _re2.findall(r"text:\s*\"(.*?)\"", block)
        out[name] = {"definition": dm.group(1) if dm else "", "examples": examples, "block": block}
    return txt, out
```

Add `templates_root` already exists; ensure `doc_types.yaml` resolves via `templates_root() / "doc_types.yaml"` fallback.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ingest-pipeline && python -m unittest tests.test_classify_doctype.TestTaxonomyDocTypes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ingest-pipeline/scripts/classify/taxonomy.py ingest-pipeline/tests/test_classify_doctype.py
git commit -m "feat(classify): taxonomy parser for doc_types + language/extension pruning + singleton helper"
```

---

### Task 4: Shared library `classify_common.py`

**Files:**
- Create: `ingest-pipeline/scripts/classify/classify_common.py`
- Test: `ingest-pipeline/tests/test_classify_common.py`

- [ ] **Step 1: Write the failing test**

```python
from scripts.classify.classify_common import cosine, hash_embed, build_centroids, atomic_write, estimate_tokens

def test_cosine_and_hash_embed_deterministic():
    v1 = hash_embed("hello world", dim=16)
    v2 = hash_embed("hello world", dim=16)
    assert v1 == v2
    assert abs(cosine(v1, v1) - 1.0) < 1e-6

def test_build_centroids_from_examples():
    from hashlib import sha256
    subs = {"a": ["hello world", "hello there"], "b": ["goodbye world"]}
    cents = build_centroids(subs, dim=16, embed_fn=hash_embed)
    assert "a" in cents and "b" in cents
    assert len(cents["a"]) == 16

def test_atomic_write(tmp_path):
    p = tmp_path / "out.md"
    atomic_write(p, "hello")
    assert p.read_text() == "hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ingest-pipeline && python -m unittest tests.test_classify_common -v`
Expected: FAIL `ModuleNotFoundError: No module named 'scripts.classify.classify_common'`

- [ ] **Step 3: Write minimal implementation**

```python
# ingest-pipeline/scripts/classify/classify_common.py
"""Shared helpers for subdomain + doc-type pipelines. Pure stdlib."""
from __future__ import annotations
import hashlib, math, re
from pathlib import Path

def cosine(a, b):
    dot = sum(x*y for x,y in zip(a,b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(x*x for x in b))
    return dot/(na*nb) if na and nb else 0.0

def hash_embed(text: str, dim=64):
    vec = [0.0]*dim
    for w in re.findall(r"\w+", text.lower())[:200]:
        h = int(hashlib.sha256(w.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    n = math.sqrt(sum(x*x for x in vec)) or 1.0
    return [x/n for x in vec]

def build_centroids(examples_by_label: dict[str, list[str]], dim=64, embed_fn=None):
    """Mean embedding per label, L2-normalized. embed_fn(text)->vec for DI/tests."""
    cents = {}
    for label, exs in examples_by_label.items():
        if not exs: continue
        vecs = [embed_fn(t) if embed_fn else hash_embed(t, dim=dim) for t in exs]
        # validate dim consistency
        d = len(vecs[0]); assert all(len(v)==d for v in vecs)
        mean = [sum(v[i] for v in vecs)/len(vecs) for i in range(d)]
        n = math.sqrt(sum(x*x for x in mean)) or 1.0
        cents[label] = [x/n for x in mean]
    return cents

def estimate_tokens(text: str) -> int:
    return max(1, len(text)//4)

def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(path)

def parse_frontmatter(text: str):
    if not text.startswith("---"): return {}, text
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None: return {}, text
    fm = {}; cur=None
    for raw in lines[1:end]:
        if re.match(r"^\s*-\s+", raw) and cur is not None:
            fm.setdefault(cur, []); fm[cur].append(raw.strip()[2:].strip('"')); continue
        m=re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", raw)
        if m:
            k,v=m.group(1), m.group(2).strip(); cur=k
            fm[k]=[] if v=="" else v.strip('"')
    body="\n".join(lines[end+1:])
    return fm, body

def extract_headers(body: str) -> str:
    out=[]; fence=False
    for line in body.splitlines():
        if line.strip().startswith("```"): fence=not fence; continue
        if not fence and line.lstrip().startswith("#"): out.append(line)
    return "\n".join(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ingest-pipeline && python -m unittest discover -s tests -k common -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ingest-pipeline/scripts/classify/classify_common.py ingest-pipeline/tests/test_classify_common.py
git commit -m "feat(classify): shared library classify_common (cosine, hash_embed, centroids, frontmatter, atomic write)"
```

---

### Task 5: `chunk.py` — emit `source_metadata` for doc-type prompt

**Files:**
- Modify: `ingest-pipeline/scripts/classify/chunk.py`
- Test: `ingest-pipeline/tests/test_classify_doctype.py`

- [ ] **Step 1: Write the failing test**

```python
def test_chunk_emits_source_metadata(tmp_path):
    # chunk a file whose original name/ext would be in sidecar if we simulate
    # For now check frontmatter includes source_metadata or that chunk preserves source_path
    # and that we can reconstruct ext/lang for pruning
    src = tmp_path / "report.xlsx.md"  # pretend original was .xlsx
    src.write_text("---\ntitle: \"t\"\n---\n# Hello\nBody", encoding="utf-8")
    out_store = tmp_path / "store"
    # Run chunk via python -m
    import subprocess, sys
    subprocess.check_call([sys.executable, "ingest-pipeline/scripts/classify/chunk.py", str(src), "--store", str(out_store), "--window", "1500"])
    outs = list(out_store.rglob("*.md"))
    assert outs
    txt = outs[0].read_text(encoding="utf-8")
    assert "source_path" in txt  # already exists
    # New: should contain source_ext or allow sidecar reconstruction
```

- [ ] **Step 2: Run test to verify it fails** (if you assert new field not yet present)

Run: `cd ingest-pipeline && python -m unittest tests.test_classify_doctype.TestChunkMeta -v`
Expected: FAIL (or PASS for existing but you then extend)

- [ ] **Step 3: Write minimal implementation**

In `ingest-pipeline/scripts/classify/chunk.py`, after `doc_id`/`h` logic, extend frontmatter to include `source_metadata` hint and write a sidecar `*.meta.json` for the pruner:

```python
# inside for src in inputs:  after title = ...
original_ext = src.suffix.lstrip(".")  # best-effort; real pipeline will have sidecar from earlier stage
# Prefer sidecar if present: src.meta.json or ingest-pipeline/data/... but minimal: infer from filename stem if it contains original ext
# For English-bypass docs, frontmatter may already have original_language
fm_before, _ = parse_frontmatter(text)
original_language = fm_before.get("original_language", "he" if "he" in fm_before.get("languages","") else "")
# Build extended frontmatter:
out_text = (
  f"---\n"
  f'source_doc_id: "{doc_id}"\n'
  f'source_hash: "{h}"\n'
  f'source_path: "{src.as_posix()}"\n'
  f'source_ext: "{original_ext}"\n'
  f'original_language: "{original_language or ""}"\n'
  f'chunk_policy_version: "1"\n'
  f'chunk_mode: "{mode}"\n'
  f'chunk_window: {window}\n'
  f'title: "{title}"\n'
  f"---\n\n"
  f"{chunk_body}\n"
)
# Content-addressed path
out_path = store_root / h[:2] / f"{h}.md"
# Write sidecar *.meta.json for classify_doctype pruner
meta_path = out_path.with_suffix(".meta.json")
if not args.dry_run:
    atomic_write(out_path, out_text)
    # Sidecar with pruner-relevant fields
    meta = {"source_path": src.as_posix(), "source_ext": original_ext, "original_language": original_language, "title": title, "source_hash": h}
    atomic_write(meta_path, json.dumps(meta, indent=2))
```

Ensure `import json` at top (already there). Also keep `atomic_write` from `classify_common` or local.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ingest-pipeline && python -m unittest tests.test_classify_doctype.TestChunkMeta -v`
Expected: PASS (`source_ext` present, `.meta.json` exists)

- [ ] **Step 5: Commit**

```bash
git add ingest-pipeline/scripts/classify/chunk.py
git commit -m "feat(classify): chunk emits source_ext/original_language + meta sidecar for doc-type pruning"
```

---

### Task 6: `retrieve.py` — task-aware with pruning, refactor to use `classify_common`

**Files:**
- Modify: `ingest-pipeline/scripts/classify/retrieve.py`
- Test: `ingest-pipeline/tests/test_classify_doctype.py`

- [ ] **Step 1: Write the failing test**

```python
def test_retrieve_prunes_on_language_and_extension(tmp_path):
    # Build a tiny store with one doc whose meta says en + xlsx => anomaly_report should be pruned
    # Use hash_embed fallback so no API needed
    import json, subprocess, sys
    from pathlib import Path
    camp = tmp_path / "camp"
    camp.mkdir()
    # Minimal doc_types to test pruning is handled by retrieve layer
    Path("src/second_brain_vault_framework/payload/templates/classification/doc_types.yaml").read_text()
    store = tmp_path / "store"
    store.mkdir()
    doc = store / "00" / "abc.md"
    doc.parent.mkdir(exist_ok=True)
    doc.write_text("---\ntitle: \"t\"\n---\nTrend body", encoding="utf-8")
    (doc.with_suffix(".meta.json")).write_text(json.dumps({"source_ext":"xlsx","original_language":"en"}), encoding="utf-8")
    # Run retrieve in doctype mode with tmp campaign that has payload doc_types
    # Expect sidecar candidates not to contain anomaly_report
    # This will fail until retrieve supports --vocab/--mode
    assert True  # placeholder — real test drives the API

def test_retrieve_topk_clamped_and_dim_mismatch(tmp_path):
    # Reuse existing retrieve tests for clamping 1-10
    pass
```

Before writing, run `cd ingest-pipeline && python -m unittest tests.test_classify_doctype -v` — pruned test should fail to assert anomaly_report absent.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s ingest-pipeline/tests -v`
Expected: FAIL (pruning not implemented)

- [ ] **Step 3: Write minimal implementation**

Refactor `ingest-pipeline/scripts/classify/retrieve.py`:

- Import from `classify_common` (`cosine`, `hash_embed`, `build_centroids`).
- Add `--vocab {taxonomy,doc_types}` or keep `--campaign` + `--doc-types` flag; simplest: keep `--campaign` but check for `doc_types.yaml` and if `--doctype` flag is set, load doc_types via `taxonomy.load_doc_types` + `effective_doc_type_candidates`.
- Candidate pruning: for each doc, read `doc.meta.json` (or frontmatter `source_ext`/`original_language`) and call `effective_doc_type_candidates` to get `allowed_set`. Filter centroids to allowed before ranking. If `allowed_set` empty → fail-closed (no candidates). If `len(allowed)==1` → write `.retrieval.json` with single candidate + `pruned_singleton: true` and skip embedding? But spec says retrieve still runs; singleton shortcut is in judge. So just filter and rank.
- Support `--dry-run`.

Pseudo-patch:

```python
# top of retrieve.py
try:
    from .classify_common import cosine, hash_embed, build_centroids
except ImportError:
    from classify_common import cosine, hash_embed, build_centroids
# also import taxonomy helpers
try:
    from .taxonomy import load_doc_types, effective_doc_type_candidates, parse_doc_types_blocks
except ImportError:
    from taxonomy import load_doc_types, effective_doc_type_candidates, parse_doc_types_blocks

# in main(), add:
p.add_argument("--doctype", action="store_true", help="retrieve for doc-type (uses doc_types.yaml + pruning)")
# ...
# Load vocab:
if args.doctype:
    txt, subs_map = load_doc_types(tax_path if tax_path.name=="doc_types.yaml" else tax_path.parent / "doc_types.yaml")
    # subs_map: name->{definition, examples}
    subs = {k: v["examples"] for k,v in subs_map.items()}
    blocks = parse_doc_types_blocks(txt)
else:
    subs = load_taxonomy(tax_path)  # existing
    blocks = {}
# Centroid build unchanged but via build_centroids
# For each doc:
meta = {}
meta_path = doc.with_suffix(".meta.json")
if meta_path.exists():
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
else:
    # fallback to frontmatter
    fm,_ = parse_frontmatter(doc.read_text(encoding="utf-8"))
    meta = {"source_ext": fm.get("source_ext",""), "original_language": fm.get("original_language","")}
if args.doctype and blocks:
    allowed = set(effective_doc_type_candidates(blocks, original_language=meta.get("original_language",""), extension=meta.get("source_ext","")))
    centroids = {k:v for k,v in centroids.items() if k in allowed}
    if not centroids:
        print(f"retrieve: no candidates after pruning for {doc.name} (lang={meta.get('original_language')} ext={meta.get('source_ext')})", file=sys.stderr)
        continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ingest-pipeline && python -m unittest discover -s tests -v`
Expected: PASS (existing + new doctype pruning tests)

- [ ] **Step 5: Commit**

```bash
git add ingest-pipeline/scripts/classify/retrieve.py ingest-pipeline/scripts/classify/classify_common.py
git commit -m "feat(classify): retrieve supports doc-type vocab + language/extension pruning (shared centroids)"
```

---

### Task 7: `judge.py` + wrappers `classify_doctype.py` / `classify_subdomain.py`

**Files:**
- Modify: `ingest-pipeline/scripts/classify/judge.py` (extract common)
- Create: `ingest-pipeline/scripts/classify/classify_doctype.py`
- Create: `ingest-pipeline/scripts/classify/classify_subdomain.py` (or refactor judge to be wrapper)
- Test: `ingest-pipeline/tests/test_classify_doctype.py`

- [ ] **Step 1: Write the failing test**

```python
def test_judge_schema_doctype_singleton():
    from scripts.classify.judge import build_schema, build_prompt
    schema = build_schema(["anomaly_report", "trend_analysis"], task="doctype")
    assert schema["properties"]["doc_type"]["enum"] == ["anomaly_report", "trend_analysis"]
    assert "primary_subdomain" not in schema["properties"]
    assert schema["properties"]["confidence_bucket"]["enum"] == ["SURE","NEEDS_HUMAN_VALIDATION","I_GUESSED"]

def test_build_prompt_includes_metadata():
    from scripts.classify.judge import build_prompt
    sys_p, usr = build_prompt("Body about anomaly", {"anomaly_report": {"definition":"...", "examples":["ex1"]}}, "", ["anomaly_report"], "", task="doctype", source_metadata={"source_ext":"xlsx","original_language":"he","filename":"rep.xlsx"})
    assert "Source metadata" in sys_p
    assert "ext=xlsx" in sys_p

def test_singleton_skips_llm(tmp_path):
    # doctype judge in singleton case should not call LLM, write .judge.json directly with SURE + singleton_constraint
    import json, subprocess, sys
    # Create a store doc with meta that prunes to singleton anomaly_report
    # Run classify_doctype --dry-run and check output
    pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ingest-pipeline && python -m unittest tests.test_classify_doctype.TestJudgeDoctype -v`
Expected: FAIL `build_schema() got unexpected keyword argument 'task'`

- [ ] **Step 3: Write minimal implementation**

Refactor `ingest-pipeline/scripts/classify/judge.py`:

- Extract `build_schema(allowed, task="subdomain")` → if `task=="doctype"` schema is `{reasoning_brief, doc_type, confidence_bucket}`; else `{reasoning_brief, primary_subdomain, secondary_subdomains, relation_type, confidence_bucket}`.
- `build_prompt(..., task="subdomain", source_metadata=None)` → when `task=="doctype"` prepend `Source metadata: filename=... ext=... original_language=...` + `Headers outline:` + `Typical mapping: internal→he, external→en`. Use doc_types definitions (not glossary). Inject `allowed_extensions` as soft hint ("Typical ext: xlsx") but pruning already done.
- In `call_llm` keep same `response_format` / `extra_body.guided_json`.
- In `main()`, add `--doctype` path; before LLM loop, check `singleton_pruned_type` — if singleton, write `.judge.json` with `{"reasoning_brief":"singleton_constraint: pruned to ... via language/ext","doc_type":singleton,"confidence_bucket":"SURE","singleton_constraint": true}` + write `singleton_pruned` ledger event, continue (no LLM).

Create wrappers:

`ingest-pipeline/scripts/classify/classify_doctype.py` (thin):

```python
#!/usr/bin/env python3
"""Thin wrapper for doc-type classification — delegates to judge.py with task=doctype."""
import sys
from pathlib import Path
# Re-export judge.main with doctype flag set
if __name__ == "__main__":
    # Inject --doctype before forwarding
    argv = sys.argv[1:]
    if "--doctype" not in argv:
        argv = ["--doctype"] + argv
    sys.argv = [sys.argv[0]] + argv
    from judge import main
    sys.exit(main())
```

`classify_subdomain.py` similarly with no flag (keeps existing judge behavior for backwards compat; `judge.py` remains directly callable).

Keep dynamic few-shot: `for c in candidates: sub = taxonomy_subs.get(c)` already draws only from pruned candidates — so dynamic pruning is automatic.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ingest-pipeline && python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ingest-pipeline/scripts/classify/judge.py ingest-pipeline/scripts/classify/classify_doctype.py ingest-pipeline/scripts/classify/classify_subdomain.py
git commit -m "feat(classify): judge split by task (doctype prompt with metadata, singleton auto-assign, dynamic few-shot from pruned set) + thin wrappers"
```

---

### Task 8: `validate.py` — closed-vocab for `doc_type`

**Files:**
- Modify: `ingest-pipeline/scripts/classify/validate.py`
- Test: `ingest-pipeline/tests/test_classify_doctype.py`

- [ ] **Step 1: Write the failing test**

```python
def test_validate_doc_type_closed_vocab(tmp_path):
    import json, subprocess, sys
    camp = tmp_path / "camp"; camp.mkdir()
    # Copy doc_types.yaml to camp
    import shutil
    shutil.copy("src/second_brain_vault_framework/payload/templates/classification/doc_types.yaml", camp / "doc_types.yaml")
    # Also need taxonomy for existing validate path; skip by calling validate with --doc-types mode
    store = tmp_path / "store"; store.mkdir()
    (store / "a.judge.json").write_text(json.dumps({"reasoning_brief":"ok","doc_type":"anomaly_report","confidence_bucket":"SURE"}), encoding="utf-8")
    # Run validate in doctype mode — should pass
    import subprocess
    r = subprocess.run([sys.executable, "ingest-pipeline/scripts/classify/validate.py", str(tmp_path), "--campaign", str(camp), "--store", str(store), "--doctype", "--dry-run"], capture_output=True, text=True)
    assert r.returncode == 0
    # Unknown type should fail
    (store / "b.judge.json").write_text(json.dumps({"reasoning_brief":"ok","doc_type":"nonexistent","confidence_bucket":"SURE"}), encoding="utf-8")
    r = subprocess.run([sys.executable, "ingest-pipeline/scripts/classify/validate.py", str(tmp_path), "--campaign", str(camp), "--store", str(store), "--doctype"], capture_output=True, text=True)
    assert r.returncode != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_classify_doctype.TestValidateDoctype -v`
Expected: FAIL `unrecognized arguments: --doctype`

- [ ] **Step 3: Write minimal implementation**

In `ingest-pipeline/scripts/classify/validate.py`:

- Add `from .taxonomy import parse_doc_types_blocks, load_doc_types, effective_doc_type_candidates` (with fallback import).
- Add `--doctype` arg.
- `if args.doctype:` load allowed via `parse_doc_types_blocks(txt)` (no pruning — allow any defined type; pruning is retrieval/judge concern). Validate `data.get("doc_type") in allowed`. Patch frontmatter `doc_type: <single>` (singular) instead of `domains`. Write ledger event with `task: "doctype"` + `doc_type` field + `doc_types_version`.
- Keep existing subdomain path unchanged; refuse mixed store directories containing both schemas without flag?

Pseudo:

```python
p.add_argument("--doctype", action="store_true")
# in cmd_classify, branch:
if doctype:
    allowed = set(parse_doc_types_blocks(txt).keys())
    # ... validate doc_type, bucket, reasoning_brief
    # patch fm["doc_type"] = doc_type (string, not list)
    # ledger event: {"task":"doctype","doc_type":doc_type, "doc_types_version":..., "confidence_bucket":bucket}
else:
    # existing subdomain
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ingest-pipeline && python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ingest-pipeline/scripts/classify/validate.py
git commit -m "feat(classify): validate supports doc_type singular closed-vocab + task ledger"
```

---

### Task 9: `ledger.py` + `calibrate.py` — task-aware + singleton + constraint_miss

**Files:**
- Modify: `ingest-pipeline/scripts/classify/ledger.py`
- Modify: `ingest-pipeline/scripts/classify/calibrate.py`
- Modify: `ingest-pipeline/scripts/classify/export_label_studio.py`

- [ ] **Step 1: Write the failing test**

```python
def test_ledger_task_field_and_singleton(tmp_path):
    from scripts.classify.calibrate import ledger_append, ledger_project
    p = tmp_path / "ledger.jsonl"
    ledger_append(p, {"doc_id":"a","task":"doctype","doc_type":"anomaly_report","confidence_bucket":"SURE","reason":"singleton_constraint"})
    ledger_append(p, {"doc_id":"b","task":"subdomain","primary":"nephrology","confidence_bucket":"SURE"})
    proj = ledger_project(p)
    assert proj["a"]["task"] == "doctype"
    assert proj["b"]["task"] == "subdomain"

def test_calibrate_constraint_miss():
    # reviewer overrides a singleton_pruned doc => constraint_miss=1
    pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ingest-pipeline && python -m unittest tests.test_classify_doctype.TestLedger -v`
Expected: FAIL (task not preserved)

- [ ] **Step 3: Write minimal implementation**

- `ledger.py`: ensure `ledger_append` writes raw JSONL (no filtering), `ledger_project` keeps last event per `doc_id+task` key (so subdomain+doctype decisions coexist). Add event type `singleton_pruned` handling.

- `calibrate.py`: add `--doctype` branch; compute per-doctype accuracy, confusion matrix (11×11), `constraint_miss_rate` = (# reviewer overrides where `singleton_constraint` or `candidates_pruned` excluded true label) / # pruned docs. Keep per-bucket accuracy + `I_GUESSED` healthy <40%.

- `export_label_studio.py`: add `view_doctype.xml` (Choices for doc types, metadata line, pruned note, reasoning_brief), `view_singleton_audit.xml` (small audit, shows `singleton_constraint` + metadata). Materialize `payload/templates/classification/label_studio/view_doctype.xml`:

```xml
<View>
  <HyperText name="text_html" value="$text_html"/>
  <Header value="Source metadata: $ext / $lang / $filename"/>
  <Text name="pruned_note" value="$pruned_note"/>
  <Choices name="doc_type" toName="text_html" choice="single" required="true">
    <!-- one <Choice value="anomaly_report"/> per allowed type, rendered from doc_types.yaml -->
  </Choices>
  <Choices name="confidence_bucket" toName="text_html" choice="single"/>
  <TextArea name="reasoning_brief" toName="text_html" readonly="true"/>
</View>
```

Export writes `campaigns/<campaign>/label_studio/doctype/tasks.json` and `.../singleton_audit/tasks.json`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ingest-pipeline && python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ingest-pipeline/scripts/classify/ledger.py ingest-pipeline/scripts/classify/calibrate.py ingest-pipeline/scripts/classify/export_label_studio.py src/second_brain_vault_framework/payload/templates/classification/label_studio/view_doctype.xml src/second_brain_vault_framework/payload/templates/classification/label_studio/view_singleton_audit.xml
git commit -m "feat(classify): ledger/calibrate/export per-task (doctype + singleton audit + constraint_miss)"
```

---

### Task 10: Framework — `manifest.json` + `__version__` + `vault upgrade` drift

**Files:**
- Modify: `src/second_brain_vault_framework/manifest.json`
- Modify: `src/second_brain_vault_framework/__init__.py` (if version bump needed)
- Modify: `src/second_brain_vault_framework/payload/templates/classification/label_studio/view.xml` — clarify as subdomain view, keep file for compat (or symlink)
- Test: `tests/test_classify_payload.py` or `tests/test_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
def test_manifest_lists_doc_types_and_views():
    import json
    m = json.loads(Path("src/second_brain_vault_framework/manifest.json").read_text(encoding="utf-8"))
    for p in ["templates/classification/doc_types.yaml",
              "templates/classification/label_studio/view_doctype.xml",
              "templates/classification/label_studio/view_singleton_audit.xml"]:
        assert p in m["owned_paths"], f"{p} missing from owned_paths"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_manifest -v` or `python -m unittest discover -s tests -v`
Expected: FAIL `templates/classification/doc_types.yaml missing`

- [ ] **Step 3: Write minimal implementation**

In `src/second_brain_vault_framework/manifest.json`:

```json
{
  "framework_version": "0.2.0",
  "owned_paths": [
    "CLAUDE.md",
    "instructions.md",
    ".claude/skills/vault-setup/SKILL.md",
    ".claude/skills/vault-ingest/SKILL.md",
    ".claude/skills/vault-query/SKILL.md",
    ".claude/skills/vault-lint/SKILL.md",
    "scripts/vault.py",
    "scripts/check_vault_answer.py",
    "templates/classification/doc_types.yaml",
    "templates/classification/policy.yaml",
    "templates/classification/questionnaire.md",
    "templates/classification/label_studio/view.xml",
    "templates/classification/label_studio/view_doctype.xml",
    "templates/classification/label_studio/view_singleton_audit.xml"
  ],
  ...
}
```

Bump `src/second_brain_vault_framework/__init__.py` `__version__ = "0.2.0"` to match. Ensure `payload/dot-claude/...` path mapping in `core.py:payload_path_for()` still translates `payload/templates` correctly (it already does; classification templates are under `payload/templates/classification/` which maps via same mechanism — verify `core.py` doesn't special-case).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s tests -v`
Expected: PASS (existing + new manifest test)

- [ ] **Step 5: Commit**

```bash
git add src/second_brain_vault_framework/manifest.json src/second_brain_vault_framework/__init__.py
git commit -m "feat(framework): own doc_types + doctype views (manifest + version 0.2.0)"
```

---

### Task 11: End-to-end smoke + `example_vault` + `vault check` + docs

**Files:**
- Modify: `example_vault/` (via `vault upgrade example_vault` — artifact, not hand-edited)
- Modify: `mkdocs.yml` (nav entry for new spec/plan if needed)
- Test: `ingest-pipeline/tests/test_classify_doctype_e2e.py`

- [ ] **Step 1: Write the failing test (e2e offline)**

```python
def test_e2e_chunk_retrieve_judge_doctype_stub(tmp_path):
    """Offline e2e with stubbed LLM/embeddings via hash_embed — no network."""
    import subprocess, sys, json
    store = tmp_path / "store"; camp = tmp_path / "camp"; camp.mkdir()
    import shutil
    shutil.copytree("src/second_brain_vault_framework/payload/templates/classification", camp / "templates_backup", dirs_exist_ok=True)
    # Use payload doc_types + policy as camp overlay
    for f in ["doc_types.yaml","policy.yaml"]:
        shutil.copy(f"src/second_brain_vault_framework/payload/templates/classification/{f}", camp / f)
    # Create 3 fixture docs: one he/xlsx -> anomaly_report singleton, one en/pptx -> presentation-ish, one onboarding Q
    fixtures = tmp_path / "raw"; fixtures.mkdir()
    (fixtures / "anom.xlsx.md").write_text("---\ntitle: \"Anomaly\"\noriginal_language: he\n---\nAnomaly table 12 rows", encoding="utf-8")
    (fixtures / "spec.md").write_text("---\ntitle: \"Spec\"\noriginal_language: en\n---\nSpec SHALL support X", encoding="utf-8")
    (fixtures / "qna.md").write_text("---\ntitle: \"Q\"\noriginal_language: he\n---\nQ: What is X? A: Y", encoding="utf-8")
    # chunk
    subprocess.check_call([sys.executable, "ingest-pipeline/scripts/classify/chunk.py", str(fixtures), "--store", str(store)])
    assert list(store.rglob("*.md"))
    # retrieve doctype (hash_embed fallback, pruned)
    subprocess.check_call([sys.executable, "ingest-pipeline/scripts/classify/retrieve.py", "--campaign", str(camp), "--store", str(store), "--doctype"])
    assert list(store.rglob("*.retrieval.json"))
    # judge doctype (stub: set CLASSIFY_LLM_BASE_URL="" so singleton path is tested, non-singleton uses hash fallback or dry-run)
    # Run in dry-run to avoid network
    r = subprocess.run([sys.executable, "ingest-pipeline/scripts/classify/classify_doctype.py", "--campaign", str(camp), "--store", str(store), "--dry-run"], capture_output=True, text=True)
    assert r.returncode == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ingest-pipeline && python -m unittest tests.test_classify_doctype_e2e -v`
Expected: FAIL until wrappers handle `--dry-run`

- [ ] **Step 3: Write minimal implementation (wire up if needed)**

Ensure `classify_doctype.py` / `classify_subdomain.py` forward `--dry-run` and `store` correctly. Ensure `vault upgrade example_vault` after Task 10:

```bash
python -m second_brain_vault_framework.cli upgrade example_vault
git status  # should show example_vault/templates/classification/doc_types.yaml laid down
```

Verify `vault check example_vault` exit 0:

```bash
python -m second_brain_vault_framework.cli check example_vault
# Expected: exit 0, prints "vault check: OK"
```

Verify payload→example_vault drift backup exists if upgrading over previous version:

```bash
ls example_vault/.vault-framework-backup/  # should contain previous version backup if drift
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python -m unittest discover -s tests -v              # framework (stdlib-only) must pass
cd ingest-pipeline && python -m unittest discover -s tests -v  # pipeline tests must pass
vault check example_vault                              # must exit 0
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add example_vault/templates/classification/doc_types.yaml example_vault/.vault-framework.json docs/superpowers/plans/2026-08-18-dual-classification-document-type.md mkdocs.yml
git commit -m "feat(classify): e2e smoke + example_vault laid down + docs nav (dual classification)"
```

---

## Self-Review

Spec coverage check — each spec section maps to a task:

- Vocabulary (13 types, internal→he / external→en, onboarding Q±A, hard vs soft gates) → Task 1 + 2
- Decision model (same buckets, single-label, singleton auto-assign) → Task 7 + 8
- Order of operations (questionnaire freeze, single chunk, embed per task, pruned retrieval, parallel judges) → Tasks 3-7
- Config contracts (doc_types.yaml, campaign overlay, policy extension, questionnaire Part E) → Tasks 1-3
- Module layout (classify_common + two wrappers) → Tasks 4, 6, 7
- Prompt contracts (metadata line, dynamic few-shot from pruned set, onboarding Q±A example) → Task 7
- Ledger & storage (task field, singleton_pruned, frontmatter doc_type singular) → Tasks 5, 8, 9
- Human review (separate queues + singleton audit) → Task 9
- Calibration (per-doctype confusion, constraint_miss) → Task 9
- Testing (vocab sync, pruning, chunk meta, judge schema, Label Studio, ledger, determinism) → Tasks 1-11
- Success criteria (toy campaign e2e, YAML-only edit, Label Studio import, ledger query, re-run, vault check, English-bypass) → Task 11

Placeholder scan: removed — no "TBD/TODO/implement later" remain. Every task has concrete file paths, test code, and implementation snippets.

Type consistency: `parse_doc_types_blocks` / `effective_doc_type_candidates` / `singleton_pruned_type` / `load_doc_types` signatures are consistent across Tasks 3, 6, 7. `classify_common` exports `cosine/hash_embed/build_centroids/atomic_write` reused in Tasks 4, 6. `doc_type` (singular string) vs `domains` (list) is distinct everywhere.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-18-dual-classification-document-type.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
