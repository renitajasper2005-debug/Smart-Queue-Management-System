"""
notifications.py - send queue notifications over SMS and email.
Falls back to dry-run logging when providers are not configured.
"""
from __future__ import annotations

import base64
import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib import error, parse, request

# ── Load .env ────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Provider configuration ───────────────────────────────────────────

SMS_PROVIDER = os.getenv("SMS_PROVIDER", "dry_run").strip().lower()
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "").strip()

EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "dry_run").strip().lower()
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").strip().lower() != "false"
TERMINAL_NOTIFICATIONS = os.getenv("TERMINAL_NOTIFICATIONS", "true").strip().lower() != "false"

def _format_phone(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit() or ch == "+")


def _format_email(email: str) -> str:
    return (email or "").strip().lower()


def _console_safe(value: object) -> str:
    return str(value).encode("cp1252", errors="replace").decode("cp1252")


def _console_print(message: object) -> None:
    print(_console_safe(message))


# Log configuration status on import
if EMAIL_PROVIDER == "smtp" and SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD:
    _console_print(f"[Notify] Email configured: SMTP via {SMTP_HOST} as {EMAIL_FROM}")
else:
    _console_print(f"[Notify] Email mode: {EMAIL_PROVIDER} (configure .env for real emails)")


def _deliver_terminal(record: dict, subject: str, plain_text: str) -> dict | None:
    if not TERMINAL_NOTIFICATIONS:
        return None

    patient_name = record.get("name", "Guest")
    patient_id = record.get("id", "") or record.get("_id_str", "")
    email = _format_email(record.get("email", ""))
    phone = _format_phone(record.get("phone", ""))

    _console_print("[Notify][Terminal] ----------------------------------------")
    _console_print(f"[Notify][Terminal] Patient: {patient_name} ({patient_id})")
    if email:
        _console_print(f"[Notify][Terminal] Email: {email}")
    if phone:
        _console_print(f"[Notify][Terminal] Phone: {phone}")
    _console_print(f"[Notify][Terminal] Subject: {subject}")
    _console_print("[Notify][Terminal] Message:")
    _console_print(plain_text)
    _console_print("[Notify][Terminal] ----------------------------------------")

    return {
        "ok": True,
        "channel": "terminal",
        "provider": "console",
        "detail": "Notification logged to the server terminal.",
    }


# ── HTML Email Template ──────────────────────────────────────────────

def _html_wrapper(title: str, body_content: str) -> str:
    """Wraps email body content in a beautiful, responsive HTML template."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:#0f0f1a;font-family:'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#0f0f1a;padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:linear-gradient(145deg,#1a1a2e 0%,#16162a 100%);border-radius:16px;border:1px solid rgba(108,71,255,0.2);overflow:hidden;">

<!-- Header -->
<tr>
<td style="padding:32px 32px 16px;text-align:center;background:linear-gradient(135deg,rgba(108,71,255,0.15) 0%,rgba(168,85,247,0.08) 100%);border-bottom:1px solid rgba(108,71,255,0.15);">
  <div style="display:inline-block;width:12px;height:12px;background:linear-gradient(135deg,#6c47ff,#a855f7);border-radius:50%;margin-right:8px;vertical-align:middle;"></div>
  <span style="font-size:22px;font-weight:700;color:#ffffff;letter-spacing:0.5px;vertical-align:middle;">SmartQueue</span>
  <div style="margin-top:12px;font-size:13px;color:rgba(255,255,255,0.5);text-transform:uppercase;letter-spacing:1.5px;">{title}</div>
</td>
</tr>

<!-- Body -->
<tr>
<td style="padding:28px 32px;">
  {body_content}
</td>
</tr>

<!-- Footer -->
<tr>
<td style="padding:20px 32px;text-align:center;border-top:1px solid rgba(255,255,255,0.06);">
  <p style="margin:0;font-size:12px;color:rgba(255,255,255,0.3);line-height:1.6;">
    This is an automated notification from SmartQueue.<br>
    Please do not reply to this email.
  </p>
</td>
</tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _info_row(label: str, value: str, icon: str = "") -> str:
    """Generates a styled key-value row for email content."""
    icon_html = f'<span style="margin-right:6px;">{icon}</span>' if icon else ""
    return f"""\
<tr>
  <td style="padding:10px 14px;font-size:13px;color:rgba(255,255,255,0.5);white-space:nowrap;vertical-align:top;">{icon_html}{label}</td>
  <td style="padding:10px 14px;font-size:14px;color:#ffffff;font-weight:600;">{value}</td>
</tr>"""


def _detail_table(rows_html: str) -> str:
    """Wraps rows in a styled details table."""
    return f"""\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:10px;margin:16px 0;">
{rows_html}
</table>"""


def _action_button(text: str, url: str = "") -> str:
    """Generates a styled call-to-action button (non-clickable if no URL)."""
    if not url:
        return ""
    return f"""\
<div style="text-align:center;margin:24px 0 8px;">
  <a href="{url}" target="_blank" style="display:inline-block;padding:12px 32px;background:linear-gradient(135deg,#6c47ff,#a855f7);color:#ffffff;text-decoration:none;border-radius:8px;font-size:14px;font-weight:600;letter-spacing:0.3px;">{text}</a>
</div>"""


def _greeting(name: str) -> str:
    return f'<p style="margin:0 0 16px;font-size:16px;color:#ffffff;line-height:1.6;">Hi <strong>{name}</strong>,</p>'


def _message_text(text: str) -> str:
    return f'<p style="margin:0 0 16px;font-size:14px;color:rgba(255,255,255,0.75);line-height:1.7;">{text}</p>'


# ── Build Messages ───────────────────────────────────────────────────

def _build_booking_confirmation_plain(record: dict) -> str:
    return (
        f"Hi {record.get('name', 'Guest')},\n\n"
        "Your SmartQueue appointment has been booked successfully.\n"
        f"Appointment ID: {record.get('id', '')}\n"
        f"Service: {record.get('service', 'General')}\n"
        f"Date: {record.get('date', '-')}\n"
        f"Time: {record.get('time', '-')}\n\n"
        "Please keep your QR code ready at reception for check-in."
    )


def _build_booking_confirmation_html(record: dict) -> str:
    name = record.get("name", "Guest")
    appt_id = record.get("id", "")
    service = record.get("service", "General")
    date = record.get("date", "-")
    time = record.get("time", "-")

    rows = (
        _info_row("Appointment ID", f'<code style="background:rgba(108,71,255,0.15);padding:3px 10px;border-radius:4px;color:#a78bfa;font-size:15px;letter-spacing:1px;">{appt_id}</code>', "🆔")
        + _info_row("Service", service, "🏥")
        + _info_row("Date", date, "📅")
        + _info_row("Time", time, "⏰")
    )

    body = (
        _greeting(name)
        + _message_text("Your appointment has been <strong style='color:#22c55e;'>booked successfully</strong>! Here are the details:")
        + _detail_table(rows)
        + _message_text("📲 Please keep your <strong>QR code</strong> ready at the reception counter for check-in.")
    )
    return _html_wrapper("Booking Confirmed", body)


def _build_next_turn_plain(record: dict, eta_minutes: int) -> str:
    if eta_minutes <= 0:
        eta_text = "Please come to the reception now."
    else:
        eta_text = f"Please be ready in about {eta_minutes} minutes."
    return (
        f"SmartQueue: Hi {record.get('name', 'Guest')}, "
        f"your turn for {record.get('service', 'your appointment')} is coming up next. "
        f"{eta_text} ID: {record.get('id', '')}"
    )


def _build_next_turn_html(record: dict, eta_minutes: int) -> str:
    name = record.get("name", "Guest")
    service = record.get("service", "your appointment")
    appt_id = record.get("id", "")

    if eta_minutes <= 0:
        eta_text = "Please come to the reception <strong style='color:#f59e0b;'>now</strong>."
        eta_display = "Now"
    else:
        eta_text = f"Please be ready in about <strong style='color:#f59e0b;'>{eta_minutes} minutes</strong>."
        eta_display = f"~{eta_minutes} min"

    rows = (
        _info_row("Appointment ID", appt_id, "🆔")
        + _info_row("Service", service, "🏥")
        + _info_row("Estimated Wait", eta_display, "⏳")
    )

    body = (
        _greeting(name)
        + _message_text(f"🔔 <strong style='color:#f59e0b;'>Your turn is coming up next</strong> for {service}!")
        + _detail_table(rows)
        + _message_text(eta_text)
    )
    return _html_wrapper("Your Turn Is Next", body)


def _build_eta_plain(record: dict, eta_minutes: int) -> str:
    return (
        f"SmartQueue: Hi {record.get('name', 'Guest')}, "
        f"your estimated wait time for {record.get('service', 'your appointment')} "
        f"is about {max(0, eta_minutes)} minutes. "
        f"Please keep your phone nearby. ID: {record.get('id', '')}"
    )


def _build_eta_html(record: dict, eta_minutes: int) -> str:
    name = record.get("name", "Guest")
    service = record.get("service", "your appointment")
    appt_id = record.get("id", "")
    wait = max(0, eta_minutes)

    rows = (
        _info_row("Appointment ID", appt_id, "🆔")
        + _info_row("Service", service, "🏥")
        + _info_row("Estimated Wait", f"{wait} minutes", "⏳")
    )

    body = (
        _greeting(name)
        + _message_text(f"Here's an update on your wait time for <strong>{service}</strong>:")
        + _detail_table(rows)
        + _message_text("Please keep your phone nearby. We'll notify you when it's your turn.")
    )
    return _html_wrapper("Wait Time Update", body)


def _build_checkin_reminder_plain(record: dict, minutes_before: int) -> str:
    return (
        f"SmartQueue: Hi {record.get('name', 'Guest')}, "
        f"please be ready to check in within {minutes_before} minutes for "
        f"{record.get('service', 'your appointment')}. ID: {record.get('id', '')}"
    )


def _build_checkin_reminder_html(record: dict, minutes_before: int) -> str:
    name = record.get("name", "Guest")
    service = record.get("service", "your appointment")
    appt_id = record.get("id", "")

    rows = (
        _info_row("Appointment ID", appt_id, "🆔")
        + _info_row("Service", service, "🏥")
        + _info_row("Check-in Within", f"{minutes_before} minutes", "⏰")
    )

    body = (
        _greeting(name)
        + _message_text(f"⚡ Please be ready to <strong style='color:#f59e0b;'>check in within {minutes_before} minutes</strong> for {service}.")
        + _detail_table(rows)
        + _message_text("Head to the reception area with your QR code ready.")
    )
    return _html_wrapper("Check-in Reminder", body)


def _build_auto_reminder_plain(record: dict, reminder_type: str) -> str:
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


def _build_auto_reminder_html(record: dict, reminder_type: str) -> str:
    name = record.get("name", "Guest")
    service = record.get("service", "appointment")
    appt_id = record.get("id", "")

    if reminder_type == "30_min":
        urgency_color = "#3b82f6"
        time_text = "~30 minutes"
        heading = "Appointment in 30 Minutes"
        msg = f"Your <strong>{service}</strong> is expected in about <strong style='color:{urgency_color};'>30 minutes</strong>. Please plan to arrive soon."
    elif reminder_type == "10_min":
        urgency_color = "#f59e0b"
        time_text = "~10 minutes"
        heading = "Appointment in 10 Minutes"
        msg = f"Your <strong>{service}</strong> is expected in about <strong style='color:{urgency_color};'>10 minutes</strong>. Please come to the reception area."
    else:
        urgency_color = "#ef4444"
        time_text = "Now"
        heading = "It's Your Turn Now"
        msg = f"It is <strong style='color:{urgency_color};'>your turn now</strong> for <strong>{service}</strong>. Please check in immediately!"

    rows = (
        _info_row("Appointment ID", appt_id, "🆔")
        + _info_row("Service", service, "🏥")
        + _info_row("Time Remaining", f'<span style="color:{urgency_color};font-weight:700;">{time_text}</span>', "⏳")
    )

    body = (
        _greeting(name)
        + _message_text(f"🔔 {msg}")
        + _detail_table(rows)
    )
    return _html_wrapper(heading, body)


def _build_status_update_plain(record: dict, status_type: str) -> str:
    service = record.get("service", "your appointment")
    appt_id = record.get("id", "")

    if status_type == "accepted":
        return (
            f"SmartQueue: Hi {record.get('name', 'Guest')}, "
            f"the admin has checked you in for {service}. "
            f"You may now proceed for your appointment. ID: {appt_id}"
        )
    if status_type == "completed":
        return (
            f"SmartQueue: Hi {record.get('name', 'Guest')}, "
            f"your {service} visit has been marked as completed. "
            f"Thank you for visiting. ID: {appt_id}"
        )
    return (
        f"SmartQueue: Hi {record.get('name', 'Guest')}, "
        f"your {service} appointment has been marked as missed by the admin. "
        "Please contact the clinic if you need help rescheduling. "
        f"ID: {appt_id}"
    )


def _build_status_update_html(record: dict, status_type: str) -> str:
    name = record.get("name", "Guest")
    service = record.get("service", "your appointment")
    appt_id = record.get("id", "")
    date = record.get("date", "-")
    time = record.get("time", "-")

    if status_type == "accepted":
        title = "Checked In Successfully"
        status_label = "Accepted"
        message = (
            f"The admin has <strong style='color:#22c55e;'>checked you in</strong> for "
            f"<strong>{service}</strong>. You may now proceed for your appointment."
        )
    elif status_type == "completed":
        title = "Visit Completed"
        status_label = "Completed"
        message = (
            f"Your <strong>{service}</strong> visit has been "
            f"<strong style='color:#22c55e;'>marked as completed</strong>. Thank you for visiting."
        )
    else:
        title = "Appointment Marked Missed"
        status_label = "Missed"
        message = (
            f"Your <strong>{service}</strong> appointment has been "
            f"<strong style='color:#ef4444;'>marked as missed</strong> by the admin. "
            "Please contact the clinic if you need help rescheduling."
        )

    rows = (
        _info_row("Appointment ID", appt_id, "ID")
        + _info_row("Service", service, "Service")
        + _info_row("Date", date, "Date")
        + _info_row("Time", time, "Time")
        + _info_row("Status", status_label, "Status")
    )

    body = _greeting(name) + _message_text(message) + _detail_table(rows)
    return _html_wrapper(title, body)


# ── SMS Delivery ─────────────────────────────────────────────────────

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
        _console_print(f"[Notify] Dry run SMS to {phone}: {message}")
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


# ── Email Delivery ───────────────────────────────────────────────────

def _deliver_email(email: str, subject: str, plain_text: str, html_body: str = "") -> dict:
    if EMAIL_PROVIDER != "smtp":
        _console_print(f"[Notify] Dry run email to {email}: {subject}\n{plain_text}")
        return {
            "ok": True,
            "channel": "email",
            "provider": "dry_run",
            "detail": "Email dry run logged locally. Configure SMTP to send real mail.",
        }

    missing = [
        name
        for name, value in [
            ("SMTP_HOST", SMTP_HOST),
            ("SMTP_USERNAME", SMTP_USERNAME),
            ("SMTP_PASSWORD", SMTP_PASSWORD),
            ("EMAIL_FROM", EMAIL_FROM),
        ]
        if not value
    ]
    if missing:
        return {
            "ok": False,
            "channel": "email",
            "provider": "smtp",
            "detail": f"Missing email configuration: {', '.join(missing)}",
        }

    # Build multipart email with both plain text and HTML
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"SmartQueue <{EMAIL_FROM}>"
    msg["To"] = email

    # Plain text part (fallback)
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))

    # HTML part (preferred by email clients)
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            if SMTP_USE_TLS:
                server.starttls()
                server.ehlo()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        _console_print(f"[Notify] ✅ Email sent to {email}: {subject}")
        return {
            "ok": True,
            "channel": "email",
            "provider": "smtp",
            "detail": f"Email sent successfully to {email}.",
        }
    except smtplib.SMTPAuthenticationError as exc:
        _console_print(f"[Notify] ❌ SMTP auth failed: {exc}")
        return {
            "ok": False,
            "channel": "email",
            "provider": "smtp",
            "detail": "SMTP authentication failed. Check your email and app password in .env file.",
        }
    except smtplib.SMTPRecipientsRefused as exc:
        _console_print(f"[Notify] ❌ Recipient refused: {exc}")
        return {
            "ok": False,
            "channel": "email",
            "provider": "smtp",
            "detail": f"Recipient refused: {email}. Check the email address.",
        }
    except Exception as exc:
        _console_print(f"[Notify] ❌ Email send failed: {exc}")
        return {
            "ok": False,
            "channel": "email",
            "provider": "smtp",
            "detail": f"Email send failed: {exc}",
        }


# ── Result Merging ───────────────────────────────────────────────────

def _merge_results(results: list[dict]) -> dict:
    valid_results = [result for result in results if result]
    if not valid_results:
        return {
            "ok": False,
            "channel": "none",
            "provider": "none",
            "detail": "No notification destination available for this patient.",
        }

    channels = "+".join(result["channel"] for result in valid_results)
    providers = "+".join(result.get("provider", "") for result in valid_results if result.get("provider"))
    detail = " ".join(result.get("detail", "") for result in valid_results if result.get("detail")).strip()
    return {
        "ok": any(result.get("ok") for result in valid_results),
        "channel": channels,
        "provider": providers or "mixed",
        "detail": detail,
        "results": valid_results,
    }


# ── Public API ───────────────────────────────────────────────────────

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


def send_email_message(record: dict, subject: str, plain_text: str, html_body: str = "") -> dict:
    email = _format_email(record.get("email", ""))
    if not email:
        return {
            "ok": False,
            "channel": "email",
            "provider": EMAIL_PROVIDER,
            "detail": "No email address available for this patient.",
        }
    return _deliver_email(email, subject, plain_text, html_body)


def send_multichannel_message(
    record: dict,
    subject: str,
    plain_text: str,
    html_body: str = "",
    include_sms: bool = True,
    include_email: bool = True,
    include_terminal: bool = True,
) -> dict:
    results = []
    if include_terminal:
        terminal_result = _deliver_terminal(record, subject, plain_text)
        if terminal_result:
            results.append(terminal_result)
    if include_sms and record.get("phone"):
        results.append(send_sms_message(record, plain_text))
    if include_email and record.get("email"):
        results.append(send_email_message(record, subject, plain_text, html_body))
    return _merge_results(results)


def send_booking_confirmation(record: dict) -> dict:
    subject = f"✅ SmartQueue — Booking Confirmed ({record.get('id', '')})"
    plain = _build_booking_confirmation_plain(record)
    html = _build_booking_confirmation_html(record)
    return send_multichannel_message(
        record,
        subject=subject,
        plain_text=plain,
        html_body=html,
        include_sms=False,
        include_email=True,
        include_terminal=True,
    )


def send_next_turn_notification(record: dict, eta_minutes: int = 0) -> dict:
    plain = _build_next_turn_plain(record, eta_minutes)
    html = _build_next_turn_html(record, eta_minutes)
    return send_multichannel_message(
        record,
        subject="🔔 SmartQueue — Your Turn Is Next",
        plain_text=plain,
        html_body=html,
        include_sms=False,
        include_email=True,
        include_terminal=True,
    )


def send_eta_notification(record: dict, eta_minutes: int) -> dict:
    plain = _build_eta_plain(record, eta_minutes)
    html = _build_eta_html(record, eta_minutes)
    return send_multichannel_message(
        record,
        subject="⏳ SmartQueue — Wait Time Update",
        plain_text=plain,
        html_body=html,
        include_sms=False,
        include_email=True,
        include_terminal=True,
    )


def send_checkin_reminder(record: dict, minutes_before: int = 10) -> dict:
    plain = _build_checkin_reminder_plain(record, minutes_before)
    html = _build_checkin_reminder_html(record, minutes_before)
    return send_multichannel_message(
        record,
        subject="⚡ SmartQueue — Check-in Reminder",
        plain_text=plain,
        html_body=html,
        include_sms=False,
        include_email=True,
        include_terminal=True,
    )


def send_auto_reminder(record: dict, reminder_type: str) -> dict:
    subject_map = {
        "30_min": "📢 SmartQueue — Appointment in 30 Minutes",
        "10_min": "⚠️ SmartQueue — Appointment in 10 Minutes",
        "now": "🚨 SmartQueue — It's Your Turn Now!",
    }
    plain = _build_auto_reminder_plain(record, reminder_type)
    html = _build_auto_reminder_html(record, reminder_type)
    return send_multichannel_message(
        record,
        subject=subject_map.get(reminder_type, "SmartQueue reminder"),
        plain_text=plain,
        html_body=html,
        include_sms=False,
        include_email=True,
        include_terminal=True,
    )


def send_status_update_notification(record: dict, status_type: str) -> dict:
    subject_map = {
        "accepted": "SmartQueue - Admin Check-in Confirmed",
        "completed": "SmartQueue - Visit Completed",
        "missed": "SmartQueue - Appointment Marked Missed",
    }
    plain = _build_status_update_plain(record, status_type)
    html = _build_status_update_html(record, status_type)
    return send_multichannel_message(
        record,
        subject=subject_map.get(status_type, "SmartQueue - Appointment Update"),
        plain_text=plain,
        html_body=html,
        include_sms=False,
        include_email=True,
        include_terminal=True,
    )


