#!/usr/bin/env python3
"""测试变更管理模块"""
import unittest
import sys
import os
import tempfile
import sqlite3

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


if __name__ == '__main__':
    unittest.main()