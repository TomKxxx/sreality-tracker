import json
import os
import requests
import subprocess
from datetime import datetime

# ================= CONFIG =================
HISTORY_FILE = "sreality_history.json"
OUTPUT_HTML = "sreality_property_history_all.html"
IMAGES_DIR = "property_images"
GIT_REPO_PATH = r"C:\Users\Rancy\Desktop\sreality-tracker"
# =========================================

os.makedirs(IMAGES_DIR, exist_ok=True)


def format_price(price):
    return f"{price:,.0f}".replace(",", " ") + " Kč"


def download_image(image_url, prop_id):
    if not image_url:
        return None

    local_path = os.path.join(IMAGES_DIR, f"{prop_id}.jpg")
    if os.path.exists(local_path):
        return local_path

    try:
        r = requests.get(image_url, timeout=10)
        r.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(r.content)
        return local_path
    except Exception:
        return None


def git_commit_and_push():
    os.chdir(GIT_REPO_PATH)

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True
    ).stdout.strip()

    if not status:
        print("ℹ️ No git changes")
        return

    subprocess.run(
        ["git", "add", OUTPUT_HTML, IMAGES_DIR],
        check=True
    )
    subprocess.run(
        ["git", "commit", "-m",
         f"Update full property history with images ({datetime.now():%Y-%m-%d %H:%M})"],
        check=True
    )
    subprocess.run(["git", "push"], check=True)

    print("✅ Uploaded to GitHub")


def build_html():
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<html>
<head>
<meta charset="utf-8">
<title>Sreality – Full Property History</title>
<style>
body {{ font-family: Arial; background:#f5f5f5; margin:20px; }}
.property {{ background:white; padding:15px; margin:15px 0; border-radius:6px; }}
.active {{ border-left:5px solid #4caf50; }}
.removed {{ border-left:5px solid #cc0000; opacity:.85; }}
.property-header {{ display:flex; gap:15px; }}
.property-image {{ width:160px; height:120px; object-fit:cover; border-radius:4px; }}
.snapshot {{ font-size:13px; margin:4px 0; }}
.price-up {{ color:#cc0000; }}
.price-down {{ color:#4caf50; }}
.small {{ color:#666; font-size:12px; }}
</style>
</head>
<body>
<h1>All Properties – Full History</h1>
<p class="small">Generated: {ts}</p>
"""

    for prop_id, snaps in history.items():
        if not snaps:
            continue

        latest = snaps[-1]
        status = "ACTIVE" if len(snaps) > 1 else "REMOVED / SOLD"
        css = "active" if status == "ACTIVE" else "removed"

        image_path = download_image(latest.get("image_url"), prop_id)
        image_html = (
            f'<img src="{image_path}" class="property-image">'
            if image_path else ""
        )

        html += f"""
<div class="property {css}">
<div class="property-header">
    {image_html}
    <div>
        <h3>
            <a href="{latest.get('url', '#')}" target="_blank">
            {latest.get('name', 'Unknown')}
            </a>
        </h3>
        <p><b>Status:</b> {status}</p>
        <p><b>Location:</b> {latest.get('locality','?')} |
        <b>Area:</b> {latest.get('area','?')} m²</p>
        <p><b>Snapshots:</b> {len(snaps)}</p>
    </div>
</div>
"""

        prev = None
        for s in snaps:
            d = datetime.fromisoformat(s["last_updated"]).strftime("%Y-%m-%d %H:%M")
            p = s["price"]
            diff_html = ""

            if prev is not None and p != prev:
                diff = p - prev
                cls = "price-down" if diff < 0 else "price-up"
                sign = "-" if diff < 0 else "+"
                diff_html = f' <span class="{cls}">({sign}{format_price(abs(diff))})</span>'

            html += f'<div class="snapshot">{d} — {format_price(p)}{diff_html}</div>'
            prev = p

        html += "</div>"

    html += "</body></html>"

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ Generated {OUTPUT_HTML}")


if __name__ == "__main__":
    build_html()
    git_commit_and_push()
