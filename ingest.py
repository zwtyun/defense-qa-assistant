"""Offline document ingestion. Answers are extracted, never generated.

Word locations refer to body paragraphs/tables, not invented printed page numbers.
PDF locations use the physical, one-based page sequence in the imported file.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET
import re


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{" + NS["w"] + "}"
QUESTION = re.compile(
    r"^(?:#{1,6}\s*)?(?:(?:问题|专家可能问|专家提问|问|追问)\s*[：:]\s*|"
    r"Q(?:uestion)?\s*\d*\s*[：:.、]\s*)(.+)$", re.I
)
NUMBERED_QUESTION = re.compile(r"^(?:\d{1,3}[.、）)]|[（(]\d{1,3}[）)])\s*(.{3,240}[？?])\s*$")
ANSWER = re.compile(r"^(?:总体回答|参考回答|参考答案|回答|答案|答|A(?:nswer)?)\s*[：:]\s*(.*)$", re.I)
SECTION = re.compile(r"^(?:[一二三四五六七八九十]+[、\s]|第[一二三四五六七八九十\d]+[章节])")
TOPIC = re.compile(r"^\d{2,3}\s+\S")


def _clean(text: str) -> str:
    return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _xml_text(element) -> str:
    parts = []
    for node in element.iter():
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag == W + "tab":
            parts.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            parts.append("\n")
    return _clean("".join(parts))


def _split_text(text: str, limit: int = 1400) -> List[str]:
    """Keep the original text, splitting long blocks at nearby boundaries."""
    text = _clean(text)
    result = []
    while len(text) > limit:
        cut = max(text.rfind(c, limit // 2, limit) for c in ("\n", "。", "；", "！", "？", ". "))
        cut = cut + 1 if cut >= limit // 2 else limit
        result.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        result.append(text)
    return result


def _docx_blocks(path: Path) -> List[Dict[str, Any]]:
    try:
        with ZipFile(path) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > 64 * 1024 * 1024:
                raise ValueError("Word 正文过大，请拆分文档后导入。")
            document = ET.fromstring(z.read(info))
            styles = {}
            if "word/styles.xml" in z.namelist():
                for style in ET.fromstring(z.read("word/styles.xml")).findall("w:style", NS):
                    outline = style.find("w:pPr/w:outlineLvl", NS)
                    name = style.find("w:name", NS)
                    styles[style.get(W + "styleId")] = {
                        "level": int(outline.get(W + "val")) + 1 if outline is not None else None,
                        "name": name.get(W + "val", "") if name is not None else "",
                    }
    except (BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError("无法读取 DOCX，请确认文件未损坏，且已另存为 .docx 格式。") from exc
    body = document.find("w:body", NS)
    if body is None:
        raise ValueError("Word 文件中没有可读正文。")
    blocks = []
    paragraph_no = table_no = 0
    for node in body:
        if node.tag == W + "p":
            paragraph_no += 1
            text = _xml_text(node)
            if not text:
                continue
            style_node = node.find("w:pPr/w:pStyle", NS)
            style = styles.get(style_node.get(W + "val"), {}) if style_node is not None else {}
            local_outline = node.find("w:pPr/w:outlineLvl", NS)
            level = int(local_outline.get(W + "val")) + 1 if local_outline is not None else style.get("level")
            if level is None and re.search(r"heading\s*[1-6]", style.get("name", ""), re.I):
                level = int(re.search(r"[1-6]", style["name"]).group())
            blocks.append({"text": text, "locator": "正文第 {} 段".format(paragraph_no), "page": None,
                           "heading": level, "caption": style.get("name", "").lower() == "caption"})
        elif node.tag == W + "tbl":
            table_no += 1
            rows = []
            for row in node.findall("w:tr", NS):
                cells = []
                for cell in row.findall("w:tc", NS):
                    cells.append("\n".join(_xml_text(p) for p in cell.findall("w:p", NS) if _xml_text(p)))
                if any(cells):
                    rows.append(cells)
            if rows:
                text = "\n".join(" | ".join(cell.replace("\n", "；") for cell in row) for row in rows)
                blocks.append({"text": text, "locator": "表格 {}".format(table_no), "page": None,
                               "heading": None, "caption": False, "rows": rows})
    return blocks


def _plain_blocks(text: str, page: Optional[int] = None) -> List[Dict[str, Any]]:
    blocks = []
    for line_no, line in enumerate(_clean(text).splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.+)", line)
        heading = len(heading_match.group(1)) if heading_match else None
        if heading_match:
            line = heading_match.group(2)
        # A question heading is itself an answer boundary, not a section.
        if heading is None and (SECTION.match(line) or TOPIC.match(line)) and len(line) < 130:
            heading = 1 if SECTION.match(line) else 2
        locator = "PDF 第 {} 页 · 第 {} 行".format(page, line_no) if page else "第 {} 行".format(line_no)
        blocks.append({"text": line, "locator": locator, "page": page, "heading": heading, "caption": False})
    return blocks


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("文本编码无法识别，请另存为 UTF-8 后导入。")


def _pdf_blocks(path: Path):
    from pypdf import PdfReader
    warnings = []
    blocks = []
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF 已加密，请先解除密码保护后导入。")
        empty_pages = []
        for page_no, page in enumerate(reader.pages, 1):
            text = _clean(page.extract_text() or "")
            if not text:
                empty_pages.append(page_no)
            else:
                blocks.extend(_plain_blocks(text, page_no))
        if empty_pages:
            preview = "、".join(str(n) for n in empty_pages[:12])
            if len(empty_pages) > 12:
                preview += "等"
            warnings.append("PDF 第 {} 页未提取到文字（可能是扫描件、图片或空白页）。请先 OCR，再重新导入；这些页当前不能按文字搜索。".format(preview))
        if not blocks:
            warnings.append("未提取到任何可搜索文字。原文件可保留查看，但尚未建立文字索引；本应用不自带 OCR。")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF 解析失败，请检查文件完整性或另存为标准 PDF：{}".format(str(exc)[:180])) from exc
    return blocks, warnings, len(reader.pages)


def _chunks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = []
    pending = []
    length = 0

    def flush():
        nonlocal pending, length
        if not pending:
            return
        text = "\n".join(b["text"] for b in pending)
        locator = pending[0]["locator"]
        if len(pending) > 1 and pending[-1]["locator"] != locator:
            locator += " 至 " + pending[-1]["locator"]
        for part_no, part in enumerate(_split_text(text), 1):
            results.append({"text": part, "locator": locator + (" · 片段 {}".format(part_no) if len(text) > 1400 else ""),
                            "page": pending[0].get("page")})
        pending, length = [], 0

    for block in blocks:
        if pending and (block.get("page") != pending[0].get("page") or block.get("heading")
                        or QUESTION.match(block["text"]) or length + len(block["text"]) > 1200 or block.get("rows")):
            flush()
        pending.append(block)
        length += len(block["text"]) + 1
        if block.get("rows"):
            flush()
    flush()
    return results


def _table_answers(block: Dict[str, Any], filename: str, category: str):
    rows = block.get("rows", [])
    if len(rows) < 2:
        return []
    headers = [re.sub(r"\s+", "", h) for h in rows[0]]
    q_col = next((i for i, h in enumerate(headers) if h in ("问题", "标准问题", "问", "Question", "question")), None)
    a_col = next((i for i, h in enumerate(headers) if h in ("答案", "回答", "参考答案", "参考回答", "标准答案", "Answer", "answer")), None)
    if q_col is None or a_col is None:
        return []
    answers = []
    for row_no, row in enumerate(rows[1:], 2):
        if len(row) <= max(q_col, a_col) or not row[q_col].strip() or not row[a_col].strip():
            continue
        question, answer = row[q_col].strip(), row[a_col].strip()
        locator = block["locator"] + " · 第 {} 行".format(row_no)
        answers.append({"question": question, "answer": answer, "short_answer": answer.splitlines()[0],
                        "keywords": [], "aliases": [], "category": category or "文档问答", "status": "reference",
                        "sources": [{"filename": filename, "locator": locator, "page": block.get("page"), "quote": answer[:1000]}]})
    return answers


def _extract_answers(blocks: List[Dict[str, Any]], filename: str) -> List[Dict[str, Any]]:
    results = []
    section = "文档问答"
    topic = ""
    active = None

    def flush():
        nonlocal active
        if active and active["lines"]:
            answer = "\n".join(active["lines"]).strip()
            first = active["lines"][0]
            am = ANSWER.match(first)
            short = am.group(1).strip() if am else first
            if answer:
                aliases = [active["topic"]] if active["topic"] and active["topic"] != active["question"] else []
                results.append({"question": active["question"], "answer": answer, "short_answer": short,
                                "keywords": [], "aliases": aliases, "category": active["category"], "status": "reference",
                                "sources": [{"filename": filename, "locator": active["locator"], "page": active["page"],
                                             "quote": answer[:1000]}]})
        active = None

    expanded = []
    for block in blocks:
        # Word may store an entire Q/A as one paragraph with manual line breaks.
        lines = block["text"].splitlines()
        if len(lines) > 1 and not block.get("rows") and any(QUESTION.match(line.strip()) or ANSWER.match(line.strip()) for line in lines):
            for line_no, line in enumerate(lines, 1):
                if line.strip():
                    expanded.append(dict(block, text=line.strip(), locator=block["locator"] + " · 段内第 {} 行".format(line_no), heading=None))
        else:
            expanded.append(block)

    for block in expanded:
        text = block["text"].strip()
        qm = QUESTION.match(text) or NUMBERED_QUESTION.match(text)
        if qm:
            flush()
            active = {"question": qm.group(1).strip(), "lines": [], "topic": topic,
                      "category": section, "locator": block["locator"], "page": block.get("page")}
            continue
        if block.get("heading"):
            flush()
            if block["heading"] == 1:
                section = text
                topic = ""
            else:
                topic = re.sub(r"^\d{1,3}\s+", "", text)
            continue
        if block.get("caption"):
            # Captions are navigation/PPT references, not spoken answer content.
            if active and "·" in text:
                pieces = [p.strip() for p in text.split("·")]
                if len(pieces) >= 2:
                    active["category"] = pieces[1]
            continue
        tab_answers = _table_answers(block, filename, section)
        if tab_answers:
            flush()
            results.extend(tab_answers)
            continue
        if active:
            # Stop a completed answer before ancillary preparation notes.
            if active["lines"] and text.startswith(("答辩准备：", "参考文献：", "参考依据：")):
                flush()
                continue
            active["lines"].append(text)
    flush()
    return results


def parse_document(path: Path, display_name: Optional[str] = None) -> Dict[str, Any]:
    """Parse a local source file without modifying it or accessing the network."""
    path = Path(path)
    filename = display_name or path.name
    # Server-side stored file names may be IDs; use the original extension.
    kind = Path(filename).suffix.lower().lstrip(".")
    warnings = []
    page_count = None
    if kind == "docx":
        blocks = _docx_blocks(path)
    elif kind == "pdf":
        blocks, warnings, page_count = _pdf_blocks(path)
    elif kind in ("txt", "md", "markdown"):
        blocks = _plain_blocks(_read_text(path))
    elif kind == "doc":
        raise ValueError("暂不直接读取旧版 .doc，请用 Word/WPS 另存为 .docx 或 PDF 后导入。")
    else:
        raise ValueError("不支持该原文格式，请使用 .docx、.pdf、.txt 或 .md。")
    chunks = _chunks(blocks)
    answers = _extract_answers(blocks, filename)
    if not chunks and kind != "pdf":
        warnings.append("未提取到正文文字，请检查文件是否为空或内容是否仅包含图片。")
    if answers:
        warnings.append("已从明确问答标记提取 {} 条参考答案，未经过人工审核；请核对原文后再标记为已审核。".format(len(answers)))
    else:
        warnings.append("未发现明确的问答格式，已按原文建立检索；可在答案管理中手动整理答案。")
    return {"filename": filename, "kind": kind, "chunks": chunks, "answers": answers, "warnings": warnings,
            "page_count": page_count}
