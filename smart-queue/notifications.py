"""
notifications.py - send queue notifications, with optional Twilio SMS delivery.
Falls back to a dry-run mode when SMS credentials are not configured.
"""
import base64
import json
import os
from urllib import error, parse, request


SMS_PROVIDER = os.getenv("SMS_PROVIDER", "dry_run").strip().lower()
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "").strip()


def _format_phone(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit() or ch == "+")


def _build_next_turn_message(record: dict, eta_minutes: int) -> str:
    if eta_minutes <= 0:
        eta_text = "Please come to the reception now."
    else:
        eta_text = f"Please be ready in about {eta_minutes} minutes."
    return (
        f"SmartQueue: Hi {record.get('name', 'Guest')}, "
        f"your turn for {record.get('service', 'your appointment')} is coming up next. "
        f"{eta_text} ID: {record.get('id', '')}"
    )


def _build_eta_message(record: dict, eta_minutes: int) -> str:
    return (
        f"SmartQueue: Hi {record.get('name', 'Guest')}, "
        f"your estimated wait time for {record.get('service', 'your appointment')} "
        f"is about {max(0, eta_minutes)} minutes. "
        f"Please keep your phone nearby. ID: {record.get('id', '')}"
    )


def _build_checkin_reminder_message(record: dict, minutes_before: int) -> str:
    return (
        f"SmartQueue: Hi {record.get('name', 'Guest')}, "
        f"please be ready to check in within {minutes_before} minutes for "
        f"{record.get('service', 'your appointment')}. ID: {record.get('id', '')}"
    )


def _build_auto_reminder_message(record: dict, reminder_type: str) -> str:
    if reminder_type == "30_min":
        return (
            f"SmartQueue: Hi {record.get('name', 'Guest')}, your "
            f"{record.get('service', 'appointment')} is expected in about 30 minutes. "
            "Please plan to arrive soon."
        )
    if reminder_type == "10_min":
        return (
            f"SmartQueue: Hi {record.get('name', 'Guest')}, your "
            f"{record.get('service', 'appointment')} is expected in about 10 minutes. "
            "Please come to the reception area."
        )
    return (
        f"SmartQueue: Hi {record.get('name', 'Guest')}, it is your turn now for "
        f"{record.get('service', 'your appointment')}. Please check in immediately."
    )


def _send_via_twilio(to_phone: str, message: str) -> dict:
    auth_pair = f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode("utf-8")
    auth_header = base64.b64encode(auth_pair).decode("ascii")
    payload = parse.urlencode(
        {
            "To": to_phone,
            "From": TWILIO_FROM_NUMBER,
            "Body": message,
        }
    ).encode("utf-8")
    url = (
        "https://api.twilio.com/2010-04-01/Accounts/"
        f"{TWILIO_ACCOUNT_SID}/Messages.json"
    )
    req = request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    return {
        "ok": True,
        "channel": "sms",
        "provider": "twilio",
        "provider_id": body.get("sid"),
        "detail": "SMS sent successfully.",
    }


def _deliver_sms(phone: str, message: str) -> dict:
    if SMS_PROVIDER != "twilio":
        print(f"[Notify] Dry run SMS to {phone}: {message}")
        return {
            "ok": True,
            "channel": "sms",
            "provider": "dry_run",
            "detail": "SMS dry run logged locally. Configure Twilio to send real texts.",
        }

    missing = [
        name
        for name, value in [
            ("TWILIO_ACCOUNT_SID", TWILIO_ACCOUNT_SID),
            ("TWILIO_AUTH_TOKEN", TWILIO_AUTH_TOKEN),
            ("TWILIO_FROM_NUMBER", TWILIO_FROM_NUMBER),
        ]
        if not value
    ]
    if missing:
        return {
            "ok": False,
            "channel": "sms",
            "provider": "twilio",
            "detail": f"Missing SMS configuration: {', '.join(missing)}",
        }

    try:
        return _send_via_twilio(phone, message)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "channel": "sms",
            "provider": "twilio",
            "detail": f"Twilio rejected the request: {detail}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "channel": "sms",
            "provider": "twilio",
            "detail": f"SMS send failed: {exc}",
        }


def send_sms_message(record: dict, message: str) -> dict:
    phone = _format_phone(record.get("phone", ""))
    if not phone:
        return {
            "ok": False,
            "channel": "sms",
            "provider": SMS_PROVIDER,
            "detail": "No phone number available for this patient.",
        }

    return _deliver_sms(phone, message)


def send_next_turn_notification(record: dict, eta_minutes: int = 0) -> dict:
    return send_sms_message(record, _build_next_turn_message(record, eta_minutes))


def send_eta_notification(record: dict, eta_minutes: int) -> dict:
    return send_sms_message(record, _build_eta_message(record, eta_minutes))


def send_checkin_reminder(record: dict, minutes_before: int = 10) -> dict:
    return send_sms_message(record, _build_checkin_reminder_message(record, minutes_before))


def send_auto_reminder(record: dict, reminder_type: str) -> dict:
    return send_sms_message(record, _build_auto_reminder_message(record, reminder_type))
