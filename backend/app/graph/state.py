"""Graph State 定义（规格第 7 节）。

原则：只保留流程真正需要的字段，不堆砌无意义 State。
"""
from typing import TypedDict


class ReviewState(TypedDict):
    """Code Review / Migration 流程状态。

    节点间数据流：
    analyze_project 填充运行范围（RunScope，含代码快照与覆盖信息）
        -> review 执行发现-核实管线，产出完整运行报告（RunReport）
        -> generate_result 为已确认问题生成 Fix Prompt（回填报告与项目级提示）
    """

    # 输入
    project_id: str
    project_path: str
    mode: str
    # Migration 专用：technology -> 目标版本；code_review 时为空 dict
    target_versions: dict[str, str]

    # analyze_project 填充：本次运行的完整范围与预算
    # {run_id, mode, confirmed_versions, pending_versions, target_versions,
    #  code_context, snapshot, coverage, budget}
    run_scope: dict

    # review 填充：完整运行报告（含 issues / counts / unresolved / errors）
    report: dict

    # generate_result 填充：项目级 Fix Prompt（回填进报告 issues 内的 fix_prompt）
    project_fix_prompt: str

    # 任一节点可写入错误，流程提前终止
    error: str
