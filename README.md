# 弱转强 (Weak-to-Strong) 量化交易系统

A股量化交易系统，基于"弱转强"技术形态，支持本地SQLite缓存、增量数据更新、多日期选股回溯。

## 策略概述

**弱转强** 是一种经典的技术分析交易模式，核心逻辑：

> 股票经历一波强劲上涨 → 进入温和缩量回调（不破位） → 某日出现放量反转信号 → 弱转强买点确认

### 交易逻辑

```
第一波强势上涨 (连续3+天上涨, 涨幅≥15%)
         ↓
    温和回调整理 (回调≤20%, 持续3-15天, 阴线占比<70%)
         ↓
  弱转强反转信号 (放量阳线, 突破前日阴线)
         ↓
     买入 → 止损/止盈管理
```

### 四种信号类型

> 信号按**优先级**依次判断，一旦命中更高级别信号就不再检测低级信号。

| 优先级 | 信号 | 含义 | 触发条件 |
|---|---|---|---|
| 1 (最高) | 异动不跌 | 前日大幅波动次日不跌反涨 | 前日振幅 > 6% **且** 当日涨幅 > 1% |
| 2 | 阳包阴 | 阳线完全包住前日阴线实体 | 前日收阴 **且** 当日涨 > 2% **且** 当日收盘 > 前日开盘 |
| 3 | 大阳反转 | 阴线后出现强势反转阳线 | 前日收阴 **且** 当日涨幅 > 3% |
| 4 (最低) | 烂板次日 | 涨停板未封住次日依然强势 | 前日涨幅 > 8% **且** 前日收盘 < 前日最高×0.97 **且** 当日涨 > 2% |

**关于"烂板次日"**: 此信号在回测中极少触发（0 笔），原因：
- 条件极为苛刻：前日涨幅 > 8% 且收盘从最高点回落 > 3%（涨停板打开）
- 次日还要涨 > 2%
- 同时满足这三个条件的组合在全市场 4593 只股票、一年数据中几乎不出现
- 此外，如果同一日也满足更高级别的信号（如异动不跌、阳包阴），会被更高级别信号"截胡"

### 评分体系 (满分 ~80 分)

| 维度 | 权重 | 条件 |
|---|---|---|
| 一波涨幅 | 10-20分 | >30% 加20分, >20% 加10分 |
| 回调深度 | 10-15分 | <8% 加15分, <15% 加10分 |
| 当日涨幅 | 5-15分 | >7% 加15分, >5% 加10分, >3% 加5分 |
| 量比 | 5-10分 | >2x 加10分, >1.5x 加5分 |
| 均线排列 | 5-10分 | 多头排列(MA5>MA10>MA20) 加10分 |
| 信号类型 | 10分 | 异动不跌 额外加10分 |
| 基础分 | 5分 | 所有信号 |

### 风险管理

- **止损位**: 回调阶段所有 K 线的 `low` 最小值 × 0.98（2% 安全边际）
  - 用 `low`（盘中最低价）而非 `close`（收盘价）
  - 盘中最低价触及止损价即触发，出场价 = 止损价
- **移动止盈**: 持仓>2天 且 从最高价回撤>8% 且 累计盈利>10% 时触发
- **最大持仓**: 20 个交易日强制平仓
- **板块联动**: 检查所属板块动量，强势板块信号权重更高

## 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      策略应用层                                  │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────┐  │
│  │ daily_run.sh │  │daily_scanner.py│  │ generate_scorecard   │  │
│  │ 每日自动化   │→ │ 每日选股       │  │ report.py            │  │
│  │ + 自适应入口 │  │ + 评分明细     │  │ 跟踪报告生成         │  │
│  │ --monitor    │  │ + 自动跟踪     │  │ + 颜色输出           │  │
│  │ --weekly-opt │  │ + 行业分类     │  │                      │  │
│  └──────────────┘  └───────┬───────┘  └──────────┬───────────┘  │
│                            │                     │              │
│  ┌─────────────────────────┴─────────────────────┤             │
│  │ pick_tracker.py                               │             │
│  │ 选股跟踪模型: 记录/退出模拟/成绩单/评分明细   │             │
│  │ + score_* 字段 + 中文/英文表头兼容            │             │
│  └─────────────────────────┬─────────────────────┘             │
│                            │                                    │
└────────────────────────────┼────────────────────────────────────┘
                             │
┌────────────────────────────┼────────────────────────────────────┐
│                            ▼                                    │
│              自适应引擎层 (adaptive_engine.py)                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  AdaptiveEngine                                           │   │
│  │  ┌─────────────────┐  ┌─────────────────┐               │   │
│  │  │ TradingDayResolv│  │daily_monitor.py │               │   │
│  │  │ er.py           │  │每日监控         │               │   │
│  │  │ 交易日统一解析   │  │异常预警         │               │   │
│  │  │ effective_date  │  │环境感知         │               │   │
│  │  │ monitor_period_ │  │Wilson置信界     │               │   │
│  │  │ key             │  └──────┬─────────┘               │   │
│  │  └────────┬────────┘         │                          │   │
│  │           │                  │                          │   │
│  │  ┌────────┴──────────────────┴───────────────────────┐ │   │
│  │  │weekly_optimizer.py                                  │ │   │
│  │  │每周四层优化: 参数/评分/信号/环境层                   │ │   │
│  │  │参数/评分/信号/环境层调度 + OOS验证                   │ │   │
│  │  └────────────────────────────────────────────────────┘ │   │
│  │                        │                                 │   │
│  │  ┌─────────────────────┴────────────────────────────┐   │   │
│  │  │ sandbox_validator.py                              │   │   │
│  │  │ 沙盒验证: 滚动窗口(3周) + 防重复应用              │   │   │
│  │  │ pending → passed → applied → rollback            │   │   │
│  │  └──────────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                     │
│  ┌─────────────────────────┴────────────────────────────────┐   │
│  │  strategy_optimizer.py (参数优化底层)                     │   │
│  │  坐标下降 + Walk-Forward 验证                            │   │
│  └─────────────────────────┬────────────────────────────────┘   │
│                            │                                     │
│  ┌─────────────────────────┴──────────────┐                    │
│  │  strategy_config.py                     │                    │
│  │  参数中心: DEFAULTS + DYNAMIC_PARAMS    │                    │
│  │  (entry/exit/scoring/score_weight/env)  │                    │
│  └─────────────────────────┬──────────────┘                    │
│                            │                                    │
│  ┌─────────────────────────┴──────────────┐                    │
│  │ backtest_weak_to_strong.py             │                    │
│  │ 历史回测系统                            │                    │
│  └─────────────────────────┬──────────────┘                    │
│                            │                                    │
└────────────────────────────┼────────────────────────────────────┘
                             │
┌────────────────────────────┼────────────────────────────────────┐
│                            ▼                                    │
│                       数据层 (data_layer.py)                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  StockDataLayer                                           │   │
│  │  ┌─────────────┐  ┌──────────────────────┐               │   │
│  │  │ get_kline() │  │ get_kline_batch()    │               │   │
│  │  │ 单只查询     │  │ 批量查询 (IN clause) │               │   │
│  │  └──────┬──────┘  └──────────┬───────────┘               │   │
│  │         └─────────┬─────────┘                            │   │
│  │                   ▼                                      │   │
│  │  ┌──────────────────────────────────────────┐           │   │
│  │  │   update_incremental()                    │           │   │
│  │  │   增量更新: 只拉最后日期之后的新数据       │           │   │
│  │  │   + 数据完整性检查 + 行业分类             │           │   │
│  │  └──────────────────────────────────────────┘           │   │
│  └─────────────────────────┬────────────────────────────────┘   │
│                            │                                     │
│  ┌─────────────────────────┴────────────────────────────────┐   │
│  │  data_source.py (多数据源备份)                            │   │
│  │  baostock: 主数据源（历史K线完整，更新较慢）              │   │
│  │  tencent:  实时行情源（盘中实时补充当日数据）             │   │
│  └─────────────────────────┬────────────────────────────────┘   │
│                            │                                     │
│  ┌─────────────────────────┴────────────────────────────────┐   │
│  │  process_lock.py (进程锁)                                 │   │
│  │  文件锁机制，防止多实例同时运行关键任务                   │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┼─────────────────────────────────────┘
                             │
┌────────────────────────────┼─────────────────────────────────────┐
│                            ▼                                     │
│                   SQLite 本地缓存 (WAL模式)                       │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  stock_meta        │ 代码, 名称, 行业, 上市/退市日期        │ │
│  │  stock_daily       │ OHLCV, MA, 涨跌幅, 振幅                  │ │
│  │  update_log        │ 每只股票最后更新日期/行数               │ │
│  │  index_daily       │ 五大指数OHLCV                           │ │
│  │  trading_day_cache │ 交易日缓存（判断非交易日）             │ │
│  │  update_session    │ 断点续传会话状态                        │ │
│  │  strategy_config   │ 策略参数 (含动态参数DYNAMIC_PARAMS)    │ │
│  │  pick_tracking     │ 每日选股记录 + 后续表现 + score_*字段  │ │
│  │  scorecard         │ 成绩单指标汇总                           │ │
│  │  signal_status     │ 信号状态 (期望值+Wilson置信界)         │ │
│  │  optimization_hist │ 参数变更历史 + 沙盒验证状态            │ │
│  │  market_regime     │ 市场环境状态记录                        │ │
│  │  daily_monitor_log │ 每日监控日志                            │ │
│  │  param_snapshot    │ 参数快照 (变更前状态保存)              │ │
│  │  sandbox_config    │ 沙盒参数隔离 (pending变更暂存)         │ │
│  │  critical_process_ │ critical处理状态 (中断恢复机制)        │ │
│  │  state             │                                       │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  baostock API (增量源)                                      │ │
│  │  仅当本地数据不足时触发增量拉取                               │ │
│  └─────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────┘
```

## 文件说明

### 核心文件

| 文件 | 说明 |
|---|---|
| `daily_run.sh` | **每日自动化入口**: 支持 `--date` 参数指定扫描日期，日志输出带时间戳、运行耗时统计、颜色输出。新增 `--monitor`、`--weekly-optimize`、`--adaptive`、`--status`、`--change-status`、`--change-history`、`--batch-trace` 选项 |
| `data_layer.py` | **数据层**: SQLite缓存管理，增量更新，批量查询，断点续传，数据完整性检查，行业分类。新增 `signal_status`、`optimization_history`、`market_regime`、`daily_monitor_log` 表 |
| `data_source.py` | **多数据源支持**: baostock主数据源 + 腾讯实时行情备份，盘中数据补充 |
| `process_lock.py` | **进程锁**: 文件锁机制，防止多实例同时运行关键任务 |
| `daily_scanner.py` | **每日选股**: 增量更新 + 本地扫描，输出评分明细（score_* 字段），自动记录选股用于跟踪 |
| `strategy_config.py` | **参数中心**: 所有策略参数集中管理，DB存储/读取。新增 `DYNAMIC_PARAMS` 支持动态参数（score_weight + environment 类） |
| `pick_tracker.py` | **选股跟踪**: 记录每日选股、模拟后续表现。新增 `score_*` 字段记录评分明细 |
| `generate_scorecard_report.py` | **报告生成**: 从跟踪数据生成 markdown 报告，含胜率、信号效果、评分预测力、操作建议 |
| `strategy_optimizer.py` | **参数优化**: 坐标下降法 + Walk-Forward 验证，寻找最优参数组合 |
| `backtest_weak_to_strong.py` | **回测系统**: 历史数据回测，输出 `backtest_results.csv` |
| `analyze_results.py` | **结果分析**: 回测结果统计报告生成 |
| `init_db.py` | **首次初始化**: 一次性全量拉取全市场历史数据 |
| `signal_constants.py` | **信号常量**: 信号类型英文主键与中文显示映射，状态层级定义 |
| `daily_monitor.py` | **每日监控**: 检测异常预警、环境感知（Wilson 置信界期望值） |
| `weekly_optimizer.py` | **每周优化**: 四层优化调度（参数/评分/信号/环境层） |
| `sandbox_validator.py` | **沙盒验证**: 滚动窗口验证（3周），防止一次性调整风险。新增 `emergency_apply_changes` 紧急应用方法 |
| `adaptive_engine.py` | **自适应引擎**: 核心控制器，调度每日监控 + 每周优化。critical 预警走沙盒流程 |
| `trading_day_resolver.py` | **交易日解析器**: 统一处理交易日判断，提供 effective_date、monitor_period_key、中断恢复机制 |
| `change_manager.py` | **变更管理**: 快照管理、参数隔离、批量回滚、主动回滚监控（30天窗口） |
| `normalizer.py` | **评分归一化**: 基于历史样本全局统计归一化评分，确保权重调整后总分均值稳定 |

### 数据文件 (不纳入版本管理)

| 文件 | 说明 |
|---|---|
| `stock_data.db` | SQLite 数据库 (~140MB，包含全市场K线 + 策略参数 + 选股跟踪 + 行业分类 + 信号状态 + 优化历史 + 环境记录) |
| `YYYYMMDD_today_signals.xlsx` | 指定日期选股结果（如 `20260424_today_signals.xlsx`），含行业分类 |
| `today_signals.csv` | 当日选股结果（旧格式，逐步淘汰） |
| `backtest_results.csv` | 回测交易明细 |
| `tracking_report.md` | 选股跟踪报告 (自动生成，含颜色输出) |
| `daily_run.log` | 每日运行日志（带时间戳，无颜色码） |
| `optimization_summary.json` | 参数优化结果摘要 (自动生成) |
| `optimization_results.csv` | Walk-Forward 窗口结果 (自动生成) |

## 安装与使用

### 环境要求

```bash
pip install baostock pandas numpy openpyxl requests
```

### 首次使用

```bash
# 1. 初始化全市场历史数据 (约 90-100 分钟，仅需一次)
python init_db.py

# 2. 每日选股 (增量更新 ~1分钟 + 扫描 ~7分钟)
python daily_scanner.py

# 3. 回测 
python backtest_weak_to_strong.py
```

### 日常使用流程

**推荐方式**：每天收盘后一键执行，自动完成选股 → 跟踪更新 → 生成报告：

```bash
./daily_run.sh   # 完整流程（扫描当天）
```

**指定日期扫描**（支持回溯历史日期）：

```bash
./daily_run.sh --date 2026-04-20 --scan   # 扫描指定日期
./daily_run.sh --date 2026-04-20          # 指定日期完整流程
```

**分步执行**：

```bash
# 基础流程
./daily_run.sh --scan          # 仅扫描选股（默认当天）
./daily_run.sh --track         # 仅更新已有选股的跟踪状态
./daily_run.sh --report        # 仅生成跟踪报告
./daily_run.sh --scorecard     # 跟踪更新 + 成绩单

# 参数优化
./daily_run.sh --optimize      # 参数优化（坐标下降法）
./daily_run.sh --walkforward   # Walk-Forward 验证（滚动训练/测试）

# 自适应系统（新增）
./daily_run.sh --monitor           # 每日监控（异常预警 + 环境感知）
./daily_run.sh --weekly-optimize   # 每周四层优化（参数/评分/信号/环境）
./daily_run.sh --adaptive          # 一键执行：监控 + 优化 + 状态查看
./daily_run.sh --status            # 查看沙盒验证状态

# 变更管理
./daily_run.sh --change-status     # 变更管理状态摘要
./daily_run.sh --change-history    # 变更历史查询（默认90天）
./daily_run.sh --batch-trace <ID>  # 批次变更追溯
./daily_run.sh --rollback-monitor  # 执行主动回滚监控
```

**单独调用 Python 模块**：

```bash
# 选股跟踪（不扫描）
python pick_tracker.py --action both --lookback 90

# 生成跟踪报告
python generate_scorecard_report.py --lookback 90 --output tracking_report.md

# 参数优化
python strategy_optimizer.py --mode coordinate --rounds 3 --sample 200
python strategy_optimizer.py --mode walkforward --train-window 180 --test-window 60
python strategy_optimizer.py --mode grid   # 网格搜索（少量参数）
```

**Cron 自动化**（建议每个交易日 15:30 自动运行）：

```
30 15 * * 1-5 /path/to/daily_run.sh >> /path/to/daily_run.log 2>&1
```

**输出格式说明**：

- **选股结果**: `YYYYMMDD_today_signals.xlsx`（Excel格式，含行业分类）
- **终端输出**: 带ANSI颜色码，涨幅/盈利显示红色（正）、绿色（负）
- **日志文件**: `daily_run.log`，不含颜色码，便于查看和归档
- **运行耗时**: 结束时自动打印运行耗时（如"运行耗时: 0小时15分钟32秒"）

**参数优化建议**：每周或每月运行一次 Walk-Forward 验证，检查策略是否仍有效：

```bash
# 查看优化结果
cat optimization_summary.json
cat optimization_results.csv
```

### 性能指标

| 操作 | v1 (纯API) | v2 (缓存+增量) | 提升 |
|---|---|---|---|
| 首次初始化 | N/A | ~100分钟 (一次性) | - |
| 每日增量更新 | ~97分钟 | **~1分钟** | **97x** |
| 全市场扫描 | ~97分钟 | **~6分钟** | **16x** |
| 回测 (200只) | ~10分钟 | **~1分钟** | **10x** |
| 回测 (全市场4593只) | - | **~15分钟** | - |

> v2 扫描优化：批量SQL查询 + 提前过滤ST股票，从原来~10分钟降至~6分钟。

## 回测表现

基于全市场 4593 只A股全部回测（非抽样），回测区间 2025-04-22 至 2026-04-22：

| 指标 | 值 |
|---|---|
| 总交易次数 | 6,369 |
| 盈利次数 / 亏损次数 | 3,811 / 2,558 |
| 胜率 | 59.8% |
| 盈亏比 | 1.47 |
| 平均盈利 | +12.94% |
| 平均亏损 | -8.82% |
| 每笔期望值 | +4.20% |
| 最大单笔盈利 | +114.46% |
| 最大单笔亏损 | -38.53% |
| 平均持仓天数 | 9.7 天 |
| 最大连续亏损 | 12 次 |
| 夏普比率 | 4.11 |

### 按信号类型

| 信号 | 交易笔数 | 占比 | 胜率 | 平均收益 |
|---|---|---|---|---|
| 异动不跌 | 4,265 | 67.0% | 61.0% | +4.26% |
| 阳包阴 | 2,043 | 32.1% | 57.4% | +4.01% |
| 大阳反转 | 61 | 1.0% | 60.7% | +6.21% |

### 板块联动效果

| 板块状态 | 交易笔数 | 胜率 | 平均收益 |
|---|---|---|---|
| 弱势板块 | 2,962 | 61.8% | +4.98% |
| 强势板块 | 3,407 | 58.1% | +3.52% |

> 全市场回测显示弱势板块信号反而胜率更高，可能因为弱势板块中个股更容易走出独立行情。

### 市场环境效果

> 每笔交易根据信号日对应指数的均线关系判断市场环境：
> - 上升期: 收盘价 > MA20 且 MA5 > MA10
> - 震荡期: 其他情况
> - 退潮期: 收盘价 < MA20 且 MA5 < MA20

| 市场环境 | 交易笔数 | 胜率 | 平均收益 |
|---|---|---|---|
| 上升期 | 2,681 | **62.3%** | **+5.01%** |
| 震荡期 | 1,547 | 59.2% | +3.91% |
| 退潮期 | 2,141 | 57.3% | +3.39% |

> **弱转强模式不适用于退潮期**。回测证实该论断：退潮期胜率最低（57.3%），平均收益也最差（+3.39%）。实盘中应结合对应大盘指数（主板→沪深300、创业板→创业板指、科创板→科创50）判断，退潮期建议休息。

### 持仓天数分析

| 持仓天数 | 笔数 | 胜率 | 平均收益 |
|---|---|---|---|
| 1-3天 | 1,096 | 53.7% | +1.25% |
| 4-5天 | 953 | **71.2%** | +5.51% |
| 6-10天 | 1,813 | **70.9%** | **+7.41%** |
| 11天+ | 2,446 | 51.4% | +2.74% |

### 退出策略详解

系统采用四重退出机制，按**每日盘中逐条判断**的优先级依次执行：

```
每日判断顺序:
  1. 盘中最低价 ≤ 固定止损价 → 立即止损
  2. 持仓 > 2天 且 从峰值回撤 > 8% 且 累计盈利 > 10% → 移动止盈
  3. 持仓 ≥ 20天 → 到期强制平仓
  4. 数据结束 → 最终平仓
```

| 退出类型 | 笔数 | 占比 | 胜率 | 平均收益 |
|---|---|---|---|---|
| 移动止盈 | 3,448 | 54.1% | **86.1%** | **+11.02%** |
| 固定止损 | 1,224 | 19.2% | 5.6% | -10.88% |
| 时间止损 | 884 | 13.9% | 35.1% | -2.49% |
| 最终平仓 | 813 | 12.8% | 56.8% | +5.28% |

#### 1. 固定止损 (Stop Loss) — 优先级最高

> 参数: 止损安全边际 = **2%**

**止损价计算（精确公式）**:
```
# 回调阶段 = 第一波上涨结束后的第二天 到 回调最低点出现的那天
# 回调阶段最低价(mn) = 回调期间所有K线的 low 字段的最小值
# 注意: 用的是 low（盘中最低价），不是 close（收盘价）

止损价 = mn × 0.98
```

**逐步拆解**:
```
步骤1: 找到第一波上涨的最后一根K线，记下它的收盘价 = peak_price
步骤2: 从 peak_price 的第二天开始，逐日记录 low 字段
步骤3: 在回调的 N 天里（N ≤ 15），取所有 low 的最小值 = mn
步骤4: 止损价 = mn × 0.98（留 2% 安全边际）

示例:
  第一波上涨最后一天收盘价 = 100.00 (peak)
  回调第1天 low=98.00, close=97.50
  回调第2天 low=96.00, close=95.80
  回调第3天 low=94.50, close=94.00  ← 最低点
  回调第4天 low=95.00, close=95.50
  回调第5天 low=96.50, close=97.00

  mn = min(98.00, 96.00, 94.50, 95.00, 96.50) = 94.50
  止损价 = 94.50 × 0.98 = 92.61

  买入后某日 low 触及 92.00 → 92.00 ≤ 92.61 → 触发止损
  出场价 = 92.61（不是 92.00，是以止损限价出场）
```

**判断逻辑**（每日盘中检查，优先级最高）:
```
if 当日最低价 <= 止损价:
    出场价 = 止损价
    出场原因 = "stop_loss"
```

**为什么用 low 而不是 close**: 止损的目的是判断"弱转强"结构是否被破坏。
回调最低点的 low 代表了支撑位，如果盘中价格跌破了支撑位再打 2% 的折扣，
说明趋势已经彻底反转，必须离场。

**效果**: 19.2% 的交易由此退出，胜率仅 5.6%，平均亏损 -10.88%。

#### 2. 移动止盈 (Trailing Stop) — 核心盈利来源

> 参数: 最大回撤 = **8%**，最低盈利要求 = **10%**

**变量定义**:
```
peak = max(买入价, 持仓期间所有最高价)    # 跟踪持仓最高价
current_close = 当日收盘价
total_gain = (peak - 买入价) / 买入价     # 从买入到峰值的总盈利
drawdown_from_peak = (peak - current_close) / peak  # 从峰值回撤幅度
```

**判断逻辑**（固定止损不触发时才判断）:
```
if 持仓天数 > 2:                          # 前2天不触发，给足反应时间
    if 回撤幅度 > 8% and 累计盈利 > 10%:  # 两个条件必须同时满足
        出场价 = 当日收盘价
        出场原因 = "trailing_stop"
```

**两个条件缺一不可**:
- `持仓 > 2 天`: 避免买入后正常波动就被误触发
- `从峰值回撤 > 8%`: 趋势已经明显反转
- `累计盈利 > 10%`: 确保已经积累了足够的利润缓冲

**举例**:
```
买入价 = 100.00 元
第1天: 最高 105, 收盘 103 → peak=105, 回撤=(105-103)/105=1.9%, 盈利=5%  → 不触发
第2天: 最高 112, 收盘 110 → peak=112, 回撤=(112-110)/112=1.8%, 盈利=12% → 不触发(天数不够)
第3天: 最高 118, 收盘 115 → peak=118, 回撤=(118-115)/118=2.5%, 盈利=18% → 不触发(回撤不够)
第4天: 最高 120, 收盘 109 → peak=120, 回撤=(120-109)/120=9.2%, 盈利=20% → ✅ 触发!
出场价 = 109.00, 盈利 = +9.0%
```

**效果**: 54.1% 的交易由此退出，胜率 86.1%，平均盈利 +11.02%。

#### 3. 时间止损 (Time Exit / Max Hold) — 到期强制平仓

> 参数: 最大持仓天数 = **20 天**

**判断逻辑**:
```
if 持仓天数 >= 20:
    出场价 = 当日收盘价
    出场原因 = "time_exit"
```

**为什么是 20 天**: 弱转强策略的本质是"短线爆发"。如果买入后 20 个交易日
（约一个月）都没有明显的止盈或止损信号，说明这笔交易处于"横盘僵持"状态。
僵持意味着资金被占用，机会成本高。

**注意**: 这与"最终平仓"不同。时间止损是策略主动设定的持仓上限，
而最终平仓是回测数据窗口耗尽导致的被动平仓。

**效果**: 13.9% 的交易由此退出，平均亏损 -2.49%，亏损幅度很小。

#### 4. 最终平仓 (Final Exit) — 数据窗口耗尽

**触发条件**: 回测数据区间结束（没有更多K线数据），按最后可用收盘价平仓。

**出场逻辑**:
```
if 到达数据最后一条K线:
    出场价 = 最后一条收盘价
    出场原因 = "final"
```

**举例**:
```
回测区间: 2025-04-22 至 2026-04-22
某股票在 2026-04-20 产生买入信号，但 2026-04-22 是回测最后一天
持仓仅 2 天 → 以 2026-04-22 收盘价强制平仓
```

**效果**: 12.8% 的交易由此退出，平均收益 +5.28%。
```

---

### 完整操作流程量化指标汇总

#### 买入条件（全部满足才触发）

| 阶段 | 条件 | 参数值 |
|---|---|---|
| **一波上涨** | 连续上涨天数 ≥ 3 天 | `first_wave_min_days = 3` |
| | 累计涨幅（仅涨日累加）≥ 15% | `first_wave_min_gain = 0.15` |
| **回调整理** | 回调持续天数 ≤ 15 天 | `consolidation_max_days = 15` |
| | 最大回撤幅度 ≤ 20% | `consolidation_max_drawdown = 0.20` |
| | 回调天数 ≥ 3 天 | — |
| | 阴线占比 < 70% | — |
| **反转信号** | 信号日涨幅 > 3%（大阳反转） | `weak_strong_threshold = 0.03` |
| | 信号日涨幅 > 2% + 阳包阴 | — |
| | 前日振幅 > 6% + 次日涨 > 1% | `anomaly_amplitude = 0.06` |
| | 前日涨幅 > 8% 未封板 + 次日涨 > 2% | `limit_up_pct = 0.095` |

#### 评分体系

| 维度 | 加分条件 | 分值 |
|---|---|---|
| 一波涨幅 | > 30% | +20 |
| | > 20% | +10 |
| 回调深度 | < 8% | +15 |
| | < 15% | +10 |
| 当日涨幅 | > 7% | +15 |
| | > 5% | +10 |
| | > 3% | +5 |
| 量比 | > 2.0x | +10 |
| | > 1.5x | +5 |
| 均线排列 | MA5 > MA10 > MA20 | +10 |
| | MA5 > MA10 | +5 |
| 信号类型 | 异动不跌 | +10 |
| 板块动量 | 强势板块（10日均涨幅 > 0.5%） | +5 |
| 基础分 | — | +5 |

#### 出场规则

| 规则 | 触发条件 | 出场价 | 优先级 |
|---|---|---|---|
| 固定止损 | 当日最低价 ≤ 回调最低价 × 0.98 | 止损价 | 1（最高） |
| 移动止盈 | 持仓>2天 且 回撤>8% 且 盈利>10% | 当日收盘价 | 2 |
| 到期平仓 | 持仓 ≥ 20 天 | 当日收盘价 | 3 |
| 数据耗尽 | 无更多K线数据 | 最后收盘价 | 4（最低） |

## 自适应系统

> 系统支持四层自适应优化，通过 Wilson 置信界保守估计和小样本保护机制确保稳定性。

### 每日监控

自动检测异常预警，环境感知，不做主动调整：

```bash
./daily_run.sh --monitor
```

**监控指标：**

| 指标 | 触发条件 | 严重程度 |
|------|----------|----------|
| 信号期望值下降 | Wilson 下界期望值 < 0 持续 3 天 | warning |
| 市场环境退潮 | 退潮期持续 > 5 天 | info |
| 评分预测力下降 | 评分与期望值相关系数 < 0.1（原 > 0.3） | warning |
| 新信号样本不足 | 某信号实盘退出样本 < 10 笔 | info |
| 沙盒验证失败 | 新参数沙盒期望值低于基准 | critical |

### 每周优化

周末运行四层优化，结果先入沙盒验证：

```bash
./daily_run.sh --weekly-optimize
```

**四层优化顺序：**

```
Step 0: 环境状态更新
  ├── 更新 market_regime 表当前状态
  └── 调整全局活跃度系数

Step 1: 参数层优化（OOS验证）
  ├── 训练集：过去120天数据
  ├── 验证集：最近30天数据（Out-of-Sample）
  └── OOS验证通过 → 写入 sandbox 待验证

Step 2: 评分层调整（期望值驱动）
  ├── 计算各评分维度与期望值的 Spearman 相关性
  ├── 相关性 > 0.3 → 权重 ↑（上限 +20%）
  ├── 相关性 < -0.2 → 权重 ↓（下限 -20%）
  └── 写入 sandbox 待验证

Step 3: 信号层状态检查
  ├── 计算各信号 Wilson 置信下界期望值
  └── 应用渐进降级机制

Step 4: 冷启动检查
  └── 某信号退出样本 < 10 笔 → 保持 active 状态
```

### 沙盒验证

所有变更先入 sandbox 验证，连续 3 周通过才正式应用：

```bash
./daily_run.sh --status    # 查看沙盒状态
```

**验证机制：**

| 参数 | 值 | 说明 |
|------|------|------|
| validation_window_weeks | 3 | 滚动窗口验证周期 |
| min_validation_trades | 10 | 最小验证交易数 |
| pass_expectancy_threshold | 0.5% | 期望值阈值（通过） |
| fail_expectancy_threshold | -2% | 期望值阈值（失败） |

**状态流转：**

```
pending → passed → applied
   ↓
 failed → rollback
```

**防重复执行机制：**

周四多次执行 `--weekly-optimize` 时，系统通过两层检查避免重复验证：

| 检查 | 条件 | 结果 |
|------|------|------|
| `_check_optimization_already_run` | 当天有非 pending 记录 | 返回 `already_run_today` |
| `_check_has_today_pending` | 当天有 pending 记录 | 返回 `pending_validation_in_progress` |

防止问题：阈值边界附近的 pending 记录因多次 `_make_decision()` 调用导致判定结果翻转（第一次 passed → 第二次 failed）。

### 一键执行

```bash
./daily_run.sh --adaptive   # 每日监控 + 每周优化 + 状态查看
```

### 自适应模块文件

| 文件 | 说明 |
|------|------|
| `signal_constants.py` | 信号类型常量（英文主键 ↔ 中文显示） |
| `daily_monitor.py` | 每日监控 + 环境感知 |
| `weekly_optimizer.py` | 每周四层优化调度 |
| `sandbox_validator.py` | 沙盒滚动窗口验证 |
| `adaptive_engine.py` | 自适应引擎核心控制器 |

### 自适应数据表

| 表名 | 说明 |
|------|------|
| `signal_status` | 信号状态管理（期望值 + Wilson 置信界） |
| `optimization_history` | 参数变更历史 + 沙盒验证状态 |
| `market_regime` | 市场环境状态记录 |
| `daily_monitor_log` | 每日监控日志 |

### 关键设计

**Wilson 置信下界期望值：**

小样本保守估计，避免过早禁用信号：

| 胜率 | 平均盈利 | 平均亏损 | 样本量 | Wilson 下界期望值 |
|------|----------|----------|--------|-------------------|
| 60% | 10% | 5% | 5 笔 | 0.8%（保守） |
| 60% | 10% | 5% | 50 笔 | 3.5%（接近真实） |

**渐进降级机制：**

```
active(权重1.0) → watching(权重0.5) → warning(权重0.2) → disabled(权重0)

降级条件：
  样本 < 10 笔 → 保持 active（冷启动保护）
  Wilson 下界 < 0 且样本 ≥ 10 笔 → watching
  Wilson 下界 < 0 持续 2 周且样本 ≥ 30 笔 → warning
  Wilson 下界 < 0 持续 3 周且样本 ≥ 30 笔 → disabled（需人工确认）
```

**环境感知响应：**

| 环境 | 活跃度系数 | 影响 |
|------|-----------|------|
| 上升期 | 1.0 | 正常选股，正常评分阈值 |
| 震荡期 | 0.7 | 评分阈值提高 15% |
| 退潮期 | 0.3 | 评分阈值提高 30%，仅保留高置信信号 |

---

## 变更管理系统

> 参数变更的生命周期管理：变更前快照 → 沙盒隔离验证 → 批量回滚 → 主动监控

### 四层设计

| 层级 | 功能 | 说明 |
|------|------|------|
| D. 快照管理 | 变更前保存完整状态 | params、signal_status、environment 全量保存 |
| C. 参数隔离 | pending变更暂存到sandbox_config | 不影响实盘选股，验证后才写入strategy_config |
| B. 批量回滚 | 同批次变更整体回滚 | 恢复快照，保持一致性 |
| A. 主动监控 | 变更生效后30天持续监控 | 恶化超过阈值自动触发回滚 |

### 状态流转

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
```

### 主动回滚阈值

| 参数 | 值 | 说明 |
|------|------|------|
| monitor_days | 30 | 监控周期（变更生效后30天） |
| expectancy_drop_threshold | 30% | 期望值下降超过30%触发回滚 |
| win_rate_drop_threshold | 10% | 胜率下降超过10%触发回滚 |
| consecutive_bad_days | 5 | 连续5个交易日期望值<0触发回滚 |
| min_samples_for_check | 10 | 检查表现的最小样本数 |

### 变更管理数据表

| 表名 | 说明 |
|------|------|
| param_snapshot | 参数快照表（变更前状态保存） |
| sandbox_config | 沙盒参数隔离表（pending变更暂存） |
| optimization_history | 参数变更历史（扩展batch_id、rollback字段） |

### CLI 命令

```bash
# 查看变更管理状态
./daily_run.sh --change-status

# 查询变更历史
./daily_run.sh --change-history

# 追溯批次变更
./daily_run.sh --batch-trace 20260426-001

# 或直接调用 Python
python change_manager.py --mode status
python change_manager.py --mode history --days 30
python change_manager.py --mode trace --batch-id 20260426-001
python change_manager.py --mode monitor  # 执行主动回滚监控
```

---

## 跟踪验证系统

> 选股之后如何验证？系统通过 `pick_tracker` 自动记录每日选股，模拟后续表现，生成可量化的验证报告。

### 工作流程

```
每日扫描 → 选股结果 → 写入 pick_tracking 表
                          ↓
                    次日运行 daily_run.sh 时
                          ↓
              自动检查每只已选股票的后续 K 线
                          ↓
        应用退出规则（止损/止盈/时间退出），更新状态
                          ↓
              生成 scorecard + tracking_report.md
```

### 退出规则（与回测一致）

| 优先级 | 规则 | 触发条件 |
|---|---|---|
| 1 | 固定止损 | 盘中最低价 ≤ 入场价 × (1 - stop_loss_buffer) |
| 2 | 移动止盈 | 持仓>2天 且 从峰值回撤>trailing_stop_pct 且 累计盈利>trailing_min_gain |
| 3 | 时间退出 | 持仓 ≥ max_hold_days |
| 4 | 数据结束 | 无更多K线数据，保持 active 状态 |

### 成绩单内容 (`tracking_report.md`)

| 章节 | 内容 |
|---|---|
| 总体表现 | 选股数、已退出数、胜率、平均盈亏、持仓天数 |
| 信号效果 | 各信号类型的胜率对比（异动不跌 vs 阳包阴 vs 大阳反转） |
| 市场环境 | 上升期/震荡期/退潮期下的选股表现 |
| 评分预测力 | Spearman 相关系数，验证评分高低是否真的对应盈亏 |
| 个股排行 | Top 5 盈利 / Top 5 亏损 |
| 趋势对比 | 近90天 vs 前90天的胜率变化 |
| 操作建议 | 基于数据自动生成的建议（如降低某信号权重、回避某环境等） |

### 查看成绩单

```bash
# 一键生成
./daily_run.sh --report

# 或直接查看
cat tracking_report.md
```

## 参数优化系统

> 策略参数是否合理？`strategy_optimizer` 通过系统搜索最优组合，避免凭感觉调参。

### 优化方法

| 方法 | 适用场景 | 速度 |
|---|---|---|
| 坐标下降法 (`--mode coordinate`) | 快速找到好参数 | 快（线性复杂度） |
| Walk-Forward (`--mode walkforward`) | 验证稳健性，防过拟合 | 慢（多窗口） |
| 网格搜索 (`--mode grid`) | 小范围精细搜索 | 中等（指数增长） |

### Walk-Forward 验证

```
窗口1: [训练: 180天] → 优化 → [测试: 60天] → 记录 OOS 表现
窗口2: [训练: 180天(滑动30天)] → 优化 → [测试: 60天] → 记录 OOS 表现
窗口3: ...
```

所有窗口的 **Out-of-Sample** 结果汇总，得到推荐的参数组合。

### 可优化的参数

| 参数 | 搜索范围 | 含义 |
|---|---|---|
| first_wave_min_days | 2-5 | 一波上涨最少连续天数 |
| first_wave_min_gain | 0.10-0.25 | 一波上涨最低累计涨幅 |
| consolidation_max_days | 8-20 | 回调最长持续天数 |
| consolidation_max_drawdown | 0.10-0.30 | 回调最大允许回撤 |
| anomaly_amplitude | 0.04-0.08 | 异动不跌所需最小振幅 |
| stop_loss_buffer | 0.01-0.05 | 止损安全边际 |
| trailing_stop_pct | 0.05-0.12 | 移动止盈回撤阈值 |
| trailing_min_gain | 0.05-0.15 | 移动止盈最低盈利要求 |
| max_hold_days | 10-30 | 最大持仓天数 |

### 目标函数

```
score = 0.35 × expectancy + 0.25 × win_rate + 0.20 × (1 - max_dd) + 0.10 × sharpe + 0.10 × trade_count_penalty
```

多目标加权，避免单一指标过拟合。

## 策略参数中心

所有策略参数集中存储在 `strategy_config` 表中，替代原来散落在各文件的硬编码 CONFIG。

| 特性 | 说明 |
|---|---|
| 统一存储 | 25 个参数，分 entry / exit / scoring 三类 |
| 默认回退 | 表中无记录时使用内置 DEFAULTS |
| 可动态修改 | `strategy_config.py` 提供 set/set_batch 接口 |
| 快照导出 | `export_snapshot()` 导出 JSON 便于复现 |

## 完整参数说明

### 命令行选项

| 选项 | 说明 | 示例 |
|------|------|------|
| `--date YYYY-MM-DD` | 指定扫描日期 | `./daily_run.sh --date 2026-04-20 --scan` |
| `--scan` | 仅扫描选股 | `./daily_run.sh --scan` |
| `--track` | 仅更新跟踪状态 | `./daily_run.sh --track` |
| `--report` | 仅生成跟踪报告 | `./daily_run.sh --report` |
| `--scorecard` | 跟踪更新 + 成绩单 | `./daily_run.sh --scorecard` |
| `--optimize` | 参数优化（坐标下降法） | `./daily_run.sh --optimize` |
| `--walkforward` | Walk-Forward 验证 | `./daily_run.sh --walkforward` |
| `--monitor` | 每日监控（异常预警） | `./daily_run.sh --monitor` |
| `--weekly-optimize` | 每周四层优化 | `./daily_run.sh --weekly-optimize` |
| `--adaptive` | 一键执行（监控+优化+状态） | `./daily_run.sh --adaptive` |
| `--status` | 查看沙盒验证状态 | `./daily_run.sh --status` |
| `--change-status` | 变更管理状态摘要 | `./daily_run.sh --change-status` |
| `--change-history` | 变更历史查询 | `./daily_run.sh --change-history` |
| `--batch-trace` | 批次变更追溯 | `./daily_run.sh --batch-trace <ID>` |
| `--rollback-monitor` | 执行主动回滚监控 | `./daily_run.sh --rollback-monitor` |

### 入场参数（category: entry）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `first_wave_min_days` | 3 | 一波上涨最少连续天数 |
| `first_wave_min_gain` | 0.15 | 一波上涨最低累计涨幅（15%） |
| `consolidation_max_days` | 15 | 回调最长持续天数 |
| `consolidation_max_drawdown` | 0.20 | 回调最大允许回撤（20%） |
| `weak_strong_threshold` | 0.03 | 大阳反转所需最小涨幅（3%） |
| `anomaly_amplitude` | 0.06 | 异动不跌所需最小振幅（6%） |
| `sector_momentum_window` | 10 | 板块动量回看窗口（天） |
| `sector_min_strength` | 0.05 | 板块强势判断阈值（5%） |

### 出场参数（category: exit）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `stop_loss_buffer` | 0.02 | 止损安全边际（回调最低点再降2%） |
| `trailing_stop_pct` | 0.08 | 移动止盈回撤阈值（8%） |
| `trailing_min_gain` | 0.10 | 移动止盈最低盈利要求（10%） |
| `max_hold_days` | 20 | 最大持仓天数 |

### 评分参数（category: scoring）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `score_base` | 5 | 基础分 |
| `score_wave_high` | 20 | 一波涨幅 > 30% 加分 |
| `score_wave_med` | 10 | 一波涨幅 > 20% 加分 |
| `score_shallow_dd` | 15 | 回调深度 < 8% 加分 |
| `score_med_dd` | 10 | 回调深度 < 15% 加分 |
| `score_strong_gain` | 15 | 当日涨幅 > 7% 加分 |
| `score_med_gain` | 10 | 当日涨幅 > 5% 加分 |
| `score_weak_gain` | 5 | 当日涨幅 > 3% 加分 |
| `score_high_vol` | 10 | 量比 > 2x 加分 |
| `score_med_vol` | 5 | 量比 > 1.5x 加分 |
| `score_full_bull` | 10 | MA5 > MA10 > MA20 加分 |
| `score_partial_bull` | 5 | MA5 > MA10 加分 |
| `score_anomaly_bonus` | 10 | 异动不跌额外加分 |
| `score_sector_strong` | 5 | 强势板块加分 |

### 评分权重参数（category: score_weight）

> 自适应系统动态调整，调整幅度限制在 ±20%

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `weight_wave_gain` | 1.0 | 波段涨幅评分权重系数 |
| `weight_shallow_dd` | 1.0 | 浅回调评分权重系数 |
| `weight_strong_gain` | 1.0 | 强势涨幅评分权重系数 |
| `weight_volume` | 1.0 | 放量评分权重系数 |
| `weight_ma_bull` | 1.0 | 多头排列评分权重系数 |
| `weight_anomaly` | 1.0 | 异动信号额外权重 |
| `weight_sector` | 1.0 | 板块动量权重 |
| `weight_signal_bonus` | 1.0 | 信号类型加分权重 |

### 环境参数（category: environment）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `activity_coefficient` | 1.0 | 当前环境活跃度系数 |
| `bull_threshold` | 1.0 | 上升期活跃度 |
| `range_threshold` | 0.7 | 震荡期活跃度 |
| `bear_threshold` | 0.3 | 退潮期活跃度 |

### 沙盒验证配置

| 参数 | 值 | 说明 |
|------|------|------|
| `validation_window_weeks` | 3 | 滚动窗口验证周期（周） |
| `min_validation_trades` | 10 | 最小验证交易数 |
| `pass_expectancy_threshold` | 0.005 | 通过期望值阈值（0.5%） |
| `pass_win_rate_threshold` | 50 | 通过胜率阈值 |
| `fail_expectancy_threshold` | -0.02 | 失败期望值阈值（-2%） |
| `fail_win_rate_threshold` | 40 | 失败胜率阈值 |
| `improvement_threshold` | 0.002 | 相比基准提升阈值 |
| `max_pending_days` | 21 | pending 状态最大天数 |

### Critical 预警配置

| 参数 | 值 | 说明 |
|------|------|------|
| `auto_disable_threshold` | -0.05 | 自动禁用信号的期望值阈值 |
| `min_sample_for_critical` | 20 | Critical 判断最小样本数 |
| `notification_methods` | ['log', 'print'] | 通知方式 |

### 参数查看与修改

```bash
# 查看 Python 模块参数
python strategy_config.py         # 显示当前参数
python strategy_config.py --help  # 显示帮助

# 查看自适应系统状态
./daily_run.sh --status           # 查看沙盒验证状态

# 手动修改参数（需通过数据库）
# 参数存储在 stock_data.db 的 strategy_config 表中
# 可通过 sqlite3 命令行或 Python API 修改
```

---

## 设计要点

### 数据层

- **WAL 模式**: SQLite Write-Ahead Logging，支持并发读写
- **增量更新**: 记录每只股票最后更新日期，只拉新数据
- **批量查询**: `get_kline_batch()` 使用一条 SQL IN 查询替代逐只查询
- **断点续传**: 更新中断后自动从上次位置继续，避免重复拉取
- **API容错**: 连续失败20次等待10秒，100次停止更新并提示
- **行业分类**: 从 baostock 获取股票行业数据，存储在 stock_meta 表
- **多数据源备份**: baostock主数据源 + 腾讯实时行情源（盘中补充当日数据）
- **进程锁保护**: `process_lock.py` 提供文件锁，防止 batch_update 等关键任务并发执行

### 数据验证

扫描前自动验证指定日期数据完整性：
1. 检查指定日期是否有足够股票数据（≥100只）
2. 数据不完整时自动尝试增量更新（最多2次）
3. 更新后仍不完整则退出提示，不自动切换日期
4. 检查 pct_chg 和 MA 字段是否为 NULL（异常数据修复）

### 时间判断

A股15:00收盘，数据约15:30后可用：
- 15:30前运行 → 自动扫描上一工作日
- 15:30后运行 → 扫描当天

### 策略层

- **ST 股过滤**: 自动剔除名称含 "ST" 的股票
- **板块动量**: 计算所属板块 10 日平均收益率，强势板块加分
- **多信号融合**: 不依赖单一技术指标，综合形态、量能、均线
- **行业分类**: 选股结果包含行业信息，便于板块分析

### 风控层

- **固定止损**: 入场价跌破回调最低点 2% 以下立即止损
- **移动止盈**: 盈利后锁定利润，回撤 8% 出场
- **时间止损**: 20 个交易日未盈利强制平仓

### 输出层

- **XLSX 格式**: 选股结果输出为 Excel 格式，便于查看和分享
- **颜色输出**: 
  - 终端显示带 ANSI 颜色码（涨幅正数红色、负数绿色）
  - 日志文件不含颜色码，便于归档和查看
- **耗时统计**: 自动打印运行耗时，便于监控性能
- **中文字符宽度**: 表格对齐考虑中文字符显示宽度（East Asian Width）

## 技术栈

- Python 3.8+
- baostock (A股历史数据)
- pandas / numpy (数据处理)
- openpyxl (XLSX文件读写)
- requests (腾讯实时行情API)
- SQLite (本地缓存)

## 目录结构

```
shikong_fufei/
├── daily_run.sh                 # 每日自动化流程入口
├── data_layer.py                # 数据层（SQLite缓存管理）
├── data_source.py               # 多数据源支持（baostock + 腾讯实时行情）
├── process_lock.py              # 进程锁（CLI模式，防止多实例并发）
├── daily_scanner.py             # 每日选股（评分明细字段）
├── strategy_config.py           # 参数中心（DEFAULTS + DYNAMIC_PARAMS）
├── pick_tracker.py              # 选股跟踪模型
├── generate_scorecard_report.py # 跟踪报告生成
├── strategy_optimizer.py        # 参数优化模型（坐标下降 + Walk-Forward）
├── backtest_weak_to_strong.py   # 回测系统
├── analyze_results.py           # 结果分析
├── init_db.py                   # 首次初始化
├── signal_constants.py          # 信号常量（英文主键 ↔ 中文显示）
├── daily_monitor.py             # 每日监控（Wilson置信界期望值 + 环境感知）
├── weekly_optimizer.py          # 每周四层优化调度
├── sandbox_validator.py         # 沙盒验证（滚动窗口3周 + emergency_apply）
├── adaptive_engine.py           # 自适应引擎核心控制器
├── trading_day_resolver.py      # 交易日统一解析器（effective_date + 中断恢复）
├── change_manager.py            # 变更管理（快照/隔离/回滚/监控）
├── normalizer.py                # 评分归一化（权重调整后总分均值稳定）
├── stock_data.db                # SQLite缓存 (gitignore)
├── YYYYMMDD_today_signals.xlsx  # 指定日期选股 (gitignore)
├── tracking_report.md           # 跟踪报告 (自动生成)
├── daily_run.log                # 运行日志 (带时间戳，无颜色码)
├── optimization_summary.json    # 优化结果 (自动生成)
├── optimization_results.csv     # 优化窗口结果 (自动生成)
├── tests/                       # 单元测试目录
│   ├── __init__.py
│   ├── test_adaptive.py         # 自适应系统测试
│   ├── test_change_manager.py   # 变更管理测试
│   ├── test_normalizer.py       # 评分归一化测试
│   └── test_data_completeness.py# 数据完整性测试
├── docs/                        # 文档目录
│   └── superpowers/
│       ├── plans/               # 实现计划
│       └── specs/               # 设计文档
├── .locks/                      # 进程锁目录 (gitignore)
├── .gitignore
└── README.md
```

## 更新日志

### 2026-04-27: TradingDayResolver 实现

引入统一交易日解析模块，解决自适应引擎日期判断分散的问题。

**核心功能：**

| 功能 | 说明 |
|------|------|
| TradingDayInfo | 数据结构：target_date、effective_data_date、status、monitor_period_key |
| TradingDayResolver | 统一交易日判断：缓存查询 → 数据库推断 → 周末兜底 |
| 状态枚举 | data_ready、data_not_updated、non_trading_day、historical |
| 中断恢复 | critical_process_state 表，两阶段状态标记（handling → handled/failed） |

**模块改造：**

| 文件 | 改动 |
|------|------|
| adaptive_engine.py | run_daily/run_weekly 使用 resolver，删除旧的日期判断方法 |
| daily_monitor.py | 参数改为 effective_date，统一数据查询基准 |
| data_layer.py | 新增 critical_process_state 表 |

**解决的问题：**

- 交易日数据未更新时，critical 处理基于旧日期判断
- 非交易日（节假日）简单周末判断可能误判
- 每日多次执行防重复机制基准不一致
- critical 处理中断后无恢复机制

**测试覆盖：**

- 124 个单元测试和集成测试全部通过
- 覆盖所有状态转换、边界情况、中断恢复场景

### 2026-04-26: 系统问题修复 (v3.1)

基于设计文档 `docs/superpowers/plans/2026-04-26-system-fix-plan.md` 完成以下修复：

| 问题ID | 修复内容 | 文件 |
|--------|----------|------|
| P0-1 | 进程锁重构：CLI sleep loop 模式，解决 stdin.read() TOCTOU 问题 | process_lock.py, daily_run.sh |
| P0-2 | 每周优化绕过沙盒：删除 cfg.set() 直接调用，走沙盒流程 | weekly_optimizer.py |
| P0-3 | 信号层直接写库：删除 UPDATE signal_status，走沙盒流程 | weekly_optimizer.py |
| P0-4 | critical 预警路径：新增 emergency_apply_changes 紧急应用方法 | sandbox_validator.py, adaptive_engine.py |
| P1-4 | 目标函数硬截断：smooth_objective 改用二次惩罚 + 下界 -0.5 | strategy_optimizer.py |
| P1-5 | 失败通知机制：handle_error + trap ERR + release_lock guard | daily_run.sh |
| P1-6 | 评分权重梯度消失：adjust_score_weight 添加动量机制 | weekly_optimizer.py |
| P2-7 | Walk-Forward 应用：新增 --auto-apply CLI，调用 emergency_apply | strategy_optimizer.py |
| P2-8 | 坐标下降轮数：OPT_ROUNDS 从 3 增加到 10 | daily_run.sh |
| P2-9 | 采样偏差：get_dynamic_seed 使用日期+时间+PID 动态种子 | strategy_optimizer.py |

**关键改进：**

- 进程锁：修复并发启动时的锁竞争问题，通过检查子进程输出 `LOCK_ACQUIRED` 确认锁真正获取
- 沙盒机制：所有参数变更都通过 `sandbox_config` 暂存，由 `apply_passed_changes` 统一应用
- 紧急应用：`emergency_apply_changes` 用于 critical 预警场景，绕过 3 周验证窗口
- 目标函数：负期望值二次惩罚，优化方向更明确

### 2026-04-25: ChangeManager 实现

- 新增 `change_manager.py`：参数变更生命周期管理
- 快照管理：变更前状态保存到 `param_snapshot`
- 参数隔离：变更暂存到 `sandbox_config`，不直接写入生产参数
- 批量回滚：`rollback_batch()` 支持按批次回滚
- 主动监控：`monitor_and_rollback()` 自动检测需要回滚的批次（30天窗口）

## 免责声明

本系统仅用于量化策略学习和研究，不构成任何投资建议。股市有风险，交易需谨慎。
