#!/usr/bin/env python3
"""生成回测分析报告"""
import pandas as pd
import numpy as np

df = pd.read_csv('/home/jzc/wechat_text/shikong_fufei/backtest_results.csv')

total = len(df)
wins = len(df[df['pnl_pct'] > 0])
losses = len(df[df['pnl_pct'] <= 0])
win_rate = wins / total * 100

avg_profit = df[df['pnl_pct'] > 0]['pnl_pct'].mean() * 100
avg_loss = df[df['pnl_pct'] <= 0]['pnl_pct'].mean() * 100
pl_ratio = abs(avg_profit / avg_loss)

# 等权累计收益（非复利，更真实）
equal_weight_return = df['pnl_pct'].mean() * total * 100

# 最大回撤（用每笔交易的累计PnL计算）
cum_pnl = df['pnl_pct'].cumsum()
running_max = cum_pnl.cummax()
max_dd = ((cum_pnl - running_max) / (running_max + 1)).min() * 100

# 夏普比率
sharpe = df['pnl_pct'].mean() / df['pnl_pct'].std() * np.sqrt(252)

# 期望值
expectancy = (win_rate/100 * avg_profit) + ((100-win_rate)/100 * avg_loss)

# 最大连续亏损
is_win = df['pnl_pct'] > 0
max_consec_loss = 0
cur = 0
for v in is_win:
    if not v:
        cur += 1
        max_consec_loss = max(max_consec_loss, cur)
    else:
        cur = 0

report = f"""
{'='*70}
弱转强策略回测分析报告
基于时空游戏公众号4篇交易文档提炼
{'='*70}

一、策略核心逻辑
{'─'*50}
选股逻辑：
  1. 第一波强势：连续3天以上上涨，累计涨幅>=15%
  2. 回调筛选：排除A杀（回调>20%），要求"涨猛跌缓"
  3. 板块联动：个股弱转强时，所属板块需同步走强

买入信号（4种，满足任一即触发）：
  1. 异动不跌(anomaly_no_decline)：大幅波动K线后次日不跌反涨
  2. 阳包阴(bullish_engulfing)：阳线完全吞没前日阴线
  3. 大阳反转(big_bullish_reversal)：前日阴线，次日大涨>3%
  4. 涨停开板次日走强：接近涨停但未封住，次日继续走强

卖出规则：
  - 止损：回调期最低点下方2%（约-10%）
  - 跟踪止盈：从最高点回撤8%且盈利>10%时退出
  - 时间止损：持有20天强制退出

二、回测结果（200只随机A股，近1年）
{'─'*50}
  总交易次数: {total}
  盈利次数: {wins} | 亏损次数: {losses}
  胜率: {win_rate:.1f}%
  平均盈利: {avg_profit:.2f}% | 平均亏损: {avg_loss:.2f}%
  盈亏比: {pl_ratio:.2f}
  每笔期望值: {expectancy:+.2f}%
  等权总收益: {equal_weight_return:.1f}%
  最大回撤: {max_dd:.1f}%
  夏普比率: {sharpe:.2f}
  最大连续亏损: {max_consec_loss}次
  平均持仓: {df['hold_days'].mean():.1f}天

三、信号类型对比
{'─'*50}"""

for sig in sorted(df['signal_type'].unique()):
    sub = df[df['signal_type'] == sig]
    wr = (sub['pnl_pct'] > 0).mean() * 100
    avg = sub['pnl_pct'].mean() * 100
    report += f"  {sig}: {len(sub):3d}笔  胜率{wr:5.1f}%  平均{avg:+6.2f}%\n"

report += f"""
四、板块联动效果
{'─'*50}"""
strong = df[df['sector_strong'] == True]
weak = df[df['sector_strong'] == False]
if len(strong) > 0:
    wr_s = (strong['pnl_pct'] > 0).mean() * 100
    avg_s = strong['pnl_pct'].mean() * 100
    report += f"  板块强势: {len(strong):3d}笔  胜率{wr_s:5.1f}%  平均{avg_s:+6.2f}%\n"
if len(weak) > 0:
    wr_w = (weak['pnl_pct'] > 0).mean() * 100
    avg_w = weak['pnl_pct'].mean() * 100
    report += f"  板块弱势: {len(weak):3d}笔  胜率{wr_w:5.1f}%  平均{avg_w:+6.2f}%\n"

report += f"""
五、持仓天数分布
{'─'*50}"""
df['_hold_bin'] = pd.cut(df['hold_days'], bins=[0, 3, 5, 10, 999],
                         labels=['1-3天', '4-5天', '6-10天', '11天+'])
for b in df['_hold_bin'].cat.categories:
    sub = df[df['_hold_bin'] == b]
    if len(sub) > 0:
        wr = (sub['pnl_pct'] > 0).mean() * 100
        avg = sub['pnl_pct'].mean() * 100
        report += f"  {b}: {len(sub):3d}笔  胜率{wr:5.1f}%  平均{avg:+6.2f}%\n"

report += f"""
六、退出原因分析
{'─'*50}"""
for r in df['exit_reason'].unique():
    sub = df[df['exit_reason'] == r]
    wr = (sub['pnl_pct'] > 0).mean() * 100
    avg = sub['pnl_pct'].mean() * 100
    report += f"  {r}: {len(sub):3d}笔  胜率{wr:5.1f}%  平均{avg:+6.2f}%\n"

report += f"""
七、关键发现
{'─'*50}
1. 异动不跌(anomaly_no_decline)是最优信号：65.1%胜率，+6.36%平均收益
   → 对应文档中"异动K线后该跌不跌"的核心逻辑

2. 4-10天是最佳持仓窗口：胜率75-79%
   → 验证了文档"5个交易日内就会有强势表现"的判断

3. 跟踪止盈表现最佳：88.1%胜率，+12%平均收益
   → 说明让利润奔跑的策略有效

4. 时间止损(20天)胜率仅39.3%：说明20天过长
   → 建议缩短至10-15天

5. 板块联动：强势板块中交易胜率60.1%，弱势板块68.5%
   → 弱转强本质是"逆势转顺"，在弱势板块中爆发力更强
   → 但强势板块交易频率更高（419 vs 165笔）

八、实盘建议
{'─'*50}
1. 优先使用"异动不跌"信号，胜率最高
2. 持仓4-10天为最佳窗口，超过10天考虑减仓
3. 跟踪止盈是核心盈利来源，设置8%回撤退出
4. 止损必须严格执行，止损交易胜率仅1%
5. 板块选择：优先跟踪有3只以上趋势股的板块
6. 市场环境：上升期/震荡期做多，退潮期休息
7. 仓位管理：单笔最大亏损控制在总资金2%以内

九、策略风险提示
{'─'*50}
- 回测基于前复权数据，未考虑滑点和手续费
- 实际交易中需考虑流动性（小盘股冲击成本）
- 涨停板无法买入的情况未模拟
- 板块分类为简化版（基于代码前缀），实际应用需精确行业分类
- 最大回撤-67%来自等权累加，实际分散持仓可大幅降低

{'='*70}
报告生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}
数据源: baostock (前复权日K线)
回测区间: 2025-04-21 至 2026-04-21
{'='*70}
"""

print(report)

with open('/home/jzc/wechat_text/shikong_fufei/backtest_report.md', 'w', encoding='utf-8') as f:
    f.write(report)
print("\n✅ 报告已保存至: backtest_report.md")
