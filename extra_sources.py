"""Best-effort, respectful discovery of advertised houses outside Sreality.

All external sources are *partial* search-page observations. A missing result
must never be interpreted as a sale, withdrawal or complete inventory scan.
This module stores only factual advert metadata and links, not third-party
marketing photos or descriptions. A skipped/blocked source remains visible
in the per-source coverage report.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
import time
import unicodedata
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import requests

USER_AGENT = "OstravaPropertyHistory/1.1 (+market research; once daily)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
TIMEOUT = 18

@dataclass(frozen=True)
class Source:
    key: str
    label: str
    sale: str
    rent: str
    pattern: str
    max_pages: int = 1

SOURCES = (
    Source("idnes", "Reality iDNES",
           "https://reality.idnes.cz/s/prodej/domy/ostrava/",
           "https://reality.idnes.cz/s/pronajem/domy/ostrava/",
           r"/detail/(?:prodej|pronajem)/(?:dum|vila)/", 4),
    Source("bezrealitky", "Bezrealitky",
           "https://www.bezrealitky.cz/vypis/nabidka-prodej/dum/okres-ostrava-mesto",
           "https://www.bezrealitky.cz/vypis/nabidka-pronajem/dum/okres-ostrava-mesto",
           r"/nemovitosti-byty-domy/\d+-nabidka-(?:prodej|pronajem)-dom", 1),
    Source("mmreality", "M&M Reality",
           "https://www.mmreality.cz/nemovitosti/prodej/rodinne-domy/ostrava-mesto/",
           "https://www.mmreality.cz/nemovitosti/pronajem/rodinne-domy/ostrava-mesto/",
           r"/nemovitosti/\d+/?$", 2),
    Source("remax", "RE/MAX",
           "https://www.remax-czech.cz/reality/domy-a-vily/prodej/moravskoslezky-kraj/ostrava/",
           "https://www.remax-czech.cz/reality/domy-a-vily/pronajem/moravskoslezky-kraj/ostrava/",
           r"/reality/(?!vyhledavani|domy-a-vily)[^?#]*(?:\d|detail)/", 1),
    Source("bazos", "Bazoš (classifieds)",
           "https://reality.bazos.cz/prodam/dum/?hledat=Ostrava&hlokalita=Ostrava&humkreis=15",
           "https://reality.bazos.cz/pronajmu/dum/?hledat=Ostrava&hlokalita=Ostrava&humkreis=15",
           r"/inzerat/\d+/", 1),
)
# This is a broad shortlist, NOT a proof that any individual property is
# within 15 km. Precise source coordinates must pass the radius test.
NEARBY = ("ostrava", "senov", "šenov", "hlucin", "hlučín", "klimkovice",
          "vratimov", "rychvald", "petřvald", "petrvald",
          "dolni lhota", "dolní lhota", "horní lhota", "horni lhota",
          "vresina", "vřesina", "markvartovice", "ludgeřovice",
          "ludgerovice", "krmelin", "krmelín", "paskov", "stara ves",
          "stará ves", "dehylov", "děhylov", "bohumin", "bohumín")
PRICE = re.compile(r"(?<!\d)(\d[\d \u00a0\u202f]{2,})\s*Kč\b", re.I)
AREA = re.compile(r"(\d[\d \u00a0\u202f]*)\s*m[²2]\b", re.I)
PLOT = re.compile(r"(?:pozem(?:ek|kem|ku)|parcela|parcel[ay])\s*(\d[\d \u00a0\u202f]+)\s*m[²2]\b", re.I)
HOUSE = re.compile(r"\b(d[ůu]m|domu|domy|vila|vily|rodinn[ýé])\b", re.I)
ROBOTS_CACHE = {}

def compact(value):
    return " ".join(str(value or "").split())

def normalize(value):
    s = unicodedata.normalize("NFKD", compact(value).casefold())
    return "".join(c for c in s if not unicodedata.combining(c))

def price_from(text):
    for match in PRICE.finditer(text):
        number = re.sub(r"\D", "", match.group(1))
        if number and 1000 <= int(number) <= 1_000_000_000:
            return int(number)
    return None

def area_from(regex, text):
    m = regex.search(text)
    return int(re.sub(r"\D", "", m.group(1))) if m else None

def extract_street(text):
    s = compact(text)
    m = re.search(r"(?:\bul(?:ice)?\.?\s+)([^\d,()]{3,65})", s, re.I)
    if m:
        return compact(m.group(1))
    m = re.search(r"(?:d[oů]mu\s*)([^\d,()]{3,55}),\s*(?:Ostrava|Šenov|Klimkovice|Vratimov|Hlučín)", s, re.I)
    if m:
        return compact(m.group(1))
    # Reality iDNES often puts street immediately after 'pozemkem ... m²'.
    m = re.search(r"(?:s pozemkem\s+\d[\d\s]*\s*m[²2]\s+)([^,]{3,55}),\s*(?:Ostrava|Šenov|Klimkovice|Vratimov|Hlučín)", s, re.I)
    if m:
        return compact(m.group(1))
    return ""

def page_text_for_link(link):
    """Smallest container including link plus advertised price, not page footer."""
    text = compact(link.get_text(" ", strip=True))
    if price_from(text):
        return text[:1100]
    current = link
    for _ in range(5):
        current = current.parent
        if current is None:
            break
        value = compact(current.get_text(" ", strip=True))
        if 50 <= len(value) <= 2200 and price_from(value):
            return value
    return text[:1100]

def primary_text(link):
    text = compact(link.get_text(" ", strip=True))
    if text and len(text) > 12:
        return text
    for parent in (link.parent, getattr(link.parent, "parent", None)):
        if parent:
            h = parent.find(["h2", "h3", "h4"])
            if h:
                return compact(h.get_text(" ", strip=True))
    return text

def get_id(source, url):
    parsed = urlsplit(url)
    if source.key == "idnes":
        match = re.search(r"/([a-zA-Z0-9]{18,})/?$", parsed.path)
    elif source.key == "remax":
        match = re.search(r"/([a-zA-Z0-9-]{6,})/?$", parsed.path)
    else:
        match = re.search(r"/(\d{5,})(?:/|$|[-.])", parsed.path)
        if not match:
            match = re.search(r"(\d{5,})", parsed.path)
    return match.group(1) if match else hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

def valid_region(text):
    s = normalize(text)
    return any(normalize(x) in s for x in NEARBY)

def parse_cards(html, source, market, timestamp, page_url):
    soup = BeautifulSoup(html, "html.parser")
    candidates = {}
    for link in soup.select("a[href]"):
        href = urljoin(page_url, link.get("href", ""))
        if urlsplit(href).hostname != urlsplit(page_url).hostname:
            continue
        if not re.search(source.pattern, urlsplit(href).path, re.I):
            continue
        title = primary_text(link)
        block = page_text_for_link(link)
        # Never infer a listing from a navigation link, advertisement or a
        # seller-provided generic long-form description.
        if not HOUSE.search(title) or not valid_region(block):
            continue
        if re.search(r"\b(chata|chalupa|pozemek bez domu|rekreační pozemek)\b", title, re.I):
            continue
        if re.search(r"\b(prod[aá]no|sold|rezervov[aá]no)\b", title, re.I):
            continue
        price = price_from(block)
        if price is None:
            continue
        # Rent and purchase must never be mixed, including REMAX pages.
        if source.key == "remax":
            t = normalize(block[:90])
            if market == "rent" and "pronajem" not in t:
                continue
            if market == "sale" and "prodej" not in t:
                continue
        ident = get_id(source, href)
        area = area_from(AREA, title) or area_from(AREA, block)
        plot = area_from(PLOT, title) or area_from(PLOT, block)
        if area is not None and not (20 <= area <= 5000):
            area = None
        if plot is not None and not (30 <= plot <= 500000):
            plot = None
        street = extract_street(title)
        source_id = f"{source.key}:{ident}"
        candidates[source_id] = {
            "id": source_id, "source_id": ident, "source": source.label,
            "market": market, "name": title[:210],
            "locality": compact(title[-110:])[:110],
            "street": street, "price": price,
            "usable_m2": area, "plot_m2": plot,
            "url": href, "image_url": "", "photo_path": "",
            "lat": None, "lon": None, "distance_checked": False,
            "radius_unverified": True, "coverage": "partial_portal",
            "observed_at": timestamp,
        }
    return candidates

def allowed_by_robots(session, url):
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in ROBOTS_CACHE:
        response = session.get(origin + "/robots.txt", headers=HEADERS, timeout=TIMEOUT)
        if response.status_code == 404:
            ROBOTS_CACHE[origin] = None
        elif response.status_code != 200:
            raise RuntimeError(f"robots.txt HTTP {response.status_code}")
        else:
            rp = RobotFileParser()
            rp.parse(response.text.splitlines())
            ROBOTS_CACHE[origin] = rp
    rp = ROBOTS_CACHE[origin]
    return rp is None or rp.can_fetch(USER_AGENT, url)

def fetch_extra(market, timestamp, session=None, pause=0.8):
    """Return (rows, statuses); blocked/failed sources remain explicitly unknown."""
    client = session or requests.Session()
    results, statuses = {}, {}
    for source in SOURCES:
        url = source.sale if market == "sale" else source.rent
        status = {"source": source.label, "key": source.key, "url": url,
                  "state": "unknown", "results": 0,
                  "coverage": "partial; search cards only; radius not independently verified"}
        try:
            if not allowed_by_robots(client, url):
                status.update(state="robots_disallowed", note="Not scanned")
                continue
            count = 0
            for page in range(1, source.max_pages + 1):
                link = url
                if source.key in ("idnes", "mmreality") and page > 1:
                    sep = "&" if "?" in url else "?"
                    link = url + sep + f"page={page}"
                if page > 1 and not allowed_by_robots(client, link):
                    break
                response = client.get(link, headers=HEADERS, timeout=TIMEOUT)
                if response.status_code in (401, 403, 429):
                    status.update(state="access_denied", note=f"HTTP {response.status_code}")
                    break
                response.raise_for_status()
                parsed = parse_cards(response.text, source, market, timestamp, link)
                if not parsed:
                    if page == 1:
                        status.update(state="no_verified_cards",
                                      note="No parseable matching offers; not treated as zero inventory")
                    break
                results.update(parsed)
                count += len(parsed)
                if page < source.max_pages:
                    time.sleep(pause)
            if count:
                status.update(state="partial_ok", results=count,
                              note="Observed entries, not complete 15 km coverage")
        except requests.RequestException as exc:
            status.update(state="network_error", note=str(exc)[:180])
        except Exception as exc:
            status.update(state="parse_error", note=str(exc)[:180])
        finally:
            statuses[source.key] = status
    return results, statuses

def strict_duplicate(left, right):
    """Conservative: street + house area + plot area + city tokens required."""
    if left.get("market") != right.get("market"):
        return False
    a, b = normalize(left.get("street")), normalize(right.get("street"))
    if len(a) < 5 or len(b) < 5 or a != b:
        return False
    aa, bb = left.get("usable_m2"), right.get("usable_m2")
    pa, pb = left.get("plot_m2"), right.get("plot_m2")
    if not all(isinstance(v, (int,float)) and v > 0 for v in (aa,bb,pa,pb)):
        return False
    if abs(aa-bb) > max(3, .025*max(aa,bb)):
        return False
    if abs(pa-pb) > max(8, .035*max(pa,pb)):
        return False
    # Reject a clearly different municipality. We don't assume every
    # similarly named street around Ostrava refers to the same address.
    loc1, loc2 = normalize(left.get("locality")), normalize(right.get("locality"))
    towns = {normalize(n) for n in NEARBY if len(n)>4}
    mentioned1 = {t for t in towns if re.search(r"\b"+re.escape(t)+r"\b", loc1)}
    mentioned2 = {t for t in towns if re.search(r"\b"+re.escape(t)+r"\b", loc2)}
    if mentioned1 and mentioned2 and not (mentioned1 & mentioned2):
        return False
    return True

def dedupe_incoming(rows):
    """Collapse only high-confidence cross-source duplicates, keep all source links."""
    combined = {}
    for row in sorted(rows.values(), key=lambda r: (r.get("source")!="sreality.cz", r["id"])):
        duplicate = next((v for v in combined.values()
                          if v.get("source") != row.get("source")
                          and strict_duplicate(v,row)), None)
        source_link = {"source": row.get("source"), "id": row.get("id"),
                       "url": row.get("url")}
        if duplicate:
            duplicate.setdefault("sources",[]).append(source_link)
        else:
            copied = dict(row)
            copied["sources"] = [source_link]
            combined[copied["id"]] = copied
    return combined
