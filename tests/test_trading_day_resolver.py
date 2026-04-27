#!/usr/bin/env python3
"""
TradingDayResolver 单元测试
"""
import pytest
import sys
import os
import tempfile
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_day_resolver import (
    TradingDayInfo,
    TradingDayResolver,
    STATUS_DATA_READY,
    STATUS_DATA_NOT_UPDATED,
    STATUS_NON_TRADING_DAY,
    STATUS_HISTORICAL,
)


@pytest.fixture
def tmp_db():
    """临时数据库 fixture（包含所有必要表结构）"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    # 创建表结构（与生产环境一致）
    conn = sqlite3.connect(db_path)

    # stock_daily
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily (
            date TEXT,
            code TEXT,
            close REAL
        )
    """)

    # trading_day_cache
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trading_day_cache (
            date TEXT PRIMARY KEY,
            is_trading_day INTEGER,
            checked_at TEXT
        )
    """)

    # market_regime
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_regime (
            regime_date TEXT PRIMARY KEY,
            regime_type TEXT,
            activity_coefficient REAL,
            index_close REAL,
            index_ma5 REAL,
            index_ma20 REAL,
            consecutive_days INTEGER
        )
    """)

    # signal_status
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_status (
            signal_type TEXT PRIMARY KEY,
            display_name TEXT,
            status_level TEXT DEFAULT 'active',
            weight_multiplier REAL DEFAULT 1.0,
            live_win_rate REAL,
            live_avg_win_pct REAL,
            live_avg_loss_pct REAL,
            live_expectancy REAL,
            live_expectancy_lb REAL,
            live_sample_count INTEGER,
            last_check_date TEXT
        )
    """)

    # pick_tracking（用于期望值计算）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pick_tracking (
            id INTEGER PRIMARY KEY,
            signal_type TEXT,
            status TEXT,
            final_pnl_pct REAL
        )
    """)

    # daily_monitor_log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_monitor_log (
            id INTEGER PRIMARY KEY,
            monitor_date TEXT,
            alert_type TEXT,
            alert_detail TEXT,
            severity TEXT,
            action_taken TEXT,
            created_at TEXT
        )
    """)

    # sandbox_config
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sandbox_config (
            id INTEGER PRIMARY KEY,
            optimize_id INTEGER,
            batch_id TEXT NOT NULL,
            param_key TEXT NOT NULL,
            sandbox_value TEXT NOT NULL,
            current_value TEXT,
            optimize_type TEXT NOT NULL,
            status TEXT DEFAULT 'staged',
            staged_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(param_key, batch_id)
        )
    """)

    # optimization_history
    conn.execute("""
        CREATE TABLE IF NOT EXISTS optimization_history (
            id INTEGER PRIMARY KEY,
            optimize_date TEXT,
            optimize_type TEXT,
            param_key TEXT,
            old_value TEXT,
            new_value TEXT,
            batch_id TEXT,
            trigger_reason TEXT,
            sandbox_test_result TEXT,
            created_at TEXT
        )
    """)

    # critical_process_state
    conn.execute("""
        CREATE TABLE IF NOT EXISTS critical_process_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_key TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL DEFAULT 'handling',
            alerts_total INTEGER DEFAULT 0,
            alerts_processed INTEGER DEFAULT 0,
            changes_applied INTEGER DEFAULT 0,
            error_detail TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_critical_period_status ON critical_process_state(period_key, status)")

    # strategy_config（AdaptiveEngine.__init__ 需要）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategy_config (
            param_key TEXT PRIMARY KEY,
            param_value TEXT,
            description TEXT,
            category TEXT,
            updated_at TEXT
        )
    """)

    # param_snapshot（ChangeManager.__init__ 需要）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS param_snapshot (
            id INTEGER PRIMARY KEY,
            snapshot_type TEXT NOT NULL,
            snapshot_data TEXT NOT NULL,
            batch_id TEXT,
            trigger_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_batch ON param_snapshot(batch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_type ON param_snapshot(snapshot_type)")

    # stock_meta（DataLayer._create_adaptive_tables 需要）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_meta (
            code TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            list_date TEXT
        )
    """)

    # index_daily（用于市场环境判断）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_daily (
            date TEXT,
            code TEXT,
            close REAL,
            volume REAL,
            PRIMARY KEY (date, code)
        )
    """)

    conn.commit()
    conn.close()

    yield db_path

    # 清理
    os.unlink(db_path)


class TestTradingDayInfo:
    """TradingDayInfo 数据结构测试"""

    def test_valid_data_ready(self):
        """测试有效的 data_ready 状态"""
        info = TradingDayInfo(
            target_date='2026-04-27',
            effective_data_date='2026-04-27',
            is_trading_day=True,
            data_ready=True,
            data_lag_days=0,
            status=STATUS_DATA_READY,
            monitor_period_key='2026-04-27',
            is_current_monitor=True,
        )
        assert info.status == STATUS_DATA_READY
        assert info.is_non_trading_day is False
        assert info.should_process_critical is True

    def test_valid_data_not_updated(self):
        """测试有效的 data_not_updated 状态"""
        info = TradingDayInfo(
            target_date='2026-04-27',
            effective_data_date='2026-04-24',
            is_trading_day=True,
            data_ready=False,
            data_lag_days=3,
            status=STATUS_DATA_NOT_UPDATED,
            monitor_period_key='2026-04-24',
            is_current_monitor=True,
        )
        assert info.status == STATUS_DATA_NOT_UPDATED
        assert info.is_non_trading_day is False
        assert info.should_process_critical is True

    def test_valid_non_trading_day(self):
        """测试有效的 non_trading_day 状态"""
        info = TradingDayInfo(
            target_date='2026-04-26',
            effective_data_date='2026-04-24',
            is_trading_day=False,
            data_ready=True,
            data_lag_days=0,
            status=STATUS_NON_TRADING_DAY,
            monitor_period_key='2026-04-24',
            is_current_monitor=True,
        )
        assert info.is_non_trading_day is True
        assert info.should_process_critical is False

    def test_valid_historical(self):
        """测试有效的 historical 状态"""
        info = TradingDayInfo(
            target_date='2026-04-20',
            effective_data_date='2026-04-24',
            is_trading_day=True,
            data_ready=True,
            data_lag_days=0,
            status=STATUS_HISTORICAL,
            monitor_period_key='2026-04-20',
            is_current_monitor=False,
        )
        assert info.status == STATUS_HISTORICAL
        assert info.should_process_critical is False

    def test_invalid_date_format(self):
        """测试无效日期格式"""
        with pytest.raises(ValueError, match="格式错误"):
            TradingDayInfo(
                target_date='2026-04-27-',
                effective_data_date='2026-04-27',
                is_trading_day=True,
                data_ready=True,
                data_lag_days=0,
                status=STATUS_DATA_READY,
                monitor_period_key='2026-04-27',
                is_current_monitor=True,
            )

    def test_invalid_status(self):
        """测试无效状态枚举"""
        with pytest.raises(ValueError, match="无效状态"):
            TradingDayInfo(
                target_date='2026-04-27',
                effective_data_date='2026-04-27',
                is_trading_day=True,
                data_ready=True,
                data_lag_days=0,
                status='invalid_status',
                monitor_period_key='2026-04-27',
                is_current_monitor=True,
            )

    def test_negative_lag_days(self):
        """测试负数 lag_days"""
        with pytest.raises(ValueError, match="不能为负"):
            TradingDayInfo(
                target_date='2026-04-27',
                effective_data_date='2026-04-27',
                is_trading_day=True,
                data_ready=True,
                data_lag_days=-1,
                status=STATUS_DATA_READY,
                monitor_period_key='2026-04-27',
                is_current_monitor=True,
            )

    def test_should_process_critical_edge_cases(self):
        """测试 should_process_critical 边界情况"""
        # is_current_monitor=False + STATUS_DATA_READY -> False
        info = TradingDayInfo(
            target_date='2026-04-27',
            effective_data_date='2026-04-27',
            is_trading_day=True,
            data_ready=True,
            data_lag_days=0,
            status=STATUS_DATA_READY,
            monitor_period_key='2026-04-27',
            is_current_monitor=False,
        )
        assert info.should_process_critical is False

        # historical + is_current_monitor=False -> False
        info = TradingDayInfo(
            target_date='2026-04-20',
            effective_data_date='2026-04-24',
            is_trading_day=True,
            data_ready=True,
            data_lag_days=0,
            status=STATUS_HISTORICAL,
            monitor_period_key='2026-04-20',
            is_current_monitor=False,
        )
        assert info.should_process_critical is False


class TestTradingDayResolverGetEffectiveDataDate:
    """_get_effective_data_date 方法测试"""

    def test_get_from_stock_daily(self, tmp_db):
        """从 stock_daily 获取有效数据日期"""
        from trading_day_resolver import TradingDayResolver
        resolver = TradingDayResolver(db_path=tmp_db)
        # 插入测试数据
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")

        result = resolver._get_effective_data_date('2026-04-27')
        assert result == '2026-04-24'

    def test_empty_database_returns_today(self, tmp_db):
        """空数据库返回今天"""
        from trading_day_resolver import TradingDayResolver
        resolver = TradingDayResolver(db_path=tmp_db)
        today = datetime.now().strftime('%Y-%m-%d')
        result = resolver._get_effective_data_date(today)
        assert result == today

    def test_fallback_to_trading_day_cache(self, tmp_db):
        """从 trading_day_cache 获取最近交易日"""
        from trading_day_resolver import TradingDayResolver
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-24', 1, datetime('now'))")

        result = resolver._get_effective_data_date('2026-04-27')
        assert result == '2026-04-24'
