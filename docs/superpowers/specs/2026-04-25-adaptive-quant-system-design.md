# 自适应量化系统设计（修订版）

日期: 2026-04-25
修订原因: 解决原设计中与现有系统冲突、样本阈值不合理、风险指标单一等问题

## 概述

为 shikong_fufei 量化系统添加三层自适应优化能力：
- **参数层**：自动优化入场/出场参数
- **评分层**：动态调整评分权重（基于期望值）
- **信号层**：自动启用/禁用信号类型（基于期望值+Wilson置信界）
- **环境层**：根据市场环境动态调整策略活跃度

采用混合验证机制：历史回测(OOS验证) → 实盘沙盒验证 → 表现好才正式启用

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                      自适应引擎 (adaptive_engine.py)                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │ daily_monitor.py│  │weekly_optimizer │  │sandbox_validator.py │  │
│  │ 每日监控        │  │每周四层优化      │  │沙盒验证            │  │
│  │ 异常预警        │  │参数/评分/信号    │  │实盘测试            │  │
│  │ 环境感知        │  │环境感知调整      │  │                    │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────────┘  │
│           │                    │                    │               │
│           └────────────────────┼────────────────────┘               │
│                                ▼                                    │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ optimization_history.py                                        │ │
│  │ 变更记录 + 追溯分析 + 回滚机制                                  │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         SQLite 数据库 (stock_data.db)               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │strategy_config  │  │ signal_status   │  │optimization_history │  │
│  │ 统一参数管理    │  │ 信号状态管理    │  │ 参数变更历史        │  │
│  │ (含评分权重)    │  │ (期望值+置信度) │  │                    │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────┘  │
│  ┌─────────────────┐  ┌─────────────────┐                           │
│  │daily_monitor_log│  │ market_regime   │                           │
│  │ 每日监控日志    │  │ 环境状态记录    │                           │
│  └─────────────────┘  └─────────────────┘                           │
└─────────────────────────────────────────────────────────────────────┘
```

**设计变更**：不再新建 `score_weights` 表，统一使用现有的 `strategy_config` 表管理所有参数（含评分权重），通过 `category` 字段区分。

---

## 信号类型定义

系统支持的信号类型（按优先级排序）：

| 信号类型 | 描述 | 检测条件 |
|----------|------|----------|
| `异动不跌` | 异动后次日不跌 | 前日振幅>6%，当日涨>1% |
| `阳包阴` | 阳线吞没阴线 | 当日涨>2%，阳包阴形态 |
| `大阳反转` | 大阳线反转 | 当日涨>3%，前日为阴线 |
| `烂板次日` | 涨停板次日表现 | 前日涨停未封死，当日涨>2% |

---

## 双周期机制

### 每日监控 (daily_monitor.py)

**职责：** 检测异常，预警通知，环境感知，不做主动调整

**监控指标：**

| 指标 | 触发条件 | 严重程度 |
|------|----------|----------|
| 信号期望值下降 | Wilson下界期望值<0持续3天 | warning |
| 市场环境退潮 | 退潮期持续>5天 | info |
| 评分预测力下降 | 评分与期望值相关系数<0.1（原>0.3） | warning |
| 新信号样本不足 | 某信号实盘退出样本<10笔 | info |
| 沙盒验证失败 | 新参数沙盒期望值低于基准 | critical |

**环境感知逻辑：**

```python
def get_market_regime(index_data, lookback=20):
    """判断市场环境：上升期/震荡期/退潮期"""
    # 基于 sh.000001 指数
    ma5 = index_data['close'].rolling(5).mean().iloc[-1]
    ma20 = index_data['close'].rolling(20).mean().iloc[-1]
    current = index_data['close'].iloc[-1]

    if current > ma5 > ma20:
        return 'bull', 1.0  # 上升期，正常活跃
    elif current > ma20 * 0.95:
        return 'range', 0.7  # 震荡期，降低活跃度
    else:
        return 'bear', 0.3  # 退潮期，大幅降低活跃度
```

**环境响应机制：**

| 环境 | 活跃度系数 | 影响 |
|------|-----------|------|
| 上升期 | 1.0 | 正常选股，正常评分阈值 |
| 震荡期 | 0.7 | 评分阈值提高15%，减少低分选股 |
| 退潮期 | 0.3 | 评分阈值提高30%，仅保留高置信信号 |

**运行时机：** `daily_run.sh --track` 后自动调用

### 每周优化 (weekly_optimizer.py)

**职责：** 周末运行四层优化，结果先入沙盒验证

**优化顺序：**

```
Step 0: 环境状态更新
  ├── 更新 market_regime 表当前状态
  └── 调整全局活跃度系数

Step 1: 参数层优化 (OOS验证)
  ├── 训练集：过去120天数据
  ├── 验证集：最近30天数据 (Out-of-Sample)
  ├── 运行贝叶斯优化搜索参数
  ├── OOS验证通过则写入sandbox待验证
  └── OOS验证失败则跳过本轮

Step 2: 评分层调整 (期望值驱动)
  ├── 计算各评分维度与最终期望值的Spearman相关性
  ├── 相关性>0.3 → 权重↑10%（上限+50%）
  ├── 相关性<0.1 → 权重↓10%（下限-30%）
  ├── 归一化约束：调整后总分均值≈基准值
  └── 写入sandbox待验证

Step 3: 信号层状态检查 (期望值+Wilson置信界)
  ├── 计算各信号Wilson置信下界期望值
  ├── 应用渐进降级机制
  └── 写入sandbox待验证

Step 4: 冷启动检查
  ├── 若某信号退出样本<5笔 → 保持active状态
  └── 记录样本不足状态，不做降级判断
```

---

## 期望值计算与Wilson置信界

**期望值公式：**

```python
def calculate_expectancy(win_rate, avg_win, avg_loss):
    """计算期望值 = 平均盈利×胜率 - 平均亏损×败率"""
    expectancy = avg_win * win_rate - avg_loss * (1 - win_rate)
    return expectancy

def wilson_expectancy_lower_bound(win_rate, avg_win, avg_loss, n, z=1.96):
    """Wilson置信区间下界期望值（保守估计）"""
    if n == 0:
        return 0.0

    p = win_rate
    # Wilson下界胜率
    p_lower = (p + z**2/(2*n) - z*sqrt(p*(1-p)/n + z**2/(4*n**2))) / (1 + z**2/n)

    # 使用下界胜率计算期望值
    expectancy_lower = avg_win * p_lower - avg_loss * (1 - p_lower)
    return expectancy_lower
```

**为什么使用期望值而非纯胜率：**

| 胜率 | 平均盈利 | 平均亏损 | 期望值 | 结论 |
|------|----------|----------|--------|------|
| 40% | +8% | -4% | +0.8% | 有效，不应禁用 |
| 40% | +3% | -5% | -0.2% | 无效，应降级 |
| 55% | +2% | -4% | -0.7% | 高胜率但负期望，应禁用 |

---

## 信号禁用安全机制（修订版）

**基于Wilson置信下界期望值的渐进降级：**

```
状态层级：
  active(权重1.0) → watching(权重0.5) → warning(权重0.2) → disabled(权重0)

判断标准（统一使用Wilson置信下界期望值）：

样本阈值统一：
  样本<10笔 → 保持active（不做判断，冷启动保护）
  样本10-30笔 → 可降级至watching
  样本>=30笔 → 可降级至warning/disabled

降级条件：
  Wilson下界期望值<0 且 样本>=10笔 → watching
  Wilson下界期望值<0 持续2周 且 样本>=30笔 → warning
  Wilson下界期望值<0 持续3周 且 样本>=30笔 → disabled（需人工确认）

升级条件（表现好转）：
  Wilson下界期望值>0 持续1周 → 向上一级恢复
  Wilson下界期望值>0.02 持续2周 → 直接恢复至active
```

**置信度计算（修订版）：**

```python
def get_signal_confidence(signal_stats):
    """综合置信度评分"""
    n = signal_stats['exit_count']
    win_rate = signal_stats['win_rate']
    avg_win = signal_stats['avg_win_pct']
    avg_loss = signal_stats['avg_loss_pct']

    if n < 10:
        return {'confidence': 'unknown', 'action': 'cold_start', 'reason': '样本不足'}

    expectancy_lb = wilson_expectancy_lower_bound(win_rate, avg_win, avg_loss, n)

    if expectancy_lb > 0.01:
        return {'confidence': 'high', 'action': 'active', 'reason': f'期望值下界{expectancy_lb:.2%}>0'}
    elif expectancy_lb > 0:
        return {'confidence': 'medium', 'action': 'active', 'reason': f'期望值下界{expectancy_lb:.2%}≈0'}
    elif expectancy_lb > -0.01:
        return {'confidence': 'low', 'action': 'watching', 'reason': f'期望值下界{expectancy_lb:.2%}<0'}
    else:
        return {'confidence': 'very_low', 'action': 'warning', 'reason': f'期望值下界{expectancy_lb:.2%}显著负值'}
```

---

## 沙盒验证机制（修订版）

**仅用实盘退出数据验证（不使用回测补充）**

**样本约束下的验证周期：**

```
实盘样本积累（修订估算）：
  - 当前实际数据：4天249笔选股，退出仅6笔
  - 持仓平均2-3天 → 约3-5天后有退出记录
  - 验证周期需更长以积累足够样本

验证周期：4-8周（非固定）
  - 样本>=10笔退出 → 开始评估（分级阈值）
  - 样本>=30笔退出 → 正式决策
  - 样本>=50笔退出 → 应用变更（高置信）

分级验证阈值（样本少时更保守）：

| 退出样本量 | sandbox期望值要求 | 说明 |
|------------|-------------------|------|
| 10笔 | >= 正式期望值×1.15 | 样本少，要求更高 |
| 20笔 | >= 正式期望值×1.08 | 中等保守 |
| 30笔+ | >= 正式期望值×1.02 | 允许小幅下降 |
| 50笔+ | >= 正式期望值×0.98 | 接近等效即可 |

核心逻辑：样本少 → 验证更严格 → 不轻易变更
```

---

## 数据表设计（修订版）

### strategy_config（现有表扩展）

不新建 `score_weights` 表，扩展现有 `strategy_config` 表的 `category` 字段：

```sql
-- 现有表结构已支持，仅需新增category值：
-- category 可选值：'entry' | 'exit' | 'scoring' | 'score_weight' | 'environment'

-- 评分权重参数示例（新增category='score_weight'）：
INSERT INTO strategy_config (param_key, param_value, description, category, updated_at)
VALUES
('weight_wave_gain', 1.0, '波段涨幅评分权重系数', 'score_weight', datetime('now')),
('weight_shallow_dd', 1.0, '浅回调评分权重系数', 'score_weight', datetime('now')),
('weight_strong_gain', 1.0, '强势涨幅评分权重系数', 'score_weight', datetime('now')),
('weight_volume', 1.0, '放量评分权重系数', 'score_weight', datetime('now')),
('weight_ma_bull', 1.0, '多头排列评分权重系数', 'score_weight', datetime('now')),
('weight_anomaly', 1.0, '异动信号额外权重', 'score_weight', datetime('now'));
```

### signal_status（修订版）

```sql
CREATE TABLE IF NOT EXISTS signal_status (
    signal_type TEXT PRIMARY KEY,           -- 英文主键：'anomaly_no_decline' | 'bullish_engulfing' | 'big_bullish_reversal' | 'limit_up_open_next_strong'
    display_name TEXT,                      -- 中文显示：'异动不跌' | '阳包阴' | '大阳反转' | '烂板次日'
    status_level TEXT DEFAULT 'active',     -- 'active' | 'watching' | 'warning' | 'disabled'
    weight_multiplier REAL DEFAULT 1.0,     -- 1.0 / 0.5 / 0.2 / 0

    -- 实盘统计（修订：新增期望值相关字段）
    live_win_rate REAL,
    live_avg_win_pct REAL,                  -- 平均盈利百分比
    live_avg_loss_pct REAL,                 -- 平均亏损百分比
    live_expectancy REAL,                   -- 期望值 = avg_win × win_rate - avg_loss × (1-win_rate)
    live_expectancy_lb REAL,                -- Wilson置信下界期望值
    live_sample_count INTEGER DEFAULT 0,    -- 退出样本数

    -- 状态跟踪
    live_observation_weeks INTEGER DEFAULT 0,
    confidence_level TEXT DEFAULT 'unknown', -- 'high' | 'medium' | 'low' | 'very_low' | 'unknown'
    min_sample_threshold INTEGER DEFAULT 10, -- 修订：统一为10笔
    last_check_date TEXT,
    disable_reason TEXT,
    can_auto_disable INTEGER DEFAULT 0      -- 需人工确认才能禁用
);
```

### optimization_history（保持不变）

```sql
CREATE TABLE IF NOT EXISTS optimization_history (
    id INTEGER PRIMARY KEY,
    optimize_date TEXT,
    optimize_type TEXT,          -- 'parameter' | 'score_weight' | 'signal_status' | 'environment'
    param_key TEXT,
    old_value REAL,
    new_value REAL,
    sandbox_test_result TEXT,    -- 'passed' | 'failed' | 'pending'
    apply_date TEXT,
    -- 回测指标（修订：新增OOS验证指标）
    backtest_train_sharpe REAL,
    backtest_oos_sharpe REAL,    -- Out-of-Sample验证集Sharpe
    backtest_win_rate REAL,
    backtest_expectancy REAL,
    -- 实盘指标
    live_win_rate REAL,
    live_expectancy REAL,
    rollback_needed INTEGER DEFAULT 0,
    rollback_date TEXT,
    created_at TEXT
);
```

### market_regime（新增）

```sql
CREATE TABLE IF NOT EXISTS market_regime (
    id INTEGER PRIMARY KEY,
    regime_date TEXT NOT NULL,
    regime_type TEXT,            -- 'bull' | 'range' | 'bear'
    activity_coefficient REAL,   -- 1.0 / 0.7 / 0.3
    index_close REAL,
    index_ma5 REAL,
    index_ma20 REAL,
    consecutive_days INTEGER,    -- 连续处于该环境的天数
    created_at TEXT DEFAULT datetime('now'),
    UNIQUE(regime_date)
);
```

### daily_monitor_log（保持不变）

```sql
CREATE TABLE IF NOT EXISTS daily_monitor_log (
    id INTEGER PRIMARY KEY,
    monitor_date TEXT,
    alert_type TEXT,
    alert_detail TEXT,
    severity TEXT,               -- 'info' | 'warning' | 'critical'
    action_taken TEXT,
    created_at TEXT
);
```

---

## 评分权重调整公式（明确版）

**调整公式：**

```python
def adjust_score_weight(current_weight, correlation, base_weight):
    """
    根据相关性调整评分权重

    Args:
        current_weight: 当前权重系数（相对于基准）
        correlation: Spearman相关性（评分维度与期望值）
        base_weight: 原始基准权重值

    Returns:
        new_weight: 新权重系数
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

def normalize_scores(scores_dict, weights_dict, base_total=50):
    """
    归一化评分，使总分均值≈基准值

    Args:
        scores_dict: 各维度原始评分 {'wave_gain': 10, 'shallow_dd': 15, ...}
        weights_dict: 各维度权重系数 {'wave_gain': 1.2, 'shallow_dd': 0.9, ...}
        base_total: 目标总分均值

    Returns:
        normalized_total: 归一化后的总分
    """
    raw_total = sum(scores_dict[k] * weights_dict[k] for k in scores_dict)
    # 归一化系数 = base_total / 当前基准总分
    # 基准总分 = 各维度基准分 × 权重1.0
    base_raw = sum(scores_dict.values())  # 权重均为1.0时的总分
    normalization_factor = base_total / base_raw if base_raw > 0 else 1.0

    return raw_total * normalization_factor
```

---

## 回滚机制（修订版）

**触发条件：**

```
- 变更生效后30天Wilson下界期望值 < sandbox预估期望值的80%
- 变更生效后30天实盘期望值 < sandbox预估期望值的70%
- 持续2周期望值低于阈值
```

**回滚流程：**

```
1. 标记 optimization_history 记录为 rollback_needed=1
2. 恢复 old_value 到正式表
3. 记录 rollback_date 和原因
4. 写入 daily_monitor_log 通知用户
5. 如为信号禁用回滚 → 恢复至warning状态（不直接active）
```

---

## 冷启动方案

**新信号/新参数部署时的处理：**

```
场景1：新信号类型首次启用
  → status_level='active', confidence='unknown'
  → min_sample_threshold=10（前10笔不做判断）
  → weight_multiplier=1.0（正常权重）
  → 监控日志标记 'cold_start: 新信号待验证'

场景2：系统首次部署（无历史数据）
  → 所有信号默认active，confidence='unknown'
  → 使用回测数据初始化 backtest_* 字段
  → live_* 字段全部为NULL
  → 等待实盘样本积累后再做优化

场景3：参数优化OOS验证失败
  → 保持当前参数不变
  → 记录优化失败原因
  → 不进入sandbox验证
```

---

## 与现有系统集成（修订版）

| 集成点 | 修改内容 |
|--------|----------|
| `strategy_config.py` | 新增 `category='score_weight'` 和 `category='environment'` 参数 |
| `daily_scanner.py` | 读取环境活跃度系数调整评分阈值；读取信号权重乘数调整各信号权重 |
| `pick_tracker.py` | 新增填充 `live_avg_win_pct`, `live_avg_loss_pct`, `live_expectancy` 到 `signal_status` |
| `daily_run.sh` | 新增 `--monitor` 和 `--weekly-optimize` 选项 |

**daily_scanner.py 修改示例：**

```python
def apply_environment_adjustment(base_threshold, activity_coefficient):
    """根据环境活跃度调整评分阈值"""
    # 退潮期提高阈值，减少选股
    adjusted = base_threshold / activity_coefficient
    return adjusted

def apply_signal_weight_multiplier(signal_type, base_score, signal_status):
    """根据信号状态调整评分"""
    multiplier = signal_status.get('weight_multiplier', 1.0)
    return base_score * multiplier
```

---

## 模块文件清单

```
adaptive_engine.py      # 核心控制器（调度每日监控+每周优化）
daily_monitor.py        # 每日监控模块 + 环境感知
weekly_optimizer.py     # 每周四层优化模块（参数OOS验证+期望值驱动）
sandbox_validator.py    # 沙盒验证模块
optimization_history.py # 变更历史+回滚模块
```

---

## 风险与缓解（修订版）

| 风险 | 缓解措施 |
|------|----------|
| 样本少导致误判 | Wilson置信下界期望值，样本<10笔不做判断 |
| 过度调整 | 单次调整幅度≤10%，回滚机制 |
| 市场环境突变导致策略失效 | 环境感知层自动降活跃度 |
| 回测过拟合 | OOS验证（120天训练+30天验证），验证失败不启用 |
| 评分权重归一化失效 | 归一化约束确保总分均值稳定 |
| 新信号冷启动风险 | 前10笔不做判断，保持active观察 |
| 沙盒验证周期过长 | 10笔开始评估，分级放宽阈值 |

---

## 后续扩展方向

- 策略发现层：自动探索新形态
- 多策略并行：不同市场环境启用不同策略组合
- 仓位管理自适应：根据期望值动态调整建议仓位

---

## 附录：修订对照表

| 原设计问题 | 修订方案 |
|------------|----------|
| 新建score_weights表与现有系统冲突 | 合并到strategy_config，用category区分 |
| 样本阈值过于乐观(20笔应用变更) | 改为10笔开始评估，30笔决策，50笔高置信 |
| 信号禁用只看胜率不看期望值 | 改用Wilson置信下界期望值作为判断标准 |
| 样本阈值不一致(sandbox vs 信号) | 统一为10/30/50三级阈值体系 |
| 未处理市场环境差异 | 新增环境感知层，退潮期降活跃度 |
| 信号类型未明确 | 定义4种信号类型及其检测条件 |
| 回测过拟合风险 | 新增OOS验证，120天训练+30天验证 |
| 评分权重调整公式不明 | 明确公式+归一化约束 |
| 无冷启动方案 | 新增冷启动处理逻辑 |

---

## 附录B：评审意见修正（2026-04-25补充）

### B.1 与现有 strategy_optimizer.py 的关系

**问题：** 两个优化器共存时会互相改写 strategy_config 参数，产生冲突。

**解决方案：** weekly_optimizer 复用现有 StrategyOptimizer，只做调度层：

```python
# weekly_optimizer.py
from strategy_optimizer import StrategyOptimizer

class WeeklyOptimizer:
    """四层优化调度器，复用现有优化能力"""
    
    def __init__(self):
        self.optimizer = StrategyOptimizer()  # 复用现有类
    
    def run_weekly_optimization(self):
        """每周优化入口"""
        # Step 0: 环境更新（新增）
        self._update_market_regime()
        
        # Step 1: 参数层 - 调用现有优化器的evaluate_params
        params = self.optimizer.coordinate_descent(...)
        if self._oos_validate(params):  # OOS验证（新增）
            self._save_to_sandbox('parameter', params)
        
        # Step 2-4: 评分/信号/冷启动（新增逻辑）
        self._adjust_score_weights()
        self._check_signal_status()
        self._cold_start_check()
```

**合并后的命令入口：**

```
daily_run.sh:
  --optimize        → 全量优化（现有：180天训练+60天测试）
  --weekly-optimize → 轻量自适应（新增：120天训练+30天OOS验证）
  
两者不冲突：
  --optimize 手动运行，结果直接写入strategy_config
  --weekly-optimize 自动运行，结果先入sandbox验证
```

---

### B.2 信号类型命名统一

**问题：** 中文命名与现有代码英文命名不一致。

**解决方案：** 使用英文标识作为主键，映射表：

```python
# signal_constants.py
SIGNAL_TYPE_MAPPING = {
    # 英文主键（数据库存储） → 中文显示（终端输出）
    'anomaly_no_decline': '异动不跌',
    'bullish_engulfing': '阳包阴',
    'big_bullish_reversal': '大阳反转',
    'limit_up_open_next_strong': '烂板次日',  # 修正：与strategy_optimizer.py:127一致
}

# 与 pick_tracking 表的 signal 字段映射
# pick_tracking.signal 存储中文 → 转换为英文主键
def normalize_signal_type(signal_str):
    """统一信号类型标识"""
    # 反向映射
    reverse = {v: k for k, v in SIGNAL_TYPE_MAPPING.items()}
    return reverse.get(signal_str, signal_str)
```

**signal_status 表主键改为英文：**

```sql
signal_status (
    signal_type TEXT PRIMARY KEY,  -- 'anomaly_no_decline' | 'bullish_engulfing' | ...
    display_name TEXT,             -- '异动不跌' | '阳包阴' | ...（显示用）
    ...
)
```

---

### B.3 daily_run.sh 执行流程

**问题：** 执行顺序和依赖关系不明确。

**解决方案：** 流程图：

```
daily_run.sh --track 执行流程：

┌─────────────────────────────────────────────────────────────────┐
│  Step 1: pick_tracker.py --action update                        │
│  ├── 查询 pick_tracking 表中所有 active 状态的选股              │
│  ├── 获取每只股票的最新K线数据                                  │
│  ├── 应用退出规则更新状态（stop_loss/trailing_stop/time_exit） │
│  └── 计算并更新 final_pnl_pct, hold_days 等字段                 │
│  └────────────────────────┬────────────────────────────────────┘
│                           │ 依赖：退出数据已更新                 │
│                           ▼                                     │
│  Step 2: daily_monitor.py（新增，自动调用）                     │
│  ├── 读取最新退出数据，计算各信号期望值                         │
│  ├── 检查异常指标（期望值下降、评分预测力下降）                 │
│  ├── 更新 signal_status 表的 live_* 字段                        │
│  └── 写入 daily_monitor_log 预警记录                            │
│  └────────────────────────┬────────────────────────────────────┘
│                           │                                     │
│                           ▼                                     │
│  Step 3: generate_scorecard_report.py（现有）                   │
│  └── 生成 tracking_report.md                                    │
└─────────────────────────────────────────────────────────────────┘

依赖关系：
  daily_monitor.py 依赖 pick_tracker.py 的退出数据
  若 pick_tracker 无新退出 → daily_monitor 使用上次数据（不变）
```

---

### B.4 评分权重归一化修正

**问题：** 用单样本的 base_raw 做归一化因子会导致每只股票的归一化系数不同。

**解决方案：** 基于全局统计均值计算：

```python
def normalize_scores_global(scores_dict, weights_dict, history_samples):
    """
    基于历史样本全局统计的归一化
    
    Args:
        scores_dict: 当前股票各维度原始评分
        weights_dict: 各维度权重系数
        history_samples: 历史样本的评分统计 {'avg_wave_gain': 12.3, 'avg_shallow_dd': 8.7, ...}
    
    Returns:
        normalized_total: 归一化后的总分
    """
    # 当前股票加权总分
    weighted_total = sum(scores_dict[k] * weights_dict.get(k, 1.0) for k in scores_dict)
    
    # 全局基准总分 = 历史各维度均值 × 权重1.0
    global_base_total = sum(history_samples.get(f'avg_{k}', 10) for k in scores_dict)
    
    # 全局加权总分 = 历史各维度均值 × 当前权重
    global_weighted_total = sum(
        history_samples.get(f'avg_{k}', 10) * weights_dict.get(k, 1.0) 
        for k in scores_dict
    )
    
    # 缩放系数：使加权后的全局均值 = 原始全局均值
    scale_factor = global_base_total / global_weighted_total if global_weighted_total > 0 else 1.0
    
    return weighted_total * scale_factor


# 每周优化后更新历史统计
def update_history_stats(pick_tracking_df):
    """从 pick_tracking 更新全局评分统计
    
    注意：依赖 pick_tracking 表的 score_* 字段（见 B.4.1 表结构变更）
    """
    exited = pick_tracking_df[pick_tracking_df['status'] == 'exited']
    
    stats = {}
    for dim in ['wave_gain', 'shallow_dd', 'day_gain', 'volume', 'ma_bull', 'sector', 'signal_bonus']:  # 修正：加入 signal_bonus 监控
        stats[f'avg_{dim}'] = exited[f'score_{dim}'].mean()
    
    return stats


"""
B.4.1 pick_tracking 表结构变更（新增评分维度字段）

现有 pick_tracking 表缺少各维度评分字段，无法支持评分权重归一化。
需要新增以下字段：

ALTER TABLE pick_tracking ADD COLUMN score_wave_gain REAL;      -- 波段涨幅评分（0/10/20）
ALTER TABLE pick_tracking ADD COLUMN score_shallow_dd REAL;     -- 回调深度评分（0/10/15）
ALTER TABLE pick_tracking ADD COLUMN score_day_gain REAL;       -- 当日涨幅评分（0/5/10/15）
ALTER TABLE pick_tracking ADD COLUMN score_volume REAL;         -- 量比评分（0/5/10）
ALTER TABLE pick_tracking ADD COLUMN score_ma_bull REAL;        -- 均线排列评分（0/5/10）
ALTER TABLE pick_tracking ADD COLUMN score_sector REAL;         -- 板块动量评分（0/5）
ALTER TABLE pick_tracking ADD COLUMN score_signal_bonus REAL;   -- 信号类型加分（0/10）
ALTER TABLE pick_tracking ADD COLUMN score_base REAL DEFAULT 5; -- 基础分

写入时机：
  - daily_scanner.py 选股时计算各维度评分，写入 pick_tracking 表
  - pick_tracker.py record_picks() 方法扩展，接收评分明细

修改示例（daily_scanner.py）：

    results.append({
        ...
        '评分明细': {
            'score_wave_gain': 20 if wg > 0.30 else (10 if wg > 0.20 else 0),
            'score_shallow_dd': 15 if dd < 0.08 else (10 if dd < 0.15 else 0),
            'score_day_gain': 15 if tp > 0.07 else (10 if tp > 0.05 else (5 if tp > 0.03 else 0)),
            'score_volume': 10 if vr > 2 else (5 if vr > 1.5 else 0),
            'score_ma_bull': 10 if ma5 > ma10 > ma20 else (5 if ma5 > ma10 else 0),
            'score_sector': 5 if sector_strong else 0,
            'score_signal_bonus': 10 if sig == '异动不跌' else 0,
            'score_base': 5,
        },
        '评分': total_score,
    })
"""
```

---

### B.5 环境感知增强

**问题：** MA5/MA20 判断过于简化，震荡市频繁切换影响稳定性。

**解决方案：** 加入连续天数要求 + 平滑处理：

```python
def get_market_regime_smoothed(index_data, lookback=20, new_value_weight=0.3):
    """
    增强的市场环境判断（连续天数+平滑处理）
    
    Args:
        index_data: 指数K线数据
        lookback: 回看窗口
        new_value_weight: 新值权重（默认0.3，即新值占30%，旧值占70%）
                          值越小越保守，值越大越敏感
    
    Returns:
        (regime_type, activity_coefficient, consecutive_days)
    """
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
    
    # 连续天数计算（最近5天都是同一环境才算确认）
    regime_history = []
    for i in range(-5, 0):
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
    
    # 连续5天确认才生效，否则保持上一状态
    if consecutive_days >= 5:
        confirmed_regime = raw_regime
    else:
        # 从 market_regime 表读取上一状态
        prev_regime = get_previous_regime()
        confirmed_regime = prev_regime
        consecutive_days = consecutive_days  # 继续累计
    
    # 平滑处理活跃度系数（新值权重0.3 = 新值占30%，旧值占70%）
    prev_coeff = get_previous_activity_coefficient()
    smoothed_coeff = prev_coeff * (1 - new_value_weight) + raw_coeff * new_value_weight
    
    return confirmed_regime, smoothed_coeff, consecutive_days
```

---

### B.6 沙盒滚动窗口验证

**问题：** 4-8周验证周期过长，参数可能已过时。

**解决方案：** 滚动窗口验证（每周评估，连续K周通过才应用）：

```python
SANDBOX_VALIDATION_CONFIG = {
    'min_weeks_to_confirm': 3,      # 连续3周通过才应用
    'min_samples_per_week': 5,      # 每周至少5笔新退出样本
    'rolling_window_days': 30,      # 滚动窗口30天
}

def rolling_sandbox_validate(sandbox_config, weeks_passed):
    """
    滚动窗口沙盒验证
    
    每周运行一次，使用最近30天退出数据评估
    连续3周通过才正式应用
    """
    results = []
    
    for week in range(weeks_passed):
        # 取最近30天退出样本
        recent_exits = get_recent_exits(days=30)
        sandbox_exits = apply_sandbox_config(recent_exits, sandbox_config)
        live_exits = apply_current_config(recent_exits)
        
        # 本周验证结果
        sandbox_exp = calculate_expectancy(sandbox_exits)
        live_exp = calculate_expectancy(live_exits)
        
        week_passed = sandbox_exp >= live_exp * get_threshold(len(sandbox_exits))
        results.append(week_passed)
    
    # 修正：检查最近N周是否全部通过（连续通过）
    min_weeks = SANDBOX_VALIDATION_CONFIG['min_weeks_to_confirm']
    recent_results = results[-min_weeks:]
    
    # all() 确保全部通过，才是"连续"
    consecutive_all_passed = all(recent_results) and len(recent_results) >= min_weeks
    
    if consecutive_all_passed:
        return {'status': 'passed', 'weeks_evaluated': len(results)}
    elif len(results) < min_weeks:
        return {'status': 'evaluating', 'weeks_passed': len(results)}
    else:
        return {'status': 'failed', 'reason': '最近{}周未全部通过'.format(min_weeks)}
```

---

### B.7 adaptive_engine.py 调度逻辑

**问题：** 核心控制器调度逻辑未定义。

**解决方案：** 伪代码：

```python
# adaptive_engine.py
class AdaptiveEngine:
    """自适应引擎核心控制器"""
    
    def __init__(self):
        self.monitor = DailyMonitor()
        self.optimizer = WeeklyOptimizer()
        self.sandbox = SandboxValidator()
        self.history = OptimizationHistory()
    
    def run_daily(self):
        """每日调度（由 daily_run.sh --track 后调用）"""
        try:
            # 1. 运行监控
            alerts = self.monitor.run()
            
            # 2. 检查是否有critical级别预警
            critical = [a for a in alerts if a['severity'] == 'critical']
            if critical:
                self._handle_critical(critical)
            
            # 3. 更新信号状态表的live字段
            self._update_signal_live_stats()
            
            # 4. 检查沙盒状态（修正：增加已应用检查，防止重复应用）
            sandbox_status = self.sandbox.get_status()
            # 只有 pending 状态的变更才检查是否通过
            pending_changes = self.history.get_pending_sandbox_changes()
            if sandbox_status['weeks_passed'] >= 3 and pending_changes:
                self._apply_sandbox_if_passed(pending_changes)
            
        except Exception as e:
            self._log_error('daily', e)
    
    def run_weekly(self):
        """每周调度（由 daily_run.sh --weekly-optimize 调用）"""
        try:
            # 1. 环境更新
            self.optimizer.update_market_regime()
            
            # 2. 参数层优化（复用StrategyOptimizer）
            param_changes = self.optimizer.run_param_optimization()
            
            # 3. 评分层调整
            score_changes = self.optimizer.adjust_score_weights()
            
            # 4. 信号层检查
            signal_changes = self.optimizer.check_signal_status()
            
            # 5. 所有变更写入sandbox
            for change in [param_changes, score_changes, signal_changes]:
                if change:
                    self.sandbox.save(change)
                    self.history.record_pending(change)
            
            # 6. 输出周报
            self._generate_weekly_report()
            
        except Exception as e:
            self._log_error('weekly', e)
    
    def _handle_critical(self, alerts):
        """处理critical预警"""
        for alert in alerts:
            # 立即通知用户（不自动调整）
            self._notify_user(alert)
            # 记录到历史
            self.history.record_alert(alert)
    
    def _apply_sandbox_if_passed(self, pending_changes):
        """沙盒验证通过后应用（修正：防止重复应用）
        
        Args:
            pending_changes: 待应用的变更列表（status='pending'的记录）
        """
        # 再次检查这些变更是否已应用（双重保险）
        unapplied = [c for c in pending_changes if c.get('sandbox_test_result') == 'pending']
        if not unapplied:
            return  # 无待应用变更
        
        sandbox_results = self.sandbox.validate(unapplied)
        if sandbox_results['passed']:
            # 应用变更
            for change in sandbox_results['changes']:
                self._apply_change(change)
                # 立即标记为已应用，防止重复
                self.history.record_applied(change)
                self.sandbox.mark_applied(change['id'])
        else:
            self.history.record_failed(sandbox_results)
```

---

### B.8 strategy_config 扩展支持动态 category

**问题：** set() 方法从 DEFAULTS 读取 description 和 category，新增参数不在 DEFAULTS 中会丢失。

**解决方案：** 扩展 StrategyConfig 类：

```python
# strategy_config.py 修改

class StrategyConfig:
    # 现有 DEFAULTS 不变
    
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
        
        # environment 类参数
        'activity_coefficient': (1.0, '当前环境活跃度系数', 'environment'),
        'bull_threshold': (1.0, '上升期活跃度', 'environment'),
        'range_threshold': (0.7, '震荡期活跃度', 'environment'),
        'bear_threshold': (0.3, '退潮期活跃度', 'environment'),
    }
    
    def set(self, key, value, description=None, category=None):
        """
        设置参数值，支持动态参数
        
        修改：若不在DEFAULTS中，从DYNAMIC_PARAMS获取description和category
        """
        # 先检查DEFAULTS
        if key in self.DEFAULTS:
            default_desc = self.DEFAULTS[key][2]
            default_cat = self.DEFAULTS[key][1]
        elif key in self.DYNAMIC_PARAMS:
            default_desc = self.DYNAMIC_PARAMS[key][2]
            default_cat = self.DYNAMIC_PARAMS[key][1]
        else:
            default_desc = description or ''
            default_cat = category or 'unknown'
        
        # 使用传入值或默认值
        final_desc = description or default_desc
        final_cat = category or default_cat
        
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO strategy_config (param_key, param_value, description, category, updated_at) VALUES (?, ?, ?, ?, ?)",
                (key, value, final_desc, final_cat, datetime.now().strftime('%Y-%m-%d %H:%M'))
            )
    
    def get_weights(self):
        """获取所有 score_weight 类参数"""
        return self.get_by_category('score_weight')
    
    def get_environment(self):
        """获取所有 environment 类参数"""
        return self.get_by_category('environment')
```

---

### B.9 sandbox_validator 状态管理（新增，解决重复应用问题）

**问题：** run_daily() 和 run_weekly() 都可以触发 sandbox 应用，缺少互斥。

**解决方案：** sandbox_validator 增加 applied 状态标记：

```python
# sandbox_validator.py 修改

class SandboxValidator:
    """沙盒验证器，增加状态管理防止重复应用"""
    
    # 沙盒变更状态
    STATUS_PENDING = 'pending'      # 待验证
    STATUS_PASSED = 'passed'        # 验证通过，待应用
    STATUS_APPLIED = 'applied'      # 已应用
    STATUS_FAILED = 'failed'        # 验证失败
    
    def save(self, change):
        """保存变更到sandbox"""
        change['sandbox_test_result'] = self.STATUS_PENDING
        change['weeks_passed'] = 0
        change['validation_started_at'] = datetime.now().strftime('%Y-%m-%d')  # 修正：记录验证起始时间
        self._write_to_optimization_history(change)
    
    def get_status(self):
        """获取当前sandbox状态
        
        修正：使用 validation_started_at（验证起始时间）而非 created_at（创建时间）
        这样如果中间因验证失败重置过计数器，validation_started_at 会随之更新
        """
        pending = self._get_pending_changes()
        if pending:
            # 取验证起始时间最早的记录（或直接取最小 weeks_passed）
            oldest = min(pending, key=lambda x: x['validation_started_at'])
            min_weeks = min(c['weeks_passed'] for c in pending)
        else:
            oldest = None
            min_weeks = 0
        
        return {
            'has_pending': len(pending) > 0,
            'weeks_passed': min_weeks,  # 修正：取最小值，更精确
            'pending_count': len(pending),
            'validation_started': oldest['validation_started_at'] if oldest else None,
        }
    
    def mark_applied(self, change_id):
        """标记变更已应用"""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE optimization_history SET sandbox_test_result = ?, apply_date = ? WHERE id = ?",
                (self.STATUS_APPLIED, datetime.now().strftime('%Y-%m-%d'), change_id)
            )
    
    def validate(self, pending_changes):
        """验证待应用变更"""
        results = []
        for change in pending_changes:
            if change['sandbox_test_result'] != self.STATUS_PENDING:
                continue  # 跳过已处理
            
            # 执行验证逻辑
            validation = self._validate_single(change)
            if validation['passed']:
                change['sandbox_test_result'] = self.STATUS_PASSED
            else:
                change['sandbox_test_result'] = self.STATUS_FAILED
            results.append(change)
        
        passed = all(c['sandbox_test_result'] == self.STATUS_PASSED for c in results)
        return {'passed': passed, 'changes': results}
```

---

### B.10 修订汇总（第二轮）

| 评审问题 | 解决方案 | 章节 |
|----------|----------|------|
| limit_up_open_next命名不一致 | 统一为 limit_up_open_next_strong | B.2 |
| pick_tracking缺少评分字段 | 新增 score_* 字段DDL | B.4.1 |
| consecutive_passes计算错误 | 改用 all() 检查连续通过 | B.6 |
| smooth_factor命名反直觉 | 改名为 new_value_weight + 注释 | B.5 |
| sandbox缺少互斥机制 | 增加 STATUS_APPLIED 状态标记 | B.9 |

---

### B.11 修订汇总（第三轮）

| 评审问题 | 解决方案 | 章节 |
|----------|----------|------|
| signal_status DDL注释不一致 | 改为英文主键 + display_name | 正文293行 |
| weeks_passed使用created_at | 新增 validation_started_at 字段 | B.9 |
| 维度列表遗漏signal_bonus | 加入 'signal_bonus' 监控 | B.4.1 |

---

### B.12 optimization_history 表新增字段（补充）

为支持 validation_started_at，需扩展 optimization_history 表：

```sql
-- 在原有 optimization_history 表结构基础上新增：
ALTER TABLE optimization_history ADD COLUMN validation_started_at TEXT;
-- 验证起始时间（首次开始滚动验证的时间）
-- 与 created_at 区分：created_at 是变更写入时间，validation_started_at 是验证周期开始时间
-- 如果验证失败重置，validation_started_at 会随之更新，created_at 保持不变
```