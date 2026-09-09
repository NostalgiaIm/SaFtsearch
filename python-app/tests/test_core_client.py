"""Rust/Python JSON 协议的最小测试。

这些测试不启动 PySide6，也不启动真正的 Rust 子进程。它们只验证
Rust stdout 的 JSON 结构是否能被 Python 桌面层正确转换。
"""

import unittest

from saftsearch_app.core_client import CoreClientError, parse_search_payload


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


if __name__ == "__main__":
    unittest.main()
