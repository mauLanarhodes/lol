import os, time, json, requests, sys
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from watchdog.events import (
    FileSystemEventHandler,
    FileCreatedEvent, FileModifiedEvent, FileDeletedEvent
)


# ---- Config ----
API_URL   = "http://127.0.0.1:5000/log"
API_TOKEN = "supersecrettoken123"   # must match Flask
DB_PATH   = r"C:\AuditData\logs.db" # must match db.py
CONFIG    = os.path.join(os.path.expanduser("~"), ".secure_audit_watcher.json")

# ---- Tiny GUI folder picker (no terminal typing) ----
def pick_directory():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except Exception as e:
        print("[WARN] Tkinter not available, falling back to last or default.")
        return None

    last = load_last_dir()
    root = tk.Tk(); root.withdraw()
    initial = last if (last and os.path.isdir(last)) else os.path.expanduser("~")
    d = filedialog.askdirectory(title="Select a folder to monitor", initialdir=initial)
    root.update()
    if d:
        save_last_dir(d)
    return d

def load_last_dir():
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                return json.load(f).get("last_dir")
        except: pass
    return None

def save_last_dir(d):
    try:
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump({"last_dir": d}, f)
    except: pass

# ---- Path normalization / ignore our DB files to prevent loop ----
def norm(p): return os.path.abspath(p).replace("\\", "/").lower()
EXCLUDED = {
    norm(DB_PATH),
    norm(DB_PATH + "-journal"),
    norm(DB_PATH + "-wal"),
    norm(DB_PATH + "-shm"),
}

# tiny de-dupe so we don’t spam identical rapid events
LAST, DEBOUNCE = {}, 0.3
def dedupe(key):
    now = time.time()
    t = LAST.get(key, 0)
    if now - t < DEBOUNCE:
        return False
    LAST[key] = now
    return True

class Handler(FileSystemEventHandler):
    def on_any_event(self, event):
        if not isinstance(event, (FileCreatedEvent, FileModifiedEvent, FileDeletedEvent)):
            return
        path = event.src_path

        name = os.path.basename(path).lower()
        if name in {"desktop.ini", "thumbs.db"} or name.endswith((".tmp", ".crdownload")):
            return
    
        p = norm(path)
        if p in EXCLUDED:
            return

        et = event.event_type
        if isinstance(event, FileCreatedEvent):   action = "File created"
        elif isinstance(event, FileModifiedEvent):action = "File modified"
        elif isinstance(event, FileDeletedEvent): action = "File deleted"
        else: return
        
        key = f"{action}:{p}"
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
    # 1) Let user pick a folder (GUI)
    selected = pick_directory()
    if not selected:
        # fallback to last saved, then user home
        selected = load_last_dir() or os.path.expanduser("~")
        if not os.path.isdir(selected):
            print("No folder selected and no valid fallback. Exiting.")
            sys.exit(1)

    # 2) Start watchdog
    observer = Observer()
    handler = Handler()
    observer.schedule(handler, path=selected, recursive=True)
    observer.start()
    print(f"🔍 Watching folder: {selected}  (recursive)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    print("👋 Stopped monitoring.")