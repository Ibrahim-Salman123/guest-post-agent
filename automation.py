"""Publisher: Playwright browser automation for HTML/CSS/JS (custom) sites,
WordPress REST API only when no browser selectors are configured.
Images come from the Unsplash API."""
import json
import os
import re
import requests
from database import Website, decrypt

UA = {"User-Agent": "Mozilla/5.0 (GuestPostAgent)"}


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
                    try:  # Unsplash guideline: register the download
                        dl = photo.get("links", {}).get("download_location")
                        if dl:
                            requests.get(dl, headers={"Authorization": f"Client-ID {key}"}, timeout=8)
                    except Exception:
                        pass
                    return photo["urls"]["regular"]
            else:
                print(f"[image] Unsplash API status {r.status_code}: {r.text[:150]}")
        else:
            print("[image] UNSPLASH_ACCESS_KEY missing in .env")
    except Exception as e:
        print(f"[image] Unsplash failed: {e}")
    return "https://picsum.photos/1200/630"


def _inject_image(html: str, image_url: str, alt: str) -> str:
    """Prepend image at top of article."""
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


def _use_browser(site: Website, s: dict) -> bool:
    """Browser is used whenever selectors are configured (HTML/CSS/JS sites)."""
    return bool(s.get("login_url"))


def _need_selectors_msg() -> str:
    return ("Is website ke liye browser selectors set nahi hain. Websites page me "
            "'Browser selectors (JSON)' bharo (login_url, user_sel, pass_sel, submit_sel, "
            "new_post_url, title_sel, body_sel, save_draft_sel, publish_sel) "
            "aur Posting method = Custom CMS rakho.")


def _browser_login(page, site: Website, s: dict):
    page.goto(s["login_url"], timeout=60000)
    page.fill(s["user_sel"], site.username)
    page.fill(s["pass_sel"], decrypt(site.password_hash))
    page.click(s["submit_sel"])
    page.wait_for_load_state("networkidle", timeout=30000)


def _fill_new_post(page, s: dict, title: str, html_with_img: str):
    page.goto(s["new_post_url"], timeout=60000)
    page.wait_for_load_state("networkidle", timeout=30000)
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
def _base(site: Website) -> str:
    return site.url.rstrip("/")


def _auth(site: Website):
    return (site.username, decrypt(site.password_hash))


def _wp_err(r) -> str:
    try:
        d = r.json()
        return f"{r.status_code}: {d.get('message') or d}"
    except Exception:
        return (f"{r.status_code}: yeh site WordPress REST API allow nahi karti "
                f"(shayad WordPress hi nahi hai). Posting method = Custom CMS karo "
                f"aur browser selectors bharo.")


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
            print(f"[image] WP media upload failed {_wp_err(r)}")
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
        raise RuntimeError(f"WordPress API error {_wp_err(r)}")
    p = r.json()
    return {"id": p["id"], "link": p.get("link"), "image_url": img_src}


def _wp_post_id(draft_url: str):
    m = re.search(r"[?&]post=(\d+)", draft_url or "")
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ draft
def create_draft(site: Website, title, html, meta, keyword, slug, image_alt="") -> dict:
    """Create draft. Selectors configured -> Playwright. Otherwise WordPress API."""
    s = _selectors(site)

    if _use_browser(site, s):
        return _create_draft_browser(site, s, title, html, keyword, image_alt)

    if (site.cms_type or "wordpress") == "wordpress":
        p = _wp_create_post(site, title, html, meta, keyword, slug, image_alt, "draft")
        edit_url = f"{_base(site)}/wp-admin/post.php?post={p['id']}&action=edit"
        return {"draft_url": edit_url, "live_url": None,
                "method": "wp-api", "image_url": p["image_url"]}

    raise RuntimeError(_need_selectors_msg())


def _create_draft_browser(site, s, title, html, keyword, image_alt, publish=False) -> dict:
    from playwright.sync_api import sync_playwright
    required = ["login_url", "user_sel", "pass_sel", "submit_sel",
                "new_post_url", "title_sel", "body_sel", "save_draft_sel"]
    missing = [k for k in required if not s.get(k)]
    if missing:
        raise RuntimeError(f"Selectors missing: {', '.join(missing)}")
    if publish and not s.get("publish_sel"):
        raise RuntimeError("Need 'publish_sel' in selectors JSON")

    image_url = _fetch_related_image(keyword or title)
    html_with_img = _inject_image(html, image_url, image_alt or keyword or title)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            _browser_login(page, site, s)
            _fill_new_post(page,
