#!/usr/bin/env python3
"""
集中管理策略参数，从 SQLite 读取/写入，替代分散在各文件的硬编码 CONFIG。
"""
import os
import json
import sqlite3
from datetime import datetime


class StrategyConfig:
    """DB-backed strategy parameters with default fallbacks."""

    DEFAULTS = {
        # Entry parameters
        'first_wave_min_days':      (3,    'entry',  'Min consecutive up days for wave'),
        'first_wave_min_gain':      (0.15, 'entry',  'Min cumulative gain for wave'),
        'consolidation_max_days':   (15,   'entry',  'Max consolidation duration'),
        'consolidation_max_drawdown': (0.20, 'entry', 'Max consolidation drawdown'),
        'weak_strong_threshold':    (0.03, 'entry',  'Min gain for big bullish reversal'),
        'anomaly_amplitude':        (0.06, 'entry',  'Min amplitude for anomaly signal'),
        # Exit parameters
        'stop_loss_buffer':         (0.02, 'exit',   'Buffer below cons low for stop'),
        'trailing_stop_pct':        (0.08, 'exit',   'Max DD from peak for trailing stop'),
        'trailing_min_gain':        (0.10, 'exit',   'Min gain before trailing activates'),
        'max_hold_days':            (20,   'exit',   'Max holding period'),
        # Sector parameters
        'sector_momentum_window':   (10,   'entry',  'Lookback window for sector momentum'),
        'sector_min_strength':      (0.05, 'entry',  'Min sector momentum to be strong'),
        # Scoring parameters
        'score_base':               (5,    'scoring', 'Base score'),
        'score_wave_high':          (20,   'scoring', 'Bonus: wave gain > 30%'),
        'score_wave_med':           (10,   'scoring', 'Bonus: wave gain > 20%'),
        'score_shallow_dd':         (15,   'scoring', 'Bonus: cons DD < 8%'),
        'score_med_dd':             (10,   'scoring', 'Bonus: cons DD < 15%'),
        'score_strong_gain':        (15,   'scoring', 'Bonus: signal day gain > 7%'),
        'score_med_gain':           (10,   'scoring', 'Bonus: signal day gain > 5%'),
        'score_weak_gain':          (5,    'scoring', 'Bonus: signal day gain > 3%'),
        'score_high_vol':           (10,   'scoring', 'Bonus: volume ratio > 2x'),
        'score_med_vol':            (5,    'scoring', 'Bonus: volume ratio > 1.5x'),
        'score_full_bull':          (10,   'scoring', 'Bonus: MA5>MA10>MA20'),
        'score_partial_bull':       (5,    'scoring', 'Bonus: MA5>MA10'),
        'score_anomaly_bonus':      (10,   'scoring', 'Extra bonus for anomaly signal'),
        'score_sector_strong':       (5,    'scoring', 'Bonus: strong sector momentum'),
    }

    # 新增：动态参数注册表
    DYNAMIC_PARAMS = {
        # score_weight 类参数
        'weight_wave_gain': (1.0, 'score_weight', '波段涨幅评分权重系数'),
        'weight_shallow_dd': (1.0, 'score_weight', '浅回调评分权重系数'),
        'weight_strong_gain': (1.0, 'score_weight', '强势涨幅评分权重系数'),
        'weight_volume': (1.0, 'score_weight', '放量评分权重系数'),
        'weight_ma_bull': (1.0, 'score_weight', '多头排列评分权重系数'),
        'weight_anomaly': (1.0, 'score_weight', '异动信号额外权重'),
        'weight_sector': (1.0, 'score_weight', '板块动量权重'),
        'weight_signal_bonus': (1.0, 'score_weight', '信号类型加分权重'),

        # environment 类参数（统一使用 _threshold 命名，与设计文档 B.8 一致）
        'activity_coefficient': (1.0, 'environment', '当前环境活跃度系数'),
        'bull_threshold': (1.0, 'environment', '上升期活跃度'),
        'range_threshold': (0.7, 'environment', '震荡期活跃度'),
        'bear_threshold': (0.3, 'environment', '退潮期活跃度'),
    }

    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data.db')
        self.db_path = db_path
        self._ensure_table()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self):
        with self._get_conn() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS strategy_config (
                param_key   TEXT PRIMARY KEY,
                param_value REAL,
                description TEXT,
                category    TEXT,
                updated_at  TEXT
            )''')

    def init_if_empty(self):
        """Insert DEFAULTS into DB if table is empty. Idempotent."""
        with self._get_conn() as conn:
            count = conn.execute('SELECT COUNT(*) FROM strategy_config').fetchone()[0]
            if count == 0:
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                for key, (value, category, desc) in self.DEFAULTS.items():
                    conn.execute(
                        'INSERT INTO strategy_config (param_key, param_value, description, category, updated_at) '
                        'VALUES (?, ?, ?, ?, ?)',
                        (key, float(value), desc, category, now)
                    )

    def get(self, key):
        """Get a parameter value, falling back to DEFAULTS if not in DB."""
        with self._get_conn() as conn:
            row = conn.execute(
                'SELECT param_value FROM strategy_config WHERE param_key = ?', (key,)
            ).fetchone()
            if row is not None:
                return row['param_value']
        if key in self.DEFAULTS:
            return float(self.DEFAULTS[key][0])
        raise KeyError(f'Unknown parameter: {key}')

    def get_dict(self, category=None):
        """Get all parameters as a dict, optionally filtered by category."""
        result = {}
        with self._get_conn() as conn:
            if category:
                rows = conn.execute(
                    'SELECT param_key, param_value FROM strategy_config WHERE category = ?',
                    (category,)
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT param_key, param_value FROM strategy_config'
                ).fetchall()
            for row in rows:
                result[row['param_key']] = row['param_value']
        # Fill in any defaults not in DB
        for key, (value, cat, _) in self.DEFAULTS.items():
            if category and cat != category:
                continue
            if key not in result:
                result[key] = float(value)
        return result

    def set(self, key, value, description=None, category=None):
        """Set a parameter in the database, supporting dynamic params."""
        # 先检查DEFAULTS
        if key in self.DEFAULTS:
            default_val = self.DEFAULTS[key]
            default_desc = default_val[2] if len(default_val) > 2 else ''
            default_cat = default_val[1] if len(default_val) > 1 else ''
        elif key in self.DYNAMIC_PARAMS:
            default_val = self.DYNAMIC_PARAMS[key]
            default_desc = default_val[2] if len(default_val) > 2 else ''
            default_cat = default_val[1] if len(default_val) > 1 else ''
        else:
            default_desc = description or ''
            default_cat = category or 'unknown'

        # 使用传入值或默认值
        final_desc = description or default_desc
        final_cat = category or default_cat

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._get_conn() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO strategy_config (param_key, param_value, description, category, updated_at) '
                'VALUES (?, ?, ?, ?, ?)',
                (key, float(value), final_desc, final_cat, now)
            )

    def set_batch(self, param_dict):
        """Set multiple parameters at once."""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._get_conn() as conn:
            for key, value in param_dict.items():
                # 先检查DEFAULTS
                if key in self.DEFAULTS:
                    default_val = self.DEFAULTS[key]
                    default_desc = default_val[2] if len(default_val) > 2 else ''
                    default_cat = default_val[1] if len(default_val) > 1 else ''
                elif key in self.DYNAMIC_PARAMS:
                    default_val = self.DYNAMIC_PARAMS[key]
                    default_desc = default_val[2] if len(default_val) > 2 else ''
                    default_cat = default_val[1] if len(default_val) > 1 else ''
                else:
                    default_desc = ''
                    default_cat = 'unknown'

                conn.execute(
                    'INSERT OR REPLACE INTO strategy_config (param_key, param_value, description, category, updated_at) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (key, float(value), default_desc, default_cat, now)
                )

    def get_weights(self):
        """获取所有 score_weight 类参数"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT param_key, param_value FROM strategy_config WHERE category='score_weight'"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_environment(self):
        """获取所有 environment 类参数"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT param_key, param_value FROM strategy_config WHERE category='environment'"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_by_category(self, category):
        """按类别获取参数"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT param_key, param_value FROM strategy_config WHERE category=?",
                (category,)
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def export_snapshot(self, label=None):
        """Export current config as JSON string for reproducibility."""
        params = self.get_dict()
        snapshot = {
            'label': label or 'snapshot',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'params': params,
        }
        return json.dumps(snapshot, indent=2, ensure_ascii=False)


# ── CLI: quick test ──
if __name__ == '__main__':
    cfg = StrategyConfig()
    cfg.init_if_empty()
    print('StrategyConfig initialized')
    print(f'  first_wave_min_days = {cfg.get("first_wave_min_days")}')
    print(f'  trailing_stop_pct   = {cfg.get("trailing_stop_pct")}')
    print(f'  score_base          = {cfg.get("score_base")}')
    print(f'\nAll entry params: {cfg.get_dict("entry")}')
    print(f'\nSnapshot:\n{cfg.export_snapshot("test")}')
