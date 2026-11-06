"""Exercise the packaged EXE with disposable data; --generation also loads the LLM."""
import argparse
import io
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import socket
import tempfile
import time

import httpx
from docx import Document
from pypdf import PdfWriter

if __package__:
    from .verify_generation import content_review_metrics
else:
    from verify_generation import content_review_metrics

ROOT = Path(__file__).resolve().parents[1]


def free_test_port(preferred=0):
    # A fixed port could cause launcher.py to reuse an unrelated running library.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", preferred))
        return probe.getsockname()[1]


def check_generation(client, source_id, question="便携版如何验证？"):
    started = time.monotonic()
    deadline = started + 240
    while time.monotonic() < deadline:
        response = client.get("/api/generation/status")
        response.raise_for_status()
        status = response.json()
        if status.get("ready"):
            break
        if status.get("status") != "loading":
            raise RuntimeError("便携版回答模型不可用：" + json.dumps(status, ensure_ascii=False))
        time.sleep(.5)
    else:
        raise RuntimeError("便携版回答模型未在240秒内就绪")
    print("Packaged answer model ready: " + str(status.get("model")), flush=True)
    events, first_overview_ms, first_answer_ms = [], None, None
    started = time.monotonic()
    with client.stream("POST", "/api/generate", json={"question": question, "length": "detailed", "selection": status.get("selection", {"provider": status.get("provider", "local")})},
                       timeout=httpx.Timeout(180, connect=10)) as stream:
        assert stream.status_code == 200, stream.read().decode("utf-8", errors="replace")
        assert "application/x-ndjson" in stream.headers.get("content-type", "")
        for line in stream.iter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            events.append(event)
            kind = event.get("type")
            if kind == "error":
                raise AssertionError(event.get("message"))
            if kind == "overview_delta" and str(event.get("text", "")).strip() and first_overview_ms is None:
                first_overview_ms = round((time.monotonic() - started) * 1000, 1)
                print("Packaged overview arrived", flush=True)
            if kind == "delta" and str(event.get("text", "")).strip() and first_answer_ms is None:
                first_answer_ms = round((time.monotonic() - started) * 1000, 1)
                print("Packaged complete answer started", flush=True)
    generation_http_ms = round((time.monotonic() - started) * 1000, 1)
    kinds = [event.get("type") for event in events]
    assert kinds and kinds[0] == "turn" and kinds[-1] == "done", kinds
    assert kinds.count("done") == 1, kinds
    overview_positions = [i for i, kind in enumerate(kinds) if kind == "overview_delta"]
    answer_positions = [i for i, kind in enumerate(kinds) if kind == "delta"]
    assert answer_positions and not overview_positions, kinds
    evidence = next(event.get("sources", []) for event in events if event["type"] == "sources")
    assert evidence, "生成必须有本次临时资料的来源"
    assert any(source.get("document_id") == source_id for item in evidence for source in item.get("sources", [])), evidence
    done = events[-1]
    assert done.get("layout") == "defense" and done.get("text", "").strip(), done
    assert all(name in done["text"] for name in ("现场回答", "一句话收束", "依据与提醒")), done
    assert "```mermaid" not in done["text"] and "【答辩思维逻辑图】" not in done["text"]
    opening = next(event["opening"] for event in events if event["type"] == "opening")
    assert done["opening"] == opening
    overview_matches = "".join(event.get("text", "") for event in events if event.get("type") == "overview_delta").strip() == done["overview"].strip()
    final_parts = []
    for event in events:
        if event.get("type") == "answer_reset":
            final_parts.clear()
        elif event.get("type") == "delta":
            final_parts.append(event.get("text", ""))
    answer_matches = "".join(final_parts).strip() == done["text"].strip()
    assert overview_matches and answer_matches, "流式文本与完成事件不一致"
    assert "<think>" not in done["overview"] + done["text"]
    for asset in ("generation.js", "voice.js", "pcm-worklet.js"):
        assert client.get("/static/" + asset).status_code == 200, asset
    return {"question": question, "model": status.get("model"), "backend": status.get("backend"),
            "provider": done.get("provider", "local"), "usage": done.get("usage"), "per_question": done.get("per_question", []),
            "source_count": len(evidence), "ordered_stages": [kind for i, kind in enumerate(kinds) if i == 0 or kind != kinds[i - 1]],
            "sources": evidence,
            "source_filenames": list(dict.fromkeys(source.get("filename", "") for item in evidence for source in item.get("sources", []))),
            "overview": done["overview"], "text": done["text"],
            "overview_chars": len(done["overview"]), "answer_chars": len(done["text"]),
            "overview_stream_matches_done": overview_matches, "answer_stream_matches_done": answer_matches,
            "insufficient_evidence": done.get("insufficient_evidence"),
            "refined": done.get("refined", False), "initial_spoken_chars": done.get("initial_spoken_chars"),
            "first_overview_http_ms": first_overview_ms, "first_answer_http_ms": first_answer_ms,
            "generation_http_ms": generation_http_ms,
            **content_review_metrics(done["text"], evidence)}


def stop_test_process_tree(process):
    if process.poll() is None:
        if os.name == "nt":
            # Kill only this spawned test process and its owned llama-server child.
            subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                            "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", action="store_true", help="另外加载实际回答模型，验证先总述后完整稿的流式生成")
    parser.add_argument("--port", type=int, default=0, help="测试端口，默认选择空闲端口；指定端口必须未被占用")
    parser.add_argument("--output", help="验证报告文件名，默认按是否测试生成分别保存")
    args = parser.parse_args()
    port = free_test_port(args.port)
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="portable-test-", dir=str(build)) as folder:
        # The only cleanup target is a newly created temporary child of our build directory.
        assert Path(folder).resolve().parent == build.resolve()
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        process = subprocess.Popen([str(ROOT / "答辩速查.exe"), "--headless", "--no-browser",
                                    "--port", str(port), "--data-dir", folder], cwd=str(ROOT), env=env,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        checks = []
        generation_result = None
        try:
            with httpx.Client(base_url="http://127.0.0.1:%s" % port, trust_env=False, timeout=30) as client:
                for _ in range(120):
                    if process.poll() is not None:
                        raise RuntimeError("打包程序在启动时退出")
                    try:
                        ready = client.get("/api/health")
                        if ready.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.25)
                else:
                    raise RuntimeError("打包程序未在预期时间内启动")
                assert ready.json()["answer_count"] == 0
                assert ready.json()["version"] == "2.2.2", ready.json()
                empty_generation = client.post("/api/generate", json={"question": "便携版如何验证？"})
                assert empty_generation.status_code == 409 and "资料库为空" in empty_generation.text
                checks.append("EXE在不含Python的PATH下成功启动独立空资料库")
                document = Document()
                document.add_paragraph("问题：便携版如何验证？")
                document.add_paragraph("回答：通过真实导入、检索和来源打开进行验证。")
                docx = io.BytesIO()
                document.save(docx)
                imported = client.post("/api/import", files={"file": ("便携验收.docx", docx.getvalue())})
                assert imported.status_code == 200, imported.text
                assert imported.json()["answer_count"] == 1
                source_id = imported.json()["document_id"]
                found = client.get("/api/search", params={"q": "便携版 验证"}).json()["results"]
                assert found[0]["question"] == "便携版如何验证？"
                assert client.get("/api/documents/%s/file" % source_id).content == docx.getvalue()
                checks.append("DOCX上传、问答提取、关键词检索、原件下载成功")
                for fmt in ("json", "xlsx"):
                    exported = client.get("/api/export", params={"format": fmt})
                    assert exported.status_code == 200
                    restored = client.post("/api/import", files={"file": ("roundtrip." + fmt, exported.content)})
                    assert restored.status_code == 200, restored.text
                checks.append("JSON与Excel导出后可再次导入")
                writer = PdfWriter()
                writer.add_blank_page(width=200, height=200)
                pdf = io.BytesIO()
                writer.write(pdf)
                result = client.post("/api/import", files={"file": ("空白页.pdf", pdf.getvalue())})
                assert result.status_code == 200, result.text
                assert result.json()["warnings"]
                checks.append("PDF解析依赖已打包，空文字层明确显示OCR提示")
                page = client.get("/").text
                checkbox = re.search(r'<input\b[^>]*id="voiceAutoStop"[^>]*>', page).group()
                assert "checked" not in checkbox and "最长3分钟" in page
                voice_js = client.get("/static/voice.js").text
                assert "MAX_SECONDS = 180" in voice_js and "$('voiceAutoStop').checked = false" in voice_js
                app_js = client.get("/static/app.js")
                assert app_js.status_code == 200 and "state.query.length > 500" in app_js.text
                assert "等待精简问题" in app_js.text
                checks.append("最终EXE前端资源包含自动发送默认关闭、三分钟录音及超长问题正确提示")
                for _ in range(120):
                    speech = client.get("/api/speech/status").json()
                    if speech.get("ready"):
                        break
                    assert speech.get("status") == "loading", speech
                    time.sleep(.25)
                else:
                    raise RuntimeError("便携版语音模型未就绪")
                sample = (ROOT / "models" / "sensevoice" / "test_wavs" / "zh.wav").read_bytes()
                recognized = client.post("/api/speech/transcribe", files={"file": ("公开中文测试.wav", sample, "audio/wav")})
                assert recognized.status_code == 200, recognized.text
                assert recognized.json()["text"]
                assert client.get("/static/pcm-worklet.js").status_code == 200
                checks.append("PATH不含Python时真实中文ASR推理成功，语音前端与Worklet已打包")
                if args.generation:
                    counts_before = client.get("/api/health").json()
                    generation_result = check_generation(client, source_id)
                    counts_after = client.get("/api/health").json()
                    assert all(counts_before[key] == counts_after[key] for key in ("answer_count", "document_count", "chunk_count"))
                    checks.append("最终EXE版本2.2.2，PATH不含Python时真实模型单次流式生成现场答辩结构，完成文本一致、来源可追溯且未写入答案卡")
                    # Prove a later imported answer library is available immediately,
                    # rather than generation remaining tied to the initial corpus.
                    later_question = "蓝桥方案的校准周期和记录方式是什么？"
                    assert client.get("/api/search", params={"q": "蓝桥 校准"}).json()["results"] == []
                    later_document = Document()
                    later_document.add_paragraph("问题：" + later_question)
                    later_document.add_paragraph("总体回答：蓝桥方案拟每7天开展一次校准，使用编号B7的记录表登记校准日期、参比值与误差。7天是本次临时验收材料中的计划周期，不表示已经实施。")
                    later_bytes = io.BytesIO()
                    later_document.save(later_bytes)
                    later_import = client.post("/api/import", files={"file": ("后来导入的答案库.docx", later_bytes.getvalue())})
                    assert later_import.status_code == 200, later_import.text
                    assert later_import.json()["answer_count"] == 1, later_import.json()
                    later_source_id = later_import.json()["document_id"]
                    assert later_source_id != source_id
                    later_found = client.get("/api/search", params={"q": "蓝桥 校准"}).json()["results"]
                    assert later_found and later_found[0]["question"] == later_question
                    after_import = client.get("/api/health").json()
                    assert after_import["answer_count"] == counts_after["answer_count"] + 1
                    added_result = check_generation(client, later_source_id, later_question)
                    assert "后来导入的答案库.docx" in added_result["source_filenames"]
                    added_result["source_facts_for_manual_review"] = [
                        "蓝桥方案拟每7天校准一次；这是计划周期，不表示已经实施。",
                        "使用编号B7的记录表登记校准日期、参比值与误差。",
                    ]
                    after_new_generation = client.get("/api/health").json()
                    assert all(after_import[key] == after_new_generation[key] for key in ("answer_count", "document_count", "chunk_count"))
                    generation_result["later_import"] = added_result
                    checks.append("运行中新增第二份答案库后立即检索并生成其独有主题，来源指向新文件，无需重启；回答与新资料事实口径保留供人工核对")
                    subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "verify_competing_answers.py"),
                                    "--url", str(client.base_url)], cwd=str(ROOT), check=True, timeout=180,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    generation_result["competing_answers"] = json.loads((ROOT / "重复问题比较验证.json").read_text(encoding="utf-8"))
                    checks.append("同题初稿与正式方案均被检索并交给真实模型比较，口述稿含具体重点词，结果保留供逐项核对")
                # Delete the imported files through the same public API as the UI.
                removed = []
                for document in client.get("/api/documents").json():
                    response = client.delete("/api/documents/" + document["id"])
                    assert response.status_code == 200, response.text
                    removed.append(response.json())
                empty = client.get("/api/health").json()
                assert all(empty[key] == 0 for key in ("document_count", "answer_count", "chunk_count")), empty
                for mode in ("answers", "sources"):
                    assert client.get("/api/search", params={"q": "", "mode": mode}).json()["results"] == []
                assert client.get("/api/answers").json() == []
                refused = client.post("/api/generate", json={"question": "蓝桥方案的校准周期和记录方式是什么？"})
                assert refused.status_code == 409 and "资料库为空" in refused.text
                checks.append("删除全部DOCX/PDF/JSON/XLSX导入文件后答案、片段和搜索全部为空；空库不再生成旧回答")
        finally:
            stop_test_process_tree(process)
        output = args.output or ("便携版生成验证.json" if args.generation else "便携版验证.json")
        report = {"passed": True, "version": "2.2.2", "checks": checks, "generation_enabled": args.generation,
                  "deletion_results": removed,
                  "scope": "功能验收通过不代表回答事实正确；结构、原文重合及新增资料事实口径留供人工审阅，不设内容或相似度阈值。",
                  "isolated_data": True, "generation": generation_result}
        (ROOT / output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
