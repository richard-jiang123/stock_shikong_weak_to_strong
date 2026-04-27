#!/usr/bin/env python3
"""
交易日统一解析器

提供 TradingDayInfo 数据结构和 TradingDayResolver 类，
统一处理所有交易日相关判断。
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_layer import get_data_layer, StockDataLayer

# 状态枚举常量（模块级别）
STATUS_DATA_READY = 'data_ready'
STATUS_DATA_NOT_UPDATED = 'data_not_updated'
STATUS_NON_TRADING_DAY = 'non_trading_day'
STATUS_HISTORICAL = 'historical'
VALID_STATUSES = (STATUS_DATA_READY, STATUS_DATA_NOT_UPDATED,
                  STATUS_NON_TRADING_DAY, STATUS_HISTORICAL)


@dataclass
class TradingDayInfo:
    """交易日解析结果"""

    # 基本信息
    target_date: str                    # 用户指定或当前日期
    effective_data_date: str            # 数据库最新数据日期

    # 状态判断
    is_trading_day: bool                # target_date 是否是交易日
    data_ready: bool                    # 数据是否已更新到 target_date
    data_lag_days: int                  # 数据滞后天数（0表示已更新）

    # 状态枚举
    status: str                         # 状态值

    # 计算属性
    monitor_period_key: str             # 防重复处理的周期键
    is_current_monitor: bool            # 是否是当前监控（非历史）

    def __post_init__(self):
        """数据验证"""
        # 日期格式验证（monitor_period_key 由计算得出，无需验证）
        for date_field in ('target_date', 'effective_data_date'):
            try:
                datetime.strptime(getattr(self, date_field), '%Y-%m-%d')
            except ValueError:
                raise ValueError(f"{date_field} 格式错误: {getattr(self, date_field)}")

        # 状态枚举验证
        if self.status not in VALID_STATUSES:
            raise ValueError(f"无效状态: {self.status}")

        # 数值范围验证
        if self.data_lag_days < 0:
            raise ValueError(f"data_lag_days 不能为负: {self.data_lag_days}")

    @property
    def is_non_trading_day(self) -> bool:
        """是否是非交易日"""
        return self.status == STATUS_NON_TRADING_DAY

    @property
    def should_process_critical(self) -> bool:
        """是否应该处理 critical（语义判断）

        仅判断是否满足处理 critical 的语义条件：
        - 当前监控（非历史）
        - 交易日且数据相关状态

        注：防重复检查在 _handle_critical_alerts_with_recovery 中通过
        _get_critical_state 查询数据库实现，与此属性职责分离。

        Returns:
            bool: True 表示语义上可以处理，False 表示不应处理
        """
        # 非交易日和历史日期都不处理
        return self.is_current_monitor and self.status in (
            STATUS_DATA_READY,
            STATUS_DATA_NOT_UPDATED
        )


class TradingDayResolver:
    """交易日统一解析器"""

    def __init__(self, db_path=None):
        if db_path is None:
            self.dl = get_data_layer()
        else:
            self.dl = StockDataLayer(db_path)

    def resolve(self, target_date=None) -> TradingDayInfo:
        """
        解析目标日期，返回统一的交易日信息

        Args:
            target_date: 目标日期，默认今天

        Returns:
            TradingDayInfo: 包含所有判断所需信息
        """
        # 1. 参数处理与格式验证
        if target_date is None:
            target_date = datetime.now().strftime('%Y-%m-%d')

        # 格式验证：确保日期格式正确
        try:
            datetime.strptime(target_date, '%Y-%m-%d')
        except ValueError:
            raise ValueError(f"Invalid date format: {target_date}, expected YYYY-MM-DD")

        # 边界检查：未来日期不允许
        today = datetime.now().strftime('%Y-%m-%d')
        if target_date > today:
            raise ValueError(f"Future date not allowed: {target_date}")

        # 2. 获取有效数据日期
        # _get_effective_data_date 永远返回字符串（最坏情况回退到今天），不会返回 None
        effective_data_date = self._get_effective_data_date(target_date)

        # 3. 判断是否是交易日
        is_trading_day = self._determine_trading_day(target_date)

        # 4. 判断是否是历史运行（使用 datetime 对象比较）
        target_dt = datetime.strptime(target_date, '%Y-%m-%d')
        effective_dt = datetime.strptime(effective_data_date, '%Y-%m-%d')
        is_current_monitor = target_dt >= effective_dt

        # 5. 确定状态
        if not is_trading_day:
            status = STATUS_NON_TRADING_DAY
            data_ready = True
            data_lag_days = 0
        elif not is_current_monitor:
            status = STATUS_HISTORICAL
            data_ready = True
            data_lag_days = 0
        elif target_date == effective_data_date:
            status = STATUS_DATA_READY
            data_ready = True
            data_lag_days = 0
        else:
            status = STATUS_DATA_NOT_UPDATED
            data_ready = False
            data_lag_days = self._count_trading_days_gap(effective_data_date, target_date)

        # 6. 计算 monitor_period_key
        if status in (STATUS_NON_TRADING_DAY, STATUS_DATA_NOT_UPDATED):
            monitor_period_key = effective_data_date
        else:
            monitor_period_key = target_date

        return TradingDayInfo(
            target_date=target_date,
            effective_data_date=effective_data_date,
            is_trading_day=is_trading_day,
            data_ready=data_ready,
            data_lag_days=data_lag_days,
            status=status,
            monitor_period_key=monitor_period_key,
            is_current_monitor=is_current_monitor,
        )

    def _get_effective_data_date(self, target_date) -> str:
        """
        获取有效数据日期

        规则：
        - 从 stock_daily 获取 MAX(date)
        - 如果空，从 trading_day_cache 获取最近交易日
        - 最后回退到今天

        Args:
            target_date: 目标日期（用于空库回退）

        Returns:
            str: 有效数据日期
        """
        # 方法1: 从实际数据获取（最可靠）
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT MAX(date) FROM stock_daily
            """).fetchone()
            if row and row[0]:
                return row[0]

            # 方法2: 从交易日缓存获取最近交易日
            row = conn.execute("""
                SELECT date FROM trading_day_cache
                WHERE is_trading_day = 1
                ORDER BY date DESC LIMIT 1
            """).fetchone()
            if row and row[0]:
                return row[0]

        # 方法3: 回退到今天
        return datetime.now().strftime('%Y-%m-%d')

    def _determine_trading_day(self, date_str) -> bool:
        """
        判断是否是交易日

        优先级（权威来源优先，推断性来源后置）：
        1. trading_day_cache 缓存（权威）
        2. 数据库是否有该日数据（推断性：有数据则大概率是交易日）
        3. 周末简单判断（兜底：周末必然非交易日）
        4. 工作日默认为交易日（需后续通过其他逻辑确认）

        Args:
            date_str: 日期字符串 YYYY-MM-DD

        Returns:
            bool: True=交易日, False=非交易日
        """
        # 1. 查缓存（权威）
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT is_trading_day FROM trading_day_cache WHERE date=?
            """, (date_str,)).fetchone()
            if row is not None:
                return row[0] == 1

        # 2. 查数据库是否有该日数据（推断性）
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) FROM stock_daily WHERE date=?
            """, (date_str,)).fetchone()
            if row and row[0] > 0:
                # 有数据 -> 大概率是交易日，缓存结果
                self._cache_trading_day(date_str, True)
                return True

        # 3. 周末判断（兜底）
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        if dt.weekday() >= 5:  # 周六=5, 周日=6
            # 周末必然非交易日，缓存结果
            self._cache_trading_day(date_str, False)
            return False

        # 4. 工作日默认为交易日（需后续确认）
        return True

    def _cache_trading_day(self, date_str, is_trading_day):
        """缓存交易日判断结果"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trading_day_cache
                (date, is_trading_day, checked_at)
                VALUES (?, ?, datetime('now'))
            """, (date_str, 1 if is_trading_day else 0))

    def _count_trading_days_gap(self, from_date, to_date) -> int:
        """
        计算两个日期之间的交易日间隔天数

        规则：
        - 仅计算交易日（排除周末和节假日）
        - 从 trading_day_cache 获取交易日列表进行计算
        - 无缓存时按工作日估算

        Args:
            from_date: 起始日期（较早）
            to_date: 结束日期（较晚）

        Returns:
            int: 交易日间隔天数（>= 0）
        """
        if from_date >= to_date:
            return 0

        from_dt = datetime.strptime(from_date, '%Y-%m-%d')
        to_dt = datetime.strptime(to_date, '%Y-%m-%d')

        # 从缓存获取交易日列表
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT date FROM trading_day_cache
                WHERE date > ? AND date <= ? AND is_trading_day = 1
                ORDER BY date
            """, (from_date, to_date)).fetchall()

        if rows:
            # 有缓存，精确计算
            return len(rows)

        # 无缓存，按工作日估算（排除周末）
        gap = 0
        current = from_dt + timedelta(days=1)
        while current <= to_dt:
            if current.weekday() < 5:  # 周一至周五
                gap += 1
            current += timedelta(days=1)

        return gap
