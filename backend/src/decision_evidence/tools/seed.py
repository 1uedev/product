"""Demo/test seed. Idempotent: re-running never duplicates data. Only runs in demo/test mode (see bootstrap.py).

The synthetic datasets go through the same import pipeline as user uploads (parsing, plan, apply), so the demo
exercises the real code paths. Curated problems/initiatives/decisions are created with the real services.
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from decision_evidence.config import get_settings
from decision_evidence.db import models as m
from decision_evidence.db.engines import get_engine
from decision_evidence.db.session import plain_session, tenant_session
from decision_evidence.modules.decisions import service as decisions
from decision_evidence.modules.imports import service as imports
from decision_evidence.modules.problems.service import log_history
from decision_evidence.modules.scoring.service import ensure_default_policy
from decision_evidence.storage.port import new_object_key
from decision_evidence.storage.s3 import get_storage
from decision_evidence.tenancy.context import add_audit
from decision_evidence.tools import demo_data as dd

NS = uuid.UUID("6f9e0f2e-6a58-4b3a-9a38-0d7a1e7d1c01")
DEMO_USERS = {  # key -> (email, display name); subjects are fixed so the Keycloak realm export matches
    "alice": ("alice@lumen.example", "Alice Owner"),
    "dora": ("dora@lumen.example", "Dora Admin"),
    "bob": ("bob@lumen.example", "Bob Editor"),
    "vera": ("vera@lumen.example", "Vera Viewer"),
    "finn": ("finn@fjord.example", "Finn Fjord"),
}


def user_subject(key: str) -> str:
    return str(uuid.uuid5(NS, f"user/{key}"))


def tenant_uuid(slug: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"tenant/{slug}")


TENANTS = [
    ("lumen-demo", "Lumen Analytics GmbH (Demo)", {"alice": "owner", "dora": "admin", "bob": "editor", "vera": "viewer"}),
    ("fjord-demo", "Fjord Systems AG (Demo)", {"finn": "owner"}),
]
E2E_MEMBERS = {"alice": "owner", "dora": "admin", "bob": "editor", "vera": "viewer"}
E2E_TENANTS = [  # only created in APP_ENV=test: empty workspaces for the browser and resilience tests
    ("e2e-clean", "E2E Leerer Workspace (Test)", E2E_MEMBERS),
    ("e2e-resilience", "E2E Resilienz Workspace (Test)", E2E_MEMBERS),
]


def ensure_users_and_tenants(include_e2e: bool) -> dict[str, uuid.UUID]:
    issuer = get_settings().oidc_issuer
    users: dict[str, uuid.UUID] = {}
    with plain_session(get_engine("migrator")) as s:
        for key, (email, name) in DEMO_USERS.items():
            u = s.scalar(select(m.User).where(m.User.oidc_issuer == issuer, m.User.oidc_subject == user_subject(key)))
            if u is None:
                u = m.User(oidc_issuer=issuer, oidc_subject=user_subject(key), email=email, display_name=name)
                s.add(u)
                s.flush()
            users[key] = u.id
        for slug, name, members in TENANTS + (E2E_TENANTS if include_e2e else []):
            tid = tenant_uuid(slug)
            if s.get(m.Tenant, tid) is None:
                s.add(m.Tenant(id=tid, slug=slug, name=name))
                s.flush()
            for ukey, role in members.items():
                if s.get(m.Membership, (tid, users[ukey])) is None:
                    s.add(m.Membership(tenant_id=tid, user_id=users[ukey], role=role))
    for slug, _n, _m in TENANTS + (E2E_TENANTS if include_e2e else []):
        with tenant_session(get_engine("migrator"), tenant_uuid(slug)) as s:
            if s.scalar(select(m.TenantSettings.id)) is None:
                s.add(m.TenantSettings(tenant_id=tenant_uuid(slug), is_demo=True))
            ensure_default_policy(s)
    return users


def _store_import(s: Session, user: uuid.UUID, tenant_id: uuid.UUID, kind: str, name: str, content: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    data = content.encode()
    key = new_object_key(tenant_id)
    get_storage().put(key, data, "text/csv" if name.endswith(".csv") else "text/plain")
    f = m.FileRecord(object_key=key, original_name=name, media_type="text/csv" if name.endswith(".csv") else "text/plain",
                     byte_size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                     purpose="import_csv" if name.endswith(".csv") else "import_text", status="ready", uploaded_by=user)
    s.add(f)
    s.flush()
    detected = imports.detect_batch(kind, name, data, 100000)
    batch = imports.create_batch(s, user, kind, f, detected, imports._norm_options(kind, {"synthetic": True, **(options or {})}))
    plan = imports.compute_import_plan(s, batch, f, data)
    if plan["blocking"]:
        raise RuntimeError(f"seed import {name} blocked: {plan['blocking']}")
    result = imports.apply_plan(s, batch, f, plan)
    batch.status, batch.result = "committed", result
    batch.preview = imports.summarise_plan(plan)
    add_audit(s, user, "import.committed", "import_batch", batch.id, {"kind": kind, "seed": True, **{k: v for k, v in result.items() if isinstance(v, int)}})
    return result


def _evidence(s: Session, problem: m.Problem, statements: list[dd.Statement], actor: uuid.UUID, verify_every: int = 5) -> int:
    by_ext = {fi.external_id: fi for fi in s.scalars(select(m.FeedbackItem).where(m.FeedbackItem.external_id.in_([x.external_id for x in statements])))}
    created = 0
    for i, st in enumerate(statements):
        fi = by_ext.get(st.external_id)
        if fi is None:
            continue
        chunk = s.scalar(select(m.SourceChunk).where(m.SourceChunk.source_record_id == fi.source_record_id).order_by(m.SourceChunk.ordinal))
        theme = dd.THEMES[st.theme or ""]
        core = next((c for c in (theme.supports + theme.counters) if c in st.text), st.text)
        verified = (i % verify_every) < 3
        s.add(m.ProblemEvidence(problem_id=problem.id, feedback_item_id=fi.id, source_chunk_id=chunk.id, relation=st.relation, extracted_quote=core,
                                origin="human", human_verified=verified, verified_by=actor if verified else None,
                                verified_at=func.now() if verified else None))
        created += 1
    return created


def seed_lumen(users: dict[str, uuid.UUID]) -> None:
    tid = tenant_uuid("lumen-demo")
    ds = dd.generate("full")
    with tenant_session(get_engine("migrator"), tid) as s:
        if (s.scalar(select(func.count()).select_from(m.CustomerAccount)) or 0) > 0:
            return
        alice, bob, dora = users["alice"], users["bob"], users["dora"]
        _store_import(s, alice, tid, "customers", "kunden.csv", ds.customers_csv)
        _store_import(s, alice, tid, "opportunities", "opportunities.csv", ds.opportunities_csv)
        _store_import(s, bob, tid, "feedback", "feedback.csv", ds.feedback_csv)
        _store_import(s, bob, tid, "notes_text", "gespraechsnotiz-hanse.txt", dd.NOTE_TEXT,
                      {"title": "Gesprächsnotiz Jour fixe, Hanse Reederei", "customer_external_id": "K-1013", "channel": "call", "occurred_at": "2026-09-18"})
        # curated problems (6): four confirmed, two still proposed; billing and noise stay unassigned for the analysis demo
        status = {"export": "confirmed", "sso": "confirmed", "mobile": "confirmed", "permissions": "confirmed", "reports": "proposed", "api": "proposed"}
        problems: dict[str, m.Problem] = {}
        for key in ("export", "sso", "mobile", "reports", "api", "permissions"):
            t = dd.THEMES[key]
            p = m.Problem(title=t.title, description=t.description, status=status[key], origin="manual", created_by=alice, owner_user_id=dora if key in ("export", "sso") else None)
            s.add(p)
            s.flush()
            n = _evidence(s, p, [x for x in ds.statements if x.theme == key], alice)
            log_history(s, p.id, "created", alice, seed=True, evidence=n)
            problems[key] = p
        s.flush()
        policy = ensure_default_policy(s)
        init_specs = [
            ("export", "Streaming-Export für große Tabellen", "CSV-Exporte mit über 100.000 Zeilen laufen zuverlässig durch.", "enterprise", Decimal(20), Decimal(35)),
            ("sso", "SAML-SSO für Unternehmenskunden", "Enterprise-Kunden können sich mit ihrem Identity Provider anmelden.", "enterprise", Decimal(40), Decimal(70)),
            ("mobile", "Offline-fähige mobile Erfassung", "Außendienst kann Aufträge ohne Netz erfassen und später synchronisieren.", "mid-market", None, None),
        ]
        inits: dict[str, m.Initiative] = {}
        for key, title, outcome, seg, lo, hi in init_specs:
            i = m.Initiative(problem_id=problems[key].id, title=title, desired_outcome=outcome, target_segment=seg, effort_low=lo, effort_high=hi,
                             status="proposed", owner_user_id=dora, created_by=bob)
            s.add(i)
            s.flush()
            inits[key] = i
        exp_chunk = s.scalar(select(m.ProblemEvidence.source_chunk_id).where(m.ProblemEvidence.problem_id == problems["export"].id, m.ProblemEvidence.relation == "supports"))
        s.add_all([
            m.InitiativeAssumption(initiative_id=inits["export"].id, statement="Die Abbrüche entstehen beim Aufbau der gesamten Datei im Speicher.", kind="hypothesis", owner_user_id=bob),
            m.InitiativeAssumption(initiative_id=inits["export"].id, statement="Ein Streaming-Export lässt sich in 20 bis 35 Personentagen umsetzen.", kind="estimate", owner_user_id=bob),
            m.InitiativeAssumption(initiative_id=inits["export"].id, statement="Kunden melden Abbrüche ab etwa 50.000 Zeilen.", kind="observed", source_chunk_id=exp_chunk, validation_status="validated"),
            m.InitiativeAssumption(initiative_id=inits["sso"].id, statement="Mit SAML sind die genannten Ausschlusskriterien der Informationssicherheit erfüllt.", kind="hypothesis", owner_user_id=dora),
            m.InitiativeAssumption(initiative_id=inits["sso"].id, statement="Zwei Identity Provider decken die meisten Anfragen ab.", kind="estimate"),
        ])
        s.flush()
        # decision 1: approved by another person (four-eyes), then new knowledge arrives (drift demo)
        def option(name: str, desc: str, lo: int, hi: int, segs: list[str], risk: str, effects: str) -> dict[str, Any]:
            return {"name": name, "description": desc, "effort_low": lo, "effort_high": hi, "effort_unit": "person_days", "addressed_segments": segs,
                    "expected_effects": effects, "risks": "Technische Risiken sind noch nicht vollständig bewertet.", "risk_rating": risk}
        d1 = decisions.create_revision(s, inits["export"].id, bob)
        d1.title = "Entscheidungsvorlage: Streaming-Export"
        d1.options = decisions.normalise_options([
            option("Streaming-Export", "Export wird in Blöcken gestreamt, keine vollständige Datei im Speicher.", 20, 35, [], "medium", "Abbrüche bei großen Tabellen sollten entfallen."),
            option("Export-Limit mit Hinweis", "Hartes Limit von 50.000 Zeilen mit klarer Fehlermeldung und Anleitung zum Teilen.", 3, 6, ["smb"], "low", "Weniger Frust durch klare Meldung, Problem bleibt bestehen."),
        ])
        d1.recommendation_text = ("Wir empfehlen, den Streaming-Export einzuplanen. Begründung: Das Problem betrifft mehrere Segmente, die Belege sind überwiegend aktuell. "
                                  "Die laute Einzelquelle K-1001 haben wir bei der Bewertung nur als einen Kunden gezählt. Offen bleibt die technische Machbarkeit.")
        decisions.refresh_snapshot(s, d1)
        decisions.submit(s, d1, bob)
        decisions.approve(s, d1, dora, allow_self=False)
        add_audit(s, dora, "decision.approved", "decision_document", d1.id, {"revision": 1, "seed": True})
        s.flush()
        # decision 2: in review
        d2 = decisions.create_revision(s, inits["sso"].id, bob)
        d2.title = "Entscheidungsvorlage: SAML-SSO"
        d2.options = decisions.normalise_options([
            option("Eigene SAML-Implementierung", "SAML 2.0 selbst implementieren.", 40, 70, ["enterprise"], "high", "Volle Kontrolle, höherer Aufwand."),
            option("Anbindung über Broker", "Anmeldung über einen vorhandenen Identity-Broker.", 15, 25, ["enterprise", "mid-market"], "medium", "Schneller verfügbar, zusätzliche Abhängigkeit."),
        ])
        d2.recommendation_text = "Die Anbindung über einen Broker erscheint wegen des geringeren Aufwands vorzugswürdig; die Belege stammen überwiegend von Enterprise-Kunden. Bitte Prüfung."
        decisions.refresh_snapshot(s, d2)
        decisions.submit(s, d2, bob)
        s.add(m.DecisionComment(decision_document_id=d2.id, author_user_id=dora, body="Bitte die Annahme zu den Identity Providern noch mit zwei Kundengesprächen validieren."))
        s.flush()
        _ = policy
        # new knowledge after the approval of decision 1
        late = "external_id,customer_external_id,opportunity_external_id,channel,occurred_at,body,language\n" + "\n".join([
            "T-30001,K-1005,,ticket,2026-09-28,Der CSV-Export bricht auch bei uns ab sobald die Tabelle mehr als 50.000 Zeilen hat.,de",
            "T-30002,K-1006,,email,2026-09-29,Neu seit dem letzten Update: Beim Export nach CSV kommt ein Timeout und die Datei bleibt unvollständig.,de",
            "T-30003,K-1016,,chat,2026-09-30,Der Export funktioniert bei uns weiterhin einwandfrei, kein Problem.,de"])
        _store_import(s, bob, tid, "feedback", "feedback-nachtrag.csv", late + "\n")
        extra = {"T-30001": "supports", "T-30002": "supports", "T-30003": "contradicts"}
        for ext, rel in extra.items():
            fi = s.scalar(select(m.FeedbackItem).where(m.FeedbackItem.external_id == ext))
            chunk = s.scalar(select(m.SourceChunk).where(m.SourceChunk.source_record_id == fi.source_record_id))
            s.add(m.ProblemEvidence(problem_id=problems["export"].id, feedback_item_id=fi.id, source_chunk_id=chunk.id, relation=rel, extracted_quote=chunk.text[:160],
                                    origin="human", human_verified=False))
        add_audit(s, alice, "seed.completed", "tenant", tid, {"profile": "full"})


def seed_fjord(users: dict[str, uuid.UUID]) -> None:
    tid = tenant_uuid("fjord-demo")
    ds = dd.generate("small", seed=99)
    with tenant_session(get_engine("migrator"), tid) as s:
        if (s.scalar(select(func.count()).select_from(m.CustomerAccount)) or 0) > 0:
            return
        finn = users["finn"]
        renamed = ds.customers_csv.replace("GmbH", "ApS").replace("AG", "A/S")
        _store_import(s, finn, tid, "customers", "kunden.csv", renamed)
        _store_import(s, finn, tid, "opportunities", "opportunities.csv", ds.opportunities_csv)
        _store_import(s, finn, tid, "feedback", "feedback.csv", ds.feedback_csv.replace("T-", "FJ-"))
        p = m.Problem(title="Fjord-Mandant: Streaming-Export gewünscht", description="Nur im Mandanten Fjord sichtbar (Isolationsnachweis).", status="confirmed", created_by=finn)
        s.add(p)
        s.flush()
        add_audit(s, finn, "seed.completed", "tenant", tid, {"profile": "small"})


def run(include_e2e: bool) -> None:
    users = ensure_users_and_tenants(include_e2e)
    seed_lumen(users)
    seed_fjord(users)
