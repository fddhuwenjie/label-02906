"""统计结果导出器"""

import csv
import logging
from typing import Dict

from models import AuthorStats

logger = logging.getLogger(__name__)


class StatsExporter:
    """统计结果导出器"""
    
    @staticmethod
    def to_csv(stats: Dict[str, AuthorStats], output_file: str) -> None:
        sorted_stats = sorted(
            stats.values(), 
            key=lambda x: x.additions + x.deletions, 
            reverse=True
        )
        
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                '作者', '邮箱', '新增行数', '删除行数', 
                '净增行数', '提交次数', '参与项目数', '参与项目'
            ])
            for author in sorted_stats:
                writer.writerow([
                    author.author_name,
                    author.author_email,
                    author.additions,
                    author.deletions,
                    author.additions - author.deletions,
                    author.total_commits,
                    len(author.projects),
                    '; '.join(sorted(author.projects))
                ])
        
        logger.info(f"统计结果已导出到: {output_file}")
    
    @staticmethod
    def to_table(stats: Dict[str, AuthorStats]) -> None:
        sorted_stats = sorted(
            stats.values(), 
            key=lambda x: x.additions + x.deletions, 
            reverse=True
        )
        
        print("\n" + "=" * 100)
        print(f"{'作者':<20} {'邮箱':<30} {'新增':>10} {'删除':>10} {'净增':>10} {'提交':>8} {'项目数':>8}")
        print("=" * 100)
        
        total_additions = 0
        total_deletions = 0
        total_commits = 0
        
        for author in sorted_stats:
            net = author.additions - author.deletions
            print(f"{author.author_name:<20} {author.author_email:<30} "
                  f"{author.additions:>10} {author.deletions:>10} {net:>10} "
                  f"{author.total_commits:>8} {len(author.projects):>8}")
            total_additions += author.additions
            total_deletions += author.deletions
            total_commits += author.total_commits
        
        print("=" * 100)
        print(f"{'合计':<20} {'':<30} {total_additions:>10} {total_deletions:>10} "
              f"{total_additions - total_deletions:>10} {total_commits:>8}")
        print("=" * 100)
