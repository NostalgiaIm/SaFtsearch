"""桌面层搜索服务。

SearchService 位于 UI 和 CoreClient 之间：

    MainWindow -> SearchService -> CoreClient -> Rust CLI

窗口只需要提交查询，不需要知道命令行参数、JSON 字段或子进程错误。
后续如果增加索引缓存、搜索历史或多根目录，这里会是主要的业务编排位置。
"""

from __future__ import annotations

from pathlib import Path
from threading import Event

from saftsearch_app.config import AppConfig
from saftsearch_app.core_client import CoreClient, IndexStatus, SearchResult


class SearchService:
    """组合桌面配置与 Rust 客户端，提供 UI 需要的搜索接口。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.client = CoreClient(config.core_binary)

    def search(
        self,
        query: str,
        root: Path | None = None,
        cancel_event: Event | None = None,
    ) -> list[SearchResult]:
        """搜索指定目录；未显式传入时使用第一个配置根目录。

        UI 会在任务启动前固定 ``root``，避免用户切换目录后，正在运行的
        worker 读取到已经变化的配置。
        """

        root = self._validated_root(root) if root is not None else self._first_root()
        return self.client.search(
            query=query,
            root=root,
            limit=self.config.result_limit,
            exclude_patterns=self.config.exclude_patterns,
            follow_symlinks=self.config.follow_symlinks,
            cancel_event=cancel_event,
        )

    def build_index(self) -> IndexStatus:
        """为当前第一个根目录构建持久化索引。"""

        root = self._first_root()
        return self.client.build_index(
            index_path=self.config.index_file,
            root=root,
            exclude_patterns=self.config.exclude_patterns,
            follow_symlinks=self.config.follow_symlinks,
        )

    def search_index(self, query: str) -> list[SearchResult]:
        """查询持久化索引，不触发目录扫描。"""

        return self.client.search_index(
            index_path=self.config.index_file,
            query=query,
            limit=self.config.result_limit,
        )

    def index_status(self) -> IndexStatus:
        """返回当前持久化索引状态。"""

        return self.client.index_status(self.config.index_file)

    def _first_root(self) -> Path:
        if not self.config.index_roots:
            raise ValueError("At least one search root is required")
        return self._validated_root(self.config.index_roots[0])

    @staticmethod
    def _validated_root(root: Path) -> Path:
        root = Path(root).resolve()
        if not root.exists():
            raise ValueError(f"Search root was not found: {root}")
        if not root.is_dir():
            raise ValueError(f"Search root is not a directory: {root}")
        return root
