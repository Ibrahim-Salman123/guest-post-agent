"""SQLite models, password encryption, auto-migration and guardrail layer."""
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from sqlalchemy import (Column, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint, and_, create_engine, func, inspect,
                        or_, text)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

load_dotenv()
engine = create_engine(
    os.getenv("DATABASE_URL", "sqlite:///./guestposting.db"),
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=True, expire_on_commit=False)
Base = declarative_base()

STATUSES = ["Pending", "Generating", "Optimizing", "Draft Created",
            "Waiting for Approval", "Published", "Rejected", "Failed"]


def normalize_url(u: str) -> str:
    """Lowercase scheme/host, strip trailing slash, drop fragment."""
    if not u:
        return u
    p = urlparse(u.strip())
    scheme = (p.scheme or "https").lower()
    netloc = p.netloc.lower()
    path = p.path.rstrip("/") or ""
    query = "&".join(
        kv for kv in p.query.split("&")
        if kv and not kv.lower().startswith(("utm_", "fbclid", "gclid", "_ga"))
    )
    return urlunparse((scheme, netloc, path, p.params, query, ""))


def _fernet() -> Fernet:
    key = os.getenv("SECRET_KEY")
    if not key:
        if not os.path.exists(".secret_key"):
            with open(".secret_key", "w") as f:
                f.write(Fernet.generate_key().decode())
        key = open(".secret_key").read().strip()
    return Fernet(key.encode())


def encrypt(v: str) -> str:
    return _fernet().encrypt(v.encode()).decode()


def decrypt(v: str) -> str:
    return _fernet().decrypt(v.encode()).decode()


class Website(Base):
    __tablename__ = "websites"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    url = Column(String(300), nullable=False, unique=True)
    username = Column(String(120), nullable=False)
    password_hash = Column(Text, nullable=False)
    niche = Column(String(120), default="")
    article_limit = Column(Integer, nullable=False, default=1)
    daily_limit = Column(Integer, nullable=False, default=0)   # 0 = no daily limit
    min_words = Column(Integer, nullable=False, default=900)
    max_words = Column(Integer, nullable=False, default=1200)
    published_count = Column(Integer, nullable=False, default=0)
    cms_type = Column(String(20), default="wordpress")
    requirements = Column(Text, default="")
    notes = Column(Text, default="")
    selectors = Column(Text, default="")
    internal_links = Column(Text, default="")                  # JSON list
    created_at = Column(DateTime, default=datetime.utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    status = Column(String(20), default="Active")
    created_at = Column(DateTime, default=datetime.utcnow)
    articles = relationship("Article", back_populates="campaign",
                            cascade="all, delete-orphan")


class KeywordURLPair(Base):
    __tablename__ = "keyword_url_pairs"
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    keyword = Column(String(200), nullable=False)
    target_url = Column(String(500), nullable=False)
    normalized_url = Column(String(500))
    assigned_website_id = Column(Integer, ForeignKey("websites.id"))


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("keyword", "website_id", name="uq_kw_site"),
        UniqueConstraint("normalized_url", "website_id", name="uq_url_site"),
    )
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    website_id = Column(Integer, ForeignKey("websites.id"), nullable=False)
    article_number = Column(Integer, default=1)
    keyword = Column(String(200), nullable=False)
    target_url = Column(String(500), nullable=False)
    normalized_url = Column(String(500))
    title = Column(String(300))
    meta_description = Column(String(400))
    content = Column(Text)
    image_alt = Column(String(300))
    warnings = Column(Text)
    seo_score = Column(Integer)
    draft_url = Column(String(600))
    live_url = Column(String(600))
    status = Column(String(30), default="Pending")
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    published_at = Column(DateTime)   # ✅ FIX #4: separate publish timestamp
    campaign = relationship("Campaign", back_populates="articles")
    website = relationship("Website")


def _auto_migrate():
    """SQLite lightweight migration: add missing columns without Alembic."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    migrations = [
        # websites
        ("websites", "min_words", "INTEGER NOT NULL DEFAULT 900"),
        ("websites", "max_words", "INTEGER NOT NULL DEFAULT 1200"),
        ("websites", "published_count", "INTEGER NOT NULL DEFAULT 0"),
        ("websites", "requirements", "TEXT DEFAULT ''"),
        ("websites", "notes", "TEXT DEFAULT ''"),
        ("websites", "selectors", "TEXT DEFAULT ''"),
        ("websites", "cms_type", "VARCHAR(20) DEFAULT 'wordpress'"),
        ("websites", "daily_limit", "INTEGER NOT NULL DEFAULT 0"),
        ("websites", "internal_links", "TEXT DEFAULT ''"),
        # articles
        ("articles", "normalized_url", "VARCHAR(500)"),
        ("articles", "image_alt", "VARCHAR(300)"),
        ("articles", "warnings", "TEXT"),
        ("articles", "updated_at", "DATETIME"),
        ("articles", "article_number", "INTEGER DEFAULT 1"),
        ("articles", "meta_description", "VARCHAR(400)"),
        ("articles", "published_at", "DATETIME"),   # ✅ FIX #4
        # keyword_url_pairs
        ("keyword_url_pairs", "normalized_url", "VARCHAR(500)"),
    ]
    with engine.begin() as conn:
        for tbl, col, typ in migrations:
            if tbl not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(tbl)}
            if col in existing:
                continue
            try:
                conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}"))
                print(f"[migrate] added {tbl}.{col}")
            except Exception as e:
                print(f"[migrate] skip {tbl}.{col}: {e}")


def init_db():
    Base.metadata.create_all(engine)
    _auto_migrate()
    with SessionLocal() as db:
        db.query(Article).filter(Article.status.in_(["Generating", "Optimizing"])) \
            .update({"status": "Pending"}, synchronize_session=False)
        db.commit()


def remaining_slots(db, site: Website) -> int:
    used = db.query(func.count(Article.id)).filter(
        Article.website_id == site.id,
        Article.status != "Rejected",
    ).scalar()
    return max(site.article_limit - used, 0)


def daily_used(db, site: Website) -> int:
    """
    ✅ FIX #1: Daily limit ab publish-date pe based hai.

    - Published articles  -> `published_at` date use hoti hai
    - In-flight articles  -> `created_at` date use hoti hai (jab tak publish na ho)
    Isse raat 11:59 pe bana article agle din publish hone pe agle din count hoga,
    aur fresh campaign jo aaj chala wo aaj count hoga.
    """
    today_local = datetime.now().date()
    today_start = datetime.combine(today_local, datetime.min.time())
    today_end = today_start + timedelta(days=1)

    return db.query(func.count(Article.id)).filter(
        Article.website_id == site.id,
        Article.status.in_(["Pending", "Generating", "Optimizing",
                            "Draft Created", "Waiting for Approval", "Published"]),
        or_(
            # Published today (kabhi bhi create hua ho)
            and_(Article.published_at.isnot(None),
                 Article.published_at >= today_start,
                 Article.published_at < today_end),
            # Not yet published but created today
            and_(Article.published_at.is_(None),
                 Article.created_at >= today_start,
                 Article.created_at < today_end),
        ),
    ).scalar()


def check_guardrails(db, site: Website, keyword: str, url: str, campaign_id=None):
    # Daily limit check first (Requirement #14)
    if site.daily_limit and site.daily_limit > 0:
        if daily_used(db, site) >= site.daily_limit:
            return (f"'{site.name}' has reached today's daily limit "
                    f"({site.daily_limit}). Try again tomorrow.")
    # Total limit
    if site.published_count >= site.article_limit or remaining_slots(db, site) <= 0:
        return f"'{site.name}' has reached its article limit ({site.article_limit})."
    norm = normalize_url(url)
    q = db.query(Article).filter(Article.website_id == site.id)
    if q.filter(func.lower(Article.keyword) == keyword.lower()).first():
        return f"Keyword '{keyword}' was already used on '{site.name}'."
    if q.filter(Article.normalized_url == norm).first():
        return f"URL {url} was already used on '{site.name}'."
    if campaign_id and db.query(Article).filter(
            Article.campaign_id == campaign_id,
            func.lower(Article.keyword) == keyword.lower(),
            Article.status == "Published").first():
        return f"'{keyword}' is already published in this campaign."
    return None