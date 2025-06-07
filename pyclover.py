#!/usr/bin/env python3
"""
clover_net_sales.py  – Version 2
--------------------------------
• Net‑sales report between 12 p.m. and 12 a.m. Central Time
  -r {today,yesterday,week,month}   (default: today)

• Quick listings
  -l {employees,discounts,items}

CONFIG  – ./config.json (same as before)
{
  "merchant_id": "YOUR_13_CHAR_MID",
  "access_token": "YOUR_PRIVATE_TOKEN",
  "base_url": "https://api.clover.com"
}
"""

import argparse, json, sys
from pathlib import Path
from datetime import datetime, time, timedelta, date, timezone
from zoneinfo import ZoneInfo
import requests

# ── constants ──────────────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).with_name("config.json")
PAGE_LIMIT  = 1000
CENTRAL_TZ  = ZoneInfo("America/Chicago")

# ── helpers ────────────────────────────────────────────────────────────────
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

    if range_key == "today":
        s = e = today
    elif range_key == "yesterday":
        s = e = today - timedelta(days=1)
    elif range_key == "week":
        s = sunday_of_week(today)
        first = today.replace(day=1)
        if s < first:
            s = first
        e = today
    elif range_key == "month":
        s, e = today.replace(day=1), today
    else:
        sys.exit("❌  Range must be: today, yesterday, week, month")

    start_dt = datetime.combine(s, time(12, 0), tzinfo=CENTRAL_TZ)
    end_dt   = datetime.combine(e + timedelta(days=1), time(0, 0), tzinfo=CENTRAL_TZ)
    return epoch_ms(start_dt), epoch_ms(end_dt), s, e

def paged_get(cfg: dict, path: str) -> list[dict]:
    base, tok = cfg.get("base_url", "https://api.clover.com"), cfg["access_token"]
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

# ── net‑sales logic ────────────────────────────────────────────────────────
def get_payments(cfg, start_ms, end_ms):
    mid = cfg["merchant_id"]
    path = (f"/v3/merchants/{mid}/payments"
            f"?filter=createdTime>{start_ms}"
            f"&filter=createdTime<{end_ms}"
            f"&filter=result=SUCCESS"
            f"&filter=voided=false"
            f"&expand=refunds")
    return paged_get(cfg, path)

def net_sales_cents(payments):
    gross = sum(p.get("amount", 0) for p in payments)
    refunds = sum(r.get("amount", 0)
                  for p in payments
                  for r in (p.get("refunds", {}).get("elements", []) if p.get("refunds") else []))
    return gross - refunds

def print_net_sales(cfg, range_key):
    start_ms, end_ms, sd, ed = window(range_key)
    cents = net_sales_cents(get_payments(cfg, start_ms, end_ms))
    label = sd.strftime("%Y‑%m‑%d") if sd == ed else f"{sd:%Y‑%m‑%d} → {ed:%Y‑%m‑%d}"
    print(f"Net sales (12 p.m.–midnight CT) for {label}: ${cents/100:,.2f}")

# ── listing logic ──────────────────────────────────────────────────────────
def list_resource(cfg, resource):
    mid = cfg["merchant_id"]
    if resource == "employees":
        path = f"/v3/merchants/{mid}/employees?"
        key  = "name"
    elif resource == "discounts":
        path = f"/v3/merchants/{mid}/discounts?"
        key  = "name"
    elif resource == "items":
        path = f"/v3/merchants/{mid}/items?"
        key  = "name"
    else:
        sys.exit("❌  List option must be: employees, discounts, items")

    rows = paged_get(cfg, path)
    if not rows:
        print(f"(no {resource} found)")
        return

    print(f"{resource.capitalize()} ({len(rows)}):")
    for r in rows:
        print(f"• {r.get(key, '(no name)')}   [{r.get('id')}]")

# ── main ───────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Clover net‑sales & listing tool")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("-r", "--range",
                       choices=["today", "yesterday", "week", "month"],
                       default="today",
                       help="Date range for net‑sales (default: today)")
    group.add_argument("-l", "--list",
                       choices=["employees", "discounts", "items"],
                       help="List Clover resources and exit")
    args = ap.parse_args()

    cfg = load_cfg(CONFIG_FILE)

    if args.list:
        list_resource(cfg, args.list)
    else:
        print_net_sales(cfg, args.range)

if __name__ == "__main__":
    main()
