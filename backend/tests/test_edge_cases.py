"""单元测试：边界条件与异常场景"""

import pytest
import sys
import os
import time
from unittest.mock import Mock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitlab_client import GitLabClient, RateLimiter, GitLabAPIError
from stats_collector import CodeStatsCollector
from models import AuthorStats


class TestPaginationConcatenation:
    """分页数据拼接正确性"""

    @pytest.fixture
    def mock_client(self):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()
            return client

    @pytest.mark.parametrize("total_items,per_page,expected_pages", [
        (250, 100, 3),
        (100, 100, 1),
        (101, 100, 2),
        (1, 100, 1),
    ])
    def test_should_concatenate_all_pages_when_api_returns_paginated_data(
        self, mock_client, total_items, per_page, expected_pages
    ):
        all_items = [{"id": i, "title": f"item-{i}"} for i in range(total_items)]

        responses = []
        for page_idx in range(expected_pages):
            start = page_idx * per_page
            end = start + per_page
            page_data = all_items[start:end]

            resp = Mock()
            resp.status_code = 200
            resp.headers = {'x-total-pages': str(expected_pages)}
            resp.json.return_value = page_data
            responses.append(resp)

        mock_client.session.get.side_effect = responses

        results = mock_client._request("/test", context="test pagination")

        assert len(results) == total_items
        for i in range(total_items):
            assert results[i]["id"] == i

    @pytest.mark.parametrize("page_sizes,expected_total", [
        ([3, 3, 1], 7),
        ([5, 5, 2], 12),
        ([100], 100),
    ])
    def test_should_stop_fetching_when_page_is_not_full(
        self, mock_client, page_sizes, expected_total
    ):
        all_items = [{"id": i} for i in range(expected_total)]
        total_pages = len(page_sizes)

        responses = []
        offset = 0
        for page_idx, size in enumerate(page_sizes):
            resp = Mock()
            resp.status_code = 200
            resp.headers = {'x-total-pages': str(total_pages)}
            resp.json.return_value = all_items[offset:offset + size]
            responses.append(resp)
            offset += size

        mock_client.session.get.side_effect = responses

        results = mock_client._request("/test", context="test partial page")

        assert len(results) == expected_total

    @pytest.mark.parametrize("max_pages,expected_count", [
        (1, 3),
        (2, 6),
    ])
    def test_should_limit_pages_when_max_pages_is_set(
        self, mock_client, max_pages, expected_count
    ):
        responses = []
        for page_idx in range(3):
            resp = Mock()
            resp.status_code = 200
            resp.headers = {'x-total-pages': '3'}
            resp.json.return_value = [
                {"id": page_idx * 3 + j} for j in range(3)
            ]
            responses.append(resp)

        mock_client.session.get.side_effect = responses

        results = mock_client._request(
            "/test", context="test max_pages", max_pages=max_pages
        )

        assert len(results) == expected_count


class TestExponentialBackoffRetry:
    """HTTP 429 指数退避重试"""

    @pytest.mark.parametrize("consecutive_429s,min_interval,expected_wait", [
        (1, 1.0, 2.0),
        (2, 1.0, 4.0),
        (3, 1.0, 8.0),
        (4, 1.0, 16.0),
    ])
    def test_should_increase_wait_time_exponentially_when_consecutive_429s_increase(
        self, consecutive_429s, min_interval, expected_wait
    ):
        limiter = RateLimiter(min_interval=min_interval, max_interval=60.0)
        mock_response = Mock()
        mock_response.headers = {}

        for _ in range(consecutive_429s):
            limiter.on_rate_limited(mock_response)

        wait_time = limiter.on_rate_limited(mock_response)
        assert wait_time == min(60.0, (2 ** (consecutive_429s + 1)) * min_interval)

    @pytest.mark.parametrize("retry_count", [3, 5])
    def test_should_verify_increasing_sleep_intervals_when_429_occurs_repeatedly(
        self, retry_count
    ):
        limiter = RateLimiter(min_interval=1.0, max_interval=60.0)
        mock_response = Mock()
        mock_response.headers = {}

        wait_times = []
        for _ in range(retry_count):
            wait_time = limiter.on_rate_limited(mock_response)
            wait_times.append(wait_time)

        for i in range(1, len(wait_times)):
            assert wait_times[i] >= wait_times[i - 1]
        assert wait_times[-1] > wait_times[0]

    @pytest.mark.parametrize("max_interval", [5.0, 10.0, 30.0])
    def test_should_cap_wait_time_at_max_interval_when_backoff_exceeds_limit(
        self, max_interval
    ):
        limiter = RateLimiter(min_interval=1.0, max_interval=max_interval)
        mock_response = Mock()
        mock_response.headers = {}

        for _ in range(20):
            limiter.on_rate_limited(mock_response)

        wait_time = limiter.on_rate_limited(mock_response)
        assert wait_time <= max_interval

    def test_should_call_time_sleep_with_backoff_intervals_when_429_returned(
        self,
    ):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.session = Mock()
            client.rate_limiter = RateLimiter(min_interval=0.5, max_interval=30.0)

        response_429_1 = Mock()
        response_429_1.status_code = 429
        response_429_1.headers = {}

        response_429_2 = Mock()
        response_429_2.status_code = 429
        response_429_2.headers = {}

        response_ok = Mock()
        response_ok.status_code = 200
        response_ok.headers = {'x-total-pages': '1'}
        response_ok.json.return_value = [{"id": "final_commit"}]

        client.session.get.side_effect = [response_429_1, response_429_2, response_ok]

        with patch('gitlab_client.time.sleep') as mock_sleep:
            with patch('gitlab_client.time.time', return_value=100.0):
                results = client._request("/test", context="test 429 retry")

        assert len(results) == 1
        assert results[0]["id"] == "final_commit"

        sleep_calls = [c[0][0] for c in mock_sleep.call_args_list]
        assert len(sleep_calls) >= 2
        for i in range(1, len(sleep_calls)):
            assert sleep_calls[i] >= sleep_calls[i - 1]


class TestRevertDetection:
    """Revert 提交检测"""

    @pytest.mark.parametrize("title,expected_revert", [
        ('Revert "feat: add login"', True),
        ('Revert "fix: memory leak in sensor polling"', True),
        ('Revert "Revert "original change""', True),
        ('Reverted some changes', False),
        ('revert: undo feature', False),
        ('Feature: revert handling', False),
    ])
    def test_should_detect_revert_correctly_when_title_contains_revert_pattern(
        self, title, expected_revert
    ):
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": title,
            "message": title,
        }
        result = CodeStatsCollector.is_non_code_commit(commit)
        assert result is expected_revert

    @pytest.mark.parametrize("message,expected_revert", [
        ("Revert \"feat: experimental wifi6 support\"\n\nThis reverts commit e5f6a1b2.", True),
        ("Normal commit message without revert", False),
    ])
    def test_should_filter_revert_when_message_starts_with_revert(
        self, message, expected_revert
    ):
        title = message.split("\n")[0]
        commit = {
            "id": "abc123",
            "parent_ids": ["def456"],
            "title": title,
            "message": message,
        }
        result = CodeStatsCollector.is_non_code_commit(commit)
        assert result is expected_revert


class TestAuthorMergeByDifferentEmails:
    """同一作者不同邮箱的合并统计"""

    @pytest.mark.parametrize(
        "commits_data,expected_key,expected_commits,expected_additions",
        [
            (
                [
                    {
                        "author_name": "Alice",
                        "author_email": "alice@company.com",
                        "additions": 100,
                        "deletions": 10,
                    },
                    {
                        "author_name": "Alice",
                        "author_email": "alice@personal.com",
                        "additions": 50,
                        "deletions": 5,
                    },
                ],
                "alice@company.com",
                1,
                100,
            ),
            (
                [
                    {
                        "author_name": "Bob",
                        "author_email": "",
                        "additions": 80,
                        "deletions": 8,
                    },
                    {
                        "author_name": "Bob",
                        "author_email": "bob@company.com",
                        "additions": 60,
                        "deletions": 6,
                    },
                ],
                "Bob",
                1,
                80,
            ),
        ],
    )
    def test_should_track_separately_when_same_name_different_emails(
        self, commits_data, expected_key, expected_commits, expected_additions
    ):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()

        client.get_projects = Mock(return_value=[{"id": 1, "name": "test-proj"}])

        commits = []
        for i, cd in enumerate(commits_data):
            commits.append({
                "id": f"commit_{i}",
                "parent_ids": [f"parent_{i}"],
                "title": f"feat: feature {i}",
                "author_name": cd["author_name"],
                "author_email": cd["author_email"],
                "stats": {"additions": cd["additions"], "deletions": cd["deletions"]},
            })

        client.get_commits = Mock(return_value=commits)

        collector = CodeStatsCollector(client)
        stats = collector.collect()

        assert expected_key in stats
        assert stats[expected_key].total_commits == expected_commits
        assert stats[expected_key].additions == expected_additions

    def test_should_merge_by_email_when_email_is_used_as_key(self):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()

        client.get_projects = Mock(return_value=[{"id": 1, "name": "test-proj"}])

        commits = [
            {
                "id": "commit_1",
                "parent_ids": ["parent_1"],
                "title": "feat: feature 1",
                "author_name": "Alice",
                "author_email": "alice@example.com",
                "stats": {"additions": 100, "deletions": 10},
            },
            {
                "id": "commit_2",
                "parent_ids": ["parent_2"],
                "title": "feat: feature 2",
                "author_name": "Alice",
                "author_email": "alice@example.com",
                "stats": {"additions": 50, "deletions": 5},
            },
        ]

        client.get_commits = Mock(return_value=commits)

        collector = CodeStatsCollector(client)
        stats = collector.collect()

        assert "alice@example.com" in stats
        assert stats["alice@example.com"].total_commits == 2
        assert stats["alice@example.com"].additions == 150
        assert stats["alice@example.com"].deletions == 15


class TestEmptyRepository:
    """空仓库（无 commit）"""

    @pytest.mark.parametrize("commits_response,expected_stats_count", [
        ([], 0),
    ])
    def test_should_not_raise_exception_when_repository_has_no_commits(
        self, commits_response, expected_stats_count
    ):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()

        client.get_projects = Mock(return_value=[{"id": 1, "name": "empty-project"}])
        client.get_commits = Mock(return_value=commits_response)

        collector = CodeStatsCollector(client)
        stats = collector.collect()

        assert len(stats) == expected_stats_count

    @pytest.mark.parametrize("projects_response,expected_stats_count", [
        ([], 0),
    ])
    def test_should_not_raise_exception_when_no_projects_exist(
        self, projects_response, expected_stats_count
    ):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()

        client.get_projects = Mock(return_value=projects_response)

        collector = CodeStatsCollector(client)
        stats = collector.collect()

        assert len(stats) == expected_stats_count

    @pytest.mark.parametrize("api_error_status", [403, 404])
    def test_should_skip_project_gracefully_when_api_returns_error(
        self, api_error_status
    ):
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()

        client.get_projects = Mock(return_value=[
            {"id": 1, "name": "forbidden-project"},
            {"id": 2, "name": "not-found-project"},
        ])
        client._request = Mock(
            side_effect=GitLabAPIError(api_error_status, "error")
        )

        collector = CodeStatsCollector(client)
        stats = collector.collect()

        assert len(stats) == 0
