"""Export the unchanged app as a clean portable package and GitHub source tree."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "scripts" / "distribution_docs"
VERSION = re.search(r"StringStruct\('ProductVersion', '([^']+)'\)",
                    (ROOT / "scripts" / "windows_version_info.txt").read_text(encoding="utf-8")).group(1)
PORTABLE_NAME = "DefenseDesk-%s-Windows-x64" % VERSION
CORE = ("cloud_runtime.py", "conversation_context.py", "conversation_store.py", "generation.py",
        "ingest.py", "instant_opening.py", "launcher.py", "llm_runtime.py", "provider_settings.py",
        "search_engine.py", "server.py", "speech.py", "store.py")
SOURCE_SCRIPTS = ("build_windows.py", "windows_version_info.txt", "collect_licenses.py",
                  "download_answer_model.py", "download_speech_model.py", "smoke_portable.py",
                  "verify_generation.py", "verify_competing_answers.py", "prepare_distribution.py")
UNUSED_TOOLS = {"llama-batched-bench-impl.dll", "llama-bench-impl.dll", "llama-cli-impl.dll",
                "llama-completion-impl.dll", "llama-fit-params-impl.dll",
                "llama-perplexity-impl.dll", "llama-quantize-impl.dll"}
VC_RUNTIME = ("msvcp140.dll", "concrt140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
IGNORE = """# Local environments and generated output
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
build/
build_release/
dist/
发布包/
归档/
*.spec
# User documents, chat, credentials and logs
data/
logs/
*.sqlite3*
*.db
*.log
.env
.env.*
generation-settings.json
# Generated root-level reports, screenshots and ad-hoc imported material
/*.json
/*.png
/*.jpg
/*.pdf
/*.docx
/*.xlsx
/*验证*.txt
/*复测*.txt
/*报告*.txt
# Binary distributions and downloaded model weights
_internal/
models/
runtime/
*.exe
*.dll
*.pyd
*.gguf
*.onnx
*.zip
*.7z
*.part[0-9]*
"""


def copy_file(relative, destination):
    source = ROOT / relative
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(target))


def copy_tree(source, target, omit=()):
    excluded = set(omit) | {"__pycache__", ".pytest_cache"}
    shutil.copytree(str(source), str(target), ignore=lambda folder, names: [name for name in names if name in excluded])


def notices(destination):
    target = destination / "third_party_licenses"
    copy_tree(ROOT / "third_party_licenses", target)
    shutil.copy2(str(DOCS / "mermaid-10.9.3-LICENSE.txt"), str(target / "mermaid-10.9.3-LICENSE.txt"))
    shutil.copy2(str(Path(sys.base_prefix) / "LICENSE.txt"), str(target / "PYTHON-LICENSE.txt"))
    shutil.copy2(str(DOCS / "Microsoft-VC-Runtime-NOTICE.txt"), str(target / "Microsoft-VC-Runtime-NOTICE.txt"))
    render_doc("THIRD_PARTY_NOTICES.md", destination / "THIRD_PARTY_NOTICES.md")


def render_doc(name, destination):
    destination.write_text((DOCS / name).read_text(encoding="utf-8").replace("__VERSION__", VERSION), encoding="utf-8")


def stage_source(output):
    target = output / "GitHub源码"
    target.mkdir()
    for relative in CORE + ("requirements.txt", "requirements-dev.txt", "启动答辩速查.bat"):
        copy_file(relative, target)
    copy_tree(ROOT / "static", target / "static")
    for source in (ROOT / "tests").iterdir():
        if source.is_file() and source.suffix in (".py", ".cjs"):
            copy_file(source.relative_to(ROOT), target)
    for name in SOURCE_SCRIPTS:
        copy_file(Path("scripts") / name, target)
    copy_tree(DOCS, target / "scripts" / "distribution_docs")
    notices(target)
    render_doc("README-source.md", target / "README.md")
    render_doc("PUBLISHING.md", target / "PUBLISHING.md")
    (target / ".gitignore").write_text(IGNORE, encoding="utf-8")
    (target / ".gitattributes").write_text("* text=auto\n*.bat text eol=crlf\n*.ps1 text eol=crlf\n", encoding="utf-8")
    return target


def stage_portable(output, vc_runtime_dir):
    target = output / PORTABLE_NAME
    target.mkdir()
    for name in ("答辩速查.exe", "启动答辩速查.bat", "使用说明.txt"):
        copy_file(name, target)
    for name in ("_internal", "models/answer-model", "models/sensevoice"):
        copy_tree(ROOT / name, target / name)
    copy_tree(ROOT / "runtime", target / "runtime", omit=UNUSED_TOOLS)
    for relative in ("_internal", "runtime/llama/cpu", "runtime/llama/vulkan"):
        for name in VC_RUNTIME:
            shutil.copy2(str(vc_runtime_dir / name), str(target / relative / name))
    notices(target)
    render_doc("PORTABLE-START.txt", target / "先读我.txt")
    # No data directory is copied: the unchanged app creates a fresh library.
    return target


def archive_tree(folder, archive, archive_root=None):
    skipped = {"__pycache__", ".pytest_cache", ".git"}
    root_name = archive_root or folder.name
    with zipfile.ZipFile(str(archive), "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            if (not path.is_file() or relative.parts[0] in {"data", "logs"}
                    or any(part in skipped for part in relative.parts) or path.suffix == ".pyc"):
                continue
            bundle.write(str(path), str(Path(root_name) / relative))
            if path.stat().st_size > 100 * 1024 * 1024:
                print("Archived " + str(relative), flush=True)
    return archive.stat().st_size


def split_zip(archive, part_size_mib):
    if not archive.is_file() or archive.suffix.lower() != ".zip":
        raise ValueError("Please specify an existing ZIP archive")
    if not 1 <= part_size_mib < 2048:
        raise ValueError("Part size must be between 1 and 2047 MiB")
    target = archive.with_name(archive.stem + "-github-parts")
    target.mkdir()
    parts = []
    limit = part_size_mib * 1024 * 1024
    with archive.open("rb") as source:
        while True:
            first = source.read(min(limit, 1024 * 1024))
            if not first:
                break
            part = target / (archive.name + ".part%03d" % (len(parts) + 1))
            length = 0
            with part.open("xb") as output:
                output.write(first)
                length += len(first)
                while length < limit:
                    block = source.read(min(limit - length, 1024 * 1024))
                    if not block:
                        break
                    output.write(block)
                    length += len(block)
            parts.append((part.name, length))
    quoted_name = archive.name.replace("'", "''")
    rows = ",\n".join("    @{Name='%s'; Length=%d}" % (name.replace("'", "''"), length) for name, length in parts)
    script = """$ErrorActionPreference = 'Stop'
$parts = @(
%s
)
$target = Join-Path $PSScriptRoot '%s'
foreach ($part in $parts) {
    $path = Join-Path $PSScriptRoot $part.Name
    if (!(Test-Path -LiteralPath $path) -or (Get-Item -LiteralPath $path).Length -ne $part.Length) {
        throw ('分卷缺失或长度不符，请重新下载：' + $part.Name)
    }
}
$output = [System.IO.File]::Open($target, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
$failed = $false
try {
    foreach ($part in $parts) {
        $inputStream = [System.IO.File]::OpenRead((Join-Path $PSScriptRoot $part.Name))
        try { $inputStream.CopyTo($output) } finally { $inputStream.Dispose() }
    }
} catch { $failed = $true; throw } finally {
    $output.Dispose()
    if ($failed) { Remove-Item -LiteralPath $target -Force }
}
Write-Host ('已合并：' + $target)
Write-Host '接下来完整解压ZIP，再双击答辩速查.exe。保留所有程序配套目录。'
""" % (rows, quoted_name)
    (target / "Merge-Portable.ps1").write_text(script, encoding="utf-8-sig")
    (target / "Merge-Portable.bat").write_text(
        '@echo off\r\nchcp 65001 >nul\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Merge-Portable.ps1"\r\npause\r\n', encoding="utf-8")
    (target / "SPLIT-README.txt").write_text(
        "将全部part分卷和合并脚本放在同一目录，双击Merge-Portable.bat。\n"
        "合并得到普通ZIP后再完整解压；不在ZIP内部启动程序。\n"
        "已有同名ZIP时合并会停止，不覆盖已有文件；需要重试时先自行移走旧ZIP。\n"
        "分卷与完整ZIP是同一份压缩包的不同传输形式，选一种发送即可。\n", encoding="utf-8")
    return {"folder": str(target), "parts": len(parts), "bytes": sum(size for _, size in parts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "发布包" / ("v" + VERSION))
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--vc-runtime-dir", type=Path,
                        default=Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32",
                        help="Directory containing the four licensed x64 VC runtime DLLs for application-local deployment")
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument("--stage-only", action="store_true")
    phase.add_argument("--archive-only", action="store_true")
    parser.add_argument("--split-zip", type=Path)
    parser.add_argument("--part-size-mib", type=int, default=1536)
    args = parser.parse_args()
    if args.split_zip:
        print(json.dumps(split_zip(args.split_zip.resolve(), args.part_size_mib), ensure_ascii=True), flush=True)
        return
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not args.archive_only:
        source = stage_source(output)
        print("Source staged: " + str(source), flush=True)
        if not args.source_only:
            portable = stage_portable(output, args.vc_runtime_dir)
            print("Portable staged: " + str(portable), flush=True)
        render_doc("PUBLISHING.md", output / "发布说明.md")
    if not args.stage_only:
        source_zip = output / ("DefenseDesk-%s-source.zip" % VERSION)
        source_size = archive_tree(output / "GitHub源码", source_zip, "DefenseDesk-source")
        result = {"version": VERSION, "source_zip": source_zip.name, "source_bytes": source_size}
        if not args.source_only:
            portable_zip = output / (PORTABLE_NAME + ".zip")
            result.update(portable_zip=portable_zip.name,
                          portable_bytes=archive_tree(output / PORTABLE_NAME, portable_zip))
        (output / "文件包信息.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
