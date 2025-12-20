import requests
import json
import time
from datetime import datetime
import os
import subprocess

class SrealityScraper:
    def __init__(self, enable_github_upload=True, github_repo_path=None):
        """Initialize scraper with Radial Search and GitHub Sync"""
        self.data_file = 'sreality_data.json'
        self.history_file = 'sreality_history.json'
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
    
    def save_data(self, data):
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def format_price(self, price):
        return f"{price:,.0f}".replace(",", " ") + " Kč"

    def fetch_property_details(self, property_url, property_id):
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            api_url = f"https://www.sreality.cz/api/cs/v2/estates/{property_id}"
            response = requests.get(api_url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get('text', {}).get('value', 'No description available')
        except Exception:
            return "Description not available"
    
    def fetch_properties(self):
        """
        Performs a 10km Radial Search around Ostrava center.
        This captures everything inside the circle, even if the district is 'N/A'.
        """
        params = {
            'category_main_cb': 2,        # Houses
            'category_type_cb': 1,        # For Sale
            'price_from': 4948302,
            'price_to': 21623887,
            'usable_area_from': 200,
            'locality_region_id': 10,     # Required for GPS distance searches
            'lat': 49.8209228,            # Ostrava Center Lat
            'lon': 18.2625243,            # Ostrava Center Lon
            'distance': 15000,            # 15km radius in meters
            'per_page': 60
        }
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        all_properties = {}
        
        print("\n--- Starting 10km Radial Search around Ostrava ---")
        page = 1
        while True:
            try:
                params['page'] = page
                print(f"  > Fetching page {page}...")
                response = requests.get(self.base_url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
                estates = data.get('_embedded', {}).get('estates', [])
                if not estates: break
                
                for item in estates:
                    prop_id = str(item['hash_id'])
                    if prop_id not in all_properties:
                        img_url = item.get('_links', {}).get('images', [{}])[0].get('href')
                        description = self.fetch_property_details(item['seo']['locality'], prop_id)
                        all_properties[prop_id] = {
                            'id': prop_id,
                            'name': item.get('name', 'N/A'),
                            'price': item.get('price', 0),
                            'locality': item.get('locality', 'N/A'),
                            'url': f"https://www.sreality.cz/detail/prodej/dum/rodinny/{item['seo']['locality']}/{item['hash_id']}",
                            'area': item.get('usable_area', 'N/A'),
                            'image_url': img_url,
                            'description': description,
                            'last_updated': datetime.now().isoformat()
                        }
                if len(estates) < params['per_page']: break
                page += 1
                time.sleep(1)
            except Exception as e:
                print(f"Error on page {page}: {e}")
                break
        return all_properties

    def download_image(self, image_url, property_id):
        if not image_url: return None
        try:
            path = os.path.join(self.images_folder, f"{property_id}.jpg")
            if os.path.exists(path): return f"{self.images_folder}/{property_id}.jpg"
            r = requests.get(image_url, timeout=10)
            with open(path, 'wb') as f: f.write(r.content)
            return f"{self.images_folder}/{property_id}.jpg"
        except: return None

    def save_property_history_html(self, history, current_data):
        """Generates a visual history report with cards, images, and descriptions"""
        html = """<html><head><meta charset="utf-8">
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; background: #f0f2f5; color: #1c1e21; }
            .container { max-width: 1000px; margin: 40px auto; padding: 0 20px; }
            h1 { text-align: center; color: #1a73e8; margin-bottom: 40px; }
            .card { background: white; border-radius: 12px; overflow: hidden; display: flex; box-shadow: 0 2px 12px rgba(0,0,0,0.08); margin-bottom: 30px; }
            .card-image { width: 350px; min-height: 250px; background-position: center; background-size: cover; }
            .card-content { flex: 1; padding: 25px; display: flex; flex-direction: column; }
            .card-content h2 { margin-top: 0; font-size: 1.4rem; }
            .card-content h2 a { text-decoration: none; color: #1a73e8; }
            .locality { font-weight: 600; color: #5f6368; margin-bottom: 10px; }
            .description { font-size: 0.9rem; line-height: 1.5; color: #4a4a4a; margin-bottom: 20px; border-top: 1px solid #eee; padding-top: 15px; }
            .history-table { width: 100%; border-collapse: collapse; background: #f8f9fa; border-radius: 8px; font-size: 0.85rem; }
            .history-table td { padding: 8px 12px; border-bottom: 1px solid #eee; }
            .history-table tr:last-child td { border-bottom: none; }
            .price-val { font-weight: bold; color: #202124; text-align: right; }
        </style></head><body><div class="container"><h1>🏡 Sreality Property Tracker</h1>"""

        # Only show history for properties currently active in the results
        for p_id in current_data:
            if p_id in history:
                snaps = history[p_id]
                latest = snaps[-1]
                img_path = self.download_image(latest['image_url'], p_id)
                
                # Card Layout
                html += f"""
                <div class="card">
                    <div class="card-image" style="background-image: url('{img_path if img_path else ''}');"></div>
                    <div class="card-content">
                        <h2><a href="{latest['url']}" target="_blank">{latest['name']}</a></h2>
                        <div class="locality">📍 {latest['locality']}</div>
                        <div class="description">{latest['description'][:500]}...</div>
                        <table class="history-table">"""
                
                for s in reversed(snaps):
                    d = datetime.fromisoformat(s['last_updated']).strftime('%Y-%m-%d %H:%M')
                    html += f"<tr><td>📅 {d}</td><td class='price-val'>{self.format_price(s['price'])}</td></tr>"
                
                html += "</table></div></div>"

        html += "</div></body></html>"
        with open(self.history_html_file, 'w', encoding='utf-8') as f: f.write(html)

    def upload_to_github(self):
        """Commit all changes (data, reports, and images) to GitHub"""
        if not self.github_repo_path: return
        try:
            os.chdir(self.github_repo_path)
            # Use 'git add .' to ensure images folder is also uploaded
            subprocess.run(['git', 'add', '.'], check=True)
            commit_msg = f"Automatic Update {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            subprocess.run(['git', 'commit', '-m', commit_msg], check=True)
            subprocess.run(['git', 'push'], check=True)
            print("✅ GitHub update successful.")
        except Exception as e:
            print(f"❌ GitHub Error: {e}")

    def run_scraper(self):
        prev = self.load_previous_data()
        history = self.load_history()
        curr = self.fetch_properties()
        
        # Update history logs
        for p_id, p in curr.items():
            if p_id not in history: history[p_id] = []
            # Only record a new entry if price has changed or it's the first time
            if not history[p_id] or history[p_id][-1]['price'] != p['price']:
                history[p_id].append(p)

        self.save_data(curr)
        self.save_history(history)
        self.save_property_history_html(history, curr)
        
        if self.enable_github_upload:
            self.upload_to_github()

if __name__ == '__main__':
    # PATH TO YOUR LOCAL GITHUB FOLDER
    REPO_PATH = r"C:\Users\Rancy\Desktop\sreality-tracker" 
    
    scraper = SrealityScraper(enable_github_upload=True, github_repo_path=REPO_PATH)
    scraper.run_scraper()