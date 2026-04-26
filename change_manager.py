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

    # ─────────────────────────────────────────────
    # 快照管理
    # ─────────────────────────────────────────────

    def save_snapshot(self, trigger_reason: str, batch_id: str = None,
                      snapshot_type: str = 'pre_change') -> int:
        """
        保存当前参数快照（变更前调用）

        Args:
            trigger_reason: 触发原因 ('weekly_optimize' | 'critical_alert' | 'manual')
            batch_id: 批次ID，同批次变更共享
            snapshot_type: 快照类型 ('pre_change' | 'daily_backup' | 'manual')

        Returns:
            snapshot_id: 快照记录ID
        """
        snapshot_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 1. 采集 strategy_config 全部参数
        params_dict = self.cfg.get_dict()
        params_json = json.dumps(params_dict, ensure_ascii=False)

        # 2. 采集 signal_status 全部记录
        with self.dl._get_conn() as conn:
            signal_rows = conn.execute("""
                SELECT signal_type, display_name, status_level, weight_multiplier
                FROM signal_status
            """).fetchall()
        signal_status = [
            {
                'signal_type': r[0],
                'display_name': r[1],
                'status_level': r[2],
                'weight_multiplier': r[3],
            }
            for r in signal_rows
        ]
        signal_status_json = json.dumps(signal_status, ensure_ascii=False)

        # 3. 采集环境参数
        environment_dict = self.cfg.get_environment()
        environment_json = json.dumps(environment_dict, ensure_ascii=False)

        # 4. 写入快照表
        with self.dl._get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO param_snapshot
                (snapshot_date, snapshot_type, batch_id, trigger_reason,
                 params_json, signal_status_json, environment_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (snapshot_date, snapshot_type, batch_id, trigger_reason,
                  params_json, signal_status_json, environment_json))
            snapshot_id = cursor.lastrowid

        return snapshot_id

    def restore_snapshot(self, snapshot_id: int, reason: str = None) -> bool:
        """
        从快照恢复参数（原子操作）

        Args:
            snapshot_id: 快照ID
            reason: 恢复原因说明

        Returns:
            bool: 是否恢复成功
        """
        # 1. 先在事务外读取快照数据（避免长事务）
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT params_json, signal_status_json, environment_json, is_restored
                FROM param_snapshot
                WHERE id = ?
            """, (snapshot_id,)).fetchone()

        if row is None:
            return False

        # Check if already restored
        if row[3] == 1:
            return False

        params_json = row[0]
        signal_status_json = row[1]
        environment_json = row[2]

        params_dict = json.loads(params_json) if params_json else {}
        signal_status = json.loads(signal_status_json) if signal_status_json else []
        environment_dict = json.loads(environment_json) if environment_json else {}

        # 2. 原子写入（单事务）
        with self.dl._get_conn() as conn:
            # 恢复 strategy_config 参数
            for key, value in params_dict.items():
                conn.execute("""
                    INSERT OR REPLACE INTO strategy_config
                    (param_key, param_value, description, category, updated_at)
                    VALUES (?, ?,
                            COALESCE((SELECT description FROM strategy_config WHERE param_key=?), ''),
                            COALESCE((SELECT category FROM strategy_config WHERE param_key=?), 'unknown'),
                            ?)
                """, (key, value, key, key, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

            # 恢复 signal_status 状态
            for sig in signal_status:
                conn.execute("""
                    UPDATE signal_status
                    SET status_level = ?, weight_multiplier = ?, last_check_date = datetime('now')
                    WHERE signal_type = ?
                """, (sig.get('status_level'), sig.get('weight_multiplier'), sig.get('signal_type')))

            restored_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # 恢复 environment 参数
            for key, value in environment_dict.items():
                conn.execute("""
                    INSERT OR REPLACE INTO strategy_config
                    (param_key, param_value, description, category, updated_at)
                    VALUES (?, ?,
                            COALESCE((SELECT description FROM strategy_config WHERE param_key=?), ''),
                            COALESCE((SELECT category FROM strategy_config WHERE param_key=?), 'environment'),
                            ?)
                """, (key, value, key, key, restored_at))

            # 标记快照已恢复
            conn.execute("""
                UPDATE param_snapshot
                SET is_restored = 1, restored_at = ?, restore_reason = ?
                WHERE id = ?
            """, (restored_at, reason, snapshot_id))

        return True

    def get_latest_snapshot(self, batch_id: str = None) -> Optional[Dict]:
        """
        获取最新的快照

        Args:
            batch_id: 批次ID，如果指定则返回该批次的最新快照

        Returns:
            dict: 快照信息，包含 id, snapshot_date, snapshot_type, batch_id 等
        """
        with self.dl._get_conn() as conn:
            if batch_id:
                row = conn.execute("""
                    SELECT id, snapshot_date, snapshot_type, batch_id, trigger_reason,
                           params_json, signal_status_json, environment_json,
                           is_restored, restored_at, restore_reason, created_at
                    FROM param_snapshot
                    WHERE batch_id = ? AND is_restored = 0
                    ORDER BY id DESC
                    LIMIT 1
                """, (batch_id,)).fetchone()
            else:
                # 默认返回最新的 pre_change 类型快照
                row = conn.execute("""
                    SELECT id, snapshot_date, snapshot_type, batch_id, trigger_reason,
                           params_json, signal_status_json, environment_json,
                           is_restored, restored_at, restore_reason, created_at
                    FROM param_snapshot
                    WHERE snapshot_type = 'pre_change' AND is_restored = 0
                    ORDER BY id DESC
                    LIMIT 1
                """).fetchone()

        if row is None:
            return None

        return {
            'id': row[0],
            'snapshot_date': row[1],
            'snapshot_type': row[2],
            'batch_id': row[3],
            'trigger_reason': row[4],
            'params': json.loads(row[5]) if row[5] else {},
            'signal_status': json.loads(row[6]) if row[6] else [],
            'environment': json.loads(row[7]) if row[7] else {},
            'is_restored': row[8],
            'restored_at': row[9],
            'restore_reason': row[10],
            'created_at': row[11],
        }

    def get_snapshot_by_id(self, snapshot_id: int) -> Optional[Dict]:
        """
        根据ID获取快照详情

        Args:
            snapshot_id: 快照ID

        Returns:
            dict: 快照信息
        """
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT id, snapshot_date, snapshot_type, batch_id, trigger_reason,
                       params_json, signal_status_json, environment_json,
                       is_restored, restored_at, restore_reason, created_at
                FROM param_snapshot
                WHERE id = ?
            """, (snapshot_id,)).fetchone()

        if row is None:
            return None

        return {
            'id': row[0],
            'snapshot_date': row[1],
            'snapshot_type': row[2],
            'batch_id': row[3],
            'trigger_reason': row[4],
            'params': json.loads(row[5]) if row[5] else {},
            'signal_status': json.loads(row[6]) if row[6] else [],
            'environment': json.loads(row[7]) if row[7] else {},
            'is_restored': row[8],
            'restored_at': row[9],
            'restore_reason': row[10],
            'created_at': row[11],
        }