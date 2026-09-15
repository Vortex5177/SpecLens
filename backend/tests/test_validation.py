"""纯程序校验测试：候选身份校验、证据生成、核实输出契约。"""
import unittest

from app.models.schemas import Evidence, EvidenceBundle
from app.services.validation import (
    VerificationError,
    check_candidate_identity,
    make_evidence,
    parse_verification,
)


def _bundle(*evidences: Evidence) -> EvidenceBundle:
    return EvidenceBundle(
        candidate_id="cand-001",
        evidence_ids=[e.evidence_id for e in evidences],
        evidences=list(evidences),
        retrieval_status="ok" if evidences else "no_hit",
    )


def _evidence(eid: str = "ev-aaa") -> Evidence:
    return Evidence(
        evidence_id=eid,
        content="FastAPI 0.120 移除了旧版 on_event 钩子",
        source="official/fastapi/0.120/whatsnew_0.120.md",
        technology="fastapi",
        version="0.120",
        document_type="whats_new",
        chunk_index=3,
        content_hash="deadbeefdeadbeef",
    )


SNAPSHOT = {"app/main.py": {"total_lines": 50, "visible_lines": 50, "truncated": False}}


class TestCandidateIdentity(unittest.TestCase):
    def test_hallucinated_file_rejected(self):
        reason = check_candidate_identity({"file": "ghost.py", "line": 1, "description": "x"}, SNAPSHOT)
        self.assertIn("不在审查快照内", reason)

    def test_out_of_range_line_rejected(self):
        reason = check_candidate_identity({"file": "app/main.py", "line": 51, "description": "x"}, SNAPSHOT)
        self.assertIn("超出可见范围", reason)

    def test_valid_candidate_passes(self):
        self.assertIsNone(
            check_candidate_identity({"file": "app/main.py", "line": 10, "description": "x"}, SNAPSHOT)
        )

    def test_missing_description_rejected(self):
        reason = check_candidate_identity({"file": "app/main.py", "line": None, "description": ""}, SNAPSHOT)
        self.assertIn("description", reason)


class TestMakeEvidence(unittest.TestCase):
    def test_missing_source_returns_none(self):
        self.assertIsNone(make_evidence({"content": "x", "metadata": {}}))

    def test_deterministic_id(self):
        item = {
            "score": 0.87,
            "content": "同一段内容",
            "metadata": {"source": "official/fastapi/0.120/a.md", "chunk_index": 2,
                         "technology": "fastapi", "version": "0.120", "document_type": "whats_new"},
        }
        e1, e2 = make_evidence(item), make_evidence(item)
        self.assertEqual(e1.evidence_id, e2.evidence_id)
        self.assertEqual(e1.retrieval_score, 0.87)
        self.assertEqual(e1.chunk_index, 2)


class TestParseVerification(unittest.TestCase):
    def setUp(self):
        self.bundle = _bundle(_evidence())

    def _raw(self, **overrides) -> str:
        base = {
            "decision": "confirmed",
            "evidence_status": "document_supported",
            "evidence_ids": ["ev-aaa"],
            "title": "使用了已移除的钩子",
            "reason": "代码使用 on_event，目标版本已移除",
            "severity": "high",
            "confidence": "high",
            "suggestion": "改用 lifespan",
        }
        base.update(overrides)
        import json

        return json.dumps(base, ensure_ascii=False)

    def test_valid_confirmed_passes(self):
        output = parse_verification(self._raw(), self.bundle)
        self.assertEqual(output.decision, "confirmed")

    def test_extra_field_rejected(self):
        with self.assertRaises(VerificationError):
            parse_verification(self._raw(file="app/main.py"), self.bundle)

    def test_unknown_evidence_id_rejected(self):
        with self.assertRaises(VerificationError):
            parse_verification(self._raw(evidence_ids=["ev-fake"]), self.bundle)

    def test_document_supported_requires_ids(self):
        with self.assertRaises(VerificationError):
            parse_verification(self._raw(evidence_ids=[]), self.bundle)

    def test_inferred_may_not_reference_ids(self):
        with self.assertRaises(VerificationError):
            parse_verification(
                self._raw(evidence_status="inferred", evidence_ids=["ev-aaa"],
                          confidence="medium"),
                self.bundle,
            )

    def test_inferred_caps_confidence(self):
        with self.assertRaises(VerificationError):
            parse_verification(
                self._raw(evidence_status="inferred", evidence_ids=[], confidence="high"),
                self.bundle,
            )

    def test_none_status_blocks_confirmation(self):
        with self.assertRaises(VerificationError):
            parse_verification(
                self._raw(evidence_status="none", evidence_ids=[]), self.bundle
            )

    def test_confirmed_requires_mandatory_fields(self):
        with self.assertRaises(VerificationError):
            parse_verification(self._raw(title=""), self.bundle)

    def test_migration_confirmed_requires_behaviors(self):
        with self.assertRaises(VerificationError):
            parse_verification(self._raw(), self.bundle, require_migration_fields=True)

    def test_migration_confirmed_with_behaviors_passes(self):
        output = parse_verification(
            self._raw(current_behavior="使用 on_event", target_behavior="使用 lifespan",
                      change_reason="官方迁移指南要求"),
            self.bundle,
            require_migration_fields=True,
        )
        self.assertEqual(output.decision, "confirmed")

    def test_rejected_ok_without_optional_fields(self):
        output = parse_verification(
            self._raw(decision="rejected", evidence_status="none", evidence_ids=[],
                      title="", reason="", severity=None, confidence=None, suggestion=""),
            self.bundle,
        )
        self.assertEqual(output.decision, "rejected")


if __name__ == "__main__":
    unittest.main()
