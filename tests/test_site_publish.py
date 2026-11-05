import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

class PagesPublicationTests(unittest.TestCase):
    def test_large_legacy_assets_not_published(self):
        config = (ROOT / "_config.yml").read_text(encoding="utf-8")
        self.assertIn("  - property_images", config)
        self.assertIn("  - sreality_alerts.html", config)
        for critical in ("  - assets", "  - data", "  - reports",
                         "  - index.html", "  - archive.html"):
            self.assertNotIn(critical + "\n", config)

    def test_schedule_is_local_and_does_not_skip_late_events(self):
        workflow = (ROOT / ".github" / "workflows" / "ostrava-market.yml").read_text(encoding="utf-8")
        self.assertIn("cron: '13 10 * * *'", workflow)
        self.assertIn("timezone: 'Europe/London'", workflow)
        self.assertIn("workflow_dispatch|push|schedule", workflow)
        self.assertNotIn("date +%H", workflow)

    def test_original_history_uses_git_archive_photo_urls(self):
        legacy = (ROOT / "sreality_property_history.html").read_text(encoding="utf-8")
        self.assertIn("https://raw.githubusercontent.com/TomKxxx/sreality-tracker/main/property_images/", legacy)
        self.assertNotIn("url('property_images/", legacy)

if __name__ == "__main__":
    unittest.main()
