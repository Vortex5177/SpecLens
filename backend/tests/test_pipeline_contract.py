"""管线契约测试：fake 模型 + fake 检索，覆盖发现-核实的核心行为。

不依赖 LLM / Qdrant / 网络；检索函数通过 mock.patch 替换。
"""
import json
import unittest
from unittest import mock

from app.graph.nodes import pipeline
from app.graph.nodes.pipeline import run_migration_pipeline, run_review_pipeline
from app.models.schemas import Evidence
from app.services.retrieval import RetrievalError


class FakeModel:
    """按脚本依次返回内容的假模型；记录每次调用的消息。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        idx = min(len(self.calls) - 1, len(self.replies) - 1)

        class _Resp:
            content = self.replies[idx]
            usage_metadata = {"input_tokens": 10, "output_tokens": 5}

        return _Resp()


def make_scope(mode="code_review", files=None, confirmed=None, targets=None,
               pending=None, budget_overrides=None):
    """构造最小 RunScope。files: {rel: 文本}；行数按文本自动计算。"""
    files = files or {"app/main.py": "import fastapi\n\n@app.on_event('startup')\ndef boot():\n    pass\n"}
    code_context, snapshot_files = {}, {}
    for rel, text in files.items():
        lines = len(text.splitlines())
        code_context[rel] = text
        snapshot_files[rel] = {
            "truncated": False, "total_lines": lines, "visible_lines": lines, "sha256": "x",
        }
    budget = {
        "max_candidates": 32, "max_llm_calls": 37, "doc_enum_max_blocks": 40,
        "doc_scan_batch_blocks": 10, "doc_scan_max_calls": 4, "deadline": 900,
    }
    if budget_overrides:
        budget.update(budget_overrides)
    return {
        "run_id": "run-test",
        "mode": mode,
        "confirmed_versions": confirmed if confirmed is not None else {"fastapi": "0.115"},
        "pending_versions": pending or [],
        "target_versions": targets or {},
        "code_context": code_context,
        "snapshot": {"files": snapshot_files, "omitted_files": []},
        "coverage": {},
        "budget": budget,
    }


SCAN_REPLY = json.dumps(
    {"suspicions": [
        {"file": "app/main.py", "line": 3, "technology": "fastapi", "topic": "api",
         "description": "使用了 on_event", "severity_guess": "high", "query": "on_event 移除"},
        {"file": "ghost.py", "line": 1, "technology": "fastapi", "topic": "api",
         "description": "幻觉文件", "query": "x"},
        {"file": "app/main.py", "line": 999, "technology": "fastapi", "topic": "api",
         "description": "越界行号", "query": "x"},
    ]},
    ensure_ascii=False,
)

def fake_retrieval_result(eid="ev-abc"):
    return [{
        "score": 0.9,
        "content": "FastAPI 0.120 移除了 on_event，请改用 lifespan",
        "metadata": {"source": "official/fastapi/0.120/whatsnew.md", "chunk_index": 0,
                     "technology": "fastapi", "version": "0.120", "document_type": "whats_new"},
    }]


# 核实回复引用真实计算出的证据 ID（与 fake 检索结果一致）
VERIFY_REPLY = json.dumps(
    {"decision": "confirmed", "evidence_status": "document_supported",
     "evidence_ids": [pipeline.make_evidence(fake_retrieval_result()[0]).evidence_id],
     "title": "使用了已移除的 on_event",
     "reason": "目标版本已移除该钩子", "severity": "high", "confidence": "high",
     "suggestion": "改用 lifespan"},
    ensure_ascii=False,
)


class TestReviewPipeline(unittest.TestCase):
    def test_scan_bad_json_fails_run(self):
        model = FakeModel(["这不是 JSON"])
        report = run_review_pipeline(make_scope(), model)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["errors"][0]["type"], "model_error")

    def test_scan_empty_produces_complete_report(self):
        model = FakeModel([json.dumps({"suspicions": []})])
        report = run_review_pipeline(make_scope(), model)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["counts"]["candidates_total"], 0)
        self.assertIn("完成", report["summary"])

    def test_hallucinated_file_and_bad_line_discarded(self):
        model = FakeModel([SCAN_REPLY, VERIFY_REPLY])
        with mock.patch.object(pipeline.retrieval, "search_official_docs",
                               return_value=fake_retrieval_result()):
            report = run_review_pipeline(make_scope(), model)
        self.assertEqual(report["counts"]["discarded"], 2)
        self.assertEqual(report["counts"]["candidates_total"], 1)
        self.assertEqual(report["status"], "partial")  # discarded -> partial

    def test_confirmed_issue_binds_identity_and_evidence(self):
        model = FakeModel([SCAN_REPLY, VERIFY_REPLY])
        with mock.patch.object(pipeline.retrieval, "search_official_docs",
                               return_value=fake_retrieval_result()):
            report = run_review_pipeline(make_scope(), model)
        self.assertEqual(report["status"], "partial")  # discarded 候选 -> partial
        issue = report["issues"][0]
        self.assertEqual(issue["file"], "app/main.py")  # 身份来自候选，不可被模型改写
        self.assertEqual(issue["category"], "api")
        self.assertEqual(issue["evidence_status"], "document_supported")
        self.assertEqual(issue["source"], "official/fastapi/0.120/whatsnew.md")
        self.assertTrue(issue["evidences"][0]["evidence_id"].startswith("ev-"))
        # 身份血缘：候选 ID 由程序赋值并写入 issue；fix_prompt 由 result 节点回填
        self.assertTrue(issue["candidate_ids"][0].startswith("cand-"))
        self.assertNotIn("fix_prompt", issue)

    def test_retrieval_error_fails_candidate(self):
        model = FakeModel([SCAN_REPLY])
        with mock.patch.object(pipeline.retrieval, "search_official_docs",
                               side_effect=RetrievalError("Qdrant down")):
            report = run_review_pipeline(make_scope(), model)
        self.assertEqual(report["counts"]["failed"], 1)
        self.assertEqual(report["errors"][0]["type"], "retrieval_error")

    def test_no_hit_allows_inferred_verification(self):
        model = FakeModel([
            SCAN_REPLY,
            json.dumps({"decision": "confirmed", "evidence_status": "inferred",
                        "evidence_ids": [], "title": "嫌疑成立", "reason": "基于自身知识",
                        "severity": "medium", "confidence": "medium", "suggestion": "检查"}),
        ])
        with mock.patch.object(pipeline.retrieval, "search_official_docs", return_value=[]):
            report = run_review_pipeline(make_scope(), model)
        issue = report["issues"][0]
        self.assertEqual(issue["evidence_status"], "inferred")
        self.assertEqual(issue["source"], "llm_inference")
        self.assertEqual(issue["evidence"], "")

    def test_invalid_reference_recorded_as_failed(self):
        bad_verify = json.dumps(
            {"decision": "confirmed", "evidence_status": "document_supported",
             "evidence_ids": ["ev-fake"], "title": "t", "reason": "r", "severity": "high",
             "confidence": "high", "suggestion": "s"}
        )
        model = FakeModel([SCAN_REPLY, bad_verify])
        with mock.patch.object(pipeline.retrieval, "search_official_docs",
                               return_value=fake_retrieval_result()):
            report = run_review_pipeline(make_scope(), model)
        self.assertEqual(report["counts"]["failed"], 1)
        self.assertEqual(report["errors"][0]["type"], "invalid_output")
        self.assertEqual(report["issues"], [])

    def test_budget_exhaustion_stops_verification(self):
        model = FakeModel([SCAN_REPLY, VERIFY_REPLY])
        scope = make_scope(budget_overrides={"max_llm_calls": 1})
        with mock.patch.object(pipeline.retrieval, "search_official_docs",
                               return_value=fake_retrieval_result()):
            report = run_review_pipeline(scope, model)
        self.assertEqual(report["counts"]["failed"], 1)
        self.assertEqual(report["errors"][0]["type"], "budget_exhausted")


class TestMigrationPipeline(unittest.TestCase):
    def _migration_scope(self):
        return make_scope(
            mode="migration",
            confirmed={"fastapi": "0.110"},
            targets={"fastapi": "0.120"},
        )

    CODE_SCAN = json.dumps(
        {"usage_points": [
            {"file": "app/main.py", "line": 3, "technology": "fastapi",
             "usage": "on_event 用法", "query": "on_event"},
        ]},
        ensure_ascii=False,
    )

    def test_bidirectional_confirmed_with_document_evidence(self):
        doc_blocks = [{
            "content": "0.120: on_event 已移除",
            "metadata": {"source": "official/fastapi/0.120/whatsnew.md", "chunk_index": 0,
                         "technology": "fastapi", "version": "0.120", "document_type": "whats_new"},
        }]
        # 种子证据 ID 由程序确定性生成：先用同一转换算出真实 ID 再写进脚本
        real_id = pipeline.make_evidence(doc_blocks[0]).evidence_id
        doc_scan_reply = json.dumps(
            {"usage_points": [
                {"file": "app/main.py", "line": 3, "technology": "fastapi",
                 "usage": "on_event 用法", "seed_evidence_ids": [real_id]},
            ]},
            ensure_ascii=False,
        )
        verify_reply = json.dumps(
            {"decision": "confirmed", "evidence_status": "document_supported",
             "evidence_ids": [real_id], "title": "需要迁移到 lifespan",
             "reason": "r", "severity": "high", "confidence": "high",
             "suggestion": "改用 lifespan", "current_behavior": "on_event",
             "target_behavior": "lifespan", "change_reason": "官方移除"},
            ensure_ascii=False,
        )
        model = FakeModel([self.CODE_SCAN, doc_scan_reply, verify_reply])
        with mock.patch.object(pipeline.retrieval, "list_migration_changes",
                               return_value={"results": doc_blocks, "partitions": [
                                   {"technology": "fastapi", "version": "0.120",
                                    "read": 1, "has_more": False}]}), \
             mock.patch.object(pipeline.retrieval, "search_migration_docs",
                               return_value=[]):
            report = run_migration_pipeline(self._migration_scope(), model)
        self.assertEqual(len(report["issues"]), 1)  # 双向候选去重为一条
        issue = report["issues"][0]
        self.assertEqual(issue["origin"], ["code", "document"])  # 双向命中
        self.assertEqual(issue["confidence"], "high")  # 由核实输出决定，不按来源升级
        self.assertEqual(issue["current_version"], "0.110")
        self.assertEqual(issue["target_version"], "0.120")
        self.assertEqual(issue["evidence_status"], "document_supported")

    def test_list_migration_changes_items_are_evidence_compatible(self):
        # 回归：list_migration_changes 曾把嵌套结构解包成扁平 dict，
        # 导致 make_evidence 全部丢弃、文档方向零调用（端到端实测暴露）。
        # 契约：真实生产者输出必须能被 make_evidence 直接消费。
        items = [{
            "content": "0.3: LLMChain 已废弃",
            "metadata": {"source": "official/langchain/0.3/whatsnew.txt", "chunk_index": 0,
                         "technology": "langchain", "version": "0.3",
                         "document_type": "whats_new"},
        }]
        retrieval = pipeline.retrieval
        # ingestion 顶层导入 qdrant_client（离线环境不可用），用 stub 替身避免真实导入
        stub_ingestion = mock.MagicMock()
        with mock.patch.object(retrieval, "_scroll_whats_new", return_value=(items, None)), \
             mock.patch.object(retrieval, "_versions_in_range", return_value=["0.3"]), \
             mock.patch.dict("sys.modules", {"app.services.ingestion": stub_ingestion}):
            out = retrieval.list_migration_changes(
                {"langchain": "0.2.16"}, {"langchain": "0.3.0"}
            )
        self.assertEqual(len(out["results"]), 1)
        self.assertIsNotNone(pipeline.make_evidence(out["results"][0]))

    def test_inferred_migration_goes_to_unresolved(self):
        # 文档方向无块时不消耗模型调用：脚本为 [代码初筛, 核实]
        model = FakeModel([
            self.CODE_SCAN,
            json.dumps({"decision": "confirmed", "evidence_status": "inferred",
                        "evidence_ids": [], "title": "疑似需要迁移", "reason": "基于自身知识",
                        "severity": "medium", "confidence": "medium", "suggestion": "检查",
                        "current_behavior": "a", "target_behavior": "b", "change_reason": "c"}),
        ])
        with mock.patch.object(pipeline.retrieval, "list_migration_changes",
                               return_value={"results": [], "partitions": [
                                   {"technology": "fastapi", "version": "0.120",
                                    "read": 0, "has_more": False, "empty": True}]}), \
             mock.patch.object(pipeline.retrieval, "search_migration_docs", return_value=[]):
            report = run_migration_pipeline(self._migration_scope(), model)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["unresolved"][0]["type"], "inferred_suggestion")

    def test_doc_direction_failure_is_partial_not_failed(self):
        model = FakeModel([self.CODE_SCAN,
                           json.dumps({"decision": "rejected", "evidence_status": "none",
                                       "evidence_ids": []})])
        with mock.patch.object(pipeline.retrieval, "list_migration_changes",
                               side_effect=RetrievalError("scroll broken")), \
             mock.patch.object(pipeline.retrieval, "search_migration_docs", return_value=[]):
            report = run_migration_pipeline(self._migration_scope(), model)
        self.assertEqual(report["status"], "partial")  # 代码方向成功，文档方向失败
        self.assertEqual(report["errors"][0]["type"], "retrieval_error")
        self.assertEqual(report["counts"]["rejected"], 1)


if __name__ == "__main__":
    unittest.main()
