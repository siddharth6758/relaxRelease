import os
import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, DateTime, Text, Integer, Boolean, BigInteger, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, joinedload
from supabase import create_client
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set.")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# initialise Supabase client
def get_supabase():
    return create_client(
        os.environ.get("SUPABASE_URL"),
        os.environ.get("SUPABASE_SERVICE_KEY")
    )

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
    expires_at = Column(DateTime, nullable=True)
    plan_activated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Repository(Base):
    """Tracks each user's GitHub repositories."""
    __tablename__ = "repositories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    repo_full_name = Column(Text, nullable=False)
    webhook_id = Column(BigInteger, nullable=True)
    webhook_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

class TicketStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    closed = "closed"

class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    subject = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String, default="open")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    replies = relationship("TicketReply", back_populates="ticket", order_by="TicketReply.created_at")
    images = relationship("TicketImage", backref="ticket_ref", order_by="TicketImage.created_at")

class TicketReply(Base):
    __tablename__ = "ticket_replies"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id"), nullable=False)
    sender = Column(String, nullable=False)  # "user" or "admin"
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    ticket = relationship("Ticket", back_populates="replies")

class TicketImage(Base):
    __tablename__ = "ticket_images"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id"), nullable=False)
    image_url = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

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
# Ticket helpers
# ---------------------------------------------------------------------------

def upload_ticket_images(ticket_id: str, files: list) -> None:
    """Upload images to Supabase Storage and save URLs to ticket_images table."""
    supabase = get_supabase()
    db = SessionLocal()
    try:
        for file in files:
            if not file.filename:
                continue
            ext = file.filename.rsplit(".", 1)[-1].lower()
            if ext not in ["jpg", "jpeg", "png", "gif", "webp"]:
                continue
            contents = file.file.read()
            if len(contents) > 5 * 1024 * 1024:  # 5MB limit
                continue
            path = f"{ticket_id}/{uuid.uuid4()}.{ext}"
            supabase.storage.from_("ticket-images").upload(
                path,
                contents,
                {"content-type": file.content_type}
            )
            public_url = f"{os.environ.get('SUPABASE_URL')}/storage/v1/object/public/ticket-images/{path}"
            image = TicketImage(
                ticket_id=uuid.UUID(ticket_id),
                image_url=public_url
            )
            db.add(image)
        db.commit()
    finally:
        db.close()

def create_ticket(user_id: str, subject: str, message: str) -> Ticket:
    db = SessionLocal()
    try:
        ticket = Ticket(
            user_id=uuid.UUID(user_id),
            subject=subject,
            message=message,
            status="open"
        )
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
        return ticket
    finally:
        db.close()

def get_tickets_by_user(user_id: str) -> list:
    db = SessionLocal()
    try:
        return db.query(Ticket).options(
            joinedload(Ticket.images)
        ).filter(
            Ticket.user_id == uuid.UUID(user_id)
        ).order_by(Ticket.created_at.desc()).all()
    finally:
        db.close()

def get_ticket_by_id(ticket_id: str, user_id: str) -> Ticket | None:
    db = SessionLocal()
    try:
        return db.query(Ticket).options(
            joinedload(Ticket.images),
            joinedload(Ticket.replies)
        ).filter(
            Ticket.id == uuid.UUID(ticket_id),
            Ticket.user_id == uuid.UUID(user_id)
        ).first()
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
    sub = get_subscription(user_id)
    if not sub:
        return "free"
    if sub.expires_at and datetime.utcnow() > sub.expires_at:
        return "free"
    if sub.status != "active":
        return "free"
    return sub.plan


def upsert_subscription(
    user_id: str,
    plan: str,
    ls_subscription_id: str,
    ls_customer_id: str,
    ls_variant_id: str,
    status: str,
    expires_at: datetime = None,
    current_period_end: datetime = None,
) -> Subscription:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
        if sub:
            sub.plan = plan
            sub.ls_subscription_id = ls_subscription_id
            sub.ls_customer_id = ls_customer_id
            sub.ls_variant_id = ls_variant_id
            sub.status = status
            sub.expires_at = expires_at
            sub.plan_activated_at = now
            sub.updated_at = now
        else:
            sub = Subscription(
                user_id=user_id,
                plan=plan,
                ls_subscription_id=ls_subscription_id,
                ls_customer_id=ls_customer_id,
                ls_variant_id=ls_variant_id,
                status=status,
                expires_at=expires_at,
                plan_activated_at=now,
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
# Repository helpers
# ---------------------------------------------------------------------------
def add_repository(user_id: str, repo_full_name: str, webhook_id: int) -> Repository:
    with SessionLocal() as session:
        repo = Repository(
            user_id=user_id,
            repo_full_name=repo_full_name,
            webhook_id=str(webhook_id),
            webhook_active=True
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)
        return repo


def list_repositories(user_id: str) -> list[Repository]:
    with SessionLocal() as session:
        return session.query(Repository)\
            .filter(Repository.user_id == user_id)\
            .order_by(Repository.created_at.desc())\
            .all()


def delete_repository(user_id: str, repo_full_name: str) -> Repository | None:
    with SessionLocal() as session:
        repo = session.query(Repository)\
            .filter(Repository.user_id == user_id,
                    Repository.repo_full_name == repo_full_name)\
            .first()
        if not repo:
            return None
        session.delete(repo)
        session.commit()
        return repo

# ---------------------------------------------------------------------------
# Plan enforcement helpers
# ---------------------------------------------------------------------------

def get_user_id_by_repo(repo_full_name: str) -> str | None:
    db = SessionLocal()
    try:
        record = db.query(Repository).filter(
            Repository.repo_full_name == repo_full_name
        ).first()
        return str(record.user_id) if record else None
    finally:
        db.close()

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
    db = SessionLocal()
    try:
        sub = get_subscription(user_id)
        # Use downgrade date as counter start if on free plan
        if sub and sub.plan_activated_at and get_user_plan(user_id) == "free":
            count_from = sub.updated_at or sub.plan_activated_at
        else:
            now = datetime.utcnow()
            count_from = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        result = db.query(Release).filter(
            Release.user_id == user_id,
            Release.created_at >= count_from,
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

def enforce_free_tier_on_expiry(user_id: str) -> None:
    """
    Called when a user's plan expires.
    Keeps only their oldest repo, resets release counter from now.
    """
    db = SessionLocal()
    try:
        # Find all distinct repos ordered by first use
        from sqlalchemy import func
        oldest = (
            db.query(Release.repo, func.min(Release.created_at).label("first_used"))
            .filter(Release.user_id == user_id)
            .group_by(Release.repo)
            .order_by("first_used")
            .first()
        )
        if not oldest:
            return
        keep_repo = oldest.repo
        # Soft-delete releases for all other repos by nulling user_id
        db.query(Release).filter(
            Release.user_id == user_id,
            Release.repo != keep_repo,
        ).update({"user_id": None})
        db.commit()
        print(f"⬇️  Downgraded {user_id}: kept repo={keep_repo}")
    finally:
        db.close()


def get_expired_paid_users() -> list:
    """Returns user_ids whose plan has expired but are still marked active."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        subs = db.query(Subscription).filter(
            Subscription.status == "active",
            Subscription.plan != "free",
            Subscription.expires_at <= now,
        ).all()
        return [s.user_id for s in subs]
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print("Database module loaded successfully.")
    print("Tables: releases, subscriptions")