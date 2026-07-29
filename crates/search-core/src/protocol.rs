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
