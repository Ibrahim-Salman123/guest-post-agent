"""Draft creation: WordPress REST API first, Playwright fallback for custom CMSs.
Posts are ALWAYS created as drafts - this module never publishes."""
import json
import re
import requests
from database import Website, decrypt, normalize_url


def _wp_api(site: Website, title, html, meta, keyword, slug, image_alt="") -> dict:
    base = site.url.rstrip("/")
    payload = {
        "title": title,
        "content": html,
        "status": "draft",
        "slug": slug,
        "excerpt": meta,   # ✅ Meta description save via excerpt (always works)
        # Yoast meta — only saves if site registers these for REST (see README)
        "meta": {
            "_yoast_wpseo_metadesc": meta,
            "_yoast_wpseo_focuskw": keyword,
            "_yoast_wpseo_title": title,
        },
    }
    r = requests.post(f"{base}/wp-json/wp/v2/posts", json=payload,
                      auth=(site.username, decrypt(site.password_hash)), timeout=60)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"WP API {r.status_code}: {r.text[:200]}")
    pid = r.json()["id"]
    return {"draft_url": f"{base}/wp-admin/post.php?post={pid}&action=edit",
            "method": "wp-api", "image_alt": image_alt, "post_id": pid}


def delete_wp_post(site: Website, draft_url: str) -> bool:
    """✅ Claude #4: Delete old WP draft on reject/regenerate."""
    if not draft_url:
        return False
    m = re.search(r"[?&]post=(\d+)", draft_url)
    if not m:
        return False
    pid = m.group(1)
    base = site.url.rstrip("/")
    try:
        r = requests.delete(
            f"{base}/wp-json/wp/v2/posts/{pid}?force=true",
            auth=(site.username, decrypt(site.password_hash)),
            timeout=30)
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def _playwright(site: Website, title, html, image_alt="") -> dict:
    """✅ Claude #7: Better HTML handling — copy as HTML, not plain text."""
    from playwright.sync_api import sync_playwright
    s = json.loads(site.selectors or "{}")
    need = ["login_url", "user_sel", "pass_sel", "submit_sel", "new_post_url",
            "title_sel", "body_sel", "save_draft_sel"]
    if any(k not in s for k in need):
        raise RuntimeError("Playwright fallback needs selectors JSON: " + ", ".join(need))
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            page = b.new_page()
            page.goto(s["login_url"], timeout=60000)
            page.fill(s["user_sel"], site.username)
            page.fill(s["pass_sel"], decrypt(site.password_hash))
            page.click(s["submit_sel"])
            page.wait_for_load_state("networkidle")
            page.goto(s["new_post_url"], timeout=60000)
            page.fill(s["title_sel"], title)
            # ✅ HTML paste via clipboard-like evaluation (better than fill for contenteditable)
            page.click(s["body_sel"])
            page.evaluate(
                """([sel, html]) => {
                    const el = document.querySelector(sel);
                    if (!el) throw new Error('body selector not found');
                    el.focus();
                    if (el.isContentEditable) {
                        document.execCommand('insertHTML', false, html);
                    } else {
                        el.value = html;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                }""",
                [s["body_sel"], html])
            page.click(s["save_draft_sel"])
            page.wait_for_load_state("networkidle")
            return {"draft_url": page.url, "method": "playwright", "image_alt": image_alt}
        finally:
            b.close()


def create_draft(site: Website, title, html, meta, keyword, slug,
                 image_alt: str = "") -> dict:
    if site.cms_type != "custom":
        try:
            return _wp_api(site, title, html, meta, keyword, slug, image_alt)
        except Exception as e:
            if not (site.selectors or "").strip():
                raise RuntimeError(f"Draft creation failed: {e}")
    return _playwright(site, title, html, image_alt)


def _check_url(u: str) -> bool:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; GuestPostBot/1.0)"}
    try:
        r = requests.head(u, timeout=6, allow_redirects=True, headers=headers)
        if r.status_code in (403, 405, 501) or r.status_code >= 500:
            r = requests.get(u, timeout=6, allow_redirects=True, headers=headers, stream=True)
    except Exception:
        return False
    return 200 <= r.status_code < 400


def validate_external_links(html: str, limit: int = 5) -> list:
    urls = re.findall(r'<a\s[^>]*href="(https?://[^"]+)"', html, re.I)
    broken = []
    for u in urls[:limit]:
        if not _check_url(u):
            broken.append(u)
    return broken



def publish_wp_draft(site: Website, article) -> dict:
    """Change WP post status from 'draft' to 'publish'."""
    if not article.draft_url:
        raise RuntimeError("No draft URL - publish not possible")
    m = re.search(r"[?&]post=(\d+)", article.draft_url)
    if not m:
        raise RuntimeError("Cannot find post ID in draft URL")
    pid = m.group(1)
    base = site.url.rstrip("/")
    r = requests.post(
        f"{base}/wp-json/wp/v2/posts/{pid}",
        json={"status": "publish"},
        auth=(site.username, decrypt(site.password_hash)),
        timeout=60,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"WP publish {r.status_code}: {r.text[:200]}")
    published = r.json()
    live_url = published.get("link") or f"{base}/?p={pid}"
    return {"live_url": live_url, "post_id": pid}
