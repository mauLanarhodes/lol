from flask import Flask, request, jsonify, abort
from functools import wraps
import os
from db import log_action, init_db, get_db, calculate_hash

# --- Flask Setup ---
app = Flask(__name__)
init_db()

# --- Security Token Setup ---
API_TOKEN = os.getenv("SECURE_API_TOKEN", "supersecrettoken123")

def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token or token != f"Bearer {API_TOKEN}":
            abort(401, description="Unauthorized: Invalid or missing token.")
        return f(*args, **kwargs)
    return decorated

# --- Routes ---

@app.route('/log', methods=['POST'])
@require_token
def log_event():
    data = request.get_json()
    action = data.get("action")
    if not action:
        return jsonify({"error": "Missing 'action' field"}), 400
    result = log_action(action)
    return jsonify(result), 201

@app.route('/verify', methods=['GET'])
@require_token
def verify_logs():
    conn = get_db()
    logs = conn.execute("SELECT * FROM audit_logs ORDER BY id ASC").fetchall()

    for i in range(1, len(logs)):
        expected_hash = calculate_hash(
            logs[i-1]["hash"], logs[i]["timestamp"], logs[i]["action"]
        )
        if logs[i]["hash"] != expected_hash:
            return jsonify({
                "status": "FAILED",
                "message": f"Tampering detected at entry ID {logs[i]['id']}"
            }), 200

    return jsonify({
        "status": "SUCCESS",
        "message": "All logs are intact and verified."
    }), 200

@app.route('/log-batch', methods=['POST'])
@require_token
def log_batch():
    data = request.get_json()
    actions = data.get("actions")
    if not isinstance(actions, list) or not actions:
        return jsonify({"error": "Missing or invalid 'actions' field; expected non-empty list"}), 400

    results = []
    for action in actions:
        if not isinstance(action, str) or not action.strip():
            # Skip bad items but continue processing others
            continue
        results.append(log_action(action.strip()))
    return jsonify({"logged": results, "count": len(results)}), 201

if __name__ == '__main__':
    init_db()  # Ensure DB is initialized before starting the app
    app.run(debug=True)
