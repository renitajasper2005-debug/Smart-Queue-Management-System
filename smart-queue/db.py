"""
db.py — MongoDB connection with in-memory fallback.
If MongoDB is not running the app still works using a plain Python list.
"""
import os
import uuid
from datetime import datetime

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
USE_MONGO = False
_mem_store: list = []

try:
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
    client.server_info()          # throws if Mongo not reachable
    db = client["smart_queue"]
    collection = db["appointments"]
    USE_MONGO = True
except Exception:
    print("[DB] MongoDB not reachable - using in-memory store.")
else:
    print("[DB] Connected to MongoDB")


# ── helpers ──────────────────────────────────────────────────────────────────

def _new_id() -> str:
    return str(uuid.uuid4())[:8].upper()


def insert_record(record: dict) -> dict:
    record["_id_str"] = _new_id()
    record["created_at"] = datetime.now().isoformat()
    record["status"] = "waiting"
    if USE_MONGO:
        collection.insert_one(record)
        record.pop("_id", None)
    else:
        _mem_store.append(record)
    return record


def get_all() -> list:
    if USE_MONGO:
        rows = list(collection.find({}, {"_id": 0}))
    else:
        rows = list(_mem_store)
    # Ensure every record surfaces its id as "id"
    for r in rows:
        if "id" not in r:
            r["id"] = r.get("_id_str", "?")
    return rows


def update_fields(record_id: str, fields: dict) -> bool:
    if USE_MONGO:
        res = collection.update_one({"_id_str": record_id}, {"$set": fields})
        return res.matched_count > 0
    else:
        for r in _mem_store:
            if r.get("_id_str") == record_id:
                r.update(fields)
                return True
        return False


def update_status(record_id: str, new_status: str) -> bool:
    return update_fields(record_id, {"status": new_status})


def find_by_id(record_id: str) -> dict | None:
    if USE_MONGO:
        r = collection.find_one({"_id_str": record_id}, {"_id": 0})
    else:
        r = next((x for x in _mem_store if x.get("_id_str") == record_id), None)
    if r and "id" not in r:
        r["id"] = r.get("_id_str", "?")
    return r
