import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stores  # noqa: E402
import watcher  # noqa: E402
from stores import is_ps5_pro_console  # noqa: E402

CONFIG = {"search_terms": ["ps5 pro"], "threshold_cop": 0, "stores_enabled": {}}
NOW = "2026-10-05T00:00:00+00:00"


def offer(price=4_599_900, in_stock=True):
    return {"id": "1", "title": "Consola PS5 Pro", "price": price,
            "url": "https://example.com/p", "in_stock": in_stock}


def run(state, offers_by_store):
    """Corre run_once con tiendas falsas. offers_by_store: nombre -> lista o Exception."""
    def fake(result):
        def fetch(_terms, direct_urls=None):
            if isinstance(result, Exception):
                raise result
            return result
        return fetch

    stores = {name: fake(r) for name, r in offers_by_store.items()}
    sent = []
    with mock.patch.object(watcher, "ALL_STORES", stores):
        new_state, events = watcher.run_once(CONFIG, state, sent.append, NOW)
    return new_state, events, sent


class RunOnceTest(unittest.TestCase):
    def setUp(self):
        self.state, _, _ = run({}, {"Exito": [offer()], "Falabella": [{**offer(), "id": "2"}]})

    def test_first_run_sends_summary_and_records_history(self):
        _, events, sent = run({}, {"Exito": [offer()]})
        self.assertEqual(len(sent), 1)
        self.assertIn("watcher iniciado", sent[0])
        self.assertEqual(len(events), 1)

    def test_single_miss_does_not_alert(self):
        state, events, sent = run(self.state, {"Exito": [], "Falabella": [{**offer(), "id": "2"}]})
        # Exito devolvio 0 cuando antes tenia ofertas -> se trata como fallo, sin alertas
        self.assertEqual(sent, [])
        self.assertEqual(events, [])
        self.assertNotIn("_misses", state["Exito::1"])

    def test_product_missing_twice_alerts_once(self):
        other = {**offer(), "id": "9"}  # la tienda responde, pero sin el producto 1
        state, _, sent = run(self.state, {"Exito": [other], "Falabella": [{**offer(), "id": "2"}]})
        self.assertFalse([m for m in sent if "AGOTADO/RETIRADO" in m])
        self.assertEqual(state["Exito::1"]["_misses"], 1)

        state, events, sent = run(state, {"Exito": [other], "Falabella": [{**offer(), "id": "2"}]})
        self.assertEqual(len([m for m in sent if "AGOTADO/RETIRADO" in m]), 1)
        self.assertTrue(state["Exito::1"]["_stale"])
        self.assertEqual([(e["product_id"], e["in_stock"]) for e in events], [("1", 0)])

        _, _, sent = run(state, {"Exito": [other], "Falabella": [{**offer(), "id": "2"}]})
        self.assertFalse([m for m in sent if "AGOTADO/RETIRADO" in m])

    def test_product_back_after_one_miss_is_silent(self):
        other = {**offer(), "id": "9"}
        state, _, _ = run(self.state, {"Exito": [other], "Falabella": [{**offer(), "id": "2"}]})
        state, _, sent = run(state, {"Exito": [other, offer()], "Falabella": [{**offer(), "id": "2"}]})
        self.assertEqual(sent, [])
        self.assertNotIn("_misses", state["Exito::1"])

    def test_store_exception_keeps_previous_state(self):
        state, _, sent = run(self.state, {"Exito": RuntimeError("bloqueado"),
                                          "Falabella": [{**offer(), "id": "2"}]})
        self.assertEqual(sent, [])
        self.assertEqual(state["Exito::1"], self.state["Exito::1"])

    def test_price_drop_alerts_and_records_history(self):
        _, events, sent = run(self.state, {"Exito": [offer(4_499_900)],
                                           "Falabella": [{**offer(), "id": "2"}]})
        self.assertEqual(len(sent), 1)
        self.assertIn("BAJO", sent[0])
        self.assertEqual([e["price"] for e in events], [4_499_900])


class MinChangeTest(unittest.TestCase):
    def setUp(self):
        self.falabella = {**offer(), "id": "2"}
        self.state, _, _ = run({}, {"Exito": [offer(5_000_000)], "Falabella": [self.falabella]})

    def step(self, price):
        self.state, events, sent = run(self.state, {"Exito": [offer(price)],
                                                    "Falabella": [self.falabella]})
        return events, sent

    def test_tiny_change_is_recorded_but_not_alerted(self):
        events, sent = self.step(4_999_950)
        self.assertEqual(sent, [])
        self.assertEqual([e["price"] for e in events], [4_999_950])

    def test_slow_drift_alerts_once_it_adds_up(self):
        for price in (4_980_000, 4_960_000):  # -0,4% y -0,8% acumulado
            self.assertEqual(self.step(price)[1], [])
        _, sent = self.step(4_940_000)  # -1,2% contra el ultimo precio avisado
        self.assertEqual(len(sent), 1)
        self.assertIn("Antes: $5.000.000", sent[0])

    def test_threshold_crossing_alerts_once(self):
        with mock.patch.dict(CONFIG, {"threshold_cop": 4_990_000}):
            self.assertEqual(len(self.step(4_989_000)[1]), 1)
            self.assertEqual(self.step(4_988_500)[1], [])


def product_page(offers):
    data = {"@type": "Product", "name": "Consola PS5 Pro", "offers": offers}
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


class ProductPageTest(unittest.TestCase):
    def test_picks_cheapest_in_stock_offer(self):
        html = product_page([
            {"price": 4_799_800, "availability": "https://schema.org/InStock"},
            {"price": 4_199_900, "availability": "https://schema.org/OutOfStock"},
            {"price": 4_798_136, "availability": "https://schema.org/InStock"},
            {"price": 0, "availability": "https://schema.org/OutOfStock"},
        ])
        self.assertEqual(stores._extract_price_from_html(html), ("Consola PS5 Pro", 4_798_136, True))

    def test_all_out_of_stock_returns_cheapest(self):
        html = product_page([
            {"price": "4.599.900", "availability": "https://schema.org/OutOfStock"},
            {"price": 4_299_900, "availability": "https://schema.org/OutOfStock"},
        ])
        self.assertEqual(stores._extract_price_from_html(html), ("Consola PS5 Pro", 4_299_900, False))

    def test_single_offer_object(self):
        html = product_page({"price": "4199900", "availability": "https://schema.org/InStock"})
        self.assertEqual(stores._extract_price_from_html(html), ("Consola PS5 Pro", 4_199_900, True))

    def test_exito_keeps_product_id_from_url(self):
        def fake_get(url, **_kwargs):
            return mock.Mock(text=product_page({"price": 1, "availability": "InStock"}),
                             raise_for_status=lambda: None)

        urls = ["https://www.exito.com/consola-ps5-pro-blanca-104569527-mp/p",
                "https://www.exito.com/consola-ps5-pro-2-tb-blanco-3192604/p"]
        with mock.patch.object(stores.requests, "get", fake_get), \
                mock.patch.object(stores, "REQUEST_DELAY_S", 0):
            ids = [o["id"] for o in stores.fetch_exito([], direct_urls=urls)]
        self.assertEqual(ids, ["104569527", "3192604"])


class ConsoleFilterTest(unittest.TestCase):
    def test_accepts_consoles(self):
        for title in ["Consola PS5 Pro 2TB", "Consola PlayStation 5 Pro Digital + 2 Controles"]:
            self.assertTrue(is_ps5_pro_console(title), title)

    def test_rejects_accessories_and_other_models(self):
        for title in ["Audifonos Pulse para PS5 Pro", "Consola PS5 Slim", "Base vertical consola PS5 Pro",
                      "Juego Tony Hawk Pro Skater PS5"]:
            self.assertFalse(is_ps5_pro_console(title), title)


if __name__ == "__main__":
    unittest.main()
