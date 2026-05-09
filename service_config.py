"""
service_config.py - single source of truth for service durations.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_SERVICE_MINUTES = 15
SERVICE_FILE = Path(__file__).with_name("service_catalog.json")

DEFAULT_SERVICE_CATALOG = [
    {"name": "General Consultation", "duration_minutes": 15},
    {"name": "Follow-up", "duration_minutes": 10},
    {"name": "Lab Test", "duration_minutes": 12},
    {"name": "Specialist", "duration_minutes": 20},
    {"name": "Emergency", "duration_minutes": 30},
    {"name": "General", "duration_minutes": 12},
]


def _normalize_services(services: list[dict]) -> list[dict]:
    normalized = []
    for service in services:
        name = str(service.get("name", "")).strip()
        duration = int(service.get("duration_minutes", DEFAULT_SERVICE_MINUTES))
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "duration_minutes": max(1, duration),
            }
        )
    return normalized or list(DEFAULT_SERVICE_CATALOG)


def load_service_catalog() -> list[dict]:
    if not SERVICE_FILE.exists():
        save_service_catalog(DEFAULT_SERVICE_CATALOG)
        return list(DEFAULT_SERVICE_CATALOG)
    try:
        data = json.loads(SERVICE_FILE.read_text(encoding="utf-8"))
    except Exception:
        save_service_catalog(DEFAULT_SERVICE_CATALOG)
        return list(DEFAULT_SERVICE_CATALOG)
    return _normalize_services(data)


def save_service_catalog(services: list[dict]) -> list[dict]:
    normalized = _normalize_services(services)
    SERVICE_FILE.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    return normalized


def get_service_catalog() -> list[dict]:
    return load_service_catalog()


def get_service_duration(service_name: str | None) -> int:
    service_map = {item["name"]: item["duration_minutes"] for item in load_service_catalog()}
    return service_map.get(service_name or "", DEFAULT_SERVICE_MINUTES)
