use crate::protocol::{FileFeature, IndexConfig, SearchHit};
use crate::{query, scanner};
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::io::{BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub const INDEX_FORMAT_VERSION: u32 = 1;

/// 持久化到磁盘的第一版文件名索引。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StoredIndex {
    pub format_version: u32,
    pub created_unix_ms: u128,
    pub config: IndexConfig,
    pub features: Vec<FileFeature>,
}

impl StoredIndex {
    pub fn build(config: &IndexConfig) -> Result<Self> {
        if config.roots.is_empty() {
            bail!("At least one index root is required");
        }

        let mut features = Vec::new();
        let mut seen_paths = HashSet::new();
        for root in &config.roots {
            if !root.exists() {
                bail!("Index root was not found: {}", root.display());
            }
            if !root.is_dir() {
                bail!("Index root is not a directory: {}", root.display());
            }

            for feature in
                scanner::scan_root(root, &config.exclude_patterns, config.follow_symlinks)?
            {
                if seen_paths.insert(feature.path.clone()) {
                    features.push(feature);
                }
            }
        }

        features.sort_by(|left, right| left.path.cmp(&right.path));

        Ok(Self {
            format_version: INDEX_FORMAT_VERSION,
            created_unix_ms: now_unix_ms()?,
            config: config.clone(),
            features,
        })
    }

    pub fn search(&self, query_text: &str, limit: usize) -> Vec<SearchHit> {
        query::search(&self.features, query_text, limit)
    }

    pub fn status(&self, index_path: PathBuf) -> IndexStatus {
        IndexStatus {
            format_version: self.format_version,
            created_unix_ms: self.created_unix_ms,
            index_path,
            roots: self.config.roots.clone(),
            file_count: self.features.len(),
        }
    }
}

/// CLI 和 Python 桌面层都可以直接消费的索引摘要。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct IndexStatus {
    pub format_version: u32,
    pub created_unix_ms: u128,
    pub index_path: PathBuf,
    pub roots: Vec<PathBuf>,
    pub file_count: usize,
}

/// 索引存储边界。后续切换 SQLite 时可保持上层构建与查询流程不变。
pub trait IndexStore {
    fn save(&self, index: &StoredIndex) -> Result<()>;
    fn load(&self) -> Result<StoredIndex>;
}

#[derive(Debug, Clone)]
pub struct JsonIndexStore {
    path: PathBuf,
}

impl JsonIndexStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn status(&self) -> Result<IndexStatus> {
        Ok(self.load()?.status(self.path.clone()))
    }

    fn backup_path(&self) -> PathBuf {
        append_suffix(&self.path, ".bak")
    }

    fn temporary_path(&self) -> Result<PathBuf> {
        let nonce = now_unix_ms()?;
        Ok(append_suffix(
            &self.path,
            &format!(".{}.{}.tmp", std::process::id(), nonce),
        ))
    }
}

impl IndexStore for JsonIndexStore {
    fn save(&self, index: &StoredIndex) -> Result<()> {
        validate_index(index)?;

        if let Some(parent) = self
            .path
            .parent()
            .filter(|path| !path.as_os_str().is_empty())
        {
            fs::create_dir_all(parent).with_context(|| {
                format!("Could not create index directory: {}", parent.display())
            })?;
        }

        let temporary_path = self.temporary_path()?;
        let temporary_file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary_path)
            .with_context(|| {
                format!(
                    "Could not create temporary index: {}",
                    temporary_path.display()
                )
            })?;

        let mut writer = BufWriter::new(temporary_file);
        if let Err(error) = serde_json::to_writer(&mut writer, index)
            .context("Could not serialize search index")
            .and_then(|_| writer.flush().context("Could not flush search index"))
            .and_then(|_| {
                writer
                    .get_ref()
                    .sync_all()
                    .context("Could not synchronize search index")
            })
        {
            drop(writer);
            let _ = fs::remove_file(&temporary_path);
            return Err(error);
        }
        drop(writer);

        let backup_path = self.backup_path();
        if self.path.exists() {
            if backup_path.exists() {
                fs::remove_file(&backup_path).with_context(|| {
                    format!(
                        "Could not remove old index backup: {}",
                        backup_path.display()
                    )
                })?;
            }
            fs::rename(&self.path, &backup_path).with_context(|| {
                format!("Could not back up existing index: {}", self.path.display())
            })?;
        }

        if let Err(error) = fs::rename(&temporary_path, &self.path) {
            if backup_path.exists() && !self.path.exists() {
                let _ = fs::rename(&backup_path, &self.path);
            }
            let _ = fs::remove_file(&temporary_path);
            return Err(error).with_context(|| {
                format!("Could not replace search index: {}", self.path.display())
            });
        }

        if backup_path.exists() {
            fs::remove_file(&backup_path).with_context(|| {
                format!("Could not remove index backup: {}", backup_path.display())
            })?;
        }

        Ok(())
    }

    fn load(&self) -> Result<StoredIndex> {
        let backup_path = self.backup_path();
        let load_path = if self.path.is_file() {
            &self.path
        } else if backup_path.is_file() {
            &backup_path
        } else {
            bail!("Search index was not found: {}", self.path.display());
        };

        let reader =
            BufReader::new(File::open(load_path).with_context(|| {
                format!("Could not open search index: {}", load_path.display())
            })?);
        let index: StoredIndex = serde_json::from_reader(reader)
            .with_context(|| format!("Search index is invalid: {}", load_path.display()))?;
        validate_index(&index)?;
        Ok(index)
    }
}

fn validate_index(index: &StoredIndex) -> Result<()> {
    if index.format_version != INDEX_FORMAT_VERSION {
        bail!(
            "Unsupported index format version: {} (expected {})",
            index.format_version,
            INDEX_FORMAT_VERSION
        );
    }
    if index.config.roots.is_empty() {
        bail!("Search index does not contain any roots");
    }
    Ok(())
}

fn now_unix_ms() -> Result<u128> {
    Ok(SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .context("System clock is before the Unix epoch")?
        .as_millis())
}

fn append_suffix(path: &Path, suffix: &str) -> PathBuf {
    let mut value = path.as_os_str().to_os_string();
    value.push(suffix);
    PathBuf::from(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temporary_directory(name: &str) -> PathBuf {
        let unique = format!(
            "saftsearch-{}-{}-{}",
            name,
            std::process::id(),
            now_unix_ms().expect("time should be available")
        );
        let directory = std::env::temp_dir().join(unique);
        fs::create_dir_all(&directory).expect("temporary directory should be created");
        directory
    }

    #[test]
    fn json_index_round_trip_preserves_searchable_files() {
        let directory = temporary_directory("round-trip");
        let root = directory.join("files");
        fs::create_dir_all(&root).expect("root should be created");
        fs::write(root.join("report.md"), "report").expect("fixture should be written");
        fs::write(root.join("notes.txt"), "notes").expect("fixture should be written");

        let config = IndexConfig {
            roots: vec![root],
            exclude_patterns: Vec::new(),
            follow_symlinks: false,
        };
        let index = StoredIndex::build(&config).expect("index should build");
        let store = JsonIndexStore::new(directory.join("index.json"));
        store.save(&index).expect("index should save");

        let loaded = store.load().expect("index should load");
        let hits = loaded.search("report", 10);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].feature.file_name, "report.md");
        assert_eq!(store.status().expect("status should load").file_count, 2);

        fs::remove_dir_all(directory).expect("temporary directory should be removed");
    }

    #[test]
    fn unsupported_index_version_is_rejected() {
        let directory = temporary_directory("version");
        let path = directory.join("index.json");
        fs::write(
            &path,
            r#"{"format_version":999,"created_unix_ms":0,"config":{"roots":["."],"exclude_patterns":[],"follow_symlinks":false},"features":[]}"#,
        )
        .expect("fixture should be written");

        let error = JsonIndexStore::new(path)
            .load()
            .expect_err("unsupported version should fail");
        assert!(error
            .to_string()
            .contains("Unsupported index format version"));

        fs::remove_dir_all(directory).expect("temporary directory should be removed");
    }
}
