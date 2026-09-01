import { useState } from "react";

/**
 * 版本检测与确认面板。
 * - exact：展示检测到的精确版本（可覆盖）
 * - needs_confirmation：展示范围约束，必须填写具体版本才能继续
 * - 手动添加：未检测到依赖文件时（如只上传了源码），用户可自行指定技术与版本（规格第 1 节）
 */
function VersionPanel({ versions, projectId, onConfirmed }) {
  // 本地可增删的版本列表（初始为后端检测结果，手动添加的技术追加到末尾）
  const [rows, setRows] = useState(versions);
  // 每个技术的用户输入值，初始取检测到的版本
  const [inputs, setInputs] = useState(() =>
    Object.fromEntries(versions.map((v) => [v.technology, v.version || ""]))
  );
  const [manualTech, setManualTech] = useState("");
  const [manualVersion, setManualVersion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

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
      setError("手动添加需同时填写技术名与版本");
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
        <input
          type="text"
          placeholder="技术名，如 fastapi"
          value={manualTech}
          onChange={(e) => setManualTech(e.target.value)}
        />
        <input
          type="text"
          placeholder="版本，如 0.120.0"
          value={manualVersion}
          onChange={(e) => setManualVersion(e.target.value)}
        />
        <button type="button" onClick={handleAddManual} disabled={submitting}>
          手动添加
        </button>
      </div>

      <button type="submit" disabled={submitting}>
        {submitting ? "提交中..." : "确认版本"}
      </button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default VersionPanel;
