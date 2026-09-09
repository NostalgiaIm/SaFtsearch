from pathlib import Path
import sys

from pydantic import BaseModel, Field


def project_root() -> Path:
    """返回 SaFtsearch 项目根目录。

    ``config.py`` 位于 ``python-app/src/saftsearch_app``。使用文件自身
    的绝对路径推导项目根目录，可以让用户从仓库根目录、Python 源码目录
    或 IDE 中启动应用时，都能找到同一个 Rust 可执行文件。
    """

    return Path(__file__).resolve().parents[3]


class AppConfig(BaseModel):
    """桌面层配置。

    这里保存的是 Python UI 自己关心的参数，以及启动 Rust 内核所需的
    最小配置。搜索算法和文件扫描规则仍然属于 Rust 层，避免 UI 层
    逐渐变成第二个搜索引擎。
    """

    index_roots: list[Path] = Field(default_factory=lambda: [Path.home()])
    exclude_patterns: list[str] = Field(
        default_factory=lambda: ["target", ".git", "node_modules", "__pycache__"]
    )
    result_limit: int = 50
    debounce_ms: int = 120
    follow_symlinks: bool = False
    # 开发阶段优先使用 debug 构建，方便修改 Rust 后马上验证。
    # 使用绝对路径是为了避免启动目录不同导致“找不到 Rust 内核”。
    core_binary: Path = Field(
        default_factory=lambda: (
            project_root()
            / "target"
            / "debug"
            / ("saftsearch-indexer.exe" if sys.platform == "win32" else "saftsearch-indexer")
        )
    )
