#!/usr/bin/env python3
"""
弱转强策略 · 每日选股 v2
使用本地数据库缓存，增量更新
"""
import baostock as bs
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os
warnings = __import__('warnings'); warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer

CONFIG = {
    'first_wave_min_days': 3, 'first_wave_min_gain': 0.15,
    'consolidation_max_days': 15, 'consolidation_max_drawdown': 0.20,
    'weak_strong_threshold': 0.03, 'anomaly_amplitude': 0.06,
}

def detect_pattern(df):
    n = len(df)
    if n < 20: return None
    # === 阶段1: 检测一波上涨 ===
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
    # === 阶段2: 检测温和回调 ===
    peak = df.iloc[we]['close']; cs = we + 1
    mn, mi, days = peak, cs, 0
    for k in range(cs, min(n, cs + CONFIG['consolidation_max_days'])):
        days += 1
        if df.iloc[k]['low'] < mn: mn = df.iloc[k]['low']; mi = k
    dd = (peak - mn) / peak
    if dd > CONFIG['consolidation_max_drawdown']: return None
    dn = sum(1 for k in range(cs, mi+1) if k < n and df.iloc[k]['pct_chg'] < 0)
    if days < 3 or dn/max(days,1) >= 0.7: return None
    # === 阶段3: 检测反转信号 (必须是最新一个交易日) ===
    ti = n - 1; tp = df.iloc[ti]['pct_chg']
    se = mi; ss = cs
    if ti < max(se, ss+2) or ti > max(se, ss+2) + 10: return None
    # 按优先级检测信号，一旦命中就不再覆盖
    sig = None
    if df.iloc[ti-1]['amplitude'] > CONFIG['anomaly_amplitude'] and tp > 0.01:
        sig = '异动不跌'
    elif tp > 0.02 and df.iloc[ti-1]['close'] < df.iloc[ti-1]['open'] and df.iloc[ti]['close'] > df.iloc[ti-1]['open']:
        sig = '阳包阴'
    elif tp > CONFIG['weak_strong_threshold'] and df.iloc[ti-1]['close'] < df.iloc[ti-1]['open']:
        sig = '大阳反转'
    elif df.iloc[ti-1]['pct_chg'] > 0.08 and df.iloc[ti-1]['close'] < df.iloc[ti-1]['high']*0.97 and tp > 0.02:
        sig = '烂板次日'
    if not sig: return None
    # === 评分 ===
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
    # 止损价 = 回调阶段最低价(mn) × 0.98
    # mn 是回调期间所有 K 线的 low 字段的最小值，即盘中实际触及的最低价格
    # ×0.98 表示留 2% 安全边际，避免盘中波动误触发
    return {'sig': sig, 'score': sc, 'reasons': ' | '.join(reasons),
            'wg': wg, 'dd': dd, 'tp': tp, 'vr': vr,
            'sl': mn*0.98, 'ep': df.iloc[ti]['close'], 'cons_low': mn}

def main():
    print("="*70)
    print("弱转强策略 · 每日选股 v2（本地缓存）")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    sys.stdout.flush()

    bs.login()
    dl = get_data_layer()

    # 1. 更新股票列表 + 增量数据 + 指数
    print("\n[1/2] 增量更新数据...")
    stock_list = dl.update_stock_list()
    codes = stock_list['code'].tolist()
    t0 = datetime.now()
    dl.batch_update(codes, verbose=True, total=len(codes))
    print(f"  个股增量耗时: {(datetime.now()-t0).total_seconds()/60:.1f} 分钟")

    # 更新指数数据
    print("\n更新大盘指数数据...")
    dl.update_index_data()

    # 判断各主要指数对应的市场环境
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"\n{'='*40}")
    print("当前市场环境（按指数）")
    print(f"{'='*40}")
    regime_cache = {}
    for code, name in dl.INDEX_CODES.items():
        regime = dl.get_market_regime(today, code)
        regime_name = {'bull': '上升期', 'range': '震荡期', 'bear': '退潮期'}[regime]
        print(f"  {name}: 【{regime_name}】")
        regime_cache[code] = regime
    print(f"{'='*40}")
    sys.stdout.flush()

    # 2. 本地扫描
    print(f"\n[2/2] 批量加载数据并扫描...")
    sys.stdout.flush()
    today = datetime.now().strftime('%Y-%m-%d')
    start_full = (datetime.now() - timedelta(days=200)).strftime('%Y-%m-%d')
    t1 = datetime.now()

    # 批量加载所有股票数据（一次SQL查询）
    print("  从本地数据库加载数据...")
    sys.stdout.flush()
    kline_cache = dl.get_kline_batch(codes, start_full, today)
    print(f"  成功加载 {len(kline_cache)}/{len(codes)} 只股票数据")
    sys.stdout.flush()

    # 加载股票名称映射
    with dl._get_conn() as conn:
        name_df = pd.read_sql("SELECT code, name FROM stock_meta", conn)
    name_map = dict(zip(name_df['code'], name_df['name']))

    results = []
    for i, code in enumerate(codes):
        if code not in kline_cache:
            continue
        # 剔除ST股
        name = name_map.get(code, '')
        if 'ST' in name.upper():
            continue
        df = kline_cache[code]
        r = detect_pattern(df)
        if r:
            last = df.iloc[-1]
            results.append({
                'code': code.split('.')[1], 'name': name_map.get(code, ''),
                'close': last['close'],
                'pct_chg': last['pct_chg'], 'signal': r['sig'],
                'score': r['score'], 'wave_gain': r['wg'],
                'cons_dd': r['dd'], 'vol_ratio': r['vr'],
                'stop_loss': r['sl'], 'entry': r['ep'],
                'index': dl.code_to_index(code).split('.')[1],
                'market_regime': {'bull': '上升期', 'range': '震荡期', 'bear': '退潮期'}.get(
                    regime_cache.get(dl.code_to_index(code), 'range'), '震荡期'),
                'reasons': r['reasons'],
            })
        if (i+1) % 500 == 0 or i + 1 == len(codes):
            print(f"  扫描 {i+1}/{len(codes)} | 命中 {len(results)}")
            sys.stdout.flush()

    scan_elapsed = (datetime.now() - t1).total_seconds()
    results.sort(key=lambda x: x['score'], reverse=True)

    print(f"\n{'='*70}")
    print(f"完成! 扫描耗时 {scan_elapsed:.1f}s, 发现 {len(results)} 只候选")
    print(f"{'='*70}")

    if results:
        print(f"\n{'#':<3} {'代码':<10} {'收盘':>7} {'涨幅':>6} {'信号':<12} {'分':>3} {'调':>5}")
        print("-"*52)
        for i, c in enumerate(results[:20]):
            print(f"{i+1:<3} {c['code']:<10} {c['close']:>7.2f} {c['pct_chg']*100:>5.1f}% "
                  f"{c['signal']:<12} {c['score']:>3} {c['cons_dd']*100:>4.0f}%")

        print(f"\n{'─'*70}")
        print("TOP 10 详细分析")
        print(f"{'─'*70}\n")
        for i, c in enumerate(results[:10]):
            print(f"  ★ {c['code']}")
            print(f"    收盘 {c['close']:.2f} | 涨幅 {c['pct_chg']*100:+.1f}%")
            print(f"    信号 {c['signal']} | 评分 {c['score']}")
            print(f"    一波涨幅 {c['wave_gain']*100:.0f}% | 回调 {c['cons_dd']*100:.0f}% | 量比 {c['vol_ratio']:.1f}x")
            print(f"    建议止损 {c['stop_loss']:.2f}")
            print(f"    依据: {c['reasons']}")
            print()

        pd.DataFrame(results).to_csv(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'today_signals.csv'),
            index=False, encoding='utf-8-sig')
        print(f"✅ 保存至 today_signals.csv")
    else:
        print("\n今日无弱转强信号")

    bs.logout()

if __name__ == '__main__':
    main()
