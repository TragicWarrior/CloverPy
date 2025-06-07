#!/usr/bin/env python3
"""
clover_net_sales.py
-------------------
Print net sales (gross – refunds) that occurred between 12 p.m. and 12 a.m.
Central Time for a chosen range: today | yesterday | week | month.

Week = current calendar week starting Sunday, but never before the 1 st.
Month = current calendar month (1 st → today).

CONFIG  –  create ./config.json alongside this file:

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
CENTRAL_TZ  = ZoneInfo("America/Chicago")     # handles CST / CDT

# ── helpers ────────────────────────────────────────────────────────────────
def load_cfg(p: Path) -> dict:
    if not p.exists():
        sys.exit(f"❌  Config file {p} not found.")
    return json.loads(p.read_text())

def epoch_ms(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)

def sunday_of_week(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)

def window(range_key: str):
    today = datetime.now(CENTRAL_TZ).date()

    if range_key == "today":
        s, e = today, today
    elif range_key == "yesterday":
        s = e = today - timedelta(days=1)
    elif range_key == "week":
        s = sunday_of_week(today)
        first = today.replace(day=1)
        if s < first:            # first week of month can’t dip into prev month
            s = first
        e = today
    elif range_key == "month":
        s, e = today.replace(day=1), today
    else:
        sys.exit("❌  Range must be: today, yesterday, week, month")

    start_dt = datetime.combine(s, time(12, 0), tzinfo=CENTRAL_TZ)
    end_dt   = datetime.combine(e + timedelta(days=1), time(0, 0), tzinfo=CENTRAL_TZ)
    return epoch_ms(start_dt), epoch_ms(end_dt), s, e

def get_payments(cfg, start_ms, end_ms):
    base, mid, tok = cfg.get("base_url", "https://api.clover.com"), cfg["merchant_id"], cfg["access_token"]
    out, offset = [], 0
    while True:
        url = (f"{base}/v3/merchants/{mid}/payments"
               f"?filter=createdTime>{start_ms}"
               f"&filter=createdTime<{end_ms}"
               f"&filter=result=SUCCESS"
               f"&filter=voided=false"            # ← fixed field name
               f"&expand=refunds"
               f"&limit={PAGE_LIMIT}&offset={offset}")
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

def net_sales_cents(payments):
    gross = sum(p.get("amount", 0) for p in payments)
    refunds = sum(r.get("amount", 0)
                  for p in payments
                  for r in (p.get("refunds", {}).get("elements", []) if p.get("refunds") else []))
    return gross - refunds

# ── main ───────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Print net sales via Clover API")
    ap.add_argument("-r", "--range",
                    choices=["today", "yesterday", "week", "month"],
                    default="today",
                    help="Date range (default: today)")
    args = ap.parse_args()

    cfg = load_cfg(CONFIG_FILE)
    start_ms, end_ms, sd, ed = window(args.range)
    cents = net_sales_cents(get_payments(cfg, start_ms, end_ms))

    label = sd.strftime("%Y-%m-%d") if sd == ed else f"{sd:%Y-%m-%d} → {ed:%Y-%m-%d}"
    print(f"Net sales (12 p.m.–midnight CT) for {label}: ${cents/100:,.2f}")

if __name__ == "__main__":
    main()
