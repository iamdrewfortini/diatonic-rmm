"""Minimal multi-tenant ticket, asset, event and knowledge store."""
import json
import sqlite3
import time
import uuid
import threading
from functools import wraps


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


class Store:
    def __init__(self, path=":memory:"):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS records (
          tenant TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
          body TEXT NOT NULL, created INTEGER NOT NULL,
          PRIMARY KEY (tenant,kind,id));
        CREATE TABLE IF NOT EXISTS audit (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL,
          actor TEXT NOT NULL, verb TEXT NOT NULL, kind TEXT NOT NULL,
          record_id TEXT NOT NULL, created INTEGER NOT NULL);
        """)

    @locked
    def create(self, tenant, actor, kind, body):
        if not tenant or not actor or not isinstance(body, dict):
            raise ValueError("tenant, actor and JSON object required")
        if kind not in {"tickets", "endpoints", "events", "documents", "actions", "policies"}:
            raise ValueError("unsupported resource")
        record = {**body, "id": str(uuid.uuid4())}
        if kind == "actions":
            record["state"] = "proposed"
            record["requested_by"] = actor
            record.pop("approved_by", None)
        if kind == "tickets":
            record.setdefault("status", "open")
        now = int(time.time())
        with self.db:
            self.db.execute("INSERT INTO records VALUES (?,?,?,?,?)",
                            (tenant, kind, record["id"], json.dumps(record), now))
            self.db.execute("INSERT INTO audit(tenant,actor,verb,kind,record_id,created) VALUES (?,?,?,?,?,?)",
                            (tenant, actor, "create", kind, record["id"], now))
        return record

    @locked
    def list(self, tenant, kind):
        if not tenant:
            raise ValueError("tenant required")
        return [json.loads(r["body"]) for r in self.db.execute(
            "SELECT body FROM records WHERE tenant=? AND kind=? ORDER BY created DESC LIMIT 500",
            (tenant, kind))]

    @locked
    def get(self, tenant, kind, record_id):
        row = self.db.execute("SELECT body FROM records WHERE tenant=? AND kind=? AND id=?",
                              (tenant, kind, record_id)).fetchone()
        return json.loads(row["body"]) if row else None

    @locked
    def approve(self, tenant, actor, action_id):
        if not tenant or not actor:
            raise ValueError("tenant and actor required")
        record = self.get(tenant, "actions", action_id)
        if not record:
            raise LookupError("action not found")
        if record["state"] != "proposed":
            raise ValueError("action is not proposed")
        if actor == record.get("requested_by"):
            raise ValueError("separation of duties: requester cannot approve")
        record.update(state="approved", approved_by=actor)
        with self.db:
            self.db.execute("UPDATE records SET body=? WHERE tenant=? AND kind='actions' AND id=?",
                            (json.dumps(record), tenant, action_id))
            self.db.execute("INSERT INTO audit(tenant,actor,verb,kind,record_id,created) VALUES (?,?,?,?,?,?)",
                            (tenant, actor, "approve", "actions", action_id, int(time.time())))
        return record

    @locked
    def audit(self, tenant):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM audit WHERE tenant=? ORDER BY seq DESC LIMIT 500", (tenant,))]
