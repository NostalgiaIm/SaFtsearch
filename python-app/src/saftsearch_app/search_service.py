"""桌面层搜索服务。

SearchService 位于 UI 和 CoreClient 之间：

    MainWindow -> SearchService -> CoreClient -> Rust CLI

窗口只需要提交查询，不需要知道命令行参数、JSON 字段或子进程错误。
后续如果增加索引缓存、搜索历史或多根目录，这里会是主要的业务编排位置。
"""

from __future__ import annotations

from pathlib import Path

from saftsearch_app.config import AppConfig
from saftsearch_app.core_client import CoreClient, SearchResult


class SearchService:
    """组合桌面配置与 Rust 客户端，提供 UI 需要的搜索接口。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.client = CoreClient(config.core_binary)

    def search(self, query: str) -> list[SearchResult]:
        """搜索第一个索引根目录。

        当前阶段先支持一个根目录，配置仍然保留列表结构，方便后续扩展
        到多磁盘或多个工作区，而不需要修改外部数据模型。
        """

        if not self.config.index_roots:
            raise ValueError("At least one search root is required")

        root = Path(self.config.index_roots[0]).resolve()
        return self.client.search(
            query=query,
            root=Path(root),
            limit=self.config.result_limit,
            exclude_patterns=self.config.exclude_patterns,
            follow_symlinks=self.config.follow_symlinks,
        )
