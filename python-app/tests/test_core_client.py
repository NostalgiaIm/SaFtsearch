"""Rust/Python JSON 协议的最小测试。

这些测试不启动 PySide6，也不启动真正的 Rust 子进程。它们只验证
Rust stdout 的 JSON 结构是否能被 Python 桌面层正确转换。
"""

import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Timer
from unittest.mock import Mock, patch

from saftsearch_app.core_client import (
    MAX_EXCLUDE_PATTERN_LENGTH,
    MAX_EXCLUDE_PATTERNS,
    MAX_QUERY_LENGTH,
    CoreClient,
    CoreClientCancelled,
    CoreClientError,
    parse_index_status_payload,
    parse_search_payload,
)


class ParseSearchPayloadTests(unittest.TestCase):
    """验证 Rust SearchHit JSON 到 Python SearchResult 的转换规则。"""

    def test_parse_search_payload_returns_result_objects(self) -> None:
        """合法 SearchHit JSON 应转换为 SearchResult 对象。"""

        payload = """
        [
          {
            "feature": {
              "path": "D:/demo/report.md",
              "file_name": "report.md",
              "extension": "md",
              "size_bytes": 128,
              "modified_unix_ms": 1700000000000
            },
            "score": 80.0
          }
        ]
        """

        results = parse_search_payload(payload)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].file_name, "report.md")
        self.assertEqual(results[0].score, 80.0)

    def test_parse_search_payload_rejects_non_array_payload(self) -> None:
        """Rust search 响应必须是数组，避免 UI 猜测不稳定格式。"""

        with self.assertRaisesRegex(CoreClientError, "JSON array"):
            parse_search_payload('{"hits": []}')


class CoreClientValidationTests(unittest.TestCase):
    """验证用户输入和子进程调用边界。"""

    def setUp(self) -> None:
        self.client = CoreClient(Path("saftsearch-indexer.exe"))

    def test_query_rejects_empty_null_and_oversized_input(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            for query in ("   ", "name\x00.txt", "x" * (MAX_QUERY_LENGTH + 1)):
                with self.subTest(query=query):
                    with self.assertRaises(CoreClientError):
                        self.client._build_command(query, root, 10, [], False)

    def test_limit_and_exclude_patterns_are_bounded(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            with self.assertRaises(CoreClientError):
                self.client._build_command("report", root, 0, [], False)
            with self.assertRaises(CoreClientError):
                self.client._build_command("report", root, 501, [], False)
            with self.assertRaises(CoreClientError):
                self.client._build_command("report", root, 10, [""], False)
            with self.assertRaises(CoreClientError):
                self.client._build_command(
                    "report", root, 10, ["x" * (MAX_EXCLUDE_PATTERN_LENGTH + 1)], False
                )
            with self.assertRaises(CoreClientError):
                self.client._build_command(
                    "report", root, 10, ["target"] * (MAX_EXCLUDE_PATTERNS + 1), False
                )

    def test_shell_metacharacters_remain_one_argument(self) -> None:
        """查询中的 shell 字符不应被拼接或解释为命令。"""

        with TemporaryDirectory() as directory:
            root = Path(directory)
            command = self.client._build_command(
                "report & whoami",
                root,
                10,
                ["target | echo injected"],
                False,
            )

        self.assertIn("report & whoami", command)
        self.assertIn("target | echo injected", command)
        self.assertNotIn("report", command[:2])
        self.assertNotIn("whoami", command[:2])

    def test_search_uses_shell_false(self) -> None:
        """子进程调用必须显式关闭 shell 解释。"""

        with TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "saftsearch-indexer.exe"
            binary.write_bytes(b"test binary placeholder")
            client = CoreClient(binary)
            process = Mock()
            process.communicate.return_value = ("[]", "")
            process.returncode = 0

            with patch(
                "saftsearch_app.core_client.subprocess.Popen", return_value=process
            ) as popen:
                results = client.search("report", root, 10, [], False)

        self.assertEqual(results, [])
        self.assertFalse(popen.call_args.kwargs["shell"])

    def test_running_process_can_be_cancelled_promptly(self) -> None:
        client = CoreClient(Path(sys.executable), timeout_seconds=5.0)
        cancel_event = Event()
        timer = Timer(0.2, cancel_event.set)
        started = time.monotonic()
        timer.start()
        try:
            with self.assertRaises(CoreClientCancelled):
                client._run(
                    [sys.executable, "-c", "import time; time.sleep(10)"],
                    "test process timed out",
                    cancel_event=cancel_event,
                )
        finally:
            timer.cancel()

        self.assertLess(time.monotonic() - started, 3.0)

    def test_index_status_payload_is_typed(self) -> None:
        status = parse_index_status_payload(
            """
            {
              "format_version": 1,
              "created_unix_ms": 1700000000000,
              "index_path": "D:/demo/index.json",
              "roots": ["D:/demo"],
              "file_count": 42
            }
            """
        )

        self.assertEqual(status.format_version, 1)
        self.assertEqual(status.file_count, 42)
        self.assertEqual(status.roots, (Path("D:/demo"),))

    def test_build_index_uses_structured_arguments_and_shell_false(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "saftsearch-indexer.exe"
            binary.write_bytes(b"test binary placeholder")
            index_path = root / ".saftsearch" / "index.json"
            client = CoreClient(binary)
            process = Mock()
            process.communicate.return_value = (
                '{"format_version":1,"created_unix_ms":1,'
                f'"index_path":"{index_path.as_posix()}",'
                f'"roots":["{root.as_posix()}"],"file_count":2}}',
                "",
            )
            process.returncode = 0

            with patch(
                "saftsearch_app.core_client.subprocess.Popen", return_value=process
            ) as popen:
                status = client.build_index(index_path, root, ["target"], False)

        command = popen.call_args.args[0]
        self.assertEqual(status.file_count, 2)
        self.assertEqual(command[1:3], ["index", "build"])
        self.assertIn("--exclude", command)
        self.assertFalse(popen.call_args.kwargs["shell"])

    def test_index_search_requires_existing_index(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CoreClientError, "not found"):
                self.client.search_index(Path(directory) / "missing.json", "report", 10)


if __name__ == "__main__":
    unittest.main()
