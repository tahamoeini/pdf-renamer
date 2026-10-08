"""Local, review-first PDF title renamer.

The command only changes files when --apply is explicitly supplied.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import fitz

MAX_TITLE_BYTES = 160
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
WHITESPACE = re.compile(r"\s+")
WINDOWS_DEVICES = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I)
BAD_TITLES = {
    "untitled", "title", "paper title", "document", "document1",
    "microsoft word", "draft", "abstract", "introduction",
    "table of contents", "contents", "cover page", "scan",
}
BAD_LINE = re.compile(
    r"^(?:https?://|www\.|doi\s*[:.]|10\.\d{4,9}/|arxiv\s*[:.]|"
    r"abstract\b|keywords?\b|introduction\b|references\b|"
    r"copyright\b|all rights reserved\b|page\s+\d+\b)", re.I
)
EMAIL = re.compile(r"\b\S+@\S+\.\S+\b")


@dataclass
class Proposal:
    source: Path
    target: Path | None
    status: str
    reason: str
    title_source: str | None = None

    def as_json(self) -> dict[str, str | None]:
        return {
            "source": str(self.source),
            "target": str(self.target) if self.target else None,
            "status": self.status,
            "reason": self.reason,
            "title_source": self.title_source,
        }


def normalize_text(text: str) -> str:
    """Keep international writing systems while removing control characters."""
    normalized = unicodedata.normalize("NFKC", str(text))
    normalized = "".join(
        " " if ch.isspace() else ch
        for ch in normalized
        if ch.isspace() or unicodedata.category(ch) not in {"Cc", "Cf", "Cs"}
    )
    return WHITESPACE.sub(" ", normalized).strip()


def plausible_title(text: str) -> bool:
    value = normalize_text(text).strip(" .-_:;")
    is_cjk = any("CJK" in unicodedata.name(ch, "") or "HANGUL" in unicodedata.name(ch, "") for ch in value)
    if len(value) < (2 if is_cjk else 5) or len(value) > 240:
        return False
    if value.casefold() in BAD_TITLES or BAD_LINE.search(value) or EMAIL.search(value):
        return False
    if re.fullmatch(r"[\d\W_]+", value, re.UNICODE):
        return False
    if value.casefold().startswith(("microsoft word -", "adobe indesign", "powerpoint")):
        return False
    return True


def sanitize_filename(title: str, max_bytes: int = MAX_TITLE_BYTES) -> str:
    """Produce a portable PDF stem within a UTF-8 byte budget."""
    value = normalize_text(title)
    value = INVALID_CHARS.sub("-", value)
    value = WHITESPACE.sub(" ", value).strip(" .-_")
    if WINDOWS_DEVICES.fullmatch(value):
        value = f"_{value}"
    if value in {"", ".", ".."}:
        return ""
    if len(value.encode("utf-8")) <= max_bytes:
        return value
    # Keep a readable prefix without breaking UTF-8 or leaving punctuation.
    prefix: list[str] = []
    used = 0
    for char in value:
        length = len(char.encode("utf-8"))
        if used + length > max_bytes:
            break
        prefix.append(char)
        used += length
    return "".join(prefix).rstrip(" .-_")


def _page_candidates(page: fitz.Page) -> list[tuple[float, float, str]]:
    """Return prominent line candidates from the upper part of page one."""
    candidates: list[tuple[float, float, str]] = []
    blocks = page.get_text("dict").get("blocks", [])
    for block in blocks:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            spans = line.get("spans", [])
            text = normalize_text("".join(s.get("text", "") for s in spans))
            if not plausible_title(text):
                continue
            y = float(line["bbox"][1])
            if y < 0 or y > page.rect.height * 0.67:
                continue
            size = max((float(s.get("size", 0)) for s in spans), default=0)
            candidates.append((size, y, text))
    return candidates


def title_from_page(page: fitz.Page) -> str | None:
    candidates = _page_candidates(page)
    if not candidates:
        return None
    # Large display type is a better signal than the first text line.
    # Among similarly sized lines, prefer the earlier location.
    candidates.sort(key=lambda row: (-row[0], row[1]))
    size, y, text = candidates[0]
    continuations = [
        (other_y, other_text)
        for other_size, other_y, other_text in candidates[1:]
        if abs(other_size - size) <= max(1.0, size * 0.12)
        and 0 < other_y - y <= size * 3.4
    ]
    if continuations:
        next_y, next_text = min(continuations)
        if len(text) + len(next_text) + 1 <= 180:
            text = f"{text} {next_text}"
    return text


def extract_title(pdf_path: Path) -> tuple[str | None, str | None, str]:
    """Return (title, origin, reason). No OCR or external services."""
    try:
        with fitz.open(pdf_path) as document:
            if document.needs_pass:
                return None, None, "password-protected PDF"
            metadata = document.metadata or {}
            meta_title = normalize_text(metadata.get("title") or "")
            if plausible_title(meta_title):
                return meta_title, "metadata", ""
            if document.page_count == 0:
                return None, None, "empty PDF"
            title = title_from_page(document[0])
            if title:
                return title, "page typography", ""
            # Fallback when the PDF has no usable font-size structure.
            for page in document:
                if page.number >= 3:
                    break
                for line in page.get_text("text").splitlines():
                    value = normalize_text(line)
                    if plausible_title(value):
                        return value, "page text (low confidence)", ""
            return None, None, "no extractable title (image-only PDFs need OCR)"
    except Exception as exc:
        return None, None, f"PDF could not be read: {type(exc).__name__}: {exc}"


def discover_pdfs(paths: Iterable[Path], recursive: bool) -> list[Path]:
    """Resolve selected paths without following or renaming symlinked files."""
    found: dict[str, Path] = {}
    for item in paths:
        if not item.exists():
            raise ValueError(f"Path does not exist: {item}")
        if item.is_symlink():
            raise ValueError(f"Symlink inputs are unsupported: {item}")
        if item.is_file():
            if item.suffix.casefold() != ".pdf":
                raise ValueError(f"Not a PDF file: {item}")
            candidates = [item]
        elif item.is_dir():
            candidates = item.rglob("*") if recursive else item.iterdir()
        else:
            raise ValueError(f"Not a regular file or directory: {item}")
        for candidate in candidates:
            if candidate.suffix.casefold() != ".pdf" or candidate.is_symlink() or not candidate.is_file():
                continue
            absolute = candidate.absolute()
            found[str(absolute)] = absolute
    return sorted(found.values(), key=lambda item: (str(item.parent).casefold(), item.name.casefold(), item.name))


def build_plan(files: Iterable[Path], max_bytes: int = MAX_TITLE_BYTES) -> list[Proposal]:
    reserved: dict[Path, set[str]] = {}
    proposals: list[Proposal] = []
    for path in files:
        parent = path.parent
        if parent not in reserved:
            # Reserve every current entry, not just PDFs, and account for case-insensitive FS.
            reserved[parent] = {entry.name.casefold() for entry in parent.iterdir()}
        title, origin, reason = extract_title(path)
        if not title:
            proposals.append(Proposal(path, None, "skipped", reason))
            continue
        safe = sanitize_filename(title, max_bytes)
        if not safe:
            proposals.append(Proposal(path, None, "skipped", "title cannot form a safe filename", origin))
            continue
        if path.stem.casefold() == safe.casefold() and path.suffix.casefold() == ".pdf":
            proposals.append(Proposal(path, path, "unchanged", "already named", origin))
            continue
        name = f"{safe}.pdf"
        suffix_index = 2
        while name.casefold() in reserved[parent]:
            suffix = f" ({suffix_index})"
            cut = sanitize_filename(safe, max_bytes - len(suffix.encode("utf-8")))
            name = f"{cut}{suffix}.pdf"
            suffix_index += 1
        reserved[parent].add(name.casefold())
        proposals.append(Proposal(path, parent / name, "planned", "", origin))
    return proposals


def apply_plan(proposals: list[Proposal]) -> None:
    """Hard-link exclusively, then unlink: no overwrite even if a target appears mid-run.

    Hard-link creation is atomic with respect to target existence. Unsupported
    filesystems report an error rather than falling back to an unsafe overwrite.
    """
    for proposal in proposals:
        if proposal.status != "planned" or proposal.target is None:
            continue
        try:
            if proposal.source.is_symlink() or not proposal.source.is_file():
                raise OSError("source is not a regular PDF file")
            os.link(proposal.source, proposal.target, follow_symlinks=False)
        except OSError as exc:
            proposal.status = "error"
            proposal.reason = f"could not create destination safely: {exc}"
            continue
        try:
            proposal.source.unlink()
        except OSError as exc:
            # Retain the source, attempt to remove only the newly created link.
            try:
                proposal.target.unlink()
                proposal.reason = f"could not remove source: {exc}; destination rolled back"
            except OSError as rollback_error:
                proposal.reason = (
                    f"could not remove source: {exc}; both paths may exist; "
                    f"rollback failed: {rollback_error}"
                )
            proposal.status = "error"
        else:
            proposal.status = "renamed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rename PDFs from their titles. Preview is the default; --apply changes files."
    )
    parser.add_argument("paths", nargs="*", type=Path, default=[Path(".")], help="PDF files or folders (default: current directory)")
    parser.add_argument("--apply", action="store_true", help="perform the planned renames")
    parser.add_argument("--no-recursive", action="store_true", help="only scan the top level of supplied folders")
    parser.add_argument("--max-title-bytes", type=int, default=MAX_TITLE_BYTES, metavar="N", help="maximum UTF-8 bytes in filename stem (40-200)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = parser.parse_args(argv)
    if not 40 <= args.max_title_bytes <= 200:
        parser.error("--max-title-bytes must be between 40 and 200")
    try:
        files = discover_pdfs(args.paths, recursive=not args.no_recursive)
        plan = build_plan(files, max_bytes=args.max_title_bytes)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    if args.apply:
        apply_plan(plan)
    if args.json:
        print(json.dumps([item.as_json() for item in plan], ensure_ascii=False, indent=2))
    else:
        for item in plan:
            target = f" -> {item.target.name}" if item.target and item.status in {"planned", "renamed"} else ""
            detail = f" ({item.reason})" if item.reason else ""
            print(f"{item.status.upper()}: {item.source}{target}{detail}")
        counts = {status: sum(p.status == status for p in plan) for status in ("planned", "renamed", "unchanged", "skipped", "error")}
        print(f"Total: {len(plan)} | " + " | ".join(f"{key}: {value}" for key, value in counts.items() if value))
        if not args.apply and counts["planned"]:
            print("Preview only. Pass --apply to rename files.")
    return 1 if any(p.status == "error" for p in plan) else 0


if __name__ == "__main__":
    sys.exit(main())
