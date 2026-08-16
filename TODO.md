# TODO

Known-but-unimplemented work. Items are in priority order. Usage and design
background is in `README.md` and `CLAUDE.md`; the intent here is to record *why*
each one matters so it can be picked up later without re-deriving the reasoning.

When an item is finished, delete it (or the finished part of it) instead of
marking it done, this file should only ever list outstanding work. But
"finished" means verified working, not just changed: making the edit is not
enough, confirm it actually does the thing before removing the item. Delete only
once verified, in the same commit as the last of the work, and put how it was
verified in that commit's message (what was checked, and the result), this file
is not the place to keep that record.

## 1. Publish to winget

`pdfmerge` currently reaches people who already have `uv` or Python:

```pwsh
uv tool install git+https://github.com/ja-ortiz-uniandes/PDF-merge-n-mark
```

That is the entire audience. A Windows user who wants to merge a folder of PDFs
and has no Python toolchain has no path in at all, and installing Python to get
a PDF utility is a fair thing to refuse. winget is how that user expects to
install anything, and the tool is Windows-first by design: `--here` orders files
with `StrCmpLogicalW` specifically so the merge order matches the Explorer
listing they are looking at. The natural audience is exactly the one that cannot
currently install it.

Submission itself is free. Fork `microsoft/winget-pkgs`, add the manifest YAML
(or generate it with `wingetcreate`), open a PR, an automated pipeline validates
it and a moderator reviews. No signup fee, no verification cost, no certificate
demanded.

**The blocker is the artifact, not the paperwork.** winget installs `.msi`,
`.exe`, `.msix`, or a portable zip/exe. It has no concept of a Python wheel, and
a wheel is all the release workflow currently produces. So the work is:

- **Build a standalone executable with PyInstaller in CI**, attached to each
  release alongside the existing wheel and sdist. This is the real cost, and the
  cost is `reportlab`: it ships font and data files that PyInstaller does not
  pick up automatically, so expect to fight `--add-data` and hidden imports
  until a one-file build actually renders a ToC page. Budget for that rather
  than assuming a clean first build. Verify by running the built `.exe` on a
  machine with no Python at all, not just on this one, where a working
  interpreter can mask a missing bundled dependency.
- **Submit the manifest** as `AlejandroOrtiz.PdfMergeNMark` (winget's identifier
  convention is `Publisher.Package`), pointing at the release asset URL. Release
  assets are stable public URLs, so they qualify as installer sources.
- **Automate the per-release manifest update**, a `wingetcreate update` step in
  `.github/workflows/release.yml`. Without it every future tag needs a manual PR
  against `winget-pkgs`, and the winget listing silently rots one release behind
  while the update notice tells people about a version they cannot get.

Decided: **portable installer type**, not an MSI. The tool is a single
executable with no registry keys, no services and no uninstall state worth
tracking, so an MSI would be ceremony around a file copy. Portable also keeps
the packaging in the same workflow that already builds and publishes.

Two consequences worth knowing before starting rather than after:

- **SmartScreen will warn on the unsigned `.exe`** until the binary builds
  download reputation. winget does not require code signing, but early users see
  a scary blue dialog. A code-signing certificate removes it and is the only
  part of this that costs money (roughly $200-400/yr for OV). Not worth buying
  speculatively, worth reconsidering if the package gets real traffic and the
  warning is visibly costing installs.
- **The update notice assumes `uv tool upgrade`.** `pdf_combine/updates.py`
  prints that command unconditionally, which is wrong for a winget install,
  where the right answer is `winget upgrade`. Decide then whether to detect the
  install source or to soften the wording. Do not ship a winget package that
  tells people to run a command their install cannot use.

Not urgent. The tool works, it is installable today for its current audience,
and nothing degrades while this sits. Bring it forward if someone actually asks
for a no-Python install, or if the PyInstaller build turns out to be cheap
enough that the winget PR is the only real work left.
