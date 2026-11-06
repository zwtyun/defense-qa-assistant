"""Speech-input boundaries and speech-to-answer flow without downloading a model."""
import io
import struct
from types import SimpleNamespace
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server import create_app
from speech import MAX_AUDIO_BYTES, SpeechService


def wav(seconds=1, rate=16000, channels=1, silence=False, width=2):
    sample_count = round(seconds * rate)
    samples = np.zeros(sample_count) if silence else np.sin(np.arange(sample_count) * 2 * np.pi * 440 / rate) * 10000
    pcm = np.repeat(samples.astype("<i2")[:, None], channels, axis=1)
    result = io.BytesIO()
    with wave.open(result, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(pcm.tobytes())
    return result.getvalue()


class Recognizer:
    def __init__(self):
        self.calls = []

    def create_stream(self):
        stream = SimpleNamespace(result=SimpleNamespace(text=""))
        stream.accept_waveform = lambda rate, samples: self.calls.append((rate, len(samples)))
        return stream

    def decode_stream(self, stream):
        stream.result.text = "<|zh|><|NEUTRAL|>怎样区分传感器漂移和真实工况变化？"


def ready_service(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "model.int8.onnx").write_bytes(b"test stand-in")
    (model / "tokens.txt").write_text("test", encoding="utf-8")
    recognizer = Recognizer()
    service = SpeechService(model, recognizer_factory=lambda: recognizer)
    service.start_background()
    service._worker.join(timeout=3)
    assert service.status()["ready"]
    return service, recognizer


def test_spoken_question_to_existing_answer_and_no_saved_recording(tmp_path):
    service, recognizer = ready_service(tmp_path)
    data_dir = tmp_path / "library"
    with TestClient(create_app(data_dir, speech_service=service)) as client:
        answer = client.post("/api/answers", json={"question": "怎样区分传感器漂移和真实工况变化？", "answer": "通过独立参考测量与多通道一致性判别。"}).json()
        before_files = {p.relative_to(data_dir) for p in data_dir.rglob("*") if p.is_file()}
        response = client.post("/api/speech/transcribe", files={"file": ("question.wav", wav())})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["text"] == "怎样区分传感器漂移和真实工况变化？"
        assert result["duration_ms"] == 1000 and result["elapsed_ms"] >= 0
        found = client.get("/api/search", params={"q": result["text"]}).json()["results"]
        assert found[0]["id"] == answer["id"]
        assert {p.relative_to(data_dir) for p in data_dir.rglob("*") if p.is_file()} == before_files
        assert client.get("/api/health").json()["version"] == "2.2.2"
        assert client.get("/api/speech/status").json()["ready"] is True
        assert client.get("/api/speech/status").json()["max_duration_seconds"] == 180
        assert recognizer.calls[-1] == (16000, 16000)


def test_silence_is_empty_and_stereo_sample_rate_preserved(tmp_path):
    service, recognizer = ready_service(tmp_path)
    with TestClient(create_app(tmp_path / "data", speech_service=service)) as client:
        call_count = len(recognizer.calls)
        silent = client.post("/api/speech/transcribe", files={"file": ("silence.wav", wav(silence=True))})
        assert silent.status_code == 200 and silent.json()["text"] == ""
        assert len(recognizer.calls) == call_count
        voiced = client.post("/api/speech/transcribe", files={"file": ("stereo.wav", wav(rate=44100, channels=2))})
        assert voiced.status_code == 200
        assert recognizer.calls[-1] == (44100, 44100)


@pytest.mark.parametrize("audio", [b"", b"not a wave", wav(seconds=.05), wav(seconds=180.1), wav(width=1), wav(channels=3), wav(rate=4000), wav()[:-20]],
                         ids=["empty", "not-wave", "too-short", "too-long", "wrong-bit-depth", "wrong-channels", "wrong-sample-rate", "truncated"])
def test_invalid_audio_is_rejected_before_model_call(tmp_path, audio):
    service = SpeechService(tmp_path / "missing")
    with TestClient(create_app(tmp_path / "data", speech_service=service)) as client:
        response = client.post("/api/speech/transcribe", files={"file": ("bad.wav", audio)})
        assert response.status_code == 400, response.text


def test_missing_model_and_busy_decode_keep_text_search_available(tmp_path):
    missing = SpeechService(tmp_path / "absent")
    with TestClient(create_app(tmp_path / "missing-data", speech_service=missing)) as client:
        assert client.get("/api/speech/status").json()["status"] == "missing"
        assert client.post("/api/speech/transcribe", files={"file": ("question.wav", wav())}).status_code == 503
        assert client.get("/api/search?q=指标").status_code == 200
    ready, _ = ready_service(tmp_path)
    with TestClient(create_app(tmp_path / "ready-data", speech_service=ready)) as client:
        ready._decode_lock.acquire()
        try:
            assert client.post("/api/speech/transcribe", files={"file": ("question.wav", wav())}).status_code == 429
            assert client.get("/api/search?q=指标").status_code == 200
        finally:
            ready._decode_lock.release()
        assert client.post("/api/speech/transcribe", files={"file": ("question.wav", wav())}).status_code == 200


def test_recording_over_old_one_minute_limit_reaches_recognizer(tmp_path):
    service, recognizer = ready_service(tmp_path)
    with TestClient(create_app(tmp_path / "data", speech_service=service)) as client:
        result = client.post("/api/speech/transcribe", files={"file": ("long.wav", wav(seconds=60.1))})
        assert result.status_code == 200, result.text
        assert result.json()["duration_ms"] == 60100
        assert recognizer.calls[-1] == (16000, 961600)


def test_long_recording_is_kept_in_memory_and_oversize_rejected(tmp_path, monkeypatch):
    import starlette.formparsers
    original = starlette.formparsers.SpooledTemporaryFile
    created = []

    def tracked_file(*args, **kwargs):
        file = original(*args, **kwargs)
        created.append(file)
        return file

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", tracked_file)
    service, recognizer = ready_service(tmp_path)
    with TestClient(create_app(tmp_path / "data", speech_service=service)) as client:
        audio = wav(seconds=180)
        assert 5 * 1024 * 1024 < len(audio) <= MAX_AUDIO_BYTES
        result = client.post("/api/speech/transcribe", files={"file": ("long.wav", audio)})
        assert result.status_code == 200, result.text
        assert result.json()["duration_ms"] == 180000
        assert recognizer.calls[-1] == (16000, 2880000)
        assert created and all(not f._rolled for f in created)
        result = client.post("/api/speech/transcribe", files={"file": ("huge.wav", b"x" * (MAX_AUDIO_BYTES + 1))})
        assert result.status_code == 413


def test_initialization_failure_is_reported_without_breaking_app(tmp_path):
    model = tmp_path / "broken-model"
    model.mkdir()
    for name in ("model.int8.onnx", "tokens.txt"):
        (model / name).write_bytes(b"broken")

    def fail():
        raise RuntimeError("model cannot load")

    service = SpeechService(model, recognizer_factory=fail)
    service.start_background()
    service._worker.join(timeout=3)
    assert service.status()["status"] == "error"
    with TestClient(create_app(tmp_path / "data", speech_service=service)) as client:
        assert client.get("/api/health").status_code == 200
        assert client.post("/api/speech/transcribe", files={"file": ("q.wav", wav())}).status_code == 503


@pytest.mark.parametrize("offset,value", [(16, 0xffffffff), (24, 0)], ids=["invalid-fmt-chunk-size", "zero-sample-rate"])
def test_corrupt_wave_header_returns_useful_error(tmp_path, offset, value):
    audio = bytearray(wav())
    struct.pack_into("<I", audio, offset, value)
    with TestClient(create_app(tmp_path / "data", speech_service=SpeechService(tmp_path / "missing"))) as client:
        result = client.post("/api/speech/transcribe", files={"file": ("corrupt.wav", bytes(audio))})
        assert result.status_code == 400
        assert result.json()["detail"]


@pytest.mark.parametrize("content_type,body", [
    ("multipart/form-data", b"missing boundary"),
    ("multipart/form-data; boundary=test", b"this is not a multipart upload"),
    ("multipart/form-data; boundary=test", b"--test\r\nContent-Disposition: form-data; name=\"file\"; filename=\"q.wav\"\r\n\r\ntruncated"),
], ids=["missing-boundary", "invalid-body", "truncated-body"])
def test_broken_multipart_is_not_a_server_failure(tmp_path, content_type, body):
    with TestClient(create_app(tmp_path / "data", speech_service=SpeechService(tmp_path / "missing"))) as client:
        response = client.post("/api/speech/transcribe", content=body, headers={"Content-Type": content_type})
        assert response.status_code == 400
        assert client.get("/api/search?q=指标").status_code == 200
