"""Own one local llama.cpp process; no cloud calls or system installation."""
import http.client
import json
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
MODEL_NAME = "Qwen3.5-4B · 本地生成"
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class AnswerRuntime:
    def __init__(self, root=None):
        self.root = Path(root or ROOT)
        self.model_dir = Path(os.environ.get("DEFENSE_ANSWER_MODEL_DIR", str(self.root / "models" / "answer-model")))
        self.runtime_dir = Path(os.environ.get("DEFENSE_LLM_RUNTIME_ROOT", str(self.root / "runtime" / "llama")))
        self.thinking_budget = int(os.environ.get("DEFENSE_ANSWER_THINKING_BUDGET", "0"))
        self._lock = threading.RLock()
        self._process = None
        self._port = None
        self._worker = None
        self._stop_event = threading.Event()
        self._state = "missing"
        self._message = "正在检查本地回答模型。"
        self._backend = ""
        self._log = None

    def _available(self):
        return (self.model_dir / "model.gguf").is_file() and any(
            (self.runtime_dir / backend / "llama-server.exe").is_file() for backend in ("vulkan", "cpu"))

    def status(self):
        with self._lock:
            if self._state == "ready" and self._process and self._process.poll() is not None:
                self._state = "error"
                self._message = "本地回答模型已停止，请重新启动应用；资料检索和语音输入仍可使用。"
            return {"status": self._state, "ready": self._state == "ready", "available": self._available(),
                    "message": self._message, "model": MODEL_NAME, "backend": self._backend}

    def start_background(self):
        with self._lock:
            if self._state in ("loading", "ready"):
                return
            if not self._available():
                self._state = "missing"
                self._message = "未找到本地回答模型，请保留程序旁的 models 和 runtime 文件夹。已有答案仍可检索。"
                return
            self._stop_event.clear()
            self._state = "loading"
            self._message = "正在准备本地回答模型，首次加载请稍候；可以先查看已有答案。"
            self._worker = threading.Thread(target=self._load, name="answer-model-start", daemon=True)
            self._worker.start()

    def _vulkan_device(self, executable):
        try:
            result = subprocess.run([str(executable), "--list-devices"], capture_output=True,
                                    timeout=15, creationflags=HIDDEN)
            devices = re.findall(r"(Vulkan\d+):\s*([^\r\n]+)", (result.stdout + result.stderr).decode("utf-8", errors="replace"))
            for device, description in devices:
                if "NVIDIA" in description.upper() or "RADEON" in description.upper():
                    return device
            return devices[0][0] if devices else None
        except (OSError, subprocess.SubprocessError):
            return None

    def _healthy(self):
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=.5)
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            response.read()
            return response.status == 200
        except (OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    @staticmethod
    def _terminate(process):
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def _warmup(self):
        # The first large prompt compiles GPU kernels; do it before the UI says ready.
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=90)
        try:
            payload = {"model": "local", "messages": [{"role": "user", "content":
                       ("这是一段仅用于本地模型启动预热的示例文字，不是项目资料。" * 100) + "请只回复好。"}],
                       "max_tokens": 1, "stream": False, "chat_template_kwargs": {"enable_thinking": False}}
            connection.request("POST", "/v1/chat/completions", body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            response.read()
            if response.status != 200:
                raise RuntimeError("回答模型预热未完成")
        finally:
            connection.close()

    def _load(self):
        logs = self.root / "logs"
        logs.mkdir(exist_ok=True)
        failure = None
        for backend in ("vulkan", "cpu"):
            if self._stop_event.is_set():
                return
            executable = self.runtime_dir / backend / "llama-server.exe"
            if not executable.is_file():
                continue
            device = self._vulkan_device(executable) if backend == "vulkan" else "none"
            if device is None:
                continue
            process = None
            log = None
            try:
                port = free_port()
                args = [str(executable), "--model", str(self.model_dir / "model.gguf"),
                        "--host", "127.0.0.1", "--port", str(port),
                        "--ctx-size", "16384", "--parallel", "1", "--threads", "8", "--threads-batch", "14",
                        "--device", device, "--gpu-layers", "auto" if backend == "vulkan" else "0",
                        "--fit", "on", "--fit-target", "512",
                        "--no-webui", "--reasoning", "off", "--flash-attn", "auto"]
                log = (logs / "answer-model.log").open("ab", buffering=0)
                process = subprocess.Popen(args, cwd=str(executable.parent), stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=log, creationflags=HIDDEN)
                with self._lock:
                    self._process, self._port, self._log = process, port, log
                    self._backend = "Vulkan" if backend == "vulkan" else "CPU"
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline and not self._stop_event.is_set():
                    if process.poll() is not None:
                        raise RuntimeError("本地模型进程退出：%s" % process.returncode)
                    if self._healthy():
                        with self._lock:
                            self._message = "正在预热本地回答模型，完成后即可生成；已有答案可以先查看。"
                        self._warmup()
                        with self._lock:
                            if self._stop_event.is_set():
                                break
                            self._state = "ready"
                            self._message = "本地回答模型已就绪，可以输入或说出完整问题。"
                        return
                    self._stop_event.wait(.2)
                if self._stop_event.is_set():
                    self._terminate(process)
                    if log:
                        log.close()
                    return
                raise RuntimeError("本地模型加载超时")
            except Exception as exc:
                failure = exc
                logging.exception("回答模型启动失败（%s）", backend)
                self._terminate(process)
                if log:
                    log.close()
                if backend == "vulkan":
                    with self._lock:
                        self._message = "正在使用处理器加载本地回答模型，稍后即可生成；已有答案可直接查看。"
        with self._lock:
            if not self._stop_event.is_set():
                self._state = "error"
                self._message = "本地回答模型未能启动，请重新启动应用；详细原因见 logs/answer-model.log。"
                logging.error("本地回答模型不可用：%s", failure)

    def stream_chat(self, messages, max_tokens=400, temperature=.25, cancel_event=None):
        import h11

        if not self.status()["ready"]:
            raise RuntimeError(self.status()["message"])
        cancelled = cancel_event if cancel_event is not None else threading.Event()
        connection = None
        complete = False
        try:
            if cancelled.is_set() or self._stop_event.is_set():
                return
            payload = {"model": "local", "messages": messages, "stream": True,
                       "max_tokens": max_tokens, "temperature": temperature, "top_p": .85,
                       "repeat_penalty": 1.0, "chat_template_kwargs": {"enable_thinking": False}}
            if self.thinking_budget > 0:
                payload.update(max_tokens=max_tokens + self.thinking_budget,
                               reasoning_budget_tokens=self.thinking_budget, reasoning_format="deepseek",
                               chat_template_kwargs={"enable_thinking": True})
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            connection = socket.create_connection(("127.0.0.1", self._port), timeout=5)
            if cancelled.is_set() or self._stop_event.is_set():
                return
            protocol = h11.Connection(h11.CLIENT)
            headers = [("Host", "127.0.0.1:%s" % self._port), ("Content-Type", "application/json"),
                       ("Accept", "text/event-stream"), ("Content-Length", str(len(body))), ("Connection", "close")]
            connection.sendall(protocol.send(h11.Request(method="POST", target="/v1/chat/completions", headers=headers)))
            connection.sendall(protocol.send(h11.Data(data=body)))
            connection.sendall(protocol.send(h11.EndOfMessage()))
            # Raw recv can resume after timeout. HTTPResponse.readline uses a
            # buffered socket file, which cannot be resumed after timeout and on
            # Windows may keep waiting even when another thread calls shutdown.
            connection.settimeout(.2)
            last_data = time.monotonic()
            pending = b""
            while not cancelled.is_set() and not self._stop_event.is_set():
                event = protocol.next_event()
                if event is h11.NEED_DATA:
                    try:
                        block = connection.recv(65536)
                    except socket.timeout:
                        if time.monotonic() - last_data > 90:
                            raise RuntimeError("回答生成等待超时，请重新生成；已有答案仍可查看。")
                        continue
                    protocol.receive_data(block)
                    last_data = time.monotonic()
                    continue
                if isinstance(event, h11.Response):
                    if event.status_code != 200:
                        logging.error("本地模型请求失败：HTTP %s", event.status_code)
                        raise RuntimeError("本地模型暂时未能生成回答，请缩短问题后重试。")
                    continue
                if isinstance(event, (h11.EndOfMessage, h11.ConnectionClosed)):
                    break
                if not isinstance(event, h11.Data):
                    continue
                pending += bytes(event.data)
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        complete = True
                        break
                    item = json.loads(data.decode("utf-8"))
                    if item.get("error"):
                        raise RuntimeError("本地模型未能完成回答，请重试。")
                    choices = item.get("choices") or []
                    if choices:
                        text = choices[0].get("delta", {}).get("content")
                        if text:
                            yield text
                        reason = choices[0].get("finish_reason")
                        if reason == "stop":
                            complete = True
                        elif reason == "length":
                            raise RuntimeError("回答达到长度上限，当前内容尚未完成。请缩小问题范围后重新生成。")
                        elif reason:
                            raise RuntimeError("本地模型未完整结束回答，请重新生成。")
                if complete:
                    break
            if not complete and not cancelled.is_set() and not self._stop_event.is_set():
                raise RuntimeError("回答生成连接中断，当前内容尚未完成，请重新生成。")
        except (OSError, h11.ProtocolError, ValueError):
            if not cancelled.is_set() and not self._stop_event.is_set():
                raise RuntimeError("回答生成连接中断，请重新生成；已有答案仍可查看。")
        finally:
            if connection:
                connection.close()

    def stop(self):
        self._stop_event.set()
        with self._lock:
            process = self._process
        self._terminate(process)
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=6)
        with self._lock:
            if self._log:
                self._log.close()
                self._log = None
            self._process = None
            self._state = "missing"
            self._message = "回答模型已停止。"


_runtime = None
_runtime_lock = threading.Lock()


def get_answer_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = AnswerRuntime()
        return _runtime
