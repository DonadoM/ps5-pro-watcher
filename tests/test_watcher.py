import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
