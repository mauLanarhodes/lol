import argparse, sqlite3, os, re, csv
from datetime import datetime, timedelta
from collections import defaultdict

DB_FILE = r"C:\AuditData\logs.db"  

RE_EXE   = re.compile(r'exe="([^"]+)"')
RE_TITLE = re.compile(r'title="([^"]*)"')
RE_PATH  = re.compile(r'path="([^"]*)"')
RE_DUR   = re.compile(r'duration=([\d.]+)s')

def parse_args():
    p = argparse.ArgumentParser(
        description="App Usage Summary (sessions count & total time by app)"
    )
    p.add_argument("--since", help="ISO time (e.g. 2025-08-10T00:00:00) or 'today'/'yesterday'")
    p.add_argument("--until", help="ISO time (e.g. 2025-08-10T23:59:59)")
    p.add_argument("--by", choices=["exe", "exe+title"], default="exe",
                   help="Group by executable only, or executable + window title")
    p.add_argument("--top", type=int, default=25, help="Show top N (default 25)")
    p.add_argument("--export-csv", metavar="FILE", help="Export detailed rows to CSV")
    return p.parse_args()

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

def connect():
    if not os.path.exists(DB_FILE):
        raise SystemExit(f"DB not found: {DB_FILE}")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_focus_ends(since, until):
    q = "SELECT timestamp, action FROM audit_logs WHERE action LIKE 'App focus end:%'"
    params = []
    if since:
        q += " AND timestamp >= ?"; params.append(since)
    if until:
        q += " AND timestamp <= ?"; params.append(until)
    q += " ORDER BY timestamp ASC"
    with connect() as conn:
        return conn.execute(q, params).fetchall()

def humanize_seconds(s):
    s = int(round(s))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h: return f"{h}h {m}m {sec}s"
    if m: return f"{m}m {sec}s"
    return f"{sec}s"

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

def main():
    args = parse_args()
    since = resolve_relative(args.since)
    until = resolve_relative(args.until)

    rows = fetch_focus_ends(since, until)

    # Aggregate
    agg = defaultdict(lambda: {"sessions": 0, "seconds": 0.0, "first": None, "last": None})
    detailed_rows = []  # for CSV export

    for r in rows:
        ts = r["timestamp"]
        action = r["action"]
        exe   = (RE_EXE.search(action).group(1) if RE_EXE.search(action) else "").lower()
        title = RE_TITLE.search(action).group(1) if RE_TITLE.search(action) else ""
        path  = RE_PATH.search(action).group(1) if RE_PATH.search(action) else ""
        dur_m = RE_DUR.search(action)
        if not dur_m:
            continue
        dur = float(dur_m.group(1))

        key = exe if args.by == "exe" else f"{exe} | {title}"
        a = agg[key]
        a["sessions"] += 1
        a["seconds"]  += dur
        a["first"] = ts if not a["first"] else min(a["first"], ts)
        a["last"]  = ts if not a["last"]  else max(a["last"], ts)

        detailed_rows.append({
            "timestamp": ts,
            "exe": exe,
            "title": title,
            "path": path,
            "duration_s": f"{dur:.2f}"
        })

    # Sort by total time desc
    items = sorted(agg.items(), key=lambda kv: kv[1]["seconds"], reverse=True)

    # Prepare printable rows
    out = []
    for key, v in items[:args.top]:
        exe = key if args.by == "exe" else key.split(" | ", 1)[0]
        title = "" if args.by == "exe" else key.split(" | ", 1)[1]
        out.append((
            exe,
            title,
            v["sessions"],
            humanize_seconds(v["seconds"]),
            v["first"] or "",
            v["last"] or ""
        ))

    headers = ["Executable", "Window Title" if args.by=="exe+title" else "—", "Sessions", "Total Time", "First Seen", "Last Seen"]
    if args.by == "exe":
        headers = ["Executable", "Sessions", "Total Time", "First Seen", "Last Seen"]
        out = [(r[0], r[2], r[3], r[4], r[5]) for r in out]

    print()
    print("📊 App Usage Summary")
    if since or until:
        print(f"   Range: {since or 'beginning'} → {until or 'now'}")
    print()

    if out:
        print_table(out, headers)
    else:
        print("(no data)")

    if args.export_csv:
        with open(args.export_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["timestamp","exe","title","path","duration_s"])
            w.writeheader()
            w.writerows(detailed_rows)
        print(f"\n✅ Exported details to: {args.export_csv}")

if __name__ == "__main__":
    main()
