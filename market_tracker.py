#!/usr/bin/env python3
"""Daily Sreality house-market monitor for Ostrava +/-15 km.
Historical asking-price observations are NOT verified sale prices.
"""
from __future__ import annotations
import argparse
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BASE = "https://www.sreality.cz"
CENTER = (49.8209, 18.2625)
RADIUS_KM = 15
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OstravaPropertyHistory/1.0; public housing market research)"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
TZ = ZoneInfo("Europe/Prague")
MARKETS = ("sale", "rent")
SEARCH = {
    "sale": "/hledani/prodej/domy/rodinne-domy",
    "rent": "/hledani/pronajem/domy/rodinne-domy",
}
LINK_PATTERN = {
    "sale": r'/detail/prodej/dum/[^"\s<>]+',
    "rent": r'/detail/pronajem/dum/[^"\s<>]+',
}

def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)

def distance_km(lat, lon):
    a, b = map(math.radians, CENTER)
    c, d = math.radians(lat), math.radians(lon)
    v = math.sin((c-a)/2)**2 + math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return 12742 * math.asin(min(1, math.sqrt(v)))

def as_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def coords(item):
    for obj in [item, item.get("gps"), item.get("coordinates"), item.get("location")]:
        if not isinstance(obj, dict):
            continue
        lat = as_float(obj.get("latitude", obj.get("lat")))
        lon = as_float(obj.get("longitude", obj.get("lon", obj.get("lng"))))
        if lat is not None and lon is not None and 48 < lat < 51 and 17 < lon < 20:
            return lat, lon
    return None

def unwrap_search(html):
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise RuntimeError("Sreality embedded __NEXT_DATA__ not found (site layout may have changed)")
    data = json.loads(match.group(1))
    queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    for query in queries:
        key = query.get("queryKey") or []
        if key and str(key[0]) == "estatesSearch":
            response = query.get("state", {}).get("data") or {}
            if isinstance(response, dict):
                return response
    raise RuntimeError("Sreality estatesSearch state not found")

def link_map(html, market):
    result = {}
    for m in re.finditer(r'id=["\']estate-list-item-(\d+)["\']', html):
        nearby = html[m.end():m.end()+5500]
        found = re.search(r'href=["\'](' + LINK_PATTERN[market] + r')["\']', nearby)
        if found:
            result[m.group(1)] = urljoin(BASE, found.group(1).replace("&amp;", "&"))
    return result

def best_image(item):
    images = item.get("images") or []
    for im in images:
        url = im.get("url") if isinstance(im, dict) else im
        if isinstance(url, str) and url.startswith("//"):
            url = "https:" + url
        if isinstance(url, str) and url.startswith("https://"):
            return url
    return ""

def property_row(item, url, market, timestamp):
    ident = str(item.get("id") or "")
    if not ident.isdecimal() or not url:
        return None
    gps = coords(item)
    if gps and distance_km(*gps) > RADIUS_KM:
        return None
    loc = item.get("locality") or {}
    if isinstance(loc, dict):
        loc = ", ".join(filter(None, [loc.get("cityPart") or loc.get("city"), loc.get("district")]))
    name = str(item.get("name") or "House")
    usable = as_float(item.get("estateArea") or item.get("usableArea"))
    plot = as_float(item.get("landArea") or item.get("plotArea"))
    # Sreality search card 'name' often includes usable and land areas.
    m = re.search(r'(\d[\d\s]*)\s*m[²2]', name)
    if not usable and m:
        usable = as_float(m.group(1).replace(" ", ""))
    plots = re.search(r'pozemek\s+(\d[\d\s]*)\s*m[²2]', name, re.I)
    if not plot and plots:
        plot = as_float(plots.group(1).replace(" ", ""))
    price = item.get("priceCzk")
    try:
        price = int(price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return {
        "id": "sreality:" + ident, "source": "sreality.cz",
        "source_id": ident, "market": market, "name": name,
        "locality": str(loc or "Unknown locality"), "price": price,
        "usable_m2": usable, "plot_m2": plot, "url": url,
        "image_url": best_image(item), "photo_path": "",
        "lat": gps[0] if gps else None, "lon": gps[1] if gps else None,
        "distance_checked": bool(gps), "observed_at": timestamp,
    }

def fetch_market(market, timestamp, sleep=0.6):
    """Fetch a complete search; never return a partial result as success."""
    collection = {}
    max_pages = 60
    source_page_count = 0
    for page in range(1, max_pages + 1):
        url = BASE + SEARCH[market]
        params = {"region": "Ostrava", "region-id": 4730, "region-typ": "municipality",
                  "vzdalenost": 15, "strana": page}
        response = SESSION.get(url, params=params, timeout=40)
        response.raise_for_status()
        payload = unwrap_search(response.text)
        items = payload.get("results")
        if not isinstance(items, list):
            raise RuntimeError("Sreality results missing; retaining previous snapshot")
        total = (payload.get("pagination") or {}).get("total")
        if page == 1 and not items:
            raise RuntimeError("Zero search results; refusing to treat as empty market")
        hrefs = link_map(response.text, market)
        for item in items:
            ident = str(item.get("id") or "")
            url = hrefs.get(ident)
            if not url:
                # Stable fallback URL; only recorded if id is valid.
                if not ident.isdecimal():
                    continue
                url = BASE + ("/detail/prodej/dum/rodinny/unknown/" if market == "sale"
                              else "/detail/pronajem/dum/rodinny/unknown/") + ident
            row = property_row(item, url, market, timestamp)
            if row:
                collection[row["id"]] = row
        source_page_count += 1
        if not items or (isinstance(total, int) and page * len(items) >= total):
            break
        # Total may be missing. Last partial page means last page.
        if len(items) < 20:
            break
        if page == max_pages:
            raise RuntimeError("Reached page safety cap, scan incomplete")
        time.sleep(sleep)
    if not collection:
        raise RuntimeError("No usable property cards found; retaining previous snapshot")
    return collection, source_page_count

def legacy_index():
    """One-time compact index of the original unmodified price-history file.
    Unmatched legacy adverts are NOT counted in the 15 km market inventory.
    """
    target = DATA / "legacy_sale_index.json"
    if target.exists():
        return
    raw = read_json(ROOT / "sreality_price_history.json", {})
    details = read_json(ROOT / "sreality_data.json", {})
    index = []
    for ident, snapshots in raw.items():
        valid = [s for s in snapshots if isinstance(s, dict) and isinstance(s.get("price"), (int,float))]
        if not valid:
            continue
        rec = details.get(ident, {})
        index.append({
            "id": "sreality:" + ident, "source_id": ident,
            "first_seen": valid[0].get("date"), "last_seen": valid[-1].get("date"),
            "first_price": valid[0]["price"], "last_price": valid[-1]["price"],
            "observations": len(valid), "locality": rec.get("locality", "Unverified"),
            "name": rec.get("name", "Historic listing"), "image_url": rec.get("image_url", ""),
            "url": rec.get("url", ""), "geo_unverified": True, "status": "historic_unverified"
        })
    index.sort(key=lambda r: r.get("last_seen") or "", reverse=True)
    write_json(target, index)
    print("Legacy archive indexed:", len(index))

@lru_cache(maxsize=1)
def legacy_history():
    return read_json(ROOT / "sreality_price_history.json", {})

def legacy_events(ident):
    # Only import detailed price changes for properties seen in a new valid local scan.
    all_prices = legacy_history()
    rows = all_prices.get(ident, [])
    unique = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("price"), (float,int)):
            continue
        if not unique or unique[-1]["price"] != row["price"]:
            unique.append({"at": row.get("date", ""), "price": int(row["price"]), "source": "legacy"})
    return unique

def archive_photo(row, market):
    """Best effort identifying thumbnail; always keep attribution/source and never overwrite.
    Photos belong to their respective copyright owners; public reuse requires rights.
    """
    if os.environ.get("ARCHIVE_IMAGES", "0") != "1" or not row.get("image_url"):
        return ""
    folder = ROOT / "assets" / "photos" / market
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (row["source_id"] + ".jpg")
    if path.exists() and path.stat().st_size > 1000:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    try:
        from io import BytesIO
        from PIL import Image, ImageOps
        response = SESSION.get(row["image_url"], timeout=15)
        response.raise_for_status()
        if not response.headers.get("content-type","").startswith("image/") or len(response.content)>6_000_000:
            return ""
        with Image.open(BytesIO(response.content)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((720, 540))
            image.save(path, format="JPEG", quality=78, optimize=True)
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except Exception as error:
        print("Photo unavailable:", row["id"], type(error).__name__)
        return ""

def update(market, incoming, timestamp, day):
    folder = DATA / market
    history = read_json(folder / "history.json", {})
    old_current = read_json(folder / "current.json", {})
    if not isinstance(old_current, dict):
        old_current = {}
    new_ids = set(incoming)
    counts = {"new": 0, "reduced": 0, "increased": 0, "returned": 0, "absent": 0}
    changes = []
    for ident, row in incoming.items():
        entry = history.get(ident)
        if entry is None:
            legacy = legacy_events(row["source_id"]) if market=="sale" else []
            events = legacy or []
            entry = {"id": ident, "market": market, "first_seen": events[0]["at"] if events else timestamp,
                     "last_seen": timestamp, "status":"active", "events": events,
                     "missing_scans": 0}
            if not events or (events[-1]["price"] != row["price"]):
                entry["events"].append({"at":timestamp, "price":row["price"],"source":"observed"})
            counts["new"] += 1
            changes.append({"kind":"new","id":ident,"old":None,"new":row["price"]})
        else:
            last_price = entry["events"][-1]["price"] if entry.get("events") else row["price"]
            if row["price"] != last_price:
                kind = "reduced" if row["price"] < last_price else "increased"
                counts[kind] += 1
                changes.append({"kind":kind,"id":ident,"old":last_price,"new":row["price"],
                                "percent":round((row["price"]/last_price-1)*100,2) if last_price else None})
                entry.setdefault("events", []).append({"at":timestamp,"price":row["price"],"source":"observed"})
            if entry.get("status") != "active":
                counts["returned"] += 1
                changes.append({"kind":"returned","id":ident,"old":last_price,"new":row["price"]})
            entry["status"] = "active"
            entry["missing_scans"] = 0
        entry["last_seen"] = timestamp
        if not row["image_url"] and entry.get("image_url"):
            row["image_url"] = entry["image_url"]
        row["photo_path"] = entry.get("photo_path","") or archive_photo(row, market)
        for field in ("name","locality","url","image_url","photo_path","usable_m2","plot_m2","lat","lon"):
            if row.get(field) not in (None,""):
                entry[field] = row[field]
        history[ident] = entry
    # Only mark missing when the source scan finished successfully.
    for ident, entry in history.items():
        if ident in new_ids or entry.get("status") == "historic_unverified":
            continue
        was_active = entry.get("status") == "active"
        entry["missing_scans"] = entry.get("missing_scans",0) + 1
        entry["status"] = "absent_unverified"
        if was_active:
            counts["absent"] += 1
            changes.append({"kind":"absent_unverified","id":ident,"old":entry.get("events",[{}])[-1].get("price"),"new":None})
    for ident, row in incoming.items():
        row["first_seen"] = history[ident]["first_seen"]
        row["events"] = history[ident]["events"]
        row["status"] = "active"
    payload = {"date":day,"observed_at":timestamp,"market":market,"source":"sreality.cz",
               "scope":"Ostrava + 15 km advertised search; some listings without GPS not independently radius-verified",
               "counts":counts,"inventory":len(incoming),
               "large_houses":sum((v.get("usable_m2") or 0)>=150 for v in incoming.values()),
               "over_10m":sum(v["price"]>=10_000_000 for v in incoming.values()) if market=="sale" else None,
               "changes":changes,"status":"ok"}
    write_json(folder/"history.json",history)
    write_json(folder/"current.json",incoming)
    write_json(folder/"latest.json",payload)
    write_json(folder/"daily"/(day+".json"),payload)
    return payload

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--market",choices=["sale","rent","both"],default="both")
    parser.add_argument("--test-fixture",help="JSON with {'sale':{id:row}, 'rent':{id:row}} for offline tests")
    args=parser.parse_args()
    now=datetime.now(TZ)
    timestamp=now.isoformat(timespec="seconds")
    day=now.date().isoformat()
    legacy_index()
    fixture=read_json(args.test_fixture,{}) if args.test_fixture else None
    markets=MARKETS if args.market=="both" else (args.market,)
    failures=[]
    for market in markets:
        try:
            if fixture is not None:
                incoming=fixture.get(market,{})
                if not incoming:
                    raise RuntimeError("Empty test fixture")
            else:
                incoming,pages=fetch_market(market,timestamp)
                print(market,"pages fetched:",pages)
            report=update(market,incoming,timestamp,day)
            print(market,json.dumps({"counts":report["counts"],"inventory":report["inventory"]}))
        except Exception as exc:
            failures.append(market)
            print(market,"scan failed, historical data unchanged:",repr(exc),file=sys.stderr)
            write_json(DATA/market/"errors"/(day+".json"),
                       {"date":day,"market":market,"status":"unverified","error":str(exc)})
    if failures:
        sys.exit(1)

if __name__=="__main__":
    main()
