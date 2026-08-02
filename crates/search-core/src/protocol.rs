use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

#[derive(Debug, Clone, Serialize, Deserialize)]
/// 索引范围配置：控制扫描根目录、排除规则和符号链接策略。
pub struct IndexConfig {
    pub roots: Vec<PathBuf>,
    pub exclude_patterns: Vec<String>,
    pub follow_symlink: bool,
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
