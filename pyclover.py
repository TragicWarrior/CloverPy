#!/usr/bin/env python3
"""
clover_net_sales.py  – Version 11 (sales excluding tax, employee tip & discount breakdown)
-----------------------------------------------------------------------
• Net-metrics between 12 p.m. and 12 a.m. Central Time
  -r {today,yesterday,week,month}     (default: today)
  -q {sales,tax,tips,discounts}       (default: sales)
  -d                                  (detailed breakdown - employee tips or discount names)

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

    # 1) if it's a ISO-date we'll treat it as a single-day range
    try:
        single = date.fromisoformat(range_key)
    except ValueError:
        single = None

    if single:
        s = e = single
    elif range_key == "today":
        s = e = today
    elif range_key == "yesterday":
        s = e = today - timedelta(days=1)
    elif range_key == "week":
        s = max(sunday_of_week(today), today.replace(day=1))
        e = today
    elif range_key == "month":
        s, e = today.replace(day=1), today
    else:
        sys.exit(f"❌  Unknown range or bad date '{range_key}'.")
    start_dt = datetime.combine(s, time(12, 0), tzinfo=CENTRAL_TZ)
    end_dt = datetime.combine(e + timedelta(days=1), time(0, 0), tzinfo=CENTRAL_TZ)
    return epoch_ms(start_dt), epoch_ms(end_dt), s, e

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

# Data fetch

def get_payments(cfg: dict, start_ms: int, end_ms: int) -> list[dict]:
    mid = cfg["merchant_id"]
    path = (
        f"/v3/merchants/{mid}/payments"
        f"?filter=createdTime>{start_ms}"
        f"&filter=createdTime<{end_ms}"
        f"&filter=result=SUCCESS"
        f"&filter=voided=false"
        f"&expand=refunds"
        f"&expand=order"
        f"&expand=employee"
        f"&expand=order.employee"
    )
    return paged_get(cfg, path)

def get_orders(cfg: dict, start_ms: int, end_ms: int) -> list[dict]:
    mid = cfg["merchant_id"]
    path = (
        f"/v3/merchants/{mid}/orders"
        f"?filter=createdTime>{start_ms}"
        f"&filter=createdTime<{end_ms}"
        f"&expand=discounts"
        f"&expand=lineItems"
    )
    return paged_get(cfg, path)

# Mapping helpers
def build_employee_map(cfg: dict) -> dict:
    mid = cfg["merchant_id"]
    emps = paged_get(cfg, f"/v3/merchants/{mid}/employees?")
    return {e.get("id"): e.get("name", e.get("id")) for e in emps}

def build_discount_map(cfg: dict) -> dict:
    mid = cfg["merchant_id"]
    discs = paged_get(cfg, f"/v3/merchants/{mid}/discounts?")
    return {d.get("id"): d.get("name", d.get("id")) for d in discs}

# Metrics
def net_sales_cents(payments: list[dict]) -> int:
    gross = sum(p.get("amount", 0) for p in payments)
    tax = sum(p.get("taxAmount", 0) for p in payments)
    refunds = sum(
        ref.get("amount", 0)
        for p in payments
        for ref in (p.get("refunds", {}).get("elements", []) if p.get("refunds") else [])
    )
    return gross - tax - refunds

def total_tax_cents(payments: list[dict]) -> int:
    return sum(p.get("taxAmount", 0) for p in payments)

def total_tips_cents(payments: list[dict]) -> int:
    return sum(p.get("tipAmount", 0) for p in payments)

def tips_by_employee(payments: list[dict], employee_map: dict) -> dict:
    emap = {}
    for p in payments:
        amt = p.get("tipAmount", 0)
        if amt <= 0:
            continue
        emp_id = None
        if p.get("employee") and isinstance(p["employee"], dict):
            emp_id = p["employee"].get("id")
        elif p.get("order") and p["order"].get("employee") and isinstance(p["order"]["employee"], dict):
            emp_id = p["order"]["employee"].get("id")
        name = employee_map.get(emp_id, emp_id) if emp_id else "Unknown Employee"
        emap[name] = emap.get(name, 0) + amt
    return emap

def total_discounts_cents(orders: list[dict]) -> int:
    return sum(d.get("amount", 0) for o in orders for d in o.get("discounts", {}).get("elements", []))

# Discount breakdown using discount map for all definitions
def discounts_breakdown(orders: list[dict], discount_map: dict) -> dict:
    dmap = {}
    for o in orders:
        # Get order total for percentage calculations
        order_total = o.get("total", 0)
        
        for d in o.get("discounts", {}).get("elements", []):
            # Use the name directly from the discount if available
            name = d.get("name", "Unknown Discount")
            
            # If name is generic "Discount", try to get more specific name from discount map
            if name == "Discount":
                did = None
                if isinstance(d.get("discount"), dict):
                    did = d["discount"].get("id")
                elif d.get("discountDefinitionId"):
                    did = d.get("discountDefinitionId")
                elif d.get("discountId"):
                    did = d.get("discountId")
                
                if did and did in discount_map:
                    name = discount_map[did]
                else:
                    name = "Manual Discount"
            
            # Calculate discount amount
            amount = 0
            if d.get("amount"):
                # Fixed amount discount
                amount = d.get("amount", 0)
            elif d.get("percentage") and order_total > 0:
                # Percentage-based discount - calculate from order total
                percentage = d.get("percentage", 0)
                # Calculate percentage discount (negative because it's a discount)
                amount = -int((order_total * percentage) / 100)
            
            dmap[name] = dmap.get(name, 0) + amount
    return dmap

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
        print(f"• {r.get(key)}   [{r.get('id')}] ")

# Main
def main():
    parser = argparse.ArgumentParser(
        description="Clover net metrics (sales, tax, tips, discounts)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("-r", "--range",
                        dest="range",
                        default="today",
                        help=(
                            "Date range (today, yesterday, week, month) "
                            "or a specific date YYYY-MM-DD"
                        ))
    parser.add_argument("-q", "--query",
                        dest="query",
                        choices=["sales","tax","tips","discounts"],
                        default="sales",
                        help="Metric to calculate (default: sales)")
    parser.add_argument("-d", "--detail",
                        action="store_true",
                        help="Show detailed breakdown for tips or discounts")
    parser.add_argument("-l", "--list",
                        choices=["employees","discounts","items"],
                        help="Quick list of employees, discounts, or items")
    args = parser.parse_args()

    cfg = load_cfg(CONFIG_FILE)

    if args.list:
        list_resource(cfg, args.list)
        return

    start_ms, end_ms, sd, ed = window(args.range)

    if args.query == "discounts":
        orders = get_orders(cfg, start_ms, end_ms)
        if args.detail:
            disc_map = build_discount_map(cfg)
            breakdown = discounts_breakdown(orders, disc_map)
            print("\nBreakdown by discount:")
            if breakdown:
                for name, cents in sorted(breakdown.items()):
                    print(f"• {name}: ${cents/100:,.2f}")
            else:
                print("• No discounts recorded")
            return
        cents, label = total_discounts_cents(orders), "Total discounts"
    else:
        payments = get_payments(cfg, start_ms, end_ms)
        if args.query == "sales":
            cents, label = net_sales_cents(payments), "Net sales"
        elif args.query == "tax":
            cents, label = total_tax_cents(payments), "Total tax"
        elif args.query == "tips":
            cents, label = total_tips_cents(payments), "Total tips"
            if args.detail:
                emp_map = build_employee_map(cfg)
                breakdown = tips_by_employee(payments, emp_map)
                print("\nBreakdown by employee:")
                if breakdown:
                    for name, cents in sorted(breakdown.items()):
                        print(f"• {name}: ${cents/100:,.2f}")
                else:
                    print("• No tips recorded")
                return
        else:
            sys.exit(f"❌  Unknown query '{args.query}'")

    date_lbl = sd.strftime("%Y-%m-%d") if sd == ed else f"{sd:%Y-%m-%d} → {ed:%Y-%m-%d}"
    print(f"{label} (12 p.m.–midnight CT) for {date_lbl}: ${abs(cents)/100:,.2f}")

if __name__ == "__main__":
    main()
