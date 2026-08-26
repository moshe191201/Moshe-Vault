# CLAUDE.md — package internals

Scope: `src/second_brain_vault_framework/`. Repo-wide rules are in the root `CLAUDE.md`.

## Module boundaries

| Module | Holds | Must not |
|--------|-------|----------|
| `cli.py` | argparse wiring, one dispatch line per subcommand | contain behavior — no file I/O, no logic |
| `core.py` | every `cmd_*` implementation, the lint, the manifest/upgrade machinery | — |
| `manifest.json` | `owned_paths` + `user_zones` | list content paths (`raw/`, `wiki/`, `index/`) |
| `payload/` | verbatim files laid into a vault | contain anything generated at build time |

`core` never imports `cli`. `__init__` exposes `__version__` and re-exports `core`.

## Adding a payload file

1. Add it under `payload/` (`.claude/…` goes to `payload/dot-claude/…`).
2. Add its **vault-relative** path to `owned_paths` in `manifest.json`.
3. `vault upgrade example_vault` and commit the result.

Skipping step 2 means `scaffold` and `upgrade` silently ignore the file.

## The upgrade contract

`cmd_upgrade` runs in a fixed order, and the order is the contract:

1. **extract** user zones from the on-disk copies
2. **back up** any owned file whose bytes differ from the payload → `.vault-framework-backup/<old-version>/`
3. **re-lay** the payload, re-injecting the extracted zones
4. **delete** paths the *previous* stamped manifest owned that this version no longer ships
5. **stamp** `.vault-framework.json`

Step 4 reads the vault's stamped manifest, never the filesystem — that is what makes it
impossible for the delete pass to touch content. Keep it that way.

A missing stamp means "unknown previous version": everything is treated as drifted and
backed up before being overwritten. Never make a missing stamp a hard error — hand-made
vaults predate stamping and must still upgrade cleanly.

## Templating

`render_template` does `{{VAR}}` string substitution — deliberately not a template engine,
so payload files stay readable as their own final form. `{{VAULT_NAME}}` is substituted on
`scaffold` only; `upgrade` leaves an already-rendered vault's name alone.
