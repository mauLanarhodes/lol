import sqlite3
from collections import defaultdict
from tabulate import tabulate

DB_FILE = 'logs.db'

def fetch_logs():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    logs = conn.execute("SELECT timestamp, action FROM audit_logs ORDER BY timestamp DESC").fetchall()
    conn.close()
    return logs

def generate_summary(logs):
    type_count = defaultdict(int)
    file_count = defaultdict(int)

    for log in logs:
        action = log['action']
        if ":" in action:
            action_type, file_path = action.split(":", 1)
            action_type = action_type.strip()
            file_path = file_path.strip()
            type_count[action_type] += 1
            file_count[file_path] += 1
        else:
            type_count["Other"] += 1

    return type_count, file_count

def print_summary():
    logs = fetch_logs()
    type_count, file_count = generate_summary(logs)

    print("\n🔢 Actions by Type:\n")
    print(tabulate(type_count.items(), headers=["Action Type", "Count"], tablefmt="fancy_grid"))

    print("\n📁 Actions by File:\n")
    top_files = sorted(file_count.items(), key=lambda x: x[1], reverse=True)[:10]
    print(tabulate(top_files, headers=["File Path", "Total Actions"], tablefmt="fancy_grid"))

if __name__ == "__main__":
    print_summary()
