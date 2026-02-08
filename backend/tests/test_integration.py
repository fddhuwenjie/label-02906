"""集成测试：端到端流程测试"""

import pytest
import sys
import os
import json
import tempfile
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitlab_client import GitLabClient
from stats_collector import CodeStatsCollector
from exporter import StatsExporter


class TestEndToEndFlow:
    """端到端流程测试"""
    
    @pytest.fixture
    def mock_gitlab_responses(self):
        """模拟 GitLab API 响应"""
        return {
            "projects": [
                {"id": 1, "name": "project-a", "namespace": {"full_path": "team/backend"}},
                {"id": 2, "name": "project-b", "namespace": {"full_path": "team/frontend"}},
            ],
            "commits_project_1": [
                {
                    "id": "abc123",
                    "parent_ids": ["def456"],
                    "title": "feat: add feature",
                    "message": "feat: add feature",
                    "author_name": "Alice",
                    "author_email": "alice@example.com",
                    "stats": {"additions": 100, "deletions": 20}
                },
                {
                    "id": "merge123",
                    "parent_ids": ["abc123", "xyz789"],  # merge commit
                    "title": "Merge branch 'feature'",
                    "message": "Merge branch 'feature'",
                    "author_name": "Alice",
                    "author_email": "alice@example.com",
                    "stats": {"additions": 200, "deletions": 50}
                },
            ],
            "commits_project_2": [
                {
                    "id": "xyz789",
                    "parent_ids": ["uvw123"],
                    "title": "fix: bug fix",
                    "message": "fix: bug fix",
                    "author_name": "Bob",
                    "author_email": "bob@example.com",
                    "stats": {"additions": 50, "deletions": 10}
                },
            ]
        }
    
    @pytest.fixture
    def mock_client(self, mock_gitlab_responses):
        """创建模拟的 GitLabClient"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()
            
            def mock_get_projects():
                return mock_gitlab_responses["projects"]
            
            def mock_get_commits(project_id, project_name, **kwargs):
                if project_id == 1:
                    return mock_gitlab_responses["commits_project_1"]
                elif project_id == 2:
                    return mock_gitlab_responses["commits_project_2"]
                return []
            
            client.get_projects = mock_get_projects
            client.get_commits = mock_get_commits
            return client
    
    def test_full_collection_flow(self, mock_client):
        """完整的统计收集流程"""
        collector = CodeStatsCollector(mock_client)
        stats = collector.collect()
        
        # 应该有两个作者
        assert len(stats) == 2
        
        # Alice 的统计（merge commit 应被过滤）
        alice_stats = stats.get("alice@example.com")
        assert alice_stats is not None
        assert alice_stats.additions == 100  # 只有非 merge commit
        assert alice_stats.deletions == 20
        assert alice_stats.total_commits == 1
        
        # Bob 的统计
        bob_stats = stats.get("bob@example.com")
        assert bob_stats is not None
        assert bob_stats.additions == 50
        assert bob_stats.deletions == 10
        assert bob_stats.total_commits == 1
    
    def test_csv_export(self, mock_client):
        """CSV 导出测试"""
        collector = CodeStatsCollector(mock_client)
        stats = collector.collect()
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            csv_path = f.name
        
        try:
            StatsExporter.to_csv(stats, csv_path)
            
            # 验证文件存在且有内容
            assert Path(csv_path).exists()
            content = Path(csv_path).read_text(encoding='utf-8')
            assert "Alice" in content or "alice" in content
            assert "Bob" in content or "bob" in content
        finally:
            Path(csv_path).unlink(missing_ok=True)


class TestIncrementalCollection:
    """增量收集测试"""
    
    def test_incremental_mode_saves_cache(self):
        """增量模式应保存缓存"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()
            
            # Mock API 响应
            client.get_projects = Mock(return_value=[
                {"id": 1, "name": "test-project"}
            ])
            client.get_commits = Mock(return_value=[
                {
                    "id": "commit123",
                    "parent_ids": ["parent456"],
                    "title": "test commit",
                    "author_name": "Test",
                    "author_email": "test@example.com",
                    "stats": {"additions": 10, "deletions": 5}
                }
            ])
            
            with tempfile.TemporaryDirectory() as tmpdir:
                collector = CodeStatsCollector(
                    client, 
                    incremental=True, 
                    cache_dir=tmpdir
                )
                collector.collect()
                
                # 验证缓存文件已创建
                cache_path = Path(tmpdir) / CodeStatsCollector.CACHE_FILE
                assert cache_path.exists()
                
                # 验证缓存内容
                cache_data = json.loads(cache_path.read_text())
                assert "projects_cursor" in cache_data
                assert "1" in cache_data["projects_cursor"]
                assert cache_data["projects_cursor"]["1"] == "commit123"
    
    def test_incremental_mode_loads_cache(self):
        """增量模式应加载缓存"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.rate_limiter = Mock()
            
            with tempfile.TemporaryDirectory() as tmpdir:
                # 预先创建缓存文件
                cache_path = Path(tmpdir) / CodeStatsCollector.CACHE_FILE
                cache_data = {
                    "version": "1.0",
                    "last_updated": "2025-01-01T00:00:00",
                    "projects_cursor": {"1": "old_commit_sha"},
                    "author_stats": {
                        "test@example.com": {
                            "author_name": "Test",
                            "author_email": "test@example.com",
                            "additions": 100,
                            "deletions": 50,
                            "total_commits": 5,
                            "projects": ["old-project"]
                        }
                    }
                }
                cache_path.write_text(json.dumps(cache_data))
                
                collector = CodeStatsCollector(
                    client, 
                    incremental=True, 
                    cache_dir=tmpdir
                )
                
                # 验证缓存已加载
                assert "test@example.com" in collector.author_stats
                assert collector.author_stats["test@example.com"].additions == 100
                assert collector.cache.projects_cursor.get("1") == "old_commit_sha"


class TestFilteringIntegration:
    """过滤功能集成测试"""
    
    def test_group_filtering(self):
        """Group 过滤集成测试"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient(
                "https://gitlab.example.com", 
                "test-token",
                group="team-a"
            )
            assert client.group == "team-a"
    
    def test_namespace_and_project_filtering(self):
        """Namespace 和项目名称过滤集成测试"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient(
                "https://gitlab.example.com", 
                "test-token",
                namespace_pattern=r"backend",
                project_pattern=r"api"
            )
            
            projects = [
                {"name": "user-api", "namespace": {"full_path": "team/backend"}},
                {"name": "web-app", "namespace": {"full_path": "team/frontend"}},
                {"name": "api-gateway", "namespace": {"full_path": "team/backend"}},
                {"name": "api-docs", "namespace": {"full_path": "team/frontend"}},
            ]
            
            filtered = client._filter_projects(projects)
            
            # 只有 backend namespace 下包含 api 的项目
            assert len(filtered) == 2
            names = [p["name"] for p in filtered]
            assert "user-api" in names
            assert "api-gateway" in names
