"""CSV/document parsing and validation for imports. Pure functions, no database access.

Imported content is untrusted data: it is parsed into values, never interpreted as instructions.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

CSV_KINDS = ("customers", "opportunities", "feedback")

FIELD_SPECS: dict[str, dict[str, bool]] = {  # field -> required
    "customers": {"external_id": True, "name": True, "segment": False, "country": False, "commercial_value": False,
                  "value_basis": False, "currency": False, "value_as_of": False},
    "opportunities": {"external_id": True, "customer_external_id": True, "name": True, "stage": True, "amount": False,
                      "currency": False, "closed_at": False},
    "feedback": {"external_id": False, "customer_external_id": False, "opportunity_external_id": False, "channel": False,
                 "occurred_at": True, "body": True, "language": False},
}

ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "customers": {
        "external_id": ("external_id", "id", "kunden_id", "kundennummer", "customer_id", "account_id"),
        "name": ("name", "kunde", "kundenname", "firma", "customer", "account", "account_name"),
        "segment": ("segment", "kundensegment", "branche", "tier"),
        "country": ("country", "land", "ländercode", "laendercode"),
        "commercial_value": ("commercial_value", "wert", "umsatz", "arr", "jahresumsatz", "value", "revenue"),
        "value_basis": ("value_basis", "wertbasis", "basis", "wertart"),
        "currency": ("currency", "währung", "waehrung"),
        "value_as_of": ("value_as_of", "stand", "wert_stand", "as_of", "stichtag"),
    },
    "opportunities": {
        "external_id": ("external_id", "id", "opportunity_id", "chance_id", "deal_id"),
        "customer_external_id": ("customer_external_id", "kunden_id", "kundennummer", "customer_id", "account_id"),
        "name": ("name", "bezeichnung", "opportunity", "chance", "deal"),
        "stage": ("stage", "phase", "status"),
        "amount": ("amount", "betrag", "volumen", "wert", "value"),
        "currency": ("currency", "währung", "waehrung"),
        "closed_at": ("closed_at", "abschlussdatum", "geschlossen_am", "close_date"),
    },
    "feedback": {
        "external_id": ("external_id", "id", "ticket_id", "ticket", "feedback_id"),
        "customer_external_id": ("customer_external_id", "kunden_id", "kundennummer", "customer_id", "account_id"),
        "opportunity_external_id": ("opportunity_external_id", "opportunity_id", "chance_id", "deal_id"),
        "channel": ("channel", "kanal", "quelle", "source"),
        "occurred_at": ("occurred_at", "datum", "zeitpunkt", "date", "created_at", "erstellt_am", "timestamp"),
        "body": ("body", "text", "feedback", "nachricht", "beschreibung", "kommentar", "message", "inhalt"),
        "language": ("language", "sprache", "lang"),
    },
}

STAGE_ALIASES = {
    "open": "open", "offen": "open", "in arbeit": "open", "pipeline": "open",
    "won": "won", "gewonnen": "won", "closed won": "won",
    "lost": "lost", "verloren": "lost", "closed lost": "lost",
    "no_decision": "no_decision", "no decision": "no_decision", "keine entscheidung": "no_decision", "zurückgestellt": "no_decision",
}
BASIS_ALIASES = {"arr": "arr", "annual_sales": "annual_sales", "jahresumsatz": "annual_sales", "umsatz": "annual_sales",
                 "unknown": "unknown", "unbekannt": "unknown", "": "unknown"}
CHANNEL_ALIASES = {
    "email": "email", "e-mail": "email", "mail": "email", "ticket": "ticket", "support": "ticket", "call": "call",
    "telefon": "call", "gespräch": "call", "gespraech": "call", "interview": "interview", "chat": "chat",
    "survey": "survey", "umfrage": "survey", "nps": "survey", "document": "document", "dokument": "document", "other": "other",
    "sonstiges": "other", "": "other",
}
MAX_CELL_CHARS = 20_000
MAX_CHUNK_CHARS = 1500

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Issue:
    row: int
    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "field": self.field, "code": self.code, "message": self.message}


@dataclass
class ParsedRow:
    number: int                      # 1-based data row number (header excluded) as shown to users
    values: dict[str, Any]
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)


class FileRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_text(value: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def content_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(normalize_text(p).casefold().encode())
        h.update(b"\x1f")
    return h.hexdigest()


def safe_filename(name: str) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = "".join(ch for ch in unicodedata.normalize("NFKC", base) if ch.isprintable() and ch not in '<>:"|?*')
    base = base.strip(" .") or "upload"
    return base[:200]


# --------------------------------------------------------------------------- file validation
ALLOWED_UPLOADS = {
    ".csv": ("import_csv", {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain", "application/octet-stream"}),
    ".txt": ("import_document", {"text/plain", "application/octet-stream"}),
    ".md": ("import_document", {"text/markdown", "text/plain", "text/x-markdown", "application/octet-stream"}),
    ".pdf": ("import_document", {"application/pdf", "application/octet-stream"}),
}


def validate_upload(filename: str, declared_type: str | None, data: bytes) -> tuple[str, str]:
    """Returns (purpose, detected media type) or raises FileRejected. Magic bytes win over the declared type."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_UPLOADS:
        raise FileRejected("unsupported_extension", "Erlaubt sind CSV-, TXT-, Markdown- und PDF-Dateien.")
    purpose, allowed_types = ALLOWED_UPLOADS[ext]
    declared = (declared_type or "application/octet-stream").split(";")[0].strip().lower()
    if declared not in allowed_types:
        raise FileRejected("media_type_mismatch", f"Der Dateityp „{declared}“ passt nicht zur Endung {ext}.")
    if not data:
        raise FileRejected("empty_file", "Die Datei ist leer.")
    if ext == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise FileRejected("not_a_pdf", "Die Datei ist kein gültiges PDF.")
        return purpose, "application/pdf"
    if data.startswith(b"%PDF-") or data[:4] == b"PK\x03\x04" or data[:2] == b"MZ" or data[:4] == b"\x7fELF":
        raise FileRejected("content_mismatch", "Der Dateiinhalt passt nicht zur Endung.")
    if b"\x00" in data[:8192]:
        raise FileRejected("binary_content", "Die Datei enthält Binärdaten und ist kein Text.")
    return purpose, "text/csv" if ext == ".csv" else "text/plain"


# --------------------------------------------------------------------------- CSV reading
def decode_text(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise FileRejected("undecodable", "Die Datei konnte nicht als Text gelesen werden (erwartet UTF-8 oder Windows-1252).")


def read_csv(data: bytes, max_rows: int) -> tuple[list[str], list[list[str]], dict[str, Any]]:
    text, encoding = decode_text(data)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
    csv.field_size_limit(MAX_CELL_CHARS)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        rows = [r for r in reader if any(c.strip() for c in r)]
    except csv.Error as exc:
        raise FileRejected("csv_malformed", f"Die CSV-Datei ist fehlerhaft: {exc}") from exc
    if not rows:
        raise FileRejected("csv_empty", "Die CSV-Datei enthält keine Zeilen.")
    header = [h.strip() for h in rows[0]]
    if len(set(h.casefold() for h in header)) != len(header) or not all(header):
        raise FileRejected("csv_header", "Die Kopfzeile enthält leere oder doppelte Spaltennamen.")
    body = rows[1:]
    meta = {"delimiter": delimiter, "encoding": encoding, "row_count": len(body)}
    if len(body) > max_rows:
        raise FileRejected("too_many_rows", f"Die Datei hat {len(body)} Zeilen, erlaubt sind {max_rows}.")
    return header, body, meta


def suggest_mapping(kind: str, header: list[str]) -> dict[str, str | None]:
    lowered = {h.casefold().replace(" ", "_"): h for h in header}
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    for fld in FIELD_SPECS[kind]:
        hit = next((lowered[a] for a in ALIASES[kind][fld] if a in lowered and lowered[a] not in used), None)
        mapping[fld] = hit
        if hit:
            used.add(hit)
    return mapping


def validate_mapping(kind: str, mapping: dict[str, str | None], header: list[str]) -> list[str]:
    problems = []
    for fld, required in FIELD_SPECS[kind].items():
        col = mapping.get(fld)
        if col is not None and col not in header:
            problems.append(f"Die Spalte „{col}“ für {fld} gibt es in der Datei nicht.")
        if required and not col:
            problems.append(f"Pflichtfeld {fld} ist keiner Spalte zugeordnet.")
    unknown = set(mapping) - set(FIELD_SPECS[kind])
    if unknown:
        problems.append(f"Unbekannte Felder: {', '.join(sorted(unknown))}")
    chosen = [c for c in mapping.values() if c]
    if len(chosen) != len(set(chosen)):
        problems.append("Eine Spalte darf nur einem Feld zugeordnet werden.")
    return problems


# --------------------------------------------------------------------------- value parsing
def parse_decimal(raw: str) -> Decimal:
    s = raw.strip().replace(" ", "").replace(" ", "").replace("€", "").replace("$", "")
    if not s:
        raise ValueError("empty")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        value = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError("not a number") from exc
    if not value.is_finite() or value < 0:
        raise ValueError("negative or not finite")
    return value.quantize(Decimal("0.01"))


def parse_date(raw: str) -> date:
    s = raw.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def parse_datetime(raw: str) -> datetime:
    s = raw.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _cell(row: list[str], header: list[str], col: str | None) -> str:
    if not col:
        return ""
    idx = header.index(col)
    return row[idx].strip() if idx < len(row) else ""


_CURRENCY = re.compile(r"^[A-Z]{3}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")


def parse_rows(kind: str, header: list[str], body: list[list[str]], mapping: dict[str, str | None],
               options: dict[str, Any]) -> list[ParsedRow]:
    out: list[ParsedRow] = []
    for i, raw in enumerate(body, start=1):
        row = ParsedRow(number=i, values={})
        get = lambda f: _cell(raw, header, mapping.get(f))  # noqa: E731
        err = lambda f, code, msg: row.errors.append(Issue(i, f, code, msg))  # noqa: E731
        warn = lambda f, code, msg: row.warnings.append(Issue(i, f, code, msg))  # noqa: E731
        for fld, required in FIELD_SPECS[kind].items():
            if required and not get(fld):
                err(fld, "required", f"{fld} fehlt.")
        if any(len(c) > MAX_CELL_CHARS for c in raw):
            err("*", "cell_too_long", "Eine Zelle ist zu lang.")
        if kind == "customers":
            _parse_customer(row, get, err, warn)
        elif kind == "opportunities":
            _parse_opportunity(row, get, err, warn)
        else:
            _parse_feedback(row, get, err, warn, options)
        out.append(row)
    return out


def _parse_customer(row: ParsedRow, get, err, warn) -> None:  # type: ignore[no-untyped-def]
    v = row.values
    v["external_id"] = get("external_id")[:100]
    v["name"] = get("name")[:300]
    v["segment"] = get("segment") or None
    country = get("country").upper()
    if country and not _COUNTRY.match(country):
        err("country", "bad_country", "Ländercode muss aus zwei Buchstaben bestehen (z. B. DE).")
        country = ""
    v["country"] = country or None
    basis_raw = get("value_basis").casefold()
    basis = BASIS_ALIASES.get(basis_raw)
    if basis is None:
        err("value_basis", "bad_value_basis", "Wertbasis muss arr, annual_sales oder unknown sein.")
        basis = "unknown"
    raw_value, currency = get("commercial_value"), get("currency").upper()
    value: Decimal | None = None
    if raw_value:
        try:
            value = parse_decimal(raw_value)
        except ValueError:
            err("commercial_value", "bad_number", f"„{raw_value}“ ist kein gültiger Betrag.")
        if value is not None:
            if not currency:
                err("currency", "currency_missing", "Zu einem Betrag gehört eine Währung (z. B. EUR).")
            elif not _CURRENCY.match(currency):
                err("currency", "bad_currency", "Währung muss ein ISO-Code aus drei Buchstaben sein.")
            if basis == "unknown":
                err("value_basis", "basis_missing", "Zu einem Betrag gehört die Wertbasis (arr oder annual_sales).")
    else:
        if basis != "unknown":
            warn("commercial_value", "value_unknown", "Wertbasis angegeben, aber kein Betrag: Wert bleibt unbekannt.")
        basis = "unknown"
        currency = ""
    v["commercial_value"], v["value_basis"] = value, basis if value is not None else "unknown"
    v["currency"] = currency if value is not None and _CURRENCY.match(currency or "") else None
    if value is None and not raw_value:
        warn("commercial_value", "value_missing", "Kein Umsatzwert: bleibt unbekannt (nicht 0).")
    raw_asof = get("value_as_of")
    v["value_as_of"] = None
    if raw_asof:
        try:
            v["value_as_of"] = parse_date(raw_asof)
        except ValueError:
            err("value_as_of", "bad_date", f"„{raw_asof}“ ist kein Datum.")


def _parse_opportunity(row: ParsedRow, get, err, warn) -> None:  # type: ignore[no-untyped-def]
    v = row.values
    v["external_id"] = get("external_id")[:100]
    v["customer_external_id"] = get("customer_external_id")[:100]
    v["name"] = get("name")[:300]
    stage = STAGE_ALIASES.get(get("stage").casefold())
    if get("stage") and stage is None:
        err("stage", "bad_stage", "Phase muss open, won, lost oder no_decision sein (deutsche Entsprechungen sind erlaubt).")
    v["stage"] = stage or "open"
    raw_amount, currency = get("amount"), get("currency").upper()
    amount: Decimal | None = None
    if raw_amount:
        try:
            amount = parse_decimal(raw_amount)
        except ValueError:
            err("amount", "bad_number", f"„{raw_amount}“ ist kein gültiger Betrag.")
        if amount is not None and not _CURRENCY.match(currency):
            err("currency", "currency_missing", "Zu einem Betrag gehört ein ISO-Währungscode (z. B. EUR).")
    else:
        warn("amount", "amount_unknown", "Kein Betrag: bleibt unbekannt (nicht 0).")
    v["amount"] = amount
    v["currency"] = currency if amount is not None and _CURRENCY.match(currency or "") else None
    raw_closed = get("closed_at")
    v["closed_at"] = None
    if raw_closed:
        try:
            v["closed_at"] = parse_date(raw_closed)
        except ValueError:
            err("closed_at", "bad_date", f"„{raw_closed}“ ist kein Datum.")
    if stage == "open" and v["closed_at"] is not None:
        err("closed_at", "open_with_close_date", "Eine offene Chance darf kein Abschlussdatum haben.")
    if stage in {"won", "lost", "no_decision"} and v["closed_at"] is None and not any(e.field == "closed_at" for e in row.errors):
        warn("closed_at", "closed_without_date", "Abgeschlossene Chance ohne Abschlussdatum.")


def _parse_feedback(row: ParsedRow, get, err, warn, options: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    v = row.values
    v["external_id"] = get("external_id")[:200] or None
    v["customer_external_id"] = get("customer_external_id")[:100] or None
    v["opportunity_external_id"] = get("opportunity_external_id")[:100] or None
    raw_channel = get("channel").casefold()
    channel = CHANNEL_ALIASES.get(raw_channel if raw_channel else "", None)
    if raw_channel and channel is None:
        channel = options.get("default_channel") or "other"
        warn("channel", "unknown_channel", f"Unbekannter Kanal „{get('channel')}“, gespeichert als {channel}.")
    v["channel"] = channel or options.get("default_channel") or "other"
    body = get("body")
    v["body"] = body
    if body and len(normalize_text(body)) < 3:
        err("body", "body_too_short", "Der Feedbacktext ist zu kurz.")
    raw_dt = get("occurred_at")
    v["occurred_at"] = None
    if raw_dt:
        try:
            v["occurred_at"] = parse_datetime(raw_dt)
        except ValueError:
            err("occurred_at", "bad_date", f"„{raw_dt}“ ist kein Datum.")
        else:
            if v["occurred_at"] > datetime.now(UTC).replace(microsecond=0) + timedelta(days=1):
                warn("occurred_at", "future_date", "Das Datum liegt in der Zukunft.")
    lang = get("language").lower()[:2]
    v["language"] = lang if re.fullmatch(r"[a-z]{2}", lang) else None
    v["content_hash"] = content_hash(v["customer_external_id"] or "", body)


# --------------------------------------------------------------------------- chunking
def split_paragraphs(text: str) -> list[str]:
    """Paragraphs separated by blank lines; very long ones are split at sentence ends (<= MAX_CHUNK_CHARS)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
    out: list[str] = []
    for p in paras:
        if len(p) <= MAX_CHUNK_CHARS:
            out.append(p)
            continue
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+", p):
            if current and len(current) + 1 + len(sentence) > MAX_CHUNK_CHARS:
                out.append(current)
                current = ""
            while len(sentence) > MAX_CHUNK_CHARS:  # pathological text without sentence ends
                out.append(sentence[:MAX_CHUNK_CHARS])
                sentence = sentence[MAX_CHUNK_CHARS:]
            current = f"{current} {sentence}".strip()
        if current:
            out.append(current)
    return out


@dataclass(frozen=True)
class ChunkDraft:
    text: str
    locator: dict[str, Any]


def chunk_feedback_row(body: str, row_number: int, filename: str) -> list[ChunkDraft]:
    paras = split_paragraphs(body) or [body.strip()]
    return [ChunkDraft(p, {"file": filename, "row": row_number, **({"paragraph": i} if len(paras) > 1 else {})})
            for i, p in enumerate(paras, start=1)]


@dataclass(frozen=True)
class ExtractedDocument:
    chunks: list[ChunkDraft]
    text: str
    pages: int | None
    no_text_layer: bool


def extract_document(data: bytes, filename: str, max_pages: int = 300) -> ExtractedDocument:
    if filename.lower().endswith(".pdf"):
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise FileRejected("pdf_encrypted", "Das PDF ist verschlüsselt und kann nicht gelesen werden.")
            if len(reader.pages) > max_pages:
                raise FileRejected("pdf_too_long", f"Das PDF hat mehr als {max_pages} Seiten.")
            chunks: list[ChunkDraft] = []
            texts: list[str] = []
            for page_no, page in enumerate(reader.pages, start=1):
                page_text = (page.extract_text() or "").strip()
                texts.append(page_text)
                for k, para in enumerate(split_paragraphs(page_text), start=1):
                    chunks.append(ChunkDraft(para, {"file": filename, "page": page_no, "paragraph": k}))
            full = "\n\n".join(t for t in texts if t)
            # no text layer: scanned pages carry images only. We never invent OCR text.
            return ExtractedDocument(chunks, full, len(reader.pages), no_text_layer=not full.strip())
        except FileRejected:
            raise
        except (PyPdfError, ValueError, KeyError, TypeError, OSError, RecursionError) as exc:
            raise FileRejected("pdf_unreadable", "Das PDF konnte nicht gelesen werden.") from exc
    text, _ = decode_text(data)
    chunks = [ChunkDraft(p, {"file": filename, "paragraph": k}) for k, p in enumerate(split_paragraphs(text), start=1)]
    return ExtractedDocument(chunks, text.strip(), None, no_text_layer=not text.strip())
