"""数据模型定义"""

from dataclasses import dataclass, field
from typing import Dict, Set


@dataclass
class AuthorStats:
    """作者统计数据"""
    author_name: str = ""
    author_email: str = ""
    additions: int = 0
    deletions: int = 0
    total_commits: int = 0
    projects: Set[str] = field(default_factory=set)


@dataclass
class CacheData:
    """缓存数据结构"""
    last_updated: str = ""
    projects_cursor: Dict[str, str] = field(default_factory=dict)  # project_id -> last_commit_sha
    author_stats: Dict[str, dict] = field(default_factory=dict)
