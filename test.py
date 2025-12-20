import requests

property_ids = ["1854534220", "3748074316"]

for pid in property_ids:
    response = requests.get(f"https://www.sreality.cz/api/cs/v2/estates/{pid}")
    if response.status_code == 200:
        data = response.json()
        print(f"ID: {pid}")
        for key, value in data.items():
            print(f"{key}: {value}")
        print("\n" + "-"*50 + "\n")
    else:
        print(f"ID: {pid} not found, status code: {response.status_code}")
