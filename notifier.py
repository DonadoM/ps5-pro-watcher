import logging
import requests


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or token.startswith("PEGA_") or not chat_id or chat_id.startswith("PEGA_"):
        logging.warning("Telegram no configurado, saltando envio.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        if not r.ok:
            logging.error("Telegram HTTP %s: %s", r.status_code, r.text[:300])
            return False
        return True
    except Exception as e:
        logging.error("Telegram exception: %s", e)
        return False
