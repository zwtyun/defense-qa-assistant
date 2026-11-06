"""One-time developer download. The app never downloads models at runtime."""
from pathlib import Path
import shutil
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NAME = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/" + NAME + ".tar.bz2"
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def main():
    cache = ROOT / "build" / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    target = ROOT / "models" / "sensevoice"
    target.mkdir(parents=True, exist_ok=True)
    archive = cache / (NAME + ".tar.bz2")
    if not archive.exists():
        partial = archive.with_suffix(".part")
        with HTTP.open(URL, timeout=60) as response, partial.open("wb") as output:
            total = int(response.headers.get("Content-Length", 0))
            print("Downloading %.1f MB" % (total / 1048576), flush=True)
            size, last = 0, time.monotonic()
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                size += len(block)
                if time.monotonic() - last > 5:
                    print("Downloaded %.1f / %.1f MB" % (size / 1048576, total / 1048576), flush=True)
                    last = time.monotonic()
        if total and size != total:
            raise RuntimeError("Model archive download was incomplete")
        partial.replace(archive)
    names = ["model.int8.onnx", "tokens.txt", "LICENSE", "README.md", "test_wavs/zh.wav"]
    with tarfile.open(archive, "r:bz2") as bundle:
        for name in names:
            member = bundle.getmember(NAME + "/" + name)
            if not member.isfile():
                raise RuntimeError("Expected a model file: " + name)
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            print("Installed " + name, flush=True)
    license_url = "https://raw.githubusercontent.com/modelscope/FunASR/main/MODEL_LICENSE"
    with HTTP.open(license_url, timeout=60) as response:
        (target / "FUNASR_MODEL_LICENSE.txt").write_bytes(response.read())
    (target / "来源说明.txt").write_text(
        "SenseVoiceSmall INT8 / FunAudioLLM, iic / 导出：k2-fsa sherpa-onnx\n"
        "离线中文、英文、粤语、日语、韩语语音识别。\n"
        "模型版本：2024-07-17；模型权重遵循随附的 FunASR 模型协议。\n"
        "下载来源：" + URL + "\n"
        "模型卡：https://huggingface.co/FunAudioLLM/SenseVoiceSmall\n"
        "使用说明：https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html\n"
        "模型协议来源：" + license_url + "\n", encoding="utf-8")
    print("Speech model ready: " + str(target), flush=True)


if __name__ == "__main__":
    main()
