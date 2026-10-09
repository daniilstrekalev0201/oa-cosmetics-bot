"""Сайт-витрина: тот же каталог, что и в боте.

Работает в том же процессе, что и бот, на порту PORT (его задаёт Bothost).
Всё, что меняют в /admin, сразу видно и на сайте.
Нажатия на кнопки-ссылки идут через /go/... — так их можно посчитать в статистике.
"""

import hashlib
import html
import logging
import os
import re
from pathlib import Path

from aiohttp import web
from aiogram import Bot

import stats
from catalog import CATALOG_FILE, find_product, load_catalog, product_categories

ROOT = Path(__file__).parent
# Адрес сайта: Bothost передаёт домен в переменной DOMAIN.
SITE_URL = "https://" + re.sub(r"^https?://", "", os.getenv("DOMAIN") or "bot-1791469457-9104-daniilstrekalev.bothost.tech").rstrip("/")
LOGO_FILE = ROOT / "avatar.jpg"
DEFAULT_TAGLINE = "Это больше, чем просто косметика. Это философия любви к себе и гармонии с природой."
CACHE = {"Cache-Control": "public, max-age=3600"}

_bot: Bot | None = None
_bot_url = "https://t.me/Olga_Shutova_bot"
_photo_cache: dict[str, bytes] = {}  # фото, загруженные через админку (file_id Telegram)

e = html.escape


def _plain(text: str) -> str:
    """Текст из Telegram-разметки → безопасный HTML с переносами строк."""
    text = html.unescape(re.sub(r"<[^>]+>", "", text or ""))
    return "<br>".join(e(line) for line in text.strip().split("\n"))


def _is_robot(request: web.Request) -> bool:
    ua = request.headers.get("User-Agent", "").lower()
    return any(w in ua for w in ("bot", "crawler", "spider", "preview"))


def _photo_url(product: dict) -> str | None:
    photo = product.get("photo")
    if not photo:
        return None
    version = hashlib.md5(photo.encode()).hexdigest()[:8]
    return f"/photo/{e(product['id'])}?v={version}"


# ---------- страница ----------

def _product_card(p: dict) -> str:
    photo = _photo_url(p)
    img = (
        f'<img src="{photo}" alt="{e(p["name"])}" loading="lazy">'
        if photo else '<div class="noimg">O&amp;A</div>'
    )
    meta = " · ".join(e(x) for x in (p.get("volume"), p.get("brand")) if x)
    details = []
    if p.get("description"):
        details.append(f"<p>{_plain(p['description'])}</p>")
    for key, label in (("composition", "Состав"), ("how_to_use", "Применение"),
                       ("sku", "Артикул Ozon"), ("where_to_buy", "Где купить")):
        if p.get(key):
            details.append(f"<p><b>{label}:</b> {_plain(p[key])}</p>")
    more = f"<details><summary>Подробнее</summary>{''.join(details)}</details>" if details else ""
    buttons = "".join(
        f'<a class="btn{" btn-main" if i == 0 else ""}" href="/go/{e(p["id"])}/{i}" target="_blank" rel="noopener">'
        f'{e(link["title"])}</a>'
        for i, link in enumerate(p.get("buy_links", []))
    )
    return (
        f'<article class="card" id="p-{e(p["id"])}">{img}<div class="body">'
        f'<h3>{e(p["name"])}</h3><p class="meta">{meta}</p>'
        f'<p class="price">{e(p["price"])}</p>{more}'
        f'<div class="buttons">{buttons}</div></div></article>'
    )


def render_index(catalog: dict, base_url: str = "") -> str:
    shop = catalog["shop"]
    name = e(shop.get("name", "O&A cosmetics"))
    tagline = e(shop.get("site_tagline", DEFAULT_TAGLINE))

    sections, chips = [], []
    for cat in catalog["categories"]:
        items = [p for p in catalog["products"] if cat["id"] in product_categories(p)]
        if not items:
            continue
        anchor = f"c-{e(cat['id'])}"
        chips.append(f'<a href="#{anchor}">{e(cat["title"])}</a>')
        cards = "".join(_product_card(p) for p in items)
        sections.append(f'<section class="cat" id="{anchor}"><h2>{e(cat["title"])}</h2><div class="grid">{cards}</div></section>')
    chips_html = f'<nav class="chips">{"".join(chips)}</nav>' if len(chips) > 1 else ""

    shop_links = "".join(
        f'<a class="btn" href="/go/shop/{i}" target="_blank" rel="noopener">{e(link["title"])}</a>'
        for i, link in enumerate(shop.get("links", []))
    )
    first_photo = next((_photo_url(p) for p in catalog["products"] if p.get("photo")), None)
    og_image = base_url + (first_photo or "/logo.jpg")  # превью ссылки в Telegram/ВК требует полный адрес

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} — натуральная косметика</title>
<meta name="description" content="{tagline}">
<meta property="og:title" content="{name}">
<meta property="og:description" content="{tagline}">
<meta property="og:image" content="{og_image}">
<meta property="og:url" content="{base_url}/">
<meta property="og:type" content="website">
<link rel="icon" href="/logo.jpg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600&family=Manrope:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<header class="top">
  <a class="logo" href="/"><img src="/logo.jpg" alt=""><span>{name}</span></a>
  <a class="tg" href="{_bot_url}" target="_blank" rel="noopener">Telegram</a>
</header>
<main>
  <section class="hero">
    <p class="kicker">Натуральная косметика · Россия</p>
    <h1>{name}</h1>
    <p class="tagline">{tagline}</p>
    <div class="buttons center">
      <a class="btn btn-main" href="#catalog">Смотреть каталог</a>
      <a class="btn" href="{_bot_url}" target="_blank" rel="noopener">Открыть в Telegram</a>
    </div>
  </section>
  <div id="catalog">{chips_html}{"".join(sections) or '<p class="empty">Скоро здесь появятся товары.</p>'}</div>
  <section class="about">
    <h2>О нас</h2>
    <p>{_plain(shop.get("about", ""))}</p>
    <div class="buttons center">{shop_links}</div>
  </section>
</main>
<footer>© {name} · <a href="{_bot_url}" target="_blank" rel="noopener">каталог в Telegram</a></footer>
</body>
</html>"""


CSS = """
:root{--bg:#f6efe6;--paper:#fffaf3;--ink:#2b2622;--muted:#7a6e64;--accent:#a8794f;--line:#e6d9c8}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 Manrope,system-ui,sans-serif}
a{color:inherit}
h1,h2,h3{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;line-height:1.15;margin:0}
.top{position:sticky;top:0;z-index:5;display:flex;justify-content:space-between;align-items:center;
  padding:12px 16px;background:rgba(246,239,230,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.logo{display:flex;align-items:center;gap:10px;text-decoration:none;font-family:'Cormorant Garamond',serif;font-size:22px;font-weight:600}
.logo img{width:36px;height:36px;border-radius:50%;object-fit:cover}
.tg{font-size:14px;text-decoration:none;border:1px solid var(--ink);border-radius:999px;padding:6px 14px}
main{max-width:1100px;margin:0 auto;padding:0 16px}
.hero{text-align:center;padding:64px 0 40px}
.kicker{letter-spacing:.18em;text-transform:uppercase;font-size:12px;color:var(--muted);margin:0 0 12px}
.hero h1{font-size:clamp(42px,9vw,76px)}
.tagline{max-width:560px;margin:16px auto 28px;color:var(--muted);font-size:18px}
.buttons{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.buttons.center{justify-content:center}
.btn{display:inline-block;text-decoration:none;font-size:14px;font-weight:500;padding:10px 16px;border-radius:999px;
  border:1px solid var(--ink);background:transparent;transition:.15s}
.btn:hover{background:var(--ink);color:var(--paper)}
.btn-main{background:var(--ink);color:var(--paper)}
.btn-main:hover{background:var(--accent);border-color:var(--accent)}
.chips{display:flex;gap:8px;overflow-x:auto;padding:4px 0 16px}
.chips a{white-space:nowrap;text-decoration:none;font-size:14px;padding:6px 14px;border-radius:999px;background:var(--paper);border:1px solid var(--line)}
.cat{padding:24px 0}
.cat h2,.about h2{font-size:34px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:20px}
.card{background:var(--paper);border:1px solid var(--line);border-radius:20px;overflow:hidden;display:flex;flex-direction:column}
.card img,.noimg{width:100%;aspect-ratio:4/5;object-fit:cover;display:block;background:#efe4d6}
.noimg{display:flex;align-items:center;justify-content:center;font-family:'Cormorant Garamond',serif;font-size:48px;color:var(--accent)}
.body{padding:16px 18px 20px;display:flex;flex-direction:column;flex:1}
.card h3{font-size:24px}
.meta{color:var(--muted);font-size:14px;margin:4px 0 0}
.price{font-size:20px;font-weight:600;margin:10px 0 0}
details{margin-top:10px;font-size:14px}
summary{cursor:pointer;color:var(--accent);font-weight:500}
details p{margin:8px 0 0}
.card .buttons{margin-top:auto;padding-top:14px}
.about{text-align:center;max-width:640px;margin:0 auto;padding:48px 0 32px}
.empty{text-align:center;color:var(--muted)}
footer{text-align:center;color:var(--muted);font-size:13px;padding:32px 16px;border-top:1px solid var(--line);margin-top:24px}
"""


# ---------- обработчики ----------

async def index(request: web.Request) -> web.Response:
    if not _is_robot(request):
        stats.track_site("visit")
    local = request.host.startswith(("127.", "localhost"))
    scheme = "http" if local else "https"  # Bothost отдаёт сайт только по https, но прокси об этом не сообщает
    base_url = f"{scheme}://{request.host}"
    return web.Response(text=render_index(load_catalog(), base_url), content_type="text/html")


async def photo(request: web.Request) -> web.StreamResponse:
    product = find_product(load_catalog(), request.match_info["pid"])
    value = product.get("photo") if product else None
    if not value:
        raise web.HTTPNotFound()
    if value.startswith(("http://", "https://")):
        raise web.HTTPFound(value)
    path = CATALOG_FILE.parent / value
    if path.is_file():
        return web.FileResponse(path, headers=CACHE)
    data = _photo_cache.get(value)
    if data is None:  # фото загружено через админку: берём его у Telegram по file_id
        if _bot is None:
            raise web.HTTPNotFound()
        file = await _bot.get_file(value)
        data = (await _bot.download_file(file.file_path)).read()
        _photo_cache[value] = data
    return web.Response(body=data, content_type="image/jpeg", headers=CACHE)


async def go(request: web.Request) -> web.Response:
    owner, idx = request.match_info["owner"], request.match_info["idx"]
    catalog = load_catalog()
    if owner == "shop":
        links, label = catalog["shop"].get("links", []), "О магазине"
    else:
        product = find_product(catalog, owner)
        links, label = (product.get("buy_links", []), product["name"]) if product else ([], "")
    if not idx.isdigit() or int(idx) >= len(links):
        raise web.HTTPFound("/")
    link = links[int(idx)]
    if not _is_robot(request):
        stats.track_site("click", f"{label} — {link['title']}")
    raise web.HTTPFound(link["url"])


async def logo(request: web.Request) -> web.StreamResponse:
    if not LOGO_FILE.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(LOGO_FILE, headers=CACHE)


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def make_app() -> web.Application:
    app = web.Application()
    app.add_routes([
        web.get("/", index),
        web.get("/photo/{pid}", photo),
        web.get("/go/{owner}/{idx}", go),
        web.get("/logo.jpg", logo),
        web.get("/health", health),
    ])
    return app


async def start(bot: Bot) -> None:
    """Запускает сайт рядом с ботом. Ошибка сайта не должна мешать боту работать."""
    global _bot, _bot_url
    _bot = bot
    try:
        me = await bot.get_me()
        _bot_url = f"https://t.me/{me.username}"
        runner = web.AppRunner(make_app(), access_log=None)
        await runner.setup()
        port = int(os.getenv("PORT", "3000"))  # Bothost: «Порт веб-приложения» = 3000
        await web.TCPSite(runner, "0.0.0.0", port).start()
        logging.info("Сайт запущен на порту %s: %s", port, SITE_URL)
    except Exception:  # noqa: BLE001 — бот важнее сайта
        logging.exception("Не удалось запустить сайт")
