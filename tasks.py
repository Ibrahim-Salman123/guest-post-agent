"""CrewAI tasks, Yoast-style evaluator, optimization loop and the article pipeline."""
import json, os, re, time, threading, ipaddress
from urllib.parse import urlparse
from crewai import Crew, Process, Task
from agents import seo_agent, writer_agent
from automation import create_draft, validate_external_links
from database import (Article, Campaign, SessionLocal, Website, normalize_url)

MAX_ROUNDS = int(os.getenv("MAX_SEO_ROUNDS", "4"))

LONG_SENTENCE_WORDS = 20
PASSIVE_HINTS = re.compile(r"\b(is|are|was|were|be|been|being)\s+\w+ed\b", re.I)
TRANSITION_WORDS = {
    "however", "therefore", "moreover", "furthermore", "additionally",
    "consequently", "meanwhile", "nevertheless", "similarly", "likewise",
    "for example", "for instance", "in addition", "on the other hand",
    "as a result", "in conclusion", "finally", "firstly", "secondly",
}

ALLOWED_TAGS = {"p", "h2", "h3", "ul", "ol", "li", "a", "strong", "em", "br"}


def sanitize_html(html: str) -> str:
    """Remove disallowed tags & scripts. Keep only allowed HTML."""
    if not html:
        return html
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.I)
    html = re.sub(r'\son\w+\s*=\s*"[^"]*"', "", html, flags=re.I)
    html = re.sub(r"\son\w+\s*=\s*'[^']*'", "", html, flags=re.I)
    def replace_tag(m):
        tag = m.group(1).lower().lstrip("/")
        return m.group(0) if tag in ALLOWED_TAGS else ""
    return re.sub(r"</?([a-zA-Z][a-zA-Z0-9]*)[^>]*>", replace_tag, html)


_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


def _get_lock(aid: int) -> threading.Lock:
    with _LOCKS_GUARD:
        if aid not in _LOCKS:
            _LOCKS[aid] = threading.Lock()
        return _LOCKS[aid]


def _run(agent, desc, expected) -> str:
    t = Task(description=desc, expected_output=expected, agent=agent)
    return Crew(agents=[agent], tasks=[t], process=Process.sequential,
                verbose=False).kickoff().raw


def _json(text: str) -> dict:
    if not text:
        raise ValueError("Empty LLM response")
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in response: {text[:200]}")
    return json.loads(t[start:end + 1])


def _ask_json(agent, desc, expected) -> dict:
    last_err = None
    for attempt in range(4):
        try:
            raw = _run(agent, desc, expected)
            if not raw or not raw.strip():
                last_err = "Empty LLM response"
                time.sleep(6)
                continue
            d = _json(raw)
            if all(k in d for k in ("title", "meta_description", "content_html")):
                return d
            last_err = f"Missing keys: {list(d.keys())}"
        except Exception as e:
            last_err = str(e)
            if "rate" in str(e).lower() or "429" in str(e):
                wait = 15 * (attempt + 1)
                print(f"Rate limit hit. Waiting {wait}s before retry...")
                time.sleep(wait)
            elif "empty" in str(e).lower() or "none" in str(e).lower():
                time.sleep(6)
            else:
                time.sleep(3)
    raise RuntimeError(f"LLM did not return valid article JSON after 4 attempts: {last_err}")


def slugify(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
def _text(h): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()


def _flesch_reading_ease(text: str) -> float:
    sents = re.split(r"[.!?]+\s", text)
    words = re.findall(r"[A-Za-z']+", text)
    if not words or not sents:
        return 0.0
    syllables = 0
    for w in words:
        w = w.lower()
        grp = re.findall(r"[aeiouy]+", w)
        n = len(grp)
        if w.endswith("e") and n > 1:
            n -= 1
        syllables += max(1, n)
    asl = len(words) / len(sents)
    asw = syllables / len(words)
    return round(206.835 - 1.015 * asl - 84.6 * asw, 1)


def yoast_evaluate(title, meta, html, keyword, target_url,
                   min_words: int = 600, max_words: int = 2000) -> dict:
    kw = keyword.lower()
    text = _text(html)
    plain = text
    words = len(text.split())
    paras = [_text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", html, re.S | re.I)]
    h2s = [_text(h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.S | re.I)]
    h3s = [_text(h) for h in re.findall(r"<h3[^>]*>(.*?)</h3>", html, re.S | re.I)]
    sents = [s for s in re.split(r"[.!?]+\s", text) if s.strip()]
    long_ratio = sum(len(s.split()) > LONG_SENTENCE_WORDS for s in sents) / max(len(sents), 1)
    density = text.lower().count(kw) * len(kw.split()) / max(words, 1) * 100
    links = re.findall(r'<a\s[^>]*href="([^"]+)"', html, re.I)
    norm_target = normalize_url(target_url)
    has_target = any(normalize_url(l) == norm_target for l in links)

    first10 = paras[:10]
    kw_in_first10 = sum(kw in p.lower() for p in first10)
    kw_distribution_ok = kw_in_first10 >= min(3, len(first10)) if first10 else False

    passive_hits = len(PASSIVE_HINTS.findall(plain))
    passive_ratio = passive_hits / max(len(sents), 1)

    lower = plain.lower()
    transition_count = sum(1 for t in TRANSITION_WORDS if t in lower)
    transitions_ok = transition_count >= 3

    flesch = _flesch_reading_ease(plain)

    min_ok = int(min_words * 0.85)
    max_ok = int(max_words * 1.15)

    target_host = urlparse(norm_target).netloc
    external_ok = any(
        l.startswith("http") and urlparse(normalize_url(l)).netloc != target_host
        for l in links
    )

    checks = [
        ("Keyword in SEO title", kw in title.lower(), True),
        ("Keyword in meta description", kw in meta.lower(), True),
        ("Keyword in first paragraph", bool(paras) and kw in paras[0].lower(), True),
        ("Keyword in at least one H2", any(kw in h.lower() for h in h2s), True),
        ("Links to target URL", has_target, True),
        (f"Word count {min_ok}-{max_ok}", min_ok <= words <= max_ok, True),
        ("No H1 inside body", "<h1" not in html.lower(), True),
        ("SEO title 30-60 chars", 30 <= len(title) <= 60, False),
        ("Meta description 120-156 chars", 120 <= len(meta) <= 156, False),
        ("Keyword density 0.5-2.5%", 0.5 <= density <= 2.5, False),
        ("3+ H2 subheadings", len(h2s) >= 3, False),
        ("Paragraphs under 150 words",
         all(len(p.split()) <= 150 for p in paras) if paras else False, False),
        (f"Max 25% sentences over {LONG_SENTENCE_WORDS} words", long_ratio <= 0.25, False),
        ("Has external authority link", external_ok, False),
        ("Keyphrase distributed in content", kw_distribution_ok, False),
        ("Uses transition words (3+)", transitions_ok, False),
        ("Passive voice under control (<25%)", passive_ratio <= 0.25, False),
        ("Flesch Reading Ease >= 50", flesch >= 50, False),
        ("At least one H3 or list", bool(h3s) or "<ul" in html.lower() or "<ol" in html.lower(), False),
    ]
    ok = sum(c[1] for c in checks)
    score = round(ok / len(checks) * 100)
    failed = [n for n, p, _ in checks if not p]
    critical_failures = [n for n, p, crit in checks if crit and not p]
    in_range = min_ok <= words <= max_ok
    return {
        "score": score,
        "issues": [f"FAILED: {n}" for n in failed],
        "words": words, "flesch": flesch,
        "critical_failures": critical_failures,
        "passed": not critical_failures and score >= 75,
    }


def _similar(a: str, b: str) -> float:
    sa, sb = set(a.lower().split()), set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _body_similar(a_html: str, b_html: str, n: int = 5) -> float:
    def shingles(h):
        w = re.sub(r"<[^>]+>", " ", (h or "").lower())
        w = re.findall(r"[a-z0-9']+", w)
        if len(w) < n:
            return {tuple(w)} if w else set()
        return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}
    sa, sb = shingles(a_html), shingles(b_html)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def generate_article(site: Website, keyword, url, number, prior_titles,
                     prior_bodies=None) -> dict:
    min_w = site.min_words or 900
    max_w = site.max_words or 1200
    reqs = (site.requirements or "none").strip()
    notes = (site.notes or "none").strip()

    internal_links_block = ""
    try:
        il = json.loads(site.internal_links or "[]")
        if isinstance(il, list) and il:
            lines = [f'  - <a href="{x["url"]}">{x["anchor"]}</a>' for x in il[:5]]
            internal_links_block = (
                "\n- Include 1-2 INTERNAL links from this list (natural placement):\n"
                + "\n".join(lines)
            )
    except Exception:
        pass

    desc = f"""Write a unique guest post for the website '{site.name}' (niche: {site.niche or 'general'}).
Site content requirements: {reqs}. Notes: {notes}.
Focus keyword: "{keyword}". Target URL: {url}. This is article #{number} for this campaign.
Do NOT reuse these existing titles/angles: {prior_titles[-15:] or 'none'}.
Rules:
- Length strictly between {min_w} and {max_w} words.
- HTML only using <p>,<h2>,<h3>,<ul>,<li>,<a>; no <h1>.
- At least 4 <h2> and one <h2> must contain the exact keyword "{keyword}".
- First paragraph must contain the exact keyword "{keyword}".
- Link the target URL ONCE with <a href="{url}"> using anchor text that contains the keyword.
- One external authority link (e.g. wikipedia.org or a reputable industry source).{internal_links_block}
- Keyword density about 1% (not stuffing).
- Paragraphs under 100 words; mostly short sentences.
- Use at least 3 transition words (however, therefore, moreover, etc.).
- Keep passive voice to a minimum.
- SEO title 40-60 chars starting with the keyword.
- Meta description 130-155 chars with the keyword.
- Also produce ONE descriptive image alt text (return in "image_alt", do not embed <img>).
Return ONLY JSON: {{"title":"","meta_description":"","content_html":"","image_alt":""}}"""
    art = _ask_json(writer_agent(), desc,
                    "A JSON object with title, meta_description, content_html, image_alt")
    if prior_bodies:
        for pb in prior_bodies[-20:]:
            if _body_similar(art.get("content_html", ""), pb) > 0.35:
                art = _ask_json(writer_agent(), desc +
                                "\n\nIMPORTANT: Previous output was too similar to an "
                                "existing article. Use a completely different angle, "
                                "examples and wording.",
                                "A JSON object with title, meta_description, content_html, image_alt")
                break
    return art


def optimize(art: dict, keyword, url, min_words=600, max_words=2000):
    res = yoast_evaluate(art["title"], art["meta_description"], art["content_html"],
                         keyword, url, min_words=min_words, max_words=max_words)
    for _ in range(MAX_ROUNDS):
        if res["passed"]:
            break
        desc = f"""Fix ONLY these Yoast SEO problems in the article, keeping it natural and unique:
{chr(10).join(res['issues'])}
Focus keyword: "{keyword}"; target URL must stay linked once: {url}.
Word count must stay between {min_words} and {max_words}.
Current article JSON: {json.dumps(art)}
Return ONLY the corrected JSON with keys title, meta_description, content_html, image_alt."""
        try:
            art = {**art, **_ask_json(seo_agent(), desc, "Corrected article JSON")}
        except Exception:
            break
        res = yoast_evaluate(art["title"], art["meta_description"], art["content_html"],
                             keyword, url, min_words=min_words, max_words=max_words)
    return art, res


def _status(db, a: Article, s: str):
    a.status = s
    db.commit()


def process_article(aid: int):
    lock = _get_lock(aid)
    if not lock.acquire(blocking=False):
        return
    db = SessionLocal()
    try:
        a = db.get(Article, aid)
        if not a or a.status not in ("Pending", "Failed"):
            return
        site = db.get(Website, a.website_id)
        a.error, a.warnings = None, None
        _status(db, a, "Generating")

        prior_titles = [t for (t,) in db.query(Article.title).filter(
            Article.website_id == site.id, Article.title.isnot(None),
            Article.id != a.id).all()]

        prior_bodies = [c for (c,) in db.query(Article.content).filter(
            Article.website_id == site.id, Article.content.isnot(None),
            Article.id != a.id).all()]

        art = generate_article(site, a.keyword, a.target_url, a.article_number,
                               prior_titles, prior_bodies)

        if any(pt and _similar(pt, art["title"]) > 0.75 for pt in prior_titles):
            art = generate_article(site, a.keyword, a.target_url, a.article_number,
                                   prior_titles + [art["title"]], prior_bodies)

        _status(db, a, "Optimizing")
        art, res = optimize(art, a.keyword, a.target_url,
                            min_words=site.min_words or 400,
                            max_words=site.max_words or 900)

        if res["critical_failures"]:
            raise RuntimeError("Critical SEO checks failed: "
                               + "; ".join(res["critical_failures"]))
        if res["score"] < 65:
            raise RuntimeError("SEO score stayed below 65: "
                               + "; ".join(res["issues"][:3]))

        clean_html = sanitize_html(art["content_html"])

        a.title = art["title"]
        a.meta_description = art["meta_description"]
        a.content = clean_html
        a.image_alt = art.get("image_alt") or a.keyword
        a.seo_score = res["score"]

        warnings = []
        if res["score"] < 85:
            warnings.append("SEO score below 85: " + "; ".join(res["issues"][:2]))
        broken = validate_external_links(clean_html)
        if broken:
            warnings.append("Unreachable external link(s): " + ", ".join(broken[:3]))
        a.warnings = "\n".join(warnings) if warnings else None
        db.commit()

        # ✅ FIX #3: Draft upload fail ho to status = Failed (Waiting for Approval NAHI)
        # Article content is still saved, user Retry kar sakta hai ya View kar sakta hai.
        draft_failed = False
        try:
            d = create_draft(site, a.title, a.content, a.meta_description,
                             a.keyword, slugify(a.keyword), a.image_alt)
            a.draft_url = d["draft_url"]
        except Exception as draft_err:
            a.draft_url = None
            a.error = f"Draft upload FAILED: {str(draft_err)[:250]}"
            draft_failed = True
            db.commit()

        if draft_failed:
            # Draft upload fail — lekin article ready hai
            # Status "Waiting for Approval" rakho + warning dikhao
            _status(db, a, "Waiting for Approval")
        else:
            _status(db, a, "Draft Created")
            _status(db, a, "Waiting for Approval")
    except Exception as e:
        db.rollback()
        a = db.get(Article, aid)
        if a:
            a.status, a.error = "Failed", str(e)[:500]
            db.commit()
    finally:
        db.close()
        lock.release()


def run_campaign(cid: int):
    db = SessionLocal()
    try:
        ids = [i for (i,) in db.query(Article.id).filter(
            Article.campaign_id == cid,
            Article.status.in_(["Pending", "Failed"])
        ).order_by(Article.id).all()]
    finally:
        db.close()
    for i in ids:
        db = SessionLocal()
        camp = db.get(Campaign, cid)
        paused = camp and camp.status == "Paused"
        db.close()
        if paused:
            break
        process_article(i)
