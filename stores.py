"""
Adaptadores por tienda. Cada funcion recibe (search_terms, direct_urls) y devuelve:
    [{"id": str, "title": str, "price": int (COP), "url": str}, ...]
Solo consolas PS5 Pro (filtra accesorios y juegos).
"""
import json
import logging
import re
import requests

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}

# Palabras que NO deben aparecer en el titulo. NO incluyo "control/mando/dualsense"
# porque toda PS5 Pro viene con uno o dos controles en la caja.
ACCESSORY_TERMS = [
    "headset", "audifon", "auricular", "pulse",
    "carcasa", "estuche", "cover",
    "silla", "gafas", "portal", "camara", "camera", "microfono",
    "protector", "vertical",
    # juegos comunes que colisionan con "Pro" en el nombre
    "skater", "hawk", "hawks", "juego", "videojuego", "game ", "gameplay",
]


def is_ps5_pro_console(title: str) -> bool:
    if not title:
        return False
    t = title.lower()
    has_pro = re.search(r"\bpro\b", t) is not None
    has_ps5 = ("ps5" in t) or ("playstation 5" in t) or ("playstation5" in t)
    if not (has_pro and has_ps5):
        return False
    if "ps4" in t or "playstation 4" in t:
        return False
    if re.search(r"\bslim\b", t):
        return False
    # DEBE contener "consola" o "console" para asegurar que es hardware
    if not re.search(r"\b(consola|console)\b", t):
        return False
    for w in ACCESSORY_TERMS:
        pattern = r"\b" + re.escape(w.strip())
        if re.search(pattern, t):
            return False
    return True


# --- VTEX (Exito) ---

def _fetch_vtex(store_name: str, base_url: str, search_terms):
    results = []
    seen_ids = set()
    for term in search_terms:
        url = f"{base_url}/api/catalog_system/pub/products/search"
        params = {"ft": term, "_from": 0, "_to": 29}
        try:
            r = requests.get(url, params=params, headers=UA, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logging.warning("%s search '%s' fallo: %s", store_name, term, e)
            continue
        if not isinstance(data, list):
            continue
        for prod in data:
            title = prod.get("productName") or ""
            if not is_ps5_pro_console(title):
                continue
            pid = str(prod.get("productId") or "")
            if not pid or pid in seen_ids:
                continue
            items = prod.get("items") or []
            if not items:
                continue
            item = items[0]
            sellers = item.get("sellers") or []
            if not sellers:
                continue
            offer = sellers[0].get("commertialOffer") or {}
            price = offer.get("Price") or 0
            available = offer.get("AvailableQuantity") or 0
            if price <= 0 or available <= 0:
                continue
            link = prod.get("linkText") or ""
            results.append({
                "id": pid,
                "title": title.strip(),
                "price": int(price),
                "url": f"{base_url}/{link}/p" if link else base_url,
            })
            seen_ids.add(pid)
    return results


def fetch_exito(search_terms, direct_urls=None):
    return _fetch_vtex("Exito", "https://www.exito.com", search_terms)


# --- Falabella (parsea __NEXT_DATA__) ---

def fetch_falabella(search_terms, direct_urls=None):
    results = []
    seen_ids = set()
    for term in search_terms:
        url = "https://www.falabella.com.co/falabella-co/search"
        params = {"Ntt": term}
        try:
            r = requests.get(url, params=params, headers=UA, timeout=25)
            r.raise_for_status()
            html = r.text
        except Exception as e:
            logging.warning("Falabella search '%s' fallo: %s", term, e)
            continue
        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if not m:
            logging.info("Falabella: sin __NEXT_DATA__ para '%s'", term)
            continue
        try:
            payload = json.loads(m.group(1))
        except Exception as e:
            logging.warning("Falabella JSON parse fallo: %s", e)
            continue

        def walk(node):
            if isinstance(node, dict):
                title = node.get("displayName") or node.get("name")
                prices = node.get("prices")
                pid = node.get("productId") or node.get("skuId") or node.get("id")
                url_frag = node.get("url") or node.get("productUrl")
                if title and isinstance(prices, list) and prices and pid and url_frag:
                    if is_ps5_pro_console(title):
                        vals = []
                        for p in prices:
                            pv = p.get("price")
                            if isinstance(pv, list) and pv:
                                pv = pv[0]
                            if isinstance(pv, str):
                                pv = re.sub(r"[^\d]", "", pv)
                                pv = int(pv) if pv else 0
                            if isinstance(pv, (int, float)) and pv > 0:
                                vals.append(int(pv))
                        if vals and str(pid) not in seen_ids:
                            best = min(vals)
                            full_url = (
                                url_frag if url_frag.startswith("http")
                                else f"https://www.falabella.com.co{url_frag}"
                            )
                            results.append({
                                "id": str(pid),
                                "title": title.strip(),
                                "price": best,
                                "url": full_url,
                            })
                            seen_ids.add(str(pid))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(payload)
    return results


# --- URL directa (Alkosto, Ktronix, cualquier tienda) ---

JSONLD_PATTERN = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _extract_price_from_html(html: str):
    """Intenta extraer (title, price) desde JSON-LD, OpenGraph o meta itemprop."""
    # 1) JSON-LD Product
    for m in JSONLD_PATTERN.finditer(html):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            if entry.get("@type") in ("Product", "product"):
                name = entry.get("name") or ""
                offers = entry.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = offers.get("price") or offers.get("lowPrice")
                if isinstance(price, str):
                    price = re.sub(r"[^\d.]", "", price).split(".")[0]
                try:
                    price_int = int(float(price)) if price else 0
                except Exception:
                    price_int = 0
                if name and price_int > 0:
                    return name.strip(), price_int
    # 2) og:title + og:price:amount
    og_title = re.search(
        r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html
    )
    og_price = re.search(
        r'<meta[^>]+property="(?:og:price:amount|product:price:amount)"[^>]+content="([^"]+)"',
        html,
    )
    if og_title and og_price:
        try:
            p = int(float(re.sub(r"[^\d.]", "", og_price.group(1))))
            if p > 0:
                return og_title.group(1).strip(), p
        except Exception:
            pass
    # 3) itemprop=price
    ip = re.search(
        r'<meta[^>]+itemprop="price"[^>]+content="([^"]+)"', html
    )
    ttl = re.search(r"<title>([^<]+)</title>", html)
    if ip and ttl:
        try:
            p = int(float(re.sub(r"[^\d.]", "", ip.group(1))))
            if p > 0:
                return ttl.group(1).strip(), p
        except Exception:
            pass
    return None, None


def _fetch_direct_urls(store_name: str, urls):
    """Monitorea precios de URLs de producto especificas."""
    results = []
    for url in urls or []:
        if not url or not isinstance(url, str):
            continue
        try:
            r = requests.get(url, headers=UA, timeout=25, allow_redirects=True)
            r.raise_for_status()
        except Exception as e:
            logging.warning("%s URL %s fallo: %s", store_name, url, e)
            continue
        title, price = _extract_price_from_html(r.text)
        if not price:
            logging.info("%s URL %s: no se pudo extraer precio", store_name, url)
            continue
        pid = re.sub(r"[^a-zA-Z0-9]", "_", url)[-40:]
        results.append({
            "id": pid,
            "title": (title or url)[:200],
            "price": price,
            "url": url,
        })
    return results


_SITEMAP_CACHE = {}


def _discover_from_sitemap(base_url: str, keywords):
    """Devuelve URLs del sitemap-productos que contengan cualquier keyword."""
    cache_key = base_url
    if cache_key in _SITEMAP_CACHE:
        cached_at, urls = _SITEMAP_CACHE[cache_key]
        import time as _t
        if _t.time() - cached_at < 3600:
            return urls
    urls = []
    sitemap_url = f"{base_url}/sitemap-productos.xml"
    try:
        r = requests.get(sitemap_url, headers=UA, timeout=25)
        r.raise_for_status()
    except Exception as e:
        logging.warning("Sitemap %s fallo: %s", sitemap_url, e)
        return urls
    for m in re.finditer(r"<loc>([^<]+)</loc>", r.text):
        u = m.group(1).strip()
        low = u.lower()
        if any(k in low for k in keywords):
            urls.append(u)
    import time as _t
    _SITEMAP_CACHE[cache_key] = (_t.time(), urls)
    return urls


def _alkosto_like(store_name, base_url, direct_urls):
    urls = list(direct_urls or [])
    if not urls:
        # Descubre automaticamente. Filtramos por titulo/slug con "ps5-pro" o "playstation-5-pro"
        urls = _discover_from_sitemap(
            base_url, ["ps5-pro", "playstation-5-pro"]
        )
        logging.info("%s: descubri %d URL(s) via sitemap", store_name, len(urls))
    results = _fetch_direct_urls(store_name, urls)
    # Aplicar el mismo filtro de consola
    return [r for r in results if is_ps5_pro_console(r["title"])]


def fetch_alkosto(search_terms, direct_urls=None):
    return _alkosto_like("Alkosto", "https://www.alkosto.com", direct_urls)


def fetch_ktronix(search_terms, direct_urls=None):
    return _alkosto_like("Ktronix", "https://www.ktronix.com", direct_urls)


# --- MercadoLibre (requiere OAuth token desde 2024) ---

def fetch_mercadolibre(search_terms, direct_urls=None):
    """
    MercadoLibre exige token OAuth para /sites/MCO/search desde 2024.
    Si config trae 'mercadolibre_access_token' lo usamos; si no, saltamos.
    """
    # El token se lee via variable de entorno para no romper el contrato de la firma
    import os
    token = os.environ.get("ML_ACCESS_TOKEN", "").strip()
    if not token:
        logging.info(
            "MercadoLibre saltado: falta ML_ACCESS_TOKEN "
            "(ver instrucciones en el README)."
        )
        return []
    headers = dict(UA)
    headers["Authorization"] = f"Bearer {token}"
    results = []
    seen_ids = set()
    for term in search_terms:
        url = "https://api.mercadolibre.com/sites/MCO/search"
        params = {"q": term, "limit": 30, "condition": "new"}
        try:
            r = requests.get(url, params=params, headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logging.warning("MercadoLibre search '%s' fallo: %s", term, e)
            continue
        for item in data.get("results", []):
            title = item.get("title") or ""
            if not is_ps5_pro_console(title):
                continue
            if not item.get("official_store_id"):
                continue
            pid = item.get("id") or ""
            if not pid or pid in seen_ids:
                continue
            price = item.get("price") or 0
            if price <= 0:
                continue
            results.append({
                "id": pid,
                "title": title.strip(),
                "price": int(price),
                "url": item.get("permalink") or "",
            })
            seen_ids.add(pid)
    return results


ALL_STORES = {
    "Exito": fetch_exito,
    "Falabella": fetch_falabella,
    "Alkosto": fetch_alkosto,
    "Ktronix": fetch_ktronix,
    "MercadoLibre": fetch_mercadolibre,
}
