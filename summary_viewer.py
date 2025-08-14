import argparse
import sqlite3
import csv
import os
from datetime import datetime
from collections import Counter
from html import escape

DB_FILE = r"C:\AuditData\logs.db"   # must match db.py

def connect():
    if not os.path.exists(DB_FILE):
        raise SystemExit(f"DB not found: {DB_FILE}")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def parse_args():
    p = argparse.ArgumentParser(
        description="Audit Log Summary Viewer (filters, grouping, export)"
    )
    p.add_argument("--since", help="ISO time (e.g. 2025-08-09T00:00:00) or 'today', 'yesterday'")
    p.add_argument("--until", help="ISO time (e.g. 2025-08-09T23:59:59)")
    p.add_argument("--type", dest="atype", help='Filter by action type substring (e.g. "File created")')
    p.add_argument("--contains", help='Filter rows whose "action" contains this text (e.g. "Downloads")')
    p.add_argument("--group", choices=["type", "path", "none"], default="type",
                   help="Group summary by 'type', 'path', or 'none' for raw rows")
    p.add_argument("--top", type=int, default=10, help="Show top N when grouping by type/path (default 10)")
    p.add_argument("--export-csv", metavar="FILE", help="Export filtered rows to CSV")
    p.add_argument("--export-html", metavar="FILE", help="Export filtered rows to HTML")
    p.add_argument("--limit", type=int, default=0, help="Limit raw rows shown (0 = all)")
    return p.parse_args()

def resolve_relative(ts):
    if not ts: return None
    s = ts.lower()
    now = datetime.now()
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if s == "yesterday":
        y = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (y.fromtimestamp(y.timestamp() - 86400)).isoformat()
    # assume ISO
    return ts

def fetch_rows(args):
    q = "SELECT timestamp, action FROM audit_logs WHERE 1=1"
    params = []
    since = resolve_relative(args.since)
    until = resolve_relative(args.until)

    if since:
        q += " AND timestamp >= ?"
        params.append(since)
    if until:
        q += " AND timestamp <= ?"
        params.append(until)
    if args.atype:
        q += " AND action LIKE ?"
        params.append(f"%{args.atype}%")
    if args.contains:
        q += " AND action LIKE ?"
        params.append(f"%{args.contains}%")

    q += " ORDER BY timestamp DESC"
    with connect() as conn:
        rows = conn.execute(q, params).fetchall()
    return rows

def split_action(row_action):
    if ":" in row_action:
        a, d = row_action.split(":", 1)
        return a.strip(), d.strip()
    return row_action.strip(), ""

def group_summary(rows, by="type", top=10):
    if by == "none":
        return None
    counter = Counter()
    for r in rows:
        a, d = split_action(r["action"])
        key = a if by == "type" else (d if d else a)
        counter[key] += 1
    return counter.most_common(top)

def print_table(headers, data):
    # simple fixed-width print
    colw = [max(len(str(h)), *(len(str(row[i])) for row in data)) for i, h in enumerate(headers)]
    def line():
        print("+-" + "-+-".join("-" * w for w in colw) + "-+")
    def row(vals):
        print("| " + " | ".join(str(vals[i]).ljust(colw[i]) for i in range(len(headers))) + " |")

    line(); row(headers); line()
    for r in data: row(r)
    line()

def export_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "action_type", "detail"])
        for r in rows:
            at, dt = split_action(r["action"])
            w.writerow([r["timestamp"], at, dt])
    print(f"✅ Exported CSV: {path}")

def export_html(rows, path, title="Audit Log Report"):
    html_rows = []
    for r in rows:
        at, dt = split_action(r["action"])
        html_rows.append(
            f"<tr><td>{escape(r['timestamp'])}</td>"
            f"<td>{escape(at)}</td>"
            f"<td>{escape(dt)}</td></tr>"
        )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{escape(title)}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;padding:16px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ddd;padding:8px}}
th{{background:#f4f4f4;text-align:left}}
tr:nth-child(even){{background:#fafafa}}
</style></head>
<body>
<h2>{escape(title)}</h2>
<table>
  <thead><tr><th>Timestamp</th><th>Action Type</th><th>Detail</th></tr></thead>
  <tbody>
    {''.join(html_rows)}
  </tbody>
</table>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Exported HTML: {path}")

def main():
    args = parse_args()
    rows = fetch_rows(args)

    # Summary
    if args.group != "none":
        summary = group_summary(rows, by=args.group, top=args.top)
        if args.group == "type":
            print("\n📊 Actions by Type:\n")
        else:
            print("\n📁 Top Items by Path/Detail:\n")
        if summary:
            print_table(["Item", "Count"], [(k, v) for k, v in summary])
        else:
            print("(no data)")

    # Raw rows (optional)
    if args.group == "none":
        print("\n🧾 Rows:\n")
        data = []
        lim = args.limit if args.limit > 0 else len(rows)
        for r in rows[:lim]:
            at, dt = split_action(r["action"])
            data.append((r["timestamp"], at, dt))
        if data:
            print_table(["Timestamp", "Action Type", "Detail"], data)
        else:
            print("(no data)")

    # Exports
    if args.export_csv:
        export_csv(rows, args.export_csv)
    if args.export_html:
        export_html(rows, args.export_html, title="Audit Log Report")

if __name__ == "__main__":
    main()
