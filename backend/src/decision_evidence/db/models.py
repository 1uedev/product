"""ORM models. DDL is owned by the SQL migrations (migrations/sql); these models mirror it.

``tests/integration/test_schema.py`` fails if columns drift between the two. Composite foreign keys
(tenant_id, parent_id) are declared so that the unit of work orders inserts correctly.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from decision_evidence.db.base import Base
from decision_evidence.db.context import tenant_default

NOW = text("now()")
UUID_DEFAULT = text("gen_random_uuid()")
EMPTY_JSON = text("'{}'::jsonb")


def tenant_fk(column: str, parent: str, **kwargs: Any) -> ForeignKeyConstraint:
    """Composite (tenant_id, <column>) -> parent(tenant_id, id)."""
    return ForeignKeyConstraint(
        ["tenant_id", column], [f"app.{parent}.tenant_id", f"app.{parent}.id"], **kwargs
    )


class TenantMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_DEFAULT)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.tenants.id"), nullable=False, default=tenant_default
    )


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class Updated(Timestamps):
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW, onupdate=NOW)


# ----------------------------------------------------------------------------- identity schema
class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "identity"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_DEFAULT)
    oidc_issuer: Mapped[str] = mapped_column(Text)
    oidc_subject: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = {"schema": "identity"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_DEFAULT)
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(Text, server_default="de-DE")
    timezone: Mapped[str] = mapped_column(Text, server_default="Europe/Berlin")
    status: Mapped[str] = mapped_column(Text, server_default="active")
    external_org_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = {"schema": "identity"}
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.tenants.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = {"schema": "identity"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_DEFAULT)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    token_hash: Mapped[str] = mapped_column(Text)
    csrf_secret: Mapped[str] = mapped_column(Text)
    id_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = {"schema": "identity"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_DEFAULT)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.tenants.id"))
    normalized_email: Mapped[str] = mapped_column(Text)
    intended_role: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = {"schema": "identity"}
    state_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    nonce: Mapped[str] = mapped_column(Text)
    code_verifier_enc: Mapped[bytes] = mapped_column(LargeBinary)
    redirect_after: Mapped[str] = mapped_column(Text, server_default="/")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)


# ----------------------------------------------------------------------------- infra schema
class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_DEFAULT)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.tenants.id"), default=tenant_default)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(Text, default="job.enqueued")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "job_id"], ["app.jobs.tenant_id", "app.jobs.id"]),
        {"schema": "infra"},
    )


# ----------------------------------------------------------------------------- app schema: core
class TenantSettings(TenantMixin, Updated, Base):
    __tablename__ = "tenant_settings"
    __table_args__ = {"schema": "app"}
    max_file_bytes: Mapped[int] = mapped_column(BigInteger, server_default="10485760")
    max_import_rows: Mapped[int] = mapped_column(Integer, server_default="5000")
    max_running_jobs: Mapped[int] = mapped_column(Integer, server_default="3")
    ai_monthly_call_budget: Mapped[int] = mapped_column(Integer, server_default="50")
    max_ai_context_chunks: Mapped[int] = mapped_column(Integer, server_default="400")
    allow_self_approval: Mapped[bool] = mapped_column(Boolean, server_default="false")
    evidence_fresh_days: Mapped[int] = mapped_column(Integer, server_default="180")
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default="false")


class FileRecord(TenantMixin, Updated, Base):
    __tablename__ = "files"
    __table_args__ = {"schema": "app"}
    object_key: Mapped[str] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    failure_code: Mapped[str | None] = mapped_column(Text)


class Job(TenantMixin, Updated, Base):
    __tablename__ = "jobs"
    __table_args__ = {"schema": "app"}
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    progress: Mapped[int] = mapped_column(Integer, server_default="0")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    idempotency_key: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="3")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)


class ImportBatch(TenantMixin, Updated, Base):
    __tablename__ = "import_batches"
    kind: Mapped[str] = mapped_column(Text)
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, server_default="uploaded")
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    detected: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    preview: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    preview_hash: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    __table_args__ = (tenant_fk("file_id", "files"), tenant_fk("job_id", "jobs"), {"schema": "app"})


class AiRun(TenantMixin, Timestamps, Base):
    __tablename__ = "ai_runs"
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    purpose: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="running")
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    cost_currency: Mapped[str | None] = mapped_column(Text)
    price_basis: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    __table_args__ = (tenant_fk("job_id", "jobs"), {"schema": "app"})


class SourceRecord(TenantMixin, Updated, Base):
    __tablename__ = "source_records"
    source_kind: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=EMPTY_JSON)
    __table_args__ = (tenant_fk("file_id", "files"), tenant_fk("import_batch_id", "import_batches"), {"schema": "app"})


class SourceChunk(TenantMixin, Timestamps, Base):
    __tablename__ = "source_chunks"
    source_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    search_vector: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('german', text)", persisted=True))
    __table_args__ = (tenant_fk("source_record_id", "source_records", ondelete="CASCADE"), {"schema": "app"})


class AuditEvent(TenantMixin, Timestamps, Base):
    __tablename__ = "audit_events"
    __table_args__ = {"schema": "app"}
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)
    request_id: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=EMPTY_JSON)


# ----------------------------------------------------------------------------- app schema: domain
class CustomerAccount(TenantMixin, Updated, Base):
    __tablename__ = "customer_accounts"
    external_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    segment: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    commercial_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    value_basis: Mapped[str] = mapped_column(Text, server_default="unknown")
    currency: Mapped[str | None] = mapped_column(Text)
    value_as_of: Mapped[date | None] = mapped_column(Date)
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_row: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (tenant_fk("import_batch_id", "import_batches"), {"schema": "app"})


class Opportunity(TenantMixin, Updated, Base):
    __tablename__ = "opportunities"
    external_id: Mapped[str] = mapped_column(Text)
    customer_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[date | None] = mapped_column(Date)
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_row: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        tenant_fk("customer_account_id", "customer_accounts"),
        tenant_fk("import_batch_id", "import_batches"),
        {"schema": "app"},
    )


class FeedbackItem(TenantMixin, Updated, Base):
    __tablename__ = "feedback_items"
    source_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    customer_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    channel: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    body: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    search_vector: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('german', body)", persisted=True))
    __table_args__ = (
        tenant_fk("source_record_id", "source_records", ondelete="CASCADE"),
        tenant_fk("customer_account_id", "customer_accounts"),
        tenant_fk("opportunity_id", "opportunities"),
        {"schema": "app"},
    )


class Problem(TenantMixin, Updated, Base):
    __tablename__ = "problems"
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    status: Mapped[str] = mapped_column(Text, server_default="proposed")
    origin: Mapped[str] = mapped_column(Text, server_default="manual")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    created_by_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    job_ordinal: Mapped[int | None] = mapped_column(Integer)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    merged_into_problem_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    __table_args__ = (
        tenant_fk("created_by_job_id", "jobs"),
        tenant_fk("ai_run_id", "ai_runs"),
        tenant_fk("merged_into_problem_id", "problems"),
        {"schema": "app"},
    )


class ProblemEvidence(TenantMixin, Updated, Base):
    __tablename__ = "problem_evidence"
    problem_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    feedback_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    relation: Mapped[str] = mapped_column(Text)
    extracted_quote: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text, server_default="human")
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    human_verified: Mapped[bool] = mapped_column(Boolean, server_default="false")
    verified_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        tenant_fk("problem_id", "problems", ondelete="CASCADE"),
        tenant_fk("feedback_item_id", "feedback_items", ondelete="CASCADE"),
        tenant_fk("source_chunk_id", "source_chunks", ondelete="CASCADE"),
        tenant_fk("ai_run_id", "ai_runs"),
        {"schema": "app"},
    )


class ProblemHistory(TenantMixin, Timestamps, Base):
    __tablename__ = "problem_history"
    problem_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    __table_args__ = (tenant_fk("problem_id", "problems", ondelete="CASCADE"), {"schema": "app"})


class ScoringPolicy(TenantMixin, Updated, Base):
    __tablename__ = "scoring_policies"
    __table_args__ = {"schema": "app"}
    name: Mapped[str] = mapped_column(Text)
    formula_version: Mapped[str] = mapped_column(Text, server_default="v1")
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    missing_value_policy: Mapped[str] = mapped_column(Text, server_default="exclude")
    active: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))


class Initiative(TenantMixin, Updated, Base):
    __tablename__ = "initiatives"
    problem_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text)
    desired_outcome: Mapped[str] = mapped_column(Text, server_default="")
    target_segment: Mapped[str | None] = mapped_column(Text)
    effort_low: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    effort_high: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    effort_unit: Mapped[str] = mapped_column(Text, server_default="person_days")
    status: Mapped[str] = mapped_column(Text, server_default="proposed")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    __table_args__ = (tenant_fk("problem_id", "problems"), {"schema": "app"})


class InitiativeAssumption(TenantMixin, Updated, Base):
    __tablename__ = "initiative_assumptions"
    initiative_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    statement: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    validation_status: Mapped[str] = mapped_column(Text, server_default="open")
    __table_args__ = (
        tenant_fk("initiative_id", "initiatives", ondelete="CASCADE"),
        tenant_fk("source_chunk_id", "source_chunks"),
        {"schema": "app"},
    )


class DecisionDocument(TenantMixin, Updated, Base):
    __tablename__ = "decision_documents"
    initiative_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text, server_default="")
    options: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    recommendation_text: Mapped[str] = mapped_column(Text, server_default="")
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    scoring_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON)
    scoring_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    snapshot_taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ai_draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(Text, server_default="draft")
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    __table_args__ = (
        tenant_fk("initiative_id", "initiatives"),
        tenant_fk("ai_run_id", "ai_runs"),
        tenant_fk("scoring_policy_id", "scoring_policies"),
        {"schema": "app"},
    )


class DecisionComment(TenantMixin, Updated, Base):
    __tablename__ = "decision_comments"
    decision_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    author_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("identity.users.id"))
    body: Mapped[str] = mapped_column(Text)
    __table_args__ = (tenant_fk("decision_document_id", "decision_documents", ondelete="CASCADE"), {"schema": "app"})


class DecisionEvidence(TenantMixin, Timestamps, Base):
    __tablename__ = "decision_evidence"
    decision_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    relation: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        tenant_fk("decision_document_id", "decision_documents", ondelete="CASCADE"),
        tenant_fk("source_chunk_id", "source_chunks", ondelete="RESTRICT"),
        {"schema": "app"},
    )


APP_TABLES = sorted(t.name for t in Base.metadata.tables.values() if t.schema == "app")
