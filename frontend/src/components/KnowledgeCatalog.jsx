import { useCallback, useEffect, useState } from "react";

/**
 * 本地知识库文档清单：展示 knowledge/ 下已有的官方文档（按技术/版本分组）
 * 与安全规范，并显示每个文档已入库的分块数（0 表示尚未入库）。
 * 同时提供添加文档表单：上传后立即增量入库并刷新清单。
 */
function KnowledgeCatalog() {
  const [catalog, setCatalog] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // 添加文档表单状态
  const [sourceType, setSourceType] = useState("official");
  const [technology, setTechnology] = useState("");
  const [version, setVersion] = useState("");
  const [docFile, setDocFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadOk, setUploadOk] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/knowledge/catalog");
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || `获取知识库清单失败（HTTP ${res.status}）`);
      }
      setCatalog(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleAdd(event) {
    event.preventDefault();
    setUploadError("");
    setUploadOk("");
    if (!docFile) {
      setUploadError("请选择要添加的文档（.md/.txt/.rst）或压缩包（.zip）");
      return;
    }
    if (sourceType === "official" && (!technology.trim() || !version.trim())) {
      setUploadError("官方文档必须填写技术名与版本");
      return;
    }
    const formData = new FormData();
    formData.append("file", docFile);
    formData.append("source_type", sourceType);
    if (sourceType === "official") {
      formData.append("technology", technology.trim());
      formData.append("version", version.trim());
    }
    setUploading(true);
    try {
      const res = await fetch("/api/knowledge/documents", {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || `添加失败（HTTP ${res.status}）`);
      }
      if (data.files_ingested) {
        // .zip 压缩包：返回文件级明细与总计块数
        setUploadOk(
          `已保存并入库 ${data.files_ingested.length} 个文档（共 ${data.total_chunks} 块）到 ${data.saved_to}`
        );
      } else {
        setUploadOk(`已保存并入库：${data.saved_to}（${data.chunks_ingested} 块）`);
      }
      setDocFile(null);
      setTechnology("");
      setVersion("");
      load(); // 刷新清单与入库状态
    } catch (err) {
      setUploadError(err.message);
    } finally {
      setUploading(false);
    }
  }

  // 删除单个知识文档（文件 + Qdrant 向量），确认后执行并刷新清单
  async function handleDelete({ sourceType: type, technology: tech = "", version = "", file }) {
    const label = tech ? `${tech} ${version} 的 ${file}` : file;
    if (!window.confirm(`确定删除知识文档「${label}」？文件与向量分块都会被移除`)) {
      return;
    }
    try {
      const params = new URLSearchParams({ source_type: type, file });
      if (tech) {
        params.set("technology", tech);
        params.set("version", version);
      }
      const res = await fetch(`/api/knowledge/documents?${params}`, { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || `删除失败（HTTP ${res.status}）`);
      }
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  function chunkBadge(chunks) {
    if (chunks === null || chunks === undefined) {
      return <span className="badge">未知</span>;
    }
    return chunks > 0 ? (
      <span className="badge badge-ok">{chunks} 块已入库</span>
    ) : (
      <span className="badge badge-warning">未入库</span>
    );
  }

  // 官方文档文件数（与后端 total_files 不同：后者含安全规范）
  const officialCount = catalog
    ? catalog.official.reduce(
        (n, tech) => n + tech.versions.reduce((m, v) => m + v.documents.length, 0),
        0
      )
    : 0;

  return (
    <section className="knowledge-card">
      <div className="knowledge-header">
        <h2>本地知识库文档</h2>
        <button type="button" onClick={load} disabled={loading}>
          {loading ? "加载中..." : "刷新"}
        </button>
      </div>
      <p className="hint">
        审查与迁移检索的依据来自这些文档；可在下方表单添加新文档（上传后立即入库）
      </p>

      <form className="knowledge-add" onSubmit={handleAdd}>
        <div className="mode-options">
          <label>
            <input
              type="radio"
              name="doc-type"
              checked={sourceType === "official"}
              onChange={() => setSourceType("official")}
              disabled={uploading}
            />
            官方文档
          </label>
          <label>
            <input
              type="radio"
              name="doc-type"
              checked={sourceType === "security"}
              onChange={() => setSourceType("security")}
              disabled={uploading}
            />
            安全规范
          </label>
        </div>
        {sourceType === "official" && (
          <div className="knowledge-add-fields">
            <input
              type="text"
              placeholder="技术名，如 fastapi"
              value={technology}
              onChange={(e) => setTechnology(e.target.value)}
              disabled={uploading}
            />
            <input
              type="text"
              placeholder="版本，如 0.120"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              disabled={uploading}
            />
          </div>
        )}
        <div className="knowledge-add-fields">
          <input
            type="file"
            accept=".md,.txt,.rst,.zip"
            onChange={(e) => setDocFile(e.target.files[0] || null)}
            disabled={uploading}
          />
          <button type="submit" disabled={uploading}>
            {uploading ? "入库中..." : "添加并入库"}
          </button>
        </div>
        {uploadError && <p className="error">{uploadError}</p>}
        {uploadOk && <p className="ok">{uploadOk}</p>}
      </form>

      {error && <p className="error">{error}</p>}

      {catalog && (
        <>
          <h3>官方文档（{officialCount} 个文件）</h3>
          {catalog.official.length === 0 ? (
            <p className="hint">暂无官方文档</p>
          ) : (
            <table className="version-table">
              <thead>
                <tr>
                  <th>技术</th>
                  <th>版本</th>
                  <th>文档</th>
                  <th>入库状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {catalog.official.map((tech) =>
                  tech.versions.map((v, vi) =>
                    v.documents.map((doc, di) => (
                      <tr key={`${tech.technology}-${v.version}-${doc.file}`}>
                        {vi === 0 && di === 0 && (
                          <td rowSpan={
                            tech.versions.reduce((n, x) => n + x.documents.length, 0)
                          }>
                            <code>{tech.technology}</code>
                          </td>
                        )}
                        {di === 0 && (
                          <td rowSpan={v.documents.length}>
                            <code>{v.version}</code>
                          </td>
                        )}
                        <td>{doc.file}</td>
                        <td>{chunkBadge(doc.chunks)}</td>
                        <td>
                          <button
                            type="button"
                            className="delete-btn"
                            onClick={() =>
                              handleDelete({
                                sourceType: "official",
                                technology: tech.technology,
                                version: v.version,
                                file: doc.file,
                              })
                            }
                          >
                            删除
                          </button>
                        </td>
                      </tr>
                    ))
                  )
                )}
              </tbody>
            </table>
          )}

          <h3>安全规范（{catalog.security.length} 个文件）</h3>
          {catalog.security.length === 0 ? (
            <p className="hint">暂无安全规范</p>
          ) : (
            <table className="version-table">
              <thead>
                <tr>
                  <th>文档</th>
                  <th>主题</th>
                  <th>入库状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {catalog.security.map((doc) => (
                  <tr key={doc.file}>
                    <td>{doc.file}</td>
                    <td>{doc.topic}</td>
                    <td>{chunkBadge(doc.chunks)}</td>
                    <td>
                      <button
                        type="button"
                        className="delete-btn"
                        onClick={() =>
                          handleDelete({ sourceType: "security", file: doc.file })
                        }
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}

export default KnowledgeCatalog;
