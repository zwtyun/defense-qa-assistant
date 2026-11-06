"""Offline, in-memory speech recognition for spoken search questions."""
import io
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
import wave

import numpy as np


MAX_AUDIO_BYTES = 6 * 1024 * 1024
MAX_DURATION_SECONDS = 180
ENGINE = "SenseVoice · 本地离线"
RUN_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


class SpeechError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


def read_wave(data):
    """Validate a PCM wave before allowing any model work; no audio is written."""
    if not data:
        raise SpeechError(400, "没有收到录音，请重新录制。")
    if len(data) > MAX_AUDIO_BYTES:
        raise SpeechError(413, "录音文件不能超过 6 MB，请缩短问题后重试。")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            rate = audio.getframerate()
            channels = audio.getnchannels()
            frames = audio.getnframes()
            if audio.getcomptype() != "NONE" or audio.getsampwidth() != 2:
                raise SpeechError(400, "请使用 16 位 PCM WAV 录音；可直接点击语音提问录制。")
            if channels not in (1, 2) or not 8000 <= rate <= 48000:
                raise SpeechError(400, "录音需为 8–48 kHz 的单声道或双声道 WAV。")
            duration = frames / rate
            if duration > MAX_DURATION_SECONDS:
                raise SpeechError(400, "每次语音提问最长 3 分钟，请分成较短的问题。")
            if duration < 0.15:
                raise SpeechError(400, "录音太短，请说完整问题后再停止。")
            pcm = audio.readframes(frames)
            if len(pcm) != frames * channels * 2:
                raise SpeechError(400, "录音数据不完整，请重新录制。")
    except (wave.Error, EOFError, OSError, ValueError, RuntimeError) as exc:
        raise SpeechError(400, "无法读取录音，请使用 WAV 文件或直接点击语音提问。") from exc
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    return samples / 32768.0, rate, duration


class SpeechService:
    def __init__(self, model_dir=None, recognizer_factory=None):
        self.model_dir = Path(model_dir or os.environ.get("DEFENSE_SPEECH_MODEL_DIR") or RUN_ROOT / "models" / "sensevoice")
        self._factory = recognizer_factory
        self._recognizer = None
        self._state_lock = threading.Lock()
        self._decode_lock = threading.Lock()
        self._worker = None
        self._state = "missing"
        self._message = "未找到本地语音模型，请保留程序旁的 models 文件夹后重新启动。文字检索仍可使用。"

    def _has_model(self):
        return all((self.model_dir / filename).is_file() for filename in ("model.int8.onnx", "tokens.txt"))

    def status(self):
        with self._state_lock:
            return {"status": self._state, "ready": self._state == "ready",
                    "available": self._has_model(), "message": self._message,
                    "engine": ENGINE, "max_duration_seconds": MAX_DURATION_SECONDS}

    def start_background(self):
        with self._state_lock:
            if self._state in ("loading", "ready") or not self._has_model():
                return
            self._state = "loading"
            self._message = "正在加载本地语音识别，首次启动请稍候；文字检索可正常使用。"
            self._worker = threading.Thread(target=self._load, name="speech-warmup", daemon=True)
            self._worker.start()

    def _load(self):
        try:
            if self._factory is None:
                import sherpa_onnx
                recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(self.model_dir / "model.int8.onnx"),
                    tokens=str(self.model_dir / "tokens.txt"),
                    num_threads=min(4, os.cpu_count() or 1),
                    use_itn=True, language="auto", provider="cpu")
            else:
                recognizer = self._factory()
            # Prepare inference before displaying readiness; avoids a slow first question.
            stream = recognizer.create_stream()
            stream.accept_waveform(16000, np.zeros(8000, dtype=np.float32))
            recognizer.decode_stream(stream)
            with self._state_lock:
                self._recognizer = recognizer
                self._state = "ready"
                self._message = "语音识别已就绪，全程在本机处理。"
        except Exception:
            logging.exception("本地语音识别初始化失败")
            with self._state_lock:
                self._state = "error"
                self._message = "本地语音识别未能启动，请检查 models 文件夹或重新启动程序；文字检索仍可使用。"

    def transcribe(self, data):
        started = time.perf_counter()
        samples, rate, duration = read_wave(data)
        info = self.status()
        if not info["ready"]:
            raise SpeechError(503, info["message"])
        if not self._decode_lock.acquire(blocking=False):
            raise SpeechError(429, "上一段语音仍在识别，请稍候再试。")
        try:
            # Actual digital silence should not produce hallucinated words from the model.
            if float(np.max(np.abs(samples))) < 0.0005:
                text = ""
            else:
                stream = self._recognizer.create_stream()
                stream.accept_waveform(rate, samples)
                self._recognizer.decode_stream(stream)
                text = re.sub(r"<\|[^>]*\|>", "", stream.result.text or "").strip()
            return {"text": text, "duration_ms": round(duration * 1000),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "engine": ENGINE}
        except SpeechError:
            raise
        except Exception as exc:
            logging.exception("本地语音识别失败")
            raise SpeechError(500, "这段语音未能识别，请重新录制或输入关键词。") from exc
        finally:
            self._decode_lock.release()


_default_service = None
_default_lock = threading.Lock()


def get_speech_service():
    # server.app and launcher.create_app share one model in the same process.
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = SpeechService()
        return _default_service
