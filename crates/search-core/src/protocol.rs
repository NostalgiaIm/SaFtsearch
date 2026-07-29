use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;
# [derive(Debug,Clone,Serialize,Deserialize)]

/// 索引范围配置：控制扫描根目录、排除规则和符号链接策略。
pub struct IndexConfig {
    pub roots : Vec<PathBuf>,
    pub exclude_patterns:Vec<String>,
    pub follow_symlink:bool,
}

/// 文件特征量：搜索、排序、展示都依赖这组基础字段。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileFeature {
    pub path :PathBuf ,
    pub file_name : String ,
    pub extension :Option<u128> ,
    pub size_bytes : u64,
    pub modified_unix_ms :Option<u128>
}

