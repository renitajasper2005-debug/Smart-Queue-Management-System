"""
app.py — Main Flask application for Smart Queue Appointment & Notification System
"""
import os

# Load .env before anything else reads environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import threading
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, session, url_for
from flask_cors import CORS

from auth_store import authenticate_user, create_user
import db
from notifications import (
    send_auto_reminder,
    send_booking_confirmation,
    send_checkin_reminder,
    send_eta_notification,
    send_next_turn_notification,
    send_status_update_notification,
)
from queue_manager import auto_fill_gaps, get_expected_duration, get_queue_summary, get_wait_metrics, mark_missed
from qr_module import generate_qr
from service_config import get_service_catalog, save_service_catalog

app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv("SECRET_KEY", "smart-queue-dev-secret")

BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")
NOTIFICATION_POLL_SECONDS = int(os.getenv("NOTIFICATION_POLL_SECONDS", "30"))
ADMIN_LOGIN_USERNAME = os.getenv("ADMIN_LOGIN_USERNAME", "admin")
ADMIN_LOGIN_PASSWORD = os.getenv("ADMIN_LOGIN_PASSWORD", "admin123")
AUTO_REMINDER_STEPS = [
    {"field": "notify_now_sent_at", "type": "now", "minutes": 0},
    {"field": "notify_10_sent_at", "type": "10_min", "minutes": 10},
    {"field": "notify_30_sent_at", "type": "30_min", "minutes": 30},
]
_worker_started = False
ROLE_LEVELS = {"guest": 0, "user": 1, "admin": 2}
WORKDAY_START_HOUR = 8
WORKDAY_END_HOUR = 20
SLOT_BREAK_MINUTES = 5


def _timestamp() -> str:
    return datetime.now().isoformat()


def _remember_booking(record_id: str) -> None:
    booking_ids = session.get("user_booking_ids", [])
    if record_id in booking_ids:
        booking_ids = [rid for rid in booking_ids if rid != record_id]
    booking_ids.insert(0, record_id)
    session["user_booking_ids"] = booking_ids[:20]
    session.modified = True


def _get_saved_bookings() -> list[dict]:
    bookings = []
    for record_id in session.get("user_booking_ids", []):
        record = db.find_by_id(record_id)
        if not record:
            continue
        serialized = _serialize_record(record) or {}
        serialized["qr_url"] = f"/qr/{record_id}"
        wait_metrics = get_wait_metrics(record_id)
        if wait_metrics:
            serialized["wait_metrics"] = {
                "position": wait_metrics["position"],
                "estimated_wait_minutes": wait_metrics["estimated_wait_minutes"],
                "average_service_minutes": wait_metrics["average_service_minutes"],
            }
        bookings.append(serialized)
    return bookings


def _current_user_email() -> str:
    return session.get("user_email", "")


def _current_role() -> str:
    return session.get("role", "guest")


def _is_admin() -> bool:
    return _role_meets("admin")


def _role_meets(required_role: str) -> bool:
    return ROLE_LEVELS.get(_current_role(), 0) >= ROLE_LEVELS.get(required_role, 0)


def _is_api_request() -> bool:
    return request.path.startswith("/api/") or request.method != "GET"


def _unauthorized_response(login_endpoint: str, required_role: str):
    if _is_api_request():
        return jsonify({"error": f"{required_role.title()} login required."}), 401
    return redirect(url_for(login_endpoint, next=request.path))


def require_role(required_role: str, login_endpoint: str):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not _role_meets(required_role):
                return _unauthorized_response(login_endpoint, required_role)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def _finish_login(role: str, username: str, fallback_endpoint: str):
    session["role"] = role
    session["username"] = username
    if role != "user":
        session.pop("user_email", None)
    next_path = request.args.get("next", "").strip()
    if next_path.startswith("/") and not next_path.startswith("//"):
        return redirect(next_path)
    return redirect(url_for(fallback_endpoint))


def _finish_user_login(email: str, name: str = ""):
    session["role"] = "user"
    session["username"] = name or email
    session["user_email"] = email
    next_path = request.args.get("next", "").strip()
    if next_path.startswith("/") and not next_path.startswith("//"):
        return redirect(next_path)
    return redirect(url_for("booking_page"))


@app.context_processor
def inject_auth_context():
    return {
        "current_role": _current_role(),
        "current_username": session.get("username", ""),
        "current_user_email": _current_user_email(),
    }


EDITABLE_FIELDS = ["name", "date", "time", "service", "phone", "email"]


def _notify_next_patient(*, skip_if_already_sent: bool = True) -> dict | None:
    summary = get_queue_summary()
    next_up = summary.get("next_up")
    if not next_up:
        return None

    next_record = db.find_by_id(next_up["id"])
    if not next_record:
        return None

    if skip_if_already_sent and next_record.get("last_notification_type") == "next_turn":
        return {
            "id": next_record["id"],
            "name": next_record.get("name", ""),
            "phone": next_record.get("phone", ""),
            "estimated_wait_minutes": int(next_up.get("estimated_wait_minutes", 0) or 0),
            "notification": {
                "ok": True,
                "channel": next_record.get("last_notification_channel", "email"),
                "detail": "Next-turn notification was already sent for this patient.",
                "provider": "existing",
            },
        }

    eta_minutes = int(next_up.get("estimated_wait_minutes", 0) or 0)
    result = send_next_turn_notification(next_record, eta_minutes=eta_minutes)
    updates = {
        "last_notified_at": _timestamp(),
        "last_notification_channel": result.get("channel", "sms"),
        "last_notification_detail": result.get("detail", ""),
        "last_notification_type": "next_turn",
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


def _user_can_access_record(record: dict | None) -> bool:
    if not record:
        return False
    if _is_admin():
        return True

    record_id = record.get("id") or record.get("_id_str")
    saved_ids = set(session.get("user_booking_ids", []))
    record_email = (record.get("email") or "").strip().lower()
    current_email = _current_user_email().strip().lower()

    return bool(
        (record_id and record_id in saved_ids)
        or (record_email and current_email and record_email == current_email)
    )


def _user_can_edit_record(record: dict | None) -> bool:
    if not _user_can_access_record(record):
        return False
    return bool(record and record.get("type") == "appointment" and record.get("status") == "waiting")


def _queue_record_for_current_role(record: dict) -> dict:
    serialized = _serialize_record(record) or {}
    if _is_admin():
        return serialized

    safe_id = serialized.get("id", "")
    safe_name = serialized.get("name") or "Patient"
    if safe_id:
        safe_name = f"Patient {safe_id[:4]}"

    return {
        "id": safe_id,
        "name": safe_name,
        "service": serialized.get("service", ""),
        "type": serialized.get("type", ""),
        "date": serialized.get("date", ""),
        "time": serialized.get("time", ""),
        "status": serialized.get("status", ""),
        "estimated_wait_minutes": serialized.get("estimated_wait_minutes", 0),
        "predicted_start": serialized.get("predicted_start"),
        "service_duration_minutes": serialized.get("service_duration_minutes"),
        "expected_duration_minutes": serialized.get("expected_duration_minutes"),
        "gap_reason": serialized.get("gap_reason"),
    }


def _queue_summary_for_current_role(summary: dict) -> dict:
    if _is_admin():
        enriched = dict(summary)
        enriched["viewer_role"] = "admin"
        return enriched

    filtered = dict(summary)
    for key in ("waiting", "accepted", "arrived", "completed", "missed"):
        filtered[key] = [_queue_record_for_current_role(record) for record in summary.get(key, [])]

    next_up = summary.get("next_up")
    filtered["next_up"] = _queue_record_for_current_role(next_up) if next_up else None
    filtered["viewer_role"] = "user"
    return filtered


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


def _scheduled_datetime(record: dict) -> datetime | None:
    date_value = (record.get("date") or "").strip()
    time_value = (record.get("time") or "").strip()
    if not date_value or not time_value or date_value == "Walk-in" or time_value == "Walk-in":
        return None
    try:
        return datetime.fromisoformat(f"{date_value}T{time_value}")
    except ValueError:
        return None


def _target_wait_minutes(record: dict) -> int | None:
    scheduled_dt = _scheduled_datetime(record)
    if scheduled_dt:
        return int((scheduled_dt - datetime.now()).total_seconds() // 60)

    predicted_start = record.get("predicted_start")
    if not predicted_start:
        return None
    try:
        predicted_dt = datetime.fromisoformat(predicted_start)
    except ValueError:
        return None
    return int((predicted_dt - datetime.now()).total_seconds() // 60)


def _parse_appointment_start(date_value: str | None, time_value: str | None) -> datetime | None:
    if not date_value or not time_value or date_value == "Walk-in" or time_value == "Walk-in":
        return None
    try:
        return datetime.fromisoformat(f"{date_value}T{time_value}")
    except ValueError:
        return None


def _appointments_for_date(date_value: str, service: str | None = None, *, exclude_id: str | None = None) -> list[dict]:
    records = []
    for record in db.get_all():
        if record.get("type") != "appointment":
            continue
        if record.get("date") != date_value:
            continue
        if service and record.get("service") != service:
            continue
        if exclude_id and record.get("id") == exclude_id:
            continue
        if record.get("status") == "missed":
            continue
        records.append(record)
    return records


def _booking_conflicts(date_value: str, time_value: str, service: str, *, exclude_id: str | None = None) -> bool:
    start_dt = _parse_appointment_start(date_value, time_value)
    if not start_dt:
        return True

    duration_minutes = get_expected_duration({"service": service})
    occupied_minutes = duration_minutes + SLOT_BREAK_MINUTES
    end_dt = start_dt + timedelta(minutes=occupied_minutes)
    if start_dt.hour < WORKDAY_START_HOUR or end_dt > start_dt.replace(hour=WORKDAY_END_HOUR, minute=0, second=0, microsecond=0):
        return True

    for record in _appointments_for_date(date_value, exclude_id=exclude_id):
        existing_start = _parse_appointment_start(record.get("date"), record.get("time"))
        if not existing_start:
            continue
        existing_duration = get_expected_duration(record)
        existing_end = existing_start + timedelta(minutes=existing_duration + SLOT_BREAK_MINUTES)
        if start_dt < existing_end and existing_start < end_dt:
            return True
    return False


def _available_slots(date_value: str, service: str, *, exclude_id: str | None = None) -> list[str]:
    if not date_value or not service:
        return []

    duration_minutes = get_expected_duration({"service": service})
    slot_span_minutes = duration_minutes + SLOT_BREAK_MINUTES
    try:
        workday_start = datetime.fromisoformat(f"{date_value}T{WORKDAY_START_HOUR:02d}:00")
    except ValueError:
        return []

    workday_end = datetime.fromisoformat(f"{date_value}T{WORKDAY_END_HOUR:02d}:00")
    latest_start = workday_end - timedelta(minutes=slot_span_minutes)
    if latest_start < workday_start:
        return []

    slots = []
    current = workday_start
    while current <= latest_start:
        candidate = current.strftime("%H:%M")
        if not _booking_conflicts(date_value, candidate, service, exclude_id=exclude_id):
            slots.append(candidate)
        current += timedelta(minutes=slot_span_minutes)
    return slots


def process_due_notifications() -> list[dict]:
    summary = get_queue_summary()
    processed = []
    for record in summary.get("waiting", []):
        wait_minutes = _target_wait_minutes(record)
        if wait_minutes is None:
            continue

        chosen_step = None
        if wait_minutes <= 0:
            chosen_step = AUTO_REMINDER_STEPS[0]
        elif wait_minutes <= 10:
            chosen_step = AUTO_REMINDER_STEPS[1]
        elif wait_minutes <= 30:
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


@app.route("/login")
def login_redirect():
    return redirect(url_for("user_login"))


@app.route("/login/user", methods=["GET", "POST"])
def user_login():
    error = None
    selected_mode = "login"
    if request.method == "POST":
        selected_mode = (request.form.get("mode") or "login").strip().lower()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        name = (request.form.get("name") or "").strip()

        if selected_mode == "signup":
            ok, message = create_user(email, password, name)
            if ok:
                return _finish_user_login(email, name)
            error = message
        else:
            user = authenticate_user(email, password)
            if user:
                return _finish_user_login(user.get("email", email), user.get("name", ""))
            error = "Invalid email or password."

    return render_template("user_login.html", error=error, selected_mode=selected_mode)


@app.route("/login/admin", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username == ADMIN_LOGIN_USERNAME and password == ADMIN_LOGIN_PASSWORD:
            return _finish_login("admin", username, "dashboard_page")
        error = "Invalid admin username or password."
    return render_template("admin_login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/booking")
@require_role("user", "user_login")
def booking_page():
    return render_template("booking.html")

@app.route("/walkin")
@require_role("user", "user_login")
def walkin_page():
    return render_template("walkin.html")

@app.route("/queue")
@require_role("user", "user_login")
def queue_page():
    return render_template("queue.html")

@app.route("/status")
@require_role("user", "user_login")
def status_page():
    return render_template("status.html")

@app.route("/my-bookings")
@require_role("user", "user_login")
def my_bookings_page():
    return render_template("my_bookings.html")

@app.route("/checkin")
@require_role("admin", "admin_login")
def checkin_page():
    return render_template("checkin.html")

@app.route("/dashboard")
@require_role("admin", "admin_login")
def dashboard_page():
    return render_template("dashboard.html")


@app.route("/api/services", methods=["GET"])
@require_role("user", "user_login")
def get_services():
    return jsonify({"services": get_service_catalog()})


@app.route("/api/slots", methods=["GET"])
@require_role("user", "user_login")
def get_slots():
    date_value = (request.args.get("date") or "").strip()
    service = (request.args.get("service") or "").strip()
    exclude_id = (request.args.get("exclude_id") or "").strip() or None
    slots = _available_slots(date_value, service, exclude_id=exclude_id)
    return jsonify(
        {
            "slots": slots,
            "working_hours": {
                "start": f"{WORKDAY_START_HOUR:02d}:00",
                "end": f"{WORKDAY_END_HOUR:02d}:00",
            },
        }
    )


@app.route("/api/services", methods=["POST"])
@require_role("admin", "admin_login")
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
@require_role("user", "user_login")
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
        "email": (data.get("email") or _current_user_email()).strip().lower(),
    }
    if not record["email"]:
        return jsonify({"error": "Email is required"}), 400
    if _booking_conflicts(record["date"], record["time"], record["service"]):
        return jsonify({"error": "That slot is no longer available. Please choose another time."}), 409
    record["expected_duration_minutes"] = get_expected_duration(record)
    saved = db.insert_record(record)
    rid = saved["_id_str"]
    _remember_booking(rid)

    # Generate QR code
    qr_path = generate_qr(rid, BASE_URL)
    saved_for_notification = {k: v for k, v in saved.items() if k != "_id"}
    saved_for_notification["id"] = rid
    confirmation = send_booking_confirmation(saved_for_notification)

    return jsonify({
        "message": "Appointment booked successfully!",
        "id": rid,
        "qr_url": f"/static/{qr_path}",
        "record": {k: v for k, v in saved.items() if k != "_id"},
        "notification": confirmation,
    }), 201


@app.route("/walkin", methods=["POST"])
@require_role("user", "user_login")
def register_walkin():
    data = request.get_json(force=True)
    if not data.get("name"):
        return jsonify({"error": "Name is required"}), 400

    record = {
        "name": data["name"],
        "service": data.get("service", "General"),
        "phone": data.get("phone", ""),
        "email": (data.get("email") or _current_user_email()).strip().lower(),
        "type": "walkin",
        "date": "Walk-in",
        "time": "Walk-in",
    }
    record["expected_duration_minutes"] = get_expected_duration(record)
    saved = db.insert_record(record)
    rid = saved["_id_str"]
    _remember_booking(rid)

    # Attempt gap filling
    promoted = auto_fill_gaps()

    return jsonify({
        "message": "Walk-in registered!",
        "id": rid,
        "promoted_ids": promoted,
        "record": {k: v for k, v in saved.items() if k != "_id"},
    }), 201


@app.route("/api/queue", methods=["GET"])
@require_role("user", "user_login")
def get_queue():
    summary = get_queue_summary()
    return jsonify(_queue_summary_for_current_role(summary))


@app.route("/api/my-bookings", methods=["GET"])
@require_role("user", "user_login")
def get_my_bookings():
    return jsonify({"bookings": _get_saved_bookings()})


@app.route("/api/notifications/run", methods=["POST"])
@require_role("admin", "admin_login")
def run_notifications_now():
    processed = process_due_notifications()
    return jsonify({"processed": processed, "count": len(processed)})


@app.route("/record/<record_id>", methods=["GET"])
@require_role("user", "user_login")
def get_record(record_id):
    record = db.find_by_id(record_id)
    if not record:
        return jsonify({"error": "Record not found"}), 404
    if not _user_can_access_record(record):
        return jsonify({"error": "You do not have access to this record."}), 403
    wait_metrics = get_wait_metrics(record_id)
    response = {"record": _serialize_record(record)}
    if wait_metrics:
        response["wait_metrics"] = {
            "position": wait_metrics["position"],
            "estimated_wait_minutes": wait_metrics["estimated_wait_minutes"],
            "average_service_minutes": wait_metrics["average_service_minutes"],
        }
    return jsonify(response)


@app.route("/api/status/<record_id>", methods=["GET"])
@require_role("user", "user_login")
def get_patient_status(record_id):
    record = db.find_by_id(record_id)
    if not record:
        return jsonify({"error": "Record not found"}), 404
    if not _user_can_access_record(record):
        return jsonify({"error": "You do not have access to this record."}), 403
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
@require_role("user", "user_login")
def update_record(record_id):
    record = db.find_by_id(record_id)
    if not record:
        return jsonify({"error": "Record not found"}), 404
    if _is_admin():
        can_edit = record.get("status") != "completed"
        denied_message = "Completed records cannot be edited"
    else:
        can_edit = _user_can_edit_record(record)
        denied_message = "Only your own waiting appointments can be edited."
    if not can_edit:
        return jsonify({"error": denied_message}), 403

    payload = request.get_json(force=True) or {}
    updates = {}
    for field in EDITABLE_FIELDS:
        if field in payload:
            updates[field] = payload.get(field, "")

    if not updates:
        return jsonify({"error": "No editable fields provided"}), 400

    if not _is_admin():
        updates["email"] = _current_user_email()

    merged = dict(record)
    merged.update(updates)
    updates["expected_duration_minutes"] = get_expected_duration(merged)
    if any(field in updates for field in ("date", "time", "service")):
        candidate_date = updates.get("date", record.get("date"))
        candidate_time = updates.get("time", record.get("time"))
        candidate_service = updates.get("service", record.get("service"))
        if _booking_conflicts(candidate_date, candidate_time, candidate_service, exclude_id=record_id):
            return jsonify({"error": "That slot is no longer available. Please choose another time."}), 409

    db.update_fields(record_id, updates)
    updated = db.find_by_id(record_id)
    return jsonify({"message": "Record updated successfully.", "record": _serialize_record(updated)})


@app.route("/admin/scan/<record_id>", methods=["POST", "GET"])
@app.route("/checkin/<record_id>", methods=["POST", "GET"])
@require_role("admin", "admin_login")
def check_in(record_id):
    record = db.find_by_id(record_id)
    if not record:
        if request.method == "GET":
            return render_template("checkin.html", error="ID not found", record_id=record_id)
        return jsonify({"error": "Record not found"}), 404

    newly_accepted = False
    if request.method == "GET":
        return render_template(
            "checkin.html",
            record=record,
            record_id=record_id,
            message="Patient loaded. Review the details and choose an action.",
        )

    if record["status"] == "completed":
        msg = "Already completed."
    elif record["status"] == "missed":
        msg = "Marked as missed. Please check with staff."
    elif record["status"] == "accepted":
        msg = "Already accepted. Patient may go to the doctor."
    else:
        accepted_at = _timestamp()
        db.update_fields(record_id, {"status": "accepted", "accepted_at": accepted_at})
        record["status"] = "accepted"
        record["accepted_at"] = accepted_at
        newly_accepted = True
        status_result = send_status_update_notification(record, "accepted")
        status_updates = _apply_notification_result(record_id, "status_accepted", status_result)
        record.update(status_updates)
        msg = f"Accepted. Patient may go to the doctor. {status_result.get('detail', '')}".strip()

    response = {"message": msg, "record": record}
    if newly_accepted:
        notification = _notify_next_patient()
        if notification and notification.get("notification", {}).get("detail"):
            response["next_notification"] = notification
            response["message"] += f" Next patient: {notification['name']}. {notification['notification']['detail']}"

    return jsonify(response)


@app.route("/complete/<record_id>", methods=["POST"])
@require_role("admin", "admin_login")
def complete_appointment(record_id):
    record = db.find_by_id(record_id)
    if not record:
        return jsonify({"error": "Record not found"}), 404
    db.update_fields(record_id, {"status": "completed", "completed_at": _timestamp()})
    record["status"] = "completed"
    status_result = send_status_update_notification(record, "completed")
    _apply_notification_result(record_id, "status_completed", status_result)
    notification = _notify_next_patient()
    response = {"message": f"Marked as completed. {status_result.get('detail', '')}".strip(), "id": record_id}
    if notification:
        note = notification["notification"]["detail"]
        if note != "Next-turn notification was already sent for this patient.":
            response["next_notification"] = notification
            response["message"] += f" Next patient: {notification['name']}. {note}"
    if "next_notification" not in response:
        response["message"] += " No new waiting patients to notify."
    return jsonify(response)


@app.route("/notify/eta/<record_id>", methods=["POST"])
@require_role("admin", "admin_login")
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
@require_role("admin", "admin_login")
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
@require_role("admin", "admin_login")
def miss_appointment(record_id):
    record = db.find_by_id(record_id)
    if not record:
        return jsonify({"error": "Record not found"}), 404
    mark_missed(record_id)
    record["status"] = "missed"
    status_result = send_status_update_notification(record, "missed")
    _apply_notification_result(record_id, "status_missed", status_result)
    promoted = auto_fill_gaps()
    return jsonify({
        "message": f"Marked as missed. Walk-ins promoted. {status_result.get('detail', '')}".strip(),
        "promoted": promoted,
    })


@app.route("/qr/<record_id>")
def get_qr(record_id):
    qr_dir = os.path.join(app.root_path, "static", "qrcodes")
    filename = f"{record_id}.png"
    if not os.path.exists(os.path.join(qr_dir, filename)):
        generate_qr(record_id, BASE_URL)
    return send_from_directory(qr_dir, filename)


@app.route("/api/email-config", methods=["GET"])
@require_role("admin", "admin_login")
def email_config_status():
    """Check current email configuration status."""
    from notifications import EMAIL_PROVIDER, EMAIL_FROM, SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD
    configured = (
        EMAIL_PROVIDER == "smtp"
        and bool(SMTP_HOST)
        and bool(SMTP_USERNAME)
        and bool(SMTP_PASSWORD)
        and bool(EMAIL_FROM)
    )
    return jsonify({
        "provider": EMAIL_PROVIDER,
        "smtp_host": SMTP_HOST or "(not set)",
        "smtp_username": SMTP_USERNAME[:3] + "***" if SMTP_USERNAME else "(not set)",
        "email_from": EMAIL_FROM or "(not set)",
        "password_set": bool(SMTP_PASSWORD),
        "configured": configured,
        "message": "✅ Email is configured and ready!" if configured else "⚠️ Email is in dry-run mode. Update .env file with SMTP credentials.",
    })


@app.route("/api/test-email", methods=["POST"])
@require_role("admin", "admin_login")
def test_email():
    """Send a test email to verify SMTP configuration."""
    from notifications import send_email_message
    payload = request.get_json(force=True) or {}
    to_email = (payload.get("email") or "").strip().lower()
    if not to_email or "@" not in to_email:
        return jsonify({"error": "Provide a valid email address."}), 400

    test_record = {"email": to_email, "name": "Test User"}
    result = send_email_message(
        test_record,
        subject="🧪 SmartQueue — Test Email",
        plain_text=(
            "Hi there!\n\n"
            "This is a test email from SmartQueue.\n"
            "If you're reading this, your email configuration is working correctly!\n\n"
            "You can now receive:\n"
            "• Booking confirmations\n"
            "• Queue position updates\n"
            "• Turn notifications\n"
            "• Check-in reminders\n\n"
            "— SmartQueue System"
        ),
        html_body=_build_test_email_html(),
    )
    return jsonify({
        "message": result.get("detail", "Test processed."),
        "result": result,
    })


def _build_test_email_html() -> str:
    """Build a beautiful test email."""
    from notifications import _html_wrapper, _greeting, _message_text
    body = (
        _greeting("there")
        + _message_text(
            "This is a <strong style='color:#22c55e;'>test email</strong> from SmartQueue. "
            "If you're reading this, your email configuration is working correctly! 🎉"
        )
        + _message_text(
            "You can now receive:<br>"
            "✅ Booking confirmations<br>"
            "✅ Queue position updates<br>"
            "✅ Turn notifications<br>"
            "✅ Check-in reminders"
        )
    )
    return _html_wrapper("Test Email", body)


if __name__ == "__main__":
    _start_notification_worker()
    print("=" * 55)
    print("  Smart Queue System  |  http://localhost:5000")
    print("=" * 55)
    app.run(debug=True, use_reloader=False, port=5000)
