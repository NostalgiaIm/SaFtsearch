//! 命令行工具，用于扫描目录和搜索文件内容。
//!
//! 支持子命令：
//! - `config`   : 打印默认配置（可扩展为读取配置文件）
//! - `scan`     : 扫描目录并输出文件特征（JSON）
//! - `search`   : 执行搜索，输出匹配结果（JSON）
//!
//! 所有子命令均可通过参数调整排除模式、符号链接行为等。

use anyhow::{bail, Result};
use saftsearch_core::{search_with_config, IndexConfig}; // ✅ 移除未使用的 Searcher
use std::{env, path::PathBuf};

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
            // scan [ROOT] [--exclude PATTERN]... [--follow-symlink]
            let (root, excludes, follow_symlink) = parse_common_options(&args, 2)?;
            Ok(Command::Scan {
                root,
                excludes,
                follow_symlink,
            })
        }
        "search" => {
            // search <QUERY> [ROOT] [--exclude PATTERN]... [--follow-symlink] [--limit N]
            let query = args.get(2).map(String::as_str).unwrap_or("");
            if query.is_empty() {
                bail!("Search query cannot be empty");
            }
            let (root, excludes, follow_symlink) = parse_common_options(&args, 3)?;
            let limit = parse_limit(&args).unwrap_or(50);
            Ok(Command::Search {
                query: query.to_string(),
                root,
                excludes,
                follow_symlink,
                limit,
            })
        }
        other => bail!("Unknown command: {}", other),
    }
}

/// 解析公共选项：根目录、排除模式、是否跟随符号链接
fn parse_common_options(args: &[String], root_pos: usize) -> Result<(PathBuf, Vec<String>, bool)> {
    let root = args.get(root_pos).map(String::as_str).unwrap_or(".");
    let mut excludes = Vec::new();
    let mut follow_symlink = false;

    let mut i = root_pos + 1;
    while i < args.len() {
        match args[i].as_str() {
            "--exclude" => {
                if i + 1 >= args.len() {
                    bail!("Missing pattern for --exclude");
                }
                excludes.push(args[i + 1].clone());
                i += 2;
            }
            "--follow-symlink" => {
                follow_symlink = true;
                i += 1;
            }
            other => {
                eprintln!("Warning: ignoring unknown option: {}", other);
                i += 1;
            }
        }
    }

    Ok((PathBuf::from(root), excludes, follow_symlink))
}

/// 解析 --limit 参数
fn parse_limit(args: &[String]) -> Option<usize> {
    for (i, arg) in args.iter().enumerate() {
        if arg == "--limit" {
            if let Some(val) = args.get(i + 1) {
                if let Ok(n) = val.parse::<usize>() {
                    return Some(n);
                }
            }
        }
    }
    None
}

/// 打印简易帮助信息
fn print_help() {
    eprintln!(
        r#"Usage: saftsearch <COMMAND> [OPTIONS]

Commands:
  config                                 Print default configuration
  scan [ROOT] [--exclude PATTERN]... [--follow-symlink]
                                         Scan directory and output features
  search <QUERY> [ROOT] [--exclude PATTERN]... [--follow-symlink] [--limit N]
                                         Search for query in directory

Examples:
  saftsearch scan ./src --exclude tests
  saftsearch search "fn main" . --limit 10 --follow-symlink
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
        follow_symlink: bool,
    },
    Search {
        query: String,
        root: PathBuf,
        excludes: Vec<String>,
        follow_symlink: bool,
        limit: usize,
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
            follow_symlink,
        } => {
            let config = IndexConfig {
                roots: vec![root],
                exclude_patterns: excludes,
                follow_symlink,
            };
            let root_path = &config.roots[0];
            let features =
                saftsearch_core::scanner::scan_root(root_path, &config.exclude_patterns)?;
            println!("{}", serde_json::to_string_pretty(&features)?);
        }
        Command::Search {
            query,
            root,
            excludes,
            follow_symlink,
            limit,
        } => {
            let config = IndexConfig {
                roots: vec![root],
                exclude_patterns: excludes,
                follow_symlink,
            };
            let hits = search_with_config(&config, &query, limit)?;
            println!("{}", serde_json::to_string_pretty(&hits)?);
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    // 单元测试略
}
