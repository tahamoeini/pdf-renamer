import json
from pathlib import Path

import fitz
import pytest

from rename import (
    apply_plan,
    build_plan,
    discover_pdfs,
    extract_title,
    main,
    normalize_text,
    plausible_title,
    sanitize_filename,
)


def make_pdf(path: Path, *, title: str = "", lines: list[tuple[str, int, int]] | None = None, encrypted=False):
    doc = fitz.open()
    page = doc.new_page()
    for text, size, y in lines or []:
        page.insert_text((60, y), text, fontsize=size)
    if title:
        doc.set_metadata({"title": title})
    if encrypted:
        doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    else:
        doc.save(path)
    doc.close()


def test_normalization_preserves_international_text_and_rejects_invalid_filenames():
    assert normalize_text("  مقاله\t جدید  ") == "مقاله جدید"
    assert sanitize_filename('  CON  ') == "_CON"
    assert sanitize_filename('Research: A/B?') == "Research- A-B"
    assert not plausible_title("Paper Title")
    assert not plausible_title("https://example.com/x")
    assert len(sanitize_filename("عنوان" * 100, 50).encode("utf-8")) <= 50


def test_metadata_has_priority(tmp_path):
    pdf = tmp_path / "random.pdf"
    make_pdf(pdf, title="Trusted Research Title", lines=[("A Different Printed Heading", 25, 100)])
    assert extract_title(pdf)[:2] == ("Trusted Research Title", "metadata")


def test_large_type_beats_first_line(tmp_path):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, title="Untitled", lines=[
        ("Journal of Reports", 9, 60),
        ("The Real Document Title", 22, 130),
        ("A short summary of our work", 10, 180),
    ])
    assert extract_title(pdf)[:2] == ("The Real Document Title", "page typography")


def test_scanned_paper_without_metadata_skipped(tmp_path):
    pdf = tmp_path / "scanned.pdf"
    make_pdf(pdf)
    plan = build_plan([pdf])
    assert plan[0].status == "skipped"
    assert "OCR" in plan[0].reason


def test_encrypted_document_skipped(tmp_path):
    pdf = tmp_path / "locked.pdf"
    make_pdf(pdf, title="Private Report", encrypted=True)
    assert "password" in extract_title(pdf)[2]


def test_dry_run_does_not_modify_and_apply_does(tmp_path, capsys):
    pdf = tmp_path / "old.pdf"
    make_pdf(pdf, title="A Better Name")
    assert main([str(tmp_path)]) == 0
    assert pdf.exists()
    assert not (tmp_path / "A Better Name.pdf").exists()
    assert "Preview only" in capsys.readouterr().out
    assert main([str(tmp_path), "--apply"]) == 0
    assert not pdf.exists()
    assert (tmp_path / "A Better Name.pdf").exists()
    assert main([str(tmp_path), "--apply"]) == 0
    assert len(list(tmp_path.glob("*.pdf"))) == 1


def test_existing_case_insensitive_collision_is_suffixed(tmp_path):
    first = tmp_path / "source.pdf"
    make_pdf(first, title="A Great Title")
    (tmp_path / "a great title.PDF").write_bytes(b"original")
    plan = build_plan([first])
    assert plan[0].target.name == "A Great Title (2).pdf"
    apply_plan(plan)
    assert (tmp_path / "a great title.PDF").read_bytes() == b"original"
    assert plan[0].status == "renamed"


def test_two_files_same_title_do_not_collide(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    make_pdf(a, title="Shared Title")
    make_pdf(b, title="Shared Title")
    plan = build_plan([a, b])
    assert [p.target.name for p in plan] == ["Shared Title.pdf", "Shared Title (2).pdf"]
    apply_plan(plan)
    assert all(p.status == "renamed" for p in plan)
    assert len(list(tmp_path.glob("*.pdf"))) == 2


def test_existing_target_that_appears_after_planning_is_never_overwritten(tmp_path):
    source = tmp_path / "original.pdf"
    make_pdf(source, title="Target File")
    plan = build_plan([source])
    target = tmp_path / "Target File.pdf"
    target.write_bytes(b"must not change")
    apply_plan(plan)
    assert target.read_bytes() == b"must not change"
    assert source.exists()
    assert plan[0].status == "error"


def test_recursive_uppercase_and_symlink_avoidance(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    pdf = nested / "file.PDF"
    make_pdf(pdf, title="Sample Title")
    assert discover_pdfs([tmp_path], recursive=False) == []
    assert discover_pdfs([tmp_path, pdf], recursive=True) == [pdf]
    link = tmp_path / "link.pdf"
    try:
        link.symlink_to(pdf)
    except OSError:
        pytest.skip("symlinks unsupported")
    assert discover_pdfs([tmp_path], recursive=True) == [pdf]
    with pytest.raises(ValueError):
        discover_pdfs([link], recursive=True)


def test_json_is_machine_readable(tmp_path, capsys):
    pdf = tmp_path / "original.pdf"
    make_pdf(pdf, title="Metadata Title")
    assert main([str(pdf), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["status"] == "planned"
    assert payload[0]["title_source"] == "metadata"


def test_invalid_path_fails_cleanly(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main([str(tmp_path / "missing")])
    assert exc.value.code == 2
