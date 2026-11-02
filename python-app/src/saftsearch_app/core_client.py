"""Rust 搜索内核的 Python 进程客户端。

本模块只处理“如何启动 Rust”和“如何把 JSON 转成 Python 对象”。
它不负责绘制界面，也不负责实现搜索评分，这样后续把 CLI 替换为
常驻 JSON-RPC 服务时，UI 层不需要大面积改写。
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CoreClientError(RuntimeError):
    """Rust 内核不可用、执行失败或返回数据格式错误时抛出的异常。"""


# 当前阶段使用一次性 CLI 搜索，因此需要限制外部输入规模，避免
# 恶意或误操作导致无意义的超大扫描请求。
MAX_QUERY_LENGTH = 256
MAX_EXCLUDE_PATTERN_LENGTH = 256
MAX_EXCLUDE_PATTERNS = 64
MAX_RESULT_LIMIT = 500


def _validate_query(query: str) -> str:
    """校验并规范化查询字符串。"""

    if not isinstance(query, str):
        raise CoreClientError("Search query must be text")

    normalized = query.strip()
    if not normalized:
        raise CoreClientError("Search query cannot be empty")
    if len(normalized) > MAX_QUERY_LENGTH:
        raise CoreClientError(f"Search query is limited to {MAX_QUERY_LENGTH} characters")
    if "\x00" in normalized:
        raise CoreClientError("Search query cannot contain a null character")
    return normalized


def _validate_limit(limit: int) -> None:
    """限制结果数量，防止无界的结果加载。"""

    if isinstance(limit, bool) or not isinstance(limit, int):
        raise CoreClientError("Result limit must be an integer")
    if not 1 <= limit <= MAX_RESULT_LIMIT:
        raise CoreClientError(f"Result limit must be between 1 and {MAX_RESULT_LIMIT}")


def _validate_exclude_patterns(exclude_patterns: list[str]) -> list[str]:
    """校验排除模式，防止空模式意外排除整个扫描树。"""

    if len(exclude_patterns) > MAX_EXCLUDE_PATTERNS:
        raise CoreClientError(
            f"At most {MAX_EXCLUDE_PATTERNS} exclude patterns are allowed"
        )

    validated: list[str] = []
    for pattern in exclude_patterns:
        if not isinstance(pattern, str):
            raise CoreClientError("Exclude patterns must be text")
        if not pattern.strip():
            raise CoreClientError("Exclude patterns cannot be empty")
        if len(pattern) > MAX_EXCLUDE_PATTERN_LENGTH:
            raise CoreClientError(
                f"Exclude patterns are limited to {MAX_EXCLUDE_PATTERN_LENGTH} characters"
            )
        if "\x00" in pattern:
            raise CoreClientError("Exclude patterns cannot contain a null character")
        validated.append(pattern)
    return validated


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

        if not isinstance(payload, dict):
            raise CoreClientError("Rust search result must be a JSON object")

        feature = payload.get("feature")
        if not isinstance(feature, dict):
            raise CoreClientError("Rust result is missing the 'feature' object")

        path_value = feature.get("path")
        file_name = feature.get("file_name")
        if (
            not isinstance(path_value, str)
            or not path_value
            or not isinstance(file_name, str)
            or not file_name
        ):
            raise CoreClientError("Rust result contains an invalid file path or name")

        extension = feature.get("extension")
        if extension is not None and not isinstance(extension, str):
            raise CoreClientError("Rust result contains an invalid file extension")

        size_bytes = feature.get("size_bytes")
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
        ):
            raise CoreClientError("Rust result contains an invalid file size")

        modified_unix_ms = feature.get("modified_unix_ms")
        if (
            modified_unix_ms is not None
            and (
                not isinstance(modified_unix_ms, int)
                or isinstance(modified_unix_ms, bool)
                or modified_unix_ms < 0
            )
        ):
            raise CoreClientError("Rust result contains an invalid modified time")

        score_value = payload.get("score")
        if (
            not isinstance(score_value, (int, float))
            or isinstance(score_value, bool)
            or not math.isfinite(float(score_value))
        ):
            raise CoreClientError("Rust result contains an invalid score")

        return cls(
            path=Path(path_value),
            file_name=file_name,
            extension=extension,
            size_bytes=size_bytes,
            modified_unix_ms=modified_unix_ms,
            score=float(score_value),
            raw=payload,
        )


@dataclass(frozen=True)
class IndexStatus:
    """持久化索引的可展示摘要。"""

    format_version: int
    created_unix_ms: int
    index_path: Path
    roots: tuple[Path, ...]
    file_count: int

    @classmethod
    def from_payload(cls, payload: Any) -> IndexStatus:
        if not isinstance(payload, dict):
            raise CoreClientError("Rust index status must be a JSON object")

        format_version = payload.get("format_version")
        created_unix_ms = payload.get("created_unix_ms")
        index_path = payload.get("index_path")
        roots = payload.get("roots")
        file_count = payload.get("file_count")

        if not isinstance(format_version, int) or isinstance(format_version, bool):
            raise CoreClientError("Rust index status contains an invalid format version")
        if (
            not isinstance(created_unix_ms, int)
            or isinstance(created_unix_ms, bool)
            or created_unix_ms < 0
        ):
            raise CoreClientError("Rust index status contains an invalid creation time")
        if not isinstance(index_path, str) or not index_path:
            raise CoreClientError("Rust index status contains an invalid index path")
        if not isinstance(roots, list) or not roots or not all(
            isinstance(root, str) and root for root in roots
        ):
            raise CoreClientError("Rust index status contains invalid roots")
        if (
            not isinstance(file_count, int)
            or isinstance(file_count, bool)
            or file_count < 0
        ):
            raise CoreClientError("Rust index status contains an invalid file count")

        return cls(
            format_version=format_version,
            created_unix_ms=created_unix_ms,
            index_path=Path(index_path),
            roots=tuple(Path(root) for root in roots),
            file_count=file_count,
        )


class CoreClient:
    """通过一次性子进程调用当前阶段的 Rust CLI。

    现在的 Rust 程序每次搜索都会扫描目标目录，因此这里把它封装在
    独立客户端中。未来接入持久化索引或 JSON-RPC 服务时，只需要替换
    这个类，窗口和搜索服务的调用方式可以保持不变。
    """

    def __init__(
        self,
        binary: Path,
        timeout_seconds: float = 30.0,
        index_build_timeout_seconds: float = 600.0,
    ) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self.index_build_timeout_seconds = index_build_timeout_seconds

    def _ensure_binary(self) -> None:
        if not self.binary.is_file():
            raise CoreClientError(
                f"Rust core binary was not found: {self.binary}\n"
                "Run `cargo build --bin saftsearch-indexer` first."
            )

    @staticmethod
    def _validate_root(root: Path) -> Path:
        root = Path(root)
        root_text = str(root)
        if "\x00" in root_text:
            raise CoreClientError("Search root cannot contain a null character")
        if not root.exists():
            raise CoreClientError(f"Search root was not found: {root}")
        if not root.is_dir():
            raise CoreClientError(f"Search root is not a directory: {root}")
        return root

    @staticmethod
    def _validate_index_path(index_path: Path) -> Path:
        index_path = Path(index_path)
        if not str(index_path) or "\x00" in str(index_path):
            raise CoreClientError("Index path is invalid")
        if index_path.exists() and index_path.is_dir():
            raise CoreClientError(f"Index path is a directory: {index_path}")
        return index_path

    def _run(
        self,
        command: list[str],
        timeout_message: str,
        timeout_seconds: float | None = None,
    ) -> str:
        self._ensure_binary()
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds if timeout_seconds is None else timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CoreClientError(timeout_message) from exc
        except OSError as exc:
            raise CoreClientError(f"Could not start Rust core: {exc}") from exc

        if completed.returncode != 0:
            message = completed.stderr.strip() or "Rust core returned a non-zero exit code"
            raise CoreClientError(message)
        return completed.stdout

    def _build_command(
        self,
        query: str,
        root: Path,
        limit: int,
        exclude_patterns: list[str],
        follow_symlinks: bool,
    ) -> list[str]:
        """构建不会经过 shell 解释的参数数组。

        所有用户输入都作为独立参数传给 subprocess，而不是拼接成一条
        shell 命令。这是避免空格、引号或 shell 特殊字符造成命令注入的
        关键边界。
        """

        normalized_query = _validate_query(query)
        _validate_limit(limit)
        validated_patterns = _validate_exclude_patterns(exclude_patterns)

        root = self._validate_root(root)
        root_text = str(root)
        if not isinstance(follow_symlinks, bool):
            raise CoreClientError("follow_symlinks must be a boolean")

        command = [
            str(self.binary),
            "search",
            normalized_query,
            root_text,
            "--limit",
            str(limit),
        ]

        # Rust CLI 使用重复的 --exclude 表达多个排除模式。
        for pattern in validated_patterns:
            command.extend(["--exclude", pattern])

        if follow_symlinks:
            command.append("--follow-symlinks")

        return command

    def search(
        self,
        query: str,
        root: Path,
        limit: int,
        exclude_patterns: list[str],
        follow_symlinks: bool,
    ) -> list[SearchResult]:
        """调用 Rust `search` 子命令并解析 SearchHit 数组。"""

        command = self._build_command(
            query=query,
            root=root,
            limit=limit,
            exclude_patterns=exclude_patterns,
            follow_symlinks=follow_symlinks,
        )

        return parse_search_payload(self._run(command, "Rust search timed out"))

    def build_index(
        self,
        index_path: Path,
        root: Path,
        exclude_patterns: list[str],
        follow_symlinks: bool,
    ) -> IndexStatus:
        """扫描根目录并写入版本化 JSON 索引。"""

        index_path = self._validate_index_path(index_path)
        root = self._validate_root(root)
        patterns = _validate_exclude_patterns(exclude_patterns)
        if not isinstance(follow_symlinks, bool):
            raise CoreClientError("follow_symlinks must be a boolean")

        command = [
            str(self.binary),
            "index",
            "build",
            str(index_path),
            str(root),
        ]
        for pattern in patterns:
            command.extend(["--exclude", pattern])
        if follow_symlinks:
            command.append("--follow-symlinks")

        return parse_index_status_payload(
            self._run(
                command,
                "Rust index build timed out",
                timeout_seconds=self.index_build_timeout_seconds,
            )
        )

    def search_index(
        self,
        index_path: Path,
        query: str,
        limit: int,
    ) -> list[SearchResult]:
        """查询已存在的索引，不重新扫描文件系统。"""

        index_path = self._validate_index_path(index_path)
        if not index_path.is_file():
            raise CoreClientError(f"Search index was not found: {index_path}")
        normalized_query = _validate_query(query)
        _validate_limit(limit)
        command = [
            str(self.binary),
            "index",
            "search",
            str(index_path),
            normalized_query,
            "--limit",
            str(limit),
        ]
        return parse_search_payload(self._run(command, "Rust index search timed out"))

    def index_status(self, index_path: Path) -> IndexStatus:
        """读取索引版本、根目录、文件数和构建时间。"""

        index_path = self._validate_index_path(index_path)
        if not index_path.is_file():
            raise CoreClientError(f"Search index was not found: {index_path}")
        command = [str(self.binary), "index", "status", str(index_path)]
        return parse_index_status_payload(
            self._run(command, "Rust index status timed out")
        )


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


def parse_index_status_payload(payload_text: str) -> IndexStatus:
    """解析 Rust 的索引状态 JSON。"""

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise CoreClientError("Rust core returned invalid index status JSON") from exc
    return IndexStatus.from_payload(payload)
