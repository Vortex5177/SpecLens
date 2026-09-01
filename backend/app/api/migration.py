"""Migration 路由（Phase 11，规格第 19 / 25 节）。

提供：
- POST /api/migrations          创建并同步执行 Migration（与 Review 共用同一 Graph）
- GET  /api/migrations/{id}     查询 Migration 结果（id 即 project_id）

与 Review 的差异（规格第 19 节）：
- 输入多一个目标版本集合（当前版本 + 目标版本）
- Agent 同时检索两个版本的规范做对比
- 产出 MigrationIssue（current_behavior / target_behavior / reason / suggested_change）

结果持久化在 uploads/{project_id}/migration.json，重复迁移会覆盖旧结果。
"""
import json
import re

from fastapi import APIRouter, HTTPException

from app import config
from app.graph.graph import build_review_graph
from app.models.schemas import (
    MigrationRequest,
    MigrationResponse,
    MigrationResult,
)

router = APIRouter()

# 与 project / review 路由一致：32 位十六进制，防路径注入
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@router.post("/migrations", response_model=MigrationResponse, status_code=201)
def create_migration(request: MigrationRequest) -> MigrationResponse:
    """同步执行迁移分析：分析 -> Agent 对比两版本规范 -> 生成迁移 Fix Prompt。"""
    project_id = request.project_id
    if not _ID_PATTERN.match(project_id):
        raise HTTPException(status_code=400, detail="无效的项目 ID")
    project_dir = config.UPLOAD_DIR / project_id
    if not (project_dir / "meta.json").is_file():
        raise HTTPException(status_code=404, detail="项目不存在，请先上传")
    if not request.target_versions:
        raise HTTPException(status_code=400, detail="请至少提供一个迁移目标版本")

    # 列表去重转 dict（后者覆盖前者）
    target_versions = {v.technology.lower(): v.version for v in request.target_versions}

    # 与 Review 共用同一条流水线（规格第 19 节），仅 mode 与目标版本不同
    graph = build_review_graph()
    try:
        final_state = graph.invoke(
            {
                "project_id": project_id,
                "project_path": str(project_dir),
                "mode": "migration",
                "target_versions": target_versions,
            }
        )
    except Exception as exc:  # LLM / 向量库异常不向前端泄露堆栈（规格第 27 节）
        raise HTTPException(status_code=500, detail=f"迁移分析执行失败：{exc}") from exc

    if final_state.get("error"):
        raise HTTPException(status_code=400, detail=final_state["error"])

    result = MigrationResult(summary=final_state["summary"], issues=final_state["issues"])
    payload = {
        "migration_id": project_id,
        "project_id": project_id,
        "target_versions": target_versions,
        "result": result.model_dump(),
        "project_fix_prompt": final_state["project_fix_prompt"],
    }
    _save_migration(project_dir, payload)
    return MigrationResponse(
        migration_id=project_id,
        project_id=project_id,
        result=result,
        project_fix_prompt=final_state["project_fix_prompt"],
    )


@router.get("/migrations/{migration_id}")
def get_migration(migration_id: str) -> dict:
    """查询已保存的 Migration 结果（含项目级迁移 Fix Prompt）。"""
    if not _ID_PATTERN.match(migration_id):
        raise HTTPException(status_code=400, detail="无效的 Migration ID")
    migration_path = config.UPLOAD_DIR / migration_id / "migration.json"
    if not migration_path.is_file():
        raise HTTPException(status_code=404, detail="Migration 结果不存在，请先创建迁移分析")
    return json.loads(migration_path.read_text(encoding="utf-8"))


def _save_migration(project_dir, payload: dict) -> None:
    (project_dir / "migration.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
