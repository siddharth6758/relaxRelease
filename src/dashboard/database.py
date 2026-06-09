import os
from datetime import datetime
from sqlalchemy import create_engine, Column, String, DateTime, Text, Integer
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set.")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLAlchemy setup with connection pooling and SSL for Supabase
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,        # test connection before using it
    pool_recycle=300,          # recycle connections every 5 minutes
    pool_size=5,               # max 5 connections
    max_overflow=2,            # allow 2 extra connections under load
    connect_args={
        "sslmode": "require",  # Supabase requires SSL
        "connect_timeout": 10
    }
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Release(Base):
    """
    Represents a release processed by the RelaxRelease agent.
    Each row is one release event.
    """
    __tablename__ = "releases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo = Column(String(255), nullable=False)
    tag = Column(String(100), nullable=False)
    previous_tag = Column(String(100), nullable=True)
    release_type = Column(String(20), nullable=False)  # "minor" or "major"
    draft_url = Column(String(500), nullable=True)
    release_notes = Column(Text, nullable=True)
    status = Column(String(50), default="draft")  # draft, published, failed
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    """Creates all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created/verified.")


def save_release(
    repo: str,
    tag: str,
    previous_tag: str,
    release_type: str,
    draft_url: str,
    release_notes: str,
    status: str = "draft"
) -> Release:
    """Saves a new release record to the database."""
    db = SessionLocal()
    try:
        release = Release(
            repo=repo,
            tag=tag,
            previous_tag=previous_tag,
            release_type=release_type,
            draft_url=draft_url,
            release_notes=release_notes,
            status=status
        )
        db.add(release)
        db.commit()
        db.refresh(release)
        return release
    finally:
        db.close()


def get_all_releases() -> list:
    """Fetches all releases ordered by most recent first."""
    db = SessionLocal()
    try:
        return db.query(Release).order_by(Release.created_at.desc()).all()
    finally:
        db.close()


def get_release_by_id(release_id: int) -> Release:
    """Fetches a single release by ID."""
    db = SessionLocal()
    try:
        return db.query(Release).filter(Release.id == release_id).first()
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print("Database module loaded successfully.")
    print("Tables: releases")