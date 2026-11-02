"""桌面窗口搜索触发和过期结果保护测试。"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    from saftsearch_app.config import AppConfig
    from saftsearch_app.core_client import SearchResult
    from saftsearch_app.main_window import MainWindow
except ModuleNotFoundError as exc:
    if exc.name != "PySide6":
        raise
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 desktop dependency is not installed")
class MainWindowSearchFlowTests(unittest.TestCase):
    """直接扫描模式不能随输入自动启动，也不能显示旧目录结果。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_typing_does_not_start_a_direct_scan(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow(AppConfig(index_roots=[Path(directory)]))
            window.search_input.setText("report")
            self.app.processEvents()

            self.assertIsNone(window.search_thread)
            window.close()

    def test_results_from_previous_folder_are_ignored(self) -> None:
        with TemporaryDirectory() as directory, TemporaryDirectory() as previous:
            window = MainWindow(AppConfig(index_roots=[Path(directory)]))
            stale_result = SearchResult(
                path=Path(previous) / "report.md",
                file_name="report.md",
                extension="md",
                size_bytes=1,
                modified_unix_ms=None,
                score=80.0,
                raw={},
            )

            window._handle_results([stale_result], str(Path(previous).resolve()))

            self.assertEqual(window.current_results, [])
            window.close()


if __name__ == "__main__":
    unittest.main()
