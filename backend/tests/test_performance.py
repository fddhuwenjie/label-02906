"""性能测试：大数据量场景"""

import pytest
import sys
import os
import time
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitlab_client import GitLabClient, RateLimiter
from stats_collector import CodeStatsCollector


class TestPerformance:
    """性能测试"""
    
    @pytest.fixture
    def large_commit_list(self):
        """生成大量提交数据"""
        commits = []
        for i in range(1000):
            commit_type = i % 10
            if commit_type == 0:
                # 10% merge commits
                parent_ids = [f"parent_{i}_1", f"parent_{i}_2"]
                title = f"Merge branch 'feature-{i}'"
            elif commit_type == 1:
                # 10% revert commits
                parent_ids = [f"parent_{i}"]
                title = f"Revert \"feat: feature {i}\""
            else:
                # 80% normal commits
                parent_ids = [f"parent_{i}"]
                title = f"feat: add feature {i}"
            
            commits.append({
                "id": f"commit_{i}",
                "parent_ids": parent_ids,
                "title": title,
                "message": title,
                "author_name": f"Author{i % 10}",
                "author_email": f"author{i % 10}@example.com",
                "stats": {"additions": 10 + i % 100, "deletions": 5 + i % 50}
            })
        return commits
    
    def test_filter_performance(self, large_commit_list):
        """测试过滤大量提交的性能"""
        start_time = time.time()
        
        filtered_count = 0
        for commit in large_commit_list:
            if not CodeStatsCollector.is_non_code_commit(commit):
                filtered_count += 1
        
        elapsed = time.time() - start_time
        
        # 1000 个提交应该在 0.1 秒内完成
        assert elapsed < 0.1, f"过滤 1000 个提交耗时 {elapsed:.3f}s，超过预期"
        
        # 验证过滤结果（80% 应该是有效提交）
        assert 750 < filtered_count < 850
    
    def test_stats_aggregation_performance(self):
        """测试统计聚合性能"""
        with patch.object(GitLabClient, '_create_session'):
            client = GitLabClient("https://gitlab.example.com", "test-token")
            client.rate_limiter = Mock()
            client.rate_limiter.wait = Mock()
            client.rate_limiter.on_success = Mock()
            
            # 模拟 100 个项目，每个项目 100 个提交
            projects = [{"id": i, "name": f"project-{i}"} for i in range(100)]
            
            def mock_commits(project_id, project_name, **kwargs):
                return [
                    {
                        "id": f"commit_{project_id}_{j}",
                        "parent_ids": [f"parent_{project_id}_{j}"],
                        "title": f"feat: feature {j}",
                        "author_name": f"Author{j % 20}",
                        "author_email": f"author{j % 20}@example.com",
                        "stats": {"additions": 10, "deletions": 5}
                    }
                    for j in range(100)
                ]
            
            client.get_projects = Mock(return_value=projects)
            client.get_commits = mock_commits
            
            collector = CodeStatsCollector(client)
            
            start_time = time.time()
            stats = collector.collect()
            elapsed = time.time() - start_time
            
            # 10000 个提交应该在 2 秒内完成
            assert elapsed < 2.0, f"处理 10000 个提交耗时 {elapsed:.3f}s，超过预期"
            
            # 验证结果
            assert len(stats) == 20  # 20 个不同作者
            total_commits = sum(s.total_commits for s in stats.values())
            assert total_commits == 10000


class TestRateLimiterPerformance:
    """速率限制器性能测试"""
    
    def test_rate_limiter_overhead(self):
        """测试速率限制器的开销"""
        limiter = RateLimiter(min_interval=0.001)  # 极短间隔用于测试
        
        mock_response = Mock()
        mock_response.headers = {'RateLimit-Remaining': '900', 'RateLimit-Limit': '1000'}
        
        start_time = time.time()
        for _ in range(1000):
            limiter.on_success(mock_response)
        elapsed = time.time() - start_time
        
        # 1000 次调用应该在 0.1 秒内完成
        assert elapsed < 0.1, f"1000 次 on_success 调用耗时 {elapsed:.3f}s"
    
    def test_adaptive_rate_adjustment(self):
        """测试自适应速率调整"""
        limiter = RateLimiter(min_interval=0.1, max_interval=10.0)
        # 先设置一个较高的初始值
        limiter.current_interval = 1.0
        initial_interval = limiter.current_interval
        
        # 模拟配额充足
        high_quota_response = Mock()
        high_quota_response.headers = {'RateLimit-Remaining': '900', 'RateLimit-Limit': '1000'}
        
        for _ in range(10):
            limiter.on_success(high_quota_response)
        
        # 应该加速
        assert limiter.current_interval < initial_interval
        
        # 模拟配额紧张
        low_quota_response = Mock()
        low_quota_response.headers = {'RateLimit-Remaining': '50', 'RateLimit-Limit': '1000'}
        
        interval_before = limiter.current_interval
        for _ in range(10):
            limiter.on_success(low_quota_response)
        
        # 应该减速
        assert limiter.current_interval > interval_before
