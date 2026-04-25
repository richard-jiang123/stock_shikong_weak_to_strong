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


class TestPickTrackingScoreFields(unittest.TestCase):
    """测试 pick_tracking 评分字段"""

    def setUp(self):
        """测试前准备"""
        import tempfile
        self.temp_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self.temp_file.name
        self.temp_file.close()

    def tearDown(self):
        """测试后清理"""
        import os
        try:
            os.unlink(self.db_path)
        except:
            pass

    def test_pick_tracking_has_score_fields(self):
        """测试：pick_tracking表有评分字段"""
        from pick_tracker import PickTracker
        tracker = PickTracker(self.db_path)

        columns = tracker._get_conn().execute("PRAGMA table_info(pick_tracking)").fetchall()
        col_names = [c[1] for c in columns]

        # 检查评分字段存在
        self.assertIn('score_wave_gain', col_names)
        self.assertIn('score_shallow_dd', col_names)
        self.assertIn('score_day_gain', col_names)
        self.assertIn('score_volume', col_names)
        self.assertIn('score_ma_bull', col_names)
        self.assertIn('score_sector', col_names)
        self.assertIn('score_signal_bonus', col_names)
        self.assertIn('score_base', col_names)


class TestStrategyConfigDynamic(unittest.TestCase):
    """测试 StrategyConfig 动态参数"""

    def setUp(self):
        import sqlite3
        import tempfile
        # 使用临时文件数据库（:memory: 每个连接独立，无法共享）
        self.temp_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self.temp_file.name
        self.temp_file.close()
        self.conn = sqlite3.connect(self.db_path)

    def tearDown(self):
        import os
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except:
            pass

    def test_dynamic_params_exist(self):
        """测试：DYNAMIC_PARAMS 包含 score_weight 类参数"""
        from strategy_config import StrategyConfig
        cfg = StrategyConfig(self.db_path)

        self.assertIn('weight_wave_gain', cfg.DYNAMIC_PARAMS)
        self.assertIn('weight_sector', cfg.DYNAMIC_PARAMS)
        self.assertIn('activity_coefficient', cfg.DYNAMIC_PARAMS)

    def test_set_dynamic_param_with_category(self):
        """测试：set() 方法正确处理动态参数"""
        from strategy_config import StrategyConfig
        cfg = StrategyConfig(self.db_path)

        # 设置动态参数
        cfg.set('weight_wave_gain', 1.2)

        # 检查写入正确
        row = self.conn.execute(
            "SELECT param_value, description, category FROM strategy_config WHERE param_key='weight_wave_gain'"
        ).fetchone()

        self.assertEqual(row[0], 1.2)
        self.assertEqual(row[2], 'score_weight')  # category正确

    def test_get_by_category(self):
        """测试：get_weights() 返回 score_weight 类参数"""
        from strategy_config import StrategyConfig
        cfg = StrategyConfig(self.db_path)

        cfg.set('weight_wave_gain', 1.2)
        cfg.set('weight_volume', 0.9)
        cfg.set('first_wave_min_days', 3)  # entry 类

        weights = cfg.get_weights()

        self.assertIn('weight_wave_gain', weights)
        self.assertIn('weight_volume', weights)
        self.assertNotIn('first_wave_min_days', weights)


class TestDailyMonitor(unittest.TestCase):
    """测试每日监控模块"""

    def test_daily_monitor_init(self):
        """测试：DailyMonitor 初始化"""
        from daily_monitor import DailyMonitor
        monitor = DailyMonitor()
        self.assertIsNotNone(monitor.dl)
        self.assertIsNotNone(monitor.cfg)

    def test_calculate_expectancy(self):
        """测试：期望值计算"""
        from daily_monitor import calculate_expectancy

        # 胜率60%，平均盈利10%，平均亏损5%
        result = calculate_expectancy(0.6, 0.10, 0.05)
        expected = 0.10 * 0.6 - 0.05 * 0.4  # 0.06 - 0.02 = 0.04
        self.assertAlmostEqual(result, 0.04, places=4)

    def test_wilson_expectancy_lower_bound(self):
        """测试：Wilson置信下界期望值"""
        from daily_monitor import wilson_expectancy_lower_bound

        # 样本少时置信区间宽
        result_5 = wilson_expectancy_lower_bound(0.6, 0.10, 0.05, 5)
        result_50 = wilson_expectancy_lower_bound(0.6, 0.10, 0.05, 50)

        # 样本少时，下界更保守（更低）
        self.assertLess(result_5, result_50)


if __name__ == '__main__':
    unittest.main()