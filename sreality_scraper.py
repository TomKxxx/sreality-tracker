import requests
import json
import time
from datetime import datetime
import os
import subprocess

class SrealityScraper:
    def __init__(self, enable_github_upload=True, github_repo_path=None):
        """Initialize scraper with localized Radial Search"""
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
            data = response.json()
            return data.get('text', {}).get('value', 'No description available')
        except:
            return "Description not available"
    
    def fetch_properties(self):
        """Localized search to Ostrava region only"""
        # locality_region_id: 13 is Moravian-Silesian Region (MSK)
        # This prevents Prague results while allowing 'N/A' districts within MSK
        base_params = {
            'category_main_cb': 2, 'category_type_cb': 1, 'per_page': 60,
            'price_from': 4948302, 'price_to': 21623887, 'usable_area_from': 200,
            'locality_region_id': 13 
        }
        
        # We also use GPS to prioritize Ostrava center + 15km
        base_params.update({
            'lat': 49.8209228,
            'lon': 18.2625243,
            'distance': 15000 
        })

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        all_properties = {}
        
        print("\n--- Starting Search: Ostrava + 15km (Moravian-Silesian Region) ---")
        page = 1
        while True:
            try:
                params = base_params.copy()
                params['page'] = page
                response = requests.get(self.base_url, params=params, headers=headers)
                data = response.json()
                estates = data.get('_embedded', {}).get('estates', [])
                if not estates: break
                
                for item in estates:
                    prop_id = str(item['hash_id'])
                    if prop_id not in all_properties:
                        img_url = item.get('_links', {}).get('images', [{}])[0].get('href')
                        description = self.fetch_property_details(item['seo']['locality'], prop_id)
                        all_properties[prop_id] = {
                            'id': prop_id, 'name': item.get('name', 'N/A'),
                            'price': item.get('price', 0), 'locality': item.get('locality', 'N/A'),
                            'url': f"https://www.sreality.cz/detail/prodej/dum/rodinny/{item['seo']['locality']}/{item['hash_id']}",
                            'area': item.get('usable_area', 'N/A'), 'image_url': img_url,
                            'description': description, 'last_updated': datetime.now().isoformat()
                        }
                if len(estates) < 60: break
                page += 1
                time.sleep(1)
            except: break
        
        print(f"Total localized properties found: {len(all_properties)}")
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
        html = """<html><head><meta charset="utf-8">
        <style>
            body { font-family: 'Segoe UI', Tahoma; margin: 0; background: #f0f2f5; }
            .container { max-width: 900px; margin: 30px auto; padding: 0 15px; }
            .card { background: white; border-radius: 10px; display: flex; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 25px; overflow: hidden; }
            .card-img { width: 300px; background-size: cover; background-position: center; }
            .card-body { flex: 1; padding: 20px; }
            .card-body h2 { margin: 0 0 10px 0; font-size: 1.3rem; }
            .card-body a { color: #1a73e8; text-decoration: none; }
            .locality { color: #666; font-weight: bold; margin-bottom: 10px; }
            .desc { font-size: 0.85rem; color: #444; border-top: 1px solid #eee; padding-top: 10px; margin-top: 10px; }
            .history { margin-top: 15px; font-size: 0.8rem; background: #f8f9fa; padding: 10px; border-radius: 5px; }
        </style></head><body><div class="container"><h1>🏡 Ostrava Property History</h1>"""

        for p_id in current_data:
            if p_id in history:
                latest = history[p_id][-1]
                img = self.download_image(latest['image_url'], p_id)
                html += f"""
                <div class="card">
                    <div class="card-img" style="background-image: url('{img if img else ''}');"></div>
                    <div class="card-body">
                        <h2><a href="{latest['url']}" target="_blank">{latest['name']}</a></h2>
                        <div class="locality">📍 {latest['locality']}</div>
                        <div class="desc">{latest['description'][:400]}...</div>
                        <div class="history"><b>Price History:</b><br>"""
                for s in reversed(history[p_id]):
                    d = datetime.fromisoformat(s['last_updated']).strftime('%Y-%m-%d %H:%M')
                    html += f"• {d}: {self.format_price(s['price'])}<br>"
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
        prev = self.load_previous_data()
        history = self.load_history()
        curr = self.fetch_properties()
        
        for p_id, p in curr.items():
            if p_id not in history: history[p_id] = []
            if not history[p_id] or history[p_id][-1]['price'] != p['price']:
                history[p_id].append(p)

        self.save_data(curr)
        self.save_history(history)
        self.save_property_history_html(history, curr)
        if self.enable_github_upload: self.upload_to_github()

if __name__ == '__main__':
    REPO_PATH = r"C:\Users\Rancy\Desktop\sreality-tracker" 
    scraper = SrealityScraper(enable_github_upload=True, github_repo_path=REPO_PATH)
    scraper.run_scraper()