"""压缩包安全解压服务。

规格第 26 节安全要求：
1. 防路径穿越（zip slip）：拒绝绝对路径与 .. 组件，提取前校验解析后路径仍在目标目录内
2. 防 zip 炸弹：限制解压后总大小与文件数量
3. 跳过忽略目录与敏感文件（.env 等）
"""
import zipfile
from pathlib import Path

from app.config import (
    IGNORED_DIRS,
    MAX_FILE_COUNT,
    MAX_SINGLE_FILE,
    MAX_UNCOMPRESSED_SIZE,
    SENSITIVE_FILE_PREFIXES,
)


class UploadError(Exception):
    """上传处理错误，由路由层转换为友好 HTTP 响应。"""


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> int:
    """安全解压 zip 到 dest_dir，返回提取的文件数量。"""
    dest_dir = dest_dir.resolve()
    file_count = 0
    total_size = 0

    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue

                rel_path = _validate_member_name(member.filename)
                if _should_skip(rel_path):
                    continue

                # zip 炸弹防护：累计解压大小与文件数
                total_size += member.file_size
                if total_size > MAX_UNCOMPRESSED_SIZE:
                    raise UploadError("解压后总大小超出限制，已拒绝该压缩包")
                if member.file_size > MAX_SINGLE_FILE:
                    raise UploadError(f"文件 {rel_path} 超过单文件大小限制")
                file_count += 1
                if file_count > MAX_FILE_COUNT:
                    raise UploadError("文件数量超出限制，已拒绝该压缩包")

                target = (dest_dir / rel_path).resolve()
                # 双重保险：即使成员名合法，也确认最终路径没有逃出目标目录
                if not target.is_relative_to(dest_dir):
                    raise UploadError("检测到非法路径，已拒绝该压缩包")

                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    dst.write(src.read())
    except zipfile.BadZipFile:
        raise UploadError("无效的 zip 文件")

    if file_count == 0:
        raise UploadError("压缩包中没有可分析的文件")
    return file_count


def _validate_member_name(filename: str) -> str:
    """校验并规范化成员路径，非法则抛错。"""
    name = filename.replace("\\", "/").lstrip("/")
    parts = name.split("/")
    if ".." in parts:
        raise UploadError("检测到路径穿越攻击（..），已拒绝该压缩包")
    return name


def _should_skip(rel_path: str) -> bool:
    """判断是否跳过：忽略目录下的文件与敏感文件。"""
    parts = Path(rel_path).parts
    if any(part in IGNORED_DIRS for part in parts[:-1]):
        return True
    filename = parts[-1]
    return filename.startswith(SENSITIVE_FILE_PREFIXES)
