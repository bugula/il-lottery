#!/usr/bin/env python3
"""
Illinois Pick 3 + Fireball scraper.
Fetches from illinoislottery.com (official) and saves to data/draws.json.
Designed to run as a GitHub Actions scheduled job.

Strategy for incremental runs:
  1. Try the official illinoislottery.com list page first (most up-to-date).
     Uses a session warmup + browser headers, with a Playwright fallback.
  2. Also scrape illinoislotterynumbers.net for the current + previous year
     to catch any draws the official page doesn't show (it only lists ~20 recent draws).
  3. Merge all results, deduplicating by (date, type).

For full historical seed (--all flag): uses illinoislotterynumbers.net only.
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
import urllib.request
import urllib.error

DATA_FILE = Path(__file__).parent.parent / "data" / "draws.json"

OFFICIAL_BASE = "https://www.illinoislottery.com"
OFFICIAL_URL  = f"{OFFICIAL_BASE}/dbg/results/pick3"

# Browser-like headers — same approach as the Streamlit dashboard
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": OFFICIAL_BASE + "/",
}

# Fallback headers for third-party site
SIMPLE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ─── Official site scraper ────────────────────────────────────────────────────

def fetch_official_html_requests() -> str:
    """
    Fetch the official IL Lottery Pick 3 list page using a requests Session.
    Warms up with a homepage hit to establish cookies, then fetches the results page.
    """
    import requests

    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)

    # Warm up: visit homepage to get cookies/session state
    try:
        s.get(OFFICIAL_BASE + "/", timeout=20)
        time.sleep(0.5)
    except Exception as e:
        print(f"  [official] Warmup failed (ignored): {e}", file=sys.stderr)

    for attempt in range(1, 4):
        try:
            url = OFFICIAL_URL if attempt == 1 else f"{OFFICIAL_URL}?_={int(time.time())}"
            r = s.get(url, timeout=30, allow_redirects=True)
            if r.status_code == 403:
                raise Exception(f"403 Forbidden")
            r.raise_for_status()
            print(f"  [official] Fetched via requests (HTTP {r.status_code})")
            return r.text
        except Exception as e:
            print(f"  [official] requests attempt {attempt} failed: {e}", file=sys.stderr)
            if attempt < 3:
                time.sleep(1.5)

    raise Exception("requests path exhausted")


def fetch_official_html_playwright() -> str:
    """Playwright fallback — renders the page like a real browser."""
    from playwright.sync_api import sync_playwright

    print("  [official] Trying Playwright fallback…")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent=BROWSER_HEADERS["User-Agent"],
            locale="en-US",
            timezone_id="America/Chicago",
            viewport={"width": 1366, "height": 2000},
        )
        page = context.new_page()
        page.set_default_timeout(60_000)
        page.goto(OFFICIAL_URL, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_selector("li[data-test-id^='draw-result-']", timeout=12_000)
        except Exception:
            pass  # may still have content even if selector not found in time
        html = page.content()
        browser.close()
    print("  [official] Playwright fetch succeeded")
    return html


def fetch_official_html() -> str:
    """Try requests first; fall back to Playwright if available."""
    try:
        return fetch_official_html_requests()
    except Exception as e:
        print(f"  [official] requests path failed: {e}", file=sys.stderr)

    try:
        return fetch_official_html_playwright()
    except ImportError:
        print("  [official] Playwright not installed; skipping fallback.", file=sys.stderr)
    except Exception as e:
        print(f"  [official] Playwright fallback failed: {e}", file=sys.stderr)

    return ""


def parse_official_html(html: str) -> list[dict]:
    """
    Parse the official illinoislottery.com Pick 3 results list page.
    Each draw is an <li data-test-id="draw-result-…"> card containing:
      - .dbg-results__date-info  → date string
      - [data-test-id^="draw-result-schedule-type-text-"]  → "Midday" / "Evening"
      - .grid-ball--pick3-primary--selected (×3)  → Pick 3 digits
      - .grid-ball--pick3-secondary--selected (×1) → Fireball
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("  [official] beautifulsoup4 not installed; cannot parse official HTML.", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "lxml")
    cards = soup.select('li[data-test-id^="draw-result-"]')
    print(f"  [official] Found {len(cards)} draw cards")

    draws = []
    for li in cards:
        # Date
        date_el = li.select_one(".dbg-results__date-info")
        date_str = (date_el.get_text(strip=True) if date_el else "").strip()

        # Draw type
        type_el = li.select_one('[data-test-id^="draw-result-schedule-type-text-"]')
        type_raw = (type_el.get_text(strip=True) if type_el else "").lower()
        if "mid" in type_raw:
            draw_type = "midday"
        elif "eve" in type_raw:
            draw_type = "evening"
        else:
            continue

        # Pick 3 digits
        digit_els = li.select(".grid-ball--pick3-primary--selected")
        digits = [int(el.get_text(strip=True)) for el in digit_els
                  if el.get_text(strip=True).isdigit()]
        if len(digits) != 3:
            continue

        # Fireball
        fb_el = li.select_one(".grid-ball--pick3-secondary--selected")
        fb_str = (fb_el.get_text(strip=True) if fb_el else "")
        if not fb_str.isdigit():
            continue

        # Parse date
        dt = None
        for fmt in ("%b %d, %Y", "%B %d, %Y"):
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                break
            except ValueError:
                pass
        if dt is None:
            print(f"  [official] Unrecognised date: {date_str!r}", file=sys.stderr)
            continue

        dow = dt.isoweekday() % 7  # Mon=1…Sun=7 → Mon=1…Sat=6, Sun=0

        draws.append({
            "date":      dt.strftime("%Y-%m-%d"),
            "dayOfWeek": dow,
            "type":      draw_type,
            "digits":    digits,
            "fireball":  int(fb_str),
        })

    print(f"  [official] Parsed {len(draws)} draws")
    return draws


def scrape_official() -> list[dict]:
    """Fetch + parse the official site. Returns [] on any failure."""
    html = fetch_official_html()
    if not html:
        print("  [official] No HTML; skipping.", file=sys.stderr)
        return []
    return parse_official_html(html)


# ─── Third-party fallback (illinoislotterynumbers.net) ───────────────────────

def fetch_url(url: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=SIMPLE_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} for {url} (attempt {attempt+1})", file=sys.stderr)
            if e.code == 404:
                return ""
            time.sleep(2 ** attempt)
        except Exception as e:
            print(f"  Error fetching {url}: {e} (attempt {attempt+1})", file=sys.stderr)
            time.sleep(2 ** attempt)
    return ""


def parse_year_page_v2(html: str) -> list[dict]:
    draws = []
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)

    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
        if len(cells) < 3:
            continue

        date_cell = re.sub(r'<[^>]+>', '', cells[0]).strip()
        date_cell = re.sub(r'\s+', ' ', date_cell)

        dt = None
        for fmt in ["%A, %b %d, %Y", "%A, %B %d, %Y"]:
            try:
                dt = datetime.strptime(date_cell, fmt)
                break
            except ValueError:
                continue

        if dt is None:
            continue

        dow = dt.isoweekday() % 7
        ball_pattern = re.compile(r'<li[^>]*>\s*(\d)\s*</li>')

        for draw_type, cell_idx in [("midday", 1), ("evening", 2)]:
            if cell_idx >= len(cells):
                continue
            balls = [int(b) for b in ball_pattern.findall(cells[cell_idx])]
            if len(balls) != 4:
                continue
            draws.append({
                "date":      dt.strftime("%Y-%m-%d"),
                "dayOfWeek": dow,
                "type":      draw_type,
                "digits":    balls[:3],
                "fireball":  balls[3],
            })

    return draws


def scrape_year(year: int) -> list[dict]:
    url = f"https://illinoislotterynumbers.net/pick-3/results/{year}"
    print(f"  Fetching {url}...")
    html = fetch_url(url)
    if not html:
        print(f"  No HTML returned for {year}", file=sys.stderr)
        return []
    draws = parse_year_page_v2(html)
    print(f"  Parsed {len(draws)} draws for {year}")
    return draws


# ─── Common helpers ───────────────────────────────────────────────────────────

def load_existing() -> list[dict]:
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            data = json.load(f)
            return data.get("draws", [])
    return []


def merge_draws(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge new draws into existing, deduplicating by (date, type)."""
    seen = {(d["date"], d["type"]): d for d in existing}
    for d in new:
        seen[(d["date"], d["type"])] = d
    return sorted(seen.values(), key=lambda d: d["date"], reverse=True)


# ─── Entry point ─────────────────────────────────────────────────────────────

FIRST_YEAR = 2010


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", help="Specific years to scrape (third-party site)")
    parser.add_argument("--all",   action="store_true", help=f"Seed all history from {FIRST_YEAR} (third-party site)")
    args = parser.parse_args()

    current_year = datetime.now().year
    existing = load_existing()
    print(f"Existing draws in file: {len(existing)}")

    all_new = []

    if args.all or args.years:
        # Historical seed — use third-party site only
        years = list(range(FIRST_YEAR, current_year + 1)) if args.all else args.years
        print(f"Scraping years (third-party): {years}")
        for year in years:
            all_new.extend(scrape_year(year))
            time.sleep(1)
    else:
        # Incremental update
        # 1) Official site first — most current data, posts results immediately after each draw
        print("Trying official illinoislottery.com…")
        official_draws = scrape_official()
        all_new.extend(official_draws)

        # 2) Also scrape third-party for current + previous year to fill any gaps
        #    (official page only shows ~20 recent draws)
        years = [current_year - 1, current_year]
        print(f"Also scraping third-party for years: {years}")
        for year in years:
            all_new.extend(scrape_year(year))
            time.sleep(1)

    merged = merge_draws(existing, all_new)

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "updatedAt": datetime.utcnow().isoformat() + "Z",
        "count":     len(merged),
        "draws":     merged,
    }
    with open(DATA_FILE, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    print(f"Saved {len(merged)} total draws to {DATA_FILE}")


if __name__ == "__main__":
    main()
