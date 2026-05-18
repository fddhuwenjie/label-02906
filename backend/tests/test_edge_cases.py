"""边界情况单元测试"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitlab_client import GitLabClient, RateLimiter, GitLabAPIError
from stats_collector import CodeStatsCollector


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

    @pytest.mark.parametrize("page_data,expected_count", [
        (
            [
                [{"id": "c1"}, {"id": "c2"}],
                [{"id": "c3"}, {"id": "c4"}],
                [{"id": "c5"}, {"id": "c6"}],
            ],
            6
        ),
        (
            [
                [{"id": "c1"}],
                [],
            ],
            1
        ),
        (
            [
                [],
            ],
            0
        ),
    ])
    def test_should_collect_all_pages_when_api_returns_paginated_data(self, mock_client, page_data, expected_count):
        responses = []
        total_pages = len(page_data)
        for i, data in enumerate(page_data, 1):
            resp = Mock()
            resp.status_code = 200
            resp.headers = {'x-total-pages': str(total_pages)}
            resp.json.return_value = data
            responses.append(resp)

        mock_client.session.get.side_effect = responses

        results = mock_client._request("/projects/1/repository/commits", context="test")

        assert len(results) == expected_count
        assert mock_client.session.get.call_count == max(1, total_pages)

    def test_should_stop_pagination_when_data_less_than_per_page(self, mock_client):
        resp = Mock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = [{"id": "c1"}, {"id": "c2"}]
        mock_client.session.get.return_value = resp

        results = mock_client._request("/projects/1/repository/commits", context="test")

        assert len(results) == 2
        assert mock_client.session.get.call_count == 1

    def test_should_concatenate_pages_in_correct_order(self, mock_client):
        page1 = Mock()
        page1.status_code = 200
        page1.headers = {'x-total-pages': '3'}
        page1.json.return_value = [{"id": "c1"}, {"id": "c2"}]

        page2 = Mock()
        page2.status_code = 200
        page2.headers = {'x-total-pages': '3'}
        page2.json.return_value = [{"id": "c3"}, {"id": "c4"}]

        page3 = Mock()
        page3.status_code = 200
        page3.headers = {'x-total-pages': '3'}
        page3.json.return_value = [{"id": "c5"}, {"id": "c6"}]

        mock_client.session.get.side_effect = [page1, page2, page3]

        results = mock_client._request("/projects/1/repository/commits", context="test")

        assert [r["id"] for r in results] == ["c1", "c2", "c3", "c4", "c5", "c6"]


class TestRateLimiting:
    """测试速率限制和重试逻辑"""

    @pytest.fixture
    def mock_client(self):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = RateLimiter(min_interval=1.0, max_interval=60.0)
            return client

    @pytest.mark.parametrize("retry_after_values,expected_sleep_calls", [
        (["30"], [30.0]),
        (["10", "20"], [10.0, 20.0]),
    ])
    def test_should_use_retry_after_header_when_api_returns_429(self, mock_client, retry_after_values, expected_sleep_calls):
        mock_client.rate_limiter.wait = Mock()
        mock_client.rate_limiter.on_success = Mock()

        responses = []
        for retry_after in retry_after_values:
            resp_429 = Mock()
            resp_429.status_code = 429
            resp_429.headers = {'Retry-After': retry_after}
            responses.append(resp_429)

        resp_200 = Mock()
        resp_200.status_code = 200
        resp_200.headers = {'x-total-pages': '1', 'RateLimit-Remaining': '500', 'RateLimit-Limit': '1000'}
        resp_200.json.return_value = [{"id": "c1"}]
        responses.append(resp_200)

        mock_client.session.get.side_effect = responses

        with patch('time.sleep') as mock_sleep:
            results = mock_client._request("/projects/1/repository/commits", context="test")

        assert len(results) == 1
        retry_sleep_calls = [c for c in mock_sleep.call_args_list if c[0][0] >= 1.0]
        assert len(retry_sleep_calls) == len(expected_sleep_calls)
        for i, expected in enumerate(expected_sleep_calls):
            assert abs(retry_sleep_calls[i][0][0] - expected) < 0.01

    @pytest.mark.parametrize("consecutive_429s,expected_wait_times", [
        (1, [2.0]),
        (2, [2.0, 4.0]),
        (3, [2.0, 4.0, 8.0]),
    ])
    def test_should_apply_exponential_backoff_when_429_without_retry_after(self, mock_client, consecutive_429s, expected_wait_times):
        mock_client.rate_limiter.wait = Mock()
        mock_client.rate_limiter.on_success = Mock()

        responses = []
        for _ in range(consecutive_429s):
            resp_429 = Mock()
            resp_429.status_code = 429
            resp_429.headers = {}
            responses.append(resp_429)

        resp_200 = Mock()
        resp_200.status_code = 200
        resp_200.headers = {'x-total-pages': '1', 'RateLimit-Remaining': '500', 'RateLimit-Limit': '1000'}
        resp_200.json.return_value = [{"id": "c1"}]
        responses.append(resp_200)

        mock_client.session.get.side_effect = responses

        with patch('time.sleep') as mock_sleep:
            results = mock_client._request("/projects/1/repository/commits", context="test")

        assert len(results) == 1
        actual_calls = [c[0][0] for c in mock_sleep.call_args_list]
        for i, expected in enumerate(expected_wait_times):
            assert abs(actual_calls[i] - expected) < 0.01

    def test_should_increase_sleep_interval_exponentially_on_consecutive_429s(self, mock_client):
        mock_client.rate_limiter.wait = Mock()
        mock_client.rate_limiter.on_success = Mock()

        resp_429_1 = Mock()
        resp_429_1.status_code = 429
        resp_429_1.headers = {}

        resp_429_2 = Mock()
        resp_429_2.status_code = 429
        resp_429_2.headers = {}

        resp_429_3 = Mock()
        resp_429_3.status_code = 429
        resp_429_3.headers = {}

        resp_200 = Mock()
        resp_200.status_code = 200
        resp_200.headers = {'x-total-pages': '1', 'RateLimit-Remaining': '500', 'RateLimit-Limit': '1000'}
        resp_200.json.return_value = [{"id": "c1"}]

        mock_client.session.get.side_effect = [resp_429_1, resp_429_2, resp_429_3, resp_200]

        with patch('time.sleep') as mock_sleep:
            mock_client._request("/projects/1/repository/commits", context="test")

        sleep_times = [c[0][0] for c in mock_sleep.call_args_list]
        assert len(sleep_times) == 3
        assert sleep_times[1] > sleep_times[0]
        assert sleep_times[2] > sleep_times[1]


class TestRevertDetection:
    """测试 Revert 提交检测"""

    @pytest.mark.parametrize("commit_title,commit_message,expected_result", [
        (
            'Revert "feat: add broken feature"',
            'Revert "feat: add broken feature"\n\nThis reverts commit xyz789.',
            True
        ),
        (
            'Revert "fix: critical bug fix"',
            'Revert "fix: critical bug fix"',
            True
        ),
        (
            'Revert something else',
            'Revert something else manually',
            True
        ),
        (
            'Reverted changes from yesterday',
            'Reverted changes from yesterday',
            False
        ),
        (
            'feat: add new feature',
            'feat: add new feature',
            False
        ),
        (
            'fix: resolve merge conflict in Revert logic',
            'fix: resolve merge conflict',
            False
        ),
        (
            '',
            'Revert "test"',
            False
        ),
    ])
    def test_should_detect_revert_commit_when_title_starts_with_revert(self, commit_title, commit_message, expected_result):
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": commit_title,
            "message": commit_message
        }
        assert CodeStatsCollector.is_non_code_commit(commit) == expected_result

    @pytest.mark.parametrize("commit_title,expected_result", [
        ('Revert "test"', True),
        ('revert "test"', False),
        ('REVERT "test"', False),
    ])
    def test_should_be_case_sensitive_for_revert_detection(self, commit_title, expected_result):
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": commit_title,
            "message": commit_title
        }
        assert CodeStatsCollector.is_non_code_commit(commit) == expected_result


class TestAuthorEmailMerging:
    """测试同一作者不同邮箱的合并统计"""

    @pytest.fixture
    def mock_client(self):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            return client

    @pytest.mark.parametrize("commits,expected_authors", [
        (
            [
                {
                    "id": "c1",
                    "parent_ids": ["p1"],
                    "title": "commit 1",
                    "author_name": "张三",
                    "author_email": "zhangsan@example.com",
                    "stats": {"additions": 10, "deletions": 5}
                },
                {
                    "id": "c2",
                    "parent_ids": ["p2"],
                    "title": "commit 2",
                    "author_name": "张三",
                    "author_email": "zhangsan@work.com",
                    "stats": {"additions": 20, "deletions": 3}
                },
            ],
            {
                "zhangsan@example.com": {"additions": 10, "deletions": 5, "commits": 1},
                "zhangsan@work.com": {"additions": 20, "deletions": 3, "commits": 1},
            }
        ),
        (
            [
                {
                    "id": "c1",
                    "parent_ids": ["p1"],
                    "title": "commit 1",
                    "author_name": "张三",
                    "author_email": "",
                    "stats": {"additions": 10, "deletions": 5}
                },
                {
                    "id": "c2",
                    "parent_ids": ["p2"],
                    "title": "commit 2",
                    "author_name": "张三",
                    "author_email": "",
                    "stats": {"additions": 20, "deletions": 3}
                },
            ],
            {
                "张三": {"additions": 30, "deletions": 8, "commits": 2},
            }
        ),
    ])
    def test_should_merge_stats_by_email_or_name_when_same_author_different_emails(self, mock_client, commits, expected_authors):
        mock_client.get_projects = Mock(return_value=[{"id": 1, "name": "test-project"}])
        mock_client.get_commits = Mock(return_value=commits)

        collector = CodeStatsCollector(mock_client)
        stats = collector.collect()

        assert len(stats) == len(expected_authors)
        for key, expected in expected_authors.items():
            assert key in stats
            assert stats[key].additions == expected["additions"]
            assert stats[key].deletions == expected["deletions"]
            assert stats[key].total_commits == expected["commits"]

    def test_should_use_email_as_primary_key_when_available(self, mock_client):
        commits = [
            {
                "id": "c1",
                "parent_ids": ["p1"],
                "title": "commit 1",
                "author_name": "张三",
                "author_email": "zhangsan@example.com",
                "stats": {"additions": 10, "deletions": 5}
            },
            {
                "id": "c2",
                "parent_ids": ["p2"],
                "title": "commit 2",
                "author_name": "李四",
                "author_email": "zhangsan@example.com",
                "stats": {"additions": 20, "deletions": 3}
            },
        ]

        mock_client.get_projects = Mock(return_value=[{"id": 1, "name": "test-project"}])
        mock_client.get_commits = Mock(return_value=commits)

        collector = CodeStatsCollector(mock_client)
        stats = collector.collect()

        assert len(stats) == 1
        assert "zhangsan@example.com" in stats
        assert stats["zhangsan@example.com"].additions == 30
        assert stats["zhangsan@example.com"].total_commits == 2


class TestEmptyRepository:
    """测试空仓库场景"""

    @pytest.fixture
    def mock_client(self):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            return client

    @pytest.mark.parametrize("projects,expected_stats_count", [
        (
            [{"id": 1, "name": "empty-project"}],
            0
        ),
        (
            [
                {"id": 1, "name": "empty-project"},
                {"id": 2, "name": "another-empty"},
            ],
            0
        ),
        (
            [],
            0
        ),
    ])
    def test_should_not_raise_exception_when_repository_has_no_commits(self, mock_client, projects, expected_stats_count):
        mock_client.get_projects = Mock(return_value=projects)
        mock_client.get_commits = Mock(return_value=[])

        collector = CodeStatsCollector(mock_client)

        try:
            stats = collector.collect()
            assert len(stats) == expected_stats_count
        except Exception as e:
            pytest.fail(f"空仓库时抛出了异常: {e}")

    def test_should_return_empty_stats_when_all_projects_are_empty(self, mock_client):
        mock_client.get_projects = Mock(return_value=[
            {"id": 1, "name": "empty-1"},
            {"id": 2, "name": "empty-2"},
        ])
        mock_client.get_commits = Mock(return_value=[])

        collector = CodeStatsCollector(mock_client)
        stats = collector.collect()

        assert isinstance(stats, dict)
        assert len(stats) == 0

    def test_should_handle_mixed_empty_and_non_empty_projects(self, mock_client):
        def mock_get_commits(project_id, project_name, **kwargs):
            if project_id == 1:
                return []
            elif project_id == 2:
                return [{
                    "id": "c1",
                    "parent_ids": ["p1"],
                    "title": "test commit",
                    "author_name": "Test",
                    "author_email": "test@example.com",
                    "stats": {"additions": 10, "deletions": 5}
                }]
            return []

        mock_client.get_projects = Mock(return_value=[
            {"id": 1, "name": "empty-project"},
            {"id": 2, "name": "active-project"},
        ])
        mock_client.get_commits = mock_get_commits

        collector = CodeStatsCollector(mock_client)
        stats = collector.collect()

        assert len(stats) == 1
        assert "test@example.com" in stats
