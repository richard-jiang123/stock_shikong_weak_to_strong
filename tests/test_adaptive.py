#!/usr/bin/env python3
"""测试自适应量化系统组件"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_constants import (
    SIGNAL_TYPE_MAPPING,
    normalize_signal_type,
    get_display_name,
    get_weight_multiplier,
)


class TestSignalConstants(unittest.TestCase):
    """测试信号类型常量模块"""

    def test_signal_type_mapping_has_four_signals(self):
        """测试：四种信号类型定义"""
        self.assertEqual(len(SIGNAL_TYPE_MAPPING), 4)
        self.assertIn('anomaly_no_decline', SIGNAL_TYPE_MAPPING)
        self.assertIn('bullish_engulfing', SIGNAL_TYPE_MAPPING)
        self.assertIn('big_bullish_reversal', SIGNAL_TYPE_MAPPING)
        self.assertIn('limit_up_open_next_strong', SIGNAL_TYPE_MAPPING)

    def test_normalize_signal_type_from_chinese(self):
        """测试：中文转英文主键"""
        result = normalize_signal_type('异动不跌')
        self.assertEqual(result, 'anomaly_no_decline')

    def test_normalize_signal_type_from_english(self):
        """测试：英文主键不变"""
        result = normalize_signal_type('anomaly_no_decline')
        self.assertEqual(result, 'anomaly_no_decline')

    def test_get_display_name(self):
        """测试：英文转中文显示"""
        result = get_display_name('anomaly_no_decline')
        self.assertEqual(result, '异动不跌')

    def test_get_weight_multiplier(self):
        """测试：状态权重乘数"""
        self.assertEqual(get_weight_multiplier('active'), 1.0)
        self.assertEqual(get_weight_multiplier('watching'), 0.5)
        self.assertEqual(get_weight_multiplier('warning'), 0.2)
        self.assertEqual(get_weight_multiplier('disabled'), 0.0)


class TestDatabaseTables(unittest.TestCase):
    """测试数据库表创建"""

    def setUp(self):
        """测试前准备"""
        import sqlite3
        import tempfile
        import os
        # 使用临时文件数据库（:memory: 每个连接独立）
        self.temp_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self.temp_file.name
        self.temp_file.close()
        self.conn = sqlite3.connect(self.db_path)

    def tearDown(self):
        """测试后清理"""
        self.conn.close()
        import os
        try:
            os.unlink(self.db_path)
        except:
            pass

    def test_create_signal_status_table(self):
        """测试：signal_status表创建"""
        from data_layer import StockDataLayer
        dl = StockDataLayer(self.db_path)
        dl._create_adaptive_tables()

        # 检查表存在
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_status'"
        ).fetchall()
        self.assertEqual(len(tables), 1)

    def test_create_optimization_history_table(self):
        """测试：optimization_history表创建"""
        from data_layer import StockDataLayer
        dl = StockDataLayer(self.db_path)
        dl._create_adaptive_tables()

        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='optimization_history'"
        ).fetchall()
        self.assertEqual(len(tables), 1)

    def test_create_market_regime_table(self):
        """测试：market_regime表创建"""
        from data_layer import StockDataLayer
        dl = StockDataLayer(self.db_path)
        dl._create_adaptive_tables()

        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='market_regime'"
        ).fetchall()
        self.assertEqual(len(tables), 1)

    def test_create_daily_monitor_log_table(self):
        """测试：daily_monitor_log表创建"""
        from data_layer import StockDataLayer
        dl = StockDataLayer(self.db_path)
        dl._create_adaptive_tables()

        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_monitor_log'"
        ).fetchall()
        self.assertEqual(len(tables), 1)


if __name__ == '__main__':
    unittest.main()