# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Managed with `uv` (Python >= 3.13, pinned in `.python-version`).

```pwsh
uv sync                                              # install deps (incl. the dev group)
uv run merge_pdf.py -o .\merged.pdf --toc .\A.pdf .\B.pdf   # CLI merge
uv run merge_pdf.py -m .\manifest.yml                # manifest merge
uv run pytest                                        # test suite
uv run pytest tests/test_outline.py::test_nested_collapsed_state_survives_the_merge  # single test
uv run mypy .                                        # type check (must stay clean)
uv tool install --editable . --force                 # refresh the global `pdfmerge`
```

There is no lint config. `main.py` is a leftover `uv init` stub, not an entry point.

The shipped command is `pdfmerge`, declared as `[project.scripts] pdfmerge = "merge_pdf:cli"` and installed with `uv tool install --editable .`. Because it is editable, working-tree edits apply with no reinstall; a **new dependency** does need the `--force` reinstall above. Note the tool's own environment resolves deps fresh rather than from `uv.lock`, so it can run a newer `pypdf` than the tests do - worth a `uv run --with 'pypdf==<newer>' pytest` when that matters.

`merge_pdf.py` has two entry points: `main(argv=None)` raises on failure and is what tests call, while `cli()` wraps it to print `pdfmerge: <message>` and exit 1. Console users must never see a traceback.

Tests generate their own PDFs with reportlab (`tests/conftest.py`); no binary fixtures are committed. `merge_pdf.main()` takes an optional `argv`, so tests drive the real CLI rather than calling `merge_pdfs` directly.

To smoke-test against real files, copy `manifest.yml.template` to a `manifest.yml`, point it at real PDFs, and run the manifest merge.

## Architecture

Single-purpose CLI: merge N PDFs into one, adding a generated Table of Contents page and a nested outline (bookmarks) that preserves each input's own outline.

Two modules:

- `merge_pdf.py` - CLI parsing, manifest loading, page layout/ordering, ToC page generation and link annotations, final write.
- `pdf_combine/naming.py` - filename ordering. `sort_like_explorer` borrows `StrCmpLogicalW` from `shlwapi.dll` via `ctypes`, the same function Explorer uses for its Name column, so `--here` merges in the order the user sees in the folder. A natural-sort fallback covers non-Windows.
- `pdf_combine/processing.py` - **all** outline handling: destination/fit resolution, footnote filtering, per-input rebuilding (`rebuild_outline_under_parent`), re-importing into the master document (`import_outline_from_reader`), and `/Count` recalculation (`update_outline_counts`). `merge_pdf.py` must not grow its own copy of any of this; an earlier fork of these helpers silently diverged and discarded the collapsed-state handling.

### Merge pipeline (`merge_pdfs` in `merge_pdf.py`)

1. Inputs are split into `pre_tocs` (`pre_toc: true`) and `normals`.
2. If ToC is on, `_collect_toc_entries` opens every input **up front** just to count pages, so ToC page numbers are known before any page is written. Pre-ToC entries number from 1; normal entries are offset by `pre_toc pages + 1 ToC page`. Readers are cached per path (`reader_for`) and reused by the merge pass.
3. Each input is passed through `rebuild_outline_under_parent`, which returns an in-memory `PdfWriter` holding that file's pages plus its original outline re-parented under one top-level label. That writer is serialized to a `BytesIO`, re-read as a `PdfReader`, and only then are its pages appended to the master writer and its outline re-imported (`import_outline_from_reader`) with a page offset. **Do not "optimize" this round-trip away**: it is what makes the rebuilt destinations resolvable, and pypdf strips its internal `/%is_open%` marker on write, so collapsed state only crosses the boundary as the sign of `/Count`.
4. The ToC page is generated with `reportlab` (`_build_toc_pdf`) and inserted after pre-ToC items. `_build_toc_pdf` and `_add_toc_links` both lay rows out via `_toc_row_positions`, driven by the module-level `TOC_*` constants - keep it that way, or the clickable rectangles drift off the text. `_toc_capacity` bounds the single page; overflowing it raises rather than silently dropping entries.
5. `update_outline_counts` runs once on the master writer at the end, restating `/Count` across the whole tree so viewers honour the collapsed state.

### Outline handling

Everything lives in `pdf_combine/processing.py`. Two facts about pypdf drive its shape:

- `BooleanObject` does not implement `__bool__`, so `bool(BooleanObject(False))` is `True`. The `/%is_open%` marker must be compared by value (`is_outline_node_open`), never passed to `bool()`. pypdf's own tree code carries the same warning.
- `/%is_open%` is dropped when a document is serialized, so open/closed state survives only through the sign of `/Count`, which `update_outline_counts` recomputes. A collapsed node's `|/Count|` counts everything that would become visible on reopening - direct children plus the visible descendants of any child that is itself open, per PDF 32000-1 §12.3.3.

`import_outline_from_reader` defaults `skip_footnotes=False` while `rebuild_outline_under_parent` uses `True`. Re-running the footnote heuristic on an already-processed document would drop top-level labels that merely look numeric (a file named `12.pdf`).

Supported destination forms, in resolution order: `/Dest` (named or array), `/A` GoTo action with `/D`, explicit `/Type` + `/Page` dict, bare `/Page`. Fit types `/Fit`, `/FitB`, `/FitH`, `/FitBH`, `/FitV`, `/FitBV`, `/XYZ`, `/FitR` are preserved with their argument arity normalized.

### Options and precedence

Options can come from CLI flags or from a manifest (`.json`, `.yml`, `.yaml`); CLI wins. The manifest accepts two shapes: a bare list of file entries, or an object with `output`/`out`, `overwrite`, `toc` (bool or `{enabled|include, outline}`) and a `files` (or `inputs`) list. Manifest paths are resolved relative to the manifest's own directory; CLI paths relative to the cwd.

Per-file keys: `file`, `label` (alias `bookmark`), `pre_toc`, `toc`, `outline`. Legacy aliases `in_toc`/`toc_exclude` and `in_outline`/`outline_exclude` are still accepted. Defaults: `label` = filename stem, `outline` = true, `toc` = true except when `pre_toc` is true (then false). Custom labels are manifest-only; CLI merges always use the filename stem.

There are three ways to name inputs, and they are mutually exclusive by design: `--here` (every PDF directly in a folder, Explorer order, via `find_pdfs`/`_inputs_from_directory`), a manifest, or positional arguments. `--here` takes an optional directory (`nargs="?"`, `const="."`), so `args.here` is `None` when absent - test it with `is not None`, never for truthiness, since `"."` and a path are both truthy. In folder mode every relative path (`-o`, `--pre-toc`, `--write-manifest`) resolves against that directory rather than the cwd, which is what makes `pdfmerge --here C:\Cases\Smith -o "Case 42.pdf"` land beside the inputs. The output defaults to `default_output_for(dir)` - the folder's own name - and its resolved path is excluded from the scan so a re-run never merges its own output.

Because `--here` takes an optional value, `pdfmerge --here A.pdf` reads `A.pdf` as the directory and fails with `NotADirectoryError`; the mutual-exclusion error needs an explicit directory first (`--here . A.pdf`). `--here --write-manifest` scans identically but calls `write_manifest` and returns without merging; the scaffold is hand-rendered YAML so it can carry explanatory comments, and a test asserts it round-trips through `_load_manifest`.

**Every flag must be documented in `--help`.** `build_parser()` carries the description, per-flag help and the `EPILOG` (manifest keys plus worked examples); `tests/test_here.py` walks the parser's actions and fails if an option has no help string or a manifest key is missing from the epilog. `README.md` deliberately points at `pdfmerge --help` rather than restating the flag list, so there is one place to update.

`--pre-toc PATH` both merges the file and marks it pre-ToC. `_combine_inputs` reconciles the three sources (pre-ToC flags, manifest, positional) by `normcase`d resolved path, so a file named twice is merged once and keeps its manifest settings. `InputItem.toc_explicit` records whether the manifest stated `toc`, which is what decides if promoting an item to pre-ToC may flip `toc` to false.

`README.md` documents the full user-facing option surface and is the place to update when flags or manifest keys change.

## Releasing

Pushing a tag matching `v*` runs `.github/workflows/release.yml`: tests on Windows and Linux, then `uv build` and `gh release create` with auto-generated notes and the wheel/sdist attached. A tag containing a hyphen (`v1.2.0-rc1`) publishes as a pre-release.

The workflow **fails the release if the tag does not match `version` in `pyproject.toml`** (tag minus its leading `v`). So bump the version, commit, then tag. Use the full `vMAJOR.MINOR.PATCH` form: the repo's original `v1.0` tag predates this rule and would not satisfy it.

## Repo gotchas

- `.gitignore` ignores `*.json`, `*.yml`, `*.yaml` and `*.pdf`, with negations for `tests/**` and `.github/**` (without that second negation the release workflow itself would be invisible to git). A real `manifest.yml` and any sample PDFs are untracked by design (hence `manifest.yml.template`). Anything under `tests/` is trackable; check `git status` after adding a fixture with one of those extensions elsewhere.
- `.codegraph/` holds a local CodeGraph index; it is untracked and rebuildable with `codegraph init`.
- Encrypted inputs are opened with an empty password by `_open_reader`, which inspects `decrypt`'s **return value** - pypdf signals a wrong password with `PasswordType.NOT_DECRYPTED` rather than by raising.
- pypdf writes `Link` destinations as a bare page number (`[2 /Fit]`) instead of an indirect page reference. Viewers accept it, but it is why the test helper resolving link targets handles both forms.
