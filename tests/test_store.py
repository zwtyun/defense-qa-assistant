import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from server import create_app, export_workbook
from store import Store, clean_answer


def row(question="如何验收？"):
    return {"question": question, "answer": "通过第三方检测验证指标。", "keywords": ["验收"], "status": "reference", "sources": []}


def test_persistence_crud_and_batch_atomicity(tmp_path):
    store = Store(tmp_path)
    saved = store.save_answer(row())
    assert Store(tmp_path).get_answer(saved["id"])["answer"] == row()["answer"]
    store.favorite_answer(saved["id"], True)
    assert store.get_answer(saved["id"])["favorite"] is True
    with pytest.raises(ValueError):
        store.import_answers([row("有效"), {"question": "缺回答"}])
    assert store.stats()["answer_count"] == 1
    store.delete_answer(saved["id"])
    assert store.stats()["answer_count"] == 0


def test_delete_document_removes_linked_answer_and_file(tmp_path):
    store = Store(tmp_path)
    doc = store.add_document("原文.pdf", "local.pdf", "pdf", [{"text": "依据原文", "page": 1, "locator": "第1页"}], pages=1)
    (store.documents_dir / "local.pdf").write_bytes(b"pdf")
    data = row()
    data["sources"] = [{"document_id": doc["id"], "filename": "原文.pdf", "locator": "第1页", "page": 1, "quote": "依据原文"}]
    saved = store.save_answer(data)
    result = store.delete_document(doc["id"])
    assert result == {"removed_answers": 1, "removed_chunks": 1, "updated_answers": 0}
    assert store.get_answer(saved["id"]) is None
    assert store.stats()["chunk_count"] == 0
    assert not (store.documents_dir / "local.pdf").exists()


def test_manual_multiple_sources_and_independent_answer(tmp_path):
    store = Store(tmp_path)
    first = store.add_document("第一份.pdf", "first.pdf", "pdf", [])
    second = store.add_document("第二份.docx", "second.docx", "docx", [])
    manual = store.save_answer(row("独立手工答案"))
    combined = store.save_answer(dict(row("两份材料共同支持"), sources=[
        {"document_id": first["id"], "filename": first["filename"], "quote": "证据一"},
        {"document_id": second["id"], "filename": second["filename"], "quote": "证据二"},
    ]))
    assert store.delete_document(first["id"])["updated_answers"] == 1
    retained = store.get_answer(combined["id"])
    assert [s["document_id"] for s in retained["sources"]] == [second["id"]]
    assert store.delete_document(second["id"])["removed_answers"] == 1
    assert store.get_answer(combined["id"]) is None
    assert store.get_answer(manual["id"]) is not None
    assert Store(tmp_path).stats()["answer_count"] == 1


@pytest.mark.parametrize("delete_first", ["original", "copy"])
def test_imported_copy_belongs_to_its_own_file(tmp_path, delete_first):
    store = Store(tmp_path)
    original = store.import_document("原始.docx", "a.docx", "docx", [], [row("原始答案")])
    original_answer = store.all_answers()[0]
    copied = store.import_document("副本.json", "b.json", "json", [], [original_answer])
    copied_answer = next(a for a in store.all_answers() if a["origin_document_id"] == copied["id"])
    if delete_first == "original":
        result = store.delete_document(original["id"])
        assert result["removed_answers"] == 1
        survivor = store.get_answer(copied_answer["id"])
        assert survivor and [s["document_id"] for s in survivor["sources"]] == [copied["id"]]
        store.delete_document(copied["id"])
    else:
        # Editing the citations does not erase the file that imported this row.
        store.save_answer(dict(copied_answer, sources=[]), copied_answer["id"])
        assert store.delete_document(copied["id"])["removed_answers"] == 1
        assert store.get_answer(original_answer["id"]) is not None
        store.delete_document(original["id"])
    assert Store(tmp_path).stats()["answer_count"] == 0


def test_document_and_answer_import_is_atomic(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ValueError):
        store.import_document("不完整.json", "bad.json", "json", [{"text": "不能残留"}],
                              [row(), {"question": "缺少答案"}])
    assert all(store.stats()[key] == 0 for key in ("answer_count", "document_count", "chunk_count"))
    with pytest.raises(ValueError, match="来源文件不存在"):
        store.import_answers([row()], document_id="missing")
    assert store.all_answers() == []


def legacy_database(folder, documents, answers):
    """The pre-1.4 layout, including citations whose deleted IDs were cleared."""
    connection = sqlite3.connect(str(folder / "library.sqlite3"))
    connection.executescript("""
        CREATE TABLE answers (id TEXT PRIMARY KEY,question TEXT NOT NULL,short_answer TEXT NOT NULL,
          answer TEXT NOT NULL,keywords TEXT NOT NULL,aliases TEXT NOT NULL,category TEXT NOT NULL,
          status TEXT NOT NULL,favorite INTEGER NOT NULL,sources TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE documents (id TEXT PRIMARY KEY,filename TEXT NOT NULL,stored_name TEXT NOT NULL,
          kind TEXT NOT NULL,pages INTEGER NOT NULL,warning TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE chunks (id TEXT PRIMARY KEY,document_id TEXT NOT NULL,filename TEXT NOT NULL,
          text TEXT NOT NULL,locator TEXT NOT NULL,page INTEGER,
          FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE);
    """)
    for document_id, filename, text in documents:
        connection.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)",
                           (document_id, filename, document_id + ".docx", "docx", 0, "", "2026-01-01"))
        connection.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)",
                           ("chunk-" + document_id, document_id, filename, text, "正文第 1 段", None))
    for answer_id, data in answers:
        item = clean_answer(data)
        connection.execute("INSERT INTO answers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            answer_id, item["question"], item["short_answer"], item["answer"], json.dumps(item["keywords"]),
            json.dumps(item["aliases"]), item["category"], item["status"], int(item["favorite"]),
            json.dumps(item["sources"], ensure_ascii=False), "2026-01-01", "2026-01-01"))
    connection.commit()
    connection.close()


def test_legacy_orphan_relinks_by_filename_and_quote_not_filename_alone(tmp_path):
    answer = dict(row("旧版遗留答案"), answer="旧文件中明确的验证条件。", sources=[
        {"document_id": "", "filename": "问答.docx", "locator": "正文第 1 段", "quote": "旧文件中明确的验证条件。"}])
    legacy_database(tmp_path, [("same-name-other", "问答.docx", "另一份独立文件。"),
                              ("reimported", "问答.docx", "旧文件中明确的验证条件。")], [("old-answer", answer)])
    store = Store(tmp_path)
    assert store.migration_report["relinked_sources"] == 1
    assert store.get_answer("old-answer")["origin_document_id"] == "reimported"
    store.delete_document("same-name-other")
    assert store.get_answer("old-answer") is not None
    store.delete_document("reimported")
    reopened = Store(tmp_path)
    assert reopened.stats()["answer_count"] == 0
    assert reopened.migration_report["applied"] is False


def test_legacy_deleted_file_answers_removed_but_manual_answers_survive(tmp_path):
    orphan = dict(row("已删文件留下的答案"), sources=[{
        "filename": "已删除.docx", "locator": "正文第 4 段", "quote": row()["answer"], "document_id": ""}])
    manual = row("手动独立答案")
    bibliographic = dict(row("手动参考文献"), sources=[{
        "filename": "论文.pdf", "locator": "参考文献", "quote": "背景知识，不是导入的完整回答"}])
    legacy_database(tmp_path, [], [("orphan", orphan), ("manual", manual), ("citation", bibliographic)])
    store = Store(tmp_path)
    assert store.migration_report["removed_orphan_answers"] == 1
    assert store.get_answer("orphan") is None
    assert {a["id"] for a in store.all_answers()} == {"manual", "citation"}
    # The legacy cleanup runs once, rather than deleting new manual records on reopen.
    fresh = store.save_answer(orphan)
    assert Store(tmp_path).get_answer(fresh["id"]) is not None


def test_api_import_export_filter_and_cache_refresh(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        health = client.get("/api/health").json()
        assert health["application"] == "defense-search"
        first = client.post("/api/answers", json=row()).json()
        assert client.get("/api/search", params={"q": "验收"}).json()["total"] == 1
        client.put("/api/answers/" + first["id"], json={"question": "人员分工", "keywords": ["人员"], "answer": "按参与单位分配职责。", "status": "reviewed"})
        assert client.get("/api/search", params={"q": "验收"}).json()["total"] == 0
        assert client.get("/api/search", params={"q": "分工", "status": "reviewed"}).json()["total"] == 1
        raw = client.get("/api/export?format=json").content
        result = client.post("/api/import", files={"file": ("答案.json", raw, "application/json")})
        assert result.status_code == 200
        assert result.json()["answer_count"] == 1
        assert client.get("/api/health").json()["answer_count"] == 2
        assert client.get("/api/answers/missing").status_code == 404


def test_invalid_upload_and_cross_site_mutation(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/import", files={"file": ("bad.json", b"not json", "application/json")})
        assert response.status_code == 400
        assert list((tmp_path / "documents").iterdir()) == []
        response = client.post("/api/answers", json=row(), headers={"Origin": "https://evil.example"})
        assert response.status_code == 403
        assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403


def test_xlsx_round_trip_and_formula_text(tmp_path):
    sample = row("=SUM(1,2)")
    raw = export_workbook([sample])
    workbook = load_workbook(io.BytesIO(raw), data_only=False)
    assert workbook.active["A2"].data_type == "s"
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/import", files={"file": ("answers.xlsx", raw)})
        assert response.status_code == 200, response.text
        assert client.get("/api/answers").json()[0]["question"] == "=SUM(1,2)"
        invalid = json.dumps({"answers": [row("ok"), {"question": "无回答"}]}, ensure_ascii=False).encode()
        response = client.post("/api/import", files={"file": ("bad.json", invalid)})
        assert response.status_code == 400
        assert client.get("/api/health").json()["answer_count"] == 1
