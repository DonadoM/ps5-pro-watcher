"""
Historial de precios en data/history.csv.
Solo se escribe una fila cuando un producto cambia (precio, stock o aparece/desaparece),
asi el archivo crece poco y cada fila es un evento real.
"""
import csv
from pathlib import Path

BASE = Path(__file__).resolve().parent
HISTORY_FILE = BASE / "data" / "history.csv"
FIELDS = ["timestamp", "store", "product_id", "title", "price", "in_stock"]


def make_event(timestamp, key, offer, in_stock=None):
    store, product_id = key.split("::", 1)
    return {
        "timestamp": timestamp,
        "store": store,
        "product_id": product_id,
        "title": offer["title"],
        "price": offer["price"],
        "in_stock": int(offer.get("in_stock", True) if in_stock is None else in_stock),
    }


def append_events(events, path=HISTORY_FILE):
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerows(events)


def read_events(path=HISTORY_FILE):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [
            {**row, "price": int(row["price"]), "in_stock": row["in_stock"] == "1"}
            for row in csv.DictReader(f)
        ]
