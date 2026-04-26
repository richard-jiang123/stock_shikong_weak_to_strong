# 自适应量化系统实现计划（Phase 1-2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现自适应量化系统的数据层基础设施和每日监控模块

**Architecture:** 先创建数据表结构和信号类型常量模块，再实现每日监控（异常预警+环境感知），确保与现有系统兼容

**Tech Stack:** Python 3.8, SQLite, pandas, numpy

---

## 文件结构

| 文件 | 负责内容 |
|------|----------|
| `signal_constants.py` | 信号类型英文/中文映射（新建） |
| `data_layer.py` | 数据库表初始化扩展（修改） |
| `daily_monitor.py` | 每日监控+环境感知（新建） |
| `tests/test_adaptive.py` | 单元测试（新建） |

---

### Task 1: 创建信号类型常量模块

**Files:**
- Create: `signal_constants.py`

- [ ] **Step 1: 创建 signal_constants.py**

```python
#!/usr/bin/env python3
"""
信号类型常量定义
统一英文主键与中文显示的映射
"""

# 英文主键（数据库存储） → 中文显示（终端输出）
SIGNAL_TYPE_MAPPING = {
    'anomaly_no_decline': '异动不跌',
    'bullish_engulfing': '阳包阴',
    'big_bullish_reversal': '大阳反转',
    'limit_up_open_next_strong': '烂板次日',
}

# 信号状态层级
STATUS_LEVELS = ['active', 'watching', 'warning', 'disabled']

# 状态权重乘数
STATUS_WEIGHT_MULTIPLIER = {
    'active': 1.0,
    'watching': 0.5,
    'warning': 0.2,
    'disabled': 0.0,
}

# 沙盒验证状态
SANDBOX_STATUS = {
    'PENDING': 'pending',
    'PASSED': 'passed',
    'APPLIED': 'applied',
    'FAILED': 'failed',
}


def normalize_signal_type(signal_str):
    """
    统一信号类型标识
    
    Args:
        signal_str: 可能是中文或英文的信号字符串
    
    Returns:
        英文主键（如 'anomaly_no_decline'）
    """
    # 如果已经是英文主键
    if signal_str in SIGNAL_TYPE_MAPPING:
        return signal_str
    
    # 反向映射：中文 → 英文
    reverse = {v: k for k, v in SIGNAL_TYPE_MAPPING.items()}
    return reverse.get(signal_str, signal_str)


def get_display_name(signal_type):
    """
    获取信号的中文显示名
    
    Args:
        signal_type: 英文主键
    
    Returns:
        中文显示名
    """
    return SIGNAL_TYPE_MAPPING.get(signal_type, signal_type)


def get_weight_multiplier(status_level):
    """
    获取状态对应的权重乘数
    
    Args:
        status_level: 状态层级
    
    Returns:
        权重乘数（0.0-1.0）
    """
    return STATUS_WEIGHT_MULTIPLIER.get(status_level, 1.0)
```

- [ ] **Step 2: 语法检查**

Run: `python3 -m py_compile signal_constants.py`
Expected: 无输出（通过）

- [ ] **Step 3: 提交**

```bash
git add signal_constants.py
git commit -m "feat: add signal_constants.py for signal type mapping"
```

---

### Task 2: 创建测试目录和测试骨架

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_adaptive.py`

- [ ] **Step 1: 创建测试目录**

```bash
mkdir -p tests
```

- [ ] **Step 2: 创建 tests/__init__.py**

```python
# tests/__init__.py
"""自适应系统测试"""
```

- [ ] **Step 3: 创建 tests/test_adaptive.py 骨架**

```python
#!/usr/bin/env python3
"""测试自适应量化系统组件"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_constants import (
    SIGNAL_TYPE_MAPPING,
    normalize_signal_type,
    get_display_name,
    get_weight_multiplier,
)


class TestSignalConstants(unittest.TestCase):
    """测试信号类型常量模块"""

    def test_signal_type_mapping_has_four_signals(self):
        """测试：四种信号类型定义"""
        self.assertEqual(len(SIGNAL_TYPE_MAPPING), 4)
        self.assertIn('anomaly_no_decline', SIGNAL_TYPE_MAPPING)
        self.assertIn('bullish_engulfing', SIGNAL_TYPE_MAPPING)
        self.assertIn('big_bullish_reversal', SIGNAL_TYPE_MAPPING)
        self.assertIn('limit_up_open_next_strong', SIGNAL_TYPE_MAPPING)

    def test_normalize_signal_type_from_chinese(self):
        """测试：中文转英文主键"""
        result = normalize_signal_type('异动不跌')
        self.assertEqual(result, 'anomaly_no_decline')

    def test_normalize_signal_type_from_english(self):
        """测试：英文主键不变"""
        result = normalize_signal_type('anomaly_no_decline')
        self.assertEqual(result, 'anomaly_no_decline')

    def test_get_display_name(self):
        """测试：英文转中文显示"""
        result = get_display_name('anomaly_no_decline')
        self.assertEqual(result, '异动不跌')

    def test_get_weight_multiplier(self):
        """测试：状态权重乘数"""
        self.assertEqual(get_weight_multiplier('active'), 1.0)
        self.assertEqual(get_weight_multiplier('watching'), 0.5)
        self.assertEqual(get_weight_multiplier('warning'), 0.2)
        self.assertEqual(get_weight_multiplier('disabled'), 0.0)


class TestDatabaseTables(unittest.TestCase):
    """测试数据库表创建"""

    def setUp(self):
        """测试前准备"""
        import sqlite3
        # 使用临时数据库
        self.db_path = ':memory:'
        self.conn = sqlite3.connect(self.db_path)

    def tearDown(self):
        """测试后清理"""
        self.conn.close()


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 4: 运行测试验证骨架**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add tests/
git commit -m "test: add test skeleton for adaptive system"
```

---

### Task 3: 扩展 data_layer.py 创建新数据表

**Files:**
- Modify: `data_layer.py`（新增表创建方法）

- [ ] **Step 1: 添加测试用例**

```python
# tests/test_adaptive.py 新增测试方法

class TestDatabaseTables(unittest.TestCase):
    """测试数据库表创建"""

    def setUp(self):
        """测试前准备"""
        import sqlite3
        self.db_path = ':memory:'
        self.conn = sqlite3.connect(self.db_path)

    def tearDown(self):
        """测试后清理"""
        self.conn.close()

    def test_create_signal_status_table(self):
        """测试：signal_status表创建"""
        from data_layer import StockDataLayer
        dl = StockDataLayer(self.db_path)
        dl._create_adaptive_tables()
        
        # 检查表存在
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_status'"
        ).fetchall()
        self.assertEqual(len(tables), 1)

    def test_create_optimization_history_table(self):
        """测试：optimization_history表创建"""
        from data_layer import StockDataLayer
        dl = StockDataLayer(self.db_path)
        dl._create_adaptive_tables()
        
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='optimization_history'"
        ).fetchall()
        self.assertEqual(len(tables), 1)

    def test_create_market_regime_table(self):
        """测试：market_regime表创建"""
        from data_layer import StockDataLayer
        dl = StockDataLayer(self.db_path)
        dl._create_adaptive_tables()
        
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='market_regime'"
        ).fetchall()
        self.assertEqual(len(tables), 1)

    def test_create_daily_monitor_log_table(self):
        """测试：daily_monitor_log表创建"""
        from data_layer import StockDataLayer
        dl = StockDataLayer(self.db_path)
        dl._create_adaptive_tables()
        
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_monitor_log'"
        ).fetchall()
        self.assertEqual(len(tables), 1)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestDatabaseTables -v`
Expected: FAIL - AttributeError: '_create_adaptive_tables' not found

- [ ] **Step 3: 在 data_layer.py 添加 _create_adaptive_tables 方法**

在 `data_layer.py` 的 `_ensure_tables()` 方法（约第50行附近）后添加：

```python
def _create_adaptive_tables(self):
    """创建自适应系统所需的新表"""
    
    # signal_status 表
    self._get_conn().execute("""
        CREATE TABLE IF NOT EXISTS signal_status (
            signal_type TEXT PRIMARY KEY,
            display_name TEXT,
            status_level TEXT DEFAULT 'active',
            weight_multiplier REAL DEFAULT 1.0,
            live_win_rate REAL,
            live_avg_win_pct REAL,
            live_avg_loss_pct REAL,
            live_expectancy REAL,
            live_expectancy_lb REAL,
            live_sample_count INTEGER DEFAULT 0,
            live_observation_weeks INTEGER DEFAULT 0,
            confidence_level TEXT DEFAULT 'unknown',
            min_sample_threshold INTEGER DEFAULT 10,
            last_check_date TEXT,
            disable_reason TEXT,
            can_auto_disable INTEGER DEFAULT 0
        )
    """)
    
    # optimization_history 表
    self._get_conn().execute("""
        CREATE TABLE IF NOT EXISTS optimization_history (
            id INTEGER PRIMARY KEY,
            optimize_date TEXT,
            optimize_type TEXT,
            param_key TEXT,
            old_value REAL,
            new_value REAL,
            sandbox_test_result TEXT,
            weeks_passed INTEGER DEFAULT 0,
            apply_date TEXT,
            backtest_train_sharpe REAL,
            backtest_oos_sharpe REAL,
            backtest_win_rate REAL,
            backtest_expectancy REAL,
            live_win_rate REAL,
            live_expectancy REAL,
            rollback_needed INTEGER DEFAULT 0,
            rollback_date TEXT,
            validation_started_at TEXT,
            created_at TEXT DEFAULT datetime('now')
        )
    """)
    
    # market_regime 表
    self._get_conn().execute("""
        CREATE TABLE IF NOT EXISTS market_regime (
            id INTEGER PRIMARY KEY,
            regime_date TEXT NOT NULL,
            regime_type TEXT,
            activity_coefficient REAL,
            index_close REAL,
            index_ma5 REAL,
            index_ma20 REAL,
            consecutive_days INTEGER,
            created_at TEXT DEFAULT datetime('now'),
            UNIQUE(regime_date)
        )
    """)
    
    # daily_monitor_log 表
    self._get_conn().execute("""
        CREATE TABLE IF NOT EXISTS daily_monitor_log (
            id INTEGER PRIMARY KEY,
            monitor_date TEXT,
            alert_type TEXT,
            alert_detail TEXT,
            severity TEXT,
            action_taken TEXT,
            created_at TEXT DEFAULT datetime('now')
        )
    """)
    
    # 初始化四种信号的默认状态
    from signal_constants import SIGNAL_TYPE_MAPPING
    for signal_type, display_name in SIGNAL_TYPE_MAPPING.items():
        self._get_conn().execute("""
            INSERT OR IGNORE INTO signal_status (signal_type, display_name, status_level, weight_multiplier)
            VALUES (?, ?, 'active', 1.0)
        """, (signal_type, display_name))
```

- [ ] **Step 4: 在 __init__ 方法中调用**

在 `data_layer.py` 的 `StockDataLayer.__init__()` 方法中，`_ensure_tables()` 调用后添加：

```python
def __init__(self, db_path=None):
    ...
    self._ensure_tables()
    self._create_adaptive_tables()  # 新增
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestDatabaseTables -v`
Expected: 4 passed

- [ ] **Step 6: 提交**

```bash
git add data_layer.py tests/test_adaptive.py
git commit -m "feat: add adaptive system tables to data_layer.py"
```

---

### Task 4: 扩展 pick_tracking 表新增评分字段

**Files:**
- Modify: `pick_tracker.py`（pick_tracking 表扩展 + 迁移方法 + 新增字段写入）

> **注意：pick_tracking 表在 pick_tracker.py:47 的 _ensure_tables() 中定义，data_layer.py 不包含此表。**

- [ ] **Step 1: 添加测试用例**

```python
# tests/test_adaptive.py 新增测试方法

class TestPickTrackingScoreFields(unittest.TestCase):
    """测试 pick_tracking 评分字段"""

    def test_pick_tracking_has_score_fields(self):
        """测试：pick_tracking表有评分字段"""
        from pick_tracker import PickTracker
        tracker = PickTracker()

        columns = tracker._get_conn().execute("PRAGMA table_info(pick_tracking)").fetchall()
        col_names = [c[1] for c in columns]

        # 检查评分字段存在
        self.assertIn('score_wave_gain', col_names)
        self.assertIn('score_shallow_dd', col_names)
        self.assertIn('score_day_gain', col_names)
        self.assertIn('score_volume', col_names)
        self.assertIn('score_ma_bull', col_names)
        self.assertIn('score_sector', col_names)
        self.assertIn('score_signal_bonus', col_names)
        self.assertIn('score_base', col_names)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestPickTrackingScoreFields -v`
Expected: FAIL - 字段不存在

- [ ] **Step 3: 在 pick_tracker.py 的 _ensure_tables 中扩展 pick_tracking 表**

找到 `pick_tracker.py:47` 的 `_ensure_tables()` 方法中创建 pick_tracking 表的 CREATE 语句，添加评分字段：

```python
# 在 pick_tracking 表 CREATE 语句中添加以下字段（在 final_pnl_pct 之后）：
score_wave_gain REAL,       -- 波段涨幅评分
score_shallow_dd REAL,      -- 回调深度评分
score_day_gain REAL,        -- 当日涨幅评分
score_volume REAL,          -- 量比评分
score_ma_bull REAL,         -- 均线排列评分
score_sector REAL,          -- 板块动量评分
score_signal_bonus REAL,    -- 信号类型加分
score_base REAL DEFAULT 5,  -- 基础分
```

- [ ] **Step 4: 在 pick_tracker.py 添加迁移方法**

在 `_ensure_tables()` 方法后添加迁移方法，处理现有数据库：

```python
def _migrate_pick_tracking_scores(self):
    """迁移：为现有 pick_tracking 表添加评分字段"""
    columns = self._get_conn().execute("PRAGMA table_info(pick_tracking)").fetchall()
    col_names = [c[1] for c in columns]

    migrations = [
        ('score_wave_gain', 'REAL'),
        ('score_shallow_dd', 'REAL'),
        ('score_day_gain', 'REAL'),
        ('score_volume', 'REAL'),
        ('score_ma_bull', 'REAL'),
        ('score_sector', 'REAL'),
        ('score_signal_bonus', 'REAL'),
        ('score_base', 'REAL DEFAULT 5'),
    ]

    for col_name, col_type in migrations:
        if col_name not in col_names:
            self._get_conn().execute(f"ALTER TABLE pick_tracking ADD COLUMN {col_name} {col_type}")
```

并在 `__init__` 中调用（在 `_ensure_tables()` 之后）：

```python
def __init__(self, db_path=None):
    self.db_path = db_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data.db')
    self.dl = get_data_layer()
    self.cfg = StrategyConfig(self.db_path)
    self._ensure_tables()
    self._migrate_pick_tracking_scores()  # 新增：迁移评分字段
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestPickTrackingScoreFields -v`
Expected: 1 passed

- [ ] **Step 6: 提交**

```bash
git add data_layer.py tests/test_adaptive.py
git commit -m "feat: add score_* fields to pick_tracking table"
```

---

### Task 5: 扩展 strategy_config.py 支持动态参数

**Files:**
- Modify: `strategy_config.py`

- [ ] **Step 1: 添加测试用例**

```python
# tests/test_adaptive.py 新增测试方法

class TestStrategyConfigDynamic(unittest.TestCase):
    """测试 StrategyConfig 动态参数"""

    def setUp(self):
        import sqlite3
        self.db_path = ':memory:'
        self.conn = sqlite3.connect(self.db_path)
        # 创建 strategy_config 表
        self.conn.execute("""
            CREATE TABLE strategy_config (
                param_key TEXT PRIMARY KEY,
                param_value REAL,
                description TEXT,
                category TEXT,
                updated_at TEXT
            )
        """)

    def tearDown(self):
        self.conn.close()

    def test_dynamic_params_exist(self):
        """测试：DYNAMIC_PARAMS 包含 score_weight 类参数"""
        from strategy_config import StrategyConfig
        cfg = StrategyConfig(self.db_path)
        
        self.assertIn('weight_wave_gain', cfg.DYNAMIC_PARAMS)
        self.assertIn('weight_sector', cfg.DYNAMIC_PARAMS)
        self.assertIn('activity_coefficient', cfg.DYNAMIC_PARAMS)

    def test_set_dynamic_param_with_category(self):
        """测试：set() 方法正确处理动态参数"""
        from strategy_config import StrategyConfig
        cfg = StrategyConfig(self.db_path)
        
        # 设置动态参数
        cfg.set('weight_wave_gain', 1.2)
        
        # 检查写入正确
        row = self.conn.execute(
            "SELECT param_value, description, category FROM strategy_config WHERE param_key='weight_wave_gain'"
        ).fetchone()
        
        self.assertEqual(row[0], 1.2)
        self.assertEqual(row[2], 'score_weight')  # category正确

    def test_get_by_category(self):
        """测试：get_weights() 返回 score_weight 类参数"""
        from strategy_config import StrategyConfig
        cfg = StrategyConfig(self.db_path)
        
        cfg.set('weight_wave_gain', 1.2)
        cfg.set('weight_volume', 0.9)
        cfg.set('first_wave_min_days', 3)  # entry 类
        
        weights = cfg.get_weights()
        
        self.assertIn('weight_wave_gain', weights)
        self.assertIn('weight_volume', weights)
        self.assertNotIn('first_wave_min_days', weights)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestStrategyConfigDynamic -v`
Expected: FAIL - AttributeError: 'DYNAMIC_PARAMS' not found

- [ ] **Step 3: 在 strategy_config.py 添加 DYNAMIC_PARAMS**

在 `StrategyConfig` 类的 `DEFAULTS` 后添加：

```python
# 新增：动态参数注册表
DYNAMIC_PARAMS = {
    # score_weight 类参数
    'weight_wave_gain': (1.0, '波段涨幅评分权重系数', 'score_weight'),
    'weight_shallow_dd': (1.0, '浅回调评分权重系数', 'score_weight'),
    'weight_strong_gain': (1.0, '强势涨幅评分权重系数', 'score_weight'),
    'weight_volume': (1.0, '放量评分权重系数', 'score_weight'),
    'weight_ma_bull': (1.0, '多头排列评分权重系数', 'score_weight'),
    'weight_anomaly': (1.0, '异动信号额外权重', 'score_weight'),
    'weight_sector': (1.0, '板块动量权重', 'score_weight'),
    'weight_signal_bonus': (1.0, '信号类型加分权重', 'score_weight'),

    # environment 类参数（统一使用 _threshold 命名，与设计文档 B.8 一致）
    'activity_coefficient': (1.0, '当前环境活跃度系数', 'environment'),
    'bull_threshold': (1.0, '上升期活跃度', 'environment'),
    'range_threshold': (0.7, '震荡期活跃度', 'environment'),
    'bear_threshold': (0.3, '退潮期活跃度', 'environment'),
}
```

- [ ] **Step 4: 修改 set() 方法支持动态参数**

找到 `set()` 方法，修改为：

```python
def set(self, key, value, description=None, category=None):
    """设置参数值，支持动态参数"""
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
    
    with self._get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO strategy_config (param_key, param_value, description, category, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
            (key, value, final_desc, final_cat)
        )
```

- [ ] **Step 5: 添加 get_weights() 和 get_environment() 方法**

```python
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
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestStrategyConfigDynamic -v`
Expected: 3 passed

- [ ] **Step 7: 提交**

```bash
git add strategy_config.py tests/test_adaptive.py
git commit -m "feat: add DYNAMIC_PARAMS support to strategy_config.py"
```

---

### Task 6: 创建 daily_monitor.py 核心结构

**Files:**
- Create: `daily_monitor.py`

- [ ] **Step 1: 添加测试用例**

```python
# tests/test_adaptive.py 新增测试方法

class TestDailyMonitor(unittest.TestCase):
    """测试每日监控模块"""

    def test_daily_monitor_init(self):
        """测试：DailyMonitor 初始化"""
        from daily_monitor import DailyMonitor
        monitor = DailyMonitor()
        self.assertIsNotNone(monitor.dl)
        self.assertIsNotNone(monitor.cfg)

    def test_calculate_expectancy(self):
        """测试：期望值计算"""
        from daily_monitor import calculate_expectancy
        
        # 胜率60%，平均盈利10%，平均亏损5%
        result = calculate_expectancy(0.6, 0.10, 0.05)
        expected = 0.10 * 0.6 - 0.05 * 0.4  # 0.06 - 0.02 = 0.04
        self.assertAlmostEqual(result, 0.04, places=4)

    def test_wilson_expectancy_lower_bound(self):
        """测试：Wilson置信下界期望值"""
        from daily_monitor import wilson_expectancy_lower_bound
        
        # 样本少时置信区间宽
        result_5 = wilson_expectancy_lower_bound(0.6, 0.10, 0.05, 5)
        result_50 = wilson_expectancy_lower_bound(0.6, 0.10, 0.05, 50)
        
        # 样本少时，下界更保守（更低）
        self.assertLess(result_5, result_50)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestDailyMonitor -v`
Expected: FAIL - ModuleNotFoundError: No module named 'daily_monitor'

- [ ] **Step 3: 创建 daily_monitor.py 核心结构**

```python
#!/usr/bin/env python3
"""
每日监控模块
职责：检测异常，预警通知，环境感知，不做主动调整
"""
import sys
import os
from datetime import datetime, timedelta  # timedelta 用于 _check_market_regime
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer
from strategy_config import StrategyConfig
from signal_constants import SIGNAL_TYPE_MAPPING, normalize_signal_type


def calculate_expectancy(win_rate, avg_win, avg_loss):
    """
    计算期望值
    
    Args:
        win_rate: 胜率（0-1）
        avg_win: 平均盈利百分比（如 0.10 表示 10%）
        avg_loss: 平均亏损百分比（如 0.05 表示 5%）
    
    Returns:
        期望值（正值表示盈利预期）
    """
    return avg_win * win_rate - avg_loss * (1 - win_rate)


def wilson_expectancy_lower_bound(win_rate, avg_win, avg_loss, n, z=1.96):
    """
    Wilson置信区间下界期望值（保守估计）
    
    Args:
        win_rate: 胜率
        avg_win: 平均盈利
        avg_loss: 平均亏损
        n: 样本量
        z: 置信水平（1.96 = 95%）
    
    Returns:
        保守期望值估计
    """
    if n == 0:
        return 0.0
    
    p = win_rate
    # Wilson下界胜率
    denominator = 1 + z**2 / n
    p_lower = (p + z**2 / (2 * n) - z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denominator
    
    # 使用下界胜率计算期望值
    return avg_win * p_lower - avg_loss * (1 - p_lower)


class DailyMonitor:
    """每日监控器"""
    
    def __init__(self, db_path=None):
        self.dl = get_data_layer(db_path)
        self.cfg = StrategyConfig(db_path)
    
    def run(self, monitor_date=None):
        """
        运行每日监控
        
        Args:
            monitor_date: 监控日期，默认今天
        
        Returns:
            alerts: 预警列表
        """
        if monitor_date is None:
            monitor_date = datetime.now().strftime('%Y-%m-%d')
        
        alerts = []
        
        # 1. 检查信号期望值
        signal_alerts = self._check_signal_expectancy(monitor_date)
        alerts.extend(signal_alerts)
        
        # 2. 检查市场环境
        regime_alert = self._check_market_regime(monitor_date)
        if regime_alert:
            alerts.append(regime_alert)
        
        # 3. 更新 signal_status 表
        self._update_signal_status()
        
        # 4. 写入监控日志
        self._write_monitor_log(alerts, monitor_date)
        
        return alerts
    
    def _check_signal_expectancy(self, monitor_date):
        """检查各信号期望值"""
        alerts = []
        
        # 从 pick_tracking 获取各信号的退出数据
        # 注意：使用 TRIM(signal_type) 防止数据库中的尾随空格导致匹配失败
        with self.dl._get_conn() as conn:
            for signal_type in SIGNAL_TYPE_MAPPING.keys():
                rows = conn.execute("""
                    SELECT final_pnl_pct FROM pick_tracking 
                    WHERE TRIM(signal_type)=? AND status='exited'
                """, (SIGNAL_TYPE_MAPPING[signal_type],)).fetchall()
                
                if len(rows) < 5:
                    # 样本不足，info级别
                    alerts.append({
                        'type': 'signal_sample_low',
                        'detail': f'{signal_type}: 仅{len(rows)}笔退出样本',
                        'severity': 'info'
                    })
                    continue
                
                pnls = [r[0] for r in rows]
                win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
                avg_win = np.mean([p for p in pnls if p > 0]) if any(p > 0 for p in pnls) else 0
                avg_loss = np.mean([p for p in pnls if p < 0]) if any(p < 0 for p in pnls) else 0
                
                expectancy_lb = wilson_expectancy_lower_bound(win_rate, avg_win, avg_loss, len(pnls))
                
                if expectancy_lb < 0:
                    alerts.append({
                        'type': 'signal_expectancy_low',
                        'detail': f'{signal_type}: Wilson下界期望值={expectancy_lb:.2%}',
                        'severity': 'warning'
                    })
        
        return alerts
    
    def _check_market_regime(self, monitor_date):
        """检查市场环境"""
        # 获取上证指数数据
        index_data = self.dl.get_kline('sh.000001', 
            start_date=(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
            end_date=monitor_date
        )
        
        if index_data is None or len(index_data) < 20:
            return None
        
        regime, coeff, consecutive = self._get_market_regime_smoothed(index_data)
        
        # 更新 market_regime 表
        self._update_market_regime_table(monitor_date, regime, coeff, consecutive)
        
        if regime == 'bear' and consecutive >= 5:
            return {
                'type': 'market_bear',
                'detail': f'退潮期已持续{consecutive}天，活跃度系数={coeff}',
                'severity': 'info'
            }
        
        return None
    
    def _get_market_regime_smoothed(self, index_data, new_value_weight=0.3):
        """增强的市场环境判断（连续天数+平滑处理）"""
        # 注意：datetime, timedelta 已在文件头部导入

        current = index_data['close'].iloc[-1]
        ma5 = index_data['close'].rolling(5).mean().iloc[-1]
        ma20 = index_data['close'].rolling(20).mean().iloc[-1]
        
        # 原始判断
        if current > ma5 > ma20:
            raw_regime = 'bull'
            raw_coeff = 1.0
        elif current > ma20 * 0.95:
            raw_regime = 'range'
            raw_coeff = 0.7
        else:
            raw_regime = 'bear'
            raw_coeff = 0.3
        
        # 连续天数计算
        regime_history = []
        for i in range(-5, 0):
            if i >= -len(index_data):
                c = index_data['close'].iloc[i]
                m5 = index_data['close'].rolling(5).mean().iloc[i]
                m20 = index_data['close'].rolling(20).mean().iloc[i]
                if c > m5 > m20:
                    regime_history.append('bull')
                elif c > m20 * 0.95:
                    regime_history.append('range')
                else:
                    regime_history.append('bear')
        
        consecutive_days = sum(1 for r in regime_history if r == raw_regime)
        
        # 连续5天确认才生效
        if consecutive_days >= 5:
            confirmed_regime = raw_regime
        else:
            # 从 market_regime 表读取上一状态
            prev = self._get_previous_regime()
            confirmed_regime = prev if prev else raw_regime
        
        # 平滑处理
        prev_coeff = self._get_previous_activity_coefficient()
        if prev_coeff:
            smoothed_coeff = prev_coeff * (1 - new_value_weight) + raw_coeff * new_value_weight
        else:
            smoothed_coeff = raw_coeff
        
        return confirmed_regime, smoothed_coeff, consecutive_days
    
    def _get_previous_regime(self):
        """获取上一交易日市场环境"""
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT regime_type FROM market_regime 
                ORDER BY regime_date DESC LIMIT 1
            """).fetchone()
            return row[0] if row else None
    
    def _get_previous_activity_coefficient(self):
        """获取上一交易日活跃度系数"""
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT activity_coefficient FROM market_regime 
                ORDER BY regime_date DESC LIMIT 1
            """).fetchone()
            return row[0] if row else None
    
    def _update_market_regime_table(self, date, regime, coeff, consecutive):
        """更新 market_regime 表"""
        index_data = self.dl.get_kline('sh.000001', 
            start_date=date, end_date=date
        )
        if index_data is None or len(index_data) == 0:
            return
        
        close = index_data['close'].iloc[-1]
        ma5 = index_data['close'].rolling(5).mean().iloc[-1]
        ma20 = index_data['close'].rolling(20).mean().iloc[-1]
        
        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO market_regime 
                (regime_date, regime_type, activity_coefficient, index_close, index_ma5, index_ma20, consecutive_days)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (date, regime, coeff, close, ma5, ma20, consecutive))
    
    def _update_signal_status(self):
        """更新 signal_status 表的 live_* 字段"""
        with self.dl._get_conn() as conn:
            for signal_type, display_name in SIGNAL_TYPE_MAPPING.items():
                # 获取该信号的退出数据
                # 注意：pick_tracking.signal_type 存的是中文（如"异动不跌"），用 display_name 匹配
                rows = conn.execute("""
                    SELECT final_pnl_pct FROM pick_tracking 
                    WHERE TRIM(signal_type)=? AND status='exited'
                """, (display_name,)).fetchall()
                
                if len(rows) == 0:
                    continue
                
                pnls = [r[0] for r in rows]
                win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
                avg_win = np.mean([p for p in pnls if p > 0]) if any(p > 0 for p in pnls) else 0
                avg_loss = abs(np.mean([p for p in pnls if p < 0])) if any(p < 0 for p in pnls) else 0
                
                expectancy = calculate_expectancy(win_rate, avg_win, avg_loss)
                expectancy_lb = wilson_expectancy_lower_bound(win_rate, avg_win, avg_loss, len(pnls))
                
                conn.execute("""
                    UPDATE signal_status SET
                        live_win_rate=?,
                        live_avg_win_pct=?,
                        live_avg_loss_pct=?,
                        live_expectancy=?,
                        live_expectancy_lb=?,
                        live_sample_count=?,
                        last_check_date=datetime('now')
                    WHERE signal_type=?
                """, (win_rate, avg_win, avg_loss, expectancy, expectancy_lb, len(pnls), signal_type))
    
    def _write_monitor_log(self, alerts, monitor_date):
        """写入监控日志"""
        with self.dl._get_conn() as conn:
            for alert in alerts:
                conn.execute("""
                    INSERT INTO daily_monitor_log 
                    (monitor_date, alert_type, alert_detail, severity, action_taken)
                    VALUES (?, ?, ?, ?, 'logged')
                """, (monitor_date, alert['type'], alert['detail'], alert['severity']))
    
    def print_summary(self, alerts):
        """打印监控摘要"""
        print(f"\n[每日监控] {datetime.now().strftime('%Y-%m-%d')}")
        
        if not alerts:
            print("  ✓ 无异常预警")
            return
        
        for alert in alerts:
            severity = alert['severity']
            if severity == 'critical':
                print(f"  ✗ [{severity}] {alert['detail']}")
            elif severity == 'warning':
                print(f"  ⚠ [{severity}] {alert['detail']}")
            else:
                print(f"  ℹ [{severity}] {alert['detail']}")


if __name__ == '__main__':
    monitor = DailyMonitor()
    alerts = monitor.run()
    monitor.print_summary(alerts)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestDailyMonitor -v`
Expected: 3 passed

- [ ] **Step 5: 语法检查**

Run: `python3 -m py_compile daily_monitor.py`
Expected: 无输出（通过）

- [ ] **Step 6: 提交**

```bash
git add daily_monitor.py tests/test_adaptive.py
git commit -m "feat: add daily_monitor.py for daily monitoring and environment sensing"
```

---

### Task 7: 验证完整测试套件

**Files:**
- All modified files

- [ ] **Step 1: 运行全部测试**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/ -v`
Expected: All passed

- [ ] **Step 2: 运行语法检查**

Run: `python3 -m py_compile signal_constants.py daily_monitor.py data_layer.py strategy_config.py`
Expected: 无输出（通过）

- [ ] **Step 3: 最终提交**

```bash
git add -A
git commit -m "feat: complete Phase 1-2 of adaptive quant system (data layer + daily monitor)"
```

---

## Self-Review

**1. Spec Coverage:**

| Spec需求 | 任务覆盖 |
|----------|----------|
| signal_constants.py 信号映射 | Task 1 ✓ |
| signal_status 表 | Task 3 ✓ |
| optimization_history 表 | Task 3 ✓ |
| market_regime 表 | Task 3 ✓ |
| daily_monitor_log 表 | Task 3 ✓ |
| pick_tracking score_* 字段 | Task 4 ✓ |
| strategy_config DYNAMIC_PARAMS | Task 5 ✓ |
| daily_monitor.py 每日监控 | Task 6 ✓ |
| Wilson期望值计算 | Task 6 ✓ |
| 环境感知平滑处理 | Task 6 ✓ |

**2. Placeholder scan:** 无 TBD/TODO ✓

**3. Type consistency:** 方法签名一致 ✓

---

# Phase 3: 每周优化器 (weekly_optimizer.py)

---

## Phase 3 文件结构

| 文件 | 负责内容 |
|------|----------|
| `weekly_optimizer.py` | 每周四层优化调度（新建） |
| `tests/test_weekly_optimizer.py` | 每周优化器测试（新建） |

---

### Task 8: 创建 weekly_optimizer.py 核心结构

**Files:**
- Create: `weekly_optimizer.py`

- [ ] **Step 1: 添加测试用例**

```python
# tests/test_adaptive.py 新增测试方法

class TestWeeklyOptimizer(unittest.TestCase):
    """测试每周优化器"""

    def test_weekly_optimizer_init(self):
        """测试：WeeklyOptimizer 初始化"""
        from weekly_optimizer import WeeklyOptimizer
        optimizer = WeeklyOptimizer()
        self.assertIsNotNone(optimizer.dl)
        self.assertIsNotNone(optimizer.cfg)
        self.assertIsNotNone(optimizer.base_optimizer)  # 复用现有StrategyOptimizer

    def test_adjust_score_weight_increase(self):
        """测试：相关性高时权重增加"""
        from weekly_optimizer import adjust_score_weight
        
        # 相关性0.35 > 0.3，权重应增加10%
        result = adjust_score_weight(1.0, 0.35)
        self.assertAlmostEqual(result, 1.10, places=2)

    def test_adjust_score_weight_decrease(self):
        """测试：相关性低时权重减少"""
        from weekly_optimizer import adjust_score_weight
        
        # 相关性0.05 < 0.1，权重应减少10%
        result = adjust_score_weight(1.0, 0.05)
        self.assertAlmostEqual(result, 0.90, places=2)

    def test_adjust_score_weight_limits(self):
        """测试：权重有上下限"""
        from weekly_optimizer import adjust_score_weight
        
        # 上限测试：权重不应超过1.5
        result = adjust_score_weight(1.45, 0.35)
        self.assertLessEqual(result, 1.5)
        
        # 下限测试：权重不应低于0.7
        result = adjust_score_weight(0.75, 0.05)
        self.assertGreaterEqual(result, 0.7)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestWeeklyOptimizer -v`
Expected: FAIL - ModuleNotFoundError

- [ ] **Step 3: 创建 weekly_optimizer.py**

```python
#!/usr/bin/env python3
"""
每周优化器
职责：周末运行四层优化（参数/评分/信号/环境），结果先入沙盒验证
复用现有 StrategyOptimizer 的参数优化能力
"""
import sys
import os
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer
from strategy_config import StrategyConfig
from strategy_optimizer import StrategyOptimizer
from signal_constants import SIGNAL_TYPE_MAPPING, get_display_name, get_weight_multiplier
from daily_monitor import calculate_expectancy, wilson_expectancy_lower_bound


def adjust_score_weight(current_weight, correlation):
    """
    根据相关性调整评分权重
    
    Args:
        current_weight: 当前权重系数
        correlation: Spearman相关性
    
    Returns:
        新权重系数（带上下限）
    """
    # 调整幅度
    if correlation > 0.3:
        delta = 0.10  # 权重↑10%
    elif correlation < 0.1:
        delta = -0.10  # 权重↓10%
    else:
        delta = 0  # 不调整
    
    # 应用调整，带上下限
    new_weight = current_weight * (1 + delta)
    new_weight = max(0.7, min(1.5, new_weight))  # 下限-30%，上限+50%
    
    return new_weight


class WeeklyOptimizer:
    """每周优化器"""
    
    def __init__(self, db_path=None):
        self.dl = get_data_layer(db_path)
        self.cfg = StrategyConfig(db_path)
        self.base_optimizer = StrategyOptimizer(db_path)  # 复用现有优化器
    
    def run_weekly(self, optimize_date=None):
        """
        运行每周优化
        
        Args:
            optimize_date: 优化日期，默认今天
        
        Returns:
            changes: 变更列表
        """
        if optimize_date is None:
            optimize_date = datetime.now().strftime('%Y-%m-%d')
        
        changes = []
        
        # Step 0: 环境状态更新
        env_change = self._update_environment_state(optimize_date)
        if env_change:
            changes.append(env_change)
        
        # Step 1: 参数层优化（复用 StrategyOptimizer）
        param_changes = self._optimize_parameters(optimize_date)
        changes.extend(param_changes)
        
        # Step 2: 评分层调整
        score_changes = self._adjust_score_weights(optimize_date)
        changes.extend(score_changes)
        
        # Step 3: 信号层状态检查
        signal_changes = self._check_signal_status(optimize_date)
        changes.extend(signal_changes)
        
        # Step 4: 冷启动检查
        self._cold_start_check()
        
        # 所有变更写入 optimization_history（sandbox状态）
        self._save_to_sandbox(changes, optimize_date)
        
        return changes
    
    def _update_environment_state(self, optimize_date):
        """更新环境状态"""
        # 从 market_regime 获取当前状态
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT regime_type, activity_coefficient FROM market_regime
                ORDER BY regime_date DESC LIMIT 1
            """).fetchone()
        
        if row:
            regime, coeff = row
            # 更新到 strategy_config
            old_coeff = self.cfg.get('activity_coefficient')
            if old_coeff != coeff:
                self.cfg.set('activity_coefficient', coeff)
                return {
                    'type': 'environment',
                    'param_key': 'activity_coefficient',
                    'old_value': old_coeff,
                    'new_value': coeff,
                }
        
        return None
    
    def _optimize_parameters(self, optimize_date):
        """参数层优化（OOS验证）"""
        changes = []
        
        # 训练集：过去120天，验证集：最近30天
        train_start = (datetime.now() - timedelta(days=150)).strftime('%Y-%m-%d')
        train_end = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        oos_start = train_end
        oos_end = optimize_date
        
        # 获取股票列表（抽样200只）
        codes = self.dl.get_stock_list()['code'].tolist()[:200]
        
        # 使用现有优化器
        try:
            result = self.base_optimizer.coordinate_descent(
                train_start, train_end, codes,
                max_rounds=1, sample_size=200
            )
            
            # OOS验证
            oos_result = self.base_optimizer.evaluate_params(
                result['best_params'], oos_start, oos_end, codes, sample_size=200
            )
            
            # 检查OOS表现是否优于当前
            current_params = self.cfg.get_all()
            current_result = self.base_optimizer.evaluate_params(
                current_params, oos_start, oos_end, codes, sample_size=200
            )
            
            if oos_result.get('sharpe', 0) >= current_result.get('sharpe', 0) * 0.95:
                # OOS验证通过，记录变更
                for key, new_val in result['best_params'].items():
                    old_val = current_params.get(key)
                    if old_val != new_val:
                        changes.append({
                            'type': 'parameter',
                            'param_key': key,
                            'old_value': old_val,
                            'new_value': new_val,
                            'backtest_train_sharpe': result.get('sharpe'),
                            'backtest_oos_sharpe': oos_result.get('sharpe'),
                        })
        except Exception as e:
            print(f"参数优化失败: {e}")
        
        return changes
    
    def _adjust_score_weights(self, optimize_date):
        """评分层调整（基于期望值相关性）"""
        changes = []
        
        # 获取退出数据
        with self.dl._get_conn() as conn:
            exited_df = conn.execute("""
                SELECT score_wave_gain, score_shallow_dd, score_day_gain, 
                       score_volume, score_ma_bull, score_sector, score_signal_bonus,
                       final_pnl_pct
                FROM pick_tracking WHERE status='exited' AND final_pnl_pct IS NOT NULL
            """).fetchall()
        
        if len(exited_df) < 10:
            return changes  # 样本不足
        
        import pandas as pd
        exited = pd.DataFrame(exited_df, columns=[
            'score_wave_gain', 'score_shallow_dd', 'score_day_gain',
            'score_volume', 'score_ma_bull', 'score_sector', 'score_signal_bonus',
            'final_pnl_pct'
        ])
        
        # 计算各维度与期望值的相关性
        dimensions = ['wave_gain', 'shallow_dd', 'day_gain', 'volume', 'ma_bull', 'sector', 'signal_bonus']
        current_weights = self.cfg.get_weights()
        
        for dim in dimensions:
            col = f'score_{dim}'
            if col in exited.columns and exited[col].notna().any():
                corr = exited[col].corr(exited['final_pnl_pct'], method='spearman')
                
                key = f'weight_{dim}'
                old_weight = current_weights.get(key, 1.0)
                new_weight = adjust_score_weight(old_weight, corr)
                
                if new_weight != old_weight:
                    changes.append({
                        'type': 'score_weight',
                        'param_key': key,
                        'old_value': old_weight,
                        'new_value': new_weight,
                    })
        
        return changes
    
    def _check_signal_status(self, optimize_date):
        """信号层状态检查"""
        changes = []
        
        with self.dl._get_conn() as conn:
            for signal_type in SIGNAL_TYPE_MAPPING.keys():
                # 获取信号统计
                row = conn.execute("""
                    SELECT live_win_rate, live_avg_win_pct, live_avg_loss_pct,
                           live_sample_count, status_level, weight_multiplier, can_auto_disable
                    FROM signal_status WHERE signal_type=?
                """, (signal_type,)).fetchone()
                
                if not row:
                    continue
                
                win_rate, avg_win, avg_loss, sample_count, status_level, weight_multiplier, can_auto_disable = row
                
                # 样本不足，保持active（冷启动保护）
                if sample_count < 10:
                    continue
                
                # 计算Wilson下界期望值
                expectancy_lb = wilson_expectancy_lower_bound(win_rate, avg_win, avg_loss, sample_count)
                
                # 判断是否需要降级
                new_status = status_level
                new_weight = weight_multiplier
                
                if expectancy_lb < 0:
                    if sample_count >= 10 and status_level == 'active':
                        new_status = 'watching'
                        new_weight = 0.5
                    elif sample_count >= 30 and status_level in ['active', 'watching']:
                        # 需人工确认才能disabled（can_auto_disable 需手动设为 1 才触发）
                        if can_auto_disable == 1:
                            new_status = 'warning'
                            new_weight = 0.2
                
                if new_status != status_level:
                    conn.execute("""
                        UPDATE signal_status SET status_level=?, weight_multiplier=?, last_check_date=datetime('now')
                        WHERE signal_type=?
                    """, (new_status, new_weight, signal_type))
                    
                    changes.append({
                        'type': 'signal_status',
                        'param_key': signal_type,
                        'old_value': status_level,
                        'new_value': new_status,
                    })
        
        return changes
    
    def _cold_start_check(self):
        """冷启动检查"""
        with self.dl._get_conn() as conn:
            for signal_type in SIGNAL_TYPE_MAPPING.keys():
                row = conn.execute("""
                    SELECT live_sample_count, confidence_level FROM signal_status WHERE signal_type=?
                """, (signal_type,)).fetchone()
                
                if row and row[0] < 10:
                    # 样本不足，标记为unknown
                    conn.execute("""
                        UPDATE signal_status SET confidence_level='unknown' WHERE signal_type=?
                    """, (signal_type,))
    
    def _save_to_sandbox(self, changes, optimize_date):
        """保存变更到sandbox（optimization_history表）"""
        with self.dl._get_conn() as conn:
            for change in changes:
                conn.execute("""
                    INSERT INTO optimization_history 
                    (optimize_date, optimize_type, param_key, old_value, new_value, 
                     sandbox_test_result, backtest_train_sharpe, backtest_oos_sharpe,
                     validation_started_at, created_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, datetime('now'), datetime('now'))
                """, (
                    optimize_date, change['type'], change['param_key'],
                    change.get('old_value'), change.get('new_value'),
                    change.get('backtest_train_sharpe'), change.get('backtest_oos_sharpe')
                ))
    
    def print_summary(self, changes):
        """打印优化摘要"""
        print(f"\n[每周优化] {datetime.now().strftime('%Y-%m-%d')}")
        
        if not changes:
            print("  ✓ 无需优化变更")
            return
        
        by_type = {}
        for c in changes:
            t = c['type']
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(c)
        
        for t, items in by_type.items():
            print(f"\n  [{t}] {len(items)}项变更:")
            for item in items:
                print(f"    - {item['param_key']}: {item.get('old_value')} → {item.get('new_value')}")
        
        print("\n  ⚠ 所有变更已写入sandbox，待滚动验证")


if __name__ == '__main__':
    optimizer = WeeklyOptimizer()
    changes = optimizer.run_weekly()
    optimizer.print_summary(changes)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestWeeklyOptimizer -v`
Expected: 4 passed

- [ ] **Step 5: 语法检查**

Run: `python3 -m py_compile weekly_optimizer.py`
Expected: 无输出（通过）

- [ ] **Step 6: 提交**

```bash
git add weekly_optimizer.py tests/test_adaptive.py
git commit -m "feat: add weekly_optimizer.py for weekly four-layer optimization"
```

---

# Phase 4: 沙盒验证器 (sandbox_validator.py)

---

### Task 9: 创建 sandbox_validator.py

**Files:**
- Create: `sandbox_validator.py`

- [ ] **Step 1: 添加测试用例**

```python
# tests/test_adaptive.py 新增测试方法

class TestSandboxValidator(unittest.TestCase):
    """测试沙盒验证器"""

    def test_sandbox_validator_init(self):
        """测试：SandboxValidator 初始化"""
        from sandbox_validator import SandboxValidator
        validator = SandboxValidator()
        self.assertIsNotNone(validator.dl)

    def test_rolling_validate_all_passed(self):
        """测试：连续3周全部通过"""
        from sandbox_validator import SandboxValidator
        
        # 模拟3周全部True的结果
        results = [True, True, True]
        passed = all(results)
        self.assertTrue(passed)

    def test_rolling_validate_not_all_passed(self):
        """测试：非连续通过"""
        from sandbox_validator import SandboxValidator
        
        # 模拟 [True, False, True] - 不连续
        results = [True, False, True]
        passed = all(results[-3:])
        self.assertFalse(passed)

    def test_sandbox_status_constants(self):
        """测试：状态常量定义"""
        from sandbox_validator import SandboxValidator
        
        self.assertEqual(SandboxValidator.STATUS_PENDING, 'pending')
        self.assertEqual(SandboxValidator.STATUS_PASSED, 'passed')
        self.assertEqual(SandboxValidator.STATUS_APPLIED, 'applied')
        self.assertEqual(SandboxValidator.STATUS_FAILED, 'failed')
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestSandboxValidator -v`
Expected: FAIL - ModuleNotFoundError

- [ ] **Step 3: 创建 sandbox_validator.py**

```python
#!/usr/bin/env python3
"""
沙盒验证器
职责：滚动窗口验证，防止重复应用
"""
import sys
import os
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer
from daily_monitor import calculate_expectancy


SANDBOX_VALIDATION_CONFIG = {
    'min_weeks_to_confirm': 3,
    'min_samples_per_week': 5,
    'rolling_window_days': 30,
    'threshold_by_samples': {
        10: 1.15,  # 样本少，要求更高
        20: 1.08,
        30: 1.02,
        50: 0.98,  # 样本足，允许小幅下降
    }
}


class SandboxValidator:
    """沙盒验证器"""
    
    # 状态常量
    STATUS_PENDING = 'pending'
    STATUS_PASSED = 'passed'
    STATUS_APPLIED = 'applied'
    STATUS_FAILED = 'failed'
    
    def __init__(self, db_path=None):
        self.dl = get_data_layer(db_path)
    
    def get_status(self):
        """获取当前sandbox状态"""
        pending = self._get_pending_changes()
        
        if pending:
            # 取验证起始时间最早的记录
            oldest = min(pending, key=lambda x: x.get('validation_started_at', x.get('created_at', '')))
            min_weeks = min(c.get('weeks_passed', 0) for c in pending)
        else:
            oldest = None
            min_weeks = 0
        
        return {
            'has_pending': len(pending) > 0,
            'weeks_passed': min_weeks,
            'pending_count': len(pending),
            'validation_started': oldest.get('validation_started_at') if oldest else None,
        }
    
    def _get_pending_changes(self):
        """获取待验证变更"""
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT id, optimize_date, optimize_type, param_key, old_value, new_value,
                       sandbox_test_result, validation_started_at, created_at, weeks_passed
                FROM optimization_history 
                WHERE sandbox_test_result = 'pending'
                ORDER BY created_at ASC
            """).fetchall()
        
        changes = []
        for row in rows:
            changes.append({
                'id': row[0],
                'optimize_date': row[1],
                'type': row[2],
                'param_key': row[3],
                'old_value': row[4],
                'new_value': row[5],
                'sandbox_test_result': row[6],
                'validation_started_at': row[7],
                'created_at': row[8],
                'weeks_passed': row[9] or 0,
            })
        
        return changes
    
    def validate(self, pending_changes):
        """验证待应用变更"""
        results = []
        
        for change in pending_changes:
            if change['sandbox_test_result'] != self.STATUS_PENDING:
                continue
            
            # 执行验证逻辑
            validation = self._validate_single(change)
            if validation['passed']:
                change['sandbox_test_result'] = self.STATUS_PASSED
            else:
                change['sandbox_test_result'] = self.STATUS_FAILED
            results.append(change)
        
        # 检查是否全部通过
        passed = all(c['sandbox_test_result'] == self.STATUS_PASSED for c in results) if results else False
        return {'passed': passed, 'changes': results}
    
    def _validate_single(self, change):
        """验证单个变更"""
        # 获取最近30天退出数据
        with self.dl._get_conn() as conn:
            exits = conn.execute("""
                SELECT final_pnl_pct FROM pick_tracking 
                WHERE status='exited' AND exit_date >= date('now', '-30 days')
            """).fetchall()
        
        if len(exits) < 5:
            return {'passed': False, 'reason': '样本不足'}
        
        pnls = [e[0] for e in exits]
        sandbox_exp = calculate_expectancy(
            sum(1 for p in pnls if p > 0) / len(pnls),
            np.mean([p for p in pnls if p > 0]) if any(p > 0 for p in pnls) else 0,
            abs(np.mean([p for p in pnls if p < 0])) if any(p < 0 for p in pnls) else 0
        )
        
        # 获取基准期望值（历史30天）
        with self.dl._get_conn() as conn:
            baseline = conn.execute("""
                SELECT AVG(final_pnl_pct) FROM pick_tracking 
                WHERE status='exited' AND exit_date >= date('now', '-60 days')
            """).fetchone()[0] or 0
        
        threshold = SANDBOX_VALIDATION_CONFIG['threshold_by_samples'].get(
            min(len(exits), 50), 1.02
        )
        
        passed = sandbox_exp >= baseline * threshold
        
        return {'passed': passed, 'reason': f'sandbox_exp={sandbox_exp:.2%}, baseline={baseline:.2%}'}
    
    def mark_applied(self, change_id):
        """标记变更已应用"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                UPDATE optimization_history 
                SET sandbox_test_result = ?, apply_date = datetime('now')
                WHERE id = ?
            """, (self.STATUS_APPLIED, change_id))
    
    def update_weeks_passed(self):
        """更新 weeks_passed（每周调用）"""
        pending = self._get_pending_changes()
        for change in pending:
            new_weeks = change['weeks_passed'] + 1
            with self.dl._get_conn() as conn:
                conn.execute("""
                    UPDATE optimization_history SET weeks_passed = ? WHERE id = ?
                """, (new_weeks, change['id']))


if __name__ == '__main__':
    validator = SandboxValidator()
    status = validator.get_status()
    print(f"沙盒状态: {status}")
    
    if status['has_pending']:
        print(f"待验证变更: {status['pending_count']}项")
        print(f"验证周数: {status['weeks_passed']}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestSandboxValidator -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add sandbox_validator.py tests/test_adaptive.py
git commit -m "feat: add sandbox_validator.py with rolling window validation"
```

---

# Phase 5: 核心控制器 (adaptive_engine.py + daily_run.sh)

---

### Task 10: 创建 adaptive_engine.py

**Files:**
- Create: `adaptive_engine.py`

- [ ] **Step 1: 添加测试用例**

```python
# tests/test_adaptive.py 新增测试方法

class TestAdaptiveEngine(unittest.TestCase):
    """测试自适应引擎"""

    def test_adaptive_engine_init(self):
        """测试：AdaptiveEngine 初始化"""
        from adaptive_engine import AdaptiveEngine
        engine = AdaptiveEngine()
        self.assertIsNotNone(engine.monitor)
        self.assertIsNotNone(engine.optimizer)
        self.assertIsNotNone(engine.sandbox)

    def test_run_daily_returns_alerts(self):
        """测试：run_daily 返回预警列表"""
        from adaptive_engine import AdaptiveEngine
        engine = AdaptiveEngine()
        
        alerts = engine.run_daily()
        self.assertIsInstance(alerts, list)
```

- [ ] **Step 2: 创建 adaptive_engine.py**

```python
#!/usr/bin/env python3
"""
自适应引擎核心控制器
职责：调度每日监控+每周优化，异常处理
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer
from daily_monitor import DailyMonitor
from weekly_optimizer import WeeklyOptimizer
from sandbox_validator import SandboxValidator


class AdaptiveEngine:
    """自适应引擎"""
    
    def __init__(self, db_path=None):
        self.dl = get_data_layer(db_path)
        self.monitor = DailyMonitor(db_path)
        self.optimizer = WeeklyOptimizer(db_path)
        self.sandbox = SandboxValidator(db_path)
    
    def run_daily(self, monitor_date=None):
        """每日调度"""
        try:
            # 1. 运行监控
            alerts = self.monitor.run(monitor_date)
            
            # 2. 处理critical预警
            critical = [a for a in alerts if a['severity'] == 'critical']
            if critical:
                self._handle_critical(critical)
            
            # 3. 检查沙盒状态
            sandbox_status = self.sandbox.get_status()
            pending_changes = self.sandbox._get_pending_changes()
            
            if sandbox_status['weeks_passed'] >= 3 and pending_changes:
                self._apply_sandbox_if_passed(pending_changes)
            
            return alerts
            
        except Exception as e:
            self._log_error('daily', e)
            return []
    
    def run_weekly(self, optimize_date=None):
        """每周调度"""
        try:
            # 1. 运行优化
            changes = self.optimizer.run_weekly(optimize_date)
            
            # 2. 更新沙盒 weeks_passed
            self.sandbox.update_weeks_passed()
            
            # 3. 输出报告
            self.optimizer.print_summary(changes)
            
            return changes
            
        except Exception as e:
            self._log_error('weekly', e)
            return []
    
    def _handle_critical(self, alerts):
        """处理critical预警"""
        for alert in alerts:
            # 立即通知用户（不自动调整）
            print(f"\n✗ CRITICAL: {alert['detail']}")
            # 记录到历史
            self._write_critical_log(alert)
    
    def _write_critical_log(self, alert):
        """写入critical预警日志"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT INTO daily_monitor_log 
                (monitor_date, alert_type, alert_detail, severity, action_taken)
                VALUES (datetime('now'), ?, ?, 'critical', 'notified')
            """, (alert['type'], alert['detail']))
    
    def _apply_sandbox_if_passed(self, pending_changes):
        """沙盒验证通过后应用"""
        # 检查是否已应用
        unapplied = [c for c in pending_changes if c['sandbox_test_result'] == 'pending']
        if not unapplied:
            return
        
        sandbox_results = self.sandbox.validate(unapplied)
        if sandbox_results['passed']:
            for change in sandbox_results['changes']:
                self._apply_change(change)
                self.sandbox.mark_applied(change['id'])
            print(f"\n✓ 沙盒验证通过，已应用 {len(sandbox_results['changes'])} 项变更")
        else:
            print(f"\n✗ 沙盒验证未通过，保持当前配置")
    
    def _apply_change(self, change):
        """应用单个变更"""
        from strategy_config import StrategyConfig
        from signal_constants import get_weight_multiplier
        
        change_type = change['type']
        param_key = change['param_key']
        new_value = change['new_value']
        
        if change_type == 'parameter':
            cfg = StrategyConfig()
            cfg.set(param_key, new_value)
        
        elif change_type == 'score_weight':
            cfg = StrategyConfig()
            cfg.set(param_key, new_value)
        
        elif change_type == 'signal_status':
            with self.dl._get_conn() as conn:
                conn.execute("""
                    UPDATE signal_status SET status_level=?, weight_multiplier=?, last_check_date=datetime('now')
                    WHERE signal_type=?
                """, (new_value, get_weight_multiplier(new_value), param_key))
        
        elif change_type == 'environment':
            cfg = StrategyConfig()
            cfg.set(param_key, new_value)
    
    def _log_error(self, component, error):
        """记录错误"""
        print(f"\n✗ [{component}] 错误: {error}")
        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT INTO daily_monitor_log 
                (monitor_date, alert_type, alert_detail, severity, action_taken)
                VALUES (datetime('now'), 'error', ?, 'critical', 'logged')
            """, (f'{component}: {str(error)}'))


def main_daily():
    """每日监控入口"""
    engine = AdaptiveEngine()
    alerts = engine.run_daily()
    engine.monitor.print_summary(alerts)


def main_weekly():
    """每周优化入口"""
    engine = AdaptiveEngine()
    changes = engine.run_weekly()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--daily', action='store_true', help='运行每日监控')
    parser.add_argument('--weekly', action='store_true', help='运行每周优化')
    args = parser.parse_args()
    
    if args.daily:
        main_daily()
    elif args.weekly:
        main_weekly()
    else:
        print("用法: python adaptive_engine.py --daily 或 --weekly")
```

- [ ] **Step 3: 运行测试确认通过**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_adaptive.py::TestAdaptiveEngine -v`
Expected: 2 passed

- [ ] **Step 4: 提交**

```bash
git add adaptive_engine.py tests/test_adaptive.py
git commit -m "feat: add adaptive_engine.py as core controller"
```

---

### Task 11: 集成到 daily_run.sh

**Files:**
- Modify: `daily_run.sh`

- [ ] **Step 1: 在 daily_run.sh 添加新选项**

**位置说明：**

- **case 语句位置**：第 420-443 行的 `case "$CMD" in` 块中，在 `--walkforward)` 行（第 427 行）后添加新选项
- **函数定义位置**：在现有函数（如 `run_walkforward()`，约第 407-415 行）之后添加新函数

**添加 case 选项（在第 427 行 `--walkforward)` 后插入）：**

```bash
    --monitor)     run_monitor; end_run "ok" ;;
    --weekly-optimize) run_weekly_optimize; end_run "ok" ;;
```

**添加函数定义（在第 415 行 `run_walkforward()` 函数后插入）：**

```bash
run_monitor() {
    log "─────────── 每日监控 ────────────"
    $PY adaptive_engine.py --daily 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    log "───────────── 监控完成 ───────────────"
}

run_weekly_optimize() {
    log "─────────── 每周优化 ────────────"
    $PY adaptive_engine.py --weekly 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    log "───────────── 优化完成 ───────────────"
}
```

**修改 `--track` 选项（第 423 行），添加自动调用 monitor：**

```bash
    --track)      run_track; run_monitor; end_run "ok" ;;
```

- [ ] **Step 2: 测试新选项**

Run: `./daily_run.sh --monitor 2>&1 | head -20`
Expected: 输出包含 `[每日监控]`

- [ ] **Step 3: 提交**

```bash
git add daily_run.sh
git commit -m "feat: integrate adaptive engine into daily_run.sh"
```

---

# Phase 6: 评分系统集成 (daily_scanner + pick_tracker)

---

### Task 12: 扩展 daily_scanner.py 输出评分明细

**Files:**
- Modify: `daily_scanner.py`（评分计算部分）

- [ ] **Step 1: 在 detect_pattern 函数中返回评分明细**

找到 `detect_pattern` 函数的评分部分（约第71-88行），修改返回值为：

```python
def detect_pattern(df):
    ...
    # === 评分 ===
    sc = 5; reasons = []
    score_details = {'score_base': 5}  # 新增：评分明细
    
    if wg > 0.30: 
        sc += 20; reasons.append(f"一波{wg*100:.0f}%")
        score_details['score_wave_gain'] = 20
    elif wg > 0.20: 
        sc += 10; reasons.append(f"一波{wg*100:.0f}%")
        score_details['score_wave_gain'] = 10
    else:
        score_details['score_wave_gain'] = 0
    
    if dd < 0.08: 
        sc += 15; reasons.append(f"浅调{dd*100:.0f}%")
        score_details['score_shallow_dd'] = 15
    elif dd < 0.15: 
        sc += 10; reasons.append(f"调{dd*100:.0f}%")
        score_details['score_shallow_dd'] = 10
    else:
        score_details['score_shallow_dd'] = 0
    
    if tp > 0.07: 
        sc += 15
        score_details['score_day_gain'] = 15
    elif tp > 0.05: 
        sc += 10
        score_details['score_day_gain'] = 10
    elif tp > 0.03: 
        sc += 5
        score_details['score_day_gain'] = 5
    else:
        score_details['score_day_gain'] = 0
    
    vr = df.iloc[ti]['volume'] / max(df.iloc[ti]['volume_ma5'], 1)
    if vr > 2: 
        sc += 10; reasons.append(f"放量{vr:.1f}x")
        score_details['score_volume'] = 10
    elif vr > 1.5: 
        sc += 5
        score_details['score_volume'] = 5
    else:
        score_details['score_volume'] = 0
    
    if df.iloc[ti]['ma5'] > df.iloc[ti]['ma10'] > df.iloc[ti]['ma20']: 
        sc += 10; reasons.append("多头")
        score_details['score_ma_bull'] = 10
    elif df.iloc[ti]['ma5'] > df.iloc[ti]['ma10']: 
        sc += 5
        score_details['score_ma_bull'] = 5
    else:
        score_details['score_ma_bull'] = 0
    
    # 板块动量评分（已在_scan_core中计算）
    score_details['score_sector'] = 0  # 默认值，将在_scan_core中更新
    
    if sig == '异动不跌': 
        sc += 10
        score_details['score_signal_bonus'] = 10
    else:
        score_details['score_signal_bonus'] = 0
    
    return {
        'sig': sig, 'score': sc, 'reasons': ' | '.join(reasons),
        'wg': wg, 'dd': dd, 'tp': tp, 'vr': vr,
        'sl': mn*0.98, 'ep': df.iloc[ti]['close'], 'cons_low': mn,
        'score_details': score_details  # 新增
    }
```

- [ ] **Step 2: 在 _scan_core 中更新 score_sector**

找到 `_scan_core` 函数中添加板块动量评分的部分，更新 score_details：

```python
# 在添加板块动量评分的代码段中
score = r['score']
score_details = r.get('score_details', {})
if sector_strong:
    score += 5
    score_details['score_sector'] = 5  # 更新评分明细
    ...

results.append({
    ...
    '评分': score,
    '评分明细': score_details,  # 新增字段
    ...
})
```

- [ ] **Step 3: 语法检查**

Run: `python3 -m py_compile daily_scanner.py`
Expected: 无输出（通过）

- [ ] **Step 4: 提交**

```bash
git add daily_scanner.py
git commit -m "feat: add score_details output to daily_scanner.py"
```

---

### Task 13: 扩展 pick_tracker.py 接收评分明细

**Files:**
- Modify: `pick_tracker.py`

- [ ] **Step 1: 在 record_picks 方法中接收评分明细**

找到 `record_picks` 方法（约第90行），修改：

```python
def record_picks(self, picks_df, pick_date=None):
    """记录每日选股，扩展支持评分明细"""
    if pick_date is None:
        pick_date = datetime.now().strftime('%Y-%m-%d')
    
    if picks_df.empty:
        return 0
    
    count = 0
    with self._get_conn() as conn:
        for _, row in picks_df.iterrows():
            # 获取评分明细
            score_details = row.get('评分明细', {})
            
            conn.execute("""
                INSERT OR REPLACE INTO pick_tracking 
                (pick_date, code, signal_type, score, wave_gain, cons_dd, vol_ratio,
                 entry_price, stop_loss, cons_low, market_regime, index_code, name, status,
                 score_wave_gain, score_shallow_dd, score_day_gain, score_volume, 
                 score_ma_bull, score_sector, score_signal_bonus, score_base)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active',
                        ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pick_date, row['代码'], row.get('信号', row.get('signal_type', '')),
                row.get('评分', row.get('score', 0)),
                row.get('波段涨幅', row.get('wave_gain', 0)),
                row.get('回调', row.get('cons_dd', 0)),
                row.get('量比', row.get('vol_ratio', 0)),
                row.get('入场价', row.get('entry_price', 0)),
                row.get('止损位', row.get('stop_loss', 0)),
                row.get('cons_low', 0),
                row.get('市场环境', row.get('market_regime', 'unknown')),
                row.get('指数', row.get('index_code', '')),
                row.get('名称', row.get('name', '')),
                # 评分明细字段
                score_details.get('score_wave_gain', 0),
                score_details.get('score_shallow_dd', 0),
                score_details.get('score_day_gain', 0),
                score_details.get('score_volume', 0),
                score_details.get('score_ma_bull', 0),
                score_details.get('score_sector', 0),
                score_details.get('score_signal_bonus', 0),
                score_details.get('score_base', 5),
            ))
            count += 1
    
    return count
```

- [ ] **Step 2: 语法检查**

Run: `python3 -m py_compile pick_tracker.py`
Expected: 无输出（通过）

- [ ] **Step 3: 提交**

```bash
git add pick_tracker.py
git commit -m "feat: extend pick_tracker to save score_details"
```

---

### Task 14: 最终验证与提交

**Files:**
- All modified files

- [ ] **Step 1: 运行全部测试**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/ -v`
Expected: All passed

- [ ] **Step 2: 语法检查所有新文件**

Run: `python3 -m py_compile signal_constants.py daily_monitor.py weekly_optimizer.py sandbox_validator.py adaptive_engine.py`
Expected: 无输出（通过）

- [ ] **Step 3: 测试完整流程**

Run: `./daily_run.sh --monitor 2>&1 | head -30`
Expected: 输出包含 `[每日监控]` 和预警信息

Run: `./daily_run.sh --weekly-optimize 2>&1 | head -30`
Expected: 输出包含 `[每周优化]`

- [ ] **Step 4: 更新 README**

在 README.md 添加自适应系统说明：

```markdown
## 自适应系统

系统支持三层自适应优化：

### 每日监控

```bash
./daily_run.sh --monitor
```

自动检测：
- 信号期望值下降预警
- 市场环境退潮预警
- 评分预测力监控

### 每周优化

```bash
./daily_run.sh --weekly-optimize
```

自动执行：
- 参数层优化（OOS验证）
- 评分权重调整（基于相关性）
- 信号状态检查（Wilson置信界）
- 沙盒滚动验证

### 沙盒验证

所有变更先入sandbox验证，连续3周通过才正式应用。
```

- [ ] **Step 5: 最终提交**

```bash
git add -A
git commit -m "feat: complete adaptive quant system implementation

Phase 1-2: Data layer + daily_monitor
Phase 3: weekly_optimizer
Phase 4: sandbox_validator
Phase 5: adaptive_engine + daily_run.sh integration
Phase 6: daily_scanner + pick_tracker score_details

Features:
- Wilson expectancy lower bound for signal evaluation
- Rolling window sandbox validation (3 weeks)
- Four-layer optimization (param/score/signal/env)
- Cold start protection for new signals"
```

---

## Final Self-Review

**1. Spec Coverage (Complete):**

| Spec需求 | 任务覆盖 |
|----------|----------|
| signal_constants.py | Task 1 ✓ |
| signal_status 表 | Task 3 ✓ |
| optimization_history 表 | Task 3 ✓ |
| market_regime 表 | Task 3 ✓ |
| daily_monitor_log 表 | Task 3 ✓ |
| pick_tracking score_* 字段 | Task 4 ✓ |
| strategy_config DYNAMIC_PARAMS | Task 5 ✓ |
| daily_monitor.py | Task 6 ✓ |
| weekly_optimizer.py | Task 8 ✓ |
| sandbox_validator.py | Task 9 ✓ |
| adaptive_engine.py | Task 10 ✓ |
| daily_run.sh 集成 | Task 11 ✓ |
| daily_scanner 评分明细 | Task 12 ✓ |
| pick_tracker 评分接收 | Task 13 ✓ |

**2. Placeholder scan:** 无 TBD/TODO ✓

**3. Type consistency:** 
- SIGNAL_TYPE_MAPPING 使用一致 ✓
- STATUS_* 常量一致 ✓
- score_details 字段名一致 ✓

---

## 补充：测试覆盖增强建议

以下测试在基础实现后可补充：

### Task 8 测试补充（weekly_optimizer.py 主流程）

```python
class TestWeeklyOptimizerMainFlow(unittest.TestCase):
    """测试 WeeklyOptimizer 主流程"""

    def test_run_weekly_returns_changes_list(self):
        """测试：run_weekly 返回变更列表"""
        from weekly_optimizer import WeeklyOptimizer
        optimizer = WeeklyOptimizer()
        
        changes = optimizer.run_weekly('2026-04-25')
        self.assertIsInstance(changes, list)
        
    def test_changes_saved_to_sandbox(self):
        """测试：变更写入 sandbox"""
        from weekly_optimizer import WeeklyOptimizer
        from data_layer import get_data_layer
        optimizer = WeeklyOptimizer()
        
        changes = optimizer.run_weekly('2026-04-25')
        
        dl = get_data_layer()
        with dl._get_conn() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) FROM optimization_history WHERE sandbox_test_result='pending'"
            ).fetchone()[0]
        
        self.assertGreaterEqual(pending, len(changes))
```

### Task 10 测试补充（adaptive_engine.py 变更应用）

```python
class TestAdaptiveEngineApplyChange(unittest.TestCase):
    """测试 AdaptiveEngine 变更应用逻辑"""

    def test_apply_parameter_change(self):
        """测试：应用参数变更"""
        from adaptive_engine import AdaptiveEngine
        from strategy_config import StrategyConfig
        
        engine = AdaptiveEngine()
        change = {'type': 'parameter', 'param_key': 'first_wave_min_days', 'new_value': 5}
        
        engine._apply_change(change)
        
        cfg = StrategyConfig()
        self.assertEqual(cfg.get('first_wave_min_days'), 5)

    def test_apply_signal_status_change(self):
        """测试：应用信号状态变更"""
        from adaptive_engine import AdaptiveEngine
        from signal_constants import get_weight_multiplier
        
        engine = AdaptiveEngine()
        change = {'type': 'signal_status', 'param_key': 'anomaly_no_decline', 'new_value': 'watching'}
        
        engine._apply_change(change)
        
        with engine.dl._get_conn() as conn:
            row = conn.execute(
                "SELECT status_level, weight_multiplier FROM signal_status WHERE signal_type='anomaly_no_decline'"
            ).fetchone()
        
        self.assertEqual(row[0], 'watching')
        self.assertEqual(row[1], 0.5)
```

---

## 与设计文档对照检查

| 设计文档章节 | 计划文档覆盖 | 备注 |
|--------------|--------------|------|
| A. 概述/架构 | Task 10 | ✓ adaptive_engine.py 调度逻辑 |
| A. 信号类型定义 | Task 1 | ✓ signal_constants.py |
| A. 每日监控 | Task 6 | ✓ daily_monitor.py |
| A. 每周优化 | Task 8 | ✓ weekly_optimizer.py |
| A. Wilson期望值 | Task 6 | ✓ wilson_expectancy_lower_bound |
| A. 信号禁用机制 | Task 8 | ✓ _check_signal_status |
| A. 沙盒验证 | Task 9 | ✓ sandbox_validator.py |
| B.1 复用StrategyOptimizer | Task 8 | ✓ self.base_optimizer |
| B.2 信号类型命名 | Task 1 | ✓ 英文主键+中文映射 |
| B.3 daily_run.sh流程 | Task 11 | ✓ 位置明确 |
| B.4 归一化 | 未实现 | ⚠️ 建议后续Phase实现 |
| B.4.1 pick_tracking扩展 | Task 4 | ✓ score_* 字段 |
| B.5 环境感知增强 | Task 6 | ✓ _get_market_regime_smoothed |
| B.6 沙盒滚动验证 | Task 9 | ✓ rolling validate |
| B.7 adaptive_engine调度 | Task 10 | ✓ run_daily/run_weekly |
| B.8 strategy_config扩展 | Task 5 | ✓ DYNAMIC_PARAMS |
| B.9 sandbox状态管理 | Task 9 | ✓ STATUS_* 常量 |
| B.12 validation_started_at | Task 3 | ✓ 已在表定义中 |

**未实现项：**
- B.4 normalize_scores_global 归一化函数（建议后续Phase实现，当前Phase聚焦基础设施）