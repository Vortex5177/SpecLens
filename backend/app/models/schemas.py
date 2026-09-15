"""Pydantic 数据模型（V2：统一中间契约与运行报告）。

V2 核心变化：
- LLM 只产出判断（VerificationOutput），程序持有证据（Evidence）与候选身份（Candidate）。
- 运行报告（RunReport）携带状态 / 覆盖 / 计数 / 未决 / 错误，取代仅含 summary+issues 的旧结果。
- Migration 双向发现的候选统一进入同一核实入口，origin 仅记录来源，不决定置信度。
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict

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


# ===== 枚举 =====
# 审查维度仅三个（规格第 18 节）：API 合规 / 安全 / 健壮性。
IssueCategory = Literal["api", "security", "robustness"]
IssueSeverity = Literal["high", "medium", "low"]
IssueConfidence = Literal["high", "medium", "low"]
# 核实判决与证据状态（方案四节）：三者正交，分开记录。
Decision = Literal["confirmed", "rejected", "insufficient"]
EvidenceStatus = Literal["document_supported", "inferred", "none"]
CandidateOrigin = Literal["code", "document"]
RunStatus = Literal["complete", "partial", "failed"]


class Evidence(BaseModel):
    """程序持有的不可变证据（检索结果或文档枚举块的快照）。

    evidence_id 由程序根据来源 / 元数据 / 内容哈希确定性生成，
    LLM 只允许引用该 ID，不允许自写证据正文或来源。
    """

    evidence_id: str
    content: str
    source: str
    source_url: str = ""
    technology: str = ""
    version: str = ""
    document_type: str = ""
    chunk_index: int | None = None
    content_hash: str
    # 检索命中信息，仅表示召回相似度，不是问题正确概率；文档枚举块无此值。
    retrieval_score: float | None = None


class Candidate(BaseModel):
    """待核实候选（发现阶段产物，不是最终结论）。

    file / line / technology / dimension 由程序校验后绑定，
    LLM 不得在核实阶段改写这些身份字段。
    """

    candidate_id: str
    # suspicion = Review 嫌疑；usage = Migration 用法点（含文档方向定位的用法）
    kind: Literal["suspicion", "usage"]
    # 发现来源：代码初筛 / 文档方向；两路命中同一候选时为 ["code", "document"]
    origin: list[CandidateOrigin]
    file: str
    line: int | None = None
    technology: str = ""
    dimension: IssueCategory = "api"
    description: str
    # 初筛严重度预估：仅决定核实优先级，不是最终严重度
    severity_guess: IssueSeverity = "medium"
    query_terms: str = ""
    symbol: str = ""
    # 文档方向候选的种子证据（必须是本批枚举块 ID，程序校验）
    seed_evidence_ids: list[str] = []


class EvidenceBundle(BaseModel):
    """单个候选本次允许引用的证据集合与检索状态。"""

    candidate_id: str
    evidence_ids: list[str] = []
    evidences: list[Evidence] = []
    # ok = 检索成功；no_hit = 成功但无命中；retrieval_error = 检索失败
    retrieval_status: Literal["ok", "no_hit", "retrieval_error"] = "ok"
    retrieval_note: str = ""


class VerificationOutput(BaseModel):
    """核实模型的结构化输出（extra=forbid：未知字段即失败，不做自动修复）。

    LLM 只输出判断与解释；file / source / 证据正文等身份字段由程序绑定。
    """

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    evidence_status: EvidenceStatus
    # 只能引用本候选 EvidenceBundle 中的 ID；程序校验，非法即整次核实无效
    evidence_ids: list[str] = []
    title: str = ""
    reason: str = ""
    severity: IssueSeverity | None = None
    confidence: IssueConfidence | None = None
    suggestion: str = ""
    # Migration 专用：成立时必填（程序校验）
    current_behavior: str = ""
    target_behavior: str = ""
    change_reason: str = ""


# ===== Review Issue（程序绑定身份 + 通过校验的判断）=====


class ReviewIssue(BaseModel):
    """单个审查问题。身份字段来自 Candidate，判断字段来自 VerificationOutput。"""

    file: str
    line: int | None = None
    category: IssueCategory
    severity: IssueSeverity
    confidence: IssueConfidence
    title: str
    description: str
    # 旧字段（兼容展示）：由程序从 evidences 派生，不再接受模型自写
    evidence: str = ""
    source: str
    suggestion: str
    # V2 新增：证据状态 / 发现来源 / 结构化证据快照 / 候选血缘
    evidence_status: EvidenceStatus = "inferred"
    origin: list[CandidateOrigin] = []
    evidences: list[Evidence] = []
    candidate_ids: list[str] = []
    # 由后端模板确定性生成，LLM 不产出此字段
    fix_prompt: str = ""


class ReviewRequest(BaseModel):
    """POST /api/reviews 请求体。"""

    project_id: str
    mode: Literal["code_review"] = "code_review"


class ReviewResponse(BaseModel):
    """POST /api/reviews 响应（含完整运行报告与项目级 Fix Prompt）。"""

    review_id: str
    project_id: str
    mode: str
    result: "RunReport"
    project_fix_prompt: str = ""


# ===== Migration Issue =====


class MigrationIssue(BaseModel):
    """单个迁移问题（与 Review 共用核实入口，行为对比字段独立建模）。"""

    file: str
    line: int | None = None
    technology: str
    current_version: str
    target_version: str
    title: str
    severity: IssueSeverity
    current_behavior: str
    target_behavior: str
    reason: str
    evidence: str = ""
    source: str
    suggested_change: str
    # V2：confidence 由核实输出决定，不再按发现方向自动升级
    confidence: IssueConfidence
    evidence_status: EvidenceStatus = "inferred"
    origin: list[CandidateOrigin] = []
    evidences: list[Evidence] = []
    candidate_ids: list[str] = []
    fix_prompt: str = ""


class MigrationRequest(BaseModel):
    """POST /api/migrations 请求体（只填需要迁移的技术）。"""

    project_id: str
    target_versions: list[VersionSelection]


class MigrationResponse(BaseModel):
    """POST /api/migrations 响应。"""

    migration_id: str
    project_id: str
    result: "RunReport"
    project_fix_prompt: str


# ===== V2 运行报告 =====


class RunReport(BaseModel):
    """一次分析的完整运行报告。

    issues 只含已确认且通过校验的问题；未决 / 失败 / 未调度分别记录，
    零问题 + complete 才能表述为「已审查范围内未发现问题」。
    """

    schema_version: int = 2
    run_id: str
    mode: str
    status: RunStatus
    summary: str
    # Review 与 Migration 的 issue 结构不同，此处存已校验的 dict 序列
    issues: list[dict] = []
    coverage: dict = {}
    counts: dict = {}
    # 已运行但无法可靠判断的候选（含 Migration 推断型建议）
    unresolved: list[dict] = []
    errors: list[dict] = []


# 兼容前向引用（RunReport 在 Issue 响应模型之后定义）
ReviewResponse.model_rebuild()
MigrationResponse.model_rebuild()
