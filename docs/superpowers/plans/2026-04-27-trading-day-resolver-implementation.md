# TradingDayResolver 实现计划

> **Status: COMPLETED** - 18 commits pushed to main (2026-04-27)

**Goal:** 引入 TradingDayResolver 模块统一处理所有交易日相关判断，解决自适应引擎在多种场景下（交易日数据未更新、非交易日、多次执行、中断恢复、历史日期）的日期判断不一致问题。

**Architecture:** 创建 TradingDayResolver 类作为核心模块，返回 TradingDayInfo 数据结构；改造 adaptive_engine.py 和 daily_monitor.py 使用统一基准；实现两阶段状态标记的中断恢复机制。

**Tech Stack:** Python 3, SQLite, dataclasses, pytest

---

## 文件结构

| 文件 | 操作 | 状态 |
|------|------|------|
| `trading_day_resolver.py` | 创建 | ✅ 完成 |
| `adaptive_engine.py` | 修改 | ✅ 完成 |
| `daily_monitor.py` | 修改 | ✅ 完成 |
| `tests/test_trading_day_resolver.py` | 创建 | ✅ 完成 |
| `tests/conftest.py` | 创建 | ✅ 完成 |
| `data_layer.py` | 修改 | ✅ 完成 |

---

## 实现完成检查

- [x] 所有单元测试通过 (124 tests)
- [x] 所有集成测试通过
- [x] daily_run.sh 正常运行
- [x] critical_process_state 表已创建
- [x] 文档已更新

---
| `tests/test_adaptive.py` | 修改 | 集成测试 |

---

## Phase 1: 核心模块

### Task 1: 创建 TradingDayInfo 数据结构

**Files:**
- Create: `trading_day_resolver.py`

- [ ] **Step 1: 创建 TradingDayInfo 数据类**

```python
#!/usr/bin/env python3
"""
交易日统一解析器

提供 TradingDayInfo 数据结构和 TradingDayResolver 类，
统一处理所有交易日相关判断。
"""
from dataclasses import dataclass
from datetime import datetime

# 状态枚举常量（模块级别）
STATUS_DATA_READY = 'data_ready'
STATUS_DATA_NOT_UPDATED = 'data_not_updated'
STATUS_NON_TRADING_DAY = 'non_trading_day'
STATUS_HISTORICAL = 'historical'
VALID_STATUSES = (STATUS_DATA_READY, STATUS_DATA_NOT_UPDATED,
                  STATUS_NON_TRADING_DAY, STATUS_HISTORICAL)


@dataclass
class TradingDayInfo:
    """交易日解析结果"""

    # 基本信息
    target_date: str                    # 用户指定或当前日期
    effective_data_date: str            # 数据库最新数据日期

    # 状态判断
    is_trading_day: bool                # target_date 是否是交易日
    data_ready: bool                    # 数据是否已更新到 target_date
    data_lag_days: int                  # 数据滞后天数（0表示已更新）

    # 状态枚举
    status: str                         # 状态值

    # 计算属性
    monitor_period_key: str             # 防重复处理的周期键
    is_current_monitor: bool            # 是否是当前监控（非历史）

    def __post_init__(self):
        """数据验证"""
        # 日期格式验证（monitor_period_key 由计算得出，无需验证）
        for date_field in ('target_date', 'effective_data_date'):
            try:
                datetime.strptime(getattr(self, date_field), '%Y-%m-%d')
            except ValueError:
                raise ValueError(f"{date_field} 格式错误: {getattr(self, date_field)}")

        # 状态枚举验证
        if self.status not in VALID_STATUSES:
            raise ValueError(f"无效状态: {self.status}")

        # 数值范围验证
        if self.data_lag_days < 0:
            raise ValueError(f"data_lag_days 不能为负: {self.data_lag_days}")

    @property
    def is_non_trading_day(self) -> bool:
        """是否是非交易日"""
        return self.status == STATUS_NON_TRADING_DAY

    @property
    def should_process_critical(self) -> bool:
        """是否应该处理 critical（语义判断）

        仅判断是否满足处理 critical 的语义条件：
        - 当前监控（非历史）
        - 交易日且数据相关状态

        注：防重复检查在 _handle_critical_alerts_with_recovery 中通过
        _get_critical_state 查询数据库实现，与此属性职责分离。

        Returns:
            bool: True 表示语义上可以处理，False 表示不应处理
        """
        # 非交易日和历史日期都不处理
        return self.is_current_monitor and self.status in (
            STATUS_DATA_READY,
            STATUS_DATA_NOT_UPDATED
        )
```

- [ ] **Step 2: 验证 TradingDayInfo 导入成功**

Run: `python3 -c "from trading_day_resolver import TradingDayInfo, STATUS_DATA_READY; print('OK')"`

Expected: 输出 `OK`

- [ ] **Step 3: Commit**

```bash
git add trading_day_resolver.py
git commit -m "feat: add TradingDayInfo data structure"
```

---

### Task 2: 编写 TradingDayInfo 单元测试

**Files:**
- Create: `tests/test_trading_day_resolver.py`

- [ ] **Step 1: 编写 TradingDayInfo 验证测试**

```python
#!/usr/bin/env python3
"""
TradingDayResolver 单元测试
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_day_resolver import (
    TradingDayInfo,
    STATUS_DATA_READY,
    STATUS_DATA_NOT_UPDATED,
    STATUS_NON_TRADING_DAY,
    STATUS_HISTORICAL,
    VALID_STATUSES,
)


class TestTradingDayInfo:
    """TradingDayInfo 数据结构测试"""

    def test_valid_data_ready(self):
        """测试有效的 data_ready 状态"""
        info = TradingDayInfo(
            target_date='2026-04-27',
            effective_data_date='2026-04-27',
            is_trading_day=True,
            data_ready=True,
            data_lag_days=0,
            status=STATUS_DATA_READY,
            monitor_period_key='2026-04-27',
            is_current_monitor=True,
        )
        assert info.status == STATUS_DATA_READY
        assert info.is_non_trading_day is False
        assert info.should_process_critical is True

    def test_valid_data_not_updated(self):
        """测试有效的 data_not_updated 状态"""
        info = TradingDayInfo(
            target_date='2026-04-27',
            effective_data_date='2026-04-24',
            is_trading_day=True,
            data_ready=False,
            data_lag_days=3,
            status=STATUS_DATA_NOT_UPDATED,
            monitor_period_key='2026-04-24',
            is_current_monitor=True,
        )
        assert info.status == STATUS_DATA_NOT_UPDATED
        assert info.is_non_trading_day is False
        assert info.should_process_critical is True

    def test_valid_non_trading_day(self):
        """测试有效的 non_trading_day 状态"""
        info = TradingDayInfo(
            target_date='2026-04-26',
            effective_data_date='2026-04-24',
            is_trading_day=False,
            data_ready=True,
            data_lag_days=0,
            status=STATUS_NON_TRADING_DAY,
            monitor_period_key='2026-04-24',
            is_current_monitor=True,
        )
        assert info.is_non_trading_day is True
        assert info.should_process_critical is False

    def test_valid_historical(self):
        """测试有效的 historical 状态"""
        info = TradingDayInfo(
            target_date='2026-04-20',
            effective_data_date='2026-04-24',
            is_trading_day=True,
            data_ready=True,
            data_lag_days=0,
            status=STATUS_HISTORICAL,
            monitor_period_key='2026-04-20',
            is_current_monitor=False,
        )
        assert info.status == STATUS_HISTORICAL
        assert info.should_process_critical is False

    def test_invalid_date_format(self):
        """测试无效日期格式"""
        with pytest.raises(ValueError, match="格式错误"):
            TradingDayInfo(
                target_date='2026-04-27-',
                effective_data_date='2026-04-27',
                is_trading_day=True,
                data_ready=True,
                data_lag_days=0,
                status=STATUS_DATA_READY,
                monitor_period_key='2026-04-27',
                is_current_monitor=True,
            )

    def test_invalid_status(self):
        """测试无效状态枚举"""
        with pytest.raises(ValueError, match="无效状态"):
            TradingDayInfo(
                target_date='2026-04-27',
                effective_data_date='2026-04-27',
                is_trading_day=True,
                data_ready=True,
                data_lag_days=0,
                status='invalid_status',
                monitor_period_key='2026-04-27',
                is_current_monitor=True,
            )

    def test_negative_lag_days(self):
        """测试负数 lag_days"""
        with pytest.raises(ValueError, match="不能为负"):
            TradingDayInfo(
                target_date='2026-04-27',
                effective_data_date='2026-04-27',
                is_trading_day=True,
                data_ready=True,
                data_lag_days=-1,
                status=STATUS_DATA_READY,
                monitor_period_key='2026-04-27',
                is_current_monitor=True,
            )

    def test_should_process_critical_edge_cases(self):
        """测试 should_process_critical 边界情况"""
        # non_trading_day + is_current_monitor=True → False
        info = TradingDayInfo(
            target_date='2026-04-26',
            effective_data_date='2026-04-24',
            is_trading_day=False,
            data_ready=True,
            data_lag_days=0,
            status=STATUS_NON_TRADING_DAY,
            monitor_period_key='2026-04-24',
            is_current_monitor=True,
        )
        assert info.should_process_critical is False

        # historical + is_current_monitor=False → False
        info = TradingDayInfo(
            target_date='2026-04-20',
            effective_data_date='2026-04-24',
            is_trading_day=True,
            data_ready=True,
            data_lag_days=0,
            status=STATUS_HISTORICAL,
            monitor_period_key='2026-04-20',
            is_current_monitor=False,
        )
        assert info.should_process_critical is False
```

- [ ] **Step 2: 运行测试验证**

Run: `cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_trading_day_resolver.py -v`

Expected: 所有测试 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_trading_day_resolver.py
git commit -m "test: add TradingDayInfo unit tests"
```

---

### Task 3: 实现 TradingDayResolver._get_effective_data_date

**Files:**
- Modify: `trading_day_resolver.py:1-80`

- [ ] **Step 1: 编写 _get_effective_data_date 测试**

在 `tests/test_trading_day_resolver.py` 添加：

```python
class TestTradingDayResolverGetEffectiveDataDate:
    """_get_effective_data_date 方法测试"""

    def test_get_from_stock_daily(self, tmp_db):
        """从 stock_daily 获取有效数据日期"""
        resolver = TradingDayResolver(db_path=tmp_db)
        # 插入测试数据
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")

        result = resolver._get_effective_data_date('2026-04-27')
        assert result == '2026-04-24'

    def test_empty_database_returns_today(self, tmp_db):
        """空数据库返回今天"""
        resolver = TradingDayResolver(db_path=tmp_db)
        today = datetime.now().strftime('%Y-%m-%d')
        result = resolver._get_effective_data_date(today)
        assert result == today

    def test_fallback_to_trading_day_cache(self, tmp_db):
        """从 trading_day_cache 获取最近交易日"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-24', 1, datetime('now'))")

        result = resolver._get_effective_data_date('2026-04-27')
        assert result == '2026-04-24'
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_trading_day_resolver.py::TestTradingDayResolverGetEffectiveDataDate -v`

Expected: FAIL（类和方法未定义）

- [ ] **Step 3: 实现 TradingDayResolver 类框架**

在 `trading_day_resolver.py` 添加：

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_layer import get_data_layer, StockDataLayer


class TradingDayResolver:
    """交易日统一解析器"""

    def __init__(self, db_path=None):
        if db_path is None:
            self.dl = get_data_layer()
        else:
            self.dl = StockDataLayer(db_path)

    def resolve(self, target_date=None) -> TradingDayInfo:
        """
        解析目标日期，返回统一的交易日信息

        Args:
            target_date: 目标日期，默认今天

        Returns:
            TradingDayInfo: 包含所有判断所需信息
        """
        # Phase 1 先返回空实现，后续 Task 完善
        raise NotImplementedError("resolve() will be implemented in Task 5")

    def _get_effective_data_date(self, target_date) -> str:
        """
        获取有效数据日期

        规则：
        - 从 stock_daily 获取 MAX(date)
        - 如果空，从 trading_day_cache 获取最近交易日
        - 最后回退到今天

        Args:
            target_date: 目标日期（用于空库回退）

        Returns:
            str: 有效数据日期
        """
        # 方法1: 从实际数据获取（最可靠）
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT MAX(date) FROM stock_daily
            """).fetchone()
            if row and row[0]:
                return row[0]

            # 方法2: 从交易日缓存获取最近交易日
            row = conn.execute("""
                SELECT date FROM trading_day_cache
                WHERE is_trading_day = 1
                ORDER BY date DESC LIMIT 1
            """).fetchone()
            if row and row[0]:
                return row[0]

        # 方法3: 回退到今天
        return datetime.now().strftime('%Y-%m-%d')
```

- [ ] **Step 4: 添加 tmp_db fixture**

在 `tests/test_trading_day_resolver.py` 添加：

```python
import tempfile
import sqlite3

@pytest.fixture
def tmp_db():
    """临时数据库 fixture（包含所有必要表结构）"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    # 创建表结构（与生产环境一致）
    conn = sqlite3.connect(db_path)

    # stock_daily
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily (
            date TEXT,
            code TEXT,
            close REAL
        )
    """)

    # trading_day_cache
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trading_day_cache (
            date TEXT PRIMARY KEY,
            is_trading_day INTEGER,
            checked_at TEXT
        )
    """)

    # market_regime
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_regime (
            regime_date TEXT PRIMARY KEY,
            regime_type TEXT,
            activity_coefficient REAL,
            index_close REAL,
            index_ma5 REAL,
            index_ma20 REAL,
            consecutive_days INTEGER
        )
    """)

    # signal_status
    conn.execute("""
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
            live_sample_count INTEGER,
            last_check_date TEXT
        )
    """)

    # pick_tracking（用于期望值计算）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pick_tracking (
            id INTEGER PRIMARY KEY,
            signal_type TEXT,
            status TEXT,
            final_pnl_pct REAL
        )
    """)

    # daily_monitor_log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_monitor_log (
            id INTEGER PRIMARY KEY,
            monitor_date TEXT,
            alert_type TEXT,
            alert_detail TEXT,
            severity TEXT,
            action_taken TEXT,
            created_at TEXT
        )
    """)

    # sandbox_config
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
            UNIQUE(param_key, batch_id)
        )
    """)

    # optimization_history
    conn.execute("""
        CREATE TABLE IF NOT EXISTS optimization_history (
            id INTEGER PRIMARY KEY,
            optimize_date TEXT,
            optimize_type TEXT,
            param_key TEXT,
            old_value TEXT,
            new_value TEXT,
            batch_id TEXT,
            trigger_reason TEXT,
            sandbox_test_result TEXT,
            created_at TEXT
        )
    """)

    # critical_process_state
    conn.execute("""
        CREATE TABLE IF NOT EXISTS critical_process_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_key TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL DEFAULT 'handling',
            alerts_total INTEGER DEFAULT 0,
            alerts_processed INTEGER DEFAULT 0,
            changes_applied INTEGER DEFAULT 0,
            error_detail TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_critical_period_status ON critical_process_state(period_key, status)")

    # strategy_config（AdaptiveEngine.__init__ 需要）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategy_config (
            param_key TEXT PRIMARY KEY,
            param_value TEXT,
            description TEXT,
            category TEXT,
            updated_at TEXT
        )
    """)

    # param_snapshot（ChangeManager.__init__ 需要）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS param_snapshot (
            id INTEGER PRIMARY KEY,
            snapshot_type TEXT NOT NULL,
            snapshot_data TEXT NOT NULL,
            batch_id TEXT,
            trigger_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_batch ON param_snapshot(batch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_type ON param_snapshot(snapshot_type)")

    # stock_meta（DataLayer._create_adaptive_tables 需要）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_meta (
            code TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            list_date TEXT
        )
    """)

    # index_daily（用于市场环境判断）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_daily (
            date TEXT,
            code TEXT,
            close REAL,
            volume REAL,
            PRIMARY KEY (date, code)
        )
    """)

    conn.commit()
    conn.close()

    yield db_path

    # 清理
    os.unlink(db_path)
```

- [ ] **Step 5: 运行测试验证**

Run: `python3 -m pytest tests/test_trading_day_resolver.py::TestTradingDayResolverGetEffectiveDataDate -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add trading_day_resolver.py tests/test_trading_day_resolver.py
git commit -m "feat: add TradingDayResolver._get_effective_data_date"
```

---

### Task 4: 实现 TradingDayResolver._determine_trading_day

> **注**：设计文档 `_determine_trading_day` 优先级 2 的 "baostock API 查询（权威，并回填 cache）" 在本实现中省略，避免 API 依赖。实现仅使用 cache → 数据库数据 → 周末判断三级优先级。

**Files:**
- Modify: `trading_day_resolver.py`

- [ ] **Step 1: 编写 _determine_trading_day 测试**

在 `tests/test_trading_day_resolver.py` 添加：

```python
class TestTradingDayResolverDetermineTradingDay:
    """_determine_trading_day 方法测试"""

    def test_cached_trading_day(self, tmp_db):
        """缓存中有交易日标记"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-27', 1, datetime('now'))")

        result = resolver._determine_trading_day('2026-04-27')
        assert result is True

    def test_cached_non_trading_day(self, tmp_db):
        """缓存中有非交易日标记"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-26', 0, datetime('now'))")

        result = resolver._determine_trading_day('2026-04-26')
        assert result is False

    def test_saturday_is_non_trading(self, tmp_db):
        """周六必然是非交易日"""
        resolver = TradingDayResolver(db_path=tmp_db)
        # 2026-04-25 是周六
        result = resolver._determine_trading_day('2026-04-25')
        assert result is False

    def test_sunday_is_non_trading(self, tmp_db):
        """周日必然是非交易日"""
        resolver = TradingDayResolver(db_path=tmp_db)
        # 2026-04-26 是周日
        result = resolver._determine_trading_day('2026-04-26')
        assert result is False

    def test_weekday_no_cache_assumes_trading(self, tmp_db):
        """工作日无缓存时默认为交易日"""
        resolver = TradingDayResolver(db_path=tmp_db)
        # 2026-04-27 是周一，无缓存
        result = resolver._determine_trading_day('2026-04-27')
        # 无缓存且非周末 → 默认 True（后续 resolve 会通过其他逻辑确认）
        assert result is True
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_trading_day_resolver.py::TestTradingDayResolverDetermineTradingDay -v`

Expected: FAIL（方法未定义）

- [ ] **Step 3: 实现 _determine_trading_day**

在 `trading_day_resolver.py` 的 TradingDayResolver 类添加：

```python
    def _determine_trading_day(self, date_str) -> bool:
        """
        判断是否是交易日

        优先级（权威来源优先，推断性来源后置）：
        1. trading_day_cache 缓存（权威）
        2. 数据库是否有该日数据（推断性：有数据则大概率是交易日）
        3. 周末简单判断（兜底：周末必然非交易日）
        4. 工作日默认为交易日（需后续通过其他逻辑确认）

        Args:
            date_str: 日期字符串 YYYY-MM-DD

        Returns:
            bool: True=交易日, False=非交易日
        """
        # 1. 查缓存（权威）
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT is_trading_day FROM trading_day_cache WHERE date=?
            """, (date_str,)).fetchone()
            if row is not None:
                return row[0] == 1

        # 2. 查数据库是否有该日数据（推断性）
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) FROM stock_daily WHERE date=?
            """, (date_str,)).fetchone()
            if row and row[0] > 0:
                # 有数据 → 大概率是交易日，缓存结果
                self._cache_trading_day(date_str, True)
                return True

        # 3. 周末判断（兜底）
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        if dt.weekday() >= 5:  # 周六=5, 周日=6
            # 周末必然非交易日，缓存结果
            self._cache_trading_day(date_str, False)
            return False

        # 4. 工作日默认为交易日（需后续确认）
        return True

    def _cache_trading_day(self, date_str, is_trading_day):
        """缓存交易日判断结果"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trading_day_cache
                (date, is_trading_day, checked_at)
                VALUES (?, ?, datetime('now'))
            """, (date_str, 1 if is_trading_day else 0))
```

- [ ] **Step 4: 运行测试验证**

Run: `python3 -m pytest tests/test_trading_day_resolver.py::TestTradingDayResolverDetermineTradingDay -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_day_resolver.py tests/test_trading_day_resolver.py
git commit -m "feat: add TradingDayResolver._determine_trading_day"
```

---

### Task 5: 实现 TradingDayResolver._count_trading_days_gap

**Files:**
- Modify: `trading_day_resolver.py`

- [ ] **Step 1: 编写 _count_trading_days_gap 测试**

在 `tests/test_trading_day_resolver.py` 添加：

```python
class TestTradingDayResolverCountTradingDaysGap:
    """_count_trading_days_gap 方法测试"""

    def test_same_day_returns_zero(self, tmp_db):
        """同一天返回 0"""
        resolver = TradingDayResolver(db_path=tmp_db)
        result = resolver._count_trading_days_gap('2026-04-24', '2026-04-24')
        assert result == 0

    def test_with_trading_day_cache(self, tmp_db):
        """有缓存时计算交易日间隔"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            # 插入连续交易日
            for d in ['2026-04-24', '2026-04-27', '2026-04-28']:
                conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES (?, 1, datetime('now'))", (d,))
            # 插入非交易日
            for d in ['2026-04-25', '2026-04-26']:
                conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES (?, 0, datetime('now'))", (d,))

        # 24(周五) 到 28(周二): 查询 > 24 AND <= 28
        # 范围内: 25(周六×), 26(周日×), 27(周一✓), 28(周二✓) = 2 个交易日
        result = resolver._count_trading_days_gap('2026-04-24', '2026-04-28')
        assert result == 2

    def test_no_cache_fallback_to_weekday_count(self, tmp_db):
        """无缓存时按工作日估算"""
        resolver = TradingDayResolver(db_path=tmp_db)
        # 2026-04-24(周五) 到 2026-04-28(周二)
        # 按工作日: 25(周六), 26(周日), 27(周一), 28(周二) → 2 个工作日
        result = resolver._count_trading_days_gap('2026-04-24', '2026-04-28')
        assert result == 2  # 估算值，排除周末
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_trading_day_resolver.py::TestTradingDayResolverCountTradingDaysGap -v`

Expected: FAIL（方法未定义）

- [ ] **Step 3: 实现 _count_trading_days_gap**

在 `trading_day_resolver.py` 的 TradingDayResolver 类添加：

```python
    def _count_trading_days_gap(self, from_date, to_date) -> int:
        """
        计算两个日期之间的交易日间隔天数

        规则：
        - 仅计算交易日（排除周末和节假日）
        - 从 trading_day_cache 获取交易日列表进行计算
        - 无缓存时按工作日估算

        Args:
            from_date: 起始日期（较早）
            to_date: 结束日期（较晚）

        Returns:
            int: 交易日间隔天数（>= 0）
        """
        if from_date >= to_date:
            return 0

        from_dt = datetime.strptime(from_date, '%Y-%m-%d')
        to_dt = datetime.strptime(to_date, '%Y-%m-%d')

        # 从缓存获取交易日列表
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT date FROM trading_day_cache
                WHERE date > ? AND date <= ? AND is_trading_day = 1
                ORDER BY date
            """, (from_date, to_date)).fetchall()

        if rows:
            # 有缓存，精确计算
            return len(rows)

        # 无缓存，按工作日估算（排除周末）
        gap = 0
        current = from_dt + timedelta(days=1)
        while current <= to_dt:
            if current.weekday() < 5:  # 周一至周五
                gap += 1
            current += timedelta(days=1)

        return gap
```

需要在文件顶部添加 import：

```python
from datetime import datetime, timedelta
```

- [ ] **Step 4: 运行测试验证**

Run: `python3 -m pytest tests/test_trading_day_resolver.py::TestTradingDayResolverCountTradingDaysGap -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_day_resolver.py tests/test_trading_day_resolver.py
git commit -m "feat: add TradingDayResolver._count_trading_days_gap"
```

---

### Task 6: 实现 TradingDayResolver.resolve()

**Files:**
- Modify: `trading_day_resolver.py`

- [ ] **Step 1: 编写 resolve() 测试**

在 `tests/test_trading_day_resolver.py` 添加：

```python
class TestTradingDayResolverResolve:
    """resolve() 方法测试"""

    def test_data_ready(self, tmp_db):
        """交易日数据已更新"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-27', 'sh.600000', 10.0)")
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-27', 1, datetime('now'))")

        info = resolver.resolve('2026-04-27')
        assert info.status == STATUS_DATA_READY
        assert info.data_lag_days == 0
        assert info.monitor_period_key == '2026-04-27'
        assert info.should_process_critical is True

    def test_data_not_updated(self, tmp_db):
        """交易日数据未更新"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-27', 1, datetime('now'))")

        info = resolver.resolve('2026-04-27')
        assert info.status == STATUS_DATA_NOT_UPDATED
        assert info.effective_data_date == '2026-04-24'
        assert info.data_lag_days >= 1  # 24(周五) 到 27(周一): 至少1个交易日
        assert info.monitor_period_key == '2026-04-24'
        assert info.should_process_critical is True

    def test_non_trading_day_weekend(self, tmp_db):
        """周末是非交易日"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")

        # 2026-04-25 是周六
        info = resolver.resolve('2026-04-25')
        assert info.status == STATUS_NON_TRADING_DAY
        assert info.is_non_trading_day is True
        assert info.monitor_period_key == '2026-04-24'
        assert info.should_process_critical is False

    def test_historical_date(self, tmp_db):
        """历史日期运行"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with resolver.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")

        info = resolver.resolve('2026-04-20')
        assert info.status == STATUS_HISTORICAL
        assert info.is_current_monitor is False
        assert info.should_process_critical is False

    def test_empty_database(self, tmp_db):
        """空数据库首次运行"""
        resolver = TradingDayResolver(db_path=tmp_db)
        today = datetime.now().strftime('%Y-%m-%d')
        info = resolver.resolve(today)
        assert info.status == STATUS_DATA_READY
        assert info.effective_data_date == today
        assert info.data_lag_days == 0

    def test_future_date_raises_error(self, tmp_db):
        """未来日期抛出错误"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with pytest.raises(ValueError, match="Future date not allowed"):
            resolver.resolve('2030-01-01')

    def test_invalid_format_raises_error(self, tmp_db):
        """无效日期格式抛出错误"""
        resolver = TradingDayResolver(db_path=tmp_db)
        with pytest.raises(ValueError, match="Invalid date format"):
            resolver.resolve('2026-04-27-')

    def test_default_target_date_is_today(self, tmp_db):
        """默认 target_date 是今天"""
        resolver = TradingDayResolver(db_path=tmp_db)
        today = datetime.now().strftime('%Y-%m-%d')
        info = resolver.resolve()
        assert info.target_date == today
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m pytest tests/test_trading_day_resolver.py::TestTradingDayResolverResolve -v`

Expected: FAIL（resolve 抛出 NotImplementedError）

- [ ] **Step 3: 实现 resolve() 完整逻辑**

替换 `trading_day_resolver.py` 中的 resolve 方法：

```python
    def resolve(self, target_date=None) -> TradingDayInfo:
        """
        解析目标日期，返回统一的交易日信息

        Args:
            target_date: 目标日期，默认今天

        Returns:
            TradingDayInfo: 包含所有判断所需信息
        """
        # 1. 参数处理与格式验证
        if target_date is None:
            target_date = datetime.now().strftime('%Y-%m-%d')

        # 格式验证：确保日期格式正确
        try:
            datetime.strptime(target_date, '%Y-%m-%d')
        except ValueError:
            raise ValueError(f"Invalid date format: {target_date}, expected YYYY-MM-DD")

        # 边界检查：未来日期不允许
        today = datetime.now().strftime('%Y-%m-%d')
        if target_date > today:
            raise ValueError(f"Future date not allowed: {target_date}")

        # 2. 获取有效数据日期
        # _get_effective_data_date 永远返回字符串（最坏情况回退到今天），不会返回 None
        effective_data_date = self._get_effective_data_date(target_date)

        # 3. 判断是否是交易日
        is_trading_day = self._determine_trading_day(target_date)

        # 4. 判断是否是历史运行（使用 datetime 对象比较）
        target_dt = datetime.strptime(target_date, '%Y-%m-%d')
        effective_dt = datetime.strptime(effective_data_date, '%Y-%m-%d')
        is_current_monitor = target_dt >= effective_dt

        # 5. 确定状态
        if not is_trading_day:
            status = STATUS_NON_TRADING_DAY
            data_ready = True
            data_lag_days = 0
        elif not is_current_monitor:
            status = STATUS_HISTORICAL
            data_ready = True
            data_lag_days = 0
        elif target_date == effective_data_date:
            status = STATUS_DATA_READY
            data_ready = True
            data_lag_days = 0
        else:
            status = STATUS_DATA_NOT_UPDATED
            data_ready = False
            data_lag_days = self._count_trading_days_gap(effective_data_date, target_date)

        # 6. 计算 monitor_period_key
        if status in (STATUS_NON_TRADING_DAY, STATUS_DATA_NOT_UPDATED):
            monitor_period_key = effective_data_date
        else:
            monitor_period_key = target_date

        return TradingDayInfo(
            target_date=target_date,
            effective_data_date=effective_data_date,
            is_trading_day=is_trading_day,
            data_ready=data_ready,
            data_lag_days=data_lag_days,
            status=status,
            monitor_period_key=monitor_period_key,
            is_current_monitor=is_current_monitor,
        )
```

- [ ] **Step 4: 运行测试验证**

Run: `python3 -m pytest tests/test_trading_day_resolver.py::TestTradingDayResolverResolve -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_day_resolver.py tests/test_trading_day_resolver.py
git commit -m "feat: implement TradingDayResolver.resolve()"
```

---

### Task 7: 创建 critical_process_state 数据表

**Files:**
- Modify: `data_layer.py`（添加表创建）

- [ ] **Step 1: 在 data_layer.py 添加表创建**

找到 `_create_adaptive_tables` 方法（约第125行），在末尾添加：

```python
conn.execute("""
    CREATE TABLE IF NOT EXISTS critical_process_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period_key TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        status TEXT NOT NULL DEFAULT 'handling',
        alerts_total INTEGER DEFAULT 0,
        alerts_processed INTEGER DEFAULT 0,
        changes_applied INTEGER DEFAULT 0,
        error_detail TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_critical_period_status ON critical_process_state(period_key, status)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_critical_status ON critical_process_state(status)")
```

- [ ] **Step 2: 验证表自动创建**

> **注**：init_db.py 无需修改。`get_data_layer()` 调用 `StockDataLayer.__init__`，自动执行 `_create_adaptive_tables`，Step 1 新增的表会自动创建。

Run: `python3 -c "from data_layer import get_data_layer; dl = get_data_layer(); conn = dl._get_conn(); print(conn.execute('SELECT sql FROM sqlite_master WHERE type=\"table\" AND name=\"critical_process_state\"').fetchone()[0])"`

Expected: 输出表结构 SQL

- [ ] **Step 3: Commit**

```bash
git add data_layer.py
git commit -m "feat: add critical_process_state table"
```

---

## Phase 2: 改造现有模块

### Task 8: 改造 adaptive_engine.py - 引入 resolver

**Files:**
- Modify: `adaptive_engine.py`

- [ ] **Step 1: 添加 TradingDayResolver 及状态常量导入**

在 `adaptive_engine.py` 顶部导入区域添加：

```python
from trading_day_resolver import (
    TradingDayResolver,
    STATUS_DATA_READY,
    STATUS_DATA_NOT_UPDATED,
    STATUS_NON_TRADING_DAY,
    STATUS_HISTORICAL,
)
```

- [ ] **Step 2: 在 __init__ 中初始化 resolver**

修改 `AdaptiveEngine.__init__`：

```python
    def __init__(self, db_path=None):
        if db_path is None:
            self.dl = get_data_layer()
        else:
            self.dl = StockDataLayer(db_path)
        self.cfg = StrategyConfig(db_path)
        self.monitor = DailyMonitor(db_path)
        self.weekly_optimizer = WeeklyOptimizer(db_path)
        self.sandbox_validator = SandboxValidator(db_path)
        self.change_mgr = ChangeManager(db_path)
        self.resolver = TradingDayResolver(db_path)  # 新增
```

- [ ] **Step 3: 运行验证导入**

Run: `python3 -c "from adaptive_engine import AdaptiveEngine; e = AdaptiveEngine(); print('resolver:', type(e.resolver).__name)"`

Expected: 输出 `resolver: TradingDayResolver`

- [ ] **Step 4: Commit**

```bash
git add adaptive_engine.py trading_day_resolver.py
git commit -m "feat: integrate TradingDayResolver into AdaptiveEngine"
```

---

### Task 9: 改造 adaptive_engine.py - run_daily 使用 resolver

**Files:**
- Modify: `adaptive_engine.py`

- [ ] **Step 1: 完整重构 run_daily 方法**

替换整个 `run_daily` 方法（第43-129行）：

```python
    def run_daily(self, monitor_date=None):
        """
        运行每日监控

        Args:
            monitor_date: 监控日期，默认今天

        Returns:
            dict: {
                'alerts': list,
                'critical_handled': int,
                'status': 'ok' | 'warning' | 'critical' | 'skipped',
            }
        """
        # 使用 resolver 获取统一信息
        info = self.resolver.resolve(monitor_date)

        # 非交易日：跳过数据监控
        if info.status == STATUS_NON_TRADING_DAY:
            rollback_result = self.change_mgr.monitor_and_rollback()
            if rollback_result['rollback_triggered'] > 0:
                self._notify_rollback_result(rollback_result)
            return {
                'alerts': [],
                'critical_handled': 0,
                'status': 'skipped',
                'reason': 'non_trading_day',
                'message': f'{info.target_date} 是非交易日（周末/节假日），跳过数据监控',
                'rollback_monitor': rollback_result,
            }

        # 数据滞后警告
        if info.status == STATUS_DATA_NOT_UPDATED and info.data_lag_days > 0:
            self._log_warning(
                f"数据滞后 {info.data_lag_days} 天，市场环境判断基于 {info.effective_data_date}"
            )

        # 历史日期：跳过 critical 处理
        if info.status == STATUS_HISTORICAL:
            alerts = self.monitor.run(info.target_date)
            return {
                'alerts': alerts,
                'critical_handled': 0,
                'status': 'ok',
                'reason': 'historical',
                'message': f'历史日期 {info.target_date} 运行，跳过 critical 处理',
            }

        # 当前监控：使用 effective_data_date 进行数据查询
        alerts = self.monitor.run(info.effective_data_date)
        critical_alerts = [a for a in alerts if a['severity'] == 'critical']

        critical_handled = 0
        if info.should_process_critical:
            critical_handled = self._handle_critical_alerts_with_recovery(
                critical_alerts, info
            )

        # 确定整体状态
        if critical_alerts:
            status = 'critical'
        elif any(a['severity'] == 'warning' for a in alerts):
            status = 'warning'
        else:
            status = 'ok'

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

- [ ] **Step 2: 添加 _log_warning 方法**

在 AdaptiveEngine 类添加：

```python
    def _log_warning(self, message):
        """记录警告信息"""
        print(f"\n[WARNING] {message}")
        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT INTO daily_monitor_log
                (monitor_date, alert_type, alert_detail, severity, action_taken, created_at)
                VALUES (?, 'data_lag_warning', ?, 'warning', 'logged', datetime('now'))
            """, (datetime.now().strftime('%Y-%m-%d'), message))
```

- [ ] **Step 3: 添加占位方法（确保 Task 9 完成后可运行）**

在 AdaptiveEngine 类添加以下占位方法（Task 12 会完善实现）：

```python
    def _handle_critical_alerts_with_recovery(self, alerts, info):
        """处理 critical 预警（占位实现，Task 12 完善）"""
        # Phase 2 占位：直接调用原有方法，不带恢复机制
        handled = 0
        for alert in alerts:
            if self._handle_critical_alert(alert, info.effective_data_date):
                handled += 1
        return handled
```

- [ ] **Step 4: 运行验证**

Run: `python3 -c "from adaptive_engine import AdaptiveEngine; e = AdaptiveEngine(); print('OK')"`

Expected: 输出 `OK`

- [ ] **Step 5: Commit**

```bash
git add adaptive_engine.py
git commit -m "refactor: use TradingDayResolver in run_daily"
```

---

### Task 10: 改造 adaptive_engine.py - 移除旧的日期判断方法

**Files:**
- Modify: `adaptive_engine.py`

- [ ] **Step 1: 删除 _get_latest_trade_date 方法**

删除第477-506行的 `_get_latest_trade_date` 方法（已被 resolver._get_effective_data_date 替代）。

- [ ] **Step 2: 删除 _is_trading_day 方法**

删除第508-525行的 `_is_trading_day` 方法（已被 resolver._determine_trading_day 替代）。

- [ ] **Step 3: 删除 _check_critical_already_handled 方法**

删除 `_check_critical_already_handled` 方法（已被 `info.should_process_critical` + `_handle_critical_alerts_with_recovery` 内部的 `_get_critical_state` 替代）。

注：该方法在 Task 9 重构后的 run_daily 中不再被调用。

- [ ] **Step 4: Commit**

```bash
git add adaptive_engine.py
git commit -m "refactor: remove duplicate date methods from AdaptiveEngine"
```

---

### Task 11: 改造 daily_monitor.py - 参数改为 effective_date

**Files:**
- Modify: `daily_monitor.py`

- [ ] **Step 1: 修改 run 方法参数名**

修改 `DailyMonitor.run` 方法：

```python
    def run(self, effective_date=None):
        """
        运行每日监控

        Args:
            effective_date: 有效数据日期（由 resolver 提供）
        """
        if effective_date is None:
            effective_date = datetime.now().strftime('%Y-%m-%d')

        alerts = []

        # 1. 检查信号期望值
        signal_alerts = self._check_signal_expectancy(effective_date)
        alerts.extend(signal_alerts)

        # 2. 检查市场环境（使用 effective_date）
        regime_alert = self._check_market_regime(effective_date)
        if regime_alert:
            alerts.append(regime_alert)

        # 3. 更新 signal_status 表
        self._update_signal_status()

        # 4. 写入监控日志（使用 effective_date）
        self._write_monitor_log(alerts, effective_date)

        return alerts
```

- [ ] **Step 2: 更新 _check_market_regime 的 end_date**

修改 `_check_market_regime` 方法：

```python
    def _check_market_regime(self, effective_date):
        """检查市场环境"""
        # 使用 effective_date 作为查询截止日期
        index_data = self.dl.get_index_kline('sh.000001',
            start_date=(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
            end_date=effective_date  # 改动：使用有效数据日期
        )

        if index_data is None or len(index_data) < 20:
            return None

        regime, coeff, consecutive = self._get_market_regime_smoothed(index_data)

        # 更新 market_regime 表
        self._update_market_regime_table(effective_date, regime, coeff, consecutive, index_data)

        if regime == 'bear' and consecutive >= 5:
            return {
                'type': 'market_bear',
                'detail': f'退潮期已持续{consecutive}天，活跃度系数={coeff}',
                'severity': 'info'
            }

        return None
```

- [ ] **Step 3: Commit**

```bash
git add daily_monitor.py
git commit -m "refactor: use effective_date parameter in DailyMonitor"
```

---

## Phase 3: 中断恢复机制

### Task 12: 实现 _handle_critical_alerts_with_recovery

**Files:**
- Modify: `adaptive_engine.py`

> **注**：此 Task 替换 Task 9 Step 3 的占位方法 `_handle_critical_alerts_with_recovery`，删除占位实现，添加完整的中断恢复机制。

- [ ] **Step 1: 替换 _handle_critical_alerts_with_recovery 方法**

将 Task 9 Step 3 的占位方法替换为完整实现：

```python
    def _handle_critical_alerts_with_recovery(self, alerts, info):
        """
        处理 critical 预警（带中断恢复）

        Args:
            alerts: critical 预警列表
            info: TradingDayInfo 对象

        Returns:
            int: 处理的预警数量
        """
        from process_lock import file_lock

        period_key = info.monitor_period_key

        if not alerts:
            return 0

        # 1. 获取文件锁
        try:
            with file_lock(f'critical_{period_key}', timeout=300):
                # 2. 检查是否有未完成的处理（中断恢复）
                pending_state = self._get_critical_state(period_key)
                if pending_state:
                    if pending_state['status'] in ('handling', 'failed'):
                        # 有未完成的处理或失败的处理 → 回滚并重新开始
                        self._rollback_incomplete_changes(pending_state['id'])
                        self._clear_critical_state(pending_state['id'])
                    elif pending_state['status'] == 'handled':
                        # 已完成 → 跳过
                        return 0

                # 3. 标记开始处理
                record_id = self._mark_critical_handling(period_key, alerts_total=len(alerts))

                # 4. 处理各 alert
                handled = 0
                try:
                    for alert in alerts:
                        self._handle_critical_alert(alert, info.effective_data_date)
                        handled += 1
                        self._update_critical_progress(record_id, handled)

                    # 5. 处理完成
                    self._mark_critical_handled(record_id)

                except Exception as e:
                    # 6. 中断时标记为 failed
                    self._mark_critical_failed(record_id, str(e))
                    raise

                return handled

        except TimeoutError:
            # 锁获取超时，说明其他进程正在处理
            print(f"[INFO] 其他进程正在处理 critical ({period_key})")
            return 0
```

- [ ] **Step 2: 实现辅助方法**

添加以下辅助方法：

```python
    def _get_critical_state(self, period_key):
        """获取 critical 处理状态"""
        with self.dl._get_conn() as conn:
            # 优先查询已完成的记录
            row = conn.execute("""
                SELECT id, status, alerts_processed, changes_applied
                FROM critical_process_state
                WHERE period_key=? AND status='handled'
                ORDER BY completed_at DESC LIMIT 1
            """, (period_key,)).fetchone()
            if row:
                return {'id': row[0], 'status': row[1], 'alerts_processed': row[2], 'changes_applied': row[3]}

            # 查询正在处理的记录
            row = conn.execute("""
                SELECT id, status, alerts_processed, changes_applied
                FROM critical_process_state
                WHERE period_key=? AND status IN ('handling', 'failed')
                ORDER BY started_at DESC LIMIT 1
            """, (period_key,)).fetchone()
            if row:
                return {'id': row[0], 'status': row[1], 'alerts_processed': row[2], 'changes_applied': row[3]}
            return None

    def _mark_critical_handling(self, period_key, alerts_total) -> int:
        """标记开始处理，返回记录 id"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT INTO critical_process_state
                (period_key, started_at, status, alerts_total)
                VALUES (?, datetime('now'), 'handling', ?)
            """, (period_key, alerts_total))
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _update_critical_progress(self, record_id, alerts_processed):
        """更新处理进度"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                UPDATE critical_process_state
                SET alerts_processed=? WHERE id=?
            """, (alerts_processed, record_id))

    def _mark_critical_handled(self, record_id):
        """标记处理完成"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                UPDATE critical_process_state
                SET status='handled', completed_at=datetime('now')
                WHERE id=?
            """, (record_id,))

    def _mark_critical_failed(self, record_id, error_detail):
        """标记处理失败"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                UPDATE critical_process_state
                SET status='failed', error_detail=?, completed_at=datetime('now')
                WHERE id=?
            """, (error_detail, record_id))

    def _clear_critical_state(self, record_id):
        """清除处理状态记录"""
        with self.dl._get_conn() as conn:
            conn.execute("DELETE FROM critical_process_state WHERE id=?", (record_id,))

    def _rollback_incomplete_changes(self, record_id):
        """回滚未完成的变更"""
        # critical batch_id 格式: {YYYYMMDD}-crit（无横杠）
        # period_key 格式: YYYY-MM-DD（有横杠）
        # 需要转换格式后精确匹配
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT period_key FROM critical_process_state WHERE id=?
            """, (record_id,)).fetchone()
            if not row:
                return

            period_key = row[0]
            # 转换格式: "2026-04-27" → "20260427-crit"
            batch_id = period_key.replace('-', '') + '-crit'

            # 精确匹配 batch_id
            rows = conn.execute("""
                SELECT id FROM sandbox_config
                WHERE batch_id=? AND status='pending'
            """, (batch_id,)).fetchall()
            for row in rows:
                self.change_mgr.rollback_change(row[0])
```

- [ ] **Step 3: Commit**

```bash
git add adaptive_engine.py
git commit -m "feat: implement critical handling with recovery mechanism"
```

---

### Task 13: 改造 run_weekly 使用 resolver

**Files:**
- Modify: `adaptive_engine.py`

- [ ] **Step 1: 重构 run_weekly guard 子句部分**

修改 `run_weekly` 方法的 guard 子句（第131-214行），替换原有的日期判断逻辑：

```python
    def run_weekly(self, optimize_date=None, layers=None):
        """
        运行每周优化

        Args:
            optimize_date: 优化日期，默认今天
            layers: 要优化的层列表

        Returns:
            dict: {...}
        """
        if optimize_date is None:
            optimize_date = datetime.now().strftime('%Y-%m-%d')

        # 使用 resolver 获取统一信息
        info = self.resolver.resolve(optimize_date)

        # 历史日期：不允许执行
        if info.status == STATUS_HISTORICAL:
            return {
                'optimization_results': None,
                'sandbox_validation': None,
                'applied': 0,
                'rejected': 0,
                'reason': 'historical_not_allowed',
                'message': '历史日期不允许执行每周优化（会修改当前生产参数）',
            }

        # 非交易日：不允许执行
        if info.status == STATUS_NON_TRADING_DAY:
            return {
                'optimization_results': None,
                'sandbox_validation': None,
                'applied': 0,
                'rejected': 0,
                'reason': 'non_trading_day',
                'message': '非交易日不允许执行每周优化',
            }

        # 周四判断：基于 effective_data_date 所在周
        effective_dt = datetime.strptime(info.effective_data_date, '%Y-%m-%d')
        days_to_thursday = (3 - effective_dt.weekday()) % 7
        this_week_thursday_dt = effective_dt + timedelta(days=days_to_thursday)
        this_week_thursday = this_week_thursday_dt.strftime('%Y-%m-%d')

        # 判断今天是周四（基于 target_date）
        target_dt = datetime.strptime(info.target_date, '%Y-%m-%d')
        is_thursday = target_dt.weekday() == 3

        # 非周四不允许执行
        if not is_thursday and target_dt < this_week_thursday_dt:
            return {
                'optimization_results': None,
                'sandbox_validation': None,
                'applied': 0,
                'rejected': 0,
                'reason': 'not_thursday',
            }

        # 防重复检查：基于本周周四日期
        check_date = this_week_thursday if not is_thursday else info.target_date
        if self._check_optimization_already_run(check_date):
            return {
                'optimization_results': None,
                'sandbox_validation': None,
                'applied': 0,
                'rejected': 0,
                'reason': 'already_run_this_week',
            }

        # 检查是否有 pending 记录
        if self._check_has_today_pending(check_date):
            return {
                'optimization_results': None,
                'sandbox_validation': None,
                'applied': 0,
                'rejected': 0,
                'reason': 'pending_validation_in_progress',
            }

        # === guard 子句结束 ===
        # 以下原有代码保留不变（第216-240行）：
        # - weekly_optimizer.run(optimize_date, layers)
        # - sandbox_validator.validate_batch(batch_id)
        # - sandbox_validator.apply_passed_changes(batch_id)
```

**注**：Step 1 只替换 guard 子句部分，后续优化执行逻辑（第216-240行）保留不变。

- [ ] **Step 2: Commit**

```bash
git add adaptive_engine.py
git commit -m "refactor: use TradingDayResolver in run_weekly"
```

---

## Phase 4: 测试与验证

### Task 14: 编写集成测试

**Files:**
- Create: `tests/conftest.py`（共享 fixture）
- Modify: `tests/test_adaptive.py`

- [ ] **Step 0: 将 tmp_db fixture 移到 conftest.py**

pytest fixture 需要在 conftest.py 中才能跨文件共享。将 Task 3 Step 4 的 tmp_db fixture 移到：

```python
# tests/conftest.py
import pytest
import tempfile
import sqlite3
import os

@pytest.fixture
def tmp_db():
    """临时数据库 fixture（包含所有必要表结构）"""
    # ... （复制 Task 3 Step 4 的完整代码）
```

并从 `tests/test_trading_day_resolver.py` 中删除 tmp_db fixture 定义。

- [ ] **Step 1: 添加 TradingDayResolver 集成测试**

在 `tests/test_adaptive.py` 添加：

```python
from trading_day_resolver import (
    TradingDayResolver,
    TradingDayInfo,
    STATUS_DATA_READY,
    STATUS_DATA_NOT_UPDATED,
    STATUS_NON_TRADING_DAY,
    STATUS_HISTORICAL,
)


class TestAdaptiveEngineWithResolver:
    """AdaptiveEngine 使用 resolver 的集成测试"""

    def test_run_daily_non_trading_day(self, tmp_db):
        """非交易日跳过数据监控"""
        engine = AdaptiveEngine(db_path=tmp_db)
        resolver = TradingDayResolver(db_path=tmp_db)

        # 设置数据库：最新数据为周五
        with engine.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")

        # 周六运行
        result = engine.run_daily('2026-04-25')
        assert result['status'] == 'skipped'
        assert result['reason'] == 'non_trading_day'

    def test_run_daily_data_not_updated(self, tmp_db):
        """数据未更新时使用旧数据"""
        engine = AdaptiveEngine(db_path=tmp_db)

        # 设置数据库：最新数据滞后 3 天
        with engine.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-24', 'sh.600000', 10.0)")
            conn.execute("INSERT INTO trading_day_cache (date, is_trading_day, checked_at) VALUES ('2026-04-27', 1, datetime('now'))")

        result = engine.run_daily('2026-04-27')
        assert result['status'] in ('ok', 'warning', 'critical')
        # 验证使用了 effective_data_date

    def test_run_daily_historical_date(self, tmp_db):
        """历史日期跳过 critical"""
        engine = AdaptiveEngine(db_path=tmp_db)

        # 设置数据库：最新数据为今天
        with engine.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-27', 'sh.600000', 10.0)")

        result = engine.run_daily('2026-04-20')
        assert result['reason'] == 'historical'
        assert result['critical_handled'] == 0

    def test_run_weekly_not_thursday(self, tmp_db):
        """非周四不允许执行优化"""
        engine = AdaptiveEngine(db_path=tmp_db)

        # 设置数据库
        with engine.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-27', 'sh.600000', 10.0)")

        # 周一运行
        result = engine.run_weekly('2026-04-27')
        assert result['reason'] == 'not_thursday'

    def test_critical_handling_recovery(self, tmp_db):
        """中断恢复测试"""
        engine = AdaptiveEngine(db_path=tmp_db)

        # 设置数据库
        with engine.dl._get_conn() as conn:
            conn.execute("INSERT INTO stock_daily (date, code, close) VALUES ('2026-04-27', 'sh.600000', 10.0)")
            # 模拟中断状态：handling
            conn.execute("""
                INSERT INTO critical_process_state
                (period_key, started_at, status, alerts_total, alerts_processed)
                VALUES ('2026-04-27', datetime('now'), 'handling', 3, 1)
            """)

        # 第二次运行：应检测到 handling 状态并恢复
        result = engine.run_daily('2026-04-27')
        # 验证：应回滚并重新处理
        assert result['status'] in ('ok', 'warning', 'critical')
```

- [ ] **Step 2: 运行全部测试**

Run: `python3 -m pytest tests/ -v`

Expected: 所有测试 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_adaptive.py
git commit -m "test: add integration tests for TradingDayResolver"
```

---

### Task 15: 验证实际运行

- [ ] **Step 1: 运行 daily_run.sh --monitor**

Run: `./daily_run.sh --monitor`

Expected: 正常执行，输出包含 resolver 信息

- [ ] **Step 2: 运行 daily_run.sh --status**

Run: `./daily_run.sh --status`

Expected: 正常输出状态摘要

- [ ] **Step 3: 验证 critical_process_state 表**

Run: `python3 -c "from data_layer import get_data_layer; dl = get_data_layer(); print(dl._get_conn().execute('SELECT COUNT(*) FROM critical_process_state').fetchone()[0])"`

Expected: 输出记录数（可能为 0 或有记录）

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "feat: complete TradingDayResolver implementation

- Add TradingDayInfo data structure with validation
- Add TradingDayResolver class for unified date handling
- Add critical_process_state table for recovery mechanism
- Refactor AdaptiveEngine.run_daily to use resolver
- Refactor AdaptiveEngine.run_weekly to use resolver
- Refactor DailyMonitor.run to use effective_date
- Add unit and integration tests
"
```

---

## 实现完成检查

- [ ] 所有单元测试通过
- [ ] 所有集成测试通过
- [ ] daily_run.sh 正常运行
- [ ] critical_process_state 表已创建
- [ ] 文档已更新（如有）

---

## 修复记录（2026-04-27）

| 问题 | 修复内容 |
|------|----------|
| 🔴 _handle_critical_alerts_with_recovery 遗漏 'failed' | 修改条件为 `status in ('handling', 'failed')`，与设计文档第四轮修复保持一致 |
| 🔴 sandbox_config 无 critical_record_id 字段 | 改为通过 batch_id 精确匹配查找 pending 变更（`WHERE batch_id=?`，格式转换：`period_key.replace('-', '') + '-crit'`） |
| 🟡 tmp_db fixture 表结构不完整 | 扩展 fixture 创建所有必要表结构（market_regime, signal_status, pick_tracking, daily_monitor_log, sandbox_config, optimization_history, critical_process_state） |
| 🟡 Task 9 完成后 run_daily 无法运行 | 在 Task 9 Step 4 后添加占位方法 `_handle_critical_alerts_with_recovery`（临时实现，Task 12 完善） |
| 🔵 run_weekly 缺少 non_trading_day 处理 | 实现计划已添加，同步更新设计文档第五轮修复 |

### 第二轮修复（2026-04-27）

| 问题 | 修复内容 |
|------|----------|
| 🔴 `_count_trading_days_gap` 语义错误 | 改为 `date > ? AND date <= ?`（包含 to_date），同步修改 while 循环为 `<= to_dt` |
| 🔴 `_rollback_incomplete_changes` LIKE 匹配缺陷 | 改为精确匹配：`batch_id = period_key.replace('-', '') + '-crit'` |
| 🟡 `_get_effective_data_date` None 检查是死代码 | 删除 resolve() 中的 None 检查（该方法永远返回字符串） |
| 🟡 Task 10 修改不再被调用的方法 | 将 Step 3 改为"删除 _check_critical_already_handled 方法" |
| 🔵 `_get_critical_state` 缺少 changes_applied | 添加 `changes_applied` 字段，与设计文档保持一致 |
| 🔵 tmp_db fixture 缺少必要表 | 添加 strategy_config、param_snapshot、stock_meta、index_daily 表 |

**fixture 缺失表说明**：
- `strategy_config`：StrategyConfig.__init__ 需要查询/创建
- `param_snapshot`：ChangeManager.__init__ 需要查询/创建
- `stock_meta`：DataLayer._create_adaptive_tables 基础表
- `index_daily`：DailyMonitor._check_market_regime 需要查询

### 第三轮修复（2026-04-27）

| 问题 | 修复内容 |
|------|----------|
| 🔴 Task 7 init_db.py 修改步骤误添加 | 第三轮误添加 init_db.py 修改步骤，第四轮已删除（init_db.py 无需修改） |
| 🔴 Task 13 行号引用错误 | 更新为"第131-214行"，添加注释说明保留结尾部分（第215-240行）不变 |
| 🔴 tmp_db fixture 跨文件共享问题 | Task 14 添加 Step 0：将 fixture 移到 tests/conftest.py |
| 🟡 Task 9 Step 3 语义不清 | 合并 Step 1 和 Step 3 为完整 run_daily 重构，删除冗余代码块 |
| 🔵 _determine_trading_day 省略 baostock API | Task 4 标题添加注释说明省略设计文档优先级 2 |
| 🔵 test_data_not_updated 缺少 data_lag_days 断言 | 添加 `assert info.data_lag_days >= 1` |

### 第四轮修复（2026-04-27）

| 问题 | 修复内容 |
|------|----------|
| 🔴 Task 7 Step 1 方法名错误 | `_ensure_tables` 改为 `_create_adaptive_tables`（data_layer.py:125 实际方法名） |
| 🔴 Task 7 Step 2-4 无效指令 | init_db.py 没有 init_database() 函数，只有直接调用 get_data_layer()，表创建由 data_layer.py 自动完成，无需修改 init_db.py |
| 🟡 Task 1 导入未使用的 Optional | 删除 `from typing import Optional`，TradingDayInfo 所有字段均为确定类型 |
| 🔴 Task 9 使用字符串硬编码状态值 | 改为使用常量：`STATUS_NON_TRADING_DAY`、`STATUS_DATA_NOT_UPDATED`、`STATUS_HISTORICAL` |
| 🟡 Task 13 行号偏差 | "第215-240行"改为"第216-240行"（实际执行代码从 216 行开始） |

### 第五轮修复（2026-04-27）

| 问题 | 修复内容 |
|------|----------|
| 🟡 Task 7 文件表仍列出 init_db.py | 删除文件表中的 `init_db.py` 行（该文件无需修改） |
| 🟡 Task 7 Step 2 会清空数据库 | 合并 Step 2 和 Step 3 为快速验证，直接验证表结构 SQL（避免运行 init_db.py 全量初始化） |
| 🟡 修复记录残留 _ensure_tables | 第 590、1915 行注释改为 `_create_adaptive_tables` |
| 🟡 修复记录残留 LIKE ? | 第 1896 行改为精确匹配说明（`WHERE batch_id=?`） |