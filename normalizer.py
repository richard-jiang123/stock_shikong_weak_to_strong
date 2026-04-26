#!/usr/bin/env python3
"""
评分归一化模块

职责：基于历史样本全局统计归一化评分，确保权重调整后总分均值稳定。
"""
import pandas as pd
from data_layer import get_data_layer, StockDataLayer

# 评分维度列表（与现有数据库字段和 strategy_config.py DYNAMIC_PARAMS 权重参数对应）
# 注意：维度名去掉 'weight_' 前缀，评分字段名加 'score_' 前缀
# 重要：现有 pick_tracking 表字段名是 score_day_gain（不是 score_strong_gain）
#       strategy_config.py 的 weight_strong_gain 对应的是 score_day_gain
SCORE_DIMENSIONS = [
    'day_gain',       # 当日涨幅评分（score_day_gain）→ weight_strong_gain
    'wave_gain',      # 波段涨幅评分（score_wave_gain）→ weight_wave_gain
    'shallow_dd',     # 回调深度评分（score_shallow_dd）→ weight_shallow_dd
    'volume',         # 放量评分（score_volume）→ weight_volume
    'ma_bull',        # 多头排列评分（score_ma_bull）→ weight_ma_bull
    'sector',         # 板块动量评分（score_sector）→ weight_sector
    'signal_bonus',   # 信号类型加分（score_signal_bonus）→ weight_signal_bonus
]

# 注意：
# 1. score_base 基础分不纳入权重调整范围，始终保持固定值5分
# 2. 维度名 day_gain 对应数据库字段 score_day_gain，对应权重 weight_strong_gain
# 3. 异动信号加分（score_anomaly）目前未在数据库中实现，暂不纳入归一化范围