import os, time, requests
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

API_URL   = "http://127.0.0.1:5000/log"
API_TOKEN = "supersecrettoken123"

# --- drives to watch (add more letters if you have them) ---
WATCHED_DIRS = [r"C:/"]
# If you install psutil, you can auto-discover fixed drives:
import psutil
WATCHED_DIRS = [p.device for p in psutil.disk_partitions() if p.fstype and p.opts and 'cdrom' not in p.opts]

# --- paths to exclude: ONLY your DB and its journal file ---
DB_PATH = r"C:\AuditData\logs.db"          # <- set the same path in db.py
EXCLUDED_PATHS = {os.path.abspath(DB_PATH), os.path.abspath(DB_PATH + "-journal")}

# tiny de-dupe to reduce spam of identical rapid events
LAST = {}
DEBOUNCE = 0.3  # seconds

def normalize(path: str) -> str:
    return os.path.abspath(path).replace("\\", "/").lower()

EXCLUDED_PATHS = {
    normalize(DB_PATH),
    normalize(DB_PATH + "-journal")
}

def should_skip(path: str) -> bool:
    return normalize(path) in EXCLUDED_PATHS

def dedupe(key: str) -> bool:
    now = time.time()
    last = LAST.get(key, 0)
    if now - last < DEBOUNCE:
        return False
    LAST[key] = now
    return True

class Handler(FileSystemEventHandler):
    def on_any_event(self, event):
        if event.is_directory:
            return
        path = event.src_path
        if should_skip(path):
            return

        if event.event_type == "created":
            action = "File created"
        elif event.event_type == "modified":
            action = "File modified"
        elif event.event_type == "deleted":
            action = "File deleted"
        else:
            return

        key = f"{action}:{path}"
        if not dedupe(key):
            return

        try:
            r = requests.post(
                API_URL,
                json={"action": f"{action}: {path}"},
                headers={"Authorization": f"Bearer {API_TOKEN}"},
                timeout=3
            )
            print(f"[LOGGED] {action}: {path} - Status: {r.status_code}")
        except Exception as e:
            print(f"[ERROR] {e}")

if __name__ == "__main__":
    # Make sure DB lives outside watched dirs
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    observer = Observer()
    handler = Handler()

    for root in WATCHED_DIRS:
        if os.path.exists(root):
            observer.schedule(handler, path=root, recursive=True)
            print(f"🔍 Watching: {root}")
        else:
            print(f"⚠️ Skipped (not found): {root}")

    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
