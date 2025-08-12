# monitor/app_usage_tracker.py
import os, time, threading, queue, requests, psutil
from datetime import datetime

# ====== Config ======
API_URL = "http://127.0.0.1:5000/log-batch"
API_TOKEN = "supersecrettoken123"
FLUSH_INTERVAL = 1.0
POLL_INTERVAL  = 0.15
MIN_SESSION_SECONDS = 0.3
IDLE_IGNORE_SECONDS = 0         # set >0 to ignore changes while idle
TITLE_CHANGE_GRACE = 0.30       # debounce title flickers
MERGE_BOUNCE_WINDOW = 0.50
SECURITY_POLL_SECONDS = 5.0
ENRICH_USERNAME = True
ENRICH_SESSION  = True
ENRICH_MONITORS = True
# ====================

# Windows imports
import win32gui, win32process, ctypes, win32api
import wmi, win32evtlog
import pythoncom  # COM init for WMI threads

EVENT_Q   = queue.Queue()
CONTROL_Q = queue.Queue()

# ---------- Utils ----------
def enqueue(action: str, detail: str):
    EVENT_Q.put(f"{action}: {detail}")

def flush_loop():
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    buf, last_send = [], time.time()
    while True:
        timeout = max(0.1, FLUSH_INTERVAL - (time.time() - last_send))
        try:
            item = EVENT_Q.get(timeout=timeout)
            buf.append(item)
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
            buf, last_send = [], time.time()

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

def get_idle_seconds():
    lii = LASTINPUTINFO(); lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0
    return 0.0

def get_foreground_info():
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd: return None
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        title = win32gui.GetWindowText(hwnd) or ""
        exe = path = username = session_type = ""
        try:
            p = psutil.Process(pid)
            path = p.exe() or ""
            exe  = os.path.basename(path) if path else (p.name() or "")
            if ENRICH_USERNAME:
                username = (p.username() or "")
        except Exception:
            pass
        if ENRICH_SESSION:
            session_type = "console"
            if exe.lower() in ("mstsc.exe", "rdpclip.exe"):
                session_type = "remote"
        monitors = ""
        if ENRICH_MONITORS:
            try:
                infos = []
                def _enum(hMon, hdcMon, rect, data):
                    r = win32api.GetMonitorInfo(hMon)["Monitor"]
                    infos.append((r[2]-r[0], r[3]-r[1])); return True
                win32api.EnumDisplayMonitors(None, None, _enum, None)
                count = len(infos)
                primary = (win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1))
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

# ---------- COM decorator for WMI threads ----------
def com_thread(fn):
    def wrapper(*args, **kwargs):
        pythoncom.CoInitialize()
        try:
            return fn(*args, **kwargs)
        finally:
            pythoncom.CoUninitialize()
    return wrapper

# ---------- WMI watchers ----------
@com_thread
def session_wmi_watcher():
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

@com_thread
def usb_wmi_watcher():
    try:
        c = wmi.WMI(namespace="root\\CIMV2")
        watcher = c.watch_for(raw_wql="SELECT * FROM Win32_VolumeChangeEvent")
        print("🔌 USB watcher started.")
        typemap = {1:"USB volume arrived", 2:"USB volume changed", 3:"USB volume removed", 4:"USB docking"}
        while True:
            ev = watcher()
            et = int(getattr(ev, "EventType", 0))
            drive = getattr(ev, "DriveName", "") or ""
            enqueue(typemap.get(et, f"USB event {et}"), f"drive={drive}")
    except Exception as e:
        print(f"[USB WATCHER ERROR] {e}")

# ---------- Security log poller (privilege-safe) ----------
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

@com_thread
def security_log_poller():
    if not is_admin():
        print("🛡️ Security poller: no admin/Event Log Readers rights; skipping.")
        return
    try:
        handle = win32evtlog.OpenEventLog(None, "Security")
    except Exception as e:
        print(f"🛡️ Security poller init failed ({e}); skipping.")
        return

    print("🛡️ Security log poller started.")
    last_record = None
    flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
    while True:
        try:
            events = win32evtlog.ReadEventLog(handle, flags, 0)
            if events:
                for ev in events:
                    if last_record is None or ev.RecordNumber > last_record:
                        if ev.EventID in (4624, 4634):  # logon/logoff
                            ts = ev.TimeGenerated.Format()
                            src = ev.SourceName
                            cat = "logon" if ev.EventID == 4624 else "logoff"
                            user = ""
                            try:
                                if ev.StringInserts:
                                    for s in ev.StringInserts:
                                        if "\\" in s:
                                            user = s; break
                            except Exception:
                                pass
                            enqueue(f"Session {cat}", f'time={ts} | source={src} | user="{user}" | event={ev.EventID}')
                        last_record = ev.RecordNumber
            time.sleep(SECURITY_POLL_SECONDS)
        except Exception:
            time.sleep(SECURITY_POLL_SECONDS)

# ---------- Main loop ----------
def main():
    print("🖥️ App Usage Tracker ++ (durations, lock/unlock, USB, logon/logoff, smoothing). Ctrl+C to stop.")

    # start background workers ONCE
    threading.Thread(target=flush_loop, daemon=True).start()
    threading.Thread(target=session_wmi_watcher, daemon=True).start()
    threading.Thread(target=usb_wmi_watcher, daemon=True).start()
    threading.Thread(target=security_log_poller, daemon=True).start()

    # current session state
    cur_pid = cur_exe = cur_title = cur_path = None
    cur_user = cur_session = cur_mon = ""
    session_start_ts = None

    last_fg_sig = None
    last_switch_time = 0.0
    pending_title = None  # (title, since_ts)

    def start_session(pid, exe, title, path, user, sess, mon, source="focus"):
        nonlocal cur_pid, cur_exe, cur_title, cur_path, cur_user, cur_session, cur_mon, session_start_ts
        cur_pid, cur_exe, cur_title, cur_path = pid, exe, title, path
        cur_user, cur_session, cur_mon = user, sess, mon
        session_start_ts = time.time()
        tag = "App focus start" if source == "focus" else "App focus start (after unlock)"
        enqueue(tag, fmt_detail(pid, exe, title, path, user, sess, mon))

    def end_session(reason="focus_switch"):
        nonlocal cur_pid, cur_exe, cur_title, cur_path, cur_user, cur_session, cur_mon, session_start_ts
        if cur_pid is None or session_start_ts is None:
            return
        dur = time.time() - session_start_ts
        if dur >= MIN_SESSION_SECONDS:
            enqueue("App focus end",
                    f'{fmt_detail(cur_pid, cur_exe, cur_title, cur_path, cur_user, cur_session, cur_mon)} | duration={dur:.2f}s | reason={reason}')
        cur_pid = cur_exe = cur_title = cur_path = None
        cur_user = cur_session = cur_mon = ""
        session_start_ts = None

    try:
        while True:
            # handle lock/unlock signals
            try:
                while True:
                    cmd, sid = CONTROL_Q.get_nowait()
                    if cmd == "LOCK":
                        enqueue("Session locked", f"session_id={sid}")
                        end_session("session_lock")
                    elif cmd == "UNLOCK":
                        enqueue("Session unlocked", f"session_id={sid}")
                        time.sleep(0.4)
                        info = get_foreground_info()
                        if info:
                            pid, exe, title, path, user, sess, mon = info
                            start_session(pid, exe, title, path, user, sess, mon, source="unlock")
                            last_fg_sig = (pid, exe, title)
                            pending_title = None
            except queue.Empty:
                pass

            # normal foreground tracking
            info = get_foreground_info()
            now = time.time()
            if info:
                pid, exe, title, path, user, sess, mon = info
                fg_sig = (pid, exe, title)

                if IDLE_IGNORE_SECONDS and get_idle_seconds() >= IDLE_IGNORE_SECONDS:
                    time.sleep(POLL_INTERVAL); continue

                if last_fg_sig is None:
                    if session_start_ts is None:
                        start_session(pid, exe, title, path, user, sess, mon)
                    last_fg_sig = fg_sig
                    pending_title = None
                else:
                    same_proc = (pid == last_fg_sig[0] and exe == last_fg_sig[1])
                    title_changed = (title != last_fg_sig[2])

                    # debounce title flicker within same process
                    if same_proc and title_changed:
                        if pending_title is None:
                            pending_title = (title, now)
                        else:
                            t, since = pending_title
                            if title == t and (now - since) >= TITLE_CHANGE_GRACE:
                                end_session("title_change")
                                start_session(pid, exe, title, path, user, sess, mon)
                                last_fg_sig = (pid, exe, title)
                                pending_title = None
                        time.sleep(POLL_INTERVAL); continue
                    else:
                        pending_title = None

                    # full focus switch
                    if fg_sig != last_fg_sig:
                        if (now - last_switch_time) < MERGE_BOUNCE_WINDOW and exe == cur_exe and title == cur_title:
                            pass  # suppress bounce
                        else:
                            end_session("focus_switch")
                            start_session(pid, exe, title, path, user, sess, mon)
                            last_fg_sig = fg_sig
                            last_switch_time = now

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n👋 Stopping…")
    finally:
        end_session("shutdown")
        time.sleep(0.5)

if __name__ == "__main__":
    main()
