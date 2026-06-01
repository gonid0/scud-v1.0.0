"""initial schema — full consolidated baseline

Collapsed from the original 8-migration chain
(0001_initial … 0008_permit_max_total_issued) into a single source-of-truth
migration.  Migrations 0002_seed_test_data (dev-only data inserts) through
0008_permit_max_total_issued have been deleted; their DDL is absorbed here.

Notes
-----
* 0002_seed_test_data contained only data INSERTs guarded by SCUD_SEED_DEV=1 —
  no schema objects.  Those INSERTs are NOT folded in; they belong to a separate
  seeding process, not to the canonical schema migration.
* 0007_reader_profiles seeded param_bounds + the 4 starter reader_profiles. That
  is FUNCTIONAL reference data the resolver and the admin-API validation depend on
  (an empty param_bounds makes the profile/override API 422 every param), so it IS
  folded into upgrade() below, sourced from the canonical catalog
  (scud.domain.reader_param_catalog) so values never drift from firmware CFG[].

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ------------------------------------------------------------------
    # ENUMs
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TYPE key_status AS ENUM (
            'active', 'revoked_by_server', 'revoked_by_reader', 'revoked_in_bloom', 'expired'
        )
    """)
    op.execute("CREATE TYPE grant_kind AS ENUM ('soft', 'hard')")

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE users (
            user_id          SERIAL PRIMARY KEY,
            login            VARCHAR(64) UNIQUE NOT NULL,
            password_hash    VARCHAR(255) NOT NULL,
            display_name     VARCHAR(128) NOT NULL,
            user_group_id    UUID NOT NULL,
            is_active        BOOLEAN NOT NULL DEFAULT TRUE,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_users_group ON users(user_group_id) WHERE is_active")

    # ------------------------------------------------------------------
    # user_devices
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE user_devices (
            device_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id          INT NOT NULL REFERENCES users(user_id),
            phone_pubkey     BYTEA NOT NULL UNIQUE CHECK (length(phone_pubkey) = 32),
            device_label     VARCHAR(128),
            registered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_active        BOOLEAN NOT NULL DEFAULT TRUE,
            deactivated_at   TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX idx_devices_user ON user_devices(user_id) WHERE is_active")

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE sessions (
            session_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id               INT NOT NULL REFERENCES users(user_id),
            device_id             UUID REFERENCES user_devices(device_id),
            session_token         VARCHAR(64) NOT NULL UNIQUE,
            refresh_token         VARCHAR(64) NOT NULL UNIQUE,
            issued_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            session_expires_at    TIMESTAMPTZ NOT NULL,
            refresh_expires_at    TIMESTAMPTZ NOT NULL,
            revoked_at            TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX idx_sessions_token ON sessions(session_token) WHERE revoked_at IS NULL")
    op.execute("CREATE INDEX idx_sessions_refresh ON sessions(refresh_token) WHERE revoked_at IS NULL")

    # ------------------------------------------------------------------
    # api_keys
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE api_keys (
            api_key_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key_hash         VARCHAR(128) NOT NULL UNIQUE,
            key_prefix       VARCHAR(16) NOT NULL,
            kind             VARCHAR(32) NOT NULL,
            name             VARCHAR(128) NOT NULL,
            created_by       INT REFERENCES users(user_id),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at       TIMESTAMPTZ,
            last_used_at     TIMESTAMPTZ,
            revoked_at       TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX idx_api_keys_hash ON api_keys(key_hash) WHERE revoked_at IS NULL")

    # ------------------------------------------------------------------
    # reader_groups
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE reader_groups (
            group_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name             VARCHAR(128) NOT NULL,
            description      TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ------------------------------------------------------------------
    # reader_profile  (from 0007)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE reader_profile (
            profile_id      UUID PRIMARY KEY,
            name            VARCHAR(64) NOT NULL UNIQUE,
            hardware_class  VARCHAR(32) NOT NULL,
            description     TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ------------------------------------------------------------------
    # reader_profile_param  (from 0007)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE reader_profile_param (
            profile_id  UUID NOT NULL REFERENCES reader_profile(profile_id) ON DELETE CASCADE,
            param_key   VARCHAR(32) NOT NULL,
            value       BIGINT NOT NULL,
            PRIMARY KEY (profile_id, param_key)
        )
    """)

    # ------------------------------------------------------------------
    # param_bounds  (from 0007)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE param_bounds (
            param_key       VARCHAR(32) NOT NULL,
            hardware_class  VARCHAR(32) NOT NULL,
            min_value       BIGINT NOT NULL,
            max_value       BIGINT NOT NULL,
            default_value   BIGINT NOT NULL,
            server_floor    BIGINT,
            server_ceiling  BIGINT,
            PRIMARY KEY (param_key, hardware_class)
        )
    """)

    # ------------------------------------------------------------------
    # readers  (includes config from 0006, profile_id + hardware_class from 0007)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE readers (
            reader_id                     BYTEA PRIMARY KEY CHECK (length(reader_id) = 16),
            reader_group_id               UUID NOT NULL REFERENCES reader_groups(group_id),
            display_name                  VARCHAR(128) NOT NULL,
            description                   TEXT,
            reader_pubkey                 BYTEA NOT NULL CHECK (length(reader_pubkey) = 32),
            server_ed25519_privkey        BYTEA NOT NULL CHECK (length(server_ed25519_privkey) = 32),
            server_ed25519_pubkey         BYTEA NOT NULL CHECK (length(server_ed25519_pubkey) = 32),
            server_x25519_privkey         BYTEA NOT NULL CHECK (length(server_x25519_privkey) = 32),
            server_x25519_pubkey          BYTEA NOT NULL CHECK (length(server_x25519_pubkey) = 32),
            enrolled_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_active                     BOOLEAN NOT NULL DEFAULT TRUE,
            last_applied_filter_version   BIGINT NOT NULL DEFAULT 0,
            last_contact_at               TIMESTAMPTZ,
            last_known_time               TIMESTAMPTZ,
            config                        JSONB,
            profile_id                    UUID REFERENCES reader_profile(profile_id) ON DELETE SET NULL,
            hardware_class                VARCHAR(32) NOT NULL DEFAULT 'esp32_mains_ble'
        )
    """)
    op.execute("CREATE INDEX idx_readers_group ON readers(reader_group_id) WHERE is_active")

    # ------------------------------------------------------------------
    # reader_param_override  (from 0007)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE reader_param_override (
            reader_id   BYTEA NOT NULL REFERENCES readers(reader_id) ON DELETE CASCADE,
            param_key   VARCHAR(32) NOT NULL,
            value       BIGINT NOT NULL,
            PRIMARY KEY (reader_id, param_key)
        )
    """)

    # ------------------------------------------------------------------
    # permits  (includes revoke_initiated_at from 0003, max_total_issued from 0008)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE permits (
            permit_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                  INT NOT NULL REFERENCES users(user_id),
            reader_id                BYTEA NOT NULL REFERENCES readers(reader_id),
            display_name             VARCHAR(128) NOT NULL,
            description              TEXT,
            valid_from               TIMESTAMPTZ NOT NULL,
            valid_until              TIMESTAMPTZ NOT NULL,
            n_parallel               INT NOT NULL CHECK (n_parallel > 0),
            max_token_ttl_seconds    INT NOT NULL CHECK (max_token_ttl_seconds > 0),
            created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by_api_key       UUID REFERENCES api_keys(api_key_id),
            revoked_at               TIMESTAMPTZ,
            revoke_initiated_at      TIMESTAMPTZ,
            max_total_issued         INT,  -- квота ещё-не-истёкших ключей (expires_at>now), bloom-защита
            CHECK (valid_until > valid_from)
        )
    """)
    op.execute("CREATE INDEX idx_permits_user ON permits(user_id) WHERE revoked_at IS NULL")
    op.execute("CREATE INDEX idx_permits_reader ON permits(reader_id) WHERE revoked_at IS NULL")
    op.execute("""
        CREATE INDEX idx_permits_pending_revoke
        ON permits (revoke_initiated_at)
        WHERE revoke_initiated_at IS NOT NULL AND revoked_at IS NULL
    """)

    # ------------------------------------------------------------------
    # issued_keys
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE issued_keys (
            key_id                   BYTEA PRIMARY KEY CHECK (length(key_id) = 16),
            permit_id                UUID NOT NULL REFERENCES permits(permit_id),
            reader_id                BYTEA NOT NULL REFERENCES readers(reader_id),
            phone_pubkey             BYTEA NOT NULL CHECK (length(phone_pubkey) = 32),
            device_id                UUID NOT NULL REFERENCES user_devices(device_id),
            issued_at                TIMESTAMPTZ NOT NULL,
            expires_at               TIMESTAMPTZ NOT NULL,
            serial                   INT NOT NULL,
            payload                  BYTEA NOT NULL DEFAULT '\\x0000' CHECK (length(payload) = 2),
            status                   key_status NOT NULL DEFAULT 'active',
            is_active                BOOLEAN NOT NULL DEFAULT TRUE,
            revoked_at               TIMESTAMPTZ,
            revoke_reason            SMALLINT,
            committed_filter_version BIGINT,
            full_key_bytes           BYTEA NOT NULL CHECK (length(full_key_bytes) = 151),
            CHECK (expires_at > issued_at)
        )
    """)
    op.execute("CREATE INDEX idx_keys_permit_active ON issued_keys(permit_id) WHERE is_active")
    op.execute("CREATE INDEX idx_keys_reader_status ON issued_keys(reader_id, status)")
    op.execute("CREATE INDEX idx_keys_device ON issued_keys(device_id) WHERE is_active")
    op.execute("CREATE INDEX idx_keys_expiry ON issued_keys(expires_at) WHERE is_active")
    op.execute("""
        CREATE INDEX idx_keys_committed_version ON issued_keys(reader_id, committed_filter_version)
            WHERE committed_filter_version IS NOT NULL
    """)

    # Trigger: keep is_active in sync with status
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_is_active() RETURNS TRIGGER AS $$
        BEGIN
            NEW.is_active := NEW.status NOT IN ('revoked_by_reader', 'revoked_in_bloom', 'expired');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_sync_is_active
            BEFORE INSERT OR UPDATE OF status ON issued_keys
            FOR EACH ROW EXECUTE FUNCTION sync_is_active()
    """)

    # ------------------------------------------------------------------
    # time_grants
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE time_grants (
            grant_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            permit_id             UUID NOT NULL REFERENCES permits(permit_id),
            reader_id             BYTEA NOT NULL REFERENCES readers(reader_id),
            authority_user_id     INT NOT NULL REFERENCES users(user_id),
            authority_pubkey      BYTEA NOT NULL CHECK (length(authority_pubkey) = 32),
            kind                  grant_kind NOT NULL,
            issued_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at            TIMESTAMPTZ NOT NULL,
            full_grant_bytes      BYTEA NOT NULL CHECK (length(full_grant_bytes) = 148),
            revoked_at            TIMESTAMPTZ,
            UNIQUE (permit_id, authority_pubkey)
        )
    """)
    op.execute("CREATE INDEX idx_grants_permit ON time_grants(permit_id) WHERE revoked_at IS NULL")

    # ------------------------------------------------------------------
    # filter_packages
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE filter_packages (
            filter_version           BIGINT NOT NULL,
            reader_id                BYTEA NOT NULL REFERENCES readers(reader_id),
            generated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            m_bits                   INT NOT NULL,
            k_hashes                 SMALLINT NOT NULL,
            hash_seed                INT NOT NULL,
            filter_bytes_len         INT NOT NULL,
            whitelist_count          SMALLINT NOT NULL,
            blacklist_delta_count    SMALLINT NOT NULL,
            package_bytes            BYTEA NOT NULL,
            is_current               BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (reader_id, filter_version)
        )
    """)
    op.execute("CREATE INDEX idx_filter_current ON filter_packages(reader_id) WHERE is_current")

    # ------------------------------------------------------------------
    # delivery_tasks
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE delivery_tasks (
            task_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            reader_id            BYTEA NOT NULL REFERENCES readers(reader_id),
            filter_version       BIGINT NOT NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at         TIMESTAMPTZ,
            completion_source    VARCHAR(16),
            courier_user_id      INT REFERENCES users(user_id),
            FOREIGN KEY (reader_id, filter_version) REFERENCES filter_packages(reader_id, filter_version)
        )
    """)
    op.execute("CREATE INDEX idx_delivery_open ON delivery_tasks(reader_id) WHERE completed_at IS NULL")

    # ------------------------------------------------------------------
    # reader_reports
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE reader_reports (
            report_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            report_type              VARCHAR(32) NOT NULL,
            reader_id                BYTEA NOT NULL REFERENCES readers(reader_id),
            received_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            delivered_by_user_id     INT REFERENCES users(user_id),
            raw_bytes                BYTEA NOT NULL,
            processed_at             TIMESTAMPTZ,
            processing_error         TEXT
        )
    """)
    op.execute("CREATE INDEX idx_reports_unprocessed ON reader_reports(received_at) WHERE processed_at IS NULL")
    op.execute("CREATE INDEX idx_reports_reader ON reader_reports(reader_id, received_at)")

    # ------------------------------------------------------------------
    # background_tasks
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE background_tasks (
            task_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            task_type            VARCHAR(32) NOT NULL,
            payload              JSONB NOT NULL,
            status               VARCHAR(16) NOT NULL DEFAULT 'pending',
            scheduled_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at           TIMESTAMPTZ,
            completed_at         TIMESTAMPTZ,
            error                TEXT,
            retry_count          INT NOT NULL DEFAULT 0,
            worker_id            VARCHAR(64)
        )
    """)
    op.execute("""
        CREATE INDEX idx_tasks_pending ON background_tasks(scheduled_at, task_type)
            WHERE status = 'pending'
    """)

    # ------------------------------------------------------------------
    # admin_audit_log
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE admin_audit_log (
            audit_id             BIGSERIAL PRIMARY KEY,
            occurred_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            actor_type           VARCHAR(16) NOT NULL,
            actor_id             UUID NOT NULL,
            action               VARCHAR(64) NOT NULL,
            target_type          VARCHAR(32),
            target_id            TEXT,
            details              JSONB
        )
    """)
    op.execute("CREATE INDEX idx_audit_actor ON admin_audit_log(actor_id, occurred_at)")
    op.execute("CREATE INDEX idx_audit_target ON admin_audit_log(target_type, target_id, occurred_at)")

    # ------------------------------------------------------------------
    # passage_events  (from 0004)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE passage_events (
            event_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            reader_id         BYTEA NOT NULL CHECK (length(reader_id) = 16)
                              REFERENCES readers(reader_id),
            receipt_nonce     BYTEA NOT NULL CHECK (length(receipt_nonce) = 16),
            key_id            BYTEA NOT NULL CHECK (length(key_id) = 16),
            permit_id         UUID NOT NULL,
            user_id           INT REFERENCES users(user_id),
            phone_pubkey      BYTEA NOT NULL CHECK (length(phone_pubkey) = 32),
            passed_at         TIMESTAMPTZ NOT NULL,
            direction         SMALLINT NOT NULL DEFAULT 0,
            session_seq       INTEGER NOT NULL,
            verdict           SMALLINT NOT NULL DEFAULT 0,
            flags             SMALLINT NOT NULL DEFAULT 0,
            delivered_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            delivered_by      INT REFERENCES users(user_id),
            raw_receipt       BYTEA NOT NULL CHECK (length(raw_receipt) = 192),
            CONSTRAINT uq_passage_dedup UNIQUE (reader_id, receipt_nonce)
        )
    """)
    op.execute(
        "CREATE INDEX idx_passage_user_time "
        "ON passage_events(user_id, passed_at DESC) "
        "WHERE user_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_passage_reader_time "
        "ON passage_events(reader_id, passed_at DESC)"
    )
    op.execute("CREATE INDEX idx_passage_key ON passage_events(key_id)")

    # ------------------------------------------------------------------
    # webhook_subscriptions  (from 0005)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE webhook_subscriptions (
            webhook_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name                  VARCHAR(128) NOT NULL,
            url                   VARCHAR(512) NOT NULL,
            event_types           VARCHAR(256) NOT NULL DEFAULT 'passage_event',
            secret                VARCHAR(128),
            is_active             BOOLEAN NOT NULL DEFAULT TRUE,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by_api_key    UUID REFERENCES api_keys(api_key_id),
            last_success_at       TIMESTAMPTZ,
            last_failure_at       TIMESTAMPTZ,
            last_error            TEXT,
            consecutive_failures  INT NOT NULL DEFAULT 0
        )
    """)
    op.execute(
        "CREATE INDEX idx_webhooks_active ON webhook_subscriptions(webhook_id) "
        "WHERE is_active"
    )

    # ------------------------------------------------------------------
    # Functional reference seed (was 0007): param_bounds (every param ×
    # hardware_class) + the 4 starter reader profiles, from the canonical catalog.
    # NOT test data — the resolver + admin validation read these.
    # ------------------------------------------------------------------
    import uuid as _uuid
    from scud.domain.reader_param_catalog import (
        HARDWARE_CLASSES,
        PARAMS,
        SEED_PROFILES,
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO param_bounds (param_key, hardware_class, min_value, "
            "max_value, default_value, server_floor, server_ceiling) VALUES "
            "(:param_key, :hardware_class, :min_value, :max_value, :default_value, "
            ":server_floor, :server_ceiling)"
        ),
        [
            {
                "param_key": p.key,
                "hardware_class": hw,
                "min_value": p.min_value,
                "max_value": p.max_value,
                "default_value": p.default,
                "server_floor": p.server_floor,
                "server_ceiling": p.server_ceiling,
            }
            for hw in HARDWARE_CLASSES
            for p in PARAMS
        ],
    )
    for profile in SEED_PROFILES:
        pid = str(_uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO reader_profile (profile_id, name, hardware_class, "
                "description, created_at, updated_at) VALUES (:pid, :name, :hw, "
                ":desc, NOW(), NOW())"
            ),
            {"pid": pid, "name": profile.name, "hw": profile.hardware_class, "desc": profile.description},
        )
        param_rows = [{"pid": pid, "k": k, "v": v} for k, v in profile.params.items()]
        if param_rows:
            bind.execute(
                sa.text(
                    "INSERT INTO reader_profile_param (profile_id, param_key, value) "
                    "VALUES (:pid, :k, :v)"
                ),
                param_rows,
            )


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.execute("DROP TABLE IF EXISTS webhook_subscriptions CASCADE")
    op.execute("DROP TABLE IF EXISTS passage_events CASCADE")
    op.execute("DROP TABLE IF EXISTS admin_audit_log CASCADE")
    op.execute("DROP TABLE IF EXISTS background_tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS reader_reports CASCADE")
    op.execute("DROP TABLE IF EXISTS delivery_tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS filter_packages CASCADE")
    op.execute("DROP TABLE IF EXISTS time_grants CASCADE")
    op.execute("DROP TYPE IF EXISTS grant_kind CASCADE")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_is_active ON issued_keys")
    op.execute("DROP FUNCTION IF EXISTS sync_is_active CASCADE")
    op.execute("DROP TABLE IF EXISTS issued_keys CASCADE")
    op.execute("DROP TYPE IF EXISTS key_status CASCADE")
    op.execute("DROP TABLE IF EXISTS permits CASCADE")
    op.execute("DROP TABLE IF EXISTS reader_param_override CASCADE")
    op.execute("DROP TABLE IF EXISTS readers CASCADE")
    op.execute("DROP TABLE IF EXISTS param_bounds CASCADE")
    op.execute("DROP TABLE IF EXISTS reader_profile_param CASCADE")
    op.execute("DROP TABLE IF EXISTS reader_profile CASCADE")
    op.execute("DROP TABLE IF EXISTS reader_groups CASCADE")
    op.execute("DROP TABLE IF EXISTS api_keys CASCADE")
    op.execute("DROP TABLE IF EXISTS sessions CASCADE")
    op.execute("DROP TABLE IF EXISTS user_devices CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto CASCADE")
