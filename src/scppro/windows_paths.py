"""Checks for remote names before creating files on Windows."""
from __future__ import annotations

from pathlib import Path


_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_RESERVED.update(f"COM{number}" for number in range(1, 10))
_RESERVED.update(f"LPT{number}" for number in range(1, 10))
_RESERVED.update(f"COM{number}" for number in "¹²³")
_RESERVED.update(f"LPT{number}" for number in "¹²³")


def invalid_windows_name(name: str) -> str | None:
    if not name or name in (".", ".."):
        return "文件名为空或为保留的目录名称"
    if any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name):
        return "文件名含 Windows 不允许的字符"
    if name.endswith((" ", ".")):
        return "文件名不能以空格或句点结尾"
    if name.split(".", 1)[0].upper() in _RESERVED:
        return "文件名是 Windows 保留名称"
    return None


def case_collision(path: Path) -> bool:
    """True when a different spelling already occupies this Windows name."""
    if not path.parent.is_dir():
        return False
    return any(child.name.casefold() == path.name.casefold() and child.name != path.name
               for child in path.parent.iterdir())
