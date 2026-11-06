"""One-time download of Qwen answer models and official llama.cpp runtimes.

The app itself uses these local files and never downloads at runtime.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
from pathlib import Path, PurePosixPath
import shutil
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "build" / "downloads"
RUNTIME = ROOT / "runtime" / "llama"
PROFILES = {
    "qwen35-9b": {"repo": "lmstudio-community/Qwen3.5-9B-GGUF",
                  "original": "Qwen/Qwen3.5-9B",
                  "file": "Qwen3.5-9B-Q4_K_M.gguf",
                  "size": 5627044256, "directory": "answer-model-qwen35-9b"},
    "qwen35": {"repo": "lmstudio-community/Qwen3.5-4B-GGUF",
               "original": "Qwen/Qwen3.5-4B",
               "file": "Qwen3.5-4B-Q4_K_M.gguf",
               "size": 2707513696, "directory": "answer-model"},
    "instruct": {"repo": "lmstudio-community/Qwen3-4B-Instruct-2507-GGUF",
                 "original": "Qwen/Qwen3-4B-Instruct-2507",
                 "file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
                 "size": 2497280448, "directory": "answer-model-instruct"},
    "base": {"repo": "Qwen/Qwen3-4B-GGUF", "original": "Qwen/Qwen3-4B-GGUF",
             "file": "Qwen3-4B-Q4_K_M.gguf", "size": 2497280256,
             "directory": "answer-model-base"},
}
RELEASE = "b11960"  # Binary release referenced by stable llama.cpp v0.8.0.
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def download(url, destination, expected_size=None, refresh=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing_size = destination.stat().st_size if destination.is_file() else 0
    if not refresh and existing_size and (expected_size is None or existing_size == expected_size):
        print("Already available: " + destination.name, flush=True)
        return destination
    # Distinct model filenames keep a resumed base download separate from an
    # Instruct download even when both are explicitly targeted at one directory.
    partial_name = PurePosixPath(url).name if expected_size is not None else destination.name
    partial = destination.with_name(partial_name + ".part")
    for attempt in range(4):
        offset = partial.stat().st_size if partial.exists() and not refresh else 0
        if expected_size is not None and offset > expected_size:
            offset = 0
        headers = {"User-Agent": "DefenseDesk-development/1.2"}
        if offset:
            headers["Range"] = "bytes=%s-" % offset
        try:
            with HTTP.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
                append = offset > 0 and response.status == 206
                if not append:
                    offset = 0
                length = int(response.headers.get("Content-Length", "0"))
                total = offset + length
                last = time.monotonic()
                print("Download %s: %.1f MiB" % (destination.name, total / 1048576), flush=True)
                with partial.open("ab" if append else "wb") as output:
                    size = offset
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                        size += len(block)
                        if time.monotonic() - last > 5:
                            print("%s: %.1f / %.1f MiB" % (destination.name, size / 1048576, total / 1048576), flush=True)
                            last = time.monotonic()
                if length and size != total:
                    raise RuntimeError("Download ended before expected size")
                if expected_size is not None and size != expected_size:
                    raise RuntimeError("Downloaded model size differs from the selected model")
                partial.replace(destination)
                return destination
        except Exception as exc:
            print("Download retry %s (%s): %s" % (attempt + 1, destination.name, exc), flush=True)
            if attempt == 3:
                raise
            time.sleep(2)


def install_runtime(kind):
    target = RUNTIME / kind
    if (target / "llama-server.exe").is_file():
        print("Runtime already available: " + kind, flush=True)
        return
    archive_name = "llama-%s-bin-win-%s-x64.zip" % (RELEASE, kind)
    url = "https://github.com/ggml-org/llama.cpp/releases/download/%s/%s" % (RELEASE, archive_name)
    archive = download(url, CACHE / archive_name)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            if item.is_dir():
                continue
            # The published zips may have one enclosing directory. Flatten only
            # files used by the server, preserving their names beside the EXE.
            name = PurePosixPath(item.filename).name
            if name.lower().endswith(".dll") or name == "llama-server.exe" or "license" in name.lower():
                with bundle.open(item) as source, (target / name).open("wb") as output:
                    shutil.copyfileobj(source, output)
    if not (target / "llama-server.exe").is_file():
        raise RuntimeError("Official runtime archive has no llama-server.exe")
    print("Installed runtime: " + kind, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=tuple(PROFILES), default="qwen35")
    parser.add_argument("--directory", type=Path, help="Optional model directory; relative paths are based on the app folder")
    args = parser.parse_args()
    profile = PROFILES[args.model]
    model = args.directory if args.directory is not None else ROOT / "models" / profile["directory"]
    if not model.is_absolute():
        model = ROOT / model
    model_url = "https://huggingface.co/" + profile["repo"] + "/resolve/main/" + profile["file"]
    model.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(download, model_url, model / "model.gguf", expected_size=profile["size"]),
                pool.submit(install_runtime, "vulkan"), pool.submit(install_runtime, "cpu")]
        for job in jobs:
            job.result()
    with (model / "model.gguf").open("rb") as weights:
        if weights.read(4) != b"GGUF":
            raise RuntimeError("Model is not a GGUF file")
    download("https://huggingface.co/" + profile["original"] + "/resolve/main/LICENSE", model / "LICENSE", refresh=True)
    download("https://huggingface.co/" + profile["repo"] + "/resolve/main/README.md", model / "MODEL_CARD.md", refresh=True)
    if args.model in ("instruct", "qwen35", "qwen35-9b"):
        download("https://huggingface.co/" + profile["original"] + "/resolve/main/README.md", model / "ORIGINAL_MODEL_CARD.md", refresh=True)
    download("https://raw.githubusercontent.com/ggml-org/llama.cpp/" + RELEASE + "/LICENSE", RUNTIME / "LICENSE")
    (model / "来源说明.txt").write_text(
        "原始模型：" + profile["original"] + " / Qwen Team, Alibaba Cloud\n"
        "GGUF发行仓库：" + profile["repo"] + "\n" +
        ("量化作者：LM Studio团队，LM Studio Community发行；不是Qwen官方GGUF。\n"
         "LM Studio官方来源链接：https://lmstudio.ai/models/qwen/" + profile["original"].split("/")[-1].lower() + "\n"
         if args.model in ("qwen35", "qwen35-9b") else
         "量化作者：bartowski，LM Studio Community发行；不是Qwen官方GGUF。\n"
         "LM Studio官方来源链接：https://lmstudio.ai/models/qwen/qwen3-4b-2507\n"
         if args.model == "instruct" else "Qwen官方GGUF发行。\n") +
        "量化：Q4_K_M；下载原文件：" + profile["file"] + "\n"
        "模型许可：Apache License 2.0，见 LICENSE。\n"
        "模型来源：" + model_url + "\n" +
        ("2507 Instruct为非思考版。\n" if args.model == "instruct" else
         "使用时通过 chat_template_kwargs.enable_thinking=false 禁用思考输出。\n"),
        encoding="utf-8")
    (RUNTIME / "来源说明.txt").write_text(
        "llama.cpp / ggml-org，MIT License。\n"
        "稳定版本：v0.8.0；对应官方二进制版本：" + RELEASE + "\n"
        "来源：https://github.com/ggml-org/llama.cpp/releases/tag/" + RELEASE + "\n"
        "Vulkan 使用系统显卡驱动；CPU 运行时可在无适用GPU时回退。\n",
        encoding="utf-8")
    print("Answer model and both Windows runtimes ready.", flush=True)


if __name__ == "__main__":
    main()
