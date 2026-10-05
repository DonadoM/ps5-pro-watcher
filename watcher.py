"""
PS5 Pro price watcher para Colombia.
Hace una pasada por todas las tiendas, alerta cambios por Telegram, guarda
state.json y agrega los cambios a data/history.csv. Lo ejecuta GitHub Actions
cada 3 horas (.github/workflows/watch.yml).
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from history import append_events, make_event
from notifier import send_telegram
from stores import ALL_STORES

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "config.json"
EXAMPLE_FILE = BASE / "config.example.json"
STATE_FILE = BASE / "state.json"

# Un producto tiene que faltar en estas corridas seguidas antes de avisar que
# desaparecio. Los buscadores de las tiendas a veces omiten un producto en una
# sola corrida y eso generaba falsas alertas de "AGOTADO/RETIRADO".
MISSES_BEFORE_GONE = 2

# Algunos vendedores de Exito cambian el precio unos pesos en cada corrida
# (repricing automatico). Cambios menores a esto no generan alerta.
DEFAULT_MIN_CHANGE_PCT = 1.0


def load_config():
    # 1) Si existe config.json local, arrancar con el
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    elif EXAMPLE_FILE.exists():
        cfg = json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))
    else:
        raise FileNotFoundError("Falta config.json y config.example.json")

    # 2) Env vars sobrescriben (para GitHub Actions Secrets)
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    env_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if env_token:
        cfg["telegram_bot_token"] = env_token
    if env_chat:
        cfg["telegram_chat_id"] = env_chat
    env_threshold = os.environ.get("THRESHOLD_COP", "").strip()
    if env_threshold.isdigit():
        cfg["threshold_cop"] = int(env_threshold)

    # 3) Validacion final: token/chat_id no pueden seguir siendo placeholders
    token = cfg.get("telegram_bot_token", "")
    chat_id = cfg.get("telegram_chat_id", "")
    if not token or token.startswith("PEGA_"):
        raise RuntimeError(
            "Falta TELEGRAM_BOT_TOKEN (env var o config.json).")
    if not chat_id or str(chat_id).startswith("PEGA_"):
        raise RuntimeError(
            "Falta TELEGRAM_CHAT_ID (env var o config.json).")
    return cfg


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            logging.warning("state.json corrupto, reiniciando")
    return {}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def format_cop(v):
    return f"${v:,.0f}".replace(",", ".")


def escape_md(text: str) -> str:
    # Escapa caracteres que Telegram Markdown v1 interpreta.
    return text.replace("_", " ").replace("*", "").replace("[", "(").replace("]", ")")


def fetch_all(config, state):
    """Devuelve (ofertas actuales por key, tiendas que no dieron datos confiables)."""
    current = {}
    failed_stores = set()
    direct_urls_by_store = config.get("direct_urls", {}) or {}
    for store_name, fetcher in ALL_STORES.items():
        if not config.get("stores_enabled", {}).get(store_name, True):
            continue
        urls = [
            u for u in (direct_urls_by_store.get(store_name) or [])
            if isinstance(u, str) and u.startswith("http")
        ]
        try:
            offers = fetcher(config["search_terms"], direct_urls=urls)
        except Exception as e:
            logging.exception("%s fallo: %s", store_name, e)
            failed_stores.add(store_name)
            continue
        had_offers = any(
            v.get("store") == store_name and not v.get("_stale") for v in state.values()
        )
        if not offers and had_offers:
            # Cero resultados cuando antes habia ofertas suele ser bloqueo o caida
            # del sitio, no que se agotaron todas a la vez.
            logging.warning("%s: 0 ofertas (antes habia), se ignora esta corrida", store_name)
            failed_stores.add(store_name)
            continue
        in_stock_n = sum(1 for o in offers if o.get("in_stock", True))
        logging.info(
            "%s: %d oferta(s), %d con stock",
            store_name, len(offers), in_stock_n,
        )
        for offer in offers:
            key = f"{store_name}::{offer['id']}"
            current[key] = {
                "store": store_name,
                "title": offer["title"],
                "price": offer["price"],
                "url": offer["url"],
                "in_stock": bool(offer.get("in_stock", True)),
            }
    return current, failed_stores


def send_startup_summary(config, current, notify):
    threshold = int(config.get("threshold_cop", 0) or 0)
    if not current:
        notify(
            "*PS5 Pro watcher iniciado*\nNo encontre ofertas en esta primera pasada. "
            "Seguire intentando."
        )
        return
    lines = ["*PS5 Pro - watcher iniciado*", ""]
    # Ordenar: primero los con stock, dentro de cada grupo por precio
    ordered = sorted(
        current.values(),
        key=lambda x: (not x.get("in_stock", True), x["price"]),
    )
    for o in ordered:
        tag = "" if o.get("in_stock", True) else "  [AGOTADO]"
        lines.append(
            f"- {o['store']}: {format_cop(o['price'])}{tag}\n"
            f"  {escape_md(o['title'][:70])}\n"
            f"  {o['url']}"
        )
    lines.append("")
    lines.append(f"Umbral configurado: {format_cop(threshold)}")
    notify("\n".join(lines))


def alerted_price(prev):
    return prev.get("_alerted_price", prev["price"])


def change_message(offer, prev, threshold, min_change_pct=DEFAULT_MIN_CHANGE_PCT):
    """Mensaje de alerta para una oferta actual, o None si no hay nada que avisar."""
    now_in = offer.get("in_stock", True)
    below = threshold > 0 and offer["price"] <= threshold and now_in
    returned = prev is not None and prev.get("_stale")
    stock_changed = prev is not None and prev.get("in_stock", True) != now_in
    if prev is None:
        price_changed = True
    else:
        # Contra el ultimo precio avisado, asi una bajada lenta igual alerta al sumar.
        base = alerted_price(prev)
        price_changed = abs(offer["price"] - base) / base * 100 >= min_change_pct
    was_below = (
        prev is not None and threshold > 0 and prev.get("in_stock", True)
        and alerted_price(prev) <= threshold
    )
    below_news = below and not was_below

    if not (price_changed or below_news or stock_changed or returned):
        return None

    # Determinar el tipo principal de alerta
    if returned:
        emoji = "DE VUELTA" + (" [AGOTADO]" if not now_in else "")
        delta_line = f"\nUltimo precio visto: {format_cop(prev['price'])}"
    elif stock_changed and now_in:
        emoji = "REPUESTO EN STOCK"
        delta_line = f"\nAntes estaba agotado. Precio ahora: {format_cop(offer['price'])}"
    elif stock_changed and not now_in:
        emoji = "AGOTADO"
        delta_line = f"\nUltimo precio visto: {format_cop(prev['price'])}"
    elif prev is None:
        emoji = "NUEVO" + (" [AGOTADO]" if not now_in else "")
        delta_line = ""
    elif offer["price"] < alerted_price(prev):
        emoji = "BAJO"
        delta = alerted_price(prev) - offer["price"]
        delta_line = f"\nAntes: {format_cop(alerted_price(prev))}  (-{format_cop(delta)})"
    elif offer["price"] > alerted_price(prev):
        emoji = "SUBIO"
        delta = offer["price"] - alerted_price(prev)
        delta_line = f"\nAntes: {format_cop(alerted_price(prev))}  (+{format_cop(delta)})"
    else:
        emoji = "OFERTA"
        delta_line = ""

    tag = " *POR DEBAJO DEL UMBRAL*" if below else ""
    if not now_in and "AGOTADO" not in emoji:
        tag += " [AGOTADO]"
    return (
        f"*{emoji} {offer['store']}*{tag}\n"
        f"{escape_md(offer['title'])}\n"
        f"Precio: *{format_cop(offer['price'])}*"
        f"{delta_line}\n"
        f"{offer['url']}"
    )


def run_once(config, state, notify, now):
    """Una pasada completa. Devuelve (nuevo estado, eventos para el historial)."""
    current, failed_stores = fetch_all(config, state)
    threshold = int(config.get("threshold_cop", 0) or 0)
    min_change_pct = float(config.get("min_change_pct", DEFAULT_MIN_CHANGE_PCT))
    events = []

    if not state:
        send_startup_summary(config, current, notify)
    else:
        for key, offer in current.items():
            prev = state.get(key)
            msg = change_message(offer, prev, threshold, min_change_pct)
            if msg:
                notify(msg)
            elif prev is not None:
                # Sin alerta: se recuerda el ultimo precio avisado para comparar despues.
                offer["_alerted_price"] = alerted_price(prev)

    for key, offer in current.items():
        prev = state.get(key)
        if (
            prev is None or prev.get("_stale")
            or (prev["price"], prev.get("in_stock", True)) != (offer["price"], offer["in_stock"])
        ):
            events.append(make_event(now, key, offer))

    # Productos que no aparecieron en esta corrida
    new_state = dict(current)
    for key, prev in state.items():
        if key in current:
            continue
        prev = dict(prev)
        new_state[key] = prev
        if prev.get("_stale") or prev["store"] in failed_stores:
            continue
        prev["_misses"] = prev.get("_misses", 0) + 1
        if prev["_misses"] < MISSES_BEFORE_GONE:
            logging.info("%s no aparecio (%d/%d)", key, prev["_misses"], MISSES_BEFORE_GONE)
            continue
        del prev["_misses"]
        prev["_stale"] = True
        notify(
            f"*AGOTADO/RETIRADO {prev['store']}*\n"
            f"{escape_md(prev['title'])}\n"
            f"Ultimo precio visto: {format_cop(prev['price'])}\n"
            f"{prev['url']}"
        )
        if prev.get("in_stock", True):
            events.append(make_event(now, key, prev, in_stock=False))
    return new_state, events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="No envia Telegram ni escribe archivos; imprime lo que haria.",
    )
    # --once se mantiene por compatibilidad: ahora siempre es una sola pasada.
    parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )
    if args.dry_run:
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dry-run")
        os.environ.setdefault("TELEGRAM_CHAT_ID", "dry-run")
    config = load_config()
    state = load_state()

    def notify(text):
        if args.dry_run:
            print("--- Telegram (dry-run) ---\n" + text)
        else:
            send_telegram(config["telegram_bot_token"], config["telegram_chat_id"], text)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logging.info("Umbral=%s COP, primera corrida=%s", config.get("threshold_cop"), not state)
    state, events = run_once(config, state, notify, now)
    if args.dry_run:
        logging.info("dry-run: %d evento(s) de historial, no se guarda nada", len(events))
        return
    save_state(state)
    append_events(events)
    logging.info("Ciclo completado: %d evento(s) de historial.", len(events))


if __name__ == "__main__":
    main()
