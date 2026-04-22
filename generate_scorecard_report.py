#!/usr/bin/env python3
"""
从 pick_tracking 表生成选股跟踪报告 (markdown)。
替代 analyze_results.py 中硬编码的第 7-9 节。
"""
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pick_tracker import PickTracker


def generate_tracking_report(pick_date=None, lookback_days=90, output_file=None):
    """
    Generate a markdown report from the pick_tracking table.

    Sections:
      1. Overall Performance Summary
      2. Signal Type Effectiveness
      3. Market Regime Analysis
      4. Score Predictive Power
      5. Top / Worst Performers
      6. Trend Analysis (compare with previous period)
      7. Actionable Recommendations
    """
    if pick_date is None:
        pick_date = datetime.now().strftime('%Y-%m-%d')

    tracker = PickTracker()
    sc = tracker.get_scorecard(pick_date=pick_date, lookback_days=lookback_days)

    if sc is None:
        msg = '回溯期内无选股记录。'
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(msg + '\n')
        return msg

    # Even if no picks exited yet, always generate report
    lines = []
    lines.append(f'# 弱转强策略 · 选股跟踪报告')
    lines.append(f'')
    lines.append(f'报告日期: {pick_date} | 回溯: {lookback_days} 天')
    lines.append(f'')

    # ── Section 1: Overall Summary ──────────────────────────────
    s = sc['summary']
    lines.append(f'## 1. 总体表现')
    lines.append(f'')
    lines.append(f'| 指标 | 数值 |')
    lines.append(f'|------|------|')
    lines.append(f'| 总选股 | {s["total_picks"]} 只 |')
    lines.append(f'| 已退出 | {s["exited"]} 只 |')
    lines.append(f'| 仍活跃 | {s["still_active"]} 只 |')
    if s.get('exited', 0) > 0:
        lines.append(f'| 胜率 | {s["win_rate"]:.1f}% |')
        lines.append(f'| 平均盈亏 | {s["avg_pnl"]:+.2f}% |')
        lines.append(f'| 平均持仓 | {s["avg_hold_days"]:.1f} 天 |')
        lines.append(f'| 最大盈利 | {s["max_pnl"]:+.2f}% |')
        lines.append(f'| 最大亏损 | {s["min_pnl"]:+.2f}% |')
    else:
        lines.append(f'')
        lines.append(f'> 尚无选股退出，暂无胜率和盈亏数据。等待后续行情发展。')
    lines.append(f'')

    # ── Section 2: Active Picks List ──────────────────────────────
    with tracker._get_conn() as conn:
        from datetime import datetime as dt, timedelta as td
        start_d = (dt.strptime(pick_date, '%Y-%m-%d') - td(days=lookback_days)).strftime('%Y-%m-%d')
        active_df = pd.read_sql(
            'SELECT code, name, signal_type, score, wave_gain, cons_dd, market_regime, pick_date FROM pick_tracking WHERE status = ? AND pick_date >= ? ORDER BY score DESC',
            conn, params=('active', start_d)
        )
    if not active_df.empty:
        lines.append(f'## 2. 当前活跃候选')
        lines.append(f'')
        lines.append(f'| 选股日期 | 代码 | 名称 | 信号 | 评分 | 环境 |')
        lines.append(f'|----------|------|------|------|------|------|')
        for _, r in active_df.iterrows():
            lines.append(f'| {r["pick_date"]} | {r["code"]} | {r["name"]} | {r["signal_type"]} | {r["score"]:.0f} | {r["market_regime"]} |')
        lines.append(f'')

    # ── Section 2: Signal Type ──────────────────────────────────
    if sc.get('by_signal_type'):
        lines.append(f'## 2. 信号类型效果')
        lines.append(f'')
        lines.append(f'| 信号 | 笔数 | 胜率 | 平均盈亏 |')
        lines.append(f'|------|------|------|----------|')
        for sig, data in sorted(sc['by_signal_type'].items(), key=lambda x: x[1]['avg_pnl'], reverse=True):
            lines.append(f'| {sig} | {data["count"]} | {data["win_rate"]:.1f}% | {data["avg_pnl"]:+.2f}% |')
        lines.append(f'')

    # ── Section 3: Market Regime ────────────────────────────────
    if sc.get('by_market_regime'):
        lines.append(f'## 3. 市场环境分析')
        lines.append(f'')
        lines.append(f'| 环境 | 笔数 | 胜率 | 平均盈亏 |')
        lines.append(f'|------|------|------|----------|')
        for regime, data in sc['by_market_regime'].items():
            lines.append(f'| {regime} | {data["count"]} | {data["win_rate"]:.1f}% | {data["avg_pnl"]:+.2f}% |')
        lines.append(f'')

    # ── Section 4: Score Predictive Power ───────────────────────
    lines.append(f'## 4. 评分预测力')
    lines.append(f'')
    corr = sc.get('score_predictive_power')
    if corr is not None:
        lines.append(f'Spearman 评分-盈亏相关系数: **{corr:+.3f}**')
        lines.append(f'')
        if abs(corr) > 0.3:
            lines.append(f'- 评分体系**有效**，高分股票确实表现更好')
        elif abs(corr) > 0.1:
            lines.append(f'- 评分体系有**一定预测力**，但不够强')
        else:
            lines.append(f'- 评分体系**预测力不足**，高分不等于高收益，建议调整评分权重')
    else:
        lines.append(f'无法计算评分相关性（样本不足）')
    lines.append(f'')

    if sc.get('by_score_quartile'):
        lines.append(f'| 分位 | 笔数 | 胜率 | 平均盈亏 |')
        lines.append(f'|------|------|------|----------|')
        for q, data in sc['by_score_quartile'].items():
            lines.append(f'| {q} | {data["count"]} | {data["win_rate"]:.1f}% | {data["avg_pnl"]:+.2f}% |')
        lines.append(f'')

    # ── Section 5: Top / Worst ──────────────────────────────────
    if sc.get('top_performers'):
        lines.append(f'## 5. 个股表现')
        lines.append(f'')
        lines.append(f'### Top 5 盈利')
        lines.append(f'')
        lines.append(f'| 代码 | 名称 | 信号 | 评分 | 盈亏 | 持仓 |')
        lines.append(f'|------|------|------|------|------|------|')
        for p in sc['top_performers']:
            lines.append(f'| {p["code"]} | {p["name"]} | {p["signal_type"]} | {p["score"]:.0f} | {p["final_pnl_pct"]*100:+.2f}% | {p["hold_days"]}天 |')
        lines.append(f'')

        lines.append(f'### Top 5 亏损')
        lines.append(f'')
        lines.append(f'| 代码 | 名称 | 信号 | 评分 | 盈亏 | 持仓 |')
        lines.append(f'|------|------|------|------|------|------|')
        for p in sc['worst_performers']:
            lines.append(f'| {p["code"]} | {p["name"]} | {p["signal_type"]} | {p["score"]:.0f} | {p["final_pnl_pct"]*100:+.2f}% | {p["hold_days"]}天 |')
        lines.append(f'')

    # ── Section 6: Trend Analysis ───────────────────────────────
    if s.get('exited', 0) > 10:
        prev_start = (datetime.strptime(pick_date, '%Y-%m-%d') - timedelta(days=lookback_days * 2)).strftime('%Y-%m-%d')
        prev_end = (datetime.strptime(pick_date, '%Y-%m-%d') - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        tracker2 = PickTracker()
        prev_sc = tracker2.get_scorecard(pick_date=prev_end, lookback_days=lookback_days)
        if prev_sc and 'summary' in prev_sc and prev_sc['summary'].get('exited', 0) > 0:
            prev_wr = prev_sc['summary'].get('win_rate', 0)
            curr_wr = s.get('win_rate', 0)
            prev_pnl = prev_sc['summary'].get('avg_pnl', 0)
            curr_pnl = s.get('avg_pnl', 0)
            lines.append(f'## 6. 趋势对比')
            lines.append(f'')
            lines.append(f'| 指标 | 前{lookback_days}天 | 近{lookback_days}天 | 变化 |')
            lines.append(f'|------|------------|------------|------|')
            lines.append(f'| 胜率 | {prev_wr:.1f}% | {curr_wr:.1f}% | {curr_wr-prev_wr:+.1f}pp |')
            lines.append(f'| 平均盈亏 | {prev_pnl:+.2f}% | {curr_pnl:+.2f}% | {curr_pnl-prev_pnl:+.2f}% |')
            lines.append(f'')

            if curr_wr < prev_wr - 10:
                lines.append(f'⚠ 胜率下降超过 10pp，策略可能需要调整')
                lines.append(f'')
            elif curr_wr > prev_wr + 5:
                lines.append(f'胜率上升，策略表现改善')
                lines.append(f'')

    # ── Section 7: Recommendations ──────────────────────────────
    lines.append(f'## 7. 操作建议')
    lines.append(f'')
    recommendations = _generate_recommendations(sc)
    for rec in recommendations:
        lines.append(f'- {rec}')
    lines.append(f'')

    report = '\n'.join(lines)

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f'报告已保存: {output_file}')

    return report


def _generate_recommendations(sc):
    """Generate actionable recommendations based on scorecard data."""
    recs = []
    s = sc.get('summary', {})
    by_signal = sc.get('by_signal_type', {})
    by_regime = sc.get('by_market_regime', {})
    corr = sc.get('score_predictive_power')

    # Signal type recommendations
    if by_signal:
        best_sig = max(by_signal.items(), key=lambda x: x[1]['avg_pnl'])
        worst_sig = min(by_signal.items(), key=lambda x: x[1]['avg_pnl'])
        if best_sig[1]['count'] >= 3:
            recs.append(f'"{best_sig[0]}" 信号表现最好（胜率{best_sig[1]["win_rate"]:.1f}%），建议重点关注')
        if worst_sig[1]['count'] >= 3 and worst_sig[1]['avg_pnl'] < -5:
            recs.append(f'"{worst_sig[0]}" 信号近期表现较差（平均{worst_sig[1]["avg_pnl"]:+.2f}%），建议降低评分权重或暂时回避')

    # Market regime recommendations
    if by_regime:
        for regime, data in by_regime.items():
            if data['win_rate'] < 40 and data['count'] >= 3:
                recs.append(f'{regime}环境下胜率仅{data["win_rate"]:.1f}%，建议在该环境下降低仓位或暂停交易')
            elif data['win_rate'] > 65 and data['count'] >= 3:
                recs.append(f'{regime}环境下胜率{data["win_rate"]:.1f}%，可适当提高仓位')

    # Score system recommendations
    if corr is not None:
        if abs(corr) < 0.1 and s.get('exited', 0) >= 10:
            recs.append('评分-盈亏相关性极低，建议重新审视评分权重设计')
        elif corr > 0.3:
            recs.append('评分体系有效，可优先选择高分标的')

    # Overall performance
    if s.get('exited', 0) >= 5:
        wr = s.get('win_rate', 0)
        if wr < 40:
            recs.append(f'整体胜率{wr:.1f}%偏低，低于回测基准，需关注策略是否失效')
        elif wr > 60:
            recs.append(f'整体胜率{wr:.1f}%，策略运行良好')

    if not recs:
        recs.append('样本不足，暂无法生成有效建议')

    return recs


# ── CLI ──────────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='生成选股跟踪报告')
    parser.add_argument('--date', default=None, help='Report date YYYY-MM-DD')
    parser.add_argument('--lookback', type=int, default=90, help='Lookback days')
    parser.add_argument('--output', default='tracking_report.md', help='Output file')
    args = parser.parse_args()

    report = generate_tracking_report(
        pick_date=args.date,
        lookback_days=args.lookback,
        output_file=args.output
    )
    if not os.path.exists(args.output):
        print(report)
