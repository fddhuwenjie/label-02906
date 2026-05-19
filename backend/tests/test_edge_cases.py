"""单元测试：边界条件与异常场景"""

import pytest
import sys
import os
import time
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitlab_client import GitLabClient, RateLimiter, GitLabAPIError
from stats_collector import CodeStatsCollector
from models import AuthorStats


class TestPagination:
    """测试分页数据拼接"""

    @pytest.fixture
    def mock_client(self):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()
            return client

    @pytest.mark.parametrize("total_pages", [1, 2, 3, 5])
    def test_should_concatenate_all_pages_when_api_returns_paginated_data(self, mock_client, total_pages):
        """分页数据应正确拼接"""
        per_page = 2
        pages_data = []
        for p in range(1, total_pages + 1):
            page_items = [{"id": f"page{p}_item{i}"} for i in range(per_page)]
            pages_data.append(page_items)

        def make_response(page_num):
            resp = Mock()
            resp.status_code = 200
            resp.headers = {'x-total-pages': str(total_pages)}
            resp.json.return_value = pages_data[page_num - 1]
            return resp

        mock_client.session.get.side_effect = [make_response(p) for p in range(1, total_pages + 1)]

        results = mock_client._request(
            "/projects/1/repository/commits",
            params={"with_stats": "true"},
            context="测试分页拼接"
        )

        assert len(results) == total_pages * per_page
        for p in range(total_pages):
            for i in range(per_page):
                idx = p * per_page + i
                assert results[idx]["id"] == f"page{p + 1}_item{i}"

        assert mock_client.session.get.call_count == total_pages

    def test_should_stop_when_empty_page_returned_when_no_total_pages_header(self, mock_client):
        """无 x-total-pages header 时，遇到空数据应停止"""
        resp1 = Mock()
        resp1.status_code = 200
        resp1.headers = {}
        resp1.json.return_value = [{"id": "commit_1"}]

        resp2 = Mock()
        resp2.status_code = 200
        resp2.headers = {}
        resp2.json.return_value = []

        mock_client.session.get.side_effect = [resp1, resp2]

        results = mock_client._request(
            "/projects/1/repository/commits",
            params={"with_stats": "true"},
            context="测试空页停止"
        )

        assert len(results) == 1
        assert results[0]["id"] == "commit_1"
        assert mock_client.session.get.call_count == 1


class TestExponentialBackoff:
    """测试指数退避重试逻辑"""

    @pytest.fixture
    def mock_client(self):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = RateLimiter(min_interval=0.1, max_interval=60.0)
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()
            return client

    def test_should_exponential_backoff_on_rate_limit_when_api_returns_http_429(self, mock_client):
        """commit diff API 返回 429 时应按指数退避间隔递增"""
        resp_429 = Mock()
        resp_429.status_code = 429
        resp_429.headers = {}

        resp_success = Mock()
        resp_success.status_code = 200
        resp_success.headers = {'x-total-pages': '1'}
        resp_success.json.return_value = [{"id": "commit_final"}]

        mock_client.session.get.side_effect = [resp_429, resp_429, resp_429, resp_success]

        with patch('time.sleep') as mock_sleep:
            results = mock_client._request(
                "/projects/1/repository/commits",
                params={"with_stats": "true"},
                context="测试指数退避"
            )

            assert mock_sleep.call_count == 3

            sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
            assert sleep_args[0] < sleep_args[1] < sleep_args[2], f"退避间隔应递增，实际: {sleep_args}"

            assert len(results) == 1
            assert results[0]["id"] == "commit_final"

    def test_should_use_retry_after_header_when_present_in_429_response(self, mock_client):
        """429 响应包含 Retry-After header 时应使用该值"""
        resp_429 = Mock()
        resp_429.status_code = 429
        resp_429.headers = {'Retry-After': '15'}

        resp_success = Mock()
        resp_success.status_code = 200
        resp_success.headers = {'x-total-pages': '1'}
        resp_success.json.return_value = [{"id": "commit_ok"}]

        mock_client.session.get.side_effect = [resp_429, resp_success]

        with patch('time.sleep') as mock_sleep:
            results = mock_client._request(
                "/projects/1/repository/commits",
                params={"with_stats": "true"},
                context="测试 Retry-After"
            )

            mock_sleep.assert_called_once_with(15.0)
            assert len(results) == 1


class TestRevertDetection:
    """测试 Revert commit 过滤"""

    @pytest.mark.parametrize("title,should_filter", [
        ('Revert "feat: add broken feature"', True),
        ('Revert "fix: critical bug"', True),
        ('Revert', False),
        ('Reverted some changes manually', False),
        ('Reverting the broken change', False),
        ('feat: revert to v1.0', False),
        ('fix: revert previous PR', False),
        ('Revert "feat: add feature"\n\nThis reverts commit xyz123', True),
        ('Revert \'feat: add feature\'', True),
    ])
    def test_should_detect_revert_commits_when_commit_message_contains_revert_pattern(self, title, should_filter):
        """commit message 包含 Revert xxx 格式时应正确过滤"""
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": title,
            "message": title
        }
        assert CodeStatsCollector.is_non_code_commit(commit) is should_filter


class TestSameAuthorDifferentEmail:
    """测试同一作者使用不同邮箱提交的合并统计逻辑"""

    def test_should_merge_stats_when_same_author_uses_different_emails(self):
        """同一作者使用不同邮箱时应分别按 email 独立统计（现有行为：以 email 为 key）"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()

            client.get_projects = Mock(return_value=[
                {"id": 1, "name": "project-a", "namespace": {"full_path": "team/backend"}}
            ])
            client.get_commits = Mock(return_value=[
                {
                    "id": "commit_1",
                    "parent_ids": ["parent_1"],
                    "title": "feat: new feature",
                    "author_name": "张三",
                    "author_email": "zhangsan@company.com",
                    "stats": {"additions": 100, "deletions": 20}
                },
                {
                    "id": "commit_2",
                    "parent_ids": ["parent_2"],
                    "title": "fix: bug fix",
                    "author_name": "张三",
                    "author_email": "zhangsan@personal.com",
                    "stats": {"additions": 50, "deletions": 10}
                }
            ])

            collector = CodeStatsCollector(client)
            stats = collector.collect()

            assert len(stats) == 2

            company_key = "zhangsan@company.com"
            personal_key = "zhangsan@personal.com"
            assert company_key in stats
            assert personal_key in stats

            assert stats[company_key].author_name == "张三"
            assert stats[company_key].additions == 100
            assert stats[company_key].deletions == 20
            assert stats[company_key].total_commits == 1

            assert stats[personal_key].author_name == "张三"
            assert stats[personal_key].additions == 50
            assert stats[personal_key].deletions == 10
            assert stats[personal_key].total_commits == 1

    def test_should_handle_same_author_same_email_multiple_commits(self):
        """同一作者同一邮箱多次提交应正确累加"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()

            client.get_projects = Mock(return_value=[
                {"id": 1, "name": "project-a", "namespace": {"full_path": "team/backend"}}
            ])
            client.get_commits = Mock(return_value=[
                {
                    "id": "commit_1",
                    "parent_ids": ["parent_1"],
                    "title": "feat: first commit",
                    "author_name": "李四",
                    "author_email": "lisi@example.com",
                    "stats": {"additions": 80, "deletions": 15}
                },
                {
                    "id": "commit_2",
                    "parent_ids": ["parent_2"],
                    "title": "fix: second commit",
                    "author_name": "李四",
                    "author_email": "lisi@example.com",
                    "stats": {"additions": 20, "deletions": 5}
                }
            ])

            collector = CodeStatsCollector(client)
            stats = collector.collect()

            assert len(stats) == 1
            assert "lisi@example.com" in stats
            assert stats["lisi@example.com"].additions == 100
            assert stats["lisi@example.com"].deletions == 20
            assert stats["lisi@example.com"].total_commits == 2


class TestEmptyRepository:
    """测试空仓库场景"""

    def test_should_not_raise_exception_when_project_repository_has_no_commits(self):
        """项目仓库无 commit 时不应抛出异常"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()

            client.get_projects = Mock(return_value=[
                {"id": 99, "name": "empty-repo", "namespace": {"full_path": "team/empty"}}
            ])
            client.get_commits = Mock(return_value=[])

            collector = CodeStatsCollector(client)
            stats = collector.collect()

            assert isinstance(stats, dict)
            assert len(stats) == 0
            client.get_commits.assert_called_once()

    def test_should_handle_no_projects_gracefully_when_no_projects_returned(self):
        """无项目返回时应正常处理"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()

            client.get_projects = Mock(return_value=[])

            collector = CodeStatsCollector(client)
            stats = collector.collect()

            assert isinstance(stats, dict)
            assert len(stats) == 0

    def test_should_handle_api_error_gracefully_when_get_commits_raises_404(self):
        """get_commits 遇到 404 时应跳过该项目而不抛异常"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()

            client.get_projects = Mock(return_value=[
                {"id": 1, "name": "normal-project", "namespace": {"full_path": "team/ok"}},
                {"id": 2, "name": "broken-project", "namespace": {"full_path": "team/bad"}},
            ])

            client.get_commits = Mock(return_value=[{
                "id": "commit_1",
                "parent_ids": ["parent_1"],
                "title": "feat: normal commit",
                "author_name": "Alice",
                "author_email": "alice@example.com",
                "stats": {"additions": 50, "deletions": 10}
            }])

            collector = CodeStatsCollector(client)
            stats = collector.collect()

            assert len(stats) == 1
            assert "alice@example.com" in stats
