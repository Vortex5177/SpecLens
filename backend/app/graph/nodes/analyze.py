"""节点 1：analyze_project（确定性，无 LLM）。

职责（规格原则 3：LangGraph 只负责确定性流程）：
- 读取上传时的分析结果（meta.json）
- 确定有效版本（规格第 9/10 节）：用户已确认，或依赖文件精确锁定（exact）；
  待确认版本不阻断 Code Review，但不得作为检索版本
- Migration 门禁：选中的技术必须有有效当前版本，目标版本必须更高且可比较
- 选取源码文件并读入代码上下文（原文，行号在渲染时才注入），
  记录覆盖信息（截断 / 可见行数 / 省略文件）
"""
import hashlib
import json
import uuid
from pathlib import Path

from app import config
from app.graph.state import ReviewState
from app.services.retrieval import normalize_version, version_key


def _validate_migration_targets(
    confirmed: dict[str, str], target_versions: dict[str, str]
) -> str | None:
    """Migration 目标版本门禁（规格第 19 节），返回错误信息或 None。

    规则：目标技术必须有有效当前版本；目标版本可比较且必须更高；
    归一化后相同视为等价版本，拒绝执行。
    """
    if not target_versions:
        return "请至少选择一个需要迁移的技术及其目标版本"
    for tech, target in target_versions.items():
        current = confirmed.get(tech)
        if current is None:
            return f"技术 {tech} 没有已确认或精确锁定的当前版本，无法执行迁移"
        target_key, current_key = version_key(target), version_key(current)
        if target_key is None or current_key is None:
            return f"{tech} 的版本无法比较：当前 {current} / 目标 {target}"
        if normalize_version(target) == normalize_version(current):
            return f"{tech} 的目标版本 {target} 与当前版本 {current} 相同，无需迁移"
        if target_key <= current_key:
            return f"{tech} 的目标版本 {target} 必须高于当前版本 {current}"
    return None


def _snapshot_file(path: Path) -> tuple[str, dict]:
    """读取单个源码文件，返回（可见文本, 快照信息）。

    超长文件按行边界截断（行号必须可靠）；快照记录真实总行数、
    可见行数、截断标记与全文哈希，供行号校验与覆盖报告使用。
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    total_lines = len(text.splitlines())
    truncated = len(text) > config.REVIEW_MAX_FILE_CHARS
    if truncated:
        text = text[: config.REVIEW_MAX_FILE_CHARS]
        cut = text.rfind("\n")
        if cut > 0:
            text = text[: cut + 1]
    visible_lines = len(text.splitlines())
    snapshot = {
        "truncated": truncated,
        "total_lines": total_lines,
        "visible_lines": visible_lines,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    }
    return text, snapshot


def analyze_project(state: ReviewState) -> dict:
    project_dir = Path(state["project_path"])
    meta_path = project_dir / "meta.json"
    if not meta_path.is_file():
        return {"error": "项目分析结果不存在，请重新上传项目"}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # 有效版本 = 用户已确认，或依赖文件精确锁定（exact 自动采用，规格第 9 节）
    versions = meta.get("versions", [])
    confirmed: dict[str, str] = {}
    pending: list[str] = []
    for v in versions:
        if v.get("confirmed") or v.get("status") == "exact":
            confirmed[v["technology"]] = normalize_version(v["version"])
        else:
            pending.append(v["technology"])
    if not versions and state["mode"] == "migration":
        # Migration 需要当前版本作为对比基准，无版本信息无法执行（规格第 19 节）
        return {"error": "Migration 需要已确认的当前版本，请先在版本面板中指定技术与版本"}

    # Migration 门禁：只校验本次选中的技术（规格第 19 节）
    target_versions = {
        tech.lower(): normalize_version(v) for tech, v in state.get("target_versions", {}).items()
    }
    if state["mode"] == "migration":
        error = _validate_migration_targets(confirmed, target_versions)
        if error:
            return {"error": error}

    # 选取源码文件：跳过依赖/配置/文档，限制数量（规格第 17 节）
    file_tree: list[str] = meta.get("file_tree", [])
    candidates = [
        f for f in file_tree if Path(f).suffix.lower() in config.REVIEW_CODE_EXTENSIONS
    ]
    selected = candidates[: config.REVIEW_MAX_FILES]
    if not selected:
        return {"error": "项目中没有可审查的源码文件"}

    # 读取代码上下文（原文；截断按行边界）与覆盖快照
    project_root = project_dir / "project"
    code_context: dict[str, str] = {}
    snapshot_files: dict[str, dict] = {}
    for rel_path in selected:
        target = project_root / rel_path
        if not target.is_file():
            continue
        text, snapshot = _snapshot_file(target)
        code_context[rel_path] = text
        snapshot_files[rel_path] = snapshot
    if not code_context:
        return {"error": "项目中没有可审查的源码文件"}

    run_scope = {
        "run_id": uuid.uuid4().hex,
        "mode": state["mode"],
        # 检索只允许使用有效版本；待确认版本仅记录，不参与检索
        "confirmed_versions": confirmed,
        "pending_versions": pending,
        "target_versions": target_versions,
        "code_context": code_context,
        "snapshot": {
            "files": snapshot_files,
            # 源码扩展名内但未纳入快照的文件（超数量上限）
            "omitted_files": [f for f in candidates if f not in code_context],
        },
        "coverage": {},
        "budget": {
            "max_candidates": config.VERIFY_MAX_CANDIDATES,
            "max_llm_calls": config.RUN_MAX_LLM_CALLS,
            "doc_enum_max_blocks": config.DOC_ENUM_MAX_BLOCKS,
            "doc_scan_batch_blocks": config.DOC_SCAN_BATCH_BLOCKS,
            "doc_scan_max_calls": config.DOC_SCAN_MAX_CALLS,
            "deadline": config.RUN_DEADLINE_SECONDS,
        },
    }
    return {"run_scope": run_scope}
