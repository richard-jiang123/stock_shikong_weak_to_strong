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