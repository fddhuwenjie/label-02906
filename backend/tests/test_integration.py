"""集成测试：端到端流程测试"""

import csv
import json
import pytest
import sys
import os
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitlab_client import GitLabClient
from stats_collector import CodeStatsCollector
from exporter import StatsExporter

MOCK_GITLAB_URL = os.environ.get("GITLAB_URL", "http://mock-gitlab:8080")
MOCK_GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "glpat-mock-token-for-testing")


def _wait_for_service(url, token, timeout=30):
    import requests as req
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = req.get(
                f"{url}/api/v4/projects",
                headers={"PRIVATE-TOKEN": token},
                timeout=3,
            )
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def mock_gitlab_server():
    """等待 Docker 网络中的 mock-gitlab 服务就绪"""
    if not _wait_for_service(MOCK_GITLAB_URL, MOCK_GITLAB_TOKEN):
        pytest.skip("mock-gitlab 服务不可用，请先运行 docker compose up")
    yield MOCK_GITLAB_URL


@pytest.fixture
def gitlab_client(mock_gitlab_server):
    """创建连接到 mock-gitlab 容器的客户端"""
    return GitLabClient(
        mock_gitlab_server,
        MOCK_GITLAB_TOKEN,
        timeout=15,
    )


@pytest.fixture
def tmp_dir():
    """创建临时目录用于缓存和导出"""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def first_project_info(gitlab_client):
    """获取第一个项目及其最新 commit 的信息"""
    projects = gitlab_client.get_projects()
    assert len(projects) > 0
    first_project = projects[0]
    first_project_id = str(first_project["id"])
    commits = gitlab_client.get_commits(
        first_project["id"], first_project["name"]
    )
    assert len(commits) > 0
    first_commit_sha = commits[0]["id"]
    return first_project_id, first_commit_sha


class TestFullStatisticsFlow:
    """完整统计流程测试"""

    def test_should_produce_complete_statistics_when_collecting_all_projects(
        self, gitlab_client, tmp_dir
    ):
        collector = CodeStatsCollector(gitlab_client, cache_dir=tmp_dir)
        stats = collector.collect()

        assert len(stats) > 0

        expected_authors = {"zhangsan@example.com", "lisi@example.com",
                            "wangwu@example.com", "zhaoliu@example.com"}
        actual_emails = set(stats.keys())
        assert actual_emails == expected_authors

    @pytest.mark.parametrize("author_email,expected_min_additions,expected_min_deletions", [
        ("zhangsan@example.com", 100, 10),
        ("lisi@example.com", 10, 10),
        ("wangwu@example.com", 100, 10),
        ("zhaoliu@example.com", 10, 10),
    ])
    def test_should_aggregate_correct_stats_per_author_when_commits_collected(
        self, gitlab_client, tmp_dir, author_email, expected_min_additions, expected_min_deletions
    ):
        collector = CodeStatsCollector(gitlab_client, cache_dir=tmp_dir)
        stats = collector.collect()

        assert author_email in stats
        author = stats[author_email]
        assert author.additions >= expected_min_additions
        assert author.deletions >= expected_min_deletions

    def test_should_filter_non_code_commits_when_collecting(
        self, gitlab_client, tmp_dir
    ):
        collector = CodeStatsCollector(gitlab_client, cache_dir=tmp_dir)
        stats = collector.collect()

        zhangsan = stats.get("zhangsan@example.com")
        assert zhangsan is not None
        assert zhangsan.total_commits == 3

        wangwu = stats.get("wangwu@example.com")
        assert wangwu is not None
        assert wangwu.total_commits == 2


class TestNamespaceFiltering:
    """--namespace 过滤测试"""

    @pytest.mark.parametrize("namespace_pattern,expected_project_count", [
        ("backend", 2),
        ("frontend", 1),
        ("docs", 1),
    ])
    def test_should_filter_projects_by_namespace_when_namespace_pattern_provided(
        self, mock_gitlab_server, tmp_dir, namespace_pattern, expected_project_count
    ):
        client = GitLabClient(
            mock_gitlab_server,
            MOCK_GITLAB_TOKEN,
            timeout=15,
            namespace_pattern=namespace_pattern,
        )
        projects = client.get_projects()
        assert len(projects) == expected_project_count

        collector = CodeStatsCollector(client, cache_dir=tmp_dir)
        stats = collector.collect()

        for author in stats.values():
            for proj in author.projects:
                if namespace_pattern == "backend":
                    assert proj in ("home-gateway", "device-firmware")
                elif namespace_pattern == "frontend":
                    assert proj == "mobile-app"
                elif namespace_pattern == "docs":
                    assert proj == "docs"

    @pytest.mark.parametrize("namespace_pattern,expected_count", [
        ("nonexistent", 0),
        ("smart-home", 4),
    ])
    def test_should_return_correct_project_count_when_namespace_filter_applied(
        self, mock_gitlab_server, namespace_pattern, expected_count
    ):
        client = GitLabClient(
            mock_gitlab_server,
            MOCK_GITLAB_TOKEN,
            timeout=15,
            namespace_pattern=namespace_pattern,
        )
        projects = client.get_projects()
        assert len(projects) == expected_count


class TestIncrementalMode:
    """--incremental 增量模式测试"""

    def test_should_save_cache_after_collection_when_incremental_enabled(
        self, gitlab_client, tmp_dir
    ):
        collector = CodeStatsCollector(
            gitlab_client, incremental=True, cache_dir=tmp_dir
        )
        stats = collector.collect()

        cache_path = Path(tmp_dir) / CodeStatsCollector.CACHE_FILE
        assert cache_path.exists()

        cache_data = json.loads(cache_path.read_text(encoding='utf-8'))
        assert cache_data["version"] == "1.0"
        assert "projects_cursor" in cache_data
        assert len(cache_data["projects_cursor"]) > 0

    def test_should_accumulate_stats_when_running_incremental_twice(
        self, gitlab_client, tmp_dir
    ):
        collector1 = CodeStatsCollector(
            gitlab_client, incremental=True, cache_dir=tmp_dir
        )
        stats1 = collector1.collect()
        first_additions = {
            k: v.additions for k, v in stats1.items()
        }

        collector2 = CodeStatsCollector(
            gitlab_client, incremental=True, cache_dir=tmp_dir
        )
        stats2 = collector2.collect()

        for email, author in stats2.items():
            assert author.additions >= first_additions.get(email, 0)

    @pytest.mark.parametrize("incremental_flag", [True, False])
    def test_should_respect_incremental_flag_when_collecting(
        self, gitlab_client, tmp_dir, incremental_flag
    ):
        collector = CodeStatsCollector(
            gitlab_client, incremental=incremental_flag, cache_dir=tmp_dir
        )
        stats = collector.collect()

        cache_path = Path(tmp_dir) / CodeStatsCollector.CACHE_FILE
        if incremental_flag:
            assert cache_path.exists()
        else:
            assert not cache_path.exists()

    def test_should_use_stop_at_sha_when_cache_has_previous_cursor(
        self, mock_gitlab_server, first_project_info, tmp_dir
    ):
        first_project_id, first_commit_sha = first_project_info

        cache_data = {
            "version": "1.0",
            "last_updated": "2025-03-01T00:00:00",
            "projects_cursor": {first_project_id: first_commit_sha},
            "author_stats": {
                "cached@example.com": {
                    "author_name": "Cached",
                    "author_email": "cached@example.com",
                    "additions": 999,
                    "deletions": 100,
                    "total_commits": 5,
                    "projects": ["old-project"],
                }
            },
        }
        cache_path = Path(tmp_dir) / CodeStatsCollector.CACHE_FILE
        cache_path.write_text(json.dumps(cache_data))

        client = GitLabClient(mock_gitlab_server, MOCK_GITLAB_TOKEN, timeout=15)
        collector = CodeStatsCollector(
            client, incremental=True, cache_dir=tmp_dir
        )
        stats = collector.collect()

        assert "cached@example.com" in stats
        assert stats["cached@example.com"].additions == 999


class TestCSVExport:
    """CSV 导出格式正确性"""

    @pytest.mark.parametrize("sort_key", ["additions", "total_commits"])
    def test_should_export_valid_csv_when_stats_collected(
        self, gitlab_client, tmp_dir, sort_key
    ):
        collector = CodeStatsCollector(gitlab_client, cache_dir=tmp_dir)
        stats = collector.collect()

        csv_path = os.path.join(tmp_dir, "stats.csv")
        StatsExporter.to_csv(stats, csv_path)

        assert Path(csv_path).exists()

        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            rows = list(reader)

        header = rows[0]
        assert header == ['作者', '邮箱', '新增行数', '删除行数',
                          '净增行数', '提交次数', '参与项目数', '参与项目']

        assert len(rows) == len(stats) + 1

    def test_should_include_all_authors_in_csv_when_exported(
        self, gitlab_client, tmp_dir
    ):
        collector = CodeStatsCollector(gitlab_client, cache_dir=tmp_dir)
        stats = collector.collect()

        csv_path = os.path.join(tmp_dir, "stats.csv")
        StatsExporter.to_csv(stats, csv_path)

        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            rows = list(reader)

        csv_emails = {row[1] for row in rows[1:]}
        stats_emails = set(stats.keys())
        assert csv_emails == stats_emails

    @pytest.mark.parametrize("field_index,field_name", [
        (2, "新增行数"),
        (3, "删除行数"),
        (4, "净增行数"),
        (5, "提交次数"),
        (6, "参与项目数"),
    ])
    def test_should_have_numeric_values_in_csv_when_exported(
        self, gitlab_client, tmp_dir, field_index, field_name
    ):
        collector = CodeStatsCollector(gitlab_client, cache_dir=tmp_dir)
        stats = collector.collect()

        csv_path = os.path.join(tmp_dir, "stats.csv")
        StatsExporter.to_csv(stats, csv_path)

        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            rows = list(reader)

        for row in rows[1:]:
            value = row[field_index]
            assert value.lstrip('-').isdigit(), (
                f"{field_name} 列应为整数，实际值: {value}"
            )

    def test_should_sort_by_total_changes_descending_when_csv_exported(
        self, gitlab_client, tmp_dir
    ):
        collector = CodeStatsCollector(gitlab_client, cache_dir=tmp_dir)
        stats = collector.collect()

        csv_path = os.path.join(tmp_dir, "stats.csv")
        StatsExporter.to_csv(stats, csv_path)

        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            rows = list(reader)

        total_changes = []
        for row in rows[1:]:
            additions = int(row[2])
            deletions = int(row[3])
            total_changes.append(additions + deletions)

        for i in range(1, len(total_changes)):
            assert total_changes[i - 1] >= total_changes[i]
