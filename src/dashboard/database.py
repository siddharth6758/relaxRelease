import os
from datetime import datetime, timezone
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
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=2,
    connect_args={
        "sslmode": "require",
        "connect_timeout": 10
    }
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# ---------------------------------------------------------------------------
# Plan limits — single source of truth
# ---------------------------------------------------------------------------
PLAN_LIMITS = {
    "free": {"repos": 1,   "releases": 3},
    "pro":  {"repos": 10,  "releases": 50},
    "max":  {"repos": 999, "releases": 999},
}


class Release(Base):
    """One release event processed by the agent."""
    __tablename__ = "releases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), nullable=True)          # Supabase user UUID
    repo = Column(String(255), nullable=False)
    tag = Column(String(100), nullable=False)
    previous_tag = Column(String(100), nullable=True)
    release_type = Column(String(20), nullable=False)     # "minor" or "major"
    draft_url = Column(String(500), nullable=True)
    release_notes = Column(Text, nullable=True)
    status = Column(String(50), default="draft")          # draft, published, failed
    created_at = Column(DateTime, default=datetime.utcnow)


class Subscription(Base):
    """Tracks each user's Lemon Squeezy subscription."""
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), nullable=False, unique=True)  # Supabase user UUID
    plan = Column(String(20), nullable=False, default="free")   # free / pro / max
    ls_subscription_id = Column(String(100), nullable=True)     # Lemon Squeezy subscription ID
    ls_customer_id = Column(String(100), nullable=True)         # Lemon Squeezy customer ID
    ls_variant_id = Column(String(100), nullable=True)          # which variant they bought
    status = Column(String(50), default="active")               # active / cancelled / expired / paused
    current_period_end = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# DB init
# ---------------------------------------------------------------------------

def init_db():
    """Creates all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created/verified.")


# ---------------------------------------------------------------------------
# Release helpers
# ---------------------------------------------------------------------------

def save_release(
    repo: str,
    tag: str,
    previous_tag: str,
    release_type: str,
    draft_url: str,
    release_notes: str,
    status: str = "draft",
    user_id: str = None,
) -> Release:
    db = SessionLocal()
    try:
        release = Release(
            user_id=user_id,
            repo=repo,
            tag=tag,
            previous_tag=previous_tag,
            release_type=release_type,
            draft_url=draft_url,
            release_notes=release_notes,
            status=status,
        )
        db.add(release)
        db.commit()
        db.refresh(release)
        return release
    finally:
        db.close()


def get_all_releases(user_id: str = None) -> list:
    db = SessionLocal()
    try:
        q = db.query(Release)
        if user_id:
            q = q.filter(Release.user_id == user_id)
        return q.order_by(Release.created_at.desc()).all()
    finally:
        db.close()


def get_release_by_id(release_id: int) -> Release:
    db = SessionLocal()
    try:
        return db.query(Release).filter(Release.id == release_id).first()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Subscription helpers
# ---------------------------------------------------------------------------

def get_subscription(user_id: str) -> Subscription | None:
    db = SessionLocal()
    try:
        return db.query(Subscription).filter(Subscription.user_id == user_id).first()
    finally:
        db.close()


def get_user_plan(user_id: str) -> str:
    """Returns the user's current plan name. Defaults to 'free'."""
    sub = get_subscription(user_id)
    if not sub or sub.status not in ("active", "on_trial"):
        return "free"
    return sub.plan


def upsert_subscription(
    user_id: str,
    plan: str,
    ls_subscription_id: str,
    ls_customer_id: str,
    ls_variant_id: str,
    status: str,
    current_period_end: datetime = None,
) -> Subscription:
    db = SessionLocal()
    try:
        sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
        if sub:
            sub.plan = plan
            sub.ls_subscription_id = ls_subscription_id
            sub.ls_customer_id = ls_customer_id
            sub.ls_variant_id = ls_variant_id
            sub.status = status
            sub.current_period_end = current_period_end
            sub.updated_at = datetime.utcnow()
        else:
            sub = Subscription(
                user_id=user_id,
                plan=plan,
                ls_subscription_id=ls_subscription_id,
                ls_customer_id=ls_customer_id,
                ls_variant_id=ls_variant_id,
                status=status,
                current_period_end=current_period_end,
            )
            db.add(sub)
        db.commit()
        db.refresh(sub)
        return sub
    finally:
        db.close()


def cancel_subscription(ls_subscription_id: str) -> None:
    db = SessionLocal()
    try:
        sub = db.query(Subscription).filter(
            Subscription.ls_subscription_id == ls_subscription_id
        ).first()
        if sub:
            sub.status = "cancelled"
            sub.plan = "free"
            sub.updated_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Plan enforcement helpers
# ---------------------------------------------------------------------------

def get_user_repo_count(user_id: str) -> int:
    """Count distinct repos this user has released from."""
    db = SessionLocal()
    try:
        from sqlalchemy import func
        result = db.query(func.count(Release.repo.distinct())).filter(
            Release.user_id == user_id
        ).scalar()
        return result or 0
    finally:
        db.close()


def get_user_monthly_release_count(user_id: str) -> int:
    """Count releases this user triggered in the current calendar month."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        result = db.query(Release).filter(
            Release.user_id == user_id,
            Release.created_at >= month_start,
        ).count()
        return result or 0
    finally:
        db.close()


def check_plan_limits(user_id: str) -> dict:
    """
    Returns {"allowed": True} or {"allowed": False, "reason": "...", "limit_type": "repos"|"releases"}
    """
    plan = get_user_plan(user_id)
    limits = PLAN_LIMITS[plan]

    repo_count = get_user_repo_count(user_id)
    release_count = get_user_monthly_release_count(user_id)

    if repo_count >= limits["repos"]:
        return {
            "allowed": False,
            "reason": f"Repo limit reached ({repo_count}/{limits['repos']}) on {plan} plan.",
            "limit_type": "repos",
            "plan": plan,
        }

    if release_count >= limits["releases"]:
        return {
            "allowed": False,
            "reason": f"Monthly release limit reached ({release_count}/{limits['releases']}) on {plan} plan.",
            "limit_type": "releases",
            "plan": plan,
        }

    return {"allowed": True, "plan": plan}


if __name__ == "__main__":
    init_db()
    print("Database module loaded successfully.")
    print("Tables: releases, subscriptions")