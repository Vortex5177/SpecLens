"""官方知识库采集脚本（SpecLens §2-§5）。

读取 knowledge/sources/*.yaml 配置，下载官方文档页面（What's New / Changelog），
清理 HTML 为纯文本，保存到 knowledge/official/{tech}/{version}/，
然后调用现有 ingest_document() 完成 Chunk → Embedding → Qdrant 写入。

设计原则：
- 不创建第二套 RAG pipeline——下载/清理是预处理，之后完全复用现有 ingestion
- 确定性块 ID（uuid5）保证重复执行幂等，不会产生重复向量
- 只入库版本变化部分（What's New），稳定基础知识交给 LLM

用法：
    python scripts/ingest_official.py                          # 处理所有 Source
    python scripts/ingest_official.py --tech python             # 只处理 Python
    python scripts/ingest_official.py --tech python --version 3.13  # 只处理指定版本
"""
import argparse
import sys
import time
import urllib.request
from pathlib import Path

# ── 路径设置：让脚本能导入 backend 模块 ──────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import yaml  # noqa: E402  (pyyaml，通常随 langchain 已安装)
from bs4 import BeautifulSoup  # noqa: E402

from app import config  # noqa: E402
from app.services.ingestion import (  # noqa: E402
    ensure_collections,
    get_qdrant_client,
    ingest_document,
)

# ── 常量 ────────────────────────────────────────────────────────
SOURCES_DIR = config.KNOWLEDGE_DIR / "sources"
USER_AGENT = "SpecLens/1.0 (official-docs-ingestion)"
DOWNLOAD_TIMEOUT = 60  # 秒；What's New 页面通常 < 1MB


# ── HTML 清理 ───────────────────────────────────────────────────
def html_to_text(html: str) -> str:
    """将 HTML 页面清理为纯文本。

    策略：
    1. 移除 script / style / nav / header / footer / aside 等噪音标签
    2. 定位正文区域（Sphinx 的 div[role=main]、MkDocs 的 article.md-content__inner）
    3. 提取文本，保留段落换行

    适用于 docs.python.org（Sphinx）和大多数 MkDocs 生成的文档站。
    """
    soup = BeautifulSoup(html, "html.parser")
    # 移除噪音标签
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    # 定位正文区域（按优先级尝试多种选择器）
    main = (
        soup.find("div", {"role": "main"})           # Sphinx
        or soup.find("article", class_="md-content__inner")  # MkDocs
        or soup.find("main")                          # 通用 HTML5
        or soup.body                                  # 兜底
        or soup
    )
    # 提取文本：段落间用换行分隔，去除首尾空白
    return main.get_text(separator="\n", strip=True)


# ── 下载 ────────────────────────────────────────────────────────
def download(url: str) -> str:
    """下载 URL 内容，返回 HTML 字符串。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="ignore")


# ── Source 配置加载 ─────────────────────────────────────────────
def load_sources(
    tech_filter: str | None = None,
    version_filter: str | None = None,
) -> list[dict]:
    """加载 knowledge/sources/*.yaml，返回待处理的文档列表。"""
    if not SOURCES_DIR.is_dir():
        return []
    docs = []
    for yaml_file in sorted(SOURCES_DIR.glob("*.yaml")):
        with open(yaml_file, encoding="utf-8") as f:
            source = yaml.safe_load(f)
        if not source or "technology" not in source:
            continue
        tech = source["technology"]
        if tech_filter and tech != tech_filter:
            continue
        for doc in source.get("documents", []):
            version = str(doc.get("version", ""))
            if version_filter and version != version_filter:
                continue
            if not doc.get("url"):
                continue
            docs.append({
                "technology": tech,
                "version": version,
                "document_type": doc.get("document_type", "whats_new"),
                "url": doc["url"],
                # 文件名默认 whatsnew_{version}.txt，确保 infer_document_type 能识别
                "filename": doc.get("filename", f"whatsnew_{version}.txt"),
            })
    return docs


# ── 主流程 ──────────────────────────────────────────────────────
def ingest_one(doc: dict) -> dict:
    """处理单个文档：下载 → 清理 → 保存 → 入库。返回结果摘要。"""
    tech = doc["technology"]
    version = doc["version"]
    url = doc["url"]
    filename = doc["filename"]

    result = {
        "technology": tech,
        "version": version,
        "url": url,
        "status": "ok",
        "chunks": 0,
        "file": "",
        "error": "",
    }

    # 1. 下载
    try:
        html = download(url)
    except Exception as e:
        result["status"] = "download_failed"
        result["error"] = str(e)
        return result

    # 2. 清理 HTML → 纯文本
    text = html_to_text(html)
    if not text.strip():
        result["status"] = "empty_content"
        result["error"] = "HTML cleaned to empty text"
        return result

    # 3. 保存到 knowledge/official/{tech}/{version}/
    target_dir = config.KNOWLEDGE_DIR / "official" / tech / version
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / filename
    file_path.write_text(text, encoding="utf-8")
    result["file"] = file_path.relative_to(config.KNOWLEDGE_DIR).as_posix()

    # 4. 入库（复用现有 pipeline：Chunk → Embedding → Qdrant）
    metadata = {
        "technology": tech,
        "version": version,
        "source_type": "official",
        "document_type": doc["document_type"],
        "topic": Path(filename).stem,
        "source_url": url,  # 新增：记录原始 URL，Review 展示时可显示来源
    }
    try:
        chunks = ingest_document(file_path, metadata)
        result["chunks"] = chunks
    except Exception as e:
        result["status"] = "ingest_failed"
        result["error"] = str(e)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="采集官方文档（What's New）并入库 Qdrant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例：
  python scripts/ingest_official.py                        # 处理所有 Source 配置
  python scripts/ingest_official.py --tech python           # 只处理 Python
  python scripts/ingest_official.py --tech python --version 3.13
""",
    )
    parser.add_argument("--tech", help="只处理指定技术（如 python）")
    parser.add_argument("--version", help="只处理指定版本（如 3.13）")
    args = parser.parse_args()

    # 加载 Source 配置
    docs = load_sources(args.tech, args.version)
    if not docs:
        print("[Info] No matching Source config")
        if not SOURCES_DIR.is_dir():
            print(f"  Hint: Source dir not found -> {SOURCES_DIR}")
            print(f"  Create {SOURCES_DIR}/python.yaml etc.")
        return

    print(f"[Info] {len(docs)} document(s) to process\n")

    # 确保 Qdrant collection 存在
    try:
        client = get_qdrant_client()
        ensure_collections(client)
    except Exception as e:
        print(f"[Error] Cannot connect Qdrant ({config.QDRANT_URL}): {e}")
        print("  Start Qdrant first: tools/qdrant/start-qdrant.bat")
        sys.exit(1)

    # 逐个处理
    results = []
    for i, doc in enumerate(docs, 1):
        label = f"[{i}/{len(docs)}] {doc['technology']} {doc['version']}"
        print(f"{label} downloading {doc['url']} ...", end=" ", flush=True)
        t0 = time.time()
        r = ingest_one(doc)
        elapsed = time.time() - t0
        results.append(r)

        if r["status"] == "ok":
            print(f"[OK] {r['chunks']} chunks ({elapsed:.1f}s) -> {r['file']}")
        else:
            print(f"[FAIL] {r['status']}: {r['error']}")

    # 汇总
    ok = [r for r in results if r["status"] == "ok"]
    fail = [r for r in results if r["status"] != "ok"]
    total_chunks = sum(r["chunks"] for r in ok)
    print(f"\n{'='*50}")
    print(f"[Done] {len(ok)}/{len(results)} succeeded, {total_chunks} chunks total")
    if fail:
        print(f"[Failed] {len(fail)}:")
        for r in fail:
            print(f"  - {r['technology']} {r['version']}: {r['error']}")


if __name__ == "__main__":
    main()
