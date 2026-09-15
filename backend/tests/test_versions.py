"""版本工具与 Migration 门禁测试（含 analyze 节点的部分确认政策）。"""
import json
import tempfile
import unittest
from pathlib import Path

from app.graph.nodes.analyze import _validate_migration_targets, analyze_project
from app.services.retrieval import normalize_version, version_key


class TestVersionTools(unittest.TestCase):
    def test_normalize_version_strips_trailing_zero(self):
        self.assertEqual(normalize_version("0.120.0"), "0.120")
        self.assertEqual(normalize_version("3.13.0"), "3.13")
        self.assertEqual(normalize_version("0.120"), "0.120")

    def test_version_key(self):
        self.assertEqual(version_key("3.13"), (3, 13))
        self.assertIsNone(version_key("latest"))
        self.assertIsNone(version_key(""))


class TestMigrationGate(unittest.TestCase):
    def test_empty_targets_rejected(self):
        self.assertIsNotNone(_validate_migration_targets({"fastapi": "0.110"}, {}))

    def test_missing_current_version_rejected(self):
        error = _validate_migration_targets({}, {"fastapi": "0.120"})
        self.assertIn("没有已确认或精确锁定", error)

    def test_same_version_rejected(self):
        error = _validate_migration_targets({"fastapi": "0.120"}, {"fastapi": "0.120.0"})
        self.assertIn("相同，无需迁移", error)

    def test_lower_target_rejected(self):
        error = _validate_migration_targets({"fastapi": "0.120"}, {"fastapi": "0.110"})
        self.assertIn("必须高于当前版本", error)

    def test_valid_target_passes(self):
        self.assertIsNone(_validate_migration_targets({"fastapi": "0.110"}, {"fastapi": "0.120"}))


class TestAnalyzeProject(unittest.TestCase):
    """analyze_project：部分确认政策与代码快照覆盖。"""

    def _make_project(self, root: Path, versions: list[dict], files: dict[str, str]):
        (root / "project").mkdir(parents=True)
        (root / "meta.json").write_text(
            json.dumps(
                {
                    "file_count": len(files),
                    "languages": {"Python": len(files)},
                    "dependency_files": [],
                    "file_tree": list(files),
                    "tree_truncated": False,
                    "versions": versions,
                }
            ),
            encoding="utf-8",
        )
        for rel, content in files.items():
            target = root / "project" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def test_partial_confirmation_does_not_block_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_project(
                root,
                versions=[
                    {"technology": "fastapi", "raw_spec": "==0.115.0", "version": "0.115.0",
                     "status": "exact", "confirmed": False, "source_file": "requirements.txt"},
                    {"technology": "flask", "raw_spec": ">=2.0", "version": None,
                     "status": "needs_confirmation", "confirmed": False,
                     "source_file": "requirements.txt"},
                ],
                files={"main.py": "import fastapi\n"},
            )
            result = analyze_project(
                {"project_path": str(root), "mode": "code_review", "target_versions": {}}
            )
            self.assertNotIn("error", result)
            scope = result["run_scope"]
            # exact 自动采用；needs_confirmation 未确认不阻断也不参与检索
            self.assertEqual(scope["confirmed_versions"], {"fastapi": "0.115"})
            self.assertEqual(scope["pending_versions"], ["flask"])

    def test_migration_requires_active_current_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_project(
                root,
                versions=[
                    {"technology": "flask", "raw_spec": ">=2.0", "version": None,
                     "status": "needs_confirmation", "confirmed": False,
                     "source_file": "requirements.txt"},
                ],
                files={"main.py": "import flask\n"},
            )
            result = analyze_project(
                {
                    "project_path": str(root),
                    "mode": "migration",
                    "target_versions": {"flask": "3.0"},
                }
            )
            self.assertIn("error", result)

    def test_snapshot_records_visible_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = "\n".join(f"line{i} = {i}" for i in range(1, 21))
            self._make_project(
                root,
                versions=[
                    {"technology": "fastapi", "raw_spec": "==0.115.0", "version": "0.115.0",
                     "status": "exact", "confirmed": False, "source_file": "requirements.txt"},
                ],
                files={"main.py": content},
            )
            result = analyze_project(
                {"project_path": str(root), "mode": "code_review", "target_versions": {}}
            )
            snapshot = result["run_scope"]["snapshot"]["files"]["main.py"]
            self.assertFalse(snapshot["truncated"])
            self.assertEqual(snapshot["total_lines"], 20)
            self.assertEqual(snapshot["visible_lines"], 20)


if __name__ == "__main__":
    unittest.main()
