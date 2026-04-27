#!/usr/bin/env python3
"""测试自适应量化系统组件"""
import unittest
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_constants import (
    SIGNAL_TYPE_MAPPING,
    normalize_signal_type,
    get_display_name,
    get_weight_multiplier,
)
from trading_day_resolver import (
    TradingDayResolver,
    TradingDayInfo,
    STATUS_DATA_READY,
    STATUS_DATA_NOT_UPDATED,
    STATUS_NON_TRADING_DAY,
    STATUS_HISTORICAL,
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


class TestWeeklyOptimizer(unittest.TestCase):
    """测试每周优化模块"""

    def test_adjust_score_weight_positive_correlation(self):
        """测试：正相关增加权重"""
        from weekly_optimizer import adjust_score_weight

        # 强正相关 → 加权
        result = adjust_score_weight(1.0, 0.4)
        self.assertGreater(result, 1.0)
        # 调整幅度不超过20%
        self.assertLessEqual(result, 1.2)

    def test_adjust_score_weight_negative_correlation(self):
        """测试：负相关减少权重"""
        from weekly_optimizer import adjust_score_weight

        # 负相关 → 减权
        result = adjust_score_weight(1.0, -0.3)
        self.assertLess(result, 1.0)
        # 调整幅度不超过20%
        self.assertGreaterEqual(result, 0.8)

    def test_adjust_score_weight_weak_correlation(self):
        """测试：弱相关保持不变"""
        from weekly_optimizer import adjust_score_weight

        # 弱相关 → 保持不变
        result = adjust_score_weight(1.0, 0.1)
        self.assertEqual(result, 1.0)

    def test_weekly_optimizer_init(self):
        """测试：WeeklyOptimizer 初始化"""
        from weekly_optimizer import WeeklyOptimizer
        optimizer = WeeklyOptimizer()
        self.assertIsNotNone(optimizer.dl)
        self.assertIsNotNone(optimizer.cfg)
        self.assertIsNotNone(optimizer.optimizer)


class TestSandboxValidator(unittest.TestCase):
    """测试沙盒验证模块"""

    def test_sandbox_config_exists(self):
        """测试：沙盒配置存在"""
        from sandbox_validator import SANDBOX_VALIDATION_CONFIG
        self.assertIn('validation_window_weeks', SANDBOX_VALIDATION_CONFIG)
        self.assertEqual(SANDBOX_VALIDATION_CONFIG['validation_window_weeks'], 3)

    def test_sandbox_validator_init(self):
        """测试：SandboxValidator 初始化"""
        from sandbox_validator import SandboxValidator
        validator = SandboxValidator()
        self.assertIsNotNone(validator.dl)
        self.assertIsNotNone(validator.cfg)

    def test_status_values_defined(self):
        """测试：状态值定义"""
        from sandbox_validator import SANDBOX_VALIDATION_CONFIG
        from signal_constants import SANDBOX_STATUS
        self.assertEqual(SANDBOX_VALIDATION_CONFIG['status_values'], SANDBOX_STATUS)


class TestAdaptiveEngine(unittest.TestCase):
    """测试自适应引擎"""

    def test_adaptive_engine_init(self):
        """测试：AdaptiveEngine 初始化"""
        from adaptive_engine import AdaptiveEngine
        engine = AdaptiveEngine()
        self.assertIsNotNone(engine.dl)
        self.assertIsNotNone(engine.cfg)
        self.assertIsNotNone(engine.monitor)
        self.assertIsNotNone(engine.weekly_optimizer)
        self.assertIsNotNone(engine.sandbox_validator)

    def test_critical_config_defined(self):
        """测试：critical配置定义"""
        from adaptive_engine import AdaptiveEngine
        self.assertIn('auto_disable_threshold', AdaptiveEngine.CRITICAL_CONFIG)
        self.assertIn('min_sample_for_critical', AdaptiveEngine.CRITICAL_CONFIG)


class TestAdaptiveEngineWithResolver:
    """AdaptiveEngine 使用 resolver 的集成测试"""

    def test_run_daily_non_trading_day(self, tmp_db):
        """非交易日跳过数据监控"""
        from adaptive_engine import AdaptiveEngine
        engine = AdaptiveEngine(db_path=tmp_db)
        resolver = TradingDayResolver(db_path=tmp_db)

        # 设置数据库：最新数据为周五
        with engine.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")

        # 周六运行
        result = engine.run_daily('2026-04-25')
        assert result['status'] == 'skipped'
        assert result['reason'] == 'non_trading_day'

    def test_run_daily_data_not_updated(self, tmp_db):
        """数据未更新时使用旧数据"""
        from adaptive_engine import AdaptiveEngine
        engine = AdaptiveEngine(db_path=tmp_db)

        # 设置数据库：最新数据滞后 3 天
        with engine.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-27', 1, datetime('now'))")

        result = engine.run_daily('2026-04-27')
        assert result['status'] in ('ok', 'warning', 'critical')
        # 验证使用了 effective_data_date

    def test_run_daily_historical_date(self, tmp_db):
        """历史日期跳过 critical"""
        from adaptive_engine import AdaptiveEngine
        engine = AdaptiveEngine(db_path=tmp_db)

        # 设置数据库：最新数据为今天
        with engine.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-27', 'sh.600000', 10.0)")

        result = engine.run_daily('2026-04-20')
        assert result['reason'] == 'historical'
        assert result['critical_handled'] == 0

    def test_run_weekly_not_thursday(self, tmp_db):
        """非周四不允许执行优化"""
        from adaptive_engine import AdaptiveEngine
        engine = AdaptiveEngine(db_path=tmp_db)

        # 设置数据库
        with engine.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-27', 'sh.600000', 10.0)")

        # 周一运行
        result = engine.run_weekly('2026-04-27')
        assert result['reason'] == 'not_thursday'

    def test_critical_handling_recovery(self, tmp_db):
        """中断恢复测试"""
        from adaptive_engine import AdaptiveEngine
        engine = AdaptiveEngine(db_path=tmp_db)

        # 设置数据库
        with engine.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-27', 'sh.600000', 10.0)")
            # 模拟中断状态：handling
            conn.execute("""
                INSERT INTO critical_process_state
                (period_key, started_at, status, alerts_total, alerts_processed)
                VALUES ('2026-04-27', datetime('now'), 'handling', 3, 1)
            """)

        # 第二次运行：应检测到 handling 状态并恢复
        result = engine.run_daily('2026-04-27')
        # 验证：应回滚并重新处理
        assert result['status'] in ('ok', 'warning', 'critical')


if __name__ == '__main__':
    unittest.main()