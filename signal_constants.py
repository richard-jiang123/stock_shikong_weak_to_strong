#!/usr/bin/env python3
"""
信号类型常量定义
统一英文主键与中文显示的映射
"""

# 英文主键（数据库存储） → 中文显示（终端输出）
SIGNAL_TYPE_MAPPING = {
    'anomaly_no_decline': '异动不跌',
    'bullish_engulfing': '阳包阴',
    'big_bullish_reversal': '大阳反转',
    'limit_up_open_next_strong': '烂板次日',
}

# 信号状态层级
STATUS_LEVELS = ['active', 'watching', 'warning', 'disabled']

# 状态权重乘数
STATUS_WEIGHT_MULTIPLIER = {
    'active': 1.0,
    'watching': 0.5,
    'warning': 0.2,
    'disabled': 0.0,
}

# 沙盒验证状态
SANDBOX_STATUS = {
    'PENDING': 'pending',
    'PASSED': 'passed',
    'APPLIED': 'applied',
    'FAILED': 'failed',
}


def normalize_signal_type(signal_str):
    """
    统一信号类型标识

    Args:
        signal_str: 可能是中文或英文的信号字符串

    Returns:
        英文主键（如 'anomaly_no_decline'）
    """
    # 如果已经是英文主键
    if signal_str in SIGNAL_TYPE_MAPPING:
        return signal_str

    # 反向映射：中文 → 英文
    reverse = {v: k for k, v in SIGNAL_TYPE_MAPPING.items()}
    return reverse.get(signal_str, signal_str)


def get_display_name(signal_type):
    """
    获取信号的中文显示名

    Args:
        signal_type: 英文主键

    Returns:
        中文显示名
    """
    return SIGNAL_TYPE_MAPPING.get(signal_type, signal_type)


def get_weight_multiplier(status_level):
    """
    获取状态对应的权重乘数

    Args:
        status_level: 状态层级

    Returns:
        权重乘数（0.0-1.0）
    """
    return STATUS_WEIGHT_MULTIPLIER.get(status_level, 1.0)