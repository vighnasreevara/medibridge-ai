"""Schema ``auth``: users, roles, permissions, sessions and the rate-limit ledger."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, uuid_pk, vocab_check


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = {"schema": "auth"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)  # doctor | nurse | admin
    label: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")

    permissions: Mapped[list["Permission"]] = relationship(secondary="auth.role_permissions", lazy="selectin")


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = {"schema": "auth"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # Permissions that touch patient data are additionally checked against care-team scope.
    patient_scoped: Mapped[bool] = mapped_column(Boolean, default=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = {"schema": "auth"}

    role_id: Mapped[int] = mapped_column(ForeignKey("auth.roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("auth.permissions.id", ondelete="CASCADE"),
                                               primary_key=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        vocab_check("status", "user_status"),
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        {"schema": "auth"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(254))
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("auth.roles.id"))
    status: Mapped[str] = mapped_column(String(40), default="active")
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Bumped on role change / forced sign-out: sessions issued at an older epoch are invalid.
    session_epoch: Mapped[int] = mapped_column(Integer, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    role: Mapped[Role] = relationship(lazy="joined")
    doctor_profile: Mapped["DoctorProfile | None"] = relationship(back_populates="user", lazy="selectin")


class DoctorProfile(Base):
    __tablename__ = "doctor_profiles"
    __table_args__ = {"schema": "auth"}

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.id", ondelete="CASCADE"), primary_key=True)
    specialty: Mapped[str] = mapped_column(String(120))
    department: Mapped[str] = mapped_column(String(120), default="")
    # Synthetic licence number; administrative identifier, never shown in clinical views.
    license_number: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = created_at()

    user: Mapped[User] = relationship(back_populates="doctor_profile")


class AuthSession(Base):
    """Server-side session. Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_active", "user_id", "revoked_at"), {"schema": "auth"})

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(token) hex
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.id", ondelete="CASCADE"))
    session_epoch: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(60))
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")


class AuthAttempt(Base):
    """Append-only rate-limit ledger (login failures, per-IP request bursts)."""

    __tablename__ = "auth_attempts"
    __table_args__ = (Index("ix_auth_attempts_key_time", "key", "created_at"), {"schema": "auth"})

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(200))  # e.g. "login:ip:127.0.0.1", "login:user:<sha256(email)>"
    created_at: Mapped[datetime] = created_at()
