## **📌 Project Overview**

This project is a **cross-component monitoring system** designed to log and analyze key types of activity on a Windows machine:

1. **📋 Application Usage Tracker** – Records when users switch between apps, tracking how long each app is in focus.
2. **🔍 File Tracker** – Monitors changes to files and folders (creation, modification, deletion, rename) in real time.
3. **⌨️ Input Summary Logger** – Counts keystrokes, mouse clicks, scrolls, and movements in aggregate (no raw keystrokes unless explicitly enabled).
4. **📊 Reporting Tools** – Command-line summary scripts that generate structured usage reports (app usage, file events, input activity), exportable to CSV/HTML for analysis.

**All events** are sent to a **Flask-based logging backend** that writes them to a **tamper-evident audit log** stored in an SQLite database.
Every entry is cryptographically chained using SHA-256 hashes, making it possible to verify that no logs have been altered.

---

## **🎯 Project Goals**

This system is designed to address **three key goals**:
## 📊 System Architecture

The diagram below illustrates the data flow between the **Monitors**, **Flask Backend**, and **Summary Reporting Tools**.

<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAABAAAAAQACAIAAADwf7zUAAFmZWNhQlgAAWZlanVtYgAAAB5qdW1kYzJwYQARABCAAACqADibcQNjMnBhAAAANxNqdW1iAAAA...<truncated>...==" alt="Secure Activity Tracking System Architecture" width="800"/>



1. **🔐 Security & Compliance**

   * Provide verifiable audit trails for environments with strict compliance requirements (e.g., SOC 2, ISO 27001).
   * Tamper-proof log design: Each log entry’s hash depends on the previous entry, so any modification breaks the chain.

2. **📈 Insight & Productivity Analysis**

   * Identify application usage patterns, file access trends, and input activity levels.
   * Generate detailed summaries for managers, analysts, or security teams.

3. **🛡 Privacy-First Design**

   * By default, no actual typed content is stored—only aggregated counts and event metadata.
   * Optional modes allow deeper inspection (e.g., exact key presses) **only with explicit configuration**.

---

## **⚙️ How It Works**

### **1. Monitors (Clients)**

Each monitor runs locally and sends logs to the Flask backend over HTTP:

| Monitor                 | What It Tracks                                                                           | Example Logged Event                                   |                |                    |          |                   |
| ----------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------ | -------------- | ------------------ | -------- | ----------------- |
| **Application Tracker** | App switches, window focus changes, lock/unlock events                                   | \`App focus end: exe="chrome.exe"                      | title="GitHub" | duration=120.34s\` |          |                   |
| **File Tracker**        | File/folder create, modify, delete, rename                                               | `File created: C:/Users/Example/Documents/report.docx` |                |                    |          |                   |
| **Input Logger**        | Aggregate counts for keys, clicks, scrolls, moves (optionally with timestamps per event) | \`Input summary: keys=42                               | clicks=8       | scrolls=3          | moves=20 | interval=10.00s\` |

---

### **2. Flask Backend**

* Routes:

  * `/log` – For single-event logs.
  * `/log-batch` – For batch submission of multiple events.
  * `/verify` – Validates the cryptographic chain to detect tampering.
* Stores logs in `C:\AuditData\logs.db`.
* Uses a **security token** (`Authorization: Bearer ...`) for authenticated submissions.

---

### **3. Tamper-Proof Audit Logging**

* Each row in the database contains:

  * **Timestamp**
  * **Action** (event text)
  * **Prev\_Hash**
  * **Hash**
* Hash formula:

  ```
  hash = SHA256(prev_hash + timestamp + action)
  ```
* This means:

  * If even one character of one log changes, verification will fail.
  * The `verify` route can be run at any time to check log integrity.

---

### **4. Summary & Reporting Tools**

The project includes CLI utilities to turn raw logs into actionable insights:

* **`summary_app_usage.py`** – Shows app usage duration and session counts.
* **`summary_viewer.py`** – Filters and groups any kind of audit log events.
* **`summary_input_activity.py`** – Dedicated to input activity logs; can export flattened event lists.
* Export formats:

  * **CSV** – For spreadsheet analysis.
  * **HTML** – For formatted reports with tables.
  * *(Optional)* Could integrate with BI dashboards.

---

## **🚀 Key Features**

* **Multi-source monitoring**: Apps, files, inputs.
* **Real-time logging** with minimal overhead.
* **Batch submission** for efficiency.
* **Configurable privacy level**.
* **Cryptographic integrity verification**.
* **Cross-format reporting** (CLI + CSV + HTML).
* **Trackpad-friendly input logging** (smooth scroll and move support).
* **Extensible architecture** – add new monitors easily.

---

## **🛠 Technologies Used**

* **Python** – Core language for monitors, backend, and report generation.
* **Flask** – Lightweight web server for receiving logs.
* **SQLite** – Local database for secure log storage.
* **hashlib** – SHA-256 hashing for log chaining.
* **pynput** – Capturing keyboard/mouse events.
* **watchdog** – File system monitoring.
* **requests** – HTTP client for log submission.
* **argparse / csv / json** – CLI tools and export formats.

---

## **📂 Project Structure**

```
/monitor
    app_usage_tracker.py     # Tracks app focus and session durations
    file_watcher.py          # Monitors file/folder events
    input_summary_logger.py  # Tracks input activity (keys/clicks/scrolls/moves)
app.py                       # Flask backend server
db.py                        # DB connection, log insertion, hash calculation
summary_app_usage.py         # App usage summary
summary_viewer.py            # General log viewer/exporter
summary_input_activity.py    # Input logger summary tool
```

---

## **💡 Example Use Cases**

* **Security & Compliance** – Ensure employees handling sensitive data follow policy; detect anomalous file or app usage.
* **Productivity Analysis** – Identify top-used applications, time allocation, and workflow patterns.
* **Digital Forensics** – Provide verifiable evidence of user activity in an investigation.
* **Remote Work Oversight** – Give managers a summarized view of team activity without invasive surveillance.

---

## **🔒 Privacy Considerations**

* By default, no actual text typed by the user is stored—only event metadata and counts.
* Configuration options allow enabling exact key capture **only with explicit consent**.
* Data is stored locally; no external network transmission unless explicitly set up.

---

## **🚧 Future Enhancements**

* 📊 Web dashboard for real-time visualization.
* 📌 Configurable alert system for suspicious activity.
* 🔄 Remote sync with central logging server.
* 📈 ML-based anomaly detection for unusual behavior patterns.

---

## **📜 License**

*(Include your chosen license here, e.g., MIT, Apache 2.0, etc.)*

---

## **🙋 About the Author**

Built by **Shourya Sai Macha**, a Computer Information Systems student passionate about **cybersecurity**, **forensics**, and **system monitoring**.
This project demonstrates **full-stack problem-solving** — from low-level OS event capture to secure backend logging and data analytics.


