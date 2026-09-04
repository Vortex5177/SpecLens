"""Pydantic 数据模型。

Phase 2：项目上传与分析结果的响应模型。
Phase 3：依赖版本识别与用户确认。
Phase 9：Review 结构化输出（Structured Output）。
Phase 11：Migration 结构化输出（规格第 19 节，与 Review 共用引擎）。
"""
from typing import Literal

from pydantic import BaseModel

# 版本状态：
# - exact：从依赖文件读到精确版本（含锁文件）
# - needs_confirmation：只有范围约束，必须由用户确认（规格第 9 节：绝不猜测）
VersionStatus = Literal["exact", "needs_confirmation"]


class DetectedVersion(BaseModel):
    """单个技术的版本识别结果。"""

    technology: str
    # 依赖文件中的原始声明，例如 "==0.115.0"、">=0.30"、"^18.2.0"
    raw_spec: str
    # 精确版本；待确认时为 None
    version: str | None = None
    status: VersionStatus
    # 用户是否已确认/覆盖（规格第 10 节）
    confirmed: bool = False
    source_file: str


class ProjectAnalysis(BaseModel):
    """项目结构分析结果（上传响应与查询响应共用）。"""

    project_id: str
    file_count: int
    # 语言 -> 文件数，例如 {"Python": 12, "JavaScript": 3}
    languages: dict[str, int]
    # 检测到的依赖描述文件相对路径
    dependency_files: list[str]
    # 文件树（相对路径列表，超过上限会被截断）
    file_tree: list[str]
    tree_truncated: bool
    # 依赖版本识别结果（Phase 3）
    versions: list[DetectedVersion] = []


class UploadResponse(BaseModel):
    """POST /api/projects/upload 响应。"""

    project_id: str
    analysis: ProjectAnalysis


class VersionSelection(BaseModel):
    """用户对单个技术的版本确认/覆盖（请求体元素）。"""

    technology: str
    version: str


class ConfirmVersionsRequest(BaseModel):
    """POST /api/projects/{project_id}/versions 请求体。"""

    versions: list[VersionSelection]


class ErrorResponse(BaseModel):
    """统一错误响应体（避免向前端暴露 traceback）。"""

    detail: str


# ===== Phase 9：Review 结构化输出（规格第 20 节）=====
# 审查维度仅三个（规格第 18 节）：API 合规 / 安全 / 健壮性。
IssueCategory = Literal["api", "security", "robustness"]
IssueSeverity = Literal["high", "medium", "low"]
IssueConfidence = Literal["high", "medium", "low"]


class ReviewIssue(BaseModel):
    """单个审查问题（LLM 直接产出，fix_prompt 由后端确定性生成）。"""

    file: str
    line: int | None = None
    category: IssueCategory
    severity: IssueSeverity
    confidence: IssueConfidence
    title: str
    description: str
    # 依据：引用检索到的文档内容；无证据时留空并将 source 设为 llm_inference
    evidence: str = ""
    # 证据来源：知识文档的 source 路径，或 "llm_inference"（规格第 21 节：不得伪造官方依据）
    source: str
    suggestion: str
    # Phase 10 由后端确定性生成，LLM 不产出此字段
    fix_prompt: str = ""


class ReviewResult(BaseModel):
    """Review 阶段的结构化输出（Agent 的 response_format）。"""

    summary: str
    issues: list[ReviewIssue]


class ReviewRequest(BaseModel):
    """POST /api/reviews 请求体。"""

    project_id: str
    mode: Literal["code_review"] = "code_review"


class ReviewResponse(BaseModel):
    """POST /api/reviews 响应。"""

    review_id: str
    project_id: str
    mode: str
    result: ReviewResult


# ===== Phase 11：Migration 结构化输出（规格第 19 节）=====
# Migration Issue 的核心是对比：当前行为（代码现状，基于当前版本规范）
# vs 目标行为（目标版本规范），字段与 Review Issue 不同，故独立建模。


class MigrationIssue(BaseModel):
    """单个迁移问题（LLM 直接产出，fix_prompt 由后端确定性生成）。"""

    file: str
    line: int | None = None
    # 发生迁移的技术与版本对，如 fastapi 0.110 -> 0.120
    technology: str
    current_version: str
    target_version: str
    title: str
    # 迁移影响程度：不迁会导致破坏性变化为 high，需调整为 medium，建议性为 low
    severity: IssueSeverity
    # 代码在当前版本下的行为/用法（可结合检索到的当前版本规范）
    current_behavior: str
    # 目标版本的规范要求或行为变化（必须来自检索证据或标注为推理）
    target_behavior: str
    # 为什么需要迁移（版本差异说明）
    reason: str
    # 依据：引用检索到的文档内容；无证据时留空并将 source 设为 llm_inference
    evidence: str = ""
    # 证据来源：知识文档的 source 路径，或 "llm_inference"（规格原则 5）
    source: str
    suggested_change: str
    # Phase 12 双向对照置信度（仅迁移合并器填写）:
    #   high = 文档方向 + 代码方向双侧命中（变更依据与代码用法互相印证）
    #   medium = 仅文档方向（变更已报告，代码位置建议人工复核）
    #   low = 仅代码方向（待商榷：检索未召回变更依据）
    # Agent 单独产出时为 None
    confidence: IssueConfidence | None = None
    # Phase 11 由后端确定性生成，LLM 不产出此字段
    fix_prompt: str = ""


class MigrationResult(BaseModel):
    """Migration 阶段的结构化输出（Agent 的 response_format）。"""

    summary: str
    issues: list[MigrationIssue]


class MigrationRequest(BaseModel):
    """POST /api/migrations 请求体。

    target_versions 只填需要迁移的技术（不强制全部技术都迁）。
    """

    project_id: str
    # 复用 VersionSelection（technology + version），此处 version 为目标版本
    target_versions: list[VersionSelection]


class MigrationResponse(BaseModel):
    """POST /api/migrations 响应（直接携带项目级 Fix Prompt，前端无需二次请求）。"""

    migration_id: str
    project_id: str
    result: MigrationResult
    project_fix_prompt: str


# ===== Phase 12：两阶段管线（发现-验证分离）的中间结构 =====
# 阶段 1（侦察）的清单条目：只发现嫌疑/用法位置，不下结论；
# 阶段 2 由程序 for 循环逐条"检索 + 单次确认"。坏条目由管线丢弃，
# 模型仅用于结构校验与文档化（非 LLM response_format）。


class Suspicion(BaseModel):
    """审查阶段 1 产出的单条嫌疑（侦察清单条目，非最终审查结论）。"""

    file: str
    line: int | None = None
    # 涉及技术；security/robustness 维度可留空
    technology: str = ""
    topic: IssueCategory = "api"
    description: str
    # 侦察阶段的严重度预估（无证据支撑，仅用于确定验证优先级）
    severity_guess: IssueSeverity = "medium"
    # 侦察阶段建议的检索关键词（阶段 2 直接使用）
    query: str = ""


class UsagePoint(BaseModel):
    """迁移阶段 1 产出的单条用法点（代码中一处使用迁移技术的位置）。"""

    file: str
    line: int | None = None
    technology: str
    usage: str
    query: str = ""
