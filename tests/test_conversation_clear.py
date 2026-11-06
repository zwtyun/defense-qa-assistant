"""Manual conversation clearing removes history, not imported knowledge files."""
import asyncio
import json
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from conversation_store import ConversationStore
from server import create_app


class NoSpeech:
    def start_background(self):
        pass


class Runtime:
    def __init__(self):
        self.calls = []
        self.block = False
        self.cancel_observed = threading.Event()
        self.release_worker = threading.Event()
        self.worker_finished = threading.Event()

    def start_background(self):
        pass

    def stop(self):
        self.release_worker.set()

    def status(self):
        return {"provider": "local", "model": "clear-fixture", "ready": True, "status": "ready"}

    def stream_chat(self, messages, max_tokens, temperature, cancel_event):
        self.calls.append(messages)
        if not self.block:
            yield "【现场回答】新的回答按当前资料解释。\n【一句话收束】按资料核对。"
            return
        try:
            yield "【现场回答】此前说过的独有标记：琥珀回声。"
            if cancel_event.wait(3):
                self.cancel_observed.set()
            # Model workers may take time to finish after the browser cancels.
            self.release_worker.wait(3)
            yield "迟到的旧回答：碧空尾音。"
        finally:
            self.worker_finished.set()


def fixture_app(tmp_path, runtime):
    data = tmp_path / "data"
    original = tmp_path / "原始测试材料.txt"
    original.write_text("测量误差拟使用独立参比样本验证。", encoding="utf-8")
    app = create_app(data, NoSpeech(), runtime)
    imported = app.state.store.import_document("原始测试材料.txt", "local-copy.txt", "txt",
        [{"text": original.read_text(encoding="utf-8"), "locator": "第1行"}],
        [{"question": "测量误差如何验证？", "answer": "测量误差拟使用独立参比样本验证。"}])
    copied = app.state.store.documents_dir / imported["stored_name"]
    copied.write_bytes(original.read_bytes())
    return app, original, copied


def library_snapshot(app):
    store = app.state.store
    return (store.all_documents(), store.all_answers(), store.all_chunks(), store.revision)


def test_clear_waits_for_cancelled_worker_then_old_content_cannot_reappear(tmp_path):
    runtime = Runtime()
    runtime.block = True
    app, original, copied = fixture_app(tmp_path, runtime)
    before, original_bytes = library_snapshot(app), original.read_bytes()
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/generate")
    clear = next(route.endpoint for route in app.routes if route.path == "/api/conversation" and "DELETE" in route.methods)

    class Connected:
        async def is_disconnected(self):
            return False

    async def cancel_after_visible_text():
        response = await endpoint(Connected(), {"question": "测量误差如何验证？"})
        stream = response.body_iterator
        async for line in stream:
            if json.loads(line)["type"] == "delta":
                break
        await stream.aclose()

    try:
        asyncio.run(cancel_after_visible_text())
        assert runtime.cancel_observed.wait(1)
        saved = app.state.conversation.all_turns()[0]
        assert saved["status"] == "cancelled" and "琥珀回声" in saved["text"]
        with pytest.raises(HTTPException) as still_working:
            clear()
        assert still_working.value.status_code == 409
        runtime.release_worker.set()
        assert runtime.worker_finished.wait(2)
        deadline = time.monotonic() + 2
        while app.state.generation._busy.locked() and time.monotonic() < deadline:
            time.sleep(.01)
        assert clear() == {"deleted": 1}
        assert app.state.conversation.all_turns() == []
        # Even an old writer's late UPDATE cannot recreate a deleted turn.
        saved["text"] += "碧空尾音"
        app.state.conversation.save(saved)
        assert app.state.conversation.all_turns() == []
        runtime.block = False
        with TestClient(app) as client:
            assert client.post("/api/generate", json={"question": "刚才第二点是什么意思？"}).status_code == 200
            prompt = json.dumps(runtime.calls[-1], ensure_ascii=False)
            assert "琥珀回声" not in prompt and "碧空尾音" not in prompt
            assert len(client.get("/api/conversation").json()["turns"]) == 1
        assert library_snapshot(app) == before
        assert original.read_bytes() == copied.read_bytes() == original_bytes
    finally:
        runtime.release_worker.set()


def test_clear_removes_all_saved_states_and_openings_and_stays_empty_after_restart(tmp_path):
    runtime = Runtime()
    app, original, copied = fixture_app(tmp_path, runtime)
    before, original_bytes = library_snapshot(app), original.read_bytes()
    for number, state in enumerate(("complete", "cancelled", "error", "interrupted")):
        turn = app.state.conversation.start("旧问题" + str(number), opening={"text": "旧开场" + str(number), "ready": True})
        turn.update(text="旧正文" + str(number), status=state, error="历史错误" if state == "error" else "")
        app.state.conversation.save(turn)
    with TestClient(app) as client:
        assert client.delete("/api/conversation").json() == {"deleted": 4}
        assert client.get("/api/conversation").json() == {"turns": [], "busy": False}
        assert client.delete("/api/conversation").json() == {"deleted": 0}
    assert ConversationStore(app.state.store.data_dir).all_turns() == []
    reopened = create_app(app.state.store.data_dir, NoSpeech(), Runtime())
    with TestClient(reopened) as client:
        assert client.get("/api/conversation").json() == {"turns": [], "busy": False}
        assert len(client.get("/api/documents").json()) == 1
        assert len(client.get("/api/answers").json()) == 1
    assert library_snapshot(app) == before
    assert original.read_bytes() == copied.read_bytes() == original_bytes
