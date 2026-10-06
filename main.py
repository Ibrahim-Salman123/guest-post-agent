"""FastAPI backend. Run: uvicorn main:app --reload  ->  http://localhost:8000"""
# ---- Groq cache_breakpoint fix (CrewAI bug #5886) ----

import crewai.llms.cache as _crewai_cache
_crewai_cache.mark_cache_breakpoint = lambda msg: msg
# ------------------------------------------------------
from automation import delete_wp_post
import base64, json, os, secrets, threading
from datetime import datetime
from typing import Dict, List, Optional
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from sqlalchemy import func
import re

from database import (Article, Campaign, KeywordURLPair, SessionLocal, Website,
                      check_guardrails, daily_used, decrypt, encrypt, init_db,
                      normalize_url, remaining_slots)
from tasks import process_article, run_campaign

load_dotenv()
app = FastAPI(title="Guest Posting AI Agent")

ALLOWED_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if ALLOWED_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                       allow_methods=["*"], allow_headers=["*"])

RUNNING: set = set()
RUNNING_LOCK = threading.Lock()

ARTICLE_LOCKS: Dict[int, threading.Lock] = {}
ARTICLE_LOCKS_GUARD = threading.Lock()


def _article_lock(aid: int) -> threading.Lock:
    with ARTICLE_LOCKS_GUARD:
        if aid not in ARTICLE_LOCKS:
            ARTICLE_LOCKS[aid] = threading.Lock()
        return ARTICLE_LOCKS[aid]


UI_USER = os.getenv("UI_USER", "admin")
UI_PASS = os.getenv("UI_PASS", "")


@app.middleware("http")
async def _auth_mw(request: Request, call_next):
    if UI_PASS and request.url.path.startswith("/api"):
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.startswith("Basic "):
            try:
                user, _, pwd = base64.b64decode(auth.split(" ", 1)[1]).decode().partition(":")
                ok = secrets.compare_digest(user, UI_USER) and \
                     secrets.compare_digest(pwd, UI_PASS)
            except Exception:
                ok = False
        if not ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="GuestPost"'})
    return await call_next(request)


@app.on_event("startup")
def _start(): init_db()


def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()


def _url(v: str) -> str:
    v = (v or "").strip()
    if not v.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")
    return v


class WebsiteIn(BaseModel):
    id: Optional[int] = None
    name: str = Field(min_length=1, max_length=120)
    url: str
    username: str = Field(min_length=1)
    password: Optional[str] = None
    niche: str = ""
    article_limit: int = Field(ge=1, le=1000)
    daily_limit: int = Field(default=0, ge=0, le=1000)
    min_words: int = Field(default=900, ge=200, le=10000)
    max_words: int = Field(default=1200, ge=200, le=10000)
    cms_type: str = "wordpress"
    requirements: str = ""
    notes: str = ""
    selectors: str = ""
    internal_links: str = ""

    @field_validator("url")
    @classmethod
    def _v_url(cls, v: str) -> str:
        return _url(v)

    @field_validator("max_words")
    @classmethod
    def _v_max(cls, v: int, info: ValidationInfo) -> int:
        min_w = info.data.get("min_words", 200)
        if v < min_w:
            raise ValueError("max_words must be >= min_words")
        return v

    @field_validator("selectors")
    @classmethod
    def _v_sel(cls, v: str) -> str:
        if v and v.strip():
            try:
                json.loads(v)
            except Exception as e:
                raise ValueError(f"selectors must be valid JSON: {e}")
        return v

    @field_validator("internal_links")
    @classmethod
    def _v_il(cls, v: str) -> str:
        if v and v.strip():
            try:
                data = json.loads(v)
                if not isinstance(data, list):
                    raise ValueError("must be a JSON list")
                for item in data:
                    if not isinstance(item, dict) or "url" not in item or "anchor" not in item:
                        raise ValueError("each item needs 'url' and 'anchor'")
            except Exception as e:
                raise ValueError(f"internal_links must be JSON list: {e}")
        return v


class PairIn(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    target_url: str
    website_id: Optional[int] = None

    @field_validator("target_url")
    @classmethod
    def _v_url(cls, v: str) -> str:
        return _url(v)


class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    pairs: List[PairIn] = Field(min_length=1)
    website_ids: Optional[List[int]] = None


class ApproveIn(BaseModel):
    live_url: str

    @field_validator("live_url")
    @classmethod
    def _v_url(cls, v: str) -> str:
        return _url(v)


def site_dict(db, s: Website):
    rem = remaining_slots(db, s)
    used = s.article_limit - rem
    return {"id": s.id, "name": s.name, "url": s.url, "username": s.username,
            "niche": s.niche, "article_limit": s.article_limit,
            "daily_limit": s.daily_limit, "daily_used": daily_used(db, s),
            "min_words": s.min_words, "max_words": s.max_words,
            "assigned": used, "remaining": rem,
            "published_count": s.published_count, "cms_type": s.cms_type,
            "requirements": s.requirements, "notes": s.notes,
            "selectors": s.selectors, "internal_links": s.internal_links}


def art_dict(a: Article, full=False):
    d = {"id": a.id, "campaign_id": a.campaign_id, "website_id": a.website_id,
         "website": a.website.name if a.website else "",
         "article_number": a.article_number, "keyword": a.keyword,
         "target_url": a.target_url, "title": a.title, "seo_score": a.seo_score,
         "words": len(re.sub(r"<[^>]+>", " ", a.content or "").split()) if a.content else 0,
         "draft_url": a.draft_url, "live_url": a.live_url, "status": a.status,
         "error": a.error, "warnings": a.warnings, "image_alt": a.image_alt,
         "created_at": a.created_at.isoformat() if a.created_at else None,
         # ✅ FIX #4: expose publish timestamp
         "published_at": a.published_at.isoformat() if a.published_at else None}
    if full:
        d.update(content=a.content, meta_description=a.meta_description)
    return d


@app.get("/api/health")
def health(): return {"ok": True}


@app.post("/api/websites")
def save_website(w: WebsiteIn, db=Depends(get_db)):
    s = db.get(Website, w.id) if w.id else None
    if w.id and not s:
        raise HTTPException(404, "Website not found")

    if not s:
        if not w.password:
            raise HTTPException(422, "Password is required for a new website")
        s = Website(
            name=w.name, url=w.url, username=w.username,
            password_hash=encrypt(w.password), niche=w.niche,
            article_limit=w.article_limit, daily_limit=w.daily_limit,
            min_words=w.min_words, max_words=w.max_words,
            cms_type=w.cms_type, requirements=w.requirements,
            notes=w.notes, selectors=w.selectors,
            internal_links=w.internal_links,
        )
        db.add(s)
    else:
        if w.password:
            s.password_hash = encrypt(w.password)
        for f in ("name", "url", "username", "niche", "article_limit",
                  "daily_limit", "cms_type", "requirements", "notes",
                  "selectors", "internal_links", "min_words", "max_words"):
            setattr(s, f, getattr(w, f))

    with db.no_autoflush:
        dup = db.query(Website).filter(
            Website.url == w.url,
            Website.id != (s.id or -1)
        ).first()
    if dup:
        db.rollback()
        raise HTTPException(409, "A website with this URL already exists")

    if s.id and w.article_limit < s.published_count:
        db.rollback()
        raise HTTPException(422, "Limit cannot be below already published count")

    db.commit()
    db.refresh(s)
    return site_dict(db, s)


@app.post("/api/websites/test-connection")
def test_connection(body: dict, db=Depends(get_db)):
    import requests as _rq
    try:
        wid = body.get("id")
        if wid:
            site = db.get(Website, wid)
            if not site:
                raise HTTPException(404, "Website not found")
            url = site.url.rstrip("/")
            creds = (site.username, decrypt(site.password_hash))
        else:
            url = (body.get("url") or "").rstrip("/")
            creds = (body.get("username") or "", body.get("password") or "")
        r = _rq.get(f"{url}/wp-json/wp/v2/users/me", auth=creds, timeout=15)
        if r.status_code == 200:
            return {"ok": True, "message": "WP REST API reachable; credentials valid."}
        if r.status_code in (401, 403):
            raise HTTPException(401, "Credentials rejected. Use an Application Password.")
        raise HTTPException(400, f"WP REST API returned {r.status_code}: {r.text[:200]}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Connection failed: {e}")


@app.get("/api/websites")
def list_websites(db=Depends(get_db)):
    return [site_dict(db, s) for s in db.query(Website).order_by(Website.id)]


@app.delete("/api/websites/{wid}")
def delete_website(wid: int, force: bool = False, db=Depends(get_db)):
    """
    Delete website. If it has articles:
    - force=False → 409 error (safety, asks for confirmation)
    - force=True  → cascade delete articles + keyword pairs too
    """
    s = db.get(Website, wid)
    if not s:
        raise HTTPException(404, "Website not found")

    article_count = db.query(Article).filter(Article.website_id == wid).count()

    if article_count > 0 and not force:
        raise HTTPException(
            409,
            f"Website has {article_count} article(s). Delete anyway? "
            f"Pass force=true to confirm."
        )

    # Cascade delete: articles + keyword pairs + website
    if force:
        db.query(Article).filter(Article.website_id == wid).delete(synchronize_session=False)
        db.query(KeywordURLPair).filter(KeywordURLPair.assigned_website_id == wid).delete(synchronize_session=False)

    db.query(Website).filter(Website.id == wid).delete()
    db.commit()
    return {"ok": True, "deleted_articles": article_count}


@app.post("/api/campaigns")
def create_campaign(c: CampaignIn, db=Depends(get_db)):
    pool = db.query(Website).filter(Website.id.in_(c.website_ids)).all() if c.website_ids \
        else db.query(Website).all()
    camp = Campaign(name=c.name)
    db.add(camp)
    db.flush()
    alerts, created = [], 0
    for p in c.pairs:
        cands = [db.get(Website, p.website_id)] if p.website_id else \
            sorted(pool, key=lambda s: -remaining_slots(db, s))
        if not cands or cands[0] is None:
            alerts.append(f"'{p.keyword}': website not found.")
            continue
        err, site = None, None
        for s in cands:
            err = check_guardrails(db, s, p.keyword, p.target_url, camp.id)
            if not err:
                site = s
                break
        if not site:
            alerts.append(f"'{p.keyword}': {err}")
            continue
        n = db.query(func.count(Article.id)).filter(
            Article.campaign_id == camp.id).scalar() + 1
        db.add(KeywordURLPair(campaign_id=camp.id, keyword=p.keyword,
                              target_url=p.target_url,
                              normalized_url=normalize_url(p.target_url),
                              assigned_website_id=site.id))
        db.add(Article(campaign_id=camp.id, website_id=site.id, keyword=p.keyword,
                       target_url=p.target_url,
                       normalized_url=normalize_url(p.target_url),
                       article_number=n))
        db.flush()
        created += 1
    if not created:
        db.rollback()
        raise HTTPException(400, "No articles created. " + " | ".join(alerts))
    db.commit()
    return {"campaign_id": camp.id, "created": created, "alerts": alerts}


@app.get("/api/campaigns")
def list_campaigns(db=Depends(get_db)):
    out = []
    for c in db.query(Campaign).order_by(Campaign.id.desc()):
        arts = c.articles
        pub = sum(a.status == "Published" for a in arts)
        out.append({"id": c.id, "name": c.name, "status": c.status,
                    "total": len(arts), "published": pub,
                    "created_at": c.created_at.isoformat(),
                    "running": c.id in RUNNING})
    return out


@app.delete("/api/campaigns/{cid}")
def delete_campaign(cid: int, db=Depends(get_db)):
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    if any(a.status == "Published" for a in c.articles):
        raise HTTPException(409, "Campaign has published articles.")
    db.delete(c)
    db.commit()
    return {"ok": True}


@app.post("/api/campaigns/{cid}/execute")
def execute(cid: int, bg: BackgroundTasks, db=Depends(get_db)):
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    if not os.getenv("GROQ_API_KEY"):
        raise HTTPException(400, "GROQ_API_KEY is missing in .env")
    todo = db.query(Article).filter(
        Article.campaign_id == cid,
        Article.status.in_(["Pending", "Failed"])).count()
    if not todo:
        raise HTTPException(400, "Nothing to run: no Pending or Failed articles")
    with RUNNING_LOCK:
        if cid in RUNNING:
            raise HTTPException(409, "Campaign is already running")
        RUNNING.add(cid)
    c.status = "Active"
    db.commit()

    def job():
        try: run_campaign(cid)
        finally: RUNNING.discard(cid)
    bg.add_task(job)
    return {"started": todo}


@app.post("/api/campaigns/{cid}/toggle")
def toggle(cid: int, db=Depends(get_db)):
    c = db.get(Campaign, cid)
    if not c or c.status == "Completed":
        raise HTTPException(400, "Cannot pause/resume this campaign")
    c.status = "Active" if c.status == "Paused" else "Paused"
    db.commit()
    return {"status": c.status}


@app.get("/api/campaigns/{cid}/status")
def campaign_status(cid: int, db=Depends(get_db)):
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    arts = db.query(Article).filter(
        Article.campaign_id == cid).order_by(Article.article_number).all()
    counts: Dict[str, int] = {}
    for a in arts:
        counts[a.status] = counts.get(a.status, 0) + 1
    return {"id": c.id, "name": c.name, "status": c.status,
            "running": cid in RUNNING, "counts": counts,
            "articles": [art_dict(a) for a in arts]}


@app.get("/api/articles")
def list_articles(campaign_id: Optional[int] = None,
                  status: Optional[str] = None, db=Depends(get_db)):
    q = db.query(Article)
    if campaign_id: q = q.filter(Article.campaign_id == campaign_id)
    if status: q = q.filter(Article.status == status)
    return [art_dict(a) for a in q.order_by(Article.id.desc()).limit(500)]


@app.get("/api/articles/{aid}")
def get_article(aid: int, db=Depends(get_db)):
    a = db.get(Article, aid)
    if not a:
        raise HTTPException(404, "Article not found")
    return art_dict(a, full=True)


@app.delete("/api/articles/{aid}")
def delete_article(aid: int, db=Depends(get_db)):
    a = db.get(Article, aid)
    if not a:
        raise HTTPException(404, "Article not found")
    if a.status == "Published":
        raise HTTPException(409, "Published articles cannot be deleted")
    db.delete(a)
    db.commit()
    return {"ok": True}


@app.post("/api/articles/{aid}/approve")
def approve(aid: int, body: ApproveIn, db=Depends(get_db)):
    a = db.get(Article, aid)
    if not a:
        raise HTTPException(404, "Article not found")
    if a.status not in ("Draft Created", "Waiting for Approval"):
        raise HTTPException(409, f"Article is '{a.status}', not awaiting approval")
    a.live_url, a.status = body.live_url, "Published"
    # ✅ FIX #4: record publish time for accurate daily-limit tracking
    a.published_at = datetime.utcnow()
    a.website.published_count += 1
    c = a.campaign
    if all(x.status == "Published" for x in c.articles):
        c.status = "Completed"
    db.commit()
    return art_dict(a)


@app.post("/api/articles/{aid}/reject")
def reject(aid: int, bg: BackgroundTasks, regenerate: bool = True,
           db=Depends(get_db)):
    a = db.get(Article, aid)
    if not a:
        raise HTTPException(404, "Article not found")
    if a.status not in ("Draft Created", "Waiting for Approval", "Failed", "Rejected"):
        raise HTTPException(409, f"Cannot reject article in status '{a.status}'")

    # Delete old WP draft before regenerating
    if a.draft_url:
        site = db.get(Website, a.website_id)
        if site:
            try:
                delete_wp_post(site, a.draft_url)
            except Exception as e:
                print(f"[reject] WP delete failed: {e}")

    a.title = a.meta_description = a.content = a.draft_url = a.live_url = None
    a.image_alt = a.warnings = a.error = None
    a.seo_score = None
    a.status = "Pending" if regenerate else "Rejected"
    db.commit()
    if regenerate:
        bg.add_task(_run_one_locked, aid)
    return {"ok": True, "regenerating": regenerate}


@app.post("/api/articles/{aid}/retry")
def retry(aid: int, bg: BackgroundTasks, db=Depends(get_db)):
    """✅ FIX #2: Rejected articles bhi retry ho sakte hain ab."""
    a = db.get(Article, aid)
    if not a or a.status not in ("Failed", "Rejected"):
        raise HTTPException(400, "Only Failed or Rejected articles can be retried")
    a.status = "Pending"
    db.commit()
    bg.add_task(_run_one_locked, aid)
    return {"ok": True}


def _run_one_locked(aid: int):
    lock = _article_lock(aid)
    if not lock.acquire(blocking=False):
        return
    try:
        process_article(aid)
    finally:
        lock.release()


@app.get("/api/dashboard")
def dashboard(db=Depends(get_db)):
    pend = db.query(Article).filter(
        Article.status.in_(["Draft Created", "Waiting for Approval"])).count()
    return {"active_campaigns": db.query(Campaign).filter(
                Campaign.status == "Active").count(),
            "total_sites": db.query(Website).count(),
            "pending_approval": pend,
            "published": db.query(Article).filter(
                Article.status == "Published").count()}


FRONTEND = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.isdir(FRONTEND):
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="ui")
