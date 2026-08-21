"""Ejecuta una pasada por todas las tiendas y muestra los precios en consola.
No envia mensajes de Telegram. Usalo para verificar que todo funciona."""
import json
from pathlib import Path
from stores import ALL_STORES

BASE = Path(__file__).resolve().parent
cfg_file = BASE / "config.json"
if not cfg_file.exists():
    cfg_file = BASE / "config.example.json"
cfg = json.loads(cfg_file.read_text(encoding="utf-8"))

terms = cfg["search_terms"]
direct = cfg.get("direct_urls", {}) or {}
enabled = cfg.get("stores_enabled", {}) or {}

all_offers = []
for name, fn in ALL_STORES.items():
    if not enabled.get(name, True):
        print(f"{name}: (deshabilitado)")
        continue
    urls = [u for u in (direct.get(name) or []) if isinstance(u, str) and u.startswith("http")]
    try:
        offers = fn(terms, direct_urls=urls)
    except Exception as e:
        print(f"{name}: ERROR {e}")
        continue
    print(f"{name}: {len(offers)} oferta(s)")
    for o in offers:
        p = f"${o['price']:,d}".replace(",", ".")
        stock = "OK" if o.get("in_stock", True) else "AGOTADO"
        print(f"  [{stock:>7}]  {p:>14}  {o['title'][:65]}")
        all_offers.append((name, o))

print()
print("=" * 60)
in_stock_offers = [x for x in all_offers if x[1].get("in_stock", True)]
if in_stock_offers:
    best = min(in_stock_offers, key=lambda x: x[1]["price"])
    p = f"${best[1]['price']:,d}".replace(",", ".")
    print(f"MEJOR PRECIO CON STOCK: {p} en {best[0]}")
    print(f"  {best[1]['title']}")
    print(f"  {best[1]['url']}")
elif all_offers:
    print("Todo agotado. Mejor precio historico:")
    best = min(all_offers, key=lambda x: x[1]["price"])
    p = f"${best[1]['price']:,d}".replace(",", ".")
    print(f"  {p} en {best[0]} (AGOTADO)")
    print(f"  {best[1]['title']}")
else:
    print("Sin ofertas.")
