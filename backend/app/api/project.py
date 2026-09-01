"""项目上传与分析路由。

提供：
- POST /api/projects/upload  上传项目 zip，解压并分析（含版本识别）
- GET  /api/projects/{project_id}  查询已上传项目的分析结果
- POST /api/projects/{project_id}/versions  用户确认/覆盖版本（规格第 10 节）

存储布局：uploads/{project_id}/project/（解压后的项目）
         uploads/{project_id}/meta.json（分析结果）
"""
import json
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from app import config
from app.analyzer.dependencies import detect_versions
from app.analyzer.project import analyze_project
from app.models.schemas import (
    ConfirmVersionsRequest,
    ProjectAnalysis,
    UploadResponse,
)
from app.services.upload import UploadError, safe_extract_zip

router = APIRouter()

# project_id 只允许 32 位十六进制（uuid hex），防止路径注入
_PROJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

# 用户提交的版本号只允许常规版本字符，防止注入
_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.\-+_]*$")

# zip 文件魔数（"PK" 开头）
_ZIP_MAGIC = b"PK\x03\x04"


@router.post("/projects/upload", response_model=UploadResponse, status_code=201)
async def upload_project(file: UploadFile) -> UploadResponse:
    """接收项目 zip，安全解压后返回结构分析结果。"""
    content = await file.read()

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > config.MAX_ZIP_SIZE:
        raise HTTPException(status_code=413, detail="压缩包超过 50MB 大小限制")
    if not content.startswith(_ZIP_MAGIC):
        raise HTTPException(status_code=400, detail="仅支持 zip 格式的项目压缩包")

    project_id = uuid.uuid4().hex
    project_dir = config.UPLOAD_DIR / project_id
    project_root = project_dir / "project"
    project_root.mkdir(parents=True, exist_ok=True)

    zip_path = project_dir / "upload.zip"
    try:
        zip_path.write_bytes(content)
        safe_extract_zip(zip_path, project_root)
    except UploadError as exc:
        shutil.rmtree(project_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        zip_path.unlink(missing_ok=True)

    analysis = analyze_project(project_root, project_id)
    # Phase 3：依赖版本识别（只读取依赖文件，不猜测）
    analysis["versions"] = detect_versions(project_root, analysis["dependency_files"])
    _save_meta(project_dir, analysis)
    return UploadResponse(project_id=project_id, analysis=ProjectAnalysis(**analysis))


@router.get("/projects/{project_id}", response_model=ProjectAnalysis)
def get_project(project_id: str) -> ProjectAnalysis:
    """查询指定项目的分析结果。"""
    return ProjectAnalysis(**_load_meta(project_id))


@router.post("/projects/{project_id}/versions", response_model=ProjectAnalysis)
def confirm_versions(project_id: str, request: ConfirmVersionsRequest) -> ProjectAnalysis:
    """用户确认或覆盖版本（规格第 10 节）。

    后续 RAG 必须严格依据此处最终确认的版本。
    """
    if not request.versions:
        raise HTTPException(status_code=400, detail="未提交任何版本确认")
    for selection in request.versions:
        if not _VERSION_PATTERN.match(selection.version):
            raise HTTPException(
                status_code=400, detail=f"无效的版本号：{selection.version}"
            )

    meta = _load_meta(project_id)
    known = {entry["technology"]: entry for entry in meta.get("versions", [])}

    for selection in request.versions:
        tech = selection.technology.lower()
        entry = known.get(tech)
        if entry is None:
            raise HTTPException(
                status_code=400, detail=f"未检测到技术：{selection.technology}"
            )
        entry["version"] = selection.version
        entry["raw_spec"] = selection.version
        entry["status"] = "exact"
        entry["confirmed"] = True

    _save_meta(_project_dir(project_id), meta)
    return ProjectAnalysis(**meta)


def _load_meta(project_id: str) -> dict:
    """读取项目分析结果，不存在则 404。"""
    meta_path = _project_dir(project_id) / "meta.json"
    if not meta_path.is_file():
        raise HTTPException(status_code=404, detail="项目不存在")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _project_dir(project_id: str) -> Path:
    """校验 project_id 格式并返回项目目录。"""
    if not _PROJECT_ID_PATTERN.match(project_id):
        raise HTTPException(status_code=400, detail="无效的项目 ID")
    return config.UPLOAD_DIR / project_id


def _save_meta(project_dir: Path, analysis: dict) -> None:
    """持久化分析结果，供后续查询与 Phase 3+ 复用。"""
    (project_dir / "meta.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
