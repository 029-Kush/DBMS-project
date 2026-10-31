"""ORM models. The knowledge-base tables (entities, chunk_refs, bodies, entity_links) are created by
knowledge_base/sql/schema.sql and mapped here as they are; the app's own tables come from sql/001_web.sql."""
from datetime import datetime, timezone

import bcrypt
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ARRAY, BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, LargeBinary, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

db = SQLAlchemy()

ROLES = ("viewer", "editor", "admin")   # each role can do everything the roles before it can


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(32), nullable=False, unique=True)
    email = Column(String(254), nullable=False, unique=True)
    password_hash = Column(String(60), nullable=False)
    role = Column(String(10), nullable=False, default="viewer")
    active = Column("is_active", Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime(timezone=True))

    bookmarks = relationship("Bookmark", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)

    @property
    def is_active(self):          # Flask-Login refuses inactive users
        return self.active

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()

    def check_password(self, password):
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    def can(self, role):
        return ROLES.index(self.role) >= ROLES.index(role)


class Entity(db.Model):
    __tablename__ = "entities"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    type = Column(Text, nullable=False)
    domain = Column(ARRAY(Text), nullable=False, default=list)
    aliases = Column(ARRAY(Text), nullable=False, default=list)
    summary = Column(Text)
    version = Column(Text)
    ext_id = Column(Text, nullable=False, default="")
    facts = Column(JSONB, nullable=False, default=dict)

    chunks = relationship("ChunkRef", back_populates="entity", passive_deletes=True, lazy="dynamic")


class Body(db.Model):
    """One distinct text; several chunk_refs can point at it (content-addressed by md5 hash)."""
    __tablename__ = "bodies"
    id = Column(BigInteger, primary_key=True)
    hash = Column(LargeBinary, nullable=False, unique=True)
    body = Column(Text, nullable=False)
    flags = Column(ARRAY(Text), nullable=False, default=list)
    trust = Column(SmallInteger, nullable=False, default=2)


class ChunkRef(db.Model):
    """A passage: (entity, section, source) -> body."""
    __tablename__ = "chunk_refs"
    id = Column(BigInteger, primary_key=True)
    entity_id = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    section = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    source_version = Column(Text)
    trust = Column(SmallInteger, nullable=False, default=2)
    is_code = Column(Boolean, nullable=False, default=False)
    content_hash = Column(LargeBinary, nullable=False, unique=True)
    model_version = Column(Text, nullable=False)
    body_id = Column(BigInteger, ForeignKey("bodies.id"), nullable=False)

    entity = relationship("Entity", back_populates="chunks")
    body = relationship("Body", lazy="joined")


class EntityLink(db.Model):
    __tablename__ = "entity_links"
    src = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True)
    dst = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True)
    rel = Column(Text, primary_key=True)
    note = Column(Text)
    source = Column(Text, nullable=False, default="")


class Bookmark(db.Model):
    __tablename__ = "bookmarks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chunk_id = Column(BigInteger, ForeignKey("chunk_refs.id", ondelete="CASCADE"), nullable=False)
    note = Column(String(500))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="bookmarks")
    chunk = relationship("ChunkRef", lazy="joined")


class SearchLog(db.Model):
    __tablename__ = "search_log"
    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    query = Column(String(300), nullable=False)
    n_results = Column(SmallInteger, nullable=False)
    latency_ms = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class AuditLog(db.Model):
    """Written only by the audit_row() trigger; read-only in the app."""
    __tablename__ = "audit_log"
    id = Column(BigInteger, primary_key=True)
    table_name = Column(Text, nullable=False)
    row_id = Column(Text, nullable=False)
    action = Column(Text, nullable=False)
    actor = Column(Text, nullable=False)
    old_row = Column(JSONB)
    new_row = Column(JSONB)
    changed_at = Column(DateTime(timezone=True), nullable=False)
