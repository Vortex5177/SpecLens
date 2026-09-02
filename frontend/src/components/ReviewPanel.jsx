import { useEffect, useState } from "react";

/**
 * 审查模式选择与触发面板（规格第 24 节：Review Mode）。
 * - code_review：直接开始审查
 * - migration：为每个已确认技术填写目标版本（至少填一个），
 *   后端对比当前版本与目标版本的规范（规格第 19 节）
 * - 两种模式都是同步接口，耗时可能超过 1 分钟，期间禁用按钮
 */
function ReviewPanel({ projectId, versions, reviewEnabled, onCompleted }) {
  const [mode, setMode] = useState("code_review");
  // migration 目标版本输入：technology -> 用户输入
  const [targets, setTargets] = useState(() =>
    Object.fromEntries(versions.map((v) => [v.technology, ""]))
  );
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

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

  function handleTarget(technology, value) {
    setTargets((prev) => ({ ...prev, [technology]: value }));
  }

  // migration 必须至少填写一个目标版本
  function selectedTargets() {
    return versions
      .filter((v) => (targets[v.technology] || "").trim() !== "")
      .map((v) => ({
        technology: v.technology,
        version: (targets[v.technology] || "").trim(),
      }));
  }

  async function handleStart() {
    if (mode === "migration" && selectedTargets().length === 0) {
      setError("请至少为一个技术填写迁移目标版本（如 0.120）");
      return;
    }
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
        // POST 响应不含 project_fix_prompt，补一次 GET 拿完整结果
        const full = await fetch(`/api/reviews/${data.review_id}`);
        if (!full.ok) {
          throw new Error("审查完成，但获取完整结果失败");
        }
        onCompleted({ mode, data: await full.json() });
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
        // migration 响应直接包含 project_fix_prompt，无需二次请求
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
      <h3>审查模式</h3>
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
            填写迁移目标版本（至少一个）；留空的技术不参与迁移
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
              {versions.map((v) => (
                <tr key={v.technology}>
                  <td>
                    <code>{v.technology}</code>
                  </td>
                  <td>
                    <code>{v.version}</code>
                  </td>
                  <td>
                    <input
                      type="text"
                      placeholder="如 0.120"
                      value={targets[v.technology]}
                      onChange={(e) => handleTarget(v.technology, e.target.value)}
                      disabled={running}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <button
        type="button"
        onClick={handleStart}
        disabled={running || !reviewEnabled || (mode === "migration" && versions.length === 0)}
      >
        {running ? runningLabel : startLabel}
      </button>
      {mode === "code_review" && versions.length === 0 && (
        <p className="hint">
          未提供任何技术版本信息：本次审查将基于安全规范与模型自身知识（不做版本敏感的官方文档检索，
          依据标注为 llm_inference）。如需版本依据，可在上方版本面板手动添加。
        </p>
      )}
      {!reviewEnabled && (
        <p className="hint">请先确认所有待确认的技术版本</p>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}

export default ReviewPanel;
