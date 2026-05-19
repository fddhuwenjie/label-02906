"""集成测试：通过 HTTP 连接到 Mock GitLab Docker 服务，验证完整统计流程"""

import pytest
import sys
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from gitlab_client import GitLabClient
from stats_collector import CodeStatsCollector
from exporter import StatsExporter

MOCK_GITLAB_URL = os.environ.get("GITLAB_URL", "http://localhost:8906")
MOCK_TOKEN = os.environ.get("GITLAB_TOKEN", "glpat-mock-token-for-testing")


def _is_service_available(url: str, token: str) -> bool:
    """检查 Mock GitLab 服务是否可达"""
    try:
        resp = requests.get(
            f"{url}/api/v4/projects",
            headers={"PRIVATE-TOKEN": token},
            timeout=3,
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


@pytest.fixture(scope="session")
def mock_gitlab_service():
    """确认 Mock GitLab Docker 服务可用"""
    if not _is_service_available(MOCK_GITLAB_URL, MOCK_TOKEN):
        pytest.skip(
            f"Mock GitLab 服务不可用 ({MOCK_GITLAB_URL})，请先启动: "
            "docker compose up mock-gitlab"
        )
    return {"url": MOCK_GITLAB_URL, "token": MOCK_TOKEN}


@pytest.fixture
def real_client(mock_gitlab_service):
    """连接到 Mock GitLab 服务的真实 GitLabClient"""
    client = GitLabClient(
        mock_gitlab_service["url"],
        mock_gitlab_service["token"],
        timeout=10,
    )
    return client


class TestFullCollectionFlow:
    """验证完整统计流程（从获取项目列表到输出最终统计结果）"""

    def test_should_collect_all_projects_and_authors_when_running_full_statistics(self, real_client):
        """完整统计流程应收集到所有项目和作者数据"""
        collector = CodeStatsCollector(real_client)
        stats = collector.collect()

        assert len(stats) > 0

        emails = set(stats.keys())
        expected_emails = {
            "zhangsan@example.com",
            "lisi@example.com",
            "wangwu@example.com",
            "zhaoliu@example.com",
        }
        assert expected_emails.issubset(emails), f"期望包含 {expected_emails - emails}"

    @pytest.mark.parametrize("email,expected_additions,expected_deletions,expected_commits", [
        ("zhangsan@example.com", 1220, 395, 3),
        ("lisi@example.com", 250, 80, 3),
        ("wangwu@example.com", 510, 80, 2),
        ("zhaoliu@example.com", 275, 50, 2),
    ])
    def test_should_calculate_correct_author_stats_when_projects_contain_filtered_commits(
        self, real_client, email, expected_additions, expected_deletions, expected_commits
    ):
        """各作者的新增/删除行数与提交数应与预期一致（merge/revert/squash/cherry-pick 已被过滤）"""
        collector = CodeStatsCollector(real_client)
        stats = collector.collect()

        assert email in stats, f"未找到 {email} 的统计"
        author = stats[email]
        assert author.additions == expected_additions, f"{email} 新增行数: 期望 {expected_additions}, 实际 {author.additions}"
        assert author.deletions == expected_deletions, f"{email} 删除行数: 期望 {expected_deletions}, 实际 {author.deletions}"
        assert author.total_commits == expected_commits, f"{email} 提交数: 期望 {expected_commits}, 实际 {author.total_commits}"


class TestNamespaceFilterIntegration:
    """验证 --namespace 过滤功能"""

    def test_should_filter_projects_by_namespace_when_namespace_pattern_is_provided(self, mock_gitlab_service):
        """指定 namespace 应只返回匹配 namespace 的项目"""
        client = GitLabClient(
            mock_gitlab_service["url"],
            mock_gitlab_service["token"],
            namespace_pattern=r"backend",
        )
        collector = CodeStatsCollector(client)
        stats = collector.collect()

        projects = collector.client.get_projects()
        namespaces = {p["namespace"]["full_path"] for p in projects}
        assert all("backend" in ns for ns in namespaces), f"存在非 backend namespace: {namespaces}"

        assert len(stats) > 0

    def test_should_return_empty_stats_when_namespace_pattern_matches_nothing(self, mock_gitlab_service):
        """namespace 无匹配时应返回空统计"""
        client = GitLabClient(
            mock_gitlab_service["url"],
            mock_gitlab_service["token"],
            namespace_pattern=r"^non_existent_namespace$",
        )
        collector = CodeStatsCollector(client)
        stats = collector.collect()

        assert len(stats) == 0


class TestIncrementalModeIntegration:
    """验证 --incremental 增量模式"""

    def test_should_load_and_extend_cached_stats_when_incremental_mode_is_enabled(self, mock_gitlab_service):
        """增量模式应加载已有缓存并追加新统计"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = GitLabClient(mock_gitlab_service["url"], mock_gitlab_service["token"])

            existing_stats = {
                "alice_old@example.com": {
                    "author_name": "Alice Old",
                    "author_email": "alice_old@example.com",
                    "additions": 100,
                    "deletions": 20,
                    "total_commits": 1,
                    "projects": ["legacy-project"],
                }
            }
            cache_path = Path(tmpdir) / CodeStatsCollector.CACHE_FILE
            cache_data = {
                "version": "1.0",
                "last_updated": "2025-01-01T00:00:00",
                "projects_cursor": {},
                "author_stats": existing_stats,
            }
            cache_path.write_text(json.dumps(cache_data))

            collector = CodeStatsCollector(client, incremental=True, cache_dir=tmpdir)
            stats = collector.collect()

            assert "alice_old@example.com" in stats
            assert stats["alice_old@example.com"].additions == 100

            for email, values in existing_stats.items():
                assert email in stats
                assert stats[email].total_commits >= values["total_commits"]

            assert cache_path.exists()
            saved_cache = json.loads(cache_path.read_text())
            assert "projects_cursor" in saved_cache
            assert len(saved_cache["projects_cursor"]) > 0

    def test_should_stop_at_cached_cursor_when_incremental_mode_finds_cursor_sha(self, mock_gitlab_service):
        """增量模式遇到 cursor SHA 时应停止获取"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = GitLabClient(mock_gitlab_service["url"], mock_gitlab_service["token"])

            cache_path = Path(tmpdir) / CodeStatsCollector.CACHE_FILE
            cache_data = {
                "version": "1.0",
                "last_updated": "2025-01-01T00:00:00",
                "projects_cursor": {
                    "1": "d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5",
                },
                "author_stats": {},
            }
            cache_path.write_text(json.dumps(cache_data))

            collector = CodeStatsCollector(client, incremental=True, cache_dir=tmpdir)
            stats = collector.collect()

            assert cache_path.exists()
            saved_cache = json.loads(cache_path.read_text())
            assert "projects_cursor" in saved_cache


class TestCSVExportIntegration:
    """验证 CSV 导出格式"""

    def test_should_export_valid_csv_when_stats_are_provided(self, real_client):
        """CSV 导出应包含正确的表头和作者统计"""
        collector = CodeStatsCollector(real_client)
        stats = collector.collect()

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            csv_path = f.name

        try:
            StatsExporter.to_csv(stats, csv_path)

            content = Path(csv_path).read_text(encoding='utf-8')
            lines = content.strip().split('\n')

            header = lines[0]
            assert '作者' in header or 'author' in header.lower() or 'Author' in header
            assert '新增' in header or 'additions' in header.lower() or 'Additions' in header
            assert '删除' in header or 'deletions' in header.lower() or 'Deletions' in header

            assert len(lines) > 1

            author_rows = lines[1:]
            emails_in_csv = set()
            for row in author_rows:
                fields = row.split(',')
                if len(fields) >= 2:
                    emails_in_csv.add(fields[1])

            expected_emails = {"zhangsan@example.com", "lisi@example.com", "wangwu@example.com", "zhaoliu@example.com"}
            assert expected_emails.issubset(emails_in_csv), f"CSV 缺少预期邮箱: {expected_emails - emails_in_csv}"
        finally:
            Path(csv_path).unlink(missing_ok=True)

    def test_should_sort_by_total_lines_when_exporting_csv(self, real_client):
        """CSV 应按新增+删除行数降序排列"""
        collector = CodeStatsCollector(real_client)
        stats = collector.collect()

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            csv_path = f.name

        try:
            StatsExporter.to_csv(stats, csv_path)

            content = Path(csv_path).read_text(encoding='utf-8')
            lines = content.strip().split('\n')

            prev_net = None
            for row in lines[1:]:
                fields = row.split(',')
                additions = int(fields[2])
                deletions = int(fields[3])
                total = additions + deletions
                if prev_net is not None:
                    assert total <= prev_net, f"CSV 排序错误: 前项 {prev_net}, 当前 {total}"
                prev_net = total
        finally:
            Path(csv_path).unlink(missing_ok=True)
