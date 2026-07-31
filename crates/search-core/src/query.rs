use crate::protocol::{FileFeature, SearchHit};

/// 对单个文件特征进行相关性评分。
///
/// # 参数
/// - `feature`: 文件特征，包含文件名、扩展名等信息。
/// - `query`: 用户输入的查询字符串（会被自动修剪并转为小写）。
///
/// # 返回值
/// 返回 `Some(score)`，其中 `score` 为浮点数，表示匹配程度；
/// 如果查询为空或文件名完全不匹配，则返回 `None`。
///
/// # 评分规则
/// 1. 文件名完全等于查询 → 100 分
/// 2. 文件名以查询开头 → 80 分
/// 3. 文件名包含查询 → 50 分
/// 4. 否则不匹配，返回 `None`
/// 5. 若文件扩展名等于查询，额外加 10 分
pub fn score_file(feature: &FileFeature, query: &str) -> Option<f32> {
    let query = query.trim().to_lowercase();
    if query.is_empty() {
        return None;
    }

    let file_name = feature.file_name.to_lowercase();

    // 基础评分：按匹配精确度分级
    let mut score = if file_name == query {
        100.0
    } else if file_name.starts_with(&query) {
        80.0
    } else if file_name.contains(&query) {
        50.0
    } else {
        return None;
    };

    // 扩展名完全匹配时给予额外加分
    if let Some(extension) = &feature.extension {
        if extension == &query {
            score += 10.0;
        }
    }

    Some(score)
}

/// 在文件特征列表中执行搜索，返回按相关性降序排列的结果。
///
/// # 参数
/// - `features`: 待搜索的文件特征列表。
/// - `query`: 搜索查询字符串。
/// - `limit`: 返回的最大结果数量。
///
/// # 返回值
/// 返回一个 `SearchHit` 向量，每个命中项包含文件特征和对应的评分。
/// 结果按评分从高到低排序，最多返回 `limit` 条。
///
/// # 算法简述
/// 对每个文件调用 `score_file` 计算得分，过滤掉不匹配项，
/// 然后按得分降序排序，并截断到指定数量。
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

    // 按得分降序排列（使用 total_cmp 确保 NaN 安全）
    hits.sort_by(|a, b| b.score.total_cmp(&a.score));
    hits.truncate(limit);
    hits
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// 辅助函数：根据文件名创建一个 `FileFeature` 实例，用于测试。
    fn feature(name: &str) -> FileFeature {
        FileFeature {
            path: PathBuf::from(name),
            file_name: name.to_string(),
            extension: name.rsplit_once('.').map(|(_, ext)| ext.to_string()),
            size_bytes: 0,
            modified_unix_ms: None,
        }
    }

    /// 测试精确匹配的得分应高于仅包含匹配的得分。
    #[test]
    fn exact_match_scores_higher_than_contains_match() {
        let exact = score_file(&feature("report.pdf"), "report.pdf").unwrap();
        let contains = score_file(&feature("my-report.pdf"), "report").unwrap();
        assert!(exact > contains);
    }
}