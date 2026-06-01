"""SQLAlchemy ORM models mirroring the §4 schema."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSON works on both PostgreSQL and SQLite; JSONType is PostgreSQL-only and handled in migrations.
JSONType = JSON

# Phase-3 / BE-DAT reader config: JSONB on PostgreSQL, plain JSON on SQLite
# (used for the Reader.config column so tests on SQLite don't break).
JSONBType = JSON().with_variant(JSONB, "postgresql")

# Uuid works on both PostgreSQL (native UUID) and SQLite (string-backed)
UUIDType = Uuid(as_uuid=True)


class TzDateTime(TypeDecorator):
    """DateTime that always returns UTC-aware datetimes regardless of DB backend.

    SQLite stores datetimes as naive strings; this decorator ensures the loaded
    value always carries tzinfo=UTC so domain code can compare freely.
    PostgreSQL stores the TZ offset natively and returns aware datetimes already.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(
        self, value: datetime | None, dialect: object
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    user_group_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )

    devices: Mapped[list[UserDevice]] = relationship("UserDevice", back_populates="user")
    sessions: Mapped[list[Session]] = relationship("Session", back_populates="user")


class UserDevice(Base):
    __tablename__ = "user_devices"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.user_id"), nullable=False)
    phone_pubkey: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    device_label: Mapped[Optional[str]] = mapped_column(String(128))
    registered_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)

    user: Mapped[User] = relationship("User", back_populates="devices")


class Session(Base):
    __tablename__ = "sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.user_id"), nullable=False)
    device_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUIDType, ForeignKey("user_devices.device_id")
    )
    session_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    refresh_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    session_expires_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False
    )
    refresh_expires_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)

    user: Mapped[User] = relationship("User", back_populates="sessions")


class ApiKey(Base):
    __tablename__ = "api_keys"

    api_key_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)


class ReaderGroup(Base):
    __tablename__ = "reader_groups"

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )

    readers: Mapped[list[Reader]] = relationship("Reader", back_populates="group")


# ---------------------------------------------------------------------------
# Phase 1 — reader parameterization (migration 0007_reader_profiles)
# ---------------------------------------------------------------------------

class ReaderProfile(Base):
    """Named configuration profile for a hardware class.

    A profile stores a *sparse* set of parameter overrides above the firmware
    defaults. Rows are inserted into reader_profile_param, not directly here.
    """
    __tablename__ = "reader_profile"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hardware_class: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )

    params: Mapped[list[ReaderProfileParam]] = relationship(
        "ReaderProfileParam",
        back_populates="profile",
        cascade="all, delete-orphan",
    )
    readers: Mapped[list[Reader]] = relationship("Reader", back_populates="profile")


class ReaderProfileParam(Base):
    """Sparse per-profile parameter override.

    Only stores params that differ from firmware defaults. PK is
    (profile_id, param_key).
    """
    __tablename__ = "reader_profile_param"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("reader_profile.profile_id", ondelete="CASCADE"),
        primary_key=True,
    )
    param_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False)

    profile: Mapped[ReaderProfile] = relationship(
        "ReaderProfile", back_populates="params"
    )


class ReaderParamOverride(Base):
    """Sparse per-reader parameter override (applied on top of the profile).

    PK is (reader_id, param_key).
    """
    __tablename__ = "reader_param_override"

    reader_id: Mapped[bytes] = mapped_column(
        LargeBinary(16),
        ForeignKey("readers.reader_id", ondelete="CASCADE"),
        primary_key=True,
    )
    param_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ParamBounds(Base):
    """Read-only firmware-class bounds for each parameter.

    Seeded once in migration 0007_reader_profiles from the catalog module.
    One row per (param_key, hardware_class).
    """
    __tablename__ = "param_bounds"

    param_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    hardware_class: Mapped[str] = mapped_column(String(32), primary_key=True)
    min_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    default_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    server_floor: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    server_ceiling: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


# ---------------------------------------------------------------------------
# Reader — extended with profile_id and hardware_class (Phase 1)
# ---------------------------------------------------------------------------

class Reader(Base):
    __tablename__ = "readers"

    reader_id: Mapped[bytes] = mapped_column(LargeBinary(16), primary_key=True)
    reader_group_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("reader_groups.group_id"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    reader_pubkey: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    server_ed25519_privkey: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    server_ed25519_pubkey: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    server_x25519_privkey: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    server_x25519_pubkey: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    enrolled_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_applied_filter_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    last_contact_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    last_known_time: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    # Phase 3 (docs/11): nullable JSONB column mirroring firmware-provisioned reader
    # parameters. The backend stores whatever the caller provides; field semantics
    # are owned by the firmware. Added in migration 0006_reader_config.
    config: Mapped[Optional[dict]] = mapped_column(JSONBType, nullable=True)
    # Phase 1 (migration 0007_reader_profiles): structured parameterization layer.
    # profile_id NULL => reader uses pure firmware defaults.
    profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUIDType,
        ForeignKey("reader_profile.profile_id", ondelete="SET NULL"),
        nullable=True,
    )
    # hardware_class identifies the ESP32 variant for bound lookups.
    # Default 'esp32_mains_ble' is the primary/mains variant.
    hardware_class: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="esp32_mains_ble", default="esp32_mains_ble"
    )

    group: Mapped[ReaderGroup] = relationship("ReaderGroup", back_populates="readers")
    profile: Mapped[Optional[ReaderProfile]] = relationship(
        "ReaderProfile", back_populates="readers"
    )
    param_overrides: Mapped[list[ReaderParamOverride]] = relationship(
        "ReaderParamOverride",
        foreign_keys=[ReaderParamOverride.reader_id],
        primaryjoin="Reader.reader_id == ReaderParamOverride.reader_id",
        cascade="all, delete-orphan",
    )


class Permit(Base):
    __tablename__ = "permits"

    permit_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.user_id"), nullable=False)
    reader_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("readers.reader_id"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)
    valid_until: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)
    n_parallel: Mapped[int] = mapped_column(Integer, nullable=False)
    max_token_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    # Квота на число ещё-не-истёкших ключей permit'а (expires_at > now, любой
    # статus: active / revoked_*). NULL = без лимита. Защищает bloom-фильтр
    # отзыва от раздувания: ограничивает, сколько ключей одного permit'а могут
    # одновременно находиться в окне валидности (и потому попасть в фильтр).
    # В отличие от n_parallel (потолок ОДНОВРЕМЕННО активных по is_active) сюда
    # входят и отозванные, пока их срок не вышел; истёкшие выпадают сами.
    # Имя историческое (колонка из 0008) — менять не стали ради совместимости.
    max_total_issued: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    created_by_api_key: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUIDType, ForeignKey("api_keys.api_key_id")
    )
    # Двухфазный admin-revoke (migration 0003_permit_revoke_initiated):
    #   revoke_initiated_at IS NOT NULL AND revoked_at IS NULL → "revoke pending"
    #     (ключи помечены revoked_by_server, ждём пока ридер применит bloom)
    #   revoked_at IS NOT NULL → permit окончательно отозван
    revoke_initiated_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)


# NOTE: SQLAlchemy Enum — values mirror PostgreSQL key_status type (§4)
KeyStatusEnum = Enum(
    "active",
    "revoked_by_server",
    "revoked_by_reader",
    "revoked_in_bloom",
    "expired",
    name="key_status",
)

GrantKindEnum = Enum("soft", "hard", name="grant_kind")


class IssuedKey(Base):
    __tablename__ = "issued_keys"

    key_id: Mapped[bytes] = mapped_column(LargeBinary(16), primary_key=True)
    permit_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("permits.permit_id"), nullable=False
    )
    reader_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("readers.reader_id"), nullable=False
    )
    phone_pubkey: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("user_devices.device_id"), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)
    serial: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary(2), nullable=False, default=b"\x00\x00")
    status: Mapped[str] = mapped_column(KeyStatusEnum, nullable=False, default="active")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    revoke_reason: Mapped[Optional[int]] = mapped_column(SmallInteger)
    committed_filter_version: Mapped[Optional[int]] = mapped_column(BigInteger)
    full_key_bytes: Mapped[bytes] = mapped_column(LargeBinary(151), nullable=False)


class TimeGrant(Base):
    __tablename__ = "time_grants"
    __table_args__ = (
        UniqueConstraint("permit_id", "authority_pubkey"),
    )

    grant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    permit_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("permits.permit_id"), nullable=False
    )
    reader_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("readers.reader_id"), nullable=False
    )
    authority_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.user_id"), nullable=False
    )
    authority_pubkey: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    kind: Mapped[str] = mapped_column(GrantKindEnum, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    expires_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)
    full_grant_bytes: Mapped[bytes] = mapped_column(LargeBinary(148), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)


class FilterPackage(Base):
    __tablename__ = "filter_packages"

    filter_version: Mapped[int] = mapped_column(BigInteger, nullable=False, primary_key=True)
    reader_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("readers.reader_id"), nullable=False, primary_key=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    m_bits: Mapped[int] = mapped_column(Integer, nullable=False)
    k_hashes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    hash_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    filter_bytes_len: Mapped[int] = mapped_column(Integer, nullable=False)
    whitelist_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    blacklist_delta_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    package_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DeliveryTask(Base):
    __tablename__ = "delivery_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["reader_id", "filter_version"],
            ["filter_packages.reader_id", "filter_packages.filter_version"],
        ),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    reader_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("readers.reader_id"), nullable=False
    )
    filter_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    completion_source: Mapped[Optional[str]] = mapped_column(String(16))
    courier_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.user_id")
    )


class ReaderReport(Base):
    __tablename__ = "reader_reports"

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reader_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("readers.reader_id"), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    delivered_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.user_id")
    )
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    processing_error: Mapped[Optional[str]] = mapped_column(Text)


class BackgroundTask(Base):
    __tablename__ = "background_tasks"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    scheduled_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    error: Mapped[Optional[str]] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_id: Mapped[Optional[str]] = mapped_column(String(64))


class PassageEvent(Base):
    """Учёт проходов (shared §15). Один экземпляр = одна квитанция, подписанная
    ридером, доставленная phone'ом через POST /app/reports/submit.

    Дедупликация по (reader_id, receipt_nonce). user_id денормализован через
    issued_keys.permit_id → permits.user_id для быстрых выборок «история по
    сотруднику» без двойного JOIN.
    """

    __tablename__ = "passage_events"
    __table_args__ = (
        UniqueConstraint("reader_id", "receipt_nonce", name="uq_passage_dedup"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    reader_id: Mapped[bytes] = mapped_column(
        LargeBinary(16), ForeignKey("readers.reader_id"), nullable=False
    )
    receipt_nonce: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    key_id: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    permit_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False)
    # user_id - может быть NULL если key_id не найден (например, доставлен после
    # удаления permit'а). Тогда квитанция всё равно сохраняется — это улика.
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.user_id")
    )
    phone_pubkey: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    passed_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)
    direction: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    session_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    flags: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    delivered_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    delivered_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.user_id")
    )
    raw_receipt: Mapped[bytes] = mapped_column(LargeBinary(192), nullable=False)


class WebhookSubscription(Base):
    """Подписка внешнего сервиса на события SCUD.

    Минимальная имплементация: один URL + список event-типов + опциональный
    secret для HMAC-подписи payload'а. Срабатывает на passage_event (можно
    расширять).

    Доставка идёт через worker-task notify_webhook с retry/backoff.
    """

    __tablename__ = "webhook_subscriptions"

    webhook_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    # CSV списка типов событий: "passage_event,delivery_receipt".
    # JSON был бы каноничнее, но CSV проще для form-input и без потери expressiveness.
    event_types: Mapped[str] = mapped_column(String(256), nullable=False, default="passage_event")
    # Если задан — payload подписывается HMAC-SHA256 ключом и шлётся в X-SCUD-Signature.
    secret: Mapped[Optional[str]] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    created_by_api_key: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUIDType, ForeignKey("api_keys.api_key_id")
    )
    last_success_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    last_failure_at: Mapped[Optional[datetime]] = mapped_column(TzDateTime)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"

    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        TzDateTime, nullable=False, default=_now
    )
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(32))
    target_id: Mapped[Optional[str]] = mapped_column(Text)
    details: Mapped[Optional[dict]] = mapped_column(JSONType)
