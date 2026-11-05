# Ostrava Property Lens — GitHub Pages market tracker

A public dashboard for **sale and rental house listings** in and around Ostrava, with separate daily reports and cumulative asking-price history. The primary source is Sreality; secondary sources are **partial, best-effort monitoring** of other portals and estate agents, not a guarantee of every advert.

## Pages

- \`index.html\` — modern mobile-friendly market dashboard, tabbed sale/rent, searchable with price, area, status, and change filters.
- \`archive.html\` — searchable legacy archive from the user's existing price-history file (location not always verified).
- \`reports/sale/latest.html\` and \`reports/rent/latest.html\` — two separate daily reports created by the first successful scan.
- \`data/sale/history.csv\` and \`data/rent/history.csv\` — separate cumulative observations, one line per recorded asking-price event.
- \`data/<market>/daily/<YYYY-MM-DD>.json\` — dated daily change snapshots.
- \`data/<market>/history.json\` and \`current.json\` — cumulative status and last complete scan.
- \`data/legacy_sale_index.json\` — compact archive index built from the original \`sreality_price_history.json\`, without modifying the original.

## Daily schedule

\`.github/workflows/ostrava-market.yml\` runs via GitHub Actions at **10:13 Europe/London**, whether the owner's laptop is on or off. GitHub's native IANA timezone schedule handles BST/GMT. The 13-minute offset avoids top-of-hour congestion; a delayed event still runs rather than being silently skipped by an hour-of-day gate.

The workflow runs \`python -m unittest discover -s tests -v\`, then \`python market_tracker.py --market both\` with \`EXTRA_SOURCES=1\` and \`ARCHIVE_IMAGES=1\`, and commits successful observation snapshots and reports. If the source fails, the previous snapshot is retained and a dated error record is written. Both categories are tracked separately.

## Critical limitations

1. **An absent advert is not a completed sale or letting.** Absences are labelled \`absent_unverified\`; genuine completed transactions require independent confirmation, for example cadastre evidence.
2. **These are asking prices**, not sale-price indices or transaction figures.
3. **The 15 km search is based on Sreality's 15 km query.** Records with exact coordinates are verified; those without coordinates are flagged as radius-unverified.
4. **Sreality integration is unofficial.** The site may block automated access, change its Next.js embedded search layout or limit image fetches. Check source usage and scraping terms before relying on continuous data.
5. **Photos:** the GitHub Actions job enables \`ARCHIVE_IMAGES=1\` to attempt archiving actual card photos in \`assets/photos/<market>/\`. Existing photos are retained after listing disappearance. Download failures are non-fatal and original source images can be blocked; publication of third-party marketing photographs may require a licence or permission.
6. **Legacy data:** original raw files remain untouched. Legacy pricing history is attached when an old listing ID reappears in a currently verified scan. All other old IDs remain in a labelled archive (outside-radius status unknown), not counted in current Ostrava inventory.
7. **Coverage:** Sreality is the primary radius query. Additional discovery checks Reality iDNES, Bezrealitky, M&M Reality, RE/MAX, RealityMIX, ČeskéReality, TRIMEX Reality and Bazoš. These are partial search-card samples. Each source's status appears in dashboard **Source coverage**, including access denied, robots excluded, or no parseable cards. Broader portals and agents may include listings outside the radius until independently verified. Missing adverts on partial sources are NEVER classified as sold or withdrawn; after eight days without re-observation, they are displayed as stale/unverified in history. Strict cross-source matching merges only confident duplicates; others may remain separate.
8. **Rentals:** base advertised monthly rent is recorded, but extra charges/utilities and deposits cannot be reliably inferred from search cards and are not set to zero.
9. **First run required:** until the first successful GitHub Actions scan, the new dashboard displays “awaiting scan” rather than invented current inventory.

## Cover-photo quality

The scanner prefers property photographs to estate-agent headshots. When archiving is enabled, it:
1. Skips known agent/portrait/image-logo URLs from search results.
2. Checks saved photos with a local OpenCV frontal-face classifier; only a *prominent* face triggers replacement, not a small person in a garden.
3. Looks for a high-confidence matched listing at another source (same asking price, usable area and two distinctive street/location tokens) and reuses an already archived house photograph.
4. Checks the original advert's gallery if necessary, subject to robots.txt permissions and a per-run request limit.
5. Clears the display image when no appropriate replacement is available instead of displaying a headshot.

The original listing link is retained. When another source supplies the photo, the metadata includes `photo_source_listing`. Face detection is conservative and can occasionally miss a profile/obscured face or reject a property image; the archive is not a person-identification system. Photo-quality decisions are cached by image hash in `data/photo_quality_cache.json` to avoid repeat work.

## 5 November 2026 deployment and notification reliability

The GitHub Action is the **single source of truth** for fresh observations; ChatGPT's
scheduled email should read the **completed** GitHub run and the corresponding
`data/sale/latest.json` and `data/rent/latest.json`, rather than independently
scraping portals at the same time. The GitHub scan is targeted for 10:13
Europe/London. Email reporting occurs later, to allow the scan to finish.
If GitHub is late or has not committed fresh Prague-dated results, the email
must say *pending/unverified*, not report yesterday's counts as today's.

GitHub Pages was timing out while publishing a large legacy site artifact.
`_config.yml` excludes the old `property_images/` directory (over 200 MB)
and `sreality_alerts.html` (over 80 MB) **from website publication only**.
Neither is removed from Git, so historical source files and pictures remain
available in the repository; historic embedded photo links in
`sreality_property_history.html` use raw.githubusercontent.com instead.
The current site still publishes separate daily sale/rent reports, history CSVs,
JSON snapshots and `assets/photos/sale` and `assets/photos/rent`.

The scan workflow explicitly verifies both latest reports, dated snapshots and
independent history exports before declaring a run successful. Source outages,
blocked images and secondary-portal incomplete coverage remain explicitly
unverified; the code cannot guarantee that external sites will never fail.

## Same-day reruns

Each complete scan writes an immutable timestamped JSON snapshot in
`data/<market>/snapshots/`. The latest/daily report now **accumulates all
changes from that Prague calendar day**, including archived observations
from earlier runs, rather than replacing daily counts with zero on a second
scan. The `scan_counts` field records only the newest scan's incremental
changes; `counts` and `changes` describe the full day. When a previous
latest report was accidentally overwritten, the earlier archived snapshots
are used to restore that day's activity. This works independently for
sale and rental records.

## Administration

After merging to \`main\`:
1. Check **Settings → Actions → General → Workflow permissions**. GitHub Actions must be allowed to write repository contents.
2. In the **Actions** tab, open **Ostrava sale & rent market history** and use **Run workflow** once on \`main\`.
3. Check the workflow output and verify that \`data/sale/latest.json\`, \`data/rent/latest.json\`, and both report HTML files exist.
4. Confirm GitHub Pages publishes from \`main\` / root. Public address is \`https://tomkxxx.github.io/sreality-tracker/\`.
5. Do not treat an inaccessible data source as a market collapse. Review failed scan logs and keep previous verified records.

## Technical checks

- \`python -m py_compile market_tracker.py\`
- \`python -m unittest discover -s tests -v\`
- Optional offline fixture: \`python market_tracker.py --test-fixture local-fixture.json\` (will write data and reports; use only in a separate test checkout).

This upgrade does not delete, modify or misclassify the original \`sreality_price_history.json\`, \`sreality_data.json\`, or historic HTML pages.
