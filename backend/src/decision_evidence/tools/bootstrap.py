"""Controlled bootstrap step (runs once after the migrations, before api/worker start).

* ensures the object storage bucket exists (private; there is no public access in the S3 configuration)
* demo/test mode: pre-provisions demo users/tenants and the idempotent synthetic seed
* production: NEVER seeds demo data; optionally creates the first tenant and prints a one-time owner invitation
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import select

from decision_evidence.config import ConfigError, get_settings
from decision_evidence.db import models as m
from decision_evidence.db.engines import get_engine
from decision_evidence.db.session import plain_session, tenant_session
from decision_evidence.identity.crypto import random_token, sha256_hex
from decision_evidence.modules.scoring.service import ensure_default_policy
from decision_evidence.observability.logging import configure_logging, get_logger
from decision_evidence.storage.s3 import S3Storage, build_s3_client

log = get_logger("decision_evidence.bootstrap")


def ensure_bucket() -> None:
    s = get_settings()
    S3Storage(build_s3_client(), s.s3_bucket).ensure_bucket()
    log.info("object storage bucket ready", extra={"bucket": s.s3_bucket})


def create_tenant(slug: str, name: str, owner_email: str) -> str:
    """Creates a tenant (idempotent by slug) and returns an owner invitation path. Used by production bootstrap/admin CLI."""
    import uuid
    from datetime import UTC, datetime, timedelta

    with plain_session(get_engine("migrator")) as s:
        t = s.scalar(select(m.Tenant).where(m.Tenant.slug == slug))
        if t is None:
            t = m.Tenant(id=uuid.uuid4(), slug=slug, name=name)
            s.add(t)
            s.flush()
        tid = t.id
        token = random_token(32)
        s.add(m.Invitation(tenant_id=tid, normalized_email=owner_email.strip().lower(), intended_role="owner", token_hash=sha256_hex(token),
                           expires_at=datetime.now(UTC) + timedelta(days=7)))
    with tenant_session(get_engine("migrator"), tid) as s:
        if s.scalar(select(m.TenantSettings.id)) is None:
            s.add(m.TenantSettings(tenant_id=tid))
        ensure_default_policy(s)
    return f"/invite?token={token}"


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        settings.validate_for_runtime(needs={"storage"})
    except ConfigError as exc:
        log.error("configuration refused: %s", exc)
        return 2
    ensure_bucket()
    if settings.app_env == "production":
        slug, name, email = (os.environ.get(k, "") for k in ("BOOTSTRAP_TENANT_SLUG", "BOOTSTRAP_TENANT_NAME", "BOOTSTRAP_OWNER_EMAIL"))
        if slug and name and email:
            path = create_tenant(slug, name, email)
            print(f"[bootstrap] one-time owner invitation for {email}: {settings.public_origin}{path}", flush=True)
        log.info("production bootstrap done (no demo data)")
        return 0
    if os.environ.get("DEMO_SEED", "true").lower() != "false":
        from decision_evidence.tools import seed
        seed.run(include_e2e=settings.app_env == "test")
        log.info("demo seed done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
