import requests
import json
import time
import re
from datetime import datetime
import os
import subprocess
import sys

# Windows console defaults to cp1252, which can't print emoji/Czech text and
# crashes the whole run. Force UTF-8 so print() never blows up mid-scrape.
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

class SrealityScraper:
    def __init__(self, enable_github_upload=True, github_repo_path=None):
        """Initialize scraper with localized Radial Search"""
        self.data_file = 'sreality_data.json'
        # Full-fidelity snapshot history (every field, every change). This can grow to
        # hundreds of MB, so it stays LOCAL ONLY - see .gitignore - and is never committed.
        self.history_file = 'sreality_history.json'
        # Lightweight price-only history (id/date/price). Small enough to commit and
        # push to GitHub on every run; this is what powers the public history page.
        self.price_history_file = 'sreality_price_history.json'
        self.alerts_file = 'sreality_alerts.html'
        self.catalog_file = 'sreality_all_properties.html'
        self.removed_file = 'sreality_removed_properties.html'
        self.history_html_file = 'sreality_property_history.html'
        self.images_folder = 'property_images'
        self.base_url = 'https://www.sreality.cz/api/cs/v2/estates'

        self.enable_github_upload = enable_github_upload
        self.github_repo_path = github_repo_path

        if not os.path.exists(self.images_folder):
            os.makedirs(self.images_folder)

    def load_previous_data(self):
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def load_history(self):
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def save_history(self, history):
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def load_price_history(self):
        try:
            with open(self.price_history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def save_price_history(self, price_history):
        with open(self.price_history_file, 'w', encoding='utf-8') as f:
            json.dump(price_history, f, ensure_ascii=False, indent=2)

    def save_data(self, data):
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def format_price(self, price):
        return f"{price:,.0f}".replace(",", " ") + " Kč"

    HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    def _extract_next_data(self, html):
        """Sreality is a Next.js app - every page embeds its fetched data as a
        __NEXT_DATA__ JSON blob. Their old REST API (api/cs/v2/estates) was
        retired at some point after 2026-05, so we read this instead."""
        idx = html.find('__NEXT_DATA__')
        if idx == -1:
            return None
        start = html.find('>', idx) + 1
        end = html.find('</script>', start)
        return json.loads(html[start:end])

    def _find_query(self, next_data, key):
        queries = next_data.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
        for q in queries:
            if q.get('queryKey', [None])[0] == key:
                return q.get('state', {}).get('data')
        return None

    def _detail_hrefs_by_id(self, html):
        """Search-result cards don't carry their canonical URL in the JSON data,
        only in the rendered <a href>. Map id -> href from the raw HTML."""
        hrefs = {}
        for m in re.finditer(r'id="estate-list-item-(\d+)"', html):
            prop_id, pos = m.group(1), m.end()
            href_m = re.search(r'href="(/detail/prodej/dum/rodinny/[^"]+)"', html[pos:pos + 3000])
            if href_m:
                hrefs[prop_id] = href_m.group(1)
        return hrefs

    def fetch_property_details(self, detail_url, property_id):
        try:
            response = requests.get(detail_url, headers=self.HEADERS, timeout=10)
            next_data = self._extract_next_data(response.text)
            estate = self._find_query(next_data, 'estate') or {}
            description = estate.get('description') or 'No description available'
            area = estate.get('estateArea', 'N/A')
            return description, area
        except Exception:
            return "Description not available", "N/A"

    def fetch_properties(self):
        """Localized search to Ostrava region (Moravian-Silesian Region) via the
        public search page's embedded data, filtered by price and usable area."""
        search_url = 'https://www.sreality.cz/hledani/prodej/domy/moravskoslezsky-kraj'
        params = {'cena-od': 4948302, 'cena-do': 21623887, 'plocha-od': 200}
        all_properties = {}

        print("\n--- Starting Search: Moravian-Silesian Region (houses for sale) ---")
        page = 1
        while True:
            try:
                params['strana'] = page
                response = requests.get(search_url, params=params, headers=self.HEADERS, timeout=15)
                next_data = self._extract_next_data(response.text)
                search_data = self._find_query(next_data, 'estatesSearch')
                results = (search_data or {}).get('results', [])
                if not results: break

                hrefs = self._detail_hrefs_by_id(response.text)

                for item in results:
                    prop_id = str(item['id'])
                    if prop_id in all_properties: continue
                    href = hrefs.get(prop_id)
                    if not href: continue
                    detail_url = f"https://www.sreality.cz{href}"

                    loc = item.get('locality') or {}
                    locality = ', '.join(filter(None, [loc.get('cityPart') or loc.get('city'), loc.get('district')]))

                    images = item.get('images') or [{}]
                    img_url = images[0].get('url')
                    if img_url and img_url.startswith('//'):
                        img_url = f"https:{img_url}"

                    description, area = self.fetch_property_details(detail_url, prop_id)
                    all_properties[prop_id] = {
                        'id': prop_id, 'name': item.get('name', 'N/A'),
                        'price': item.get('priceCzk', 0), 'locality': locality or 'N/A',
                        'url': detail_url,
                        'area': area, 'image_url': img_url,
                        'description': description, 'last_updated': datetime.now().isoformat()
                    }

                total = (search_data or {}).get('pagination', {}).get('total', 0)
                if page * len(results) >= total: break
                page += 1
                time.sleep(1)
            except Exception as e:
                print(f"Error fetching page {page}: {e}")
                break

        print(f"Total localized properties found: {len(all_properties)}")
        return all_properties

    def download_image(self, image_url, property_id):
        if not image_url: return None
        try:
            path = os.path.join(self.images_folder, f"{property_id}.jpg")
            # Web-facing path must always use forward slashes, regardless of OS.
            web_path = f"{self.images_folder}/{property_id}.jpg"
            if os.path.exists(path): return web_path
            r = requests.get(image_url, timeout=10)
            with open(path, 'wb') as f: f.write(r.content)
            return web_path
        except: return None

    def save_property_history_html(self, price_history, current_data):
        html = """<html><head><meta charset="utf-8">
        <style>
            body { font-family: 'Segoe UI', Tahoma; margin: 0; background: #f0f2f5; }
            .container { max-width: 900px; margin: 30px auto; padding: 0 15px; }
            .card { background: white; border-radius: 10px; display: flex; align-items: flex-start; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 25px; overflow: hidden; }
            .card-img { width: 300px; height: 220px; flex-shrink: 0; background-size: cover; background-position: center; background-color: #e0e0e0; }
            .card-body { flex: 1; padding: 20px; min-width: 0; }
            .card-body h2 { margin: 0 0 10px 0; font-size: 1.3rem; }
            .card-body a { color: #1a73e8; text-decoration: none; }
            .locality { color: #666; font-weight: bold; margin-bottom: 10px; }
            .desc { font-size: 0.85rem; color: #444; border-top: 1px solid #eee; padding-top: 10px; margin-top: 10px; }
            .history { margin-top: 15px; font-size: 0.8rem; background: #f8f9fa; padding: 10px; border-radius: 5px; }
        </style></head><body><div class="container"><h1>🏡 Ostrava Property History</h1>"""

        for p_id, p in current_data.items():
            img = self.download_image(p.get('image_url'), p_id)
            html += f"""
            <div class="card">
                <div class="card-img" style="background-image: url('{img if img else ''}');"></div>
                <div class="card-body">
                    <h2><a href="{p.get('url', '#')}" target="_blank">{p.get('name', 'N/A')}</a></h2>
                    <div class="locality">📍 {p.get('locality', 'N/A')}</div>
                    <div class="desc">{(p.get('description') or '')[:400]}...</div>
                    <div class="history"><b>Price History:</b><br>"""
            for s in reversed(price_history.get(p_id, [])):
                html += f"• {s['date']}: {self.format_price(s['price'])}<br>"
            html += "</div></div></div>"

        html += "</div></body></html>"
        with open(self.history_html_file, 'w', encoding='utf-8') as f: f.write(html)

    def upload_to_github(self):
        if not self.github_repo_path: return
        try:
            os.chdir(self.github_repo_path)
            subprocess.run(['git', 'add', '.'], check=True)
            msg = f"Update {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            subprocess.run(['git', 'commit', '-m', msg], check=True)
            subprocess.run(['git', 'push'], check=True)
            print("✅ GitHub update successful.")
        except Exception as e: print(f"❌ GitHub Error: {e}")

    def run_scraper(self):
        history = self.load_history()
        price_history = self.load_price_history()
        curr = self.fetch_properties()

        for p_id, p in curr.items():
            if p_id not in history: history[p_id] = []
            if not history[p_id] or history[p_id][-1]['price'] != p['price']:
                history[p_id].append(p)

            if p_id not in price_history: price_history[p_id] = []
            if not price_history[p_id] or price_history[p_id][-1]['price'] != p['price']:
                price_history[p_id].append({
                    'date': p['last_updated'][:16].replace('T', ' '),
                    'price': p['price']
                })

        self.save_data(curr)
        self.save_history(history)
        self.save_price_history(price_history)
        self.save_property_history_html(price_history, curr)
        if self.enable_github_upload: self.upload_to_github()

if __name__ == '__main__':
    REPO_PATH = r"C:\Users\Rancy\Desktop\sreality-tracker"
    scraper = SrealityScraper(enable_github_upload=True, github_repo_path=REPO_PATH)
    scraper.run_scraper()
