"""SaFtsearch Python 桌面应用入口。"""

import sys

from saftsearch_app.config import AppConfig


def main() -> int:
    """创建 Qt 应用、主窗口，并把退出码交给操作系统。"""

    try:
        from PySide6.QtWidgets import QApplication

        from saftsearch_app.main_window import MainWindow
    except ModuleNotFoundError as exc:
        if exc.name != "PySide6":
            raise
        print(
            "PySide6 is not installed. Run `python -m pip install -e .[desktop]` "
            "from the project root, then start SaFtsearch again.",
            file=sys.stderr,
        )
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("SaFtsearch")
    app.setOrganizationName("NostalgiaIm")

    window = MainWindow(AppConfig())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
