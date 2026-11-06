"""Build the Windows portable executable alongside sources and shared data."""
from pathlib import Path
import shutil
import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]


def main():
    PyInstaller.__main__.run([
        str(ROOT / "launcher.py"), "--name=答辩速查", "--onedir", "--windowed", "--noconfirm",
        "--version-file=" + str(ROOT / "scripts" / "windows_version_info.txt"),
        "--distpath=" + str(ROOT / "build_release"),
        "--workpath=" + str(ROOT / "build"), "--specpath=" + str(ROOT),
        "--add-data=" + str(ROOT / "static") + ";static",
        "--collect-data=jieba", "--collect-submodules=uvicorn", "--collect-submodules=rank_bm25",
        "--collect-binaries=sherpa_onnx", "--collect-submodules=sherpa_onnx",
        "--hidden-import=sherpa_onnx.lib._sherpa_onnx", "--hidden-import=speech",
        "--hidden-import=generation", "--hidden-import=llm_runtime", "--hidden-import=instant_opening",
        "--hidden-import=cloud_runtime", "--hidden-import=provider_settings", "--hidden-import=conversation_store", "--hidden-import=conversation_context",
        "--hidden-import=server", "--hidden-import=ingest", "--hidden-import=search_engine",
        "--hidden-import=store", "--paths=" + str(ROOT),
    ])
    bundle = ROOT / "build_release" / "答辩速查"
    for item in bundle.iterdir():
        dest = ROOT / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    print("Windows app ready:", ROOT / "答辩速查.exe")


if __name__ == "__main__":
    main()
