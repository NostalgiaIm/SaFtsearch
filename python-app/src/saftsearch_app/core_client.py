"""Rust 搜索内核的 Python 进程客户端。

本模块只处理“如何启动 Rust”和“如何把 JSON 转成 Python 对象”。
它不负责绘制界面，也不负责实现搜索评分，这样后续把 CLI 替换为
常驻 JSON-RPC 服务时，UI 层不需要大面积改写。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CoreClientError(RuntimeError):
    """Rust 内核不可用、执行失败或返回数据格式错误时抛出的异常。"""


@dataclass(frozen=True)
class SearchResult:
    """UI 展示所需的一个搜索结果。

    Rust 返回的 JSON 还包含了更多字段。这里先保留界面当前需要的字段，
    并把原始数据放在 ``raw`` 中，便于后续增加列而不破坏协议。
    """

    path: Path
    file_name: str
    extension: str | None
    size_bytes: int
    modified_unix_ms: int | None
    score: float
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SearchResult:
        """把 Rust 的 SearchHit JSON 转为类型明确的 Python 对象。"""

        feature = payload.get("feature")
        if not isinstance(feature, dict):
            raise CoreClientError("Rust result is missing the 'feature' object")

        path_value = feature.get("path")
        file_name = feature.get("file_name")
        if not isinstance(path_value, str) or not isinstance(file_name, str):
            raise CoreClientError("Rust result contains an invalid file path or name")

        return cls(
            path=Path(path_value),
            file_name=file_name,
            extension=feature.get("extension"),
            size_bytes=int(feature.get("size_bytes", 0)),
            modified_unix_ms=feature.get("modified_unix_ms"),
            score=float(payload.get("score", 0.0)),
            raw=payload,
        )


class CoreClient:
    """通过一次性子进程调用当前阶段的 Rust CLI。

    现在的 Rust 程序每次搜索都会扫描目标目录，因此这里把它封装在
    独立客户端中。未来接入持久化索引或 JSON-RPC 服务时，只需要替换
    这个类，窗口和搜索服务的调用方式可以保持不变。
    """

    def __init__(self, binary: Path, timeout_seconds: float = 30.0) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds

    def search(
        self,
        query: str,
        root: Path,
        limit: int,
        exclude_patterns: list[str],
        follow_symlinks: bool,
    ) -> list[SearchResult]:
        """调用 Rust `search` 子命令并解析 SearchHit 数组。"""

        if not self.binary.exists():
            raise CoreClientError(
                f"Rust core binary was not found: {self.binary}\n"
                "Run `cargo build --bin saftsearch-indexer` first."
            )

        command = [
            str(self.binary),
            "search",
            query,
            str(root),
            "--limit",
            str(limit),
        ]

        # Rust CLI 使用重复的 --exclude 表达多个排除模式。
        for pattern in exclude_patterns:
            command.extend(["--exclude", pattern])

        if follow_symlinks:
            command.append("--follow-symlinks")

        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CoreClientError("Rust search timed out") from exc
        except OSError as exc:
            raise CoreClientError(f"Could not start Rust core: {exc}") from exc

        if completed.returncode != 0:
            message = completed.stderr.strip() or "Rust core returned a non-zero exit code"
            raise CoreClientError(message)

        return parse_search_payload(completed.stdout)


def parse_search_payload(payload_text: str) -> list[SearchResult]:
    """解析 Rust stdout 中的 JSON 数组。

    这个小函数把纯数据转换从子进程启动逻辑中拆出来，因此可以在没有
    启动 Rust 的情况下测试协议边界，例如字段缺失或返回对象类型错误。
    """

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise CoreClientError("Rust core returned invalid JSON") from exc

    if not isinstance(payload, list):
        raise CoreClientError("Rust search response must be a JSON array")

    if not all(isinstance(item, dict) for item in payload):
        raise CoreClientError("Rust search response items must be JSON objects")

    try:
        return [SearchResult.from_payload(item) for item in payload]
    except (TypeError, ValueError) as exc:
        raise CoreClientError("Rust search response contains invalid fields") from exc
