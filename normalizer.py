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

    def normalize_scores(self, scores_dict, weights_dict):
        """
        归一化评分

        核心公式：
        scale_factor = global_base_total / global_weighted_total
        normalized_score = weighted_total * scale_factor

        Args:
            scores_dict: 当前股票各维度评分 {'day_gain': 10, 'wave_gain': 20, ...}
            weights_dict: 当前权重 {'weight_strong_gain': 1.0, 'weight_wave_gain': 1.2, ...}

        Returns:
            normalized_score: float 归一化后总分（不含score_base）
            meta: dict {'method': ..., 'confidence': ..., 'n': ..., 'scale_factor': ...}
        """
        # 维度名到权重键名的映射（因为 day_gain 对应 weight_strong_gain）
        DIMENSION_TO_WEIGHT = {
            'day_gain': 'weight_strong_gain',  # 特殊映射
        }

        # 1. 获取历史统计
        history_stats, meta = self.get_history_stats()

        # 2. 计算当前股票加权总分
        weighted_total = 0
        for dim in SCORE_DIMENSIONS:
            score_val = scores_dict.get(dim, 0)
            # 查找对应的权重键名
            weight_key = DIMENSION_TO_WEIGHT.get(dim, f'weight_{dim}')
            weight_val = weights_dict.get(weight_key, 1.0)
            weighted_total += score_val * weight_val

        # 3. 计算全局基准总分（权重=1.0）
        global_base_total = sum(
            history_stats.get(f'avg_{dim}', 10.0) for dim in SCORE_DIMENSIONS
        )

        # 4. 计算全局加权总分（当前权重）
        global_weighted_total = 0
        for dim in SCORE_DIMENSIONS:
            avg_val = history_stats.get(f'avg_{dim}', 10.0)
            weight_key = DIMENSION_TO_WEIGHT.get(dim, f'weight_{dim}')
            weight_val = weights_dict.get(weight_key, 1.0)
            global_weighted_total += avg_val * weight_val

        # 5. 缩放因子（避免除零）
        scale_factor = global_base_total / global_weighted_total if global_weighted_total > 0 else 1.0

        # 6. 归一化得分
        normalized_score = weighted_total * scale_factor

        # 7. 补充meta信息
        meta['scale_factor'] = scale_factor
        meta['weighted_total_raw'] = weighted_total
        meta['global_base_total'] = global_base_total
        meta['global_weighted_total'] = global_weighted_total

        return normalized_score, meta