"""Persistent conversation turns, kept separately from the imported library."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ConversationStore:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "conversations.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS turns (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    question TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    overview TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    sources TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'local',
                    model TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
            """)
            # A previous process cannot finish its interrupted network/model job.
            db.execute("UPDATE turns SET status='interrupted', updated_at=? WHERE status='generating'", (timestamp(),))

    @contextmanager
    def connection(self):
        with self._lock:
            db = sqlite3.connect(str(self.path), timeout=15)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    @staticmethod
    def _row(row):
        item = dict(row)
        item.pop("sequence", None)
        item["sources"] = json.loads(item["sources"])
        item.update(json.loads(item.pop("metadata")))
        return item

    def all_turns(self):
        with self.connection() as db:
            return [self._row(row) for row in db.execute("SELECT * FROM turns ORDER BY sequence")]

    def start(self, question, provider="local", model="", opening=None):
        now = timestamp()
        turn = {"id": uuid.uuid4().hex, "question": question, "text": "", "overview": "",
                "status": "generating", "sources": [], "created_at": now, "updated_at": now,
                "provider": provider, "model": model, "error": "", "layout": "defense"}
        metadata = {"layout": "defense"}
        if opening is not None:
            turn["opening"] = opening
            metadata["opening"] = opening
        with self.connection() as db:
            db.execute("INSERT INTO turns (id,question,status,created_at,updated_at,provider,model,metadata) VALUES (?,?,?,?,?,?,?,?)",
                       (turn["id"], question, turn["status"], now, now, provider, model, json.dumps(metadata, ensure_ascii=False)))
        return turn

    def save(self, turn):
        turn["updated_at"] = timestamp()
        metadata = {key: turn[key] for key in ("layout", "elapsed_ms", "first_token_ms", "answer_first_token_ms",
                    "usage", "insufficient_evidence", "opening") if key in turn}
        with self.connection() as db:
            db.execute("UPDATE turns SET text=?,overview=?,status=?,sources=?,updated_at=?,provider=?,model=?,error=?,metadata=? WHERE id=?",
                       (turn["text"], turn["overview"], turn["status"], json.dumps(turn["sources"], ensure_ascii=False),
                        turn["updated_at"], turn["provider"], turn["model"], turn["error"],
                        json.dumps(metadata, ensure_ascii=False), turn["id"]))

    def clear(self):
        with self.connection() as db:
            return db.execute("DELETE FROM turns").rowcount


class ConversationWriter:
    """Persist streamed content periodically and always on completion/cancellation."""
    def __init__(self, store, turn):
        self.store, self.turn = store, turn
        self.last_write = time.monotonic()

    def consume(self, event):
        kind = event.get("type")
        if kind == "opening":
            self.turn["opening"] = event.get("opening")
        elif kind == "sources":
            self.turn["sources"] = event.get("sources", [])
        elif kind == "delta":
            self.turn["text"] += str(event.get("text", ""))
        elif kind == "overview_delta":
            self.turn["overview"] += str(event.get("text", ""))
        elif kind == "answer_reset":
            self.turn["text"] = ""
        elif kind == "done":
            self.turn.update(text=str(event.get("text", self.turn["text"])),
                             overview=str(event.get("overview", self.turn["overview"])), status="complete")
            for key in ("provider", "model", "layout", "elapsed_ms", "first_token_ms", "answer_first_token_ms", "usage", "insufficient_evidence", "opening"):
                if key in event:
                    self.turn[key] = event[key]
        elif kind == "error":
            self.turn.update(status="error", error=str(event.get("message", "回答未完成。")))
        if kind in ("opening", "sources", "done", "error") or time.monotonic() - self.last_write >= .5:
            self.store.save(self.turn)
            self.last_write = time.monotonic()

    def finish(self, status="cancelled", error=""):
        if self.turn["status"] == "generating":
            self.turn.update(status=status, error=error)
            self.store.save(self.turn)
