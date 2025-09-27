# PDF Combine & Mark

Combine multiple PDFs into a single document while preserving (and improving) navigation:

- Add a one-page Table of Contents (ToC) with clickable links.
- Add a top-level outline (bookmark) per input file using its filename or a custom `label`.
- Preserve each input PDF’s original outline, nested under its top-level label.
- Start every top-level outline collapsed so PDF viewers hide nested bookmarks until expanded.
- Optionally place certain PDFs before the ToC (pre-ToC). By default these are omitted from the ToC but still get a label in the outline.
- Fine-grained control over which items appear in the ToC and/or the outline via per-file manifest keys.

Powered by `pypdf` for PDF manipulation and `reportlab` for the ToC page.

## Installation

Requirements:

- Python `>= 3.13`

Install dependencies:

```pwsh
uv sync
```

Dependencies (from `pyproject.toml`): `pypdf`, `pyyaml`, `reportlab`, `mypy` (dev).

## Quick Start

Minimal CLI merge (two or more files):

```pwsh
uv run merge_pdf.py -o .\merged.pdf --toc ".\A.pdf" ".\B.pdf"
```

Using a manifest (YAML):

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

Run:

```pwsh
uv run merge_pdf.py -m .\manifest.yml
```

## CLI Usage

```text
merge_pdf.py [-o OUTPUT] [-f] [-m MANIFEST] [--toc] [--toc-outline]
             [--pre-toc PATH] [--pre-toc PATH] ...
             [inputs ...]
```

- `-o, --output` (required unless provided by manifest): Path to the output PDF.
- `-f, --force`: Overwrite the output if it already exists.
- `-m, --manifest`: Path to a manifest (`.json`, `.yml`, `.yaml`).
- `--toc`: Prepend a one-page Table of Contents.
- `--toc-outline`: Also add a top-level outline entry pointing to the ToC page (off by default).
- `--pre-toc <path>`: Mark a file to be placed before the ToC (pre-ToC). Use one flag per file; repeat the flag for multiple files.
- `inputs`: Additional input PDFs in order.

Notes on `--pre-toc`:

- The flag accepts exactly one path per use (repeat the flag for multiple files).
- Only files passed via `--pre-toc` are placed before the ToC. All other positional inputs are appended after the ToC page (when `--toc` is used).

Precedence rules:

- CLI options override manifest options where applicable (e.g., `--toc`, `--toc-outline`, `-f`).
- The output path can come from CLI or manifest; CLI takes precedence.

### Running `pdfmerge` Globally on Windows (PowerShell)

You can set up a PowerShell function so that the `pdfmerge` command is available in any directory. This allows you to keep the code inside the project, always run the latest version, and pass input files (like `manifest.yml`) from your current working folder.

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
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
    $proj   = "PATH_TO_PROJECT\PDF-merge-n-mark"
    $script = "$proj\merge_pdf.py"
    uv run --project $proj python $script @Args
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
- `toc` (bool): Include this item in the ToC (defaults to true; if `pre_toc: true`, defaults to false).
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
- ToC page numbers are 1-based; pre-ToC entries (when included) count from 1; normal entries account for pre-ToC pages + the ToC page.
- A top-level outline label is created per input (unless excluded), and each input’s original outline is preserved under that label with original view/fit behavior (`/XYZ`, `/FitH`, `/FitV`, `/FitR`, `/Fit`, `/FitB`, `/FitBH`, `/FitBV`). These newly created top-level labels start collapsed by default so you can expand only the sections you need.
- The ToC itself can have a top-level outline entry when `toc.outline: true` (or CLI `--toc-outline`).
- Encrypted PDFs: the tool attempts to open them with an empty password; if that fails, you’ll get an error.
- Precedence: CLI flags override manifest values.

## Examples

1. CLI only, with ToC, pre-ToC items, and ToC outline entry (repeat `--pre-toc` for each pre-ToC file):

```pwsh
uv run merge_pdf.py -o .\merged.pdf --toc --toc-outline \
  --pre-toc .\intro.pdf \
  --pre-toc .\license.pdf \
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

## Troubleshooting

- If you see an error about the output file existing, add `-f` or set `overwrite: true` in the manifest.
- If ToC links don’t appear, ensure `reportlab` is installed and you passed `--toc` or `toc: true`.
- If a PDF is encrypted, supply a decrypted copy or remove it from the manifest.

## Installation & Running (recap)

```pwsh
# Install dependencies (Python >= 3.13)
uv sync

# Run with CLI only
uv run python .\merge_pdf.py -o .\merged.pdf --toc .\A.pdf .\B.pdf

# Run with a manifest
uv run python .\merge_pdf.py -m .\manifest.yml
```
