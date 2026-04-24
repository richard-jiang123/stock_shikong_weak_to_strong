# tests/test_data_completeness.py
"""测试数据完整性检查功能"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestDataCompleteness(unittest.TestCase):
    """测试 ensure_data_complete 方法"""

    def setUp(self):
        """测试前准备"""
        from data_layer import get_data_layer
        self.dl = get_data_layer()

    def test_check_stock_completeness_no_missing(self):
        """测试：无缺失股票时返回空列表"""
        # TODO: 实现后补充
        pass

    def test_check_index_completeness_all_present(self):
        """测试：所有必须指数都有数据"""
        # TODO: 实现后补充
        pass

    def test_check_stock_completeness_returns_dict(self):
        """测试返回结构正确"""
        result = self.dl._check_stock_completeness('2026-04-24')
        self.assertIn('lagging', result)
        self.assertIn('no_record', result)
        self.assertIsInstance(result['lagging'], list)
        self.assertIsInstance(result['no_record'], list)

    def test_check_index_completeness_returns_dict(self):
        """测试返回结构正确"""
        result = self.dl._check_index_completeness('2026-04-24')
        self.assertIn('required_missing', result)
        self.assertIn('optional_missing', result)
        self.assertIsInstance(result['required_missing'], list)
        self.assertIsInstance(result['optional_missing'], list)

    def test_prompt_user_non_interactive_returns_false(self):
        """测试非交互模式直接返回 False"""
        # 模拟非交互模式：stdin 不是 tty
        import io
        old_stdin = sys.stdin
        sys.stdin = io.StringIO()  # StringIO 没有 isatty 方法，会返回 False

        result = self.dl._prompt_user_continue({'stocks': [], 'indexes': []}, timeout_seconds=5)
        self.assertFalse(result)

        sys.stdin = old_stdin

    def test_update_missing_data_returns_counts(self):
        """测试返回更新计数正确"""
        # 用空列表测试返回结构
        result = self.dl._update_missing_data(
            lagging_stocks=[],
            no_record_stocks=[],
            missing_indexes=[]
        )
        self.assertIn('stocks_updated', result)
        self.assertIn('stocks_empty', result)
        self.assertIn('stocks_failed', result)
        self.assertIn('indexes_updated', result)

    def test_ensure_data_complete_returns_tuple(self):
        """测试返回结构正确"""
        is_complete, missing_info = self.dl.ensure_data_complete('2026-04-24', max_retries=0)
        self.assertIsInstance(is_complete, bool)
        self.assertIsInstance(missing_info, dict)
        self.assertIn('stocks', missing_info)
        self.assertIn('indexes', missing_info)

if __name__ == '__main__':
    unittest.main()