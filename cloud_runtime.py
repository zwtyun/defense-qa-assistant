"""Cancellable Responses/Chat Completions streaming for an explicitly selected API."""
import asyncio
import json
import queue
import re
import threading

import httpx


class CloudError(RuntimeError):
    """Safe user-facing error; never includes remote response text or secrets."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _http_error(status):
    if status == 400 or status == 422:
        return "API请求参数不被当前模型或接口接受，请核对模型名称、接口格式和推理设置。"
    if status == 401:
        return "API Key 无效或已过期，请重新填写。"
    if status == 403:
        return "API拒绝访问，请检查账号、模型权限或服务地区限制。"
    if status == 404:
        return "未找到API地址或模型，请检查API地址、接口格式和模型名称。"
    if status == 408:
        return "API等待超时，请检查网络后重新生成。"
    if status == 429:
        return "API额度不足或请求过于频繁，请检查余额和限额后重试。"
    if 300 <= status < 400:
        return "API地址发生重定向，请填写最终接口地址后重试；当前请求未跟随跳转。"
    if status >= 500:
        return "API服务暂时不可用，请稍后重试，或切换到本地模型。"
    return "API请求失败（HTTP %s），请检查接口设置后重试。" % status


def _reasoning_model(model):
    name = str(model).lower().rsplit("/", 1)[-1]
    return bool(re.match(r"^(?:gpt-[5-9](?:[.\-]|$)|o[1-9](?:[.\-]|$))", name))


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class CloudRuntime:
    """One immutable provider snapshot, with usage accumulated over its stages.

    The existing generation worker consumes a synchronous generator. A small async
    HTTP worker makes cancellation interrupt DNS/connect/read rather than waiting
    for a synchronous buffered socket read to finish on Windows.
    """

    def __init__(self, config):
        self._config = dict(config)
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._tasks = set()
        self._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                       "cached_input_tokens": 0, "reasoning_tokens": 0,
                       "requests": 0, "reported_requests": 0}

    def status(self):
        configured = all(isinstance(self._config.get(key), str) and self._config[key].strip()
                         for key in ("base_url", "model", "api_key"))
        configured = configured and self._config.get("api_format") in ("responses", "chat_completions")
        stopped = self._stop_event.is_set()
        return {"provider": "api", "status": "stopped" if stopped else "ready" if configured else "missing",
                "ready": bool(configured and not stopped), "configured": bool(configured),
                "available": bool(configured), "model": self._config.get("model", ""), "backend": "API",
                "message": "API调用已停止。" if stopped else
                "外部API已配置，网络和模型可用性以连接测试为准。" if configured else
                "请先填写API地址、模型名称和API Key。"}

    def start_background(self):
        # A status poll must never spend tokens or send project material.
        pass

    def usage(self):
        with self._lock:
            return dict(self._usage)

    def _record_usage(self, usage):
        if not isinstance(usage, dict):
            return
        inputs = _count(usage.get("input_tokens", usage.get("prompt_tokens")))
        outputs = _count(usage.get("output_tokens", usage.get("completion_tokens")))
        input_details = usage.get("input_tokens_details", usage.get("prompt_tokens_details")) or {}
        output_details = usage.get("output_tokens_details", usage.get("completion_tokens_details")) or {}
        with self._lock:
            self._usage["input_tokens"] += inputs
            self._usage["output_tokens"] += outputs
            self._usage["total_tokens"] += _count(usage.get("total_tokens")) or inputs + outputs
            self._usage["cached_input_tokens"] += _count(input_details.get("cached_tokens")) if isinstance(input_details, dict) else 0
            self._usage["reasoning_tokens"] += _count(output_details.get("reasoning_tokens")) if isinstance(output_details, dict) else 0
            self._usage["reported_requests"] += 1

    def _payload(self, messages, max_tokens, temperature):
        model = self._config["model"]
        effort = self._config.get("reasoning_effort", "default")
        reasoning = _reasoning_model(model) or effort not in ("default", "none")
        # The limit includes hidden reasoning. Leave room for the requested visible
        # overview/body; this is a per-request ceiling, not prepaid token usage.
        budget = max_tokens + (4096 if reasoning and effort != "none" else 0)
        body = {"model": model, "stream": True}
        if self._config["api_format"] == "responses":
            body.update(input=messages, max_output_tokens=budget, store=False)
            if effort != "default":
                body["reasoning"] = {"effort": effort}
        else:
            body.update(messages=messages, stream_options={"include_usage": True})
            body["max_completion_tokens" if _reasoning_model(model) else "max_tokens"] = budget
            if effort != "default":
                body["reasoning_effort"] = effort
        if not reasoning and effort == "default":
            body["temperature"] = temperature
        return body

    async def _request(self, messages, max_tokens, temperature, offer, cancelled):
        api_format = self._config["api_format"]
        suffix = "/responses" if api_format == "responses" else "/chat/completions"
        url = self._config["base_url"].rstrip("/") + suffix
        timeout = float(self._config.get("timeout_seconds", 120))
        headers = {"Authorization": "Bearer " + self._config["api_key"], "Accept": "text/event-stream"}
        complete, emitted, reported_usage = False, False, None

        async def consume(data, event_name):
            nonlocal complete, emitted, reported_usage
            if data == "[DONE]":
                if api_format == "chat_completions":
                    complete = True
                return True
            try:
                item = json.loads(data)
            except (TypeError, ValueError):
                raise CloudError("API返回了无法解析的流式数据，请检查接口格式后重试。") from None
            if not isinstance(item, dict):
                raise CloudError("API返回了无法解析的流式数据，请检查接口格式后重试。")
            if api_format == "responses":
                kind = item.get("type") or event_name
                response = item.get("response") or {}
                if isinstance(response, dict) and isinstance(response.get("usage"), dict):
                    reported_usage = response["usage"]
                if kind == "response.output_text.delta":
                    delta = item.get("delta")
                    if isinstance(delta, str) and delta:
                        emitted = True
                        await offer(("text", delta))
                elif kind in ("response.refusal.delta", "response.refusal.done"):
                    raise CloudError("API模型未能回答这个问题，请调整问题后重试。")
                elif kind == "response.incomplete":
                    details = response.get("incomplete_details") or {} if isinstance(response, dict) else {}
                    if details.get("reason") == "max_output_tokens":
                        raise CloudError("API回答达到输出上限，当前内容尚未完成，请缩小问题范围或降低推理强度后重试。")
                    raise CloudError("API模型未完整生成回答，请调整问题或设置后重试。")
                elif kind in ("response.failed", "error") or item.get("error"):
                    raise CloudError("API模型未能完成这次回答，请检查模型和额度设置后重试。")
                elif kind == "response.completed":
                    if not isinstance(response, dict) or response.get("status", "completed") != "completed":
                        raise CloudError("API模型未完整结束回答，请重新生成。")
                    complete = True
                    return True
            else:
                if item.get("error"):
                    raise CloudError("API模型未能完成这次回答，请检查模型和额度设置后重试。")
                if isinstance(item.get("usage"), dict):
                    reported_usage = item["usage"]
                choices = item.get("choices") or []
                if not isinstance(choices, list):
                    raise CloudError("API返回了无法解析的流式数据，请检查接口格式后重试。")
                if choices:
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        raise CloudError("API返回了无法解析的流式数据，请检查接口格式后重试。")
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        raise CloudError("API返回了无法解析的流式数据，请检查接口格式后重试。")
                    if delta.get("refusal"):
                        raise CloudError("API模型未能回答这个问题，请调整问题后重试。")
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        emitted = True
                        await offer(("text", text))
                    reason = choice.get("finish_reason")
                    if reason == "stop":
                        complete = True
                    elif reason == "length":
                        raise CloudError("API回答达到输出上限，当前内容尚未完成，请缩小问题范围或降低推理强度后重试。")
                    elif reason:
                        raise CloudError("API模型未完整结束文字回答，请调整问题后重试。")
            return False

        try:
            if cancelled():
                return
            with self._lock:
                self._usage["requests"] += 1
            # Certificate validation stays enabled. Redirects could move the key
            # to another destination, so a configured endpoint must be final.
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(15, timeout)),
                                         follow_redirects=False,
                                         trust_env=bool(self._config.get("use_system_proxy", False))) as client:
                async with client.stream("POST", url, json=self._payload(messages, max_tokens, temperature),
                                         headers=headers) as response:
                    if response.status_code != 200:
                        raise CloudError(_http_error(response.status_code))
                    data, event_name = [], ""
                    async for line in response.aiter_lines():
                        if cancelled():
                            return
                        if not line:
                            if data and await consume("\n".join(data), event_name):
                                break
                            data, event_name = [], ""
                        elif line.startswith("data:"):
                            data.append(line[5:].removeprefix(" "))
                        elif line.startswith("event:"):
                            event_name = line[6:].strip()
                    else:
                        if data:
                            await consume("\n".join(data), event_name)
            if not cancelled():
                if not complete:
                    raise CloudError("API连接提前结束，当前回答尚未完成，请重新生成。")
                if not emitted:
                    raise CloudError("API没有返回可用的文字回答，请检查模型和输出设置。")
        except httpx.TimeoutException:
            raise CloudError("API等待超时，请检查网络后重试，或切换到本地模型。") from None
        except httpx.ConnectError:
            raise CloudError("无法连接API，请检查网络、API地址及证书配置。") from None
        except httpx.HTTPError:
            raise CloudError("API连接中断，当前回答尚未完成，请重新生成。") from None
        finally:
            if reported_usage is not None:
                self._record_usage(reported_usage)

    def stream_chat(self, messages, max_tokens=400, temperature=.25, cancel_event=None):
        info = self.status()
        if not info["ready"]:
            raise CloudError(info["message"])
        cancel = cancel_event if cancel_event is not None else threading.Event()
        abandoned = threading.Event()
        channel = queue.Queue(maxsize=64)
        control = {}

        def cancelled():
            return cancel.is_set() or abandoned.is_set() or self._stop_event.is_set()

        async def offer(item):
            while not cancelled():
                try:
                    channel.put_nowait(item)
                    return
                except queue.Full:
                    await asyncio.sleep(.02)

        async def run():
            loop, task = asyncio.get_running_loop(), asyncio.current_task()
            with self._lock:
                control.update(loop=loop, task=task)
                self._tasks.add((loop, task))
            try:
                await self._request(messages, max_tokens, temperature, offer, cancelled)
            except asyncio.CancelledError:
                pass
            except CloudError as exc:
                await offer(("error", str(exc)))
            except Exception:
                # Never expose remote payloads, URLs with credentials or key text.
                await offer(("error", "API请求未能完成，请检查接口设置后重试。"))
            finally:
                with self._lock:
                    self._tasks.discard((loop, task))
            await offer(("end", None))

        def worker_main():
            try:
                asyncio.run(run())
            except Exception:
                if not cancelled():
                    try:
                        channel.put_nowait(("error", "API请求未能完成，请重新生成。"))
                    except queue.Full:
                        pass

        if cancelled():
            return
        worker = threading.Thread(target=worker_main, name="api-answer-stream", daemon=True)
        worker.start()
        try:
            while not cancelled():
                try:
                    kind, value = channel.get(timeout=.05)
                except queue.Empty:
                    if cancelled():
                        return
                    if not worker.is_alive():
                        raise CloudError("API连接提前结束，当前回答尚未完成，请重新生成。")
                    continue
                if cancelled():
                    return
                if kind == "error":
                    raise CloudError(value)
                if kind == "end":
                    return
                yield value
        finally:
            abandoned.set()
            with self._lock:
                loop, task = control.get("loop"), control.get("task")
            if loop is not None and task is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass
            worker.join(timeout=2)

    def stop(self):
        self._stop_event.set()
        with self._lock:
            tasks = list(self._tasks)
        for loop, task in tasks:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
