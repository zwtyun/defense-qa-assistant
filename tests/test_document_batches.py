"""Batch deletion changes only the explicitly selected imported documents."""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import create_app
from store import Store


def answer(question, sources=()):
    return {"question": question, "answer": question + "的临时验收回答。", "sources": list(sources)}


def source(document):
    return {"document_id": document["id"], "filename": document["filename"], "quote": "测试出处"}


def document(store, name, answers=()):
    result = store.import_document(name + ".json", name + ".json", "json", [{"text": name + "原文"}], answers)
    (store.documents_dir / result["stored_name"]).write_text(name, encoding="utf-8")
    return result


def test_batch_deletes_selected_rows_once_and_keeps_independent_copies(tmp_path):
    original_file = tmp_path / "用户的原始文件.json"
    original_file.write_text("用户原件", encoding="utf-8")
    store = Store(tmp_path / "app-data")
    original = document(store, "原始导入", [answer("原始问题")])
    original_answer = store.all_answers()[0]
    copied = document(store, "独立导入副本", [original_answer])
    extra = document(store, "另一个选中文件", [answer("第二个问题")])
    remaining = document(store, "未选资料")
    manual = store.save_answer(answer("独立手工答案"))
    combined = store.save_answer(answer("多份材料支持", [source(original), source(extra), source(remaining)]))
    removed_combined = store.save_answer(answer("仅由选中材料支持", [source(original), source(extra)]))
    revision = store.revision

    result = store.delete_documents([original["id"], extra["id"], original["id"], "already-deleted"])

    assert result["deleted_document_ids"] == [original["id"], extra["id"]]
    assert result["missing_document_ids"] == ["already-deleted"]
    assert result["removed_documents"] == 2
    assert result["removed_answers"] == 3
    assert result["updated_answers"] == 2
    assert result["removed_chunks"] == 2
    assert store.revision == revision + 1
    reopened = Store(store.data_dir)
    assert {row["id"] for row in reopened.all_documents()} == {copied["id"], remaining["id"]}
    assert reopened.get_answer(original_answer["id"]) is None
    assert reopened.get_answer(removed_combined["id"]) is None
    assert reopened.get_answer(manual["id"]) is not None
    assert [item["document_id"] for item in reopened.get_answer(combined["id"])["sources"]] == [remaining["id"]]
    survivor = next(row for row in reopened.all_answers() if row["origin_document_id"] == copied["id"])
    assert [item["document_id"] for item in survivor["sources"]] == [copied["id"]]
    assert {path.name for path in store.documents_dir.iterdir()} == {copied["stored_name"], remaining["stored_name"]}
    assert original_file.read_text(encoding="utf-8") == "用户原件"


def test_batch_all_keeps_manual_answers_and_repeated_request_is_noop(tmp_path):
    store = Store(tmp_path)
    selected = [document(store, name, [answer(name)]) for name in ("甲", "乙")]
    manual = store.save_answer(answer("独立手工答案"))
    ids = [item["id"] for item in selected]
    assert store.delete_documents(ids)["removed_documents"] == 2
    assert store.all_documents() == [] and store.all_chunks() == []
    assert [item["id"] for item in store.all_answers()] == [manual["id"]]
    revision = store.revision
    repeat = store.delete_documents(ids)
    assert repeat["removed_documents"] == 0 and repeat["missing_document_ids"] == ids
    assert store.revision == revision


def test_database_failure_rolls_back_entire_batch_and_keeps_copies(tmp_path):
    store = Store(tmp_path)
    selected = [document(store, name, [answer(name)]) for name in ("甲", "乙")]
    before = store.stats()
    revision = store.revision
    with store.connection() as db:
        db.execute("""CREATE TRIGGER reject_test_delete BEFORE DELETE ON documents
            WHEN OLD.id='{}' BEGIN SELECT RAISE(ABORT, 'test rollback'); END""".format(selected[1]["id"]))
    with pytest.raises(sqlite3.IntegrityError, match="test rollback"):
        store.delete_documents([item["id"] for item in selected])
    assert store.stats() == before
    assert store.revision == revision
    assert all((store.documents_dir / item["stored_name"]).is_file() for item in selected)


@pytest.mark.parametrize("ids", [None, [], "all", [""], [None], [123]])
def test_invalid_selection_does_not_delete_anything(tmp_path, ids):
    store = Store(tmp_path)
    selected = document(store, "保留", [answer("保留")])
    revision = store.revision
    with pytest.raises(ValueError):
        store.delete_documents(ids)
    assert store.get_document(selected["id"]) is not None
    assert store.revision == revision


def test_locked_copy_reports_warning_and_other_copies_are_still_removed(tmp_path, monkeypatch):
    store = Store(tmp_path)
    first, second = [document(store, name, [answer(name)]) for name in ("占用", "可删除")]
    unlink = Path.unlink

    def locked(path, *args, **kwargs):
        if path == store.documents_dir / first["stored_name"]:
            raise PermissionError("test locked file")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    result = store.delete_documents([first["id"], second["id"]])
    assert result["removed_documents"] == 2
    assert result["cleanup_warnings"] == [{"filename": first["filename"], "message": "应用副本可能正被占用，未能清理"}]
    assert store.all_documents() == [] and store.all_answers() == []
    assert (store.documents_dir / first["stored_name"]).is_file()
    assert not (store.documents_dir / second["stored_name"]).exists()


def test_deletion_never_follows_stored_path_outside_import_directory(tmp_path):
    store = Store(tmp_path / "app-data")
    original = store.data_dir / "outside.txt"
    original.write_text("不能删除原件", encoding="utf-8")
    item = store.add_document("异常记录.txt", "../outside.txt", "txt", [])
    result = store.delete_documents([item["id"]])
    assert result["cleanup_warnings"]
    assert original.read_text(encoding="utf-8") == "不能删除原件"


class NoSpeech:
    def start_background(self):
        pass


class NoModel:
    def start_background(self):
        pass

    def stop(self):
        pass

    def status(self):
        return {"provider": "local", "model": "batch-test", "ready": True, "status": "ready"}

    def stream_chat(self, **kwargs):
        raise AssertionError("Empty library must not generate from old conversation")


def upload(client, name):
    raw = json.dumps([answer(name)], ensure_ascii=False).encode("utf-8")
    response = client.post("/api/import", files={"file": (name + ".json", raw)})
    response.raise_for_status()
    return response.json()["document_id"]


def test_batch_api_refreshes_once_preserves_chat_and_does_not_implicitly_delete_new_imports(tmp_path, monkeypatch):
    app = create_app(tmp_path, NoSpeech(), NoModel())
    with TestClient(app) as client:
        first, second = [upload(client, name) for name in ("旧验证甲", "旧验证乙")]
        selection = [first, second]
        newer = upload(client, "确认选择之后的新导入")
        conversation = app.state.conversation
        turn = conversation.start("之前问过的旧验证甲", opening={"text": "以前的开场", "ready": True})
        turn.update(text="以前的模型回答", status="complete")
        conversation.save(turn)
        saved_chat = client.get("/api/conversation").json()["turns"]
        warmup = app.state.generation.warmup_index
        calls = []

        def counted_warmup():
            calls.append(True)
            return warmup()

        monkeypatch.setattr(app.state.generation, "warmup_index", counted_warmup)
        rejected = client.post("/api/documents/delete-batch", json={"document_ids": selection}, headers={"Origin": "https://external.example"})
        assert rejected.status_code == 403
        assert client.post("/api/documents/delete-batch", json={"document_ids": []}).status_code == 400
        assert calls == []
        removed = client.post("/api/documents/delete-batch", json={"document_ids": selection + [first, "missing"]})
        assert removed.status_code == 200, removed.text
        assert removed.json()["removed_documents"] == 2
        assert removed.json()["missing_document_ids"] == ["missing"]
        assert len(calls) == 1
        assert [item["id"] for item in client.get("/api/documents").json()] == [newer]
        assert client.get("/api/search", params={"q": "旧验证甲"}).json()["results"] == []
        assert client.get("/api/conversation").json()["turns"] == saved_chat
        for deleted in selection:
            assert client.get("/api/documents/" + deleted + "/file").status_code == 404
        assert client.post("/api/documents/delete-batch", json={"document_ids": selection}).json()["removed_documents"] == 0
        assert len(calls) == 1
        assert client.post("/api/documents/delete-batch", json={"document_ids": [newer]}).json()["removed_documents"] == 1
        assert len(calls) == 2
        assert all(client.get("/api/health").json()[key] == 0 for key in ("document_count", "answer_count", "chunk_count"))
        assert client.get("/api/conversation").json()["turns"] == saved_chat
        empty = client.post("/api/generate", json={"question": "那刚刚的回答呢？"})
        assert empty.status_code == 409 and "资料库为空" in empty.text
        assert list((tmp_path / "documents").iterdir()) == []
    with TestClient(create_app(tmp_path, NoSpeech(), NoModel())) as reopened:
        assert reopened.get("/api/documents").json() == []
        assert reopened.get("/api/conversation").json()["turns"] == saved_chat
