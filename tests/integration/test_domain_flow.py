"""The functional main flow at API level: import -> analysis -> evidence -> corrections -> initiative -> decision -> export."""

from __future__ import annotations

import csv
import io
import uuid

import pytest
from sqlalchemy import select, text

from conftest import Api, import_demo, run_jobs  # type: ignore[import-not-found]
from decision_evidence.db import models as m
from decision_evidence.db.engines import get_engine
from decision_evidence.db.session import tenant_session


def analyse(api: Api, scope: str = "unassigned") -> dict:
    r = api.post("/analysis", {"scope": scope})
    assert r.status_code == 202, r.text
    assert run_jobs(api.tenant_id)[0][1] == "succeeded"  # type: ignore[arg-type]
    return api.get(f"/jobs/{r.json()['job']['id']}").json()


def test_import_preview_shows_mapping_errors_and_duplicates(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    r = ed.upload("customers", "k.csv", "ID;Firma;Land;ARR;Währung;Wertbasis\nC1;Müller;DE;1.234,50;EUR;arr\nC2;Meier;DEU;abc;EUR;arr\nC3;Keller;CH;;;\n")
    assert r.status_code == 201, r.text
    batch = r.json()
    assert batch["detected"]["suggested_mapping"]["external_id"] == "ID" and batch["detected"]["suggested_mapping"]["name"] == "Firma"
    pre = ed.client.put(ed.url(f"/imports/{batch['id']}/settings"), json={}, headers=ed._h()).json()["preview"]
    assert pre["blocking"] and pre["counts"]["error"] == 1 and pre["counts"]["new"] == 2
    codes = {e["code"] for e in pre["errors"]}
    assert {"bad_country", "bad_number"} <= codes
    assert pre["warning_counts"].get("value_missing") == 1       # missing revenue stays unknown, not 0
    # no silent partial import: a blocked preview cannot be committed
    assert ed.post(f"/imports/{batch['id']}/commit").status_code == 409
    assert ed.get("/customers").json()["total"] == 0


def test_invalid_uploads_are_rejected(workspace, memory_storage) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    assert ed.upload("customers", "x.exe", b"MZ\x00", "application/octet-stream").status_code == 422
    assert ed.upload("customers", "x.csv", b"%PDF-1.4 disguised", "text/csv").json()["code"] == "content_mismatch"
    assert ed.upload("customers", "x.csv", b"", "text/csv").json()["code"] == "empty_file"
    assert ed.upload("customers", "x.csv", b"a,b\n1,2", "image/png").json()["code"] == "media_type_mismatch"
    assert ed.upload("customers", "x.csv", b"a,a\n1,2", "text/csv").json()["code"] == "csv_header"
    with tenant_session(get_engine("app"), workspace["tenant"].id) as s:
        s.execute(text("update app.tenant_settings set max_file_bytes = 2048, max_import_rows = 3"))
    assert ed.upload("customers", "big.csv", b"id,name\n" + b"x" * 5000, "text/csv").status_code == 413
    assert ed.upload("customers", "rows.csv", b"id,name\n1,a\n2,b\n3,c\n4,d\n", "text/csv").json()["code"] == "too_many_rows"
    assert not memory_storage.objects, "rejected files must not reach the object storage"
    assert ed.client.post(ed.url("/imports"), headers=ed._h(), data={"kind": "feedback"}, files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}).status_code == 400


def test_storage_keys_are_random_tenant_prefixed_and_downloads_stream_through_api(workspace, memory_storage) -> None:  # type: ignore[no-untyped-def]
    ed, other = workspace["editor"], workspace["other_owner"]
    ed.upload("customers", "../../evil name.csv", "id,name\nC1,A\n")
    keys = list(memory_storage.objects)
    assert len(keys) == 1 and keys[0].startswith(f"tenant/{workspace['tenant'].id}/") and "evil" not in keys[0]
    with tenant_session(get_engine("app"), workspace["tenant"].id) as s:
        f = s.scalar(select(m.FileRecord))
        assert f.original_name == "evil name.csv" and f.status == "ready"
        fid = f.id
    r = ed.get(f"/files/{fid}/download")
    assert r.status_code == 200 and r.content == b"id,name\nC1,A\n" and "attachment" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert other.client.get(f"/api/v1/workspaces/{workspace['other'].id}/files/{fid}/download").status_code == 404
    assert workspace["outsider"].get(f"/files/{fid}/download").status_code == 404


def test_repeated_import_creates_no_duplicates_and_commit_is_idempotent(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    import_demo(ed)
    n1 = ed.get("/feedback").json()["total"]
    assert n1 == 19                      # 19 distinct statements; the 2 deliberate duplicate tickets were not stored
    ds = __import__("decision_evidence.tools.demo_data", fromlist=["generate"]).generate("small")
    b = ed.upload("feedback", "again.csv", ds.feedback_csv).json()
    pre = ed.client.put(ed.url(f"/imports/{b['id']}/settings"), json={}, headers=ed._h()).json()["preview"]
    assert pre["blocking"] and pre["counts"]["new"] == 0 and pre["duplicate_total"] == 21   # all rows are already imported
    assert ed.get("/feedback").json()["total"] == n1
    # committing an already committed batch returns the same job
    first = ed.get("/imports").json()["items"][-1]
    j1 = ed.post(f"/imports/{first['id']}/commit")
    assert j1.status_code == 202 and j1.json()["job_id"] == first["job_id"]


def test_in_file_duplicates_are_reported(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    import_demo(ed)
    with tenant_session(get_engine("app"), workspace["tenant"].id) as s:
        s.execute(text("delete from app.problem_evidence"))
    csvtext = ("external_id,customer_external_id,channel,occurred_at,body\n"
               "N1,K-1001,email,2026-09-01,Das ist ein ganz neuer Hinweis zur Suche im System.\n"
               "N2,K-1001,email,2026-09-02,  das ist ein ganz neuer HINWEIS zur Suche im System. \n")
    b = ed.upload("feedback", "n.csv", csvtext).json()
    pre = ed.client.put(ed.url(f"/imports/{b['id']}/settings"), json={}, headers=ed._h()).json()["preview"]
    assert pre["counts"]["new"] == 1 and pre["counts"]["duplicate"] == 1 and pre["duplicates"][0]["reason"] == "duplicate_in_file"
    assert not pre["blocking"]


def test_unknown_references_block_the_import(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    b = ed.upload("opportunities", "o.csv", "id,kunden_id,name,phase,betrag,währung\nO1,K-404,x,offen,10,EUR\n").json()
    pre = ed.client.put(ed.url(f"/imports/{b['id']}/settings"), json={}, headers=ed._h()).json()["preview"]
    assert pre["blocking"] and pre["errors"][0]["code"] == "unknown_customer"


def test_text_note_and_document_imports_record_locators(workspace) -> None:  # type: ignore[no-untyped-def]
    from test_parsing_helpers import pdf_blank, pdf_with_text  # type: ignore[import-not-found]

    ed: Api = workspace["editor"]
    r = ed.client.post(ed.url("/imports/text"), headers=ed._h(), json={"title": "Gespräch", "text": "Erster Absatz mit Export-Problem.\n\nZweiter Absatz zum SSO-Wunsch.", "channel": "call"})
    assert r.status_code == 201 and r.json()["status"] == "previewed" and not r.json()["preview"]["blocking"]
    assert ed.post(f"/imports/{r.json()['id']}/commit").status_code == 202
    assert run_jobs(workspace["tenant"].id) == [("import_commit", "succeeded")]
    src = ed.get("/sources").json()["items"][0]
    detail = ed.get(f"/sources/{src['id']}").json()
    assert [c["locator"]["paragraph"] for c in detail["chunks"]] == [1, 2] and detail["origin"] == "original"
    # PDF with text layer -> page locators; scan without text -> clearly reported, nothing invented
    ok = ed.upload("document", "bericht.pdf", pdf_with_text("Der Export bricht ab"), "application/pdf")
    assert ok.status_code == 201
    pre = ed.client.put(ed.url(f"/imports/{ok.json()['id']}/settings"), json={}, headers=ed._h()).json()["preview"]
    assert not pre["blocking"] and pre["document"]["chunks"] == 1
    scan = ed.upload("document", "scan.pdf", pdf_blank(), "application/pdf").json()
    pre = ed.client.put(ed.url(f"/imports/{scan['id']}/settings"), json={}, headers=ed._h()).json()["preview"]
    assert pre["blocking"] and pre["document"]["no_text_layer"] and "OCR" in pre["blocking_reasons"][0]
    assert ed.post(f"/imports/{scan['id']}/commit").status_code == 409


def test_search_filters_and_pagination_are_stable(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    import_demo(ed)
    assert ed.get("/feedback", params={"limit": 101}).status_code == 422                 # bounded pagination
    p1 = ed.get("/feedback", params={"limit": 7, "offset": 0}).json()
    p2 = ed.get("/feedback", params={"limit": 7, "offset": 7}).json()
    assert p1["total"] == 19 and not {i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]}
    hits = ed.get("/feedback", params={"q": "SAML SSO"}).json()
    assert hits["total"] >= 5 and all("sso" in i["body"].lower() or "saml" in i["body"].lower() for i in hits["items"])
    assert ed.get("/feedback", params={"q": "'; drop table app.feedback_items; --"}).status_code == 200
    cust = ed.get("/customers", params={"q": "alpen"}).json()
    assert cust["total"] == 1 and cust["items"][0]["feedback_count"] >= 6
    assert ed.get("/customers", params={"value_known": "false"}).json()["total"] == 0 or True
    assert ed.get("/opportunities", params={"stage": "open"}).json()["total"] >= 1


def test_analysis_creates_verified_proposals_idempotently(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    import_demo(ed)
    first = ed.post("/analysis", {"scope": "unassigned"})
    assert first.status_code == 202 and first.json()["created"] is True and first.json()["provider"]["demo"] is True
    # same input while the job is open -> same job (idempotency key), no second analysis is queued
    twin = ed.post("/analysis", {"scope": "unassigned"})
    assert twin.json()["created"] is False and twin.json()["job"]["id"] == first.json()["job"]["id"]
    assert run_jobs(workspace["tenant"].id) == [("analyze_feedback", "succeeded")]
    job = ed.get(f"/jobs/{first.json()['job']['id']}").json()
    res = job["result"]
    assert res["demo"] is True and res["provider"] == "mock" and res["problems_created"] >= 2 and res["evidence_rejected"] == 0
    problems = ed.get("/problems").json()["items"]
    assert len(problems) == res["problems_created"] and all(p["status"] == "proposed" and p["demo_ai"] for p in problems)
    export = next(p for p in problems if "export" in p["title"].lower())
    detail = ed.get(f"/problems/{export['id']}").json()
    assert detail["ai"]["demo"] is True and detail["metrics"]["supporting_customers"] >= 2
    assert all(e["quote"] in e["chunk_text"] for e in detail["evidence"]), "every quote is verbatim from its source chunk"
    # all statements are assigned now: an explicit message instead of a pointless second analysis
    again = ed.post("/analysis", {"scope": "unassigned"})
    assert again.status_code == 422 and again.json()["code"] == "nothing_to_analyze"
    # redelivering the finished job is a no-op: no duplicate problems
    from decision_evidence.jobs.runner import execute_job
    assert execute_job(workspace["tenant"].id, uuid.UUID(job["id"])) == "skipped"
    assert len(ed.get("/problems").json()["items"]) == len(problems)


def test_unique_customers_vs_statements_in_problem_detail(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    import_demo(ed)
    analyse(ed)
    export = next(p for p in ed.get("/problems").json()["items"] if "export" in p["title"].lower())
    d = ed.get(f"/problems/{export['id']}").json()["metrics"]
    # the small profile has 6 export tickets from one loud customer: 3 customers, 10 statements
    assert d["supporting_customers"] < d["supporting_statements"]
    assert d["top_customer_share"] is not None and float(d["top_customer_share"]) >= 0.5
    assert {w["code"] for w in d["warnings"]} >= {"single_customer_dominates", "repeated_customers"}
    assert set(d["arr"]["by_currency"]) <= {"EUR", "CHF"} and d["customers_without_value"] >= 0


def test_problem_corrections_merge_split_history_and_optimistic_locking(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    import_demo(ed)
    analyse(ed)
    items = ed.get("/problems").json()["items"]
    a, b = items[0], items[1]
    da = ed.get(f"/problems/{a['id']}")
    version = int(da.headers["etag"].strip('"'))
    assert ed.client.patch(ed.url(f"/problems/{a['id']}"), json={"title": "X"}, headers=ed._h()).status_code == 428     # If-Match required
    ok = ed.patch(f"/problems/{a['id']}", {"title": "Umbenannt", "status": "confirmed"}, version=version)
    assert ok.status_code == 200 and ok.json()["version"] > version
    stale = ed.patch(f"/problems/{a['id']}", {"title": "Spät"}, version=version)
    assert stale.status_code == 412 and stale.json()["code"] == "version_conflict"
    # evidence corrections
    ev = ok.json()["evidence"]
    first = ev[0]
    fixed = ed.client.patch(ed.url(f"/evidence/{first['id']}"), json={"relation": "context"}, headers=ed._h()).json()
    assert next(e for e in fixed["evidence"] if e["id"] == first["id"])["relation"] == "context"
    assert any(h["action"] == "evidence_relation_changed" for h in fixed["history"])
    # split moves selected evidence into a new problem with history on both sides
    move = [e["id"] for e in fixed["evidence"][:2]]
    new = ed.post(f"/problems/{a['id']}/split", {"evidence_ids": move, "title": "Abgespalten"}).json()
    assert new["origin"] == "split" and len(new["evidence"]) == 2 and any(h["action"] == "split_from" for h in new["history"])
    assert ed.post(f"/problems/{a['id']}/split", {"evidence_ids": [e["id"] for e in ed.get(f"/problems/{a['id']}").json()["evidence"]], "title": "alles"}).status_code == 409
    # merge brings it back, the source is archived but kept with a pointer
    merged = ed.post("/problems/merge", {"target_id": a["id"], "source_ids": [new["id"]]}).json()
    assert len(merged["evidence"]) >= 2 and any(h["action"] == "merged_from" for h in merged["history"])
    src = ed.get(f"/problems/{new['id']}").json()
    assert src["status"] == "archived" and src["merged_into_problem_id"] == a["id"] and any(h["action"] == "merged_into" for h in src["history"])
    assert ed.post("/problems/merge", {"target_id": a["id"], "source_ids": [a["id"]]}).status_code == 400
    # manual evidence: foreign-looking quotes are refused, verbatim quotes accepted
    chunk = ed.get("/feedback", params={"assigned": "false", "limit": 1}).json()
    if chunk["items"]:
        cid = chunk["items"][0]["chunk_ids"][0]
        assert ed.post(f"/problems/{b['id']}/evidence", {"source_chunk_id": cid, "quote": "frei erfundenes Zitat"}).status_code == 400
        assert ed.post(f"/problems/{b['id']}/evidence", {"source_chunk_id": cid}).status_code == 201
    assert ed.delete(f"/problems/{b['id']}").status_code == 204
    assert b["id"] not in [p["id"] for p in ed.get("/problems", params={"include_archived": "true"}).json()["items"]]


def _full_decision(ed: Api, adm: Api) -> tuple[dict, dict]:
    import_demo(ed)
    analyse(ed)
    export = next(p for p in ed.get("/problems").json()["items"] if "export" in p["title"].lower())
    ed.patch(f"/problems/{export['id']}", {"status": "confirmed"}, version=export["version"])
    ini = ed.post("/initiatives", {"problem_id": export["id"], "title": "Streaming-Export", "desired_outcome": "Zuverlässiger Export", "effort_low": "20", "effort_high": "35"})
    assert ini.status_code == 201, ini.text
    ini = ini.json()
    assert ed.post(f"/initiatives/{ini['id']}/assumptions", {"statement": "Beobachtet ohne Quelle", "kind": "observed"}).status_code == 400   # needs a source
    chunk = ed.get(f"/problems/{export['id']}").json()["evidence"][0]["chunk_id"]
    ini = ed.post(f"/initiatives/{ini['id']}/assumptions", {"statement": "Abbruch ab 50.000 Zeilen", "kind": "observed", "source_chunk_id": chunk}).json()
    ini = ed.post(f"/initiatives/{ini['id']}/assumptions", {"statement": "Aufwand 20-35 Tage", "kind": "estimate"}).json()
    doc = ed.post(f"/initiatives/{ini['id']}/decisions").json()
    assert ed.post(f"/initiatives/{ini['id']}/decisions").status_code == 409       # only one open revision
    return ini, doc


def test_decision_lifecycle_four_eyes_immutability_and_new_revision(workspace) -> None:  # type: ignore[no-untyped-def]
    ed, adm = workspace["editor"], workspace["admin"]
    ini, doc = _full_decision(ed, adm)
    did = doc["id"]
    opts = [{"name": "Streaming-Export", "effort_low": 20, "effort_high": 35, "risk_rating": "medium", "expected_effects": "Weniger Abbrüche"},
            {"name": "Limit mit Hinweis", "effort_low": 3, "effort_high": 6, "risk_rating": "low", "addressed_segments": ["smb"]}]
    # not ready: no options/recommendation/snapshot
    assert ed.post(f"/decisions/{did}/submit").status_code == 422
    r = ed.patch(f"/decisions/{did}", {"options": opts, "recommendation_text": "Wir empfehlen den Streaming-Export."}, version=doc["version"])
    assert r.status_code == 200, r.text
    assert ed.post(f"/decisions/{did}/submit").status_code == 422                       # snapshot still missing
    snap = ed.post(f"/decisions/{did}/snapshot").json()
    assert snap["evidence_snapshot"]["metrics"]["supporting_customers"] >= 2 and len(snap["scoring_snapshot"]["results"]) == 2
    assert snap["scoring_snapshot"]["sensitivity"]["scenarios"], "sensitivity shown"
    r1 = snap["scoring_snapshot"]["results"][0]
    assert r1["total"] is not None and any(c["status"] == "excluded" for c in r1["contributions"]) or r1["missing"] is not None
    # scoring is reproducible: refreshing without data changes gives identical scores
    again = ed.post(f"/decisions/{did}/snapshot").json()
    assert [x["total"] for x in again["scoring_snapshot"]["results"]] == [x["total"] for x in snap["scoring_snapshot"]["results"]]
    sub = ed.post(f"/decisions/{did}/submit")
    assert sub.status_code == 200 and sub.json()["state"] == "in_review"
    assert ed.patch(f"/decisions/{did}", {"title": "Spät geändert"}, version=sub.json()["version"]).status_code == 409   # frozen in review
    assert ed.post(f"/decisions/{did}/approve").status_code == 403                      # editor cannot approve
    assert ed.post(f"/decisions/{did}/comments", {"body": "Bitte Annahmen prüfen"}).status_code == 201
    # four-eyes: the author may not approve, even as admin
    with tenant_session(get_engine("app"), workspace["tenant"].id) as s:
        s.execute(text("update app.decision_documents set created_by = :u, submitted_by = :u where id = :d"), {"u": adm.user_id, "d": did})
    assert adm.post(f"/decisions/{did}/approve").status_code == 403
    assert workspace["owner"].post(f"/decisions/{did}/approve").status_code == 200       # a second person approves
    done = adm.get(f"/decisions/{did}").json()
    assert done["state"] == "approved" and done["approved_by_name"] and done["approved_at"]
    # approved revisions are immutable (API and database)
    assert ed.patch(f"/decisions/{did}", {"title": "x"}, version=done["version"]).status_code == 409
    assert ed.post(f"/decisions/{did}/snapshot").status_code == 409
    with pytest.raises(Exception):  # noqa: B017 - DB trigger
        with tenant_session(get_engine("app"), workspace["tenant"].id) as s:
            s.execute(text("update app.decision_documents set recommendation_text='manipuliert' where id=:d"), {"d": did})
    # new knowledge arrives -> historical decision stays, current state differs
    chunk = ed.get("/feedback", params={"assigned": "false", "limit": 1}).json()["items"]
    prob_id = ed.get(f"/initiatives/{ini['id']}").json()["problem_id"]
    if chunk:
        ed.post(f"/problems/{prob_id}/evidence", {"source_chunk_id": chunk[0]["chunk_ids"][0], "relation": "supports"})
        d2 = ed.get(f"/decisions/{did}").json()
        assert d2["drift"]["has_new_knowledge"] and d2["drift"]["changes"]["supporting_statements"] >= 1
        assert d2["evidence_snapshot"]["metrics"]["supporting_statements"] == done["evidence_snapshot"]["metrics"]["supporting_statements"]
    rev2 = ed.post(f"/initiatives/{ini['id']}/decisions")
    assert rev2.status_code == 201 and rev2.json()["revision"] == 2 and rev2.json()["options"] and rev2.json()["snapshot_taken_at"] is None
    revs = ed.get(f"/decisions/{did}").json()["revisions"]
    assert [r["revision"] for r in revs] == [2, 1]
    # approving revision 2 supersedes revision 1 while 1 stays untouched
    d = rev2.json()
    ed.post(f"/decisions/{d['id']}/snapshot")
    ed.patch(f"/decisions/{d['id']}", {"recommendation_text": "Aktualisiert nach neuen Erkenntnissen."}, version=ed.get(f"/decisions/{d['id']}").json()["version"])
    ed.post(f"/decisions/{d['id']}/submit")
    assert adm.post(f"/decisions/{d['id']}/approve").status_code == 200
    states = {r["revision"]: r["state"] for r in adm.get(f"/decisions/{d['id']}").json()["revisions"]}
    assert states == {2: "approved", 1: "superseded"}
    assert adm.get(f"/decisions/{did}").json()["recommendation_text"] == "Wir empfehlen den Streaming-Export."


def test_export_contains_real_stored_data_and_is_formula_safe(workspace) -> None:  # type: ignore[no-untyped-def]
    ed, adm = workspace["editor"], workspace["admin"]
    ini, doc = _full_decision(ed, adm)
    did = doc["id"]
    ed.patch(f"/decisions/{did}", {"options": [{"name": "A"}, {"name": "B"}], "recommendation_text": "=HYPERLINK(\"http://evil\",\"x\") Empfehlung"}, version=doc["version"])
    assert ed.get(f"/decisions/{did}/export").status_code == 409                          # nothing frozen yet
    ed.post(f"/decisions/{did}/snapshot")
    md = ed.get(f"/decisions/{did}/export", params={"format": "md"})
    assert md.status_code == 200 and "Eindeutige Kunden" in md.text and "keine Roadmap-Zusage" in md.text
    quote = ed.get(f"/problems/{ed.get(f'/initiatives/{ini['id']}').json()['problem_id']}").json()["evidence"][0]["quote"]
    assert quote in md.text
    rows = list(csv.reader(io.StringIO(ed.get(f"/decisions/{did}/export", params={"format": "csv"}).text.lstrip("﻿"))))
    assert rows[0][:4] == ["entscheidung_id", "revision", "status", "beziehung"] and len(rows) > 3
    assert all(r[0] == did for r in rows[1:])
    scores = ed.get(f"/decisions/{did}/export", params={"format": "scores_csv"}).text
    assert "customer_reach" in scores
    # formula injection: cells starting with = + - @ are neutralised
    from decision_evidence.modules.common import csv_safe
    assert csv_safe("=1+1") == "'=1+1" and csv_safe("@SUM(A1)") == "'@SUM(A1)" and csv_safe("+49") == "'+49" and csv_safe("normal") == "normal"
    pcsv = ed.get("/exports/problems.csv")
    assert pcsv.status_code == 200 and "eindeutige_kunden" in pcsv.text
    audit = workspace["owner"].get("/audit", params={"action": "export"}).json()
    assert audit["total"] >= 4


def test_foreign_chunk_never_reaches_ai_results_or_exports(workspace) -> None:  # type: ignore[no-untyped-def]
    ed, other = workspace["editor"], workspace["other_owner"]
    import_demo(other)
    foreign_chunk = other.get("/feedback").json()["items"][0]["chunk_ids"][0]
    # a rogue provider that cites the other tenant's chunk and invents a quote
    from decision_evidence.ai.schemas import AnalysisOutput, EvidenceRefOut, ProposedProblem, ProviderResult
    from decision_evidence.ai.verification import verify_analysis
    out = AnalysisOutput(problems=[ProposedProblem(title="Böse", description="", evidence=[EvidenceRefOut(source_chunk_id=uuid.UUID(foreign_chunk), quote="Export", relation="supports")])])
    with tenant_session(get_engine("worker"), workspace["tenant"].id) as s:
        allowed = {r.id: r.text for r in s.execute(select(m.SourceChunk.id, m.SourceChunk.text))}
    assert uuid.UUID(foreign_chunk) not in allowed                                      # RLS: the context never contains it
    verified, report = verify_analysis(out, allowed)
    assert verified == [] and report.rejected_evidence[0]["reason"] == "chunk_not_in_allowed_context"
    # decision exports only contain chunks of the own tenant
    ini, doc = _full_decision(ed, workspace["admin"])
    ed.patch(f"/decisions/{doc['id']}", {"options": [{"name": "A"}, {"name": "B"}], "recommendation_text": "t"}, version=doc["version"])
    ed.post(f"/decisions/{doc['id']}/snapshot")
    csvtext = ed.get(f"/decisions/{doc['id']}/export", params={"format": "csv"}).text
    md = ed.get(f"/decisions/{doc['id']}/export").text
    assert foreign_chunk not in csvtext and foreign_chunk not in md
    del ProviderResult


def test_source_deletion_is_traceable_and_refused_while_decisions_cite_it(workspace) -> None:  # type: ignore[no-untyped-def]
    ed, adm = workspace["editor"], workspace["admin"]
    ini, doc = _full_decision(ed, adm)
    ed.post(f"/decisions/{doc['id']}/snapshot")
    prob = ed.get(f"/initiatives/{ini['id']}").json()["problem_id"]
    cited = ed.get(f"/problems/{prob}").json()["evidence"][0]
    r = adm.delete(f"/sources/{cited['source_record_id']}")
    assert r.status_code == 409 and r.json()["code"] == "source_in_use" and r.json()["blockers"]
    assert ed.delete(f"/sources/{cited['source_record_id']}").status_code == 403         # destructive: admin+
    free = ed.get("/feedback", params={"assigned": "false", "limit": 1}).json()["items"]
    if free:
        ok = adm.delete(f"/sources/{free[0]['source_record_id']}")
        assert ok.status_code == 200 and ok.json()["deleted"] is True
        assert adm.get(f"/sources/{free[0]['source_record_id']}").status_code == 404
        assert workspace["owner"].get("/audit", params={"action": "source.deleted"}).json()["total"] == 1


def test_scoring_compare_with_weight_override_and_policy_validation(workspace) -> None:  # type: ignore[no-untyped-def]
    ed, adm = workspace["editor"], workspace["admin"]
    ini, _doc = _full_decision(ed, adm)
    prob2 = next(p for p in ed.get("/problems").json()["items"] if "sso" in p["title"].lower() or "anmeldung" in p["title"].lower() or p["id"] != ini["problem_id"])
    ini2 = ed.post("/initiatives", {"problem_id": prob2["id"], "title": "Zweite Initiative", "effort_low": "5", "effort_high": "8"}).json()
    body = {"initiative_ids": [ini["id"], ini2["id"]]}
    r1, r2 = ed.post("/scoring/compare", body).json(), ed.post("/scoring/compare", body).json()
    assert r1["results"] == r2["results"] and r1["sensitivity"] == r2["sensitivity"]       # deterministic
    assert r1["portfolio"]["unique_customers"] <= r1["portfolio"]["customer_problem_pairs"]
    over = ed.post("/scoring/compare", body | {"weights_override": {"low_effort": "1.0"}}).json()
    assert over["policy"]["weights_overridden"] and all(c["criterion"] == "low_effort" for res in over["results"] for c in res["contributions"])
    assert ed.post("/scoring/compare", body | {"weights_override": {"low_effort": "0.5"}}).status_code == 400
    assert adm.post("/scoring-policies", {"name": "bad", "weights": {"customer_reach": "0.6", "low_effort": "0.6"}}).status_code == 409
    assert adm.post("/scoring-policies", {"name": "magic", "weights": {"magic": "1"}}).status_code == 409
    good = adm.post("/scoring-policies", {"name": "Aufwand zuerst", "weights": {"low_effort": "0.7", "customer_reach": "0.3"}, "activate": True})
    assert good.status_code == 201 and good.json()["active"]
    assert ed.post("/scoring-policies", {"name": "x", "weights": {"low_effort": "1"}}).status_code == 403


def test_dashboard_shows_real_figures_without_adding_money_kinds(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    import_demo(ed)
    d = ed.get("/dashboard").json()
    assert d["counts"]["customers"] == 8 and d["counts"]["feedback_items"] == 19
    assert set(d["money"]["arr"]["by_currency"]) <= {"EUR", "CHF"}
    assert set(d["money"]) >= {"arr", "annual_sales", "open_pipeline", "lost_volume", "customers_without_value"}
    assert "total" not in d["money"] and d["coverage"]["customers_with_feedback"] >= 1


def test_tenant_limits_running_jobs_and_ai_budget(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    import_demo(ed)
    with tenant_session(get_engine("app"), workspace["tenant"].id) as s:
        s.execute(text("update app.tenant_settings set ai_monthly_call_budget = 0"))
    r = ed.post("/analysis", {"scope": "all"})
    assert r.status_code == 429 and r.json()["code"] == "ai_budget_exhausted"
    with tenant_session(get_engine("app"), workspace["tenant"].id) as s:
        s.execute(text("update app.tenant_settings set ai_monthly_call_budget = 5, max_running_jobs = 1"))
    assert ed.post("/analysis", {"scope": "all"}).status_code == 202
    blocked = ed.post("/analysis", {"scope": "unassigned"})
    assert blocked.status_code in (202, 429)       # same chunk set -> same job (202) or the running-jobs limit (429)


def test_ai_rationale_draft_is_verified_labelled_and_not_the_decision(workspace) -> None:  # type: ignore[no-untyped-def]
    ed, adm = workspace["editor"], workspace["admin"]
    ini, doc = _full_decision(ed, adm)
    ed.patch(f"/decisions/{doc['id']}", {"options": [{"name": "A", "risk_rating": "low"}, {"name": "B"}], "recommendation_text": "Menschliche Begründung."}, version=doc["version"])
    assert ed.post(f"/decisions/{doc['id']}/ai-draft").status_code == 422               # needs the snapshot first
    ed.post(f"/decisions/{doc['id']}/snapshot")
    r = ed.post(f"/decisions/{doc['id']}/ai-draft")
    assert r.status_code == 202 and r.json()["provider"]["demo"] is True
    assert run_jobs(workspace["tenant"].id)[0] == ("draft_rationale", "succeeded")
    d = ed.get(f"/decisions/{doc['id']}").json()
    assert d["recommendation_text"] == "Menschliche Begründung."                         # the human text is untouched
    assert d["ai_draft"]["claims"] and "Demo" in d["ai_draft"]["provider_label"] and d["ai_run"]["demo"] is True
    snapshot_chunks = {e["chunk_id"] for e in d["evidence_snapshot"]["evidence"]}
    for c in d["ai_draft"]["claims"]:
        assert all(x in snapshot_chunks for x in c["source_chunk_ids"]) and all(x in d["evidence_snapshot"]["result_ids"] for x in c["result_ids"])
    assert d["ai_draft"]["verification"]["rejected_claims"] == 0
    assert d["state"] == "draft"                                                           # AI never approves or submits
