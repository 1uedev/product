-- 0002: shared tenant-scoped tables (files, sources, jobs, AI runs, audit) and the transactional outbox.
-- Conventions for every app.* table: id uuid PK, tenant_id NOT NULL (FK to identity.tenants), UNIQUE (tenant_id, id)
-- so that child tables can use composite foreign keys (tenant_id, parent_id) and cross-tenant references fail
-- in the database. RLS is enabled and forced for all app.* tables in 0004.

CREATE TABLE app.tenant_settings (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id              uuid NOT NULL UNIQUE REFERENCES identity.tenants (id),
    max_file_bytes         bigint  NOT NULL DEFAULT 10485760 CHECK (max_file_bytes BETWEEN 1024 AND 104857600),
    max_import_rows        integer NOT NULL DEFAULT 5000 CHECK (max_import_rows BETWEEN 1 AND 100000),
    max_running_jobs       integer NOT NULL DEFAULT 3 CHECK (max_running_jobs BETWEEN 1 AND 50),
    ai_monthly_call_budget integer NOT NULL DEFAULT 50 CHECK (ai_monthly_call_budget >= 0),
    max_ai_context_chunks  integer NOT NULL DEFAULT 400 CHECK (max_ai_context_chunks BETWEEN 1 AND 5000),
    allow_self_approval    boolean NOT NULL DEFAULT false,
    evidence_fresh_days    integer NOT NULL DEFAULT 180 CHECK (evidence_fresh_days BETWEEN 1 AND 3650),
    is_demo                boolean NOT NULL DEFAULT false,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id)
);

CREATE TABLE app.files (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES identity.tenants (id),
    object_key    text NOT NULL UNIQUE,
    original_name text NOT NULL CHECK (length(original_name) BETWEEN 1 AND 255),
    media_type    text NOT NULL,
    byte_size     bigint NOT NULL CHECK (byte_size > 0 AND byte_size <= 104857600),
    sha256        text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    purpose       text NOT NULL CHECK (purpose IN ('import_csv', 'import_document', 'import_text')),
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'ready', 'failed', 'deleting')),
    uploaded_by   uuid REFERENCES identity.users (id),
    failure_code  text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    CONSTRAINT files_key_in_tenant_prefix CHECK (object_key LIKE 'tenant/' || tenant_id::text || '/%')
);
CREATE INDEX files_status_idx ON app.files (status, created_at);
CREATE INDEX files_tenant_created_idx ON app.files (tenant_id, created_at DESC);

CREATE TABLE app.jobs (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES identity.tenants (id),
    kind             text NOT NULL CHECK (kind IN ('import_commit', 'analyze_feedback', 'draft_rationale')),
    status           text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    progress         integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    requested_by     uuid REFERENCES identity.users (id),
    idempotency_key  text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
    result           jsonb,
    attempts         integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts     integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    lease_expires_at timestamptz,
    claim_token      uuid,
    started_at       timestamptz,
    finished_at      timestamptz,
    error_code       text,
    error_message    text,
    correlation_id   text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, kind, idempotency_key),
    CONSTRAINT jobs_running_has_lease CHECK (status <> 'running' OR (lease_expires_at IS NOT NULL AND claim_token IS NOT NULL)),
    CONSTRAINT jobs_failed_has_code CHECK (status <> 'failed' OR error_code IS NOT NULL)
);
CREATE INDEX jobs_tenant_status_idx ON app.jobs (tenant_id, status, created_at DESC);
CREATE INDEX jobs_lease_idx ON app.jobs (status, lease_expires_at) WHERE status = 'running';

CREATE TABLE app.import_batches (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES identity.tenants (id),
    kind         text NOT NULL CHECK (kind IN ('customers', 'opportunities', 'feedback', 'notes_text', 'document')),
    file_id      uuid NOT NULL,
    status       text NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded', 'previewed', 'committing', 'committed', 'failed')),
    mapping      jsonb NOT NULL DEFAULT '{}'::jsonb,
    options      jsonb NOT NULL DEFAULT '{}'::jsonb,
    detected     jsonb NOT NULL DEFAULT '{}'::jsonb,
    preview      jsonb,
    preview_hash text,
    result       jsonb,
    job_id       uuid,
    created_by   uuid REFERENCES identity.users (id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, file_id) REFERENCES app.files (tenant_id, id),
    FOREIGN KEY (tenant_id, job_id) REFERENCES app.jobs (tenant_id, id)
);
CREATE INDEX import_batches_tenant_idx ON app.import_batches (tenant_id, created_at DESC);
CREATE INDEX import_batches_file_idx ON app.import_batches (tenant_id, file_id);
CREATE INDEX import_batches_job_idx ON app.import_batches (tenant_id, job_id);

CREATE TABLE app.ai_runs (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES identity.tenants (id),
    job_id         uuid,
    purpose        text NOT NULL CHECK (purpose IN ('analysis', 'rationale')),
    provider       text NOT NULL CHECK (provider IN ('mock', 'anthropic')),
    model          text NOT NULL,
    prompt_version text NOT NULL,
    input_hash     text NOT NULL,
    status         text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
    input_tokens   integer CHECK (input_tokens >= 0),
    output_tokens  integer CHECK (output_tokens >= 0),
    duration_ms    integer CHECK (duration_ms >= 0),
    error_code     text,
    -- cost is only recorded when a price basis is configured; both columns stay NULL otherwise
    estimated_cost numeric(14, 6) CHECK (estimated_cost >= 0),
    cost_currency  text CHECK (cost_currency ~ '^[A-Z]{3}$'),
    price_basis    text,
    verification   jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, job_id) REFERENCES app.jobs (tenant_id, id),
    CONSTRAINT ai_runs_cost_consistent CHECK ((estimated_cost IS NULL) = (cost_currency IS NULL))
);
CREATE INDEX ai_runs_tenant_created_idx ON app.ai_runs (tenant_id, created_at DESC);
CREATE INDEX ai_runs_job_idx ON app.ai_runs (tenant_id, job_id);

CREATE TABLE app.source_records (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES identity.tenants (id),
    source_kind      text NOT NULL CHECK (source_kind IN ('csv_feedback', 'note_text', 'document')),
    origin           text NOT NULL CHECK (origin IN ('original', 'imported', 'synthetic')),
    external_id      text,
    file_id          uuid,
    import_batch_id  uuid,
    source_timestamp timestamptz,
    title            text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    raw_text         text NOT NULL,
    content_hash     text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, content_hash),
    FOREIGN KEY (tenant_id, file_id) REFERENCES app.files (tenant_id, id),
    FOREIGN KEY (tenant_id, import_batch_id) REFERENCES app.import_batches (tenant_id, id)
);
CREATE INDEX source_records_file_idx ON app.source_records (tenant_id, file_id);
CREATE INDEX source_records_batch_idx ON app.source_records (tenant_id, import_batch_id);
CREATE INDEX source_records_created_idx ON app.source_records (tenant_id, created_at DESC, id);

CREATE TABLE app.source_chunks (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES identity.tenants (id),
    source_record_id uuid NOT NULL,
    ordinal          integer NOT NULL CHECK (ordinal >= 0),
    text             text NOT NULL CHECK (length(text) > 0),
    locator          jsonb NOT NULL DEFAULT '{}'::jsonb,
    search_vector    tsvector GENERATED ALWAYS AS (to_tsvector('german', text)) STORED,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, source_record_id, ordinal),
    FOREIGN KEY (tenant_id, source_record_id) REFERENCES app.source_records (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX source_chunks_search_idx ON app.source_chunks USING gin (search_vector);

CREATE TABLE app.audit_events (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES identity.tenants (id),
    actor_user_id uuid REFERENCES identity.users (id),
    action        text NOT NULL,
    entity_type   text NOT NULL,
    entity_id     uuid,
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    request_id    text,
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id)
);
CREATE INDEX audit_events_tenant_time_idx ON app.audit_events (tenant_id, occurred_at DESC, id);
CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON app.audit_events
    FOR EACH ROW EXECUTE FUNCTION infra.forbid_modification();

-- ---------------------------------------------------------------------------
-- infra.outbox: only mediation metadata, never business content (see ADR 0003).
-- ---------------------------------------------------------------------------
CREATE TABLE infra.outbox (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES identity.tenants (id),
    job_id         uuid NOT NULL,
    event_type     text NOT NULL CHECK (event_type IN ('job.enqueued')),
    schema_version integer NOT NULL DEFAULT 1,
    correlation_id text,
    available_at   timestamptz NOT NULL DEFAULT now(),
    published_at   timestamptz,
    attempts       integer NOT NULL DEFAULT 0,
    last_error_code text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, job_id) REFERENCES app.jobs (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX outbox_unpublished_idx ON infra.outbox (available_at) WHERE published_at IS NULL;
CREATE INDEX outbox_job_idx ON infra.outbox (tenant_id, job_id);
