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
 * 证据折叠块：两种模式共用（规格原则 5：无证据时明确标注）。
 */
function EvidenceBlock({ evidence, source }) {
  if (!evidence) {
    return <p className="hint">依据：LLM 推断（{source}），无官方文档证据</p>;
  }
  return (
    <details className="issue-evidence">
      <summary>证据来源：{source}</summary>
      <pre>{evidence}</pre>
    </details>
  );
}

/**
 * 单个 Review Issue 卡片（规格第 24 节）。
 */
function ReviewIssueCard({ issue }) {
  return (
    <article className={`issue issue-${issue.severity}`}>
      <header className="issue-header">
        <span className={`badge badge-${issue.severity}`}>
          {SEVERITY_LABELS[issue.severity]}
        </span>
        <span className="badge badge-category">{CATEGORY_LABELS[issue.category]}</span>
        <span className="issue-location">
          <code>{issue.file}</code>
          {issue.line != null && `:${issue.line}`}
        </span>
      </header>

      <h4>{issue.title}</h4>
      <p className="issue-desc">{issue.description}</p>
      <EvidenceBlock evidence={issue.evidence} source={issue.source} />
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
 * 单个 Migration Issue 卡片（规格第 19 节：当前行为 / 目标行为 / 原因 / 建议修改）。
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
      <EvidenceBlock evidence={issue.evidence} source={issue.source} />
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
 * - 顶部：标题 + 总览 + High/Medium/Low 统计
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

  return (
    <section className="review-result">
      <h2>{isMigration ? "迁移分析结果" : "审查结果"}</h2>
      <p className="summary">{result.summary}</p>

      <div className="severity-stats">
        <span className="badge badge-high">High {counts.high}</span>
        <span className="badge badge-medium">Medium {counts.medium}</span>
        <span className="badge badge-low">Low {counts.low}</span>
      </div>

      {issues.length === 0 ? (
        <p className="hint">
          {isMigration ? "未发现需要迁移的改动" : "未发现明显问题"}
        </p>
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
