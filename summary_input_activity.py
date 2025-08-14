import argparse, sqlite3, os, json, csv
from datetime import datetime, timedelta
from collections import defaultdict
from html import escape

# Match your existing DB path (db.py / other summaries)
DB_FILE = r"C:\AuditData\logs.db"

def connect():
    if not os.path.exists(DB_FILE):
        raise SystemExit(f"DB not found: {DB_FILE}")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def resolve_relative(ts):
    if not ts: return None
    s = ts.lower()
    now = datetime.now()
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if s == "yesterday":
        y = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (y - timedelta(days=1)).isoformat()
    return ts  # assume ISO

def fetch_rows(since, until, include_events=True, include_summaries=True):
    clauses = []
    params = []

    if since:
        clauses.append("timestamp >= ?")
        params.append(since)
    if until:
        clauses.append("timestamp <= ?")
        params.append(until)

    type_clause = []
    if include_summaries:
        type_clause.append("action LIKE 'Input summary:%'")
    if include_events:
        type_clause.append("action LIKE 'Input events:%'")

    where = "WHERE (" + " OR ".join(type_clause) + ")"
    if clauses:
        where += " AND " + " AND ".join(clauses)

    q = f"SELECT timestamp, action FROM audit_logs {where} ORDER BY timestamp ASC"
    with connect() as conn:
        return conn.execute(q, params).fetchall()

def parse_summary_line(action):
    # "Input summary: keys=... | clicks=... | scrolls=... | moves=... | interval=...s"
    try:
        _, detail = action.split(":", 1)
        parts = [p.strip() for p in detail.split("|")]
        d = {}
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                d[k.strip()] = v.strip().rstrip("s")
        return {
            "keys": int(float(d.get("keys", "0"))),
            "clicks": int(float(d.get("clicks", "0"))),
            "scrolls": int(float(d.get("scrolls", "0"))),
            "moves": int(float(d.get("moves", "0"))),
            "interval_s": float(d.get("interval", "0") or 0)
        }
    except Exception:
        return None

def parse_events_line(action):
    # "Input events: {json}"
    try:
        _, j = action.split(":", 1)
        payload = json.loads(j.strip())
        return payload
    except Exception:
        return None

def bucket_key(ts: datetime, bucket="hour"):
    if bucket == "minute":
        return ts.replace(second=0, microsecond=0)
    if bucket == "day":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    return ts.replace(minute=0, second=0, microsecond=0)

def print_table(rows, headers):
    widths = [len(h) for h in headers]
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(str(v)))
    def line():
        print("+-" + "-+-".join("-"*w for w in widths) + "-+")
    def row(vals):
        print("| " + " | ".join(str(vals[i]).ljust(widths[i]) for i in range(len(headers))) + " |")
    line(); row(headers); line()
    for r in rows: row(r)
    line()

def export_csv_summary(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bucket_start", "keys", "clicks", "scrolls", "moves", "interval_s"])
        for r in rows:
            w.writerow(r)
    print(f"✅ Exported CSV summary: {path}")

def export_csv_events(events_rows, path):
    keys = set()
    for ev in events_rows:
        keys.update(ev.keys())
    keys = ["t","e","k","b","dx","dy"] if not keys else sorted(keys)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for ev in events_rows:
            w.writerow(ev)
    print(f"✅ Exported CSV events: {path}")

def export_html_summary(rows, path, title="Input Activity Summary"):
    html_rows = []
    for r in rows:
        b, keys, clicks, scrolls, moves, interval_s = r
        html_rows.append(
            f"<tr><td>{escape(str(b))}</td><td>{keys}</td><td>{clicks}</td><td>{scrolls}</td><td>{moves}</td><td>{interval_s}</td></tr>"
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
  <thead>
    <tr><th>Bucket</th><th>Keys</th><th>Clicks</th><th>Scrolls</th><th>Moves</th><th>Interval(s)</th></tr>
  </thead>
  <tbody>
    {''.join(html_rows)}
  </tbody>
</table>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Exported HTML summary: {path}")

def main():
    p = argparse.ArgumentParser(description="Input Activity Summary (from Input summary/Input events rows)")
    p.add_argument("--since", help="ISO time or 'today'/'yesterday'")
    p.add_argument("--until", help="ISO time")
    p.add_argument("--bucket", choices=["minute","hour","day"], default="hour", help="Aggregate bucket size")
    p.add_argument("--export-csv", metavar="FILE", help="Export the summary table to CSV")
    p.add_argument("--export-html", metavar="FILE", help="Export the summary table to HTML")
    p.add_argument("--export-events-csv", metavar="FILE", help="Export flattened per-event rows to CSV")
    p.add_argument("--top", type=int, default=0, help="Show only top N buckets by total activity")
    args = p.parse_args()

    since = resolve_relative(args.since)
    until = resolve_relative(args.until)
    rows = fetch_rows(since, until, include_events=True, include_summaries=True)

    buckets = defaultdict(lambda: {"keys":0,"clicks":0,"scrolls":0,"moves":0,"interval_s":0.0})
    flat_events = []

    for r in rows:
        ts = datetime.fromisoformat(r["timestamp"])
        bkey = bucket_key(ts, args.bucket)
        act = r["action"]

        if act.startswith("Input summary:"):
            d = parse_summary_line(act)
            if d:
                agg = buckets[bkey]
                agg["keys"] += d["keys"]
                agg["clicks"] += d["clicks"]
                agg["scrolls"] += d["scrolls"]
                agg["moves"] += d["moves"]
                agg["interval_s"] += d["interval_s"]

        elif act.startswith("Input events:"):
            payload = parse_events_line(act)
            if payload:
                try:
                    wstart = datetime.fromisoformat(payload["window"]["start"])
                    bkey = bucket_key(wstart, args.bucket)
                except Exception:
                    pass
                cnts = payload.get("counts", {})
                agg = buckets[bkey]
                agg["keys"] += int(cnts.get("keys", 0))
                agg["clicks"] += int(cnts.get("clicks", 0))
                agg["scrolls"] += int(cnts.get("scrolls", 0))
                agg["moves"] += int(cnts.get("moves", 0))
                agg["interval_s"] += float(payload.get("window",{}).get("seconds", 0.0) or 0)
                for ev in payload.get("events", []):
                    ev = dict(ev)
                    if "t" not in ev: ev["t"] = r["timestamp"]
                    if "e" not in ev: ev["e"] = "key"
                    flat_events.append(ev)

    out_rows = []
    for b, v in buckets.items():
        out_rows.append((b.isoformat(sep=" "), v["keys"], v["clicks"], v["scrolls"], v["moves"], round(v["interval_s"],2)))

    out_rows.sort(key=lambda x: x[0])

    print("\n⌨️ Input Activity Summary\n")
    if since or until:
        print(f"Range: {since or 'beginning'} → {until or 'now'}  |  Bucket: {args.bucket}")
    else:
        print(f"Bucket: {args.bucket}")
    print()

    if args.top and args.top > 0:
        ranked = sorted(out_rows, key=lambda r: (r[1]+r[2]+r[3]+r[4]), reverse=True)[:args.top]
        print_table(ranked, ["Bucket Start", "Keys", "Clicks", "Scrolls", "Moves", "Interval(s)"])
    else:
        print_table(out_rows, ["Bucket Start", "Keys", "Clicks", "Scrolls", "Moves", "Interval(s)"])

    if args.export_csv:
        export_csv_summary(out_rows, args.export_csv)
    if args.export_html:
        export_html_summary(out_rows, args.export_html, title=f"Input Activity Summary ({args.bucket})")
    if args.export_events_csv and flat_events:
        export_csv_events(flat_events, args.export_events_csv)

if __name__ == "__main__":
    main()
