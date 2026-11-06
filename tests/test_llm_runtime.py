"""Verify stream completeness and cancellation against actual local HTTP I/O."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from llm_runtime import AnswerRuntime


def event(text="", reason=None):
    return ("data: " + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": reason}]}, ensure_ascii=False) + "\n\n").encode("utf-8")


@contextmanager
def local_stream(tmp_path, body, connection_close=False, headers_ready=None, release=None, chunked=False):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            elif not connection_close:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.flush()
            if headers_ready:
                headers_ready.set()
            if release:
                release.wait(5)
            try:
                if chunked:
                    # Split across both Chinese UTF-8 characters and SSE lines.
                    for offset in range(0, len(body), 3):
                        chunk = body[offset:offset + 3]
                        self.wfile.write(("%x\r\n" % len(chunk)).encode() + chunk + b"\r\n")
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
    runtime = AnswerRuntime(tmp_path)
    runtime._state = "ready"
    runtime._port = server.server_port
    try:
        yield runtime
    finally:
        if release:
            release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.parametrize("ending", [event(reason="stop") + b"data: [DONE]\n\n", event(reason="stop"), b"data: [DONE]\n\n"])
@pytest.mark.parametrize("chunked", [False, True])
def test_normal_stream_requires_an_explicit_completion_signal(tmp_path, ending, chunked):
    with local_stream(tmp_path, event("这是一段完整回答。") + ending, chunked=chunked) as runtime:
        assert "".join(runtime.stream_chat([{"role": "user", "content": "测试"}])) == "这是一段完整回答。"


@pytest.mark.parametrize("ending, expected_message", [(b"", "连接中断"), (event(reason="length") + b"data: [DONE]\n\n", "长度上限")])
def test_partial_output_is_not_reported_as_complete(tmp_path, ending, expected_message):
    with local_stream(tmp_path, event("只生成了半句") + ending) as runtime:
        stream = runtime.stream_chat([{"role": "user", "content": "测试"}])
        assert next(stream) == "只生成了半句"
        with pytest.raises(RuntimeError, match=expected_message):
            list(stream)


def test_cancel_interrupts_read_even_when_http_connection_drops_socket_reference(tmp_path):
    headers_ready, release, cancel = threading.Event(), threading.Event(), threading.Event()
    with local_stream(tmp_path, b"data: [DONE]\n\n", connection_close=True,
                      headers_ready=headers_ready, release=release) as runtime:
        outcome = []

        def consume():
            try:
                outcome.append(list(runtime.stream_chat([{"role": "user", "content": "测试"}], cancel_event=cancel)))
            except Exception as exc:
                outcome.append(exc)

        worker = threading.Thread(target=consume, daemon=True)
        worker.start()
        assert headers_ready.wait(2)
        cancel.set()
        worker.join(timeout=1)
        assert not worker.is_alive(), "取消后仍等待服务端下一批内容"
        assert outcome == [[]]
