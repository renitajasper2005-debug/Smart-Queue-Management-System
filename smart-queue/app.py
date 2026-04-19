"""
app.py — Main Flask application for Smart Queue Appointment & Notification System
"""
import os
import threading
import time
from datetime import datetime

from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS

import db
from notifications import (
    send_auto_reminder,
    send_checkin_reminder,
    send_eta_notification,
    send_next_turn_notification,
)
from queue_manager import auto_fill_gaps, get_expected_duration, get_queue_summary, get_wait_metrics, mark_missed
from qr_module import generate_qr
from service_config import get_service_catalog, save_service_catalog

app = Flask(__name__)
CORS(app)

BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")
NOTIFICATION_POLL_SECONDS = int(os.getenv("NOTIFICATION_POLL_SECONDS", "30"))
AUTO_REMINDER_STEPS = [
    {"field": "notify_now_sent_at", "type": "now", "minutes": 0},
    {"field": "notify_10_sent_at", "type": "10_min", "minutes": 10},
    {"field": "notify_30_sent_at", "type": "30_min", "minutes": 30},
]
_worker_started = False


def _timestamp() -> str:
    return datetime.now().isoformat()


EDITABLE_FIELDS = ["name", "date", "time", "service", "phone"]


def _notify_next_patient() -> dict | None:
    summary = get_queue_summary()
    next_up = summary.get("next_up")
    if not next_up:
        return None

    next_record = db.find_by_id(next_up["id"])
    if not next_record:
        return None

    eta_minutes = int(next_up.get("estimated_wait_minutes", 0) or 0)
    result = send_next_turn_notification(next_record, eta_minutes=eta_minutes)
    updates = {
        "last_notified_at": _timestamp(),
        "last_notification_channel": result.get("channel", "sms"),
        "last_notification_detail": result.get("detail", ""),
        "notification_state": "sent" if result.get("ok") else "failed",
    }
    db.update_fields(next_record["id"], updates)
    next_record.update(updates)
    next_record["estimated_wait_minutes"] = eta_minutes
    return {
        "id": next_record["id"],
        "name": next_record.get("name", ""),
        "phone": next_record.get("phone", ""),
        "estimated_wait_minutes": eta_minutes,
        "notification": result,
    }


def _serialize_record(record: dict | None) -> dict | None:
    if not record:
        return None
    return {k: v for k, v in record.items() if k != "_id"}


def _apply_notification_result(record_id: str, notification_type: str, result: dict) -> dict:
    updates = {
        "last_notified_at": _timestamp(),
        "last_notification_channel": result.get("channel", "sms"),
        "last_notification_detail": result.get("detail", ""),
        "last_notification_type": notification_type,
        "notification_state": "sent" if result.get("ok") else "failed",
    }
    db.update_fields(record_id, updates)
    return updates


def _apply_auto_reminder_result(record_id: str, reminder_step: dict, result: dict) -> dict:
    updates = _apply_notification_result(record_id, f"auto_{reminder_step['type']}", result)
    updates[reminder_step["field"]] = _timestamp()
    db.update_fields(record_id, {reminder_step["field"]: updates[reminder_step["field"]]})
    return updates


def process_due_notifications() -> list[dict]:
    summary = get_queue_summary()
    processed = []
    for record in summary.get("waiting", []):
        predicted_start = record.get("predicted_start")
        if not predicted_start:
            continue

        eta_minutes = int(record.get("estimated_wait_minutes", 0) or 0)
        chosen_step = None
        if eta_minutes <= 0:
            chosen_step = AUTO_REMINDER_STEPS[0]
        elif eta_minutes <= 10:
            chosen_step = AUTO_REMINDER_STEPS[1]
        elif eta_minutes <= 30:
            chosen_step = AUTO_REMINDER_STEPS[2]

        if not chosen_step:
            continue
        if record.get(chosen_step["field"]):
            continue

        live_record = db.find_by_id(record["id"])
        if not live_record:
            continue

        result = send_auto_reminder(live_record, chosen_step["type"])
        updates = _apply_auto_reminder_result(record["id"], chosen_step, result)
        processed.append(
            {
                "id": record["id"],
                "name": record.get("name", ""),
                "reminder_type": chosen_step["type"],
                "result": result,
                "updates": updates,
            }
        )
    return processed


def _notification_worker_loop() -> None:
    while True:
        try:
            processed = process_due_notifications()
            if processed:
                print(f"[NotifyWorker] processed {len(processed)} reminder(s)")
        except Exception as exc:
            print(f"[NotifyWorker] error: {exc}")
        time.sleep(max(10, NOTIFICATION_POLL_SECONDS))


def _start_notification_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    worker = threading.Thread(target=_notification_worker_loop, name="smartqueue-reminders", daemon=True)
    worker.start()

# ─────────────────────────────────────────────
#  Page routes
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/booking")
def booking_page():
    return render_template("booking.html")

@app.route("/walkin")
def walkin_page():
    return render_template("walkin.html")

@app.route("/queue")
def queue_page():
    return render_template("queue.html")

@app.route("/checkin")
def checkin_page():
    return render_template("checkin.html")

@app.route("/dashboard")
def dashboard_page():
    return render_template("dashboard.html")


@app.route("/api/services", methods=["GET"])
def get_services():
    return jsonify({"services": get_service_catalog()})


@app.route("/api/services", methods=["POST"])
def update_services():
    payload = request.get_json(force=True) or {}
    services = payload.get("services")
    if not isinstance(services, list):
        return jsonify({"error": "services must be a list"}), 400
    try:
        saved = save_service_catalog(services)
    except Exception as exc:
        return jsonify({"error": f"Could not save service catalog: {exc}"}), 500
    return jsonify({"message": "Service durations updated.", "services": saved})

# ─────────────────────────────────────────────
#  API routes
# ─────────────────────────────────────────────

@app.route("/appointment", methods=["POST"])
def book_appointment():
    data = request.get_json(force=True)
    required = ["name", "date", "time", "service"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400

    record = {
        "name": data["name"],
        "date": data["date"],
        "time": data["time"],
        "service": data["service"],
        "type": "appointment",
        "phone": data.get("phone", ""),
    }
    record["expected_duration_minutes"] = get_expected_duration(record)
    saved = db.insert_record(record)
    rid = saved["_id_str"]

    # Generate QR code
    qr_path = generate_qr(rid, BASE_URL)

    return jsonify({
        "message": "Appointment booked successfully!",
        "id": rid,
        "qr_url": f"/static/{qr_path}",
        "record": {k: v for k, v in saved.items() if k != "_id"},
    }), 201


@app.route("/walkin", methods=["POST"])
def register_walkin():
    data = request.get_json(force=True)
    if not data.get("name"):
        return jsonify({"error": "Name is required"}), 400

    record = {
        "name": data["name"],
        "service": data.get("service", "General"),
        "phone": data.get("phone", ""),
        "type": "walkin",
        "date": "Walk-in",
        "time": "Walk-in",
    }
    record["expected_duration_minutes"] = get_expected_duration(record)
    saved = db.insert_record(record)
    rid = saved["_id_str"]

    # Attempt gap filling
    promoted = auto_fill_gaps()

    return jsonify({
        "message": "Walk-in registered!",
        "id": rid,
        "promoted_ids": promoted,
        "record": {k: v for k, v in saved.items() if k != "_id"},
    }), 201


@app.route("/api/queue", methods=["GET"])
def get_queue():
    summary = get_queue_summary()
    return jsonify(summary)


@app.route("/api/notifications/run", methods=["POST"])
def run_notifications_now():
    processed = process_due_notifications()
    return jsonify({"processed": processed, "count": len(processed)})


@app.route("/record/<record_id>", methods=["GET"])
def get_record(record_id):
    record = db.find_by_id(record_id)
    if not record:
        return jsonify({"error": "Record not found"}), 404
    wait_metrics = get_wait_metrics(record_id)
    response = {"record": _serialize_record(record)}
    if wait_metrics:
        response["wait_metrics"] = {
            "position": wait_metrics["position"],
            "estimated_wait_minutes": wait_metrics["estimated_wait_minutes"],
            "average_service_minutes": wait_metrics["average_service_minutes"],
        }
    return jsonify(response)


@app.route("/record/<record_id>", methods=["POST"])
def update_record(record_id):
    record = db.find_by_id(record_id)
    if not record:
        return jsonify({"error": "Record not found"}), 404

    payload = request.get_json(force=True) or {}
    updates = {}
    for field in EDITABLE_FIELDS:
        if field in payload:
            updates[field] = payload.get(field, "")

    if not updates:
        return jsonify({"error": "No editable fields provided"}), 400

    if record.get("status") == "completed":
        return jsonify({"error": "Completed records cannot be edited"}), 400

    merged = dict(record)
    merged.update(updates)
    updates["expected_duration_minutes"] = get_expected_duration(merged)

    db.update_fields(record_id, updates)
    updated = db.find_by_id(record_id)
    return jsonify({"message": "Record updated successfully.", "record": _serialize_record(updated)})


@app.route("/checkin/<record_id>", methods=["POST", "GET"])
def check_in(record_id):
    record = db.find_by_id(record_id)
    if not record:
        if request.method == "GET":
            return render_template("checkin.html", error="ID not found", record_id=record_id)
        return jsonify({"error": "Record not found"}), 404

    if record["status"] == "completed":
        msg = "Already completed."
    elif record["status"] == "missed":
        msg = "Marked as missed. Please check with staff."
    else:
        arrived_at = _timestamp()
        db.update_fields(record_id, {"status": "arrived", "arrived_at": arrived_at})
        record["status"] = "arrived"
        record["arrived_at"] = arrived_at
        msg = "Checked in successfully!"

    if request.method == "GET":
        return render_template("checkin.html", record=record, message=msg)
    return jsonify({"message": msg, "record": record})


@app.route("/complete/<record_id>", methods=["POST"])
def complete_appointment(record_id):
    record = db.find_by_id(record_id)
    if not record:
        return jsonify({"error": "Record not found"}), 404
    db.update_fields(record_id, {"status": "completed", "completed_at": _timestamp()})
    notification = _notify_next_patient()
    response = {"message": "Marked as completed.", "id": record_id}
    if notification:
        response["next_notification"] = notification
        note = notification["notification"]["detail"]
        response["message"] += f" Next patient: {notification['name']}. {note}"
    else:
        response["message"] += " No waiting patients to notify."
    return jsonify(response)


@app.route("/notify/eta/<record_id>", methods=["POST"])
def notify_eta(record_id):
    record = db.find_by_id(record_id)
    if not record:
        return jsonify({"error": "Record not found"}), 404

    wait_metrics = get_wait_metrics(record_id)
    if not wait_metrics:
        return jsonify({"error": "Only waiting patients can receive ETA notifications."}), 400

    result = send_eta_notification(record, wait_metrics["estimated_wait_minutes"])
    updates = _apply_notification_result(record_id, "eta", result)
    updated = db.find_by_id(record_id)
    if updated:
        updated.update(updates)

    return jsonify(
        {
            "message": result.get("detail", "ETA notification processed."),
            "record": _serialize_record(updated),
            "wait_metrics": {
                "position": wait_metrics["position"],
                "estimated_wait_minutes": wait_metrics["estimated_wait_minutes"],
                "average_service_minutes": wait_metrics["average_service_minutes"],
            },
            "notification": result,
        }
    )


@app.route("/notify/checkin-soon/<record_id>", methods=["POST"])
def notify_checkin_soon(record_id):
    record = db.find_by_id(record_id)
    if not record:
        return jsonify({"error": "Record not found"}), 404

    result = send_checkin_reminder(record, minutes_before=10)
    updates = _apply_notification_result(record_id, "checkin_reminder", result)
    updated = db.find_by_id(record_id)
    if updated:
        updated.update(updates)

    return jsonify(
        {
            "message": result.get("detail", "Check-in reminder processed."),
            "record": _serialize_record(updated),
            "notification": result,
        }
    )


@app.route("/miss/<record_id>", methods=["POST"])
def miss_appointment(record_id):
    record = db.find_by_id(record_id)
    if not record:
        return jsonify({"error": "Record not found"}), 404
    mark_missed(record_id)
    promoted = auto_fill_gaps()
    return jsonify({"message": "Marked as missed. Walk-ins promoted.", "promoted": promoted})


@app.route("/qr/<record_id>")
def get_qr(record_id):
    qr_dir = os.path.join(app.root_path, "static", "qrcodes")
    filename = f"{record_id}.png"
    if not os.path.exists(os.path.join(qr_dir, filename)):
        generate_qr(record_id, BASE_URL)
    return send_from_directory(qr_dir, filename)


if __name__ == "__main__":
    _start_notification_worker()
    print("=" * 55)
    print("  Smart Queue System  |  http://localhost:5000")
    print("=" * 55)
    app.run(debug=True, use_reloader=False, port=5000)
