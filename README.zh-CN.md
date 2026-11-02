# SaFtsearch

SaFtsearch 是一款正在制作中的桌面端极速文件搜索软件，计划使用 Rust 与 Python 共同实现。

> 项目状态：仍在开发中。当前仓库主要包含基础工程框架、早期 Rust 命令行检索逻辑、第一版桌面 UI 外壳，以及学习与实现文档。后续 API、命令、配置字段和桌面端接入方式都可能继续调整。

## 项目目标

- 面向桌面环境提供快速的本地文件搜索能力。
- Rust 负责扫描、索引、查询评分等性能敏感部分。
- Python 负责桌面 UI、配置管理、进程调度和用户交互。
- 搜索内核保持独立，方便 CLI 和 GUI 共同复用。

## 当前能力

- Rust workspace 和 search-core crate 已搭建。
- 已定义基础文件特征量模型。
- 支持目录扫描和排除规则。
- 支持简单文件名搜索与评分。
- 支持扫描结果和搜索结果的 JSON 输出。
- 支持版本化 JSON 索引的构建、查询和状态读取。
- Python 桌面 UI 已具备搜索框、结果表格、目录选择和基础结果操作。
- Python 服务层已能调用 Rust CLI 并解析 `SearchHit` JSON。

## 快速运行

检查 Rust 代码：

```powershell
cargo check
cargo test
```

输出默认配置：

```powershell
cargo run --bin saftsearch-indexer -- config
```

扫描当前项目：

```powershell
cargo run --bin saftsearch-indexer -- scan . --exclude target --exclude .git
```

按文件名搜索：

```powershell
cargo run --bin saftsearch-indexer -- search toml . --limit 10 --exclude target --exclude .git
```

构建并查询持久化索引：

```powershell
cargo run --bin saftsearch-indexer -- index build .saftsearch/index.json . --exclude target --exclude .git --exclude .saftsearch
cargo run --bin saftsearch-indexer -- index status .saftsearch/index.json
cargo run --bin saftsearch-indexer -- index search .saftsearch/index.json toml --limit 10
```

安装 Python 桌面依赖：

```powershell
python -m pip install -e .[desktop]
```

运行 Python 桌面 UI：

```powershell
python -m saftsearch_app.main
```

## 后续计划

1. 先完成最小可运行的 Rust CLI 扫描与搜索。
2. 再让 Python 桌面层调用 Rust 搜索进程。
3. 引入持久化索引，避免每次查询都重新扫描（JSON 索引内核已实现，桌面接入进行中）。
4. 加入文件系统监听，实现增量更新。
5. 优化排序、过滤和桌面端交互。
6. 在文件名搜索稳定后，再扩展全文索引。

运行 Python 功能与安全测试：

```powershell
python -m unittest discover -s python-app\tests -v
```

当前搜索链路会在启动 Rust 之前校验查询、搜索根目录、结果数量和排除
模式。用户输入会作为独立参数传给子进程，并明确使用 `shell=False`，不会
把搜索文本拼接成 shell 命令。

## 说明

本项目目前还不是生产可用版本。当前代码主要用于让整体架构先跑起来，并为后续逐步实现搜索内核、索引存储和桌面 UI 打好基础。
