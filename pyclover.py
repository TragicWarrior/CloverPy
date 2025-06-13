#!/usr/bin/env python3
"""
clover_net_sales.py  – Version 13 (sales excluding tax, employee tip & discount breakdown + sales detail)
-----------------------------------------------------------------------
• Net-metrics between 12 p.m. and 12 a.m. Central Time
  -r {today,yesterday,week,month,last_week,last_month,year,YYYY}     (default: today)
  -q {sales,tax,tips,discounts}       (default: sales)
  -d                                  (detailed breakdown - employee tips, discount names, or sales by time)

• Quick listings
  -l {employees,discounts,items}

CONFIG  – ./config.json

{
  "merchant_id": "YOUR_13_CHAR_MID",
  "access_token": "YOUR_PRIVATE_TOKEN",
  "base_url": "https://api.clover.com"
}
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, time, timedelta, date, timezone
from zoneinfo import ZoneInfo
import requests
from collections import defaultdict
import calendar

# Constants
CONFIG_FILE = Path(__file__).with_name("config.json")
PAGE_LIMIT = 1000
CENTRAL_TZ = ZoneInfo("America/Chicago")

# Helpers

def load_cfg(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"❌  Config file {path} not found.")
    return json.loads(path.read_text())

def epoch_ms(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)

def sunday_of_week(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)

def window(range_key: str):
    today = datetime.now(CENTRAL_TZ).date()

    # Special case: the literal word "year" → use current calendar year
    if range_key.lower() == "year":
        year = today.year
        s = date(year, 1, 1)
        e = date(year, 12, 31)
        start_dt = datetime.combine(s, time(12, 0), tzinfo=CENTRAL_TZ)
        end_dt   = datetime.combine(e + timedelta(days=1), time(0, 0), tzinfo=CENTRAL_TZ)
        return epoch_ms(start_dt), epoch_ms(end_dt), s, e, "year"

    # Check if it's a year in YYYY format
    try:
        year = int(range_key)
        if 2000 <= year <= 2099:  # Reasonable year range
            s = date(year, 1, 1)
            e = date(year, 12, 31)
            start_dt = datetime.combine(s, time(12, 0), tzinfo=CENTRAL_TZ)
            end_dt   = datetime.combine(e + timedelta(days=1), time(0, 0), tzinfo=CENTRAL_TZ)
            return epoch_ms(start_dt), epoch_ms(end_dt), s, e, "year"
    except ValueError:
        pass

    # Check if it's an ISO date (single-day)
    try:
        single = date.fromisoformat(range_key)
    except ValueError:
        single = None

    if single:
        s = e = single
        range_type = "day"
    elif range_key == "today":
        s = e = today
        range_type = "day"
    elif range_key == "yesterday":
        s = e = today - timedelta(days=1)
        range_type = "day"
    elif range_key == "week":
        s = max(sunday_of_week(today), today.replace(day=1))
        e = today
        range_type = "range"
    elif range_key == "month":
        s, e = today.replace(day=1), today
        range_type = "range"
    elif range_key == "last_week":
        current_week_start = sunday_of_week(today)
        s = current_week_start - timedelta(days=7)
        e = current_week_start - timedelta(days=1)
        range_type = "range"
    elif range_key == "last_month":
        first_of_current = today.replace(day=1)
        last_of_prev = first_of_current - timedelta(days=1)
        s = last_of_prev.replace(day=1)
        e = last_of_prev
        range_type = "range"
    else:
        sys.exit(f"❌  Unknown range or bad date '{range_key}'.")

    start_dt = datetime.combine(s, time(12, 0), tzinfo=CENTRAL_TZ)
    end_dt   = datetime.combine(e + timedelta(days=1), time(0, 0), tzinfo=CENTRAL_TZ)
    return epoch_ms(start_dt), epoch_ms(end_dt), s, e, range_type

def paged_get(cfg: dict, path: str) -> list[dict]:
    base = cfg.get("base_url", "https://api.clover.com")
    tok = cfg["access_token"]
    out, offset = [], 0
    while True:
        url = f"{base}{path}&limit={PAGE_LIMIT}&offset={offset}"
        r = requests.get(url, headers={"Authorization": f"Bearer {tok}"})
        r.raise_for_status()
        batch = r.json().get("elements", [])
        if not batch:
            break
        out.extend(batch)
        if len(batch) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    return out

# (remaining data‐fetch, mapping, and metric helper functions follow unchanged…)

def main():
    parser = argparse.ArgumentParser(
        description="Clover net metrics (sales, tax, tips, discounts)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "-r", "--range",
        dest="range",
        default="today",
        help=(
            "Date range (today, yesterday, week, month, last_week, last_month, year, YYYY) "
            "or a specific date YYYY-MM-DD"
        )
    )
    parser.add_argument(
        "-q", "--query",
        dest="query",
        choices=["sales","tax","tips","discounts"],
        default="sales",
        help="Metric to calculate (default: sales)"
    )
    parser.add_argument(
        "-d", "--detail",
        action="store_true",
        help="Show detailed breakdown for tips, discounts, or sales by time"
    )
    parser.add_argument(
        "-l", "--list",
        choices=["employees","discounts","items"],
        help="Quick list of employees, discounts, or items"
    )
    args = parser.parse_args()

    cfg = load_cfg(CONFIG_FILE)

    if args.list:
        list_resource(cfg, args.list)
        return

    start_ms, end_ms, sd, ed, range_type = window(args.range)

    # (rest of main’s logic unchanged…)

if __name__ == "__main__":
    main()
