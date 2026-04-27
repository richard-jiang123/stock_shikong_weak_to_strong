#!/usr/bin/env python3
"""
TradingDayResolver 单元测试
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_day_resolver import (
    TradingDayInfo,
    STATUS_DATA_READY,
    STATUS_DATA_NOT_UPDATED,
    STATUS_NON_TRADING_DAY,
    STATUS_HISTORICAL,
)


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
