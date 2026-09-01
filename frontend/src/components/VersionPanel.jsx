import { useEffect, useState } from "react";

/**
 * 版本检测与确认面板。
 * - exact：展示检测到的精确版本（可覆盖）
 * - needs_confirmation：展示范围约束，必须填写具体版本才能继续
 * - 手动添加：从知识库已有文档中选择技术与版本（选项来自 /api/knowledge/catalog），
 *   保证后续版本敏感检索有据可依（规格第 1 节）
 */
function VersionPanel({ versions, projectId, onConfirmed }) {
  // 本地可增删的版本列表（初始为后端检测结果，手动添加的技术追加到末尾）
  const [rows, setRows] = useState(versions);
  // 每个技术的用户输入值，初始取检测到的版本
  const [inputs, setInputs] = useState(() =>
    Object.fromEntries(versions.map((v) => [v.technology, v.version || ""]))
  );
  // 知识库目录：手动添加的技术/版本候选项（加载失败时降级为不可用并提示）
  const [catalog, setCatalog] = useState(null);
  const [manualTech, setManualTech] = useState("");
  const [manualVersion, setManualVersion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/knowledge/catalog")
      .then((res) => res.json())
      .then((data) => setCatalog(Array.isArray(data.official) ? data : { official: [] }))
      .catch(() => setCatalog({ official: [] }));
  }, []);

  const officialOptions = catalog ? catalog.official : [];
  // 选中技术对应的可选版本列表（知识库目录顺序即展示顺序）
  const versionOptions = officialOptions.find((t) => t.technology === manualTech)?.versions
    .map((v) => v.version) || [];

  const pendingCount = rows.filter(
    (v) => v.status === "needs_confirmation" && !v.confirmed
  ).length;

  function handleInput(technology, value) {
    setInputs((prev) => ({ ...prev, [technology]: value }));
  }

  function handleAddManual(event) {
    event.preventDefault();
    const tech = manualTech.trim().toLowerCase();
    const version = manualVersion.trim();
    if (!tech || !version) {
      setError("手动添加需同时选择技术与版本（候选范围 = 知识库已有文档）");
      return;
    }
    if (!versionOptions.includes(version)) {
      setError(`知识库中没有 ${tech} ${version} 的文档，请先在知识库中添加或选择其他版本`);
      return;
    }
    if (rows.some((v) => v.technology === tech)) {
      setError(`技术 ${tech} 已在列表中，直接在表格里修改版本即可`);
      return;
    }
    setRows((prev) => [
      ...prev,
      {
        technology: tech,
        raw_spec: version,
        version,
        status: "exact",
        confirmed: false,
        source_file: "用户手动指定",
      },
    ]);
    setInputs((prev) => ({ ...prev, [tech]: version }));
    setManualTech("");
    setManualVersion("");
    setError("");
  }

  async function handleConfirm(event) {
    event.preventDefault();
    const selections = rows
      .filter((v) => inputs[v.technology].trim() !== "")
      .map((v) => ({
        technology: v.technology,
        version: inputs[v.technology].trim(),
      }));

    if (selections.length === 0) {
      setError("请先填写至少一个技术的版本（或手动添加技术）");
      return;
    }

    // 待确认的技术必须全部填写（规格第 9 节）
    const missing = rows.filter(
      (v) => v.status === "needs_confirmation" && !v.confirmed && !inputs[v.technology].trim()
    );
    if (missing.length > 0) {
      setError(
        `请填写待确认技术的具体版本：${missing.map((v) => v.technology).join("、")}`
      );
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      const res = await fetch(`/api/projects/${projectId}/versions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ versions: selections }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || `确认失败（HTTP ${res.status}）`);
      }
      onConfirmed(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="version-panel" onSubmit={handleConfirm}>
      <h3>
        检测到的版本
        {pendingCount > 0 && (
          <span className="badge badge-warning">{pendingCount} 项待确认</span>
        )}
      </h3>

      <table className="version-table">
        <thead>
          <tr>
            <th>技术</th>
            <th>依赖文件声明</th>
            <th>状态</th>
            <th>确认版本</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((v) => (
            <tr key={v.technology}>
              <td>
                <code>{v.technology}</code>
              </td>
              <td>
                <code>{v.raw_spec}</code>
                <div className="hint">{v.source_file}</div>
              </td>
              <td>
                {v.confirmed ? (
                  <span className="badge badge-ok">已确认</span>
                ) : v.status === "exact" ? (
                  <span className="badge badge-ok">已检测</span>
                ) : (
                  <span className="badge badge-warning">待确认</span>
                )}
              </td>
              <td>
                <input
                  type="text"
                  placeholder="如 0.120.0"
                  value={inputs[v.technology]}
                  onChange={(e) => handleInput(v.technology, e.target.value)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {rows.length === 0 && (
        <p className="hint">未检测到依赖文件，请在下方手动添加项目使用的技术与版本</p>
      )}

      <div className="manual-add">
        {officialOptions.length === 0 ? (
          <p className="hint">
            {catalog === null
              ? "正在加载知识库文档目录..."
              : "知识库中暂无官方文档，请先在「本地知识库文档」卡片中添加后再手动指定版本"}
          </p>
        ) : (
          <>
            <select
              value={manualTech}
              onChange={(e) => {
                setManualTech(e.target.value);
                setManualVersion(""); // 切换技术后重置版本选择
              }}
            >
              <option value="">选择技术</option>
              {officialOptions.map((t) => (
                <option key={t.technology} value={t.technology}>
                  {t.technology}
                </option>
              ))}
            </select>
            <select
              value={manualVersion}
              onChange={(e) => setManualVersion(e.target.value)}
              disabled={!manualTech}
            >
              <option value="">{manualTech ? "选择版本" : "先选技术"}</option>
              {versionOptions.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
            <button type="button" onClick={handleAddManual} disabled={submitting}>
              手动添加
            </button>
          </>
        )}
      </div>

      <button type="submit" disabled={submitting}>
        {submitting ? "提交中..." : "确认版本"}
      </button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default VersionPanel;
