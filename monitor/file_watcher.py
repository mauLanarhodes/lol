import time
import requests
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --- CONFIG ---
WATCHED_DIR = "C:/Users/shaur/Downloads/lundfrf"  # change to your path
API_URL = "http://127.0.0.1:5000/log"
API_TOKEN = "supersecrettoken123"  # must match Flask token

class WatcherHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if not event.is_directory:
            self.log_event("File modified", event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self.log_event("File created", event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self.log_event("File deleted", event.src_path)

    def log_event(self, action_type, file_path):
        payload = {
            "action": f"{action_type}: {file_path}"
        }
        headers = {"Authorization": f"Bearer {API_TOKEN}"}
        try:
            r = requests.post(API_URL, json=payload, headers=headers)
            print(f"[LOGGED] {payload['action']} - Status: {r.status_code}")
        except Exception as e:
            print(f"[ERROR] Failed to log action: {e}")

if __name__ == "__main__":
    observer = Observer()
    handler = WatcherHandler()
    observer.schedule(handler, path=WATCHED_DIR, recursive=True)
    observer.start()
    print(f"🔍 Watching folder: {WATCHED_DIR}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
