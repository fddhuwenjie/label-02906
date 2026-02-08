"""单元测试：GitLabClient 增量模式逻辑"""

import pytest
import sys
import os
import time
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitlab_client import GitLabClient, RateLimiter, GitLabAPIError


class TestIncrementalMode:
    """测试增量模式的 stop_at_sha 逻辑"""
    
    @pytest.fixture
    def mock_client(self):
        """创建一个 mock 的 GitLabClient"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()
            return client
    
    def test_stop_at_sha_collects_commits_before_stop_point(self, mock_client):
        """stop_at_sha 应该收集停止点之前的所有提交"""
        # 模拟 API 返回的数据（按时间倒序：最新的在前）
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {'x-total-pages': '1'}
        mock_response.json.return_value = [
            {"id": "commit_new_1", "title": "newest commit"},
            {"id": "commit_new_2", "title": "second newest"},
            {"id": "commit_stop", "title": "this is the stop point"},  # stop_at_sha
            {"id": "commit_old_1", "title": "older commit"},
            {"id": "commit_old_2", "title": "oldest commit"},
        ]
        mock_client.session.get.return_value = mock_response
        
        # 调用 _request 方法，设置 stop_at_sha
        results = mock_client._request(
            "/projects/1/repository/commits",
            params={"with_stats": "true"},
            context="test",
            stop_at_sha="commit_stop"
        )
        
        # 应该只返回 stop_at_sha 之前的提交（即更新的提交）
        assert len(results) == 2
        assert results[0]["id"] == "commit_new_1"
        assert results[1]["id"] == "commit_new_2"
    
    def test_stop_at_sha_not_found_returns_all(self, mock_client):
        """如果 stop_at_sha 不在当前页，应返回所有数据并继续下一页"""
        # 第一页数据
        mock_response_page1 = Mock()
        mock_response_page1.status_code = 200
        mock_response_page1.headers = {'x-total-pages': '2'}
        mock_response_page1.json.return_value = [
            {"id": "commit_1", "title": "commit 1"},
            {"id": "commit_2", "title": "commit 2"},
        ]
        
        # 第二页数据（包含 stop_at_sha）
        mock_response_page2 = Mock()
        mock_response_page2.status_code = 200
        mock_response_page2.headers = {'x-total-pages': '2'}
        mock_response_page2.json.return_value = [
            {"id": "commit_3", "title": "commit 3"},
            {"id": "commit_stop", "title": "stop point"},
            {"id": "commit_4", "title": "commit 4"},
        ]
        
        mock_client.session.get.side_effect = [mock_response_page1, mock_response_page2]
        
        results = mock_client._request(
            "/projects/1/repository/commits",
            params={"with_stats": "true"},
            context="test",
            stop_at_sha="commit_stop"
        )
        
        # 应该返回第一页全部 + 第二页 stop_at_sha 之前的
        assert len(results) == 3
        assert results[0]["id"] == "commit_1"
        assert results[1]["id"] == "commit_2"
        assert results[2]["id"] == "commit_3"
    
    def test_stop_at_sha_at_first_position(self, mock_client):
        """stop_at_sha 在第一个位置时应返回空列表"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {'x-total-pages': '1'}
        mock_response.json.return_value = [
            {"id": "commit_stop", "title": "stop point"},
            {"id": "commit_2", "title": "commit 2"},
        ]
        mock_client.session.get.return_value = mock_response
        
        results = mock_client._request(
            "/projects/1/repository/commits",
            params={"with_stats": "true"},
            context="test",
            stop_at_sha="commit_stop"
        )
        
        # stop_at_sha 在第一个位置，没有新提交
        assert len(results) == 0
    
    def test_no_stop_at_sha_returns_all(self, mock_client):
        """不设置 stop_at_sha 时应返回所有数据"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {'x-total-pages': '1'}
        mock_response.json.return_value = [
            {"id": "commit_1", "title": "commit 1"},
            {"id": "commit_2", "title": "commit 2"},
            {"id": "commit_3", "title": "commit 3"},
        ]
        mock_client.session.get.return_value = mock_response
        
        results = mock_client._request(
            "/projects/1/repository/commits",
            params={"with_stats": "true"},
            context="test"
        )
        
        assert len(results) == 3
    
    def test_stop_at_sha_stops_pagination(self, mock_client):
        """找到 stop_at_sha 后不应继续请求下一页"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {'x-total-pages': '3'}  # 声称有 3 页
        mock_response.json.return_value = [
            {"id": "commit_1", "title": "commit 1"},
            {"id": "commit_stop", "title": "stop point"},
            {"id": "commit_2", "title": "commit 2"},
        ]
        mock_client.session.get.return_value = mock_response
        
        results = mock_client._request(
            "/projects/1/repository/commits",
            params={"with_stats": "true"},
            context="test",
            stop_at_sha="commit_stop"
        )
        
        # 应该只调用一次 API（不继续请求后续页面）
        assert mock_client.session.get.call_count == 1
        assert len(results) == 1
        assert results[0]["id"] == "commit_1"


class TestRateLimiter:
    """测试速率限制器"""
    
    def test_initial_state(self):
        """初始状态检查"""
        limiter = RateLimiter(min_interval=0.1, max_interval=60.0)
        assert limiter.current_interval == 0.1
        assert limiter.consecutive_429s == 0
    
    def test_on_success_resets_consecutive_429s(self):
        """成功请求后重置连续 429 计数"""
        limiter = RateLimiter()
        limiter.consecutive_429s = 3
        
        mock_response = Mock()
        mock_response.headers = {}
        limiter.on_success(mock_response)
        
        assert limiter.consecutive_429s == 0
    
    def test_on_success_speeds_up_with_high_quota(self):
        """配额充足时加快速度"""
        limiter = RateLimiter(min_interval=0.1)
        limiter.current_interval = 1.0
        
        mock_response = Mock()
        mock_response.headers = {'RateLimit-Remaining': '800', 'RateLimit-Limit': '1000'}
        limiter.on_success(mock_response)
        
        assert limiter.current_interval < 1.0
    
    def test_on_success_slows_down_with_low_quota(self):
        """配额紧张时减慢速度"""
        limiter = RateLimiter(max_interval=60.0)
        limiter.current_interval = 1.0
        
        mock_response = Mock()
        mock_response.headers = {'RateLimit-Remaining': '100', 'RateLimit-Limit': '1000'}
        limiter.on_success(mock_response)
        
        assert limiter.current_interval > 1.0
    
    def test_on_rate_limited_uses_retry_after(self):
        """使用 Retry-After header"""
        limiter = RateLimiter()
        
        mock_response = Mock()
        mock_response.headers = {'Retry-After': '30'}
        
        wait_time = limiter.on_rate_limited(mock_response)
        assert wait_time == 30.0
    
    def test_on_rate_limited_exponential_backoff(self):
        """指数退避"""
        limiter = RateLimiter(min_interval=1.0, max_interval=60.0)
        
        mock_response = Mock()
        mock_response.headers = {}
        
        # 第一次 429
        wait1 = limiter.on_rate_limited(mock_response)
        # 第二次 429
        wait2 = limiter.on_rate_limited(mock_response)
        
        assert wait2 > wait1
        assert limiter.consecutive_429s == 2


class TestProjectFiltering:
    """测试项目过滤功能"""
    
    @pytest.fixture
    def mock_client(self):
        """创建一个 mock 的 GitLabClient"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()
            return client
    
    def test_filter_by_namespace_pattern(self, mock_client):
        """按 namespace 正则过滤"""
        mock_client.namespace_pattern = __import__('re').compile(r'team-a', __import__('re').IGNORECASE)
        mock_client.project_pattern = None
        
        projects = [
            {"name": "project1", "namespace": {"full_path": "team-a/backend"}},
            {"name": "project2", "namespace": {"full_path": "team-b/frontend"}},
            {"name": "project3", "namespace": {"full_path": "team-a/frontend"}},
        ]
        
        filtered = mock_client._filter_projects(projects)
        assert len(filtered) == 2
        assert all('team-a' in p['namespace']['full_path'] for p in filtered)
    
    def test_filter_by_project_pattern(self, mock_client):
        """按项目名称正则过滤"""
        mock_client.namespace_pattern = None
        mock_client.project_pattern = __import__('re').compile(r'api', __import__('re').IGNORECASE)
        
        projects = [
            {"name": "user-api", "namespace": {"full_path": "team-a"}},
            {"name": "frontend", "namespace": {"full_path": "team-a"}},
            {"name": "api-gateway", "namespace": {"full_path": "team-b"}},
        ]
        
        filtered = mock_client._filter_projects(projects)
        assert len(filtered) == 2
        assert all('api' in p['name'].lower() for p in filtered)
    
    def test_filter_combined(self, mock_client):
        """组合过滤"""
        mock_client.namespace_pattern = __import__('re').compile(r'team-a', __import__('re').IGNORECASE)
        mock_client.project_pattern = __import__('re').compile(r'api', __import__('re').IGNORECASE)
        
        projects = [
            {"name": "user-api", "namespace": {"full_path": "team-a/backend"}},
            {"name": "frontend", "namespace": {"full_path": "team-a/web"}},
            {"name": "api-gateway", "namespace": {"full_path": "team-b/infra"}},
        ]
        
        filtered = mock_client._filter_projects(projects)
        assert len(filtered) == 1
        assert filtered[0]['name'] == 'user-api'
    
    def test_no_filter_returns_all(self, mock_client):
        """无过滤器时返回全部"""
        mock_client.namespace_pattern = None
        mock_client.project_pattern = None
        
        projects = [{"name": "p1"}, {"name": "p2"}, {"name": "p3"}]
        filtered = mock_client._filter_projects(projects)
        assert len(filtered) == 3


class TestInputValidation:
    """测试输入验证"""
    
    def test_invalid_regex_pattern(self):
        """无效正则表达式应抛出异常"""
        with pytest.raises(ValueError, match="无效的正则表达式"):
            with patch.object(GitLabClient, '_create_session'):
                GitLabClient(
                    "https://gitlab.example.com", 
                    "test-token",
                    namespace_pattern="[invalid"
                )
    
    def test_valid_regex_pattern(self):
        """有效正则表达式应正常创建"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient(
                "https://gitlab.example.com", 
                "test-token",
                namespace_pattern=r"team-\d+"
            )
            assert client.namespace_pattern is not None
