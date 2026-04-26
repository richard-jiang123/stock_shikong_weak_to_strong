#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变更管理模块
职责：快照管理、参数隔离、批量回滚、主动回滚监控
"""
import os
import sys
import json
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer, StockDataLayer
from strategy_config import StrategyConfig
from signal_constants import SIGNAL_TYPE_MAPPING, get_weight_multiplier


class ChangeManager:
    """变更生命周期管理器"""

    # 主动回滚阈值配置
    ROLLBACK_CONFIG = {
        'monitor_days': 30,
        'expectancy_drop_threshold': 0.3,
        'win_rate_drop_threshold': 10,
        'consecutive_bad_days': 5,
        'min_samples_for_check': 10,
    }

    def __init__(self, db_path=None):
        if db_path is None:
            self.dl = get_data_layer()
        else:
            self.dl = StockDataLayer(db_path)
        self.cfg = StrategyConfig(db_path)
        self._ensure_tables()

    def _ensure_tables(self):
        """确保表存在"""
        with self.dl._get_conn() as conn:
            # param_snapshot 表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS param_snapshot (
                    id INTEGER PRIMARY KEY,
                    snapshot_date TEXT NOT NULL,
                    snapshot_type TEXT,
                    batch_id TEXT,
                    trigger_reason TEXT,
                    params_json TEXT,
                    signal_status_json TEXT,
                    environment_json TEXT,
                    is_restored INTEGER DEFAULT 0,
                    restored_at TEXT,
                    restore_reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_date ON param_snapshot(snapshot_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_batch ON param_snapshot(batch_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_type ON param_snapshot(snapshot_type)")

            # sandbox_config 表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sandbox_config (
                    id INTEGER PRIMARY KEY,
                    optimize_id INTEGER,
                    batch_id TEXT NOT NULL,
                    param_key TEXT NOT NULL,
                    sandbox_value TEXT NOT NULL,
                    current_value TEXT,
                    optimize_type TEXT NOT NULL,
                    status TEXT DEFAULT 'staged',
                    staged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    validation_started_at TEXT,
                    validated_at TEXT,
                    applied_at TEXT,
                    rejected_at TEXT,
                    rejection_reason TEXT,
                    rollback_triggered INTEGER DEFAULT 0,
                    rollback_at TEXT,
                    rollback_reason TEXT,
                    UNIQUE(param_key, batch_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sandbox_batch ON sandbox_config(batch_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sandbox_status ON sandbox_config(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sandbox_type ON sandbox_config(optimize_type)")

            # optimization_history 扩展字段
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN batch_id TEXT")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN snapshot_id INTEGER")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN trigger_reason TEXT")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN rollback_triggered INTEGER DEFAULT 0")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN rollback_at TEXT")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN rollback_reason TEXT")
            except Exception:
                pass

            conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_history_batch ON optimization_history(batch_id)")