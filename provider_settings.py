"""Local generation preferences; saved API credentials use Windows user DPAPI."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit, urlunsplit


DEFAULTS = {
    "provider": "local", "base_url": "https://api.openai.com/v1",
    "api_format": "responses", "model": "",
    "reasoning_effort": "default", "timeout_seconds": 120, "remember_key": True, "use_system_proxy": False,
}


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(data, decrypt=False):
    if os.name != "nt":
        raise ValueError("此系统不支持Windows密钥加密，请取消记住密钥，仅在本次运行中使用。")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = _Blob(len(data), source_buffer), _Blob()
    # CRYPTPROTECT_UI_FORBIDDEN; no machine-wide scope, so only this Windows user can decrypt.
    if decrypt:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        arguments = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        operation = crypt.CryptProtectData
        operation.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        arguments = (ctypes.byref(source), "Defense Search API", None, None, None, 1, ctypes.byref(output))
    operation.restype = wintypes.BOOL
    if not operation(*arguments):
        raise ValueError("无法读取或保存此Windows用户的加密密钥，请重新填写密钥，或取消记住密钥。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def normalize_base_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("请填写API服务地址。")
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("API地址格式不正确。") from None
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
        raise ValueError("API地址不能包含账号、密码、查询参数或空白，请填写服务的Base URL。")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")):
        raise ValueError("外部API地址必须使用HTTPS；仅本机测试地址可使用HTTP。")
    if port == 0:
        raise ValueError("API地址端口不正确。")
    path = parsed.path.rstrip("/") or "/v1"
    if path.endswith(("/chat/completions", "/responses", "/models")):
        raise ValueError("请填写Base URL（通常以/v1结尾），不要填写具体接口路径。")
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), path, "", ""))


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("连接设置格式不正确。")
    result = {key: config.get(key, value) for key, value in DEFAULTS.items()}
    if result["provider"] not in ("local", "api"):
        raise ValueError("回答方式应为本地模型或GPT API。")
    result["base_url"] = normalize_base_url(result["base_url"])
    if result["api_format"] not in ("responses", "chat_completions"):
        raise ValueError("请选择Responses或Chat Completions接口。")
    model = result["model"]
    if not isinstance(model, str) or len(model) > 200 or any(ord(c) < 32 for c in model):
        raise ValueError("请填写有效的模型名称。")
    result["model"] = model.strip()
    if result["reasoning_effort"] not in ("default", "none", "low", "medium", "high"):
        raise ValueError("推理强度设置无效。")
    timeout = result["timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 15 <= timeout <= 300:
        raise ValueError("请求等待时间应在15至300秒之间。")
    result["timeout_seconds"] = int(timeout)
    if not isinstance(result["remember_key"], bool):
        raise ValueError("记住密钥设置应为是或否。")
    if not isinstance(result["use_system_proxy"], bool):
        raise ValueError("系统代理设置应为是或否。")
    return result


class ProviderSettings:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "generation-settings.json"
        self._lock = threading.RLock()
        self._config = dict(DEFAULTS)
        self._key = ""
        self._encrypted_key = ""
        self._warning = ""
        if not self.path.exists():
            return
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self._config = validate_config(saved)
            encrypted = saved.get("encrypted_api_key", "")
            if encrypted:
                self._key = _protect(base64.b64decode(encrypted, validate=True), decrypt=True).decode("utf-8")
                self._encrypted_key = encrypted
        except (ValueError, TypeError, UnicodeError, OSError):
            self._warning = "配置或加密密钥无法读取。跨电脑或Windows用户复制后，请重新填写API密钥。"
            self._key = ""
            self._encrypted_key = ""

    def public(self):
        with self._lock:
            return dict(self._config, has_api_key=bool(self._key),
                        key_storage=("windows" if self._encrypted_key else "session") if self._key else "missing",
                        credential_warning=self._warning)

    def snapshot(self):
        with self._lock:
            return dict(self._config, api_key=self._key)

    def _prepare(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("连接设置格式不正确。")
        allowed = set(DEFAULTS) | {"api_key", "clear_api_key"}
        if set(payload) - allowed:
            raise ValueError("连接设置包含不支持的字段。")
        config = validate_config(dict(self._config, **{key: value for key, value in payload.items() if key in DEFAULTS}))
        if "clear_api_key" in payload and not isinstance(payload["clear_api_key"], bool):
            raise ValueError("清除密钥设置应为是或否。")
        incoming = payload.get("api_key")
        if incoming is not None and not isinstance(incoming, str):
            raise ValueError("API密钥格式不正确。")
        incoming = (incoming or "").strip()
        if incoming and (len(incoming) > 8192 or not incoming.isascii() or any(c.isspace() or ord(c) < 32 for c in incoming)):
            raise ValueError("API密钥格式不正确，请检查是否包含空格或换行。")
        if incoming and payload.get("clear_api_key"):
            raise ValueError("不能同时填写新密钥并选择清除密钥。")
        key = "" if payload.get("clear_api_key") else incoming or self._key
        if config["base_url"] != self._config["base_url"] and self._key and not incoming and not payload.get("clear_api_key"):
            raise ValueError("更换API服务地址时，请重新填写该服务的密钥，避免把原密钥发送到其他服务。")
        return config, key

    def preview(self, payload=None):
        with self._lock:
            config, key = self._prepare(payload or {})
            return dict(config, api_key=key)

    def update(self, payload):
        with self._lock:
            config, key = self._prepare(payload)
            encrypted = ""
            if key and config["remember_key"]:
                encrypted = base64.b64encode(_protect(key.encode("utf-8"))).decode("ascii")
            saved = dict(config, encrypted_api_key=encrypted)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            self._config, self._key, self._encrypted_key, self._warning = config, key, encrypted, ""
            return self.public()
