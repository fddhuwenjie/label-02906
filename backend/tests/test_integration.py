"""集成测试：端到端流程测试（使用真实 Mock GitLab 服务）"""

import pytest
import sys
import os
import json
import tempfile
import time
from pathlib import Path
from typing import Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitlab_client import GitLabClient
from stats_collector import CodeStatsCollector
from exporter import StatsExporter


@pytest.fixture(scope="session")
def mock_gitlab_server():
    """连接到已运行的 Mock GitLab 服务（Docker Compose 环境）"""
    url = os.environ.get("GITLAB_URL", "http://mock-gitlab:8080")
    token = os.environ.get("GITLAB_TOKEN", "glpat-mock-token-for-testing")
    
    max_retries = 30
    for i in range(max_retries):
        try:
            import urllib.request
            req = urllib.request.Request(f"{url}/api/v4/projects")
            req.add_header("PRIVATE-TOKEN", token)
            urllib.request.urlopen(req, timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError(f"无法连接到 Mock GitLab 服务: {url}")
    
    yield {"url": url, "token": token}


@pytest.fixture
def gitlab_client(mock_gitlab_server):
    """创建连接到 Mock 服务的 GitLabClient"""
    client = GitLabClient(
        mock_gitlab_server["url"],
        mock_gitlab_server["token"]
    )
    return client


@pytest.fixture
def temp_cache_dir():
    """创建临时缓存目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestFullStatsFlow:
    """测试完整的统计流程"""

    @pytest.mark.parametrize("expected_authors", [
        {
            "zhangsan@example.com": {"commits": 3, "additions": 1220, "deletions": 395},
            "lisi@example.com": {"commits": 3, "additions": 250, "deletions": 80},
            "wangwu@example.com": {"commits": 2, "additions": 510, "deletions": 80},
            "zhaoliu@example.com": {"commits": 2, "additions": 275, "deletions": 50},
        }
    ])
    def test_should_collect_all_valid_commits_when_running_full_stats(self, gitlab_client, expected_authors):
        collector = CodeStatsCollector(gitlab_client)
        stats = collector.collect()

        assert len(stats) == len(expected_authors)
        for email, expected in expected_authors.items():
            assert email in stats
            assert stats[email].total_commits == expected["commits"]
            assert stats[email].additions == expected["additions"]
            assert stats[email].deletions == expected["deletions"]

    def test_should_filter_merge_revert_squash_cherry_pick_commits(self, gitlab_client):
        collector = CodeStatsCollector(gitlab_client)
        stats = collector.collect()

        total_commits = sum(s.total_commits for s in stats.values())
        assert total_commits == 10

        zhangsan = stats.get("zhangsan@example.com")
        assert zhangsan is not None
        assert zhangsan.total_commits == 3

    @pytest.mark.parametrize("project_name,expected_in_projects", [
        ("home-gateway", ["zhangsan@example.com", "lisi@example.com"]),
        ("mobile-app", ["wangwu@example.com", "zhaoliu@example.com"]),
        ("device-firmware", ["zhangsan@example.com", "lisi@example.com"]),
        ("docs", ["zhaoliu@example.com", "wangwu@example.com"]),
    ])
    def test_should_track_project_participation_for_each_author(self, gitlab_client, project_name, expected_in_projects):
        collector = CodeStatsCollector(gitlab_client)
        stats = collector.collect()

        for email in expected_in_projects:
            assert email in stats
            assert project_name in stats[email].projects


class TestNamespaceFiltering:
    """测试 Namespace 过滤功能"""

    @pytest.mark.parametrize("namespace_pattern,expected_project_count,expected_authors", [
        (
            r"smart-home/backend",
            2,
            ["zhangsan@example.com", "lisi@example.com"]
        ),
        (
            r"smart-home/frontend",
            1,
            ["wangwu@example.com", "zhaoliu@example.com"]
        ),
        (
            r"smart-home/docs",
            1,
            ["zhaoliu@example.com", "wangwu@example.com"]
        ),
    ])
    def test_should_filter_projects_by_namespace_when_pattern_provided(
        self,
        mock_gitlab_server,
        namespace_pattern,
        expected_project_count,
        expected_authors
    ):
        client = GitLabClient(
            mock_gitlab_server["url"],
            mock_gitlab_server["token"],
            namespace_pattern=namespace_pattern
        )
        
        projects = client.get_projects()
        assert len(projects) == expected_project_count

        collector = CodeStatsCollector(client)
        stats = collector.collect()

        assert len(stats) == len(expected_authors)
        for email in expected_authors:
            assert email in stats


class TestIncrementalMode:
    """测试增量统计模式"""

    def test_should_save_cache_after_first_run_when_incremental_enabled(self, gitlab_client, temp_cache_dir):
        collector = CodeStatsCollector(
            gitlab_client,
            incremental=True,
            cache_dir=temp_cache_dir
        )
        stats = collector.collect()

        cache_path = Path(temp_cache_dir) / CodeStatsCollector.CACHE_FILE
        assert cache_path.exists()

        cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
        assert "projects_cursor" in cache_data
        assert len(cache_data["projects_cursor"]) == 4
        assert "version" in cache_data
        assert cache_data["version"] == "1.0"

    def test_should_load_existing_cache_when_incremental_enabled(self, gitlab_client, temp_cache_dir):
        pre_existing_stats = {
            "existing@example.com": {
                "author_name": "Existing User",
                "author_email": "existing@example.com",
                "additions": 100,
                "deletions": 50,
                "total_commits": 5,
                "projects": ["old-project"]
            }
        }
        cache_path = Path(temp_cache_dir) / CodeStatsCollector.CACHE_FILE
        cache_data = {
            "version": "1.0",
            "last_updated": "2025-01-01T00:00:00",
            "projects_cursor": {},
            "author_stats": pre_existing_stats
        }
        cache_path.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")

        collector = CodeStatsCollector(
            gitlab_client,
            incremental=True,
            cache_dir=temp_cache_dir
        )

        assert "existing@example.com" in collector.author_stats
        assert collector.author_stats["existing@example.com"].additions == 100

    @pytest.mark.parametrize("expected_combined", [
        {"additions": 1320, "deletions": 445, "commits": 8}
    ])
    def test_should_merge_new_stats_with_cached_stats(self, gitlab_client, temp_cache_dir, expected_combined):
        pre_existing_stats = {
            "zhangsan@example.com": {
                "author_name": "张三",
                "author_email": "zhangsan@example.com",
                "additions": 100,
                "deletions": 50,
                "total_commits": 5,
                "projects": ["old-project"]
            }
        }
        cache_path = Path(temp_cache_dir) / CodeStatsCollector.CACHE_FILE
        cache_data = {
            "version": "1.0",
            "last_updated": "2025-01-01T00:00:00",
            "projects_cursor": {},
            "author_stats": pre_existing_stats
        }
        cache_path.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")

        collector = CodeStatsCollector(
            gitlab_client,
            incremental=True,
            cache_dir=temp_cache_dir
        )
        stats = collector.collect()

        zhangsan = stats.get("zhangsan@example.com")
        assert zhangsan is not None
        assert zhangsan.additions == expected_combined["additions"]
        assert zhangsan.deletions == expected_combined["deletions"]
        assert zhangsan.total_commits == expected_combined["commits"]
        assert "old-project" in zhangsan.projects


class TestCSVExport:
    """测试 CSV 导出功能"""

    @pytest.fixture
    def sample_stats(self, gitlab_client):
        collector = CodeStatsCollector(gitlab_client)
        return collector.collect()

    @pytest.mark.parametrize("expected_columns", [
        ['作者', '邮箱', '新增行数', '删除行数', '净增行数', '提交次数', '参与项目数', '参与项目']
    ])
    def test_should_export_csv_with_correct_headers(self, sample_stats, expected_columns):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            csv_path = f.name

        try:
            StatsExporter.to_csv(sample_stats, csv_path)

            assert Path(csv_path).exists()
            
            import csv
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                headers = next(reader)
                assert headers == expected_columns
        finally:
            Path(csv_path).unlink(missing_ok=True)

    def test_should_include_all_authors_in_csv_output(self, sample_stats):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            csv_path = f.name

        try:
            StatsExporter.to_csv(sample_stats, csv_path)

            content = Path(csv_path).read_text(encoding='utf-8')
            
            assert "张三" in content
            assert "李四" in content
            assert "王五" in content
            assert "赵六" in content
            
            assert "zhangsan@example.com" in content
            assert "lisi@example.com" in content
            assert "wangwu@example.com" in content
            assert "zhaoliu@example.com" in content
        finally:
            Path(csv_path).unlink(missing_ok=True)

    @pytest.mark.parametrize("author_email,expected_values", [
        ("zhangsan@example.com", {"additions": "1220", "deletions": "395", "commits": "3"}),
        ("lisi@example.com", {"additions": "250", "deletions": "80", "commits": "3"}),
    ])
    def test_should_contain_correct_numeric_values_in_csv(
        self,
        sample_stats,
        author_email,
        expected_values
    ):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            csv_path = f.name

        try:
            StatsExporter.to_csv(sample_stats, csv_path)

            import csv
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                found = False
                for row in reader:
                    if row["邮箱"] == author_email:
                        assert row["新增行数"] == expected_values["additions"]
                        assert row["删除行数"] == expected_values["deletions"]
                        assert row["提交次数"] == expected_values["commits"]
                        found = True
                        break
                assert found, f"未在 CSV 中找到 {author_email}"
        finally:
            Path(csv_path).unlink(missing_ok=True)


class TestDateRangeFiltering:
    """测试日期范围过滤"""

    @pytest.mark.parametrize("since,until,expected_min_commits,expected_max_commits", [
        ("2025-03-01T00:00:00", "2025-03-31T23:59:59", 5, 15),
        ("2025-03-10T00:00:00", "2025-03-13T23:59:59", 1, 5),
    ])
    def test_should_filter_commits_by_date_range(
        self,
        gitlab_client,
        since,
        until,
        expected_min_commits,
        expected_max_commits
    ):
        collector = CodeStatsCollector(
            gitlab_client,
            since=since,
            until=until
        )
        stats = collector.collect()

        total_commits = sum(s.total_commits for s in stats.values())
        assert expected_min_commits <= total_commits <= expected_max_commits
