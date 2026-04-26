#!/usr/bin/env python3
"""测试变更管理模块"""
import unittest
import sys
import os
import tempfile
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from change_manager import ChangeManager
from strategy_config import StrategyConfig
from data_layer import StockDataLayer


class TestChangeManager(unittest.TestCase):
    """测试 ChangeManager 快照管理功能"""

    def setUp(self):
        """创建临时数据库"""
        self.temp_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self.temp_file.name
        self.temp_file.close()

        # 初始化数据层和表
        self.dl = StockDataLayer(self.db_path)
        self.dl._create_adaptive_tables()

        # 初始化 strategy_config 表
        self.cfg = StrategyConfig(self.db_path)
        self.cfg.init_if_empty()

        # 初始化 signal_status 表（添加测试数据）
        self._init_signal_status()

        # 初始化 pick_tracking 表（用于回滚监控测试）
        self._init_pick_tracking()

        # 创建 ChangeManager 实例
        self.mgr = ChangeManager(self.db_path)

    def tearDown(self):
        """清理临时数据库"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    def _init_signal_status(self):
        """初始化 signal_status 表的测试数据"""
        with sqlite3.connect(self.db_path) as conn:
            # 插入测试信号数据
            signals = [
                ('anomaly_no_decline', '异动不跌', 'active', 1.0),
                ('bullish_engulfing', '阳包阴', 'active', 1.0),
                ('big_bullish_reversal', '弱转强', 'active', 1.0),
                ('limit_up_open_next_strong', '涨停高开', 'watching', 0.5),
            ]
            for signal in signals:
                conn.execute("""
                    INSERT OR REPLACE INTO signal_status
                    (signal_type, display_name, status_level, weight_multiplier)
                    VALUES (?, ?, ?, ?)
                """, signal)

    def _init_pick_tracking(self):
        """初始化 pick_tracking 表（回滚监控测试需要）"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pick_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pick_date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    score REAL,
                    wave_gain REAL,
                    cons_dd REAL,
                    vol_ratio REAL,
                    entry_price REAL,
                    stop_loss REAL,
                    cons_low REAL,
                    market_regime TEXT,
                    index_code TEXT,
                    name TEXT,
                    status TEXT DEFAULT 'active',
                    exit_date TEXT,
                    exit_price REAL,
                    exit_reason TEXT,
                    hold_days INTEGER,
                    max_price REAL,
                    min_price REAL,
                    final_pnl_pct REAL,
                    max_pnl_pct REAL,
                    max_dd_pct REAL,
                    score_wave_gain REAL,
                    score_shallow_dd REAL,
                    score_day_gain REAL,
                    score_volume REAL,
                    score_ma_bull REAL,
                    score_sector REAL,
                    score_signal_bonus REAL,
                    score_base REAL DEFAULT 5,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(pick_date, code)
                )
            """)
            conn.execute('CREATE INDEX IF NOT EXISTS idx_pick_date ON pick_tracking(pick_date)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_pick_status ON pick_tracking(status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_pick_exit_date ON pick_tracking(exit_date)')

    def test_save_snapshot(self):
        """测试：保存快照并验证可以检索"""
        # 保存快照
        snapshot_id = self.mgr.save_snapshot(
            trigger_reason='weekly_optimize',
            batch_id='batch_20260426_001',
            snapshot_type='pre_change'
        )

        # 验证返回了有效的 snapshot_id
        self.assertIsInstance(snapshot_id, int)
        self.assertGreater(snapshot_id, 0)

        # 通过 get_snapshot_by_id 验证快照内容
        snapshot = self.mgr.get_snapshot_by_id(snapshot_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot['trigger_reason'], 'weekly_optimize')
        self.assertEqual(snapshot['batch_id'], 'batch_20260426_001')
        self.assertEqual(snapshot['snapshot_type'], 'pre_change')
        self.assertIsNotNone(snapshot['params'])
        self.assertIsNotNone(snapshot['signal_status'])
        self.assertIsNotNone(snapshot['environment'])

        # 验证 params 包含预期参数
        params = snapshot['params']
        self.assertIn('first_wave_min_days', params)
        self.assertIn('stop_loss_buffer', params)

        # 验证 signal_status 包含信号状态
        signal_status = snapshot['signal_status']
        self.assertEqual(len(signal_status), 4)
        signal_types = [s['signal_type'] for s in signal_status]
        self.assertIn('anomaly_no_decline', signal_types)

    def test_restore_snapshot(self):
        """测试：从快照恢复参数"""
        # 1. 保存初始快照
        snapshot_id = self.mgr.save_snapshot(
            trigger_reason='manual',
            batch_id='restore_test_001',
            snapshot_type='pre_change'
        )

        # 2. 修改参数（模拟变更）
        self.cfg.set('first_wave_min_days', 10)  # 改为 10
        self.cfg.set('stop_loss_buffer', 0.05)   # 改为 0.05

        # 修改 signal_status
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE signal_status SET status_level = 'disabled' WHERE signal_type = 'anomaly_no_decline'
            """)

        # 3. 验证参数已变更
        self.assertEqual(self.cfg.get('first_wave_min_days'), 10)
        self.assertEqual(self.cfg.get('stop_loss_buffer'), 0.05)

        # 4. 从快照恢复
        result = self.mgr.restore_snapshot(snapshot_id, reason='test_restore')
        self.assertTrue(result)

        # 5. 创建新的 StrategyConfig 实例读取数据库，验证恢复
        cfg_verify = StrategyConfig(self.db_path)
        self.assertEqual(cfg_verify.get('first_wave_min_days'), 3)  # 恢复为默认值 3
        self.assertEqual(cfg_verify.get('stop_loss_buffer'), 0.02)   # 恢复为默认值 0.02

        # 6. 验证 signal_status 恢复
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status_level FROM signal_status WHERE signal_type = 'anomaly_no_decline'
            """).fetchone()
            self.assertEqual(row[0], 'active')

        # 7. 验证快照标记为已恢复
        snapshot = self.mgr.get_snapshot_by_id(snapshot_id)
        self.assertEqual(snapshot['is_restored'], 1)
        self.assertEqual(snapshot['restore_reason'], 'test_restore')

    def test_get_latest_snapshot(self):
        """测试：获取最新快照"""
        # 保存多个快照
        id1 = self.mgr.save_snapshot('manual', 'batch_001', 'pre_change')
        id2 = self.mgr.save_snapshot('manual', 'batch_002', 'pre_change')
        id3 = self.mgr.save_snapshot('manual', 'batch_001', 'daily_backup')

        # 获取最新 pre_change 快照
        latest = self.mgr.get_latest_snapshot()
        self.assertIsNotNone(latest)
        self.assertEqual(latest['id'], id2)  # id2 是最新的 pre_change

        # 获取特定批次的最新快照
        batch_latest = self.mgr.get_latest_snapshot(batch_id='batch_001')
        self.assertIsNotNone(batch_latest)
        self.assertEqual(batch_latest['id'], id3)  # id3 是 batch_001 的最新快照

    def test_get_snapshot_by_id_not_found(self):
        """测试：获取不存在的快照返回 None"""
        snapshot = self.mgr.get_snapshot_by_id(99999)
        self.assertIsNone(snapshot)

    def test_get_latest_snapshot_empty_db(self):
        """测试：空数据库获取最新快照返回 None"""
        # 使用新的临时数据库
        temp_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        empty_db_path = temp_file.name
        temp_file.close()

        try:
            mgr = ChangeManager(empty_db_path)
            result = mgr.get_latest_snapshot()
            self.assertIsNone(result)
        finally:
            os.unlink(empty_db_path)

    def test_restore_snapshot_already_restored(self):
        """测试：已恢复的快照不能再次恢复"""
        # 1. 保存快照
        snapshot_id = self.mgr.save_snapshot(
            trigger_reason='manual',
            snapshot_type='pre_change'
        )

        # 2. 第一次恢复应该成功
        result1 = self.mgr.restore_snapshot(snapshot_id, reason='first_restore')
        self.assertTrue(result1)

        # 3. 第二次恢复应该失败（已标记为已恢复）
        result2 = self.mgr.restore_snapshot(snapshot_id, reason='second_restore')
        self.assertFalse(result2)

    def test_get_latest_snapshot_filters_restored(self):
        """测试：get_latest_snapshot 过滤已恢复的快照"""
        # 1. 保存多个快照
        id1 = self.mgr.save_snapshot('manual', None, 'pre_change')
        id2 = self.mgr.save_snapshot('manual', None, 'pre_change')

        # 2. 恢复 id2（最新的快照）
        self.mgr.restore_snapshot(id2, reason='test')

        # 3. get_latest_snapshot 应该返回 id1（因为 id2 已被标记为已恢复）
        latest = self.mgr.get_latest_snapshot()
        self.assertIsNotNone(latest)
        self.assertEqual(latest['id'], id1)

    # ─────────────────────────────────────────────
    # 参数隔离测试
    # ─────────────────────────────────────────────

    def test_stage_change_strategy_config(self):
        """测试：暂存 strategy_config 参数变更"""
        batch_id = 'batch_20260426_test_001'

        # 暂存变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=5,
            batch_id=batch_id
        )

        # 验证返回了有效的 sandbox_id
        self.assertIsInstance(sandbox_id, int)
        self.assertGreater(sandbox_id, 0)

        # 通过 get_staged_params 验证暂存内容
        params = self.mgr.get_staged_params(batch_id)
        self.assertEqual(len(params), 1)

        param = params[0]
        self.assertEqual(param['param_key'], 'first_wave_min_days')
        self.assertEqual(param['sandbox_value'], 5.0)  # 转换为 float
        self.assertEqual(param['current_value'], 3)    # 默认值
        self.assertEqual(param['optimize_type'], 'strategy_config')
        self.assertEqual(param['status'], 'staged')

        # 验证实际配置未被修改
        self.assertEqual(self.cfg.get('first_wave_min_days'), 3)

    def test_stage_change_signal_status(self):
        """测试：暂存 signal_status 参数变更"""
        batch_id = 'batch_20260426_test_002'

        # 暂存变更：将 anomaly_no_decline 的状态改为 watching
        sandbox_id = self.mgr.stage_change(
            optimize_type='signal_status',
            param_key='anomaly_no_decline',
            new_value='watching',
            batch_id=batch_id
        )

        self.assertGreater(sandbox_id, 0)

        # 验证暂存内容
        params = self.mgr.get_staged_params(batch_id)
        self.assertEqual(len(params), 1)

        param = params[0]
        self.assertEqual(param['param_key'], 'anomaly_no_decline')
        self.assertEqual(param['sandbox_value'], 'watching')
        self.assertEqual(param['current_value'], 'active')  # 初始状态
        self.assertEqual(param['optimize_type'], 'signal_status')

        # 验证实际配置未被修改
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status_level FROM signal_status WHERE signal_type = 'anomaly_no_decline'
            """).fetchone()
            self.assertEqual(row[0], 'active')

    def test_get_staged_params_filters_by_status(self):
        """测试：get_staged_params 过滤状态"""
        batch_id = 'batch_20260426_test_003'

        # 暂存变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=7,
            batch_id=batch_id
        )

        # 验证可以获取
        params = self.mgr.get_staged_params(batch_id)
        self.assertEqual(len(params), 1)

        # 更新状态为 applied
        self.mgr.update_status(sandbox_id, 'applied')

        # applied 不在过滤范围内，应该获取不到
        params = self.mgr.get_staged_params(batch_id)
        self.assertEqual(len(params), 0)

    def test_get_all_staged_batches(self):
        """测试：获取所有暂存批次"""
        # 创建两个批次
        self.mgr.stage_change('strategy_config', 'first_wave_min_days', 5, 'batch_001')
        self.mgr.stage_change('strategy_config', 'stop_loss_buffer', 0.03, 'batch_001')
        self.mgr.stage_change('signal_status', 'anomaly_no_decline', 'watching', 'batch_002')

        batches = self.mgr.get_all_staged_batches()

        # 验证返回结果
        self.assertEqual(len(batches), 2)

        batch_ids = [b['batch_id'] for b in batches]
        self.assertIn('batch_001', batch_ids)
        self.assertIn('batch_002', batch_ids)

        # 验证变更计数
        batch_001 = next(b for b in batches if b['batch_id'] == 'batch_001')
        self.assertEqual(batch_001['change_count'], 2)

        batch_002 = next(b for b in batches if b['batch_id'] == 'batch_002')
        self.assertEqual(batch_002['change_count'], 1)

    def test_update_status(self):
        """测试：更新沙盒状态"""
        batch_id = 'batch_test_status'

        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=10,
            batch_id=batch_id
        )

        # 测试 validating
        self.mgr.update_status(sandbox_id, 'validating')
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status, validation_started_at FROM sandbox_config WHERE id = ?
            """, (sandbox_id,)).fetchone()
            self.assertEqual(row[0], 'validating')
            self.assertIsNotNone(row[1])

        # 测试 passed
        self.mgr.update_status(sandbox_id, 'passed')
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status, validated_at FROM sandbox_config WHERE id = ?
            """, (sandbox_id,)).fetchone()
            self.assertEqual(row[0], 'passed')
            self.assertIsNotNone(row[1])

        # 测试 rejected
        self.mgr.update_status(sandbox_id, 'rejected', reason='test_reject')
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status, rejected_at, rejection_reason FROM sandbox_config WHERE id = ?
            """, (sandbox_id,)).fetchone()
            self.assertEqual(row[0], 'rejected')
            self.assertIsNotNone(row[1])
            self.assertEqual(row[2], 'test_reject')

    def test_commit_change_strategy_config(self):
        """测试：提交 strategy_config 变更"""
        batch_id = 'batch_commit_001'

        # 暂存变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=8,
            batch_id=batch_id
        )

        # 验证暂存前配置未变
        self.assertEqual(self.cfg.get('first_wave_min_days'), 3)

        # 提交变更
        result = self.mgr.commit_change(sandbox_id)
        self.assertTrue(result)

        # 验证配置已更新
        cfg_verify = StrategyConfig(self.db_path)
        self.assertEqual(cfg_verify.get('first_wave_min_days'), 8.0)

        # 验证沙盒状态已更新为 applied
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status, applied_at FROM sandbox_config WHERE id = ?
            """, (sandbox_id,)).fetchone()
            self.assertEqual(row[0], 'applied')
            self.assertIsNotNone(row[1])

    def test_commit_change_signal_status(self):
        """测试：提交 signal_status 变更"""
        batch_id = 'batch_commit_002'

        # 暂存变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='signal_status',
            param_key='anomaly_no_decline',
            new_value='watching',
            batch_id=batch_id
        )

        # 验证暂存前状态未变
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status_level, weight_multiplier FROM signal_status
                WHERE signal_type = 'anomaly_no_decline'
            """).fetchone()
            self.assertEqual(row[0], 'active')
            self.assertEqual(row[1], 1.0)

        # 提交变更
        result = self.mgr.commit_change(sandbox_id)
        self.assertTrue(result)

        # 验证状态和权重已更新
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status_level, weight_multiplier FROM signal_status
                WHERE signal_type = 'anomaly_no_decline'
            """).fetchone()
            self.assertEqual(row[0], 'watching')
            self.assertEqual(row[1], 0.5)  # watching 对应 0.5 权重

    def test_reject_change(self):
        """测试：拒绝变更"""
        batch_id = 'batch_reject_001'

        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=99,
            batch_id=batch_id
        )

        # 拒绝变更
        result = self.mgr.reject_change(sandbox_id, reason='invalid_value')
        self.assertTrue(result)

        # 验证状态已更新
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status, rejection_reason FROM sandbox_config WHERE id = ?
            """, (sandbox_id,)).fetchone()
            self.assertEqual(row[0], 'rejected')
            self.assertEqual(row[1], 'invalid_value')

        # 验证配置未变
        self.assertEqual(self.cfg.get('first_wave_min_days'), 3)

    def test_stage_change_update_existing(self):
        """测试：暂存已存在的参数会更新"""
        batch_id = 'batch_update_001'

        # 第一次暂存
        sandbox_id1 = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=5,
            batch_id=batch_id
        )

        # 第二次暂存相同参数
        sandbox_id2 = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=7,
            batch_id=batch_id
        )

        # 应该是同一个 sandbox_id（更新而非新增）
        self.assertEqual(sandbox_id1, sandbox_id2)

        # 验证只有一条记录，且值已更新
        params = self.mgr.get_staged_params(batch_id)
        self.assertEqual(len(params), 1)
        self.assertEqual(params[0]['sandbox_value'], 7.0)

    def test_commit_change_nonexistent_id(self):
        """测试：提交不存在的 sandbox_id 返回 False"""
        result = self.mgr.commit_change(99999)
        self.assertFalse(result)

    def test_commit_change_invalid_status(self):
        """测试：提交 rejected 状态的变更返回 False"""
        batch_id = 'batch_invalid_status_001'

        # 暂存变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=10,
            batch_id=batch_id
        )

        # 拒绝变更
        self.mgr.reject_change(sandbox_id, reason='test_reject')

        # 验证状态为 rejected
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status FROM sandbox_config WHERE id = ?
            """, (sandbox_id,)).fetchone()
            self.assertEqual(row[0], 'rejected')

        # 尝试提交应该失败
        result = self.mgr.commit_change(sandbox_id)
        self.assertFalse(result)

        # 验证配置未变
        self.assertEqual(self.cfg.get('first_wave_min_days'), 3)

    # ─────────────────────────────────────────────
    # 批量回滚测试
    # ─────────────────────────────────────────────

    def test_rollback_batch(self):
        """测试：批量回滚批次变更"""
        batch_id = 'batch_rollback_001'

        # 1. 设置初始参数值
        self.cfg.set('first_wave_min_days', 3)
        self.cfg.set('stop_loss_buffer', 0.02)

        # 2. 保存快照（参数值为 3 和 0.02）
        snapshot_id = self.mgr.save_snapshot(
            trigger_reason='weekly_optimize',
            batch_id=batch_id,
            snapshot_type='pre_change'
        )

        # 3. 暂存变更到 0.18
        sandbox_id_1 = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=5,
            batch_id=batch_id
        )
        sandbox_id_2 = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='stop_loss_buffer',
            new_value=0.18,
            batch_id=batch_id
        )

        # 4. 提交变更
        self.mgr.commit_change(sandbox_id_1)
        self.mgr.commit_change(sandbox_id_2)

        # 验证参数已更新
        cfg_before = StrategyConfig(self.db_path)
        self.assertEqual(cfg_before.get('first_wave_min_days'), 5.0)
        self.assertEqual(cfg_before.get('stop_loss_buffer'), 0.18)

        # 5. 执行回滚
        result = self.mgr.rollback_batch(batch_id, reason='performance_degradation')

        # 6. 验证回滚结果
        self.assertGreaterEqual(result['rolled_back'], 2)  # 至少回滚了 2 条变更
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['snapshot_id'], snapshot_id)
        self.assertEqual(result['reason'], 'performance_degradation')

        # 7. 验证参数已恢复到快照时的值
        cfg_verify = StrategyConfig(self.db_path)
        self.assertEqual(cfg_verify.get('first_wave_min_days'), 3)
        self.assertEqual(cfg_verify.get('stop_loss_buffer'), 0.02)

        # 8. 验证 sandbox_config 状态已更新为 rejected
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT status, rollback_triggered, rollback_reason
                FROM sandbox_config
                WHERE batch_id = ?
            """, (batch_id,)).fetchall()

            for row in rows:
                self.assertEqual(row[0], 'rejected')
                self.assertEqual(row[1], 1)
                self.assertEqual(row[2], 'performance_degradation')

    def test_rollback_batch_no_snapshot(self):
        """测试：回滚不存在的批次返回错误信息"""
        result = self.mgr.rollback_batch('nonexistent_batch', reason='test')

        self.assertEqual(result['rolled_back'], 0)
        self.assertEqual(result['failed'], 0)
        self.assertIsNone(result['snapshot_id'])
        self.assertEqual(result['reason'], 'no_snapshot_found')

    def test_rollback_batch_partial_applied(self):
        """测试：回滚混合状态的批次（staged/applied混合）"""
        batch_id = 'batch_partial_rollback_001'

        # 1. 设置初始参数值
        self.cfg.set('first_wave_min_days', 3)
        self.cfg.set('stop_loss_buffer', 0.02)

        # 2. 保存快照
        snapshot_id = self.mgr.save_snapshot(
            trigger_reason='weekly_optimize',
            batch_id=batch_id,
            snapshot_type='pre_change'
        )

        # 3. 暂存多个变更
        sandbox_id_1 = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=5,
            batch_id=batch_id
        )
        sandbox_id_2 = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='stop_loss_buffer',
            new_value=0.05,
            batch_id=batch_id
        )
        sandbox_id_3 = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='max_hold_days',
            new_value=10,
            batch_id=batch_id
        )

        # 4. 只提交部分变更（sandbox_id_1 和 sandbox_id_2）
        self.mgr.commit_change(sandbox_id_1)
        self.mgr.commit_change(sandbox_id_2)

        # sandbox_id_3 仍为 staged 状态
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status FROM sandbox_config WHERE id = ?
            """, (sandbox_id_3,)).fetchone()
            self.assertEqual(row[0], 'staged')

        # 5. 验证已提交的参数已更新
        cfg_before = StrategyConfig(self.db_path)
        self.assertEqual(cfg_before.get('first_wave_min_days'), 5.0)
        self.assertEqual(cfg_before.get('stop_loss_buffer'), 0.05)

        # 6. 执行回滚
        result = self.mgr.rollback_batch(batch_id, reason='test_partial_rollback')

        # 7. 验证回滚结果：应该回滚所有 3 条记录（包括 staged 状态的）
        self.assertEqual(result['rolled_back'], 3)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['snapshot_id'], snapshot_id)

        # 8. 验证参数已恢复到快照时的值
        cfg_verify = StrategyConfig(self.db_path)
        self.assertEqual(cfg_verify.get('first_wave_min_days'), 3)
        self.assertEqual(cfg_verify.get('stop_loss_buffer'), 0.02)

        # 9. 验证所有 sandbox_config 记录状态已更新为 rejected
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT status, rollback_triggered FROM sandbox_config
                WHERE batch_id = ?
            """, (batch_id,)).fetchall()

            for row in rows:
                self.assertEqual(row[0], 'rejected')
                self.assertEqual(row[1], 1)

    def test_rollback_batch_validating_status(self):
        """测试：回滚 validating 状态的记录"""
        batch_id = 'batch_validating_rollback_001'

        # 1. 保存快照
        snapshot_id = self.mgr.save_snapshot(
            trigger_reason='weekly_optimize',
            batch_id=batch_id,
            snapshot_type='pre_change'
        )

        # 2. 暂存变更并设置为 validating 状态
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=7,
            batch_id=batch_id
        )
        self.mgr.update_status(sandbox_id, 'validating')

        # 验证状态为 validating
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status FROM sandbox_config WHERE id = ?
            """, (sandbox_id,)).fetchone()
            self.assertEqual(row[0], 'validating')

        # 3. 执行回滚
        result = self.mgr.rollback_batch(batch_id, reason='validation_failed')

        # 4. 验证回滚结果
        self.assertEqual(result['rolled_back'], 1)
        self.assertEqual(result['snapshot_id'], snapshot_id)

        # 5. 验证 sandbox_config 状态已更新为 rejected
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status, rollback_triggered FROM sandbox_config
                WHERE batch_id = ?
            """, (batch_id,)).fetchone()
            self.assertEqual(row[0], 'rejected')
            self.assertEqual(row[1], 1)

    def test_get_batch_changes(self):
        """测试：获取批次变更记录"""
        batch_id = 'batch_changes_001'

        # 暂存多个变更
        self.mgr.stage_change('strategy_config', 'first_wave_min_days', 5, batch_id)
        self.mgr.stage_change('strategy_config', 'stop_loss_buffer', 0.03, batch_id)

        # 获取变更记录
        changes = self.mgr.get_batch_changes(batch_id)

        self.assertEqual(len(changes), 2)

        param_keys = [c['param_key'] for c in changes]
        self.assertIn('first_wave_min_days', param_keys)
        self.assertIn('stop_loss_buffer', param_keys)

        # 验证变更记录字段
        for change in changes:
            self.assertIn('id', change)
            self.assertIn('param_key', change)
            self.assertIn('sandbox_value', change)
            self.assertIn('current_value', change)
            self.assertIn('optimize_type', change)
            self.assertIn('status', change)
            self.assertIn('staged_at', change)
            self.assertIn('applied_at', change)
            self.assertIn('rollback_triggered', change)
            self.assertIn('rollback_at', change)

    def test_get_batch_info(self):
        """测试：获取批次详细信息"""
        batch_id = 'batch_info_001'

        # 保存快照
        self.mgr.save_snapshot('weekly_optimize', batch_id, 'pre_change')

        # 暂存并提交变更
        sandbox_id_1 = self.mgr.stage_change('strategy_config', 'first_wave_min_days', 5, batch_id)
        sandbox_id_2 = self.mgr.stage_change('strategy_config', 'stop_loss_buffer', 0.03, batch_id)

        self.mgr.commit_change(sandbox_id_1)
        self.mgr.commit_change(sandbox_id_2)

        # 获取批次信息
        info = self.mgr.get_batch_info(batch_id)

        self.assertIsNotNone(info)
        self.assertEqual(info['batch_id'], batch_id)
        self.assertEqual(info['total_changes'], 2)
        self.assertEqual(info['applied_count'], 2)
        self.assertEqual(info['rollback_count'], 0)
        self.assertFalse(info['is_rolled_back'])
        self.assertIsNotNone(info['snapshot'])

        # 执行回滚
        self.mgr.rollback_batch(batch_id, reason='test_rollback')

        # 再次获取批次信息
        info_after = self.mgr.get_batch_info(batch_id)
        self.assertTrue(info_after['is_rolled_back'])
        self.assertGreater(info_after['rollback_count'], 0)

    def test_get_batch_info_nonexistent(self):
        """测试：获取不存在批次的信息返回 None"""
        info = self.mgr.get_batch_info('nonexistent_batch')
        self.assertIsNone(info)

    # ─────────────────────────────────────────────
    # 主动回滚监控测试（Layer A）
    # ─────────────────────────────────────────────

    def test_monitor_and_rollback_empty(self):
        """测试：没有批次需要监控"""
        result = self.mgr.monitor_and_rollback()

        self.assertEqual(result['checked'], 0)
        self.assertEqual(result['rollback_triggered'], 0)
        self.assertEqual(len(result['details']), 0)

    def test_get_applied_batches_in_monitor_window(self):
        """测试：获取监控窗口内的已应用批次"""
        batch_id = 'batch_monitor_001'

        # 保存快照
        self.mgr.save_snapshot('weekly_optimize', batch_id, 'pre_change')

        # 暂存并提交变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=5,
            batch_id=batch_id
        )
        self.mgr.commit_change(sandbox_id)

        # 获取监控窗口内的批次
        batches = self.mgr.get_applied_batches_in_monitor_window()

        # 验证结果
        self.assertGreaterEqual(len(batches), 1)
        batch_ids = [b['batch_id'] for b in batches]
        self.assertIn(batch_id, batch_ids)

        # 验证批次信息字段
        target_batch = next(b for b in batches if b['batch_id'] == batch_id)
        self.assertIn('applied_at', target_batch)
        self.assertIn('monitor_days_elapsed', target_batch)

    def test_check_performance_degradation_sample_insufficient(self):
        """测试：样本数不足时不触发回滚"""
        batch_id = 'batch_degradation_001'

        # 保存快照
        self.mgr.save_snapshot('weekly_optimize', batch_id, 'pre_change')

        # 暂存并提交变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=5,
            batch_id=batch_id
        )
        self.mgr.commit_change(sandbox_id)

        # 获取批次信息
        batches = self.mgr.get_applied_batches_in_monitor_window()
        target_batch = next(b for b in batches if b['batch_id'] == batch_id)

        # 检查性能退化（没有 pick_tracking 数据，样本数不足）
        degradation = self.mgr.check_performance_degradation(target_batch)

        # 样本数不足，不应触发回滚
        self.assertFalse(degradation['should_rollback'])
        self.assertEqual(degradation['reason'], 'sample_insufficient')

    def test_calc_metrics_in_range(self):
        """测试：计算时间范围内的指标"""
        # 使用当前日期附近的日期进行测试
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
        three_days_ago = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')

        with sqlite3.connect(self.db_path) as conn:
            # 插入已退出的交易数据，exit_date 控制在测试范围内
            # 只插入 yesterday 和 today 的数据（共2条）
            # final_pnl_pct 存储格式：小数形式（0.038 表示 3.8%）
            test_data = [
                (three_days_ago, 'sh600001', 'anomaly_no_decline', 'exited', two_days_ago, 0.025),  # 不在范围内
                (two_days_ago, 'sh600003', 'big_bullish_reversal', 'exited', yesterday, 0.038),     # 在范围内（3.8%）
                (two_days_ago, 'sh600004', 'limit_up_open_next_strong', 'exited', today, 0.005),    # 在范围内（0.5%）
            ]
            for data in test_data:
                conn.execute("""
                    INSERT OR REPLACE INTO pick_tracking
                    (pick_date, code, signal_type, status, exit_date, final_pnl_pct)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, data)

        # 计算指标 - 查询 yesterday 和 today 的数据（2条）
        metrics = self.mgr._calc_metrics_in_range(yesterday, today)

        # 验证结果
        self.assertEqual(metrics['sample_count'], 2)  # 2条数据在范围内
        # 期望值: (3.8 + 0.5) / 2 = 2.15
        self.assertAlmostEqual(metrics['expectancy'], 2.15, places=2)
        # 胜率: 2盈利 / 2 = 100%
        self.assertAlmostEqual(metrics['win_rate'], 100.0, places=1)

    def test_check_consecutive_bad_trading_days_empty(self):
        """测试：没有数据时连续负期望值天数为0"""
        batch_id = 'batch_consecutive_001'

        # 保存快照
        self.mgr.save_snapshot('weekly_optimize', batch_id, 'pre_change')

        # 暂存并提交变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=5,
            batch_id=batch_id
        )
        self.mgr.commit_change(sandbox_id)

        # 获取批次信息
        batches = self.mgr.get_applied_batches_in_monitor_window()
        target_batch = next(b for b in batches if b['batch_id'] == batch_id)

        # 检查连续负期望值天数（没有数据）
        consecutive_bad = self.mgr._check_consecutive_bad_trading_days(target_batch['applied_at'])

        # 没有数据，应为0
        self.assertEqual(consecutive_bad, 0)

    def test_check_performance_degradation_triggers_rollback(self):
        """测试：退化超过阈值触发回滚"""
        batch_id = 'batch_degradation_trigger_001'

        # 模拟批次是5天前应用的场景
        five_days_ago_ts = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S')
        five_days_ago = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        thirty_days_ago = (datetime.now() - timedelta(days=35)).strftime('%Y-%m-%d')  # 基线范围起点
        today = datetime.now().strftime('%Y-%m-%d')

        # 先插入基线数据（positive pnl），在 [35天前, 5天前] 范围内
        # 这些数据将在 baseline 范围 [30天前snapshot, snapshot] 内
        # final_pnl_pct 存储格式：小数形式（0.042 表示 4.2%）
        with sqlite3.connect(self.db_path) as conn:
            # 插入10条正 pnl 数据作为基线
            for i in range(10):
                conn.execute("""
                    INSERT INTO pick_tracking
                    (pick_date, code, signal_type, status, exit_date, final_pnl_pct)
                    VALUES (?, ?, ?, 'exited', ?, 0.042)
                """, (thirty_days_ago, f'sh9000{i}', 'anomaly_no_decline', five_days_ago))

        # 保存快照（snapshot_date 将被设置为模拟时间）
        snapshot_id = self.mgr.save_snapshot('weekly_optimize', batch_id, 'pre_change')

        # 暂存并提交变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=5,
            batch_id=batch_id
        )
        self.mgr.commit_change(sandbox_id)

        # 更新 applied_at 和 snapshot_date 到5天前
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE sandbox_config SET applied_at = ? WHERE batch_id = ?
            """, (five_days_ago_ts, batch_id))
            conn.execute("""
                UPDATE param_snapshot SET snapshot_date = ? WHERE id = ?
            """, (five_days_ago_ts, snapshot_id))

            # 插入当前数据（negative pnl），在 [5天前, 今天] 范围内
            # final_pnl_pct 存储格式：小数形式（-0.05 表示 -5%）
            for i in range(15):
                conn.execute("""
                    INSERT INTO pick_tracking
                    (pick_date, code, signal_type, status, exit_date, final_pnl_pct)
                    VALUES (?, ?, ?, 'exited', ?, -0.05)
                """, (five_days_ago, f'sh6000{i}', 'anomaly_no_decline', today))

        # 获取批次信息
        batches = self.mgr.get_applied_batches_in_monitor_window()
        target_batch = next(b for b in batches if b['batch_id'] == batch_id)

        # 检查性能退化
        degradation = self.mgr.check_performance_degradation(target_batch)

        # 验证触发回滚
        # baseline: expectancy=4.2, win_rate=100%, sample_count=10
        # current: expectancy=-5.0, win_rate=0, sample_count=15
        # expectancy_drop = (4.2 - (-5.0)) / 4.2 ≈ 2.19 > 0.3
        # win_rate_drop = 100 - 0 = 100 > 10
        self.assertTrue(degradation['should_rollback'])
        self.assertIn('expectancy_drop', degradation['reason'])
        self.assertGreater(degradation['expectancy_drop'], 0.3)  # 下降超过30%

    def test_check_consecutive_bad_trading_days_with_data(self):
        """测试：有数据时连续负期望值天数计算"""
        batch_id = 'batch_consecutive_with_data_001'

        # 保存快照
        self.mgr.save_snapshot('weekly_optimize', batch_id, 'pre_change')

        # 暂存并提交变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=5,
            batch_id=batch_id
        )
        self.mgr.commit_change(sandbox_id)

        # 模拟批次是5天前应用的（手动更新 applied_at）
        five_days_ago_ts = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S')
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE sandbox_config SET applied_at = ? WHERE batch_id = ?
            """, (five_days_ago_ts, batch_id))

        # 获取批次信息（此时 applied_at 已更新为5天前）
        batches = self.mgr.get_applied_batches_in_monitor_window()
        target_batch = next(b for b in batches if b['batch_id'] == batch_id)

        # 插入多个日期的负 pnl 数据（连续5天负期望值）
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
        three_days_ago = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
        four_days_ago = (datetime.now() - timedelta(days=4)).strftime('%Y-%m-%d')
        five_days_ago = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')

        with sqlite3.connect(self.db_path) as conn:
            # 连续5天负 pnl（每天2条），exit_date 从今天往前5天
            # final_pnl_pct 存储格式：小数形式（-0.02 表示 -2%）
            for day in [today, yesterday, two_days_ago, three_days_ago, four_days_ago]:
                for i in range(2):
                    conn.execute("""
                        INSERT INTO pick_tracking
                        (pick_date, code, signal_type, status, exit_date, final_pnl_pct)
                        VALUES (?, ?, ?, 'exited', ?, -0.02)
                    """, (five_days_ago, f'sh600{day.replace("-", "")}{i}', 'anomaly_no_decline', day))

            # 第6天插入正 pnl（中断连续负期望值）
            # final_pnl_pct 存储格式：小数形式（0.05 表示 5%）
            conn.execute("""
                INSERT INTO pick_tracking
                (pick_date, code, signal_type, status, exit_date, final_pnl_pct)
                VALUES (?, ?, ?, 'exited', ?, 0.05)
            """, (five_days_ago, 'sh60000100', 'anomaly_no_decline', five_days_ago))

        # 检查连续负期望值天数
        consecutive_bad = self.mgr._check_consecutive_bad_trading_days(target_batch['applied_at'])

        # 验证连续5天负期望值（从今天往前5天，直到遇到第6天的正 pnl）
        self.assertEqual(consecutive_bad, 5)

    def test_log_rollback(self):
        """测试：_log_rollback 写入 daily_monitor_log"""
        batch_id = 'batch_log_rollback_001'

        # 构造 batch 和 degradation 信息
        batch = {
            'batch_id': batch_id,
            'applied_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'monitor_days_elapsed': 5
        }

        degradation = {
            'should_rollback': True,
            'reason': 'expectancy_drop_45.0%; win_rate_drop_15.0%',
            'expectancy_drop': 0.45,
            'win_rate_drop': 15.0
        }

        # 调用 _log_rollback
        result = self.mgr._log_rollback(batch, degradation)

        # 验证写入成功
        self.assertTrue(result)

        # 验证 daily_monitor_log 表有记录
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT monitor_date, alert_type, alert_detail, severity, action_taken
                FROM daily_monitor_log
                WHERE alert_type = 'auto_rollback'
                ORDER BY id DESC LIMIT 1
            """).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row[1], 'auto_rollback')
            self.assertEqual(row[3], 'critical')
            self.assertEqual(row[4], 'rollback_completed')
            self.assertIn(batch_id, row[2])
            self.assertIn('expectancy_drop', row[2])

    def test_monitor_and_rollback_triggers(self):
        """测试：monitor_and_rollback 检测到退化并触发回滚"""
        batch_id = 'batch_monitor_trigger_001'

        # 模拟批次是5天前应用的场景
        five_days_ago_ts = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S')
        five_days_ago = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        thirty_days_ago = (datetime.now() - timedelta(days=35)).strftime('%Y-%m-%d')  # 基线范围起点
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
        three_days_ago = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
        four_days_ago = (datetime.now() - timedelta(days=4)).strftime('%Y-%m-%d')

        # 先插入基线数据（positive pnl）
        with sqlite3.connect(self.db_path) as conn:
            for i in range(10):
                conn.execute("""
                    INSERT INTO pick_tracking
                    (pick_date, code, signal_type, status, exit_date, final_pnl_pct)
                    VALUES (?, ?, ?, 'exited', ?, 0.042)
                """, (thirty_days_ago, f'sh9000{i}', 'anomaly_no_decline', five_days_ago))

        # 保存快照
        snapshot_id = self.mgr.save_snapshot('weekly_optimize', batch_id, 'pre_change')

        # 暂存并提交变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=5,
            batch_id=batch_id
        )
        self.mgr.commit_change(sandbox_id)

        # 更新 applied_at 和 snapshot_date
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE sandbox_config SET applied_at = ? WHERE batch_id = ?
            """, (five_days_ago_ts, batch_id))
            conn.execute("""
                UPDATE param_snapshot SET snapshot_date = ? WHERE id = ?
            """, (five_days_ago_ts, snapshot_id))

            # 插入当前数据（negative pnl）
            for i in range(15):
                conn.execute("""
                    INSERT INTO pick_tracking
                    (pick_date, code, signal_type, status, exit_date, final_pnl_pct)
                    VALUES (?, ?, ?, 'exited', ?, -0.05)
                """, (five_days_ago, f'sh6000{i}', 'anomaly_no_decline', today))

            # 连续5天负 pnl（触发 consecutive_bad_days）
            # final_pnl_pct 存储格式：小数形式（-0.02 表示 -2%）
            for day in [today, yesterday, two_days_ago, three_days_ago, four_days_ago]:
                for i in range(2):
                    conn.execute("""
                        INSERT INTO pick_tracking
                        (pick_date, code, signal_type, status, exit_date, final_pnl_pct)
                        VALUES (?, ?, ?, 'exited', ?, -0.02)
                    """, (five_days_ago, f'sh600{day.replace("-", "")}{i}', 'anomaly_no_decline', day))

        # 执行监控和回滚
        result = self.mgr.monitor_and_rollback()

        # 验证触发回滚
        self.assertGreater(result['checked'], 0)
        self.assertGreater(result['rollback_triggered'], 0)

        # 验证 daily_monitor_log 有记录
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT alert_type, severity, action_taken
                FROM daily_monitor_log
                WHERE alert_type = 'auto_rollback'
                ORDER BY id DESC LIMIT 1
            """).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], 'auto_rollback')
            self.assertEqual(row[1], 'critical')
            self.assertEqual(row[2], 'rollback_completed')

        # 验证 sandbox_config 状态已更新为 rejected
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT status, rollback_triggered, rollback_reason
                FROM sandbox_config
                WHERE batch_id = ?
            """, (batch_id,)).fetchone()

            self.assertEqual(row[0], 'rejected')
            self.assertEqual(row[1], 1)

    # ─────────────────────────────────────────────
    # 辅助方法测试
    # ─────────────────────────────────────────────

    def test_generate_batch_id(self):
        """测试批次ID生成"""
        # 第一次生成，无已存在批次，应返回 001
        batch_id1 = self.mgr.generate_batch_id('20260426')
        self.assertEqual(batch_id1, '20260426-001')

        # 创建一条记录使用该批次ID
        self.mgr.stage_change('strategy_config', 'first_wave_min_days', 5, batch_id1)

        # 第二次生成，已有1个批次，应返回 002
        batch_id2 = self.mgr.generate_batch_id('20260426')
        self.assertEqual(batch_id2, '20260426-002')

    def test_get_change_history(self):
        """测试变更历史查询"""
        self.mgr.stage_change('strategy_config', 'first_wave_min_days', 0.18, '20260426-001')
        self.mgr.stage_change('strategy_config', 'stop_loss_buffer', 0.03, '20260426-001')
        self.mgr.stage_change('strategy_config', 'first_wave_min_days', 0.20, '20260426-002')

        history = self.mgr.get_change_history('first_wave_min_days', days=30)

        self.assertEqual(len(history), 2)

    def test_get_status_summary(self):
        """测试状态摘要"""
        self.mgr.stage_change('strategy_config', 'first_wave_min_days', 1.0, 'test-001')
        self.mgr.stage_change('strategy_config', 'stop_loss_buffer', 2.0, 'test-002')

        summary = self.mgr.get_status_summary()

        self.assertEqual(summary['staged'], 2)
        self.assertEqual(summary['applied'], 0)
        self.assertEqual(summary['rolled_back'], 0)

    def test_get_batch_trace(self):
        """测试批次追溯"""
        self.mgr.stage_change('strategy_config', 'first_wave_min_days', 0.18, '20260426-001')
        self.mgr.stage_change('strategy_config', 'stop_loss_buffer', 0.03, '20260426-001')

        trace = self.mgr.get_batch_trace('20260426-001')

        self.assertEqual(trace['found'], True)
        self.assertEqual(trace['batch_id'], '20260426-001')
        self.assertEqual(len(trace['changes']), 2)
        self.assertEqual(trace['total_changes'], 2)

    def test_get_batch_sandbox_status_map(self):
        """测试N+1查询修复：_get_batch_sandbox_status_map"""
        self.mgr.stage_change('strategy_config', 'first_wave_min_days', 1.0, 'test-batch-001')
        self.mgr.stage_change('strategy_config', 'stop_loss_buffer', 2.0, 'test-batch-001')
        self.mgr.stage_change('strategy_config', 'max_hold_days', 3.0, 'test-batch-001')

        status_map = self.mgr._get_batch_sandbox_status_map('test-batch-001')

        # 验证返回字典格式正确
        self.assertIsInstance(status_map, dict)
        self.assertEqual(len(status_map), 3)
        self.assertEqual(status_map['first_wave_min_days'], 'staged')
        self.assertEqual(status_map['stop_loss_buffer'], 'staged')
        self.assertEqual(status_map['max_hold_days'], 'staged')

    def test_get_batch_trace_not_found(self):
        """测试批次不存在时的追溯"""
        trace = self.mgr.get_batch_trace('nonexistent-batch')

        self.assertEqual(trace['found'], False)

    # ─────────────────────────────────────────────
    # 集成测试
    # ─────────────────────────────────────────────

    def test_weekly_optimizer_integration(self):
        """测试 WeeklyOptimizer 能正常初始化 ChangeManager"""
        from weekly_optimizer import WeeklyOptimizer

        opt = WeeklyOptimizer(self.db_path)

        # 验证 change_mgr 已初始化
        self.assertTrue(hasattr(opt, 'change_mgr'))
        self.assertIsNotNone(opt.change_mgr)

        # 验证能生成 batch_id
        batch_id = opt.change_mgr.generate_batch_id('20260426')
        self.assertEqual(batch_id, '20260426-001')

    def test_sandbox_validator_integration(self):
        """测试 SandboxValidator 能正常初始化 ChangeManager"""
        from sandbox_validator import SandboxValidator

        validator = SandboxValidator(self.db_path)

        # 验证 change_mgr 已初始化
        self.assertTrue(hasattr(validator, 'change_mgr'))
        self.assertIsNotNone(validator.change_mgr)

        # 验证 validate_batch 方法存在
        self.assertTrue(hasattr(validator, 'validate_batch'))

    def test_adaptive_engine_integration(self):
        """测试 AdaptiveEngine 能正常初始化 ChangeManager"""
        from adaptive_engine import AdaptiveEngine

        engine = AdaptiveEngine(self.db_path)

        # 验证 change_mgr 已初始化
        self.assertTrue(hasattr(engine, 'change_mgr'))
        self.assertIsNotNone(engine.change_mgr)

        # 验证 monitor_and_rollback 方法被调用
        # 添加一些测试数据
        self.mgr.save_snapshot('weekly_optimize', 'test-batch', 'pre_change')
        self.mgr.stage_change('strategy_config', 'first_wave_min_days', 5, 'test-batch')
        self.mgr.commit_change(self.mgr.get_staged_params('test-batch')[0]['id'])

        result = engine.run_daily()
        self.assertIn('rollback_monitor', result)

    # ─────────────────────────────────────────────
    # 新增测试：覆盖修复的场景
    # ─────────────────────────────────────────────

    def test_expectancy_baseline_zero_triggers_rollback(self):
        """测试：baseline expectancy=0 且 current<0 时触发回滚（修复C1）"""
        batch_id = 'batch_zero_baseline_001'

        # 模拟场景：baseline expectancy=0，current expectancy=-5%
        five_days_ago_ts = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S')
        five_days_ago = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        thirty_days_ago = (datetime.now() - timedelta(days=35)).strftime('%Y-%m-%d')
        today = datetime.now().strftime('%Y-%m-%d')

        # 插入基线数据（expectancy=0）
        with sqlite3.connect(self.db_path) as conn:
            for i in range(10):
                conn.execute("""
                    INSERT INTO pick_tracking
                    (pick_date, code, signal_type, status, exit_date, final_pnl_pct)
                    VALUES (?, ?, ?, 'exited', ?, 0.0)
                """, (thirty_days_ago, f'sh9000{i}', 'anomaly_no_decline', five_days_ago))

        # 保存快照
        snapshot_id = self.mgr.save_snapshot('weekly_optimize', batch_id, 'pre_change')

        # 暂存并提交变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='strategy_config',
            param_key='first_wave_min_days',
            new_value=5,
            batch_id=batch_id
        )
        self.mgr.commit_change(sandbox_id)

        # 更新 applied_at 和 snapshot_date
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE sandbox_config SET applied_at = ? WHERE batch_id = ?
            """, (five_days_ago_ts, batch_id))
            conn.execute("""
                UPDATE param_snapshot SET snapshot_date = ? WHERE id = ?
            """, (five_days_ago_ts, snapshot_id))

            # 插入当前数据（expectancy=-5%）
            for i in range(15):
                conn.execute("""
                    INSERT INTO pick_tracking
                    (pick_date, code, signal_type, status, exit_date, final_pnl_pct)
                    VALUES (?, ?, ?, 'exited', ?, -0.05)
                """, (five_days_ago, f'sh6000{i}', 'anomaly_no_decline', today))

        # 获取批次信息
        batches = self.mgr.get_applied_batches_in_monitor_window()
        target_batch = next(b for b in batches if b['batch_id'] == batch_id)

        # 检查性能退化
        degradation = self.mgr.check_performance_degradation(target_batch)

        # 验证：baseline=0, current=-5%，expectancy_drop应为1（完全退化）
        self.assertTrue(degradation['should_rollback'])
        self.assertEqual(degradation['expectancy_drop'], 1)
        self.assertIn('expectancy_drop', degradation['reason'])

    def test_restore_snapshot_with_corrupted_json(self):
        """测试：损坏的JSON不会导致崩溃（修复I2）"""
        batch_id = 'batch_corrupted_json_001'

        # 直接插入一条损坏的JSON快照
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO param_snapshot
                (snapshot_date, snapshot_type, batch_id, trigger_reason, params_json, signal_status_json, environment_json)
                VALUES (?, 'pre_change', ?, 'test', '{invalid json}', 'also broken', '{"good": true}')
            """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), batch_id))
            corrupted_snapshot_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 尝试恢复：损坏的params_json和signal_status_json会返回空dict/list，但environment_dict正常
        result = self.mgr.restore_snapshot(corrupted_snapshot_id, reason='test_corrupted')

        # 验证恢复成功（虽然数据为空，但不会崩溃）
        self.assertTrue(result)

        # 验证快照已标记为已恢复
        snapshot = self.mgr.get_snapshot_by_id(corrupted_snapshot_id)
        self.assertEqual(snapshot['is_restored'], 1)

    def test_stage_change_missing_signal_type(self):
        """测试：signal_type不存在时使用'unknown'（修复I4）"""
        batch_id = 'batch_missing_signal_001'

        # 暂存一个不存在的signal_type变更
        sandbox_id = self.mgr.stage_change(
            optimize_type='signal_status',
            param_key='nonexistent_signal_type',
            new_value='watching',
            batch_id=batch_id
        )

        # 验证暂存成功
        self.assertGreater(sandbox_id, 0)

        # 验证current_value为'unknown'（而非None字符串）
        params = self.mgr.get_staged_params(batch_id)
        self.assertEqual(len(params), 1)
        self.assertEqual(params[0]['current_value'], 'unknown')


if __name__ == '__main__':
    unittest.main()