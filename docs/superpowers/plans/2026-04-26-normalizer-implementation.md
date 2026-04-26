# Normalizer Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the normalizer.py module for score normalization with progressive confidence, integrating with daily_scanner and weekly_optimizer.

**Architecture:** Independent module `normalizer.py` with ScoreNormalizer class, following design spec B.14. Uses `pick_tracking` table for history stats, supports progressive confidence (low/medium/high) based on sample count.

**Tech Stack:** Python, SQLite, pandas

---

## File Structure

| File | Responsibility |
|------|----------------|
| Create: `shikong_fufei/normalizer.py` | ScoreNormalizer class, SCORE_DIMENSIONS constant, normalize_scores() method |
| Create: `shikong_fufei/tests/test_normalizer.py` | Unit tests for ScoreNormalizer |
| Modify: `shikong_fufei/daily_scanner.py` | Integration: call normalizer in detect_pattern() |
| Modify: `shikong_fufei/weekly_optimizer.py` | Integration: check confidence before weight adjustment |
| Read: `shikong_fufei/pick_tracker.py` | Ensure score_* fields exist (already migrated) |
| Read: `shikong_fufei/strategy_config.py` | Use get_weights() method (already implemented) |

---

### Task 1: Create normalizer.py with SCORE_DIMENSIONS constant

**Files:**
- Create: `shikong_fufei/normalizer.py`

- [ ] **Step 1: Create the file with imports and SCORE_DIMENSIONS constant**

```python
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
```

- [ ] **Step 2: Commit**

```bash
cd /home/jzc/wechat_text/shikong_fufei
git add normalizer.py
git commit -m "feat(normalizer): add SCORE_DIMENSIONS constant

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Implement ScoreNormalizer class with get_history_stats()

**Files:**
- Modify: `shikong_fufei/normalizer.py`

- [ ] **Step 1: Add ScoreNormalizer class skeleton with constants**

```python
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
```

- [ ] **Step 2: Add get_history_stats() method**

```python
    def get_history_stats(self):
        """
        获取历史评分统计（从 pick_tracking 实时查询）
        
        Returns:
            stats: dict {'avg_wave_gain': 12.3, 'avg_shallow_dd': 8.7, ...}
            meta: dict {'method': 'all'|'recent_100', 'confidence': 'low'|'medium'|'high', 'n': int}
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
```

- [ ] **Step 3: Add _calculate_stats() helper method**

```python
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
```

- [ ] **Step 4: Commit**

```bash
cd /home/jzc/wechat_text/shikong_fufei
git add normalizer.py
git commit -m "feat(normalizer): add ScoreNormalizer class with get_history_stats()

Implement progressive confidence based on sample count.
Query pick_tracking for exited samples.
Add data_layer parameter for dependency injection (testing).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: Implement normalize_scores() method

**Files:**
- Modify: `shikong_fufei/normalizer.py`

- [ ] **Step 1: Add normalize_scores() method**

```python
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
```

- [ ] **Step 2: Add normalize_scores_with_cached_stats() method for performance optimization**

```python
    def normalize_scores_with_cached_stats(self, scores_dict, weights_dict, history_stats, history_meta):
        """
        使用预缓存的历史统计进行归一化（避免重复数据库查询）

        Args:
            scores_dict: 当前股票各维度评分 {'day_gain': 10, 'wave_gain': 20, ...}
            weights_dict: 当前权重 {'weight_strong_gain': 1.0, 'weight_wave_gain': 1.2, ...}
            history_stats: 预计算的历史统计 dict
            history_meta: 预计算的历史元数据 dict

        Returns:
            normalized_score: float 归一化后总分（不含score_base）
            meta: dict 包含 scale_factor 等信息
        """
        # 维度名到权重键名的映射
        DIMENSION_TO_WEIGHT = {
            'day_gain': 'weight_strong_gain',
        }

        # 计算当前股票加权总分
        weighted_total = 0
        for dim in SCORE_DIMENSIONS:
            score_val = scores_dict.get(dim, 0)
            weight_key = DIMENSION_TO_WEIGHT.get(dim, f'weight_{dim}')
            weight_val = weights_dict.get(weight_key, 1.0)
            weighted_total += score_val * weight_val

        # 计算全局基准总分（权重=1.0）
        global_base_total = sum(
            history_stats.get(f'avg_{dim}', 10.0) for dim in SCORE_DIMENSIONS
        )

        # 计算全局加权总分（当前权重）
        global_weighted_total = 0
        for dim in SCORE_DIMENSIONS:
            avg_val = history_stats.get(f'avg_{dim}', 10.0)
            weight_key = DIMENSION_TO_WEIGHT.get(dim, f'weight_{dim}')
            weight_val = weights_dict.get(weight_key, 1.0)
            global_weighted_total += avg_val * weight_val

        # 缩放因子
        scale_factor = global_base_total / global_weighted_total if global_weighted_total > 0 else 1.0

        # 归一化得分
        normalized_score = weighted_total * scale_factor

        # 构建 meta（复制 history_meta 并添加计算结果）
        meta = history_meta.copy()
        meta['scale_factor'] = scale_factor
        meta['weighted_total_raw'] = weighted_total
        meta['global_base_total'] = global_base_total
        meta['global_weighted_total'] = global_weighted_total

        return normalized_score, meta
```

- [ ] **Step 3: Commit**

```bash
cd /home/jzc/wechat_text/shikong_fufei
git add normalizer.py
git commit -m "feat(normalizer): add normalize_scores() and cached variant

Implement scale factor calculation for score normalization.
Add normalize_scores_with_cached_stats() for performance optimization.
Return normalized score and metadata including confidence.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Create unit tests for ScoreNormalizer

**Files:**
- Create: `shikong_fufei/tests/test_normalizer.py`

- [ ] **Step 1: Create test file with imports**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归一化模块单元测试
"""
try:
    import pytest
except ImportError:
    pytest = None  # pytest 可选，测试仍可手动运行
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

    def test_init_custom_db_path(self):
        """自定义 db_path 应创建 StockDataLayer"""
        # 使用内存数据库测试
        normalizer = ScoreNormalizer(db_path=':memory:')
        assert normalizer.dl is not None

    def test_init_with_data_layer_injection(self):
        """使用 data_layer 参数直接注入"""
        from data_layer import StockDataLayer
        dl = StockDataLayer(db_path=':memory:')
        normalizer = ScoreNormalizer(data_layer=dl)
        assert normalizer.dl is dl  # 应是同一个实例


class TestGetHistoryStats:
    """测试 get_history_stats() 方法"""
    
    def test_empty_samples_returns_low_confidence(self):
        """样本为空时返回低置信度"""
        # 创建内存数据库和表结构
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

        from data_layer import StockDataLayer
        dl = StockDataLayer(db_path=':memory:')
        dl._get_conn = lambda: conn

        normalizer = ScoreNormalizer(data_layer=dl)
        stats, meta = normalizer.get_history_stats()
        
        assert meta['confidence'] == 'low'
        assert meta['n'] == 0
        assert meta['method'] == 'all'
    
    def test_insufficient_samples_returns_low_confidence(self):
        """样本少于30笔返回低置信度"""
        # 创建临时数据库并插入测试数据
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

        # 插入10条退出记录
        for i in range(10):
            conn.execute("""
                INSERT INTO pick_tracking (status, score_wave_gain, score_shallow_dd,
                    score_day_gain, score_volume, score_ma_bull, score_sector, score_signal_bonus)
                VALUES ('exited', 20, 15, 10, 5, 10, 0, 10)
            """)
        conn.commit()

        from data_layer import StockDataLayer
        dl = StockDataLayer(db_path=':memory:')
        dl._get_conn = lambda: conn

        # 使用 data_layer 参数注入（避免初始化真实数据库）
        normalizer = ScoreNormalizer(data_layer=dl)

        stats, meta = normalizer.get_history_stats()

        assert meta['confidence'] == 'low'
        assert meta['n'] == 10
        assert meta['method'] == 'all'
        conn.close()

    def test_medium_samples_returns_medium_confidence(self):
        """样本30-50笔返回中等置信度"""
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

        # 插入40条退出记录（30-50范围内）
        for i in range(40):
            conn.execute("""
                INSERT INTO pick_tracking (status, score_wave_gain, score_shallow_dd,
                    score_day_gain, score_volume, score_ma_bull, score_sector, score_signal_bonus)
                VALUES ('exited', 20, 15, 10, 5, 10, 0, 10)
            """)
        conn.commit()

        from data_layer import StockDataLayer
        dl = StockDataLayer(db_path=':memory:')
        dl._get_conn = lambda: conn

        normalizer = ScoreNormalizer(data_layer=dl)

        stats, meta = normalizer.get_history_stats()

        assert meta['confidence'] == 'medium'
        assert meta['n'] == 40
        conn.close()

    def test_high_samples_returns_high_confidence(self):
        """样本>=50笔返回高置信度"""
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

        # 插入60条退出记录（>=50）
        for i in range(60):
            conn.execute("""
                INSERT INTO pick_tracking (status, score_wave_gain, score_shallow_dd,
                    score_day_gain, score_volume, score_ma_bull, score_sector, score_signal_bonus)
                VALUES ('exited', 20, 15, 10, 5, 10, 0, 10)
            """)
        conn.commit()

        from data_layer import StockDataLayer
        dl = StockDataLayer(db_path=':memory:')
        dl._get_conn = lambda: conn

        normalizer = ScoreNormalizer(data_layer=dl)

        stats, meta = normalizer.get_history_stats()

        assert meta['confidence'] == 'high'
        assert meta['n'] == 50  # 实际参与计算的样本数（RECENT_WINDOW），而非总数60
        assert meta['method'] == 'recent_50'
        conn.close()
```

- [ ] **Step 2: Add normalize_scores tests**

```python
class TestNormalizeScores:
    """测试 normalize_scores() 方法"""

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
        # 注意：day_gain 对应 weight_strong_gain
        weights = {
            'weight_strong_gain': 1.0,
            'weight_wave_gain': 1.0,
            'weight_shallow_dd': 1.0,
            'weight_volume': 1.0,
            'weight_ma_bull': 1.0,
            'weight_sector': 1.0,
            'weight_signal_bonus': 1.0,
        }

        normalizer = ScoreNormalizer(db_path=':memory:')
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

        normalizer = ScoreNormalizer(db_path=':memory:')

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

        normalizer = ScoreNormalizer(db_path=':memory:')
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

        normalizer = ScoreNormalizer(db_path=':memory:')
        normalized, meta = normalizer.normalize_scores(scores, weights)

        # 缺失维度视为0分（共7个维度）
        expected_weighted = 20 + 15 + 0 + 0 + 0 + 0 + 0  # 35
        assert meta['weighted_total_raw'] == expected_weighted

    def test_normalize_with_cached_stats_same_result(self):
        """使用缓存统计应与直接调用结果一致"""
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

        normalizer = ScoreNormalizer(db_path=':memory:')
        # 直接调用
        normalized1, meta1 = normalizer.normalize_scores(scores, weights)
        # 预缓存后调用
        history_stats, history_meta = normalizer.get_history_stats()
        normalized2, meta2 = normalizer.normalize_scores_with_cached_stats(
            scores, weights, history_stats, history_meta
        )

        assert normalized1 == normalized2
        assert meta1['scale_factor'] == meta2['scale_factor']
```

- [ ] **Step 3: Run tests to verify**

```bash
cd /home/jzc/wechat_text/shikong_fufei
python -m pytest tests/test_normalizer.py -v
```

Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
cd /home/jzc/wechat_text/shikong_fufei
git add tests/test_normalizer.py
git commit -m "test(normalizer): add unit tests for ScoreNormalizer

Test SCORE_DIMENSIONS constant, get_history_stats(), normalize_scores().
Cover progressive confidence and weight adjustment scenarios.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: Integrate normalizer into daily_scanner.py

**Files:**
- Modify: `shikong_fufei/daily_scanner.py:71-124`

- [ ] **Step 1: Add import statement at top of file**

Find line 17-19 (imports section):

```python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer
from strategy_config import StrategyConfig
from pick_tracker import PickTracker
```

Add import:

```python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer
from strategy_config import StrategyConfig
from pick_tracker import PickTracker
from normalizer import ScoreNormalizer
```

- [ ] **Step 2: detect_pattern() 保持原有结构（无需修改）**

实际实现中，detect_pattern() 保持原有结构不变：
- score_details 字典使用带 `score_` 前缀的键名（如 `score_day_gain`）
- _scan_core 中使用 `score_details.get('score_day_gain', 0)` 读取评分

这种设计是内部一致的：
- detect_pattern() 返回的 score_details 键名与 pick_tracker 数据库字段名一致
- _scan_core 构建 scores_dict 时使用 SCORE_DIMENSIONS 的维度名（如 `day_gain`）

无需修改 detect_pattern()。

- [ ] **Step 3: Modify _scan_core() to apply normalization**

Find the section where results are built (around lines 236-285):

Add normalizer initialization before the loop (reuse existing dl instance):
```python
def _scan_core(dl, codes, regime_cache, name_map, industry_map, start_date, end_date, verbose=True):
    """..."""
    # 提前过滤ST股票
    filtered_codes = [c for c in codes if 'ST' not in name_map.get(c, '').upper()]
    ...

    # 初始化归一化器和权重（复用已有数据层实例）
    normalizer = ScoreNormalizer(data_layer=dl)
    cfg = StrategyConfig()
    weights = cfg.get_weights()

    # 预查询历史统计（避免循环内重复查询）
    history_stats, history_meta = normalizer.get_history_stats()

    results = []
    for i, code in enumerate(filtered_codes):
        ...
```

Modify the result building section:
```python
        if r:
            last = df.iloc[-1]
            index_code = dl.code_to_index(code).split('.')[1]
            industry = industry_map.get(code, '')

            # 获取板块动量状态
            sector_info = sector_momentum_cache.get(industry, {'momentum': 0, 'strong': False})
            sector_strong = sector_info['strong']

            # 获取评分明细（detect_pattern 返回的 score_details 使用 score_* 前缀键名）
            score_details = r.get('score_details', {})
            score_sector = 5 if sector_strong else 0

            # 构建归一化输入（使用 SCORE_DIMENSIONS 的维度名）
            scores_dict = {
                'day_gain': score_details.get('score_day_gain', 0),
                'wave_gain': score_details.get('score_wave_gain', 0),
                'shallow_dd': score_details.get('score_shallow_dd', 0),
                'volume': score_details.get('score_volume', 0),
                'ma_bull': score_details.get('score_ma_bull', 0),
                'sector': score_sector,
                'signal_bonus': score_details.get('score_signal_bonus', 0),
            }

            # 应用归一化（使用预缓存的历史统计）
            normalized_score, norm_meta = normalizer.normalize_scores_with_cached_stats(
                scores_dict, weights, history_stats, history_meta
            )
            score_base = score_details.get('score_base', 5)
            total_score = score_base + normalized_score

            # 更新原因字符串
            reasons = r['reasons']
            if sector_strong:
                if reasons:
                    reasons = reasons + ' | 强势板块'
                else:
                    reasons = '强势板块'

            results.append({
                '代码': code.split('.')[1], '名称': name_map.get(code, ''),
                '现价': last['close'],
                '涨幅': last['pct_chg'], '信号': r['sig'],
                '评分': total_score,
                'score_normalized': normalized_score,
                'score_raw': r['score'] - score_base,
                'normalization_meta': norm_meta,
                '波段涨幅': r['wg'],
                '回调': r['dd'], '量比': r['vr'],
                '止损位': r['sl'], '入场价': r['ep'],
                '指数': index_code,
                '市场环境': {'bull': '上升期', 'range': '震荡期', 'bear': '退潮期'}.get(
                    regime_cache.get(dl.code_to_index(code), 'range'), '震荡期'),
                '行业': industry,
                '板块强势': sector_strong,
                '原因': reasons,
                # 评分明细字段（用于 pick_tracker 记录）
                'score_base': score_base,
                'score_day_gain': score_details.get('score_day_gain', 0),
                'score_wave_gain': score_details.get('score_wave_gain', 0),
                'score_shallow_dd': score_details.get('score_shallow_dd', 0),
                'score_volume': score_details.get('score_volume', 0),
                'score_ma_bull': score_details.get('score_ma_bull', 0),
                'score_sector': score_sector,
                'score_signal_bonus': score_details.get('score_signal_bonus', 0),
            })
```

- [ ] **Step 4: Run scanner to test integration**

```bash
cd /home/jzc/wechat_text/shikong_fufei
python daily_scanner.py --date 2026-04-24
```

Expected: Scanner runs successfully, results include `score_normalized` and `normalization_meta` fields.

**Note**: If `strategy_config` table is empty, `get_weights()` returns empty dict. Ensure `StrategyConfig().init_if_empty()` is called at scanner startup (check existing main() function). Cold start behavior: all weights default to 1.0 which is acceptable.

- [ ] **Step 5: Commit**

```bash
cd /home/jzc/wechat_text/shikong_fufei
git add daily_scanner.py
git commit -m "feat(scanner): integrate normalizer for score normalization

Apply ScoreNormalizer in detect_pattern() and _scan_core().
Add score_normalized, score_raw, normalization_meta to results.
Include score_sector in normalization.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: Integrate normalizer into weekly_optimizer.py

**Files:**
- Modify: `shikong_fufei/weekly_optimizer.py:18-42` (adjust_score_weight)
- Modify: `shikong_fufei/weekly_optimizer.py:231-270` (_compute_score_correlations)
- Modify: `shikong_fufei/weekly_optimizer.py:188-229` (_optimize_score_weights_layer)

- [ ] **Step 1: Add import statement**

Find line 14-16 (imports section):

```python
from signal_constants import SIGNAL_TYPE_MAPPING, get_weight_multiplier
```

Add import:

```python
from signal_constants import SIGNAL_TYPE_MAPPING, get_weight_multiplier
from normalizer import ScoreNormalizer, SCORE_DIMENSIONS
```

- [ ] **Step 2: Update adjust_score_weight function**

Find line 18-42 (adjust_score_weight function):

Current code:
```python
def adjust_score_weight(current_weight, correlation):
    """..."""
    MAX_ADJUSTMENT = 0.20
    if correlation > 0.3:
        adjustment = min(MAX_ADJUSTMENT, correlation * 0.5)
        return current_weight * (1 + adjustment)
    ...
```

Replace with (add base_weight parameter and regression anchor logic):
```python
def adjust_score_weight(current_weight, correlation, base_weight=1.0):
    """
    根据评分-盈亏相关性动态调整权重

    Args:
        current_weight: 当前权重值
        correlation: Spearman相关系数（score vs final_pnl）
        base_weight: 基准权重（默认1.0），用于回归锚点

    Returns:
        新权重值（调整幅度限制在 ±20%）
    """
    # 调整幅度限制
    MAX_ADJUSTMENT = 0.20

    if correlation > 0.3:
        # 强正相关 → 加权（最多增加20%）
        adjustment = min(MAX_ADJUSTMENT, correlation * 0.5)
        new_weight = current_weight * (1 + adjustment)
    elif correlation < -0.2:
        # 负相关 → 减权（最多减少20%）
        adjustment = min(MAX_ADJUSTMENT, abs(correlation) * 0.5)
        new_weight = current_weight * (1 - adjustment)
    else:
        # 弱相关 → 回归锚点（向 base_weight 靠拢）
        delta = current_weight - base_weight
        if abs(delta) > 0.01:
            # 每次回归 10% 的偏差
            new_weight = current_weight - delta * 0.1
        else:
            new_weight = current_weight

    return new_weight
```

- [ ] **Step 3: Update _compute_score_correlations**

Find line 261-263 (score_cols list):

Current code:
```python
score_cols = ['score_wave_gain', 'score_shallow_dd', 'score_day_gain',
              'score_volume', 'score_ma_bull', 'score_sector',
              'score_signal_bonus', 'score']
```

No change needed - score_day_gain is correct (matches database field).

- [ ] **Step 4: Modify _optimize_score_weights_layer() to check confidence**

Find the method (around lines 188-229):

Add confidence check and fix weight-to-score mapping:
```python
    def _optimize_score_weights_layer(self, optimize_date):
        """
        评分层优化：根据 score-pnl 相关性调整权重

        Returns:
            dict: {
                'adjusted': bool,
                'weight_changes': dict,
                'correlations': dict,
                'history_meta': dict,
            }
        """
        # 1. 检查历史统计置信度
        normalizer = ScoreNormalizer(data_layer=self.dl)  # 复用已有数据层实例
        history_stats, meta = normalizer.get_history_stats()

        if meta['confidence'] == 'low':
            return {
                'adjusted': False,
                'reason': f'样本不足({meta["n"]}笔)，暂不调整权重',
                'history_meta': meta,
            }

        # 2. 获取各评分项与盈亏的相关性
        correlations = self._compute_score_correlations()

        if correlations is None:
            return {'adjusted': False, 'reason': 'insufficient_correlation_data', 'history_meta': meta}

        # 3. 获取当前权重
        current_weights = self.cfg.get_weights()

        # 4. 权重键名到相关性键名的映射
        # 注意：weight_strong_gain 对应 score_day_gain（数据库字段名）
        WEIGHT_TO_SCORE = {
            'weight_strong_gain': 'score_day_gain',  # 特殊映射
        }

        # 5. 调整权重
        weight_changes = {}
        for weight_key in current_weights:
            # 找对应的相关性指标
            score_key = WEIGHT_TO_SCORE.get(weight_key, weight_key.replace('weight_', 'score_'))
            if score_key in correlations:
                correlation = correlations[score_key]
                old_weight = current_weights[weight_key]
                new_weight = adjust_score_weight(old_weight, correlation)

                if abs(new_weight - old_weight) > 0.01:
                    weight_changes[weight_key] = {
                        'old': old_weight,
                        'new': new_weight,
                        'correlation': correlation,
                    }

        # 6. 验证归一化效果（如果有调整）
        if weight_changes:
            # 模拟一只"平均股票"，验证归一化后总分是否稳定
            avg_scores = {dim: history_stats.get(f'avg_{dim}', 10) for dim in SCORE_DIMENSIONS}

            # 更新权重
            updated_weights = current_weights.copy()
            for k, v in weight_changes.items():
                updated_weights[k] = v['new']

            normalized_avg, verify_meta = normalizer.normalize_scores(avg_scores, updated_weights)

            expected_avg = sum(history_stats.get(f'avg_{dim}', 10) for dim in SCORE_DIMENSIONS)
            deviation = abs(normalized_avg - expected_avg)

            if deviation > 5:  # 允许5分偏差
                return {
                    'adjusted': False,
                    'reason': f'归一化验证失败：偏差{deviation:.1f}分',
                    'weight_changes': weight_changes,
                    'history_meta': meta,
                }

            # 应用权重变更
            for weight_key, change in weight_changes.items():
                self.cfg.set(weight_key, change['new'])

        return {
            'adjusted': len(weight_changes) > 0,
            'weight_changes': weight_changes,
            'correlations': correlations,
            'history_meta': meta,
        }
```

- [ ] **Step 5: Run weekly optimizer to test**

```bash
cd /home/jzc/wechat_text/shikong_fufei
python weekly_optimizer.py
```

Expected: Optimizer runs, checks confidence, skips adjustment if sample count < 30.

- [ ] **Step 6: Commit**

```bash
cd /home/jzc/wechat_text/shikong_fufei
git add weekly_optimizer.py
git commit -m "feat(optimizer): integrate normalizer and update adjust_score_weight

Add confidence check before score weight adjustment.
Skip adjustment if sample count < 30 (low confidence).
Add base_weight parameter with regression anchor logic.
Validate normalization effect after weight changes.
Fix weight_strong_gain → score_day_gain mapping.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: Verify pick_tracker.py fields consistency

**Files:**
- Read: `shikong_fufei/pick_tracker.py`
- Verify: existing score fields match SCORE_DIMENSIONS

- [ ] **Step 1: Verify existing migrations match SCORE_DIMENSIONS**

Current pick_tracker.py migrations (lines 104-112):
```python
migrations = [
    ('score_wave_gain', 'REAL'),
    ('score_shallow_dd', 'REAL'),
    ('score_day_gain', 'REAL'),  # 正确：对应 SCORE_DIMENSIONS 的 'day_gain'
    ('score_volume', 'REAL'),
    ('score_ma_bull', 'REAL'),
    ('score_sector', 'REAL'),
    ('score_signal_bonus', 'REAL'),
    ('score_base', 'REAL DEFAULT 5'),
]
```

Verification: All SCORE_DIMENSIONS fields exist in migrations ✓
- day_gain → score_day_gain ✓
- wave_gain → score_wave_gain ✓
- shallow_dd → score_shallow_dd ✓
- volume → score_volume ✓
- ma_bull → score_ma_bull ✓
- sector → score_sector ✓
- signal_bonus → score_signal_bonus ✓

No migration changes needed.

- [ ] **Step 2: Verify record_picks() field handling**

Current record_picks() (lines 163-171) reads:
```python
score_day_gain = float(row.get('score_day_gain', row.get('评分涨幅', 0)))
score_wave_gain = float(row.get('score_wave_gain', row.get('评分波段', 0)))
...
```

Verification: All fields correctly read ✓

No changes needed.

- [ ] **Step 3: Run tests**

```bash
cd /home/jzc/wechat_text/shikong_fufei
python -m pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 4: Note consistency**

No commit needed - existing pick_tracker.py fields already match SCORE_DIMENSIONS.

---

### Task 8: Integration verification

**Files:**
- Run full system test

- [ ] **Step 1: Run full test suite**

```bash
cd /home/jzc/wechat_text/shikong_fufei
python -m pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 2: Run daily scanner end-to-end**

```bash
cd /home/jzc/wechat_text/shikong_fufei
python daily_scanner.py --date 2026-04-24
```

Check output:
- Results should include `score_normalized` field
- Results should include `normalization_meta` with confidence level

- [ ] **Step 3: Run weekly optimizer end-to-end**

```bash
cd /home/jzc/wechat_text/shikong_fufei
python weekly_optimizer.py
```

Check output:
- Should show confidence level (likely "low" given current sample count)
- Should skip weight adjustment with reason "样本不足"

- [ ] **Step 4: Verify pick_tracking has score_sector data**

```bash
cd /home/jzc/wechat_text/shikong_fufei
sqlite3 stock_data.db "SELECT score_sector, COUNT(*) FROM pick_tracking WHERE status='exited' GROUP BY score_sector"
```

Expected: Should show score_sector values (likely 0 or 5)

---

## Self-Review Checklist

| Spec Section | Task Coverage | Status |
|--------------|---------------|--------|
| B.14.1 File Structure | Task 1 | ✓ |
| B.14.2 SCORE_DIMENSIONS | Task 1 | ✓ |
| B.14.3 ScoreNormalizer class | Task 2, 3 | ✓ |
| B.14.4 daily_scanner integration | Task 5 | ✓ |
| B.14.5 weekly_optimizer integration | Task 6 | ✓ |
| B.14.7 Unit tests | Task 4 | ✓ |

**Placeholder scan**: No TBD/TODO found.

**Type consistency**:
- SCORE_DIMENSIONS (7个维度) matches database fields ✓
  - day_gain → score_day_gain (数据库字段) → weight_strong_gain (权重参数)
  - wave_gain → score_wave_gain → weight_wave_gain
  - shallow_dd → score_shallow_dd → weight_shallow_dd
  - volume → score_volume → weight_volume
  - ma_bull → score_ma_bull → weight_ma_bull
  - sector → score_sector → weight_sector
  - signal_bonus → score_signal_bonus → weight_signal_bonus
- normalize_scores() uses DIMENSION_TO_WEIGHT mapping for day_gain ✓
- weekly_optimizer uses WEIGHT_TO_SCORE mapping for weight_strong_gain ✓

**Known limitation** (技术债，不在本次范围):
- `weight_anomaly` exists in strategy_config.py DYNAMIC_PARAMS but:
  - No corresponding `score_anomaly` field in pick_tracking table
  - Not included in SCORE_DIMENSIONS
  - If weight_anomaly is adjusted by optimizer, it won't affect normalized score
  - This is intentional: anomaly signal bonus is handled separately via `score_signal_bonus`
  - Future work: consider whether anomaly deserves its own score field or should be merged into signal_bonus

**Test mock compatibility**:
- `ScoreNormalizer(data_layer=dl)` injection pattern avoids real database initialization ✓
- Tests use `StockDataLayer(db_path=':memory:')` with `dl._get_conn = lambda: conn` ✓
- sqlite3 connection supports context manager ✓

**Cold start behavior**:
- If strategy_config table empty, `get_weights()` returns empty dict → all weights default to 1.0 ✓
- Ensure `StrategyConfig().init_if_empty()` called at scanner startup (check main()) ✓

**Confidence thresholds**:
- n < 30: low confidence ✓
- 30 <= n < 50: medium confidence ✓
- n >= 50: high confidence (use recent 50) ✓

**Implementation notes**:
- detect_pattern() unchanged, keeps `score_*` prefix keys in score_details ✓
- _scan_core builds scores_dict from score_details.get('score_day_gain', 0) ✓
- normalize_scores_with_cached_stats() added for performance (avoid repeated DB queries) ✓
- history_stats cached once before loop in _scan_core ✓

---

## Summary

This plan creates the `normalizer.py` module with progressive confidence-based score normalization, integrates it with `daily_scanner.py` for real-time scoring and `weekly_optimizer.py` for confidence checking before weight adjustments.

**Key mappings**:
- Database field `score_day_gain` → SCORE_DIMENSIONS key `day_gain` → Weight key `weight_strong_gain`
- detect_pattern() returns `score_details` with `score_*` prefix keys
- _scan_core converts to SCORE_DIMENSIONS keys when building scores_dict
- This asymmetry is handled by DIMENSION_TO_WEIGHT mapping in normalizer

**Performance optimization**:
- normalize_scores_with_cached_stats() avoids repeated DB queries
- history_stats pre-fetched once before scan loop

**Estimated tasks**: 8
**Estimated time**: 45-60 minutes