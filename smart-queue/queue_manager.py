"""
queue_manager.py - queue prediction, no-show handling, and walk-in gap filling.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from db import get_all, update_fields, update_status
from service_config import DEFAULT_SERVICE_MINUTES, get_service_duration

NO_SHOW_GRACE_MINUTES = 10
MIN_GAP_FOR_WALKIN_MINUTES = 8


def _now() -> datetime:
    return datetime.now()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _scheduled_dt(record: dict) -> datetime | None:
    if record.get('type') != 'appointment':
        return None
    date_value = record.get('date')
    time_value = record.get('time')
    if not date_value or not time_value or date_value == 'Walk-in' or time_value == 'Walk-in':
        return None
    try:
        return datetime.fromisoformat(f'{date_value}T{time_value}')
    except ValueError:
        return None


def _sort_key(record: dict) -> tuple:
    predicted = _parse_iso(record.get('predicted_start'))
    scheduled = _scheduled_dt(record)
    created = _parse_iso(record.get('created_at')) or datetime.max
    promoted = _parse_iso(record.get('promoted_at'))
    return (
        predicted or scheduled or promoted or created,
        0 if record.get('type') == 'appointment' else 1,
        created,
    )


def get_expected_duration(record: dict) -> int:
    stored = record.get('expected_duration_minutes')
    if isinstance(stored, int) and stored > 0:
        return stored
    return get_service_duration(record.get('service'))


def _minutes_between(start_iso: str | None, end_iso: str | None) -> int | None:
    start_dt = _parse_iso(start_iso)
    end_dt = _parse_iso(end_iso)
    if not start_dt or not end_dt:
        return None
    seconds = max((end_dt - start_dt).total_seconds(), 0)
    return max(1, round(seconds / 60))


def estimate_service_minutes(records: list[dict]) -> int:
    durations = []
    for record in records:
        duration = _minutes_between(record.get('arrived_at'), record.get('completed_at'))
        if duration is not None:
            durations.append(duration)
    if not durations:
        return DEFAULT_SERVICE_MINUTES
    return round(sum(durations) / len(durations))


def auto_mark_no_shows() -> list[str]:
    now = _now()
    marked = []
    for record in get_all():
        if record.get('type') != 'appointment' or record.get('status') != 'waiting':
            continue
        scheduled = _scheduled_dt(record)
        if not scheduled:
            continue
        grace_deadline = scheduled + timedelta(minutes=NO_SHOW_GRACE_MINUTES)
        if now > grace_deadline:
            update_fields(
                record['id'],
                {
                    'status': 'missed',
                    'missed_at': now.isoformat(),
                    'gap_filled': record.get('gap_filled', False),
                },
            )
            marked.append(record['id'])
    return marked


def _promote_walkin(walkin: dict, reason: str, slot_time: datetime) -> None:
    update_fields(
        walkin['id'],
        {
            'promoted_into_gap': True,
            'gap_reason': reason,
            'promoted_at': _now().isoformat(),
            'predicted_start': slot_time.isoformat(),
            'expected_duration_minutes': get_expected_duration(walkin),
        },
    )


def _fill_missed_appointment_gaps(records: list[dict]) -> list[str]:
    waiting_walkins = [r for r in records if r.get('status') == 'waiting' and r.get('type') == 'walkin' and not r.get('promoted_into_gap')]
    waiting_walkins.sort(key=lambda r: _parse_iso(r.get('created_at')) or datetime.max)

    missed_appts = [
        r for r in records
        if r.get('status') == 'missed' and r.get('type') == 'appointment' and not r.get('gap_filled')
    ]
    missed_appts.sort(key=lambda r: _scheduled_dt(r) or datetime.max)

    promoted = []
    for appointment in missed_appts:
        if not waiting_walkins:
            break
        walkin = waiting_walkins.pop(0)
        slot_time = _scheduled_dt(appointment) or _now()
        _promote_walkin(walkin, 'missed_appointment', slot_time)
        update_fields(appointment['id'], {'gap_filled': True})
        promoted.append(walkin['id'])
    return promoted


def _fill_early_finish_gap(records: list[dict]) -> list[str]:
    waiting_walkins = [r for r in records if r.get('status') == 'waiting' and r.get('type') == 'walkin' and not r.get('promoted_into_gap')]
    waiting_walkins.sort(key=lambda r: _parse_iso(r.get('created_at')) or datetime.max)
    if not waiting_walkins:
        return []

    completed = [r for r in records if r.get('status') == 'completed' and r.get('completed_at')]
    if not completed:
        return []
    last_completed = max(completed, key=lambda r: _parse_iso(r.get('completed_at')) or datetime.min)
    completed_at = _parse_iso(last_completed.get('completed_at'))
    if not completed_at:
        return []

    upcoming_appts = [
        r for r in records
        if r.get('status') == 'waiting' and r.get('type') == 'appointment' and not r.get('promoted_into_gap')
    ]
    upcoming_appts.sort(key=lambda r: _scheduled_dt(r) or datetime.max)
    next_appt = upcoming_appts[0] if upcoming_appts else None
    next_appt_time = _scheduled_dt(next_appt) if next_appt else None

    if next_appt_time:
        gap_minutes = int((next_appt_time - completed_at).total_seconds() // 60)
    else:
        gap_minutes = get_expected_duration(waiting_walkins[0])

    if gap_minutes < MIN_GAP_FOR_WALKIN_MINUTES:
        return []

    for walkin in waiting_walkins:
        if get_expected_duration(walkin) <= gap_minutes:
            _promote_walkin(walkin, 'early_finish', completed_at)
            return [walkin['id']]
    return []


def auto_fill_gaps() -> list[str]:
    auto_mark_no_shows()
    records = get_all()
    promoted = []
    promoted.extend(_fill_missed_appointment_gaps(records))
    records = get_all()
    promoted.extend(_fill_early_finish_gap(records))
    return promoted


def rebuild_predictions() -> dict:
    auto_mark_no_shows()
    auto_fill_gaps()
    records = get_all()
    average_service_minutes = estimate_service_minutes(records)

    waiting = [r for r in records if r.get('status') == 'waiting']
    waiting.sort(key=_sort_key)
    arrived = [r for r in records if r.get('status') == 'arrived']
    arrived.sort(key=_sort_key)
    completed = [r for r in records if r.get('status') == 'completed']
    completed.sort(key=lambda r: _parse_iso(r.get('completed_at')) or datetime.min, reverse=True)
    missed = [r for r in records if r.get('status') == 'missed']
    missed.sort(key=_sort_key)

    current_start = _now()
    arrived_in_service = arrived[0] if arrived else None
    if arrived_in_service and arrived_in_service.get('arrived_at'):
        arrived_at = _parse_iso(arrived_in_service.get('arrived_at')) or current_start
        current_start = max(arrived_at, current_start)
    elif completed:
        last_completed_time = _parse_iso(completed[0].get('completed_at'))
        if last_completed_time:
            current_start = max(last_completed_time, current_start)

    position_map = {}
    for index, record in enumerate(waiting, start=1):
        scheduled = _scheduled_dt(record)
        preferred_start = max(current_start, scheduled) if scheduled else current_start
        if record.get('promoted_into_gap') and record.get('predicted_start'):
            preferred_start = _parse_iso(record.get('predicted_start')) or preferred_start
        predicted_start = preferred_start
        eta_minutes = max(0, round((predicted_start - _now()).total_seconds() / 60))
        expected_duration = get_expected_duration(record)
        record['predicted_start'] = predicted_start.isoformat()
        record['estimated_wait_minutes'] = eta_minutes
        record['service_duration_minutes'] = expected_duration
        position_map[record['id']] = index
        current_start = predicted_start + timedelta(minutes=expected_duration)
        update_fields(
            record['id'],
            {
                'predicted_start': record['predicted_start'],
                'expected_duration_minutes': expected_duration,
            },
        )

    next_up = waiting[0] if waiting else None
    next_up_summary = None
    if next_up:
        next_up_summary = {
            'id': next_up['id'],
            'name': next_up.get('name', ''),
            'service': next_up.get('service', ''),
            'phone': next_up.get('phone', ''),
            'estimated_wait_minutes': next_up.get('estimated_wait_minutes', 0),
            'notification_state': next_up.get('notification_state', 'pending'),
            'last_notified_at': next_up.get('last_notified_at'),
            'last_notification_channel': next_up.get('last_notification_channel'),
            'predicted_start': next_up.get('predicted_start'),
            'gap_reason': next_up.get('gap_reason'),
            'service_duration_minutes': next_up.get('service_duration_minutes'),
        }

    return {
        'waiting': waiting,
        'arrived': arrived,
        'completed': completed,
        'missed': missed,
        'total': len(records),
        'position_map': position_map,
        'average_service_minutes': average_service_minutes,
        'next_up': next_up_summary,
    }


def get_queue_summary() -> dict:
    return rebuild_predictions()


def get_next_waiting_record(records: list[dict] | None = None) -> dict | None:
    if records is None:
        records = rebuild_predictions()['waiting']
    waiting = [r for r in records if r.get('status') == 'waiting']
    waiting.sort(key=_sort_key)
    return waiting[0] if waiting else None


def get_wait_metrics(record_id: str) -> dict | None:
    summary = rebuild_predictions()
    for record in summary['waiting']:
        if record.get('id') == record_id:
            return {
                'position': summary['position_map'].get(record_id, 0),
                'estimated_wait_minutes': record.get('estimated_wait_minutes', 0),
                'average_service_minutes': summary['average_service_minutes'],
                'record': record,
            }
    return None


def mark_missed(record_id: str) -> bool:
    return update_status(record_id, 'missed')
