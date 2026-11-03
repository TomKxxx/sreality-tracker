import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import photo_quality as q
import market_tracker as m


class CoverImageTests(unittest.TestCase):
    def test_identify_agent_photo_urls(self):
        self.assertTrue(q.suspect_url(
            "https://st.realitymix.cz/i/66676326/makleri/makler_1881111.jpg"))
        self.assertTrue(q.suspect_url(
            "https://example.cz/photos/agent/profile.jpg"))
        self.assertFalse(q.suspect_url(
            "https://d18-a.sdn.cz/d_18/c_img_qH_A/path/32da.jpeg"))

    @staticmethod
    def sample():
        realitymix = {
            "id": "realitymix:8721059", "source": "RealityMIX",
            "source_id": "8721059", "market": "rent",
            "price": 47000, "usable_m2": 214, "plot_m2": None,
            "url": ("https://realitymix.cz/detail/ostrava/"
                    "pronajem-bytu-4-kk-v-rodinnem-dome-vcetne-2-parkovacich-"
                    "stani-ulice-hrabek-ostrava-plesna-8721059.html"),
            "image_url": "https://st.realitymix.cz/i/66676326/makleri/makler_1881111.jpg",
            "photo_path": "assets/photos/rent/realitymix-8721059.jpg",
        }
        sreality = {
            "id": "sreality:3550220364", "source": "sreality.cz",
            "source_id": "3550220364", "market": "rent",
            "price": 47000, "usable_m2": 214, "plot_m2": 100,
            "url": ("https://www.sreality.cz/detail/pronajem/dum/rodinny/"
                    "ostrava-plesna-hrabek/3550220364"),
            "image_url": "https://d18-a.sdn.cz/d_18/c_img_qH_A/nQO0xPd86TBgJSgC4UrIP4MSe/a95f.jpeg",
            "photo_path": "assets/photos/rent/3550220364.jpg",
        }
        return realitymix,sreality

    def test_real_duplicate_property_photo_is_recognised(self):
        a,b = self.sample()
        self.assertTrue(q.same_house_for_photo(a,b))
        self.assertIn("hrabek",q.tokens(a))
        self.assertIn("plesna",q.tokens(b))
        self.assertFalse(q.same_house_for_photo(a, {**b,"price":48000}))
        self.assertFalse(q.same_house_for_photo(a, {**b,"usable_m2":240}))

    def test_agent_cover_replaced_from_archived_sreality_property(self):
        a,b = self.sample()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for x in (a,b):
                p=root/x["photo_path"]
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_bytes(b"fake image fixture")
            with patch.object(m,"ROOT",root), \
                 patch.object(q,"looks_like_portrait",
                              side_effect=lambda p:"realitymix-" in str(p)):
                result=m.choose_property_photo(a,"rent",a["photo_path"],
                                               {a["id"]:a,b["id"]:b},{})
            self.assertEqual(result,b["photo_path"])
            self.assertEqual(a["photo_source_listing"],b["id"])
            self.assertEqual(a["image_url"],b["image_url"])

    def test_gallery_excludes_agent_portrait_and_keeps_house(self):
        html = '''<html><head>
          <meta property="og:image" content="https://st.realitymix.cz/i/11/makleri/makler_22.jpg"/>
        </head><body><main>
        <img src="https://st.realitymix.cz/i/11/foto/fasada_11.jpg"/>
        <img src="https://st.realitymix.cz/i/11/makleri/makler_22.jpg"/>
        </main></body></html>'''
        images=q.image_sources_from_html(html,"https://realitymix.cz/detail/example")
        self.assertEqual(images,["https://st.realitymix.cz/i/11/foto/fasada_11.jpg"])


if __name__ == "__main__":
    unittest.main()
