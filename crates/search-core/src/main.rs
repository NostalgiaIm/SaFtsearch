use anyhow::Result;
use saftsearch_core::IndexConfig;
use std::path::PathBuf;

fn main() -> Result<()> {
    // 最小 CLI 占位：后续扩展为 build-index / search / watch 子命令。
    let config = IndexConfig {
        roots: vec![PathBuf::from(".")],
        exclude_patterns: vec!["target".into(), ".git".into()],
        follow_symlinks: false,

    };




    println!("{}", serde_json::to_string_pretty(&config)?);
    Ok(())
}
