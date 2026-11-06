from instant_opening import build_opening


def answer(question, text, short="", **extra):
    return {"id": "answer-1", "kind": "answer", "question": question, "answer": text,
            "short_answer": short, "status": "reference",
            "sources": [{"document_id": "doc-1", "filename": "资料.docx", "locator": "正文第3段", "quote": text}], **extra}


def test_prefers_prepared_overall_answer_instead_of_import_metadata():
    summary = "我们拟在统一对象与误差口径下逐项验证2%目标，先确定参照、量程和统计规则，再开展代表工况测试，不能选择有利口径报告结果。"
    result = build_opening("2%误差如何定义和验证？", [answer("2%误差如何定义和验证？", "原库题号：088—090\n总体回答：" + summary + "\n第一，定义参考值。", "原库题号：088—090 | PPT第12页")])
    assert result["text"] == summary
    assert result["ready"] and result["kind"] == "local_answer"
    assert result["sources"][0]["locator"] == "正文第3段"


def test_keeps_following_caveat_including_when_on_next_line():
    result = build_opening("误差指标已经实现了吗？", [answer("误差指标已经实现了吗？", "总体回答：误差指标设定为2%。\n但目前尚未完成独立验收，不能说已经实现。\n第一，拟采用参比验证。")])
    assert "2%" in result["text"] and "尚未完成" in result["text"] and "不能说已经实现" in result["text"]


def test_whole_sentence_preserves_decimal_unit_and_does_not_pad_short_facts():
    text = "光学课题预算为48.5万元，尚未支出。"
    result = build_opening("光学课题预算多少钱？", [answer("光学课题预算多少钱？", text, text)])
    assert result["text"] == text
    assert len(result["text"]) < 50


def test_rejects_unrelated_or_draft_material_without_filler_answer():
    result = build_opening("火星基地哪年完成？", [answer("预算怎么安排？", "预算计划为100万元，尚未支出。")])
    assert not result["ready"] and result["kind"] == "insufficient"
    result = build_opening("预算怎么安排？", [answer("预算怎么安排？", "预算计划为100万元，尚未支出。", status="draft")])
    assert not result["ready"]


def test_numeric_conflict_is_shown_not_silently_resolved_by_rank():
    rows = [answer("设备多久校准？", "设备每7天校准一次。"), answer("设备多久校准？", "设备每14天校准一次。")]
    result = build_opening("设备多久校准？", rows)
    assert not result["ready"] and result["kind"] == "clarification"
    assert "不同数字口径" in result["text"]


def test_status_question_does_not_use_expected_deliverables_as_current_status():
    result = build_opening("项目成果都已经完成了吗？", [answer("项目最终交付哪些成果？", "项目应交付工程示范结果，论文和专利用于表达贡献。")])
    assert not result["ready"]


def test_numbered_tasks_must_all_be_in_actual_opening_not_only_answer_title():
    row = answer("课题1和课题2如何分工？", "总体回答：课题1负责传感器与数据质量。\n第一，课题1负责实测感知，课题2负责建模预测，两者通过统一数据接口衔接。")
    result = build_opening("课题一和课题二怎么分工？", [row])
    assert "课题1负责实测" in result["text"] and "课题2负责建模" in result["text"]


def test_unasked_task_clause_is_omitted_but_shared_condition_is_preserved():
    row = answer("课题1、课题2和课题4如何分工？", "总体回答：课题1负责实测；课题2负责建模；仅在验证后用于预测；课题4负责平台。")
    result = build_opening("课题一和课题二怎么分工？", [row])
    assert "课题4" not in result["text"]
    assert "仅在验证后" in result["text"]


def test_followup_uses_previous_question_but_never_old_assistant_facts():
    history = [{"question": "NIRS与LIBS怎样互补？", "text": "已经达到世界第一，精度为0.01%。"}]
    row = answer("NIRS与LIBS怎样验证？", "总体回答：NIRS与LIBS的融合增益需通过同批样本对应与独立测试验证，尚不能宣称已完成工程验收。")
    result = build_opening("那怎么验证呢？", [row], history)
    assert result["ready"] and "独立测试" in result["text"]
    assert "世界第一" not in result["text"] and "0.01" not in result["text"]


def test_complete_why_question_changes_topic_after_pinn_but_bare_why_keeps_context():
    history = [{"question": "PINN模型的可信度如何验证？"}]
    question = "为什么选择锅炉和窑炉两个示范场景，成果如何推广复制？"
    text = "两种对象覆盖锅炉与工业窑炉类型，能够检验方法在不同热工对象的适配，但两个示范不能自动代表全部炉型。"
    row = answer(question, "总体回答：" + text)
    result = build_opening(question, [row], history)
    assert result["ready"] and result["text"] == text
    # An omitted subject still needs the preceding user question.
    pinn = answer("PINN模型的可信度如何验证？", "总体回答：PINN不自动保证预测正确，仍需要独立验证和物理校核。")
    followup = build_opening("为什么呢？", [pinn], history)
    assert followup["ready"] and "PINN" in followup["text"]


def test_raw_excerpt_keeps_its_locator_and_does_not_quote_question_as_answer():
    row = answer("原文位置", "问题：NIRS测什么？\nNIRS利用分子吸收特征补充水分与有机结构信息，预测结果需经参比标定。", kind="source")
    result = build_opening("NIRS水分预测依据是什么？", [row])
    assert result["ready"] and result["kind"] == "source_excerpt"
    assert "问题：" not in result["text"] and "需经参比标定" in result["text"]


def test_credibility_challenge_is_not_answered_with_only_a_method_definition():
    row = answer("PINN是什么？", "总体回答：PINN是物理信息神经网络，可用方程约束学习。\n第三，PINN不自动保证预测正确，仍需要独立验证和物理校核。")
    result = build_opening("PINN为什么能提高预测可信度？", [row])
    assert result["ready"]
    assert "独立验证" in result["text"] and "不自动保证" in result["text"]


def test_acronym_touching_chinese_text_still_identifies_required_method():
    wrong = answer("模型预测可信度如何验证？", "总体回答：轻量化部署需要独立测试，以确认模型在目标设备上有效。")
    correct = answer("PINN模型如何验证？", "总体回答：PINN需要独立误差和物理校核，不能只在训练集上检验有效性。")
    result = build_opening("PINN模型预测可信度如何验证？", [wrong, correct])
    assert result["ready"] and "PINN" in result["text"]


def test_pdf_line_wrap_does_not_hide_that_a_plant_is_from_previous_work():
    row = answer("PDF第61页", "已有工作基础。\n牵头承担前期煤炭清洁高效燃烧\n关键技术等项目，分别面向660 MW燃煤机组\n和300 MW等级锅炉开展技术集成与工程验证。", kind="source")
    result = build_opening("在哪些机组开展示范，怎么证明可推广？", [row])
    assert not result["ready"]


def test_where_to_demonstrate_is_not_replaced_with_generic_maintenance_or推广():
    row = answer("示范后如何维护和推广？", "总体回答：示范推广需要完善维护机制，两处示范不能证明所有炉型适用。")
    result = build_opening("在哪些机组开展示范，怎么证明可推广？", [row])
    assert not result["ready"]


def test_figurative_where_is_innovation_not_a_physical_location_query():
    row = answer("项目创新点新在哪里？", "总体回答：创新在于将物理约束与数据学习结合，提高不同工况下的预测能力，但仍需独立验证。")
    result = build_opening("本项目创新点是什么，相比已有技术新在哪里？", [row])
    assert result["ready"] and "物理约束" in result["text"]


def test_budget_task_allocation_is_not_replaced_with_only_total_funding():
    row = answer("预算如何与研究任务匹配？", "总体回答：预算由专项1000万元和自筹2600万元组成。\n第二，从任务匹配看，设备和试验费用应对应研究任务与交付，按进度安排资金并用明细解释测算。")
    result = build_opening("项目预算如何分配，资金和任务怎样对应？", [row])
    assert result["ready"] and "任务与交付" in result["text"]


def test_ambiguous_transcribed_unit_is_not_silently_chosen_from_a_nearby_fact():
    row = answer("计算响应时间是多少？", "总体回答：计算响应目标为5秒，尚待验证。")
    result = build_opening("响应时间那个五是5毫秒还是5秒？", [row])
    assert not result["ready"] and result["kind"] == "clarification"


def test_explicit_independent_questions_keep_separate_answers():
    rows = [answer("标定误差如何验证？", "标定误差需要采用独立样本和参比方法验证，尚未完成现场验收。"), answer("设备预算如何安排？", "设备预算为48万元，目前经费尚未支出，采购处于准备阶段。")]
    result = build_opening("第一，标定误差如何验证？第二，设备预算如何安排？", rows)
    assert result["ready"] and len(result["parts"]) == 2
    assert "独立样本" in result["parts"][0]["text"]
    assert "48万元" in result["parts"][1]["text"]
