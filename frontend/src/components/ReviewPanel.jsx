import { useEffect, useState } from "react";

/**
 * 分析模式选择与触发面板（规格第 24 节：Review Mode）。
 * - code_review：直接开始审查
 * - migration：为每个已确认技术填写目标版本（至少填一个），
 *   后端对比当前版本与目标版本的规范（规格第 19 节）
 * - 两种模式都是同步接口，耗时可能超过 1 分钟，期间禁用按钮
 */
function ReviewPanel({ projectId, versions, onStart, onCompleted }) {
  const [mode, setMode] = useState("code_review");
  // migration 目标版本输入：technology -> 用户输入
  const [targets, setTargets] = useState(() =>
    Object.fromEntries(versions.map((v) => [v.technology, ""]))
  );
  // 知识库中各技术可用版本（用于下拉选项）
  const [availableVersions, setAvailableVersions] = useState({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  // 有效当前版本：用户已确认或依赖文件精确锁定（V2 部分确认政策）
  const activeVersions = versions.filter(
    (v) => v.confirmed || v.status === "exact"
  );

  // versions 变化时同步 targets（用户手动添加新技术后，targets 需要补上新条目）
  useEffect(() => {
    setTargets((prev) => {
      const next = {};
      for (const v of versions) {
        next[v.technology] = prev[v.technology] ?? "";
      }
      return next;
    });
  }, [versions]);

  // 加载知识库目录，获取各技术可用版本
  useEffect(() => {
    fetch("/api/knowledge/catalog")
      .then((res) => res.json())
      .then((data) => {
        const map = {};
        for (const t of data.official || []) {
          map[t.technology] = t.versions.map((v) => v.version);
        }
        setAvailableVersions(map);
      })
      .catch(() => {});
  }, []);

  function handleTarget(technology, value) {
    setTargets((prev) => ({ ...prev, [technology]: value }));
  }

  // migration 必须至少填写一个目标版本；选中的技术必须有有效当前版本
  function selectedTargets() {
    return versions
      .filter((v) => (targets[v.technology] || "").trim() !== "")
      .map((v) => ({
        technology: v.technology,
        version: (targets[v.technology] || "").trim(),
      }));
  }

  async function handleStart() {
    if (mode === "migration") {
      const selected = selectedTargets();
      if (selected.length === 0) {
        setError("请至少为一个技术填写迁移目标版本（如 0.120）");
        return;
      }
      const missing = selected.filter(
        (t) => !activeVersions.some((v) => v.technology === t.technology)
      );
      if (missing.length > 0) {
        setError(
          `以下技术没有已确认或精确锁定的当前版本，无法迁移：${missing.map((t) => t.technology).join("、")}`
        );
        return;
      }
    }
    // 发起重跑立即清除旧结果；请求失败不会把上次结果当本次结果
    onStart();
    setRunning(true);
    setError("");
    try {
      if (mode === "code_review") {
        const res = await fetch("/api/reviews", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project_id: projectId, mode }),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || `审查失败（HTTP ${res.status}）`);
        }
        // V2：POST 响应直接携带完整运行报告与 project_fix_prompt
        onCompleted({ mode, data });
      } else {
        const res = await fetch("/api/migrations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_id: projectId,
            target_versions: selectedTargets(),
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || `迁移分析失败（HTTP ${res.status}）`);
        }
        onCompleted({ mode, data });
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  }

  const startLabel = mode === "code_review" ? "开始审查" : "开始迁移分析";
  const runningLabel =
    mode === "code_review"
      ? "审查中，请稍候（可能需要 1~2 分钟）..."
      : "迁移分析中，请稍候（可能需要 1~2 分钟）...";

  return (
    <section className="review-panel">
      <h3>分析模式</h3>
      <div className="mode-options">
        <label>
          <input
            type="radio"
            name="mode"
            value="code_review"
            checked={mode === "code_review"}
            onChange={() => setMode("code_review")}
            disabled={running}
          />
          Code Review
        </label>
        <label>
          <input
            type="radio"
            name="mode"
            value="migration"
            checked={mode === "migration"}
            onChange={() => setMode("migration")}
            disabled={running}
          />
          Migration
        </label>
      </div>

      {mode === "migration" && versions.length === 0 && (
        <p className="hint">Migration 需要已确认的当前版本：请先在版本面板中手动添加技术与版本</p>
      )}

      {mode === "migration" && versions.length > 0 && (
        <div className="migration-targets">
          <p className="hint">
            填写迁移目标版本（至少一个）；留空的技术不参与迁移；待确认且未确认的技术无法迁移
          </p>
          <table className="version-table">
            <thead>
              <tr>
                <th>技术</th>
                <th>当前版本</th>
                <th>目标版本</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => {
                const isActive = v.confirmed || v.status === "exact";
                return (
                  <tr key={v.technology}>
                    <td>
                      <code>{v.technology}</code>
                    </td>
                    <td>
                      {isActive ? (
                        <code>{v.version}</code>
                      ) : (
                        <span className="badge badge-warning">待确认</span>
                      )}
                    </td>
                    <td>
                      <select
                        value={targets[v.technology]}
                        onChange={(e) => handleTarget(v.technology, e.target.value)}
                        disabled={running || !isActive}
                      >
                        <option value="">-- 选择目标版本 --</option>
                        {(availableVersions[v.technology] || []).map((ver) => (
                          <option key={ver} value={ver}>
                            {ver}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <button
        type="button"
        onClick={handleStart}
        disabled={running || (mode === "migration" && versions.length === 0)}
      >
        {running ? runningLabel : startLabel}
      </button>
      {mode === "code_review" && versions.length === 0 && (
        <p className="hint">
          未提供任何技术版本信息：本次审查将基于安全规范与模型自身知识（不做版本敏感的官方文档检索，
          依据标注为 llm_inference）。如需版本依据，可在上方版本面板手动添加。
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}

export default ReviewPanel;
