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