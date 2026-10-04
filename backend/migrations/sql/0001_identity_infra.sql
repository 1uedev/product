-- 0001: schemas, identity tables, infrastructure helpers.
-- Roles are created by infra/postgres/db-init.sh (they must exist before this migration runs).

CREATE SCHEMA identity;
CREATE SCHEMA app;
CREATE SCHEMA IF NOT EXISTS infra;

REVOKE ALL ON SCHEMA identity, app, infra FROM PUBLIC;
GRANT USAGE ON SCHEMA identity TO de_auth;
GRANT USAGE ON SCHEMA app TO de_app, de_worker, de_recovery;
GRANT USAGE ON SCHEMA infra TO de_app, de_worker, de_outbox, de_recovery;

-- pg_trgm is a trusted extension; it lives in infra so that runtime roles only need USAGE there.
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA infra;

-- ---------------------------------------------------------------------------
-- helpers
-- ---------------------------------------------------------------------------
CREATE FUNCTION infra.current_tenant_id() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE AS
$$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;
REVOKE ALL ON FUNCTION infra.current_tenant_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION infra.current_tenant_id() TO de_app, de_worker, de_outbox, de_recovery, de_migrator;

CREATE FUNCTION infra.touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

CREATE FUNCTION infra.forbid_modification() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN RAISE EXCEPTION '% on %.% is not allowed (append-only table)', TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
  USING ERRCODE = 'restrict_violation'; END $$;

-- ---------------------------------------------------------------------------
-- identity schema (global, not row-level-secured; only de_auth may touch it, see ADR 0002)
-- ---------------------------------------------------------------------------
CREATE TABLE identity.users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    oidc_issuer   text NOT NULL,
    oidc_subject  text NOT NULL,
    email         text,
    display_name  text,
    last_login_at timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (oidc_issuer, oidc_subject)
);

CREATE TABLE identity.tenants (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    locale          text NOT NULL DEFAULT 'de-DE',
    timezone        text NOT NULL DEFAULT 'Europe/Berlin',
    status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    -- explicit, configurable mapping to a future central organisation id (never inferred from names or domains)
    external_org_id text UNIQUE,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE identity.memberships (
    tenant_id  uuid NOT NULL REFERENCES identity.tenants (id),
    user_id    uuid NOT NULL REFERENCES identity.users (id),
    role       text NOT NULL CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    status     text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id)
);
CREATE INDEX memberships_user_idx ON identity.memberships (user_id);

CREATE TABLE identity.sessions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES identity.users (id),
    token_hash   text NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    csrf_secret  text NOT NULL,
    id_token_enc bytea,
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sessions_user_idx ON identity.sessions (user_id);
CREATE INDEX sessions_expiry_idx ON identity.sessions (expires_at);

CREATE TABLE identity.invitations (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES identity.tenants (id),
    normalized_email text NOT NULL,
    intended_role    text NOT NULL CHECK (intended_role IN ('owner', 'admin', 'editor', 'viewer')),
    token_hash       text NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    expires_at       timestamptz NOT NULL,
    accepted_at      timestamptz,
    accepted_by      uuid REFERENCES identity.users (id),
    revoked_at       timestamptz,
    invited_by       uuid REFERENCES identity.users (id),
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX invitations_tenant_idx ON identity.invitations (tenant_id);

-- short-lived OIDC login attempts (state, nonce, PKCE verifier); consumed exactly once
CREATE TABLE identity.login_attempts (
    state_hash        text PRIMARY KEY CHECK (length(state_hash) = 64),
    nonce             text NOT NULL,
    code_verifier_enc bytea NOT NULL,
    redirect_after    text NOT NULL DEFAULT '/',
    expires_at        timestamptz NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Last-owner protection: a tenant must always keep at least one active owner.
CREATE FUNCTION identity.protect_last_owner() RETURNS trigger LANGUAGE plpgsql AS
$$
DECLARE other_owners integer;
BEGIN
  IF OLD.role = 'owner' AND OLD.status = 'active'
     AND (TG_OP = 'DELETE' OR NEW.role <> 'owner' OR NEW.status <> 'active') THEN
    -- serialise concurrent demotions of the same tenant
    PERFORM pg_advisory_xact_lock(hashtextextended('owners:' || OLD.tenant_id::text, 0));
    SELECT count(*) INTO other_owners FROM identity.memberships
     WHERE tenant_id = OLD.tenant_id AND role = 'owner' AND status = 'active' AND user_id <> OLD.user_id;
    IF other_owners = 0 THEN
      RAISE EXCEPTION 'cannot remove the last owner of tenant %', OLD.tenant_id USING ERRCODE = 'restrict_violation';
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  NEW.updated_at := now();
  RETURN NEW;
END
$$;
CREATE TRIGGER memberships_protect_last_owner BEFORE UPDATE OR DELETE ON identity.memberships
    FOR EACH ROW EXECUTE FUNCTION identity.protect_last_owner();

GRANT SELECT, INSERT, UPDATE ON identity.users TO de_auth;
GRANT SELECT ON identity.tenants TO de_auth;
GRANT SELECT, INSERT, UPDATE, DELETE ON identity.memberships TO de_auth;
GRANT SELECT, INSERT, UPDATE, DELETE ON identity.sessions TO de_auth;
GRANT SELECT, INSERT, UPDATE ON identity.invitations TO de_auth;
GRANT SELECT, INSERT, DELETE ON identity.login_attempts TO de_auth;
