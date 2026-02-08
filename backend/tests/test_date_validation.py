"""单元测试：日期范围校验"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 导入校验函数
from gitlab_stats import validate_date_range, parse_date


class TestDateValidation:
    """测试日期范围校验"""
    
    def test_valid_date_range(self):
        """有效的日期范围不应抛出异常"""
        validate_date_range("2025-01-01T00:00:00", "2025-12-31T00:00:00")
    
    def test_same_date(self):
        """相同日期应该有效"""
        validate_date_range("2025-06-15T00:00:00", "2025-06-15T00:00:00")
    
    def test_invalid_date_range(self):
        """since > until 应抛出异常"""
        with pytest.raises(ValueError, match="日期范围无效"):
            validate_date_range("2025-12-31T00:00:00", "2025-01-01T00:00:00")
    
    def test_none_since(self):
        """since 为 None 时不校验"""
        validate_date_range(None, "2025-12-31T00:00:00")
    
    def test_none_until(self):
        """until 为 None 时不校验"""
        validate_date_range("2025-01-01T00:00:00", None)
    
    def test_both_none(self):
        """都为 None 时不校验"""
        validate_date_range(None, None)


class TestParseDate:
    """测试日期解析"""
    
    def test_valid_date(self):
        """有效日期格式"""
        result = parse_date("2025-06-15")
        assert result == "2025-06-15T00:00:00"
    
    def test_invalid_format(self):
        """无效日期格式应抛出异常"""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError, match="日期格式错误"):
            parse_date("15-06-2025")
    
    def test_invalid_date(self):
        """无效日期应抛出异常"""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError):
            parse_date("2025-13-01")  # 月份无效
