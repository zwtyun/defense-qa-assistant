"""Select conversational context from every saved turn, without making it evidence.

The persisted transcript is never shortened. This module picks complete, useful
excerpts for a particular question and keeps the prompt within its text budget.
"""
import json
import math
import re

from search_engine import normalize, query_tokens


_GENERIC = set("现在 目前 之前 刚才 刚刚 前面 上面 上述 先前 此前 前文 这里 那里 这个 那个 这些 那些 这样 那样 这种 那种 具体 详细 解释 说明 展开 继续 进一步 接着 回答 问题 提到 说过 说道 意思 理解 就是 指的 什么 为什么 如何 怎样 怎么 能否 是否 请问 确定 采用 需要 进行 还有 方案 效果 原因 措施 步骤 依据 证明 验证 独立 可靠性 可行性 内容 实际 方法 技术 结果 数据 训练 方面 第一 第二 第三 一点 二点 三点 项目 课题 我们 你们 到底 半天 听懂 有啥 比如 比如说".split())
_REFERENCE = re.compile(r"刚才|刚刚|前面|上面|上述|先前|此前|前文|你(?:刚)?说|你提到|你用的|上(?:一|次)|前(?:一|次)|(?:第[一二三四五六七八九\d]+)(?:点|个问题|个分支|个方面)|前者|后者|(?:这|那)(?:个|些|种|样|里)|它们?|继续|接着|进一步|^(?:不是|不对|我是说|我说的是|我问的是|我的意思)")
_ELLIPTICAL = re.compile(r"^(?:那|那么)?(?:为什么|为何|怎么做|怎么验证|如何验证|怎么证明|如何证明|具体怎么做|具体如何做|为什么不行|还能解释|能举例|举个例子|展开说|详细说)(?:一下|呢|吗|呀|啊)?[？?！!。\s]*$")
_CORRECTION = re.compile(r"我(?:已经|已)?确认|以.{0,40}为准|采用.{0,40}(?:版本|依据)|更正|纠正|不是.{0,30}(?:而是|我说的是|我问的是)|应该是|改为|更准确")
_CN = {char: str(index) for index, char in enumerate("零一二三四五六七八九")}


def _terms(text):
    direct, expanded = query_tokens(text)
    if direct.intersection({"仪器", "设备", "传感器"}):
        expanded.update({"仪器", "设备", "传感器", "测量装置", "检测装置"})
    direct = {term for term in direct if len(term) > 1 and term not in _GENERIC and not term.isdigit()
              and not re.fullmatch(r"第[一二三四五六七八九\d]+(?:个|点|轮|个问题)?", term)}
    direct = {term for term in direct if not any(term != other and term in other for other in direct)}
    return direct, {term for term in expanded if len(term) > 1 and term not in _GENERIC and not term.isdigit()}


def _visible_text(turn):
    text = str(turn.get("text") or turn.get("overview") or "").strip()
    # Graphs are hidden in the application and references are not spoken. Keep
    # each answer's actual prose and clarification, including an interrupted end.
    text = re.sub(r"```mermaid\b[^\n]*\n.*?(?:```|\Z)", "", text, flags=re.S | re.I)
    text = re.sub(r"【答辩思维逻辑图】.*?(?=【|\Z)", "", text, flags=re.S)
    text = re.sub(r"【依据与提醒[^】]*】.*?(?=\n\s*#{0,6}\s*问题\s*\d+\s*[：:]|\Z)", "", text, flags=re.S)
    opening = turn.get("opening") or {}
    if not text and opening.get("ready") and opening.get("text"):
        text = "【已显示的开场回答】\n" + str(opening["text"])
    return text.strip()


def _point(text, question):
    problem = re.search(r"第([一二三四五六七八九\d]+)(?:个)?问题", question)
    headings = list(re.finditer(r"(?m)^\s*#{0,6}\s*问题\s*(\d+)\s*[：:]", text))
    if problem and len(headings) > 1:
        number = problem.group(1).translate(str.maketrans(_CN))
        for index, heading in enumerate(headings):
            if heading.group(1) == number:
                return text[heading.start():headings[index + 1].start() if index + 1 < len(headings) else len(text)].strip()
    requested = re.search(r"第([一二三四五六七八九\d]+)(?:点|个分支|个方面)", question)
    if not requested:
        return ""
    number = requested.group(1)
    if number.isdigit() and int(number) < 10:
        number = "零一二三四五六七八九"[int(number)]
    pattern = r"(?ms)^[ \t]*(?:#{1,6}\s*)?(?:\*\*)?第" + re.escape(number) + r"[，,、.．：:].*?(?=^[ \t]*(?:#{1,6}\s*)?(?:\*\*)?第[一二三四五六七八九]|【一句话收束】|【依据与提醒|\Z)"
    matches = list(re.finditer(pattern, text))
    return matches[-1].group(0).strip() if matches else ""


def _excerpt(text, question, limit):
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    if limit < 30:
        return text[:max(0, limit)]
    point = _point(text, question)
    if point:
        text = point
        if len(text) <= limit:
            return text
    terms, expanded = _terms(question)
    quoted = re.findall(r"[“「『\"]([^”」』\"]{2,70})[”」』\"]", question)
    segments = [match for match in re.finditer(r"[^\n。！？!?]+[。！？!?]?", text)]
    if not segments:
        return text[:limit - 1] + "…"
    scores = []
    for index, segment in enumerate(segments):
        value = normalize(segment.group(0))
        score = sum(5 for word in terms if word in value) + sum(1 for word in expanded - terms if word in value)
        score += sum(15 for phrase in quoted if normalize(phrase) in value)
        scores.append((score, index))
    score, best = max(scores, key=lambda item: (item[0], item[1]))
    if not score:
        # "Continue / explain what you just said" should see the delivered end,
        # not just the beginning of a long answer that the expert interrupted.
        best = len(segments) - 1 if _REFERENCE.search(question) or _ELLIPTICAL.match(question) else 0
    start, end = segments[best].span()
    while end - start < limit - 40 and (best > 0 or end < len(text)):
        left = segments[best - 1].start() if best > 0 else start
        right_match = next((part for part in segments if part.start() >= end), None)
        right = right_match.end() if right_match else end
        if start - left and end - left <= limit - 20:
            start, best = left, best - 1
        elif right > end and right - start <= limit - 20:
            end = right
        else:
            break
    result = text[start:end]
    if len(result) > limit - 20:
        result = result[:limit - 20] + "…"
    return ("…" if start else "") + result + ("…" if end < len(text) else "")


def _clarification(question, rows):
    reply = re.sub(r"[\s。！？?!，,、；;:：]", "", question)
    if reply not in {"是", "是的", "对", "对的", "没错", "正确", "前者", "后者", "第一个", "第二个", "第1个", "第2个", "第一种", "第二种", "前一种", "后一种", "第一个选项", "第二个选项"}:
        return ""
    latest = next((row for row in reversed(rows) if row["text"]), None)
    if latest is None:
        return ""
    markers = [match for match in re.finditer(r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?【需要确认】(?:\*\*)?[ \t]*(.*)$", latest["text"])
               if len(re.findall(r"(?m)^\s*```", latest["text"][:match.start()])) % 2 == 0]
    if not markers:
        return ""
    pending = markers[-1]
    confirmation = re.split(r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?(?:问题\s*\d+\s*[:：]|【)", latest["text"][pending.end():], 1)[0].strip()
    confirmation = (pending.group(1) + "\n" + confirmation).strip()
    original = latest["question"]
    headings = list(re.finditer(r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?\*{0,2}问题\s*(\d+)\s*[:：]\s*([^\n]+)", latest["text"][:pending.start()]))
    if headings:
        original = headings[-1].group(2).strip(" *")
    return "；".join([original[:300], confirmation[:300], question]) if confirmation else ""


def _plan(question, history):
    question = str(question or "").strip()
    rows = []
    for index, turn in enumerate(history or []):
        if not isinstance(turn, dict) or not str(turn.get("question") or "").strip():
            continue
        user, text = str(turn["question"]).strip(), _visible_text(turn)
        rows.append({"index": index, "turn": turn, "question": user, "text": text,
                     "user_normalized": normalize(user), "normalized": normalize(user + "\n" + text)})
    if not rows:
        return {"query": question, "rows": [], "primary": None, "dependent": False}
    direct, expanded = _terms(question)
    quotes = [normalize(value) for value in re.findall(r"[“「『\"]([^”」』\"]{2,70})[”」』\"]", question)]
    frequency = {term: sum(term in row["normalized"] for row in rows) for term in direct}
    explicit = bool(_REFERENCE.search(question))
    dependent = explicit or bool(_ELLIPTICAL.match(question))
    if not direct and re.match(r"^(?:那|那么|具体|怎么|如何|为什么|为何|能否|是否|还有|依据)", question):
        dependent = True
    for row in rows:
        user_hits = sum(3 / math.sqrt(max(1, frequency[word])) for word in direct if word in row["user_normalized"])
        answer_hits = sum(4 / math.sqrt(max(1, frequency[word])) for word in direct if word in normalize(row["text"]))
        row["score"] = user_hits + answer_hits + sum(12 for value in quotes if value in row["normalized"])
        row["score"] += sum(.25 for word in expanded - direct if word in row["normalized"])
        row["correction"] = bool(_CORRECTION.search(row["question"]))
        row["point"] = _point(row["text"], question)
    related = [row for row in rows if row["score"] > 0]
    primary = max(related, key=lambda row: (row["score"], row["index"])) if related else None
    # "刚才" can refer back across an intervening topic. A named concept in
    # an older answer is a stronger antecedent than this loose time reference.
    # A direct correction still addresses the latest response, even if its new
    # focus (such as "仪器") also occurs in an older, unrelated topic.
    matched_concept = primary and (any(word in primary["normalized"] for word in direct)
                                   or any(value in primary["normalized"] for value in quotes))
    immediate = re.search(r"上一(?:轮|段|个回答)|你用的|^(?:不是|不对|我是说|我说的是|我问的是|我的意思)", question)
    if immediate or (re.search(r"刚才|刚刚", question) and not matched_concept):
        primary = next((row for row in reversed(rows) if row["text"]), rows[-1])
    # Explicit references to a numbered point must retain the point itself.
    pointed = [row for row in rows if row["point"]]
    if pointed and dependent:
        primary = max(pointed, key=lambda row: (row["score"], row["index"]))
    elif dependent:
        numbered = re.search(r"第([一二三四五六七八九\d]+)(?:轮|个问题)", question)
        if numbered:
            requested = int(numbered.group(1).translate(str.maketrans(_CN))) - 1
            if 0 <= requested < len(rows):
                primary = rows[requested]
    if dependent and primary is None:
        primary = next((row for row in reversed(rows) if row["text"]), rows[-1])
    clarification = _clarification(question, rows)
    if clarification:
        return {"query": clarification, "rows": rows, "primary": rows[-1], "dependent": True}
    # An acknowledgement only becomes a follow-up when a real clarification is
    # pending; do not invent an antecedent for a bare "后者" after normal prose.
    if re.sub(r"[\s。！？?!，,、；;:：]", "", question) in {"是", "是的", "对", "对的", "没错", "正确", "前者", "后者", "第一个", "第二个", "第1个", "第2个", "第一种", "第二种", "前一种", "后一种", "第一个选项", "第二个选项"}:
        return {"query": question, "rows": rows, "primary": None, "dependent": False}
    query = question
    if dependent and primary:
        context_prefix = re.split(r"到底|分别|如何|为什么|怎么|怎样|能否|是否|有哪些|是什么|有啥|有何|需要", primary["question"], 1)[0]
        context_subjects, _ = _terms(context_prefix)
        for row in rows:
            if row["correction"]:
                row["score"] += sum(3 for word in context_subjects if word in row["user_normalized"])
        # Existing assistant prose helps locate an antecedent, but only topic
        # words enter evidence search. Never inject its numerical claims as facts.
        focus = primary["point"] or _excerpt(primary["text"], question, 700)
        if not direct:
            anchor = primary["question"]
            if primary["point"] and re.match(r"^\s*#{0,6}\s*问题\s*\d+", primary["point"]):
                anchor = re.sub(r"^\s*#{0,6}\s*问题\s*\d+\s*[：:]", "", primary["point"].splitlines()[0]).strip()
            if _REFERENCE.search(anchor) or _ELLIPTICAL.match(anchor):
                # Walk a chain of short questions to its explicit subject.
                earlier = [row for row in rows if row["index"] < primary["index"]]
                anchor = next((row["question"] for row in reversed(earlier) if _terms(row["question"])[0] and not _REFERENCE.search(row["question"])), anchor)
            terms, _ = _terms(focus)
            emphasized = re.findall(r"\*\*([^*\n]{2,45})\*\*", focus)
            hints = emphasized + sorted(terms, key=lambda term: (-len(term), term))
            hints = [term for term in hints if not re.search(r"\d", term)][:5]
            query = anchor[:240] + "；" + question
            if hints and (not _terms(anchor)[0] or primary["point"]):
                query += "；" + "、".join(hints)[:120]
        else:
            if primary["point"]:
                query += "；所指分点：" + primary["point"].splitlines()[0].strip(" #*")[:120]
            # A narrowing correction can introduce new words while omitting the
            # subject ("不是说方法，我问的是仪器"). Preserve that subject.
            prefix = re.split(r"到底|分别|如何|为什么|怎么|怎样|能否|是否|有哪些|是什么|有啥|有何|需要", primary["question"], 1)[0]
            subjects, _ = _terms(prefix)
            named = bool(re.search(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9/-]{1,}(?![A-Za-z0-9])", question))
            if subjects and not named and not quotes and not direct.intersection(subjects):
                focus_question = re.split(r"我说的是|我问的是|我的意思是|不是.{0,40}而是", question)[-1].strip(" ，,。")
                focus_question = re.sub(r"^(?:你用的|你说的|刚才说的|那|那么)", "", focus_question).strip()
                query = "、".join(sorted(subjects, key=lambda term: (-len(term), term)))[:140] + "；" + focus_question
        # A named concept already present in the question must not acquire every
        # acronym from a broad previous answer (e.g. NIRS -> LIBS+TDLAS+NIRS).
    return {"query": query, "rows": rows, "primary": primary, "dependent": dependent}


def contextual_query(question, history=None):
    return _plan(question, history)["query"]


def history_records(question, history=None, char_budget=2200):
    plan = _plan(question, history)
    rows, primary = plan["rows"], plan["primary"]
    if not rows or char_budget < 80:
        return []
    chosen = []
    primary_index = primary["index"] if primary else None
    # Search every saved question AND answer. Age is a tie-breaker, never a hard
    # cutoff. Corrections relevant to this topic travel with the selected answer.
    ranked = sorted(rows, key=lambda row: (row["index"] == primary_index,
                    row["correction"] and row["score"] > 0, row["score"], row["index"]), reverse=True)
    for row in ranked:
        if row["index"] != primary_index and row["score"] <= 0:
            # A genuinely new subject has no need for an unrelated old draft.
            continue
        remaining = char_budget - len(json.dumps([item[1] for item in chosen], ensure_ascii=False, separators=(",", ":"))) - 2
        if remaining < 100:
            break
        primary_row = row["index"] == primary_index
        cap = min(remaining, int(char_budget * (.62 if primary_row else .32)))
        record = {"对话轮次": row["index"] + 1,
                  "关联": "本次追问所指" if primary_row and plan["dependent"] else "此前相关问答",
                  "用户提问或纠正": _excerpt(row["question"], question, min(450, max(60, cap // 3)))}
        if row["correction"]:
            record["口径提醒"] = "用户此前的确认或纠正，仅适用于原对象和范围；同对象较新的纠正优先。"
        cost = len(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        answer_budget = max(0, cap - cost - 75)
        if row["text"] and answer_budget >= 60:
            status = row["turn"].get("status", "complete")
            record["历史回答状态"] = "完整回答" if status == "complete" else "未完成，仅包含当时已显示部分；可解释但不可当成完整结论"
            record["历史AI回答（非项目事实依据）"] = _excerpt(row["point"] or row["text"], question, answer_budget)
        if len(json.dumps([item[1] for item in chosen] + [record], ensure_ascii=False, separators=(",", ":"))) <= char_budget:
            chosen.append((row["index"], record))
    return [record for _, record in sorted(chosen)]
