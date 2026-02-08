"""单元测试：CodeStatsCollector 核心过滤逻辑"""

import pytest
import sys
import os

# 添加 backend 目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stats_collector import CodeStatsCollector


class TestIsMergeCommit:
    """测试 is_merge_commit 方法"""
    
    def test_normal_commit_single_parent(self):
        """普通提交（单个 parent）不是 merge commit"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": "feat: add new feature"
        }
        assert CodeStatsCollector.is_merge_commit(commit) is False
    
    def test_merge_commit_multiple_parents(self):
        """多个 parent 的提交是 merge commit"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456", "ghi789"],
            "title": "Merge branch 'feature' into 'main'"
        }
        assert CodeStatsCollector.is_merge_commit(commit) is True
    
    def test_empty_parent_ids(self):
        """空 parent_ids 不是 merge commit"""
        commit = {
            "id": "abc123",
            "parent_ids": [],
            "title": "Initial commit"
        }
        assert CodeStatsCollector.is_merge_commit(commit) is False
    
    def test_none_parent_ids(self):
        """parent_ids 为 None 不是 merge commit"""
        commit = {
            "id": "abc123",
            "parent_ids": None,
            "title": "Some commit"
        }
        assert CodeStatsCollector.is_merge_commit(commit) is False
    
    def test_missing_parent_ids(self):
        """缺少 parent_ids 字段不是 merge commit"""
        commit = {
            "id": "abc123",
            "title": "Some commit"
        }
        assert CodeStatsCollector.is_merge_commit(commit) is False
    
    def test_none_commit(self):
        """None 输入返回 False"""
        assert CodeStatsCollector.is_merge_commit(None) is False
    
    def test_empty_dict(self):
        """空字典返回 False"""
        assert CodeStatsCollector.is_merge_commit({}) is False
    
    def test_invalid_type(self):
        """非字典类型返回 False"""
        assert CodeStatsCollector.is_merge_commit("not a dict") is False
        assert CodeStatsCollector.is_merge_commit(123) is False
        assert CodeStatsCollector.is_merge_commit([]) is False


class TestIsNonCodeCommit:
    """测试 is_non_code_commit 方法"""
    
    def test_normal_commit(self):
        """普通代码提交不应被过滤"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": "feat: add user authentication",
            "message": "feat: add user authentication\n\nImplemented OAuth2 login flow"
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is False
    
    def test_merge_commit_by_parents(self):
        """多 parent 的 merge commit 应被过滤"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456", "ghi789"],
            "title": "Merge branch 'feature' into 'main'",
            "message": "Merge branch 'feature' into 'main'"
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is True
    
    def test_revert_commit(self):
        """Revert 提交应被过滤"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": "Revert \"feat: add broken feature\"",
            "message": "Revert \"feat: add broken feature\"\n\nThis reverts commit xyz789."
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is True
    
    def test_revert_without_space(self):
        """Revert 后没有空格的不应被过滤"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": "Reverted some changes manually",
            "message": "Reverted some changes manually"
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is False
    
    def test_squash_merge_gitlab_format(self):
        """GitLab 格式的 squash merge 应被过滤"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": "Merge branch 'feature-xyz' into main",
            "message": "Merge branch 'feature-xyz' into main"
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is True
    
    def test_squash_merge_with_quotes(self):
        """带引号的 squash merge 应被过滤"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": "Merge branch 'develop' into 'main'",
            "message": "Merge branch 'develop' into 'main'"
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is True
    
    def test_squashed_commit_message(self):
        """包含 Squashed commit 标记的应被过滤"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": "Feature: complete user module",
            "message": "Feature: complete user module\n\nSquashed commit of the following:\n\ncommit abc\ncommit def"
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is True
    
    def test_cherry_pick_commit(self):
        """Cherry-pick 提交应被过滤"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": "fix: critical bug fix",
            "message": "fix: critical bug fix\n\n(cherry picked from commit xyz789abc123)"
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is True
    
    def test_none_commit(self):
        """None 输入返回 False"""
        assert CodeStatsCollector.is_non_code_commit(None) is False
    
    def test_empty_dict(self):
        """空字典返回 False"""
        assert CodeStatsCollector.is_non_code_commit({}) is False
    
    def test_invalid_type(self):
        """非字典类型返回 False"""
        assert CodeStatsCollector.is_non_code_commit("not a dict") is False
    
    def test_none_title_and_message(self):
        """title 和 message 为 None 时不应崩溃"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": None,
            "message": None
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is False
    
    def test_missing_title_and_message(self):
        """缺少 title 和 message 字段时不应崩溃"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"]
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is False
    
    def test_title_contains_merge_but_not_at_start(self):
        """标题包含 Merge 但不是 merge commit 格式"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": "fix: resolve Merge conflict in config",
            "message": "fix: resolve Merge conflict in config"
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is False
