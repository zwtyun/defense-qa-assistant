"""Immediate, source-bound opening statements; no model or network calls.

Prefer the author's own short answer / 总体回答. This is deliberately extractive:
if the library cannot support a useful brief answer, say so instead of filling
the first second with an invented conclusion.
"""
import re

from search_engine import normalize, query_tokens
from conversation_context import contextual_query

_GENERIC = set("现在 目前 这个 那个 怎么 怎样 如何 为什么 什么 是否 能否 已经 完成 具体 相关 分别 总体 说明 问题 回答 项目 课题 核心 主要 这里 里面 这些 那些 之前 一个 进行 已有 相比 相对于 哪里 技术".split())
_META = re.compile(r"^(?:依据|出处|来源|原库题号|对应[页题]|PPT第|指南PDF|资料位置|参考文献|https?://)", re.I)
_QUESTION = re.compile(r"^(?:问题|专家(?:可能)?问|提问|追问|Q\s*\d*)\s*[:：]", re.I)
_SUMMARY = re.compile(r"(?:^|\n)\s*(?:\*\*)?(?:总体回答|简短回答|简要回答|直接回答|核心回答|回答要点|答|回答|A)\s*[:：]\s*(.+)", re.I)
_NUMBERED = re.compile(r"^(?:#{1,6}\s*|(?:第一|第二|第三|第四|第五|第六)(?:点)?[，、:：]|[①②③④⑤⑥]|\d+[、.）)])")
_QUALIFIER = re.compile(r"^(?:但|不过|然而|尚|目前|现阶段|需要注意|需注意|必须注意|不能|并非|仅在|只有|前提|这不|上述.*(?:仅|尚|拟))")
_NUMBERS = re.compile(r"(?:≤|≥|<|>)?\d+(?:\.\d+)?\s*(?:%|％|万元|亿元|天|年|月|小时|分钟|秒|℃|ppm|ppb|MW|kW)?", re.I)
_CN_DIGITS = str.maketrans("一二三四五六七八九", "123456789")


def _plain(value):
    text = str(value or "").replace("**", "").replace("\r", "")
    # Citation markers are not spoken; preserve their provenance separately.
    return re.sub(r"\[(?:\d+[–—,， -]*)+\]", "", text).strip()


def _terms(value):
    direct, expanded = query_tokens(_normalize_tasks(value))
    direct = {term for term in direct if term not in _GENERIC and (len(term) > 1 or term.isdigit())}
    expanded = {term for term in expanded if term not in _GENERIC and len(term) > 1}
    direct = {term for term in direct if not any(term != other and term in other for other in direct)}
    return direct, expanded


def _subject_ids(value):
    return set(re.findall(r"(?:课题|任务)\d+", _normalize_tasks(value)))


def _normalize_tasks(value):
    def number(match):
        raw = match.group(2)
        if raw.isdigit():
            converted = raw
        elif "十" in raw:
            left, right = raw.split("十", 1)
            converted = str(int(left.translate(_CN_DIGITS) or "1") * 10 + int(right.translate(_CN_DIGITS) or "0"))
        else:
            converted = raw.translate(_CN_DIGITS)
        return match.group(1) + converted
    return re.sub(r"(课题|任务)\s*([一二三四五六七八九十\d]+)", number, normalize(value))


def _context_question(question, history):
    # Use the same antecedent as the complete answer. Historical wording only
    # guides retrieval; the opening itself still comes exclusively from evidence.
    return contextual_query(question, history)

def _sentences(value):
    """Keep whole sentences and qualifying clauses, including decimal units."""
    lines = []
    paragraphs = []
    for line in _plain(value).splitlines():
        line = line.strip()
        if not line:
            continue
        structural = _META.match(line) or _QUESTION.match(line) or _NUMBERED.match(line) or re.match(r"^(?:总体回答|简短回答|总结)[:：]", line)
        if paragraphs and not re.search(r"[。！？!?：:]$", paragraphs[-1]) and not structural:
            paragraphs[-1] += line
        else:
            paragraphs.append(line)
    for line in paragraphs:
        line = line.strip()
        if not line or _META.match(line) or _QUESTION.match(line) or line.startswith("```"):
            continue
        line = re.sub(r"^(?:总体回答|简短回答|简要回答|直接回答|核心回答|答|回答|A)\s*[:：]\s*", "", line, flags=re.I)
        line = _NUMBERED.sub("", line).strip()
        if not line or line.endswith(("？", "?", "：", ":")):
            continue
        # Chinese full stops and line boundaries are reliable; commas/semicolons
        # and decimal points do not safely delimit a complete factual claim.
        for sentence in re.findall(r"[^。！？!?]+[。！？!?]?", line):
            sentence = sentence.strip()
            if sentence and not sentence.endswith(("？", "?")):
                lines.append(sentence)
    return lines


def _units(sentences):
    """A following caveat travels with its claim, even if that makes it long."""
    units = []
    for sentence in sentences:
        if units and _QUALIFIER.match(sentence):
            units[-1] += sentence
        else:
            units.append(sentence)
    return units


def _fit_text(value, direct, expanded, prefer_start=True):
    units = _units(_sentences(value))
    options = []
    for start, unit in enumerate(units):
        if len(unit) < 8 or len(unit) > 165:
            continue
        if start and re.match(r"^(?:但|因此|所以|其中|上述|其|它|该|这些|这就|同时)", unit):
            continue  # Do not orphan a conclusion or pronoun from its antecedent.
        variants = [unit]
        if len(unit) < 50 and start + 1 < len(units) and len(unit + units[start + 1]) <= 125:
            variants.append(unit + units[start + 1])
        for text in variants:
            content = normalize(text)
            hits = sum(term in content for term in direct)
            score = hits * 3 + sum(term in content for term in expanded - direct)
            score += 3 if 45 <= len(text) <= 100 else 0
            score -= max(0, len(text) - 105) / 15
            if prefer_start:
                score -= start * 4
            options.append((score, text))
    return max(options, default=(0, ""), key=lambda entry: entry[0])[1]


def _summary_text(answer):
    match = _SUMMARY.search(answer)
    if not match:
        return ""
    result = [match.group(1).strip()]
    for line in answer[match.end():].splitlines():
        line = line.strip()
        if not line:
            continue
        if _NUMBERED.match(line) or re.match(r"^(?:总结|依据|出处|提醒)[:：]", line):
            break
        result.append(line)
    return "\n".join(result)


def _candidate(item, query, direct, expanded, rank):
    if item.get("status") == "draft":
        return None
    title = str(item.get("question") or "") if item.get("kind") != "source" else ""
    answer = _plain(item.get("answer"))
    short = _plain(item.get("short_answer"))
    content = _normalize_tasks(title + "\n" + answer + "\n" + short)
    ids = _subject_ids(query)
    if ids and not ids.issubset(_subject_ids(content)):
        return None
    # Scientific abbreviations identify the object, not merely generic intent.
    acronyms = {normalize(t) for t in re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9/-]{1,}(?![A-Za-z0-9])", query)}
    if acronyms and not all(term in content for term in acronyms):
        return None
    hits = sum(term in content for term in direct)
    if not direct or hits < min(2, len(direct)):
        return None
    title_text = _normalize_tasks(title)
    title_hits = sum(term in title_text for term in direct)
    title_coverage = title_hits / max(1, len(direct))
    raw = item.get("kind") == "source"
    if not raw and title_coverage < .25:
        return None
    summary = _summary_text(answer)
    if short and (_META.match(short) or _QUESTION.match(short)):
        short = ""
    selected = ""
    # A manually prepared overall answer is normally a better opening than an
    # isolated detail in a much longer response.
    for value in (summary, short):
        if value and len(value) >= 12:
            selected = _fit_text(value, direct, expanded)
            if selected:
                break
    if not selected:
        selected = _fit_text(answer, direct, expanded, prefer_start=False)
    if not selected:
        return None
    if re.search(r"在哪.*(?:示范|建设|部署|机组|电厂|场景)|哪[些个].*(?:机组|场景|示范|单位)|(?:示范|机组|电厂).*(?:在哪里|地点|位置)", query):
        concrete_site = re.compile(r"\d+(?:\.\d+)?\s*(?:MW|kW|兆瓦|千瓦)|[\u4e00-\u9fff]{2,}(?:电厂|集团|基地|园区)", re.I)
        if not concrete_site.search(selected):
            alternatives = [unit for unit in _units(_sentences(answer)) if concrete_site.search(unit) and 12 <= len(unit) <= 165]
            if not alternatives:
                return None
            selected = _fit_text("\n".join(alternatives), direct, expanded, prefer_start=False)
            if not selected:
                return None
        if raw and not re.search(r"本(?:项目|课题)|(?:拟|计划|将)(?:在|开展|选择|选取|部署|采用|完成|建设|实施)|示范(?:场景|对象|机组|基地|应用)", selected):
            return None
    if re.search(r"预算|经费|资金", query) and re.search(r"分配|任务|对应|匹配", query):
        allocation = re.compile(r"任务|交付|研究内容|具体用途")
        if not allocation.search(selected):
            alternatives = [unit for unit in _units(_sentences(answer)) if allocation.search(unit) and re.search(r"经费|费用|资金|预算|支出|设备|软件|人员", unit) and 12 <= len(unit) <= 165]
            if not alternatives:
                return None
            selected = _fit_text("\n".join(alternatives), direct, expanded, prefer_start=False)
    method_question = bool(re.search(r"如何(?:开展|实现|建立|进行)|怎样(?:开展|实现)|怎么(?:做|实现)", query))
    if method_question and not re.search(r"通过|利用|采用|结合|建立|组织|训练|标定", selected):
        alternatives = [unit for unit in _units(_sentences(answer)) if re.search(r"通过|利用|采用|结合|建立|组织|训练|标定", unit) and 12 <= len(unit) <= 165
                        and (not ids or ids.issubset(_subject_ids(unit)))]
        if not alternatives:
            return None
        selected = _fit_text("\n".join(alternatives), direct, expanded, prefer_start=False)
    if re.search(r"可信|可靠|保证.*正确|一定.*准确|是否.*(?:正确|有效)", query):
        verification = re.compile(r"独立|验证|校核|不自动|不能自动|并不保证|不保证|需要检")
        if not verification.search(selected):
            alternatives = [unit for unit in _units(_sentences(answer))
                            if verification.search(unit) and 12 <= len(unit) <= 165
                            and (not acronyms or all(term in normalize(unit) for term in acronyms))]
            if not alternatives:
                return None
            selected = _fit_text("\n".join(alternatives), direct, expanded, prefer_start=False)
            if not selected:
                return None
    # Status questions need an actual status/boundary, not a nearby description
    # of expected deliverables or mechanisms.
    if re.search(r"(?:已经|目前|现在|是否已|都已).*(?:完成|实现|验收|采购|支出|达标)", query):
        if not re.search(r"尚|未|已经|已完成|已实现|已验收|已采购|已支出|拟|计划|目标|待", selected):
            return None
    # A comparison of numbered tasks must name each requested task in the
    # opening itself, not only somewhere in a long supporting answer.
    if len(ids) > 1 and not ids.issubset(_subject_ids(selected)):
        alternatives = [unit for unit in _units(_sentences(answer)) if ids.issubset(_subject_ids(unit)) and 12 <= len(unit) <= 165]
        if not alternatives:
            return None
        selected = min(alternatives, key=len)
    if len(ids) > 1 and "；" in selected:
        clauses = selected.split("；")
        # Remove only explicitly independent clauses about unasked numbered
        # tasks. Clauses without an explicit task may contain shared conditions
        # and must remain attached to the statement.
        clauses = [clause for clause in clauses if not _subject_ids(clause) or _subject_ids(clause) & ids]
        selected = "；".join(clauses).rstrip("；。") + "。"
    if raw and sum(term in normalize(selected) for term in direct) < min(2, len(direct)):
        return None
    # Do not confidently answer an unmentioned named object using an incidental
    # match to a broad word such as 预算 or 验证.
    if title_coverage < .5 and hits / max(1, len(direct)) < .45:
        return None
    score = title_coverage * 20 + title_hits * 2 + min(hits, 8) - rank * .12
    score += 2 if summary or short else 0
    score += 1 if item.get("status") == "reviewed" else 0
    if method_question and re.search(r"如何|三步|技术内容|路线|步骤|怎样|对应", title):
        score += 8
    if raw:
        score -= 10
    return {"text": selected, "score": score, "kind": "source_excerpt" if raw else "local_answer",
            "title": title, "item": item, "summary": bool(summary or short)}


def _sources(item, text):
    sources = []
    seen = set()
    for source in item.get("sources") or []:
        if not isinstance(source, dict):
            continue
        key = (source.get("document_id"), source.get("filename"), source.get("locator"))
        if key in seen:
            continue
        seen.add(key)
        sources.append(dict(source))
    if not sources and item.get("id"):
        sources.append({"answer_id": item["id"], "filename": "本地答案库", "locator": item.get("question", ""), "quote": text})
    return sources


def _build_single(question, evidence, history=None):
    """Return an honest immediately-displayable opening from current evidence."""
    if re.search(r"(?:那个|没听清|听不清|不确定|转写).*?(?:还是|或者)", str(question)):
        return {"text": "请先确认这里的数字、单位或术语。两种说法可能影响结论，不能直接按其中一种作答。", "kind": "clarification", "sources": [], "ready": False}
    query = _context_question(question, history)
    direct, expanded = _terms(query)
    candidates = [_candidate(item, query, direct, expanded, index) for index, item in enumerate(evidence or [])]
    candidates = sorted((item for item in candidates if item), key=lambda item: item["score"], reverse=True)
    empty = {"text": "当前资料中未找到可直接使用的简短回答，请等待完整回答并核对依据。", "kind": "insufficient", "sources": [], "ready": False}
    if not candidates:
        return empty
    best = candidates[0]
    # Exact duplicate questions with incompatible numerical answers are not a
    # license to pick whichever file happened to rank first.
    for other in candidates[1:]:
        same_question = normalize(best["title"]).rstrip("？?") == normalize(other["title"]).rstrip("？?")
        if same_question and best["title"] and set(_NUMBERS.findall(best["text"])) != set(_NUMBERS.findall(other["text"])):
            if _NUMBERS.search(best["text"]) and _NUMBERS.search(other["text"]):
                return {"text": "同一问题的资料存在不同数字口径，暂不能直接采用其中一个结论；请先核对适用条件和已确认版本。", "kind": "clarification", "sources": _sources(best["item"], best["text"]) + _sources(other["item"], other["text"]), "ready": False}
    return {"text": best["text"], "kind": best["kind"], "sources": _sources(best["item"], best["text"]),
            "question": str(question), "matched_question": best["title"], "ready": True}


def _split_explicit(question):
    markers = list(re.finditer(r"(?:^|[；;。？?\n，,])\s*(?:第[一二三四五六七八九\d]+(?:个问题|点)?[，、:：]|[一二三四五六七八九\d]+[、）)])", str(question)))
    if len(markers) < 2:
        return [str(question)]
    return [str(question)[marker.end():markers[index + 1].start() if index + 1 < len(markers) else len(str(question))].strip() for index, marker in enumerate(markers)]


def build_opening(question, evidence, history=None):
    """Return source-bound text, preserving explicitly separate question order."""
    questions = _split_explicit(question)
    if len(questions) < 2:
        return _build_single(question, evidence, history)
    parts = [_build_single(part, evidence, history) for part in questions if part]
    ready = all(part["ready"] for part in parts)
    return {"text": "\n".join("%s. %s" % (index + 1, part["text"]) for index, part in enumerate(parts)),
            "kind": "local_answer" if ready else "insufficient", "ready": ready,
            "sources": [source for part in parts for source in part["sources"]], "parts": parts}
