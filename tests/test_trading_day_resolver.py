#!/usr/bin/env python3
"""
TradingDayResolver 单元测试
"""
import pytest
import sys
import os
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


# tmp_db fixture 已移至 conftest.py 以便跨文件共享


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


class TestTradingDayResolverDetermineTradingDay:
    """_determine_trading_day 方法测试"""

    def test_cached_trading_day(self, tmp_db):
        """缓存中有交易日标记"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-27', 1, datetime('now'))")

        result = resolver._determine_trading_day('2026-04-27')
        assert result is True

    def test_cached_non_trading_day(self, tmp_db):
        """缓存中有非交易日标记"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-26', 0, datetime('now'))")

        result = resolver._determine_trading_day('2026-04-26')
        assert result is False

    def test_saturday_is_non_trading(self, tmp_db):
        """周六必然是非交易日"""
        resolver = TradingDayResolver(db_path=tmp_db)
        # 2026-04-25 是周六
        result = resolver._determine_trading_day('2026-04-25')
        assert result is False

    def test_sunday_is_non_trading(self, tmp_db):
        """周日必然是非交易日"""
        resolver = TradingDayResolver(db_path=tmp_db)
        # 2026-04-26 是周日
        result = resolver._determine_trading_day('2026-04-26')
        assert result is False

    def test_weekday_no_cache_assumes_trading(self, tmp_db):
        """工作日无缓存时默认为交易日"""
        resolver = TradingDayResolver(db_path=tmp_db)
        # 2026-04-27 是周一，无缓存
        result = resolver._determine_trading_day('2026-04-27')
        # 无缓存且非周末 -> 默认 True（后续 resolve 会通过其他逻辑确认）
        assert result is True


class TestTradingDayResolverGetEffectiveDataDate:
    """_get_effective_data_date 方法测试"""

    def test_get_from_stock_daily(self, tmp_db):
        """从 stock_daily 获取有效数据日期"""
        resolver = TradingDayResolver(db_path=tmp_db)
        # 插入测试数据
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")

        result = resolver._get_effective_data_date('2026-04-27')
        assert result == '2026-04-24'

    def test_empty_database_returns_today(self, tmp_db):
        """空数据库返回今天"""
        resolver = TradingDayResolver(db_path=tmp_db)
        today = datetime.now().strftime('%Y-%m-%d')
        result = resolver._get_effective_data_date(today)
        assert result == today

    def test_fallback_to_trading_day_cache(self, tmp_db):
        """从 trading_day_cache 获取最近交易日"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-24', 1, datetime('now'))")

        result = resolver._get_effective_data_date('2026-04-27')
        assert result == '2026-04-24'


class TestTradingDayResolverCountTradingDaysGap:
    """_count_trading_days_gap 方法测试"""

    def test_same_day_returns_zero(self, tmp_db):
        """同一天返回 0"""
        resolver = TradingDayResolver(db_path=tmp_db)
        result = resolver._count_trading_days_gap('2026-04-24', '2026-04-24')
        assert result == 0

    def test_with_trading_day_cache(self, tmp_db):
        """有缓存时计算交易日间隔"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            # 插入连续交易日
            for d in ['2026-04-24', '2026-04-27', '2026-04-28']:
                conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES (?, 1, datetime('now'))", (d,))
            # 插入非交易日
            for d in ['2026-04-25', '2026-04-26']:
                conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES (?, 0, datetime('now'))", (d,))

        # 24(周五) 到 28(周二): 查询 > 24 AND <= 28
        # 范围内: 25(周六x), 26(周日x), 27(周一ok), 28(周二ok) = 2 个交易日
        result = resolver._count_trading_days_gap('2026-04-24', '2026-04-28')
        assert result == 2

    def test_no_cache_fallback_to_weekday_count(self, tmp_db):
        """无缓存时按工作日估算"""
        resolver = TradingDayResolver(db_path=tmp_db)
        # 2026-04-24(周五) 到 2026-04-28(周二)
        # 按工作日: 25(周六), 26(周日), 27(周一), 28(周二) -> 2 个工作日
        result = resolver._count_trading_days_gap('2026-04-24', '2026-04-28')
        assert result == 2  # 估算值，排除周末


class TestTradingDayResolverResolve:
    """resolve() 方法测试"""

    def test_data_ready(self, tmp_db):
        """交易日数据已更新"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-27', 'sh.600000', 10.0)")
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-27', 1, datetime('now'))")

        info = resolver.resolve('2026-04-27')
        assert info.status == STATUS_DATA_READY
        assert info.data_lag_days == 0
        assert info.monitor_period_key == '2026-04-27'
        assert info.should_process_critical is True

    def test_data_not_updated(self, tmp_db):
        """交易日数据未更新"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-27', 1, datetime('now'))")

        info = resolver.resolve('2026-04-27')
        assert info.status == STATUS_DATA_NOT_UPDATED
        assert info.effective_data_date == '2026-04-24'
        assert info.data_lag_days >= 1  # 24(周五) 到 27(周一): 至少1个交易日
        assert info.monitor_period_key == '2026-04-24'
        assert info.should_process_critical is True

    def test_non_trading_day_weekend(self, tmp_db):
        """周末是非交易日"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")

        # 2026-04-25 是周六
        info = resolver.resolve('2026-04-25')
        assert info.status == STATUS_NON_TRADING_DAY
        assert info.is_non_trading_day is True
        assert info.monitor_period_key == '2026-04-24'
        assert info.should_process_critical is False

    def test_historical_date(self, tmp_db):
        """历史日期运行"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")

        info = resolver.resolve('2026-04-20')
        assert info.status == STATUS_HISTORICAL
        assert info.is_current_monitor is False
        assert info.should_process_critical is False

    def test_empty_database(self, tmp_db):
        """空数据库首次运行"""
        resolver = TradingDayResolver(db_path=tmp_db)
        today = datetime.now().strftime('%Y-%m-%d')
        info = resolver.resolve(today)
        assert info.status == STATUS_DATA_READY
        assert info.effective_data_date == today
        assert info.data_lag_days == 0

    def test_future_date_raises_error(self, tmp_db):
        """未来日期抛出错误"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with pytest.raises(ValueError, match="Future date not allowed"):
            resolver.resolve('2030-01-01')

    def test_invalid_format_raises_error(self, tmp_db):
        """无效日期格式抛出错误"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with pytest.raises(ValueError, match="Invalid date format"):
            resolver.resolve('2026-04-27-')

    def test_default_target_date_is_today(self, tmp_db):
        """默认 target_date 是今天"""
        resolver = TradingDayResolver(db_path=tmp_db)
        today = datetime.now().strftime('%Y-%m-%d')
        info = resolver.resolve()
        assert info.target_date == today
