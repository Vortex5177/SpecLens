"""运行报告序列化与 Fix Prompt 模板测试。"""
import unittest

from app.graph.nodes.result import generate_result
from app.models.schemas import RunReport


def make_state(mode="code_review"):
    scope = {
        "run_id": "run-test",
        "mode": mode,
        "confirmed_versions": {"fastapi": "0.115"},
        "pending_versions": ["flask"],
        "target_versions": {"fastapi": "0.120"} if mode == "migration" else {},
        "code_context": {},
        "snapshot": {"files": {}, "omitted_files": []},
        "coverage": {},
        "budget": {},
    }
    report = {
        "run_id": "run-test",
        "mode": mode,
        "status": "partial",
        "summary": "测试摘要",
        "issues": [],
        "coverage": {"files": {}, "truncated_files": [], "omitted_files": [],
                     "pending_versions": ["flask"], "scans": {}},
        "counts": {"candidates_total": 1, "scheduled": 1, "not_scheduled": 0,
                   "discarded": 0, "verified": 1, "confirmed": 1, "rejected": 0,
                   "insufficient": 0, "failed": 0},
        "unresolved": [],
        "errors": [],
    }
    return {"run_scope": scope, "report": report}


class TestRunReportSerialization(unittest.TestCase):
    def test_report_round_trip(self):
        state = make_state()
        validated = RunReport.model_validate(state["report"])
        dumped = validated.model_dump()
        self.assertEqual(dumped["schema_version"], 2)
        self.assertEqual(dumped["status"], "partial")
        self.assertEqual(dumped["counts"]["confirmed"], 1)

    def test_review_issue_response_uses_report(self):
        # API 响应模型引用 RunReport（前向引用已 rebuild）
        from app.models.schemas import ReviewResponse

        state = make_state()
        state["report"]["issues"].append({
            "file": "app/main.py", "line": 3, "category": "api",
            "severity": "high", "confidence": "high", "title": "t",
            "description": "d", "evidence": "", "source": "llm_inference",
            "suggestion": "s", "evidence_status": "inferred", "origin": ["code"],
            "evidences": [], "candidate_ids": ["cand-001"], "fix_prompt": "fix",
        })
        response = ReviewResponse(
            review_id="p", project_id="p", mode="code_review",
            result=RunReport.model_validate(state["report"]),
            project_fix_prompt="project prompt",
        )
        self.assertEqual(response.result.issues[0]["file"], "app/main.py")


class TestFixPromptTemplates(unittest.TestCase):
    def test_review_fix_prompt_generated_and_versions_included(self):
        state = make_state()
        state["report"]["issues"].append({
            "file": "app/main.py", "line": 3, "category": "api",
            "severity": "high", "confidence": "high", "title": "用了旧 API",
            "description": "on_event 已移除", "evidence": "", "source": "llm_inference",
            "suggestion": "改用 lifespan", "evidence_status": "inferred",
            "origin": ["code"], "evidences": [], "candidate_ids": ["cand-001"],
        })
        result = generate_result(state)
        issue = result["report"]["issues"][0]
        self.assertIn("fix_prompt", issue)
        self.assertIn("fastapi 0.115", issue["fix_prompt"])
        # 无文档证据的推断：模板明确标注，不冒充官方依据
        self.assertIn("LLM 推理", issue["fix_prompt"])
        project_prompt = result["project_fix_prompt"]
        self.assertIn("1 个问题", project_prompt)
        self.assertIn("fastapi 0.115", project_prompt)

    def test_unresolved_never_enters_template(self):
        state = make_state()
        state["report"]["unresolved"].append({
            "candidate_id": "cand-009", "type": "insufficient", "file": "app/x.py",
            "line": None, "technology": "", "dimension": "api",
            "description": "证据不足", "origin": ["code"], "note": "无法可靠判断",
        })
        result = generate_result(state)
        self.assertIn("（未发现问题）", result["project_fix_prompt"])

    def test_migration_fix_prompt_uses_scope_versions(self):
        state = make_state(mode="migration")
        state["report"]["issues"].append({
            "file": "app/main.py", "line": 3, "technology": "fastapi",
            "current_version": "0.115", "target_version": "0.120",
            "title": "需要迁移", "severity": "high",
            "current_behavior": "on_event", "target_behavior": "lifespan",
            "reason": "官方移除", "evidence": "", "source": "llm_inference",
            "suggested_change": "改用 lifespan", "confidence": "medium",
            "evidence_status": "inferred", "origin": ["code"], "evidences": [],
            "candidate_ids": ["cand-001"],
        })
        result = generate_result(state)
        issue = result["report"]["issues"][0]
        self.assertIn("0.115 -> 0.120", issue["fix_prompt"])
        self.assertIn("lifespan", issue["fix_prompt"])


if __name__ == "__main__":
    unittest.main()
