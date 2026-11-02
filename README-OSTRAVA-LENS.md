# Ostrava Property Lens — GitHub Pages market tracker

A public dashboard for **sale and rental house listings** in and around Ostrava, with separate daily reports and cumulative asking-price history.

## Pages

- \`index.html\` — modern mobile-friendly market dashboard, tabbed sale/rent, searchable with price, area, status, and change filters.
- \`archive.html\` — searchable legacy archive from the user's existing price-history file (location not always verified).
- \`reports/sale/latest.html\` and \`reports/rent/latest.html\` — two separate daily reports created by the first successful scan.
- \`data/sale/history.csv\` and \`data/rent/history.csv\` — separate cumulative observations, one line per recorded asking-price event.
- \`data/<market>/daily/<YYYY-MM-DD>.json\` — dated daily change snapshots.
- \`data/<market>/history.json\` and \`current.json\` — cumulative status and last complete scan.
- \`data/legacy_sale_index.json\` — compact archive index built from the original \`sreality_price_history.json\`, without modifying the original.

## Daily schedule

\`.github/workflows/ostrava-market.yml\` runs via GitHub Actions at a target of **10:00 Europe/London**, whether the owner's laptop is on or off. UTC 09:00/10:00 schedule slots with a UK local-time guard account for BST/GMT. GitHub may delay scheduled workflows.

The workflow runs \`python -m unittest discover -s tests -v\`, then \`python market_tracker.py --market both\` and commits successful observation snapshots and reports. If the source fails, the previous snapshot is retained and a dated error record is written. Both categories are tracked separately.

## Critical limitations

1. **An absent advert is not a completed sale or letting.** Absences are labelled \`absent_unverified\`; genuine completed transactions require independent confirmation, for example cadastre evidence.
2. **These are asking prices**, not sale-price indices or transaction figures.
3. **The 15 km search is based on Sreality's 15 km query.** Records with exact coordinates are verified; those without coordinates are flagged as radius-unverified.
4. **Sreality integration is unofficial.** The site may block automated access, change its Next.js embedded search layout or limit image fetches. Check source usage and scraping terms before relying on continuous data.
5. **Photos:** the tracker keeps source photo URLs and any already archived property images. Automatic downloading/re-publication of third-party marketing photographs is disabled by default (\`ARCHIVE_IMAGES=0\`) pending rights to do so. The dashboard shows listing photos from their source if allowed and available; image disappearance cannot be prevented without suitable rights.
6. **Legacy data:** original raw files remain untouched. Legacy pricing history is attached when an old listing ID reappears in a currently verified scan. All other old IDs remain in a labelled archive (outside-radius status unknown), not counted in current Ostrava inventory.
7. **Coverage:** current GitHub Actions collector scans Sreality only; it does not falsely claim multi-agency complete market coverage. Other portals can be incorporated later with stable-source identifiers and deduplication.
8. **Rentals:** base advertised monthly rent is recorded, but extra charges/utilities and deposits cannot be reliably inferred from search cards and are not set to zero.
9. **First run required:** until the first successful GitHub Actions scan, the new dashboard displays “awaiting scan” rather than invented current inventory.

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
