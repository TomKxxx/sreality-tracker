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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlsplit
import requests
from extra_sources import fetch_extra, dedupe_incoming, strict_duplicate

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
    if not ident.isdecimal():
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
    expected_page_size = None
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
        pageinfo = payload.get("pagination") or {}
        raw_total = pageinfo.get("total")
        try:
            total = int(raw_total)
        except (ValueError, TypeError):
            total = None
        if expected_page_size is None:
            try:
                expected_page_size = int(pageinfo.get("limit") or pageinfo.get("pageSize") or len(items))
            except (ValueError, TypeError):
                expected_page_size = len(items)
        if page == 1 and not items:
            raise RuntimeError("Zero search results; refusing to treat as empty market")
        hrefs = link_map(response.text, market)
        for item in items:
            ident = str(item.get("id") or "")
            # Do not fabricate a listing URL when the site omits it.
            url = hrefs.get(ident) or ""
            row = property_row(item, url, market, timestamp)
            if row:
                collection[row["id"]] = row
        source_page_count += 1
        if not items or (total is not None and page * expected_page_size >= total):
            break
        # A full scan is required to infer absences. An incomplete page is
        # a valid end only when the source does not provide a total.
        if total is None and len(items) < expected_page_size:
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

# Prevent a blocked CDN from being hammered hundreds of times in one scan.
PHOTO_BLOCKED_HOSTS = set()
PHOTO_HTTP_ERRORS = {}

def photo_candidates(url):
    """Use the Seznam CDN's documented-style image transform URL, then raw original."""
    if not url or not url.startswith("https://"):
        return []
    from urllib.parse import urlsplit, urlunsplit
    parsed = urlsplit(url)
    if not (parsed.hostname == "sdn.cz" or parsed.hostname.endswith(".sdn.cz")):
        return [url]
    base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    options = [
        base + "?fl=res,400,300,3|shr,,20|jpg,90",
        base + "?fl=res,720,540,3|shr,,20|jpg,90",
        url,
    ]
    return list(dict.fromkeys(options))

def archive_photo(row, market, *, force=False, reject_faces=False):
    """Archive an actual validated image, never a CDN error page.
    Respect 403/429 blocks rather than repeatedly retrying a denied host.
    Photos remain owned by their respective copyright owners.
    """
    if os.environ.get("ARCHIVE_IMAGES", "0") != "1" or not row.get("image_url"):
        return ""
    from io import BytesIO
    from urllib.parse import urlsplit
    from PIL import Image, ImageOps, UnidentifiedImageError
    folder = ROOT / "assets" / "photos" / market
    folder.mkdir(parents=True, exist_ok=True)
    listing_source = str(row.get("id", "sreality:")).split(":")[0]
    basename = str(row["source_id"]) if listing_source == "sreality" else listing_source + "-" + str(row["source_id"])
    path = folder / (basename + ".jpg")
    if not force and path.exists() and path.stat().st_size > 1000:
        try:
            with Image.open(path) as check:
                check.verify()
            if reject_faces:
                from photo_quality import looks_like_portrait
                if looks_like_portrait(path):
                    print("Skipping cached portrait:", row["id"])
                    return ""
            return str(path.relative_to(ROOT)).replace("\\", "/")
        except (OSError, UnidentifiedImageError):
            path.unlink(missing_ok=True)
    url = row["image_url"]
    host = urlsplit(url).hostname or ""
    if host in PHOTO_BLOCKED_HOSTS:
        return ""
    denied = []
    for candidate in photo_candidates(url):
        try:
            response = SESSION.get(
                candidate, timeout=12,
                headers={"Referer": row.get("url") or "https://www.sreality.cz/",
                         "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
                         "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"},
            )
            if response.status_code in (401, 403, 429):
                denied.append(response.status_code)
                continue
            if response.status_code >= 500:
                # Server unavailable. Do not flood a failing image service.
                PHOTO_BLOCKED_HOSTS.add(host)
                print("Photo CDN unavailable:", host, "HTTP", response.status_code)
                return ""
            if response.status_code >= 400:
                count = PHOTO_HTTP_ERRORS.get(response.status_code, 0)
                PHOTO_HTTP_ERRORS[response.status_code] = count + 1
                if count < 3:
                    print("Photo URL failed:", row["id"], "HTTP", response.status_code,
                          "candidate:", candidate.split("?")[0])
                continue
            kind = response.headers.get("content-type", "").lower()
            if not kind.startswith("image/") or len(response.content) > 6_000_000:
                print("Photo invalid response:", row["id"], kind[:60])
                continue
            with Image.open(BytesIO(response.content)) as original:
                image = ImageOps.exif_transpose(original).convert("RGB")
                image.thumbnail((720, 540))
                if min(image.size) < 80:
                    continue
                temporary = path.with_suffix(".tmp")
                try:
                    image.save(temporary, format="JPEG", quality=80, optimize=True)
                    if reject_faces:
                        from photo_quality import looks_like_portrait
                        if looks_like_portrait(temporary):
                            print("Rejected face-dominated photo:", row["id"])
                            continue
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
            return str(path.relative_to(ROOT)).replace("\\", "/")
        except (requests.RequestException, OSError, ValueError) as error:
            print("Photo download error:", row["id"], type(error).__name__, str(error)[:120])
    if denied and len(denied) == len(photo_candidates(url)):
        PHOTO_BLOCKED_HOSTS.add(host)
        print("Photo CDN blocked all variants:", host, "HTTP codes", denied,
              "- skipping further downloads from this host")
    return ""

def choose_property_photo(row, market, previous_photo, incoming, old_current):
    """Prefer an already archived house image; never keep a prominent face.

    The high-confidence cross-source match is particularly useful for agency
    advert cards which show the estate agent rather than the house.
    """
    import photo_quality as pq
    previous_file = ROOT / previous_photo if previous_photo else None
    existing = bool(previous_file and previous_file.is_file())
    agent_url = pq.suspect_url(row.get("image_url", ""))
    # Old files can have valid image headers but still be agent portraits.
    if existing and not pq.looks_like_portrait(previous_file) and not (
        agent_url and row["source_id"] in previous_photo):
        return previous_photo
    if existing:
        print("Replacing suspect existing cover:", row["id"],
              "agent_url=", agent_url)

    # First try the card thumbnail, but validate before replacing old files.
    if row.get("image_url") and not agent_url:
        saved = archive_photo(row, market, force=existing, reject_faces=True)
        if saved:
            return saved

    # Cross-source photos already live locally and do not require another HTTP
    # request. Require same price, almost-identical area and matching street.
    match = pq.archived_match(
        row, list(incoming.values()) + list(old_current.values()), ROOT)
    if match:
        row["photo_source_listing"] = match["id"]
        row["image_url"] = match.get("image_url", "")
        print("Using matching property photo:", row["id"], "from", match["id"])
        return match["photo_path"]

    # If no matching property exists, inspect a small number of real advert
    # galleries, respecting their robots.txt and rate limits.
    if (row.get("source") != "sreality.cz" and
            os.environ.get("ARCHIVE_IMAGES", "0") == "1"):
        for image_url in pq.gallery_alternatives(row):
            if image_url == row.get("image_url") or pq.suspect_url(image_url):
                continue
            alternative = dict(row)
            alternative["image_url"] = image_url
            saved = archive_photo(alternative, market, force=True,
                                  reject_faces=True)
            if saved:
                row["image_url"] = image_url
                print("Recovered house photo from advert:", row["id"])
                return saved

    # Never display a known portrait as the property cover. A neutral empty
    # placeholder is more truthful than an estate agent's headshot.
    row["image_url"] = ""
    return ""


def canonical_listing_url(value):
    """Stable URL key: ignore query strings, fragments and trailing slashes."""
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return parsed.netloc.lower().removeprefix("www.") + parsed.path.rstrip("/").lower()


def prior_listing_aliases(history):
    """Include cross-source IDs/URLs retained on older deduplicated adverts."""
    aliases = {}
    for original_id, entry in history.items():
        aliases.setdefault("id:" + original_id, original_id)
        key = canonical_listing_url(entry.get("url"))
        if key:
            aliases.setdefault("url:" + key, original_id)
        for linked in entry.get("sources") or []:
            if linked.get("id"):
                aliases.setdefault("id:" + str(linked["id"]), original_id)
            key = canonical_listing_url(linked.get("url"))
            if key:
                aliases.setdefault("url:" + key, original_id)
    return aliases


def comparable_primary_baseline(previous, timestamp):
    """A complete Sreality scan within 72h is a comparable observation.
    Even this proves first observed, not the property's publication date.
    """
    if previous.get("status") != "ok":
        return False
    try:
        delta = datetime.fromisoformat(timestamp) - datetime.fromisoformat(previous["observed_at"])
        return timedelta(0) <= delta <= timedelta(hours=72)
    except (KeyError, TypeError, ValueError):
        return False


def archive_prior_scan(folder, market, day, timestamp):
    """Preserve reports and JSON on a second run on the same calendar date."""
    previous_file = folder / "daily" / (day + ".json")
    if not previous_file.exists():
        return
    prior = read_json(previous_file, {})
    old_at = str(prior.get("observed_at", "unknown")).replace(":", "-").replace("+", "_")
    backup = folder / "snapshots" / (day + "_" + old_at + ".json")
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(previous_file.read_bytes())
    report = ROOT / "reports" / market / (day + ".html")
    if report.is_file():
        archive = report.parent / "snapshots" / (day + "_" + old_at + ".html")
        if not archive.exists():
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(report.read_bytes())


def update(market, incoming, timestamp, day, source_status=None):
    folder = DATA / market
    history = read_json(folder / "history.json", {})
    old_current = read_json(folder / "current.json", {})
    if not isinstance(old_current, dict):
        old_current = {}
    new_ids = set(incoming)
    previous = read_json(folder / "latest.json", {})
    has_primary_baseline = comparable_primary_baseline(previous, timestamp)
    aliases = prior_listing_aliases(history)
    counts = {"new": 0, "discovered": 0, "historical_return": 0,
              "reduced": 0, "increased": 0, "returned": 0, "absent": 0}
    changes = []
    for ident, row in incoming.items():
        entry = history.get(ident)
        if entry is None:
            legacy = legacy_events(row["source_id"]) if market=="sale" and row.get("source")=="sreality.cz" else []
            events = legacy or []
            entry = {"id": ident, "market": market, "first_seen": events[0]["at"] if events else timestamp,
                     "last_seen": timestamp, "status":"active", "events": events,
                     "missing_scans": 0}
            if not events or (events[-1]["price"] != row["price"]):
                entry["events"].append({"at":timestamp, "price":row["price"],"source":"observed"})
            linked = aliases.get("id:" + ident) or aliases.get(
                "url:" + canonical_listing_url(row.get("url")))
            if not linked:
                linked = next((aliases.get("id:" + str(source.get("id")))
                               for source in row.get("sources", [])
                               if aliases.get("id:" + str(source.get("id")))), None)
            if not linked:
                # A relisted advert may receive a different portal ID. Require
                # unambiguous street, municipality, usable area AND plot match.
                candidates = [old_id for old_id, old in history.items()
                              if old_id != ident and old.get("market") == market
                              and old.get("usable_m2") and old.get("plot_m2")
                              and row.get("usable_m2") and row.get("plot_m2")
                              and abs(old["usable_m2"] - row["usable_m2"]) <= max(3, .025 * row["usable_m2"])
                              and abs(old["plot_m2"] - row["plot_m2"]) <= max(8, .035 * row["plot_m2"])
                              and strict_duplicate(old, row)]
                if len(candidates) == 1:
                    linked = candidates[0]
            if linked and linked != ident:
                entry["related_listing_id"] = linked
            # Historic Sreality IDs or cross-listed adverts are not new homes.
            if events or linked:
                kind = "historical_return"
            elif row.get("source") == "sreality.cz" and has_primary_baseline:
                kind = "new"  # First seen on consecutive *complete* Sreality scans.
            else:
                kind = "discovered"  # New to our crawler, NOT necessarily newly posted.
            counts[kind] += 1
            changes.append({"kind":kind,"id":ident,"old":None,"new":row["price"],
                            "interpretation": "first_observed_not_publication_date"})
        else:
            last_price = entry["events"][-1]["price"] if entry.get("events") else row["price"]
            if row["price"] != last_price:
                kind = "reduced" if row["price"] < last_price else "increased"
                counts[kind] += 1
                changes.append({"kind":kind,"id":ident,"old":last_price,"new":row["price"],
                                "percent":round((row["price"]/last_price-1)*100,2) if last_price else None})
                entry.setdefault("events", []).append({"at":timestamp,"price":row["price"],"source":"observed"})
            if entry.get("status") != "active" and row.get("source") == "sreality.cz":
                counts["returned"] += 1
                changes.append({"kind":"returned","id":ident,"old":last_price,"new":row["price"]})
            entry["status"] = "active"
            entry["missing_scans"] = 0
        entry["last_seen"] = timestamp
        if not row["image_url"] and entry.get("image_url"):
            row["image_url"] = entry["image_url"]
        previous_photo = entry.get("photo_path", "")
        if previous_photo and not (ROOT / previous_photo).is_file():
            previous_photo = ""
        row["photo_path"] = choose_property_photo(
            row, market, previous_photo, incoming, old_current)
        for field in ("source","name","locality","street","url","image_url","photo_path","usable_m2","plot_m2","lat","lon","sources","radius_unverified","coverage","photo_source_listing"):
            if row.get(field) not in (None,""):
                entry[field] = row[field]
        # Clear rejected agent thumbnails from saved listing metadata as well.
        entry["photo_path"] = row["photo_path"]
        entry["image_url"] = row["image_url"]
        history[ident] = entry
    # Best-effort photo backfill for previously observed listings, including
    # withdrawn adverts. Never change their listing status or observation dates.
    if os.environ.get("ARCHIVE_IMAGES", "0") == "1":
        for ident, entry in history.items():
            if ident in incoming or not entry.get("image_url"):
                continue
            old_path = entry.get("photo_path", "")
            if old_path and (ROOT / old_path).is_file():
                from photo_quality import looks_like_portrait, suspect_url
                if looks_like_portrait(ROOT / old_path) or (
                        suspect_url(entry.get("image_url", "")) and
                        str(ident.split(":")[-1]) in old_path):
                    entry["photo_path"] = ""
                    if suspect_url(entry.get("image_url", "")):
                        entry["image_url"] = ""
                continue
            candidate = {"id": ident, "source_id": ident.split(":")[-1],
                         "image_url": entry["image_url"]}
            archived = archive_photo(candidate, market, reject_faces=True)
            if archived:
                entry["photo_path"] = archived
    # Only mark missing when the source scan finished successfully.
    for ident, entry in history.items():
        if ident in new_ids or entry.get("status") == "historic_unverified":
            continue
        if entry.get("source", "sreality.cz") != "sreality.cz":
            # Partial portal scans cannot prove withdrawal or completion.
            # After 8 unseen days, keep history but exclude stale entries
            # from live inventory metrics.
            try:
                age = (datetime.fromisoformat(timestamp) -
                       datetime.fromisoformat(entry["last_seen"])).days
            except (KeyError, TypeError, ValueError):
                age = 0
            if age >= 8:
                entry["status"] = "stale_unverified"
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
        if history[ident].get("related_listing_id"):
            row["related_listing_id"] = history[ident]["related_listing_id"]
    # The public dashboard must retain withdrawn adverts, not only active ones.
    # These do NOT count towards active inventory or verified completed sales.
    dashboard_records = dict(incoming)
    for ident, entry in history.items():
        if ident in dashboard_records or entry.get("status") not in ("absent_unverified", "stale_unverified"):
            continue
        events = entry.get("events") or []
        if not events:
            continue
        dashboard_records[ident] = {
            "id": ident, "source_id": ident.split(":")[-1], "source": entry.get("source", "sreality.cz"),
            "sources": entry.get("sources", []), "street": entry.get("street", ""),
            "market": market, "name": entry.get("name", "Historic house"),
            "locality": entry.get("locality", "Unknown locality"),
            "price": events[-1].get("price"), "usable_m2": entry.get("usable_m2"),
            "plot_m2": entry.get("plot_m2"), "url": entry.get("url", ""),
            "image_url": entry.get("image_url", ""),
            "photo_path": entry.get("photo_path", ""), "events": events,
            "first_seen": entry.get("first_seen"), "observed_at": entry.get("last_seen"),
            "status": entry["status"]
        }
    payload = {"date":day,"observed_at":timestamp,"market":market,"source":"sreality.cz",
               "scope":"Ostrava + 15 km advertised search; some listings without GPS not independently radius-verified",
               "classification_version":2,
               "classification_note":"New = first observed in a comparable complete Sreality scan, not confirmed date first advertised. Discovered = new to tracking from incomplete or expanded coverage. Historic matches excluded.",
               "counts":counts,"inventory":len(incoming),
               "source_status":source_status or {},
               "sources_scanned":1+sum(1 for x in (source_status or {}).values() if x.get("state") in ("partial_ok", "partial_with_errors")),
               "source_listing_count":{"sreality":sum(r.get("source")=="sreality.cz" for r in incoming.values()),
                    **{k:v.get("results",0) for k,v in (source_status or {}).items()}},
               "large_houses":sum((v.get("usable_m2") or 0)>=150 for v in incoming.values()),
               "over_10m":sum(v["price"]>=10_000_000 for v in incoming.values()) if market=="sale" else None,
               "changes":changes,"status":"ok"}
    archive_prior_scan(folder, market, day, timestamp)
    write_json(folder/"history.json",history)
    export_csv(market, history)
    generate_report(market, payload, incoming, history)
    write_json(folder/"current.json",dashboard_records)
    write_json(folder/"latest.json",payload)
    write_json(folder/"daily"/(day+".json"),payload)
    safe_at = timestamp.replace(":", "-").replace("+", "_")
    write_json(folder/"snapshots"/(day+"_"+safe_at+".json"),payload)
    return payload


def export_csv(market, history):
    """Cumulative one-row-per-price-observation file; preserve unmodified JSON too."""
    import csv
    output = DATA / market / "history.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["listing_id", "source_id", "market", "locality", "first_seen",
                         "last_seen", "status", "event_date", "asking_price_czk",
                         "event_source", "original_url", "photo_path", "image_source_url"])
        for record in history.values():
            for event in record.get("events", []):
                writer.writerow([record.get("id"), record.get("id","").split(":")[-1],
                                 market, record.get("locality"), record.get("first_seen"),
                                 record.get("last_seen"), record.get("status"), event.get("at"),
                                 event.get("price"), event.get("source"), record.get("url"),
                                 record.get("photo_path"), record.get("image_url")])

def generate_report(market, payload, current, history):
    """Write separately dated HTML reports for the two property markets."""
    import html
    from urllib.parse import urlparse
    date = payload["date"]
    title = "Houses for sale" if market == "sale" else "Houses for rent"
    changes = payload["changes"]
    counts = payload["counts"]
    def esc(value):
        return html.escape(str(value or ""), quote=True)
    def money(value):
        return f"{int(value):,} CZK" if value is not None else "Not stated"
    metrics = [("Active listings", payload["inventory"]), ("First seen (Sreality)", counts["new"]),
               ("New to tracker, unverified", counts["discovered"]),
               ("Reduced", counts["reduced"]), ("Increased", counts["increased"]),
               ("Newly absent", counts["absent"])]
    metric_html = "".join('<div class="metric"><small>'+esc(label)+'</small><strong>'+str(value)+'</strong></div>'
                           for label, value in metrics)
    rows = []
    for change in changes[:120]:
        record = current.get(change["id"]) or history.get(change["id"]) or {}
        name = esc(record.get("name") or change["id"])
        locality = esc(record.get("locality") or "Location not confirmed")
        url = record.get("url") or ""
        href = '<a class="link" href="'+esc(url)+'" rel="noopener" target="_blank">Original advert ↗</a>' if url.startswith("https://") else '<small>Advert URL unavailable</small>'
        photo = record.get("photo_path") or record.get("image_url") or ""
        if photo.startswith("assets/photos/"):
            photo = "../../" + photo
        img = '<img src="'+esc(photo)+'" loading="lazy" alt="Listing photo">' if (photo.startswith("https://") or photo.startswith("../../assets/")) else '<div class="noimage">Photo unavailable</div>'
        oldprice = money(change.get("old"))
        newprice = money(change.get("new"))
        subtitle = {"new":"First observed since comparable Sreality scan",
                    "discovered":"New to tracker — actual advert age unknown",
                    "historical_return":"Previously known advert / cross-list",
                    "reduced":"Price reduced", "increased":"Price increased",
                    "absent_unverified":"Advert no longer observed", "returned":"Returned listing"}.get(change["kind"], change["kind"])
        delta = str(change.get("percent"))+"%" if change.get("percent") is not None else ""
        rows.append('<article class="item">'+img+'<div><span class="eyebrow">'+esc(subtitle)+'</span><h3>'+name+
                    '</h3><p>'+locality+'</p><p><b>'+oldprice+' → '+newprice+'</b> '+esc(delta)+'</p>'+href+'</div></article>')
    html_text = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>"""+title+" | Ostrava Property Lens | "+date+"""</title><style>
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,sans-serif;background:#f5f8f6;color:#153631}
header{background:linear-gradient(130deg,#072c2b,#195548);padding:50px max(20px,calc((100vw - 980px)/2));color:#fff}
header a{color:#a9e2cb}h1{font-size:clamp(33px,5vw,50px);margin:18px 0 8px}
header p{color:#bbd8cb}.wrap{max-width:1000px;margin:30px auto;padding:0 20px}
.metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}.metric{background:white;padding:19px;border:1px solid #dae5dc;border-radius:9px}
.metric small{color:#69877a;display:block}.metric strong{font-size:33px;display:block}
.notice{background:#e9f4ed;padding:14px 18px;border-left:4px solid #269171;margin:24px 0}
.item{display:flex;background:white;border:1px solid #e0eae2;border-radius:10px;overflow:hidden;gap:18px;margin:12px 0;align-items:center}
.item img,.noimage{width:195px;height:145px;object-fit:cover;background:#d9e5de;flex-shrink:0;display:grid;place-items:center;color:#78948a;font-size:11px}
.item>div:last-child{padding:12px 12px 12px 0}.item h3{margin:3px 0;font-size:16px}
.item p{margin:5px 0;color:#687f75}.link{color:#09775c;font-weight:750}.eyebrow{font-size:10px;color:#08735a;font-weight:800;text-transform:uppercase;letter-spacing:1px}
@media(max-width:720px){.metrics{grid-template-columns:repeat(2,1fr)}.item img,.noimage{width:115px;height:125px}.item{gap:9px}.item h3{font-size:13px}}
</style></head><body><header><a href="../../index.html">← Back to Property Lens dashboard</a><h1>"""+title+"""</h1><p>Daily market update · """+date+""" · Ostrava + 15 km</p></header>
<main class="wrap"><section class="metrics">"""+metric_html+"""</section>
<p class="notice"><b>Data limitation:</b> Prices are advertised, not completed transaction prices.
Disappeared adverts are NOT proof of sold or rented status. Search radius may be unverified for individual locations.
"New" means first observed since a comparable complete Sreality scan, not independently verified as newly published.
"New to tracker" may reflect expanded or partial portal coverage. Older Sreality IDs and known cross-lists are excluded.
Additional monthly rental charges are not reliably supplied in these search cards.</p>
<h2>Daily listing changes</h2>"""+("".join(rows) if rows else "<p>No listing changes were detected in this scan.</p>")+"""
<p><a class="link" href="../../data/"""+market+"""/history.csv">Download complete historical observations (CSV) ↗</a></p></main></body></html>"""
    directory = ROOT / "reports" / market
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (date+".html")).write_text(html_text,encoding="utf-8")
    (directory / "latest.html").write_text(html_text,encoding="utf-8")

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
                print(market,"Sreality pages fetched:",pages)
            source_status = {}
            if fixture is None and os.environ.get("EXTRA_SOURCES", "1") == "1":
                try:
                    extra, source_status = fetch_extra(market,timestamp)
                    raw_count = len(incoming) + len(extra)
                    incoming = dedupe_incoming({**incoming, **extra})
                    print(market, "extra sources:", json.dumps(source_status,
                          ensure_ascii=False), "raw:", raw_count,
                          "unique:", len(incoming))
                except Exception as extra_error:
                    # The primary scan and history must survive secondary issues.
                    source_status = {"extra": {"state":"failed", "note":str(extra_error)[:200]}}
                    print(market, "extra discovery unavailable:", repr(extra_error))
            report=update(market,incoming,timestamp,day,source_status=source_status)
            print(market,json.dumps({"counts":report["counts"],"inventory":report["inventory"]}))
        except Exception as exc:
            failures.append(market)
            print(market,"scan failed, historical data unchanged:",repr(exc),file=sys.stderr)
            write_json(DATA/market/"errors"/(day+".json"),
                       {"date":day,"market":market,"status":"unverified","error":str(exc)})
    # Persist image-quality results so each unchanged photo is analysed once.
    try:
        from photo_quality import save_quality_cache
        save_quality_cache()
    except Exception as exc:
        print("Could not save optional image-quality cache:", repr(exc))
    if failures:
        sys.exit(1)

if __name__=="__main__":
    main()
# Trigger 2026-11-03 Ostrava market scan; no functional changes.

# Manual scan request 2026-11-04: run sale and rent and persist daily snapshots.
