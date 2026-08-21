"""Diagnostica la conexion a Telegram. Muestra la respuesta cruda de la API."""
import json
import requests
from pathlib import Path

cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
token = cfg["telegram_bot_token"]
chat_id = cfg["telegram_chat_id"]

print(f"Token (primeros 10 chars): {token[:10]}...")
print(f"Chat ID: {chat_id}")
print()

if token.startswith("PEGA_") or chat_id.startswith("PEGA_"):
    print("ERROR: todavia hay placeholders en config.json. Edita el archivo.")
    raise SystemExit(1)

print("== 1. Verificando el bot con getMe ==")
r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
print(f"HTTP {r.status_code}")
print(r.text)
print()

print("== 2. Enviando mensaje de prueba ==")
r = requests.post(
    f"https://api.telegram.org/bot{token}/sendMessage",
    json={"chat_id": chat_id, "text": "Prueba desde diag_telegram.py"},
    timeout=15,
)
print(f"HTTP {r.status_code}")
print(r.text)
