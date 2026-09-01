"""节点 1：analyze_project（确定性，无 LLM）。

职责（规格原则 3：LangGraph 只负责确定性流程）：
- 读取上传时的分析结果（meta.json）
- 校验版本已全部确认（规格第 9/10 节：未确认不允许审查）
- 选取源码文件并读入代码上下文（不整项目无脑塞给 LLM）
"""
import json
from pathlib import Path

from app import config
from app.graph.state import ReviewState


def analyze_project(state: ReviewState) -> dict:
    project_dir = Path(state["project_path"])
    meta_path = project_dir / "meta.json"
    if not meta_path.is_file():
        return {"error": "项目分析结果不存在，请重新上传项目"}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # 版本校验：所有检测到的技术都必须已确认，绝不猜测（规格第 9 节）
    versions = meta.get("versions", [])
    if not versions:
        return {"error": "未识别到任何依赖版本，无法进行版本敏感审查"}
    pending = [v["technology"] for v in versions if not v.get("confirmed")]
    if pending:
        return {
            "error": "以下技术的版本尚未确认，请先在版本确认接口中指定：" + ", ".join(pending)
        }
    confirmed = {v["technology"]: v["version"] for v in versions}

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
