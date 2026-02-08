"""代码统计收集器"""

import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from gitlab_client import GitLabClient
from models import AuthorStats, CacheData

logger = logging.getLogger(__name__)

# 缓存数据版本号，模型结构变更时需要更新
CACHE_VERSION = "1.0"


class CodeStatsCollector:
    """代码统计收集器"""
    
    CACHE_FILE = ".gitlab-stats-cache.json"
    
    def __init__(
        self, 
        client: GitLabClient,
        branch: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        incremental: bool = False,
        cache_dir: Optional[str] = None
    ):
        self.client = client
        self.branch = branch
        self.since = since
        self.until = until
        self.incremental = incremental
        self.cache_path = Path(cache_dir or ".") / self.CACHE_FILE
        self.author_stats: Dict[str, AuthorStats] = defaultdict(AuthorStats)
        self.cache: CacheData = CacheData()
        
        if incremental:
            self._load_cache()
    
    def _load_cache(self) -> None:
        """加载增量缓存"""
        if not self.cache_path.exists():
            logger.info("未找到缓存文件，将进行全量统计")
            return
        
        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 校验缓存版本
            cache_version = data.get("version", "")
            if cache_version != CACHE_VERSION:
                logger.warning(f"缓存版本不兼容 (缓存: {cache_version}, 当前: {CACHE_VERSION})，将进行全量统计")
                return
            
            self.cache.last_updated = data.get("last_updated", "")
            self.cache.projects_cursor = data.get("projects_cursor", {})
            
            # 恢复已有的统计数据
            for key, stats_dict in data.get("author_stats", {}).items():
                # 校验必要字段存在
                if not isinstance(stats_dict, dict):
                    logger.warning(f"跳过无效的统计数据: {key}")
                    continue
                
                stats = AuthorStats(
                    author_name=stats_dict.get("author_name", ""),
                    author_email=stats_dict.get("author_email", ""),
                    additions=stats_dict.get("additions", 0),
                    deletions=stats_dict.get("deletions", 0),
                    total_commits=stats_dict.get("total_commits", 0),
                    projects=set(stats_dict.get("projects", []))
                )
                self.author_stats[key] = stats
            
            logger.info(f"已加载缓存，上次更新: {self.cache.last_updated}")
            logger.info(f"缓存中有 {len(self.author_stats)} 位作者的统计数据")
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"缓存加载失败: {e}，将进行全量统计")
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"缓存数据格式异常: {e}，将进行全量统计")

    def _save_cache(self) -> None:
        """保存增量缓存"""
        if not self.incremental:
            return
        
        cache_data = {
            "version": CACHE_VERSION,
            "last_updated": datetime.now().isoformat(),
            "projects_cursor": self.cache.projects_cursor,
            "author_stats": {
                key: {
                    "author_name": stats.author_name,
                    "author_email": stats.author_email,
                    "additions": stats.additions,
                    "deletions": stats.deletions,
                    "total_commits": stats.total_commits,
                    "projects": list(stats.projects)
                }
                for key, stats in self.author_stats.items()
            }
        }
        
        try:
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)
            logger.info(f"缓存已保存到: {self.cache_path}")
        except IOError as e:
            logger.warning(f"缓存保存失败: {e}")
    
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
    
    @staticmethod
    def is_non_code_commit(commit: dict) -> bool:
        """
        判断是否为非代码提交（需要过滤的提交类型）
        
        检测以下类型:
        - Merge commit: parent_ids > 1
        - Squash merge: 标题包含 "Merge branch" 或 "Squashed commit"
        - Revert commit: 标题以 "Revert" 开头
        - Cherry-pick: 标题包含 "(cherry picked from"
        """
        if not commit or not isinstance(commit, dict):
            return False
        
        # 检查 merge commit
        parent_ids = commit.get("parent_ids") or []
        if len(parent_ids) > 1:
            return True
        
        # 检查提交标题
        title = commit.get("title", "") or ""
        message = commit.get("message", "") or ""
        
        # Revert commit
        if title.startswith("Revert "):
            return True
        
        # Squash merge (GitLab 默认格式)
        if re.match(r"^Merge branch '.+' into", title):
            return True
        if "Squashed commit of the following:" in message:
            return True
        
        # Cherry-pick
        if "(cherry picked from commit" in message:
            return True
        
        return False
    
    def collect(self) -> Dict[str, AuthorStats]:
        """收集所有项目的代码统计"""
        projects = self.client.get_projects()
        
        if not projects:
            return dict(self.author_stats)
        
        for i, project in enumerate(projects, 1):
            project_id = project["id"]
            project_name = project["name"]
            project_key = str(project_id)
            
            # 增量模式：获取上次处理到的 commit SHA
            stop_at_sha = None
            if self.incremental:
                stop_at_sha = self.cache.projects_cursor.get(project_key)
                if stop_at_sha:
                    logger.info(f"[{i}/{len(projects)}] 增量统计项目: {project_name} (从 {stop_at_sha[:8]}... 之后)")
                else:
                    logger.info(f"[{i}/{len(projects)}] 全量统计项目: {project_name}")
            else:
                logger.info(f"[{i}/{len(projects)}] 正在统计项目: {project_name}")
            
            commits = self.client.get_commits(
                project_id,
                project_name,
                branch=self.branch,
                since=self.since,
                until=self.until,
                stop_at_sha=stop_at_sha
            )
            
            valid_commits = 0
            filtered_commits = 0
            first_commit_sha = None
            
            for commit in commits:
                # 记录第一个 commit 的 SHA（用于下次增量）
                if first_commit_sha is None:
                    first_commit_sha = commit.get("id")
                
                if self.is_non_code_commit(commit):
                    filtered_commits += 1
                    continue
                
                valid_commits += 1
                author_name = commit.get("author_name", "Unknown")
                author_email = commit.get("author_email", "")
                stats = commit.get("stats") or {}
                
                # 校验 stats 数据完整性
                if not isinstance(stats, dict):
                    logger.debug(f"跳过无效的 stats 数据: commit {commit.get('id', 'unknown')[:8]}")
                    stats = {}
                
                key = author_email or author_name
                author = self.author_stats[key]
                author.author_name = author_name
                author.author_email = author_email
                author.additions += stats.get("additions", 0) or 0
                author.deletions += stats.get("deletions", 0) or 0
                author.total_commits += 1
                author.projects.add(project_name)
            
            # 更新游标（记录最新的 commit SHA）
            if first_commit_sha:
                self.cache.projects_cursor[project_key] = first_commit_sha
            
            if commits:
                logger.info(f"  有效提交: {valid_commits}, 过滤非代码提交: {filtered_commits}")
        
        # 保存缓存
        self._save_cache()
        
        return dict(self.author_stats)
