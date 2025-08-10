import os, time, json, requests, sys
from watchdog.observers import Observer
from watchdog.events import (
    FileSystemEventHandler,
    FileCreatedEvent, FileModifiedEvent, FileDeletedEvent, FileMovedEvent,
    DirCreatedEvent, DirDeletedEvent, DirMovedEvent
)

# -------------------- Config --------------------
API_URL   = "http://127.0.0.1:5000/log"
API_TOKEN = "supersecrettoken123"       # must match Flask's token
DB_PATH   = r"C:\AuditData\logs.db"     # must match db.py
CONFIG    = os.path.join(os.path.expanduser("~"), ".secure_audit_watcher.json")
DEBOUNCE_SECS = 0.3                     # de-dupe identical events within this window
# ------------------------------------------------

# Optional: ignore some noisy system artifacts
IGNORED_FILENAMES = {"desktop.ini", "thumbs.db"}
IGNORED_SUFFIXES  = (".tmp", ".crdownload")

# ---- Path normalization / ignore our DB files to prevent loop ----
def norm(p: str) -> str:
    return os.path.abspath(p).replace("\\", "/").lower()

EXCLUDED = {
    norm(DB_PATH),
    norm(DB_PATH + "-journal"),
    norm(DB_PATH + "-wal"),
    norm(DB_PATH + "-shm"),
}

# tiny de-dupe so we don’t spam identical rapid events
_LAST: dict[str, float] = {}
def dedupe(key: str) -> bool:
    now = time.time()
    t = _LAST.get(key, 0)
    if now - t < DEBOUNCE_SECS:
        return False
    _LAST[key] = now
    return True

def post_action(action: str, detail: str):
    """
    action: label (e.g., 'File created', 'Folder renamed (from: A to: B)')
    detail: path or 'old -> new' string
    """
    key = f"{action}:{norm(detail)}"
    if not dedupe(key):
        return
    try:
        r = requests.post(
            API_URL,
            json={"action": f"{action}: {detail}"},
            headers={"Authorization": f"Bearer {API_TOKEN}"},
            timeout=3
        )
        print(f"[LOGGED] {action}: {detail} - Status: {r.status_code}")
    except Exception as e:
        print(f"[ERROR] {e}")

class Handler(FileSystemEventHandler):
    def on_any_event(self, event):
        path = event.src_path
        p = norm(path)

        # Skip our DB + side files (feedback loop guard)
        if p in EXCLUDED:
            return

        # -------- Folder events --------
        if isinstance(event, DirCreatedEvent):
            base = os.path.basename(path)
            # Scenario 1 + 2: always log creation immediately (default or custom)
            post_action(f"Folder created (name: {base})", path)
            return

        if isinstance(event, DirDeletedEvent):
            base = os.path.basename(path)
            post_action(f"Folder deleted (name: {base})", path)
            return

        if isinstance(event, DirMovedEvent):
            # Scenario 3: any folder rename
            old_name = os.path.basename(event.src_path)
            new_name = os.path.basename(event.dest_path)
            post_action(f"Folder renamed (from: {old_name} to: {new_name})",
                        f"{event.src_path} -> {event.dest_path}")
            return

        # -------- File events --------
        if isinstance(event, FileCreatedEvent):
            name = os.path.basename(path).lower()
            if name in IGNORED_FILENAMES or name.endswith(IGNORED_SUFFIXES):
                return
            post_action("File created", path)
            return

        if isinstance(event, FileModifiedEvent):
            name = os.path.basename(path).lower()
            if name in IGNORED_FILENAMES or name.endswith(IGNORED_SUFFIXES):
                return
            post_action("File modified", path)
            return

        if isinstance(event, FileDeletedEvent):
            name = os.path.basename(path).lower()
            if name in IGNORED_FILENAMES or name.endswith(IGNORED_SUFFIXES):
                return
            post_action("File deleted", path)
            return

        if isinstance(event, FileMovedEvent):
            # Scenario 3: any file rename
            old_name = os.path.basename(event.src_path)
            new_name = os.path.basename(event.dest_path)
            post_action(f"File renamed (from: {old_name} to: {new_name})",
                        f"{event.src_path} -> {event.dest_path}")
            return

        # Ignore everything else (like parent-dir modified noise)
        return

# ---- GUI folder picker (no typing) ----
def pick_directory():
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        print("[WARN] Tkinter not available; falling back to last saved or home directory.")
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
        except:
            pass
    return None

def save_last_dir(d):
    try:
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump({"last_dir": d}, f)
    except:
        pass

if __name__ == "__main__":
    selected = pick_directory()
    if not selected:
        selected = load_last_dir() or os.path.expanduser("~")
        if not os.path.isdir(selected):
            print("No folder selected and no valid fallback. Exiting.")
            sys.exit(1)

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
