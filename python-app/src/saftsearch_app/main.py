from saftsearch_app.config import AppConfig


def main() -> None:
    """应用入口占位：后续接入 PySide6 主窗口与 Rust 搜索进程。"""

    config = AppConfig()
    print(f"SaFtsearch desktop shell ready. roots={config.index_roots}")


if __name__ == "__main__":
    main()

