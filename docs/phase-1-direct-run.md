# SaFtsearch 第一阶段可直接运行版

这份文档把讲义中的第一阶段代码整理成“可以按文件写入项目”的版本。目标很小：先实现 Rust 侧的目录扫描和文件名搜索，让项目从框架变成能跑出真实结果的小工具。

本阶段不做 GUI、不做持久化索引、不做全文检索。

## 1. 最终效果

完成后可以运行：

```powershell
cd D:\KAIFA6666\RustProjects\SaFtsearch
cargo run --bin saftsearch-indexer -- scan .
```

输出当前目录下文件特征量 JSON。

也可以运行：

```powershell
cargo run --bin saftsearch-indexer -- search toml .
```

输出文件名中包含 `toml` 的搜索结果。

## 2. 文件结构

需要整理成这样：

```text
crates/search-core/src/
  lib.rs
  main.rs
  protocol.rs
  scanner.rs
  query.rs
```

## 3. 写入 protocol.rs

新建：

```text
crates/search-core/src/protocol.rs
```

内容：

```rust
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

/// 索引范围配置：控制扫描根目录、排除规则和符号链接策略。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexConfig {
    pub roots: Vec<PathBuf>,
    pub exclude_patterns: Vec<String>,
    pub follow_symlinks: bool,
}

/// 文件特征量：搜索、排序、展示都依赖这组基础字段。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileFeature {
    pub path: PathBuf,
    pub file_name: String,
    pub extension: Option<String>,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
}

impl FileFeature {
    pub fn from_path(path: impl AsRef<Path>) -> anyhow::Result<Self> {
        let path = path.as_ref();
        let metadata = path.metadata()?;

        let file_name = path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("")
            .to_string();

        let extension = path
            .extension()
            .and_then(|ext| ext.to_str())
            .map(str::to_lowercase);

        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());

        Ok(Self {
            path: path.to_path_buf(),
            file_name,
            extension,
            size_bytes: metadata.len(),
            modified_unix_ms,
        })
    }
}

/// 搜索命中结果：score 越高，越应该排在前面。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchHit {
    pub feature: FileFeature,
    pub score: f32,
}
```

## 4. 写入 scanner.rs

新建：

```text
crates/search-core/src/scanner.rs
```

内容：

```rust
use crate::protocol::FileFeature;
use anyhow::Result;
use std::path::Path;
use walkdir::WalkDir;

pub fn scan_root(root: impl AsRef<Path>, exclude_patterns: &[String]) -> Result<Vec<FileFeature>> {
    let mut features = Vec::new();

    for entry in WalkDir::new(root) {
        let entry = match entry {
            Ok(entry) => entry,
            Err(_) => continue, // 单个路径失败不影响整体扫描。
        };

        let path_text = entry.path().to_string_lossy();
        if exclude_patterns.iter().any(|pattern| path_text.contains(pattern)) {
            continue;
        }

        if !entry.file_type().is_file() {
            continue;
        }

        if let Ok(feature) = FileFeature::from_path(entry.path()) {
            features.push(feature);
        }
    }

    Ok(features)
}
```

## 5. 写入 query.rs

新建：

```text
crates/search-core/src/query.rs
```

内容：

```rust
use crate::protocol::{FileFeature, SearchHit};

pub fn score_file(feature: &FileFeature, query: &str) -> Option<f32> {
    let query = query.trim().to_lowercase();
    if query.is_empty() {
        return None;
    }

    let file_name = feature.file_name.to_lowercase();

    let mut score = if file_name == query {
        100.0
    } else if file_name.starts_with(&query) {
        80.0
    } else if file_name.contains(&query) {
        50.0
    } else {
        return None;
    };

    if let Some(extension) = &feature.extension {
        if extension == &query {
            score += 10.0;
        }
    }

    Some(score)
}

pub fn search(features: &[FileFeature], query: &str, limit: usize) -> Vec<SearchHit> {
    let mut hits: Vec<SearchHit> = features
        .iter()
        .filter_map(|feature| {
            score_file(feature, query).map(|score| SearchHit {
                feature: feature.clone(),
                score,
            })
        })
        .collect();

    hits.sort_by(|a, b| b.score.total_cmp(&a.score));
    hits.truncate(limit);
    hits
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn feature(name: &str) -> FileFeature {
        FileFeature {
            path: PathBuf::from(name),
            file_name: name.to_string(),
            extension: name.rsplit_once('.').map(|(_, ext)| ext.to_string()),
            size_bytes: 0,
            modified_unix_ms: None,
        }
    }

    #[test]
    fn exact_match_scores_higher_than_contains_match() {
        let exact = score_file(&feature("report.pdf"), "report.pdf").unwrap();
        let contains = score_file(&feature("my-report.pdf"), "report").unwrap();
        assert!(exact > contains);
    }
}
```

## 6. 替换 lib.rs

修改：

```text
crates/search-core/src/lib.rs
```

内容：

```rust
pub mod protocol;
pub mod query;
pub mod scanner;

pub use protocol::{FileFeature, IndexConfig, SearchHit};

pub fn search(query_text: &str, root: &str, limit: usize) -> anyhow::Result<Vec<SearchHit>> {
    let config = IndexConfig {
        roots: vec![root.into()],
        exclude_patterns: vec!["target".into(), ".git".into(), "__pycache__".into()],
        follow_symlinks: false,
    };

    let features = scanner::scan_root(&config.roots[0], &config.exclude_patterns)?;
    Ok(query::search(&features, query_text, limit))
}
```

## 7. 替换 main.rs

修改：

```text
crates/search-core/src/main.rs
```

内容：

```rust
use anyhow::{bail, Result};
use saftsearch_core::{scanner, IndexConfig};
use std::{env, path::PathBuf};

fn main() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    let command = args.get(1).map(String::as_str).unwrap_or("config");

    match command {
        "config" => print_config(),
        "scan" => {
            let root = args.get(2).map(String::as_str).unwrap_or(".");
            run_scan(root)
        }
        "search" => {
            let query = args.get(2).map(String::as_str).unwrap_or("");
            let root = args.get(3).map(String::as_str).unwrap_or(".");
            run_search(query, root)
        }
        other => bail!("unknown command: {other}"),
    }
}

fn default_config(root: &str) -> IndexConfig {
    IndexConfig {
        roots: vec![PathBuf::from(root)],
        exclude_patterns: vec!["target".into(), ".git".into(), "__pycache__".into()],
        follow_symlinks: false,
    }
}

fn print_config() -> Result<()> {
    let config = default_config(".");
    println!("{}", serde_json::to_string_pretty(&config)?);
    Ok(())
}

fn run_scan(root: &str) -> Result<()> {
    let config = default_config(root);
    let features = scanner::scan_root(&config.roots[0], &config.exclude_patterns)?;
    println!("{}", serde_json::to_string_pretty(&features)?);
    Ok(())
}

fn run_search(query: &str, root: &str) -> Result<()> {
    let hits = saftsearch_core::search(query, root, 50)?;
    println!("{}", serde_json::to_string_pretty(&hits)?);
    Ok(())
}
```

## 8. 运行检查

格式检查：

```powershell
cargo fmt
```

测试：

```powershell
cargo test
```

扫描：

```powershell
cargo run --bin saftsearch-indexer -- scan .
```

搜索：

```powershell
cargo run --bin saftsearch-indexer -- search toml .
```

Python 入口仍然可以单独运行：

```powershell
cd D:\KAIFA6666\RustProjects\SaFtsearch\python-app\src
python -m saftsearch_app.main
```

## 9. 第一阶段学到的内容

这一阶段对应的官方知识点：

- Rust 变量、函数、控制流。
- Rust 结构体、`Option`、`Result`。
- Rust 模块拆分。
- Rust 集合、字符串、迭代器。
- Rust 文件系统元数据读取。
- JSON 序列化。

你完成后得到的项目能力：

- 能扫描目录。
- 能提取文件特征量。
- 能根据文件名搜索。
- 能输出 JSON。

下一阶段再做：

- Python 调用 Rust。
- 持久化索引。
- 搜索结果展示。
