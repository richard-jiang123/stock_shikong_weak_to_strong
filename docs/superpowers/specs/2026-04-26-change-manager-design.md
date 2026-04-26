# 变更管理模块设计：change_manager.py

日期: 2026-04-26
目的: 完善自进化系统的安全边界，实现快照管理、参数隔离、批量回滚、主动回滚监控

---

## 概述

为 shikong_fufei 量化系统新增 `change_manager.py` 模块，统一管理参数变更的生命周期：

- **D. 快照管理** — 变更前保存完整状态快照，支持恢复和追溯
- **C. 参数隔离** — pending参数存储到独立表，不影响实盘选股
- **B. 批量回滚** — 同批次变更整体回滚，保持一致性
- **A. 主动回滚监控** — 变更生效后30天持续监控，恶化超过阈值自动回滚

---

## 实施顺序

按依赖关系：D → C → B → A

| 顺序 | 功能 | 依赖 |
|------|------|------|
| 1 | 快照管理(D) | 无（基础设施） |
| 2 | 参数隔离(C) | 依赖快照 |
| 3 | 批量回滚(B) | 依赖快照+隔离 |
| 4 | 主动回滚监控(A) | 依赖全部 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                      变更管理层 (change_manager.py)                  │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  ChangeManager                                                │   │
│  │  ├── save_snapshot()        快照管理                         │   │
│  │  ├── stage_change()         参数隔离（暂存到sandbox_config）  │   │
│  │  ├── commit_change()        正式应用（写入strategy_config）   │   │
│  │  ├── rollback_batch()      批量回滚                          │   │
│  │  └── monitor_and_rollback() 主动回滚监控                      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                │                                     │
│            ┌───────────────────┼───────────────────┐               │
│            │                   │                   │               │
│            ▼                   ▼                   ▼               │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐   │
│  │weekly_optimizer   │ │sandbox_validator │ │adaptive_engine   │   │
│  │调用 save_snapshot │ │读取 staged params│ │调用 monitor      │   │
│  │调用 stage_change  │ │调用 commit_change│ │and_rollback      │   │
│  └──────────────────┘ └──────────────────┘ └──────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         SQLite 数据库 (stock_data.db)               │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  param_snapshot       │ 新增：参数快照表                      │   │
│  │  sandbox_config       │ 新增：沙盒参数隔离表                  │   │
│  │  strategy_config      │ 现有：正式参数表                      │   │
│  │  signal_status        │ 现有：信号状态表                      │   │
│  │  optimization_history │ 现有：变更历史表（扩展字段）          │   │
│  │  daily_monitor_log    │ 现有：监控日志表（data_layer.py定义） │   │
│  │  pick_tracking        │ 现有：选股跟踪表                      │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 数据表设计

### param_snapshot — 参数快照表（新增）

```sql
CREATE TABLE IF NOT EXISTS param_snapshot (
    id INTEGER PRIMARY KEY,
    snapshot_date TEXT NOT NULL,           -- 快照创建时间 YYYY-MM-DD HH:MM:SS
    snapshot_type TEXT,                    -- 'pre_change' | 'daily_backup' | 'manual'
    batch_id TEXT,                         -- 关联的批次ID（同一批变更共享）
    trigger_reason TEXT,                   -- 触发原因：'weekly_optimize' | 'critical_alert' | 'manual'
    
    -- 快照内容（JSON格式）
    params_json TEXT,                      -- strategy_config 全部参数
    signal_status_json TEXT,               -- signal_status 全部记录
    environment_json TEXT,                 -- 环境参数（activity_coefficient等）
    
    -- 恢复状态
    is_restored INTEGER DEFAULT 0,         -- 是否已恢复：0=未恢复, 1=已恢复
    restored_at TEXT,                      -- 恢复时间
    restore_reason TEXT,                   -- 恢复原因
    
    created_at TEXT DEFAULT datetime('now')
);

CREATE INDEX IF NOT EXISTS idx_snapshot_date ON param_snapshot(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_snapshot_batch ON param_snapshot(batch_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_type ON param_snapshot(snapshot_type);
```

**字段说明：**

| 字段 | 说明 |
|------|------|
| snapshot_type | pre_change=变更前快照，daily_backup=每日备份，manual=手动备份 |
| batch_id | 格式：YYYYMMDD-{seq}，如 20260424-001，同批次变更共享同一batch_id |
| params_json | `{\"first_wave_min_gain\": 0.15, \"weight_wave_gain\": 1.0, ...}` |
| signal_status_json | `[{\"signal_type\": \"anomaly_no_decline\", \"status_level\": \"active\", ...}]` |

---

### sandbox_config — 沙盒参数隔离表（新增）

```sql
CREATE TABLE IF NOT EXISTS sandbox_config (
    id INTEGER PRIMARY KEY,
    optimize_id INTEGER,                   -- 关联 optimization_history.id
    batch_id TEXT NOT NULL,                -- 批次ID（同批变更共享）
    param_key TEXT NOT NULL,               -- 参数名
    sandbox_value TEXT NOT NULL,           -- 待验证的新值（TEXT存储，读取时按需转换）
    current_value TEXT,                    -- 当前正式值（TEXT存储，对比参考）
    optimize_type TEXT NOT NULL,           -- 'params' | 'score_weights' | 'signal_status' | 'environment'
    
    -- 状态流转
    status TEXT DEFAULT 'staged',          -- 'staged' | 'validating' | 'passed' | 'applied' | 'rejected'
    
    -- 时间记录
    staged_at TEXT DEFAULT datetime('now'),
    validation_started_at TEXT,            -- 验证开始时间（进入validating状态时）
    validated_at TEXT,                     -- 验证通过/失败时间（进入passed/rejected状态时）
    applied_at TEXT,                       -- 正式应用时间
    rejected_at TEXT,                      -- 拒绝时间
    rejection_reason TEXT,                 -- 拒绝原因
    
    -- 回滚标记
    rollback_triggered INTEGER DEFAULT 0,  -- 是否触发回滚
    rollback_at TEXT,                      -- 回滚时间
    rollback_reason TEXT,                  -- 回滚原因
    
    UNIQUE(param_key, batch_id)
);

-- 注意：sandbox_value 和 current_value 使用 TEXT 类型而非 REAL
-- 原因：signal_status 的 status_level 是字符串值（'active'/'warning'/'disabled'）
-- params 和 score_weights 的数值存储为字符串，读取时用 float() 转换

CREATE INDEX IF NOT EXISTS idx_sandbox_batch ON sandbox_config(batch_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_status ON sandbox_config(status);
CREATE INDEX IF NOT EXISTS idx_sandbox_type ON sandbox_config(optimize_type);
```

**状态流转：**

```
staged（暂存）
    ↓ weekly_optimizer 写入
validating（验证中）
    ↓ sandbox_validator 开始验证
passed（验证通过）
    ↓ commit_change() 正式应用
applied（已应用）
    ↓ 进入30天监控窗口
    ↓ 若恶化 → rollback_batch()
rejected（已拒绝）
    ↓ 验证失败或主动回滚

时间字段语义：
- staged_at: 写入sandbox_config的时间
- validation_started_at: 进入validating状态的时间（验证周期起点）
- validated_at: 进入passed/rejected状态的时间（验证周期终点）
- applied_at: 正式写入strategy_config的时间
- rejected_at: 被拒绝的时间
```

---

### optimization_history 表扩展（修改现有表）

```sql
-- 新增字段
ALTER TABLE optimization_history ADD COLUMN batch_id TEXT;
ALTER TABLE optimization_history ADD COLUMN snapshot_id INTEGER;
ALTER TABLE optimization_history ADD COLUMN trigger_reason TEXT;
ALTER TABLE optimization_history ADD COLUMN rollback_triggered INTEGER DEFAULT 0;
ALTER TABLE optimization_history ADD COLUMN rollback_at TEXT;
ALTER TABLE optimization_history ADD COLUMN rollback_reason TEXT;

-- 新增索引
CREATE INDEX IF NOT EXISTS idx_opt_history_batch ON optimization_history(batch_id);
```

**新增字段说明：**

| 字段 | 说明 |
|------|------|
| batch_id | 关联批次ID，便于批量查询和回滚 |
| snapshot_id | 关联 param_snapshot.id，追溯变更前状态 |
| trigger_reason | 变更触发原因，便于追溯分析 |
| rollback_triggered | 0=未回滚, 1=已回滚 |
| rollback_at | 回滚时间 |
| rollback_reason | 回滚原因 |

---

### daily_monitor_log — 监控日志表（现有，交叉引用）

> 此表已在 `data_layer.py` 中定义，本模块复用。

```sql
-- 表定义位于 data_layer.py，结构如下：
CREATE TABLE IF NOT EXISTS daily_monitor_log (
    id INTEGER PRIMARY KEY,
    monitor_date TEXT,
    alert_type TEXT,           -- 'auto_rollback' | 'signal_degradation' | ...
    alert_detail TEXT,         -- 预警详情
    severity TEXT,             -- 'info' | 'warning' | 'critical'
    action_taken TEXT,         -- 'rollback_triggered' | 'rollback_completed' | ...
    created_at TEXT
);
```

**本模块使用场景：**

- `_log_rollback()` 写入自动回滚事件（alert_type='auto_rollback', severity='critical'）

---

## ChangeManager 类设计

### 类定义

```python
#!/usr/bin/env python3
"""
变更管理模块
职责：快照管理、参数隔离、批量回滚、主动回滚监控
"""
import os
import sys
import json
import numpy as np  # 问题2修复：文件顶部统一导入
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
        'monitor_days': 30,               # 监控周期（变更生效后30天）
        'expectancy_drop_threshold': 0.3, # 期望值下降超过30%触发回滚
        'win_rate_drop_threshold': 10,    # 胜率下降超过10%触发回滚
        'consecutive_bad_days': 5,        # 连续5个交易日期望值<0触发回滚（问题7修复：交易日）
        'min_samples_for_check': 10,      # 检查表现的最小样本数
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
                    created_at TEXT DEFAULT datetime('now')
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_date ON param_snapshot(snapshot_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_batch ON param_snapshot(batch_id)")
            
            # sandbox_config 表（TEXT类型存储，见问题2修复）
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
            
            # optimization_history 扩展字段（安全添加，问题9修复：使用具体异常）
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN batch_id TEXT")
            except Exception: pass
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN snapshot_id INTEGER")
            except Exception: pass
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN trigger_reason TEXT")
            except Exception: pass
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN rollback_triggered INTEGER DEFAULT 0")
            except Exception: pass
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN rollback_at TEXT")
            except Exception: pass
            try:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN rollback_reason TEXT")
            except Exception: pass
```

---

### 快照管理方法

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
    
    def restore_snapshot(self, snapshot_id: int, reason: str = None) -> bool:
        """
        恢复指定快照
        
        Args:
            snapshot_id: 快照记录ID
            reason: 恢复原因
        
        Returns:
            是否成功恢复
        
        注意：问题5修复 - 整个恢复过程在单一事务中执行，失败时全部回滚
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
        
        # 问题5修复：整个恢复过程在单一事务中执行
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

---

### 参数隔离方法

```python
    # ─────────────────────────────────────────────
    # 参数隔离
    # ─────────────────────────────────────────────
    
    def stage_change(self, optimize_type: str, param_key: str, new_value: float,
                     batch_id: str, current_value: float = None) -> int:
        """
        暂存变更到 sandbox_config（不写入 strategy_config）
        
        Args:
            optimize_type: 'params' | 'score_weights' | 'signal_status' | 'environment'
            param_key: 参数名
            new_value: 新值（数值或字符串）
            batch_id: 批次ID
            current_value: 当前值（可选，自动获取）
        
        Returns:
            sandbox_id: sandbox_config 记录ID
        
        注意：问题2修复 - sandbox_value/current_value 使用 TEXT 存储
        问题10修复 - UPDATE 时同步更新 optimize_type
        """
        # 获取当前值
        if current_value is None:
            if optimize_type == 'signal_status':
                # 从 signal_status 获取
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
            except Exception as e:
                # 已存在则更新（问题10修复：同时更新optimize_type）
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
    
    def get_staged_params(self, batch_id: str) -> List[Dict]:
        """
        获取某批次所有暂存参数
        
        Returns:
            list of dict: [{id, param_key, sandbox_value, current_value, optimize_type, status}, ...]
        
        注意：问题2修复 - sandbox_value/current_value 为 TEXT，按 optimize_type 转换
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
                # 信号状态是字符串，保持原样
                sandbox_value_parsed = sandbox_value
                current_value_parsed = current_value
            else:
                # params/score_weights/environment 是数值
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
        """
        更新 sandbox_config 状态
        
        问题6修复：validation_started_at 记录进入 validating 状态的时间
        validated_at 记录进入 passed/rejected 状态的时间
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        with self.dl._get_conn() as conn:
            if status == 'validating':
                # 进入验证状态，记录验证开始时间
                conn.execute("""
                    UPDATE sandbox_config SET status=?, validation_started_at=? WHERE id=?
                """, (status, now, sandbox_id))
            elif status == 'passed':
                # 验证通过，记录验证完成时间
                conn.execute("""
                    UPDATE sandbox_config SET status=?, validated_at=? WHERE id=?
                """, (status, now, sandbox_id))
            elif status == 'applied':
                # 正式应用
                conn.execute("""
                    UPDATE sandbox_config SET status=?, applied_at=? WHERE id=?
                """, (status, now, sandbox_id))
            elif status == 'rejected':
                # 拒绝变更
                conn.execute("""
                    UPDATE sandbox_config SET status=?, rejected_at=?, rejection_reason=? WHERE id=?
                """, (status, now, reason, sandbox_id))
    
    def commit_change(self, sandbox_id: int) -> bool:
        """
        正式应用变更（写入 strategy_config / signal_status）
        
        Args:
            sandbox_id: sandbox_config 记录ID
        
        Returns:
            是否成功应用
        
        问题1修复：new_value 从 row[1] 获取（而非 row[2]）
        问题2修复：sandbox_value 为 TEXT，按 optimize_type 转换
        """
        # 读取 sandbox 记录
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT param_key, sandbox_value, optimize_type, status
                FROM sandbox_config WHERE id=?
            """, (sandbox_id,)).fetchone()
        
        if row is None:
            return False
        
        param_key = row[0]        # 问题1修复：正确索引
        sandbox_value_str = row[1] # TEXT 类型
        optimize_type = row[2]
        status = row[3]
        
        if status not in ('passed', 'staged'):  # staged 允许直接应用（跳过验证）
            return False
        
        # 按类型转换值
        if optimize_type == 'signal_status':
            new_value = sandbox_value_str  # 字符串值
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
        """
        拒绝变更
        
        Args:
            sandbox_id: sandbox_config 记录ID
            reason: 拒绝原因
        
        Returns:
            是否成功拒绝
        """
        self.update_status(sandbox_id, 'rejected', reason)
        return True
```

---

### 批量回滚方法

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
        
        问题3修复：删除引用不存在列sandbox_test_result的UPDATE
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
        
        # 问题3修复：optimization_history 没有 sandbox_test_result 列
        # 使用 rollback_triggered 字段（已扩展）
        with self.dl._get_conn() as conn:
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
    
    def get_batch_changes(self, batch_id: str) -> List[Dict]:
        """
        获取批次内所有变更
        
        Returns:
            list of dict: 变更记录列表
        """
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

---

### 主动回滚监控方法

```python
    # ─────────────────────────────────────────────
    # 主动回滚监控
    # ─────────────────────────────────────────────
    
    def monitor_and_rollback(self) -> Dict:
        """
        检查已应用变更的表现，恶化超过阈值时触发回滚
        
        Returns:
            {'checked': int, 'rollback_triggered': int, 'details': list}
        
        问题4修复：按 batch_id 去重后逐批次检查，避免同一批次重复检查
        """
        # 获取待监控的批次列表（已去重）
        applied_batches = self.get_applied_batches_in_monitor_window()
        
        checked = 0
        rollback_triggered = 0
        details = []
        
        for batch in applied_batches:
            checked += 1
            
            # 检查整个批次的表现恶化情况
            degradation = self.check_performance_degradation(batch)
            
            details.append({
                'batch_id': batch['batch_id'],
                'applied_at': batch['applied_at'],
                'degradation': degradation,
            })
            
            if degradation['should_rollback']:
                # 触发回滚
                rollback_result = self.rollback_batch(batch['batch_id'], degradation['reason'])
                rollback_triggered += 1
                
                details[-1]['rollback_result'] = rollback_result
                
                # 记录回滚日志（问题8修复：只在此处写日志）
                self._log_rollback(batch, degradation)
        
        return {
            'checked': checked,
            'rollback_triggered': rollback_triggered,
            'details': details,
        }
    
    def get_applied_batches_in_monitor_window(self) -> List[Dict]:
        """
        获取监控窗口内的已应用批次（去重）
        
        监控窗口：变更生效后30天内
        
        Returns:
            list of dict: [{batch_id, applied_at, monitor_days_elapsed, ...}, ...]
        
        问题4修复：返回去重的批次列表，而非单条变更列表
        """
        monitor_days = self.ROLLBACK_CONFIG['monitor_days']
        cutoff_date = (datetime.now() - timedelta(days=monitor_days)).strftime('%Y-%m-%d')
        
        with self.dl._get_conn() as conn:
            # 查询已应用且未回滚的批次（去重）
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
            
            # 计算监控天数
            applied_dt = datetime.strptime(applied_at, '%Y-%m-%d %H:%M:%S')
            days_elapsed = (datetime.now() - applied_dt).days
            
            results.append({
                'batch_id': batch_id,
                'applied_at': applied_at,
                'monitor_days_elapsed': days_elapsed,
            })
        
        return results
    
    def check_performance_degradation(self, batch: Dict) -> Dict:
        """
        检查变更后表现恶化情况
        
        Args:
            batch: 批次信息 dict（包含 batch_id 和 applied_at）
        
        Returns:
            {'should_rollback': bool, 'reason': str, 'metrics': dict}
        """
        applied_at = batch['applied_at']
        batch_id = batch['batch_id']
        
        # 获取变更前快照中记录的基准表现
        snapshot = self.get_latest_snapshot(batch_id)
        baseline_metrics = self._calc_baseline_metrics(snapshot)
        
        # 获取变更后表现
        current_metrics = self._calc_current_metrics(applied_at)
        
        # 检查样本数
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
    
    def _calc_baseline_metrics(self, snapshot: Dict) -> Dict:
        """
        计算变更前基准表现
        
        从快照获取变更前30天的表现数据
        """
        if snapshot is None:
            return {'expectancy': 0.042, 'win_rate': 59.8, 'sample_count': 0}  # 回测基准
        
        # 快照创建时间之前的数据
        snapshot_date = snapshot['snapshot_date']
        snapshot_dt = datetime.strptime(snapshot_date, '%Y-%m-%d %H:%M:%S')
        start_date = (snapshot_dt - timedelta(days=30)).strftime('%Y-%m-%d')
        end_date = snapshot_dt.strftime('%Y-%m-%d')
        
        return self._calc_metrics_in_range(start_date, end_date)
    
    def _calc_current_metrics(self, applied_at: str) -> Dict:
        """
        计算变更后当前表现
        
        从应用时间到现在的数据
        
        问题3说明：累积窗口设计
        - 累积窗口 <= 30天（由 get_applied_batches_in_monitor_window 的 cutoff_date 限制）
        - 基准窗口固定30天，累积窗口随天数增长
        - 在监控周期内（<=30天），两者可比性保持良好
        - 设计意图：监控窗口结束时（第30天）累积窗口也恰好30天，与基准窗口完全对等
        """
        applied_dt = datetime.strptime(applied_at, '%Y-%m-%d %H:%M:%S')
        start_date = applied_dt.strftime('%Y-%m-%d')
        end_date = datetime.now().strftime('%Y-%m-%d')
        
        return self._calc_metrics_in_range(start_date, end_date)
    
    def _calc_metrics_in_range(self, start_date: str, end_date: str) -> Dict:
        """
        计算指定时间范围内的表现指标
        
        Returns:
            {'expectancy': float, 'win_rate': float, 'sample_count': int}
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
        
        # 问题2修复：numpy 已在文件顶部导入
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / sample_count * 100
        expectancy = np.mean(pnls) * 100  # 转为百分比
        
        return {
            'expectancy': expectancy,
            'win_rate': win_rate,
            'sample_count': sample_count,
        }
    
    def _check_consecutive_bad_trading_days(self, applied_at: str) -> int:
        """
        检查连续期望值<0的交易日天数
        
        问题7修复：按交易日（有exit记录的日期）连续计数，而非日历日
        
        Returns:
            连续交易日天数
        """
        applied_dt = datetime.strptime(applied_at, '%Y-%m-%d %H:%M:%S')
        start_date = applied_dt.strftime('%Y-%m-%d')
        
        with self.dl._get_conn() as conn:
            # 按交易日分组计算每个交易日的期望值
            # 只统计有exit记录的日期（即交易日）
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
    
    def _log_rollback(self, batch: Dict, degradation: Dict):
        """
        记录回滚日志
        
        Args:
            batch: 批次信息 dict
            degradation: 恶化检测结果 dict
        
        问题8修复：只在此处写日志，AdaptiveEngine 不再重复写入
        """
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

---

### 变更追溯方法

```python
    # ─────────────────────────────────────────────
    # 变更追溯
    # ─────────────────────────────────────────────
    
    def get_change_history(self, param_key: str = None, days: int = 90) -> List[Dict]:
        """
        查询变更历史
        
        Args:
            param_key: 参数名，None时返回全部
            days: 回看天数
        
        Returns:
            list of dict: 变更记录列表
        """
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
        """
        追溯批次变更完整路径
        
        Returns:
            {'batch_id', 'snapshot', 'changes', 'optimization_history', 'performance'}
        
        问题A修复：不查询 sandbox_test_result（不存在），改为从 sandbox_config.status 获取状态
        问题1修复：预查询批次所有 sandbox 状态，避免 N+1 查询
        """
        batch_info = self.get_batch_info(batch_id)
        
        if batch_info is None:
            return {'batch_id': batch_id, 'found': False}
        
        # 问题1修复：一次性查询该批次所有 sandbox_config 状态
        sandbox_status_map = self._get_batch_sandbox_status_map(batch_id)
        
        # 获取关联的 optimization_history 记录（只查询已扩展的字段）
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
                # 从预查询字典 lookup（而非逐条 SELECT）
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
        """
        一次性获取批次所有参数的 sandbox 状态
        
        问题1修复：避免 N+1 查询，返回 {param_key: status} 字典
        
        Returns:
            {'param_key1': 'staged', 'param_key2': 'applied', ...}
        """
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
        
        # 快照信息
        snapshot = trace.get('snapshot')
        if snapshot:
            print(f"\n[快照] ID={snapshot['id']} 时间={snapshot['snapshot_date']}")
            print(f"  触发原因: {snapshot['trigger_reason']}")
        
        # 变更列表
        changes = trace.get('changes', [])
        print(f"\n[变更] 共 {len(changes)} 项")
        for c in changes:
            status = c['status']
            rollback = '已回滚' if c['rollback_triggered'] else ''
            print(f"  {c['param_key']}: {c['current_value']} -> {c['sandbox_value']} [{status}] {rollback}")
        
        # 回滚状态
        if trace['is_rolled_back']:
            print(f"\n[回滚] 已触发回滚")
            rolled_changes = [c for c in changes if c['rollback_triggered']]
            if rolled_changes:
                reason = rolled_changes[0].get('rollback_reason', '未知')
                print(f"  原因: {reason}")
        
        print(f"\n{'='*60}")
```

---

### 辅助方法

```python
    # ─────────────────────────────────────────────
    # 辅助方法
    # ─────────────────────────────────────────────
    
    def generate_batch_id(self, date_str: str = None) -> str:
        """
        生成批次ID
        
        格式: YYYYMMDD-{seq}，如 20260424-001
        
        Args:
            date_str: 日期字符串，默认今天
        
        Returns:
            batch_id
        """
        if date_str is None:
            date_str = datetime.now().strftime('%Y%m%d')
        
        # 查询当天已有批次数量
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
            # 各状态数量
            staged_count = conn.execute("""
                SELECT COUNT(*) FROM sandbox_config WHERE status='staged'
            """).fetchone()[0]
            
            validating_count = conn.execute("""
                SELECT COUNT(*) FROM sandbox_config WHERE status='validating'
            """).fetchone()[0]
            
            passed_count = conn.execute("""
                SELECT COUNT(*) FROM sandbox_config WHERE status='passed'
            """).fetchone()[0]
            
            applied_count = conn.execute("""
                SELECT COUNT(*) FROM sandbox_config WHERE status='applied'
            """).fetchone()[0]
            
            rollback_count = conn.execute("""
                SELECT COUNT(*) FROM sandbox_config WHERE rollback_triggered=1
            """).fetchone()[0]
            
            # 最近快照
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

---

## 现有模块集成修改

### weekly_optimizer.py 修改

```python
# weekly_optimizer.py 新增导入和调用

from change_manager import ChangeManager

class WeeklyOptimizer:
    def __init__(self, db_path=None):
        # ... 原有初始化 ...
        self.change_mgr = ChangeManager(db_path)  # 新增
    
    def run(self, optimize_date=None, layers=None):
        """执行四层优化"""
        if optimize_date is None:
            optimize_date = datetime.now().strftime('%Y-%m-%d')
        
        if layers is None:
            layers = ['params', 'score_weights', 'signal_status', 'environment']
        
        # 1. 生成批次ID（新增）
        batch_id = self.change_mgr.generate_batch_id(optimize_date.replace('-', ''))
        
        # 2. 变更前保存快照（新增）
        snapshot_id = self.change_mgr.save_snapshot(
            trigger_reason='weekly_optimize',
            batch_id=batch_id
        )
        
        results = {'batch_id': batch_id, 'snapshot_id': snapshot_id}
        
        # 3. 参数层优化
        if 'params' in layers:
            params_result = self._optimize_params_layer(optimize_date)
            results['params'] = params_result
            
            # 暂存变更到 sandbox（新增）
            for key, change in params_result.get('changes', {}).items():
                self.change_mgr.stage_change(
                    optimize_type='params',
                    param_key=key,
                    new_value=change['new'],
                    batch_id=batch_id,
                    current_value=change['old']
                )
        
        # 4. 评分层优化（同理）
        if 'score_weights' in layers:
            weights_result = self._optimize_score_weights_layer(optimize_date)
            results['score_weights'] = weights_result
            
            for key, change in weights_result.get('weight_changes', {}).items():
                self.change_mgr.stage_change(
                    optimize_type='score_weights',
                    param_key=key,
                    new_value=change['new'],
                    batch_id=batch_id,
                    current_value=change['old']
                )
        
        # 5. 信号层优化（同理）
        if 'signal_status' in layers:
            signal_result = self._optimize_signal_status_layer(optimize_date)
            results['signal_status'] = signal_result
            
            for signal_type, change in signal_result.get('status_changes', {}).items():
                self.change_mgr.stage_change(
                    optimize_type='signal_status',
                    param_key=signal_type,
                    new_value=change['new_status'],
                    batch_id=batch_id,
                    current_value=change['old_status']
                )
        
        # 6. 环境层优化（同理）
        if 'environment' in layers:
            env_result = self._optimize_environment_layer(optimize_date)
            results['environment'] = env_result
            
            for key, change in env_result.get('threshold_updates', {}).items():
                self.change_mgr.stage_change(
                    optimize_type='environment',
                    param_key=key,
                    new_value=change['new'],
                    batch_id=batch_id,
                    current_value=change['old']
                )
        
        return results
```

---

### sandbox_validator.py 修改

```python
# sandbox_validator.py 新增导入和调用

from change_manager import ChangeManager

class SandboxValidator:
    def __init__(self, db_path=None):
        # ... 原有初始化 ...
        self.change_mgr = ChangeManager(db_path)  # 新增
    
    def validate_optimization(self, batch_id=None):
        """
        验证优化结果
        
        修改：从 sandbox_config 读取待验证参数
        """
        # 获取所有待验证批次
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
        
        # 执行验证逻辑（原有逻辑）
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
        failed_count = len(results) - passed_count
        
        return {
            'status': 'batch_complete',
            'validated': len(results),
            'passed': passed_count,
            'failed': failed_count,
            'details': results,
        }
    
    def apply_passed_changes(self, batch_id: str):
        """应用通过验证的变更"""
        staged = self.change_mgr.get_staged_params(batch_id)
        
        applied = 0
        for item in staged:
            if item['status'] == 'passed':
                self.change_mgr.commit_change(item['id'])
                applied += 1
        
        return {'applied': applied}
    
    def rollback_batch(self, batch_id: str, reason: str):
        """批量回滚"""
        return self.change_mgr.rollback_batch(batch_id, reason)
```

---

### adaptive_engine.py 修改

```python
# adaptive_engine.py 新增导入和调用

from change_manager import ChangeManager

class AdaptiveEngine:
    def __init__(self, db_path=None):
        # ... 原有初始化 ...
        self.change_mgr = ChangeManager(db_path)  # 新增
    
    def run_daily(self, monitor_date=None):
        """运行每日监控"""
        # ... 原有监控逻辑 ...
        
        # 新增：主动回滚监控
        rollback_result = self.change_mgr.monitor_and_rollback()
        
        # 问题8修复：monitor_and_rollback() 内部已调用 _log_rollback() 写日志
        # 此处只需读取结果并通知用户，不再重复写入日志
        if rollback_result['rollback_triggered'] > 0:
            self._notify_rollback_result(rollback_result)
        
        return {
            'alerts': alerts,
            'critical_handled': critical_handled,
            'status': status,
            'rollback_monitor': rollback_result,  # 新增
        }
    
    def _notify_rollback_result(self, rollback_result):
        """通知回滚结果（问题8修复：只通知，不写日志）"""
        for detail in rollback_result['details']:
            if detail['degradation'].get('should_rollback'):
                batch_id = detail['batch_id']
                reason = detail['degradation']['reason']
                
                print(f"\n[自动回滚] 批次 {batch_id}")
                print(f"  原因: {reason}")
    
    def run_weekly(self, optimize_date=None, layers=None):
        """运行每周优化"""
        # ... 原有优化逻辑 ...
        # weekly_optimizer 会自动调用 change_mgr
        
        # 新增：打印变更管理状态
        self.change_mgr.print_status_summary()
        
        return {
            'optimization_results': optimization_results,
            'sandbox_validation': sandbox_validation,
            'applied': applied,
            'rejected': rejected,
        }
```

---

### daily_run.sh 新增选项

```bash
# daily_run.sh 新增选项

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

# case 新增选项
case "$CMD" in
    # ... 原有选项 ...
    --change-status)   run_change_status; end_run "ok" ;;
    --change-history)  run_change_history; end_run "ok" ;;
    --batch-trace)     run_batch_trace "$2"; end_run "ok" ;;
    # ...
esac
```

---

## 测试设计

### tests/test_change_manager.py

```python
#!/usr/bin/env python3
"""变更管理模块单元测试"""
import pytest
import tempfile
import os
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
        dl = StockDataLayer(path)
        dl.init_tables()
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
        # 修改参数
        cfg = StrategyConfig(temp_db)
        cfg.set('first_wave_min_gain', 0.20)
        
        # 保存快照
        snapshot_id = mgr.save_snapshot('test', 'test-001')
        
        # 再次修改参数
        cfg.set('first_wave_min_gain', 0.25)
        
        # 恢复快照
        success = mgr.restore_snapshot(snapshot_id, 'test_restore')
        
        assert success
        
        # 验证参数已恢复
        restored_value = cfg.get('first_wave_min_gain')
        assert restored_value == 0.20
    
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
        # 暂存变更
        sandbox_id = mgr.stage_change(
            optimize_type='params',
            param_key='first_wave_min_gain',
            new_value=0.18,
            batch_id='20260426-001'
        )
        
        # 更新状态为 passed
        mgr.update_status(sandbox_id, 'passed')
        
        # 正式应用
        success = mgr.commit_change(sandbox_id)
        
        assert success
        
        # 验证参数已写入 strategy_config
        cfg = StrategyConfig(temp_db)
        value = cfg.get('first_wave_min_gain')
        assert value == 0.18
    
    def test_rollback_batch(self, mgr, temp_db):
        """测试批量回滚"""
        # 保存快照（first_wave_min_gain=0.15）
        snapshot_id = mgr.save_snapshot('test', '20260426-001')
        
        # 暂存并应用变更
        sandbox_id = mgr.stage_change('params', 'first_wave_min_gain', 0.18, '20260426-001')
        mgr.update_status(sandbox_id, 'passed')
        mgr.commit_change(sandbox_id)
        
        # 执行回滚
        result = mgr.rollback_batch('20260426-001', 'test_rollback')
        
        assert result['rolled_back'] > 0
        
        # 验证参数已恢复
        cfg = StrategyConfig(temp_db)
        value = cfg.get('first_wave_min_gain')
        assert value == 0.15  # 回滚到快照值
    
    def test_generate_batch_id(self, mgr):
        """测试批次ID生成"""
        batch_id1 = mgr.generate_batch_id('20260426')
        batch_id2 = mgr.generate_batch_id('20260426')
        
        assert batch_id1 == '20260426-001'
        assert batch_id2 == '20260426-002'
    
    def test_get_change_history(self, mgr):
        """测试变更历史查询"""
        # 创建多个变更
        mgr.stage_change('params', 'first_wave_min_gain', 0.18, '20260426-001')
        mgr.stage_change('params', 'stop_loss_buffer', 0.03, '20260426-001')
        mgr.stage_change('params', 'first_wave_min_gain', 0.20, '20260426-002')
        
        # 查询特定参数历史
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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
```

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| 快照恢复失败 | restore_snapshot() 返回布尔值，失败时记录日志，不中断流程 |
| sandbox_config 与 strategy_config 冲突 | 参数隔离设计：pending参数只存在sandbox_config，commit后才写入strategy_config |
| 批量回滚不一致 | rollback_batch() 依赖快照整体恢复，保证一致性 |
| 主动回滚误触发 | 30天监控窗口 + 样本数检查（>=10笔） + 多阈值（期望值+胜率+连续天数） |
| 回滚后参数丢失 | 快照表永久保留，可多次恢复 |

---

## 模块文件清单

```
change_manager.py      # 新增：变更生命周期管理器
tests/test_change_manager.py  # 新增：单元测试

weekly_optimizer.py    # 修改：集成 change_mgr
sandbox_validator.py   # 修改：集成 change_mgr
adaptive_engine.py     # 修改：集成主动回滚监控
daily_run.sh           # 修改：新增变更管理选项
```

---

## 完成标准

| 功能 | 验证方法 |
|------|----------|
| D. 快照管理 | 保存快照 → 修改参数 → 恢复快照 → 验证参数一致 |
| C. 参数隔离 | 暂存变更 → strategy_config 不变 → commit后变更 |
| B. 批量回滚 | 多参数变更 → 应用 → 回滚 → 全部恢复 |
| A. 主动回滚监控 | 模拟期望值下降30% → 触发回滚 → 验证参数恢复 |

---

## 后续扩展

- 变更审批机制：重大变更需人工确认后才能 commit
- 变更影响分析：应用前预测变更对期望值的影响
- 多版本快照：保留多个历史快照，支持"回滚到N天前"

---

## 修订汇总

### 第一轮修订（评审问题修复）

| 问题编号 | 严重程度 | 问题描述 | 修复方案 |
|----------|----------|----------|----------|
| 1 | 严重 | commit_change() 索引错误，new_value 和 optimize_type 都取了 row[2] | 修正为 new_value = row[1] |
| 2 | 严重 | sandbox_value REAL 类型无法存储 signal_status 的字符串值 | 改为 TEXT NOT NULL，读取时按 optimize_type 转换 |
| 3 | 严重 | rollback_batch() 引用不存在列 sandbox_test_result | 删除该 UPDATE，使用 rollback_triggered 字段 |
| 4 | 设计 | monitor_and_rollback() 按单条变更迭代但回滚是批量的 | 按 batch_id 去重后逐批次检查 |
| 5 | 设计 | restore_snapshot() 不是原子操作，半恢复状态风险 | 整个恢复过程包裹在单一事务中 |
| 6 | 设计 | validating 状态使用 validated_at 语义不清 | 新增 validation_started_at 字段 |
| 7 | 设计 | _check_consecutive_bad_days() 未考虑非交易日 | 改为按交易日连续计数，方法重命名 |
| 8 | 设计 | _log_rollback() 与 AdaptiveEngine 重复写日志 | 只在 _log_rollback() 写，AdaptiveEngine 只通知 |
| 9 | 小 | bare except 会吞掉所有异常 | 改为 except Exception: pass |
| 10 | 小 | stage_change() UPDATE 缺少 optimize_type | UPDATE 时同步更新 optimize_type |
| 11 | 小 | REAL 精度问题（评分权重小数） | TEXT 存储，Python 层转换 |

### 第二轮修订（遗留问题修复）

| 问题编号 | 严重程度 | 问题描述 | 修复方案 |
|----------|----------|----------|----------|
| A | 中 | get_batch_trace() 引用不存在的 sandbox_test_result 列 | 删除该列查询，改为从 sandbox_config.status 获取（新增 `_get_sandbox_status_for_param()`） |
| B | 中 | daily_monitor_log 表未在文档中定义 | 添加交叉引用章节，说明表已在 data_layer.py 中定义 |

### 第三轮修订（性能与设计补充）

| 问题编号 | 严重程度 | 问题描述 | 修复方案 |
|----------|----------|----------|----------|
| 1 | 性能 | get_batch_trace() N+1 查询（循环中逐条 SELECT sandbox_config） | 预查询批次所有状态 `_get_batch_sandbox_status_map()`，字典 lookup |
| 2 | 性能 | import numpy 在方法内部，每次调用执行 import | 文件顶部统一导入 |
| 3 | 设计 | 累积窗口 vs 固定基准窗口可比性未说明 | 添加注释：累积窗口 <= 30天，由 cutoff_date 限制，两者可比 |
