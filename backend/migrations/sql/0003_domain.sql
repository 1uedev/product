-- 0003: product domain (customers, feedback, problems, initiatives, scoring, decision documents).

CREATE TABLE app.customer_accounts (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES identity.tenants (id),
    external_id      text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 100),
    name             text NOT NULL CHECK (length(name) BETWEEN 1 AND 300),
    segment          text,
    country          text CHECK (country ~ '^[A-Z]{2}$'),
    commercial_value numeric(18, 2) CHECK (commercial_value >= 0),
    value_basis      text NOT NULL DEFAULT 'unknown' CHECK (value_basis IN ('arr', 'annual_sales', 'unknown')),
    currency         text CHECK (currency ~ '^[A-Z]{3}$'),
    value_as_of      date,
    import_batch_id  uuid,
    source_row       integer,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, external_id),
    FOREIGN KEY (tenant_id, import_batch_id) REFERENCES app.import_batches (tenant_id, id),
    -- a known value needs a basis and a currency; unknown stays NULL (never 0)
    CONSTRAINT customer_value_complete CHECK (
        commercial_value IS NULL OR (currency IS NOT NULL AND value_basis <> 'unknown')),
    CONSTRAINT customer_unknown_has_no_value CHECK (value_basis <> 'unknown' OR commercial_value IS NULL)
);
CREATE INDEX customer_accounts_segment_idx ON app.customer_accounts (tenant_id, segment);
CREATE INDEX customer_accounts_batch_idx ON app.customer_accounts (tenant_id, import_batch_id);
CREATE INDEX customer_accounts_name_trgm_idx ON app.customer_accounts USING gin (name infra.gin_trgm_ops);

CREATE TABLE app.opportunities (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES identity.tenants (id),
    external_id         text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 100),
    customer_account_id uuid NOT NULL,
    name                text NOT NULL CHECK (length(name) BETWEEN 1 AND 300),
    stage               text NOT NULL CHECK (stage IN ('open', 'won', 'lost', 'no_decision')),
    amount              numeric(18, 2) CHECK (amount >= 0),
    currency            text CHECK (currency ~ '^[A-Z]{3}$'),
    closed_at           date,
    import_batch_id     uuid,
    source_row          integer,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, external_id),
    FOREIGN KEY (tenant_id, customer_account_id) REFERENCES app.customer_accounts (tenant_id, id),
    FOREIGN KEY (tenant_id, import_batch_id) REFERENCES app.import_batches (tenant_id, id),
    CONSTRAINT opportunity_amount_has_currency CHECK (amount IS NULL OR currency IS NOT NULL),
    CONSTRAINT opportunity_open_not_closed CHECK (stage <> 'open' OR closed_at IS NULL)
);
CREATE INDEX opportunities_customer_idx ON app.opportunities (tenant_id, customer_account_id);
CREATE INDEX opportunities_stage_idx ON app.opportunities (tenant_id, stage);
CREATE INDEX opportunities_batch_idx ON app.opportunities (tenant_id, import_batch_id);

CREATE TABLE app.feedback_items (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES identity.tenants (id),
    source_record_id    uuid NOT NULL,
    customer_account_id uuid,
    opportunity_id      uuid,
    channel             text NOT NULL CHECK (channel IN ('email', 'ticket', 'call', 'interview', 'chat', 'survey', 'document', 'other')),
    occurred_at         timestamptz NOT NULL,
    body                text NOT NULL CHECK (length(body) > 0),
    language            text CHECK (language ~ '^[a-z]{2}$'),
    external_id         text,
    search_vector       tsvector GENERATED ALWAYS AS (to_tsvector('german', body)) STORED,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, source_record_id),
    FOREIGN KEY (tenant_id, source_record_id) REFERENCES app.source_records (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, customer_account_id) REFERENCES app.customer_accounts (tenant_id, id),
    FOREIGN KEY (tenant_id, opportunity_id) REFERENCES app.opportunities (tenant_id, id)
);
CREATE UNIQUE INDEX feedback_items_external_idx ON app.feedback_items (tenant_id, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX feedback_items_customer_idx ON app.feedback_items (tenant_id, customer_account_id);
CREATE INDEX feedback_items_opportunity_idx ON app.feedback_items (tenant_id, opportunity_id);
CREATE INDEX feedback_items_time_idx ON app.feedback_items (tenant_id, occurred_at DESC, id);
CREATE INDEX feedback_items_search_idx ON app.feedback_items USING gin (search_vector);

CREATE TABLE app.problems (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             uuid NOT NULL REFERENCES identity.tenants (id),
    title                 text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    description           text NOT NULL DEFAULT '',
    status                text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'confirmed', 'archived')),
    origin                text NOT NULL DEFAULT 'manual' CHECK (origin IN ('ai', 'manual', 'split')),
    owner_user_id         uuid REFERENCES identity.users (id),
    created_by            uuid REFERENCES identity.users (id),
    created_by_job_id     uuid,
    job_ordinal           integer,
    ai_run_id             uuid,
    merged_into_problem_id uuid,
    version               integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, created_by_job_id) REFERENCES app.jobs (tenant_id, id),
    FOREIGN KEY (tenant_id, ai_run_id) REFERENCES app.ai_runs (tenant_id, id),
    FOREIGN KEY (tenant_id, merged_into_problem_id) REFERENCES app.problems (tenant_id, id),
    CONSTRAINT problems_job_ordinal_pair CHECK ((created_by_job_id IS NULL) = (job_ordinal IS NULL)),
    CONSTRAINT problems_merged_is_archived CHECK (merged_into_problem_id IS NULL OR status = 'archived')
);
-- idempotent persistence of analysis results: a re-delivered job cannot create the same proposal twice
CREATE UNIQUE INDEX problems_job_ordinal_idx ON app.problems (tenant_id, created_by_job_id, job_ordinal)
    WHERE created_by_job_id IS NOT NULL;
CREATE INDEX problems_status_idx ON app.problems (tenant_id, status, updated_at DESC);
CREATE INDEX problems_owner_idx ON app.problems (tenant_id, owner_user_id);
CREATE INDEX problems_merged_idx ON app.problems (tenant_id, merged_into_problem_id);
CREATE INDEX problems_ai_run_idx ON app.problems (tenant_id, ai_run_id);

CREATE TABLE app.problem_evidence (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES identity.tenants (id),
    problem_id       uuid NOT NULL,
    feedback_item_id uuid NOT NULL,
    source_chunk_id  uuid NOT NULL,
    relation         text NOT NULL CHECK (relation IN ('supports', 'contradicts', 'context')),
    extracted_quote  text NOT NULL CHECK (length(extracted_quote) > 0),
    origin           text NOT NULL DEFAULT 'human' CHECK (origin IN ('ai', 'human')),
    ai_run_id        uuid,
    human_verified   boolean NOT NULL DEFAULT false,
    verified_by      uuid REFERENCES identity.users (id),
    verified_at      timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, problem_id, source_chunk_id),
    FOREIGN KEY (tenant_id, problem_id) REFERENCES app.problems (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, feedback_item_id) REFERENCES app.feedback_items (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, source_chunk_id) REFERENCES app.source_chunks (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, ai_run_id) REFERENCES app.ai_runs (tenant_id, id),
    CONSTRAINT problem_evidence_verified_pair CHECK (human_verified = (verified_at IS NOT NULL))
);
CREATE INDEX problem_evidence_problem_idx ON app.problem_evidence (tenant_id, problem_id);
CREATE INDEX problem_evidence_feedback_idx ON app.problem_evidence (tenant_id, feedback_item_id);
CREATE INDEX problem_evidence_chunk_idx ON app.problem_evidence (tenant_id, source_chunk_id);
CREATE INDEX problem_evidence_ai_run_idx ON app.problem_evidence (tenant_id, ai_run_id);

-- the chunk must belong to the feedback item's source record
CREATE FUNCTION app.check_evidence_consistency() RETURNS trigger LANGUAGE plpgsql AS
$$
BEGIN
  IF NOT EXISTS (
      SELECT 1 FROM app.source_chunks c JOIN app.feedback_items f
        ON f.tenant_id = c.tenant_id AND f.source_record_id = c.source_record_id
       WHERE c.tenant_id = NEW.tenant_id AND c.id = NEW.source_chunk_id AND f.id = NEW.feedback_item_id) THEN
    RAISE EXCEPTION 'source chunk % does not belong to feedback item %', NEW.source_chunk_id, NEW.feedback_item_id
      USING ERRCODE = 'foreign_key_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER problem_evidence_consistency BEFORE INSERT OR UPDATE OF feedback_item_id, source_chunk_id
    ON app.problem_evidence FOR EACH ROW EXECUTE FUNCTION app.check_evidence_consistency();

CREATE TABLE app.problem_history (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL REFERENCES identity.tenants (id),
    problem_id uuid NOT NULL,
    action     text NOT NULL CHECK (action IN (
        'created', 'edited', 'status_changed', 'merged_into', 'merged_from', 'split_to', 'split_from',
        'evidence_added', 'evidence_removed', 'evidence_moved_out', 'evidence_moved_in',
        'evidence_relation_changed', 'evidence_verified', 'evidence_unverified')),
    actor_user_id uuid REFERENCES identity.users (id),
    details    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, problem_id) REFERENCES app.problems (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX problem_history_problem_idx ON app.problem_history (tenant_id, problem_id, created_at DESC);
CREATE TRIGGER problem_history_append_only BEFORE UPDATE ON app.problem_history
    FOR EACH ROW EXECUTE FUNCTION infra.forbid_modification();

-- scoring -------------------------------------------------------------------
CREATE FUNCTION app.valid_scoring_weights(w jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS
$$
SELECT jsonb_typeof(w) = 'object'
   AND NOT EXISTS (
        SELECT 1 FROM jsonb_each(w) e
         WHERE e.key NOT IN ('customer_reach', 'arr_exposure', 'pipeline_at_stake', 'evidence_quality',
                             'consistency', 'low_effort', 'low_risk')
            OR jsonb_typeof(e.value) <> 'number'
            OR (e.value #>> '{}')::numeric < 0)
   AND abs((SELECT coalesce(sum((e.value #>> '{}')::numeric), 0) FROM jsonb_each(w) e) - 1) < 0.0001
$$;

CREATE TABLE app.scoring_policies (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid NOT NULL REFERENCES identity.tenants (id),
    name                 text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    formula_version      text NOT NULL DEFAULT 'v1' CHECK (formula_version IN ('v1')),
    weights              jsonb NOT NULL,
    parameters           jsonb NOT NULL DEFAULT '{}'::jsonb,
    missing_value_policy text NOT NULL DEFAULT 'exclude' CHECK (missing_value_policy IN ('exclude', 'zero', 'block')),
    active               boolean NOT NULL DEFAULT false,
    created_by           uuid REFERENCES identity.users (id),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    CONSTRAINT scoring_weights_valid CHECK (app.valid_scoring_weights(weights))
);
CREATE UNIQUE INDEX scoring_policies_one_active_idx ON app.scoring_policies (tenant_id) WHERE active;

-- initiatives -----------------------------------------------------------------
CREATE TABLE app.initiatives (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES identity.tenants (id),
    problem_id     uuid NOT NULL,
    title          text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    desired_outcome text NOT NULL DEFAULT '',
    target_segment text,
    effort_low     numeric(10, 2) CHECK (effort_low >= 0),
    effort_high    numeric(10, 2) CHECK (effort_high >= 0),
    effort_unit    text NOT NULL DEFAULT 'person_days' CHECK (effort_unit IN ('person_days', 'person_weeks')),
    status         text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'evaluating', 'decided', 'dropped')),
    owner_user_id  uuid REFERENCES identity.users (id),
    created_by     uuid REFERENCES identity.users (id),
    version        integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, problem_id) REFERENCES app.problems (tenant_id, id),
    CONSTRAINT initiative_effort_range CHECK (effort_low IS NULL OR effort_high IS NULL OR effort_low <= effort_high)
);
CREATE INDEX initiatives_problem_idx ON app.initiatives (tenant_id, problem_id);
CREATE INDEX initiatives_status_idx ON app.initiatives (tenant_id, status, updated_at DESC);

CREATE TABLE app.initiative_assumptions (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES identity.tenants (id),
    initiative_id     uuid NOT NULL,
    statement         text NOT NULL CHECK (length(statement) BETWEEN 1 AND 2000),
    kind              text NOT NULL CHECK (kind IN ('observed', 'estimate', 'hypothesis')),
    source_chunk_id   uuid,
    owner_user_id     uuid REFERENCES identity.users (id),
    validation_status text NOT NULL DEFAULT 'open' CHECK (validation_status IN ('open', 'validated', 'refuted')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, initiative_id) REFERENCES app.initiatives (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, source_chunk_id) REFERENCES app.source_chunks (tenant_id, id),
    -- an "observed" assumption must point at a source
    CONSTRAINT assumption_observed_has_source CHECK (kind <> 'observed' OR source_chunk_id IS NOT NULL)
);
CREATE INDEX initiative_assumptions_initiative_idx ON app.initiative_assumptions (tenant_id, initiative_id);
CREATE INDEX initiative_assumptions_chunk_idx ON app.initiative_assumptions (tenant_id, source_chunk_id);

-- decision documents ------------------------------------------------------------
CREATE TABLE app.decision_documents (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES identity.tenants (id),
    initiative_id       uuid NOT NULL,
    revision            integer NOT NULL CHECK (revision >= 1),
    title               text NOT NULL DEFAULT '' CHECK (length(title) <= 300),
    options             jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(options) = 'array'),
    recommendation_text text NOT NULL DEFAULT '',
    evidence_snapshot   jsonb NOT NULL DEFAULT '{}'::jsonb,
    scoring_snapshot    jsonb NOT NULL DEFAULT '{}'::jsonb,
    scoring_policy_id   uuid,
    snapshot_taken_at   timestamptz,
    ai_run_id           uuid,
    ai_draft            jsonb,
    state               text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'in_review', 'approved', 'superseded')),
    submitted_by        uuid REFERENCES identity.users (id),
    approved_by         uuid REFERENCES identity.users (id),
    approved_at         timestamptz,
    created_by          uuid REFERENCES identity.users (id),
    version             integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, initiative_id, revision),
    FOREIGN KEY (tenant_id, initiative_id) REFERENCES app.initiatives (tenant_id, id),
    FOREIGN KEY (tenant_id, ai_run_id) REFERENCES app.ai_runs (tenant_id, id),
    FOREIGN KEY (tenant_id, scoring_policy_id) REFERENCES app.scoring_policies (tenant_id, id),
    CONSTRAINT decision_approved_has_approver CHECK (
        state NOT IN ('approved', 'superseded') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
    CONSTRAINT decision_review_needs_two_options CHECK (state = 'draft' OR jsonb_array_length(options) >= 2),
    CONSTRAINT decision_review_needs_snapshot CHECK (state = 'draft' OR snapshot_taken_at IS NOT NULL)
);
-- at most one open working revision and one approved revision per initiative
CREATE UNIQUE INDEX decision_one_open_idx ON app.decision_documents (tenant_id, initiative_id)
    WHERE state IN ('draft', 'in_review');
CREATE UNIQUE INDEX decision_one_approved_idx ON app.decision_documents (tenant_id, initiative_id)
    WHERE state = 'approved';
CREATE INDEX decision_documents_state_idx ON app.decision_documents (tenant_id, state, updated_at DESC);
CREATE INDEX decision_documents_ai_run_idx ON app.decision_documents (tenant_id, ai_run_id);
CREATE INDEX decision_documents_policy_idx ON app.decision_documents (tenant_id, scoring_policy_id);

-- Approved revisions are immutable. The only allowed change is the transition approved -> superseded.
CREATE FUNCTION app.guard_decision_document() RETURNS trigger LANGUAGE plpgsql AS
$$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.state IN ('approved', 'superseded') THEN
      RAISE EXCEPTION 'approved decision revisions cannot be deleted' USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.state IN ('approved', 'superseded') THEN
    IF OLD.state = 'approved' AND NEW.state = 'superseded'
       AND (to_jsonb(NEW) - 'state' - 'updated_at' - 'version')
         = (to_jsonb(OLD) - 'state' - 'updated_at' - 'version') THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'approved decision revisions are immutable' USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER decision_documents_guard BEFORE UPDATE OR DELETE ON app.decision_documents
    FOR EACH ROW EXECUTE FUNCTION app.guard_decision_document();

CREATE TABLE app.decision_comments (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid NOT NULL REFERENCES identity.tenants (id),
    decision_document_id uuid NOT NULL,
    author_user_id       uuid NOT NULL REFERENCES identity.users (id),
    body                 text NOT NULL CHECK (length(body) BETWEEN 1 AND 5000),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, decision_document_id) REFERENCES app.decision_documents (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX decision_comments_doc_idx ON app.decision_comments (tenant_id, decision_document_id, created_at);

CREATE TABLE app.decision_evidence (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid NOT NULL REFERENCES identity.tenants (id),
    decision_document_id uuid NOT NULL,
    source_chunk_id      uuid NOT NULL,
    relation             text NOT NULL CHECK (relation IN ('supports', 'contradicts', 'context')),
    quote                text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, decision_document_id, source_chunk_id),
    FOREIGN KEY (tenant_id, decision_document_id) REFERENCES app.decision_documents (tenant_id, id) ON DELETE CASCADE,
    -- RESTRICT: a source referenced by a decision cannot silently disappear (see source deletion rules)
    FOREIGN KEY (tenant_id, source_chunk_id) REFERENCES app.source_chunks (tenant_id, id) ON DELETE RESTRICT
);
CREATE INDEX decision_evidence_doc_idx ON app.decision_evidence (tenant_id, decision_document_id);
CREATE INDEX decision_evidence_chunk_idx ON app.decision_evidence (tenant_id, source_chunk_id);

-- evidence rows of approved revisions are frozen together with the document
CREATE FUNCTION app.guard_decision_evidence() RETURNS trigger LANGUAGE plpgsql AS
$$
DECLARE doc_state text; doc_id uuid; doc_tenant uuid;
BEGIN
  IF TG_OP = 'DELETE' THEN doc_id := OLD.decision_document_id; doc_tenant := OLD.tenant_id;
  ELSE doc_id := NEW.decision_document_id; doc_tenant := NEW.tenant_id; END IF;
  SELECT state INTO doc_state FROM app.decision_documents WHERE tenant_id = doc_tenant AND id = doc_id;
  -- cascading deletes of a draft document arrive here after the document row is gone
  IF doc_state IN ('approved', 'superseded') THEN
    RAISE EXCEPTION 'evidence of approved decision revisions is immutable' USING ERRCODE = 'restrict_violation';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER decision_evidence_guard BEFORE INSERT OR UPDATE OR DELETE ON app.decision_evidence
    FOR EACH ROW EXECUTE FUNCTION app.guard_decision_evidence();

-- updated_at maintenance -------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT c.table_name FROM information_schema.columns c
            WHERE c.table_schema = 'app' AND c.column_name = 'updated_at'
  LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON app.%I FOR EACH ROW EXECUTE FUNCTION infra.touch_updated_at()',
                   t || '_touch', t);
  END LOOP;
END
$$;
