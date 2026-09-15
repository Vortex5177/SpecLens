"""节点 3：generate_result（确定性，无 LLM）。

职责：
- 为每个已确认 Issue 生成单问题 Fix Prompt（规格第 22 节）
- 生成项目级 Fix Prompt（规格第 23 节 / 第 19 节）
- 把 fix_prompt 回填进运行报告

Fix Prompt 用模板确定性生成而不是再调一次 LLM：
规格要求的字段（技术栈/版本/问题/依据/要求/限制）全部是已知信息，
模板更简单、可预测、不花钱。

V2：只处理已确认问题（unresolved 候选一律不进模板）；
证据标注区分 document_supported 与无官方文档证据的推断。
"""
from app.graph.state import ReviewState

_ISSUE_FIX_TEMPLATE = """\
请修改 {file}。

项目技术栈与已确认版本：
{versions}

问题：{title}
{description}

依据：
来源：{source}
{evidence}

建议：
{suggestion}

要求：
1. {version_rule}
2. 不修改与该问题无关的代码
3. 不升级依赖
4. 不改变现有公开 API，除非问题明确要求
5. 修改完成后解释修改内容
"""

_PROJECT_FIX_TEMPLATE = """\
请对以下项目进行 Code Review 问题修复。

项目技术栈与已确认版本：
{versions}

共发现 {count} 个问题：

{issues}

统一修改要求：
1. {version_rule}
2. 逐个修复以上问题，不引入与问题无关的改动
3. 不升级依赖
4. 不改变现有公开 API，除非问题明确要求
5. 全部修复完成后，按问题编号逐一解释修改内容
"""

# ===== Migration Fix Prompt 模板（规格第 19 节）=====
_MIGRATION_ISSUE_FIX_TEMPLATE = """\
请修改 {file}，完成 {technology} {current_version} -> {target_version} 的迁移。

迁移问题：{title}

当前行为：{current_behavior}
目标行为：{target_behavior}
原因：{reason}

依据：
来源：{source}
{evidence}

建议的修改：
{suggested_change}

要求：
1. 修改后代码必须完全符合 {technology} {target_version} 的规范
2. 不修改与该迁移点无关的代码
3. 保持现有业务逻辑不变，只调整受版本变化影响的部分
4. 修改完成后解释修改内容与对应的版本变化
"""

_MIGRATION_PROJECT_FIX_TEMPLATE = """\
请对以下项目进行版本迁移改造。

迁移目标：
{migrations}

项目其余技术栈保持不变：
{versions}

共发现 {count} 个迁移点：

{issues}

统一修改要求：
1. 逐个完成以上迁移点的代码调整，不引入与迁移无关的改动
2. 保持现有业务逻辑不变，只调整受版本变化影响的部分
3. 同步更新依赖声明文件中的版本号（如 requirements.txt / package.json）
4. 全部修改完成后，按迁移点编号逐一解释修改内容与对应的版本变化
"""


def _format_versions(confirmed_versions: dict[str, str]) -> str:
    # Code Review 允许无版本信息（降级审查）：模板中给出占位说明而非空段落
    return "\n".join(f"- {tech} {version}" for tech, version in confirmed_versions.items()) \
        or "（未提供版本信息，修复时基于通用最佳实践即可，不要擅自升级依赖）"


def _evidence_text(issue: dict) -> str:
    """证据段落：document_supported 引用证据原文，其余明确标注为推断。"""
    if issue.get("evidence_status") == "document_supported":
        return issue.get("evidence") or "（证据正文缺失）"
    return "（无官方文档证据，基于 LLM 推理；请人工复核后再采纳）"


def generate_result(state: ReviewState) -> dict:
    """按模式分发：review 生成审查 Fix Prompt，migration 生成迁移 Fix Prompt。

    结果回填 state["report"]["issues"]（每条带 fix_prompt）与 project_fix_prompt。
    """
    report = state["report"]
    scope = state["run_scope"]
    confirmed_versions = scope["confirmed_versions"]
    if report["mode"] == "migration":
        project_fix_prompt = _generate_migration_result(
            report, confirmed_versions, scope["target_versions"]
        )
    else:
        project_fix_prompt = _generate_review_result(report, confirmed_versions)
    return {"report": report, "project_fix_prompt": project_fix_prompt}


def _generate_review_result(report: dict, confirmed_versions: dict[str, str]) -> str:
    versions_text = _format_versions(confirmed_versions)
    version_rule = (
        "严格遵循上述已确认的技术版本"
        if confirmed_versions
        else "基于通用最佳实践修改，不要擅自升级依赖"
    )

    issues = []
    issue_lines = []
    for index, issue in enumerate(report["issues"], start=1):
        fix_prompt = _ISSUE_FIX_TEMPLATE.format(
            file=issue["file"],
            versions=versions_text,
            title=issue["title"],
            description=issue["description"],
            source=issue["source"],
            evidence=_evidence_text(issue),
            suggestion=issue["suggestion"],
            version_rule=version_rule,
        )
        issue["fix_prompt"] = fix_prompt
        issues.append(issue)
        issue_lines.append(
            f"{index}. [{issue['category']}/{issue['severity']}] {issue['file']}"
            f"{'#' + str(issue['line']) if issue.get('line') else ''}"
            f"：{issue['title']}\n   建议：{issue['suggestion']}"
        )

    report["issues"] = issues
    return _PROJECT_FIX_TEMPLATE.format(
        versions=versions_text,
        count=len(issues),
        issues="\n\n".join(issue_lines) if issue_lines else "（未发现问题）",
        version_rule=version_rule,
    )


def _generate_migration_result(
    report: dict, confirmed_versions: dict[str, str], targets: dict[str, str]
) -> str:
    """Migration 模式：为每个迁移点生成 Fix Prompt 与项目级迁移提示。"""
    # 未参与迁移的技术保持原版本列出（提醒不要动它们）；
    # 全部技术都迁移时直接写（无），避免套用无版本降级占位文案
    unchanged = {tech: v for tech, v in confirmed_versions.items() if tech not in targets}
    unchanged_text = _format_versions(unchanged) if unchanged else "（无）"
    migrations_text = "\n".join(
        f"- {tech} {confirmed_versions[tech]} -> {target}" for tech, target in targets.items()
    )

    issues = []
    issue_lines = []
    for index, issue in enumerate(report["issues"], start=1):
        fix_prompt = _MIGRATION_ISSUE_FIX_TEMPLATE.format(
            file=issue["file"],
            technology=issue["technology"],
            current_version=issue["current_version"],
            target_version=issue["target_version"],
            title=issue["title"],
            current_behavior=issue["current_behavior"],
            target_behavior=issue["target_behavior"],
            reason=issue["reason"],
            source=issue["source"],
            evidence=_evidence_text(issue),
            suggested_change=issue["suggested_change"],
        )
        issue["fix_prompt"] = fix_prompt
        issues.append(issue)
        issue_lines.append(
            f"{index}. [{issue['severity']}] {issue['technology']} "
            f"{issue['file']}{'#' + str(issue['line']) if issue.get('line') else ''}"
            f"：{issue['title']}\n   建议：{issue['suggested_change']}"
        )

    report["issues"] = issues
    return _MIGRATION_PROJECT_FIX_TEMPLATE.format(
        migrations=migrations_text,
        versions=unchanged_text,
        count=len(issues),
        issues="\n\n".join(issue_lines) if issue_lines else "（无需迁移改动）",
    )
