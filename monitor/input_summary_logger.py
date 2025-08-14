# monitor/input_summary_logger.py
# Timestamped input activity (keys/clicks/scrolls/moves) with privacy-first defaults.
# Sends both a rollup summary and a compact per-event list (with timestamps) to /log-batch.

import time, threading, atexit, json, requests
from datetime import datetime
from pynput import keyboard, mouse

# ====== Config ======
API_URL   = "http://127.0.0.1:5000/log-batch"   # Flask batch endpoint
API_TOKEN = "supersecrettoken123"               # must match server token
FLUSH_INTERVAL_SEC = 10.0                       # summary + events every N seconds
COUNT_MOUSE_MOVES  = True                      # set True to include move counts/events (noisy)
INCLUDE_KEY_NAMES  = True                      # False = DO NOT send typed characters (privacy)
MAX_EVENTS_PER_FLUSH = 400                      # safety cap for payload size
# ====================

_lock = threading.Lock()
_running = True

# rollup counters
counts = {"keys": 0, "clicks": 0, "scrolls": 0, "moves": 0}
interval_started = time.time()


events = []

def now_iso():
    return datetime.now().isoformat(timespec="milliseconds")

def add_event(ev):
    with _lock:
        if len(events) < MAX_EVENTS_PER_FLUSH:
            events.append(ev)

def _reset_window():
    global interval_started
    with _lock:
        snap_counts = dict(counts)
        snap_events = list(events)
        for k in counts: counts[k] = 0
        events.clear()
        started = interval_started
        interval_started = time.time()
    return snap_counts, snap_events, started, time.time()

def _post_summary(snap_counts, snap_events, started_ts, ended_ts):
    # if absolutely no activity, skip sending
    total = snap_counts["keys"] + snap_counts["clicks"] + snap_counts["scrolls"] + snap_counts["moves"]
    if total <= 0 and not snap_events:
        return

    interval = max(0.01, ended_ts - started_ts)

    # 1) summary action (human-readable)
    summary_detail = (
        f'keys={snap_counts["keys"]} | clicks={snap_counts["clicks"]} | '
        f'scrolls={snap_counts["scrolls"]} | moves={snap_counts["moves"]} | '
        f'interval={interval:.2f}s'
    )
    actions = [f"Input summary: {summary_detail}"]


    payload = {
        "window": {
            "start": datetime.fromtimestamp(started_ts).isoformat(timespec="milliseconds"),
            "end":   datetime.fromtimestamp(ended_ts).isoformat(timespec="milliseconds"),
            "seconds": round(interval, 3)
        },
        "counts": snap_counts,
        "events": snap_events  # each has its own ISO timestamp
    }
    actions.append("Input events: " + json.dumps(payload, separators=(",", ":")))

    try:
        r = requests.post(
            API_URL,
            json={"actions": actions},
            headers={"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"},
            timeout=8
        )
        print(f"[INPUT] sent {len(actions)} action(s) -> HTTP {r.status_code} | {summary_detail}")
    except Exception as e:
        print(f"[INPUT ERROR] {e}")

def _flusher():
    while _running:
        time.sleep(FLUSH_INTERVAL_SEC)
        snap_counts, snap_events, st, en = _reset_window()
        _post_summary(snap_counts, snap_events, st, en)

# ---------- Listeners ----------
def _on_key_press(key):
    # increment counter
    with _lock:
        counts["keys"] += 1

    # add per-event entry (timestamped)
    name = "char"
    if INCLUDE_KEY_NAMES:
        try:
            name = key.char if key.char else str(key)
        except Exception:
            name = str(key)
    ev = {"t": now_iso(), "e": "key", "k": name}
    add_event(ev)

def _on_click(x, y, button, pressed):
    if not pressed:
        return
    with _lock:
        counts["clicks"] += 1
    add_event({"t": now_iso(), "e": "click", "b": str(button).split(".")[-1]})


SCROLL_ACCUM = {"units": 0}
SCROLL_UNIT_THRESHOLD = 1   # treat every 1 unit of |dy| as one 'scroll'
def _on_scroll(x, y, dx, dy):
    # accumulate smooth scrolling; count each unit of absolute dy as a "scroll"
    with _lock:
        SCROLL_ACCUM["units"] += abs(dy)
        # convert accumulated units into integer ticks
        ticks = int(SCROLL_ACCUM["units"] // SCROLL_UNIT_THRESHOLD)
        if ticks > 0:
            counts["scrolls"] += ticks
            SCROLL_ACCUM["units"] -= ticks * SCROLL_UNIT_THRESHOLD
    add_event({"t": now_iso(), "e": "scroll", "dx": int(dx), "dy": int(dy)})


MOVE_MIN_PIXELS = 1  # count a "move" when cursor shifts by >= this many pixels

_last_pos = [None, None]

def _on_move(x, y):
    if not COUNT_MOUSE_MOVES:
        return
    global _last_pos
    px, py = _last_pos
    if px is None:
        _last_pos = [x, y]
        return
    if abs(x - px) >= MOVE_MIN_PIXELS or abs(y - py) >= MOVE_MIN_PIXELS:
        with _lock:
            counts["moves"] += 1
        add_event({"t": now_iso(), "e": "move"})
        _last_pos = [x, y]


def _shutdown():
    global _running
    _running = False
    snap_counts, snap_events, st, en = _reset_window()
    _post_summary(snap_counts, snap_events, st, en)
    print("👋 Input Summary Logger stopped.")

def main():
    print("⌨️ Input Summary Logger (timestamped; privacy-first). Ctrl+C to stop.")
    kb_listener = keyboard.Listener(on_press=_on_key_press)
    ms_listener = mouse.Listener(on_click=_on_click, on_scroll=_on_scroll, on_move=_on_move)
    kb_listener.start(); ms_listener.start()
    atexit.register(_shutdown)
    threading.Thread(target=_flusher, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown()

if __name__ == "__main__":
    main()
