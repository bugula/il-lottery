#!/usr/bin/env python3
"""
Illinois Pick 3 + Fireball scraper.
Fetches from illinoislottery.com and saves to data/draws.json.
Designed to run as a GitHub Actions scheduled job.
"""

import json
import re
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path
import urllib.request
import urllib.error

DATA_FILE = Path(__file__).parent.parent / "data" / "draws.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def fetch_url(url: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
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


def parse_year_page(html: str, year: int) -> list[dict]:
    """
    illinoislotterynumbers.net table rows look like:
      <td>Monday, Mar 9, 2026</td>
      <td><ul><li>8</li><li>8</li><li>0</li><li>3</li></ul></td>  <- midday: 3 digits + fireball
      <td><ul><li>6</li><li>6</li><li>0</li><li>5</li></ul></td>  <- evening: 3 digits + fireball
    """
    draws = []

    # Find all table rows with draw data
    row_pattern = re.compile(
        r'<tr[^>]*>.*?<td[^>]*>((?:Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),\s*\w+\s+\d+,\s*\d{4})</td>'
        r'.*?<ul>(.*?)</ul>'   # midday balls
        r'.*?<ul>(.*?)</ul)',  # evening balls
        re.DOTALL | re.IGNORECASE
    )

    ball_pattern = re.compile(r'<li[^>]*>(\d)</li>')

    for m in row_pattern.finditer(html):
        date_str = re.sub(r'\s+', ' ', m.group(1).strip())
        midday_ul = m.group(2)
        evening_ul = m.group(3)

        try:
            dt = datetime.strptime(date_str, "%A, %b %d, %Y")
        except ValueError:
            try:
                # Some pages use full month names
                dt = datetime.strptime(date_str, "%A, %B %d, %Y")
            except ValueError:
                continue

        for draw_type, ul_html in [("midday", midday_ul), ("evening", evening_ul)]:
            balls = [int(b) for b in ball_pattern.findall(ul_html)]
            if len(balls) != 4:
                continue
            draws.append({
                "date": dt.strftime("%Y-%m-%d"),
                "dayOfWeek": dt.weekday() + 1 if dt.weekday() < 6 else 0,  # Sun=0 Mon=1 ... Sat=6
                "type": draw_type,
                "digits": balls[:3],
                "fireball": balls[3],
            })

    return draws


def parse_year_page_v2(html: str) -> list[dict]:
    """
    Alternative parser using a more flexible approach for table structure.
    Handles slight HTML variations between year pages.
    """
    draws = []

    # Extract all table rows
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)

    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
        if len(cells) < 3:
            continue

        # Check if first cell contains a date
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

        # day_of_week: Sun=0, Mon=1, ..., Sat=6  (JS convention)
        dow = dt.isoweekday() % 7  # isoweekday Mon=1..Sun=7 -> Mon=1..Sat=6, Sun=0

        ball_pattern = re.compile(r'<li[^>]*>\s*(\d)\s*</li>')

        for draw_type, cell_idx in [("midday", 1), ("evening", 2)]:
            if cell_idx >= len(cells):
                continue
            balls = [int(b) for b in ball_pattern.findall(cells[cell_idx])]
            if len(balls) != 4:
                continue
            draws.append({
                "date": dt.strftime("%Y-%m-%d"),
                "dayOfWeek": dow,
                "type": draw_type,
                "digits": balls[:3],
                "fireball": balls[3],
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
    merged = sorted(seen.values(), key=lambda d: d["date"], reverse=True)
    return merged


FIRST_YEAR = 2010  # Illinois Pick 3 Fireball data available from here


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", help="Specific years to scrape")
    parser.add_argument("--all", action="store_true", help=f"Seed all history from {FIRST_YEAR} to present")
    args = parser.parse_args()

    current_year = datetime.now().year

    if args.all:
        years = list(range(FIRST_YEAR, current_year + 1))
    elif args.years:
        years = args.years
    else:
        # Default incremental: current year + previous (covers ~18 months)
        years = [current_year - 1, current_year]

    print(f"Scraping years: {years}")

    existing = load_existing()
    print(f"Existing draws in file: {len(existing)}")

    all_new = []
    for year in years:
        draws = scrape_year(year)
        all_new.extend(draws)
        time.sleep(1)  # be polite

    merged = merge_draws(existing, all_new)

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "updatedAt": datetime.utcnow().isoformat() + "Z",
        "count": len(merged),
        "draws": merged,
    }
    with open(DATA_FILE, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    print(f"Saved {len(merged)} total draws to {DATA_FILE}")


if __name__ == "__main__":
    main()
