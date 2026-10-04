"""Import pipeline: upload -> mapping/preview -> commit (as job). No silent partial imports."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session

from decision_evidence.db import models as m
from decision_evidence.errors import ApiError, bad_request, conflict, not_found
from decision_evidence.modules.common import tenant_settings
from decision_evidence.modules.imports.domain import parsing as p
from decision_evidence.storage.port import new_object_key

PREVIEW_ERROR_CAP = 200
HASH_QUERY_CHUNK = 1000


# --------------------------------------------------------------------------- upload
def register_file(s: Session, ctx_user: uuid.UUID, tenant_id: uuid.UUID, *, name: str, media_type: str, purpose: str, data: bytes) -> m.FileRecord:
    f = m.FileRecord(object_key=new_object_key(tenant_id), original_name=p.safe_filename(name), media_type=media_type,
                     byte_size=len(data), sha256=hashlib.sha256(data).hexdigest(), purpose=purpose, status="pending", uploaded_by=ctx_user)
    s.add(f)
    s.flush()
    return f


def detect_batch(kind: str, filename: str, data: bytes, max_rows: int) -> dict[str, Any]:
    """Parses just enough to propose a mapping. Raises FileRejected for unusable files."""
    if kind in p.CSV_KINDS:
        header, body, meta = p.read_csv(data, max_rows)
        return {"header": header, "row_count": meta["row_count"], "delimiter": meta["delimiter"], "encoding": meta["encoding"],
                "suggested_mapping": p.suggest_mapping(kind, header), "sample_rows": body[:5]}
    doc = p.extract_document(data, filename)
    return {"pages": doc.pages, "chunk_count": len(doc.chunks), "characters": len(doc.text), "no_text_layer": doc.no_text_layer}


def create_batch(s: Session, user_id: uuid.UUID, kind: str, file: m.FileRecord, detected: dict[str, Any], options: dict[str, Any]) -> m.ImportBatch:
    mapping = detected.get("suggested_mapping", {}) if kind in p.CSV_KINDS else {}
    batch = m.ImportBatch(kind=kind, file_id=file.id, status="uploaded", mapping=mapping, options=options, detected=detected, created_by=user_id)
    s.add(batch)
    s.flush()
    return batch


# --------------------------------------------------------------------------- preview
def _norm_options(kind: str, options: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"synthetic": bool(options.get("synthetic", False))}
    if kind in ("customers", "opportunities"):
        out["on_duplicate"] = options.get("on_duplicate", "skip") if options.get("on_duplicate") in ("skip", "update") else "skip"
    if kind == "feedback":
        ch = options.get("default_channel")
        out["default_channel"] = ch if ch in p.CHANNEL_ALIASES.values() else "other"
    if kind in ("notes_text", "document"):
        for key in ("title", "customer_external_id", "channel", "occurred_at"):
            if options.get(key):
                out[key] = str(options[key])[:300]
        out.setdefault("channel", "document" if kind == "document" else "call")
    return out


def preview_hash(file_sha: str, mapping: dict[str, Any], options: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps([file_sha, mapping, options], sort_keys=True, default=str).encode()).hexdigest()


def _existing(s: Session, table: Any, column: Any, values: set[str]) -> set[str]:
    found: set[str] = set()
    vals = sorted(values)
    for i in range(0, len(vals), HASH_QUERY_CHUNK):
        found.update(s.scalars(select(column).where(column.in_(vals[i:i + HASH_QUERY_CHUNK]))))
    return found


def compute_import_plan(s: Session, batch: m.ImportBatch, file: m.FileRecord, data: bytes) -> dict[str, Any]:
    """Pure planning step shared by preview and commit: parses the stored file and classifies every row.
    The commit job runs it again on the current database state, so a stale preview can never be committed blindly."""
    ts = tenant_settings(s)
    kind = batch.kind
    plan: dict[str, Any] = {"kind": kind, "rows": [], "errors": [], "warnings": [], "duplicates": [], "blocking": [], "counts": {}}
    options = _norm_options(kind, batch.options)
    plan["options"] = options
    if kind in p.CSV_KINDS:
        header, body, _meta = p.read_csv(data, ts.max_import_rows)
        problems = p.validate_mapping(kind, batch.mapping, header)
        if problems:
            plan["blocking"].extend(problems)
            plan["counts"] = {"total_rows": len(body)}
            return plan
        rows = p.parse_rows(kind, header, body, batch.mapping, options)
        _classify_csv(s, kind, rows, options, plan)
    else:
        _plan_document(s, batch, file, data, options, plan)
    return plan


def _classify_csv(s: Session, kind: str, rows: list[p.ParsedRow], options: dict[str, Any], plan: dict[str, Any]) -> None:
    errors: list[p.Issue] = []
    warnings: list[p.Issue] = []
    dups: list[dict[str, Any]] = []
    classified: list[dict[str, Any]] = []
    for r in rows:
        errors.extend(r.errors)
        warnings.extend(r.warnings)

    if kind == "customers":
        existing = _existing(s, m.CustomerAccount, m.CustomerAccount.external_id, {r.values["external_id"] for r in rows if r.values.get("external_id")})
        seen: dict[str, int] = {}
        for r in rows:
            ext = r.values.get("external_id")
            if r.errors or not ext:
                classified.append({"row": r.number, "status": "error"})
                continue
            if ext in seen:
                errors.append(p.Issue(r.number, "external_id", "duplicate_in_file", f"ID „{ext}“ kommt schon in Zeile {seen[ext]} vor."))
                classified.append({"row": r.number, "status": "error"})
                continue
            seen[ext] = r.number
            if ext in existing:
                dups.append({"row": r.number, "reason": "exists", "key": ext})
                classified.append({"row": r.number, "status": "update" if options["on_duplicate"] == "update" else "duplicate", "values": r.values})
            else:
                classified.append({"row": r.number, "status": "new", "values": r.values})
    elif kind == "opportunities":
        cust_ids = {r.values["customer_external_id"] for r in rows if r.values.get("customer_external_id")}
        known_customers = _existing(s, m.CustomerAccount, m.CustomerAccount.external_id, cust_ids)
        existing = _existing(s, m.Opportunity, m.Opportunity.external_id, {r.values["external_id"] for r in rows if r.values.get("external_id")})
        seen = {}
        for r in rows:
            ext = r.values.get("external_id")
            if r.errors or not ext:
                classified.append({"row": r.number, "status": "error"})
                continue
            if r.values["customer_external_id"] not in known_customers:
                errors.append(p.Issue(r.number, "customer_external_id", "unknown_customer",
                                      f"Kunde „{r.values['customer_external_id']}“ ist nicht importiert. Bitte zuerst die Kundenstammdaten importieren."))
                classified.append({"row": r.number, "status": "error"})
                continue
            if ext in seen:
                errors.append(p.Issue(r.number, "external_id", "duplicate_in_file", f"ID „{ext}“ kommt schon in Zeile {seen[ext]} vor."))
                classified.append({"row": r.number, "status": "error"})
                continue
            seen[ext] = r.number
            if ext in existing:
                dups.append({"row": r.number, "reason": "exists", "key": ext})
                classified.append({"row": r.number, "status": "update" if options["on_duplicate"] == "update" else "duplicate", "values": r.values})
            else:
                classified.append({"row": r.number, "status": "new", "values": r.values})
    else:  # feedback
        cust_ids = {r.values["customer_external_id"] for r in rows if r.values.get("customer_external_id")}
        opp_ids = {r.values["opportunity_external_id"] for r in rows if r.values.get("opportunity_external_id")}
        known_c = _existing(s, m.CustomerAccount, m.CustomerAccount.external_id, cust_ids)
        known_o = _existing(s, m.Opportunity, m.Opportunity.external_id, opp_ids)
        hashes = {r.values["content_hash"] for r in rows if r.values.get("content_hash")}
        existing_hash = _existing(s, m.SourceRecord, m.SourceRecord.content_hash, hashes)
        ext_ids = {r.values["external_id"] for r in rows if r.values.get("external_id")}
        existing_ext = _existing(s, m.FeedbackItem, m.FeedbackItem.external_id, ext_ids)
        seen_hash: dict[str, int] = {}
        seen_ext: dict[str, int] = {}
        for r in rows:
            v = r.values
            if r.errors:
                classified.append({"row": r.number, "status": "error"})
                continue
            bad = False
            if v["customer_external_id"] and v["customer_external_id"] not in known_c:
                errors.append(p.Issue(r.number, "customer_external_id", "unknown_customer", f"Kunde „{v['customer_external_id']}“ ist nicht importiert."))
                bad = True
            if v["opportunity_external_id"] and v["opportunity_external_id"] not in known_o:
                errors.append(p.Issue(r.number, "opportunity_external_id", "unknown_opportunity", f"Opportunity „{v['opportunity_external_id']}“ ist nicht importiert."))
                bad = True
            if bad:
                classified.append({"row": r.number, "status": "error"})
                continue
            if not v["customer_external_id"]:
                warnings.append(p.Issue(r.number, "customer_external_id", "no_customer", "Kein Kunde zugeordnet: zählt nicht als Kunde in Auswertungen."))
            h, ext = v["content_hash"], v["external_id"]
            if h in existing_hash or (ext and ext in existing_ext):
                dups.append({"row": r.number, "reason": "already_imported", "key": ext or h[:12]})
                classified.append({"row": r.number, "status": "duplicate"})
            elif h in seen_hash or (ext and ext in seen_ext):
                first = seen_hash.get(h) or seen_ext.get(ext or "")
                dups.append({"row": r.number, "reason": "duplicate_in_file", "key": ext or h[:12], "first_row": first})
                classified.append({"row": r.number, "status": "duplicate"})
            else:
                seen_hash[h] = r.number
                if ext:
                    seen_ext[ext] = r.number
                classified.append({"row": r.number, "status": "new", "values": v})
    plan.update(rows=classified, errors=errors, warnings=warnings, duplicates=dups)
    by_status = {k: sum(1 for c in classified if c["status"] == k) for k in ("new", "update", "duplicate", "error")}
    plan["counts"] = {"total_rows": len(rows), **by_status, "warning_rows": len({w.row for w in warnings})}
    if errors:
        plan["blocking"].append(f"{len({e.row for e in errors})} Zeilen enthalten Fehler. Der Import wird nur als Ganzes ausgeführt, bitte Datei korrigieren und erneut hochladen.")
    if not by_status["new"] and not by_status["update"] and not errors:
        plan["blocking"].append("Es gibt nichts zu importieren: alle Zeilen sind bereits vorhanden.")


def _plan_document(s: Session, batch: m.ImportBatch, file: m.FileRecord, data: bytes, options: dict[str, Any], plan: dict[str, Any]) -> None:
    doc = p.extract_document(data, file.original_name)
    plan["document"] = doc
    if doc.no_text_layer:
        plan["blocking"].append("Das Dokument enthält keine Textebene (vermutlich ein Scan). Es wird kein OCR-Text erfunden. "
                                "Bitte ein durchsuchbares PDF oder den Text direkt einfügen.")
    customer = options.get("customer_external_id")
    if customer and not s.scalar(select(m.CustomerAccount.id).where(m.CustomerAccount.external_id == customer)):
        plan["blocking"].append(f"Kunde „{customer}“ ist nicht importiert.")
    if options.get("occurred_at"):
        try:
            p.parse_datetime(options["occurred_at"])
        except ValueError:
            plan["blocking"].append("Das Datum ist ungültig.")
    h = p.content_hash(batch.kind, customer or "", doc.text)
    plan["content_hash"] = h
    if not doc.no_text_layer and s.scalar(select(m.SourceRecord.id).where(m.SourceRecord.content_hash == h)):
        plan["blocking"].append("Dieser Text wurde bereits importiert (Dublette).")
        plan["duplicates"].append({"row": 1, "reason": "already_imported", "key": h[:12]})
    plan["counts"] = {"total_rows": 1, "new": 0 if plan["blocking"] else 1, "chunks": len(doc.chunks), "pages": doc.pages}


def summarise_plan(plan: dict[str, Any]) -> dict[str, Any]:
    errors = [e.as_dict() for e in plan["errors"]]
    warn_counts: dict[str, int] = {}
    for w in plan["warnings"]:
        warn_counts[w.code] = warn_counts.get(w.code, 0) + 1
    sample = [{"row": r["row"], "status": r["status"], **({"values": {k: (str(v) if v is not None else None) for k, v in r["values"].items()}} if "values" in r else {})}
              for r in plan["rows"][:8]]
    out: dict[str, Any] = {
        "kind": plan["kind"], "counts": plan["counts"], "blocking": bool(plan["blocking"]), "blocking_reasons": plan["blocking"],
        "errors": errors[:PREVIEW_ERROR_CAP], "error_total": len(errors), "warning_counts": warn_counts,
        "warnings": [w.as_dict() for w in plan["warnings"][:50]], "duplicates": plan["duplicates"][:50], "duplicate_total": len(plan["duplicates"]),
        "sample": sample, "options": plan["options"],
    }
    doc = plan.get("document")
    if doc is not None:
        out["document"] = {"pages": doc.pages, "characters": len(doc.text), "chunks": len(doc.chunks), "no_text_layer": doc.no_text_layer,
                           "chunk_samples": [{"locator": c.locator, "text": c.text[:200]} for c in doc.chunks[:5]]}
    return out


# --------------------------------------------------------------------------- commit (called from the worker)
def apply_plan(s: Session, batch: m.ImportBatch, file: m.FileRecord, plan: dict[str, Any], progress=lambda pct: None) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    kind, options = batch.kind, plan["options"]
    origin = "synthetic" if options.get("synthetic") else "imported"
    result: dict[str, Any] = {"kind": kind, "created": 0, "updated": 0, "skipped_duplicates": plan["counts"].get("duplicate", 0)}
    if kind == "customers":
        new = [r["values"] | {"row": r["row"]} for r in plan["rows"] if r["status"] == "new"]
        if new:
            s.execute(insert(m.CustomerAccount), [{**{k: v for k, v in x.items() if k != "row"}, "import_batch_id": batch.id, "source_row": x["row"]} for x in new])
        result["created"] = len(new)
        for r in (x for x in plan["rows"] if x["status"] == "update"):
            v = r["values"]
            s.execute(update(m.CustomerAccount).where(m.CustomerAccount.external_id == v["external_id"]).values(
                name=v["name"], segment=v["segment"], country=v["country"], commercial_value=v["commercial_value"],
                value_basis=v["value_basis"], currency=v["currency"], value_as_of=v["value_as_of"], import_batch_id=batch.id, source_row=r["row"]))
            result["updated"] += 1
    elif kind == "opportunities":
        cust = dict(s.execute(select(m.CustomerAccount.external_id, m.CustomerAccount.id)).all())
        new = [x for x in plan["rows"] if x["status"] == "new"]
        if new:
            s.execute(insert(m.Opportunity), [
                {"external_id": x["values"]["external_id"], "customer_account_id": cust[x["values"]["customer_external_id"]], "name": x["values"]["name"],
                 "stage": x["values"]["stage"], "amount": x["values"]["amount"], "currency": x["values"]["currency"],
                 "closed_at": x["values"]["closed_at"], "import_batch_id": batch.id, "source_row": x["row"]} for x in new])
        result["created"] = len(new)
        for r in (x for x in plan["rows"] if x["status"] == "update"):
            v = r["values"]
            s.execute(update(m.Opportunity).where(m.Opportunity.external_id == v["external_id"]).values(
                customer_account_id=cust[v["customer_external_id"]], name=v["name"], stage=v["stage"], amount=v["amount"],
                currency=v["currency"], closed_at=v["closed_at"], import_batch_id=batch.id, source_row=r["row"]))
            result["updated"] += 1
    elif kind == "feedback":
        _apply_feedback(s, batch, file, plan, origin, progress)
        result["created"] = plan["counts"].get("new", 0)
    else:
        _apply_document(s, batch, file, plan, origin)
        result["created"] = 1
    return result


def _apply_feedback(s: Session, batch: m.ImportBatch, file: m.FileRecord, plan: dict[str, Any], origin: str, progress) -> None:  # type: ignore[no-untyped-def]
    cust = dict(s.execute(select(m.CustomerAccount.external_id, m.CustomerAccount.id)).all())
    opps = dict(s.execute(select(m.Opportunity.external_id, m.Opportunity.id)).all())
    new = [r for r in plan["rows"] if r["status"] == "new"]
    sources, items, chunks = [], [], []
    for r in new:
        v = r["values"]
        sid = uuid.uuid4()
        body = v["body"]
        sources.append({"id": sid, "source_kind": "csv_feedback", "origin": origin, "external_id": v["external_id"], "file_id": file.id,
                        "import_batch_id": batch.id, "source_timestamp": v["occurred_at"],
                        "title": (p.normalize_text(body)[:80] or "Feedback"), "raw_text": body, "content_hash": v["content_hash"],
                        "metadata_": {"row": r["row"], "file": file.original_name, "channel": v["channel"]}})
        items.append({"id": uuid.uuid4(), "source_record_id": sid, "customer_account_id": cust.get(v["customer_external_id"]) if v["customer_external_id"] else None,
                      "opportunity_id": opps.get(v["opportunity_external_id"]) if v["opportunity_external_id"] else None,
                      "channel": v["channel"], "occurred_at": v["occurred_at"], "body": body, "language": v["language"], "external_id": v["external_id"]})
        for i, ch in enumerate(p.chunk_feedback_row(body, r["row"], file.original_name)):
            chunks.append({"source_record_id": sid, "ordinal": i, "text": ch.text, "locator": ch.locator})
    step = 500
    for i in range(0, len(sources), step):
        s.execute(insert(m.SourceRecord), sources[i:i + step])
        s.execute(insert(m.FeedbackItem), items[i:i + step])
        progress(int(10 + 80 * min(i + step, len(sources)) / max(1, len(sources))))
    for i in range(0, len(chunks), 2000):
        s.execute(insert(m.SourceChunk), chunks[i:i + 2000])


def _apply_document(s: Session, batch: m.ImportBatch, file: m.FileRecord, plan: dict[str, Any], origin: str) -> None:
    doc = plan["document"]
    options = plan["options"]
    customer_ext = options.get("customer_external_id")
    customer_id = s.scalar(select(m.CustomerAccount.id).where(m.CustomerAccount.external_id == customer_ext)) if customer_ext else None
    occurred = p.parse_datetime(options["occurred_at"]) if options.get("occurred_at") else datetime.now(UTC)
    kind = "note_text" if batch.kind == "notes_text" else "document"
    if kind == "note_text" and origin != "synthetic":
        origin = "original"
    title = options.get("title") or p.normalize_text(file.original_name.rsplit(".", 1)[0])[:200] or "Dokument"
    sid = uuid.uuid4()
    s.execute(insert(m.SourceRecord), [{"id": sid, "source_kind": kind, "origin": origin, "file_id": file.id, "import_batch_id": batch.id,
                                        "source_timestamp": occurred, "title": title, "raw_text": doc.text, "content_hash": plan["content_hash"],
                                        "metadata_": {"pages": doc.pages, "file": file.original_name}}])
    s.execute(insert(m.FeedbackItem), [{"source_record_id": sid, "customer_account_id": customer_id, "channel": options.get("channel", "document"),
                                        "occurred_at": occurred, "body": doc.text, "language": None}])
    s.execute(insert(m.SourceChunk), [{"source_record_id": sid, "ordinal": i, "text": c.text, "locator": c.locator} for i, c in enumerate(doc.chunks)])


def batch_counts(s: Session) -> dict[str, int]:
    return {k: v for k, v in s.execute(select(m.ImportBatch.status, func.count()).group_by(m.ImportBatch.status)).all()}


__all__ = ["ApiError", "bad_request", "conflict", "not_found", "text"]
