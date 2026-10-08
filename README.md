# PDF Title Renamer

A small, offline Python command-line tool to suggest readable filenames for PDF documents using their embedded title metadata or page text. It never uploads a file, requires no API key, and **does not modify files unless you explicitly pass `--apply`**.

## Requirements and installation

Python 3.10+ and [PyMuPDF](https://pymupdf.readthedocs.io/). From the repository directory:

```sh
python -m pip install -e .
```

## Usage

```sh
pdf-renamer ~/Documents/Papers            # preview a folder and subfolders
pdf-renamer ./my-paper.pdf                  # preview a single file
pdf-renamer ~/Documents/Papers --apply    # perform renames after review
pdf-renamer . --no-recursive              # current folder only
pdf-renamer . --json                      # machine-readable preview
python rename.py .                        # no installation of CLI entry point required
```

By default, relative paths are resolved from your working directory. Multiple folders and files may be supplied. `.PDF` files are included. Duplicate inputs are processed once.

### Safety behavior

- **Dry run by default.** No files are changed without `--apply`.
- File contents stay local. The tool never deletes or rewrites a PDF's content.
- Existing filenames are reserved, with ` (2)`, ` (3)`, etc. added to avoid collisions, including case-insensitive collisions. Files already named correctly are not changed.
- Actual renames use a same-directory, exclusive hard-link followed by removal of the original filename. This avoids overwriting an existing destination even if it appears after preview/planning. If your filesystem does not support hard links or you lack permission, that file reports an error instead of falling back to a dangerous overwrite. If interrupted in between these steps, both names may temporarily exist; the original data remains intact.
- Symbolic links to PDF files are ignored, and symlink inputs are rejected.
- Windows-invalid characters, reserved device names, trailing punctuation, Unicode normalization, and filename byte budgets are handled.

### Title extraction and limitations

The utility first checks PDF title metadata. If missing or obviously generic, it looks for prominent large text near the top of the first page, then tries the first plausible text line from up to three pages. Extracted-text suggestions can be imperfect: **always inspect the preview**, especially with academic papers, journal headers, multi-column documents, or scanned material.

The tool does **not** perform OCR, translate titles, call AI models, rename password-protected files, or guarantee semantic accuracy. Image-only/scanned documents without usable metadata are skipped. No files are uploaded or sent to external services.

### Exit codes

- `0`: completed (including previews and skipped files).
- `1`: at least one attempted rename failed.
- `2`: invalid command arguments, missing paths, or directory scanning error.

## Development

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
```

See `.github/workflows/ci.yml` for the automated test matrix.
