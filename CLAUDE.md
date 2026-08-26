# CLAUDE.md — Monorepo

This repo holds **two independent products**. Neither is a vault; a vault is a user-owned
folder the framework writes into, and `example_vault/` is the one vault that lives here, as
living documentation.

| | **second_brain_vault_framework** | **ingest-pipeline** |
|---|---|---|
| what | `vault` CLI + the payload laid into vaults | 4-stage Hebrew document pipeline |
| deps | `pyproject.toml` | `ingest-pipeline/requirements.txt` (~11 packages) |
| platform | cross-platform | **Windows only** (D1) |
| tests | `tests/` | `ingest-pipeline/tests/` |
| CI lane | `unit` | `pipeline-unit` |

**They do not import each other.** The pipeline never imports
`second_brain_vault_framework`, and the framework has no knowledge that the pipeline
exists — nothing in `manifest.json` refers to it and no vault owner ever sees it. That
separation is deliberate and load-bearing; integration will be designed later.

> Design specs: `docs/superpowers/specs/` · Plans: `docs/superpowers/plans/`
> Vault schema (for work *inside* a vault): `example_vault/CLAUDE.md`
> Pipeline state and open work: `HANDOFF.md`

## Layout

```
docs/                              mkdocs site — user guide + specs/plans (both products)
example_vault/                     a real vault, built by the current payload (CI-verified)
qmd-api/                           staging + install scripts for the qmd OpenAI-backend fork
src/second_brain_vault_framework/  the pip package
  ├── core.py                      every subcommand's implementation
  ├── cli.py                       `vault` entry point (argparse only)
  ├── manifest.json                what the framework owns in a vault + user-zone markers
  └── payload/                     the files laid down into a vault
tests/                             unittest suite for the package
ingest-pipeline/                   the other product — no framework imports
  ├── scripts/                     stages 3-6
  ├── tests/                       its own suite, its own deps
  ├── templates/                   planning/ questionnaire + classification/ taxonomy
  ├── skills/                      vault-classify (not shipped in the payload)
  ├── data/                        glossary, person names, translation policy
  └── requirements.txt             the pipeline's dependency surface
```

## The one rule that explains the layout

**`payload/` is the source of truth; `example_vault/` is an artifact.**

Never edit a framework-owned file inside `example_vault/` directly. Edit it in
`src/second_brain_vault_framework/payload/`, then re-lay it:

```bash
vault upgrade example_vault
```

`tests/test_boundary.py::TestExampleVaultIsCurrent` enforces this for every path in
`owned_paths`: it upgrades a temp copy of `example_vault/` and fails if anything would change.
An un-laid-down edit to an owned file is a broken build, and it fails in the suite — on your
laptop, not in a consumer's repo.

**Two payload files are exceptions**, because `cmd_upgrade` never re-lays them:
`tests/VAULT_TESTS.md` (`scaffold_only_paths`) and `gitignore` (written directly by
`cmd_scaffold`). Editing either does **not** reach existing vaults, and `vault upgrade` will
not fix a stale one — only a fresh `vault scaffold` picks the change up.
`test_every_payload_file_is_claimed_by_the_manifest` keeps that exception list explicit.

> This was previously documented as CI-enforced and was not. The `example-vault` job ran
> `git diff --exit-code` **without ever running `vault upgrade`**, so on a fresh checkout the
> diff was always empty. The rule went unenforced for as long as it has been written down.

Which paths are framework-owned is defined in `manifest.json` — nowhere else. Adding a file
to the payload means adding it to `owned_paths` too, or `upgrade` will never install it.

## Path mapping

`payload/dot-claude/` → `.claude/` in the vault. The rename exists because packaging backends
skip dot-directories; `core.payload_path_for()` is the only place that translation happens.

## Conventions

- **Fail closed.** `vault check` exits non-zero on any finding. Never soften it to a warning.
- **`tests/VAULT_TESTS.md` in a vault is never a qmd collection** — gold answers must not
  contaminate retrieval. `core.cmd_register` deliberately omits it.
- Version lives in `pyproject.toml` and `__init__.__version__`; `manifest.json` carries a
  copy for the vault stamp. Bump all three together.

## Before calling work done

Framework:

```bash
python -m unittest discover -s tests
```

```bash
vault check example_vault
```

Pipeline (needs `ingest-pipeline/requirements.txt` installed; YAP-dependent tests skip
cleanly without `YAP_DIR`):

```bash
cd ingest-pipeline && python -m unittest discover -s tests
```

A change touching both products has to pass both.
