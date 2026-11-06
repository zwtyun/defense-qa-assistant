"""Fast local Chinese lexical search. Scores are ranking values, not confidence."""
import logging
import re
import unicodedata
from collections import Counter

import jieba
from rank_bm25 import BM25Okapi

jieba.setLogLevel(logging.WARNING)
for _word in ("高温燃烧炉", "燃烧炉", "在线感知", "数字孪生", "多模态", "考核指标", "技术路线", "验收", "热值", "结渣", "积灰", "TDLAS", "NOx"):
    jieba.add_word(_word)

STOP = set("的 了 和 与 及 或 是 在 有 为 对 这 那 一个 什么 怎么 如何 为什么 是否 可以 应该 应当 进行 我们 你们 这个 那个 请 请问 一下 项目 课题 回答 问题 方面 情况 相关 到 说 哪些 能够 需要".split())
STOP.update("一 二 三 四 五 六 七 八 九 十 第 个 种 上 下 中 后 前 很 都 吗 呢 么 啊 呀 里 哪 它 其 该 所 等 来 去 会 将 把 被 给 以 于 从 按 做 用 要 能".split())
SCIENTIFIC_SINGLE = set("煤 氢 碳 氧 硫 氮 磷 炉 灰 油 气".split())
NUMBER = re.compile(r"(?<![a-z\d])\d+(?:\.\d+)?")
NUMBER_UNIT = re.compile(r"(?<![a-z\d])(\d+(?:\.\d+)?)\s*(%|℃|°c|ppm|ppb|mg/nm3|mg/m3|kw|mw|万元|亿元|分钟|小时|年|月|秒)")
GROUPS = [
    ["创新", "创新点", "创新性", "先进性", "优势", "突破", "首创", "领先", "全球第一", "国际第一"],
    ["预算", "经费", "资金", "费用", "投入", "花钱"],
    ["考核", "指标", "验收", "评价", "考核指标"],
    ["风险", "困难", "难点", "挑战", "应对"],
    ["合作", "分工", "协作", "中外", "国际合作"],
    ["实施", "进度", "计划", "安排", "周期"],
    ["基础", "积累", "经验", "可行性"],
    ["应用", "示范", "推广", "落地", "场景"],
    ["经济", "效益", "收益", "回报"],
    ["燃料", "煤质", "煤种"],
    ["排放", "污染物", "环保"],
    ["氢气", "h2"],
    ["氧气", "o2"],
    ["二氧化硫", "so2"],
    ["二氧化碳", "co2"],
    ["一氧化碳", "co"],
    ["氮氧化物", "nox"],
    ["热值", "发热量"],
    ["近红外", "nirs"],
    ["激光诱导击穿光谱", "libs"],
    ["可调谐半导体激光吸收光谱", "tdlas"],
    ["物理信息神经网络", "pinn"],
]
PHRASES = {"新在哪里": "创新 优势", "新在哪": "创新 优势", "好在哪里": "优势 对比", "钱花在哪": "经费 预算", "钱怎么花": "经费 预算", "花多少钱": "经费 预算", "怎么验收": "验收 考核指标", "做不出来": "风险 应对", "做不到": "风险 应对", "谁来做": "分工", "谁负责": "分工", "能不能做到": "可行性 基础", "什么时候完成": "进度 周期", "怎么证明": "验证 依据"}


def normalize(text):
    return unicodedata.normalize("NFKC", str(text)).lower().strip()


def tokenize(text):
    normalized = normalize(text)
    tokens = []
    for token in jieba.cut_for_search(normalized):
        token = token.strip()
        if token and token not in STOP and re.search(r"[\w\u4e00-\u9fff]", token) and not re.fullmatch(r"\d", token):
            tokens.append(token)
    # Keep numeric thresholds and their units, including one-digit percentages.
    tokens.extend(NUMBER.findall(normalized))
    tokens.extend(number + unit for number, unit in NUMBER_UNIT.findall(normalized))
    return tokens


def query_tokens(query):
    normalized = normalize(query)
    direct = set(tokenize(normalized))
    expanded = set(direct)
    for phrase, expansion in PHRASES.items():
        if phrase in normalized:
            expanded.update(tokenize(expansion))
    for group in GROUPS:
        if direct.intersection(group) or any(p in normalized for p in group if len(p) > 1 and re.search(r"[\u4e00-\u9fff]", p)):
            expanded.update(group)
    return direct, expanded


class SearchIndex:
    def __init__(self, items, mode="answers"):
        self.items = items
        self.mode = mode
        self.tokens = []
        self.fields = []
        self.token_sets = []
        for item in items:
            if mode == "answers":
                fields = [item.get("question", ""), " ".join(item.get("keywords", [])), " ".join(item.get("aliases", [])), item.get("short_answer", ""), item.get("answer", "")]
                weights = (4, 5, 3, 2, 1)
            else:
                fields = [item.get("filename", ""), item.get("locator", ""), item.get("text", "")]
                weights = (1, 1, 2)
            tokenized_fields = [tokenize(f) for f in fields]
            field_tokens = [set(tokens) for tokens in tokenized_fields]
            tokens = []
            for field, weight in zip(tokenized_fields, weights):
                tokens.extend(field * weight)
            self.fields.append((fields, field_tokens, weights))
            self.tokens.append(tokens or ["__empty__"])
            self.token_sets.append(set(tokens))
        self.bm25 = BM25Okapi(self.tokens) if items else None

    def search(self, query, limit=30, category="", status="all", favorites=False):
        query = normalize(query)
        direct, expanded = query_tokens(query)
        anchors = {term for term in expanded if len(term) > 1 or term in SCIENTIFIC_SINGLE or term.isdigit() or term.isascii()}
        bm_scores = self.bm25.get_scores(list(expanded)) if self.bm25 and expanded else [0] * len(self.items)
        results = []
        for index, item in enumerate(self.items):
            if self.mode == "answers":
                if category and item.get("category") != category:
                    continue
                if status != "all" and item.get("status") != status:
                    continue
                if favorites and not item.get("favorite"):
                    continue
            if not query:
                score = float(bool(item.get("favorite")))
                matched = []
            else:
                if not expanded:
                    continue
                matched = sorted(expanded.intersection(self.token_sets[index]), key=lambda t: (-len(t), t))
                if not matched or (anchors and not anchors.intersection(matched)):
                    continue
                fields, field_tokens, weights = self.fields[index]
                # Broad aliases must match the question/tags, not merely an incidental
                # word buried in a long answer, when there is no literal query hit.
                if self.mode == "answers" and not (direct & anchors).intersection(self.token_sets[index]):
                    if not any(expanded.intersection(tokens) for tokens in field_tokens[:3]):
                        continue
                weighted = sum((len(direct.intersection(tokens)) * 3 + len((expanded - direct).intersection(tokens))) * weight for tokens, weight in zip(field_tokens, weights))
                coverage = len(direct.intersection(self.token_sets[index])) / max(1, len(direct))
                score = float(max(0, bm_scores[index])) + weighted + coverage * 20
                if self.mode == "answers" and re.sub(r"\s+", "", query) in re.sub(r"\s+", "", normalize(item.get("question", ""))):
                    score += 35
                if self.mode == "answers" and any(query == normalize(alias) for alias in item.get("aliases", [])):
                    score += 45
            result = dict(item)
            result["score"] = round(score, 3)
            result["matched_terms"] = matched[:20]
            results.append(result)
        results.sort(key=lambda r: (r["score"], bool(r.get("favorite")), r.get("updated_at", "")), reverse=True)
        return results[:limit], len(results)
