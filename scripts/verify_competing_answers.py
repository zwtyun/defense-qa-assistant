"""Import contrasting temporary answers into a development library and review the real model."""
import argparse
import io
import json
from pathlib import Path

from docx import Document
import httpx

from smoke_portable import check_generation

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "青枢装置应按几天校准、用什么记录表，不同工况的周期是否一样？"
FIXTURES = [
    ("验收测试_青枢初稿.docx", "青枢装置校准周期和记录方式是什么？",
     "2025年讨论初稿：拟统一每7天校准一次，使用A1记录表。本段尚未区分工况，后续由正式验收方案修订。"),
    ("验收测试_青枢正式方案.docx", "青枢装置校准周期和记录方式是什么？",
     "2026年已批准的青枢装置正式验收方案明确替代2025年讨论初稿：稳定清洁工况每14天校准一次；高粉尘工况每7天校准一次。统一使用B7记录表登记校准日期、标准物质、参比值、误差和处置记录。周期属于计划安排，不表示设备已经完成校准或性能已达到验收指标。该差异源于污染沉积风险不同，不可平均成一个周期。"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Owned isolated development service URL")
    args = parser.parse_args()
    documents = []
    with httpx.Client(base_url=args.url, trust_env=False, timeout=60) as client:
        try:
            for filename, question, answer in FIXTURES:
                doc = Document()
                doc.add_paragraph("问题：" + question)
                doc.add_paragraph("回答：" + answer)
                stream = io.BytesIO()
                doc.save(stream)
                response = client.post("/api/import", files={"file": (filename, stream.getvalue())})
                response.raise_for_status()
                documents.append(response.json()["document_id"])
            report = check_generation(client, documents[1], QUESTION)
            source_ids = {source.get("document_id") for item in report["sources"] for source in item.get("sources", [])}
            assert set(documents) <= source_ids, "同题不同答案必须都进入比较材料"
            report["review_questions"] = [
                "是否根据明确的替代关系采用正式方案，而不是仅凭文件名？",
                "是否区分清洁工况14天与高粉尘7天，不平均或混淆？",
                "是否正确选择B7记录表并保留计划尚未实施的状态？",
                "关键词是否足以提示回答结构且没有整段加粗？",
            ]
            (ROOT / "重复问题比较验证.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            assert report["emphasized_keyword_count"] > 0, "正文需要关键词提示"
        finally:
            for document_id in documents:
                response = client.delete("/api/documents/" + document_id)
                response.raise_for_status()
            assert client.get("/api/search", params={"q": "青枢"}).json()["results"] == []


if __name__ == "__main__":
    main()

