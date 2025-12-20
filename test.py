import requests

# Your specific filters from sreality_scraper.py
params = {
    'category_main_cb': 2,        # Houses
    'category_type_cb': 1,        # For Sale
    'price_from': 4948302,
    'price_to': 21623887,
    'usable_area_from': 200,
    'per_page': 1                 # We only need the 'result_size' count
}

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

print("--- LIVE API CHECK ---")

# 1. Get total for the whole country (includes N/A)
res_all = requests.get('https://www.sreality.cz/api/cs/v2/estates', params=params, headers=headers).json()
total_all = res_all.get('result_size', 0)

# 2. Get totals for your specific districts
target_districts = [65, 64, 66, 67, 69]
total_in_districts = 0

for d in target_districts:
    dist_params = params.copy()
    dist_params['locality_district_id'] = d
    res = requests.get('https://www.sreality.cz/api/cs/v2/estates', params=dist_params, headers=headers).json()
    count = res.get('result_size', 0)
    print(f"District {d}: {count} properties")
    total_in_districts += count

print("-" * 30)
print(f"Total in your 5 districts: {total_in_districts}")
print(f"Total in Whole Country:   {total_all}")
print(f"Difference (N/A or Other): {total_all - total_in_districts}")