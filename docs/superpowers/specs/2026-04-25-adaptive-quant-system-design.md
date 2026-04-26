# 自适应量化系统设计（修订版）

日期: 2026-04-25
修订原因: 解决原设计中与现有系统冲突、样本阈值不合理、风险指标单一等问题

## 概述

为 shikong_fufei 量化系统添加四层自适应优化能力：
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
│  │ 环境感知        │  │环境感知调整      │  │分级阈值验证        │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────────┘  │
│           │                    │                    │               │
│           └────────────────────┼────────────────────┘               │
│                                ▼                                    │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ adaptive_engine.py 内置变更管理                                 │ │
│  │ 变更记录 + 追溯分析 + 回滚机制                                   │ │
│  └────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ normalizer.py 评分归一化                                        │ │
│  │ 基于历史样本全局统计 + 渐进置信度                                │ │
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
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │daily_monitor_log│  │ market_regime   │  │trading_day_cache    │  │
│  │ 每日监控日志    │  │ 环境状态记录    │  │交易日缓存          │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────┘  │
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
# [简化版 - 已废弃，生产环境请使用附录B.5的 get_market_regime_smoothed()]
def get_market_regime(index_data, lookback=20):
    """判断市场环境：上升期/震荡期/退潮期（简化版，仅供参考）"""
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

| 环境 | 活跃度系数 | 评分阈值调整公式 | 实际效果 |
|------|-----------|------------------|----------|
| 上升期 | 1.0 | base / 1.0 | 基准阈值不变 |
| 震荡期 | 0.7 | base / 0.7 | 阈值提高至约1.43倍（提高43%） |
| 退潮期 | 0.3 | base / 0.3 | 阈值提高至约3.33倍（仅保留高分信号） |

**说明：** 活跃度系数越低，需要更高的评分才能入选，从而减少选股数量、提高质量门槛。

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
  ├── 若某信号退出样本<10笔 → 保持active状态
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

**样本阈值体系对比（信号降级 vs 沙盒验证）：**

| 样本量 | 信号降级 | 沙盒验证 | 适用场景区别 |
|--------|----------|----------|--------------|
| <10笔 | 保持active（冷启动保护） | 使用最严格阈值×1.15 | 信号降级：不做判断；沙盒验证：可评估但要求极高 |
| 10笔 | 可降级至watching | 开始评估，阈值×1.15 | 信号降级：首次可降级；沙盒验证：首次可决策 |
| 20笔 | 可降级至watching | 中等阈值×1.08 | 沙盒验证放宽但信号降级仍保守 |
| 30笔 | 可降级至warning/disabled | 正式决策，阈值×1.02 | 信号降级：可深度降级；沙盒验证：接近等效 |
| 50笔 | 可降级至disabled | 高置信应用，阈值×0.98 | 两机制均高度可信 |

**核心区别**：
- **信号降级**：基于单个信号的期望值判断，决定是否降低权重或禁用
- **沙盒验证**：基于参数变更后的整体表现对比，决定是否应用新参数

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

分级阈值实现代码：
```python
# sandbox_validator.py 新增分级阈值函数

def get_sample_adjusted_threshold(sample_count, base_expectancy):
    """
    根据样本量动态调整验证阈值
    
    样本少时要求更严格，样本充足时放宽要求
    
    Args:
        sample_count: 验证窗口内的退出样本数
        base_expectancy: 正式参数的期望值（基准）
    
    Returns:
        required_expectancy: 样本调整后的要求期望值
        threshold_multiplier: 阈值乘数因子
    """
    TIERED_THRESHOLDS = {
        # 样本量下限 → (阈值乘数, 说明)
        10: (1.15, '样本少，要求更高'),
        20: (1.08, '中等保守'),
        30: (1.02, '允许小幅下降'),
        50: (0.98, '接近等效即可'),
    }
    
    # 找到适用的阈值级别
    multiplier = 1.15  # 默认最严格
    reason = '样本不足10笔，使用最严格阈值'
    
    for threshold_samples, (mult, desc) in sorted(TIERED_THRESHOLDS.items(), reverse=True):
        if sample_count >= threshold_samples:
            multiplier = mult
            reason = desc
            break
    
    # 边界情况处理：当基准期望值 <= 0 时，乘数放大无意义（负值乘数反而更宽松）
    # 此时应使用固定正阈值作为验证线，而非乘数关系
    if base_expectancy <= 0:
        # 基准为负或零 → 新参数至少要达到正阈值才算改进
        required_expectancy = 0.005  # 固定阈值：0.5%（至少要有正向收益）
        reason = f'{reason}，基准<=0时使用固定阈值'
        multiplier = None  # 乘数逻辑不适用
    else:
        required_expectancy = base_expectancy * multiplier
    
    return {
        'required_expectancy': required_expectancy,
        'multiplier': multiplier,
        'reason': reason,
        'tier': threshold_samples if sample_count >= 10 else 0,
        'base_expectancy': base_expectancy,
    }


# sandbox_validator.py 修改 _make_decision 方法

def _make_decision(self, validation_result, sample_count):
    """
    根据验证结果和样本量做出决策
    
    修正：使用分级阈值而非固定阈值
    
    Returns:
        'pass' | 'fail' | 'continue'
    """
    metrics = validation_result['metrics']
    comparison = validation_result['comparison']
    
    win_rate = metrics['win_rate']
    expectancy = metrics['expectancy']
    improvement = comparison['improvement']
    baseline_expectancy = comparison['baseline_expectancy']
    
    # 使用分级阈值
    threshold_info = get_sample_adjusted_threshold(sample_count, baseline_expectancy)
    required_expectancy = threshold_info['required_expectancy']
    
    # 通过条件：期望值 >= 分级阈值 + 胜率 >= 50 + 有提升
    if (expectancy >= required_expectancy and
        win_rate >= self.config['pass_win_rate_threshold'] and
        improvement >= self.config['improvement_threshold']):
        return 'pass', threshold_info
    
    # 失败条件
    if (expectancy <= self.config['fail_expectancy_threshold'] or
        win_rate <= self.config['fail_win_rate_threshold']):
        return 'fail', threshold_info
    
    # 继续观察
    return 'continue', threshold_info
```

---

## 沙盒验证阈值配置

沙盒验证模块使用的阈值配置定义如下：

```python
SANDBOX_VALIDATION_CONFIG = {
    # 滚动窗口配置
    'validation_window_weeks': 3,      # 验证窗口：3周
    'min_validation_trades': 10,       # 最小验证交易数
    
    # 通过阈值（必须同时满足）
    'pass_expectancy_threshold': 0.005, # 期望值阈值：0.5%
    'pass_win_rate_threshold': 50,      # 胜率阈值：50%
    
    # 失败阈值（满足任一即失败）
    'fail_expectancy_threshold': -0.02, # 期望值阈值：-2%
    'fail_win_rate_threshold': 40,      # 胜率阈值：40%
    
    # 比较阈值
    'improvement_threshold': 0.002,     # 相比基准提升：0.2%
    
    # 样本约束
    'min_samples_per_week': 5,          # 每周最小样本数（低于此值不算有效验证周）
}
```

阈值说明：
- **通过条件**：期望值 ≥ 分级阈值 且 胜率 ≥ 50% 且 有正向提升（≥0.2%）
- **失败条件**：期望值 ≤ -2% 或 胜率 ≤ 40%
- **中间情况**：继续观察，暂不做决策

---

## 数据表设计（修订版）

### strategy_config（现有表扩展）

不新建 `score_weights` 表，扩展现有 `strategy_config` 表的 `category` 字段：

```sql
-- 现有表结构已支持，仅需新增category值：
-- category 可选值：'entry' | 'exit' | 'scoring' | 'score_weight' | 'environment'

-- 评分权重参数示例（新增category='score_weight'）：
-- 注意：权重参数命名规则为 weight_{评分维度名}，与 SCORE_DIMENSIONS 定义对应
INSERT INTO strategy_config (param_key, param_value, description, category, updated_at)
VALUES
('weight_wave_gain', 1.0, '波段涨幅评分权重系数', 'score_weight', datetime('now')),
('weight_shallow_dd', 1.0, '浅回调评分权重系数', 'score_weight', datetime('now')),
('weight_strong_gain', 1.0, '强势涨幅评分权重系数', 'score_weight', datetime('now')),
('weight_volume', 1.0, '放量评分权重系数', 'score_weight', datetime('now')),
('weight_ma_bull', 1.0, '多头排列评分权重系数', 'score_weight', datetime('now')),
('weight_sector', 1.0, '板块动量评分权重系数', 'score_weight', datetime('now')),
('weight_signal_bonus', 1.0, '信号类型加分权重系数', 'score_weight', datetime('now'));
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
    sandbox_test_result TEXT,    -- 'pending' | 'passed' | 'applied' | 'failed'（四态：待验证/验证通过/已应用/验证失败）
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

### trading_day_cache（新增）

缓存交易日检查结果，避免非交易日重复 API 调用：

```sql
CREATE TABLE IF NOT EXISTS trading_day_cache (
    date TEXT PRIMARY KEY,           -- 检查的日期
    is_trading_day INTEGER,          -- 0=非交易日, 1=交易日
    checked_at TEXT,                 -- 检查时间
    data_available INTEGER           -- 数据源是否有数据
);
```

缓存策略：
- 当天检查的，或检查时间在收盘后（≥17:00）→ 缓存有效
- API多次失败或17:00前检查 → 不缓存（数据源可能未更新）
- 非交易日首次检查后后续执行直接使用缓存

---

## 评分权重调整公式（明确版）

**调整公式：**

```python
def adjust_score_weight(current_weight, correlation, base_weight=1.0):
    """
    根据相关性调整评分权重

    Args:
        current_weight: 当前权重系数（相对于基准）
        correlation: Spearman相关性（评分维度与期望值）
        base_weight: 原始基准权重值（默认1.0），作为权重回归锚点

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
    
    # 回归锚点：仅在有权重调整时回归，防止持续单向漂移
    # 注意：delta=0时不回归，避免无调整时权重自然衰减
    if delta != 0:
        regression_factor = 0.05  # 回归系数：每次调整后向基准微幅回归5%
        new_weight = new_weight * (1 - regression_factor) + base_weight * regression_factor

    return new_weight

def normalize_scores(scores_dict, weights_dict, base_total=50):
    """
    [已废弃] 归一化评分，使总分均值≈基准值
    
    ⚠️ 此函数基于单样本计算归一化因子，会导致每只股票的归一化系数不同。
    请使用 B.4 定义的新版 normalize_scores_global() 函数。
    
    此函数仅保留作为历史参考，实际实施时不应使用。
    
    Args:
        scores_dict: 各维度原始评分 {'wave_gain': 10, 'shallow_dd': 15, ...}
        weights_dict: 各维度权重系数 {'wave_gain': 1.2, 'shallow_dd': 0.9, ...}
        base_total: 目标总分均值
    
    Returns:
        normalized_total: 归一化后的总分
    
    问题：base_raw = sum(scores_dict.values()) 使用单样本的原始分之和，
    不同股票的 base_raw 不同，导致归一化系数不统一。
    """
    raw_total = sum(scores_dict[k] * weights_dict[k] for k in scores_dict)
    base_raw = sum(scores_dict.values())  # [问题] 单样本基准分
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
adaptive_engine.py      # 核心控制器（调度每日监控+每周优化 + 变更历史管理）
daily_monitor.py        # 每日监控模块 + 环境感知
weekly_optimizer.py     # 每周四层优化模块（参数OOS验证+期望值驱动）
sandbox_validator.py    # 沙盒验证模块
normalizer.py           # 评分归一化模块（基于历史样本全局统计）
signal_constants.py     # 信号类型常量定义（英文主键↔中文显示映射）
strategy_config.py      # 参数中心（含DYNAMIC_PARAMS动态参数支持）
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
        lookback: 回看窗口（保留参数，暂未使用，未来可用于更长周期的MA计算）
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
    'min_samples_per_week': 5,      # 每周至少5笔新退出样本（低于此值不算有效验证周）
    'rolling_window_days': 30,      # 滚动窗口30天
}

def rolling_sandbox_validate(sandbox_config, weeks_passed):
    """
    滚动窗口沙盒验证
    
    每周运行一次，累计验证结果
    连续3周通过才正式应用
    使用分级阈值（见 get_sample_adjusted_threshold 函数）
    
    Args:
        sandbox_config: 待验证的参数配置
        weeks_passed: 已累计验证的周数（从 optimization_history.weeks_passed 读取）
    
    Returns:
        dict: {'status': 'passed'|'evaluating'|'failed', 'details': [...]}
    
    注意：此函数由 run_weekly() 每周调用一次，weeks_passed 是累计值而非循环次数。
    每次调用只计算当前周的数据，append 到历史结果中。
    """
    # 取最近30天退出样本（当前周验证）
    recent_exits = get_recent_exits(days=30)
    
    # 检查样本数是否满足最低要求
    min_samples = SANDBOX_VALIDATION_CONFIG['min_samples_per_week']
    if len(recent_exits) < min_samples:
        # 样本不足，本周不算有效验证，继续 pending
        return {
            'status': 'evaluating',
            'reason': f'本周样本不足({len(recent_exits)}笔<{min_samples}笔)',
            'weeks_passed': weeks_passed,  # 不增加
            'details': None,
        }
    
    sandbox_exits = apply_sandbox_config(recent_exits, sandbox_config)
    live_exits = apply_current_config(recent_exits)
    
    # 本周验证结果
    sandbox_exp = calculate_expectancy(sandbox_exits)
    live_exp = calculate_expectancy(live_exits)
    
    # 使用分级阈值
    threshold_info = get_sample_adjusted_threshold(len(sandbox_exits), live_exp)
    required_exp = threshold_info['required_expectancy']
    
    week_passed = sandbox_exp >= required_exp
    
    # 读取历史验证结果（从 optimization_history）
    history_results = get_sandbox_validation_history(sandbox_config['optimize_id'])
    
    # 累加本周结果
    current_result = {
        'week': weeks_passed + 1,
        'passed': week_passed,
        'sandbox_exp': sandbox_exp,
        'live_exp': live_exp,
        'threshold_info': threshold_info,
        'sample_count': len(recent_exits),
        'validated_at': datetime.now().strftime('%Y-%m-%d'),
    }
    all_results = history_results + [current_result]
    
    # 检查最近N周是否全部通过（连续通过）
    min_weeks = SANDBOX_VALIDATION_CONFIG['min_weeks_to_confirm']
    recent_results = [r['passed'] for r in all_results[-min_weeks:]]
    
    # all() 确保全部通过，才是"连续"
    # 同时检查样本数约束：最近N周的样本数都 >= min_samples_per_week
    recent_samples_ok = all(r.get('sample_count', 0) >= min_samples for r in all_results[-min_weeks:])
    consecutive_all_passed = all(recent_results) and len(recent_results) >= min_weeks and recent_samples_ok
    
    if consecutive_all_passed:
        return {'status': 'passed', 'weeks_evaluated': len(all_results), 'details': all_results}
    elif len(all_results) < min_weeks:
        return {'status': 'evaluating', 'weeks_passed': len(all_results), 'details': all_results}
    else:
        return {'status': 'failed', 'reason': '最近{}周未全部通过'.format(min_weeks), 'details': all_results}


def get_sandbox_validation_history(optimize_id):
    """
    从 optimization_history 读取历史验证结果
    
    Args:
        optimize_id: optimization_history 表的记录ID
    
    Returns:
        list: 历史验证结果 [{'week': 1, 'passed': True, ...}, ...]
    """
    # 从数据库读取 validation_details JSON 字段（需在 optimization_history 表新增）
    with get_conn() as conn:
        row = conn.execute("""
            SELECT validation_details FROM optimization_history WHERE id=?
        """, (optimize_id,)).fetchone()
    
    if row and row[0]:
        return json.loads(row[0])
    return []
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
    # 元组格式：(默认值, category, description)
    # 注意：category在第2位，description在第3位（与SQL示例顺序不同，请参考set()方法读取逻辑）
    DYNAMIC_PARAMS = {
        # score_weight 类参数
        # 权重命名规则：weight_{评分维度名}，与 normalizer.py 的 SCORE_DIMENSIONS 对应
        'weight_wave_gain': (1.0, 'score_weight', '波段涨幅评分权重系数'),
        'weight_shallow_dd': (1.0, 'score_weight', '浅回调评分权重系数'),
        'weight_strong_gain': (1.0, 'score_weight', '强势涨幅评分权重系数'),
        'weight_volume': (1.0, 'score_weight', '放量评分权重系数'),
        'weight_ma_bull': (1.0, 'score_weight', '多头排列评分权重系数'),
        'weight_sector': (1.0, 'score_weight', '板块动量评分权重系数'),
        'weight_signal_bonus': (1.0, 'score_weight', '信号类型加分权重系数'),
        
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

---

### B.13 修订汇总（第四轮）

| 评审问题 | 解决方案 | 章节 |
|----------|----------|------|
| sandbox_test_result注释缺applied | 补充四态注释 | 正文326行 |
| normalize_scores与新版并存 | 标注旧版为[已废弃] | 正文406行 |

---

### B.14 归一化函数实现设计（新增）

#### B.14.1 文件结构

新增独立模块 `normalizer.py`：

```
shikong_fufei/
├── normalizer.py          # 新增模块
│   ├── get_history_stats()    # 获取历史统计（从pick_tracking查询）
│   ├── normalize_scores()     # 归一化评分（选股时调用）
│   └── ScoreNormalizer 类     # 封装逻辑，支持置信度标记
```

**设计决策**：
- 选择方案A（独立模块）而非集成到 strategy_config 或 weekly_optimizer
- 原因：归一化是核心计算逻辑，独立模块便于测试、避免循环依赖、daily_scanner和weekly_optimizer都需要调用

---

#### B.14.2 评分维度定义

```python
# normalizer.py

SCORE_DIMENSIONS = [
    'wave_gain',      # 波段涨幅评分（score_wave_gain）
    'shallow_dd',     # 回调深度评分（score_shallow_dd）
    'day_gain',       # 当日涨幅评分（score_day_gain）
    'volume',         # 放量评分（score_volume）
    'ma_bull',        # 多头排列评分（score_ma_bull）
    'sector',         # 板块动量评分（score_sector）
    'signal_bonus',   # 信号类型加分（score_signal_bonus）
]

# 注意：score_base 基础分不纳入权重调整范围，始终保持固定值5分
```

---

#### B.14.3 ScoreNormalizer 类设计

```python
class ScoreNormalizer:
    """评分归一化器：基于历史样本全局统计归一化
    
    设计目标：
    1. 调整评分权重后，总分均值保持稳定（≈基准值）
    2. 支持渐进置信度：样本不足时返回低置信标记
    3. 实时查询历史样本，无需缓存机制
    """
    
    MIN_SAMPLES = 30      # 最小样本阈值（低于此值返回低置信）
    RECENT_WINDOW = 100   # 样本充足时使用最近100笔
    
    def __init__(self, db_path=None):
        """初始化，连接数据层"""
        if db_path is None:
            self.dl = get_data_layer()
        else:
            from data_layer import StockDataLayer
            self.dl = StockDataLayer(db_path)
    
    def get_history_stats(self):
        """
        获取历史评分统计（从 pick_tracking 实时查询）
        
        渐进升级机制：
        - n < 30：全部样本，低置信度
        - n >= 50：最近100笔，中等置信度
        - n >= 30 且 n < 50：全部样本，中等置信度
        
        Returns:
            stats: dict {'avg_wave_gain': 12.3, 'avg_shallow_dd': 8.7, ...}
            meta: dict {'method': 'all'|'recent_100', 'confidence': 'low'|'medium'|'high', 'n': int}
        """
        with self.dl._get_conn() as conn:
            exited_df = pd.read_sql(
                "SELECT * FROM pick_tracking WHERE status='exited'",
                conn
            )
        
        n = len(exited_df)
        
        if n < self.MIN_SAMPLES:
            # 样本不足：全部样本，低置信
            stats = self._calculate_stats(exited_df)
            return stats, {'method': 'all', 'confidence': 'low', 'n': n}
        
        elif n >= self.MIN_SAMPLES + self.RECENT_WINDOW:
            # 样本充足：最近100笔，高置信
            recent = exited_df.tail(self.RECENT_WINDOW)
            stats = self._calculate_stats(recent)
            return stats, {'method': 'recent_100', 'confidence': 'high', 'n': n}
        
        else:
            # 样本中等：全部样本，中等置信
            stats = self._calculate_stats(exited_df)
            return stats, {'method': 'all', 'confidence': 'medium', 'n': n}
    
    def _calculate_stats(self, df):
        """计算各评分维度的均值"""
        stats = {}
        for dim in SCORE_DIMENSIONS:
            col_name = f'score_{dim}'
            if col_name in df.columns and df[col_name].notna().sum() > 0:
                stats[f'avg_{dim}'] = df[col_name].mean()
            else:
                stats[f'avg_{dim}'] = 10.0  # 默认值
        return stats
    
    def normalize_scores(self, scores_dict, weights_dict):
        """
        归一化评分
        
        核心公式：
        scale_factor = global_base_total / global_weighted_total
        normalized_score = weighted_total * scale_factor
        
        其中：
        - global_base_total = Σ history_avg[dim] （权重=1.0时的基准总分）
        - global_weighted_total = Σ history_avg[dim] × current_weight[dim]
        - weighted_total = Σ current_score[dim] × current_weight[dim]
        
        Args:
            scores_dict: 当前股票各维度评分 {'wave_gain': 20, 'shallow_dd': 15, ...}
            weights_dict: 当前权重 {'weight_wave_gain': 1.2, ...}
        
        Returns:
            normalized_score: float 归一化后总分（不含score_base）
            meta: dict {'method': ..., 'confidence': ..., 'n': ..., 'scale_factor': ...}
        """
        # 1. 获取历史统计
        history_stats, meta = self.get_history_stats()
        
        # 2. 计算当前股票加权总分
        weighted_total = sum(
            scores_dict.get(dim, 0) * weights_dict.get(f'weight_{dim}', 1.0)
            for dim in SCORE_DIMENSIONS
        )
        
        # 3. 计算全局基准总分（权重=1.0）
        global_base_total = sum(
            history_stats.get(f'avg_{dim}', 10.0) for dim in SCORE_DIMENSIONS
        )
        
        # 4. 计算全局加权总分（当前权重）
        global_weighted_total = sum(
            history_stats.get(f'avg_{dim}', 10.0) * weights_dict.get(f'weight_{dim}', 1.0)
            for dim in SCORE_DIMENSIONS
        )
        
        # 5. 缩放因子
        scale_factor = global_base_total / global_weighted_total if global_weighted_total > 0 else 1.0
        
        # 6. 归一化得分
        normalized_score = weighted_total * scale_factor
        
        # 7. 补充meta信息
        meta['scale_factor'] = scale_factor
        meta['weighted_total_raw'] = weighted_total
        meta['global_base_total'] = global_base_total
        meta['global_weighted_total'] = global_weighted_total
        
        return normalized_score, meta
```

---

#### B.14.4 daily_scanner.py 集成修改

在 `detect_pattern()` 或 `_scan_core()` 中调用归一化：

```python
# daily_scanner.py 修改示例

from normalizer import ScoreNormalizer

def detect_pattern(df):
    # ... 原有评分计算逻辑 ...
    
    score_details = {
        'wave_gain': score_wave_gain,
        'shallow_dd': score_shallow_dd,
        'day_gain': score_day_gain,
        'volume': score_volume,
        'ma_bull': score_ma_bull,
        'sector': score_sector,        # 板块评分（新增）
        'signal_bonus': score_signal_bonus,
    }
    
    # 原代码：
    # total_score = score_base + score_wave_gain + score_shallow_dd + ...
    
    # 新代码：使用归一化
    normalizer = ScoreNormalizer()
    cfg = StrategyConfig()
    weights = cfg.get_weights()  # 获取当前权重
    
    normalized_score, meta = normalizer.normalize_scores(score_details, weights)
    total_score = score_base + normalized_score
    
    return {
        'sig': sig,
        'score': total_score,
        'score_normalized': normalized_score,
        'score_raw': sum(score_details.values()),  # 原始总分（不含base）
        'normalization_meta': meta,
        'score_details': {'score_base': score_base, **{f'score_{k}': v for k, v in score_details.items()}},
        # ... 其他字段 ...
    }
```

**注意**：`score_base=5` 基础分不纳入归一化，单独相加。

---

#### B.14.5 weekly_optimizer.py 集成修改

在 `_optimize_score_weights_layer()` 中检查置信度：

```python
# weekly_optimizer.py 修改示例

from normalizer import ScoreNormalizer

def _optimize_score_weights_layer(self, optimize_date):
    """评分层优化：根据 score-pnl 相关性调整权重"""
    
    # 1. 检查历史统计置信度
    normalizer = ScoreNormalizer()
    history_stats, meta = normalizer.get_history_stats()
    
    if meta['confidence'] == 'low':
        return {
            'adjusted': False,
            'reason': f'样本不足({meta["n"]}笔)，暂不调整权重',
            'meta': meta,
        }
    
    # 2. 继续原有优化逻辑...
    correlations = self._compute_score_correlations()
    
    if correlations is None:
        return {'adjusted': False, 'reason': 'insufficient_correlation_data'}
    
    # 3. 调整权重
    current_weights = self.cfg.get_weights()
    weight_changes = {}
    
    for weight_key in current_weights:
        score_key = weight_key.replace('weight_', '')
        if score_key in correlations:
            correlation = correlations[score_key]
            old_weight = current_weights[weight_key]
            new_weight = adjust_score_weight(old_weight, correlation)
            
            if abs(new_weight - old_weight) > 0.01:
                weight_changes[weight_key] = {
                    'old': old_weight,
                    'new': new_weight,
                    'correlation': correlation,
                }
                self.cfg.set(weight_key, new_weight)
    
    # 4. 验证归一化效果（可选）
    if weight_changes:
        # 模拟一只"平均股票"，验证归一化后总分是否稳定
        avg_scores = {dim: history_stats.get(f'avg_{dim}', 10) for dim in SCORE_DIMENSIONS}
        new_weights = {k: v['new'] for k, v in weight_changes.items()}
        updated_weights = {**current_weights, **new_weights}
        
        normalized_avg, _ = normalizer.normalize_scores(avg_scores, updated_weights)
        # 验证：normalized_avg 应 ≈ global_base_total
        
    return {
        'adjusted': len(weight_changes) > 0,
        'weight_changes': weight_changes,
        'correlations': correlations,
        'history_meta': meta,
    }
```

---

#### B.14.6 输出格式示例

选股结果中新增归一化相关字段：

```json
{
  "代码": "002384",
  "名称": "东山精密",
  "评分": 48.5,
  "score_normalized": 43.5,
  "score_raw": 52.0,
  "score_base": 5,
  "normalization_meta": {
    "method": "all",
    "confidence": "low",
    "n": 6,
    "scale_factor": 0.92,
    "weighted_total_raw": 52.0,
    "global_base_total": 70.0,
    "global_weighted_total": 75.4
  },
  "score_details": {
    "score_base": 5,
    "score_wave_gain": 20,
    "score_shallow_dd": 15,
    "score_day_gain": 10,
    "score_volume": 5,
    "score_ma_bull": 10,
    "score_sector": 0,
    "score_signal_bonus": 2
  }
}
```

---

#### B.14.7 单元测试设计

```python
# tests/test_normalizer.py

import pytest
from normalizer import ScoreNormalizer, SCORE_DIMENSIONS

class TestScoreNormalizer:
    
    def test_empty_samples_returns_low_confidence(self):
        """样本为空时返回低置信度"""
        normalizer = ScoreNormalizer(db_path=':memory:')
        stats, meta = normalizer.get_history_stats()
        assert meta['confidence'] == 'low'
        assert meta['n'] == 0
    
    def test_normalize_with_default_weights(self):
        """权重全为1.0时，归一化因子应为1.0"""
        scores = {'wave_gain': 20, 'shallow_dd': 15, ...}
        weights = {f'weight_{dim}': 1.0 for dim in SCORE_DIMENSIONS}
        normalizer = ScoreNormalizer(db_path=':memory:')
        
        normalized, meta = normalizer.normalize_scores(scores, weights)
        # 当权重=1.0时，scale_factor=global_base/global_base=1.0
        assert meta['scale_factor'] == 1.0
        assert normalized == sum(scores.values())
    
    def test_normalize_with_increased_weight(self):
        """增加某维度权重后，归一化应降低缩放因子"""
        scores = {'wave_gain': 20, 'shallow_dd': 15, ...}
        weights_increased = {'weight_wave_gain': 1.5, **{f'weight_{dim}': 1.0 for dim in SCORE_DIMENSIONS if dim != 'wave_gain'}}
        
        normalizer = ScoreNormalizer(db_path=':memory:')
        normalized_default, meta_default = normalizer.normalize_scores(scores, {f'weight_{dim}': 1.0 for dim in SCORE_DIMENSIONS})
        normalized_increased, meta_increased = normalizer.normalize_scores(scores, weights_increased)
        
        # 增加权重后，缩放因子应降低（保持总分稳定）
        assert meta_increased['scale_factor'] < meta_default['scale_factor']
    
    def test_base_score_not_normalized(self):
        """基础分不纳入归一化"""
        score_base = 5
        scores = {'wave_gain': 20, ...}
        weights = {f'weight_{dim}': 1.0 for dim in SCORE_DIMENSIONS}
        
        normalizer = ScoreNormalizer(db_path=':memory:')
        normalized, _ = normalizer.normalize_scores(scores, weights)
        
        # 最终总分 = base + normalized
        total = score_base + normalized
        assert total != normalized  # base分单独加
```

---

#### B.14.8 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| 样本不足导致归一化不稳定 | 返回低置信标记，weekly_optimizer 检查后跳过权重调整 |
| 查询 pick_tracking 性能问题 | 每次选股仅查询一次，样本积累后可考虑缓存 |
| score_* 字段缺失（旧数据） | `_calculate_stats()` 使用默认值10填充 |
| 权重调整过度导致 scale_factor 过小/过大 | 在 adjust_score_weight() 中限制权重范围 [0.7, 1.5] |

---

#### B.14.9 修订汇总（第五轮）

| 评审问题 | 解决方案 | 章节 |
|----------|----------|------|
| 归一化函数缺少实现设计 | 新增 B.14 独立模块设计 | 本节 |
| 历史样本来源未明确 | 实时查询 pick_tracking（选项A） | B.14.3 |
| 样本筛选范围未定义 | 渐进升级机制（全部→最近100笔） | B.14.3 |
| daily_scanner 集成点未说明 | 修改 detect_pattern() | B.14.4 |
| weekly_optimizer 置信度检查缺失 | 新增置信度检查逻辑 | B.14.5 |

---

### B.15 交易日缓存机制（新增）

**问题：** 非交易日多次执行 --scan 时，is_all_updated() 会重复调用 baostock API 检查目标日期是否有数据，浪费时间。

**解决方案：** 新增 trading_day_cache 表缓存检查结果：

```python
# data_layer.py 新增缓存检查逻辑

def is_all_updated(self, target_date=None):
    """
    检查是否所有股票的数据都已更新到目标日期
    
    修正：先检查缓存，避免重复 API 调用
    """
    # ... 原有逻辑 ...
    
    # 先检查缓存
    cached = conn.execute("""
        SELECT is_trading_day, data_available, checked_at
        FROM trading_day_cache WHERE date=?
    """, (target_date,)).fetchone()
    
    if cached:
        is_trading_day = cached[0]
        data_available = cached[1]
        checked_at = cached[2]
        # 缓存有效条件：当天检查的，或检查时间在收盘后（≥17:00）
        checked_dt = datetime.strptime(checked_at, '%Y-%m-%d %H:%M:%S')
        is_same_day = checked_dt.strftime('%Y-%m-%d') == datetime.now().strftime('%Y-%m-%d')
        is_checked_after_close = checked_dt.hour >= 17
        
        if is_same_day or is_checked_after_close:
            if is_trading_day == 0:
                return True, max_date, {'reason': 'non_trading_day', 'cached': True}
            elif data_available == 1:
                return False, max_date, {'reason': 'need_update', 'cached': True}
            else:
                return False, max_date, {'reason': 'data_not_updated', 'cached': True}
    
    # 无缓存或缓存过期 → 执行 API 检查
    # ... 原有 API 检查逻辑 ...
    
    # 检查完成后缓存结果
    if has_target_data or index_has_data:
        self._cache_trading_day(target_date, is_trading_day=True, data_available=True)
    elif not is_early_after_close and api_error_count < 3:
        self._cache_trading_day(target_date, is_trading_day=False, data_available=False)


def _cache_trading_day(self, date, is_trading_day, data_available):
    """缓存交易日检查结果"""
    with self._get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO trading_day_cache
            (date, is_trading_day, checked_at, data_available)
            VALUES (?, ?, datetime('now'), ?)
        """, (date, 1 if is_trading_day else 0, 1 if data_available else 0))
```

**缓存策略说明：**

| 检查结果 | 是否缓存 | 原因 |
|----------|----------|------|
| 非交易日（17:00后确认） | ✓ 缓存 | 确定的非交易日，可直接使用 |
| 交易日数据可用 | ✓ 缓存 | 数据源已更新，后续可增量 |
| 17:00前检查 | ✗ 不缓存 | 数据源可能未更新 |
| API多次失败 | ✗ 不缓存 | 数据源可能不可用 |

---

### B.16 修订汇总（第六轮）

| 评审问题 | 解决方案 | 章节 |
|----------|----------|------|
| 模块清单错误（optimization_history.py） | 合并到 adaptive_engine.py，新增 normalizer.py | 正文508-516行 |
| 冷启动样本阈值不一致（5笔 vs 10笔） | 统一为10笔 | 正文145行 |
| 分级阈值缺少实现代码 | 新增 get_sample_adjusted_threshold() | 正文258-267行后 |
| 沙盒验证缺少trading_day_cache表 | 新增表定义和缓存策略 | 正文447-459行后 |
| 滚动验证缺少分级阈值集成 | 新增 threshold_info 调用 | B.6 |
| 架构图遗漏 normalizer 模块 | 新增归一化模块和 trading_day_cache 表 | 正文18-51行 |

---

### B.17 修订汇总（第七轮）

| 评审问题 | 严重程度 | 解决方案 | 章节 |
|----------|----------|----------|------|
| 概述说"三层"但列了四层 | 轻微 | 改为"四层自适应优化能力" | 正文第8行 |
| 环境判断函数两个版本未指明权威版本 | 中等 | 标注简化版为[已废弃]，指向B.5增强版 | 正文92-106行 |
| 环境阈值调整公式与描述不一致 | 严重 | 更正表格：震荡期提高43%，退潮期提高233% | 正文110-114行 |
| adjust_score_weight未使用base_weight参数 | 严重 | 新增回归锚点逻辑，防止权重单向漂移 | 正文493-513行 |
| weight_anomaly与weight_signal_bonus键名冲突 | 中等 | 统一为weight_signal_bonus，与SCORE_DIMENSIONS对应 | 正文377行、B.8 |
| DYNAMIC_PARAMS元组字段顺序未注释 | 轻微 | 新增元组格式注释：(value, category, description) | B.8 |
| _make_decision引用未定义的配置项 | 中等 | 新增沙盒验证阈值配置章节，明确各阈值数值 | 正文355行后新增章节 |
| min_samples_per_week定义但未使用 | 中等 | 在验证逻辑中新增样本数约束检查 | B.6 |
| 滚动窗口循环无意义（week变量未使用） | 严重 | 重构为累计验证模式，每次调用只算当前周 | B.6 |
| 信号降级与沙盒验证阈值易混淆 | 轻微 | 新增对比表格，明确适用场景区别 | 正文195-215行后 |

---

### B.18 修订汇总（第八轮）

| 评审问题 | 解决方案 | 章节 |
|----------|----------|------|
| get_market_regime_smoothed的lookback参数未使用 | 标注为"保留参数，暂未使用" | B.5 |
| adjust_score_weight回归逻辑始终生效(delta=0时也回归) | 新增条件判断，仅delta≠0时回归 | 正文560-565行 |
| get_sample_adjusted_threshold零/负基准边界处理 | 新增边界逻辑：base<=0时使用固定阈值0.5% | 正文322行 |