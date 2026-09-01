"""Review Agent 的工具集（Phase 8，规格第 16 节）。

V1 单 Agent，只有四个只读工具：
list_files / read_file / search_official_docs / search_security_rules

安全边界（规格第 26 节）：
- 所有文件工具限制在项目目录内，防路径穿越
- 不修改代码、不删除文件、不执行命令
- 官方文档检索强制使用用户已确认的版本，拒绝其他版本
"""
from pathlib import Path

from langchain_core.tools import tool

from app import config
from app.services import retrieval


def _norm_version(v: str) -> str:
    """统一版本格式：去掉尾部多余的 .0，让 0.120.0 与 0.120 等价。

    依赖文件常写 0.120.0，知识库目录习惯写 0.120；
    归一化后工具层版本匹配与 Qdrant 过滤都能正确命中。
    """
    while v.endswith(".0") and v.count(".") >= 2:
        v = v[:-2]
    return v


def build_tools(
    project_path: Path,
    confirmed_versions: dict[str, str],
    file_tree: list[str],
    target_versions: dict[str, str] | None = None,
):
    """按项目作用域构造工具（闭包绑定项目路径与已确认版本）。

    Migration 模式（Phase 11）传入 target_versions 后，官方文档检索同时允许
    当前版本与目标版本（规格第 19 节：检索两个版本规范）。

    为避免 LLM 陷入无限工具调用循环，search_official_docs 与
    search_security_rules 内置调用计数器，超过硬上限后返回提示让 Agent 停止。
    """
    target_versions = target_versions or {}
    # 硬上限：防止 LLM 无限调用（规格要求每个维度最多 2 次检索）
    call_counts = {"official": 0, "security": 0}
    OFFICIAL_LIMIT = 3
    SECURITY_LIMIT = 2

    @tool
    def list_files() -> str:
        """列出项目的所有文件（相对路径）。用于了解项目结构、寻找相关代码文件。"""
        return "\n".join(file_tree) if file_tree else "（项目无文件）"

    @tool
    def read_file(path: str) -> str:
        """读取项目中指定文件的内容。参数 path 为相对路径，如 app/auth.py。

        用于查看当前文件之外的相关代码（工具函数、配置等）。
        """
        root = project_path.resolve()
        target = (root / path).resolve()
        # 路径穿越防护：目标必须位于项目目录内
        if not target.is_relative_to(root):
            return f"[错误] 路径 {path} 超出项目范围，拒绝访问"
        if not target.is_file():
            return f"[错误] 文件不存在：{path}"
        text = target.read_text(encoding="utf-8", errors="ignore")
        if len(text) > config.REVIEW_MAX_FILE_CHARS:
            text = text[: config.REVIEW_MAX_FILE_CHARS] + "\n...（内容过长，已截断）"
        return text

    @tool
    def search_official_docs(technology: str, version: str, query: str) -> str:
        """检索指定技术版本的官方文档（版本敏感，绝不跨版本返回）。

        参数必须是已允许的技术与版本（当前已确认版本，
        Migration 模式下还包括用户指定的目标版本），否则会被拒绝。
        用于核实 API 用法、参数、版本行为与推荐写法。
        """
        call_counts["official"] += 1
        if call_counts["official"] > OFFICIAL_LIMIT:
            return (
                f"[终止] 官方文档检索已达上限 {OFFICIAL_LIMIT} 次，禁止继续调用。"
                "请直接用已有证据（或 llm_inference）生成最终结果。"
            )
        tech = technology.lower()
        expected = confirmed_versions.get(tech)
        if expected is None:
            return f"[错误] 未检测到技术 {technology}，可检索的技术：{', '.join(confirmed_versions) or '无'}"
        # 合法版本集合：当前已确认版本 +（Migration 时）目标版本，归一化后比较
        allowed = {_norm_version(expected)}
        target = target_versions.get(tech)
        if target:
            allowed.add(_norm_version(target))
        if _norm_version(version) not in allowed:
            allowed_text = " 或 ".join(sorted(allowed))
            return (
                f"[错误] 版本 {version} 不在允许范围内。{technology} 允许检索的版本是 {allowed_text}，"
                "检索必须使用这些版本"
            )
        results = retrieval.search_official_docs(tech, _norm_version(version), query, limit=5)
        if not results:
            return (
                f"[提示] 知识库中没有 {technology} {version} 与「{query}」相关的官方文档。"
                "请直接基于代码本身判断，不要继续反复检索。"
            )
        return "\n---\n".join(
            f"[来源: {r['source']} | 相似度: {r['score']}]\n{r['content']}" for r in results
        )

    @tool
    def search_security_rules(query: str) -> str:
        """检索通用安全编码规范（与技术版本无关）。

        用于密码处理、输入验证、认证授权、敏感信息、注入防护等安全问题。
        """
        call_counts["security"] += 1
        if call_counts["security"] > SECURITY_LIMIT:
            return (
                f"[终止] 安全规范检索已达上限 {SECURITY_LIMIT} 次，禁止继续调用。"
                "请直接用已有证据生成最终结果。"
            )
        results = retrieval.search_security_docs(query, limit=5)
        if not results:
            return f"[提示] 知识库中没有与「{query}」相关的安全规范"
        return "\n---\n".join(
            f"[来源: {r['source']} | 相似度: {r['score']}]\n{r['content']}" for r in results
        )

    return [list_files, read_file, search_official_docs, search_security_rules]
