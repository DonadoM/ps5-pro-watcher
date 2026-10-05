"""
Adaptadores por tienda. Cada funcion recibe (search_terms, direct_urls) y devuelve:
    [{"id": str, "title": str, "price": int (COP), "url": str}, ...]
Solo consolas PS5 Pro (filtra accesorios y juegos).
"""
import json
import logging
import re
import time

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

REQUEST_DELAY_S = 1.0  # pausa entre paginas de producto de una misma tienda

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


# --- Exito (paginas de producto) ---
# El robots.txt de Exito pide no usar /api/ ni el buscador (/s?), y su sitemap no
# lista productos. Por eso se revisa una lista fija de paginas de producto
# (config.json -> direct_urls.Exito), que si estan permitidas.

EXITO_ID = re.compile(r"-(\d+)(?:-mp)?/p/?$")


def fetch_exito(search_terms, direct_urls=None):
    if not direct_urls:
        logging.info("Exito: sin URLs en direct_urls.Exito, se omite")
        return []

    def product_id(url):
        m = EXITO_ID.search(url.split("?")[0])
        return m.group(1) if m else None

    # La lista es curada a mano: no se aplica is_ps5_pro_console (Exito a veces
    # titula la consola sin la palabra "Consola").
    return _fetch_direct_urls("Exito", direct_urls, id_from_url=product_id)


# --- Falabella / Homecenter (parsea __NEXT_DATA__ del Grupo Falabella) ---

def _fetch_falabella_group(store_name, base_url, search_url, search_terms):
    results = []
    seen_ids = set()
    for term in search_terms:
        try:
            r = requests.get(search_url, params={"Ntt": term}, headers=UA, timeout=25)
            r.raise_for_status()
            html = r.text
        except Exception as e:
            logging.warning("%s search '%s' fallo: %s", store_name, term, e)
            continue
        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if not m:
            logging.info("%s: sin __NEXT_DATA__ para '%s'", store_name, term)
            continue
        try:
            payload = json.loads(m.group(1))
        except Exception as e:
            logging.warning("%s JSON parse fallo: %s", store_name, e)
            continue

        def walk(node):
            if isinstance(node, dict):
                title = node.get("displayName") or node.get("name")
                prices = node.get("prices")
                pid = node.get("productId") or node.get("skuId") or node.get("id")
                url_frag = node.get("url") or node.get("productUrl")
                stock_status = (
                    node.get("stockLevelStatus")
                    or node.get("availabilityStatus")
                    or ""
                )
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
                                else f"{base_url}{url_frag}"
                            )
                            in_stock = True
                            if isinstance(stock_status, str) and stock_status:
                                low = stock_status.lower()
                                if any(x in low for x in ("outofstock", "out_of_stock", "sinstock", "no_stock", "unavailable")):
                                    in_stock = False
                            results.append({
                                "id": str(pid),
                                "title": title.strip(),
                                "price": best,
                                "url": full_url,
                                "in_stock": in_stock,
                            })
                            seen_ids.add(str(pid))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(payload)
    return results


def fetch_falabella(search_terms, direct_urls=None):
    return _fetch_falabella_group(
        "Falabella",
        "https://www.falabella.com.co",
        "https://www.falabella.com.co/falabella-co/search",
        search_terms,
    )


def fetch_homecenter(search_terms, direct_urls=None):
    return _fetch_falabella_group(
        "Homecenter",
        "https://www.homecenter.com.co",
        "https://www.homecenter.com.co/homecenter-co/search",
        search_terms,
    )


# --- URL directa (Alkosto, Ktronix, cualquier tienda) ---

JSONLD_PATTERN = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _availability_from_string(s: str) -> bool:
    """True si el availability schema.org indica disponible."""
    if not s:
        return True  # sin dato -> asumimos disponible para no ocultar la oferta
    s = s.lower()
    if "outofstock" in s or "out_of_stock" in s or "soldout" in s or "discontinued" in s:
        return False
    if "instock" in s or "in_stock" in s or "onlineonly" in s or "preorder" in s:
        return True
    return True


def _parse_price(price) -> int:
    """Acepta 4599900, "4599900.00", "$4.599.900" o "4,599,900"."""
    if isinstance(price, str):
        price = re.sub(r"[^\d.,]", "", price)
        if re.fullmatch(r"\d{1,3}([.,]\d{3})+", price):
            price = re.sub(r"[.,]", "", price)  # separadores de miles
        else:
            price = price.replace(",", ".")
    try:
        return int(float(price)) if price else 0
    except Exception:
        return 0


def _extract_price_from_html(html: str):
    """Devuelve (title, price, in_stock) desde JSON-LD, OpenGraph o itemprop."""
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
                if not isinstance(offers, list):
                    offers = [offers]
                # Varios vendedores por producto: la oferta mas barata con stock;
                # si ninguna tiene stock, la mas barata.
                parsed = []
                for offer in offers:
                    if not isinstance(offer, dict):
                        continue
                    price_int = _parse_price(offer.get("price") or offer.get("lowPrice"))
                    if price_int > 0:
                        in_stock = _availability_from_string(offer.get("availability", ""))
                        parsed.append((not in_stock, price_int, in_stock))
                if name and parsed:
                    _, price_int, in_stock = min(parsed)
                    return name.strip(), price_int, in_stock
    # 2) og:title + og:price:amount + og:availability
    og_title = re.search(
        r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html
    )
    og_price = re.search(
        r'<meta[^>]+property="(?:og:price:amount|product:price:amount)"[^>]+content="([^"]+)"',
        html,
    )
    og_avail = re.search(
        r'<meta[^>]+property="(?:og:availability|product:availability)"[^>]+content="([^"]+)"',
        html,
    )
    if og_title and og_price:
        try:
            p = _parse_price(og_price.group(1))
            if p > 0:
                in_stock = _availability_from_string(og_avail.group(1) if og_avail else "")
                return og_title.group(1).strip(), p, in_stock
        except Exception:
            pass
    # 3) itemprop=price + itemprop=availability
    ip = re.search(
        r'<meta[^>]+itemprop="price"[^>]+content="([^"]+)"', html
    )
    ttl = re.search(r"<title>([^<]+)</title>", html)
    ia = re.search(
        r'<(?:meta|link)[^>]+itemprop="availability"[^>]+(?:href|content)="([^"]+)"',
        html,
    )
    if ip and ttl:
        try:
            p = _parse_price(ip.group(1))
            if p > 0:
                in_stock = _availability_from_string(ia.group(1) if ia else "")
                return ttl.group(1).strip(), p, in_stock
        except Exception:
            pass
    return None, None, True


def _fetch_direct_urls(store_name: str, urls, id_from_url=None):
    """Monitorea precios de URLs de producto especificas."""
    results = []
    for i, url in enumerate(urls or []):
        if not url or not isinstance(url, str):
            continue
        if i:
            time.sleep(REQUEST_DELAY_S)  # no saturar el sitio
        try:
            r = requests.get(url, headers=UA, timeout=25, allow_redirects=True)
            r.raise_for_status()
        except Exception as e:
            logging.warning("%s URL %s fallo: %s", store_name, url, e)
            continue
        title, price, in_stock = _extract_price_from_html(r.text)
        if not price:
            logging.info("%s URL %s: no se pudo extraer precio", store_name, url)
            continue
        pid = (id_from_url(url) if id_from_url else None) or re.sub(r"[^a-zA-Z0-9]", "_", url)[-40:]
        results.append({
            "id": pid,
            "title": (title or url)[:200],
            "price": price,
            "url": url,
            "in_stock": in_stock,
        })
    return results


_SITEMAP_CACHE = {}


def _discover_from_sitemap(base_url: str, keywords):
    """Devuelve URLs del sitemap-productos que contengan cualquier keyword."""
    cache_key = base_url
    if cache_key in _SITEMAP_CACHE:
        cached_at, urls = _SITEMAP_CACHE[cache_key]
        if time.time() - cached_at < 3600:
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
    _SITEMAP_CACHE[cache_key] = (time.time(), urls)
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
    "Homecenter": fetch_homecenter,
    "Alkosto": fetch_alkosto,
    "Ktronix": fetch_ktronix,
    "MercadoLibre": fetch_mercadolibre,
}
