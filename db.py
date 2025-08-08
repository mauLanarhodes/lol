import sqlite3
import hashlib
from datetime import datetime

# --- SQLite DB file ---
import os

DB_FILE = r"C:/AuditData/logs.db"


# --- Connect to DB ---
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# --- Generate SHA256 hash ---
def calculate_hash(prev_hash, timestamp, action):
    data = f'{prev_hash}{timestamp}{action}'.encode('utf-8')
    return hashlib.sha256(data).hexdigest()

# --- Initialize DB table ---
def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL
            );
        ''')

# --- Get last recorded hash ---
def get_last_hash():
    with get_db() as conn:
        row = conn.execute("SELECT hash FROM audit_logs ORDER BY id DESC LIMIT 1").fetchone()
        return row['hash'] if row else '0'

# --- Insert new action into audit log ---
def log_action(action):
    timestamp = datetime.utcnow().isoformat()
    prev_hash = get_last_hash()
    hash_val = calculate_hash(prev_hash, timestamp, action)

    with get_db() as conn:
        conn.execute(
            "INSERT INTO audit_logs (timestamp, action, prev_hash, hash) VALUES (?, ?, ?, ?)",
            (timestamp, action, prev_hash, hash_val)
        )
    return {
        "timestamp": timestamp,
        "action": action,
        "hash": hash_val
    }
