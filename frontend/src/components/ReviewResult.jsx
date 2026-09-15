import { useState } from "react";

// 分类与严重级别的中文映射（规格第 18 / 20 节）
const CATEGORY_LABELS = { api: "API 合规", security: "安全", robustness: "健壮性" };
const SEVERITY_LABELS = { high: "High", medium: "Medium", low: "Low" };

/**
 * 复制文本到剪贴板（兼容非 HTTPS 环境的降级方案）。
 */
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

/**
 * 复制按钮：成功后短暂显示「已复制」反馈。
 */
function CopyButton({ text, label }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await copyText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 复制失败静默处理，避免打断用户
    }
  }

  return (
    <button type="button" className="copy-btn" onClick={handleCopy} disabled={!text}>
      {copied ? "已复制 ✓" : label}
    </button>
  );
}

/**
 * 发现来源徽标（V2）：origin 记录候选来自代码初筛 / 文档定位。
 */
function OriginBadges({ origin }) {
  const list = origin || [];
  if (list.length === 0) return null;
  const hasCode = list.includes("code");
  const hasDoc = list.includes("document");
  const label = hasCode && hasDoc ? "代码+文档" : hasCode ? "代码发现" : "文档发现";
  return <span className="badge badge-category">{label}</span>;
}

/**
 * 证据折叠块（V2）：优先展示结构化 evidences（含证据 ID 与来源），无证据时
 * 明确标注为 LLM 推断；兼容旧 JSON 的单条 evidence 字符串。
 */
function EvidenceBlock({ issue }) {
  const evs = issue.evidences || [];
  if (evs.length > 0) {
    return (
      <details className="issue-evidence">
        <summary>
          证据来源：{evs.length} 条
          {issue.evidence_status === "document_supported" ? "（文档支持）" : "（参考）"}
        </summary>
        {evs.map((e, i) => (
          <div key={i}>
            <p className="hint">
              [{e.evidence_id}] {e.source}
              {e.version ? ` | 版本 ${e.version}` : ""}
              {e.retrieval_score != null ? ` | 相似度 ${e.retrieval_score}` : ""}
            </p>
            <pre>{e.content}</pre>
          </div>
        ))}
      </details>
    );
  }
  if (issue.evidence) {
    // 旧版结果兼容：单条证据字符串
    return (
      <details className="issue-evidence">
        <summary>证据来源：{issue.source}</summary>
        <pre>{issue.evidence}</pre>
      </details>
    );
  }
  const status = issue.evidence_status || "inferred";
  const label = { document_supported: "文档支持", inferred: "LLM 推断", none: "无证据" }[status];
  return <p className="hint">依据：{label}（{issue.source || "llm_inference"}），无官方文档证据</p>;
}

/**
 * 运行状态与覆盖信息（V2）：status / counts / coverage / unresolved / errors。
 * 旧版结果（无 status 字段）不展示这些块。
 */
function RunStatusBlock({ result, isMigration }) {
  const status = result.status;
  if (!status) return null; // legacy
  const statusLabel = {
    complete: { text: "完整分析", cls: "badge-ok" },
    partial: { text: "部分完成（存在覆盖缺口）", cls: "badge-warning" },
    failed: { text: "分析失败", cls: "badge-high" },
  }[status] || { text: status, cls: "badge-warning" };
  const counts = result.counts || {};
  const coverage = result.coverage || {};

  return (
    <>
      <div className="severity-stats">
        <span className={`badge ${statusLabel.cls}`}>{statusLabel.text}</span>
        {counts.candidates_total != null && (
          <>
            <span className="badge badge-category">候选 {counts.candidates_total}</span>
            <span className="badge badge-category">核实 {counts.verified ?? 0}</span>
            <span className="badge badge-ok">确认 {counts.confirmed ?? 0}</span>
            <span className="badge badge-category">排除 {counts.rejected ?? 0}</span>
            <span className="badge badge-warning">未决 {counts.insufficient ?? 0}</span>
            <span className="badge badge-high">失败 {counts.failed ?? 0}</span>
            {counts.not_scheduled > 0 && (
              <span className="badge badge-warning">未调度 {counts.not_scheduled}</span>
            )}
          </>
        )}
      </div>

      {status === "partial" && (
        <p className="hint">
          本次运行存在缺口（截断 / 省略文件 / 待确认版本 / 检索缺失等），未发现问题不代表范围内无问题。
        </p>
      )}
      {status === "failed" && (
        <p className="hint">发现阶段失败，未产生有效分析，详见下方错误。</p>
      )}

      {(result.unresolved || []).length > 0 && (
        <details className="run-extra">
          <summary>未决 / 待人工核实（{result.unresolved.length}）</summary>
          <ul>
            {result.unresolved.map((u, i) => (
              <li key={i}>
                <code>{u.file}</code>
                {u.line != null && `:${u.line}`}
                {u.technology ? ` [${u.technology}]` : ""}：{u.description || "(无描述)"}
                {u.note && <span className="hint"> — {u.note}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}

      {(result.errors || []).length > 0 && (
        <details className="run-extra">
          <summary>运行错误（{result.errors.length}）</summary>
          <ul>
            {result.errors.map((e, i) => (
              <li key={i}>
                <span className="badge badge-high">{e.type}</span>{" "}
                {e.file || e.path || ""}：{e.message}
              </li>
            ))}
          </ul>
        </details>
      )}

      {(coverage.truncated_files || []).length +
        (coverage.omitted_files || []).length +
        (coverage.pending_versions || []).length >
        0 && (
        <details className="run-extra">
          <summary>覆盖详情</summary>
          <ul>
            {(coverage.truncated_files || []).length > 0 && (
              <li>截断文件：{coverage.truncated_files.join("、")}</li>
            )}
            {(coverage.omitted_files || []).length > 0 && (
              <li>未纳入快照的源码文件：{coverage.omitted_files.join("、")}</li>
            )}
            {(coverage.pending_versions || []).length > 0 && (
              <li>待确认版本（未参与版本敏感检索）：{coverage.pending_versions.join("、")}</li>
            )}
            {isMigration && coverage.doc_direction?.partitions && (
              <li>
                文档方向分区：
                {coverage.doc_direction.partitions
                  .map(
                    (p) =>
                      `${p.technology} ${p.version}（读取 ${p.read} 块${p.empty ? "，区间无 What's New 文档" : ""}）`
                  )
                  .join("；")}
              </li>
            )}
          </ul>
        </details>
      )}
    </>
  );
}

/**
 * 单个 Review Issue 卡片（规格第 24 节 + V2 状态徽标）。
 */
function ReviewIssueCard({ issue }) {
  return (
    <article className={`issue issue-${issue.severity}`}>
      <header className="issue-header">
        <span className={`badge badge-${issue.severity}`}>
          {SEVERITY_LABELS[issue.severity]}
        </span>
        <span className="badge badge-category">{CATEGORY_LABELS[issue.category]}</span>
        <OriginBadges origin={issue.origin} />
        <span className="issue-location">
          <code>{issue.file}</code>
          {issue.line != null && `:${issue.line}`}
        </span>
      </header>

      <h4>{issue.title}</h4>
      <p className="issue-desc">{issue.description}</p>
      <EvidenceBlock issue={issue} />
      <p className="issue-suggestion">
        <strong>建议：</strong>
        {issue.suggestion}
      </p>

      <div className="issue-actions">
        <CopyButton text={issue.fix_prompt} label="Copy Fix Prompt" />
        <span className="hint">置信度：{issue.confidence}</span>
      </div>
    </article>
  );
}

/**
 * 单个 Migration Issue 卡片（规格第 19 节 + V2 置信度/来源徽标）。
 */
function MigrationIssueCard({ issue }) {
  return (
    <article className={`issue issue-${issue.severity}`}>
      <header className="issue-header">
        <span className={`badge badge-${issue.severity}`}>
          {SEVERITY_LABELS[issue.severity]}
        </span>
        <span className="badge badge-category">
          {issue.technology} {issue.current_version} → {issue.target_version}
        </span>
        <span className="badge badge-category">置信度 {issue.confidence}</span>
        <OriginBadges origin={issue.origin} />
        <span className="issue-location">
          <code>{issue.file}</code>
          {issue.line != null && `:${issue.line}`}
        </span>
      </header>

      <h4>{issue.title}</h4>
      <div className="behavior-pair">
        <div className="behavior behavior-current">
          <strong>当前行为</strong>
          <p>{issue.current_behavior}</p>
        </div>
        <div className="behavior behavior-target">
          <strong>目标行为</strong>
          <p>{issue.target_behavior}</p>
        </div>
      </div>
      <p className="issue-desc">
        <strong>原因：</strong>
        {issue.reason}
      </p>
      <EvidenceBlock issue={issue} />
      <p className="issue-suggestion">
        <strong>建议修改：</strong>
        {issue.suggested_change}
      </p>

      <div className="issue-actions">
        <CopyButton text={issue.fix_prompt} label="Copy Fix Prompt" />
      </div>
    </article>
  );
}

/**
 * 结果展示：按 mode 分发 Review / Migration 两种卡片布局。
 * - 顶部：标题 + 摘要 + 运行状态（V2）+ High/Medium/Low 统计
 * - 底部：Copy Project Fix Prompt
 */
function ReviewResult({ mode, data }) {
  const isMigration = mode === "migration";
  const { result, project_fix_prompt } = data;
  const issues = result.issues || [];

  // 按严重级别统计数量
  const counts = { high: 0, medium: 0, low: 0 };
  for (const issue of issues) {
    counts[issue.severity] += 1;
  }

  const emptyText =
    result.status === "complete"
      ? isMigration
        ? "已审查范围内未发现需要迁移的改动"
        : "已审查范围内未发现问题"
      : isMigration
        ? "本次运行未产生迁移问题（存在缺口时请查看上方覆盖详情与错误）"
        : "本次运行未产生问题（存在缺口时请查看上方覆盖详情与错误）";

  return (
    <section className="review-result">
      <h2>{isMigration ? "迁移分析结果" : "审查结果"}</h2>
      <p className="summary">{result.summary}</p>

      <RunStatusBlock result={result} isMigration={isMigration} />

      <div className="severity-stats">
        <span className="badge badge-high">High {counts.high}</span>
        <span className="badge badge-medium">Medium {counts.medium}</span>
        <span className="badge badge-low">Low {counts.low}</span>
      </div>

      {issues.length === 0 ? (
        <p className="hint">{emptyText}</p>
      ) : (
        issues.map((issue, index) =>
          isMigration ? (
            <MigrationIssueCard key={index} issue={issue} />
          ) : (
            <ReviewIssueCard key={index} issue={issue} />
          )
        )
      )}

      {project_fix_prompt && (
        <div className="project-fix">
          <CopyButton text={project_fix_prompt} label="Copy Project Fix Prompt" />
          <p className="hint">
            {isMigration
              ? "包含全部迁移点的一次性改造提示，可直接粘贴给 AI Coding 工具"
              : "包含全部问题的一次性修复提示，可直接粘贴给 AI Coding 工具"}
          </p>
        </div>
      )}
    </section>
  );
}

export default ReviewResult;
