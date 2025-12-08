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
    
    def fetch_property_details(self, property_url, property_id):
        """Fetch full property details including description from the property page"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            # Convert web URL to API URL
            api_url = f"https://www.sreality.cz/api/cs/v2/estates/{property_id}"
            
            response = requests.get(api_url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Get description text
            description = data.get('text', {}).get('value', 'No description available')
            
            return description
        except Exception as e:
            print(f"Error fetching details for {property_id}: {e}")
            return "Description not available"
    
    def fetch_properties(self):
        """Fetch properties from Sreality API with pagination"""
        params = {
            'category_main_cb': 2,  # houses
            'category_type_cb': 1,  # sale
            'per_page': 60,
            'price_from': 4948302,
            'price_to': 21623887,
            'usable_area_from': 200,
            'locality_district_id': [65, 64, 66, 67, 69]
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        all_properties = {}
        page = 1
        
        while True:
            try:
                params['page'] = page
                print(f"Fetching page {page}...")
                print(f"Making API request with params: {params}")
                response = requests.get(self.base_url, params=params, headers=headers)
                print(f"Response status: {response.status_code}")
                response.raise_for_status()
                data = response.json()
                
                total_results = data.get('result_size', 0)
                print(f"Total results in search: {total_results}")
                
                estates = data.get('_embedded', {}).get('estates', [])
                
                if not estates:
                    print(f"No more properties on page {page}")
                    break
                
                print(f"Found {len(estates)} properties on page {page}")
                
                # Process properties from this page
                for item in estates:
                    prop_id = str(item['hash_id'])
                    
                    # Get the main image URL
                    image_url = None
                    if item.get('_links', {}).get('images'):
                        images = item['_links']['images']
                        if images and len(images) > 0:
                            image_url = images[0].get('href', '')
                    
                    # Fetch full description
                    print(f"Fetching details for property {prop_id}...")
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
                
                # Check if there are more pages
                if len(estates) < params['per_page']:
                    print("Reached last page")
                    break
                
                page += 1
                time.sleep(1)  # Be nice to the API - wait 1 second between pages
                
            except Exception as e:
                print(f"Error fetching page {page}: {e}")
                break
        
        print(f"Total properties fetched: {len(all_properties)}")
        return all_properties
    
    def download_image(self, image_url, property_id):
        """Download property image and return local path"""
        if not image_url:
            return None
        
        try:
            # Create filename from property ID
            image_path = os.path.join(self.images_folder, f"{property_id}.jpg")
            
            # Skip if already downloaded
            if os.path.exists(image_path):
                return image_path
            
            # Download the image
            response = requests.get(image_url, timeout=10)
            response.raise_for_status()
            
            # Save the image
            with open(image_path, 'wb') as f:
                f.write(response.content)
            
            print(f"Downloaded image for property {property_id}")
            return image_path
        except Exception as e:
            print(f"Error downloading image for {property_id}: {e}")
            return None
    
    def save_alerts_to_file(self, new_properties, price_changes):
        """Append alerts to HTML file to keep history"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Check if file exists to determine if we need to create header
        file_exists = os.path.exists(self.alerts_file)
        
        if not file_exists:
            # Create new file with header
            html = f"""<html>
<head>
    <meta charset="utf-8">
    <title>Sreality Alerts History</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1 {{ color: #333; border-bottom: 3px solid #0066cc; padding-bottom: 10px; }}
        .check-section {{ 
            background: white; 
            padding: 20px; 
            margin: 20px 0; 
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .check-header {{ 
            color: #0066cc; 
            border-bottom: 2px solid #eee; 
            padding-bottom: 10px;
            margin-bottom: 15px;
        }}
        .property {{ 
            background: #fafafa; 
            border: 1px solid #ddd; 
            padding: 15px; 
            margin: 15px 0; 
            border-radius: 5px;
            display: flex;
            gap: 15px;
        }}
        .property-image {{
            flex-shrink: 0;
            width: 200px;
            height: 150px;
            object-fit: cover;
            border-radius: 4px;
        }}
        .property-details {{
            flex-grow: 1;
        }}
        .property h3 {{ margin-top: 0; color: #333; }}
        .property a {{ color: #0066cc; text-decoration: none; }}
        .property a:hover {{ text-decoration: underline; }}
        .button {{ 
            background: #0066cc; 
            color: white; 
            padding: 8px 16px; 
            text-decoration: none; 
            border-radius: 3px;
            display: inline-block;
            margin-top: 10px;
            font-size: 14px;
        }}
        .new {{ border-left: 4px solid #4caf50; }}
        .price-drop {{ border-left: 4px solid #ff9800; }}
        .timestamp {{ color: #666; font-size: 0.9em; }}
        .no-results {{ color: #999; font-style: italic; }}
    </style>
</head>
<body>
    <h1>🏠 Sreality Property Alerts - Complete History</h1>
"""
        else:
            # Read existing content
            with open(self.alerts_file, 'r', encoding='utf-8') as f:
                html = f.read()
            # Remove closing tags to append
            html = html.replace('</body></html>', '')
        
        # Add new check section
        html += f"""
    <div class="check-section">
        <h2 class="check-header">Check: {timestamp}</h2>
"""
        
        if new_properties:
            html += f"<h3>✨ New Properties ({len(new_properties)})</h3>"
            for prop in new_properties:
                # Download image
                image_path = self.download_image(prop.get('image_url'), prop['id'])
                image_html = ""
                if image_path:
                    image_html = f'<img src="{image_path}" class="property-image" alt="Property photo">'
                
                html += f"""
        <div class="property new">
            {image_html}
            <div class="property-details">
                <h3><a href="{prop['url']}" target="_blank">{prop['name']}</a></h3>
                <p><strong>Price:</strong> {self.format_price(prop['price'])}</p>
                <p><strong>Area:</strong> {prop['area']} m²</p>
                <p><strong>Location:</strong> {prop['locality']}</p>
                <div style="margin: 10px 0; padding: 10px; background: white; border-radius: 4px; border-left: 3px solid #4caf50;">
                    <strong>Description:</strong><br>
                    <div style="margin-top: 5px; line-height: 1.6;">{prop.get('description', 'No description available')}</div>
                </div>
                <a href="{prop['url']}" class="button" target="_blank">View Property</a>
            </div>
        </div>
"""
        
        if price_changes:
            html += f"<h3>💰 Price Changes ({len(price_changes)})</h3>"
            for prop in price_changes:
                change_type = "📉 Reduced" if prop['price_diff'] < 0 else "📈 Increased"
                color = "green" if prop['price_diff'] < 0 else "red"
                
                # Download image
                image_path = self.download_image(prop.get('image_url'), prop['id'])
                image_html = ""
                if image_path:
                    image_html = f'<img src="{image_path}" class="property-image" alt="Property photo">'
                
                html += f"""
        <div class="property price-drop">
            {image_html}
            <div class="property-details">
                <h3><a href="{prop['url']}" target="_blank">{prop['name']}</a></h3>
                <p><strong>{change_type}:</strong> 
                   <span style="color: {color};">{self.format_price(abs(prop['price_diff']))}</span></p>
                <p><strong>Old Price:</strong> {self.format_price(prop['old_price'])}</p>
                <p><strong>New Price:</strong> {self.format_price(prop['price'])}</p>
                <p><strong>Location:</strong> {prop['locality']}</p>
                <div style="margin: 10px 0; padding: 10px; background: white; border-radius: 4px; border-left: 3px solid #ff9800;">
                    <strong>Description:</strong><br>
                    <div style="margin-top: 5px; line-height: 1.6;">{prop.get('description', 'No description available')}</div>
                </div>
                <a href="{prop['url']}" class="button" target="_blank">View Property</a>
            </div>
        </div>
"""
        
        if not new_properties and not price_changes:
            html += '<p class="no-results">No new properties or price changes found in this check.</p>'
        
        html += """
    </div>
"""
        
        # Close HTML
        html += """
</body>
</html>
"""
        
        with open(self.alerts_file, 'w', encoding='utf-8') as f:
            f.write(html)
        
        print(f"✅ Alerts appended to {self.alerts_file}")
    
    def save_complete_catalog(self, all_properties):
        """Create a complete catalog of ALL current properties"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Sort properties by price
        sorted_props = sorted(all_properties.values(), key=lambda x: x['price'])
        
        html = f"""<html>
<head>
    <meta charset="utf-8">
    <title>Sreality - All Properties Catalog</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1 {{ color: #333; border-bottom: 3px solid #0066cc; padding-bottom: 10px; }}
        .summary {{ 
            background: white; 
            padding: 15px; 
            margin: 20px 0; 
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .property {{ 
            background: white; 
            border: 1px solid #ddd; 
            padding: 15px; 
            margin: 15px 0; 
            border-radius: 5px;
            display: flex;
            gap: 15px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .property-image {{
            flex-shrink: 0;
            width: 200px;
            height: 150px;
            object-fit: cover;
            border-radius: 4px;
        }}
        .property-details {{
            flex-grow: 1;
        }}
        .property h3 {{ margin-top: 0; color: #333; }}
        .property a {{ color: #0066cc; text-decoration: none; }}
        .property a:hover {{ text-decoration: underline; }}
        .button {{ 
            background: #0066cc; 
            color: white; 
            padding: 8px 16px; 
            text-decoration: none; 
            border-radius: 3px;
            display: inline-block;
            margin-top: 10px;
            font-size: 14px;
        }}
        .timestamp {{ color: #666; font-size: 0.9em; }}
        .description-box {{
            margin: 10px 0;
            padding: 10px;
            background: #fafafa;
            border-radius: 4px;
            border-left: 3px solid #0066cc;
            max-height: 150px;
            overflow-y: auto;
        }}
    </style>
</head>
<body>
    <h1>🏠 Complete Property Catalog - Ostrava District</h1>
    <div class="summary">
        <p><strong>Total Properties:</strong> {len(all_properties)}</p>
        <p class="timestamp">Last updated: {timestamp}</p>
        <p><em>This catalog shows ALL properties currently matching your search criteria.</em></p>
    </div>
"""
        
        for prop in sorted_props:
            # Download image
            image_path = self.download_image(prop.get('image_url'), prop['id'])
            image_html = ""
            if image_path:
                image_html = f'<img src="{image_path}" class="property-image" alt="Property photo">'
            
            html += f"""
    <div class="property">
        {image_html}
        <div class="property-details">
            <h3><a href="{prop['url']}" target="_blank">{prop['name']}</a></h3>
            <p><strong>Price:</strong> {self.format_price(prop['price'])}</p>
            <p><strong>Area:</strong> {prop['area']} m²</p>
            <p><strong>Location:</strong> {prop['locality']}</p>
            <div class="description-box">
                <strong>Description:</strong><br>
                <div style="margin-top: 5px; line-height: 1.6;">{prop.get('description', 'No description available')}</div>
            </div>
            <a href="{prop['url']}" class="button" target="_blank">View Property</a>
        </div>
    </div>
"""
        
        html += """
</body>
</html>
"""
        
        with open(self.catalog_file, 'w', encoding='utf-8') as f:
            f.write(html)
        
        print(f"✅ Complete catalog saved to {self.catalog_file}")
    
    def save_removed_properties(self, removed_properties):
        """Create HTML showing properties that have been removed/sold"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if not removed_properties:
            return
        
        # Sort by when they were last seen (most recent first)
        sorted_props = sorted(removed_properties, key=lambda x: x.get('last_updated', ''), reverse=True)
        
        html = f"""<html>
<head>
    <meta charset="utf-8">
    <title>Sreality - Removed Properties</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1 {{ color: #333; border-bottom: 3px solid #cc0000; padding-bottom: 10px; }}
        .summary {{ 
            background: white; 
            padding: 15px; 
            margin: 20px 0; 
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .property {{ 
            background: white; 
            border: 1px solid #ddd; 
            padding: 15px; 
            margin: 15px 0; 
            border-radius: 5px;
            display: flex;
            gap: 15px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            opacity: 0.8;
            border-left: 4px solid #cc0000;
        }}
        .property-image {{
            flex-shrink: 0;
            width: 200px;
            height: 150px;
            object-fit: cover;
            border-radius: 4px;
            filter: grayscale(30%);
        }}
        .property-details {{
            flex-grow: 1;
        }}
        .property h3 {{ margin-top: 0; color: #333; }}
        .property a {{ color: #0066cc; text-decoration: none; }}
        .removed-badge {{
            background: #cc0000;
            color: white;
            padding: 4px 8px;
            border-radius: 3px;
            font-size: 12px;
            display: inline-block;
            margin-left: 10px;
        }}
        .timestamp {{ color: #666; font-size: 0.9em; }}
    </style>
</head>
<body>
    <h1>🔴 Removed Properties (Likely Sold)</h1>
    <div class="summary">
        <p><strong>Total Removed:</strong> {len(removed_properties)}</p>
        <p class="timestamp">Last checked: {timestamp}</p>
        <p><em>These properties were previously available but are no longer in the search results - likely sold or withdrawn.</em></p>
    </div>
"""
        
        for prop in sorted_props:
            # Download image
            image_path = self.download_image(prop.get('image_url'), prop['id'])
            image_html = ""
            if image_path:
                image_html = f'<img src="{image_path}" class="property-image" alt="Property photo">'
            
            last_seen = datetime.fromisoformat(prop['last_updated']).strftime('%Y-%m-%d %H:%M')
            
            html += f"""
    <div class="property">
        {image_html}
        <div class="property-details">
            <h3>
                <a href="{prop['url']}" target="_blank">{prop['name']}</a>
                <span class="removed-badge">REMOVED</span>
            </h3>
            <p><strong>Last Price:</strong> {self.format_price(prop['price'])}</p>
            <p><strong>Area:</strong> {prop['area']} m²</p>
            <p><strong>Location:</strong> {prop['locality']}</p>
            <p class="timestamp"><strong>Last seen:</strong> {last_seen}</p>
        </div>
    </div>
"""
        
        html += """
</body>
</html>
"""
        
        with open(self.removed_file, 'w', encoding='utf-8') as f:
            f.write(html)
        
        print(f"✅ Removed properties saved to {self.removed_file}")
    
    def save_property_history_html(self, history):
        """Create HTML showing complete history of all property changes"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        html = f"""<html>
<head>
    <meta charset="utf-8">
    <title>Sreality - Property History</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1 {{ color: #333; border-bottom: 3px solid #0066cc; padding-bottom: 10px; }}
        h2 {{ color: #0066cc; margin-top: 30px; }}
        .summary {{ 
            background: white; 
            padding: 15px; 
            margin: 20px 0; 
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .property-history {{ 
            background: white; 
            border: 1px solid #ddd; 
            padding: 20px; 
            margin: 20px 0; 
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .property-header {{
            display: flex;
            gap: 15px;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #eee;
        }}
        .property-image {{
            flex-shrink: 0;
            width: 150px;
            height: 112px;
            object-fit: cover;
            border-radius: 4px;
        }}
        .snapshot {{
            background: #f9f9f9;
            border-left: 3px solid #0066cc;
            padding: 8px 12px;
            margin: 5px 0;
            border-radius: 3px;
            font-size: 13px;
            line-height: 1.4;
        }}
        .snapshot.price-change {{
            border-left-color: #ff9800;
            background: #fff8f0;
            font-weight: bold;
        }}
        .snapshot p {{
            margin: 3px 0;
        }}
        .price-up {{ color: #cc0000; }}
        .price-down {{ color: #4caf50; }}
        .timestamp {{ color: #666; font-size: 0.85em; }}
        .removed {{ 
            background: #ffebee; 
            border-left-color: #cc0000; 
        }}
        .snapshot-date {{
            display: inline-block;
            width: 130px;
            font-weight: 600;
        }}
        .snapshot-price {{
            display: inline;
        }}
    </style>
</head>
<body>
    <h1>📊 Complete Property History & Changes</h1>
    <div class="summary">
        <p><strong>Properties Tracked:</strong> {len(history)}</p>
        <p class="timestamp">Generated: {timestamp}</p>
        <p><em>This shows the complete history of all properties, including price changes and removal dates.</em></p>
    </div>
"""
        
        # Sort properties by number of snapshots (most activity first)
        sorted_history = sorted(history.items(), key=lambda x: len(x[1]), reverse=True)
        
        for prop_id, snapshots in sorted_history:
            if not snapshots:
                continue
            
            latest = snapshots[-1]
            
            # Download image
            image_path = self.download_image(latest.get('image_url'), prop_id)
            image_html = ""
            if image_path:
                image_html = f'<img src="{image_path}" class="property-image" alt="Property photo">'
            
            html += f"""
    <div class="property-history">
        <div class="property-header">
            {image_html}
            <div>
                <h2 style="margin-top: 0;"><a href="{latest['url']}" target="_blank">{latest['name']}</a></h2>
                <p><strong>Location:</strong> {latest['locality']}</p>
                <p><strong>Area:</strong> {latest['area']} m²</p>
                <p><strong>Total Snapshots:</strong> {len(snapshots)}</p>
            </div>
        </div>
        <h3>History ({len(snapshots)} snapshots):</h3>
"""
            
            # Show each snapshot
            prev_price = None
            for i, snapshot in enumerate(snapshots):
                snapshot_date = datetime.fromisoformat(snapshot['last_updated']).strftime('%m/%d %H:%M')
                price = snapshot['price']
                
                price_change_html = ""
                css_class = "snapshot"
                
                if prev_price is not None and price != prev_price:
                    diff = price - prev_price
                    css_class = "snapshot price-change"
                    if diff < 0:
                        price_change_html = f' → <span class="price-down">📉 -{self.format_price(abs(diff))}</span>'
                    else:
                        price_change_html = f' → <span class="price-up">📈 +{self.format_price(diff)}</span>'
                
                html += f"""
        <div class="{css_class}">
            <span class="snapshot-date">#{i+1} {snapshot_date}</span>
            <span class="snapshot-price">{self.format_price(price)}{price_change_html}</span>
        </div>
"""
                
                prev_price = price
            
            html += """
    </div>
"""
        
        html += """
</body>
</html>
"""
        
        with open(self.history_html_file, 'w', encoding='utf-8') as f:
            f.write(html)
        
        print(f"✅ Property history saved to {self.history_html_file}")
    
    def upload_to_github(self):
        """Automatically commit and push changes to GitHub"""
        if not self.enable_github_upload or not self.github_repo_path:
            return
        
        try:
            print("📤 Uploading to GitHub...")
            
            # Change to the repository directory
            original_dir = os.getcwd()
            os.chdir(self.github_repo_path)
            
            # Git commands
            commands = [
                ['git', 'add', '.'],
                ['git', 'commit', '-m', f'Update property data - {datetime.now().strftime("%Y-%m-%d %H:%M")}'],
                ['git', 'push']
            ]
            
            for cmd in commands:
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    # If commit fails because nothing changed, that's okay
                    if 'nothing to commit' in result.stdout or 'nothing to commit' in result.stderr:
                        print("No changes to upload")
                        os.chdir(original_dir)
                        return
                    else:
                        print(f"Git command failed: {' '.join(cmd)}")
                        print(f"Error: {result.stderr}")
            
            print("✅ Successfully uploaded to GitHub!")
            os.chdir(original_dir)
            
        except Exception as e:
            print(f"❌ Error uploading to GitHub: {e}")
            os.chdir(original_dir)
    
    
    
    def format_price(self, price):
        """Format price with thousand separators"""
        return f"{price:,} Kč".replace(',', ' ')
    
    def check_and_notify(self):
        """Check for new properties and price changes"""
        print(f"Checking properties at {datetime.now()}")
        
        previous_data = self.load_previous_data()
        history = self.load_history()
        current_data = self.fetch_properties()
        
        if not current_data:
            print("No data fetched")
            return
        
        new_properties = []
        price_changes = []
        removed_properties = []
        
        # Check for new properties and price changes
        for prop_id, prop in current_data.items():
            # Add snapshot to history for EVERY check
            if prop_id not in history:
                history[prop_id] = []
            
            # Always add a new snapshot (even if price didn't change)
            history[prop_id].append(prop.copy())
            
            if prop_id not in previous_data:
                new_properties.append(prop)
            elif previous_data[prop_id]['price'] != prop['price']:
                old_price = previous_data[prop_id]['price']
                price_diff = prop['price'] - old_price
                price_changes.append({
                    **prop,
                    'old_price': old_price,
                    'price_diff': price_diff
                })
        
        # Check for removed properties (no longer in current results)
        for prop_id, prop in previous_data.items():
            if prop_id not in current_data:
                removed_properties.append(prop)
        
        # Save notifications to file
        if new_properties or price_changes:
            self.save_alerts_to_file(new_properties, price_changes)
        
        # Save removed properties
        if removed_properties:
            self.save_removed_properties(removed_properties)
        
        # Save complete catalog of all current properties
        self.save_complete_catalog(current_data)
        
        # Save property history HTML
        self.save_property_history_html(history)
        
        # Save data files
        self.save_data(current_data)
        self.save_history(history)
        
        # Upload to GitHub if enabled
        self.upload_to_github()
        
        print(f"Found {len(new_properties)} new properties, {len(price_changes)} price changes, {len(removed_properties)} removed")

    
    def run_continuous(self, interval_hours=6):
        """Run scraper continuously with specified interval"""
        print(f"Starting continuous monitoring (checking every {interval_hours} hours)")
        print(f"Results will be saved to:")
        print(f"  - {self.alerts_file} (new properties & price changes)")
        print(f"  - {self.catalog_file} (all current properties)")
        print(f"  - {self.removed_file} (removed/sold properties)")
        print(f"  - {self.history_html_file} (complete history with snapshots)")
        
        while True:
            try:
                self.check_and_notify()
                print(f"Next check in {interval_hours} hours")
                time.sleep(interval_hours * 3600)
            except KeyboardInterrupt:
                print("\nStopping scraper...")
                break
            except Exception as e:
                print(f"Error in main loop: {e}")
                time.sleep(300)  # Wait 5 minutes before retrying


# Example usage:
if __name__ == "__main__":
    # ===========================================
    # GITHUB UPLOAD CONFIGURATION (OPTIONAL)
    # ===========================================
    # Set to True to enable automatic upload to GitHub Pages
    ENABLE_GITHUB = True
    
    # Path to your GitHub repository folder where HTML files will be uploaded
    # Example: 'C:\\Users\\Rancy\\Documents\\sreality-tracker'
    GITHUB_REPO_PATH = 'C:\\Users\\Rancy\\Desktop\\sreality-tracker'
    
    # ===========================================
    
    # Create scraper instance
    scraper = SrealityScraper(
        enable_github_upload=ENABLE_GITHUB,
        github_repo_path=GITHUB_REPO_PATH
    )
    
    # Option 1: Run once (e.g., via Task Scheduler)
    # scraper.check_and_notify()
    
    # Option 2: Run continuously (check every 6 hours)
    scraper.run_continuous(interval_hours=6)
