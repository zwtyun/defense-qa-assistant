"""Conversational recall across answers, interruptions and changes of topic."""
import json

import pytest

from conversation_context import contextual_query, history_records
from generation import GenerationService, build_messages
from instant_opening import build_opening
from store import Store


def serialized(question, history, budget=2200):
    return json.dumps(history_records(question, history, budget), ensure_ascii=False)


def test_old_answer_only_concept_is_recalled_after_many_unrelated_turns():
    history = [{"question": "研究方案怎么安排？", "status": "complete", "text":
                "背景解释。" * 400 + "留一煤种验证是把一种煤全部留作独立检验，不能混入训练样本。" + "其他说明。" * 400}]
    history += [{"question": "预算条目%s怎么安排？" % i, "status": "complete", "text": "采购仍处于准备阶段。"} for i in range(85)]
    result = serialized("之前你说的“留一煤种验证”具体是什么意思？", history)
    assert "把一种煤全部留作独立检验" in result
    assert "不能混入训练样本" in result
    assert len(result) < 2300 and len(history) == 86


@pytest.mark.parametrize("status", ["cancelled", "error", "interrupted"])
def test_delivered_partial_answer_is_available_when_expert_interrupts(status):
    history = [{"question": "热响应怎样测量？", "status": status, "text":
                "## 问题1：测量\n【现场回答】\n### 第一，测温\n采用贴壁热电偶测量局部壁温，正在解释安装方式"}]
    prompt = build_messages("你刚才说的贴壁热电偶怎么安装？", [], history=history)[1]["content"]
    assert "贴壁热电偶测量局部壁温" in prompt
    assert "未完成，仅包含当时已显示部分" in prompt
    assert "非项目事实依据" in prompt and "本轮承接定位" in prompt


def test_only_opening_before_cancel_is_also_remembered_as_delivered_context():
    history = [{"question": "传感器如何验证？", "status": "cancelled", "text": "",
                "opening": {"ready": True, "text": "我们拟采用配对取样和独立参照验证，不能认为一次标定就是验收完成。"}}]
    result = serialized("你刚才说的配对取样是什么？", history)
    assert "已显示的开场回答" in result and "配对取样和独立参照" in result
    assert "未完成" in result


def test_narrowing_correction_keeps_original_subject_in_retrieval_and_prompt(tmp_path):
    history = [{"question": "热应力到底怎么测，有啥用，听了半天没听懂", "status": "complete",
                "text": "热应力不能由单个传感器直接读取，我们测壁温、局部热流和应变，再通过结构模型反演。"}]
    question = "不是，我不是说方法，我说的是你用的仪器，比如说煤炉 窑炉怎么测"
    query = contextual_query(question, history)
    assert "热应力" in query and "仪器" in query
    library = Store(tmp_path)
    expected = library.save_answer({"question": "热应力监测用哪些仪器？", "answer": "总体回答：热应力监测拟结合热电偶、红外测温、热流计和应变片获取数据，具体安装与量程仍需按锅炉和窑炉条件验证。"})
    library.save_answer({"question": "仪器采购预算有哪些？", "answer": "仪器预算包括采购费用和软件费用，尚未完成采购。"})
    evidence = GenerationService(library, None).retrieve(question, history)
    assert expected["id"] in {row["id"] for row in evidence}
    prompt = build_messages(question, evidence, history=history)[1]["content"]
    assert "热应力不能由单个传感器直接读取" in prompt
    assert "当前专家的新增关注" in prompt and "热电偶" in prompt


def test_explicit_new_subject_does_not_inherit_previous_optical_topic():
    history = [{"question": "LIBS和NIRS怎样融合？", "status": "complete", "text": "LIBS与NIRS提供互补光谱。"}]
    question = "为什么选择锅炉和窑炉两个示范场景，成果如何推广复制？"
    assert contextual_query(question, history) == question
    assert history_records(question, history) == []


@pytest.mark.parametrize("question", [
    "回到前面的测量问题，你刚才说的热应变和机械应变怎么区分？",
    "刚才的热应变怎么解释？",
])
def test_named_concept_beats_loose_recent_reference_after_budget_topic(question):
    history = [
        {"question": "青榆台架怎么测，有什么用？", "status": "cancelled",
         "text": "青榆台架拟联合使用高温应变计和热电偶。热应变来自自由热膨胀，机械应变需要扣除热应变和传感器温度影响。"},
        {"question": "不是，我不是说方法，我说的是你刚才用的仪器，那个台架具体怎么测？", "status": "complete",
         "text": "高温应变计测总应变，热电偶测局部温度，数据采集器同步记录。"},
        {"question": "蓝桥课题预算多少，已经采购了吗？", "status": "complete",
         "text": "蓝桥课题预算48万元，目前尚未支出，采购尚未开始。"},
    ]
    query = contextual_query(question, history)
    assert "热应变" in query and "蓝桥" not in query and "预算" not in query
    records = history_records(question, history, 2200)
    focused = next(record for record in records if record["关联"] == "本次追问所指")
    assert focused["对话轮次"] in {1, 2}
    explanation = next(record for record in records if record["对话轮次"] == 1)
    assert "自由热膨胀" in explanation["历史AI回答（非项目事实依据）"]
    assert "未完成" in explanation["历史回答状态"]
    prompt = build_messages(question, [], history=history)[1]["content"]
    location = prompt.split("本轮承接定位", 1)[1]
    assert "台架" in location and "蓝桥" not in location


def test_immediate_correction_keeps_latest_subject_despite_old_instrument_match():
    history = [
        {"question": "蓝桥仪器采购怎么安排？", "status": "complete",
         "text": "蓝桥仪器包括光学平台、激光器和光谱仪，仪器采购尚未开始。"},
        {"question": "热应力到底怎么测，有啥用，听了半天没听懂", "status": "complete",
         "text": "热应力不能直接读取，先测壁温和应变，再用结构模型计算。"},
    ]
    question = "不是，我不是说方法，我说的是仪器，比如说煤炉 窑炉怎么测"
    query = contextual_query(question, history)
    assert "热应力" in query and "蓝桥" not in query
    focused = next(record for record in history_records(question, history)
                   if record["关联"] == "本次追问所指")
    assert focused["对话轮次"] == 2


def test_first_question_after_multi_question_answer_targets_its_own_prose():
    history = [{"question": "第一，青枢装置怎么校准？第二，蓝桥预算怎么安排？", "status": "complete",
                "text": "## 问题1：青枢装置校准\n【现场回答】\n用参照样本检查青枢偏差。\n【依据与提醒｜不朗读】\n校准.docx。\n"
                        "## 问题2：蓝桥预算\n【现场回答】\n蓝桥预算17万元，尚未支出。"}]
    question = "前面第一个问题再解释一下"
    query = contextual_query(question, history)
    assert "青枢" in query and "蓝桥" not in query
    result = history_records(question, history)
    answer = result[0]["历史AI回答（非项目事实依据）"]
    assert "用参照样本" in answer and "17万元" not in answer


def test_second_point_and_visible_formula_remain_explainable():
    history = [{"question": "模型如何计算？", "status": "complete",
                "text": "## 问题1：计算\n【现场回答】\n### 第一，采集\n读取温度。\n### 第二，应变换算\n"
                        "```text\nsigma = E * strain\n```\n这里必须同时考虑约束和热膨胀。\n### 第三，验证\n用独立数据验证。"}]
    answer = history_records("刚才第二点那个公式为什么要考虑约束？", history)[0]["历史AI回答（非项目事实依据）"]
    assert "sigma = E * strain" in answer and "同时考虑约束和热膨胀" in answer
    assert "读取温度" not in answer


def test_opening_uses_same_antecedent_but_never_quotes_generated_claims():
    history = [{"question": "LIBS和NIRS为何融合？", "status": "cancelled", "text": "历史AI错误声称已达成0.001%误差。"}]
    evidence = [{"id": "optics", "kind": "answer", "status": "reference", "question": "LIBS和NIRS如何验证？",
                 "answer": "总体回答：LIBS和NIRS融合应使用同批配对样本与独立测试，只有完成验证才能判断是否优于单一光谱。",
                 "sources": [{"filename": "准备答案.docx", "locator": "正文第1段"}]}]
    opening = build_opening("那怎么验证呢？", evidence, history)
    assert opening["ready"] and "独立测试" in opening["text"]
    assert "0.001" not in opening["text"] and "已达成" not in opening["text"]
