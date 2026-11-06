"""Exercise the API adapter against local HTTP/SSE, never a paid remote API."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import httpx
import pytest

from cloud_runtime import CloudError, CloudRuntime


TEST_KEY = "sk-test-not-a-real-key"


def sse(item, event_name="", multiline=False):
    data = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, indent=2 if multiline else None)
    prefix = "event: " + event_name + "\n" if event_name else ""
    return (prefix + "".join("data: " + line + "\n" for line in data.splitlines()) + "\n").encode("utf-8")


def chat(text="", reason=None, **extra):
    return sse({"choices": [{"delta": {"content": text}, "finish_reason": reason}], **extra})


def responses(text="完整回答。", usage=None):
    return sse({"type": "response.output_text.delta", "delta": text}) + sse({
        "type": "response.completed", "response": {"status": "completed", "usage": usage}})


@contextmanager
def remote(body, api_format="responses", status=200, chunked=False, wait_before_headers=False,
           wait_after_headers=False, timeout=3, model="gpt-6.1-sol", effort="default"):
    observed, arrived, headers_ready, release = [], threading.Event(), threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append({"path": self.path, "payload": payload, "authorization": self.headers.get("Authorization")})
            arrived.set()
            if wait_before_headers:
                release.wait(3)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                if status == 302:
                    self.send_header("Location", "http://127.0.0.1:%s/stolen-key" % self.server.server_port)
                if chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                else:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.flush()
                headers_ready.set()
                if wait_after_headers:
                    release.wait(3)
                if chunked:
                    for offset in range(0, len(body), 3):
                        block = body[offset:offset + 3]
                        self.wfile.write(("%x\r\n" % len(block)).encode() + block + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.wfile.write(body)
                self.wfile.flush()
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    runtime = CloudRuntime({"base_url": "http://127.0.0.1:%s/v1" % server.server_port,
                            "api_format": api_format, "model": model, "api_key": TEST_KEY,
                            "reasoning_effort": effort, "timeout_seconds": timeout})
    try:
        yield runtime, observed, arrived, headers_ready, release
    finally:
        release.set()
        runtime.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.fixture(autouse=True)
def localhost_without_proxy(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


def test_status_and_start_are_local_and_never_reveal_key(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("status must not connect")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    runtime = CloudRuntime({"base_url": "https://api.example.invalid/v1", "api_format": "responses",
                            "model": "gpt-6.1-sol", "api_key": TEST_KEY})
    runtime.start_background()
    status = runtime.status()
    assert status["ready"] and status["configured"] and status["provider"] == "api"
    assert TEST_KEY not in json.dumps(status)
    assert runtime.usage()["requests"] == 0
    missing = CloudRuntime({"base_url": "https://api.example.invalid/v1", "api_format": "responses", "model": "m"})
    assert not missing.status()["ready"]
    with pytest.raises(CloudError, match="Key"):
        list(missing.stream_chat([]))


@pytest.mark.parametrize("chunked", [False, True])
def test_responses_stream_handles_utf8_split_multiline_and_records_usage(chunked):
    body = b": keepalive\n\n" + sse({"delta": "先给总述。"}, "response.output_text.delta", multiline=True)
    body += responses("再给正文。", {"input_tokens": 10, "output_tokens": 12, "total_tokens": 22,
                                     "input_tokens_details": {"cached_tokens": 4},
                                     "output_tokens_details": {"reasoning_tokens": 3}})
    with remote(body, chunked=chunked) as (runtime, observed, *_):
        assert "".join(runtime.stream_chat([{"role": "system", "content": "test"}], max_tokens=200)) == "先给总述。再给正文。"
        assert observed[0]["path"] == "/v1/responses"
        assert observed[0]["authorization"] == "Bearer " + TEST_KEY
        payload = observed[0]["payload"]
        assert payload["store"] is False and payload["stream"] is True
        assert payload["max_output_tokens"] == 4296
        assert "temperature" not in payload and "reasoning" not in payload and "api_key" not in payload
        assert runtime.usage() == {"input_tokens": 10, "output_tokens": 12, "total_tokens": 22,
                                   "cached_input_tokens": 4, "reasoning_tokens": 3,
                                   "requests": 1, "reported_requests": 1}


@pytest.mark.parametrize("ending", [chat(reason="stop") + sse("[DONE]"), chat(reason="stop"), sse("[DONE]")])
def test_chat_stream_accepts_explicit_completion_and_preserves_text(ending):
    with remote(chat("第一段。") + chat("第二段。") + ending, api_format="chat_completions", chunked=True) as (runtime, observed, *_):
        assert list(runtime.stream_chat([{"role": "user", "content": "test"}])) == ["第一段。", "第二段。"]
        assert observed[0]["path"] == "/v1/chat/completions"
        assert observed[0]["payload"]["stream_options"] == {"include_usage": True}


def test_chat_usage_after_finish_and_accumulation_over_stages():
    usage = {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50,
             "prompt_tokens_details": {"cached_tokens": 5}, "completion_tokens_details": {"reasoning_tokens": 6}}
    body = chat("内容。", "stop") + sse({"choices": [], "usage": usage}) + sse("[DONE]")
    with remote(body, api_format="chat_completions", model="gpt-4.1", effort="default") as (runtime, observed, *_):
        for _ in range(2):
            assert "".join(runtime.stream_chat([], max_tokens=200, temperature=.4)) == "内容。"
        assert runtime.usage() == {"input_tokens": 40, "output_tokens": 60, "total_tokens": 100,
                                   "cached_input_tokens": 10, "reasoning_tokens": 12,
                                   "requests": 2, "reported_requests": 2}
        assert observed[0]["payload"]["max_tokens"] == 200
        assert observed[0]["payload"]["temperature"] == .4


@pytest.mark.parametrize("api_format", ["responses", "chat_completions"])
@pytest.mark.parametrize("effort", ["low", "none"])
def test_explicit_reasoning_uses_correct_api_field_and_budget(api_format, effort):
    body = responses() if api_format == "responses" else chat("内容", "stop")
    with remote(body, api_format=api_format, effort=effort) as (runtime, observed, *_):
        list(runtime.stream_chat([], max_tokens=2300))
        payload = observed[0]["payload"]
        assert "temperature" not in payload
        if api_format == "responses":
            assert payload["reasoning"] == {"effort": effort}
            limit = payload["max_output_tokens"]
        else:
            assert payload["reasoning_effort"] == effort
            limit = payload["max_completion_tokens"]
        assert limit == (6396 if effort == "low" else 2300)


@pytest.mark.parametrize("api_format,body,expected", [
    ("responses", sse({"type": "response.output_text.delta", "delta": "半句"}), "提前结束"),
    ("responses", sse({"type": "response.output_text.delta", "delta": "半句"}) + sse("[DONE]"), "提前结束"),
    ("responses", sse({"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}}), "输出上限"),
    ("responses", sse({"type": "response.failed", "response": {"error": {"message": TEST_KEY}}}), "未能完成"),
    ("responses", sse({"type": "response.refusal.delta", "delta": TEST_KEY}), "未能回答"),
    ("responses", sse({"type": "response.completed", "response": {"status": "completed"}}), "没有返回"),
    ("chat_completions", chat("半句"), "提前结束"),
    ("chat_completions", chat("半句", "length") + sse("[DONE]"), "输出上限"),
    ("chat_completions", chat("半句", "content_filter") + sse("[DONE]"), "未完整结束"),
    ("chat_completions", sse({"error": {"message": TEST_KEY}}), "未能完成"),
    ("chat_completions", sse({"choices": [{"delta": {"refusal": TEST_KEY}, "finish_reason": "stop"}]}), "未能回答"),
    ("responses", b"data: not-json\n\n", "无法解析"),
])
def test_partial_failed_refused_and_malformed_streams_never_complete(api_format, body, expected):
    with remote(body, api_format=api_format) as (runtime, *_):
        with pytest.raises(CloudError, match=expected) as exc:
            list(runtime.stream_chat([]))
        assert TEST_KEY not in str(exc.value)


@pytest.mark.parametrize("status,expected", [(400, "参数"), (401, "Key"), (403, "拒绝访问"), (404, "地址或模型"),
                                             (429, "额度"), (500, "暂时不可用"), (302, "重定向")])
def test_http_errors_are_safe_and_redirects_are_not_followed(status, expected):
    with remote(("remote echo: " + TEST_KEY).encode(), status=status) as (runtime, observed, *_):
        with pytest.raises(CloudError, match=expected) as exc:
            list(runtime.stream_chat([]))
        assert len(observed) == 1 and TEST_KEY not in str(exc.value) and "remote echo" not in str(exc.value)


@pytest.mark.parametrize("before_headers", [False, True])
@pytest.mark.parametrize("action", ["cancel", "stop"])
def test_cancellation_interrupts_headers_and_body_waits(before_headers, action):
    with remote(responses(), wait_before_headers=before_headers, wait_after_headers=not before_headers) as (runtime, _, arrived, headers_ready, release):
        cancel, outcome = threading.Event(), []
        def consume():
            try:
                outcome.append(list(runtime.stream_chat([], cancel_event=cancel)))
            except Exception as exc:
                outcome.append(exc)
        worker = threading.Thread(target=consume, daemon=True)
        worker.start()
        assert (arrived if before_headers else headers_ready).wait(2)
        started = time.monotonic()
        cancel.set() if action == "cancel" else runtime.stop()
        worker.join(timeout=1.2)
        assert not worker.is_alive() and time.monotonic() - started < 1.2
        assert outcome == [[]]
        with runtime._lock:
            assert not runtime._tasks


def test_timeout_is_actionable_and_credential_free():
    with remote(responses(), wait_after_headers=True, timeout=.1) as (runtime, *_):
        with pytest.raises(CloudError, match="超时") as exc:
            list(runtime.stream_chat([]))
        assert TEST_KEY not in str(exc.value)


def test_closing_generator_cancels_request_and_leaves_runtime_reusable():
    body = chat("第一段") + chat("剩余内容", "stop") + sse("[DONE]")
    with remote(body, api_format="chat_completions") as (runtime, observed, *_):
        stream = runtime.stream_chat([])
        assert next(stream) == "第一段"
        stream.close()
        assert runtime.status()["ready"]
        assert "".join(runtime.stream_chat([])) == "第一段剩余内容"
        assert len(observed) == 2


@pytest.mark.parametrize("use_system_proxy", [False, True])
def test_proxy_preference_is_honored_without_disabling_tls_or_following_redirects(monkeypatch, use_system_proxy):
    original, clients = httpx.AsyncClient, []
    def client(**kwargs):
        clients.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    with remote(responses()) as (runtime, *_):
        runtime._config["use_system_proxy"] = use_system_proxy
        assert "".join(runtime.stream_chat([])) == "完整回答。"
    assert clients[0]["trust_env"] is use_system_proxy
    assert clients[0]["follow_redirects"] is False
    assert clients[0].get("verify", True) is True


def test_usage_reported_on_incomplete_response_is_not_lost():
    body = sse({"type": "response.incomplete", "response": {
        "incomplete_details": {"reason": "max_output_tokens"},
        "usage": {"input_tokens": 10, "output_tokens": 4096, "total_tokens": 4106}}})
    with remote(body) as (runtime, *_):
        with pytest.raises(CloudError, match="输出上限"):
            list(runtime.stream_chat([]))
        assert runtime.usage()["output_tokens"] == 4096
        assert runtime.usage()["reported_requests"] == 1
