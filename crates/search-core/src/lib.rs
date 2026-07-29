mod protocol;

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// 索引范围配置：控制哪些目录会被扫描，哪些路径应跳过。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexConfig {
    pub roots: Vec<PathBuf>,
    pub exclude_patterns: Vec<String>,
    pub follow_symlinks: bool,
}

/// 文件特征量：用于排序、过滤和后续扩展全文索引。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileFeature {
    pub path: PathBuf,
    pub file_name: String,
    pub extension: Option<String>,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
}

/// 搜索命中结果：score 越高，越应靠前展示。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchHit {
    pub feature: FileFeature,
    pub score: f32,
}

pub fn search(_query: &str, _limit: usize) -> anyhow::Result<Vec<SearchHit>> {
    // TODO: 接入倒排索引 / 模糊匹配索引后返回真实结果。
    Ok(Vec::new())
}
