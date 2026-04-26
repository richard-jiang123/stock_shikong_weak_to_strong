#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变更管理模块
职责：快照管理、参数隔离、批量回滚、主动回滚监控
"""
import os
import sys
import json
import sqlite3
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

            # daily_monitor_log 表（监控日志）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_monitor_log (
                    id INTEGER PRIMARY KEY,
                    monitor_date TEXT,
                    alert_type TEXT,
                    alert_detail TEXT,
                    severity TEXT,
                    action_taken TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_date ON daily_monitor_log(monitor_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_type ON daily_monitor_log(alert_type)")

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

    # ─────────────────────────────────────────────
    # 参数隔离（沙盒配置）
    # ─────────────────────────────────────────────

    def stage_change(self, optimize_type: str, param_key: str, new_value,
                     batch_id: str, current_value=None) -> int:
        """
        暂存变更到沙盒配置（不影响实际配置）

        Args:
            optimize_type: 优化类型 ('signal_status' | 'strategy_config')
            param_key: 参数键名（signal_type 或 strategy_config 的 key）
            new_value: 新值
            batch_id: 批次ID
            current_value: 当前值，如果为 None 则自动获取

        Returns:
            sandbox_id: 沙盒记录ID
        """
        # 自动获取当前值
        if current_value is None:
            if optimize_type == 'signal_status':
                # 从 signal_status 表获取当前 status_level
                with self.dl._get_conn() as conn:
                    row = conn.execute("""
                        SELECT status_level FROM signal_status WHERE signal_type = ?
                    """, (param_key,)).fetchone()
                    current_value = row[0] if row else None
            else:
                # 从 strategy_config 获取当前值
                current_value = self.cfg.get(param_key)

        # 转换为字符串存储
        sandbox_value_str = str(new_value)
        current_value_str = str(current_value) if current_value is not None else None

        # 尝试插入，如果已存在则更新
        with self.dl._get_conn() as conn:
            try:
                cursor = conn.execute("""
                    INSERT INTO sandbox_config
                    (batch_id, param_key, sandbox_value, current_value, optimize_type, status)
                    VALUES (?, ?, ?, ?, ?, 'staged')
                """, (batch_id, param_key, sandbox_value_str, current_value_str, optimize_type))
                sandbox_id = cursor.lastrowid
            except sqlite3.IntegrityError:
                # 已存在，更新
                conn.execute("""
                    UPDATE sandbox_config
                    SET sandbox_value = ?, current_value = ?, optimize_type = ?, status = 'staged',
                        staged_at = CURRENT_TIMESTAMP
                    WHERE param_key = ? AND batch_id = ?
                """, (sandbox_value_str, current_value_str, optimize_type, param_key, batch_id))
                cursor = conn.execute("""
                    SELECT id FROM sandbox_config WHERE param_key = ? AND batch_id = ?
                """, (param_key, batch_id))
                sandbox_id = cursor.fetchone()[0]

        return sandbox_id

    def get_staged_params(self, batch_id: str) -> List[Dict]:
        """
        获取批次的暂存参数列表

        Args:
            batch_id: 批次ID

        Returns:
            List[Dict]: 暂存参数列表，每个元素包含:
                - id: sandbox_id
                - batch_id: 批次ID
                - param_key: 参数键
                - sandbox_value: 沙盒值（已转换类型）
                - current_value: 当前值（已转换类型）
                - optimize_type: 优化类型
                - status: 状态
                - staged_at: 暂存时间
        """
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT id, batch_id, param_key, sandbox_value, current_value,
                       optimize_type, status, staged_at
                FROM sandbox_config
                WHERE batch_id = ? AND status IN ('staged', 'validating', 'passed')
                ORDER BY staged_at
            """, (batch_id,)).fetchall()

        result = []
        for row in rows:
            sandbox_id, batch_id_val, param_key, sandbox_value_str, current_value_str, \
                optimize_type, status, staged_at = row

            # 根据 optimize_type 解析值
            if optimize_type == 'signal_status':
                # signal_status 保持字符串
                sandbox_value = sandbox_value_str
                current_value = current_value_str
            else:
                # strategy_config 尝试转换为数值
                try:
                    sandbox_value = float(sandbox_value_str) if sandbox_value_str else None
                except (ValueError, TypeError):
                    sandbox_value = sandbox_value_str
                try:
                    current_value = float(current_value_str) if current_value_str else None
                except (ValueError, TypeError):
                    current_value = current_value_str

            result.append({
                'id': sandbox_id,
                'batch_id': batch_id_val,
                'param_key': param_key,
                'sandbox_value': sandbox_value,
                'current_value': current_value,
                'optimize_type': optimize_type,
                'status': status,
                'staged_at': staged_at,
            })

        return result

    def get_all_staged_batches(self) -> List[Dict]:
        """
        获取所有暂存批次列表

        Returns:
            List[Dict]: 批次列表，每个元素包含:
                - batch_id: 批次ID
                - staged_at: 暂存时间（最早的）
                - change_count: 变更数量
        """
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT batch_id, MIN(staged_at) as staged_at, COUNT(*) as change_count
                FROM sandbox_config
                WHERE status IN ('staged', 'validating', 'passed')
                GROUP BY batch_id
                ORDER BY staged_at DESC
            """).fetchall()

        return [
            {
                'batch_id': row[0],
                'staged_at': row[1],
                'change_count': row[2],
            }
            for row in rows
        ]

    def update_status(self, sandbox_id: int, status: str, reason: str = None) -> bool:
        """
        更新沙盒记录状态

        Args:
            sandbox_id: 沙盒记录ID
            status: 新状态 ('staged' | 'validating' | 'passed' | 'applied' | 'rejected')
            reason: 拒绝原因（仅 rejected 状态需要）

        Returns:
            bool: 是否更新成功
        """
        with self.dl._get_conn() as conn:
            # 根据状态设置相应的时间戳
            if status == 'validating':
                conn.execute("""
                    UPDATE sandbox_config
                    SET status = ?, validation_started_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, sandbox_id))
            elif status == 'passed':
                conn.execute("""
                    UPDATE sandbox_config
                    SET status = ?, validated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, sandbox_id))
            elif status == 'applied':
                conn.execute("""
                    UPDATE sandbox_config
                    SET status = ?, applied_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, sandbox_id))
            elif status == 'rejected':
                conn.execute("""
                    UPDATE sandbox_config
                    SET status = ?, rejected_at = CURRENT_TIMESTAMP, rejection_reason = ?
                    WHERE id = ?
                """, (status, reason, sandbox_id))
            else:
                # 默认状态（如 'staged'）
                conn.execute("""
                    UPDATE sandbox_config SET status = ? WHERE id = ?
                """, (status, sandbox_id))

            return conn.execute("SELECT changes()").fetchone()[0] > 0

    def commit_change(self, sandbox_id: int) -> bool:
        """
        提交变更（将沙盒值写入实际配置）

        Args:
            sandbox_id: 沙盒记录ID

        Returns:
            bool: 是否提交成功
        """
        # 使用单一事务确保原子性
        with self.dl._get_conn() as conn:
            # 1. 读取沙盒记录（包含状态）
            row = conn.execute("""
                SELECT param_key, sandbox_value, current_value, optimize_type, status
                FROM sandbox_config WHERE id = ?
            """, (sandbox_id,)).fetchone()

            if row is None:
                return False

            param_key, sandbox_value_str, current_value_str, optimize_type, status = row

            # 2. 验证状态：只允许从 passed 或 staged 状态提交
            if status not in ('passed', 'staged'):
                return False

            # 3. 根据 optimize_type 转换值并写入（在同一事务内）
            if optimize_type == 'signal_status':
                # signal_status: 写入 signal_status 表
                new_status = sandbox_value_str
                weight_multiplier = get_weight_multiplier(new_status)
                conn.execute("""
                    UPDATE signal_status
                    SET status_level = ?, weight_multiplier = ?, last_check_date = datetime('now')
                    WHERE signal_type = ?
                """, (new_status, weight_multiplier, param_key))
            else:
                # strategy_config: 转换为数值并写入
                try:
                    new_value = float(sandbox_value_str)
                except (ValueError, TypeError):
                    new_value = sandbox_value_str

                # 直接写入 strategy_config 表
                conn.execute("""
                    INSERT OR REPLACE INTO strategy_config
                    (param_key, param_value, description, category, updated_at)
                    VALUES (?, ?,
                            COALESCE((SELECT description FROM strategy_config WHERE param_key=?), ''),
                            COALESCE((SELECT category FROM strategy_config WHERE param_key=?), 'unknown'),
                            ?)
                """, (param_key, str(new_value), param_key, param_key,
                      datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

            # 4. 更新沙盒状态为 applied（在同一事务内）
            conn.execute("""
                UPDATE sandbox_config
                SET status = 'applied', applied_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (sandbox_id,))

            return conn.execute("SELECT changes()").fetchone()[0] > 0

    def reject_change(self, sandbox_id: int, reason: str) -> bool:
        """
        拒绝变更

        Args:
            sandbox_id: 沙盒记录ID
            reason: 拒绝原因

        Returns:
            bool: 是否拒绝成功
        """
        return self.update_status(sandbox_id, 'rejected', reason)

    # ─────────────────────────────────────────────
    # 批量回滚
    # ─────────────────────────────────────────────

    def rollback_batch(self, batch_id: str, reason: str) -> Dict:
        """
        回滚批次变更（原子操作：所有更新在单一事务内完成）

        Args:
            batch_id: 批次ID
            reason: 回滚原因

        Returns:
            dict: 包含 rolled_back, failed, snapshot_id, reason
        """
        # 1. 获取快照（事务外读取，只读操作）
        snapshot = self.get_latest_snapshot(batch_id=batch_id)

        if snapshot is None:
            return {
                'rolled_back': 0,
                'failed': 0,
                'snapshot_id': None,
                'reason': 'no_snapshot_found'
            }

        snapshot_id = snapshot['id']

        # 检查快照是否已恢复
        snapshot_full = self.get_snapshot_by_id(snapshot_id)
        if snapshot_full['is_restored'] == 1:
            return {
                'rolled_back': 0,
                'failed': 0,
                'snapshot_id': snapshot_id,
                'reason': 'snapshot_already_restored'
            }

        rollback_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 2. 所有更新在单一事务内完成（原子性）
        with self.dl._get_conn() as conn:
            # a. 恢复 strategy_config 参数（从快照内联）
            for key, value in snapshot['params'].items():
                conn.execute("""
                    INSERT OR REPLACE INTO strategy_config
                    (param_key, param_value, description, category, updated_at)
                    VALUES (?, ?,
                            COALESCE((SELECT description FROM strategy_config WHERE param_key=?), ''),
                            COALESCE((SELECT category FROM strategy_config WHERE param_key=?), 'unknown'),
                            ?)
                """, (key, value, key, key, rollback_at))

            # b. 恢复 signal_status 状态（从快照内联）
            for item in snapshot['signal_status']:
                conn.execute("""
                    UPDATE signal_status SET
                        status_level = ?, weight_multiplier = ?, last_check_date = datetime('now')
                    WHERE signal_type = ?
                """, (item['status_level'], item['weight_multiplier'], item['signal_type']))

            # c. 恢复 environment 参数（从快照内联）
            for key, value in snapshot['environment'].items():
                conn.execute("""
                    INSERT OR REPLACE INTO strategy_config
                    (param_key, param_value, description, category, updated_at)
                    VALUES (?, ?,
                            COALESCE((SELECT description FROM strategy_config WHERE param_key=?), ''),
                            COALESCE((SELECT category FROM strategy_config WHERE param_key=?), 'environment'),
                            ?)
                """, (key, value, key, key, rollback_at))

            # d. 更新 sandbox_config（处理所有相关状态）
            conn.execute("""
                UPDATE sandbox_config
                SET rollback_triggered = 1, rollback_at = ?, rollback_reason = ?, status = 'rejected'
                WHERE batch_id = ? AND status IN ('staged', 'validating', 'passed', 'applied')
            """, (rollback_at, reason, batch_id))

            # e. 更新 optimization_history
            conn.execute("""
                UPDATE optimization_history
                SET rollback_triggered = 1, rollback_at = ?, rollback_reason = ?
                WHERE batch_id = ? AND rollback_triggered = 0
            """, (rollback_at, reason, batch_id))

            # f. 标记快照为已恢复
            conn.execute("""
                UPDATE param_snapshot SET is_restored = 1, restored_at = ?, restore_reason = ?
                WHERE id = ?
            """, (rollback_at, reason, snapshot_id))

        # 3. 统计回滚数量
        with self.dl._get_conn() as conn:
            rolled_count = conn.execute("""
                SELECT COUNT(*) FROM sandbox_config
                WHERE batch_id = ? AND rollback_triggered = 1
            """, (batch_id,)).fetchone()[0]

        return {
            'rolled_back': rolled_count,
            'failed': 0,
            'snapshot_id': snapshot_id,
            'reason': reason,
        }

    def get_batch_changes(self, batch_id: str) -> List[Dict]:
        """
        获取批次的所有变更记录

        Args:
            batch_id: 批次ID

        Returns:
            List[Dict]: 变更记录列表，每个元素包含:
                - id: sandbox_id
                - param_key: 参数键
                - sandbox_value: 沙盒值
                - current_value: 当前值
                - optimize_type: 优化类型
                - status: 状态
                - staged_at: 暂存时间
                - applied_at: 应用时间
                - rollback_triggered: 是否已回滚
                - rollback_at: 回滚时间
        """
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT id, param_key, sandbox_value, current_value, optimize_type,
                       status, staged_at, applied_at, rollback_triggered, rollback_at
                FROM sandbox_config
                WHERE batch_id = ?
                ORDER BY staged_at
            """, (batch_id,)).fetchall()

        result = []
        for row in rows:
            sandbox_id, param_key, sandbox_value_str, current_value_str, optimize_type, \
                status, staged_at, applied_at, rollback_triggered, rollback_at = row

            # 根据 optimize_type 解析值
            if optimize_type == 'signal_status':
                sandbox_value = sandbox_value_str
                current_value = current_value_str
            else:
                try:
                    sandbox_value = float(sandbox_value_str) if sandbox_value_str else None
                except (ValueError, TypeError):
                    sandbox_value = sandbox_value_str
                try:
                    current_value = float(current_value_str) if current_value_str else None
                except (ValueError, TypeError):
                    current_value = current_value_str

            result.append({
                'id': sandbox_id,
                'param_key': param_key,
                'sandbox_value': sandbox_value,
                'current_value': current_value,
                'optimize_type': optimize_type,
                'status': status,
                'staged_at': staged_at,
                'applied_at': applied_at,
                'rollback_triggered': rollback_triggered,
                'rollback_at': rollback_at,
            })

        return result

    def get_batch_info(self, batch_id: str) -> Optional[Dict]:
        """
        获取批次详细信息

        Args:
            batch_id: 批次ID

        Returns:
            Dict: 批次信息，包含:
                - batch_id: 批次ID
                - snapshot: 关联的快照信息（如果有）
                - changes: 变更记录列表
                - total_changes: 总变更数
                - applied_count: 已应用数
                - rollback_count: 已回滚数
                - is_rolled_back: 批次是否已回滚
        """
        # 获取变更记录
        changes = self.get_batch_changes(batch_id)

        if not changes:
            return None

        # 获取关联的快照
        snapshot = self.get_latest_snapshot(batch_id=batch_id)

        # 统计状态
        total_changes = len(changes)
        applied_count = sum(1 for c in changes if c['status'] == 'applied')
        rollback_count = sum(1 for c in changes if c['rollback_triggered'] == 1)

        # 判断批次是否已回滚（有回滚记录）
        is_rolled_back = rollback_count > 0

        return {
            'batch_id': batch_id,
            'snapshot': snapshot,
            'changes': changes,
            'total_changes': total_changes,
            'applied_count': applied_count,
            'rollback_count': rollback_count,
            'is_rolled_back': is_rolled_back,
        }

    # ─────────────────────────────────────────────
    # 主动回滚监控（Layer A）
    # ─────────────────────────────────────────────

    def monitor_and_rollback(self) -> Dict:
        """
        检查已应用的批次并在检测到性能退化时触发回滚

        Returns:
            dict: {
                'checked': int,       # 检查的批次数量
                'rollback_triggered': int,  # 触发回滚的批次数量
                'details': list       # 详细检查结果
            }
        """
        # 1. 获取监控窗口内的已应用批次
        batches = self.get_applied_batches_in_monitor_window()

        if not batches:
            return {
                'checked': 0,
                'rollback_triggered': 0,
                'details': []
            }

        details = []
        rollback_count = 0

        # 2. 检查每个批次的性能退化
        for batch in batches:
            degradation = self.check_performance_degradation(batch)
            details.append({
                'batch_id': batch['batch_id'],
                'applied_at': batch['applied_at'],
                'should_rollback': degradation['should_rollback'],
                'reason': degradation.get('reason', ''),
                'expectancy_drop': degradation.get('expectancy_drop', 0),
                'win_rate_drop': degradation.get('win_rate_drop', 0),
            })

            # 3. 如果检测到退化，触发回滚
            if degradation['should_rollback']:
                rollback_result = self.rollback_batch(
                    batch['batch_id'],
                    reason=degradation['reason']
                )

                if rollback_result['rolled_back'] > 0:
                    rollback_count += 1
                    # 记录回滚日志
                    self._log_rollback(batch, degradation)

        return {
            'checked': len(batches),
            'rollback_triggered': rollback_count,
            'details': details
        }

    def get_applied_batches_in_monitor_window(self) -> List[Dict]:
        """
        获取监控窗口内（30天）的已应用批次列表

        Returns:
            List[Dict]: 批次列表，每个元素包含:
                - batch_id: 批次ID
                - applied_at: 应用时间
                - monitor_days_elapsed: 已监控天数
        """
        cutoff_date = datetime.now() - timedelta(days=self.ROLLBACK_CONFIG['monitor_days'])
        cutoff_str = cutoff_date.strftime('%Y-%m-%d %H:%M:%S')

        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT DISTINCT batch_id, MIN(applied_at) as applied_at
                FROM sandbox_config
                WHERE status = 'applied'
                  AND rollback_triggered = 0
                  AND applied_at >= ?
                GROUP BY batch_id
                ORDER BY applied_at DESC
            """, (cutoff_str,)).fetchall()

        result = []
        for row in rows:
            batch_id, applied_at = row
            # 计算监控天数
            applied_dt = datetime.strptime(applied_at, '%Y-%m-%d %H:%M:%S')
            days_elapsed = (datetime.now() - applied_dt).days
            result.append({
                'batch_id': batch_id,
                'applied_at': applied_at,
                'monitor_days_elapsed': days_elapsed
            })

        return result

    def check_performance_degradation(self, batch: Dict) -> Dict:
        """
        检查批次是否发生性能退化

        Args:
            batch: 批次信息 dict，包含 batch_id, applied_at

        Returns:
            dict: {
                'should_rollback': bool,
                'reason': str,
                'expectancy_drop': float,  # 期望值下降百分比
                'win_rate_drop': float,    # 胜率下降值
                'baseline': dict,          # 基线指标
                'current': dict            # 当前指标
            }
        """
        batch_id = batch['batch_id']
        applied_at = batch['applied_at']

        # 1. 获取快照作为基线
        snapshot = self.get_latest_snapshot(batch_id=batch_id)
        baseline = self._calc_baseline_metrics(snapshot)

        # 2. 计算当前指标
        current = self._calc_current_metrics(applied_at)

        # 3. 检查样本数是否足够
        if current['sample_count'] < self.ROLLBACK_CONFIG['min_samples_for_check']:
            return {
                'should_rollback': False,
                'reason': 'sample_insufficient',
                'expectancy_drop': 0,
                'win_rate_drop': 0,
                'baseline': baseline,
                'current': current
            }

        # 4. 计算退化程度
        expectancy_drop = 0
        if baseline['expectancy'] > 0 and current['expectancy'] < baseline['expectancy']:
            expectancy_drop = (baseline['expectancy'] - current['expectancy']) / baseline['expectancy']

        win_rate_drop = baseline['win_rate'] - current['win_rate']

        # 5. 检查是否超过阈值
        reasons = []

        # 期望值下降超过30%
        if expectancy_drop > self.ROLLBACK_CONFIG['expectancy_drop_threshold']:
            reasons.append(f"expectancy_drop_{expectancy_drop:.1%}")

        # 胜率下降超过10个百分点
        if win_rate_drop > self.ROLLBACK_CONFIG['win_rate_drop_threshold']:
            reasons.append(f"win_rate_drop_{win_rate_drop:.1f}%")

        # 连续5个交易日负期望值
        consecutive_bad = self._check_consecutive_bad_trading_days(applied_at)
        if consecutive_bad >= self.ROLLBACK_CONFIG['consecutive_bad_days']:
            reasons.append(f"consecutive_bad_days_{consecutive_bad}")

        should_rollback = len(reasons) > 0
        reason = '; '.join(reasons) if reasons else ''

        return {
            'should_rollback': should_rollback,
            'reason': reason,
            'expectancy_drop': expectancy_drop,
            'win_rate_drop': win_rate_drop,
            'baseline': baseline,
            'current': current
        }

    def _calc_baseline_metrics(self, snapshot: Optional[Dict]) -> Dict:
        """
        计算变更前的基线指标

        Args:
            snapshot: 快照信息，如果为 None 则返回默认值

        Returns:
            dict: {
                'expectancy': float,  # 期望值（百分比形式）
                'win_rate': float,    # 胜率（百分比）
                'sample_count': int   # 样本数量
            }
        """
        if snapshot is None:
            # 无快照时返回默认值（百分比形式）
            return {
                'expectancy': 4.2,
                'win_rate': 59.8,
                'sample_count': 0
            }

        # 从快照创建时间往前取30天的数据作为基线
        snapshot_date = snapshot.get('snapshot_date')
        if snapshot_date:
            # 解析快照日期
            try:
                snap_dt = datetime.strptime(snapshot_date, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                snap_dt = datetime.strptime(snapshot_date, '%Y-%m-%d')

            start_date = (snap_dt - timedelta(days=30)).strftime('%Y-%m-%d')
            end_date = snap_dt.strftime('%Y-%m-%d')
            return self._calc_metrics_in_range(start_date, end_date)
        else:
            # 无日期信息，返回默认值
            return {
                'expectancy': 4.2,
                'win_rate': 59.8,
                'sample_count': 0
            }

    def _calc_current_metrics(self, applied_at: str) -> Dict:
        """
        计算变更后的当前指标

        Args:
            applied_at: 应用时间字符串

        Returns:
            dict: {
                'expectancy': float,  # 期望值（百分比形式）
                'win_rate': float,    # 胜率（百分比）
                'sample_count': int   # 样本数量
            }
        """
        try:
            applied_dt = datetime.strptime(applied_at, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            applied_dt = datetime.strptime(applied_at, '%Y-%m-%d')

        # 从应用时间到当前时间的所有数据
        start_date = applied_dt.strftime('%Y-%m-%d')
        end_date = datetime.now().strftime('%Y-%m-%d')

        return self._calc_metrics_in_range(start_date, end_date)

    def _calc_metrics_in_range(self, start_date: str, end_date: str) -> Dict:
        """
        计算指定时间范围内的交易指标

        Args:
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)

        Returns:
            dict: {
                'expectancy': float,  # 期望值（百分比形式，如 4.2 表示 4.2%）
                'win_rate': float,    # 胜率（百分比，如 59.8 表示 59.8%）
                'sample_count': int   # 样本数量
            }
        """
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT final_pnl_pct FROM pick_tracking
                WHERE status = 'exited'
                  AND exit_date >= ?
                  AND exit_date <= ?
            """, (start_date, end_date)).fetchall()

        pnl_values = [row[0] for row in rows if row[0] is not None]

        if not pnl_values:
            return {
                'expectancy': 0.0,
                'win_rate': 0.0,
                'sample_count': 0
            }

        # 计算期望值（平均盈亏百分比）
        expectancy = sum(pnl_values) / len(pnl_values)

        # 计算胜率（盈利样本占比）
        wins = sum(1 for pnl in pnl_values if pnl > 0)
        win_rate = (wins / len(pnl_values)) * 100

        return {
            'expectancy': expectancy,
            'win_rate': win_rate,
            'sample_count': len(pnl_values)
        }

    def _check_consecutive_bad_trading_days(self, applied_at: str) -> int:
        """
        计算连续负期望值的交易日数量

        Args:
            applied_at: 应用时间字符串

        Returns:
            int: 连续负期望值的交易日天数
        """
        try:
            applied_dt = datetime.strptime(applied_at, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            applied_dt = datetime.strptime(applied_at, '%Y-%m-%d')

        # 从应用时间到当前时间，按交易日检查
        start_date = applied_dt.strftime('%Y-%m-%d')
        end_date = datetime.now().strftime('%Y-%m-%d')

        with self.dl._get_conn() as conn:
            # 获取每个交易日的期望值
            rows = conn.execute("""
                SELECT exit_date, final_pnl_pct FROM pick_tracking
                WHERE status = 'exited'
                  AND exit_date >= ?
                  AND exit_date <= ?
                ORDER BY exit_date DESC
            """, (start_date, end_date)).fetchall()

        if not rows:
            return 0

        # 按日期分组计算每日期望值
        daily_pnl = {}
        for row in rows:
            exit_date, pnl = row
            if exit_date and pnl is not None:
                if exit_date not in daily_pnl:
                    daily_pnl[exit_date] = []
                daily_pnl[exit_date].append(pnl)

        # 从最近日期开始，计算连续负期望值天数
        consecutive_bad = 0
        sorted_dates = sorted(daily_pnl.keys(), reverse=True)

        for date in sorted_dates:
            pnls = daily_pnl[date]
            if pnls:
                day_expectancy = sum(pnls) / len(pnls)
                if day_expectancy < 0:
                    consecutive_bad += 1
                else:
                    # 遇到正期望值，中断计数
                    break

        return consecutive_bad

    def _log_rollback(self, batch: Dict, degradation: Dict) -> bool:
        """
        记录回滚日志到 daily_monitor_log

        Args:
            batch: 批次信息
            degradation: 退化检测结果

        Returns:
            bool: 是否记录成功
        """
        monitor_date = datetime.now().strftime('%Y-%m-%d')
        alert_detail = f"batch_id={batch['batch_id']}; reason={degradation['reason']}; " \
                       f"expectancy_drop={degradation['expectancy_drop']:.2%}; " \
                       f"win_rate_drop={degradation['win_rate_drop']:.1f}%"

        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT INTO daily_monitor_log
                (monitor_date, alert_type, alert_detail, severity, action_taken, created_at)
                VALUES (?, 'auto_rollback', ?, 'critical', 'rollback_completed', CURRENT_TIMESTAMP)
            """, (monitor_date, alert_detail))

            return conn.execute("SELECT changes()").fetchone()[0] > 0

    # ─────────────────────────────────────────────
    # 辅助方法
    # ─────────────────────────────────────────────

    def generate_batch_id(self, date_str: str = None) -> str:
        """
        生成批次ID
        格式: YYYYMMDD-{seq}
        Args:
            date_str: 日期字符串，默认今天
        Returns:
            batch_id
        """
        if date_str is None:
            date_str = datetime.now().strftime('%Y%m%d')

        with self.dl._get_conn() as conn:
            count = conn.execute("""
                SELECT COUNT(DISTINCT batch_id) FROM sandbox_config
                WHERE batch_id LIKE ?
            """, (f"{date_str}-%",)).fetchone()[0]

        seq = count + 1
        return f"{date_str}-{seq:03d}"

    def get_status_summary(self) -> Dict:
        """获取变更管理状态摘要"""
        with self.dl._get_conn() as conn:
            staged_count = conn.execute(
                "SELECT COUNT(*) FROM sandbox_config WHERE status='staged'"
            ).fetchone()[0]

            validating_count = conn.execute(
                "SELECT COUNT(*) FROM sandbox_config WHERE status='validating'"
            ).fetchone()[0]

            passed_count = conn.execute(
                "SELECT COUNT(*) FROM sandbox_config WHERE status='passed'"
            ).fetchone()[0]

            applied_count = conn.execute(
                "SELECT COUNT(*) FROM sandbox_config WHERE status='applied'"
            ).fetchone()[0]

            rollback_count = conn.execute(
                "SELECT COUNT(*) FROM sandbox_config WHERE rollback_triggered=1"
            ).fetchone()[0]

            latest_snapshot = conn.execute("""
                SELECT id, snapshot_date, batch_id FROM param_snapshot
                ORDER BY id DESC LIMIT 1
            """).fetchone()

        return {
            'staged': staged_count,
            'validating': validating_count,
            'passed': passed_count,
            'applied': applied_count,
            'rolled_back': rollback_count,
            'latest_snapshot': {
                'id': latest_snapshot[0],
                'date': latest_snapshot[1],
                'batch_id': latest_snapshot[2],
            } if latest_snapshot else None,
        }

    def print_status_summary(self):
        """打印状态摘要"""
        summary = self.get_status_summary()

        print(f"\n{'='*50}")
        print("[变更管理状态]")
        print(f"{'='*50}")

        print(f"\n[待处理]")
        print(f"  暂存: {summary['staged']} 项")
        print(f"  验证中: {summary['validating']} 项")
        print(f"  已通过待应用: {summary['passed']} 项")

        print(f"\n[已处理]")
        print(f"  已应用: {summary['applied']} 项")
        print(f"  已回滚: {summary['rolled_back']} 项")

        if summary['latest_snapshot']:
            print(f"\n[最近快照] ID={summary['latest_snapshot']['id']}")
            print(f"  时间: {summary['latest_snapshot']['date']}")
            print(f"  批次: {summary['latest_snapshot']['batch_id']}")

        print(f"\n{'='*50}")

    def get_change_history(self, param_key: str = None, days: int = 90) -> List[Dict]:
        """查询变更历史"""
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        with self.dl._get_conn() as conn:
            if param_key:
                rows = conn.execute("""
                    SELECT id, batch_id, param_key, sandbox_value, current_value,
                           optimize_type, status, applied_at, rollback_triggered, rollback_reason
                    FROM sandbox_config
                    WHERE param_key=? AND staged_at >= ?
                    ORDER BY staged_at DESC
                """, (param_key, cutoff_date)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, batch_id, param_key, sandbox_value, current_value,
                           optimize_type, status, applied_at, rollback_triggered, rollback_reason
                    FROM sandbox_config
                    WHERE staged_at >= ?
                    ORDER BY staged_at DESC
                """, (cutoff_date,)).fetchall()

        return [
            {
                'id': r[0],
                'batch_id': r[1],
                'param_key': r[2],
                'sandbox_value': r[3],
                'current_value': r[4],
                'optimize_type': r[5],
                'status': r[6],
                'applied_at': r[7],
                'rollback_triggered': r[8],
                'rollback_reason': r[9],
            }
            for r in rows
        ]

    def get_batch_trace(self, batch_id: str) -> Dict:
        """追溯批次变更完整路径"""
        batch_info = self.get_batch_info(batch_id)

        if batch_info is None:
            return {'batch_id': batch_id, 'found': False}

        sandbox_status_map = self._get_batch_sandbox_status_map(batch_id)

        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT id, optimize_date, optimize_type, param_key, old_value, new_value,
                       trigger_reason, rollback_triggered, rollback_reason
                FROM optimization_history
                WHERE batch_id=?
                ORDER BY id
            """, (batch_id,)).fetchall()

        opt_history = [
            {
                'id': r[0],
                'optimize_date': r[1],
                'optimize_type': r[2],
                'param_key': r[3],
                'old_value': r[4],
                'new_value': r[5],
                'trigger_reason': r[6],
                'rollback_triggered': r[7],
                'rollback_reason': r[8],
                'sandbox_status': sandbox_status_map.get(r[3], 'unknown'),
            }
            for r in rows
        ]

        return {
            'batch_id': batch_id,
            'found': True,
            'snapshot': batch_info['snapshot'],
            'changes': batch_info['changes'],
            'optimization_history': opt_history,
            'total_changes': batch_info['total_changes'],
            'applied_count': batch_info['applied_count'],
            'rollback_count': batch_info['rollback_count'],
            'is_rolled_back': batch_info['is_rolled_back'],
        }

    def _get_batch_sandbox_status_map(self, batch_id: str) -> Dict[str, str]:
        """一次性获取批次所有参数的 sandbox 状态"""
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT param_key, status FROM sandbox_config WHERE batch_id=?
            """, (batch_id,)).fetchall()

        return {r[0]: r[1] for r in rows}

    def print_batch_trace(self, batch_id: str):
        """打印批次追溯信息"""
        trace = self.get_batch_trace(batch_id)

        if not trace.get('found'):
            print(f"\n[追溯] 批次 {batch_id} 未找到")
            return

        print(f"\n{'='*60}")
        print(f"[变更追溯] 批次: {batch_id}")
        print(f"{'='*60}")

        snapshot = trace.get('snapshot')
        if snapshot:
            print(f"\n[快照] ID={snapshot['id']} 时间={snapshot['snapshot_date']}")
            print(f"  触发原因: {snapshot['trigger_reason']}")

        changes = trace.get('changes', [])
        print(f"\n[变更] 共 {len(changes)} 项")
        for c in changes:
            status = c['status']
            rollback = '已回滚' if c['rollback_triggered'] else ''
            print(f"  {c['param_key']}: {c['current_value']} -> {c['sandbox_value']} [{status}] {rollback}")

        if trace['is_rolled_back']:
            print(f"\n[回滚] 已触发回滚")
            rolled_changes = [c for c in changes if c['rollback_triggered']]
            if rolled_changes:
                reason = rolled_changes[0].get('rollback_reason', '未知')
                print(f"  原因: {reason}")

        print(f"\n{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='变更管理模块')
    parser.add_argument('--mode', choices=['status', 'history', 'trace', 'monitor'], default='status')
    parser.add_argument('--batch-id', default=None, help='批次ID')
    parser.add_argument('--param-key', default=None, help='参数名')
    parser.add_argument('--days', type=int, default=90, help='回看天数')
    args = parser.parse_args()

    mgr = ChangeManager()

    if args.mode == 'status':
        mgr.print_status_summary()

    elif args.mode == 'history':
        history = mgr.get_change_history(args.param_key, args.days)
        print(f"\n[变更历史] 最近 {args.days} 天")
        for item in history:
            print(f"  {item['param_key']}: {item['current_value']} -> {item['sandbox_value']} [{item['status']}]")

    elif args.mode == 'trace':
        if args.batch_id:
            mgr.print_batch_trace(args.batch_id)
        else:
            print("请指定 --batch-id")

    elif args.mode == 'monitor':
        result = mgr.monitor_and_rollback()
        print(f"\n[主动回滚监控]")
        print(f"  检查: {result['checked']} 批次")
        print(f"  触发回滚: {result['rollback_triggered']} 批次")
        if result['details']:
            for d in result['details']:
                if d['should_rollback']:
                    print(f"  [回滚] {d['batch_id']}: {d['reason']}")