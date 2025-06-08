#!/usr/bin/env python3
"""
clover_net_sales.py  – Version 3 (discount endpoint fix, positive display)
-----------------------------------------------------------------------
• Net-metrics between 12 p.m. and 12 a.m. Central Time
  -r {today,yesterday,week,month}     (default: today)
  -q {sales,tax,tips,discounts}       (default: sales)

• Quick listings
  -l {employees,discounts,items}

CONFIG  – ./config.json

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

# Constants
CONFIG_FILE = Path(__file__).with_name("config.json")
PAGE_LIMIT  = 1000
CENTRAL_TZ  = ZoneInfo("America/Chicago")

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
    if range_key == "today":
        s = e = today
    elif range_key == "yesterday":
        s = e = today - timedelta(days=1)
    elif range_key == "week":
        s = max(sunday_of_week(today), today.replace(day=1))
        e = today
    elif range_key == "month":
        s, e = today.replace(day=1), today
    else:
        sys.exit(f"❌  Unknown range '{range_key}'.")
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

# Data fetch for payments

def get_payments(cfg, start_ms, end_ms):
    mid = cfg["merchant_id"]
    path = (
        f"/v3/merchants/{mid}/payments"
        f"?filter=createdTime>{start_ms}"
        f"&filter=createdTime<{end_ms}"
        f"&filter=result=SUCCESS"
        f"&filter=voided=false"
        f"&expand=refunds"
        f"&expand=order"
    )
    return paged_get(cfg, path)

# Data fetch for orders (to get discounts)

def get_orders(cfg, start_ms, end_ms):
    mid = cfg["merchant_id"]
    path = (
        f"/v3/merchants/{mid}/orders"
        f"?filter=createdTime>{start_ms}"
        f"&filter=createdTime<{end_ms}"
        f"&expand=discounts"
    )
    return paged_get(cfg, path)

# Metrics

def net_sales_cents(payments: list[dict]) -> int:
    gross = sum(p.get("amount", 0) for p in payments)
    refunds = sum(ref.get("amount", 0)
                  for p in payments
                  for ref in (p.get("refunds", {}).get("elements", []) if p.get("refunds") else []))
    return gross - refunds

def total_tax_cents(payments: list[dict]) -> int:
    return sum(p.get("taxAmount", 0) for p in payments)

def total_tips_cents(payments: list[dict]) -> int:
    return sum(p.get("tipAmount", 0) for p in payments)

def total_discounts_cents(orders: list[dict]) -> int:
    total = 0
    for o in orders:
        for d in o.get("discounts", {}).get("elements", []):
            total += d.get("amount", 0)
    return total

# Listings

def list_resource(cfg: dict, resource: str) -> None:
    mid = cfg["merchant_id"]
    if resource == "employees":
        path, key = f"/v3/merchants/{mid}/employees?", "name"
    elif resource == "discounts":
        path, key = f"/v3/merchants/{mid}/discounts?", "name"
    elif resource == "items":
        path, key = f"/v3/merchants/{mid}/items?", "name"
    else:
        sys.exit("❌  List option must be: employees, discounts, items")
    rows = paged_get(cfg, path)
    print(f"{resource.capitalize()} ({len(rows)}):")
    for r in rows:
        print(f"• {r.get(key)}   [{r.get('id')}]")

# Main

def main() -> None:
    parser = argparse.ArgumentParser(description="Clover metrics & listing tool")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-l", "--list",
                       choices=["employees","discounts","items"],
                       help="List resources and exit")
    parser.add_argument("-r", "--range",
                        choices=["today","yesterday","week","month"],
                        default="today",
                        help="Date range for metrics (default: today)")
    parser.add_argument("-q","--query",
                        choices=["sales","tax","tips","discounts"],
                        default="sales",
                        help="Metric to calculate (default: sales)")
    args = parser.parse_args()
    cfg = load_cfg(CONFIG_FILE)

    if args.list:
        list_resource(cfg,args.list)
        return

    start_ms, end_ms, sd, ed = window(args.range)
    if args.query == "discounts":
        orders = get_orders(cfg, start_ms, end_ms)
        cents, label = total_discounts_cents(orders), "Total discounts"
    else:
        payments = get_payments(cfg, start_ms, end_ms)
        if args.query == "sales":
            cents, label = net_sales_cents(payments), "Net sales"
        elif args.query == "tax":
            cents, label = total_tax_cents(payments), "Total tax"
        elif args.query == "tips":
            cents, label = total_tips_cents(payments), "Total tips"

    date_lbl = sd.strftime("%Y-%m-%d") if sd == ed else f"{sd:%Y-%m-%d} → {ed:%Y-%m-%d}"
    # Display absolute value so discounts appear positive
    value = abs(cents) / 100
    print(f"{label} (12 p.m.–midnight CT) for {date_lbl}: ${value:,.2f}")

if __name__ == "__main__":
    main()
