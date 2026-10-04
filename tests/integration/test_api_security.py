"""Authentication, CSRF, roles and tenant isolation at the HTTP level."""

from __future__ import annotations

import uuid

from conftest import Api, import_demo  # type: ignore[import-not-found]


def test_unauthenticated_requests_are_rejected(workspace) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from decision_evidence.main import create_app

    client = TestClient(create_app(), base_url="http://localhost:8480")
    r = client.get(f"/api/v1/workspaces/{workspace['tenant'].id}/problems")
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["request_id"]
    assert client.get("/api/health/live").json() == {"status": "ok"}


def test_csrf_and_origin_are_enforced_on_mutations(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    body = {"title": "Neu"}
    assert ed.client.post(ed.url("/problems"), json=body).status_code == 403               # no token
    assert ed.client.post(ed.url("/problems"), json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    bad_origin = ed.client.post(ed.url("/problems"), json=body, headers={"X-CSRF-Token": ed.csrf, "Origin": "http://evil.example"})
    assert bad_origin.status_code == 403 and bad_origin.json()["code"] == "bad_origin"
    assert ed.post("/problems", body).status_code == 201


def test_roles_are_enforced_server_side(workspace) -> None:  # type: ignore[no-untyped-def]
    v, e, a = workspace["viewer"], workspace["editor"], workspace["admin"]
    assert v.get("/problems").status_code == 200
    assert v.post("/problems", {"title": "x"}).status_code == 403                       # viewer cannot write
    assert v.post("/analysis", {"scope": "all"}).status_code == 403
    assert v.get("/audit").status_code == 403 and e.get("/audit").status_code == 403    # audit: admin+
    assert a.get("/audit").status_code == 200
    assert e.post("/invitations", {"email": "x@example.test", "role": "viewer"}).status_code == 403
    assert a.post("/invitations", {"email": "x@example.test", "role": "owner"}).status_code == 403   # only owners hand out owner
    assert workspace["owner"].post("/invitations", {"email": "x@example.test", "role": "owner"}).status_code == 201
    assert e.patch("/settings", {"max_running_jobs": 5}).status_code == 403


def test_foreign_workspace_is_not_found_for_non_members(workspace) -> None:  # type: ignore[no-untyped-def]
    o: Api = workspace["outsider"]
    for path in ("/problems", "/customers", "/dashboard", "/members", "/jobs", "/decisions"):
        assert o.get(path).status_code == 404, path
    assert o.post("/problems", {"title": "x"}).status_code == 404


def test_manipulated_ids_across_tenants_are_404(workspace) -> None:  # type: ignore[no-untyped-def]
    a_owner, b_owner = workspace["owner"], workspace["other_owner"]
    import_demo(b_owner)
    cust = b_owner.get("/customers").json()["items"][0]
    fb = b_owner.get("/feedback").json()["items"][0]
    chunk = fb["chunk_ids"][0]
    prob = b_owner.post("/problems", {"title": "B problem"}).json()
    # as tenant A owner: every foreign id is simply unknown
    assert a_owner.get(f"/customers/{cust['id']}").status_code == 404
    assert a_owner.get(f"/chunks/{chunk}").status_code == 404
    assert a_owner.get(f"/problems/{prob['id']}").status_code == 404
    assert a_owner.post(f"/problems/{prob['id']}/evidence", {"source_chunk_id": chunk}).status_code == 404
    mine = a_owner.post("/problems", {"title": "A problem"}).json()
    assert a_owner.post(f"/problems/{mine['id']}/evidence", {"source_chunk_id": chunk}).status_code == 404   # foreign chunk as evidence
    assert a_owner.post("/initiatives", {"problem_id": prob["id"], "title": "x"}).status_code == 404
    assert a_owner.get(f"/sources/{fb['source_record_id']}").status_code == 404
    assert a_owner.delete(f"/sources/{fb['source_record_id']}").status_code == 404
    assert a_owner.get("/feedback", params={"q": "Export"}).json()["total"] == 0
    assert a_owner.get("/customers").json()["total"] == 0
    # merge / split with foreign ids
    assert a_owner.post("/problems/merge", {"target_id": mine["id"], "source_ids": [prob["id"]]}).status_code == 404
    # B is untouched
    assert b_owner.get(f"/problems/{prob['id']}").json()["title"] == "B problem"


def test_session_logout_and_revocation(workspace) -> None:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    assert ed.client.get("/api/v1/auth/me").status_code == 200
    out = ed.client.post("/api/v1/auth/logout", headers=ed._h())
    assert out.status_code == 200
    assert ed.client.get("/api/v1/auth/me").status_code == 401


def test_me_lists_only_own_workspaces(workspace) -> None:  # type: ignore[no-untyped-def]
    me = workspace["editor"].client.get("/api/v1/auth/me").json()
    assert [w["tenant_id"] for w in me["workspaces"]] == [str(workspace["tenant"].id)] and me["workspaces"][0]["role"] == "editor"
    assert me["csrf_token"]


def test_invitations_are_single_use_expiring_and_email_bound(workspace, make_user) -> None:  # type: ignore[no-untyped-def]
    import datetime as dt

    from sqlalchemy import text as sql

    from decision_evidence.db.engines import get_engine
    from decision_evidence.db.session import plain_session

    owner: Api = workspace["owner"]
    invited_email = f"new-{uuid.uuid4().hex[:6]}@example.test"
    inv = owner.post("/invitations", {"email": invited_email.upper(), "role": "editor"}).json()
    assert inv["email"] == invited_email and inv["token"]
    with plain_session(get_engine("migrator")) as s:   # only the hash is stored
        stored = s.execute(sql("select token_hash from identity.invitations where id=:i"), {"i": inv["id"]}).scalar()
    assert stored != inv["token"] and len(stored) == 64

    def user_with(email: str) -> Api:
        uid = make_user("invitee")
        with plain_session(get_engine("migrator")) as s:
            s.execute(sql("update identity.users set email=:e where id=:u"), {"e": email, "u": uid})
        return Api(uid)

    wrong = user_with("someone-else@example.test")
    r = wrong.client.post("/api/v1/auth/invitations/accept", json={"token": inv["token"]}, headers=wrong._h())
    assert r.status_code == 403 and r.json()["code"] == "invitation_email_mismatch"      # not matched by domain or token alone
    right = user_with(invited_email)
    ok = right.client.post("/api/v1/auth/invitations/accept", json={"token": inv["token"]}, headers=right._h())
    assert ok.status_code == 200 and ok.json()["tenant_id"] == str(workspace["tenant"].id)
    again = right.client.post("/api/v1/auth/invitations/accept", json={"token": inv["token"]}, headers=right._h())
    assert again.status_code == 400 and again.json()["code"] == "invitation_used"
    me = right.client.get("/api/v1/auth/me").json()
    assert me["workspaces"][0]["role"] == "editor"
    expired = owner.post("/invitations", {"email": "exp@example.test", "role": "viewer"}).json()
    with plain_session(get_engine("migrator")) as s:
        s.execute(sql("update identity.invitations set expires_at=:t where id=:i"), {"t": dt.datetime.now(dt.UTC) - dt.timedelta(days=1), "i": expired["id"]})
    late = user_with("exp@example.test")
    assert late.client.post("/api/v1/auth/invitations/accept", json={"token": expired["token"]}, headers=late._h()).json()["code"] == "invitation_expired"
    revoked = owner.post("/invitations", {"email": "rev@example.test", "role": "viewer"}).json()
    assert owner.delete(f"/invitations/{revoked['id']}").status_code == 204
    r2 = user_with("rev@example.test")
    assert r2.client.post("/api/v1/auth/invitations/accept", json={"token": revoked["token"]}, headers=r2._h()).json()["code"] == "invitation_invalid"


def test_last_owner_protection_via_api(workspace) -> None:  # type: ignore[no-untyped-def]
    owner: Api = workspace["owner"]
    r = owner.patch(f"/members/{owner.user_id}", {"role": "admin"})
    assert r.status_code == 409 and r.json()["code"] == "last_owner"
    assert owner.delete(f"/members/{owner.user_id}").status_code == 409
    assert workspace["admin"].patch(f"/members/{owner.user_id}", {"role": "viewer"}).status_code == 403
    r = owner.patch(f"/members/{workspace['viewer'].user_id}", {"role": "editor"})
    assert r.status_code == 200


def test_foreign_jobs_imports_decisions_and_exports_are_not_reachable(workspace) -> None:  # type: ignore[no-untyped-def]
    a, b = workspace["owner"], workspace["other_owner"]
    # tenant B builds a complete decision with job, import batch, file and export
    from test_domain_flow import _full_decision  # type: ignore[import-not-found]

    ini, doc = _full_decision(b, b)
    b.patch(f"/decisions/{doc['id']}", {"options": [{"name": "A"}, {"name": "B"}], "recommendation_text": "x"}, version=doc["version"])
    b.post(f"/decisions/{doc['id']}/snapshot")
    job_id = b.get("/jobs").json()["items"][0]["id"]
    batch_id = b.get("/imports").json()["items"][0]["id"]
    # the same ids under tenant A's workspace route: indistinguishable from "does not exist"
    assert a.get(f"/jobs/{job_id}").status_code == 404
    assert a.post(f"/jobs/{job_id}/retry").status_code == 404
    assert a.get(f"/imports/{batch_id}").status_code == 404
    assert a.post(f"/imports/{batch_id}/commit").status_code == 404
    assert a.client.put(a.url(f"/imports/{batch_id}/settings"), json={}, headers=a._h()).status_code == 404
    assert a.get(f"/decisions/{doc['id']}").status_code == 404
    assert a.get(f"/decisions/{doc['id']}/export").status_code == 404
    assert a.post(f"/decisions/{doc['id']}/approve").status_code == 404
    assert a.post(f"/decisions/{doc['id']}/comments", {"body": "x"}).status_code == 404
    assert a.get(f"/initiatives/{ini['id']}").status_code == 404
    assert a.post("/scoring/compare", {"initiative_ids": [ini["id"]]}).json()["results"] == []   # unknown ids contribute nothing
    for path in ("/jobs", "/imports", "/decisions", "/initiatives", "/sources", "/feedback", "/customers", "/opportunities", "/problems"):
        assert a.get(path).json()["total"] == 0, path
    # tenant B's own data is intact
    assert b.get(f"/decisions/{doc['id']}").status_code == 200
