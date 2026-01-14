"""
Database models and session management for Nova Hub
"""

import secrets
from datetime import datetime
from typing import Optional

import bcrypt
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship, sessionmaker

# Database setup
Base = declarative_base()

# TODO: This will be configured from config.toml
DATABASE_URL = "sqlite:////home/lime/nova-data/nova-hub.db"
engine = None
SessionLocal = None


def init_database(database_url: str = DATABASE_URL):
    """Initialize database connection"""
    global engine, SessionLocal
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine


def create_tables():
    """Create all database tables"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency for FastAPI to get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SysopUser(Base):
    """Sysop users for web UI authentication"""

    __tablename__ = "sysop_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    def verify_password(self, password: str) -> bool:
        """Verify password against hash using bcrypt directly"""
        return bcrypt.checkpw(password.encode('utf-8'), self.hashed_password.encode('utf-8'))

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt directly"""
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


class Client(Base):
    """OAuth clients representing remote BBS nodes"""

    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String(64), unique=True, nullable=False, index=True)
    client_secret = Column(String(128), nullable=False)
    bbs_name = Column(String(100), nullable=False)
    contact_email = Column(String(100), nullable=True)
    contact_name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, nullable=True)

    # Relationships
    packets_uploaded = relationship(
        "Packet", foreign_keys="Packet.source_client_id", back_populates="source_client"
    )
    packets_received = relationship(
        "Packet", foreign_keys="Packet.dest_client_id", back_populates="dest_client"
    )
    league_memberships = relationship("LeagueMembership", back_populates="client")

    @staticmethod
    def generate_client_secret() -> str:
        """Generate a secure client secret"""
        return secrets.token_urlsafe(32)


class League(Base):
    """Game leagues (BRE or FE)"""

    __tablename__ = "leagues"

    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(String(10), nullable=False, index=True)  # e.g., "555"
    game_type = Column(String(1), nullable=False)  # 'B' for BRE, 'F' for FE
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Dosemu configuration
    dosemu_path = Column(String(255), nullable=True)
    game_executable = Column(String(100), nullable=True)

    # Relationships
    packets = relationship("Packet", back_populates="league")
    processing_runs = relationship("ProcessingRun", back_populates="league")
    memberships = relationship("LeagueMembership", back_populates="league")

    @property
    def full_id(self) -> str:
        """Return full league identifier (e.g., '555B')"""
        return f"{self.league_id}{self.game_type}"


class LeagueMembership(Base):
    """Tracks which clients are members of which leagues"""

    __tablename__ = "league_memberships"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    bbs_index = Column(Integer, nullable=True, index=True)  # BBS ID (1-255), nullable in DB but enforced in app
    fidonet_address = Column(String(50), nullable=True, index=True)  # Fidonet-style address (e.g., 13:10/100), nullable in DB but enforced in app

    # Relationships
    client = relationship("Client", back_populates="league_memberships")
    league = relationship("League", back_populates="memberships")


class Packet(Base):
    """Uploaded game packets"""

    __tablename__ = "packets"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(50), nullable=False, index=True)  # e.g., "555B0201.001"

    # Parsed from filename
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False)
    source_bbs_index = Column(String(2), nullable=False)  # Hex (e.g., "02")
    dest_bbs_index = Column(String(2), nullable=False)  # Hex (e.g., "01")
    sequence_number = Column(Integer, nullable=False)  # 0-999

    # Client references
    source_client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    dest_client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)

    # Packet data
    file_data = Column(LargeBinary, nullable=False)
    file_size = Column(Integer, nullable=False)
    checksum = Column(String(64), nullable=True)  # SHA256 hash

    # Status tracking
    uploaded_at = Column(DateTime, default=datetime.utcnow, index=True)
    downloaded_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, nullable=True)
    processing_run_id = Column(Integer, ForeignKey("processing_runs.id"), nullable=True)

    # Flags
    is_processed = Column(Boolean, default=False)
    is_downloaded = Column(Boolean, default=False)
    has_error = Column(Boolean, default=False)
    error_message = Column(Text, nullable=True)

    # Relationships
    league = relationship("League", back_populates="packets")
    source_client = relationship(
        "Client", foreign_keys=[source_client_id], back_populates="packets_uploaded"
    )
    dest_client = relationship(
        "Client", foreign_keys=[dest_client_id], back_populates="packets_received"
    )
    processing_run = relationship("ProcessingRun", back_populates="packets")


class ProcessingRun(Base):
    """Batch processing runs via Dosemu"""

    __tablename__ = "processing_runs"

    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=True)

    # Run details
    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="running")  # running, completed, failed

    # Statistics
    packets_processed = Column(Integer, default=0)
    packets_failed = Column(Integer, default=0)
    exit_code = Column(Integer, nullable=True)

    # Logs
    stdout_log = Column(Text, nullable=True)
    stderr_log = Column(Text, nullable=True)
    dosemu_log = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    # Relationships
    league = relationship("League", back_populates="processing_runs")
    packets = relationship("Packet", back_populates="processing_run")
    files = relationship("ProcessingRunFile", back_populates="processing_run")


class ProcessingRunFile(Base):
    """Files generated during processing runs (scores, routes, bbsinfo)"""

    __tablename__ = "processing_run_files"

    id = Column(Integer, primary_key=True, index=True)
    processing_run_id = Column(Integer, ForeignKey("processing_runs.id"), nullable=False)
    file_type = Column(String(20), nullable=False, index=True)  # "score", "routes", "bbsinfo"
    filename = Column(String(100), nullable=False)  # e.g., "BBSLAND.ANS", "routes.lst"
    file_data = Column(Text, nullable=True)  # Text content (ANSI files)
    file_size = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationship
    processing_run = relationship("ProcessingRun", back_populates="files")


class SequenceAlert(Base):
    """Alerts for missing packets or sequence gaps"""

    __tablename__ = "sequence_alerts"

    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False)
    source_bbs_index = Column(String(2), nullable=False)
    dest_bbs_index = Column(String(2), nullable=False)

    # Sequence gap details
    expected_sequence = Column(Integer, nullable=False)
    received_sequence = Column(Integer, nullable=False)
    gap_size = Column(Integer, nullable=False)

    # Alert tracking
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)
    is_resolved = Column(Boolean, default=False)

    # Notes
    description = Column(Text, nullable=True)
    resolution_note = Column(Text, nullable=True)


class SystemSettings(Base):
    """System-wide configuration settings"""

    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def create_default_admin(db: Session, username: str = "admin", password: str = "admin"):
    """Create default admin user if none exists"""
    existing = db.query(SysopUser).filter(SysopUser.username == username).first()
    if not existing:
        admin = SysopUser(
            username=username,
            email="admin@localhost",
            hashed_password=SysopUser.hash_password(password),
            full_name="System Administrator",
            is_active=True,
            is_superuser=True,
        )
        db.add(admin)
        db.commit()
        return admin
    return existing
