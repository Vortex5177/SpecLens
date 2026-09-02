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
    infer_document_type,
    ingest_document,
    ingest_knowledge,
    invalidate_catalog_cache,
)

router = APIRouter(prefix="/api/knowledge")

# technology / version 会直接成为目录名：只允许安全字符，防路径穿越与非法目录（规格第 26 节）
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
# 从文件名提取版本号：X.Y 或 X.Y.Z 格式（每段 1-3 位数字，覆盖 fastapi 0.120 等 3 位小版本）。
# 取最后一个匹配（文件末尾的版本号通常是该文档所描述的版本，
# 如 whatsnew_3.11.txt → 3.11、whatsnew_0.120 → 0.120、python-3.14-docs → 3.14）
# 注意：不用 \b，因为 Python 的 \w 包含 _，而文件名常见 _3.11 写法，\b 在此不成立；
# 改用 (?:^|\D) 匹配「开头或非数字字符」作为左锚点，右锚点用 (?=\D|$) 确保版本号后面不是数字
_VERSION_IN_FILENAME = re.compile(r"(?:^|\D)(\d{1,3}\.\d{1,3}(?:\.\d{1,3})?)(?=\D|$)")
_MAX_DOC_SIZE = 10 * 1024 * 1024  # 单份知识文档最大 10MB（与项目单文件上限一致）
_MAX_ZIP_SIZE = 100 * 1024 * 1024  # 整个压缩包最大 100MB（官方文档整站导出常见）
_MAX_ZIP_FILES = 2000  # 压缩包内最多入库的知识文档数（官方文档整站导出可能上千个）


def extract_version_from_filename(filename: str) -> str | None:
    """按文件名推断版本号（用于 zip 上传的 auto_detect_version 模式）。

    匹配 X.Y 或 X.Y.Z 格式，取最后一次匹配（文档末尾的版本号通常是
    该文件所描述的版本）。不匹配则返回 None，由调用方决定回退。
    典型命中：whatsnew_3.11 → 3.11；python-3.14-docs → 3.14；v2.0 → 2.0
    """
    matches = _VERSION_IN_FILENAME.findall(filename)
    return matches[-1] if matches else None


@router.post("/ingest")
def ingest() -> dict:
    """全量入库：官方文档 + 安全规范（首次加载 BGE-M3 模型可能需要数分钟）。"""
    try:
        result = ingest_knowledge()
    except Exception as exc:  # 入库失败不应向前端泄露堆栈
        raise HTTPException(status_code=500, detail=f"知识库入库失败：{exc}") from exc
    invalidate_catalog_cache()  # 分块数可能变化，失效目录缓存
    return result


@router.post("/documents", status_code=201)
def upload_document(
    file: UploadFile = File(..., description="知识文档（.md/.txt/.rst）或文档压缩包（.zip）"),
    source_type: str = Form(..., description="official 或 security"),
    technology: str = Form("", description="技术名（official 必填，如 fastapi）"),
    version: str = Form("", description="版本：单文件必填；zip 且开启 auto_detect_version 时可留空（回退值）"),
    document_type: str = Form(
        "auto",
        description="official 文档类型：reference（规范参考）/ whats_new（版本变更）/ auto（按文件名推断）",
    ),
    auto_detect_version: str = Form(
        "false",
        description="仅 zip 生效：true 时按每个文件名识别版本号，失败则回退到表单 version",
    ),
) -> dict:
    """用户上传知识文档并即时入库（规格第 11 节）。

    official：保存到 knowledge/official/{technology}/{version}/ 并携带对应元数据；
    security：保存到 knowledge/security/，固定 technology=general、version=latest。
    document_type 区分 reference 与 whats_new（Migration 检索的变更证据，
    SpecLens §2），auto 时按文件名推断。
    zip 模式下可开启 auto_detect_version，让包内每个文件按文件名识别真实版本
    （如 whatsnew_3.11.txt → version=3.11，reference 文件无版本号 → 用表单 version）；
    适用于 Python 这种「基础文档 + 各版本 What's New 打包在一起」的官方发布结构。
    同名文件覆盖，确定性块 ID 保证重复上传幂等。
    """
    if source_type not in ("official", "security"):
        raise HTTPException(status_code=400, detail="source_type 只能是 official 或 security")
    if document_type not in ("auto", "reference", "whats_new"):
        raise HTTPException(
            status_code=400, detail="document_type 只能是 auto / reference / whats_new"
        )
    # 文件名只取最后一段，扩展名白名单，防止路径穿越与非文本文件（规格第 26 节）
    filename = Path(file.filename or "").name
    is_zip = Path(filename).suffix.lower() == ".zip"
    # zip + auto_detect_version=true：允许 version 为空（识别不到的文件会报错而非静默入库）
    auto_version_enabled = is_zip and auto_detect_version.lower() == "true"
    if source_type == "official":
        if not technology:
            raise HTTPException(status_code=400, detail="official 文档必须提供 technology")
        if not version and not auto_version_enabled:
            raise HTTPException(
                status_code=400,
                detail="official 单文件上传必须提供 version；zip 可开启 auto_detect_version 由文件名自动识别",
            )
        if not _SAFE_NAME.match(technology):
            raise HTTPException(
                status_code=400,
                detail="technology 只允许字母、数字与 . _ + -，且不能以符号开头",
            )
        if version and not _SAFE_NAME.match(version):
            raise HTTPException(
                status_code=400,
                detail="version 只允许字母、数字与 . _ + -，且不能以符号开头",
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
        # official 的基准目录：{technology}/{version}，若 version 为空（仅 zip+auto 允许）
        # 则退到 technology 级别，实际文件会落在自动识别出的版本子目录下
        target_dir = config.KNOWLEDGE_DIR / "official" / technology / version if version else config.KNOWLEDGE_DIR / "official" / technology
        # document_type 先存用户选择（可能为 auto）：单文件在下面解析，
        # zip 由 _extract_and_ingest_zip 逐文件解析（压缩包内可能混合两种类型）
        metadata = {
            "technology": technology,
            "version": version,
            "source_type": "official",
            "document_type": document_type,
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
            result = _extract_and_ingest_zip(
                content, target_dir, metadata, auto_version_enabled=auto_version_enabled
            )
        except HTTPException:
            raise
        except Exception as exc:  # 入库失败不泄露堆栈；解压目录已清理，可重新上传
            raise HTTPException(status_code=500, detail=f"压缩包入库失败：{exc}") from exc
        # 计算文件实际分布的版本数（auto_detect 可能把文件分到不同版本目录）
        versions_hit = sorted({item.get("version") for item in result["files_ingested"] if item.get("version")})
        base_rel = target_dir.relative_to(config.KNOWLEDGE_DIR).as_posix()
        if not base_rel.endswith("/"):
            base_rel += "/"
        invalidate_catalog_cache()  # 新增文档与分块数，失效目录缓存
        return {
            "saved_to": base_rel,
            "metadata": metadata,
            "files_ingested": result["files_ingested"],
            "total_chunks": result["total_chunks"],
            "versions_detected": versions_hit,
        }

    file_path = target_dir / filename
    file_path.write_bytes(content)

    # auto 时按文件名解析（单文件场景；zip 在 _extract_and_ingest_zip 内逐文件解析）
    if metadata["document_type"] == "auto":
        metadata["document_type"] = infer_document_type(Path(filename).stem)

    try:
        chunks = ingest_document(file_path, metadata)
    except Exception as exc:  # 入库失败不泄露堆栈；文件已落盘，可稍后用 /ingest 重试
        raise HTTPException(status_code=500, detail=f"文档已保存但入库失败：{exc}") from exc

    invalidate_catalog_cache()  # 新增文档与分块数，失效目录缓存
    return {
        "saved_to": file_path.relative_to(config.KNOWLEDGE_DIR).as_posix(),
        "metadata": metadata,
        "chunks_ingested": chunks,
    }


def _extract_and_ingest_zip(
    content: bytes,
    target_dir: Path,
    metadata: dict,
    auto_version_enabled: bool = False,
) -> dict:
    """解压知识文档压缩包到 target_dir 并逐个入库。

    安全约束（规格第 26 节）：拒绝路径穿越条目、限制总大小与文件数、只收白名单扩展名。
    合并语义：同名文件覆盖（确定性块 ID 保证幂等），目录内其他已有文档不动；
    入库失败时仅清理本次新写入的文件。子目录结构拍平为父目录前缀，
    避免目录嵌套影响清单展示与同名冲突。

    auto_version_enabled=True 时，对每个文件按文件名识别真实版本：
    - 识别成功 → 文件落到 {technology}/{version_real}/，metadata.version 同步更新
    - 识别失败 → 回退到 metadata["version"]（若也为空则报错）
    用于 Python 这种「基础文档 + 各版本 What's New 打包在一起」的官方发布结构，
    确保每份文档落到自己的版本目录，后续区间检索自然命中。
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
        # 缓存已创建的版本目录，避免重复 mkdir
        version_dirs: dict[str, Path] = {}
        fallback_version = metadata.get("version", "")
        try:
            for m, flat_name in valid:
                data = zf.read(m)
                if not data.strip():
                    continue  # 跳过空文档，不影响其余文件入库
                doc_meta = dict(metadata, topic=Path(flat_name).stem)
                # 按文件名识别真实版本（仅当 auto_version_enabled 时）
                per_file_version = None
                if auto_version_enabled:
                    per_file_version = extract_version_from_filename(Path(flat_name).stem)
                    if not per_file_version:
                        if not fallback_version:
                            raise HTTPException(
                                status_code=400,
                                detail=f"文件 {flat_name} 无法从文件名识别版本号，且未提供回退版本",
                            )
                        per_file_version = fallback_version
                    doc_meta["version"] = per_file_version
                    if per_file_version not in version_dirs:
                        # 版本目录位于 technology 下，与单文件上传的布局保持一致
                        vdir = config.KNOWLEDGE_DIR / "official" / metadata["technology"] / per_file_version
                        vdir.mkdir(parents=True, exist_ok=True)
                        version_dirs[per_file_version] = vdir
                    file_path = version_dirs[per_file_version] / flat_name
                else:
                    file_path = target_dir / flat_name
                file_path.write_bytes(data)
                written.append(file_path)
                # 压缩包内可能混合 reference 与 whats_new：auto 时逐文件推断，
                # 显式指定时整包统一（用户已明确声明类型）
                if doc_meta.get("document_type") == "auto":
                    doc_meta["document_type"] = infer_document_type(Path(flat_name).stem)
                chunks = ingest_document(file_path, doc_meta)
                ingested.append({
                    "file": file_path.relative_to(config.KNOWLEDGE_DIR).as_posix(),
                    "chunks": chunks,
                    "version": doc_meta.get("version", ""),
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

    invalidate_catalog_cache()  # 文档与分块已移除，失效目录缓存
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


@router.get("/search/migration")
def search_migration(
    technology: str = Query(min_length=1, description="技术名，如 python"),
    current_version: str = Query(min_length=1, description="当前版本，如 3.10"),
    target_version: str = Query(min_length=1, description="目标版本，如 3.13"),
    query: str = Query(min_length=1, description="检索问题"),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    """Migration Retriever（SpecLens §6）：迁移区间 What's New + 目标版本 Reference。"""
    results = retrieval.search_migration_docs(
        technology, current_version, target_version, query, limit
    )
    return {
        "technology": technology,
        "current_version": current_version,
        "target_version": target_version,
        "results": results,
    }


@router.get("/search/security")
def search_security(
    query: str = Query(min_length=1, description="安全问题，如：密码如何存储"),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    """Security Retriever：安全规范与技术版本无关，不需要 technology/version。"""
    results = retrieval.search_security_docs(query, limit)
    return {"results": results}
