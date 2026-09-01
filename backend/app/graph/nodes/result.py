"""节点 3：generate_result（确定性，无 LLM，Phase 10）。

职责：
- 为每个 Issue 生成单问题 Fix Prompt（规格第 22 节）
- 生成项目级 Fix Prompt（规格第 23 节）

Fix Prompt 用模板确定性生成而不是再调一次 LLM：
规格要求的字段（技术栈/版本/问题/依据/要求/限制）全部是已知信息，
模板更简单、可预测、不花钱。
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
1. 严格遵循上述已确认的技术版本
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
1. 严格遵循上述已确认的技术版本
2. 逐个修复以上问题，不引入与问题无关的改动
3. 不升级依赖
4. 不改变现有公开 API，除非问题明确要求
5. 全部修复完成后，按问题编号逐一解释修改内容
"""


def _format_versions(confirmed_versions: dict[str, str]) -> str:
    return "\n".join(f"- {tech} {version}" for tech, version in confirmed_versions.items())


def generate_result(state: ReviewState) -> dict:
    versions_text = _format_versions(state["confirmed_versions"])

    issues = []
    issue_lines = []
    for index, issue in enumerate(state["issues"], start=1):
        fix_prompt = _ISSUE_FIX_TEMPLATE.format(
            file=issue["file"],
            versions=versions_text,
            title=issue["title"],
            description=issue["description"],
            source=issue["source"],
            evidence=issue["evidence"] or "（无知识库证据，基于 LLM 推理）",
            suggestion=issue["suggestion"],
        )
        issues.append({**issue, "fix_prompt": fix_prompt})
        issue_lines.append(
            f"{index}. [{issue['category']}/{issue['severity']}] {issue['file']}"
            f"{'#' + str(issue['line']) if issue.get('line') else ''}"
            f"：{issue['title']}\n   建议：{issue['suggestion']}"
        )

    project_fix_prompt = _PROJECT_FIX_TEMPLATE.format(
        versions=versions_text,
        count=len(issues),
        issues="\n\n".join(issue_lines) if issue_lines else "（未发现问题）",
    )

    return {"issues": issues, "project_fix_prompt": project_fix_prompt}
