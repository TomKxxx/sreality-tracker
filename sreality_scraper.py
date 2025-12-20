import requests
import json
import time
from datetime import datetime
import os
import subprocess

class SrealityScraper:
    def __init__(self, enable_github_upload=True, github_repo_path=None):
        """Initialize scraper
        
        Args:
            enable_github_upload: Set to True to automatically push to GitHub
            github_repo_path: Path to your GitHub repository folder
        """
        self.data_file = 'sreality_data.json'
        self.history_file = 'sreality_history.json'
        self.alerts_file = 'sreality_alerts.html'
        self.catalog_file = 'sreality_all_properties.html'
        self.removed_file = 'sreality_removed_properties.html'
        self.history_html_file = 'sreality_property_history.html'
        self.images_folder = 'property_images'
        self.base_url = 'https://www.sreality.cz/api/cs/v2/estates'
        
        # GitHub upload settings
        self.enable_github_upload = enable_github_upload
        self.github_repo_path = github_repo_path
        
        # Create images folder if it doesn't exist
        if not os.path.exists(self.images_folder):
            os.makedirs(self.images_folder)
    
    def load_previous_data(self):
        """Load previously saved property data"""
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def load_history(self):
        """Load complete history of all property snapshots"""
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def save_history(self, history):
        """Save complete property history"""
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    
    def save_data(self, data):
        """Save current property data"""
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def format_price(self, price):
        """Format price to a human-readable string"""
        return f"{price:,.0f}".replace(",", " ") + " Kč"

    def fetch_property_details(self, property_url, property_id):
        """Fetch full property details including description"""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            api_url = f"https://www.sreality.cz/api/cs/v2/estates/{property_id}"
            response = requests.get(api_url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get('text', {}).get('value', 'No description available')
        except Exception as e:
            print(f"Error fetching details for {property_id}: {e}")
            return "Description not available"
    
    def fetch_properties(self):
        """
        Fetch properties using a 10km Radial GPS Search around Ostrava.
        This captures ALL properties (including N/A) within the physical radius.
        """
        # locality_region_id=10 is required for GPS radius searches
        # lat/lon for Ostrava center: 49.8209228, 18.2625243
        # distance=10000 is 10 kilometers in meters
        params = {
            'category_main_cb': 2,        # houses
            'category_type_cb': 1,        # for sale
            'price_from': 4948302,
            'price_to': 21623887,
            'usable_area_from': 200,
            'locality_region_id': 10,
            'lat': 49.8209228,
            'lon': 18.2625243,
            'distance': 10000,
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
                if not estates:
                    break
                
                for item in estates:
                    prop_id = str(item['hash_id'])
                    if prop_id not in all_properties:
                        image_url = None
                        if item.get('_links', {}).get('images'):
                            imgs = item['_links']['images']
                            if imgs: image_url = imgs[0].get('href', '')
                        
                        description = self.fetch_property_details(item['seo']['locality'], prop_id)
                        
                        all_properties[prop_id] = {
                            'id': prop_id,
                            'name': item.get('name', 'N/A'),
                            'price': item.get('price', 0),
                            'locality': item.get('locality', 'N/A'),
                            'url': f"https://www.sreality.cz/detail/prodej/dum/rodinny/{item['seo']['locality']}/{item['hash_id']}",
                            'area': item.get('usable_area', 'N/A'),
                            'image_url': image_url,
                            'description': description,
                            'last_updated': datetime.now().isoformat()
                        }
                
                if len(estates) < params['per_page']:
                    break
                
                page += 1
                time.sleep(1)
                
            except Exception as e:
                print(f"Error fetching page {page}: {e}")
                break
        
        print(f"\nTotal unique properties found in 10km radius: {len(all_properties)}")
        return all_properties
    
    def download_image(self, image_url, property_id):
        """Download property image"""
        if not image_url: return None
        try:
            path = os.path.join(self.images_folder, f"{property_id}.jpg")
            if os.path.exists(path): return path
            r = requests.get(image_url, timeout=10)
            r.raise_for_status()
            with open(path, 'wb') as f: f.write(r.content)
            return path
        except: return None
    
    def save_alerts_to_file(self, new_properties, price_changes):
        """Append alerts to HTML history file"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        file_exists = os.path.exists(self.alerts_file)
        
        if not file_exists:
            html = f'<html><head><meta charset="utf-8"><title>Alerts</title><style>body{{font-family:Arial;margin:20px;}} .property{{background:#fff;border:1px solid #ddd;padding:15px;margin:15px 0;display:flex;gap:15px;}} .property-image{{width:200px;height:150px;object-fit:cover;}} .new{{border-left:4px solid #4caf50;}} .price-drop{{border-left:4px solid #ff9800;}}</style></head><body><h1>🏠 Sreality Alerts History</h1>'
        else:
            with open(self.alerts_file, 'r', encoding='utf-8') as f:
                html = f.read().replace('</body></html>', '')
        
        html += f'<div style="background:#fff;padding:20px;margin:20px 0;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);"><h2>Check: {timestamp}</h2>'
        
        if new_properties:
            html += f"<h3>✨ New Properties ({len(new_properties)})</h3>"
            for prop in new_properties:
                img = self.download_image(prop.get('image_url'), prop['id'])
                img_tag = f'<img src="{img}" class="property-image">' if img else ""
                html += f'<div class="property new">{img_tag}<div><h3><a href="{prop["url"]}">{prop["name"]}</a></h3><p>Price: {self.format_price(prop["price"])} | {prop["locality"]}</p></div></div>'
        
        if price_changes:
            html += f"<h3>💰 Price Changes ({len(price_changes)})</h3>"
            for prop in price_changes:
                img = self.download_image(prop.get('image_url'), prop['id'])
                img_tag = f'<img src="{img}" class="property-image">' if img else ""
                html += f'<div class="property price-drop">{img_tag}<div><h3><a href="{prop["url"]}">{prop["name"]}</a></h3><p>Old: {self.format_price(prop["old_price"])} -> New: {self.format_price(prop["price"])}</p></div></div>'

        html += "</div></body></html>"
        with open(self.alerts_file, 'w', encoding='utf-8') as f: f.write(html)

    def save_complete_catalog(self, all_properties):
        """Save active catalog"""
        html = f'<html><head><meta charset="utf-8"><title>Catalog</title></head><body><h1>Active Catalog ({len(all_properties)})</h1>'
        sorted_props = sorted(all_properties.values(), key=lambda x: x['price'])
        for prop in sorted_props:
            html += f'<div style="border:1px solid #ccc;margin:10px;padding:10px;"><h3><a href="{prop["url"]}">{prop["name"]}</a> - {self.format_price(prop["price"])}</h3><p>{prop["locality"]}</p></div>'
        html += "</body></html>"
        with open(self.catalog_file, 'w', encoding='utf-8') as f: f.write(html)

    def save_removed_properties(self, removed_properties):
        """Save removed properties"""
        if not removed_properties: return
        html = '<html><head><meta charset="utf-8"><title>Removed</title></head><body><h1>Removed Properties</h1>'
        for prop in removed_properties:
            html += f'<div style="border:1px solid red;margin:10px;padding:10px;opacity:0.6;"><h3>{prop["name"]} (REMOVED)</h3><p>Last Price: {self.format_price(prop["price"])}</p></div>'
        html += "</body></html>"
        with open(self.removed_file, 'w', encoding='utf-8') as f: f.write(html)

    def save_property_history_html(self, history):
        """Save history of all changes"""
        html = '<html><head><meta charset="utf-8"><title>History</title></head><body><h1>Property History</h1>'
        for p_id, snaps in sorted(history.items(), key=lambda x: len(x[1]), reverse=True):
            latest = snaps[-1]
            html += f'<div style="border-bottom:1px solid #eee;padding:10px;"><h3>{latest["name"]}</h3>'
            for snap in snaps:
                d = datetime.fromisoformat(snap['last_updated']).strftime('%Y-%m-%d %H:%M')
                html += f'<p>{d}: {self.format_price(snap["price"])}</p>'
            html += "</div>"
        html += "</body></html>"
        with open(self.history_html_file, 'w', encoding='utf-8') as f: f.write(html)

    def run_scraper(self):
        """Main scraping loop"""
        previous_data = self.load_previous_data()
        history = self.load_history()
        current_data = self.fetch_properties()
        
        new_props, price_chg, removed_props = [], [], []

        for p_id, prop in current_data.items():
            if p_id not in history: history[p_id] = []
            history[p_id].append(prop)
            if p_id not in previous_data: new_props.append(prop)
            elif prop['price'] != previous_data[p_id]['price']:
                price_chg.append({**prop, 'old_price': previous_data[p_id]['price'], 'price_diff': prop['price'] - previous_data[p_id]['price']})

        for p_id in set(previous_data.keys()) - set(current_data.keys()):
            removed_props.append(previous_data[p_id])

        self.save_data(current_data)
        self.save_history(history)
        self.save_alerts_to_file(new_props, price_chg)
        self.save_complete_catalog(current_data)
        self.save_removed_properties(removed_props)
        self.save_property_history_html(history)

        if self.enable_github_upload and self.github_repo_path:
            self.upload_to_github()

    def upload_to_github(self):
        """Commit and push to GitHub"""
        try:
            os.chdir(self.github_repo_path)
            subprocess.run(['git', 'add', '.'], check=True)
            subprocess.run(['git', 'commit', '-m', f"Update {datetime.now().strftime('%Y-%m-%d %H:%M')}"], check=True)
            subprocess.run(['git', 'push'], check=True)
            print("✅ Pushed to GitHub.")
        except Exception as e: print(f"❌ GitHub Error: {e}")

if __name__ == '__main__':
    REPO_PATH = r"C:\Users\Rancy\Desktop\sreality-tracker" 
    scraper = SrealityScraper(enable_github_upload=True, github_repo_path=REPO_PATH)
    scraper.run_scraper()