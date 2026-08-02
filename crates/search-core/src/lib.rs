//! 核心搜索库，支持文件特征索引和多关键字检索。
//!
//! 主要提供：
//! - 基于 [`IndexConfig`] 配置的灵活搜索
//! - 可复用的 [`Searcher`] 结构体，缓存扫描结果以提高多次查询性能
//! - 兼容旧的单根目录快捷搜索（建议迁移到新 API）

pub mod protocol;
pub mod query;
pub mod scanner;

// 重新导出核心类型
pub use protocol::{FileFeature, IndexConfig, SearchHit};

use anyhow::Result;

// ========== 高级 API：可复用的 Searcher ==========

/// 搜索引擎，持有已扫描的文件特征列表，可多次执行查询。
///
/// 适用于需要多次搜索同一目录树的场景，避免重复 I/O。
pub struct Searcher {
    features: Vec<FileFeature>,
}

impl Searcher {
    /// 根据配置扫描所有根目录，构建索引。
    pub fn new(config: &IndexConfig) -> Result<Self> {
        let mut features = Vec::new();
        for root in &config.roots {
            // 调用底层扫描器，收集所有文件特征
            let mut root_features = scanner::scan_root(root, &config.exclude_patterns)?;
            features.append(&mut root_features);
        }
        Ok(Searcher { features })
    }

    /// 在已索引的特征中执行搜索，返回前 `limit` 个匹配结果。
    pub fn search(&self, query_text: &str, limit: usize) -> Vec<SearchHit> {
        query::search(&self.features, query_text, limit)
    }
}

// ========== 一次性搜索函数（便捷调用） ==========

/// 使用指定的配置执行一次搜索（每次都会重新扫描文件系统）。
///
/// 如果需要在同一目录下多次搜索，建议使用 [`Searcher`] 以提高性能。
pub fn search_with_config(
    config: &IndexConfig,
    query_text: &str,
    limit: usize,
) -> Result<Vec<SearchHit>> {
    let searcher = Searcher::new(config)?;
    Ok(searcher.search(query_text, limit))
}

/// [已废弃] 旧版快捷搜索，仅支持单根目录且硬编码排除项。
///
/// 请迁移到 [`search_with_config`] 或 [`Searcher`]。
#[deprecated(note = "use search_with_config or Searcher instead")]
pub fn search(query_text: &str, root: &str, limit: usize) -> Result<Vec<SearchHit>> {
    let config = IndexConfig {
        roots: vec![root.into()],
        exclude_patterns: vec!["target".into(), ".git".into(), "__pycache__".into()],
        follow_symlink: false, // ✅ 修正：字段名为 follow_symlink（单数）
    };
    search_with_config(&config, query_text, limit)
}

// ========== 辅助函数 ==========

/// 生成默认配置（仅用于演示，实际应通过参数构建）
pub fn default_config(root: &str) -> IndexConfig {
    IndexConfig {
        roots: vec![root.into()],
        exclude_patterns: vec!["target".into(), ".git".into(), "__pycache__".into()],
        follow_symlink: false, // ✅ 修正
    }
}
