from pathlib import Path
import pytest
from docx import Document
from pypdf import PdfWriter

from ingest import parse_document


def test_docx_question_boundaries_and_table_order(tmp_path):
    doc = Document()
    doc.add_heading("一 课题1", 1)
    doc.add_heading("001 热应力", 2)
    doc.add_paragraph("问题：应变等于应力吗？")
    doc.add_paragraph("总体回答：不等于，需要材料参数与约束条件。")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "实测量"
    table.cell(0, 1).text = "用途"
    table.cell(1, 0).text = "应变"
    table.cell(1, 1).text = "约束反演"
    doc.add_paragraph("总结：应区分直接测量和模型估计。")
    doc.add_heading("002 指标", 2)
    doc.add_paragraph("问题：2%是已实现的指标吗？")
    doc.add_paragraph("总体回答：这是待验证的项目目标。")
    doc.add_heading("二 参考资料", 1)
    doc.add_paragraph("这段不是第二题的答案。")
    path = tmp_path / "qa.docx"
    doc.save(path)
    result = parse_document(path)
    assert len(result["answers"]) == 2
    a1, a2 = result["answers"]
    assert a1["short_answer"] == "不等于，需要材料参数与约束条件。"
    assert a1["answer"].index("约束条件") < a1["answer"].index("实测量") < a1["answer"].index("总结")
    assert "第二题的答案" not in a2["answer"]
    assert all(a["status"] == "reference" for a in result["answers"])
    assert all(a["sources"][0]["page"] is None for a in result["answers"])
    assert "段" in a1["sources"][0]["locator"]


def test_followups_are_separate_and_plain_notes_not_answers(tmp_path):
    path = tmp_path / "问答.md"
    path.write_text("# 主题\n专家可能问：为什么选择两种光谱？\n总体回答：两者提供互补信息。\n追问：已经验证了吗？\n总体回答：仍需独立样本验证。\n# 附录\n不能混进上一题。", encoding="utf-8")
    result = parse_document(path)
    assert len(result["answers"]) == 2
    assert result["answers"][1]["question"] == "已经验证了吗？"
    assert "附录" not in result["answers"][1]["answer"]
    notes = tmp_path / "笔记.txt"
    notes.write_text("燃料成分检测：\n方法：代表性取样、互补光谱。", encoding="utf-8")
    parsed = parse_document(notes)
    assert parsed["chunks"] and not parsed["answers"]


def test_word_table_question_answer(tmp_path):
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "问题"
    table.cell(0, 1).text = "参考答案"
    table.cell(1, 0).text = "课题1输出什么？"
    table.cell(1, 1).text = "带测点和时间信息的可信数据。"
    path = tmp_path / "table.docx"
    doc.save(path)
    result = parse_document(path)
    assert len(result["answers"]) == 1
    assert result["answers"][0]["sources"][0]["locator"] == "表格 1 · 第 2 行"


def test_empty_pdf_has_ocr_warning(tmp_path):
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with path.open("wb") as out:
        writer.write(out)
    result = parse_document(path)
    assert result["chunks"] == []
    assert result["page_count"] == 1
    assert any("OCR" in warning for warning in result["warnings"])


def test_pdf_page_sequence_is_preserved(tmp_path):
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject
    writer = PdfWriter()
    for text in ["Page one: target 2 percent", "Page two: verification conditions"]:
        page = writer.add_blank_page(width=400, height=400)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 12 Tf 20 350 Td (" + text + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / "pages.pdf"
    with path.open("wb") as out:
        writer.write(out)
    result = parse_document(path)
    assert {c["page"] for c in result["chunks"]} == {1, 2}
    assert "verification" in next(c["text"] for c in result["chunks"] if c["page"] == 2)


def test_original_filename_controls_stored_id_extension(tmp_path):
    path = tmp_path / "stored-id.bin"
    path.write_text("Q: What is measured?\nA: Temperature.", encoding="utf-8")
    parsed = parse_document(path, "notes.txt")
    assert parsed["kind"] == "txt" and len(parsed["answers"]) == 1


def test_word_manual_linebreak_question_answer(tmp_path):
    doc = Document()
    doc.add_paragraph("问：测量对象是什么？\n答：温度。\n问：结果如何验证？\n答：独立测量比对。")
    path = tmp_path / "linebreak.docx"
    doc.save(path)
    result = parse_document(path)
    assert len(result["answers"]) == 2
    assert result["answers"][0]["short_answer"] == "温度。"


def test_legacy_doc_explains_conversion(tmp_path):
    path = tmp_path / "old.doc"
    path.write_bytes(b"not-docx")
    with pytest.raises(ValueError, match="另存为"):
        parse_document(path)
