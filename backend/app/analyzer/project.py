"""项目结构分析器（纯 Python 实现，不依赖 LLM）。

职责：遍历已解压的项目目录，产出文件树、语言统计、依赖文件清单。
版本识别属于 Phase 3，将新增 analyzer/dependencies.py 与 versions.py。
"""
from pathlib import Path

from app.config import DEPENDENCY_FILES, IGNORED_DIRS, MAX_TREE_ENTRIES

# 扩展名 -> 语言（规格第 8 节：识别主要语言）
EXTENSION_LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
}


def analyze_project(project_root: Path, project_id: str) -> dict:
    """分析项目目录，返回与 ProjectAnalysis 模型一致的 dict。"""
    files = _collect_files(project_root)

    languages: dict[str, int] = {}
    dependency_files: list[str] = []

    for rel_path in files:
        suffix = Path(rel_path).suffix.lower()
        language = EXTENSION_LANGUAGES.get(suffix)
        if language:
            languages[language] = languages.get(language, 0) + 1

        if Path(rel_path).name in DEPENDENCY_FILES:
            dependency_files.append(rel_path)

    truncated = len(files) > MAX_TREE_ENTRIES
    return {
        "project_id": project_id,
        "file_count": len(files),
        "languages": dict(sorted(languages.items(), key=lambda item: -item[1])),
        "dependency_files": sorted(dependency_files),
        "file_tree": files[:MAX_TREE_ENTRIES],
        "tree_truncated": truncated,
    }


def _collect_files(project_root: Path) -> list[str]:
    """递归收集相对路径列表，跳过忽略目录，结果按路径排序。"""
    files: list[str] = []

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                if entry.name not in IGNORED_DIRS:
                    walk(entry)
            elif entry.is_file():
                files.append(entry.relative_to(project_root).as_posix())

    walk(project_root)
    return files
