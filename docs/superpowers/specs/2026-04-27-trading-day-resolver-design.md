# TradingDayResolver 设计文档

日期：2026-04-27
状态：**已实现**（18 commits pushed to main）

---

## 1. 问题背景

### 1.1 核心问题

当前自适应引擎中 `monitor_date`（监控日期）和 `latest_trade_date`（数据库最新日期）可能不一致，导致判断逻辑混乱。

### 1.2 问题场景

| 场景 | 描述 | 现有问题 |
|------|------|----------|
| 交易日数据未更新 | 今天是交易日，但数据库数据滞后 | critical 处理基于旧日期判断，无法感知最新市场变化 |
| 非交易日（周末） | 周末运行脚本 | 已正确处理，但逻辑分散 |
| 非交易日（节假日） | 节假日可能是工作日 | 简单周末判断可能误判 |
| 每日多次执行 | 同一天多次运行 | 防重复机制基于 latest_trade_date，数据滞后时可能失效 |
| 首次执行中断 | critical 处理过程中脚本中断 | 无恢复机制，可能重复处理或留残留状态 |
| 历史日期运行 | 指定 --date 运行历史日期 | 已正确跳过 critical，但逻辑分散 |

### 1.3 根本原因

- 日期判断逻辑分散在多个模块（adaptive_engine、daily_monitor、data_layer）
- `monitor_date` 和 `latest_trade_date` 概念混淆
- 防重复检查基准不一致
- 无中断恢复机制

---

## 2. 解决方案

### 2.1 核心思想

引入 `TradingDayResolver` 模块统一处理所有交易日相关判断，为各模块提供一致的日期基准。

### 2.2 核心概念

| 概念 | 定义 | 来源 |
|------|------|------|
| `target_date` | 用户指定或当前日期 | 用户输入或 `datetime.now()` |
| `effective_data_date` | 实际可用的数据日期 | 数据库 `MAX(date)` 或交易日计算 |
| `monitor_period_key` | 防重复处理的周期键 | 基于 `effective_data_date`（数据滞后时）或 `target_date`（数据已更新时） |

### 2.3 TradingDayInfo 数据结构

```python
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

---

## 3. TradingDayResolver 模块设计

### 3.1 职责

统一处理所有交易日相关判断，为各模块提供一致的日期基准。

### 3.2 接口设计

```python
class TradingDayResolver:
    """交易日统一解析器"""

    def __init__(self, db_path=None):
        self.dl = get_data_layer(db_path)

    def resolve(self, target_date=None) -> TradingDayInfo:
        """
        解析目标日期，返回统一的交易日信息

        Args:
            target_date: 目标日期，默认今天

        Returns:
            TradingDayInfo: 包含所有判断所需信息
        """

    def get_period_key(self, target_date) -> str:
        """
        获取监控周期键（用于防重复检查）

        规则：
        - 交易日数据已更新 → 返回 target_date
        - 交易日数据未更新 → 返回 effective_data_date
        - 非交易日 → 返回 effective_data_date（最近交易日）
        """

    def is_same_period(self, date1, date2) -> bool:
        """
        判断两个日期是否属于同一监控周期
        """

    def should_process_critical(self, info: TradingDayInfo) -> bool:
        """
        判断是否应该处理 critical 预警（语义判断）

        仅判断语义条件：
        - 历史日期 → False
        - 非交易日 → False

        注：同一周期是否已处理的防重复检查，在
        _handle_critical_alerts_with_recovery 中通过数据库查询实现。

        Returns:
            bool: True 表示语义上可以处理
        """

    def _determine_trading_day(self, date_str) -> Optional[bool]:
        """
        判断是否是交易日

        优先级（权威来源优先，推断性来源后置）：
        1. trading_day_cache 缓存（权威）
        2. baostock API 查询（权威，并回填 cache）
        3. 数据库是否有该日数据（推断性：有数据则大概率是交易日）
        4. 周末简单判断（兜底：周末必然非交易日）
        """

    def _count_trading_days_gap(self, from_date, to_date) -> int:
        """
        计算两个日期之间的交易日间隔天数

        规则：
        - 查询 from_date 之后（不含）到 to_date（含）的交易日
        - 即 date > from_date AND date <= to_date
        - 从 trading_day_cache 获取交易日列表进行计算

        Args:
            from_date: 起始日期（较早，不计入）
            to_date: 结束日期（较晚，计入）

        Returns:
            int: 交易日间隔天数（>= 0）

        示例：
            from='2026-04-24'(周五), to='2026-04-27'(周一)
            → 查询 > 24 AND <= 27 → 27(周一) = 1 天
        """

    def _get_effective_data_date(self, target_date) -> str:
        """
        获取有效数据日期

        规则：
        - 从 stock_daily 获取 MAX(date)
        - 如果空，从 trading_day_cache 获取最近交易日
        - 最后回退到今天
        """
```

### 3.3 resolve() 核心逻辑

```python
def resolve(self, target_date=None) -> TradingDayInfo:
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
    effective_data_date = self._get_effective_data_date(target_date)

    # 边界处理：空数据库情况
    if effective_data_date is None:
        # 数据库为空，使用 target_date 作为基准
        # 此时 target_date == effective_data_date → status = 'data_ready'
        # 这是首次运行场景：没有历史数据，视为"数据就绪"（无滞后）
        effective_data_date = target_date

    # 3. 判断是否是交易日
    is_trading_day = self._determine_trading_day(target_date)

    # 4. 判断是否是历史运行（使用 datetime 对象比较，避免字符串比较问题）
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    effective_dt = datetime.strptime(effective_data_date, '%Y-%m-%d')
    is_current_monitor = target_dt >= effective_dt

    # 5. 确定状态
    if not is_trading_day:
        status = 'non_trading_day'
        data_ready = True
        data_lag_days = 0
    elif not is_current_monitor:
        status = 'historical'
        data_ready = True
        data_lag_days = 0
    elif target_date == effective_data_date:
        status = 'data_ready'
        data_ready = True
        data_lag_days = 0
    else:
        status = 'data_not_updated'
        data_ready = False
        # 计算 lag 天数（仅交易日）
        data_lag_days = self._count_trading_days_gap(effective_data_date, target_date)

    # 6. 计算 monitor_period_key
    # 规则：非交易日和数据未更新时，使用 effective_data_date 作为防重复基准
    if status in ('non_trading_day', 'data_not_updated'):
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

---

## 4. 各模块改造

### 4.1 adaptive_engine.py 改造

```python
class AdaptiveEngine:
    def __init__(self, db_path=None):
        self.resolver = TradingDayResolver(db_path)  # 新增

    def run_daily(self, monitor_date=None):
        # 使用 resolver 获取统一信息
        info = self.resolver.resolve(monitor_date)

        # 非交易日：跳过数据监控
        if info.status == 'non_trading_day':
            rollback_result = self.change_mgr.monitor_and_rollback()
            return {
                'alerts': [],
                'critical_handled': 0,
                'status': 'skipped',
                'reason': 'non_trading_day',
                'message': f'{info.target_date} 是非交易日，跳过数据监控',
                'rollback_monitor': rollback_result,
            }

        # 数据滞后警告
        if info.status == 'data_not_updated' and info.data_lag_days > 0:
            self._log_warning(f"数据滞后 {info.data_lag_days} 天，市场环境判断基于 {info.effective_data_date}")

        # 历史日期：跳过 critical 处理
        if info.status == 'historical':
            # 历史运行使用 target_date 作为查询截止日期
            # 前置条件：历史数据必须存在（否则 resolve 会返回 data_not_updated）
            # 注意：历史运行仅用于数据回补场景，不触发参数变更
            alerts = self.monitor.run(info.target_date)
            return {
                'alerts': alerts,
                'critical_handled': 0,
                'status': 'ok',
                'reason': 'historical',
            }

        # 当前监控：使用 effective_data_date 进行数据查询
        # 设计决策：data_not_updated 状态也处理 critical（基于旧数据）
        # 原因：即使数据滞后，系统仍需保持运行并响应异常信号
        # monitor_period_key 用于防重复：data_not_updated 时使用 effective_data_date
        alerts = self.monitor.run(info.effective_data_date)
        critical_alerts = [a for a in alerts if a['severity'] == 'critical']

        critical_handled = 0
        if info.should_process_critical:
            # 传入 info 对象，_handle_critical_alerts_with_recovery 内部使用
            # info.effective_data_date 作为处理基准日期
            critical_handled = self._handle_critical_alerts_with_recovery(
                critical_alerts, info
            )

        # 主动回滚监控
        rollback_result = self.change_mgr.monitor_and_rollback()

        return {
            'alerts': alerts,
            'critical_handled': critical_handled,
            'status': 'ok' if not critical_alerts else 'critical',
            'rollback_monitor': rollback_result,
        }

    def run_weekly(self, optimize_date=None):
        info = self.resolver.resolve(optimize_date)

        # 历史日期：不允许执行
        if info.status == 'historical':
            return {
                'optimization_results': None,
                'applied': 0,
                'rejected': 0,
                'reason': 'historical_not_allowed',
            }

        # 非交易日：不允许执行（非交易日无新数据，优化意义不大）
        if info.status == 'non_trading_day':
            return {
                'optimization_results': None,
                'applied': 0,
                'rejected': 0,
                'reason': 'non_trading_day',
            }

        # 周四判断：基于 effective_data_date 所在周（保持现有行为）
        # 原因：数据未更新时，应判断上周是否已完成优化，而非本周
        effective_dt = datetime.strptime(info.effective_data_date, '%Y-%m-%d')
        days_to_thursday = (3 - effective_dt.weekday()) % 7
        this_week_thursday_dt = effective_dt + timedelta(days=days_to_thursday)
        this_week_thursday = this_week_thursday_dt.strftime('%Y-%m-%d')

        # 判断今天是周四（基于 target_date）
        target_dt = datetime.strptime(info.target_date, '%Y-%m-%d')
        is_thursday = target_dt.weekday() == 3

        # 非周四不允许执行（除非本周周四已过且未执行）
        # 使用 datetime 对象比较，避免字符串比较的语义歧义
        if not is_thursday and target_dt < this_week_thursday_dt:
            return {'reason': 'not_thursday'}

        # 防重复检查：基于本周周四日期
        check_date = this_week_thursday if not is_thursday else info.target_date
        if self._check_optimization_already_run(check_date):
            return {'reason': 'already_run_this_period'}

        # 正常执行优化流程...
```

### 4.2 daily_monitor.py 改造

```python
class DailyMonitor:
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

        # 2. 检查市场环境（使用 effective_date 作为 end_date）
        regime_alert = self._check_market_regime(effective_date)
        if regime_alert:
            alerts.append(regime_alert)

        # 3. 更新 signal_status 表
        self._update_signal_status()

        # 4. 写入监控日志（使用 effective_date）
        self._write_monitor_log(alerts, effective_date)

        return alerts

    def _check_market_regime(self, effective_date):
        """检查市场环境"""
        # 使用 effective_date 作为查询截止日期
        index_data = self.dl.get_index_kline('sh.000001',
            start_date=(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
            end_date=effective_date  # 改动：使用有效数据日期
        )
        # ...
```

### 4.3 改动要点总结

| 模块 | 改动点 | 原逻辑 | 新逻辑 |
|------|--------|--------|--------|
| `adaptive_engine` | 日期判断基准 | `monitor_date` vs `latest_trade_date` 分散判断 | 统一使用 `TradingDayInfo` |
| `adaptive_engine` | critical 防重复 | 基于 `latest_trade_date` | 基于 `monitor_period_key` |
| `adaptive_engine` | weekly 周四判断 | 基于 `latest_trade_date` 所在周 | 基于 `effective_data_date` 所在周（保持原有语义） |
| `adaptive_engine` | 中断恢复 | 无 | 两阶段状态标记 + 回滚机制 |
| `daily_monitor` | 数据查询 | `end_date=monitor_date` | `end_date=effective_data_date`，参数名改为 `effective_date` |

---

## 5. 中断恢复机制

### 5.1 状态流程图

```
开始处理 → [handling] → 处理各 alert → [handled] (完成)
                ↓
            中断/异常
                ↓
          标记 [failed]（记录错误信息）
                ↓
        下次运行检测到 handling 或 failed
                ↓
      回滚未完成变更 → 清除状态 → 重新处理
```

**状态说明**：
- `handling`: 正在处理中，可能已有部分变更
- `handled`: 处理完成，所有变更已应用
- `failed`: 处理失败，已记录错误信息，下次运行时会回滚并重新处理

### 5.2 新增数据表

```sql
CREATE TABLE IF NOT EXISTS critical_process_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_key TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'handling',  -- 'handling' | 'handled' | 'failed'
    alerts_total INTEGER DEFAULT 0,
    alerts_processed INTEGER DEFAULT 0,
    changes_applied INTEGER DEFAULT 0,
    error_detail TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 查询时按 period_key + status 组合过滤，允许历史回填场景
CREATE INDEX IF NOT EXISTS idx_critical_period_status
ON critical_process_state(period_key, status);
CREATE INDEX IF NOT EXISTS idx_critical_status ON critical_process_state(status);
```

**设计说明**：
- 使用 `id` 作为主键而非 `period_key`，允许同一 period 有多条记录（历史回填场景）
- 实际防重复逻辑通过查询 `period_key + status='handled'` 实现

### 5.3 处理逻辑

```python
from process_lock import file_lock

def _handle_critical_alerts_with_recovery(self, alerts, info):
    """处理 critical 预警（带中断恢复）"""

    period_key = info.monitor_period_key

    # 1. 获取文件锁（使用现有 process_lock.file_lock）
    # 注意：fcntl.flock 在进程崩溃时由 OS 自动释放，无需手动清理
    try:
        with file_lock(f'critical_{period_key}', timeout=300):
            # 2. 检查是否有未完成的处理（中断恢复）
            pending_state = self._get_critical_state(period_key)
            if pending_state:
                if pending_state['status'] in ('handling', 'failed'):
                    # 有未完成的处理或失败的处理 → 回滚并重新开始
                    # failed 状态可能已有部分变更，需要先回滚
                    self._rollback_incomplete_changes(pending_state['id'])
                    self._clear_critical_state(pending_state['id'])
                elif pending_state['status'] == 'handled':
                    # 已完成 → 跳过
                    return 0

            # 3. 标记开始处理，返回记录 id
            record_id = self._mark_critical_handling(period_key, alerts_total=len(alerts))

            # 4. 处理各 alert
            handled = 0
            try:
                for alert in alerts:
                    # 使用 effective_data_date 作为处理基准
                    self._handle_single_critical(alert, info.effective_data_date)
                    handled += 1
                    self._update_critical_progress(record_id, handled)

                # 5. 处理完成（使用 id 精确更新）
                self._mark_critical_handled(record_id)

            except Exception as e:
                # 6. 中断时标记为 failed（下次运行时检测并恢复）
                self._mark_critical_failed(record_id, str(e))
                raise

            return handled

    except TimeoutError:
        # 锁获取超时，说明其他进程正在处理
        return 0

def _get_critical_state(self, period_key):
    """获取 critical 处理状态（查询最新的 handled 或 handling）"""
    with self.dl._get_conn() as conn:
        # 优先查询已完成的记录
        row = conn.execute("""
            SELECT id, status, alerts_processed, changes_applied
            FROM critical_process_state
            WHERE period_key=? AND status='handled'
            ORDER BY completed_at DESC LIMIT 1
        """, (period_key,)).fetchone()
        if row:
            return {'id': row[0], 'status': row[1], 'alerts_processed': row[2]}

        # 查询正在处理的记录
        row = conn.execute("""
            SELECT id, status, alerts_processed, changes_applied
            FROM critical_process_state
            WHERE period_key=? AND status IN ('handling', 'failed')
            ORDER BY started_at DESC LIMIT 1
        """, (period_key,)).fetchone()
        return {'id': row[0], 'status': row[1], 'alerts_processed': row[2]} if row else None

def _mark_critical_handling(self, period_key, alerts_total) -> int:
    """标记开始处理，返回记录 id"""
    with self.dl._get_conn() as conn:
        conn.execute("""
            INSERT INTO critical_process_state
            (period_key, started_at, status, alerts_total)
            VALUES (?, datetime('now'), 'handling', ?)
        """, (period_key, alerts_total))
        # 获取刚插入的记录 id
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

def _update_critical_progress(self, record_id, alerts_processed):
    """更新处理进度"""
    with self.dl._get_conn() as conn:
        conn.execute("""
            UPDATE critical_process_state
            SET alerts_processed=?
            WHERE id=?
        """, (alerts_processed, record_id))

def _mark_critical_handled(self, record_id):
    """标记处理完成（使用 id 精确更新）"""
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
        conn.execute("""
            DELETE FROM critical_process_state WHERE id=?
        """, (record_id,))

def _rollback_incomplete_changes(self, record_id):
    """回滚未完成的变更"""
    # 查找该 record_id 对应的 sandbox_config 中 pending 状态的变更
    # 调用 change_manager.rollback_change
    pass  # 具体实现依赖 sandbox_config 表结构
```

---

## 6. 场景处理矩阵

| 场景 | resolver 返回 | 系统行为 |
|------|---------------|----------|
| 交易日数据已更新 | `data_ready`, lag=0 | 正常执行，critical 基于 `monitor_period_key`（此时等于 `target_date`）检查 |
| 交易日数据未更新 | `data_not_updated`, lag>0 | 执行监控但使用旧数据，critical 基于 `effective_data_date` 检查，发出警告 |
| 非交易日（周末） | `non_trading_day` | 跳过数据监控，只执行回滚监控 |
| 非交易日（节假日） | `non_trading_day` | 同周末处理 |
| 每日多次执行 | 同首次状态 | 使用 `monitor_period_key` 防重复 |
| 首次执行中断 | `handling` 状态保持 | 第二次运行检测 → 回滚 → 重新处理 |
| 历史日期运行 | `historical` | 执行数据监控，跳过 critical/weekly |
| 空数据库首次运行 | `data_ready`, effective_data_date=target_date | 正常执行，无滞后 |
| 未来日期运行 | 抛出 `ValueError` | 拒绝执行，返回错误信息 |
| 周六周日连续运行 | 周六/周日均返回 `non_trading_day` | 两天返回相同 `period_key`（上周五），防重复生效 |
| 锁持有期间进程崩溃 | 锁由 OS 自动释放（fcntl.flock） | 下次运行正常获取锁，检测 handling 状态并恢复 |

---

## 7. 测试方案

### 7.1 单元测试

| 测试场景 | 输入 | 预期输出 |
|----------|------|----------|
| 交易日数据已更新 | target=27, db=27 | status=data_ready, lag=0, period_key=27 |
| 交易日数据未更新 | target=27, db=24 | status=data_not_updated, lag=3, period_key=24 |
| 周末运行 | target=26(周六), db=24 | status=non_trading_day, period_key=24 |
| 节假日运行 | target=节假日周一, db=上周五 | status=non_trading_day |
| 历史日期 | target=20, db=27 | status=historical, is_current_monitor=False |
| 空数据库 | target=27, db=None | effective_data_date=27, status=data_ready, lag=0 |
| 未来日期 | target=28, today=27 | 抛出 ValueError |
| 日期格式错误 | target="2026-04-27-" | 抛出 ValueError |
| should_process_critical 验证 | non_trading_day + is_current_monitor=True | 返回 False |
| 周六周日 period_key 相同 | 周六运行，周日运行 | period_key 均为上周五（防重复生效） |

### 7.2 中断恢复测试

| 测试场景 | 操作 | 预期结果 |
|----------|------|----------|
| 正常完成 | handling → handled | 状态正确，变更完整 |
| 处理中异常 | handling → 异常 → failed | 状态标记 failed，记录错误信息 |
| 中断后恢复 | 检测 failed/handling → 回滚 → 重新处理 | 正确回滚，重新处理成功 |
| 部分变更完成 | 2/3 alert 处理完成 → 异常 | 状态记录 alerts_processed=2，下次继续 |
| 锁超时 | 其他进程持有锁 | 返回 0，不执行处理 |
| 进程崩溃后锁释放 | 模拟进程崩溃 | OS 自动释放 fcntl.flock，下次正常获取 |

### 7.3 集成测试流程

```
1. 正常流程测试
   - 模拟交易日收盘后运行 → 验证完整流程
   - 模拟空数据库首次运行 → 验证正常初始化

2. 边界流程测试
   - 模拟收盘前运行 → 验证数据滞后警告
   - 模拟周末运行 → 验证跳过数据监控
   - 模拟历史日期运行 → 验证跳过 critical
   - 模拟未来日期运行 → 验证错误拒绝
   - 模拟周六周日连续运行 → 验证防重复

3. 中断恢复测试
   - 模拟 processing 异常 → 验证 failed 状态
   - 模拟第二次运行 → 验证回滚 + 重新处理
   - 模拟进程崩溃 → 验证锁自动释放 + 恢复

4. 并发安全测试
   - 多进程同时运行 → 验证 file_lock 机制
   - 多进程处理同一 period → 验证只有一个成功
```

---

## 8. 实现优先级

### Phase 1: 核心模块
1. 创建 `TradingDayResolver` 类和 `TradingDayInfo` 数据结构
2. 创建 `critical_process_state` 表
3. 改造 `adaptive_engine.py` 使用 resolver

### Phase 2: 中断恢复
4. 实现两阶段状态标记机制
5. 实现回滚逻辑

### Phase 3: 测试与验证
6. 编写单元测试
7. 编写集成测试
8. 验证各场景处理正确性

---

## 9. 待审核项

- [x] TradingDayInfo 数据结构是否完整 → 已添加 `__post_init__` 验证
- [x] TradingDayResolver 接口是否合理 → 已添加 `_count_trading_days_gap` 方法，修正交易日判断优先级
- [x] 各模块改动是否清晰 → 已明确 historical 场景监控日期选择逻辑
- [x] 中断恢复机制是否健壮 → 已添加锁机制和事务边界说明
- [x] 测试方案是否覆盖所有场景 → 已添加边界情况测试
- [x] 实现优先级是否合理 → 无变化

## 10. 修复记录

### 第一轮修复（2026-04-27）

| 问题 | 修复内容 |
|------|----------|
| `should_process_critical` Bug | 修改为 `status in (data_ready, data_not_updated)` 条件 |
| 交易日判断优先级 | 调整为权威来源优先，推断性来源后置 |
| `_count_trading_days_gap` 缺失 | 补充方法定义 |
| 日期字符串比较 | 改用 datetime 对象比较，添加格式验证 |
| Weekly 周四判断 | 保持基于 `effective_data_date` 所在周的语义 |
| 边界情况处理 | 添加空数据库、未来日期、周六周日连续运行场景 |
| critical_process_state 表约束 | 改用自增主键，允许多 period_key 并发 |
| 中断恢复事务边界 | 添加锁机制和 finally 释放 |
| 数据结构验证 | 添加 `__post_init__` 方法验证日期格式、状态枚举、数值范围 |

### 第二轮修复（2026-04-27）

| 问题 | 修复内容 |
|------|----------|
| 🔴 acquire_lock/release_lock 不存在 | 改用 `process_lock.file_lock` 上下文管理器，进程崩溃时 OS 自动释放 |
| 🔴 _mark_critical_handled UPDATE 缺少主键 | 改用 `record_id` 精确更新，新增 `_mark_critical_handling` 返回 id |
| 🔴 空数据库状态矛盾 | 明确逻辑：空库时 `effective_data_date=target_date` → `status=data_ready` |
| 🟡 data_not_updated 处理 critical | 明确设计决策：数据滞后时仍处理，基于 `effective_data_date` |
| 🟡 should_skip_data_check 命名不准确 | 改名为 `is_non_trading_day` |
| 🟡 类常量放 dataclass 内部 | 移至模块级别：`STATUS_*` 和 `VALID_STATUSES` |
| 🟡 _mark_critical_failed 未定义 | 补充 `_mark_critical_failed`、`_update_critical_progress` 方法定义 |
| 🟡 monitor_period_key 验证冗余 | 移除 `__post_init__` 中对 `monitor_period_key` 的验证 |
| 🔵 daily_monitor 参数名差异 | 改动要点表格明确：参数名改为 `effective_date` |
| 🔵 缺少锁崩溃场景 | 场景矩阵添加：fcntl.flock 进程崩溃时 OS 自动释放 |
| 🔵 run_weekly 字符串比较 | 改用 `datetime` 对象比较，避免语义歧义 |

### 第三轮修复（2026-04-27）

| 问题 | 修复内容 |
|------|----------|
| 🔵 should_process_critical 职责边界不清 | docstring 明确：此方法仅做语义判断，防重复检查在 `_handle_critical_alerts_with_recovery` 中实现，两者职责分离 |

### 第四轮修复（2026-04-27）

| 问题 | 修复内容 |
|------|----------|
| 🟡 'failed' 状态未被恢复处理 | 修改条件为 `status in ('handling', 'failed')`，failed 状态也会被回滚并重新处理 |
| 🔵 状态流程图缺少 failed | 补充 failed 状态流程，添加状态说明表格 |

### 第五轮修复（2026-04-27）

| 问题 | 修复内容 |
|------|----------|
| 🔵 run_weekly 缺少 non_trading_day 处理 | 添加非交易日跳过优化逻辑，与实现计划保持一致 |

### 第六轮修复（2026-04-27）

| 问题 | 修复内容 |
|------|----------|
| 🔴 `_count_trading_days_gap` 语义错误 | 实现计划已改为 `date > ? AND date <= ?`（包含 to_date），设计文档接口说明需同步更新 |
| 🔴 `_rollback_incomplete_changes` LIKE 匹配缺陷 | 实现计划已改为精确匹配：`batch_id = period_key.replace('-', '') + '-crit'` |