from pathlib import Path

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    """桌面层配置：保存用户体验和调用 Rust 内核所需的轻量参数。"""

    index_roots: list[Path] = Field(default_factory=lambda: [Path.home()])
    result_limit: int = 50
    debounce_ms: int = 120
    core_binary: Path = Path("target/release/saftsearch-indexer.exe")
