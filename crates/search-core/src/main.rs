//! 命令行工具，用于扫描目录和搜索文件名。
//!
//! 支持子命令：
//! - `config`   : 打印默认配置（可扩展为读取配置文件）
//! - `scan`     : 扫描目录并输出文件特征（JSON）
//! - `search`   : 执行直接扫描搜索，输出匹配结果（JSON）
//! - `index`    : 构建、查询和检查持久化 JSON 索引
//!
//! 所有子命令均可通过参数调整排除模式、符号链接行为等。

use anyhow::{bail, Result};
use saftsearch_core::{search_with_config, IndexConfig, IndexStore, JsonIndexStore, StoredIndex};
use std::{env, path::PathBuf};

const MAX_QUERY_LENGTH: usize = 256;
const MAX_EXCLUDE_PATTERN_LENGTH: usize = 256;
const MAX_EXCLUDE_PATTERNS: usize = 64;
const MAX_RESULT_LIMIT: usize = 500;

// ========== 命令行参数解析（手动实现，但支持丰富的选项） ==========

/// 解析命令行参数，返回子命令和对应的参数结构
fn parse_args() -> Result<Command> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        print_help();
        bail!("Missing subcommand");
    }

    let subcommand = args[1].as_str();
    match subcommand {
        "config" => Ok(Command::Config),
        "scan" => {
            // scan [ROOT] [--exclude PATTERN]... [--follow-symlinks]
            let (root, excludes, follow_symlinks) = parse_common_options(&args, 2)?;
            Ok(Command::Scan {
                root,
                excludes,
                follow_symlinks,
            })
        }
        "search" => {
            // search <QUERY> [ROOT] [--exclude PATTERN]... [--follow-symlinks] [--limit N]
            let query = args.get(2).map(String::as_str).unwrap_or("");
            validate_query(query)?;
            let (root, excludes, follow_symlinks) = parse_common_options(&args, 3)?;
            let limit = parse_limit(&args)?.unwrap_or(50);
            Ok(Command::Search {
                query: query.to_string(),
                root,
                excludes,
                follow_symlinks,
                limit,
            })
        }
        "index" => parse_index_command(&args),
        other => bail!("Unknown command: {}", other),
    }
}

fn parse_index_command(args: &[String]) -> Result<Command> {
    let action = args.get(2).map(String::as_str).unwrap_or("");
    match action {
        "build" => {
            let index_path = required_path(args, 3, "index path")?;
            let (root, excludes, follow_symlinks) = parse_common_options(args, 4)?;
            Ok(Command::IndexBuild {
                index_path,
                root,
                excludes,
                follow_symlinks,
            })
        }
        "search" => {
            let index_path = required_path(args, 3, "index path")?;
            let query = args.get(4).map(String::as_str).unwrap_or("");
            validate_query(query)?;
            let limit = parse_index_search_options(args, 5)?;
            Ok(Command::IndexSearch {
                index_path,
                query: query.to_string(),
                limit,
            })
        }
        "status" => {
            let index_path = required_path(args, 3, "index path")?;
            if let Some(value) = args.get(4) {
                bail!("Unexpected argument for index status: {}", value);
            }
            Ok(Command::IndexStatus { index_path })
        }
        "" => bail!("Missing index command (expected build, search, or status)"),
        other => bail!("Unknown index command: {}", other),
    }
}

fn required_path(args: &[String], position: usize, label: &str) -> Result<PathBuf> {
    let value = args
        .get(position)
        .ok_or_else(|| anyhow::anyhow!("Missing {}", label))?;
    if value.is_empty() || value.contains('\0') {
        bail!("Invalid {}", label);
    }
    Ok(PathBuf::from(value))
}

fn parse_index_search_options(args: &[String], start: usize) -> Result<usize> {
    let mut limit = 50;
    let mut i = start;
    while i < args.len() {
        match args[i].as_str() {
            "--limit" => {
                let value = args
                    .get(i + 1)
                    .ok_or_else(|| anyhow::anyhow!("Missing value for --limit"))?;
                limit = parse_limit_value(value)?;
                i += 2;
            }
            option if option.starts_with("--") => bail!("Unknown option: {}", option),
            value => bail!("Unexpected positional argument: {}", value),
        }
    }
    Ok(limit)
}

/// 解析公共选项：根目录、排除模式、是否跟随符号链接
fn parse_common_options(args: &[String], root_pos: usize) -> Result<(PathBuf, Vec<String>, bool)> {
    let mut root = None;
    let mut excludes = Vec::new();
    let mut follow_symlinks = false;

    let mut i = root_pos;
    while i < args.len() {
        match args[i].as_str() {
            "--exclude" => {
                if i + 1 >= args.len() {
                    bail!("Missing pattern for --exclude");
                }
                let pattern = &args[i + 1];
                validate_exclude_pattern(pattern)?;
                if excludes.len() >= MAX_EXCLUDE_PATTERNS {
                    bail!(
                        "At most {} exclude patterns are allowed",
                        MAX_EXCLUDE_PATTERNS
                    );
                }
                excludes.push(pattern.clone());
                i += 2;
            }
            "--follow-symlink" | "--follow-symlinks" => {
                follow_symlinks = true;
                i += 1;
            }
            "--limit" => {
                if i + 1 >= args.len() {
                    bail!("Missing value for --limit");
                }
                i += 2;
            }
            option if option.starts_with("--") => {
                bail!("Unknown option: {}", option);
            }
            value => {
                if root.is_some() {
                    bail!("Unexpected positional argument: {}", value);
                }
                root = Some(PathBuf::from(value));
                i += 1;
            }
        }
    }

    Ok((
        root.unwrap_or_else(|| PathBuf::from(".")),
        excludes,
        follow_symlinks,
    ))
}

/// 解析 --limit 参数
fn parse_limit(args: &[String]) -> Result<Option<usize>> {
    // 只从子命令参数区域读取选项，避免用户搜索 "--limit" 时
    // 被误认为是控制参数。
    let mut i = 3;
    while i < args.len() {
        match args[i].as_str() {
            "--exclude" => {
                // 跳过排除模式的值，即使它看起来像一个选项。
                i += 2;
            }
            "--follow-symlink" | "--follow-symlinks" => i += 1,
            "--limit" => {
                let value = args
                    .get(i + 1)
                    .ok_or_else(|| anyhow::anyhow!("Missing value for --limit"))?;
                return Ok(Some(parse_limit_value(value)?));
            }
            _ => i += 1,
        }
    }
    Ok(None)
}

fn parse_limit_value(value: &str) -> Result<usize> {
    let limit = value
        .parse::<usize>()
        .map_err(|_| anyhow::anyhow!("Invalid --limit value: {}", value))?;
    if !(1..=MAX_RESULT_LIMIT).contains(&limit) {
        bail!("--limit must be between 1 and {}", MAX_RESULT_LIMIT);
    }
    Ok(limit)
}

/// 校验扫描根目录，避免“不存在目录”被误认为是空搜索结果。
fn validate_root(root: &PathBuf) -> Result<()> {
    if !root.exists() {
        bail!("Search root was not found: {}", root.display());
    }
    if !root.is_dir() {
        bail!("Search root is not a directory: {}", root.display());
    }
    Ok(())
}

/// 校验搜索字符串，避免无意义的空查询和异常大的参数进入扫描流程。
fn validate_query(query: &str) -> Result<()> {
    if query.trim().is_empty() {
        bail!("Search query cannot be empty");
    }
    if query.chars().count() > MAX_QUERY_LENGTH {
        bail!("Search query is limited to {} characters", MAX_QUERY_LENGTH);
    }
    if query.contains('\0') {
        bail!("Search query cannot contain a null character");
    }
    Ok(())
}

/// 校验排除模式；空模式会匹配所有路径，必须禁止。
fn validate_exclude_pattern(pattern: &str) -> Result<()> {
    if pattern.trim().is_empty() {
        bail!("Exclude patterns cannot be empty");
    }
    if pattern.chars().count() > MAX_EXCLUDE_PATTERN_LENGTH {
        bail!(
            "Exclude patterns are limited to {} characters",
            MAX_EXCLUDE_PATTERN_LENGTH
        );
    }
    if pattern.contains('\0') {
        bail!("Exclude patterns cannot contain a null character");
    }
    Ok(())
}

/// 打印简易帮助信息
fn print_help() {
    eprintln!(
        r#"Usage: saftsearch <COMMAND> [OPTIONS]

Commands:
  config                                 Print default configuration
  scan [ROOT] [--exclude PATTERN]... [--follow-symlinks]
                                         Scan directory and output features
  search <QUERY> [ROOT] [--exclude PATTERN]... [--follow-symlinks] [--limit N]
                                         Search by scanning the directory
  index build <INDEX_PATH> [ROOT] [--exclude PATTERN]... [--follow-symlinks]
                                         Build a persistent JSON index
  index search <INDEX_PATH> <QUERY> [--limit N]
                                         Search without rescanning the directory
  index status <INDEX_PATH>              Print persistent index metadata

Examples:
  saftsearch scan ./src --exclude tests
  saftsearch search "fn main" . --limit 10 --follow-symlinks
  saftsearch index build .saftsearch/index.json . --exclude target --exclude .git
  saftsearch index search .saftsearch/index.json "report" --limit 10
"#
    );
}

// ========== 命令枚举 ==========

#[derive(Debug)]
enum Command {
    Config,
    Scan {
        root: PathBuf,
        excludes: Vec<String>,
        follow_symlinks: bool,
    },
    Search {
        query: String,
        root: PathBuf,
        excludes: Vec<String>,
        follow_symlinks: bool,
        limit: usize,
    },
    IndexBuild {
        index_path: PathBuf,
        root: PathBuf,
        excludes: Vec<String>,
        follow_symlinks: bool,
    },
    IndexSearch {
        index_path: PathBuf,
        query: String,
        limit: usize,
    },
    IndexStatus {
        index_path: PathBuf,
    },
}

// ========== 主函数 ==========

fn main() -> Result<()> {
    let command = parse_args()?;

    match command {
        Command::Config => {
            let config = saftsearch_core::default_config(".");
            println!("{}", serde_json::to_string_pretty(&config)?);
        }
        Command::Scan {
            root,
            excludes,
            follow_symlinks,
        } => {
            let config = IndexConfig {
                roots: vec![root],
                exclude_patterns: excludes,
                follow_symlinks,
            };
            let root_path = &config.roots[0];
            validate_root(root_path)?;
            let features = saftsearch_core::scanner::scan_root(
                root_path,
                &config.exclude_patterns,
                config.follow_symlinks,
            )?;
            println!("{}", serde_json::to_string_pretty(&features)?);
        }
        Command::Search {
            query,
            root,
            excludes,
            follow_symlinks,
            limit,
        } => {
            let config = IndexConfig {
                roots: vec![root],
                exclude_patterns: excludes,
                follow_symlinks,
            };
            validate_root(&config.roots[0])?;
            let hits = search_with_config(&config, &query, limit)?;
            println!("{}", serde_json::to_string_pretty(&hits)?);
        }
        Command::IndexBuild {
            index_path,
            root,
            excludes,
            follow_symlinks,
        } => {
            validate_root(&root)?;
            let config = IndexConfig {
                roots: vec![root],
                exclude_patterns: excludes,
                follow_symlinks,
            };
            let index = StoredIndex::build(&config)?;
            let store = JsonIndexStore::new(index_path);
            store.save(&index)?;
            println!("{}", serde_json::to_string_pretty(&store.status()?)?);
        }
        Command::IndexSearch {
            index_path,
            query,
            limit,
        } => {
            let store = JsonIndexStore::new(index_path);
            let hits = store.load()?.search(&query, limit);
            println!("{}", serde_json::to_string_pretty(&hits)?);
        }
        Command::IndexStatus { index_path } => {
            let status = JsonIndexStore::new(index_path).status()?;
            println!("{}", serde_json::to_string_pretty(&status)?);
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| value.to_string()).collect()
    }

    #[test]
    fn common_options_allow_options_without_root() {
        let parsed = parse_common_options(&args(&["saftsearch", "scan", "--exclude", "target"]), 2)
            .expect("options without root should parse");

        assert_eq!(parsed.0, PathBuf::from("."));
        assert_eq!(parsed.1, vec!["target".to_string()]);
        assert!(!parsed.2);
    }

    #[test]
    fn common_options_parse_root_and_follow_symlinks() {
        let parsed = parse_common_options(
            &args(&["saftsearch", "scan", "src", "--follow-symlinks"]),
            2,
        )
        .expect("root and follow flag should parse");

        assert_eq!(parsed.0, PathBuf::from("src"));
        assert!(parsed.2);
    }

    #[test]
    fn limit_requires_numeric_value() {
        let result = parse_limit(&args(&[
            "saftsearch",
            "search",
            "toml",
            ".",
            "--limit",
            "x",
        ]));
        assert!(result.is_err());
    }

    #[test]
    fn limit_rejects_zero_and_overflow_values() {
        assert!(parse_limit(&args(&[
            "saftsearch",
            "search",
            "toml",
            ".",
            "--limit",
            "0"
        ]))
        .is_err());
        assert!(parse_limit(&args(&[
            "saftsearch",
            "search",
            "toml",
            ".",
            "--limit",
            "501"
        ]))
        .is_err());
    }

    #[test]
    fn empty_exclude_pattern_is_rejected() {
        let result =
            parse_common_options(&args(&["saftsearch", "scan", ".", "--exclude", "   "]), 2);
        assert!(result.is_err());
    }

    #[test]
    fn query_validation_rejects_empty_and_oversized_input() {
        assert!(validate_query("   ").is_err());
        assert!(validate_query(&"x".repeat(MAX_QUERY_LENGTH + 1)).is_err());
    }

    #[test]
    fn exclude_value_named_like_an_option_is_not_a_limit() {
        let args = args(&[
            "saftsearch",
            "search",
            "report",
            ".",
            "--exclude",
            "--limit",
        ]);
        assert_eq!(parse_limit(&args).unwrap(), None);
    }

    #[test]
    fn index_search_parses_path_query_and_limit() {
        let command = parse_index_command(&args(&[
            "saftsearch",
            "index",
            "search",
            "index.json",
            "report",
            "--limit",
            "12",
        ]))
        .expect("index search should parse");

        match command {
            Command::IndexSearch {
                index_path,
                query,
                limit,
            } => {
                assert_eq!(index_path, PathBuf::from("index.json"));
                assert_eq!(query, "report");
                assert_eq!(limit, 12);
            }
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn index_status_rejects_extra_arguments() {
        let result = parse_index_command(&args(&[
            "saftsearch",
            "index",
            "status",
            "index.json",
            "extra",
        ]));
        assert!(result.is_err());
    }
}
