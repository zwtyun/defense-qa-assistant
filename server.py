"""Loopback FastAPI application with local retrieval and selectable generation."""
import io
import json
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.formparsers import MultiPartException, MultiPartParser
from openpyxl import Workbook, load_workbook

from search_engine import SearchIndex
from store import Store, clean_answer, strings
from speech import MAX_AUDIO_BYTES, SpeechError, get_speech_service
from generation import GenerationService, LENGTHS, ndjson
from provider_settings import ProviderSettings
from conversation_store import ConversationStore, ConversationWriter
from instant_opening import build_opening

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
RUN_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
MAX_UPLOAD = 60 * 1024 * 1024
XLSX_HEADERS = ["问题", "简短回答", "详细回答", "关键词", "常见问法", "分类", "状态", "收藏", "出处JSON"]
FIELD_KEYS = ["question", "short_answer", "answer", "keywords", "aliases", "category", "status", "favorite", "sources"]
STATUS_CN = {"参考资料": "reference", "待审核": "reference", "已审核": "reviewed", "草稿": "draft"}
SAMPLE = {"question": "本项目的核心创新点是什么？", "short_answer": "请填写经核对的简短回答。", "answer": "请根据申报书填写完整回答，保留数值、单位和适用条件。", "keywords": ["创新点", "技术优势"], "aliases": ["新在哪里", "与现有技术有何区别"], "category": "创新与技术路线", "status": "draft", "favorite": False, "sources": [{"filename": "申报书.pdf", "locator": "请填写章节", "page": None, "quote": "请填写支撑回答的原文"}]}


class GenerationResponse(StreamingResponse):
    def __init__(self, *args, release_unstarted, **kwargs):
        super().__init__(*args, **kwargs)
        self.release_unstarted = release_unstarted

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.release_unstarted()


def export_workbook(answers):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "答辩答案"
    sheet.append(XLSX_HEADERS)
    for answer in answers:
        values = [answer.get("question", ""), answer.get("short_answer", ""), answer.get("answer", ""),
                      "；".join(answer.get("keywords", [])), "；".join(answer.get("aliases", [])),
                      answer.get("category", ""), answer.get("status", "reference"),
                      "是" if answer.get("favorite") else "否",
                      json.dumps(answer.get("sources", []), ensure_ascii=False)]
        if any(isinstance(value, str) and len(value) > 32767 for value in values):
            raise ValueError("答案或出处超过 Excel 单元格长度限制，请改为导出 JSON 以保留完整内容")
        sheet.append(values)
    # User text must remain text, not executable Excel formulas.
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "-", "@")):
                cell.data_type = "s"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    from openpyxl.styles import Alignment, Font, PatternFill
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="176B58")
    for col, width in zip("ABCDEFGHI", [42, 65, 85, 30, 42, 24, 16, 10, 70]):
        sheet.column_dimensions[col].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def read_answer_file(path, suffix):
    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON 格式不正确，请使用 UTF-8 编码和导出的模板") from exc
        if isinstance(payload, dict):
            payload = payload.get("answers", payload.get("items"))
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise ValueError("JSON 应为答案数组，或包含 answers 数组的对象")
        return [clean_answer(row) for row in payload]
    if suffix == ".xlsx":
        workbook = None
        try:
            workbook = load_workbook(path, read_only=True, data_only=False)
            sheet = workbook.active
            rows = sheet.iter_rows(values_only=True)
            headers = [str(x or "").strip() for x in next(rows, [])]
            names = dict(zip(XLSX_HEADERS, FIELD_KEYS))
            names.update({k: k for k in FIELD_KEYS})
            names.update({"标准问题": "question", "回答": "answer", "答案": "answer", "同义问法": "aliases", "审核状态": "status"})
            mapped = [names.get(h, "") for h in headers]
            if "question" not in mapped or "answer" not in mapped:
                raise ValueError("Excel 表头需要包含“问题”和“详细回答”，请下载模板")
            answers = []
            for number, row in enumerate(rows, 2):
                if not any(value is not None for value in row):
                    continue
                item = {key: (value if value is not None else "") for key, value in zip(mapped, row) if key}
                if item.get("sources"):
                    try:
                        item["sources"] = json.loads(str(item["sources"]))
                    except json.JSONDecodeError as exc:
                        raise ValueError("Excel 第 {} 行出处JSON格式错误".format(number)) from exc
                else:
                    item["sources"] = []
                item["status"] = STATUS_CN.get(str(item.get("status", "")), item.get("status") or "reference")
                item["favorite"] = str(item.get("favorite", "")).lower() in ("是", "true", "1", "yes")
                try:
                    answers.append(clean_answer(item))
                except ValueError as exc:
                    raise ValueError("Excel 第 {} 行：{}".format(number, exc)) from exc
            return answers
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("无法读取 Excel，请检查是否为未加密的 .xlsx 文件") from exc
        finally:
            if workbook is not None:
                workbook.close()
    raise ValueError("答案库支持 JSON、XLSX；Word/PDF 请使用文档导入")


def create_app(data_dir=None, speech_service=None, answer_runtime=None, provider_settings=None, cloud_runtime_factory=None):
    speech = speech_service if speech_service is not None else get_speech_service()

    @asynccontextmanager
    async def lifespan(app):
        speech.start_background()
        await run_in_threadpool(generation.warmup_index)
        try:
            yield
        finally:
            await run_in_threadpool(generation.stop)

    app = FastAPI(title="答辩问题回答检索", version="2.2.2", docs_url=None, redoc_url=None, lifespan=lifespan)
    library = Store(data_dir or os.environ.get("DEFENSE_DATA_DIR") or RUN_ROOT / "data")
    app.state.store = library
    app.state.speech = speech
    settings = provider_settings if provider_settings is not None else ProviderSettings(library.data_dir)
    app.state.provider_settings = settings
    generation = GenerationService(library, answer_runtime, settings=settings, cloud_runtime_factory=cloud_runtime_factory)
    app.state.generation = generation
    conversation = ConversationStore(library.data_dir)
    app.state.conversation = conversation
    conversation_lock = threading.Lock()
    index_cache = {}
    cache_lock = threading.RLock()

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        revision_before = library.revision
        host = request.url.hostname
        if host not in ("127.0.0.1", "localhost", "::1", "testserver"):
            return JSONResponse({"detail": "仅支持本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            parsed = urlparse(origin)
            if parsed.netloc != request.url.netloc:
                return JSONResponse({"detail": "拒绝跨站修改请求，请从本机应用界面操作"}, status_code=403)
        response = await call_next(request)
        if request.method not in ("GET", "HEAD", "OPTIONS") and library.revision != revision_before:
            # Finishing an import/edit includes preparing the new retrieval
            # index. The next question must not pay this multi-second rebuild
            # or see an outdated answer after a deletion.
            await run_in_threadpool(generation.warmup_index)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def required_answer(answer_id):
        result = library.get_answer(answer_id)
        if result is None:
            raise HTTPException(404, "答案不存在或已删除")
        return result

    def required_document(document_id):
        result = library.get_document(document_id)
        if result is None:
            raise HTTPException(404, "原文不存在或已删除")
        return result

    @app.get("/api/health")
    def health():
        provider = settings.public()["provider"]
        return dict(library.stats(), application="defense-search", status="ok", version="2.2.2",
                    offline=provider == "local", retrieval_offline=True, provider=provider)

    @app.get("/api/conversation")
    def conversation_history():
        return {"turns": conversation.all_turns(), "busy": conversation_lock.locked() or generation._busy.locked()}

    @app.delete("/api/conversation")
    def clear_conversation():
        if not conversation_lock.acquire(blocking=False):
            raise HTTPException(409, "回答仍在生成，请先停止后再清空对话。")
        reserved = False
        try:
            reserved = generation.reserve()
            if not reserved:
                raise HTTPException(409, "回答或连接测试仍在进行，请先停止并稍候再清空对话。")
            return {"deleted": conversation.clear()}
        finally:
            if reserved:
                generation._busy.release()
            conversation_lock.release()

    @app.get("/api/generation/status")
    def generation_status():
        return generation.status()

    @app.get("/api/generation/settings")
    def generation_settings():
        return dict(settings.public(), busy=generation._busy.locked())

    @app.put("/api/generation/settings")
    def save_generation_settings(payload: dict):
        if not generation.reserve():
            raise HTTPException(409, "回答或连接测试仍在进行，请先停止并稍候再保存设置。")
        try:
            return settings.update(payload)
        finally:
            generation._busy.release()

    @app.post("/api/generation/test")
    async def test_generation_connection(request: Request, payload: dict = None):
        if not generation.reserve():
            raise HTTPException(409, "回答或连接测试仍在进行，请先停止或稍候再测试。")
        try:
            config = settings.preview(payload or {})
        except Exception:
            generation._busy.release()
            raise
        result = await generation.test_connection(config, request)
        if not result.get("ok"):
            raise HTTPException(502, result["message"])
        return result

    @app.post("/api/generate")
    async def generate(request: Request, payload: dict):
        received_at = time.perf_counter()
        question = payload.get("question")
        length = payload.get("length", "detailed")
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            raise HTTPException(400, "请输入500字以内的完整问题。")
        if not isinstance(length, str) or length not in LENGTHS:
            raise HTTPException(400, "回答长度应为 brief、standard 或 detailed。")
        question = question.strip()
        if not conversation_lock.acquire(blocking=False):
            raise HTTPException(409, "上一条回答仍在生成，请先停止或稍候再试。")
        if not generation.reserve():
            conversation_lock.release()
            raise HTTPException(409, "上一条回答仍在生成，请先停止或稍候再试。")
        reservation = {"started": False, "released": False}
        try:
            # Serialize history reads with the prior response's final save: the
            # model worker can finish before its last event reaches the browser.
            history = conversation.all_turns()
            evidence_revision = library.revision
            evidence = await run_in_threadpool(generation.retrieve, question, history=history)
            if not evidence and not library.all_chunks() and not any(row.get("status") != "draft" for row in library.all_answers()):
                raise HTTPException(409, "资料库为空，请先导入 Word、PDF 或答案库后再生成回答。")
            # Capture one provider after reserving the slot. Settings cannot
            # change between readiness, overview and full-answer generation.
            runtime = generation.runtime()
            info = generation.status(runtime)
            selection = payload.get("selection")
            if selection != info["selection"] and not (selection is None and info["selection"] == {"provider": "local"}):
                raise HTTPException(409, "回答方式或API设置已变更，请刷新页面后重新生成。")
            if not info.get("ready"):
                raise HTTPException(503, info.get("message") or "回答模型尚未就绪，请稍候；原答案检索仍可使用。")
            # This is local extraction, not another model round trip. Save the
            # whole usable opening before waiting for the provider's first token.
            opening = await run_in_threadpool(build_opening, question, evidence, history=history)
            opening["elapsed_ms"] = round((time.perf_counter() - received_at) * 1000, 1)
            if library.revision != evidence_revision:
                raise HTTPException(409, "资料库已变更，请重新生成回答。")
            turn = conversation.start(question, info.get("provider", "local"), info.get("model", ""), opening=opening)
            writer = ConversationWriter(conversation, turn)
        except BaseException:
            generation.release_unstarted(reservation)
            conversation_lock.release()
            raise

        response_released = False

        def release_response():
            nonlocal response_released
            if response_released:
                return
            response_released = True
            try:
                # Synchronous short SQLite writes also run under ASGI cancellation.
                writer.finish()
            finally:
                generation.release_unstarted(reservation)
                conversation_lock.release()

        async def output():
            events = generation.events(question, length, evidence, request, reservation,
                                       evidence_revision=evidence_revision, runtime=runtime, history=history)
            try:
                if library.revision != evidence_revision:
                    # Nothing from this turn has reached the browser yet.
                    turn.pop("opening", None)
                    event = {"type": "error", "code": "library_changed", "message": "资料库已变更，请重新生成回答。", "turn_id": turn["id"]}
                    writer.consume(event)
                    yield ndjson({"type": "turn", "turn": dict(turn)})
                    yield ndjson(event)
                    return
                yield ndjson({"type": "turn", "turn": dict(turn)})
                yield ndjson({"type": "opening", "opening": opening})
                async for event in events:
                    if event.get("type") == "done":
                        event = dict(event, opening=opening)
                    writer.consume(event)
                    if event.get("type") in ("done", "error"):
                        event = dict(event, turn_id=turn["id"])
                    yield ndjson(event)
            except Exception:
                writer.finish("error", "回答连接意外结束，已保留收到的内容。")
                raise
            finally:
                try:
                    await events.aclose()
                finally:
                    release_response()

        return GenerationResponse(output(), release_unstarted=release_response,
                                  media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/speech/status")
    def speech_status():
        return speech.status()

    @app.post("/api/speech/transcribe")
    async def transcribe(request: Request):
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            raise HTTPException(400, "请通过语音提问或 WAV 文件入口提交录音。")
        # Starlette normally spools uploads above 1 MB onto disk. A three-minute
        # question exceeds that, so this route keeps its bounded upload in RAM.
        body = bytearray()
        async for block in request.stream():
            body.extend(block)
            if len(body) > MAX_AUDIO_BYTES + 65536:
                raise HTTPException(413, "录音文件不能超过 6 MB，请缩短问题后重试。")

        async def audio_body():
            yield bytes(body)

        parser = MultiPartParser(request.headers, audio_body(), max_files=1, max_fields=0)
        parser.spool_max_size = MAX_AUDIO_BYTES + 65537
        try:
            form = await parser.parse()
        except (MultiPartException, ValueError) as exc:
            raise HTTPException(400, "录音上传格式不正确，请重新录制。") from exc
        try:
            file = form.get("file")
            if file is None or not hasattr(file, "read"):
                raise HTTPException(400, "没有收到录音文件，请重新录制。")
            audio = await file.read(MAX_AUDIO_BYTES + 1)
            if len(audio) > MAX_AUDIO_BYTES:
                raise HTTPException(413, "录音文件不能超过 6 MB，请缩短问题后重试。")
            return await run_in_threadpool(speech.transcribe, audio)
        except SpeechError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        finally:
            await form.close()

    @app.get("/api/search")
    def search(q: str = Query("", max_length=500), mode: str = "answers", category: str = "", status: str = "all", favorites: bool = False, limit: int = Query(30, ge=1, le=200)):
        if mode not in ("answers", "sources") or status not in ("all", "reference", "reviewed", "draft"):
            raise HTTPException(400, "无效的检索条件")
        started = time.perf_counter()
        with cache_lock:
            revision, index = index_cache.get(mode, (-1, None))
            if revision != library.revision or index is None:
                snapshot_revision = library.revision
                index = SearchIndex(library.all_answers() if mode == "answers" else library.all_chunks(), mode)
                # If a concurrent edit occurs while indexing, next query rebuilds.
                index_cache[mode] = (snapshot_revision, index)
        results, total = index.search(q, limit=limit, category=category, status=status, favorites=favorites)
        return {"results": results, "total": total, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "query": q, "mode": mode}

    @app.get("/api/answers")
    def answers():
        return library.all_answers()

    @app.post("/api/answers", status_code=201)
    def add_answer(payload: dict):
        return library.save_answer(payload)

    @app.get("/api/answers/{answer_id}")
    def answer(answer_id: str):
        return required_answer(answer_id)

    @app.put("/api/answers/{answer_id}")
    def edit_answer(answer_id: str, payload: dict):
        existing = required_answer(answer_id)
        existing.update(payload)
        return library.save_answer(existing, answer_id)

    @app.delete("/api/answers/{answer_id}")
    def remove_answer(answer_id: str):
        required_answer(answer_id)
        library.delete_answer(answer_id)
        return {"message": "答案已删除"}

    @app.post("/api/answers/{answer_id}/favorite")
    def favorite(answer_id: str, payload: dict):
        required_answer(answer_id)
        if not isinstance(payload.get("favorite"), bool):
            raise HTTPException(400, "favorite 必须为 true 或 false")
        library.favorite_answer(answer_id, payload["favorite"])
        return required_answer(answer_id)

    @app.get("/api/documents")
    def documents():
        return library.all_documents()

    def document_deletion_message(removed):
        message = "已删除 {} 个导入文件、{} 条关联答案及 {} 个原文片段。".format(
            removed["removed_documents"], removed["removed_answers"], removed["removed_chunks"])
        if removed.get("missing_document_ids"):
            message += "另有 {} 个文件已不存在。".format(len(removed["missing_document_ids"]))
        if removed.get("cleanup_warnings"):
            message += "注意：{} 个应用副本未能清理，资料库记录已移除；请关闭占用文件后检查应用的 data/documents 目录。".format(len(removed["cleanup_warnings"]))
        return message

    @app.post("/api/documents/delete-batch")
    def remove_documents(payload: dict):
        removed = library.delete_documents(payload.get("document_ids"))
        return dict(removed, message=document_deletion_message(removed))

    @app.delete("/api/documents/{document_id}")
    def remove_document(document_id: str):
        required_document(document_id)
        removed = library.delete_document(document_id)
        if not removed:
            raise HTTPException(404, "原文不存在或已删除")
        return dict(removed, message=document_deletion_message(dict(removed, removed_documents=1)))

    @app.get("/api/documents/{document_id}/file")
    def document_file(document_id: str):
        document = required_document(document_id)
        path = (library.documents_dir / document["stored_name"]).resolve()
        if path.parent != library.documents_dir or not path.is_file():
            raise HTTPException(404, "原文文件未找到")
        suffix = path.suffix.lower()
        media = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".txt": "text/plain", ".md": "text/plain"}.get(suffix, "application/octet-stream")
        return FileResponse(path, media_type=media, filename=document["filename"], content_disposition_type="inline" if suffix in (".pdf", ".txt", ".md") else "attachment")

    @app.get("/api/documents/{document_id}/preview")
    def document_preview(document_id: str, page: int = Query(1, ge=1)):
        document = required_document(document_id)
        chunks = library.all_chunks(document_id)
        page_chunks = [c for c in chunks if c.get("page") == page] if document["kind"] == "pdf" else chunks
        return {"document": document, "filename": document["filename"], "chunks": page_chunks,
                "pages": [{"page": c.get("page"), "locator": c.get("locator", ""), "text": c["text"]} for c in page_chunks],
                "page": page, "total_pages": document["pages"], "text": "\n\n".join(c["text"] for c in page_chunks)}

    @app.post("/api/import")
    def import_file(file: UploadFile = File(...), kind: str = Form("auto")):
        if kind not in ("auto", "answers", "sources"):
            raise HTTPException(400, "无效的导入类型")
        filename = Path((file.filename or "document").replace("\\", "/")).name
        suffix = Path(filename).suffix.lower()
        if suffix not in (".json", ".xlsx", ".docx", ".pdf", ".txt", ".md"):
            raise HTTPException(400, "支持 DOCX、PDF、TXT、MD、XLSX、JSON；旧版 DOC 请另存为 DOCX 或 PDF")
        document_id = uuid.uuid4().hex
        stored_name = document_id + suffix
        path = library.documents_dir / stored_name
        kept = False
        try:
            size = 0
            with path.open("wb") as output:
                while True:
                    block = file.file.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, "单个文件不能超过 60 MB")
                    output.write(block)
            if size == 0:
                raise HTTPException(400, "文件为空")
            if suffix in (".json", ".xlsx"):
                if kind == "sources":
                    raise HTTPException(400, "JSON/XLSX 用于导入答案库，请选择自动识别或答案库")
                answer_rows = read_answer_file(path, suffix)
                # Portable exports may refer to document IDs absent in this library.
                detached = 0
                for row in answer_rows:
                    for source in row.get("sources", []):
                        if source.get("document_id") and not library.get_document(source["document_id"]):
                            source["document_id"] = ""
                            detached += 1
                warnings = (["部分原文不在当前资料库中，已保留出处文字；导入的答案仍与本答案库文件关联，删除该文件会同步删除这些答案。"] if detached else [])
                library.import_document(filename, stored_name, suffix.lstrip("."), [], answers=answer_rows,
                                        warning="；".join(warnings), document_id=document_id)
                kept = True
                return {"message": "已导入 {} 条答案".format(len(answer_rows)), "answer_count": len(answer_rows), "chunk_count": 0,
                        "document_count": 1, "document_id": document_id, "warnings": warnings}
            from ingest import parse_document
            parsed = parse_document(path, display_name=filename)
            chunks = parsed.get("chunks", [])
            warnings = parsed.get("warnings", [])
            answer_rows = parsed.get("answers", []) if kind != "sources" else []
            for row in answer_rows:
                row["status"] = "reference"
                for source in row.get("sources", []):
                    source["document_id"] = document_id
                clean_answer(row)
            if kind == "answers" and not answer_rows:
                warnings.append("未识别到明确的问答结构，已作为原文保存；可在原文检索中找到并手工整理答案。")
            pages = parsed.get("pages") or parsed.get("page_count") or max([c.get("page") or 0 for c in chunks] or [0])
            library.import_document(filename, stored_name, parsed.get("kind", suffix.lstrip(".")), chunks, answers=answer_rows,
                                    pages=pages, warning="；".join(warnings), document_id=document_id)
            kept = True
            return {"message": "已导入 {}，提取 {} 条参考答案、{} 个原文片段".format(filename, len(answer_rows), len(chunks)), "answer_count": len(answer_rows), "chunk_count": len(chunks), "document_count": 1, "document_id": document_id, "warnings": warnings}
        finally:
            file.file.close()
            if not kept and path.exists():
                path.unlink()

    @app.get("/api/export")
    def export(format: str = "json"):
        rows = library.all_answers()
        if format == "xlsx":
            return Response(export_workbook(rows), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="defense-answers.xlsx"'})
        if format != "json":
            raise HTTPException(400, "导出格式为 json 或 xlsx")
        return Response(json.dumps({"version": 1, "answers": rows}, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="defense-answers.json"'})

    @app.get("/api/template")
    def template(format: str = "xlsx"):
        if format == "xlsx":
            return Response(export_workbook([SAMPLE]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="answer-template.xlsx"'})
        if format != "json":
            raise HTTPException(400, "模板格式为 json 或 xlsx")
        return Response(json.dumps({"answers": [SAMPLE]}, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="answer-template.json"'})

    @app.get("/")
    def home():
        path = ROOT / "static" / "index.html"
        if path.exists():
            return FileResponse(path, media_type="text/html")
        return Response("界面文件尚未就绪", status_code=503)

    static = ROOT / "static"
    if static.exists():
        app.mount("/static", StaticFiles(directory=str(static)), name="static")
    return app


app = create_app()
