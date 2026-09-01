"""Pydantic 数据模型。

Phase 2：项目上传与分析结果的响应模型。
Phase 3：依赖版本识别与用户确认。
后续 Phase 会在此追加 Review、Migration 相关模型。
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
