"""User-facing integration workflows using an isolated temporary library."""
import io
import json
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pypdf import PdfWriter

from server import create_app


def make_word():
    document = Document()
    document.add_heading("答辩问答", 0)
    document.add_paragraph("问题：创新点是什么？")
    document.add_paragraph("总体回答：本方案通过在线感知和多源信息融合改进观测能力。")
    document.add_paragraph("问题：如何进行验收？")
    document.add_paragraph("回答：在规定工况下对照考核指标逐项验证。")
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def test_document_to_answer_search_edit_and_reopen(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        word = make_word()
        response = client.post("/api/import", files={"file": ("准备问答.docx", word)})
        assert response.status_code == 200, response.text
        assert response.json()["answer_count"] == 2
        document_id = response.json()["document_id"]
        results = client.get("/api/search", params={"q": "验收 指标"}).json()["results"]
        assert results and "验收" in results[0]["question"]
        answer = results[0]
        assert answer["status"] == "reference"
        assert answer["sources"][0]["document_id"] == document_id
        assert answer["sources"][0]["page"] is None
        preview = client.get("/api/documents/%s/preview" % document_id)
        assert preview.status_code == 200
        assert "规定工况" in preview.json()["text"]
        assert client.get("/api/documents/%s/file" % document_id).content == word
        changed = client.put("/api/answers/" + answer["id"], json={"status": "reviewed", "short_answer": "逐项验证规定工况下的考核指标。"})
        assert changed.status_code == 200
        assert client.post("/api/answers/%s/favorite" % answer["id"], json={"favorite": True}).status_code == 200
        filtered = client.get("/api/search", params={"q": "验收", "status": "reviewed", "favorites": True}).json()
        assert filtered["total"] == 1
        assert filtered["results"][0]["id"] == answer["id"]
        assert client.get("/api/search", params={"q": "ZXQ999QWERTY"}).json()["results"] == []
    with TestClient(create_app(tmp_path)) as reopened:
        assert reopened.get("/api/answers/" + answer["id"]).json()["favorite"] is True
        sources = reopened.get("/api/search", params={"q": "多源信息", "mode": "sources"}).json()
        assert sources["results"]


def test_export_import_roundtrip_and_atomic_validation(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    with TestClient(create_app(first)) as client:
        assert client.post("/api/import", files={"file": ("问答.docx", make_word())}).status_code == 200
        exported_json = client.get("/api/export?format=json").content
        exported_xlsx = client.get("/api/export?format=xlsx").content
        workbook = load_workbook(io.BytesIO(exported_xlsx))
        assert workbook.active.max_row == 3
    with TestClient(create_app(second)) as client:
        imported = client.post("/api/import", files={"file": ("答案.json", exported_json)})
        assert imported.status_code == 200
        assert imported.json()["answer_count"] == 2
        assert imported.json()["warnings"]  # Missing original files are explicitly reported.
        for answer in client.get("/api/answers").json():
            assert not answer["sources"][0]["document_id"]
            assert answer["sources"][0]["quote"]
            assert any(source["document_id"] == imported.json()["document_id"] for source in answer["sources"])
        assert client.post("/api/import", files={"file": ("答案.xlsx", exported_xlsx)}).status_code == 200
        count = client.get("/api/health").json()["answer_count"]
        bad = json.dumps([{"question": "有效问题", "answer": "有效答案"}, {"question": "缺失回答"}], ensure_ascii=False).encode()
        assert client.post("/api/import", files={"file": ("坏数据.json", bad)}).status_code == 400
        assert client.get("/api/health").json()["answer_count"] == count


def test_blank_pdf_reported_and_document_delete_removes_answers(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        data = io.BytesIO()
        writer.write(data)
        blank = client.post("/api/import", files={"file": ("扫描件.pdf", data.getvalue())})
        assert blank.status_code == 200
        assert blank.json()["answer_count"] == 0
        assert blank.json()["warnings"]
        imported = client.post("/api/import", files={"file": ("问答.docx", make_word())}).json()
        doc_id = imported["document_id"]
        assert client.delete("/api/documents/" + doc_id).status_code == 200
        assert client.get("/api/health").json()["answer_count"] == 0
        assert client.get("/api/documents/%s/file" % doc_id).status_code == 404
        assert client.get("/api/answers").json() == []
        assert client.get("/api/search", params={"q": "验收"}).json()["results"] == []
        assert client.delete("/api/documents/" + blank.json()["document_id"]).status_code == 200
    with TestClient(create_app(tmp_path)) as reopened:
        assert all(reopened.get("/api/health").json()[key] == 0 for key in ("answer_count", "document_count", "chunk_count"))


@pytest.mark.parametrize("format", ["json", "xlsx"])
def test_answer_library_upload_has_its_own_deletion_boundary(tmp_path, format):
    with TestClient(create_app(tmp_path)) as client:
        original = client.post("/api/import", files={"file": ("问答.docx", make_word())}).json()
        exported = client.get("/api/export", params={"format": format}).content
        copied = client.post("/api/import", files={"file": ("答案库." + format, exported)})
        assert copied.status_code == 200, copied.text
        copy_id = copied.json()["document_id"]
        assert client.get("/api/documents/%s/file" % copy_id).content == exported
        assert client.get("/api/health").json()["answer_count"] == 4
        # The same question was imported twice, but each file owns its own rows.
        removed = client.delete("/api/documents/" + original["document_id"]).json()
        assert removed["removed_answers"] == 2
        retained = client.get("/api/answers").json()
        assert len(retained) == 2
        assert all(answer["origin_document_id"] == copy_id for answer in retained)
        assert all(all(source["document_id"] != original["document_id"] for source in answer["sources"]) for answer in retained)
        assert client.get("/api/search", params={"q": "验收"}).json()["results"]
        assert client.delete("/api/documents/" + copy_id).json()["removed_answers"] == 2
        assert client.get("/api/search", params={"q": "验收"}).json()["results"] == []
        assert list((tmp_path / "documents").iterdir()) == []
    with TestClient(create_app(tmp_path)) as reopened:
        assert all(reopened.get("/api/health").json()[key] == 0 for key in ("answer_count", "document_count", "chunk_count"))


def test_pdf_question_answer_and_chunks_are_removed_together(tmp_path):
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 350 Td (Q: How is calibration checked?) Tj 0 -20 Td (A: Use the independent reference sample.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    raw = io.BytesIO()
    writer.write(raw)
    with TestClient(create_app(tmp_path)) as client:
        imported = client.post("/api/import", files={"file": ("calibration.pdf", raw.getvalue())})
        assert imported.status_code == 200, imported.text
        assert imported.json()["answer_count"] == 1
        answer = client.get("/api/answers").json()[0]
        document_id = imported.json()["document_id"]
        assert answer["origin_document_id"] == document_id
        assert answer["sources"][0]["document_id"] == document_id and answer["sources"][0]["page"] == 1
        assert client.get("/api/search", params={"q": "calibration"}).json()["results"]
        assert client.get("/api/search", params={"q": "reference", "mode": "sources"}).json()["results"]
        removed = client.delete("/api/documents/" + document_id).json()
        assert removed["removed_answers"] == 1 and removed["removed_chunks"] > 0
        assert all(client.get("/api/health").json()[key] == 0 for key in ("answer_count", "document_count", "chunk_count"))
        assert client.get("/api/search", params={"q": "reference", "mode": "sources"}).json()["results"] == []


def test_serves_offline_assets_and_rejects_invalid_imports(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "答辩" in page.text
        assert "cdn.jsdelivr" not in page.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/styles.css").status_code == 200
        assert client.post("/api/import", files={"file": ("旧格式.doc", b"old")}).status_code == 400
        assert client.post("/api/import", files={"file": ("空.docx", b"")}).status_code == 400
        assert client.get("/api/documents/not-a-document/file").status_code == 404
        assert client.post("/api/answers", headers={"Origin": "https://untrusted.example"}, json={"question": "q", "answer": "a"}).status_code == 403
