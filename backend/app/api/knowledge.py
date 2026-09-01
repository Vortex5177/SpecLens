"""知识库路由。

提供：
- POST /api/knowledge/ingest            扫描 knowledge/official 与 knowledge/security 并入库 Qdrant
- POST /api/knowledge/documents         用户上传官方文档/安全规范（规格第 11 节），保存后立即增量入库
- GET  /api/knowledge/search            Official Retriever：按 technology+version 过滤检索（均必填）
- GET  /api/knowledge/search/security   Security Retriever：安全规范语义检索（不按版本过滤）
"""
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app import config
from app.services import retrieval
from app.services.ingestion import (
    catalog_knowledge,
    delete_document_vectors,
    ingest_document,
    ingest_knowledge,
)

router = APIRouter(prefix="/api/knowledge")

# technology / version 会直接成为目录名：只允许安全字符，防路径穿越与非法目录（规格第 26 节）
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_MAX_DOC_SIZE = 10 * 1024 * 1024  # 单份知识文档最大 10MB（与项目单文件上限一致）
_MAX_ZIP_SIZE = 100 * 1024 * 1024  # 整个压缩包最大 100MB（官方文档整站导出常见）
_MAX_ZIP_FILES = 2000  # 压缩包内最多入库的知识文档数（官方文档整站导出可能上千个）


@router.post("/ingest")
def ingest() -> dict:
    """全量入库：官方文档 + 安全规范（首次加载 BGE-M3 模型可能需要数分钟）。"""
    try:
        return ingest_knowledge()
    except Exception as exc:  # 入库失败不应向前端泄露堆栈
        raise HTTPException(status_code=500, detail=f"知识库入库失败：{exc}") from exc


@router.post("/documents", status_code=201)
def upload_document(
    file: UploadFile = File(..., description="知识文档（.md/.txt/.rst）或文档压缩包（.zip）"),
    source_type: str = Form(..., description="official 或 security"),
    technology: str = Form("", description="技术名（official 必填，如 fastapi）"),
    version: str = Form("", description="版本（official 必填，如 0.120）"),
) -> dict:
    """用户上传知识文档并即时入库（规格第 11 节）。

    official：保存到 knowledge/official/{technology}/{version}/ 并携带对应元数据；
    security：保存到 knowledge/security/，固定 technology=general、version=latest。
    支持 .zip：解压后白名单文档逐个拍平入库（官方文档整站导出常见形式）。
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
    is_zip = Path(filename).suffix.lower() == ".zip"
    if not filename or (not is_zip and Path(filename).suffix.lower() not in config.KNOWLEDGE_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail=f"仅支持 {', '.join(sorted(config.KNOWLEDGE_EXTENSIONS))} 格式的文档或 .zip 压缩包",
        )
    content = file.file.read()
    size_limit = _MAX_ZIP_SIZE if is_zip else _MAX_DOC_SIZE
    if len(content) > size_limit:
        raise HTTPException(status_code=413, detail=f"上传内容超过 {size_limit // 1024 // 1024}MB 限制")
    if not is_zip and not content.strip():
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

    # .zip 压缩包：解压 + 逐个入库，返回文件级明细与总计；返回结构与单文档不同，由前端区分渲染
    if is_zip:
        try:
            result = _extract_and_ingest_zip(content, target_dir, metadata)
        except HTTPException:
            raise
        except Exception as exc:  # 入库失败不泄露堆栈；解压目录已清理，可重新上传
            raise HTTPException(status_code=500, detail=f"压缩包入库失败：{exc}") from exc
        return {
            "saved_to": target_dir.relative_to(config.KNOWLEDGE_DIR).as_posix() + "/",
            "metadata": metadata,
            "files_ingested": result["files_ingested"],
            "total_chunks": result["total_chunks"],
        }

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


def _extract_and_ingest_zip(content: bytes, target_dir: Path, metadata: dict) -> dict:
    """解压知识文档压缩包到 target_dir 并逐个入库。

    安全约束（规格第 26 节）：拒绝路径穿越条目、限制总大小与文件数、只收白名单扩展名。
    合并语义：同名文件覆盖（确定性块 ID 保证幂等），目录内其他已有文档不动；
    入库失败时仅清理本次新写入的文件。子目录结构拍平为父目录前缀，
    避免目录嵌套影响清单展示与同名冲突。
    """
    import io

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="上传的不是有效的 zip 压缩包")
    with zf:
        members = [
            m for m in zf.infolist()
            if not m.is_dir() and not m.filename.endswith("/")
        ]
        valid: list[tuple[zipfile.ZipInfo, str]] = []
        for m in members:
            name = m.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise HTTPException(status_code=400, detail=f"压缩包含非法路径：{m.filename}")
            stem = Path(name).name
            if Path(stem).suffix.lower() in config.KNOWLEDGE_EXTENSIONS:
                flat = "_".join(p for p in name.split("/")[:-1])
                flat = f"{flat}_{Path(stem).stem}{Path(stem).suffix}" if flat else stem
                valid.append((m, flat))
        if not valid:
            raise HTTPException(
                status_code=400,
                detail=f"压缩包内没有 {', '.join(sorted(config.KNOWLEDGE_EXTENSIONS))} 格式的文档",
            )
        if len(valid) > _MAX_ZIP_FILES:
            raise HTTPException(status_code=400, detail=f"压缩包内知识文档超过 {_MAX_ZIP_FILES} 个上限")
        total_size = sum(m.file_size for m, _ in valid)
        if total_size > _MAX_ZIP_SIZE:
            raise HTTPException(status_code=413, detail="压缩包解压后超过 100MB 限制")
        target_dir.mkdir(parents=True, exist_ok=True)
        ingested = []
        written: list[Path] = []
        try:
            for m, flat_name in valid:
                data = zf.read(m)
                if not data.strip():
                    continue  # 跳过空文档，不影响其余文件入库
                file_path = target_dir / flat_name
                file_path.write_bytes(data)
                written.append(file_path)
                doc_meta = dict(metadata, topic=Path(flat_name).stem)
                chunks = ingest_document(file_path, doc_meta)
                ingested.append({
                    "file": file_path.relative_to(config.KNOWLEDGE_DIR).as_posix(),
                    "chunks": chunks,
                })
        except Exception:
            # 仅清理本次新写入的文件，不波及目录内已有文档（孤儿向量会随同名覆盖自愈）
            for p in written:
                p.unlink(missing_ok=True)
            raise
    if not ingested:
        raise HTTPException(status_code=400, detail="压缩包内的知识文档均为空内容")
    return {
        "files_ingested": ingested,
        "total_chunks": sum(item["chunks"] for item in ingested),
    }


@router.delete("/documents")
def delete_document(
    source_type: str = Query(..., description="official 或 security"),
    file: str = Query(..., description="文件名（不含目录）"),
    technology: str = Query("", description="技术名（official 必填）"),
    version: str = Query("", description="版本（official 必填）"),
) -> dict:
    """删除单个知识文档：同时移除本地文件与 Qdrant 中的全部分块。

    删除后若版本目录/技术目录为空则一并清理，保持清单整洁。
    """
    if source_type not in ("official", "security"):
        raise HTTPException(status_code=400, detail="source_type 只能是 official 或 security")

    # 文件名安全校验：禁止目录分隔符，只取末段防路径穿越（规格第 26 节）
    filename = Path(file).name
    if not filename or filename != file or filename != Path(filename).name:
        raise HTTPException(status_code=400, detail="文件名非法")
    if source_type == "official":
        if not technology or not version:
            raise HTTPException(status_code=400, detail="official 文档必须提供 technology 与 version")
        for name, value in (("technology", technology), ("version", version)):
            if not _SAFE_NAME.match(value):
                raise HTTPException(status_code=400, detail=f"{name} 非法")
        source = f"official/{technology}/{version}/{filename}"
    else:
        source = f"security/{filename}"

    file_path = config.KNOWLEDGE_DIR / source
    # 双保险：解析后的路径必须仍在 knowledge/ 内（防符号链接等意外穿越）
    if not file_path.resolve().is_relative_to(config.KNOWLEDGE_DIR.resolve()):
        raise HTTPException(status_code=400, detail="路径非法")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"文档不存在：{source}")

    file_path.unlink()
    chunks_removed = delete_document_vectors(source, source_type)

    # 清理空目录（仅 official：版本目录与技术目录）
    if source_type == "official":
        version_dir = file_path.parent
        tech_dir = version_dir.parent
        if not any(version_dir.iterdir()):
            version_dir.rmdir()
            if not any(tech_dir.iterdir()):
                tech_dir.rmdir()

    return {"deleted": source, "chunks_removed": chunks_removed}


@router.get("/catalog")
def catalog() -> dict:
    """查看本地已有规范文档清单：官方文档按 技术/版本 分组，附已入库分块数。"""
    try:
        return catalog_knowledge()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取知识库目录失败：{exc}") from exc


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
