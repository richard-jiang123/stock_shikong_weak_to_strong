#!/usr/bin/env python3
"""全市场弱转强扫描 - 写入文件供后台查看"""
import baostock as bs
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys, os

CONFIG = {
    'first_wave_min_days': 3, 'first_wave_min_gain': 0.15,
    'consolidation_max_days': 15, 'consolidation_max_drawdown': 0.20,
    'weak_strong_threshold': 0.03, 'anomaly_amplitude': 0.06,
}

def detect_pattern(df):
    n = len(df)
    if n < 20: return None
    waves = []
    i = 0
    while i < n - CONFIG['first_wave_min_days']:
        up, gain, j = 0, 0.0, i
        while j < n - 1:
            p = df.iloc[j]['pct_chg']
            if p > 0: up += 1; gain += p; j += 1
            else: break
        if up >= CONFIG['first_wave_min_days'] and gain >= CONFIG['first_wave_min_gain']:
            waves.append((i, j-1, gain, up)); i = j
        else: i += 1
    if not waves: return None
    ws, we, wg, wd = waves[-1]
    if we >= n - 5: return None
    peak = df.iloc[we]['close']; cs = we + 1
    mn, mi, days = peak, cs, 0
    for k in range(cs, min(n, cs + CONFIG['consolidation_max_days'])):
        days += 1
        if df.iloc[k]['low'] < mn: mn = df.iloc[k]['low']; mi = k
    dd = (peak - mn) / peak
    if dd > CONFIG['consolidation_max_drawdown']: return None
    dn = sum(1 for k in range(cs, mi+1) if k < n and df.iloc[k]['pct_chg'] < 0)
    if days < 3 or dn/max(days,1) >= 0.7: return None
    ti = n - 1; tp = df.iloc[ti]['pct_chg']
    se = mi; ss = cs
    if ti < max(se, ss+2) or ti > max(se, ss+2) + 10: return None
    sig = None
    if tp > CONFIG['weak_strong_threshold'] and df.iloc[ti-1]['close'] < df.iloc[ti-1]['open']: sig = '大阳反转'
    if tp > 0.02 and df.iloc[ti-1]['close'] < df.iloc[ti-1]['open'] and df.iloc[ti]['close'] > df.iloc[ti-1]['open']: sig = '阳包阴'
    if df.iloc[ti-1]['amplitude'] > CONFIG['anomaly_amplitude'] and tp > 0.01: sig = '异动不跌'
    if df.iloc[ti-1]['pct_chg'] > 0.08 and df.iloc[ti-1]['close'] < df.iloc[ti-1]['high']*0.97 and tp > 0.02: sig = '烂板次日'
    if not sig: return None
    sc = 5; reasons = []
    if wg > 0.30: sc += 20; reasons.append(f"一波{wg*100:.0f}%")
    elif wg > 0.20: sc += 10; reasons.append(f"一波{wg*100:.0f}%")
    if dd < 0.08: sc += 15; reasons.append(f"浅调{dd*100:.0f}%")
    elif dd < 0.15: sc += 10; reasons.append(f"调{dd*100:.0f}%")
    if tp > 0.07: sc += 15
    elif tp > 0.05: sc += 10
    elif tp > 0.03: sc += 5
    vr = df.iloc[ti]['volume'] / max(df.iloc[ti]['volume_ma5'], 1)
    if vr > 2: sc += 10; reasons.append(f"放量{vr:.1f}x")
    elif vr > 1.5: sc += 5
    if df.iloc[ti]['ma5'] > df.iloc[ti]['ma10'] > df.iloc[ti]['ma20']: sc += 10; reasons.append("多头")
    elif df.iloc[ti]['ma5'] > df.iloc[ti]['ma10']: sc += 5
    if sig == '异动不跌': sc += 10
    return {'sig': sig, 'score': sc, 'reasons': ' | '.join(reasons),
            'wg': wg, 'dd': dd, 'tp': tp, 'vr': vr,
            'sl': mn*0.98, 'ep': df.iloc[ti]['close']}

def get_kline(code, start, end):
    try:
        rs = bs.query_history_k_data_plus(code,
            "date,open,high,low,close,volume,amount",
            start_date=start, end_date=end, frequency="d", adjustflag="2")
        df = rs.get_data()
        if df is None or len(df) < 60: return None
        for c in ['open','high','low','close','volume','amount']: df[c] = df[c].astype(float)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        df['pct_chg'] = df['close'].pct_change()
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma10'] = df['close'].rolling(10).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        df['volume_ma5'] = df['volume'].rolling(5).mean()
        df['amplitude'] = (df['high'] - df['low']) / df['close']
        return df
    except: return None

def main():
    log = open('/home/jzc/wechat_text/shikong_fufei/scan_progress.log', 'w', encoding='utf-8')
    def msg(s):
        print(s, flush=True)
        log.write(s + '\n')
        log.flush()

    msg("="*70)
    msg("全市场弱转强扫描")
    msg(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    msg("="*70)

    bs.login()
    today = datetime.now().strftime('%Y-%m-%d')
    start_full = (datetime.now() - timedelta(days=200)).strftime('%Y-%m-%d')
    t0 = datetime.now()

    # 获取全市场
    rs = bs.query_all_stock(day=today)
    all_stocks = rs.get_data()
    mask = all_stocks['code'].str.match(r'^(sh\.60|sz\.00|sz\.30)\d{4}$')
    all_stocks = all_stocks[mask].copy()
    total = len(all_stocks)
    msg(f"\n全市场 {total} 只A股，开始逐一扫描...")
    msg(f"预计耗时: 25-35 分钟")

    results = []
    last_report = 0

    for i, (_, row) in enumerate(all_stocks.iterrows()):
        code = row['code']
        df = get_kline(code, start_full, today)
        if df is not None:
            r = detect_pattern(df)
            if r:
                last = df.iloc[-1]
                results.append({
                    'code': code.split('.')[1], 'close': last['close'],
                    'pct_chg': last['pct_chg'], 'signal': r['sig'],
                    'score': r['score'], 'wave_gain': r['wg'],
                    'cons_dd': r['dd'], 'vol_ratio': r['vr'],
                    'stop_loss': r['sl'], 'entry': r['ep'],
                    'reasons': r['reasons'],
                })

        # 每500只汇报一次
        if i + 1 - last_report >= 500 or i + 1 == total:
            elapsed = (datetime.now() - t0).total_seconds()
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            remaining = (total - i - 1) / rate if rate > 0 else 0
            msg(f"  [{i+1}/{total}] {i+1*100//total:3d}% | 候选 {len(results):2d}只 | "
                f"已用 {elapsed/60:.1f}分 | 剩余 ~{remaining/60:.0f}分 | "
                f"速度 {rate:.1f}只/秒")
            last_report = i + 1

    elapsed = (datetime.now() - t0).total_seconds()
    results.sort(key=lambda x: x['score'], reverse=True)

    msg(f"\n{'='*70}")
    msg(f"扫描完成! 总耗时 {elapsed/60:.1f} 分钟, 发现 {len(results)} 只候选")
    msg(f"{'='*70}")

    if results:
        msg(f"\n{'#':<3} {'代码':<10} {'收盘':>7} {'涨幅':>6} {'信号':<12} {'分':>3} {'调':>5}")
        msg("-"*52)
        for i, c in enumerate(results[:20]):
            msg(f"{i+1:<3} {c['code']:<10} {c['close']:>7.2f} {c['pct_chg']*100:>5.1f}% "
                f"{c['signal']:<12} {c['score']:>3} {c['cons_dd']*100:>4.0f}%")

        msg(f"\n{'─'*70}")
        msg("TOP 10 详细分析")
        msg(f"{'─'*70}\n")
        for i, c in enumerate(results[:10]):
            msg(f"  ★ {c['code']}")
            msg(f"    收盘 {c['close']:.2f} | 涨幅 {c['pct_chg']*100:+.1f}%")
            msg(f"    信号 {c['signal']} | 评分 {c['score']}")
            msg(f"    一波涨幅 {c['wave_gain']*100:.0f}% | 回调 {c['cons_dd']*100:.0f}% | 量比 {c['vol_ratio']:.1f}x")
            msg(f"    建议止损 {c['stop_loss']:.2f}")
            msg(f"    依据: {c['reasons']}")
            msg("")

        pd.DataFrame(results).to_csv('/home/jzc/wechat_text/shikong_fufei/today_signals_full.csv',
            index=False, encoding='utf-8-sig')
        msg(f"✅ 全部结果保存至 today_signals_full.csv")
    else:
        msg("\n今日全市场无弱转强信号")

    bs.logout()
    log.close()

if __name__ == '__main__':
    main()
