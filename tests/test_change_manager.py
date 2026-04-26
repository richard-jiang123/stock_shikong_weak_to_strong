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
        self.assertIsNotNone(snapshot['params_json'])
        self.assertIsNotNone(snapshot['signal_status_json'])
        self.assertIsNotNone(snapshot['environment_json'])

        # 验证 params_json 包含预期参数
        import json
        params = json.loads(snapshot['params_json'])
        self.assertIn('first_wave_min_days', params)
        self.assertIn('stop_loss_buffer', params)

        # 验证 signal_status_json 包含信号状态
        signal_status = json.loads(snapshot['signal_status_json'])
        self.assertEqual(len(signal_status), 4)
        signal_types = [s['signal_type'] for s in signal_status]
        self.assertIn('anomaly_no_decline', signal_types)

    def test_restore_snapshot(self):
        """测试：从快照恢复参数"""
        import json

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
        result = self.mgr.restore_snapshot(snapshot_id, restore_reason='test_restore')
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


if __name__ == '__main__':
    unittest.main()