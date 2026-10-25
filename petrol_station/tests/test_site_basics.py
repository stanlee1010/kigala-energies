"""Site basics: 404 page, robots.txt / noindex, meta descriptions, share image, alt text, privacy page."""
import re
from pathlib import Path

FRONT = Path(__file__).resolve().parents[1] / "frontend"
PAGES = sorted(FRONT.rglob("*.html"))


def test_custom_404_page(app):
    r = app.test_client().get("/this/page/does/not/exist.html")
    assert r.status_code == 404 and b"Page not found" in r.data and b"Go to my dashboard" in r.data
    api = app.test_client().get("/api/nothing-here")
    assert api.status_code == 404 and api.is_json


def test_search_engines_are_told_not_to_list_the_private_system(app):
    r = app.test_client().get("/robots.txt")
    assert r.status_code == 200 and "Disallow: /" in r.get_data(as_text=True)
    assert app.test_client().get("/login.html").headers["X-Robots-Tag"] == "noindex, nofollow"


def test_every_page_has_meta_description_robots_and_share_tags():
    for page in PAGES:
        html = page.read_text(encoding="utf-8")
        head = html.split("</head>")[0]
        name = page.relative_to(FRONT).as_posix()
        assert re.search(r'<meta name="description" content="[^"]{20,}">', head), name
        assert '<meta name="robots" content="noindex, nofollow">' in head, name
        assert '<meta property="og:image" content="/img/share.png">' in head, name


def test_share_image_exists(app):
    r = app.test_client().get("/img/share.png")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data.startswith(b"\x89PNG")


def test_images_and_charts_have_text_alternatives():
    for path in PAGES + sorted((FRONT / "js").rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for tag in re.findall(r"<img\b[^>]*>", text):
            assert "alt=" in tag, f"{path.name}: {tag}"
        for tag in re.findall(r'<canvas\b[^>]*>', text):
            assert 'role="img"' in tag and "aria-label=" in tag, f"{path.name}: {tag}"
        for tag in re.findall(r'<div class="sidebar__logo"[^>]*>', text):
            assert "aria-label=" in tag, f"{path.name}: {tag}"


def test_privacy_policy_page(app):
    r = app.test_client().get("/privacy.html")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "Privacy policy" in body
    for topic in ("What information is kept", "Who can see it", "How long it is kept", "no advertising or tracking"):
        assert topic in body
    assert "/privacy.html" in (FRONT / "login.html").read_text(encoding="utf-8")


def test_page_files_have_content_etags_and_come_back_304(app):
    client = app.test_client()
    r = client.get("/js/app.js")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-cache"
    etag = r.headers["ETag"].strip('"')
    assert len(etag) == 20                                              # from the file's contents, not its time
    assert client.get("/js/app.js", headers={"If-None-Match": f'"{etag}"'}).status_code == 304
    assert "Vercel-CDN-Cache-Control" not in r.headers                  # only on Vercel
    assert client.get("/../backend/config.py").status_code == 404
    assert client.get("/js/../../backend/config.py").status_code == 404


def test_vercel_keeps_page_files_in_its_cdn_but_never_api_replies(app, manager):
    app.config["VERCEL"] = True
    client = app.test_client()
    page = client.get("/staff/index.html")
    assert page.headers["Vercel-CDN-Cache-Control"] == "max-age=31536000"
    mine = manager.get("/staff/index.html")                 # logged in: still nothing personal in the reply
    assert "Set-Cookie" not in mine.headers and "Cookie" not in mine.headers.get("Vary", "")
    assert manager.get("/api/auth/me").headers.get("Set-Cookie")   # the API keeps the login fresh
    assert "Vercel-CDN-Cache-Control" not in client.get("/no-such-page.html").headers   # 404s are not kept
    api = manager.get("/api/auth/me")
    assert api.headers["Cache-Control"] == "no-store" and "Vercel-CDN-Cache-Control" not in api.headers
