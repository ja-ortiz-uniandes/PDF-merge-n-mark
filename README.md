# PDF Combine & Mark

Combine multiple PDFs into a single document while preserving (and improving) navigation:

- Add a one-page Table of Contents (ToC) with clickable links.
- Add a top-level outline (bookmark) per input file using its filename or a custom `label`.
- Preserve each input PDF’s original outline, nested under its top-level label.
- Start every top-level outline collapsed so PDF viewers hide nested bookmarks until expanded.
- Optionally place certain PDFs before the ToC (pre-ToC). By default these are omitted from the ToC but still get a label in the outline.
- Fine-grained control over which items appear in the ToC and/or the outline via per-file manifest keys.
- Merge a whole folder in one command, in the order Windows Explorer shows it.

Powered by `pypdf` for PDF manipulation and `reportlab` for the ToC page.

## Installation

Requirements:

- Python `>= 3.13`

Install `pdfmerge` as a command available from any directory:

```pwsh
uv tool install --editable .
uv tool update-shell   # only if ~\.local\bin is not already on PATH
```

`--editable` means later edits to this repo take effect immediately, with no
reinstall. Adding a new *dependency* does need `uv tool install --editable . --force`.

For development inside the repo, `uv sync` installs everything including the
`dev` group (`mypy`, `pytest`):

```pwsh
uv sync
uv run pytest      # test suite
uv run mypy .      # type check
```

Every example below can also be run without installing, as
`uv run merge_pdf.py ...` from the repo directory.

### Updating

`pdfmerge` checks GitHub for a newer release at most once a day and, if it
finds one, prints a notice after the merge:

```text
pdfmerge 1.3.0 is available (you have 1.2.0).
  Update with: uv tool upgrade pdf-merge-n-mark
  Silence this with PDFMERGE_NO_UPDATE_CHECK=1
```

It never upgrades on its own, never blocks the merge, and never changes the
exit status. The check is skipped entirely when output is piped, so scripts and
CI logs stay clean, and pre-releases never trigger it.

## Quick Start

Merge every PDF in the folder you are standing in:

```pwsh
cd C:\Cases\Smith
pdfmerge --here
```

That writes `Smith.pdf` (the folder's own name), no ToC, files in Explorer's
order. Add `--toc` for a linked Table of Contents.

Merge specific files:

```pwsh
pdfmerge -o .\merged.pdf --toc ".\A.pdf" ".\B.pdf"
```

Or drive it from a manifest (YAML):

```yaml
output: "merged.pdf"
overwrite: true
toc: true
files:
  - file: "A.pdf"
    label: "Section A"
  - file: "B.pdf"
    label: "Section B"
```

```pwsh
pdfmerge -m .\manifest.yml
```

## Merging a Whole Folder

```pwsh
pdfmerge --here                    # all PDFs here -> <folder-name>.pdf, no ToC
pdfmerge --here --toc -f           # same, with a ToC, replacing a previous run
pdfmerge --here --toc --pre-toc cover.pdf   # cover first, then the ToC, then the rest
pdfmerge --here "C:\Cases\Smith" --toc      # another folder, no cd needed
```

- `--here` takes an optional folder; with none, it uses the current one.
- The scan is **not** recursive: only PDFs directly inside the folder.
- Order matches Windows Explorer's Name column, so `1 Intro.pdf`, `2 Body.pdf`,
  `10 Annex.pdf` merge in that order rather than `1, 10, 2`. (On non-Windows
  systems a close approximation is used.)
- The output file is never merged into itself, so re-running with `-f` is safe.
- **PDFs this tool produced earlier are skipped**, and it says which ones. A
  merged PDF is stamped in its metadata, so merging a folder twice under
  different output names does not fold the first result into the second. Pass
  `--include-merged` when you actually want that. Files named explicitly (in a
  manifest, as arguments, or via `--pre-toc`) are always merged regardless.
- `-o` overrides the folder-name default. **In folder mode, a relative path is
  relative to the folder being merged**, not to where you are standing, so
  `pdfmerge --here "C:\Cases\Smith" -o "Case 42.pdf"` writes
  `C:\Cases\Smith\Case 42.pdf`. The same goes for a bare `--pre-toc cover.pdf`.
  Absolute paths are used as given.
- `--here` cannot be combined with `-m` or with file arguments: those describe
  an explicit set of files.

### When the folder order is wrong

Write a manifest listing the folder, edit it, then merge:

```pwsh
pdfmerge --here --write-manifest   # -> manifest.yml, nothing merged
notepad manifest.yml               # reorder, relabel, delete entries
pdfmerge -m manifest.yml
```

`--write-manifest` takes an optional filename (`--write-manifest order.yml`) and
refuses to replace an existing file without `-f`. The manifest is written into
the folder being merged, beside the files it lists, so its entries stay short
and the folder can be moved as a unit.

## CLI Usage

`pdfmerge --help` is the full reference: every flag, every manifest key, and
worked examples. The notes below cover the parts worth spelling out.

Notes on `--pre-toc`:

- The flag accepts exactly one path per use (repeat the flag for multiple files).
- The file is merged by the flag alone: it does not need to be repeated as a positional input.
- Files passed via `--pre-toc` lead the document, in flag order. All other positional inputs are appended after the ToC page (when `--toc` is used).
- Naming a path that is also a positional input or a manifest entry merges it once: it keeps its manifest `label`, `toc` and `outline` settings and simply moves before the ToC. Its `toc` then defaults to `false` unless the manifest states it.

Precedence rules:

- CLI options override manifest options where applicable (e.g., `--toc`, `--toc-outline`, `-f`).
- The output path can come from CLI or manifest; CLI takes precedence.

### Alternative: a PowerShell function instead of installing

`uv tool install --editable .` (see [Installation](#installation)) is the
recommended route: it puts a real `pdfmerge` on PATH that works in PowerShell,
`cmd`, Git Bash and IDE terminals alike. If you would rather not install
anything, a PowerShell profile function gives the same command in PowerShell
only.

#### 1. Open your PowerShell profile

Your profile is a script that runs each time a new PowerShell session starts.

```ps
ni -Type File -Force $PROFILE
notepad $PROFILE
```

#### 2. Add the wrapper function

Paste this into the profile file, adjusting the path to where your project is located:

```ps
function pdfmerge {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    $proj   = "PATH_TO_PROJECT\PDF-merge-n-mark"
    $script = "$proj\merge_pdf.py"
    uv run --project $proj python $script @Arguments
}
```

Save and close the file.

#### 3. Reload the profile

```ps
. $PROFILE
```

#### 4. Use pdfmerge anywhere

Now you can run the tool from any directory. Example:

```ps
cd "C:\Users\YOUR_USERNAME\Documents\MyWork"
pdfmerge -m manifest.yml
```

- The code and dependencies come from the PDF-merge-n-mark project.
- The manifest.yml is resolved relative to your current directory.
- Any edits to your project code are picked up the next time you run the command (no reinstall required).

## Manifest Formats

You can use either a list-style or an object-style manifest.

### List-style (backward compatible)

YAML:

```yaml
- file: "A.pdf"
  label: "Section A"
- file: "B.pdf"
  label: "Section B"
```

JSON:

```json
[
  { "file": "A.pdf", "label": "Section A" },
  { "file": "B.pdf", "label": "Section B" }
]
```

Use CLI flags to provide global options like `-o`, `--toc`, `--pre-toc`, `-f` when using list-style.

### Object-style (with global options)

YAML:

```yaml
output: "merged.pdf" # or key: out
overwrite: true # overwrite if file exists
# ToC can be a boolean or an object
# - Boolean:   toc: true | false
# - Object:    toc: { enabled: true, outline: true }
#              toc: { outline: true }        # implies enabled
#              toc: { enabled: true }        # outline defaults to false

toc:
  outline: true # add ToC to outline (defaults to false)

files:
  - file: "pre.pdf"
    label: "Intro"
    pre_toc: true # placed before ToC; by default not in ToC, appears in outline

  - file: "A.pdf"
    label: "Section A"

  - file: "B.pdf"
    label: "Section B"
    toc: false # not listed in ToC (still merged)

  - file: "C.pdf"
    label: "Section C"
    outline: false # merged, appears in ToC (unless excluded), but no outline label
```

JSON:

```json
{
  "output": "merged.json.pdf",
  "overwrite": true,
  "toc": { "outline": true },
  "files": [
    { "file": "pre.pdf", "label": "Intro", "pre_toc": true },
    { "file": "A.pdf", "label": "Section A" },
    { "file": "B.pdf", "label": "Section B", "toc": false },
    { "file": "C.pdf", "label": "Section C", "outline": false }
  ]
}
```

### Per-file keys (manifest only)

- `file` (string): Path to the PDF.
- `label` (string | null): Top-level outline title for the file (defaults to filename stem). Alias: `bookmark`. (CLI-only merges use the filename stem; custom labels require a manifest.)
- `pre_toc` (bool): Place the file before the ToC. Default behavior: included in outline and omitted from ToC.
- `toc` (bool): Include this item in the ToC (defaults to true; if `pre_toc: true`, defaults to false). Must be a boolean here: the object form belongs to the top-level `toc` key.
- `outline` (bool): Include this item in the outline (defaults to true).

### Global keys (object-style manifest)

- `output`/`out` (string): Output PDF path.
- `overwrite` (bool): Overwrite output if it exists.
- `toc` (bool | object): Controls the ToC.
  - Boolean: `true` or `false`.
  - Object: `{ enabled?: bool, include?: bool, outline?: bool }`.
    - `enabled` or `include` turns ToC on/off. If the object is provided with neither, ToC is treated as enabled.
    - `outline` adds a top-level outline entry for the ToC page (defaults to `false`).

## Behavior & Details

- Pre-ToC files are inserted first, in order. By default they are not listed in the ToC and they get a top-level outline label.
- Default pre-ToC behavior: pre-ToC items are omitted from the ToC unless `toc: true` is set for that item; they still receive an outline label unless `outline: false`.
- The ToC (if enabled) is inserted immediately after pre-ToC items and before normal files. It’s a single page with clickable links to included items.
- The ToC is exactly one page, so it holds a limited number of entries (40 on US Letter). Asking for more is an error rather than a silent truncation; exclude entries with `toc: false` to fit.
- ToC page numbers are 1-based; pre-ToC entries (when included) count from 1; normal entries account for pre-ToC pages + the ToC page.
- A top-level outline label is created per input (unless excluded), and each input’s original outline is preserved under that label with original view/fit behavior (`/XYZ`, `/FitH`, `/FitV`, `/FitR`, `/Fit`, `/FitB`, `/FitBH`, `/FitBV`). These newly created top-level labels start collapsed by default so you can expand only the sections you need.
- The ToC itself can have a top-level outline entry when `toc.outline: true` (or CLI `--toc-outline`).
- Encrypted PDFs: the tool attempts to open them with an empty password; if that fails, you’ll get an error.
- Precedence: CLI flags override manifest values.

## Examples

1. CLI only, with ToC, pre-ToC items, and ToC outline entry (repeat `--pre-toc` for each pre-ToC file):

```pwsh
pdfmerge -o .\merged.pdf --toc --toc-outline `
  --pre-toc .\intro.pdf `
  --pre-toc .\license.pdf `
  .\A.pdf .\B.pdf
```

1. YAML manifest with ToC outline entry:

```yaml
output: "merged.pdf"
overwrite: true
toc:
  outline: true
files:
  - file: "pre.pdf"
    label: "Intro"
    pre_toc:
      true
      # defaults here: outline: true, toc: false (set toc: true to include pre-ToC in ToC)
  - file: "A.pdf"
    label: "Section A"
  - file: "B.pdf"
    label: "Section B"
```

1. JSON manifest without ToC outline entry (default):

```json
{
  "output": "merged.pdf",
  "overwrite": true,
  "toc": true,
  "files": [
    { "file": "A.pdf", "label": "Section A" },
    { "file": "B.pdf", "label": "Section B" }
  ]
}
```

## CLI vs Manifest: What goes where?

- CLI (global/document-level):
  - `-o/--output`, `-f/--force`, `--toc`, `--toc-outline`, `--pre-toc` (repeat per file)
- Manifest (document + per-file):
  - Global: `output`/`out`, `overwrite`, `toc` (bool or object with `enabled/include` and `outline`)
  - Per-file: `file`, `label`, `pre_toc`, `toc`, `outline`

Defaults summary:

- For any file (CLI or manifest):
  - `label`: defaults to filename (without extension)
  - `outline`: defaults to `true`
  - `toc`: defaults to `true`, except when `pre_toc: true` then defaults to `false`

## License

MIT - see [`LICENSE`](LICENSE). Use it, change it, ship it commercially; just
keep the copyright notice. Dependencies are permissive too (`pypdf` BSD-3-Clause,
`reportlab` BSD, `PyYAML` MIT), so nothing here is copyleft.

## Troubleshooting

- If you see an error about the output file existing, add `-f` or set `overwrite: true` in the manifest.
- If ToC links don’t appear, ensure `reportlab` is installed and you passed `--toc` or `toc: true`.
- If a PDF is encrypted, supply a decrypted copy or remove it from the manifest. Only an empty password is attempted.
- If the merge stops with “Table of Contents does not fit on one page”, drop entries with `toc: false` or turn the ToC off.

## Installation & Running (recap)

```pwsh
# Install the pdfmerge command (Python >= 3.13)
uv tool install --editable .

# Merge the folder you are standing in
pdfmerge --here --toc

# Explicit files
pdfmerge -o .\merged.pdf --toc .\A.pdf .\B.pdf

# From a manifest
pdfmerge -m .\manifest.yml

# Full reference
pdfmerge --help
```

Without installing, from the repo directory: `uv sync`, then
`uv run merge_pdf.py ...` in place of `pdfmerge ...`.
