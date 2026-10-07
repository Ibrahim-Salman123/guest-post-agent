"""Simple Publisher: Playwright browser automation for any HTML/CSS/JS site.
Logs in, creates post with image, publishes. No WordPress. No complexity."""
import json
import re
import requests
from database import Website, decrypt


def _fetch_related_image(keyword: str) -> str:
    """Fetch related image from Unsplash (free). Fallback to Picsum."""
    try:
        safe_kw = re.sub(r'[^a-zA-Z0-9\s]', '', keyword or 'blog').strip().replace(' ', ',')
        if not safe_kw:
            safe_kw = "blog,writing"
        img_url = f"https://source.unsplash.com/featured/?{safe_kw}"
        r = requests.head(img_url, timeout=10, allow_redirects=True)
        if r.status_code == 200:
            return img_url
    except Exception as e:
        print(f"[image] Unsplash failed: {e}")
    return "https://picsum.photos/1200/630"


def _inject_image(html: str, image_url: str, alt: str) -> str:
    """Prepend image at top of article."""
    if not image_url:
        return html
    return f'<p><img src="{image_url}" alt="{alt}" style="max-width:100%;height:auto;" /></p>\n' + html


def create_draft(site: Website, title, html, meta, keyword, slug, image_alt="") -> dict:
    """Create draft on custom site using Playwright."""
    from playwright.sync_api import sync_playwright
    s = json.loads(site.selectors or "{}")
    required = ["login_url", "user_sel", "pass_sel", "submit_sel",
                "new_post_url", "title_sel", "body_sel", "save_draft_sel"]
    missing = [k for k in required if not s.get(k)]
    if missing:
        raise RuntimeError(f"Selectors missing: {', '.join(missing)}")

    image_url = _fetch_related_image(keyword or title)
    html_with_img = _inject_image(html, image_url, image_alt or keyword or title)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            # Login
            page.goto(s["login_url"], timeout=60000)
            page.fill(s["user_sel"], site.username)
            page.fill(s["pass_sel"], decrypt(site.password_hash))
            page.click(s["submit_sel"])
            page.wait_for_load_state("networkidle", timeout=30000)
            # New post
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
            page.click(s["save_draft_sel"])
            page.wait_for_load_state("networkidle", timeout=30000)
            draft_url = page.url
            live_url = None
            if s.get("publish_sel"):
                page.click(s["publish_sel"])
                page.wait_for_load_state("networkidle", timeout=30000)
                live_url = page.url
            return {"draft_url": draft_url, "live_url": live_url,
                    "method": "playwright", "image_url": image_url}
        finally:
            browser.close()


def delete_wp_post(site: Website, draft_url: str) -> bool:
    """No-op for custom sites."""
    return False


def validate_external_links(html: str, limit: int = 5) -> list:
    urls = re.findall(r'<a\s[^>]*href="(https?://[^"]+)"', html, re.I)
    broken = []
    for u in urls[:limit]:
        try:
            r = requests.head(u, timeout=6, allow_redirects=True)
            if not (200 <= r.status_code < 400):
                broken.append(u)
        except Exception:
            broken.append(u)
    return broken


def publish_wp_draft(site: Website, article) -> dict:
    """Publish on custom site: open draft, click publish button."""
    from playwright.sync_api import sync_playwright
    s = json.loads(site.selectors or "{}")
    publish_sel = s.get("publish_sel")
    if not publish_sel:
        raise RuntimeError("Need 'publish_sel' in selectors JSON")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(s["login_url"], timeout=60000)
            page.fill(s["user_sel"], site.username)
            page.fill(s["pass_sel"], decrypt(site.password_hash))
            page.click(s["submit_sel"])
            page.wait_for_load_state("networkidle", timeout=30000)
            page.goto(article.draft_url, timeout=60000)
            page.wait_for_load_state("networkidle", timeout=30000)
            page.click(publish_sel)
            page.wait_for_load_state("networkidle", timeout=30000)
            return {"live_url": page.url, "method": "playwright"}
        finally:
            browser.close()
