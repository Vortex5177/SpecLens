import { useState } from "react";

/**
 * 版本检测与确认面板。
 * - exact：展示检测到的精确版本（可覆盖）
 * - needs_confirmation：展示范围约束，必须填写具体版本才能继续
 */
function VersionPanel({ versions, projectId, onConfirmed }) {
  // 每个技术的用户输入值，初始取检测到的版本
  const [inputs, setInputs] = useState(() =>
    Object.fromEntries(versions.map((v) => [v.technology, v.version || ""]))
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const pendingCount = versions.filter(
    (v) => v.status === "needs_confirmation" && !v.confirmed
  ).length;

  function handleInput(technology, value) {
    setInputs((prev) => ({ ...prev, [technology]: value }));
  }

  async function handleConfirm(event) {
    event.preventDefault();
    const selections = versions
      .filter((v) => inputs[v.technology].trim() !== "")
      .map((v) => ({
        technology: v.technology,
        version: inputs[v.technology].trim(),
      }));

    // 待确认的技术必须全部填写（规格第 9 节）
    const missing = versions.filter(
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
          {versions.map((v) => (
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

      <button type="submit" disabled={submitting}>
        {submitting ? "提交中..." : "确认版本"}
      </button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default VersionPanel;
