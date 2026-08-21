"""
PS5 Pro price watcher para Colombia.
Modo default: loop infinito cada N horas (uso local con run_hidden.vbs).
Modo --once: una sola pasada y sale (uso en GitHub Actions cron).
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from notifier import send_telegram
from stores import ALL_STORES

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "config.json"
EXAMPLE_FILE = BASE / "config.example.json"
STATE_FILE = BASE / "state.json"
LOG_FILE = BASE / "watcher.log"


def _setup_logging(also_stdout: bool):
    handlers = [logging.FileHandler(str(LOG_FILE), encoding="utf-8")]
    if also_stdout:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )


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


def run_once(config, state, first_run):
    current = {}
    per_store_counts = {}
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
            continue
        per_store_counts[store_name] = len(offers)
        logging.info("%s: %d ofertas encontradas", store_name, len(offers))
        for offer in offers:
            key = f"{store_name}::{offer['id']}"
            current[key] = {
                "store": store_name,
                "title": offer["title"],
                "price": offer["price"],
                "url": offer["url"],
            }

    threshold = int(config.get("threshold_cop", 0) or 0)
    token = config["telegram_bot_token"]
    chat_id = config["telegram_chat_id"]

    if first_run:
        # Resumen inicial
        if current:
            lines = ["*PS5 Pro - watcher iniciado*", ""]
            ordered = sorted(current.values(), key=lambda x: x["price"])
            for o in ordered:
                lines.append(
                    f"- {o['store']}: {format_cop(o['price'])}\n"
                    f"  {escape_md(o['title'][:70])}\n"
                    f"  {o['url']}"
                )
            lines.append("")
            lines.append(f"Umbral configurado: {format_cop(threshold)}")
            lines.append(f"Chequeo cada {config['interval_hours']}h")
            send_telegram(token, chat_id, "\n".join(lines))
        else:
            send_telegram(
                token, chat_id,
                "*PS5 Pro watcher iniciado*\nNo encontre ofertas en esta primera pasada. "
                "Seguire intentando.",
            )
    else:
        # Alertas por cambio o umbral
        for key, offer in current.items():
            prev = state.get(key)
            below = threshold > 0 and offer["price"] <= threshold
            changed = (prev is None) or (prev.get("price") != offer["price"])
            if not (changed or below):
                continue

            if prev is None:
                emoji = "NUEVO"
                delta_line = ""
            elif offer["price"] < prev["price"]:
                emoji = "BAJO"
                delta = prev["price"] - offer["price"]
                delta_line = f"\nAntes: {format_cop(prev['price'])}  (-{format_cop(delta)})"
            elif offer["price"] > prev["price"]:
                emoji = "SUBIO"
                delta = offer["price"] - prev["price"]
                delta_line = f"\nAntes: {format_cop(prev['price'])}  (+{format_cop(delta)})"
            else:
                # sin cambio pero bajo umbral
                emoji = "OFERTA"
                delta_line = ""

            tag = " *POR DEBAJO DEL UMBRAL*" if below else ""
            msg = (
                f"*{emoji} {offer['store']}*{tag}\n"
                f"{escape_md(offer['title'])}\n"
                f"Precio: *{format_cop(offer['price'])}*"
                f"{delta_line}\n"
                f"{offer['url']}"
            )
            send_telegram(token, chat_id, msg)

        # Detecta productos que desaparecieron
        for key, prev in state.items():
            if key not in current:
                # Solo notificamos una vez: si ya no estaba antes, saltamos
                if prev.get("_stale"):
                    continue
                msg = (
                    f"*AGOTADO/RETIRADO {prev['store']}*\n"
                    f"{escape_md(prev['title'])}\n"
                    f"Ultimo precio visto: {format_cop(prev['price'])}\n"
                    f"{prev['url']}"
                )
                send_telegram(token, chat_id, msg)

    # Nuevo estado: current + marca stale a los desaparecidos que ya notificamos
    new_state = dict(current)
    for key, prev in state.items():
        if key not in current:
            prev["_stale"] = True
            new_state[key] = prev
    return new_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once", action="store_true",
        help="Corre un solo ciclo y sale (para cron/GitHub Actions).",
    )
    args = parser.parse_args()

    _setup_logging(also_stdout=args.once)
    config = load_config()
    state = load_state()
    first_run = not state

    if args.once:
        logging.info(
            "Modo --once. umbral=%s COP, first_run=%s",
            config.get("threshold_cop"), first_run,
        )
        state = run_once(config, state, first_run)
        save_state(state)
        logging.info("Ciclo completado, saliendo.")
        return

    interval = float(config.get("interval_hours", 3)) * 3600
    logging.info(
        "Watcher iniciado (loop). Intervalo=%ss, umbral=%s COP, first_run=%s",
        int(interval), config.get("threshold_cop"), first_run,
    )
    while True:
        try:
            state = run_once(config, state, first_run)
            save_state(state)
            first_run = False
        except Exception:
            logging.exception("run_once crash")
        logging.info(
            "Esperando %.1f h hasta la proxima corrida (%s)",
            interval / 3600,
            datetime.now().isoformat(timespec="seconds"),
        )
        time.sleep(interval)


if __name__ == "__main__":
    main()
