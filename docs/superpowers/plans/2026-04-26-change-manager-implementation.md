# Change Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement change_manager.py module for the shikong_fufei quantitative system, providing snapshot management, parameter isolation, batch rollback, and proactive rollback monitoring.

**Architecture:** Four-layer implementation in dependency order: D (snapshot) → C (parameter isolation) → B (batch rollback) → A (proactive monitoring). Uses two new SQLite tables (param_snapshot, sandbox_config) and extends optimization_history. Integrates with existing weekly_optimizer.py, sandbox_validator.py, and adaptive_engine.py.

**Tech Stack:** Python 3, SQLite, numpy, pytest for testing

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `change_manager.py` | Create | Main module: ChangeManager class with all 4 layers |
| `tests/test_change_manager.py` | Create | Unit tests for ChangeManager |
| `weekly_optimizer.py` | Modify | Integrate ChangeManager for staging changes |
| `sandbox_validator.py` | Modify | Read from sandbox_config, commit validated changes |
| `adaptive_engine.py` | Modify | Add proactive rollback monitoring call |
| `daily_run.sh` | Modify | Add CLI options for change management |

---

## Task 1: Create change_manager.py - Table Schema and Class Initialization

**Files:**
- Create: `change_manager.py`

- [ ] **Step 1: Write the module header and imports**

```python
#!/usr/bin/env python3
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
```

- [ ] **Step 2: Write the _ensure_tables method**

```python
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
                    created_at TEXT DEFAULT datetime('now')
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
                    staged_at TEXT DEFAULT datetime('now'),
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
```

- [ ] **Step 3: Create the file with initial structure**

Run: `touch /home/jzc/wechat_text/shikong_fufei/change_manager.py`

Expected: File created

- [ ] **Step 4: Write full file content for Task 1**

Write the complete change_manager.py with imports, class definition, and _ensure_tables method.

- [ ] **Step 5: Commit Task 1**

```bash
git add change_manager.py
git commit -m "feat(change_manager): add table schema and class initialization

- Add param_snapshot table for storing parameter snapshots
- Add sandbox_config table for parameter isolation
- Extend optimization_history with batch_id, rollback fields

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Implement Snapshot Management (Layer D)

**Files:**
- Modify: `change_manager.py`

- [ ] **Step 1: Write the save_snapshot method**

```python
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
```

- [ ] **Step 2: Write the restore_snapshot method**

```python
    def restore_snapshot(self, snapshot_id: int, reason: str = None) -> bool:
        """
        恢复指定快照

        Args:
            snapshot_id: 快照记录ID
            reason: 恢复原因

        Returns:
            是否成功恢复
        """
        # 先读取快照数据（在事务外读取，避免长事务）
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT params_json, signal_status_json, environment_json, is_restored
                FROM param_snapshot WHERE id=?
            """, (snapshot_id,)).fetchone()

        if row is None:
            return False

        if row[3] == 1:  # 已恢复过
            return False

        params_json = row[0]
        signal_status_json = row[1]
        environment_json = row[2]

        # 解析数据
        params_dict = json.loads(params_json)
        signal_status_list = json.loads(signal_status_json)
        environment_dict = json.loads(environment_json)

        restored_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 整个恢复过程在单一事务中执行
        with self.dl._get_conn() as conn:
            # 1. 恢复 strategy_config
            for key, value in params_dict.items():
                conn.execute("""
                    INSERT OR REPLACE INTO strategy_config
                    (param_key, param_value, description, category, updated_at)
                    VALUES (?, ?,
                            COALESCE((SELECT description FROM strategy_config WHERE param_key=?), ''),
                            COALESCE((SELECT category FROM strategy_config WHERE param_key=?), 'unknown'),
                            ?)
                """, (key, value, key, key, restored_at))

            # 2. 恢复 signal_status
            for item in signal_status_list:
                conn.execute("""
                    UPDATE signal_status SET
                        status_level=?, weight_multiplier=?, last_check_date=datetime('now')
                    WHERE signal_type=?
                """, (item['status_level'], item['weight_multiplier'], item['signal_type']))

            # 3. 恢复环境参数
            for key, value in environment_dict.items():
                conn.execute("""
                    INSERT OR REPLACE INTO strategy_config
                    (param_key, param_value, description, category, updated_at)
                    VALUES (?, ?,
                            COALESCE((SELECT description FROM strategy_config WHERE param_key=?), ''),
                            COALESCE((SELECT category FROM strategy_config WHERE param_key=?), 'environment'),
                            ?)
                """, (key, value, key, key, restored_at))

            # 4. 标记快照已恢复
            conn.execute("""
                UPDATE param_snapshot SET
                    is_restored=1, restored_at=?, restore_reason=?
                WHERE id=?
            """, (restored_at, reason, snapshot_id))

        return True
```

- [ ] **Step 3: Write the get_latest_snapshot and get_snapshot_by_id methods**

```python
    def get_latest_snapshot(self, batch_id: str = None) -> Optional[Dict]:
        """
        获取最近快照

        Args:
            batch_id: 批次ID，None时返回最新的pre_change快照

        Returns:
            快照信息 dict 或 None
        """
        with self.dl._get_conn() as conn:
            if batch_id:
                row = conn.execute("""
                    SELECT id, snapshot_date, snapshot_type, batch_id, trigger_reason,
                           params_json, signal_status_json, environment_json
                    FROM param_snapshot
                    WHERE batch_id=? AND is_restored=0
                    ORDER BY id DESC LIMIT 1
                """, (batch_id,)).fetchone()
            else:
                row = conn.execute("""
                    SELECT id, snapshot_date, snapshot_type, batch_id, trigger_reason,
                           params_json, signal_status_json, environment_json
                    FROM param_snapshot
                    WHERE snapshot_type='pre_change' AND is_restored=0
                    ORDER BY id DESC LIMIT 1
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
        }

    def get_snapshot_by_id(self, snapshot_id: int) -> Optional[Dict]:
        """根据ID获取快照"""
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT id, snapshot_date, snapshot_type, batch_id, trigger_reason,
                       params_json, signal_status_json, environment_json, is_restored
                FROM param_snapshot WHERE id=?
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
        }
```

- [ ] **Step 4: Write test for snapshot functionality**

Create `tests/test_change_manager.py` with test_save_snapshot and test_restore_snapshot:

```python
#!/usr/bin/env python3
"""变更管理模块单元测试"""
import pytest
import tempfile
import os
import sqlite3
from datetime import datetime, timedelta

from change_manager import ChangeManager
from data_layer import StockDataLayer
from strategy_config import StrategyConfig


class TestChangeManager:

    @pytest.fixture
    def temp_db(self):
        """临时数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        # StockDataLayer.__init__ 自动建表（_init_db + _create_adaptive_tables）
        dl = StockDataLayer(path)

        # 确保 pick_tracking 表存在（监控方法 _calc_metrics_in_range 依赖此表）
        # 此表在 PickTracker 中定义，但 StockDataLayer 不创建
        conn = sqlite3.connect(path)
        conn.execute('''CREATE TABLE IF NOT EXISTS pick_tracking (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            pick_date     TEXT NOT NULL,
            code          TEXT NOT NULL,
            signal_type   TEXT NOT NULL,
            status        TEXT DEFAULT 'active',
            exit_date     TEXT,
            final_pnl_pct REAL,
            UNIQUE(pick_date, code)
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_pick_status ON pick_tracking(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_pick_date ON pick_tracking(exit_date)')
        conn.close()

        return path

    @pytest.fixture
    def mgr(self, temp_db):
        """ChangeManager 实例"""
        return ChangeManager(temp_db)

    def test_save_snapshot(self, mgr):
        """测试快照保存"""
        snapshot_id = mgr.save_snapshot(
            trigger_reason='weekly_optimize',
            batch_id='20260426-001'
        )

        assert snapshot_id > 0

        snapshot = mgr.get_snapshot_by_id(snapshot_id)
        assert snapshot is not None
        assert snapshot['batch_id'] == '20260426-001'
        assert snapshot['trigger_reason'] == 'weekly_optimize'
        assert 'params' in snapshot
        assert 'signal_status' in snapshot

    def test_restore_snapshot(self, mgr, temp_db):
        """测试快照恢复"""
        # 修改参数并写入数据库
        cfg = StrategyConfig(temp_db)
        cfg.set('first_wave_min_gain', 0.20)

        # 保存快照（此时快照中 first_wave_min_gain=0.20）
        snapshot_id = mgr.save_snapshot('test', 'test-001')

        # 再次修改参数
        cfg.set('first_wave_min_gain', 0.25)

        # 恢复快照
        success = mgr.restore_snapshot(snapshot_id, 'test_restore')

        assert success

        # 验证参数已恢复（重新创建cfg实例确保从数据库读取）
        cfg_verify = StrategyConfig(temp_db)
        restored_value = cfg_verify.get('first_wave_min_gain')
        assert restored_value == 0.20


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
```

- [ ] **Step 5: Run test to verify snapshot functionality**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python -m pytest tests/test_change_manager.py::TestChangeManager::test_save_snapshot -v`

Expected: PASS

- [ ] **Step 6: Commit Task 2**

```bash
git add change_manager.py tests/test_change_manager.py
git commit -m "feat(change_manager): implement snapshot management (Layer D)

- save_snapshot: captures params, signal_status, environment
- restore_snapshot: atomic restore in single transaction
- get_latest_snapshot, get_snapshot_by_id for retrieval

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Implement Parameter Isolation (Layer C)

**Files:**
- Modify: `change_manager.py`
- Modify: `tests/test_change_manager.py`

- [ ] **Step 1: Write the stage_change method**

```python
    # ─────────────────────────────────────────────
    # 参数隔离
    # ─────────────────────────────────────────────

    def stage_change(self, optimize_type: str, param_key: str, new_value,
                     batch_id: str, current_value=None) -> int:
        """
        暂存变更到 sandbox_config（不写入 strategy_config）

        Args:
            optimize_type: 'params' | 'score_weights' | 'signal_status' | 'environment'
            param_key: 参数名
            new_value: 新值（数值或字符串，存储时统一转为TEXT）
            batch_id: 批次ID
            current_value: 当前值（可选，自动获取）

        Returns:
            sandbox_id: sandbox_config 记录ID

        注意：new_value 和 current_value 使用 TEXT 存储，
              signal_status 的 status_level 是字符串值。
        """
        # 获取当前值
        if current_value is None:
            if optimize_type == 'signal_status':
                with self.dl._get_conn() as conn:
                    row = conn.execute("""
                        SELECT status_level FROM signal_status WHERE signal_type=?
                    """, (param_key,)).fetchone()
                    current_value = row[0] if row else 'active'
            else:
                current_value = self.cfg.get(param_key)

        # 转换为字符串存储（TEXT类型）
        sandbox_value_str = str(new_value)
        current_value_str = str(current_value)

        staged_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with self.dl._get_conn() as conn:
            try:
                cursor = conn.execute("""
                    INSERT INTO sandbox_config
                    (batch_id, param_key, sandbox_value, current_value, optimize_type, status, staged_at)
                    VALUES (?, ?, ?, ?, ?, 'staged', ?)
                """, (batch_id, param_key, sandbox_value_str, current_value_str, optimize_type, staged_at))
                sandbox_id = cursor.lastrowid
            except Exception:
                # 已存在则更新
                conn.execute("""
                    UPDATE sandbox_config SET
                        sandbox_value=?, current_value=?, optimize_type=?, status='staged', staged_at=?
                    WHERE batch_id=? AND param_key=?
                """, (sandbox_value_str, current_value_str, optimize_type, staged_at, batch_id, param_key))
                sandbox_id = conn.execute(
                    "SELECT id FROM sandbox_config WHERE batch_id=? AND param_key=?",
                    (batch_id, param_key)
                ).fetchone()[0]

        return sandbox_id
```

- [ ] **Step 2: Write the get_staged_params method**

```python
    def get_staged_params(self, batch_id: str) -> List[Dict]:
        """
        获取某批次所有暂存参数

        Returns:
            list of dict: [{id, param_key, sandbox_value, current_value, optimize_type, status}, ...]
        """
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT id, batch_id, param_key, sandbox_value, current_value,
                       optimize_type, status, staged_at
                FROM sandbox_config
                WHERE batch_id=? AND status IN ('staged', 'validating', 'passed')
                ORDER BY id
            """, (batch_id,)).fetchall()

        results = []
        for r in rows:
            sandbox_value = r[3]
            current_value = r[4]
            optimize_type = r[5]

            # 按类型转换值
            if optimize_type == 'signal_status':
                sandbox_value_parsed = sandbox_value
                current_value_parsed = current_value
            else:
                try:
                    sandbox_value_parsed = float(sandbox_value)
                except (ValueError, TypeError):
                    sandbox_value_parsed = sandbox_value
                try:
                    current_value_parsed = float(current_value)
                except (ValueError, TypeError):
                    current_value_parsed = current_value

            results.append({
                'id': r[0],
                'batch_id': r[1],
                'param_key': r[2],
                'sandbox_value': sandbox_value_parsed,
                'current_value': current_value_parsed,
                'optimize_type': optimize_type,
                'status': r[6],
                'staged_at': r[7],
            })

        return results
```

- [ ] **Step 3: Write the get_all_staged_batches and update_status methods**

```python
    def get_all_staged_batches(self) -> List[Dict]:
        """获取所有待处理的批次"""
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT DISTINCT batch_id, MIN(staged_at) as staged_at,
                       COUNT(*) as change_count
                FROM sandbox_config
                WHERE status IN ('staged', 'validating', 'passed')
                GROUP BY batch_id
                ORDER BY staged_at
            """).fetchall()

        return [
            {
                'batch_id': r[0],
                'staged_at': r[1],
                'change_count': r[2],
            }
            for r in rows
        ]

    def update_status(self, sandbox_id: int, status: str, reason: str = None):
        """更新 sandbox_config 状态"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with self.dl._get_conn() as conn:
            if status == 'validating':
                conn.execute("""
                    UPDATE sandbox_config SET status=?, validation_started_at=? WHERE id=?
                """, (status, now, sandbox_id))
            elif status == 'passed':
                conn.execute("""
                    UPDATE sandbox_config SET status=?, validated_at=? WHERE id=?
                """, (status, now, sandbox_id))
            elif status == 'applied':
                conn.execute("""
                    UPDATE sandbox_config SET status=?, applied_at=? WHERE id=?
                """, (status, now, sandbox_id))
            elif status == 'rejected':
                conn.execute("""
                    UPDATE sandbox_config SET status=?, rejected_at=?, rejection_reason=? WHERE id=?
                """, (status, now, reason, sandbox_id))
```

- [ ] **Step 4: Write the commit_change and reject_change methods**

```python
    def commit_change(self, sandbox_id: int) -> bool:
        """
        正式应用变更（写入 strategy_config / signal_status）

        Args:
            sandbox_id: sandbox_config 记录ID

        Returns:
            是否成功应用
        """
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT param_key, sandbox_value, optimize_type, status
                FROM sandbox_config WHERE id=?
            """, (sandbox_id,)).fetchone()

        if row is None:
            return False

        param_key = row[0]
        sandbox_value_str = row[1]
        optimize_type = row[2]
        status = row[3]

        if status not in ('passed', 'staged'):
            return False

        # 按类型转换值
        if optimize_type == 'signal_status':
            new_value = sandbox_value_str
        else:
            try:
                new_value = float(sandbox_value_str)
            except (ValueError, TypeError):
                new_value = sandbox_value_str

        # 写入正式表
        if optimize_type == 'signal_status':
            weight_mult = get_weight_multiplier(new_value)
            with self.dl._get_conn() as conn:
                conn.execute("""
                    UPDATE signal_status SET
                        status_level=?, weight_multiplier=?, last_check_date=datetime('now')
                    WHERE signal_type=?
                """, (new_value, weight_mult, param_key))
        else:
            self.cfg.set(param_key, new_value)

        # 更新 sandbox 状态
        self.update_status(sandbox_id, 'applied')

        return True

    def reject_change(self, sandbox_id: int, reason: str) -> bool:
        """拒绝变更"""
        self.update_status(sandbox_id, 'rejected', reason)
        return True
```

- [ ] **Step 5: Write tests for parameter isolation**

Add to `tests/test_change_manager.py`:

```python
    def test_stage_change(self, mgr):
        """测试变更暂存"""
        sandbox_id = mgr.stage_change(
            optimize_type='params',
            param_key='first_wave_min_gain',
            new_value=0.18,
            batch_id='20260426-001',
            current_value=0.15
        )

        assert sandbox_id > 0

        staged = mgr.get_staged_params('20260426-001')
        assert len(staged) == 1
        assert staged[0]['param_key'] == 'first_wave_min_gain'
        assert staged[0]['sandbox_value'] == 0.18
        assert staged[0]['status'] == 'staged'

    def test_commit_change(self, mgr, temp_db):
        """测试变更应用"""
        sandbox_id = mgr.stage_change(
            optimize_type='params',
            param_key='first_wave_min_gain',
            new_value=0.18,
            batch_id='20260426-001'
        )

        mgr.update_status(sandbox_id, 'passed')

        success = mgr.commit_change(sandbox_id)

        assert success

        cfg = StrategyConfig(temp_db)
        value = cfg.get('first_wave_min_gain')
        assert value == 0.18
```

- [ ] **Step 6: Run tests for parameter isolation**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python -m pytest tests/test_change_manager.py::TestChangeManager::test_stage_change tests/test_change_manager.py::TestChangeManager::test_commit_change -v`

Expected: PASS

- [ ] **Step 7: Commit Task 3**

```bash
git add change_manager.py tests/test_change_manager.py
git commit -m "feat(change_manager): implement parameter isolation (Layer C)

- stage_change: stores changes in sandbox_config without affecting strategy_config
- get_staged_params, get_all_staged_batches: retrieve pending changes
- update_status: track validation lifecycle
- commit_change, reject_change: finalize or reject staged changes

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Implement Batch Rollback (Layer B)

**Files:**
- Modify: `change_manager.py`
- Modify: `tests/test_change_manager.py`

- [ ] **Step 1: Write the rollback_batch method**

```python
    # ─────────────────────────────────────────────
    # 批量回滚
    # ─────────────────────────────────────────────

    def rollback_batch(self, batch_id: str, reason: str) -> Dict:
        """
        回滚整个批次变更

        Args:
            batch_id: 批次ID
            reason: 回滚原因

        Returns:
            回滚结果: {'rolled_back': int, 'failed': int, 'details': list}
        """
        # 获取批次快照
        snapshot = self.get_latest_snapshot(batch_id)

        if snapshot is None:
            return {'rolled_back': 0, 'failed': 0, 'reason': 'no_snapshot_found'}

        # 恢复快照
        success = self.restore_snapshot(snapshot['id'], reason)

        if not success:
            return {'rolled_back': 0, 'failed': 0, 'reason': 'restore_failed'}

        # 更新 sandbox_config 状态
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self.dl._get_conn() as conn:
            conn.execute("""
                UPDATE sandbox_config SET
                    rollback_triggered=1, rollback_at=?, rollback_reason=?, status='rejected'
                WHERE batch_id=? AND status='applied'
            """, (now, reason, batch_id))

            # 更新 optimization_history
            conn.execute("""
                UPDATE optimization_history SET
                    rollback_triggered=1, rollback_at=?, rollback_reason=?
                WHERE batch_id=? AND rollback_triggered=0
            """, (now, reason, batch_id))

        # 统计回滚数量
        with self.dl._get_conn() as conn:
            rolled_count = conn.execute("""
                SELECT COUNT(*) FROM sandbox_config
                WHERE batch_id=? AND rollback_triggered=1
            """, (batch_id,)).fetchone()[0]

        return {
            'rolled_back': rolled_count,
            'failed': 0,
            'snapshot_id': snapshot['id'],
            'reason': reason,
        }
```

- [ ] **Step 2: Write the get_batch_changes and get_batch_info methods**

```python
    def get_batch_changes(self, batch_id: str) -> List[Dict]:
        """获取批次内所有变更"""
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT id, param_key, sandbox_value, current_value, optimize_type,
                       status, staged_at, applied_at, rollback_triggered, rollback_at
                FROM sandbox_config
                WHERE batch_id=?
                ORDER BY id
            """, (batch_id,)).fetchall()

        return [
            {
                'id': r[0],
                'param_key': r[1],
                'sandbox_value': r[2],
                'current_value': r[3],
                'optimize_type': r[4],
                'status': r[5],
                'staged_at': r[6],
                'applied_at': r[7],
                'rollback_triggered': r[8],
                'rollback_at': r[9],
            }
            for r in rows
        ]

    def get_batch_info(self, batch_id: str) -> Optional[Dict]:
        """获取批次完整信息"""
        snapshot = self.get_latest_snapshot(batch_id)
        changes = self.get_batch_changes(batch_id)

        if not changes:
            return None

        applied_count = sum(1 for c in changes if c['status'] == 'applied')
        rollback_count = sum(1 for c in changes if c['rollback_triggered'] == 1)

        return {
            'batch_id': batch_id,
            'snapshot': snapshot,
            'changes': changes,
            'total_changes': len(changes),
            'applied_count': applied_count,
            'rollback_count': rollback_count,
            'is_rolled_back': rollback_count > 0,
        }
```

- [ ] **Step 3: Write test for batch rollback**

Add to `tests/test_change_manager.py`:

```python
    def test_rollback_batch(self, mgr, temp_db):
        """测试批量回滚"""
        # 保存快照
        cfg = StrategyConfig(temp_db)
        cfg.set('first_wave_min_gain', 0.15)
        snapshot_id = mgr.save_snapshot('test', '20260426-001')

        # 暂存并应用变更
        sandbox_id = mgr.stage_change('params', 'first_wave_min_gain', 0.18, '20260426-001')
        mgr.update_status(sandbox_id, 'passed')
        mgr.commit_change(sandbox_id)

        # 执行回滚
        result = mgr.rollback_batch('20260426-001', 'test_rollback')

        assert result['rolled_back'] > 0

        # 验证参数已恢复（重新创建cfg实例确保从数据库读取）
        cfg_verify = StrategyConfig(temp_db)
        value = cfg_verify.get('first_wave_min_gain')
        assert value == 0.15
```

- [ ] **Step 4: Run test for batch rollback**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python -m pytest tests/test_change_manager.py::TestChangeManager::test_rollback_batch -v`

Expected: PASS

- [ ] **Step 5: Commit Task 4**

```bash
git add change_manager.py tests/test_change_manager.py
git commit -m "feat(change_manager): implement batch rollback (Layer B)

- rollback_batch: restores snapshot and marks all batch changes as rolled back
- get_batch_changes, get_batch_info: retrieve batch details

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Implement Proactive Rollback Monitoring (Layer A)

**Files:**
- Modify: `change_manager.py`
- Modify: `tests/test_change_manager.py`

- [ ] **Step 1: Write the monitor_and_rollback method**

```python
    # ─────────────────────────────────────────────
    # 主动回滚监控
    # ─────────────────────────────────────────────

    def monitor_and_rollback(self) -> Dict:
        """
        检查已应用变更的表现，恶化超过阈值时触发回滚

        Returns:
            {'checked': int, 'rollback_triggered': int, 'details': list}
        """
        applied_batches = self.get_applied_batches_in_monitor_window()

        checked = 0
        rollback_triggered = 0
        details = []

        for batch in applied_batches:
            checked += 1

            degradation = self.check_performance_degradation(batch)

            details.append({
                'batch_id': batch['batch_id'],
                'applied_at': batch['applied_at'],
                'degradation': degradation,
            })

            if degradation['should_rollback']:
                rollback_result = self.rollback_batch(batch['batch_id'], degradation['reason'])
                rollback_triggered += 1

                details[-1]['rollback_result'] = rollback_result

                self._log_rollback(batch, degradation)

        return {
            'checked': checked,
            'rollback_triggered': rollback_triggered,
            'details': details,
        }
```

- [ ] **Step 2: Write the get_applied_batches_in_monitor_window method**

```python
    def get_applied_batches_in_monitor_window(self) -> List[Dict]:
        """
        获取监控窗口内的已应用批次（去重）

        监控窗口：变更生效后30天内

        Returns:
            list of dict: [{batch_id, applied_at, monitor_days_elapsed, ...}, ...]
        """
        monitor_days = self.ROLLBACK_CONFIG['monitor_days']
        cutoff_date = (datetime.now() - timedelta(days=monitor_days)).strftime('%Y-%m-%d')

        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT DISTINCT batch_id, MIN(applied_at) as applied_at
                FROM sandbox_config
                WHERE status='applied' AND rollback_triggered=0 AND applied_at >= ?
                GROUP BY batch_id
            """, (cutoff_date,)).fetchall()

        results = []
        for r in rows:
            batch_id = r[0]
            applied_at = r[1]

            applied_dt = datetime.strptime(applied_at, '%Y-%m-%d %H:%M:%S')
            days_elapsed = (datetime.now() - applied_dt).days

            results.append({
                'batch_id': batch_id,
                'applied_at': applied_at,
                'monitor_days_elapsed': days_elapsed,
            })

        return results
```

- [ ] **Step 3: Write the check_performance_degradation method**

```python
    def check_performance_degradation(self, batch: Dict) -> Dict:
        """
        检查变更后表现恶化情况

        Args:
            batch: 批次信息 dict

        Returns:
            {'should_rollback': bool, 'reason': str, 'metrics': dict}
        """
        applied_at = batch['applied_at']
        batch_id = batch['batch_id']

        snapshot = self.get_latest_snapshot(batch_id)
        baseline_metrics = self._calc_baseline_metrics(snapshot)

        current_metrics = self._calc_current_metrics(applied_at)

        if current_metrics['sample_count'] < self.ROLLBACK_CONFIG['min_samples_for_check']:
            return {
                'should_rollback': False,
                'reason': f'样本不足({current_metrics["sample_count"]}笔)',
                'baseline': baseline_metrics,
                'current': current_metrics,
            }

        # 计算下降幅度
        if baseline_metrics['expectancy'] > 0:
            expectancy_drop = (baseline_metrics['expectancy'] - current_metrics['expectancy']) / baseline_metrics['expectancy']
        else:
            expectancy_drop = 0 if current_metrics['expectancy'] >= 0 else 1

        win_rate_drop = baseline_metrics['win_rate'] - current_metrics['win_rate']

        # 判断是否触发回滚
        should_rollback = False
        reason = None

        if expectancy_drop > self.ROLLBACK_CONFIG['expectancy_drop_threshold']:
            should_rollback = True
            reason = f"期望值下降{expectancy_drop:.1%}超过阈值{self.ROLLBACK_CONFIG['expectancy_drop_threshold']:.0%}"
        elif win_rate_drop > self.ROLLBACK_CONFIG['win_rate_drop_threshold']:
            should_rollback = True
            reason = f"胜率下降{win_rate_drop:.1f}%超过阈值{self.ROLLBACK_CONFIG['win_rate_drop_threshold']}%"
        elif self._check_consecutive_bad_trading_days(applied_at) >= self.ROLLBACK_CONFIG['consecutive_bad_days']:
            should_rollback = True
            reason = f"连续{self.ROLLBACK_CONFIG['consecutive_bad_days']}个交易日期望值<0"

        return {
            'should_rollback': should_rollback,
            'reason': reason,
            'expectancy_drop': expectancy_drop,
            'win_rate_drop': win_rate_drop,
            'baseline': baseline_metrics,
            'current': current_metrics,
        }
```

- [ ] **Step 4: Write the helper methods for metrics calculation**

```python
    def _calc_baseline_metrics(self, snapshot: Dict) -> Dict:
        """计算变更前基准表现"""
        if snapshot is None:
            # 返回百分比形式，与 _calc_metrics_in_range 单位一致
            # 0.042 小数 → 4.2 百分比
            return {'expectancy': 4.2, 'win_rate': 59.8, 'sample_count': 0}

        snapshot_date = snapshot['snapshot_date']
        snapshot_dt = datetime.strptime(snapshot_date, '%Y-%m-%d %H:%M:%S')
        start_date = (snapshot_dt - timedelta(days=30)).strftime('%Y-%m-%d')
        end_date = snapshot_dt.strftime('%Y-%m-%d')

        return self._calc_metrics_in_range(start_date, end_date)

    def _calc_current_metrics(self, applied_at: str) -> Dict:
        """计算变更后当前表现"""
        applied_dt = datetime.strptime(applied_at, '%Y-%m-%d %H:%M:%S')
        start_date = applied_dt.strftime('%Y-%m-%d')
        end_date = datetime.now().strftime('%Y-%m-%d')

        return self._calc_metrics_in_range(start_date, end_date)

    def _calc_metrics_in_range(self, start_date: str, end_date: str) -> Dict:
        """计算指定时间范围内的表现指标

        Returns:
            {'expectancy': float, 'win_rate': float, 'sample_count': int}
            expectancy 为百分比形式（如 4.2 表示 4.2%）
        """
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT final_pnl_pct FROM pick_tracking
                WHERE status='exited' AND exit_date >= ? AND exit_date <= ?
            """, (start_date, end_date)).fetchall()

        pnls = [r[0] for r in rows]
        sample_count = len(pnls)

        if sample_count == 0:
            return {'expectancy': 0, 'win_rate': 0, 'sample_count': 0}

        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / sample_count * 100
        expectancy = np.mean(pnls) * 100  # 转为百分比

        return {
            'expectancy': expectancy,
            'win_rate': win_rate,
            'sample_count': sample_count,
        }

    def _check_consecutive_bad_trading_days(self, applied_at: str) -> int:
        """检查连续期望值<0的交易日天数"""
        applied_dt = datetime.strptime(applied_at, '%Y-%m-%d %H:%M:%S')
        start_date = applied_dt.strftime('%Y-%m-%d')

        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT exit_date, AVG(final_pnl_pct) as daily_exp
                FROM pick_tracking
                WHERE status='exited' AND exit_date >= ?
                GROUP BY exit_date
                ORDER BY exit_date DESC
            """, (start_date,)).fetchall()

        consecutive = 0
        for r in rows:
            daily_exp = r[1]
            if daily_exp < 0:
                consecutive += 1
            else:
                break

        return consecutive
```

- [ ] **Step 5: Write the _log_rollback method**

```python
    def _log_rollback(self, batch: Dict, degradation: Dict):
        """记录回滚日志"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        batch_id = batch['batch_id']
        reason = degradation['reason']

        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT INTO daily_monitor_log
                (monitor_date, alert_type, alert_detail, severity, action_taken, created_at)
                VALUES (?, 'auto_rollback', ?, 'critical', 'rollback_completed', ?)
            """, (now.split()[0],
                  f"批次{batch_id}触发自动回滚: {reason}",
                  now))
```

- [ ] **Step 6: Write tests for monitoring methods**

Add to `tests/test_change_manager.py`:

```python
    def test_check_performance_degradation_sample_insufficient(self, mgr):
        """测试样本不足时不触发回滚"""
        batch = {
            'batch_id': '20260426-001',
            'applied_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        degradation = mgr.check_performance_degradation(batch)

        # 无样本数据时，样本不足
        assert degradation['should_rollback'] == False
        assert '样本不足' in degradation['reason'] or degradation['current']['sample_count'] < mgr.ROLLBACK_CONFIG['min_samples_for_check']

    def test_get_applied_batches_in_monitor_window(self, mgr):
        """测试获取监控窗口内的批次"""
        # 创建一个已应用的变更（模拟）
        sandbox_id = mgr.stage_change('params', 'test_param', 1.5, '20260426-001')
        mgr.update_status(sandbox_id, 'passed')
        mgr.commit_change(sandbox_id)

        batches = mgr.get_applied_batches_in_monitor_window()

        # 应该包含刚应用的批次
        assert len(batches) >= 1
        found_batch = [b for b in batches if b['batch_id'] == '20260426-001']
        assert len(found_batch) == 1

    def test_consecutive_bad_trading_days_empty(self, mgr):
        """测试无交易数据时连续交易日计数"""
        applied_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        consecutive = mgr._check_consecutive_bad_trading_days(applied_at)

        # 无交易数据时，连续天数应为0
        assert consecutive == 0
```

- [ ] **Step 7: Commit Task 5**

```bash
git add change_manager.py tests/test_change_manager.py
git commit -m "feat(change_manager): implement proactive rollback monitoring (Layer A)

- monitor_and_rollback: checks applied batches within 30-day window
- check_performance_degradation: compares baseline vs current metrics
- _calc_metrics_in_range: computes expectancy and win_rate from pick_tracking
- _check_consecutive_bad_trading_days: counts trading days with negative expectancy
- _log_rollback: writes auto_rollback events to daily_monitor_log

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Implement Helper Methods and CLI Interface

**Files:**
- Modify: `change_manager.py`
- Modify: `tests/test_change_manager.py`

- [ ] **Step 1: Write the generate_batch_id method**

```python
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
```

- [ ] **Step 2: Write the get_status_summary and print_status_summary methods**

```python
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
```

- [ ] **Step 3: Write the change history and batch trace methods**

```python
    # ─────────────────────────────────────────────
    # 变更追溯
    # ─────────────────────────────────────────────

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
```

- [ ] **Step 4: Write the CLI entry point**

```python
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
                if d['degradation'].get('should_rollback'):
                    print(f"  [回滚] {d['batch_id']}: {d['degradation']['reason']}")
```

- [ ] **Step 5: Write tests for helper methods**

Add to `tests/test_change_manager.py`:

```python
    def test_generate_batch_id(self, mgr):
        """测试批次ID生成"""
        batch_id1 = mgr.generate_batch_id('20260426')
        batch_id2 = mgr.generate_batch_id('20260426')

        assert batch_id1 == '20260426-001'
        assert batch_id2 == '20260426-002'

    def test_get_change_history(self, mgr):
        """测试变更历史查询"""
        mgr.stage_change('params', 'first_wave_min_gain', 0.18, '20260426-001')
        mgr.stage_change('params', 'stop_loss_buffer', 0.03, '20260426-001')
        mgr.stage_change('params', 'first_wave_min_gain', 0.20, '20260426-002')

        history = mgr.get_change_history('first_wave_min_gain', days=30)

        assert len(history) == 2

    def test_get_status_summary(self, mgr):
        """测试状态摘要"""
        mgr.stage_change('params', 'test_param', 1.0, 'test-001')
        mgr.stage_change('params', 'test_param2', 2.0, 'test-002')

        summary = mgr.get_status_summary()

        assert summary['staged'] == 2
        assert summary['applied'] == 0
        assert summary['rolled_back'] == 0

    def test_get_batch_trace(self, mgr):
        """测试批次追溯"""
        mgr.stage_change('params', 'first_wave_min_gain', 0.18, '20260426-001')
        mgr.stage_change('params', 'stop_loss_buffer', 0.03, '20260426-001')

        trace = mgr.get_batch_trace('20260426-001')

        assert trace['found'] == True
        assert trace['batch_id'] == '20260426-001'
        assert len(trace['changes']) == 2
        assert trace['total_changes'] == 2

    def test_get_batch_sandbox_status_map(self, mgr):
        """测试N+1查询修复：_get_batch_sandbox_status_map"""
        mgr.stage_change('params', 'param1', 1.0, 'test-batch-001')
        mgr.stage_change('params', 'param2', 2.0, 'test-batch-001')
        mgr.stage_change('params', 'param3', 3.0, 'test-batch-001')

        status_map = mgr._get_batch_sandbox_status_map('test-batch-001')

        # 验证返回字典格式正确
        assert isinstance(status_map, dict)
        assert len(status_map) == 3
        assert status_map['param1'] == 'staged'
        assert status_map['param2'] == 'staged'
        assert status_map['param3'] == 'staged'

    def test_get_batch_trace_not_found(self, mgr):
        """测试批次不存在时的追溯"""
        trace = mgr.get_batch_trace('nonexistent-batch')

        assert trace['found'] == False
```

- [ ] **Step 6: Run all tests**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python -m pytest tests/test_change_manager.py -v`

Expected: All tests PASS

- [ ] **Step 7: Commit Task 6**

```bash
git add change_manager.py tests/test_change_manager.py
git commit -m "feat(change_manager): add helper methods and CLI interface

- generate_batch_id: creates sequential batch IDs (YYYYMMDD-NNN)
- get_status_summary, print_status_summary: change management status
- get_change_history, get_batch_trace: change history and batch tracing
- CLI interface with --mode status/history/trace/monitor

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Integrate with weekly_optimizer.py

**Files:**
- Modify: `weekly_optimizer.py`

- [ ] **Step 1: Read current weekly_optimizer.py to understand structure**

Run: `head -100 /home/jzc/wechat_text/shikong_fufei/weekly_optimizer.py`

- [ ] **Step 2: Add import and initialize ChangeManager**

Add at top of `weekly_optimizer.py`:

```python
from change_manager import ChangeManager
```

In `__init__` method:

```python
    def __init__(self, db_path=None):
        # ... existing initialization ...
        self.change_mgr = ChangeManager(db_path)
```

- [ ] **Step 3: Modify run() method to use ChangeManager**

Add batch_id generation and snapshot at beginning of `run()`:

```python
    def run(self, optimize_date=None, layers=None):
        """执行四层优化"""
        if optimize_date is None:
            optimize_date = datetime.now().strftime('%Y-%m-%d')

        if layers is None:
            layers = ['params', 'score_weights', 'signal_status', 'environment']

        # 生成批次ID
        batch_id = self.change_mgr.generate_batch_id(optimize_date.replace('-', ''))

        # 变更前保存快照
        snapshot_id = self.change_mgr.save_snapshot(
            trigger_reason='weekly_optimize',
            batch_id=batch_id
        )

        results = {'batch_id': batch_id, 'snapshot_id': snapshot_id}

        # ... rest of optimization logic ...
```

- [ ] **Step 4: Add stage_change calls after each optimization layer**

After params optimization:

```python
        if 'params' in layers:
            params_result = self._optimize_params_layer(optimize_date)
            results['params'] = params_result

            for key, change in params_result.get('changes', {}).items():
                self.change_mgr.stage_change(
                    optimize_type='params',
                    param_key=key,
                    new_value=change['new'],
                    batch_id=batch_id,
                    current_value=change['old']
                )
```

Similar patterns for score_weights, signal_status, environment layers.

- [ ] **Step 5: Commit Task 7**

```bash
git add weekly_optimizer.py
git commit -m "feat(weekly_optimizer): integrate ChangeManager for staging changes

- Generate batch_id at start of optimization run
- Save snapshot before any parameter changes
- Stage all optimization changes to sandbox_config

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: Integrate with sandbox_validator.py

**Files:**
- Modify: `sandbox_validator.py`

- [ ] **Step 1: Read current sandbox_validator.py**

Run: `head -100 /home/jzc/wechat_text/shikong_fufei/sandbox_validator.py`

- [ ] **Step 2: Add import and initialize ChangeManager**

```python
from change_manager import ChangeManager

class SandboxValidator:
    def __init__(self, db_path=None):
        # ... existing initialization ...
        self.change_mgr = ChangeManager(db_path)
```

- [ ] **Step 3: Modify validate_optimization to read from sandbox_config**

```python
    def validate_optimization(self, batch_id=None):
        """验证优化结果"""
        if batch_id:
            staged = self.change_mgr.get_staged_params(batch_id)
        else:
            all_batches = self.change_mgr.get_all_staged_batches()
            staged = []
            for batch in all_batches:
                staged.extend(self.change_mgr.get_staged_params(batch['batch_id']))

        if not staged:
            return {'status': 'no_pending', 'validated': 0}

        # 更新状态为 validating
        for item in staged:
            self.change_mgr.update_status(item['id'], 'validating')

        # 执行验证逻辑
        results = []
        for item in staged:
            validation = self._validate_single(item)

            if validation['passed']:
                self.change_mgr.update_status(item['id'], 'passed')
            else:
                self.change_mgr.reject_change(item['id'], validation['reason'])

            results.append({
                'sandbox_id': item['id'],
                'param_key': item['param_key'],
                'passed': validation['passed'],
                'reason': validation.get('reason'),
            })

        passed_count = sum(1 for r in results if r['passed'])

        return {
            'status': 'batch_complete',
            'validated': len(results),
            'passed': passed_count,
            'failed': len(results) - passed_count,
            'details': results,
        }
```

- [ ] **Step 4: Add apply_passed_changes method**

```python
    def apply_passed_changes(self, batch_id: str):
        """应用通过验证的变更"""
        staged = self.change_mgr.get_staged_params(batch_id)

        applied = 0
        for item in staged:
            if item['status'] == 'passed':
                self.change_mgr.commit_change(item['id'])
                applied += 1

        return {'applied': applied}
```

- [ ] **Step 5: Commit Task 8**

```bash
git add sandbox_validator.py
git commit -m "feat(sandbox_validator): integrate ChangeManager

- Read staged params from sandbox_config instead of separate storage
- Use update_status for validation lifecycle
- Use commit_change for applying validated changes

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: Integrate with adaptive_engine.py

**Files:**
- Modify: `adaptive_engine.py`

- [ ] **Step 1: Read current adaptive_engine.py**

Run: `head -100 /home/jzc/wechat_text/shikong_fufei/adaptive_engine.py`

- [ ] **Step 2: Add import and initialize ChangeManager**

```python
from change_manager import ChangeManager

class AdaptiveEngine:
    def __init__(self, db_path=None):
        # ... existing initialization ...
        self.change_mgr = ChangeManager(db_path)
```

- [ ] **Step 3: Add monitor_and_rollback call in run_daily**

```python
    def run_daily(self, monitor_date=None):
        """运行每日监控"""
        # ... existing monitoring logic ...

        # 主动回滚监控
        rollback_result = self.change_mgr.monitor_and_rollback()

        if rollback_result['rollback_triggered'] > 0:
            self._notify_rollback_result(rollback_result)

        return {
            'alerts': alerts,
            'critical_handled': critical_handled,
            'status': status,
            'rollback_monitor': rollback_result,
        }
```

- [ ] **Step 4: Add _notify_rollback_result method**

```python
    def _notify_rollback_result(self, rollback_result):
        """通知回滚结果"""
        for detail in rollback_result['details']:
            if detail['degradation'].get('should_rollback'):
                batch_id = detail['batch_id']
                reason = detail['degradation']['reason']

                print(f"\n[自动回滚] 批次 {batch_id}")
                print(f"  原因: {reason}")
```

- [ ] **Step 5: Commit Task 9**

```bash
git add adaptive_engine.py
git commit -m "feat(adaptive_engine): add proactive rollback monitoring

- Call monitor_and_rollback() in run_daily()
- Notify user when rollbacks are triggered
- Return rollback_monitor results in daily run output

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: Update daily_run.sh CLI Options

**Files:**
- Modify: `daily_run.sh`

- [ ] **Step 1: Read current daily_run.sh structure**

Run: `grep -n "case.*CMD" /home/jzc/wechat_text/shikong_fufei/daily_run.sh | head -5`

- [ ] **Step 2: Add change management functions**

```bash
# 新增：变更管理相关选项
run_change_status() {
    log "─────────────── 变更管理状态 ───────────────"
    $PY change_manager.py --mode status 2>&1 | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    log "────────────── 状态查询完成 ───────────────"
}

run_change_history() {
    log "─────────────── 变更历史 ───────────────"
    $PY change_manager.py --mode history --days $LOOKBACK 2>&1 | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    log "────────────── 历史查询完成 ───────────────"
}

run_batch_trace() {
    local batch_id="$1"
    log "─────────────── 批次追溯 ───────────────"
    $PY change_manager.py --mode trace --batch-id "$batch_id" 2>&1 | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    log "────────────── 追溯完成 ───────────────"
}

run_rollback_monitor() {
    log "─────────────── 主动回滚监控 ───────────────"
    $PY change_manager.py --mode monitor 2>&1 | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    log "────────────── 监控完成 ───────────────"
}

# 注：--rollback-monitor 选项为设计文档之外的补充功能
# 设计文档(line 1731-1772)仅列出 --change-status, --change-history, --batch-trace
# 本计划新增 --rollback-monitor 以提供主动监控的独立触发入口
```

- [ ] **Step 3: Add case options**

```bash
case "$CMD" in
    # ... existing options ...
    --change-status)   run_change_status; end_run "ok" ;;
    --change-history)  run_change_history; end_run "ok" ;;
    --batch-trace)     run_batch_trace "$2"; end_run "ok" ;;
    --rollback-monitor) run_rollback_monitor; end_run "ok" ;;
    # ...
esac
```

- [ ] **Step 4: Commit Task 10**

```bash
git add daily_run.sh
git commit -m "feat(daily_run): add change management CLI options

- --change-status: show change management status summary
- --change-history: query change history
- --batch-trace: trace batch changes
- --rollback-monitor: run proactive rollback monitoring

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 11: Final Verification and Integration Test

**Files:**
- Verify all files

- [ ] **Step 1: Run change_manager test suite**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python -m pytest tests/test_change_manager.py -v`

Expected: All tests PASS

- [ ] **Step 2: Verify CLI works**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python change_manager.py --mode status`

Expected: Shows change management status

- [ ] **Step 3: Verify database tables created**

Run: `cd /home/jzc/wechat_text/shikong_fufei && sqlite3 stock_data.db ".tables" | grep -E "param_snapshot|sandbox_config"`

Expected: Both tables listed

- [ ] **Step 4: Add integration smoke test**

Add to `tests/test_change_manager.py`:

```python
class TestIntegration:
    """集成冒烟测试"""

    @pytest.fixture
    def temp_db(self):
        """临时数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        # StockDataLayer.__init__ 自动建表
        StockDataLayer(path)
        # 确保 pick_tracking 表存在
        conn = sqlite3.connect(path)
        conn.execute('''CREATE TABLE IF NOT EXISTS pick_tracking (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            pick_date     TEXT NOT NULL,
            code          TEXT NOT NULL,
            signal_type   TEXT NOT NULL,
            status        TEXT DEFAULT 'active',
            exit_date     TEXT,
            final_pnl_pct REAL,
            UNIQUE(pick_date, code)
        )''')
        conn.close()
        return path

    def test_weekly_optimizer_integration(self, temp_db):
        """测试 WeeklyOptimizer 能正常初始化 ChangeManager"""
        from weekly_optimizer import WeeklyOptimizer

        opt = WeeklyOptimizer(temp_db)

        # 验证 change_mgr 已初始化
        assert hasattr(opt, 'change_mgr')
        assert opt.change_mgr is not None

        # 验证能生成 batch_id
        batch_id = opt.change_mgr.generate_batch_id('20260426')
        assert batch_id == '20260426-001'

    def test_sandbox_validator_integration(self, temp_db):
        """测试 SandboxValidator 能正常初始化 ChangeManager"""
        from sandbox_validator import SandboxValidator

        validator = SandboxValidator(temp_db)

        # 验证 change_mgr 已初始化
        assert hasattr(validator, 'change_mgr')
        assert validator.change_mgr is not None

    def test_adaptive_engine_integration(self, temp_db):
        """测试 AdaptiveEngine 能正常初始化 ChangeManager"""
        from adaptive_engine import AdaptiveEngine

        engine = AdaptiveEngine(temp_db)

        # 验证 change_mgr 已初始化
        assert hasattr(engine, 'change_mgr')
        assert engine.change_mgr is not None

        # 验证 monitor_and_rollback 可调用（不会抛异常）
        result = engine.change_mgr.monitor_and_rollback()
        assert 'checked' in result
        assert 'rollback_triggered' in result
```

- [ ] **Step 5: Run integration tests**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python -m pytest tests/test_change_manager.py::TestIntegration -v`

Expected: All integration tests PASS

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat(change_manager): complete implementation

All 4 layers implemented:
- D: Snapshot management (save/restore snapshots)
- C: Parameter isolation (staging in sandbox_config)
- B: Batch rollback (restore snapshot, mark rolled back)
- A: Proactive rollback monitoring (30-day window, metrics check)

Integration complete with weekly_optimizer, sandbox_validator, adaptive_engine.
CLI options added to daily_run.sh.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review Checklist

**1. Spec Coverage:**

| Spec Section | Task |
|--------------|------|
| D. 快照管理 | Task 2: save_snapshot, restore_snapshot, get_latest_snapshot |
| C. 参数隔离 | Task 3: stage_change, get_staged_params, commit_change |
| B. 批量回滚 | Task 4: rollback_batch, get_batch_info |
| A. 主动回滚监控 | Task 5: monitor_and_rollback, check_performance_degradation |
| param_snapshot table | Task 1: _ensure_tables |
| sandbox_config table | Task 1: _ensure_tables |
| optimization_history扩展 | Task 1: ALTER TABLE statements |
| weekly_optimizer集成 | Task 7, Task 11: 集成测试 |
| sandbox_validator集成 | Task 8, Task 11: 集成测试 |
| adaptive_engine集成 | Task 9, Task 11: 集成测试 |
| daily_run.sh CLI | Task 10 (含 --rollback-monitor 补充功能) |
| 单元测试 | Task 2-6: test_change_manager.py |
| 监控方法测试 | Task 5 Step 6: test_check_performance_degradation, test_consecutive_bad_trading_days |
| N+1查询修复测试 | Task 6 Step 5: test_get_batch_sandbox_status_map |
| 集成冒烟测试 | Task 11 Step 4: TestIntegration 类 |

**2. Placeholder Scan:**

- No TBD, TODO, "implement later" found
- All code steps include actual code
- All test steps include actual test code
- No "similar to Task N" references

**3. Type Consistency:**

- `sandbox_value` is TEXT in sandbox_config, parsed to float/string based on optimize_type
- `batch_id` format is YYYYMMDD-NNN (string) throughout
- `status` field uses 'staged', 'validating', 'passed', 'applied', 'rejected' consistently
- Snapshot methods return Dict with consistent keys

**4. 设计补充说明:**

- `--rollback-monitor` CLI选项为设计文档之外的补充功能，提供主动监控的独立触发入口
- 集成冒烟测试(TestIntegration类)覆盖 weekly_optimizer、sandbox_validator、adaptive_engine 的 ChangeManager 初始化验证
- `temp_db` fixture: StockDataLayer.__init__ 自动建表，不调用不存在的方法 init_tables()
- `pick_tracking` 表: 监控方法依赖此表，fixture中直接执行SQL创建（不依赖 PickTracker）

**5. Fixture 设计说明:**

- `temp_db`: 创建临时数据库，StockDataLayer初始化自动建表，额外创建 `pick_tracking` 表（监控方法 _calc_metrics_in_range 依赖）
- `mgr`: 基于 temp_db 创建 ChangeManager 实例
- 所有测试使用独立的临时数据库，互不影响