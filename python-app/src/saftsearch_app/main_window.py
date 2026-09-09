"""SaFtsearch 第一版桌面窗口。

界面职责保持在三件事以内：

1. 收集用户输入；
2. 启动后台搜索并显示状态；
3. 对选中的路径执行桌面操作。

扫描和排序始终由 Rust 负责。搜索任务放进 QThread，是为了避免 Rust
扫描目录期间阻塞 Qt 主线程，从而保持窗口可以移动、关闭和响应输入。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from saftsearch_app.config import AppConfig
from saftsearch_app.core_client import CoreClientError, SearchResult
from saftsearch_app.search_service import SearchService


def format_size(size_bytes: int) -> str:
    """把 Rust 返回的字节数转换为适合表格阅读的文本。"""

    if size_bytes < 1024:
        return f"{size_bytes} B"

    units = ("KB", "MB", "GB", "TB")
    value = float(size_bytes)
    for unit in units:
        value /= 1024
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"

    return f"{size_bytes} B"


def format_modified_time(timestamp_ms: int | None) -> str:
    """把 Unix 毫秒时间转换为本地时间文本。"""

    if timestamp_ms is None:
        return "Unknown"

    from datetime import datetime

    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M")


class SearchWorker(QObject):
    """在线程中执行一次搜索。

    QObject 不直接创建线程；QThread 负责线程生命周期，worker 只负责
    工作内容。任务完成后通过信号把结果交回主线程，UI 不从后台线程
    直接修改控件。
    """

    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, service: SearchService, query: str) -> None:
        super().__init__()
        self.service = service
        self.query = query

    @Slot()
    def run(self) -> None:
        """在线程中调用 Rust，并把结果或错误发送给主窗口。"""

        try:
            results = self.service.search(self.query)
        except (CoreClientError, OSError, ValueError) as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(results)


class MainWindow(QMainWindow):
    """SaFtsearch 主窗口。"""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()

        self.config = config
        self.service = SearchService(config)
        self.search_thread: QThread | None = None
        self.search_worker: SearchWorker | None = None
        self.current_results: list[SearchResult] = []
        # 如果用户在旧搜索尚未完成时继续输入，保存最新查询，旧任务结束后
        # 自动补发一次，避免用户必须再次按 Enter。
        self.pending_query: str | None = None

        self.setWindowTitle("SaFtsearch")
        self.resize(1120, 720)
        self.setMinimumSize(760, 480)

        self._build_ui()
        self._connect_signals()
        self._apply_style()
        self._update_root_label()

    def _build_ui(self) -> None:
        """创建控件和布局。

        这里不执行业务逻辑，只建立稳定的界面骨架，便于后续把设置页、
        索引状态和更多结果动作接入到明确的位置。
        """

        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(24, 20, 24, 16)
        root_layout.setSpacing(14)

        header_layout = QHBoxLayout()
        title = QLabel("SaFtsearch")
        title.setObjectName("titleLabel")
        subtitle = QLabel("Fast local file search")
        subtitle.setObjectName("subtitleLabel")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        header_layout.addStretch()

        self.root_button = QPushButton("Choose folder")
        self.root_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self.root_button.setObjectName("secondaryButton")
        self.root_button.setToolTip("Choose the directory that Rust will scan")
        header_layout.addWidget(self.root_button)
        root_layout.addLayout(header_layout)

        search_frame = QFrame()
        search_frame.setObjectName("searchFrame")
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(12, 8, 12, 8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search file names...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumHeight(42)
        self.search_input.setObjectName("searchInput")

        self.search_button = QPushButton("Search")
        self.search_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        )
        self.search_button.setObjectName("primaryButton")
        self.search_button.setMinimumHeight(42)

        search_layout.addWidget(self.search_input, 1)
        search_layout.addWidget(self.search_button)
        root_layout.addWidget(search_frame)

        controls_layout = QHBoxLayout()
        self.root_label = QLabel()
        self.root_label.setObjectName("pathLabel")
        self.limit_label = QLabel("Results")
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 500)
        self.limit_spin.setValue(self.config.result_limit)
        self.limit_spin.setToolTip("Maximum number of results returned by Rust")

        controls_layout.addWidget(self.root_label, 1)
        controls_layout.addWidget(self.limit_label)
        controls_layout.addWidget(self.limit_spin)
        root_layout.addLayout(controls_layout)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.results_table = QTableWidget(0, 5)
        self.results_table.setObjectName("resultsTable")
        self.results_table.setHorizontalHeaderLabels(
            ["Name", "Location", "Size", "Modified", "Score"]
        )
        self.results_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.results_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.setColumnWidth(0, 260)
        self.results_table.setColumnWidth(1, 560)
        self.results_table.setColumnWidth(2, 100)
        self.results_table.setColumnWidth(3, 150)
        splitter.addWidget(self.results_table)

        self.detail_frame = QFrame()
        self.detail_frame.setObjectName("detailFrame")
        detail_layout = QVBoxLayout(self.detail_frame)
        detail_layout.setContentsMargins(14, 10, 14, 10)
        self.detail_title = QLabel("Select a result to see its full path.")
        self.detail_title.setObjectName("detailTitle")
        self.detail_path = QLabel("")
        self.detail_path.setObjectName("detailPath")
        self.detail_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_buttons = QHBoxLayout()
        self.open_button = QPushButton("Open")
        self.open_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self.copy_button = QPushButton("Copy path")
        self.copy_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton)
        )
        for button in (self.open_button, self.open_folder_button, self.copy_button):
            button.setEnabled(False)
            detail_buttons.addWidget(button)
        detail_buttons.addStretch()
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_path)
        detail_layout.addLayout(detail_buttons)
        splitter.addWidget(self.detail_frame)
        splitter.setSizes([510, 130])
        root_layout.addWidget(splitter, 1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        # Ctrl+L 是文件搜索工具中常见的“回到搜索框”快捷键。
        focus_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        focus_shortcut.activated.connect(self._focus_search)

    def _connect_signals(self) -> None:
        """连接控件信号，并配置输入防抖。"""

        self.search_button.clicked.connect(self._start_search)
        self.search_input.returnPressed.connect(self._start_search)
        self.root_button.clicked.connect(self._choose_root)
        self.results_table.itemSelectionChanged.connect(self._show_selected_result)
        self.results_table.itemDoubleClicked.connect(lambda _item: self._open_selected_result())
        self.open_button.clicked.connect(self._open_selected_result)
        self.open_folder_button.clicked.connect(self._open_selected_folder)
        self.copy_button.clicked.connect(self._copy_selected_path)

        # 用户连续输入时不立即启动多个扫描任务。定时器每次输入都重置，
        # 只有停止输入 debounce_ms 后才真正调用 Rust。
        self.search_debounce = QTimer(self)
        self.search_debounce.setSingleShot(True)
        self.search_debounce.setInterval(self.config.debounce_ms)
        self.search_debounce.timeout.connect(self._start_search)
        self.search_input.textChanged.connect(self._schedule_search)

    def _apply_style(self) -> None:
        """应用轻量样式，让界面先具备稳定的桌面工具视觉层次。"""

        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f5f7fa;
                color: #1f2937;
                font-size: 13px;
            }
            #titleLabel {
                color: #111827;
                font-size: 26px;
                font-weight: 700;
            }
            #subtitleLabel {
                color: #6b7280;
                padding-left: 8px;
            }
            #searchFrame, #detailFrame {
                background: #ffffff;
                border: 1px solid #d9dee7;
                border-radius: 8px;
            }
            #searchInput {
                border: none;
                background: transparent;
                color: #111827;
                font-size: 16px;
                padding: 4px;
            }
            #primaryButton {
                background: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 0 18px;
                font-weight: 600;
            }
            #primaryButton:hover {
                background: #1d4ed8;
            }
            #secondaryButton, QPushButton {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 7px 12px;
            }
            QPushButton:hover {
                background: #eef2ff;
            }
            QPushButton:disabled {
                color: #9ca3af;
                background: #f3f4f6;
            }
            #pathLabel, #detailPath {
                color: #6b7280;
            }
            #resultsTable {
                background: #ffffff;
                alternate-background-color: #f8fafc;
                border: 1px solid #d9dee7;
                gridline-color: #e5e7eb;
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            #detailTitle {
                font-weight: 600;
                color: #374151;
            }
            QHeaderView::section {
                background: #eef2f7;
                color: #4b5563;
                border: none;
                padding: 8px;
                font-weight: 600;
            }
            """
        )

    @Slot()
    def _focus_search(self) -> None:
        """把键盘焦点放回搜索框，并选中现有文本。"""

        self.search_input.setFocus()
        self.search_input.selectAll()

    @Slot(str)
    def _schedule_search(self, query: str) -> None:
        """根据输入内容安排防抖搜索。"""

        if not query.strip():
            self.search_debounce.stop()
            self._clear_results("Type a file name to search")
            return

        self.search_debounce.start()

    @Slot()
    def _start_search(self) -> None:
        """启动一次后台搜索，并取消/收尾旧任务。"""

        query = self.search_input.text().strip()
        if not query:
            self._clear_results("Type a file name to search")
            return

        if self.search_thread is not None and self.search_thread.isRunning():
            # 当前 Rust CLI 是一次性任务，不能安全地强制终止。
            # 让旧任务自然结束；任务结束后会自动执行最新查询。
            self.pending_query = query
            self.status_bar.showMessage("Finishing the current search...")
            return

        self.config.result_limit = self.limit_spin.value()
        self.search_button.setEnabled(False)
        self.status_bar.showMessage(f"Searching for '{query}'...")
        self._clear_selection()

        self.search_thread = QThread(self)
        self.search_worker = SearchWorker(self.service, query)
        self.search_worker.moveToThread(self.search_thread)
        self.search_thread.started.connect(self.search_worker.run)
        self.search_worker.finished.connect(self._handle_results)
        self.search_worker.failed.connect(self._handle_error)
        self.search_worker.finished.connect(self.search_thread.quit)
        self.search_worker.failed.connect(self.search_thread.quit)
        self.search_worker.finished.connect(self.search_worker.deleteLater)
        self.search_worker.failed.connect(self.search_worker.deleteLater)
        self.search_thread.finished.connect(self._finish_search_thread)
        self.search_thread.start()

    @Slot(list)
    def _handle_results(self, results: list[SearchResult]) -> None:
        """在 Qt 主线程中将 Rust 结果填入表格。"""

        self.current_results = results
        self.results_table.setRowCount(0)

        for row, result in enumerate(results):
            self.results_table.insertRow(row)
            values = [
                result.file_name,
                str(result.path.parent),
                format_size(result.size_bytes),
                format_modified_time(result.modified_unix_ms),
                f"{result.score:.0f}",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (2, 4):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.results_table.setItem(row, column, item)

        count = len(results)
        self.status_bar.showMessage(f"{count} result(s)")

    @Slot(str)
    def _handle_error(self, message: str) -> None:
        """将内核错误转换为用户可理解的状态信息。"""

        self.status_bar.showMessage("Search failed")
        QMessageBox.warning(self, "Search failed", message)

    @Slot()
    def _finish_search_thread(self) -> None:
        """释放线程对象，避免连续搜索时积累线程实例。"""

        if self.search_thread is not None:
            self.search_thread.deleteLater()
        self.search_thread = None
        self.search_worker = None
        self.search_button.setEnabled(True)

        if self.pending_query is not None:
            pending_query = self.pending_query
            self.pending_query = None
            if pending_query != self.search_input.text().strip():
                self.search_input.blockSignals(True)
                self.search_input.setText(pending_query)
                self.search_input.blockSignals(False)
            self.search_debounce.stop()
            self._start_search()

    @Slot()
    def _choose_root(self) -> None:
        """让用户选择新的扫描根目录。"""

        current_root = str(self.config.index_roots[0])
        selected = QFileDialog.getExistingDirectory(self, "Choose search folder", current_root)
        if not selected:
            return

        self.config.index_roots = [Path(selected)]
        self._update_root_label()
        self._clear_results("Search folder changed")

    def _update_root_label(self) -> None:
        """刷新界面上的当前扫描目录文本。"""

        self.root_label.setText(f"Folder: {self.config.index_roots[0]}")
        self.root_label.setToolTip(str(self.config.index_roots[0]))

    def _clear_results(self, message: str) -> None:
        """清空结果表和详情区域，并显示下一步提示。"""

        self.current_results = []
        self.results_table.setRowCount(0)
        self.detail_title.setText(message)
        self.detail_path.clear()
        for button in (self.open_button, self.open_folder_button, self.copy_button):
            button.setEnabled(False)
        self.status_bar.showMessage(message)

    def _clear_selection(self) -> None:
        """搜索开始时清除旧选中项，避免用户误操作旧结果。"""

        self.results_table.clearSelection()
        self.detail_title.setText("Searching...")
        self.detail_path.clear()
        for button in (self.open_button, self.open_folder_button, self.copy_button):
            button.setEnabled(False)

    @Slot()
    def _show_selected_result(self) -> None:
        """把当前选中行映射回 SearchResult，并更新详情按钮。"""

        row = self.results_table.currentRow()
        if row < 0 or row >= len(self.current_results):
            self.detail_title.setText("Select a result to see its full path.")
            self.detail_path.clear()
            for button in (self.open_button, self.open_folder_button, self.copy_button):
                button.setEnabled(False)
            return

        result = self.current_results[row]
        self.detail_title.setText(result.file_name)
        self.detail_path.setText(str(result.path))
        for button in (self.open_button, self.open_folder_button, self.copy_button):
            button.setEnabled(True)

    def _selected_result(self) -> SearchResult | None:
        """返回当前选中结果；没有选中项时返回 None。"""

        row = self.results_table.currentRow()
        if 0 <= row < len(self.current_results):
            return self.current_results[row]
        return None

    @Slot()
    def _open_selected_result(self) -> None:
        """使用系统默认程序打开选中的文件。"""

        result = self._selected_result()
        if result is None:
            return

        try:
            if sys.platform == "win32":
                os.startfile(str(result.path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(result.path)])
            else:
                subprocess.Popen(["xdg-open", str(result.path)])
        except OSError as exc:
            QMessageBox.warning(self, "Open failed", str(exc))

    @Slot()
    def _open_selected_folder(self) -> None:
        """在系统文件管理器中打开结果所在目录。"""

        result = self._selected_result()
        if result is None:
            return

        folder = result.path.parent
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            QMessageBox.warning(self, "Open folder failed", str(exc))

    @Slot()
    def _copy_selected_path(self) -> None:
        """将选中文件的完整路径复制到系统剪贴板。"""

        result = self._selected_result()
        if result is None:
            return

        QApplication.clipboard().setText(str(result.path))
        self.status_bar.showMessage("Path copied")

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭窗口时保护后台搜索线程。

        当前阶段 Rust CLI 由 ``subprocess.run`` 阻塞执行，Qt 无法在不破坏
        子进程状态的前提下立即取消它。因此搜索中关闭窗口时先拒绝关闭，
        等本次搜索结束后再允许退出。后续改为常驻 Rust 服务时，可以在
        协议层增加 cancel 请求，再把这里改成真正的取消流程。
        """

        if self.search_thread is not None and self.search_thread.isRunning():
            self.status_bar.showMessage("Search is still running. Close after it finishes.")
            event.ignore()
            return
        event.accept()
