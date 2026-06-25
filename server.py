"""
GeM Product Review Tool — Flask Backend (Google Sheets edition)

Reads/writes directly to a Google Sheet via the Sheets API.
Groups rows by app_display_product_link.
Shows ONE card per unique link in the UI.
When a decision is saved, propagates it to ALL rows sharing that link.
Skips links that already have a Human_Review value.

Optimizations:
- Sheets service is built once and reused (_SERVICE singleton)
- Sheet data is cached in memory and only re-fetched after writes
- Proxy uses a persistent requests.Session with connection pooling
- Only HTML pages are proxied; assets load directly via <base> tag
"""

import os
import json
import threading
import requests
from urllib.parse import urlparse, urljoin, quote
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory, Response

from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = "1ducMmMf1m7EsXvKJreRPOl2ScGJm1UzRUY_c2afp1x8"
SHEET_NAME     = "df_spec_10420"

# Column names we manage
REVIEW_COL = "Spec_Mismatch"   # YES or NO
REASON_COL = "Reason"          # free-text reason from reviewer
BENCH_COL  = "Benchmark Link"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")

# ---------------------------------------------------------------------------
# Singleton Sheets service
# ---------------------------------------------------------------------------

_CREDS      = None          # credentials object — built once, safe to share
_CREDS_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_thread_local = threading.local()  # per-thread service instance


def _get_creds():
    """Build credentials once and cache them (credentials are thread-safe)."""
    global _CREDS
    with _CREDS_LOCK:
        if _CREDS is not None:
            return _CREDS
        inline_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
        if inline_json:
            try:
                info = json.loads(inline_json)
            except json.JSONDecodeError as e:
                raise ValueError(f"Failed to parse GOOGLE_CREDENTIALS_JSON: {e}.")
            _CREDS = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            return _CREDS
        key_path = os.environ.get(
            "GOOGLE_SERVICE_ACCOUNT_JSON",
            os.path.join(os.path.dirname(__file__), "credentials.json"),
        )
        if not os.path.exists(key_path):
            raise FileNotFoundError(
                f"Service account key not found at {key_path}. "
                "Set GOOGLE_CREDENTIALS_JSON or place credentials.json next to server.py."
            )
        _CREDS = service_account.Credentials.from_service_account_file(key_path, scopes=SCOPES)
        return _CREDS


def _get_service():
    """Return a per-thread Sheets service instance (googleapiclient is not thread-safe)."""
    svc = getattr(_thread_local, "service", None)
    if svc is None:
        _thread_local.service = build("sheets", "v4", credentials=_get_creds(), cache_discovery=False)
    return _thread_local.service

# ---------------------------------------------------------------------------
# In-memory sheet cache
# ---------------------------------------------------------------------------

_CACHE = {"headers": None, "rows": None}

def _invalidate_cache():
    with _CACHE_LOCK:
        _CACHE["headers"] = None
        _CACHE["rows"]    = None

def read_all_rows(force=False):
    """Return (headers, rows). Uses cache unless force=True or cache is empty."""
    with _CACHE_LOCK:
        if not force and _CACHE["headers"] is not None:
            return _CACHE["headers"], _CACHE["rows"]

    svc    = _get_service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'"
    ).execute()
    values = result.get("values", [])

    with _CACHE_LOCK:
        if not values:
            _CACHE["headers"] = []
            _CACHE["rows"]    = []
            return [], []
        _CACHE["headers"] = values[0]
        _CACHE["rows"]    = values[1:]
        return _CACHE["headers"], _CACHE["rows"]

# ---------------------------------------------------------------------------
# Column helpers
# ---------------------------------------------------------------------------

def _col_letter(n):
    """Convert 1-based column number to A, B, ... Z, AA, AB, etc."""
    result = ""
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def row_to_dict(headers, row):
    """Convert a row list to a dict, padding missing columns with empty string."""
    padded = list(row) + [""] * (len(headers) - len(row))
    return dict(zip(headers, padded))


def batch_write(updates):
    """Write multiple cells at once.  updates: list of (row_1based, col_1based, value)."""
    global _SERVICE
    if not updates:
        return
    data = [{
        "range":  f"'{SHEET_NAME}'!{_col_letter(col)}{row}",
        "values": [[value]],
    } for row, col, value in updates]

    try:
        _get_service().spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
    except Exception:
        _thread_local.service = None  # force rebuild on next call for this thread
        raise
    _invalidate_cache()

# ---------------------------------------------------------------------------
# Proxy — persistent session with connection pooling
# ---------------------------------------------------------------------------

_PROXY_SESSION = requests.Session()
_PROXY_SESSION.headers.update({
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/webp,*/*;q=0.8",
    "Referer":         "https://mkp.gem.gov.in/",
})

GEM_HOST = "mkp.gem.gov.in"

STRIP_HEADERS = {
    "x-frame-options", "content-security-policy",
    "content-security-policy-report-only", "x-content-type-options",
    "transfer-encoding", "content-encoding", "content-length",
    "strict-transport-security",
}

def _make_proxy_url(href, current_url):
    abs_url = urljoin(current_url, href)
    if GEM_HOST in urlparse(abs_url).netloc:
        return f"/proxy?url={quote(abs_url, safe='')}"
    return abs_url

# ---------------------------------------------------------------------------
# Routes — Static
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")

# ---------------------------------------------------------------------------
# Routes — Proxy
# ---------------------------------------------------------------------------

@app.route("/proxy")
def proxy():
    target_url = request.args.get("url", "").strip()
    if not target_url:
        return "Missing ?url= parameter", 400

    parsed = urlparse(target_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    try:
        upstream = _PROXY_SESSION.get(target_url, timeout=20, allow_redirects=True)
    except Exception as exc:
        return (f"<h2 style='font-family:sans-serif;padding:40px;color:#c0392b'>"
                f"Proxy fetch error: {exc}</h2>"), 502

    content_type = upstream.headers.get("content-type", "text/html")

    # Non-HTML: stream through directly (images, CSS, JS, etc.)
    if "text/html" not in content_type:
        safe_headers = {k: v for k, v in upstream.headers.items()
                        if k.lower() not in STRIP_HEADERS}
        return Response(upstream.content, status=upstream.status_code,
                        headers=safe_headers, content_type=content_type)

    # HTML: parse, inject <base>, rewrite only <a> hrefs
    soup = BeautifulSoup(upstream.content, "html.parser")

    # Remove existing <base> tags and inject ours pointing to the origin
    for tag in soup.find_all("base"):
        tag.decompose()
    head = soup.find("head")
    if not head:
        head = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head)
    head.insert(0, soup.new_tag("base", href=origin + "/"))
    head.insert(0, soup.new_tag("meta", attrs={"name": "referrer", "content": "no-referrer"}))

    # Only rewrite <a> hrefs — let CSS/images/JS load directly via <base>
    for tag in soup.find_all("a", href=True):
        tag["href"]   = _make_proxy_url(tag["href"], target_url)
        tag["target"] = "_self"

    # Disable forms
    for form in soup.find_all("form"):
        form["onsubmit"] = "return false;"

    # Small info banner at bottom
    banner = soup.new_tag("div")
    banner["style"] = (
        "position:fixed;bottom:0;left:0;right:0;z-index:99999;"
        "background:rgba(15,23,42,0.92);color:#94a3b8;font-family:sans-serif;"
        "font-size:11px;padding:4px 12px;text-align:center;"
        "border-top:1px solid rgba(255,255,255,0.08);backdrop-filter:blur(8px);"
    )
    banner.string = f"📡 Proxied — {target_url}"
    if soup.body:
        soup.body.append(banner)

    return Response(str(soup), status=upstream.status_code,
                    content_type="text/html; charset=utf-8")

# ---------------------------------------------------------------------------
# API — GET /api/items
# ---------------------------------------------------------------------------

@app.route("/api/items", methods=["GET"])
def get_items():
    """Return list of unique product links that still need human review."""
    try:
        headers, rows = read_all_rows()  # uses cache; refreshed automatically after every vote
    except Exception as e:
        return jsonify({"error": f"Failed to read sheet: {e}"}), 500

    if not headers:
        return jsonify([])

    groups = {}

    for row in rows:
        record = row_to_dict(headers, row)
        link   = (record.get("app_display_product_link") or "").strip()
        s3_id  = record.get("s3_filename_number", "")
        hr     = (record.get(REVIEW_COL) or "").strip().upper()
        bench  = (record.get(BENCH_COL) or "").strip()
        reason_col = (record.get(REASON_COL) or "").strip()
        key    = link if link else f"__no_link_{s3_id}__"

        if key not in groups:
            groups[key] = {
                "link":          link,
                "title":         record.get("app_display_title", ""),
                "description":   record.get("visible_product_in_image", ""),
                "reason":        record.get("app_display_candidate_reason", ""),
                "final_reason":  record.get("final_reason", ""),
                "spec_mismatch": record.get("Spec_Mismatch", ""),
                "page_status":   record.get("page_status", ""),
                "site_title":    record.get("final_site_title", ""),
                "s3_ids":        [],
                "human_review":  None,
            }

        groups[key]["s3_ids"].append(s3_id)
        if hr or bench or reason_col:
            groups[key]["human_review"] = hr or "MANUAL"

    # Only return items that have NOT been reviewed yet
    result = []
    for g in groups.values():
        if g["human_review"]:
            continue
        result.append({
            "link":          g["link"],
            "title":         g["title"],
            "description":   g["description"],
            "reason":        g["reason"],
            "final_reason":  g["final_reason"],
            "spec_mismatch": g["spec_mismatch"],
            "page_status":   g["page_status"],
            "site_title":    g["site_title"],
            "linked_count":  len(g["s3_ids"]),
            "s3_filename_number": g["s3_ids"][0] if g["s3_ids"] else "",
        })

    return jsonify(result)

# ---------------------------------------------------------------------------
# API — POST /api/review
# ---------------------------------------------------------------------------

@app.route("/api/review", methods=["POST"])
def submit_review():
    """Save a YES/NO decision for a product link, applying to ALL rows with that link."""
    data      = request.get_json(force=True)
    link      = (data.get("link") or "").strip()
    decision  = (data.get("decision") or "").strip().upper()
    reason    = (data.get("reason") or "").strip()
    benchmark = (data.get("benchmark_link") or "").strip()

    if decision not in ("YES", "NO"):
        return jsonify({"error": "decision must be YES or NO"}), 400
    if not link:
        return jsonify({"error": "link is required"}), 400

    try:
        headers, rows = read_all_rows()
    except Exception as e:
        return jsonify({"error": f"Failed to read sheet: {e}"}), 500

    if not headers:
        return jsonify({"error": "Empty worksheet"}), 400

    if REVIEW_COL not in headers or REASON_COL not in headers:
        return jsonify({"error": f"Required columns {REVIEW_COL}/{REASON_COL} missing"}), 400

    review_col_idx = headers.index(REVIEW_COL) + 1   # 1-based for Sheets API
    reason_col_idx = headers.index(REASON_COL) + 1
    bench_col_idx  = headers.index(BENCH_COL) + 1 if BENCH_COL in headers else None

    updates      = []
    updated_rows = 0

    for i, row in enumerate(rows):
        record    = row_to_dict(headers, row)
        cell_link = (record.get("app_display_product_link") or "").strip()
        if cell_link != link:
            continue
        sheet_row = i + 2   # +1 for header, +1 for 1-based
        updates.append((sheet_row, review_col_idx, decision))
        if reason:
            updates.append((sheet_row, reason_col_idx, reason))
        if benchmark and bench_col_idx:
            updates.append((sheet_row, bench_col_idx, benchmark))
        updated_rows += 1

    if updated_rows == 0:
        return jsonify({"error": f"No rows found for link: {link}"}), 404

    try:
        batch_write(updates)
    except Exception as e:
        return jsonify({"error": f"Failed to write to sheet: {e}"}), 500

    return jsonify({
        "status":       "ok",
        "link":         link,
        "decision":     decision,
        "rows_updated": updated_rows,
    })

# ---------------------------------------------------------------------------
# API — GET /api/progress
# ---------------------------------------------------------------------------

@app.route("/api/progress", methods=["GET"])
def get_progress():
    """Return total unique links and how many have been reviewed."""
    try:
        headers, rows = read_all_rows()
    except Exception:
        return jsonify({"total": 0, "reviewed": 0})

    if not headers or "app_display_product_link" not in headers:
        return jsonify({"total": 0, "reviewed": 0})

    link_col = headers.index("app_display_product_link")
    hr_col   = headers.index(REVIEW_COL) if REVIEW_COL in headers else None

    groups_total    = set()
    groups_reviewed = set()

    for row in rows:
        padded = list(row) + [""] * (len(headers) - len(row))
        link   = (padded[link_col] or "").strip()
        hr     = (padded[hr_col] or "").strip().upper() if hr_col is not None else ""
        if link:
            groups_total.add(link)
            if hr in ("YES", "NO"):
                groups_reviewed.add(link)

    return jsonify({"total": len(groups_total), "reviewed": len(groups_reviewed)})

# ---------------------------------------------------------------------------
# Warmup — pre-load sheet data into cache on startup
# ---------------------------------------------------------------------------

def _warmup():
    try:
        print("⏳ Warming up: connecting to Google Sheets…")
        headers, rows = read_all_rows(force=True)
        print(f"  ✅ Cache loaded: {len(rows)} data rows, {len(headers)} columns")
        if headers:
            print("  ✅ Review columns verified")
    except Exception as e:
        print(f"  ❌ Warmup failed (will retry on first request): {e}")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    os.makedirs(STATIC_DIR, exist_ok=True)
    _warmup()
    print(f"\n🚀 Starting GeM Review Tool on http://0.0.0.0:5050")
    print(f"   Spreadsheet: {SPREADSHEET_ID}")
    print(f"   Sheet tab:   {SHEET_NAME}\n")
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
