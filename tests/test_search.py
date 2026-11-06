from search_engine import SearchIndex


def answers():
    return [
        {"id": "tech", "question": "项目的核心创新点是什么？", "answer": "结合多模态在线感知与数字孪生，实现高温燃烧炉智能优化。", "short_answer": "多模态感知与数字孪生。", "keywords": ["创新性", "技术优势"], "aliases": ["新在哪里", "比现有技术好在哪里"], "category": "技术", "status": "reviewed", "favorite": False},
        {"id": "money", "question": "经费预算如何安排？", "answer": "设备费、业务费和劳务费根据任务分工安排。", "keywords": ["经费", "资金"], "aliases": ["钱怎么花"], "category": "经费", "status": "reference", "favorite": True},
        {"id": "check", "question": "考核指标如何验收？", "answer": "对热值预测误差和污染物排放检测精度进行第三方测试。", "keywords": ["考核指标", "验收"], "category": "指标", "status": "draft", "favorite": False},
    ]


def test_chinese_short_query_and_aliases():
    index = SearchIndex(answers())
    assert index.search("指标 怎么验收")[0][0]["id"] == "check"
    assert index.search("钱怎么花")[0][0]["id"] == "money"
    assert index.search("新在哪里")[0][0]["id"] == "tech"
    assert index.search("数字孪生")[0][0]["id"] == "tech"


def test_filters_and_no_match():
    index = SearchIndex(answers())
    assert index.search("羽毛球奥运会")[0] == []
    assert index.search("预算", status="reviewed")[0] == []
    assert [r["id"] for r in index.search("", favorites=True)[0]] == ["money"]
    assert [r["id"] for r in index.search("", category="技术")[0]] == ["tech"]
    assert len(index.search("")[0]) == 3


def test_single_document_bm25_does_not_hide_relevant_hit():
    index = SearchIndex([answers()[0]])
    result = index.search("创新")[0]
    assert result and result[0]["id"] == "tech" and result[0]["score"] > 0


def test_source_search_retains_precise_locator():
    index = SearchIndex([{"id": "chunk", "document_id": "doc", "filename": "申报书.pdf", "text": "NOx 排放应在规定含氧量折算条件下评价。", "page": 12, "locator": "PDF 第12页"}], mode="sources")
    result = index.search("NOx 含氧量")[0][0]
    assert result["document_id"] == "doc"
    assert result["page"] == 12
    assert "nox" in result["matched_terms"]


def test_numeric_units_and_single_character_noise():
    items = [
        {"id": "rate", "question": "热值误差指标", "answer": "目标误差不超过 5%。", "keywords": [], "aliases": []},
        {"id": "other", "question": "人员计划", "answer": "不能混淆现有基础与未来目标。", "keywords": [], "aliases": []},
    ]
    index = SearchIndex(items)
    assert index.search("5%")[0][0]["id"] == "rate"
    assert index.search("不达标")[0] == []
    assert index.search("怎么做")[0] == []


def test_same_question_keeps_different_answers_and_source_versions():
    items = [
        {"id": "planned", "question": "检测误差定义是什么？", "answer": "目标值尚未明确统一口径。", "sources": [{"filename": "方案.docx"}]},
        {"id": "relative", "question": "检测误差定义是什么？", "answer": "此测试范围采用相对误差。", "sources": [{"filename": "测试说明.docx"}]},
        {"id": "absolute", "question": "检测误差定义是什么？", "answer": "低浓度范围另报绝对偏差。", "sources": [{"filename": "条件补充.docx"}]},
    ]
    result, total = SearchIndex(items).search("检测误差定义是什么？")
    assert total == 3 and {row["id"] for row in result} == {"planned", "relative", "absolute"}


def test_modal_words_alone_do_not_match_unrelated_answers_or_sources():
    for modal in ('应该', '应当'):
        text = f'PINN训练{modal}采用独立数据验证。'
        answer_index = SearchIndex([{'id': 'pinn', 'question': 'PINN训练', 'answer': text}])
        source_index = SearchIndex([{'id': 'raw', 'text': text}], mode='sources')
        for index in (answer_index, source_index):
            assert index.search(f'在火星上种土豆{modal}怎么做？')[0] == []
            assert index.search(modal)[0] == []
            assert index.search('PINN独立验证')[0]
