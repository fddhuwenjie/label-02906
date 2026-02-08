#!/usr/bin/env python3
"""
GitLab 代码统计脚本
统计所有项目提交人员的代码量，过滤 merge commits
"""

import argparse
import csv
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class AuthorStats:
    """作者统计数据"""
    author_name: str
    author_email: str
    additions: int = 0
    deletions: int = 0
    total_commits: int = 0
    projects: Set[str] = field(default_factory=set)


class GitLabAPIError(Exception):
    """GitLab API 错误"""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"[{status_code}] {message}")


class GitLabClient:
    """GitLab API 客户端"""
    
    def __init__(self, gitlab_url: str, private_token: str, timeout: int = 30, all_projects: bool = False):
        self._validate_url(gitlab_url)
        self._validate_token(private_token)
        
        self.gitlab_url = gitlab_url.rstrip('/')
        self.private_token = private_token
        self.timeout = timeout
        self.all_projects = all_projects
        self.session = self._create_session()
    
    @staticmethod
    def _validate_url(url: str) -> None:
        if not url:
            raise ValueError("GitLab URL 不能为空")
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"无效的 GitLab URL: {url}")
        if parsed.scheme not in ('http', 'https'):
            raise ValueError(f"URL 必须使用 http 或 https 协议: {url}")
    
    @staticmethod
    def _validate_token(token: str) -> None:
        if not token or not token.strip():
            raise ValueError("Private Token 不能为空")
    
    def _create_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({"PRIVATE-TOKEN": self.private_token})
        return session
    
    def _handle_error_response(self, response: requests.Response, context: str) -> None:
        """处理错误响应，提供详细错误信息"""
        status = response.status_code
        try:
            error_body = response.json()
            error_msg = error_body.get("message") or error_body.get("error") or str(error_body)
        except Exception:
            error_msg = response.text[:200] if response.text else "无响应内容"
        
        if status == 401:
            raise GitLabAPIError(status, f"认证失败: Token 无效或已过期 ({context})")
        elif status == 403:
            raise GitLabAPIError(status, f"权限不足: Token 缺少必要权限，请确保有 read_api 权限 ({context})")
        elif status == 404:
            raise GitLabAPIError(status, f"资源不存在: {context}")
        else:
            raise GitLabAPIError(status, f"{context} - {error_msg}")
    
    def _request(self, endpoint: str, params: Optional[dict] = None, context: str = "API 请求", max_pages: int = 1000) -> List[dict]:
        """发送 API 请求，支持分页"""
        url = f"{self.gitlab_url}/api/v4{endpoint}"
        results = []
        page = 1
        per_page = 100
        
        while page <= max_pages:
            request_params = {"page": page, "per_page": per_page}
            if params:
                request_params.update(params)
            
            try:
                logger.debug(f"请求: {url}, 页码: {page}")
                response = self.session.get(url, params=request_params, timeout=self.timeout)
            except requests.exceptions.Timeout:
                raise GitLabAPIError(0, f"请求超时 ({self.timeout}s): {context}")
            except requests.exceptions.ConnectionError as e:
                raise GitLabAPIError(0, f"连接失败: {e}")
            
            if response.status_code != 200:
                self._handle_error_response(response, context)
            
            data = response.json()
            if not data:
                break
            
            results.extend(data)
            page += 1
        
        if page > max_pages:
            logger.warning(f"已达到最大页数限制 ({max_pages})，可能存在数据截断: {context}")
        
        return results

    def get_projects(self) -> List[dict]:
        """
        获取项目列表
        
        - all_projects=False (默认): 获取当前用户有成员身份的项目
        - all_projects=True: 获取所有可见项目（需要足够权限，管理员可获取全部）
        """
        logger.info("正在获取项目列表...")
        
        if self.all_projects:
            # 获取所有可见项目（管理员可获取全部，普通用户获取有权限查看的）
            params = {}
            logger.info("模式: 获取所有可见项目")
        else:
            # 只获取当前用户是成员的项目
            params = {"membership": "true"}
            logger.info("模式: 获取用户所属项目 (membership=true)")
        
        projects = self._request("/projects", params, "获取项目列表")
        
        if not projects:
            logger.warning("未获取到任何项目，请检查:")
            logger.warning("  1. Token 是否有 read_api 权限")
            logger.warning("  2. 用户是否有项目成员身份")
            if self.all_projects:
                logger.warning("  3. 使用 --all 参数需要足够的权限查看项目")
        else:
            logger.info(f"共获取到 {len(projects)} 个项目")
        
        return projects
    
    def get_commits(
        self, 
        project_id: int,
        project_name: str,
        branch: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None
    ) -> List[dict]:
        """获取项目提交记录"""
        params = {"with_stats": "true"}
        if branch:
            params["ref_name"] = branch
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        
        try:
            return self._request(
                f"/projects/{project_id}/repository/commits", 
                params,
                f"获取项目 '{project_name}' 提交记录"
            )
        except GitLabAPIError as e:
            if e.status_code == 403:
                logger.warning(f"跳过项目 '{project_name}': 无权限访问仓库")
            elif e.status_code == 404:
                logger.warning(f"跳过项目 '{project_name}': 仓库为空或不存在")
            else:
                logger.warning(f"跳过项目 '{project_name}': {e.message}")
            return []


class CodeStatsCollector:
    """代码统计收集器"""
    
    def __init__(
        self, 
        client: GitLabClient,
        branch: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None
    ):
        self.client = client
        self.branch = branch
        self.since = since
        self.until = until
        self.author_stats: Dict[str, AuthorStats] = defaultdict(
            lambda: AuthorStats(author_name="", author_email="")
        )
    
    @staticmethod
    def is_merge_commit(commit: dict) -> bool:
        """
        判断是否为 merge commit
        - parent_ids 数量 > 1 表示是 merge commit
        """
        if not commit or not isinstance(commit, dict):
            return False
        parent_ids = commit.get("parent_ids") or []
        return len(parent_ids) > 1
    
    def collect(self) -> Dict[str, AuthorStats]:
        """收集所有项目的代码统计"""
        projects = self.client.get_projects()
        
        if not projects:
            return {}
        
        for i, project in enumerate(projects, 1):
            project_id = project["id"]
            project_name = project["name"]
            logger.info(f"[{i}/{len(projects)}] 正在统计项目: {project_name}")
            
            commits = self.client.get_commits(
                project_id,
                project_name,
                branch=self.branch,
                since=self.since,
                until=self.until
            )
            
            valid_commits = 0
            merge_commits = 0
            
            for commit in commits:
                if self.is_merge_commit(commit):
                    merge_commits += 1
                    continue
                
                valid_commits += 1
                author_name = commit.get("author_name", "Unknown")
                author_email = commit.get("author_email", "")
                stats = commit.get("stats") or {}
                
                key = author_email or author_name
                author = self.author_stats[key]
                author.author_name = author_name
                author.author_email = author_email
                author.additions += stats.get("additions", 0)
                author.deletions += stats.get("deletions", 0)
                author.total_commits += 1
                author.projects.add(project_name)
            
            if commits:
                logger.info(f"  有效提交: {valid_commits}, 过滤 merge: {merge_commits}")
        
        return dict(self.author_stats)


class StatsExporter:
    """统计结果导出器"""
    
    @staticmethod
    def to_csv(stats: Dict[str, AuthorStats], output_file: str) -> None:
        sorted_stats = sorted(
            stats.values(), 
            key=lambda x: x.additions + x.deletions, 
            reverse=True
        )
        
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                '作者', '邮箱', '新增行数', '删除行数', 
                '净增行数', '提交次数', '参与项目数', '参与项目'
            ])
            for author in sorted_stats:
                writer.writerow([
                    author.author_name,
                    author.author_email,
                    author.additions,
                    author.deletions,
                    author.additions - author.deletions,
                    author.total_commits,
                    len(author.projects),
                    '; '.join(sorted(author.projects))
                ])
        
        logger.info(f"统计结果已导出到: {output_file}")
    
    @staticmethod
    def to_table(stats: Dict[str, AuthorStats]) -> None:
        sorted_stats = sorted(
            stats.values(), 
            key=lambda x: x.additions + x.deletions, 
            reverse=True
        )
        
        print("\n" + "=" * 100)
        print(f"{'作者':<20} {'邮箱':<30} {'新增':>10} {'删除':>10} {'净增':>10} {'提交':>8} {'项目数':>8}")
        print("=" * 100)
        
        total_additions = 0
        total_deletions = 0
        total_commits = 0
        
        for author in sorted_stats:
            net = author.additions - author.deletions
            print(f"{author.author_name:<20} {author.author_email:<30} "
                  f"{author.additions:>10} {author.deletions:>10} {net:>10} "
                  f"{author.total_commits:>8} {len(author.projects):>8}")
            total_additions += author.additions
            total_deletions += author.deletions
            total_commits += author.total_commits
        
        print("=" * 100)
        print(f"{'合计':<20} {'':<30} {total_additions:>10} {total_deletions:>10} "
              f"{total_additions - total_deletions:>10} {total_commits:>8}")
        print("=" * 100)


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

注意:
  - 默认只统计当前用户有成员身份的项目
  - 使用 --all 可统计所有可见项目（管理员可获取全部项目）
  - Token 需要 read_api 权限
        """
    )
    
    parser.add_argument('--url', required=True, help='GitLab 服务器地址')
    parser.add_argument('--token', required=True, help='GitLab Private Token (需要 read_api 权限)')
    parser.add_argument('--all', action='store_true', dest='all_projects',
                        help='统计所有可见项目，而非仅用户所属项目 (需要足够权限)')
    parser.add_argument('--branch', help='指定分支 (默认统计默认分支)')
    parser.add_argument('--since', type=parse_date, help='开始日期 (YYYY-MM-DD)')
    parser.add_argument('--until', type=parse_date, help='结束日期 (YYYY-MM-DD)')
    parser.add_argument('--output', '-o', help='导出 CSV 文件路径')
    parser.add_argument('--timeout', type=int, default=30, help='请求超时时间 (秒, 默认 30)')
    parser.add_argument('--verbose', '-v', action='store_true', help='显示详细日志')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        client = GitLabClient(
            args.url, 
            args.token, 
            timeout=args.timeout,
            all_projects=args.all_projects
        )
        collector = CodeStatsCollector(
            client,
            branch=args.branch,
            since=args.since,
            until=args.until
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
