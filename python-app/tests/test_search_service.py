"""搜索服务的真实功能测试。

这些测试会调用已经编译好的 Rust CLI，验证 Python UI 服务层和 Rust
搜索核心之间的实际数据链路，而不是只测试模拟对象。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from saftsearch_app.config import AppConfig, project_root
from saftsearch_app.core_client import CoreClientError
from saftsearch_app.search_service import SearchService


class SearchServiceFunctionalTests(unittest.TestCase):
    """验证搜索命中、排序、限制和排除规则。"""

    @classmethod
    def setUpClass(cls) -> None:
        binary_name = "saftsearch-indexer.exe"
        cls.binary = project_root() / "target" / "debug" / binary_name
        if not cls.binary.is_file():
            raise unittest.SkipTest(
                "Rust binary is missing; run `cargo build --bin saftsearch-indexer` first"
            )

    def test_search_returns_matching_files_and_respects_exclusions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "report.md").write_text("# report", encoding="utf-8")
            (root / "report-copy.txt").write_text("copy", encoding="utf-8")
            excluded = root / "target"
            excluded.mkdir()
            (excluded / "report-hidden.md").write_text("hidden", encoding="utf-8")

            config = AppConfig(
                index_roots=[root],
                exclude_patterns=["target"],
                result_limit=10,
                core_binary=self.binary,
            )
            results = SearchService(config).search(" REPORT ")

        names = [result.file_name for result in results]
        self.assertIn("report.md", names)
        self.assertIn("report-copy.txt", names)
        self.assertNotIn("report-hidden.md", names)
        self.assertGreaterEqual(results[0].score, results[-1].score)

    def test_search_returns_no_results_for_unmatched_query(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            config = AppConfig(index_roots=[root], core_binary=self.binary)

            results = SearchService(config).search("does-not-exist")

        self.assertEqual(results, [])

    def test_search_rejects_invalid_root_before_starting_core(self) -> None:
        config = AppConfig(
            index_roots=[Path("this-folder-should-not-exist")],
            core_binary=self.binary,
        )

        with self.assertRaises(ValueError):
            SearchService(config).search("report")

    def test_search_rejects_empty_query(self) -> None:
        with TemporaryDirectory() as directory:
            config = AppConfig(
                index_roots=[Path(directory)],
                core_binary=self.binary,
            )

            with self.assertRaises(CoreClientError):
                SearchService(config).search("   ")


if __name__ == "__main__":
    unittest.main()
