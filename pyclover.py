#!/usr/bin/env python3
"""
clover_net_sales.py  – Version 12 (sales excluding tax, employee tip & discount breakdown + sales detail)
-----------------------------------------------------------------------
• Net-metrics between 12 p.m. and 12 a.m. Central Time
  -r {today,yesterday,week,month,last_week,last_month,YYYY,YYYY-MM-DD:YYYY-MM-DD}     (default: today)
  -q {sales,tax,tips,discounts}       (default: sales)
  -d                                  (detailed breakdown - employee tips, discount names, or sales by time)
  -g                                  (graph mode - only valid with -d, requires termgraph library)

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
import tempfile
import subprocess
import csv
from typing import Optional

# ANSI color codes
ANSI_RESET = '\033[0m'
ANSI_GREEN = '\033[32m'
ANSI_YELLOW = '\033[33m'
ANSI_RED = '\033[31m'

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

def create_termgraph(data: dict, title: str, value_suffix: str = "", threshold: Optional[int] = None) -> None:
    """Create and display a terminal bar graph using unicode full block (U+2587), with optional color thresholding."""
    if not data:
        print(f"\n📊  No data available for {title}")
        return

    # Prepare data: convert cents to dollars if int, and get max label/value
    processed = []
    max_value = 0
    for label, value in data.items():
        dollar_value = abs(value) / 100 if isinstance(value, int) else abs(value)
        processed.append((label, dollar_value))
        if dollar_value > max_value:
            max_value = dollar_value
    if max_value == 0:
        print(f"\n📊  No data available for {title}")
        return

    # Bar graph settings
    max_bar_width = 50
    shade = '\u2587'  # Unicode full block lower 7/8
    label_width = max(len(str(label)) for label, _ in processed)
    value_fmt = "{:.2f}"
    suffix = f" {value_suffix}" if value_suffix else " $"

    print(f"\n📊  {title}")
    print("=" * len(title))
    for label, value in processed:
        bar_len = int((value / max_value) * max_bar_width)
        bar = shade * bar_len
        color = ''
        if threshold is not None:
            # Round for color logic to avoid floating point precision issues
            v = round(value, 2)
            t = round(threshold, 2)
            if v >= t:
                color = ANSI_GREEN
            elif v <= 0.25 * t:
                color = ANSI_RED
            else:
                color = ANSI_YELLOW
        print(f"{label.ljust(label_width)} | {color}{bar.ljust(max_bar_width)}{ANSI_RESET} {value_fmt.format(value)}{suffix}")

def window(range_key: str):
    today = datetime.now(CENTRAL_TZ).date()

    # Check for discrete date range format: YYYY-MM-DD:YYYY-MM-DD
    if ':' in range_key:
        try:
            start_str, end_str = range_key.split(':', 1)
            s = date.fromisoformat(start_str)
            e = date.fromisoformat(end_str)
            
            if s > e:
                sys.exit(f"❌  Start date ({s}) cannot be after end date ({e})")
            
            start_dt = datetime.combine(s, time(12, 0), tzinfo=CENTRAL_TZ)
            end_dt = datetime.combine(e + timedelta(days=1), time(0, 0), tzinfo=CENTRAL_TZ)
            return epoch_ms(start_dt), epoch_ms(end_dt), s, e, "range"
        except ValueError as ex:
            sys.exit(f"❌  Invalid date range format '{range_key}'. Use YYYY-MM-DD:YYYY-MM-DD. Error: {ex}")

    # Check if it's "year" (current year) or a year (YYYY format)
    if range_key == "year":
        year = today.year
        s = date(year, 1, 1)
        e = date(year, 12, 31)
        start_dt = datetime.combine(s, time(12, 0), tzinfo=CENTRAL_TZ)
        end_dt = datetime.combine(e + timedelta(days=1), time(0, 0), tzinfo=CENTRAL_TZ)
        return epoch_ms(start_dt), epoch_ms(end_dt), s, e, "year"
    
    try:
        year = int(range_key)
        if 2000 <= year <= 2099:  # Reasonable year range
            s = date(year, 1, 1)
            e = date(year, 12, 31)
            start_dt = datetime.combine(s, time(12, 0), tzinfo=CENTRAL_TZ)
            end_dt = datetime.combine(e + timedelta(days=1), time(0, 0), tzinfo=CENTRAL_TZ)
            return epoch_ms(start_dt), epoch_ms(end_dt), s, e, "year"
    except ValueError:
        pass

    # Check if it's a ISO-date we'll treat it as a single-day range
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
    # Previous calendar week (Sunday→Saturday)
    elif range_key == "last_week":
        current_week_start = sunday_of_week(today)
        s = current_week_start - timedelta(days=7)
        e = current_week_start - timedelta(days=1)
        range_type = "range"
    # Previous calendar month
    elif range_key == "last_month":
        first_of_current = today.replace(day=1)
        last_of_prev = first_of_current - timedelta(days=1)
        s = last_of_prev.replace(day=1)
        e = last_of_prev
        range_type = "range"
    else:
        sys.exit(f"❌  Unknown range or bad date '{range_key}'.")
    
    start_dt = datetime.combine(s, time(12, 0), tzinfo=CENTRAL_TZ)
    end_dt = datetime.combine(e + timedelta(days=1), time(0, 0), tzinfo=CENTRAL_TZ)
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

# Data fetch

def get_payments(cfg: dict, start_ms: int, end_ms: int) -> list[dict]:
    mid = cfg["merchant_id"]
    
    # For large date ranges (more than 90 days), chunk the requests
    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    days_diff = (end_dt - start_dt).days
    
    if days_diff > 90:
        # Chunk into monthly requests for large ranges
        all_payments = []
        
        # Convert back to Central Time dates for proper chunking
        start_central = start_dt.astimezone(CENTRAL_TZ)
        end_central = end_dt.astimezone(CENTRAL_TZ)
        
        # Start with first day of the year/range
        current_date = start_central.date()
        end_date = end_central.date()
        
        while current_date <= end_date:
            # Get end of current month or end_date, whichever is earlier
            if current_date.month == 12:
                next_month_start = date(current_date.year + 1, 1, 1)
            else:
                next_month_start = date(current_date.year, current_date.month + 1, 1)
            
            # Last day of current month or end_date
            month_end = min(next_month_start - timedelta(days=1), end_date)
            
            # Create proper 12pm-12am windows for this month
            chunk_start_dt = datetime.combine(current_date, time(12, 0), tzinfo=CENTRAL_TZ)
            chunk_end_dt = datetime.combine(month_end + timedelta(days=1), time(0, 0), tzinfo=CENTRAL_TZ)
            
            chunk_start_ms = epoch_ms(chunk_start_dt)
            chunk_end_ms = epoch_ms(chunk_end_dt)
            
            path = (
                f"/v3/merchants/{mid}/payments"
                f"?filter=createdTime>={chunk_start_ms}"
                f"&filter=createdTime<{chunk_end_ms}"
                f"&filter=result=SUCCESS"
                f"&filter=voided=false"
                f"&expand=refunds"
                f"&expand=order"
                f"&expand=employee"
                f"&expand=order.employee"
            )
            
            chunk_payments = paged_get(cfg, path)
            all_payments.extend(chunk_payments)
            current_date = next_month_start
            
        return all_payments
    else:
        # Normal single request for smaller ranges
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
    
    # For large date ranges (more than 90 days), chunk the requests
    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    days_diff = (end_dt - start_dt).days
    
    if days_diff > 90:
        # Chunk into monthly requests for large ranges
        all_orders = []
        current_dt = start_dt
        
        while current_dt < end_dt:
            # Get end of current month or end_dt, whichever is earlier
            if current_dt.month == 12:
                next_month = current_dt.replace(year=current_dt.year + 1, month=1, day=1)
            else:
                next_month = current_dt.replace(month=current_dt.month + 1, day=1)
            
            chunk_end = min(next_month, end_dt)
            chunk_start_ms = int(current_dt.timestamp() * 1000)
            chunk_end_ms = int(chunk_end.timestamp() * 1000)
            
            print(f"Fetching orders for {current_dt.strftime('%Y-%m')}...")
            
            path = (
                f"/v3/merchants/{mid}/orders"
                f"?filter=createdTime>{chunk_start_ms}"
                f"&filter=createdTime<{chunk_end_ms}"
                f"&expand=discounts"
                f"&expand=lineItems"
            )
            
            chunk_orders = paged_get(cfg, path)
            all_orders.extend(chunk_orders)
            current_dt = next_month
            
        return all_orders
    else:
        # Normal single request for smaller ranges
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

# Sales breakdown functions
def sales_by_hour(payments: list[dict], target_date: date) -> dict:
    """Break down sales by hour for a single day"""
    hourly_sales = defaultdict(int)
    
    for p in payments:
        created_time = p.get("createdTime")
        if not created_time:
            continue
            
        # Convert timestamp to Central Time
        dt = datetime.fromtimestamp(created_time / 1000, tz=timezone.utc).astimezone(CENTRAL_TZ)
        
        # Only include payments from the target date between 12pm-12am
        if dt.date() == target_date:
            if dt.hour >= 12:  # 12pm to 11:59pm
                hour_key = dt.hour
            else:
                continue  # Skip payments before 12pm
        elif dt.date() == target_date + timedelta(days=1) and dt.hour == 0:
            # Include midnight (12am) of next day
            hour_key = 24  # Use 24 to represent midnight
        else:
            continue
            
        # Calculate net sales for this payment
        gross = p.get("amount", 0)
        tax = p.get("taxAmount", 0)
        refunds = sum(
            ref.get("amount", 0)
            for ref in (p.get("refunds", {}).get("elements", []) if p.get("refunds") else [])
        )
        net_sales = gross - tax - refunds
        hourly_sales[hour_key] += net_sales
    
    return hourly_sales

def sales_by_day(payments: list[dict], start_date: date, end_date: date) -> dict:
    """Break down sales by day for a date range"""
    daily_sales = defaultdict(int)
    
    for p in payments:
        created_time = p.get("createdTime")
        if not created_time:
            continue
            
        # Convert timestamp to Central Time
        dt = datetime.fromtimestamp(created_time / 1000, tz=timezone.utc).astimezone(CENTRAL_TZ)
        
        # Adjust for 12pm-12am window
        payment_date = dt.date()
        if dt.hour < 12:  # Before 12pm, count as previous day
            payment_date = payment_date - timedelta(days=1)
            
        # Only include payments within our date range
        if not (start_date <= payment_date <= end_date):
            continue
            
        # Calculate net sales for this payment
        gross = p.get("amount", 0)
        tax = p.get("taxAmount", 0)
        refunds = sum(
            ref.get("amount", 0)
            for ref in (p.get("refunds", {}).get("elements", []) if p.get("refunds") else [])
        )
        net_sales = gross - tax - refunds
        daily_sales[payment_date] += net_sales
    
    return daily_sales

def sales_by_month(payments: list[dict], year: int) -> dict:
    """Break down sales by month for a year"""
    monthly_sales = defaultdict(int)
    
    for p in payments:
        created_time = p.get("createdTime")
        if not created_time:
            continue
            
        # Convert timestamp to Central Time
        dt = datetime.fromtimestamp(created_time / 1000, tz=timezone.utc).astimezone(CENTRAL_TZ)
        
        # Adjust for 12pm-12am window
        payment_date = dt.date()
        if dt.hour < 12:  # Before 12pm, count as previous day
            payment_date = payment_date - timedelta(days=1)
            
        # Only include payments from the target year
        if payment_date.year != year:
            continue
            
        # Calculate net sales for this payment
        gross = p.get("amount", 0)
        tax = p.get("taxAmount", 0)
        refunds = sum(
            ref.get("amount", 0)
            for ref in (p.get("refunds", {}).get("elements", []) if p.get("refunds") else [])
        )
        net_sales = gross - tax - refunds
        monthly_sales[payment_date.month] += net_sales
    
    return monthly_sales

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
                            "Date range (today, yesterday, week, month, last_week, last_month, year, YYYY, "
                            "YYYY-MM-DD, or YYYY-MM-DD:YYYY-MM-DD for custom date ranges)"
                        ))
    parser.add_argument("-q", "--query",
                        dest="query",
                        choices=["sales","tax","tips","discounts"],
                        default="sales",
                        help="Metric to calculate (default: sales)")
    parser.add_argument("-d", "--detail",
                        action="store_true",
                        help="Show detailed breakdown for tips, discounts, or sales by time")
    parser.add_argument("-g", "--graph",
                        action="store_true",
                        help="Show graph visualization (requires -d flag)")
    parser.add_argument("-l", "--list",
                        choices=["employees","discounts","items"],
                        help="Quick list of employees, discounts, or items")
    parser.add_argument("-o", "--output",
                        metavar="FILENAME",
                        help="Export detailed breakdown to CSV file (requires -d)")
    parser.add_argument("-t", "--threshold",
                        type=int,
                        help="Threshold for bar graph coloring (requires -d and -g)")
    args = parser.parse_args()

    # Validate -g flag usage
    if args.graph and not args.detail:
        sys.exit("❌  Graph mode (-g) requires detail mode (-d)")
    # Validate -o flag usage
    if args.output and not args.detail:
        sys.exit("❌  Output mode (-o) requires detail mode (-d)")
    # Validate -t flag usage
    if args.threshold is not None:
        if not (args.detail and args.graph):
            sys.exit("❌  Threshold (-t) requires both detail (-d) and graph (-g) mode.")
        if args.threshold < 0:
            sys.exit("❌  Threshold (-t) must be a non-negative integer.")

    cfg = load_cfg(CONFIG_FILE)

    if args.list:
        list_resource(cfg, args.list)
        return

    start_ms, end_ms, sd, ed, range_type = window(args.range)

    def export_csv(data, headers, filename):
        with open(filename, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headers)
            for row in data:
                writer.writerow(row)
        print(f"\n✅  Exported breakdown to {filename}")

    if args.query == "discounts":
        orders = get_orders(cfg, start_ms, end_ms)
        if args.detail:
            disc_map = build_discount_map(cfg)
            breakdown = discounts_breakdown(orders, disc_map)
            print("\nBreakdown by discount:")
            if breakdown:
                for name, cents in sorted(breakdown.items()):
                    print(f"• {name}: ${cents/100:,.2f}")
                # Export to CSV if requested
                if args.output:
                    export_csv(
                        [(name, f"{cents/100:.2f}") for name, cents in sorted(breakdown.items())],
                        ["Discount Name", "Amount ($)"],
                        args.output
                    )
                # Show graph if requested
                if args.graph:
                    create_termgraph(breakdown, "Discount Breakdown", threshold=args.threshold)
            else:
                print("• No discounts recorded")
            return
        cents, label = total_discounts_cents(orders), "Total discounts"
    else:
        payments = get_payments(cfg, start_ms, end_ms)
        if args.query == "sales":
            cents, label = net_sales_cents(payments), "Net sales"
            if args.detail:
                if range_type == "day":
                    # Single day - show hourly breakdown
                    hourly_breakdown = sales_by_hour(payments, sd)
                    print(f"\nHourly sales breakdown for {sd.strftime('%Y-%m-%d')}:" )
                    if hourly_breakdown:
                        for hour in sorted(hourly_breakdown.keys()):
                            if hour == 24:
                                time_label = "12:00 AM"
                            elif hour == 12:
                                time_label = "12:00 PM"
                            elif hour > 12:
                                time_label = f"{hour-12}:00 PM"
                            else:
                                time_label = f"{hour}:00 AM"
                            print(f"• {time_label}: ${hourly_breakdown[hour]/100:,.2f}")
                        # Export to CSV if requested
                        if args.output:
                            export_csv(
                                [
                                    ("12:00 PM" if hour == 12 else
                                     "12:00 AM" if hour == 24 else
                                     f"{hour-12}:00 PM" if hour > 12 else f"{hour}:00 AM",
                                     f"{hourly_breakdown[hour]/100:.2f}")
                                    for hour in sorted(hourly_breakdown.keys())
                                ],
                                ["Hour", "Net Sales ($)"],
                                args.output
                            )
                        # Show graph if requested
                        if args.graph:
                            graph_data = {}
                            for hour in sorted(hourly_breakdown.keys()):
                                if hour == 24:
                                    time_label = "12AM"
                                elif hour == 12:
                                    time_label = "12PM"
                                elif hour > 12:
                                    time_label = f"{hour-12}PM"
                                else:
                                    time_label = f"{hour}AM"
                                graph_data[time_label] = hourly_breakdown[hour]
                            create_termgraph(graph_data, f"Hourly Sales - {sd.strftime('%Y-%m-%d')}", threshold=args.threshold)
                    else:
                        print("• No sales recorded during business hours")
                    return
                elif range_type == "range":
                    # Date range - show daily breakdown
                    daily_breakdown = sales_by_day(payments, sd, ed)
                    date_lbl = sd.strftime("%Y-%m-%d") if sd == ed else f"{sd:%Y-%m-%d} → {ed:%Y-%m-%d}"
                    print(f"\nDaily sales breakdown for {date_lbl}:")
                    if daily_breakdown:
                        for day in sorted(daily_breakdown.keys()):
                            print(f"• {day.strftime('%Y-%m-%d')}: ${daily_breakdown[day]/100:,.2f}")
                        # Export to CSV if requested
                        if args.output:
                            export_csv(
                                [
                                    (day.strftime('%Y-%m-%d'), f"{daily_breakdown[day]/100:.2f}")
                                    for day in sorted(daily_breakdown.keys())
                                ],
                                ["Date", "Net Sales ($)"],
                                args.output
                            )
                        # Show graph if requested
                        if args.graph:
                            graph_data = {day.strftime('%m/%d'): value for day, value in daily_breakdown.items()}
                            create_termgraph(graph_data, f"Daily Sales - {date_lbl}", threshold=args.threshold)
                    else:
                        print("• No sales recorded")
                    return
                elif range_type == "year":
                    # Year - show monthly breakdown
                    monthly_breakdown = sales_by_month(payments, sd.year)
                    print(f"\nMonthly sales breakdown for {sd.year}:")
                    if monthly_breakdown:
                        for month in sorted(monthly_breakdown.keys()):
                            month_name = calendar.month_name[month]
                            print(f"• {month_name}: ${monthly_breakdown[month]/100:,.2f}")
                        # Export to CSV if requested
                        if args.output:
                            export_csv(
                                [
                                    (calendar.month_name[month], f"{monthly_breakdown[month]/100:.2f}")
                                    for month in sorted(monthly_breakdown.keys())
                                ],
                                ["Month", "Net Sales ($)"],
                                args.output
                            )
                        # Show graph if requested
                        if args.graph:
                            graph_data = {calendar.month_abbr[month]: value for month, value in monthly_breakdown.items()}
                            create_termgraph(graph_data, f"Monthly Sales - {sd.year}", threshold=args.threshold)
                    else:
                        print("• No sales recorded")
                    return
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
                    # Export to CSV if requested
                    if args.output:
                        export_csv(
                            [(name, f"{cents/100:.2f}") for name, cents in sorted(breakdown.items())],
                            ["Employee", "Tips ($)"],
                            args.output
                        )
                    # Show graph if requested
                    if args.graph:
                        create_termgraph(breakdown, "Tips by Employee", threshold=args.threshold)
                else:
                    print("• No tips recorded")
                return
        else:
            sys.exit(f"❌  Unknown query '{args.query}'")

    date_lbl = sd.strftime("%Y-%m-%d") if sd == ed else f"{sd:%Y-%m-%d} → {ed:%Y-%m-%d}"
    print(f"{label} (12 p.m.–midnight CT) for {date_lbl}: ${abs(cents)/100:,.2f}")

if __name__ == "__main__":
    main()