from __future__ import annotations

from decimal import Decimal

import pytest

from decision_evidence.modules.imports.domain import parsing as p


def test_suggest_mapping_german_headers() -> None:
    header = ["Kundennummer", "Firma", "Segment", "Land", "ARR", "Währung"]
    m = p.suggest_mapping("customers", header)
    assert m["external_id"] == "Kundennummer" and m["name"] == "Firma" and m["commercial_value"] == "ARR"
    assert m["currency"] == "Währung" and m["value_basis"] is None


def test_decimal_formats() -> None:
    assert p.parse_decimal("1.234,56") == Decimal("1234.56")
    assert p.parse_decimal("1,234.56") == Decimal("1234.56")
    assert p.parse_decimal("1234.5") == Decimal("1234.50")
    assert p.parse_decimal("12.345.678") == Decimal("12345678.00")
    for bad in ("", "abc", "-5"):
        with pytest.raises(ValueError):
            p.parse_decimal(bad)


def test_customers_missing_value_stays_unknown_not_zero() -> None:
    header = ["id", "name", "arr", "currency"]
    rows = p.parse_rows("customers", header, [["C1", "A", "", ""], ["C2", "B", "1000", "EUR"], ["C3", "C", "500", ""]],
                        p.suggest_mapping("customers", header) | {"value_basis": None}, {})
    assert rows[0].values["commercial_value"] is None and rows[0].values["value_basis"] == "unknown"
    assert rows[0].errors == [] and any(w.code == "value_missing" for w in rows[0].warnings)
    # a value without a value basis is an error (no silent guess whether it is ARR or sales)
    assert any(e.code == "basis_missing" for e in rows[1].errors)
    assert any(e.code == "currency_missing" for e in rows[2].errors)


def test_customers_with_basis_and_currency() -> None:
    header = ["id", "name", "wert", "wertbasis", "währung", "stand"]
    mapping = p.suggest_mapping("customers", header)
    rows = p.parse_rows("customers", header, [["C1", "A", "12.000,50", "ARR", "chf", "31.12.2025"]], mapping, {})
    r = rows[0]
    assert not r.errors, r.errors
    assert r.values["commercial_value"] == Decimal("12000.50") and r.values["currency"] == "CHF"
    assert str(r.values["value_as_of"]) == "2025-12-31"


def test_opportunity_validation() -> None:
    header = ["id", "kunden_id", "name", "phase", "betrag", "währung", "abschlussdatum"]
    mapping = p.suggest_mapping("opportunities", header)
    rows = p.parse_rows("opportunities", header, [
        ["O1", "C1", "x", "offen", "100", "EUR", ""],
        ["O2", "C1", "y", "offen", "100", "EUR", "2025-01-01"],
        ["O3", "C1", "z", "verloren", "", "", "2025-01-01"],
        ["O4", "C1", "w", "kaputt", "10", "EUR", ""],
    ], mapping, {})
    assert not rows[0].errors
    assert rows[1].errors[0].code == "open_with_close_date"
    assert not rows[2].errors and rows[2].values["amount"] is None
    assert rows[3].errors[0].code == "bad_stage"


def test_feedback_hash_identical_for_duplicate_tickets() -> None:
    header = ["datum", "text", "kunden_id"]
    mapping = p.suggest_mapping("feedback", header)
    rows = p.parse_rows("feedback", header, [["01.03.2026", "Der Export  ist zu langsam!", "C1"], ["05.03.2026", "der export ist zu langsam!", "C1"],
                                             ["05.03.2026", "der export ist zu langsam!", "C2"]], mapping, {})
    assert rows[0].values["content_hash"] == rows[1].values["content_hash"] != rows[2].values["content_hash"]


def test_validate_mapping_reports_problems() -> None:
    probs = p.validate_mapping("feedback", {"body": None, "occurred_at": "Datum", "channel": "Datum"}, ["Datum", "Text"])
    assert any("body" in x for x in probs) and any("nur einem Feld" in x for x in probs)


def test_upload_validation() -> None:
    assert p.validate_upload("a.csv", "text/csv", b"a,b\n1,2\n")[0] == "import_csv"
    with pytest.raises(p.FileRejected) as e:
        p.validate_upload("a.csv", "text/csv", b"%PDF-1.4 fake")
    assert e.value.code == "content_mismatch"
    with pytest.raises(p.FileRejected):
        p.validate_upload("a.exe", "application/octet-stream", b"MZ")
    with pytest.raises(p.FileRejected):
        p.validate_upload("a.pdf", "application/pdf", b"not a pdf")
    with pytest.raises(p.FileRejected):
        p.validate_upload("a.csv", "image/png", b"a,b")
    with pytest.raises(p.FileRejected):
        p.validate_upload("a.txt", "text/plain", b"abc\x00def")
    with pytest.raises(p.FileRejected):
        p.validate_upload("a.csv", "text/csv", b"")


def test_safe_filename() -> None:
    assert p.safe_filename("../../etc/passwd") == "passwd"
    assert p.safe_filename("C:\\x\\y<z>.csv") == "yz.csv"
    assert p.safe_filename("") == "upload"


def test_read_csv_semicolon_and_limits() -> None:
    data = "id;name\nC1;Müller GmbH\nC2;Meier\n".encode("cp1252")
    header, body, meta = p.read_csv(data, 10)
    assert header == ["id", "name"] and len(body) == 2 and meta["delimiter"] == ";" and body[0][1] == "Müller GmbH"
    with pytest.raises(p.FileRejected) as e:
        p.read_csv(b"a,b\n" + b"1,2\n" * 11, 10)
    assert e.value.code == "too_many_rows"
    with pytest.raises(p.FileRejected):
        p.read_csv(b"a,a\n1,2\n", 10)


def test_paragraph_chunking_and_pdf_text_layer_and_scan() -> None:
    assert p.split_paragraphs("Eins.\n\nZwei.\n\n\nDrei.") == ["Eins.", "Zwei.", "Drei."]
    long = ("Satz eins ist hier. " * 200).strip()
    parts = p.split_paragraphs(long)
    assert all(len(x) <= p.MAX_CHUNK_CHARS for x in parts) and len(parts) > 1


def _pdf_with_text(text: str) -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()


def _pdf_blank() -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()


def test_pdf_with_text_layer() -> None:
    doc = p.extract_document(_pdf_with_text("Der Export bricht ab"), "n.pdf")
    assert not doc.no_text_layer and doc.pages == 1
    assert "Export" in doc.chunks[0].text and doc.chunks[0].locator["page"] == 1


def test_pdf_scan_without_text_is_reported_not_invented() -> None:
    doc = p.extract_document(_pdf_blank(), "scan.pdf")
    assert doc.no_text_layer and doc.chunks == []


def test_corrupt_pdf_rejected() -> None:
    with pytest.raises(p.FileRejected):
        p.extract_document(b"%PDF-1.4 garbage", "x.pdf")
