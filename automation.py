"""Publisher: Playwright browser automation for HTML/CSS/JS (custom) sites.
WordPress REST API is used ONLY if no browser selectors are set AND the site
really is WordPress. Images come from the Unsplash API."""
import json
import os
import re
import requests
from database import Website, decrypt

UA = {"User-Agent": "Mozilla/5.0 (GuestPostAgent)"}
BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]


# ------------------------------------------------------------------ images
def _fetch_related_image(keyword: str) -> str:
    """Fetch related image URL via Unsplash API. Fallback to Picsum."""
    key = os.getenv("UNSPLASH_ACCESS_KEY") or os.getenv("UNSPLASH_API_KEY")
    try:
        q = re.sub(r"[^a-zA-Z0-9\s]", "", keyword or "blog").strip() or "blog writing"
        if key:
            r = requests.get(
                "https://api.unsplash.com/search/photos",
                params={"query": q, "per_page": 1, "orientation": "landscape"},
                headers={"Authorization": f"Client-ID {key}"}, timeout=15)
            if r.status_code == 200:
                results = r.json().get("results") or []
                if results:
                    photo = results[0]
                    try:
                        dl = photo.get("links", {}).get("download_location")
                        if dl:
                            requests.get(dl, headers={"Authorization": f"Client-ID {key}"}, timeout=8)
                    except Exception:
                        pass
                    return photo["urls"]["regular"]
            else:
                print(f"[image] Unsplash API status {r.status_code}: {r.text[:150]}")
        else:
            print("[image] UNSPLASH_ACCESS_KEY missing in env")
    except Exception as e:
        print(f"[image] Unsplash failed: {e}")
    return "https://picsum.photos/1200/630"


def _inject_image(html: str, image_url: str, alt: str) -> str:
    if not image_url:
        return html
    return f'<p><img src="{image_url}" alt="{alt}" style="max-width:100%;height:auto;" /></p>\n' + html


# ----------------------------------------------------------------- helpers
def _selectors(site: Website) -> dict:
    try:
        d = json.loads(site.selectors or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _use_browser(s: dict) -> bool:
    return bool(s.get("login_url"))


def _base(site: Website) -> str:
    return site.url.rstrip("/")


def _auth(site: Website):
    return (site.username, decrypt(site.password_hash))


def _is_wordpress(site: Website) -> bool:
    """True only if the site really exposes a WordPress REST API."""
    try:
        r = requests.get(f"{_base(site)}/wp-json/", timeout=15, headers=UA)
        return r.status_code == 200 and "namespaces" in r.json()
    except Exception:
        return False


def _no_method_msg() -> str:
    return ("This site is not WordPress and no browser selectors are saved. "
            "Open Websites > Edit and fill 'Browser selectors (JSON)' "
            "(login_url, user_sel, pass_sel, submit_sel, new_post_url, title_sel, "
            "body_sel, save_draft_sel, publish_sel).")


def _clean_err(r) -> str:
    try:
        d = r.json()
        return f"{r.status_code}: {d.get('message') or d}"[:250]
    except Exception:
        return f"{r.status_code}: server blocked the request"


def _settle(page, timeout=30000):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass


def _launch(p):
    return p.chromium.launch(headless=True, args=BROWSER_ARGS)


def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        raise RuntimeError("Playwright is not installed on the server "
                           "(pip install playwright && playwright install chromium)")


def _browser_login(page, site: Website, s: dict):
    page.goto(s["login_url"], timeout=60000)
    page.fill(s["user_sel"], site.username)
    page.fill(s["pass_sel"], decrypt(site.password_hash))
    page.click(s["submit_sel"])
    _settle(page)


def _fill_new_post(page, s: dict, title: str, html_with_img: str):
    page.goto(s["new_post_url"], timeout=60000)
    _settle(page)
    page.fill(s["title_sel"], title)
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
        [s["body_sel"], html_with_img])


# ------------------------------------------------------------ WordPress API
def _wp_upload_image(site: Website, image_url: str, alt: str, name: str):
    try:
        img = requests.get(image_url, timeout=30, allow_redirects=True, headers=UA)
        img.raise_for_status()
        ctype = img.headers.get("Content-Type", "image/jpeg").split(";")[0]
        if not ctype.startswith("image/"):
            ctype = "image/jpeg"
        ext = "png" if "png" in ctype else "webp" if "webp" in ctype else "jpg"
        fname = f"{re.sub(r'[^a-z0-9]+', '-', (name or 'image').lower()).strip('-') or 'image'}.{ext}"
        r = requests.post(
            f"{_base(site)}/wp-json/wp/v2/media", auth=_auth(site), timeout=60,
            headers={"Content-Disposition": f'attachment; filename="{fname}"',
                     "Content-Type": ctype, **UA},
            data=img.content)
        if r.status_code not in (200, 201):
            print(f"[image] WP media upload failed {_clean_err(r)}")
            return None, image_url
        m = r.json()
        try:
            requests.post(f"{_base(site)}/wp-json/wp/v2/media/{m['id']}", auth=_auth(site),
                          json={"alt_text": alt, "title": alt}, timeout=20, headers=UA)
        except Exception:
            pass
        return m["id"], m.get("source_url") or image_url
    except Exception as e:
        print(f"[image] WP upload error: {e}")
        return None, image_url


def _wp_create_post(site: Website, title, html, meta, keyword, slug,
                    image_alt, status: str) -> dict:
    img = _fetch_related_image(keyword or title)
    media_id, img_src = _wp_upload_image(site, img, image_alt or keyword or title, slug or keyword)
    body_html = _inject_image(html, img_src, image_alt or keyword or title)
    payload = {"title": title, "content": body_html, "status": status,
               "slug": slug or "", "excerpt": meta or ""}
    if media_id:
        payload["featured_media"] = media_id
    r = requests.post(f"{_base(site)}/wp-json/wp/v2/posts", auth=_auth(site),
                      json=payload, timeout=60, headers=UA)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"WordPress API error {_clean_err(r)}")
    p = r.json()
    return {"id": p["id"], "link": p.get("link"), "image_url": img_src}


def _wp_post_id(draft_url: str):
    m = re.search(r"[?&]post=(\d+)", draft_url or "")
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ draft
def create_draft(site: Website, title, html, meta, keyword, slug, image_alt="") -> dict:
    s = _selectors(site)
    if _use_browser(s):
        return _browser_run(site, s, title, html, keyword, image_alt, publish=False)
    if _is_wordpress(site):
        p = _wp_create_post(site, title, html, meta, keyword, slug, image_alt, "draft")
        return {"draft_url": f"{_base(site)}/wp-admin/post.php?post={p['id']}&action=edit",
                "live_url": None, "method": "wp-api", "image_url": p["image_url"]}
    raise RuntimeError(_no_method_msg())


def _browser_run(site, s, title, html, keyword, image_alt, publish=False) -> dict:
    required = ["login_url", "user_sel", "pass_sel", "submit_sel",
                "new_post_url", "title_sel", "body_sel", "save_draft_sel"]
    missing = [k for k in required if not s.get(k)]
    if missing:
        raise RuntimeError(f"Selectors missing: {', '.join(missing)}")

    image_url = _fetch_related_image(keyword or title)
    html_with_img = _inject_image(html, image_url, image_alt or keyword or title)

    sync_playwright = _sync_playwright()
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            _browser_login(page, site, s)
            _fill_new_post(page, s, title, html_with_img)
            page.click(s["save_draft_sel"])
            _settle(page)
            draft_url = page.url
            live_url = None
            if publish:
                if s.get("publish_sel"):
                    page.click(s["publish_sel"])
                    _settle(page)
                live_url = page.url  # no publish_sel -> save button already publishes
            return {"draft_url": draft_url, "live_url": live_url,
                    "method": "playwright", "image_url": image_url}
        finally:
            browser.close()


# ----------------------------------------------------------------- delete
def delete_wp_post(site: Website, draft_url: str) -> bool:
    """Delete WP draft on reject. No-op for browser/custom sites."""
    if _use_browser(_selectors(site)):
        return False
    pid = _wp_post_id(draft_url)
    if not pid:
        return False
    try:
        r = requests.delete(f"{_base(site)}/wp-json/wp/v2/posts/{pid}",
                            auth=_auth(site), params={"force": "true"},
                            timeout=30, headers=UA)
        return r.status_code in (200, 201)
    except Exception:
        return False


def validate_external_links(html: str, limit: int = 5) -> list:
    urls = re.findall(r'<a\s[^>]*href="(https?://[^"]+)"', html, re.I)
    broken = []
    for u in urls[:limit]:
        try:
            r = requests.head(u, timeout=6, allow_redirects=True, headers=UA)
            if not (200 <= r.status_code < 400):
                broken.append(u)
        except Exception:
            broken.append(u)
    return broken


# ---------------------------------------------------------------- publish
def publish_wp_draft(site: Website, article) -> dict:
    s = _selectors(site)

    if _use_browser(s):
        return _publish_browser(site, s, article)

    if _is_wordpress(site):
        pid = _wp_post_id(article.draft_url)
        if pid:
            r = requests.post(f"{_base(site)}/wp-json/wp/v2/posts/{pid}",
                              auth=_auth(site), json={"status": "publish"},
                              timeout=60, headers=UA)
            if r.status_code in (200, 201):
                return {"live_url": r.json().get("link"), "method": "wp-api"}
            if r.status_code != 404:
                raise RuntimeError(f"WordPress API error {_clean_err(r)}")
        p = _wp_create_post(site, article.title or article.keyword, article.content or "",
                            article.meta_description, article.keyword,
                            re.sub(r"[^a-z0-9]+", "-", (article.keyword or "").lower()).strip("-"),
                            article.image_alt or article.keyword, "publish")
        return {"live_url": p["link"], "method": "wp-api"}

    raise RuntimeError(_no_method_msg())


def _publish_browser(site, s, article) -> dict:
    # Draft was never created -> create and publish in one browser session
    if not article.draft_url:
        d = _browser_run(site, s, article.title or article.keyword, article.content or "",
                         article.keyword, article.image_alt or article.keyword, publish=True)
        return {"live_url": d["live_url"], "method": "playwright"}

    publish_sel = s.get("publish_sel")
    if not publish_sel:
        # Save button of this CMS already publishes
        return {"live_url": article.draft_url, "method": "playwright"}

    sync_playwright = _sync_playwright()
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            _browser_login(page, site, s)
            page.goto(article.draft_url, timeout=60000)
            _settle(page)
            page.click(publish_sel)
            _settle(page)
            return {"live_url": page.url, "method": "playwright"}
        finally:
            browser.close()
