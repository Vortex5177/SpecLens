"""节点 1：analyze_project（确定性，无 LLM）。

职责（规格原则 3：LangGraph 只负责确定性流程）：
- 读取上传时的分析结果（meta.json）
- 校验版本确认状态（规格第 9/10 节）：检测到的版本必须已确认；
  Code Review 允许无任何版本信息（降级为安全规范 + LLM 自身知识审查），
  Migration 必须有已确认版本（需要当前版本做对比基准）
- 选取源码文件并读入代码上下文（不整项目无脑塞给 LLM）
"""
import json
from pathlib import Path

from app import config
from app.graph.state import ReviewState
from app.graph.tools import _norm_version


def _validate_migration_targets(
    confirmed: dict[str, str], target_versions: dict[str, str]
) -> str | None:
    """Migration 目标版本校验（规格第 19 节），返回错误信息或 None。

    规则：目标技术必须是项目已确认的技术，且目标版本与当前版本不同。
    """
    if not target_versions:
        return "请至少选择一个需要迁移的技术及其目标版本"
    for tech, target in target_versions.items():
        current = confirmed.get(tech)
        if current is None:
            return f"技术 {tech} 不在项目已确认的技术列表中：{', '.join(confirmed)}"
        if _norm_version(target) == _norm_version(current):
            return f"{tech} 的目标版本 {target} 与当前版本 {current} 相同，无需迁移"
    return None


def analyze_project(state: ReviewState) -> dict:
    project_dir = Path(state["project_path"])
    meta_path = project_dir / "meta.json"
    if not meta_path.is_file():
        return {"error": "项目分析结果不存在，请重新上传项目"}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # 版本校验（规格第 9 节：绝不猜测）
    versions = meta.get("versions", [])
    # 检测到但未确认的版本必须先确认（两种模式都拦截）
    pending = [v["technology"] for v in versions if not v.get("confirmed")]
    if pending:
        return {
            "error": "以下技术的版本尚未确认，请先在版本确认接口中指定：" + ", ".join(pending)
        }
    if not versions and state["mode"] == "migration":
        # Migration 需要当前版本作为对比基准，无版本信息无法执行（规格第 19 节）
        return {"error": "Migration 需要已确认的当前版本，请先在版本面板中指定技术与版本"}
    # code_review 且无版本：允许继续，降级为安全规范 + LLM 自身知识审查（不做版本敏感检索）
    confirmed = {v["technology"]: v["version"] for v in versions}

    # Migration 模式：额外校验目标版本（规格第 19 节：当前版本 + 目标版本）
    if state["mode"] == "migration":
        error = _validate_migration_targets(confirmed, state.get("target_versions", {}))
        if error:
            return {"error": error}

    # 选取源码文件：跳过依赖/配置/文档，限制数量（规格第 17 节）
    file_tree: list[str] = meta.get("file_tree", [])
    selected = [
        f
        for f in file_tree
        if Path(f).suffix.lower() in config.REVIEW_CODE_EXTENSIONS
    ][: config.REVIEW_MAX_FILES]
    if not selected:
        return {"error": "项目中没有可审查的源码文件"}

    # 读取代码上下文（单文件超长截断）
    project_root = project_dir / "project"
    code_context: dict[str, str] = {}
    for rel_path in selected:
        target = project_root / rel_path
        if not target.is_file():
            continue
        text = target.read_text(encoding="utf-8", errors="ignore")
        if len(text) > config.REVIEW_MAX_FILE_CHARS:
            text = text[: config.REVIEW_MAX_FILE_CHARS] + "\n...（内容过长，已截断）"
        code_context[rel_path] = text

    return {
        "languages": meta.get("languages", {}),
        "confirmed_versions": confirmed,
        "file_tree": file_tree,
        "selected_files": list(code_context),
        "code_context": code_context,
    }
