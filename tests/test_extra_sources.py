import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import extra_sources as x
import market_tracker as m


class ExtraPortalTests(unittest.TestCase):
    TS = "2026-11-03T12:00:00+01:00"

    def source(self, key):
        return next(s for s in x.SOURCES if s.key == key)

    def test_idnes_sale_search_card(self):
        html = """<article><a href="/detail/prodej/dum/ostrava-slezska-ostrava/6a419f80bb6d76016e09c598/">
        Prodej domu 210 m² s pozemkem 650 m² Polní, Ostrava - Slezská Ostrava 12 500 000 Kč
        </a></article>"""
        rows = x.parse_cards(html, self.source("idnes"), "sale", self.TS,
                             "https://reality.idnes.cz/s/prodej/domy/ostrava/")
        self.assertEqual(len(rows), 1)
        row = next(iter(rows.values()))
        self.assertEqual(row["price"], 12500000)
        self.assertEqual(row["usable_m2"], 210)
        self.assertEqual(row["plot_m2"], 650)
        self.assertEqual(row["source"], "Reality iDNES")
        self.assertTrue(row["radius_unverified"])

    def test_bezrealitky_sale_with_price_in_parent(self):
        html = """<article><h3><a href="/nemovitosti-byty-domy/1051022-nabidka-prodej-domu-polni-ostrava">
        Prodej domu Polní, Ostrava - Stará Bělá</a></h3>
        <div>130 m²</div><p>9 300 000 Kč</p></article>"""
        rows = x.parse_cards(html, self.source("bezrealitky"), "sale", self.TS,
                             "https://www.bezrealitky.cz/vypis/nabidka-prodej/dum/ostrava")
        self.assertEqual(len(rows), 1)
        self.assertEqual(next(iter(rows.values()))["price"], 9300000)

    def test_unknown_location_or_unknown_price_excluded(self):
        html = """<article><a href="/nemovitosti/955842/">
        Prodej rodinného domu, 160 m², Brno, ul. Hlavní 6 000 000 Kč</a></article>
        <article><a href="/nemovitosti/955843/">Prodej domu 200 m² Ostrava cena v RK</a></article>"""
        rows = x.parse_cards(html, self.source("mmreality"), "sale", self.TS,
                             "https://www.mmreality.cz/nemovitosti/prodej/rodinne-domy/ostrava-mesto/")
        self.assertEqual(rows, {})

    def test_rental_not_sale_at_remax(self):
        html = """<article><a href="/reality/detail/pronajem-domu/306-NP83777/">
        Pronájem domu 129 m², Ostrava Hrabek 42 000 Kč</a></article>"""
        sale = x.parse_cards(html, self.source("remax"), "sale", self.TS,
                             "https://www.remax-czech.cz/reality/domy-a-vily/prodej/moravskoslezky-kraj/ostrava/")
        rent = x.parse_cards(html, self.source("remax"), "rent", self.TS,
                             "https://www.remax-czech.cz/reality/domy-a-vily/pronajem/moravskoslezky-kraj/ostrava/")
        self.assertFalse(sale)
        self.assertEqual(len(rent), 1)

    def test_conservative_dedupe_keeps_different_houses(self):
        a = {"id": "sreality:111", "market": "sale", "source": "sreality.cz",
             "url": "https://www.sreality.cz/detail/prodej/dum/rodinny/ostrava-polni/111",
             "locality": "Ostrava", "street": "Polní",
             "usable_m2": 130, "plot_m2": 760}
        b = {**a, "id": "idnes:222", "source": "Reality iDNES",
             "url": "https://reality.idnes.cz/detail/prodej/dum/ostrava/aaa/",
             "usable_m2": 131}
        self.assertTrue(x.strict_duplicate(a, b))
        same = x.dedupe_incoming({a["id"]: a, b["id"]: b})
        self.assertEqual(len(same), 1)
        self.assertEqual(len(next(iter(same.values()))["sources"]), 2)
        c = {**b, "plot_m2": 900}
        self.assertEqual(len(x.dedupe_incoming({a["id"]: a, c["id"]: c})), 2)

    def test_secondary_source_missing_not_marked_sold(self):
        original = {
            "id": "idnes:abc", "source": "Reality iDNES", "source_id": "abc",
            "market": "sale", "name": "Prodej domu 180 m² Ostrava",
            "locality": "Ostrava", "price": 9000000, "usable_m2": 180,
            "plot_m2": 500, "url": "https://reality.idnes.cz/detail/prodej/dum/ostrava/abc/",
            "image_url": "", "photo_path": "", "observed_at": self.TS
        }
        baseline = {**original, "id": "sreality:99", "source": "sreality.cz",
                    "source_id": "99"}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(m, "ROOT", Path(folder)), patch.object(m, "DATA", Path(folder)), \
                 patch.object(m, "legacy_events", return_value=[]), patch.object(m,"archive_photo",return_value=""):
                m.update("sale", {original["id"]: original, baseline["id"]: baseline},
                         self.TS, "2026-11-03")
                m.update("sale", {baseline["id"]: baseline},
                         "2026-11-04T12:00:00+01:00", "2026-11-04")
                record = m.read_json(Path(folder)/"sale"/"history.json",{})[original["id"]]
                self.assertNotIn(record["status"], ("absent_unverified", "sold"))
