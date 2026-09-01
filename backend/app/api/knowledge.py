"""知识库路由。

提供：
- POST /api/knowledge/ingest            扫描 knowledge/official 与 knowledge/security 并入库 Qdrant
- POST /api/knowledge/documents         用户上传官方文档/安全规范（规格第 11 节），保存后立即增量入库
- GET  /api/knowledge/search            Official Retriever：按 technology+version 过滤检索（均必填）
- GET  /api/knowledge/search/security   Security Retriever：安全规范语义检索（不按版本过滤）
"""
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app import config
from app.services import retrieval
from app.services.ingestion import ingest_document, ingest_knowledge

router = APIRouter(prefix="/api/knowledge")

# technology / version 会直接成为目录名：只允许安全字符，防路径穿越与非法目录（规格第 26 节）
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_MAX_DOC_SIZE = 10 * 1024 * 1024  # 单份知识文档最大 10MB（与项目单文件上限一致）


@router.post("/ingest")
def ingest() -> dict:
    """全量入库：官方文档 + 安全规范（首次加载 BGE-M3 模型可能需要数分钟）。"""
    try:
        return ingest_knowledge()
    except Exception as exc:  # 入库失败不应向前端泄露堆栈
        raise HTTPException(status_code=500, detail=f"知识库入库失败：{exc}") from exc


@router.post("/documents", status_code=201)
def upload_document(
    file: UploadFile = File(..., description="知识文档（.md/.txt/.rst）"),
    source_type: str = Form(..., description="official 或 security"),
    technology: str = Form("", description="技术名（official 必填，如 fastapi）"),
    version: str = Form("", description="版本（official 必填，如 0.120）"),
) -> dict:
    """用户上传知识文档并即时入库（规格第 11 节）。

    official：保存到 knowledge/official/{technology}/{version}/ 并携带对应元数据；
    security：保存到 knowledge/security/，固定 technology=general、version=latest。
    同名文件覆盖，确定性块 ID 保证重复上传幂等。
    """
    if source_type not in ("official", "security"):
        raise HTTPException(status_code=400, detail="source_type 只能是 official 或 security")
    if source_type == "official":
        if not technology or not version:
            raise HTTPException(status_code=400, detail="official 文档必须提供 technology 与 version")
        for name, value in (("technology", technology), ("version", version)):
            if not _SAFE_NAME.match(value):
                raise HTTPException(
                    status_code=400,
                    detail=f"{name} 只允许字母、数字与 . _ + -，且不能以符号开头",
                )

    # 文件名只取最后一段，扩展名白名单，防止路径穿越与非文本文件（规格第 26 节）
    filename = Path(file.filename or "").name
    if not filename or Path(filename).suffix.lower() not in config.KNOWLEDGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"仅支持 {', '.join(sorted(config.KNOWLEDGE_EXTENSIONS))} 格式的文档",
        )
    content = file.file.read()
    if len(content) > _MAX_DOC_SIZE:
        raise HTTPException(status_code=413, detail="文档超过 10MB 限制")
    if not content.strip():
        raise HTTPException(status_code=400, detail="文档内容为空")

    if source_type == "official":
        target_dir = config.KNOWLEDGE_DIR / "official" / technology / version
        metadata = {
            "technology": technology,
            "version": version,
            "source_type": "official",
            "document_type": "official_doc",
            "topic": Path(filename).stem,
        }
    else:
        target_dir = config.KNOWLEDGE_DIR / "security"
        metadata = {
            "technology": "general",
            "version": "latest",
            "source_type": "security",
            "document_type": "security_rule",
            "topic": Path(filename).stem,
        }

    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / filename
    file_path.write_bytes(content)

    try:
        chunks = ingest_document(file_path, metadata)
    except Exception as exc:  # 入库失败不泄露堆栈；文件已落盘，可稍后用 /ingest 重试
        raise HTTPException(status_code=500, detail=f"文档已保存但入库失败：{exc}") from exc

    return {
        "saved_to": file_path.relative_to(config.KNOWLEDGE_DIR).as_posix(),
        "metadata": metadata,
        "chunks_ingested": chunks,
    }


@router.get("/search")
def search(
    technology: str = Query(min_length=1, description="技术名，如 fastapi"),
    version: str = Query(min_length=1, description="用户已确认的版本，如 0.110"),
    query: str = Query(min_length=1, description="检索问题"),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    """Official Retriever：缺少 technology 或 version 直接 422，绝不跨版本返回。"""
    results = retrieval.search_official_docs(technology, version, query, limit)
    return {"technology": technology, "version": version, "results": results}


@router.get("/search/security")
def search_security(
    query: str = Query(min_length=1, description="安全问题，如：密码如何存储"),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    """Security Retriever：安全规范与技术版本无关，不需要 technology/version。"""
    results = retrieval.search_security_docs(query, limit)
    return {"results": results}
