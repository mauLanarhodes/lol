import os, time, threading, queue, requests, psutil
from datetime import datetime

# ==================== Config ====================
API_URL = "http://127.0.0.1:5000/log-batch"   # batch endpoint in your app.py
API_TOKEN = "supersecrettoken123"             # must match server
FLUSH_INTERVAL = 1.0                          # seconds: batch sender
POLL_INTERVAL  = 0.15                         # seconds: foreground poll
MIN_SESSION_SECONDS = 0.3                     # ignore ultra-short focus blips
IDLE_IGNORE_SECONDS = 0                       # set >0 to ignore changes while idle
TITLE_CHANGE_GRACE = 0.30                     # require title change to be stable for this long
MERGE_BOUNCE_WINDOW = 0.50                    # suppress A->B->A bounces within this window
SECURITY_POLL_SECONDS = 5.0                   # how often to poll Security log for logon/logoff
ENRICH_USERNAME = True
ENRICH_SESSION  = True
ENRICH_MONITORS = True
# =================================================

# Windows imports
import win32gui, win32process, ctypes, win32api, win32con
import wmi
import win32evtlog, win32evtlogutil, win32evtlogdefs

EVENT_Q   = queue.Queue()   # actions -> batched POST
CONTROL_Q = queue.Queue()   # session control (LOCK/UNLOCK)

def enqueue(action: str, detail: str):
    EVENT_Q.put(f"{action}: {detail}")

def flush_loop():
    buf = []
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    last_send = time.time()
    while True:
        timeout = max(0.1, FLUSH_INTERVAL - (time.time() - last_send))
        try:
            item = EVENT_Q.get(timeout=timeout)
            buf.append(item)
            # drain quickly
            while True:
                buf.append(EVENT_Q.get_nowait())
        except queue.Empty:
            pass

        if buf and (time.time() - last_send >= FLUSH_INTERVAL):
            try:
                requests.post(API_URL, json={"actions": buf}, headers=headers, timeout=5)
                print(f"[BATCH] sent {len(buf)} events")
            except Exception as e:
                print(f"[BATCH ERROR] {e}")
            buf = []
            last_send = time.time()

# ---------- Helpers: idle, fg window, enrich ----------
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

def get_idle_seconds():
    lii = LASTINPUTINFO(); lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        millis_idle = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return millis_idle / 1000.0
    return 0.0

def get_foreground_info():
    """Return (pid, exe, title, path, username, session_type, monitors)"""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        title = win32gui.GetWindowText(hwnd) or ""
        exe = ""; path = ""; username=""; session_type=""
        try:
            p = psutil.Process(pid)
            path = p.exe() or ""
            exe  = os.path.basename(path) if path else (p.name() or "")
            if ENRICH_USERNAME:
                username = (p.username() or "")
        except Exception:
            pass

        if ENRICH_SESSION:
            try:
                # heuristic: console session vs remote, etc.
                # Using WTS APIs would be more exact; keep light:
                session_type = "console"
                # Detect common remote exe hints
                if exe.lower() in ("mstsc.exe", "rdpclip.exe"):
                    session_type = "remote"
            except Exception:
                pass

        monitors = ""
        if ENRICH_MONITORS:
            try:
                # count + primary resolution
                hmonitors = []
                def _enum(hMon, hdcMon, lprcMon, dwData):
                    r = win32api.GetMonitorInfo(hMon)["Monitor"]
                    hmonitors.append((r[2]-r[0], r[3]-r[1]))
                    return True
                win32api.EnumDisplayMonitors(None, None, _enum, None)
                count = len(hmonitors)
                primary = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
                monitors = f"monitors={count} | primary={primary[0]}x{primary[1]}"
            except Exception:
                pass

        return (pid, exe, title, path, username, session_type, monitors)
    except Exception:
        return None

def fmt_detail(pid, exe, title, path, username="", session_type="", monitors=""):
    parts = [f"pid={pid}", f'exe="{exe}"', f'title="{title}"', f'path="{path}"']
    if username:     parts.append(f'user="{username}"')
    if session_type: parts.append(f"session={session_type}")
    if monitors:     parts.append(monitors)
    return " | ".join(parts)

# ---------- WMI watchers: Session lock/unlock + USB ----------
def session_wmi_watcher():
    """Win32_SessionChangeEvent: 11=Lock, 12=Unlock"""
    try:
        c = wmi.WMI(namespace="root\\CIMV2")
        watcher = c.watch_for(raw_wql="SELECT * FROM Win32_SessionChangeEvent")
        print("🔐 Session watcher started.")
        while True:
            ev = watcher()
            et = int(ev.EventType)
            if et == 11: CONTROL_Q.put(("LOCK", getattr(ev, "SessionId", None)))
            elif et == 12: CONTROL_Q.put(("UNLOCK", getattr(ev, "SessionId", None)))
    except Exception as e:
        print(f"[SESSION WATCHER ERROR] {e}")

def usb_wmi_watcher():
    """
    Watch USB volume arrival/removal.
    Win32_VolumeChangeEvent: EventType 1=Arrive, 2=ConfigChanged, 3=Remove, 4=Docking
    """
    try:
        c = wmi.WMI(namespace="root\\CIMV2")
        watcher = c.watch_for(raw_wql="SELECT * FROM Win32_VolumeChangeEvent")
        print("🔌 USB watcher started.")
        type_map = {1: "USB volume arrived", 3: "USB volume removed", 2: "USB volume changed", 4: "USB docking"}
        while True:
            ev = watcher()
            et = int(getattr(ev, "EventType", 0))
            label = type_map.get(et, f"USB event {et}")
            drive = getattr(ev, "DriveName", None) or ""
            enqueue(label, f"drive={drive}")
    except Exception as e:
        print(f"[USB WATCHER ERROR] {e}")

# ---------- Security Log poller: Logon/Logoff ----------
# 4624 = Logon, 4634 = Logoff (Windows Security)
def security_log_poller():
    server = None   # local machine
    logtype = "Security"
    flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
    handle = win32evtlog.OpenEventLog(server, logtype)

    # Start from the end, remember record number to avoid re-logging
    # We’ll re-open each iteration (simpler), or hold handle and track last seen.
    last_record = None

    print("🛡️ Security log poller started.")
    while True:
        try:
            # simple approach: read tail chunk, newest first
            events = win32evtlog.ReadEventLog(handle, flags, 0)
            if events:
                for ev in events:
                    if last_record is None or ev.RecordNumber > last_record:
                        # Filter: only 4624/4634
                        if ev.EventID in (4624, 4634):
                            ts = ev.TimeGenerated.Format()  # local time string
                            # Extract a tiny bit of detail from the string; better would be XML API, but this is fine for audit
                            src = ev.SourceName
                            cat = "Logon" if ev.EventID == 4624 else "Logoff"
                            user = ""
                            try:
                                if ev.StringInserts:
                                    # often includes user/domain in inserts; not guaranteed
                                    for s in ev.StringInserts:
                                        if "\\" in s:
                                            user = s
                                            break
                            except Exception:
                                pass
                            enqueue(f"Session {cat.lower()}", f"time={ts} | source={src} | user=\"{user}\" | event={ev.EventID}")
                        last_record = ev.RecordNumber
            time.sleep(SECURITY_POLL_SECONDS)
        except Exception as e:
            # On access issues or empty reads, sleep and retry
            # (You may need to run as admin to read Security log)
            time.sleep(SECURITY_POLL_SECONDS)

# ---------- Main (with micro-burst smoothing) ----------
def main():
    print("🖥️ App Usage Tracker ++ (durations, lock/unlock, USB, logon/logoff, smoothing). Ctrl+C to stop.")
    threading.Thread(target=flush_loop, daemon=True).start()
    threading.Thread(target=session_wmi_watcher, daemon=True).start()
    threading.Thread(target=usb_wmi_watcher, daemon=True).start()
    threading.Thread(target=security_log_poller, daemon=True).start()

    # Current session
    cur_pid = cur_exe = cur_title = cur_path = None
    cur_user = cur_session = cur_mon = ""
    session_start_ts = None

    # For smoothing:
    last_fg_sig = None                    # (pid, exe, title)
    last_switch_time = 0.0
    pending_title = None                  # (title, seen_since)
    last_committed_title = None

    def start_session(pid, exe, title, path, user, sess, mon, source="focus"):
        nonlocal cur_pid, cur_exe, cur_title, cur_path, cur_user, cur_session, cur_mon, session_start_ts, last_committed_title
        cur_pid, cur_exe, cur_title, cur_path = pid, exe, title, path
        cur_user, cur_session, cur_mon = user, sess, mon
        session_start_ts = time.time()
        last_committed_title = title
        tag = "App focus start" if source == "focus" else "App focus start (after unlock)"
        enqueue(tag, fmt_detail(pid, exe, title, path, user, sess, mon))

    def end_session(reason="focus_switch"):
        nonlocal cur_pid, cur_exe, cur_title, cur_path, cur_user, cur_session, cur_mon, session_start_ts
        if cur_pid is None or session_start_ts is None:
            return
        dur = time.time() - session_start_ts
        if dur >= MIN_SESSION_SECONDS:
            enqueue("App focus end", f'{fmt_detail(cur_pid, cur_exe, cur_title, cur_path, cur_user, cur_session, cur_mon)} | duration={dur:.2f}s | reason={reason}')
        cur_pid = cur_exe = cur_title = cur_path = None
        cur_user = cur_session = cur_mon = ""
        session_start_ts = None

    try:
        while True:
            # Handle session lock/unlock signals
            try:
                while True:
                    cmd, sid = CONTROL_Q.get_nowait()
                    if cmd == "LOCK":
                        enqueue("Session locked", f"session_id={sid}")
                        end_session("session_lock")
                        # keep last_fg_sig as-is; we will overwrite on unlock
                    elif cmd == "UNLOCK":
                        enqueue("Session unlocked", f"session_id={sid}")
                        time.sleep(0.4)  # let shell settle
                        info = get_foreground_info()
                        if info:
                            pid, exe, title, path, user, sess, mon = info
                            start_session(pid, exe, title, path, user, sess, mon, source="unlock")
                            last_fg_sig = (pid, exe, title)
                            pending_title = None
            except queue.Empty:
                pass

            # Normal foreground tracking
            info = get_foreground_info()
            now = time.time()
            if info:
                pid, exe, title, path, user, sess, mon = info
                fg_sig = (pid, exe, title)

                # Optional idle suppression
                if IDLE_IGNORE_SECONDS and get_idle_seconds() >= IDLE_IGNORE_SECONDS:
                    time.sleep(POLL_INTERVAL)
                    continue

                if last_fg_sig is None:
                    # First observation
                    if session_start_ts is None:
                        start_session(pid, exe, title, path, user, sess, mon, source="focus")
                    last_fg_sig = fg_sig
                    pending_title = None

                else:
                    same_proc = (pid == last_fg_sig[0] and exe == last_fg_sig[1])
                    title_changed = (title != last_fg_sig[2])

                    # Title change within same process → smooth with grace window
                    if same_proc and title_changed:
                        if pending_title is None:
                            pending_title = (title, now)  # start grace
                        else:
                            t, since = pending_title
                            if title == t and (now - since) >= TITLE_CHANGE_GRACE:
                                # commit title change as a tiny end/start pair for accurate duration accounting
                                end_session("title_change")
                                start_session(pid, exe, title, path, user, sess, mon, source="focus")
                                last_fg_sig = (pid, exe, title)
                                pending_title = None
                        # skip logging until stabilized
                        time.sleep(POLL_INTERVAL)
                        continue
                    else:
                        pending_title = None

                    # Full focus switch (proc change or proc+title change)
                    if fg_sig != last_fg_sig:
                        # suppress bounce A->B->A within MERGE_BOUNCE_WINDOW
                        if (now - last_switch_time) < MERGE_BOUNCE_WINDOW and cur_exe == exe and cur_title == title:
                            # likely transient flip; ignore
                            pass
                        else:
                            end_session("focus_switch")
                            start_session(pid, exe, title, path, user, sess, mon, source="focus")
                            last_fg_sig = fg_sig
                            last_switch_time = now

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n👋 Stopping…")
    finally:
        end_session("shutdown")
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=flush_loop, daemon=True).start()
    threading.Thread(target=session_wmi_watcher, daemon=True).start()
    threading.Thread(target=usb_wmi_watcher, daemon=True).start()
    threading.Thread(target=security_log_poller, daemon=True).start()
    main()
