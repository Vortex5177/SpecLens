"""Graph State 定义（规格第 7 节）。

原则：只保留流程真正需要的字段，不堆砌无意义 State。
"""
from typing import TypedDict


class ReviewState(TypedDict):
    """Code Review 流程状态。

    节点间数据流：
    analyze_project 填充项目信息与代码上下文
        -> review 产出结构化问题列表
        -> generate_result 生成 Fix Prompt 与最终结果
    """

    # 输入
    project_id: str
    project_path: str
    mode: str

    # analyze_project 填充
    languages: dict[str, int]
    # technology -> 用户已确认版本（检索的唯一版本依据）
    confirmed_versions: dict[str, str]
    file_tree: list[str]
    selected_files: list[str]
    # 相对路径 -> 文件内容（截断后）
    code_context: dict[str, str]

    # review 填充
    summary: str
    issues: list[dict]

    # generate_result 填充
    project_fix_prompt: str

    # 任一节点可写入错误，流程提前终止
    error: str
