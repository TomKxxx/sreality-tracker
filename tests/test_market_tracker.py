import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import market_tracker as m

class PropertyHistoryTests(unittest.TestCase):
    def row(self, price=12000000):
        return {
            "id":"sreality:123", "source":"sreality.cz", "source_id":"123",
            "market":"sale", "name":"Detached house 210 m², pozemek 650 m²",
            "locality":"Stará Bělá, Ostrava", "price":price,
            "usable_m2":210, "plot_m2":650,
            "url":"https://www.sreality.cz/detail/prodej/dum/rodinny/test/123",
            "image_url":"https://example.org/photo.jpg", "photo_path":"",
            "lat":49.80,"lon":18.20,"observed_at":"2026-11-02T10:00:00+01:00"
        }

    def test_photo_candidates_use_seznam_image_transform(self):
        original = "https://d18-a.sdn.cz/d_18/c_img_qG_D/abc/123.jpeg"
        options = m.photo_candidates(original)
        self.assertTrue(options[0].startswith(original + "?fl=res,400,300,3"))
        self.assertIn(original, options)
        self.assertEqual(m.photo_candidates("https://example.org/photo.jpg"),
                         ["https://example.org/photo.jpg"])

    def test_photo_access_denied_stops_further_retries(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            img = "https://d18-a.sdn.cz/d_18/c_img_qG_D/abc/123.jpeg"
            mocked = SimpleNamespace(status_code=403, headers={}, content=b"")
            with patch.object(m, "ROOT", Path(folder)), patch.dict(m.os.environ, {"ARCHIVE_IMAGES": "1"}), \
                 patch.object(m.SESSION, "get", return_value=mocked) as http:
                m.PHOTO_BLOCKED_HOSTS.clear()
                row = {**self.row(), "image_url": img}
                self.assertEqual(m.archive_photo(row, "sale"), "")
                self.assertEqual(m.archive_photo(row, "sale"), "")
                self.assertEqual(http.call_count, len(m.photo_candidates(img)))
                m.PHOTO_BLOCKED_HOSTS.clear()

    def test_radius(self):
        self.assertLess(m.distance_km(49.82,18.26),1)
        self.assertGreater(m.distance_km(50.2,19.2),15)

    def test_html_parser(self):
        payload={"props":{"pageProps":{"dehydratedState":{"queries":[{"queryKey":["estatesSearch"],"state":{"data":{"results":[{"id":123}],"pagination":{"total":1}}}}]}}}}
        page='<script id="__NEXT_DATA__" type="application/json">'+json.dumps(payload)+'</script>'
        self.assertEqual(m.unwrap_search(page)["results"][0]["id"],123)
        with self.assertRaises(RuntimeError):
            m.unwrap_search("<p>blocked</p>")

    def test_no_fictitious_result_outside_radius(self):
        rec={"id":123,"name":"House 200 m², pozemek 400 m²","priceCzk":10500000,"gps":{"lat":50.4,"lon":19.2}}
        self.assertIsNone(m.property_row(rec,"https://www.sreality.cz/example","sale","2026-11-02"))

    def test_new_cut_and_absent_are_not_sales(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(m,"DATA",Path(folder)), patch.object(m,"ROOT",Path(folder)), patch.object(m,"legacy_events",return_value=[]), patch.object(m,"archive_photo",return_value=""):
                a=m.update("sale",{"sreality:123":self.row()},"2026-11-02T10:00:00+01:00","2026-11-02")
                self.assertEqual(a["counts"]["new"],0)
                self.assertEqual(a["counts"]["discovered"],1)
                b=m.update("sale",{"sreality:123":self.row(11000000)},"2026-11-03T10:00:00+01:00","2026-11-03")
                self.assertEqual(b["counts"]["reduced"],1)
                self.assertEqual(b["changes"][0]["percent"],-8.33)
                m.update("sale",{"sreality:999":{**self.row(),"id":"sreality:999","source_id":"999"}},"2026-11-04T10:00:00+01:00","2026-11-04")
                stored=m.read_json(Path(folder)/"sale"/"history.json",{})
                self.assertEqual(stored["sreality:123"]["status"],"absent_unverified")
                self.assertNotEqual(stored["sreality:123"]["status"],"sold")

    def test_new_requires_previous_complete_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(m, "DATA", Path(folder)), patch.object(m, "ROOT", Path(folder)), \
                 patch.object(m, "legacy_events", return_value=[]), \
                 patch.object(m, "archive_photo", return_value=""):
                a = m.update("sale", {"sreality:123": self.row()},
                             "2026-11-02T10:00:00+01:00", "2026-11-02")
                self.assertEqual(a["counts"]["discovered"], 1)
                second = {**self.row(), "id": "sreality:124", "source_id": "124",
                          "url": "https://www.sreality.cz/detail/prodej/dum/rodinny/another-house/124"}
                b = m.update("sale", {"sreality:123": self.row(), "sreality:124": second},
                             "2026-11-03T10:00:00+01:00", "2026-11-03")
                self.assertEqual(b["counts"]["new"], 1)
                self.assertEqual(b["counts"]["discovered"], 0)
                self.assertEqual(b["classification_version"], 2)

    def test_secondary_new_to_crawler_not_new_to_market(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(m, "DATA", Path(folder)), patch.object(m, "ROOT", Path(folder)), \
                 patch.object(m, "legacy_events", return_value=[]), \
                 patch.object(m, "archive_photo", return_value=""):
                m.update("sale", {"sreality:123": self.row()},
                         "2026-11-02T10:00:00+01:00", "2026-11-02")
                extra = {**self.row(), "id": "idnes:abc", "source_id": "abc",
                         "source": "Reality iDNES",
                         "url": "https://reality.idnes.cz/detail/prodej/dum/abc/"}
                b = m.update("sale", {"sreality:123": self.row(), "idnes:abc": extra},
                             "2026-11-03T10:00:00+01:00", "2026-11-03")
                self.assertEqual(b["counts"]["new"], 0)
                self.assertEqual(b["counts"]["discovered"], 1)
                self.assertEqual(b["changes"][0]["kind"], "discovered")

    def test_legacy_sreality_listing_is_not_new(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy = [{"at": "2026-05-10", "price": 12000000, "source": "legacy"}]
            with patch.object(m, "DATA", Path(folder)), patch.object(m, "ROOT", Path(folder)), \
                 patch.object(m, "legacy_events", return_value=legacy), \
                 patch.object(m, "archive_photo", return_value=""):
                a = m.update("sale", {"sreality:123": self.row()},
                             "2026-11-02T10:00:00+01:00", "2026-11-02")
                self.assertEqual(a["counts"]["historical_return"], 1)
                self.assertEqual(a["counts"]["new"], 0)

    def test_previous_crosslist_alias_prevents_false_new(self):
        prior = {"sreality:old": {
            "id": "sreality:old", "market": "sale",
            "url": "https://www.sreality.cz/detail/prodej/dum/rodinny/x/old",
            "sources": [{"id": "idnes:abc",
                         "url": "https://reality.idnes.cz/detail/prodej/dum/abc/"}]
        }}
        aliases = m.prior_listing_aliases(prior)
        self.assertEqual(aliases["id:idnes:abc"], "sreality:old")
        self.assertEqual(
            aliases["url:reality.idnes.cz/detail/prodej/dum/abc"],
            "sreality:old")

    def test_second_same_day_scan_preserves_prior_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(m, "DATA", Path(folder)), patch.object(m, "ROOT", Path(folder)), \
                 patch.object(m, "legacy_events", return_value=[]), \
                 patch.object(m, "archive_photo", return_value=""):
                m.update("sale", {"sreality:123": self.row()},
                         "2026-11-02T10:00:00+01:00", "2026-11-02")
                m.update("sale", {"sreality:123": self.row()},
                         "2026-11-02T11:00:00+01:00", "2026-11-02")
                snapshots = list((Path(folder) / "sale" / "snapshots").glob("*.json"))
                self.assertEqual(len(snapshots), 2)
                prior = m.read_json(Path(folder) / "sale" / "snapshots" /
                                    "2026-11-02_2026-11-02T10-00-00_01-00.json", {})
                self.assertEqual(prior["observed_at"], "2026-11-02T10:00:00+01:00")

if __name__=="__main__":
    unittest.main()
