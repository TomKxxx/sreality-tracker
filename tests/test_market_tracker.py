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
                self.assertEqual(a["counts"]["new"],1)
                b=m.update("sale",{"sreality:123":self.row(11000000)},"2026-11-03T10:00:00+01:00","2026-11-03")
                self.assertEqual(b["counts"]["reduced"],1)
                self.assertEqual(b["changes"][0]["percent"],-8.33)
                m.update("sale",{"sreality:999":{**self.row(),"id":"sreality:999","source_id":"999"}},"2026-11-04T10:00:00+01:00","2026-11-04")
                stored=m.read_json(Path(folder)/"sale"/"history.json",{})
                self.assertEqual(stored["sreality:123"]["status"],"absent_unverified")
                self.assertNotEqual(stored["sreality:123"]["status"],"sold")
if __name__=="__main__":
    unittest.main()
