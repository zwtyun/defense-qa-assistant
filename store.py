"""SQLite persistence for the offline defense library (Python 3.9+)."""
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def strings(value):
    if isinstance(value, str):
        return [v.strip() for v in value.replace("；", ";").replace("，", ",").replace("\n", ";").replace(",", ";").split(";") if v.strip()]
    if value is not None and not isinstance(value, (list, tuple)):
        raise ValueError("关键词和常见问法应为列表或分号分隔的文本")
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def clean_answer(data):
    question = str(data.get("question", "")).strip()
    answer = str(data.get("answer", "")).strip()
    if not question:
        raise ValueError("请输入问题标题")
    if not answer:
        raise ValueError("请输入详细回答")
    if len(question) > 1000 or len(answer) > 100000:
        raise ValueError("问题或回答过长")
    status = data.get("status", "reference")
    if status not in ("reference", "reviewed", "draft"):
        raise ValueError("无效的审核状态")
    sources = data.get("sources") or []
    if not isinstance(sources, list) or any(not isinstance(s, dict) for s in sources):
        raise ValueError("出处必须为列表")
    normalized_sources = []
    for source in sources:
        page = source.get("page")
        if page not in (None, ""):
            try:
                page = int(page)
            except (ValueError, TypeError):
                raise ValueError("出处页序必须为正整数")
            if page < 1:
                raise ValueError("出处页序从 1 开始")
        normalized_sources.append({
            "document_id": str(source.get("document_id") or ""),
            "filename": str(source.get("filename") or ""),
            "locator": str(source.get("locator") or ""),
            "page": page or None,
            "quote": str(source.get("quote") or ""),
        })
    return {
        "question": question,
        "short_answer": str(data.get("short_answer") or "").strip(),
        "answer": answer,
        "keywords": strings(data.get("keywords")),
        "aliases": strings(data.get("aliases")),
        "category": str(data.get("category") or "未分类").strip(),
        "status": status,
        "favorite": bool(data.get("favorite", False)),
        "sources": normalized_sources,
    }


class Store:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir = self.data_dir / "documents"
        self.documents_dir.mkdir(exist_ok=True)
        self.path = self.data_dir / "library.sqlite3"
        self._lock = threading.RLock()
        self.revision = 0
        with self.connection() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS answers (
                    id TEXT PRIMARY KEY, question TEXT NOT NULL,
                    short_answer TEXT NOT NULL, answer TEXT NOT NULL,
                    keywords TEXT NOT NULL, aliases TEXT NOT NULL,
                    category TEXT NOT NULL, status TEXT NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    sources TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                    stored_name TEXT NOT NULL, kind TEXT NOT NULL,
                    pages INTEGER NOT NULL DEFAULT 0,
                    warning TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    filename TEXT NOT NULL, text TEXT NOT NULL,
                    locator TEXT NOT NULL, page INTEGER,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
            """)
            self.migration_report = self._migrate_document_ownership(connection)

    @staticmethod
    def _source_matcher(connection):
        documents = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM documents")}
        texts = {key: [] for key in documents}
        for chunk in connection.execute("SELECT document_id,text FROM chunks ORDER BY rowid"):
            texts[chunk["document_id"]].append(chunk["text"])
        texts = {key: "".join("\n".join(parts).split()) for key, parts in texts.items()}

        def match(source):
            if source.get("document_id") in documents:
                return [source["document_id"]]
            filename = Path(str(source.get("filename") or "").replace("\\", "/")).name.casefold()
            quote = "".join(str(source.get("quote") or "").split())
            if not filename or not quote:
                return []
            return [key for key, document in documents.items()
                    if document["filename"].casefold() == filename and quote in texts[key]]
        return match

    @staticmethod
    def _legacy_import_source(source, answer):
        # Old document extraction recorded the answer prefix at a parser locator.
        # An arbitrary manual bibliographic citation is not enough to delete a row.
        quote = "".join(str(source.get("quote") or "").split())
        text = "".join(str(answer or "").split())
        locator = str(source.get("locator") or "")
        return (Path(str(source.get("filename") or "")).suffix.lower() in (".docx", ".pdf", ".txt", ".md")
                and bool(re.match(r"^(?:正文第\s*\d+\s*段|表格\s*\d+|PDF 第\s*\d+\s*页|第\s*\d+\s*行)", locator))
                and bool(quote) and text.startswith(quote))

    def _migrate_document_ownership(self, connection):
        report = {"applied": False, "relinked_sources": 0, "removed_orphan_answers": 0}
        if "origin_document_id" in {row["name"] for row in connection.execute("PRAGMA table_info(answers)")}:
            return report
        # Schema and legacy row repair commit together; a failed migration can retry.
        connection.execute("BEGIN")
        connection.execute("ALTER TABLE answers ADD COLUMN origin_document_id TEXT REFERENCES documents(id) ON DELETE CASCADE")
        connection.execute("CREATE INDEX answers_origin_document ON answers(origin_document_id)")
        match = self._source_matcher(connection)
        for row in connection.execute("SELECT id,answer,sources FROM answers").fetchall():
            sources, linked, imported = [], set(), False
            for source in json.loads(row["sources"]):
                matches = match(source)
                legacy_import = self._legacy_import_source(source, row["answer"])
                imported = imported or legacy_import
                if matches:
                    for document_id in matches:
                        sources.append(dict(source, document_id=document_id))
                        linked.add(document_id)
                        if source.get("document_id") != document_id:
                            report["relinked_sources"] += 1
                elif not legacy_import:
                    sources.append(dict(source, document_id=""))
            if imported and not linked:
                connection.execute("DELETE FROM answers WHERE id=?", (row["id"],))
                report["removed_orphan_answers"] += 1
                continue
            origin = next(iter(linked)) if imported and len(linked) == 1 else None
            connection.execute("UPDATE answers SET sources=?,origin_document_id=? WHERE id=?",
                               (json.dumps(sources, ensure_ascii=False), origin, row["id"]))
        report["applied"] = True
        return report

    @staticmethod
    def _linked_sources(item, connection):
        documents = {row["id"] for row in connection.execute("SELECT id FROM documents")}
        for source in item["sources"]:
            if source.get("document_id") and source["document_id"] not in documents:
                source["document_id"] = ""
        return item

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(str(self.path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def answer_row(row):
        if row is None:
            return None
        item = dict(row)
        for key in ("keywords", "aliases", "sources"):
            item[key] = json.loads(item[key])
        item["favorite"] = bool(item["favorite"])
        for source in item["sources"]:
            if source.get("document_id"):
                source["source_url"] = "/api/documents/{}/file".format(source["document_id"])
        return item

    def all_answers(self):
        with self.connection() as connection:
            return [self.answer_row(r) for r in connection.execute("SELECT * FROM answers ORDER BY favorite DESC, updated_at DESC, id")]

    def get_answer(self, answer_id):
        with self.connection() as connection:
            return self.answer_row(connection.execute("SELECT * FROM answers WHERE id=?", (answer_id,)).fetchone())

    def save_answer(self, data, answer_id=None):
        item = clean_answer(data)
        now = timestamp()
        answer_id = answer_id or uuid.uuid4().hex
        with self._lock, self.connection() as connection:
            self._linked_sources(item, connection)
            existing = connection.execute("SELECT created_at FROM answers WHERE id=?", (answer_id,)).fetchone()
            created = existing["created_at"] if existing else now
            connection.execute("""INSERT INTO answers
                (id,question,short_answer,answer,keywords,aliases,category,status,favorite,sources,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET question=excluded.question,
                short_answer=excluded.short_answer,answer=excluded.answer,keywords=excluded.keywords,
                aliases=excluded.aliases,category=excluded.category,status=excluded.status,
                favorite=excluded.favorite,sources=excluded.sources,updated_at=excluded.updated_at""",
                (answer_id, item["question"], item["short_answer"], item["answer"],
                 json.dumps(item["keywords"], ensure_ascii=False), json.dumps(item["aliases"], ensure_ascii=False),
                 item["category"], item["status"], int(item["favorite"]),
                 json.dumps(item["sources"], ensure_ascii=False), created, now))
            self.revision += 1
        return self.get_answer(answer_id)

    def _insert_answers(self, connection, prepared, document_id=None):
        document = None
        if document_id is not None:
            document = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if document is None:
                raise ValueError("导入来源文件不存在")
        inserted, now = [], timestamp()
        for number, item in enumerate(prepared, 1):
            self._linked_sources(item, connection)
            if document is not None and not any(source.get("document_id") == document_id for source in item["sources"]):
                item["sources"].append({"document_id": document_id, "filename": document["filename"],
                                        "locator": "导入答案第 {} 条".format(number), "page": None,
                                        "quote": item["answer"][:1000]})
            answer_id = uuid.uuid4().hex
            connection.execute("""INSERT INTO answers
                (id,question,short_answer,answer,keywords,aliases,category,status,favorite,sources,created_at,updated_at,origin_document_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                answer_id, item["question"], item["short_answer"], item["answer"],
                json.dumps(item["keywords"], ensure_ascii=False), json.dumps(item["aliases"], ensure_ascii=False),
                item["category"], item["status"], int(item["favorite"]),
                json.dumps(item["sources"], ensure_ascii=False), now, now, document_id))
            inserted.append(answer_id)
        return inserted

    def import_answers(self, answers, document_id=None):
        # Validate all rows before writing, then use one transaction.
        prepared = [clean_answer(a) for a in answers]
        with self._lock, self.connection() as connection:
            inserted = self._insert_answers(connection, prepared, document_id)
            self.revision += 1
        return inserted

    def import_document(self, filename, stored_name, kind, chunks, answers=(), pages=0, warning="", document_id=None):
        prepared = [clean_answer(answer) for answer in answers]
        document_id = document_id or uuid.uuid4().hex
        with self._lock, self.connection() as connection:
            self._insert_document(connection, document_id, filename, stored_name, kind, chunks, pages, warning)
            self._insert_answers(connection, prepared, document_id)
            self.revision += 1
        return self.get_document(document_id)

    def favorite_answer(self, answer_id, favorite):
        with self._lock, self.connection() as connection:
            cursor = connection.execute("UPDATE answers SET favorite=?,updated_at=? WHERE id=?", (int(favorite), timestamp(), answer_id))
            self.revision += 1
            return cursor.rowcount > 0

    def delete_answer(self, answer_id):
        with self._lock, self.connection() as connection:
            cursor = connection.execute("DELETE FROM answers WHERE id=?", (answer_id,))
            self.revision += 1
            return cursor.rowcount > 0

    def add_document(self, filename, stored_name, kind, chunks, pages=0, warning="", document_id=None):
        document_id = document_id or uuid.uuid4().hex
        with self._lock, self.connection() as connection:
            self._insert_document(connection, document_id, filename, stored_name, kind, chunks, pages, warning)
            self.revision += 1
        return self.get_document(document_id)

    @staticmethod
    def _insert_document(connection, document_id, filename, stored_name, kind, chunks, pages, warning):
        connection.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", (document_id, filename, stored_name, kind, pages, warning, timestamp()))
        for chunk in chunks:
            text = str(chunk.get("text") or "").strip()
            if text:
                connection.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)", (
                    uuid.uuid4().hex, document_id, filename, text,
                    str(chunk.get("locator") or "原文"), chunk.get("page")))

    def all_documents(self):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute("SELECT d.*, COUNT(c.id) AS chunk_count FROM documents d LEFT JOIN chunks c ON c.document_id=d.id GROUP BY d.id ORDER BY d.created_at DESC")]

    def get_document(self, document_id):
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            return dict(row) if row else None

    def all_chunks(self, document_id=None):
        with self.connection() as connection:
            if document_id:
                return [dict(row) for row in connection.execute("SELECT * FROM chunks WHERE document_id=? ORDER BY rowid", (document_id,))]
            return [dict(row) for row in connection.execute("SELECT * FROM chunks ORDER BY rowid")]

    def delete_document(self, document_id):
        result = self.delete_documents([document_id])
        if not result["removed_documents"]:
            return False
        removed = {key: result[key] for key in ("removed_answers", "removed_chunks", "updated_answers")}
        if result["cleanup_warnings"]:
            removed["cleanup_warnings"] = result["cleanup_warnings"]
        return removed

    def delete_documents(self, document_ids):
        if not isinstance(document_ids, list) or not document_ids:
            raise ValueError("请至少选择一个导入文件")
        if any(not isinstance(value, str) or not value.strip() for value in document_ids):
            raise ValueError("文件编号必须为非空文本")
        requested = list(dict.fromkeys(value.strip() for value in document_ids))
        with self._lock, self.connection() as connection:
            # Resolve the exact selection and all its dependent rows in one
            # write transaction. A newly imported, unselected file is retained.
            connection.execute("BEGIN IMMEDIATE")
            documents = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM documents")}
            deleted = [value for value in requested if value in documents]
            missing = [value for value in requested if value not in documents]
            selected = set(deleted)
            live_documents = set(documents) - selected
            removed_chunks = sum(row["count"] for row in connection.execute(
                "SELECT document_id,COUNT(*) AS count FROM chunks GROUP BY document_id")
                if row["document_id"] in selected)
            removed_answers = updated_answers = 0
            for row in connection.execute("SELECT id,sources,origin_document_id FROM answers").fetchall():
                sources = json.loads(row["sources"])
                remaining = [source for source in sources if source.get("document_id") not in selected]
                changed = len(remaining) != len(sources)
                owned_here = row["origin_document_id"] in selected
                independently_owned = row["origin_document_id"] in live_documents
                another_source = any(source.get("document_id") in live_documents for source in remaining)
                if owned_here or (changed and not independently_owned and not another_source):
                    connection.execute("DELETE FROM answers WHERE id=?", (row["id"],))
                    removed_answers += 1
                elif changed:
                    connection.execute("UPDATE answers SET sources=?,updated_at=? WHERE id=?",
                                       (json.dumps(remaining, ensure_ascii=False), timestamp(), row["id"]))
                    updated_answers += 1
            connection.executemany("DELETE FROM documents WHERE id=?", ((value,) for value in deleted))
            if deleted:
                self.revision += 1
        # Database removal has committed. A locked Windows copy must not stop
        # cleanup of the other selected copies or be reported as fully removed.
        retained_paths = {(self.documents_dir / documents[value]["stored_name"]).resolve() for value in live_documents}
        warnings = []
        for value in deleted:
            document = documents[value]
            path = (self.documents_dir / document["stored_name"]).resolve()
            if path in retained_paths:
                continue
            if path.parent != self.documents_dir:
                warnings.append({"filename": document["filename"], "message": "应用副本路径无效，未操作该路径"})
                continue
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                warnings.append({"filename": document["filename"], "message": "应用副本可能正被占用，未能清理"})
        return {"deleted_document_ids": deleted, "missing_document_ids": missing,
                "removed_documents": len(deleted), "removed_answers": removed_answers,
                "removed_chunks": removed_chunks, "updated_answers": updated_answers,
                "cleanup_warnings": warnings}

    def stats(self):
        with self.connection() as connection:
            counts = {"answer_count": connection.execute("SELECT COUNT(*) FROM answers").fetchone()[0],
                      "document_count": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                      "chunk_count": connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                      "reviewed_count": connection.execute("SELECT COUNT(*) FROM answers WHERE status='reviewed'").fetchone()[0]}
            counts["categories"] = [r[0] for r in connection.execute("SELECT DISTINCT category FROM answers ORDER BY category")]
        return counts
