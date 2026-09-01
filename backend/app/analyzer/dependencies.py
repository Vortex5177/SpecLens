"""依赖与版本识别（规格第 8、9 节）。

核心原则：
1. 版本只从依赖文件读取，绝不根据代码猜测
2. 精确锁定（==、锁文件）→ status = exact
3. 范围约束（>=、^、~）或未指定 → status = needs_confirmation，version 留空，等用户确认

支持的依赖文件：
- requirements.txt / pyproject.toml（PEP 508）
- package.json / package-lock.json
- poetry.lock / uv.lock（TOML 锁文件）

说明：yarn.lock / pnpm-lock.yaml 在 Phase 2 会被检测到，但 V1 不解析其内容。
"""
import json
import re
import tomllib
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

# npm 风格的精确版本（无任何范围符号），如 18.2.0、1.2.3-rc.1
_NPM_EXACT_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")


def detect_versions(project_root: Path, dependency_files: list[str]) -> list[dict]:
    """解析所有依赖文件，合并出每个技术的版本识别结果。

    返回与 DetectedVersion 模型一致的 dict 列表（按技术名排序）。
    """
    exact: dict[str, tuple[str, str]] = {}    # 技术 -> (精确版本, 来源文件)
    ranged: dict[str, tuple[str, str]] = {}   # 技术 -> (原始声明, 来源文件)

    for rel_path in dependency_files:
        parser = _PARSERS.get(Path(rel_path).name)
        if parser is None:
            continue
        path = project_root / rel_path
        try:
            entries = parser(path)
        except (OSError, ValueError, tomllib.TOMLDecodeError, json.JSONDecodeError, KeyError):
            # 单个文件解析失败不影响其他文件（规格第 27 节错误处理）
            continue

        for tech, raw_spec, version in entries:
            tech = tech.lower()
            if version:
                exact.setdefault(tech, (version, rel_path))
            else:
                ranged.setdefault(tech, (raw_spec, rel_path))

    results: list[dict] = []
    for tech in sorted(set(exact) | set(ranged)):
        if tech in exact:
            version, source = exact[tech]
            results.append({
                "technology": tech,
                "raw_spec": version,
                "version": version,
                "status": "exact",
                "confirmed": False,
                "source_file": source,
            })
        else:
            raw_spec, source = ranged[tech]
            results.append({
                "technology": tech,
                "raw_spec": raw_spec,
                "version": None,
                "status": "needs_confirmation",
                "confirmed": False,
                "source_file": source,
            })
    return results


# ===== 各依赖文件解析器：统一返回 [(技术名, 原始声明, 精确版本或 None)] =====

def _parse_requirements_txt(path: Path) -> list[tuple[str, str, str | None]]:
    """解析 pip requirements 文件（逐行，忽略注释与选项）。"""
    entries = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        # 跳过空行、选项（-r、--index-url 等）与本地路径依赖
        if not line or line.startswith("-") or line.startswith("."):
            continue
        entries.append(_pep508_entry(line))
    return entries


def _pep508_entry(spec: str) -> tuple[str, str, str | None]:
    """用 packaging 库解析一条 PEP 508 依赖声明。"""
    try:
        req = Requirement(spec)
    except InvalidRequirement:
        # 无法解析时保留原文，交由用户确认
        return (spec, spec, None)
    if req.url:
        # 直接 URL 依赖，无法确定版本
        return (req.name, spec, None)

    version = None
    specs = list(req.specifier)
    # 唯一的 == 约束且不包含通配符才算精确锁定
    if len(specs) == 1 and specs[0].operator == "==" and "*" not in specs[0].version:
        version = specs[0].version
    return (req.name, str(req.specifier) or "*", version)


def _parse_pyproject(path: Path) -> list[tuple[str, str, str | None]]:
    """解析 pyproject.toml：project.dependencies 与 Poetry 依赖。"""
    data = tomllib.loads(path.read_text(encoding="utf-8", errors="ignore"))
    entries = []

    for dep in data.get("project", {}).get("dependencies", []):
        if isinstance(dep, str):
            entries.append(_pep508_entry(dep))

    poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name, constraint in poetry_deps.items():
        if name.lower() == "python":
            continue
        if isinstance(constraint, dict):
            constraint = constraint.get("version", "")
        if not isinstance(constraint, str) or not constraint:
            continue
        version = constraint if _NPM_EXACT_RE.match(constraint) else None
        entries.append((name, constraint, version))
    return entries


def _parse_package_json(path: Path) -> list[tuple[str, str, str | None]]:
    """解析 package.json 的 dependencies 与 devDependencies。"""
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    entries = []
    for section in ("dependencies", "devDependencies"):
        for name, spec in data.get(section, {}).items():
            if not isinstance(spec, str):
                continue
            version = spec if _NPM_EXACT_RE.match(spec) else None
            entries.append((name, spec, version))
    return entries


def _parse_package_lock(path: Path) -> list[tuple[str, str, str | None]]:
    """解析 package-lock.json（v2/v3 的 packages 字段，v1 回退到 dependencies）。"""
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    entries = []

    packages = data.get("packages", {})
    for key, info in packages.items():
        if not key:  # 根节点
            continue
        name = key.split("node_modules/")[-1]
        version = info.get("version") if isinstance(info, dict) else None
        if name and version:
            entries.append((name, version, version))

    if not entries:  # v1 锁文件的嵌套结构
        def walk(node: dict) -> None:
            for name, info in (node.get("dependencies") or {}).items():
                version = info.get("version") if isinstance(info, dict) else None
                if version:
                    entries.append((name, version, version))
                    walk(info)
        walk(data)
    return entries


def _parse_toml_lock(path: Path) -> list[tuple[str, str, str | None]]:
    """解析 poetry.lock / uv.lock（均为 [[package]] 结构）。"""
    data = tomllib.loads(path.read_text(encoding="utf-8", errors="ignore"))
    entries = []
    for package in data.get("package", []):
        name, version = package.get("name"), package.get("version")
        if name and version:
            entries.append((name, version, version))
    return entries


_PARSERS = {
    "requirements.txt": _parse_requirements_txt,
    "pyproject.toml": _parse_pyproject,
    "package.json": _parse_package_json,
    "package-lock.json": _parse_package_lock,
    "poetry.lock": _parse_toml_lock,
    "uv.lock": _parse_toml_lock,
}
