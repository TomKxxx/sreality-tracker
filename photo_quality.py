"""Property-first cover selection: reject obvious agent portraits and find safe alternatives.

Only checks whether a photo has a dominant frontal face; it never identifies people.
Cross-source borrowing requires exact advertised price, near-identical usable area,
two distinctive matching location/street URL tokens, and a locally stored file.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from html import unescape
import json
import re
import unicodedata

import requests
from bs4 import BeautifulSoup

BAD_IMAGE = re.compile(
    r"(^|[/_\-])(makler(?:i|ka|_)?|agent(?:s)?|realtor|portrait|headshot|"
    r"avatar|profile|team|contact|person|osobn[iy]|logo|icon)([/_.\-]|$)",
    re.I,
)
PHOTO_EXT = re.compile(r"\.(?:jpg|jpeg|png|webp)(?:$|[?&#])", re.I)
URL_IN_HTML = re.compile(
    r"https?://[^\s\"'<>\\]{8,420}?\.(?:jpg|jpeg|png|webp)(?:\?[^\"'<>\s]{0,100})?",
    re.I,
)
STOP = set("ostrava prodej pronajem dum domu domy rodinny vila bytu byty reality "
           "detail ulice byt garaz parkovaci stani vcetne novy hezky "
           "sreality pronajmu nabidka bytova bytove koridor ceska republiky "
           "moravskoslezsky kraj okres mesto s pozemek pozemkem".split())
TIMEOUT = 12
DETAIL_BUDGET = 14
_detail_reads = 0


def suspect_url(url):
    return bool(BAD_IMAGE.search(urlsplit(str(url or "")).path))


@lru_cache(maxsize=4096)
def dominant_face(path, size, mtime_ns):
    """High-confidence face dominance only, avoiding distant people in photos.

    Detection with OpenCV's bundled Haar model is fast and uses no network API.
    A house photo containing a tiny person should not be rejected.
    """
    try:
        import cv2
        path = str(path)
        photo = cv2.imread(path)
        if photo is None:
            return False
        original_h, original_w = photo.shape[:2]
        if original_h < 80 or original_w < 80:
            return False
        if original_w > 640:
            scale = 640 / original_w
            photo = cv2.resize(photo, (640, round(original_h * scale)))
        gray = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)
        model = cv2.CascadeClassifier(cv2.data.haarcascades +
                                      "haarcascade_frontalface_default.xml")
        if model.empty():
            return False
        faces = model.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                       minSize=(35,35), flags=cv2.CASCADE_SCALE_IMAGE)
        h,w = gray.shape
        return any(fw / w >= .17 and fh / h >= .23 and
                   (fw * fh) / (w * h) >= .045 for x,y,fw,fh in faces)
    except (ImportError, OSError, ValueError):
        return False


def looks_like_portrait(path):
    candidate = Path(path)
    if not candidate.is_file():
        return False
    stat = candidate.stat()
    return dominant_face(str(candidate), stat.st_size, stat.st_mtime_ns)


def tokens(record):
    """Distinctive slug words; skip source names, deal type and generic nouns."""
    from urllib.parse import unquote
    url = unquote(str(record.get("url") or ""))
    words = re.findall(r"[a-zA-ZÀ-ž]{4,}", urlsplit(url).path.lower())
    words += re.findall(r"[a-zA-ZÀ-ž]{4,}", str(record.get("street") or "").lower())
    found = set()
    for word in words:
        word = "".join(c for c in unicodedata.normalize("NFKD",word)
                       if not unicodedata.combining(c))
        if len(word) >= 5 and word not in STOP:
            found.add(word)
    return found


def same_house_for_photo(a,b):
    if a.get("id") == b.get("id") or a.get("market") != b.get("market"):
        return False
    try:
        ap,bp = int(a["price"]), int(b["price"])
        aa,ba = float(a["usable_m2"]), float(b["usable_m2"])
    except (ValueError,TypeError,KeyError):
        return False
    if ap != bp or abs(aa-ba) > 2:
        return False
    return len(tokens(a) & tokens(b)) >= 2


def archived_match(row, other_rows, root):
    """Borrow only a known, non-portrait, locally archived photo."""
    matches=[]
    for candidate in other_rows:
        if not same_house_for_photo(row,candidate):
            continue
        path = candidate.get("photo_path") or ""
        if not path.startswith("assets/photos/"):
            continue
        target = root / path
        if not target.is_file() or looks_like_portrait(target):
            continue
        # Primary market source is preferred if multiple adverts match.
        matches.append((candidate.get("source") != "sreality.cz", candidate))
    if not matches:
        return None
    return sorted(matches,key=lambda v:v[0])[0][1]


def image_sources_from_html(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    candidate_urls=[]
    for meta in soup.select('meta[property="og:image"], meta[name="twitter:image"]'):
        candidate_urls.append(meta.get("content"))
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except (ValueError,TypeError):
            continue
        def walk(obj):
            if isinstance(obj,dict):
                for key,value in obj.items():
                    if key in ("image","images","photo","photos","contentUrl","thumbnailUrl"):
                        if isinstance(value,str):
                            candidate_urls.append(value)
                        else:
                            walk(value)
                    elif isinstance(value,(dict,list)):
                        walk(value)
            elif isinstance(obj,list):
                for item in obj:
                    walk(item)
        walk(data)
    for image in soup.select('main img, article img, .gallery img, .carousel img, [class*="gallery"] img'):
        for key in ("data-original","data-full","data-src","src","srcset"):
            value=image.get(key)
            if value:
                candidate_urls.append(value.split(",")[0].strip().split(" ")[0])
    for match in URL_IN_HTML.finditer(html.replace("\\/","/")):
        candidate_urls.append(match.group(0))
    answers=[]
    for value in candidate_urls:
        if not value or not isinstance(value,str):
            continue
        address=urljoin(base_url,unescape(value))
        if (not address.startswith("https://") or suspect_url(address) or
                not PHOTO_EXT.search(address)):
            continue
        if address not in answers:
            answers.append(address)
    return answers[:16]


def gallery_alternatives(row, session=None):
    """Load listing details only for suspect images. Follow robots.txt."""
    global _detail_reads
    url=str(row.get("url") or "")
    if not url.startswith("https://") or _detail_reads>=DETAIL_BUDGET:
        return []
    try:
        from extra_sources import allowed_by_robots
        http=session or requests.Session()
        if not allowed_by_robots(http,url):
            return []
        _detail_reads+=1
        res=http.get(url,timeout=TIMEOUT,headers={
            "User-Agent":"OstravaPropertyHistory/1.1 (+market research; once daily)",
            "Accept":"text/html,application/xhtml+xml"})
        if res.status_code != 200 or "html" not in res.headers.get("content-type","text/html"):
            return []
        return image_sources_from_html(res.text,res.url)[:8]
    except (requests.RequestException,ValueError,RuntimeError):
        return []
