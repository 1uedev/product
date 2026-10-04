"""Markdown and CSV export of a decision revision. Built from the frozen snapshot and verified against the stored chunks."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from decision_evidence.db import models as m
from decision_evidence.modules.common import to_csv

RELATION_DE = {"supports": "unterstützt", "contradicts": "widerspricht", "context": "Kontext"}
STATE_DE = {"draft": "Entwurf", "in_review": "In Prüfung", "approved": "Freigegeben", "superseded": "Abgelöst"}


def stored_evidence(s: Session, doc: m.DecisionDocument) -> list[dict[str, Any]]:
    """Evidence rows joined with the live chunks of THIS tenant (RLS): a foreign or deleted chunk cannot appear here."""
    rows = s.execute(
        select(m.DecisionEvidence, m.SourceChunk, m.SourceRecord, m.FeedbackItem, m.CustomerAccount.name, m.FileRecord.original_name)
        .join(m.SourceChunk, m.SourceChunk.id == m.DecisionEvidence.source_chunk_id)
        .join(m.SourceRecord, m.SourceRecord.id == m.SourceChunk.source_record_id)
        .join(m.FeedbackItem, m.FeedbackItem.source_record_id == m.SourceRecord.id)
        .outerjoin(m.CustomerAccount, m.CustomerAccount.id == m.FeedbackItem.customer_account_id)
        .outerjoin(m.FileRecord, m.FileRecord.id == m.SourceRecord.file_id)
        .where(m.DecisionEvidence.decision_document_id == doc.id)
        .order_by(m.DecisionEvidence.relation, m.FeedbackItem.occurred_at.desc(), m.DecisionEvidence.id)).all()
    return [{"chunk_id": str(de.source_chunk_id), "relation": de.relation, "quote": de.quote, "customer": cname or "", "occurred_at": fi.occurred_at.date().isoformat(),
             "channel": fi.channel, "locator": sc.locator, "source_title": sr.title, "file_name": fname or "", "origin": sr.origin} for de, sc, sr, fi, cname, fname in rows]


def _money(block: dict[str, Any]) -> str:
    parts = [f"{v} {c}" for c, v in block["by_currency"].items()]
    text = ", ".join(parts) if parts else "keine bekannten Beträge"
    if block.get("unknown_count"):
        text += f" ({block['unknown_count']} unbekannt)"
    return text


def locator_text(loc: dict[str, Any]) -> str:
    bits = []
    if loc.get("file"):
        bits.append(str(loc["file"]))
    if loc.get("page"):
        bits.append(f"Seite {loc['page']}")
    if loc.get("row"):
        bits.append(f"Zeile {loc['row']}")
    if loc.get("paragraph"):
        bits.append(f"Absatz {loc['paragraph']}")
    return ", ".join(bits)


def to_markdown(doc: m.DecisionDocument, evidence: list[dict[str, Any]], names: dict[uuid.UUID, str], comments: list[dict[str, Any]], tz_note: str) -> str:
    snap, sc = doc.evidence_snapshot, doc.scoring_snapshot
    me = snap["metrics"]
    out: list[str] = []
    out.append(f"# {doc.title or 'Entscheidungsvorlage'} (Revision {doc.revision})")
    out.append("")
    out.append(f"- **Status:** {STATE_DE[doc.state]}")
    if doc.approved_at:
        out.append(f"- **Freigegeben von:** {names.get(doc.approved_by, 'unbekannt')} am {doc.approved_at.isoformat(timespec='minutes')} ({tz_note})")
    out.append(f"- **Initiative:** {snap['initiative']['title']}")
    out.append(f"- **Problem:** {snap['problem']['title']}")
    out.append(f"- **Belegstand eingefroren am:** {snap['taken_at']}")
    out.append("")
    out.append("> Diese Vorlage bereitet eine Entscheidung vor. Sie ist keine Roadmap-Zusage. Kennzahlen sind Beobachtungen aus importierten Daten, keine Prognose von Mehrumsatz.")
    out.append("")
    out.append("## Ausgangslage")
    out.append(f"- Eindeutige Kunden: **{me['supporting_customers']}** von {me['coverage']['total_customers']} (Anteil {_pct(me['coverage']['affected_share'])}); "
               f"Aussagen insgesamt: {me['supporting_statements']}; widersprechende Aussagen: {me['contradicting_statements']}")
    out.append(f"- Zugeordnetes ARR (je Währung, nicht addiert): {_money(me['arr'])}")
    out.append(f"- Zugeordneter Jahresumsatz (getrennt von ARR): {_money(me['annual_sales'])}")
    out.append(f"- Offene Pipeline (ausdrücklich zugeordnet): {_money(me['pipeline_attributed'])}")
    out.append(f"- Verlorenes Auftragsvolumen (ausdrücklich zugeordnet): {_money(me['lost_attributed'])}")
    out.append(f"- Kunden ohne bekannten Umsatz: {me['customers_without_value']} (nicht als 0 gerechnet)")
    age = me["age"]
    out.append(f"- Alter der Belege: Median {age['median_days']} Tage, ältester {age['oldest_days']} Tage")
    for w in me["warnings"]:
        out.append(f"- Hinweis: {w['message']}")
    out.append("")
    out.append("## Handlungsoptionen")
    results = {r["name"]: r for r in sc.get("results", [])}
    for opt in doc.options:
        out.append(f"### Option {opt['key']}: {opt['name']}")
        if opt.get("description"):
            out.append(opt["description"])
        eff = ""
        if opt.get("effort_low") is not None or opt.get("effort_high") is not None:
            eff = f"{opt.get('effort_low') or '?'}–{opt.get('effort_high') or '?'} {opt.get('effort_unit', '')}"
        out.append(f"- Aufwand: {eff or 'nicht geschätzt'}")
        out.append(f"- Adressierte Segmente: {', '.join(opt.get('addressed_segments') or []) or 'alle'}")
        if opt.get("expected_effects"):
            out.append(f"- Erwartete Wirkung (qualitativ): {opt['expected_effects']}")
        if opt.get("risks"):
            out.append(f"- Risiken: {opt['risks']}")
        out.append("")
    out.append("## Score-Vergleich")
    pol = sc.get("policy", {})
    out.append(f"Policy: {pol.get('name')} (Formel {pol.get('formula_version')}, fehlende Werte: {pol.get('missing_value_policy')}). Gewichte: " +
               ", ".join(f"{k} {v}" for k, v in pol.get("weights", {}).items()))
    out.append("")
    out.append("| Option | Score | Datenabdeckung der Gewichte | fehlende Kriterien |")
    out.append("|---|---|---|---|")
    for r in sc.get("results", []):
        out.append(f"| {r['name']} | {r['total'] if r['total'] is not None else 'nicht berechenbar'} | {_pct(r['weight_coverage'])} | {', '.join(r['missing']) or '–'} |")
    sens = sc.get("sensitivity")
    if sens:
        out.append("")
        out.append(f"Sensitivität: Rangfolge bei ±25 % Gewichtsänderung {'stabil' if sens['stable'] else 'NICHT stabil'}.")
    out.append("")
    out.append("## Begründung des Teams")
    out.append(doc.recommendation_text or "_(leer)_")
    if doc.ai_draft:
        out.append("")
        out.append("## KI-Entwurf zur Begründung (nicht Teil der Entscheidung)")
        out.append(f"_{doc.ai_draft.get('provider_label', 'KI')}: {doc.ai_draft.get('summary', '')}_")
        for c in doc.ai_draft.get("claims", []):
            out.append(f"- {c['statement']}")
    out.append("")
    out.append("## Annahmen")
    for a in snap.get("assumptions", []):
        out.append(f"- [{a['kind']}, {a['validation_status']}] {a['statement']}")
    if not snap.get("assumptions"):
        out.append("- keine erfasst")
    out.append("")
    out.append("## Belege (Originalzitate)")
    for e in evidence:
        out.append(f"- **{RELATION_DE.get(e['relation'], e['relation'])}** · {e['customer'] or 'ohne Kunde'} · {e['occurred_at']} · {e['channel']} · {locator_text(e['locator'])}: „{e['quote']}“")
    if comments:
        out.append("")
        out.append("## Kommentare")
        for c in comments:
            out.append(f"- {c['author']} ({c['created_at']}): {c['body']}")
    out.append("")
    return "\n".join(out)


def _pct(x: str | None) -> str:
    return "unbekannt" if x is None else f"{float(x) * 100:.0f} %"


def evidence_csv(doc: m.DecisionDocument, evidence: list[dict[str, Any]]) -> str:
    header = ["entscheidung_id", "revision", "status", "beziehung", "kunde", "datum", "kanal", "zitat", "fundstelle", "quelle", "datei", "herkunft", "chunk_id"]
    return to_csv(header, [[str(doc.id), doc.revision, doc.state, RELATION_DE.get(e["relation"], e["relation"]), e["customer"], e["occurred_at"], e["channel"], e["quote"],
                            locator_text(e["locator"]), e["source_title"], e["file_name"], e["origin"], e["chunk_id"]] for e in evidence])


def scores_csv(doc: m.DecisionDocument) -> str:
    sc = doc.scoring_snapshot
    header = ["entscheidung_id", "revision", "option", "gesamtscore", "kriterium", "gewicht", "wirksames_gewicht", "wert", "punkte", "status", "hinweis"]
    rows = []
    for r in sc.get("results", []):
        if not r["contributions"]:
            rows.append([str(doc.id), doc.revision, r["name"], r["total"] or "nicht berechenbar", "", "", "", "", "", "blockiert" if r["blocked"] else "", ""])
        for c in r["contributions"]:
            rows.append([str(doc.id), doc.revision, r["name"], r["total"] or "", c["criterion"], c["weight"], c["effective_weight"], c["value"] or "unbekannt", c["points"], c["status"], c["note"]])
    return to_csv(header, rows)
