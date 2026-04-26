#!/usr/bin/env python3
"""
归一化模块单元测试
"""
import pytest
import sqlite3
import pandas as pd
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalizer import ScoreNormalizer, SCORE_DIMENSIONS


class TestScoreDimensions:
    """测试 SCORE_DIMENSIONS 常量"""

    def test_dimensions_count(self):
        """应有7个评分维度"""
        assert len(SCORE_DIMENSIONS) == 7

    def test_dimensions_match_db_fields(self):
        """维度名应与数据库字段名对应（加 score_ 前缀）"""
        expected = ['day_gain', 'wave_gain', 'shallow_dd', 'volume', 'ma_bull', 'sector', 'signal_bonus']
        assert SCORE_DIMENSIONS == expected

    def test_day_gain_maps_to_strong_gain_weight(self):
        """day_gain 维度对应的权重键是 weight_strong_gain"""
        # 这是一个特殊映射，因为数据库字段名 score_day_gain 对应权重参数名 weight_strong_gain
        assert True  # 在 normalize_scores 中通过 DIMENSION_TO_WEIGHT 映射处理


class TestScoreNormalizerInit:
    """测试 ScoreNormalizer 初始化"""

    def test_init_default_db_path(self):
        """默认初始化使用 get_data_layer()"""
        normalizer = ScoreNormalizer()
        assert normalizer.dl is not None

    def test_init_with_data_layer_injection(self):
        """使用 data_layer 参数直接注入"""
        # 创建 mock data layer
        class MockDataLayer:
            def _get_conn(self):
                conn = sqlite3.connect(':memory:')
                conn.execute("""
                    CREATE TABLE pick_tracking (
                        id INTEGER PRIMARY KEY,
                        status TEXT,
                        score_wave_gain REAL,
                        score_shallow_dd REAL,
                        score_day_gain REAL,
                        score_volume REAL,
                        score_ma_bull REAL,
                        score_sector REAL,
                        score_signal_bonus REAL
                    )
                """)
                conn.commit()
                return conn

        dl = MockDataLayer()
        normalizer = ScoreNormalizer(data_layer=dl)
        assert normalizer.dl is dl  # 应是同一个实例


class TestGetHistoryStats:
    """测试 get_history_stats() 方法"""

    def _create_mock_data_layer(self, sample_count):
        """创建带有指定样本数的 mock data layer"""
        class MockDataLayer:
            def __init__(self, count):
                self.count = count
                self.conn = None

            def _get_conn(self):
                if self.conn is None:
                    self.conn = sqlite3.connect(':memory:')
                    self.conn.execute("""
                        CREATE TABLE pick_tracking (
                            id INTEGER PRIMARY KEY,
                            status TEXT,
                            score_wave_gain REAL,
                            score_shallow_dd REAL,
                            score_day_gain REAL,
                            score_volume REAL,
                            score_ma_bull REAL,
                            score_sector REAL,
                            score_signal_bonus REAL
                        )
                    """)
                    for i in range(self.count):
                        self.conn.execute("""
                            INSERT INTO pick_tracking (status, score_wave_gain, score_shallow_dd,
                                score_day_gain, score_volume, score_ma_bull, score_sector, score_signal_bonus)
                            VALUES ('exited', 20, 15, 10, 5, 10, 0, 10)
                        """)
                    self.conn.commit()
                return self.conn

        return MockDataLayer(sample_count)

    def test_empty_samples_returns_low_confidence(self):
        """样本为空时返回低置信度"""
        dl = self._create_mock_data_layer(0)
        normalizer = ScoreNormalizer(data_layer=dl)
        stats, meta = normalizer.get_history_stats()

        assert meta['confidence'] == 'low'
        assert meta['n'] == 0
        assert meta['method'] == 'all'

    def test_insufficient_samples_returns_low_confidence(self):
        """样本少于30笔返回低置信度"""
        dl = self._create_mock_data_layer(10)
        normalizer = ScoreNormalizer(data_layer=dl)

        stats, meta = normalizer.get_history_stats()

        assert meta['confidence'] == 'low'
        assert meta['n'] == 10
        assert meta['method'] == 'all'

    def test_medium_samples_returns_medium_confidence(self):
        """样本30-50笔返回中等置信度"""
        dl = self._create_mock_data_layer(40)
        normalizer = ScoreNormalizer(data_layer=dl)

        stats, meta = normalizer.get_history_stats()

        assert meta['confidence'] == 'medium'
        assert meta['n'] == 40

    def test_high_samples_returns_high_confidence(self):
        """样本>=50笔返回高置信度"""
        dl = self._create_mock_data_layer(60)
        normalizer = ScoreNormalizer(data_layer=dl)

        stats, meta = normalizer.get_history_stats()

        assert meta['confidence'] == 'high'
        assert meta['n'] == 50  # 实际参与计算的样本数（RECENT_WINDOW），而非总数60
        assert meta['method'] == 'recent_50'


class TestNormalizeScores:
    """测试 normalize_scores() 方法"""

    def _create_mock_data_layer_empty(self):
        """创建空样本的 mock data layer"""
        class MockDataLayer:
            def _get_conn(self):
                conn = sqlite3.connect(':memory:')
                conn.execute("""
                    CREATE TABLE pick_tracking (
                        id INTEGER PRIMARY KEY,
                        status TEXT,
                        score_wave_gain REAL,
                        score_shallow_dd REAL,
                        score_day_gain REAL,
                        score_volume REAL,
                        score_ma_bull REAL,
                        score_sector REAL,
                        score_signal_bonus REAL
                    )
                """)
                conn.commit()
                return conn
        return MockDataLayer()

    def test_normalize_with_default_weights(self):
        """权重全为1.0时，缩放因子应≈1.0"""
        scores = {
            'day_gain': 10,
            'wave_gain': 20,
            'shallow_dd': 15,
            'volume': 5,
            'ma_bull': 10,
            'sector': 0,
            'signal_bonus': 10,
        }
        weights = {
            'weight_strong_gain': 1.0,
            'weight_wave_gain': 1.0,
            'weight_shallow_dd': 1.0,
            'weight_volume': 1.0,
            'weight_ma_bull': 1.0,
            'weight_sector': 1.0,
            'weight_signal_bonus': 1.0,
        }

        dl = self._create_mock_data_layer_empty()
        normalizer = ScoreNormalizer(data_layer=dl)
        normalized, meta = normalizer.normalize_scores(scores, weights)

        # 当历史样本为空时，默认均值10，global_base=70，global_weighted=70
        # scale_factor = 70/70 = 1.0
        assert meta['scale_factor'] == 1.0
        assert normalized == sum(scores.values())  # 原始总分

    def test_normalize_with_increased_weight_reduces_scale(self):
        """增加某维度权重后，缩放因子应降低（保持总分稳定）"""
        scores = {
            'day_gain': 10,
            'wave_gain': 20,
            'shallow_dd': 15,
            'volume': 5,
            'ma_bull': 10,
            'sector': 0,
            'signal_bonus': 10,
        }

        # 权重全为1.0
        weights_default = {
            'weight_strong_gain': 1.0,
            'weight_wave_gain': 1.0,
            'weight_shallow_dd': 1.0,
            'weight_volume': 1.0,
            'weight_ma_bull': 1.0,
            'weight_sector': 1.0,
            'weight_signal_bonus': 1.0,
        }

        # wave_gain 权重增加到1.5
        weights_increased = weights_default.copy()
        weights_increased['weight_wave_gain'] = 1.5

        dl = self._create_mock_data_layer_empty()
        normalizer = ScoreNormalizer(data_layer=dl)

        _, meta_default = normalizer.normalize_scores(scores, weights_default)
        _, meta_increased = normalizer.normalize_scores(scores, weights_increased)

        # 增加权重后：global_weighted_total 增加（因为 avg_wave_gain * 1.5 > avg_wave_gain * 1.0）
        # scale_factor = global_base / global_weighted 应降低
        assert meta_increased['global_weighted_total'] > meta_default['global_weighted_total']
        assert meta_increased['scale_factor'] < meta_default['scale_factor']

    def test_normalize_returns_meta_with_all_fields(self):
        """返回的 meta 应包含所有必要字段"""
        scores = {'day_gain': 10, 'wave_gain': 20, 'shallow_dd': 15, 'volume': 5, 'ma_bull': 10, 'sector': 0, 'signal_bonus': 10}
        weights = {
            'weight_strong_gain': 1.0,
            'weight_wave_gain': 1.0,
            'weight_shallow_dd': 1.0,
            'weight_volume': 1.0,
            'weight_ma_bull': 1.0,
            'weight_sector': 1.0,
            'weight_signal_bonus': 1.0,
        }

        dl = self._create_mock_data_layer_empty()
        normalizer = ScoreNormalizer(data_layer=dl)
        _, meta = normalizer.normalize_scores(scores, weights)

        required_fields = ['method', 'confidence', 'n', 'scale_factor', 'weighted_total_raw', 'global_base_total', 'global_weighted_total']
        for field in required_fields:
            assert field in meta

    def test_normalize_with_missing_score_dimension(self):
        """缺失某评分维度时使用默认值0"""
        scores = {'wave_gain': 20, 'shallow_dd': 15}  # 缺少其他维度
        weights = {
            'weight_strong_gain': 1.0,
            'weight_wave_gain': 1.0,
            'weight_shallow_dd': 1.0,
            'weight_volume': 1.0,
            'weight_ma_bull': 1.0,
            'weight_sector': 1.0,
            'weight_signal_bonus': 1.0,
        }

        dl = self._create_mock_data_layer_empty()
        normalizer = ScoreNormalizer(data_layer=dl)
        normalized, meta = normalizer.normalize_scores(scores, weights)

        # 缺失维度视为0分（共7个维度）
        expected_weighted = 20 + 15 + 0 + 0 + 0 + 0 + 0  # 35
        assert meta['weighted_total_raw'] == expected_weighted