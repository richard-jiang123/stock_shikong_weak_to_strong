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


class ScoreNormalizer:
    """评分归一化器：基于历史样本全局统计归一化

    设计目标：
    1. 调整评分权重后，总分均值保持稳定（≈基准值）
    2. 支持渐进置信度：样本不足时返回低置信标记
    3. 实时查询历史样本，无需缓存机制

    渐进置信度机制（与设计文档 B.14.3 一致）：
    - n < 30：全部样本，低置信度
    - n >= 30 且 n < 50：全部样本，中等置信度
    - n >= 50：最近50笔（RECENT_WINDOW），高置信度
    """

    MIN_SAMPLES = 30      # 最小样本阈值（低于此值返回低置信）
    MEDIUM_SAMPLES = 50   # 中等样本阈值（>=50 返回高置信）
    RECENT_WINDOW = 50    # 样本充足时使用最近50笔

    def __init__(self, db_path=None, data_layer=None):
        """初始化，连接数据层

        Args:
            db_path: 数据库路径，None时使用默认路径
            data_layer: 直接注入的数据层实例（优先级最高，用于测试注入）
        """
        if data_layer is not None:
            self.dl = data_layer
        elif db_path is None:
            self.dl = get_data_layer()
        else:
            self.dl = StockDataLayer(db_path)

    def get_history_stats(self):
        """
        获取历史评分统计（从 pick_tracking 实时查询）

        Returns:
            stats: dict {'avg_wave_gain': 12.3, 'avg_shallow_dd': 8.7, ...}
            meta: dict {'method': 'all'|'recent_50', 'confidence': 'low'|'medium'|'high', 'n': int}
        """
        with self.dl._get_conn() as conn:
            exited_df = pd.read_sql(
                "SELECT * FROM pick_tracking WHERE status='exited'",
                conn
            )

        n = len(exited_df)

        if n < self.MIN_SAMPLES:
            # 样本不足：全部样本，低置信
            stats = self._calculate_stats(exited_df)
            return stats, {'method': 'all', 'confidence': 'low', 'n': n}

        elif n >= self.MEDIUM_SAMPLES:
            # 样本充足：最近50笔，高置信
            recent = exited_df.tail(self.RECENT_WINDOW)
            stats = self._calculate_stats(recent)
            # meta['n'] 返回实际参与计算的样本数（len(recent)），而非总数
            return stats, {'method': 'recent_50', 'confidence': 'high', 'n': len(recent)}

        else:
            # 样本中等（30-50）：全部样本，中等置信
            stats = self._calculate_stats(exited_df)
            return stats, {'method': 'all', 'confidence': 'medium', 'n': n}

    def _calculate_stats(self, df):
        """
        计算各评分维度的均值

        Args:
            df: pick_tracking 已退出样本 DataFrame

        Returns:
            stats: dict {维度名: 均值}
        """
        stats = {}
        for dim in SCORE_DIMENSIONS:
            col_name = f'score_{dim}'
            if col_name in df.columns and df[col_name].notna().sum() > 0:
                stats[f'avg_{dim}'] = df[col_name].mean()
            else:
                stats[f'avg_{dim}'] = 10.0  # 默认值（历史均值未知时的保守估计）
        return stats