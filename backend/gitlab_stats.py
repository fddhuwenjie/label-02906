#!/usr/bin/env python3
"""
GitLab 代码统计脚本
统计所有项目提交人员的代码量，过滤 merge commits
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

from config import load_config
from exporter import StatsExporter
from gitlab_client import GitLabAPIError, GitLabClient
from stats_collector import CodeStatsCollector

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_date(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期格式错误: {date_str}，请使用 YYYY-MM-DD 格式")


def main():
    parser = argparse.ArgumentParser(
        description="GitLab 代码统计脚本 - 统计项目提交人员的代码量，过滤 merge commits",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 统计用户所属项目
  %(prog)s --url https://gitlab.example.com --token glpat-xxx

  # 统计所有可见项目（需要足够权限）
  %(prog)s --url https://gitlab.example.com --token glpat-xxx --all

  # 指定时间范围
  %(prog)s --url https://gitlab.example.com --token glpat-xxx --since 2025-01-01 --until 2025-12-31

  # 导出 CSV
  %(prog)s --url https://gitlab.example.com --token glpat-xxx -o stats.csv

  # 使用配置文件
  %(prog)s --config ~/.gitlab-stats.json

  # 增量统计（只获取上次之后的新提交）
  %(prog)s --url https://gitlab.example.com --token glpat-xxx --incremental

  # 清除缓存重新统计
  %(prog)s --url https://gitlab.example.com --token glpat-xxx --clear-cache

配置文件格式 (JSON):
  {
    "url": "https://gitlab.example.com",
    "token": "glpat-xxx",
    "all_projects": false,
    "timeout": 30
  }

注意:
  - 默认只统计当前用户有成员身份的项目
  - 使用 --all 可统计所有可见项目（管理员可获取全部项目）
  - Token 需要 read_api 权限
  - 增量模式会在当前目录生成 .gitlab-stats-cache.json 缓存文件
        """
    )
    
    parser.add_argument('--url', help='GitLab 服务器地址')
    parser.add_argument('--token', help='GitLab Private Token (需要 read_api 权限)')
    parser.add_argument('--config', '-c', help='配置文件路径')
    parser.add_argument('--all', action='store_true', dest='all_projects',
                        help='统计所有可见项目，而非仅用户所属项目 (需要足够权限)')
    parser.add_argument('--branch', help='指定分支 (默认统计默认分支)')
    parser.add_argument('--since', type=parse_date, help='开始日期 (YYYY-MM-DD)')
    parser.add_argument('--until', type=parse_date, help='结束日期 (YYYY-MM-DD)')
    parser.add_argument('--output', '-o', help='导出 CSV 文件路径')
    parser.add_argument('--timeout', type=int, help='请求超时时间 (秒, 默认 30)')
    parser.add_argument('--incremental', '-i', action='store_true',
                        help='增量统计模式，只获取上次统计之后的新提交')
    parser.add_argument('--clear-cache', action='store_true',
                        help='清除缓存文件后重新统计')
    parser.add_argument('--cache-dir', help='缓存文件目录 (默认当前目录)')
    parser.add_argument('--verbose', '-v', action='store_true', help='显示详细日志')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # 加载配置文件
    config = load_config(args.config)
    
    # 命令行参数优先于配置文件，配置文件优先于环境变量
    url = args.url or config.get("url") or os.environ.get("GITLAB_URL")
    token = args.token or config.get("token") or os.environ.get("GITLAB_TOKEN")
    all_projects = args.all_projects or config.get("all_projects", False)
    timeout = args.timeout or config.get("timeout", 30)
    
    if not url:
        logger.error("缺少 GitLab URL，请通过 --url 参数、配置文件或 GITLAB_URL 环境变量提供")
        sys.exit(1)
    if not token:
        logger.error("缺少 GitLab Token，请通过 --token 参数、配置文件或 GITLAB_TOKEN 环境变量提供")
        sys.exit(1)
    
    # 清除缓存
    cache_path = Path(args.cache_dir or ".") / CodeStatsCollector.CACHE_FILE
    if args.clear_cache and cache_path.exists():
        cache_path.unlink()
        logger.info(f"已清除缓存文件: {cache_path}")
    
    try:
        client = GitLabClient(
            url, 
            token, 
            timeout=timeout,
            all_projects=all_projects
        )
        collector = CodeStatsCollector(
            client,
            branch=args.branch,
            since=args.since,
            until=args.until,
            incremental=args.incremental,
            cache_dir=args.cache_dir
        )
        
        stats = collector.collect()
        
        if not stats:
            logger.warning("未收集到任何统计数据")
            sys.exit(0)
        
        StatsExporter.to_table(stats)
        
        if args.output:
            StatsExporter.to_csv(stats, args.output)
        
    except ValueError as e:
        logger.error(f"参数错误: {e}")
        sys.exit(1)
    except GitLabAPIError as e:
        logger.error(f"GitLab API 错误: {e}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        logger.error(f"网络请求失败: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("用户中断")
        sys.exit(130)


if __name__ == "__main__":
    main()
