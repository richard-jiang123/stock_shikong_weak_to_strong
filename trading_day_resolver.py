#!/usr/bin/env python3
"""
交易日统一解析器

提供 TradingDayInfo 数据结构和 TradingDayResolver 类，
统一处理所有交易日相关判断。
"""
from dataclasses import dataclass
from datetime import datetime

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
