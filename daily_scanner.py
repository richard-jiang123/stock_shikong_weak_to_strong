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
import argparse
import warnings
warnings = __import__('warnings'); warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer
from strategy_config import StrategyConfig
from pick_tracker import PickTracker

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
    return {'sig': sig, 'score': sc, 'reasons': ' | '.join(reasons),
            'wg': wg, 'dd': dd, 'tp': tp, 'vr': vr,
            'sl': mn*0.98, 'ep': df.iloc[ti]['close'], 'cons_low': mn}


def _find_latest_complete_date(dl, min_stocks=100):
    """找到最近一个有足够多股票数据的交易日。"""
    with dl._get_conn() as conn:
        dates = conn.execute("""
            SELECT date, COUNT(DISTINCT code) as cnt FROM stock_daily
            WHERE code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%'
            GROUP BY date
            ORDER BY date DESC
            LIMIT 30
        """).fetchall()
        for date, cnt in dates:
            if cnt >= min_stocks:
                return date, cnt
    return None, 0


def _get_real_prev_trading_date(target_date=None):
    """Query baostock to get the actual previous trading date.
    Assumes baostock is already logged in."""
    if target_date is None:
        target_date = datetime.now().strftime('%Y-%m-%d')
    td = datetime.strptime(target_date, '%Y-%m-%d')
    for i in range(1, 30):
        check_date = (td - timedelta(days=i)).strftime('%Y-%m-%d')
        rs = bs.query_history_k_data_plus('sh.000001', 'date',
            start_date=check_date, end_date=check_date, frequency='d')
        data = rs.get_data()
        if data is not None and len(data) > 0:
            return check_date
    return (td - timedelta(days=1)).strftime('%Y-%m-%d')


def _signals_filepath(script_dir, date_str):
    """Build dated CSV file path: YYYYMMDD_today_signals.csv"""
    return os.path.join(script_dir, date_str.replace('-', '') + '_today_signals.csv')


def _scan_core(dl, codes, regime_cache, name_map, start_date, end_date, verbose=True):
    """
    Run pattern scan for data up to end_date. Returns list of result dicts.
    Optimized: batch load data first, then fast iteration.
    """
    # 提前过滤ST股票
    filtered_codes = [c for c in codes if 'ST' not in name_map.get(c, '').upper()]
    if verbose:
        print(f"  扫描 {len(filtered_codes)} 只股票（已过滤ST）")
        sys.stdout.flush()

    # 批量加载K线数据
    kline_cache = dl.get_kline_batch(filtered_codes, start_date, end_date)

    results = []
    for i, code in enumerate(filtered_codes):
        if code not in kline_cache:
            continue
        df = kline_cache[code]
        r = detect_pattern(df)
        if r:
            last = df.iloc[-1]
            index_code = dl.code_to_index(code).split('.')[1]
            results.append({
                'code': code.split('.')[1], 'name': name_map.get(code, ''),
                'close': last['close'],
                'pct_chg': last['pct_chg'], 'signal': r['sig'],
                'score': r['score'], 'wave_gain': r['wg'],
                'cons_dd': r['dd'], 'vol_ratio': r['vr'],
                'stop_loss': r['sl'], 'entry': r['ep'],
                'index': index_code,
                'market_regime': {'bull': '上升期', 'range': '震荡期', 'bear': '退潮期'}.get(
                    regime_cache.get(dl.code_to_index(code), 'range'), '震荡期'),
                'reasons': r['reasons'],
            })
        if verbose and ((i+1) % 500 == 0 or i + 1 == len(filtered_codes)):
            print(f"  扫描 {i+1}/{len(filtered_codes)} | 命中 {len(results)}")
            sys.stdout.flush()

    results.sort(key=lambda x: x['score'], reverse=True)
    return results


def _print_results(results, scan_elapsed, prev_codes, verbose=True):
    """Print scan results and compare with previous day's picks."""
    # Compare with previous picks
    for r in results:
        r['是否新增'] = '是' if (prev_codes is not None and r['code'] not in prev_codes) else '否'
    if prev_codes is None:
        new_count = len(results)
        repeat_count = 0
        note = '首次扫描'
    else:
        new_count = sum(1 for r in results if r['是否新增'] == '是')
        repeat_count = len(results) - new_count
        note = f'新增 {new_count}, 延续 {repeat_count}'

    print(f"\n{'='*70}")
    print(f"完成! 扫描耗时 {scan_elapsed:.1f}s, 发现 {len(results)} 只候选 ({note})")
    print(f"{'='*70}")

    if not results:
        print("\n当日无弱转强信号")
        return

    print(f"\n{'#':<3} {'代码':<10} {'收盘':>7} {'涨幅':>6} {'信号':<12} {'分':>3} {'调':>5} {'新增':<4}")
    print("-"*58)
    for i, c in enumerate(results[:20]):
        tag = '★' if c['是否新增'] == '是' else ' '
        print(f"{i+1:<3} {c['code']:<10} {c['close']:>7.2f} {c['pct_chg']*100:>5.1f}% "
              f"{c['signal']:<12} {c['score']:>3} {c['cons_dd']*100:>4.0f}% {tag}")

    print(f"\n{'─'*70}")
    print("TOP 10 详细分析")
    print(f"{'─'*70}\n")
    for i, c in enumerate(results[:10]):
        tag = '[新增]' if c['是否新增'] == '是' else '[延续]'
        if prev_codes is None:
            tag = '[首次]'
        print(f"  ★ {c['code']} {tag}")
        print(f"    收盘 {c['close']:.2f} | 涨幅 {c['pct_chg']*100:+.1f}%")
        print(f"    信号 {c['signal']} | 评分 {c['score']}")
        print(f"    一波涨幅 {c['wave_gain']*100:.0f}% | 回调 {c['cons_dd']*100:.0f}% | 量比 {c['vol_ratio']:.1f}x")
        print(f"    建议止损 {c['stop_loss']:.2f}")
        print(f"    依据: {c['reasons']}")
        print()

    if prev_codes is None:
        print(f"  (首次扫描，无历史对比)")
    else:
        print(f"  新增: {new_count} 只 | 延续: {repeat_count} 只")
    print()


def _load_prev_csv(filepath):
    """Load stock codes from previous day's CSV file. Returns set or None."""
    try:
        if not os.path.exists(filepath):
            return None
        df = pd.read_csv(filepath)
        if 'code' not in df.columns:
            return None
        return set(df['code'].astype(str).tolist())
    except Exception:
        return None


def _get_regime_and_name(dl, target_date):
    """Get market regime cache and name map for a specific date."""
    regime_cache = {}
    for code, name in dl.INDEX_CODES.items():
        regime = dl.get_market_regime(target_date, code)
        regime_cache[code] = regime

    with dl._get_conn() as conn:
        name_df = pd.read_sql("SELECT code, name FROM stock_meta", conn)
    name_map = dict(zip(name_df['code'], name_df['name']))

    return regime_cache, name_map


def main():
    parser = argparse.ArgumentParser(description='弱转强策略 · 每日选股')
    parser.add_argument('--date', default=None, help='指定扫描日期 YYYY-MM-DD，默认今天')
    args = parser.parse_args()

    target_date = args.date or datetime.now().strftime('%Y-%m-%d')

    print("="*70)
    print("弱转强策略 · 每日选股 v2（本地缓存）")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"指定日期: {target_date}")
    print("="*70)
    sys.stdout.flush()

    bs.login()
    dl = get_data_layer()

    # 1. 根据时间决定使用哪个日期扫描
    # A股15:00收盘，15:30前数据未完全落地，使用上一工作日数据
    now_time = datetime.now()
    is_after_close = (now_time.hour > 15) or (now_time.hour == 15 and now_time.minute >= 30)

    if not is_after_close:
        # 15:30前 → 扫描上一工作日
        effective_scan_date = _get_real_prev_trading_date(target_date)
        if effective_scan_date:
            print(f"\n当前时间 {now_time.strftime('%H:%M')}（未收盘），扫描上一工作日: {effective_scan_date}")
        else:
            print(f"\n警告: 无法找到上一交易日，使用 {target_date}")
            effective_scan_date = target_date
    else:
        # 15:30后 → 扫描当天
        effective_scan_date = target_date
        print(f"\n当前时间 {now_time.strftime('%H:%M')}（已收盘），扫描当天: {effective_scan_date}")

    sys.stdout.flush()

    # 2. 更新股票列表 + 增量数据
    print("\n[1/2] 增量更新数据...")
    stock_list = dl.update_stock_list()
    codes = stock_list['code'].tolist()

    # 检查是否已更新到有效扫描日期
    is_ready, max_date, sample_cnt = dl.is_all_updated(effective_scan_date)
    if is_ready:
        detail = f"{max_date}"
        if sample_cnt > 0:
            detail += f" (采样 {sample_cnt} 只确认非交易日)"
        print(f"  数据已是最新（最新 {detail}），跳过增量更新")
    else:
        t0 = datetime.now()
        dl.batch_update(codes, verbose=True, total=len(codes))
        print(f"  个股增量耗时: {(datetime.now()-t0).total_seconds()/60:.1f} 分钟")

    # 验证数据完整性：检查指定日期是否有足够股票数据
    # 如果不完整，尝试再次增量更新（最多2次）
    max_update_attempts = 2
    for attempt in range(max_update_attempts):
        with dl._get_conn() as conn:
            target_cnt = conn.execute("""
                SELECT COUNT(DISTINCT code) FROM stock_daily WHERE date=?
                AND (code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%')
            """, (effective_scan_date,)).fetchone()[0]

        if target_cnt >= 100:
            # 指定日期数据完整
            print(f"\n[数据验证] 扫描日期 {effective_scan_date} ✓，{target_cnt} 只股票有数据")
            break

        # 数据不完整，尝试再次更新
        if attempt < max_update_attempts - 1:
            print(f"\n[数据验证] {effective_scan_date} 数据不完整（仅 {target_cnt} 只），尝试再次更新...")
            t0 = datetime.now()
            dl.batch_update(codes, verbose=True, total=len(codes))
            print(f"  再次更新耗时: {(datetime.now()-t0).total_seconds()/60:.1f} 分钟")

    # 最终检查：如果仍不完整，退出提示用户
    with dl._get_conn() as conn:
        final_cnt = conn.execute("""
            SELECT COUNT(DISTINCT code) FROM stock_daily WHERE date=?
            AND (code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%')
        """, (effective_scan_date,)).fetchone()[0]

    if final_cnt < 100:
        print(f"\n✗ 数据不足: 扫描日期 {effective_scan_date} 仅 {final_cnt} 只股票有数据")
        print(f"   请稍后重试或手动更新数据库。")
        bs.logout()
        sys.exit(1)

    print("\n更新大盘指数数据...")
    dl.update_index_data()
    sys.stdout.flush()

    # 3. 确定对比基准（上一期选股结果）
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # 获取上一期CSV文件路径
    prev_scan_date = _get_real_prev_trading_date(effective_scan_date)
    prev_file = _signals_filepath(script_dir, prev_scan_date) if prev_scan_date else None

    prev_codes = None
    if prev_file and os.path.exists(prev_file):
        prev_codes = _load_prev_csv(prev_file)
        if prev_codes is not None:
            print(f"\n[对比基准] 已加载上期选股文件 {os.path.basename(prev_file)}: {len(prev_codes)} 只")
        else:
            prev_codes = None

    if prev_codes is None:
        # 上一期文件不存在，需要先扫描上一期
        if prev_scan_date:
            print(f"\n[对比基准] 无上期选股文件，先扫描 {prev_scan_date} 作为基准...")
            prev_regime, prev_names = _get_regime_and_name(dl, prev_scan_date)
            scan_start = (datetime.strptime(effective_scan_date, '%Y-%m-%d') - timedelta(days=200)).strftime('%Y-%m-%d')
            t_scan = datetime.now()
            prev_results = _scan_core(dl, codes, prev_regime, prev_names, scan_start, prev_scan_date, verbose=True)
            prev_elapsed = (datetime.now() - t_scan).total_seconds()
            prev_codes = set(r['code'] for r in prev_results)
            # 保存上一期的结果
            prev_csv = _signals_filepath(script_dir, prev_scan_date)
            if prev_results:
                pd.DataFrame(prev_results).to_csv(prev_csv, index=False, encoding='utf-8-sig')
                print(f"  已保存 {os.path.basename(prev_csv)} ({len(prev_results)} 只)")
            print(f"  上期({prev_scan_date})扫描耗时 {prev_elapsed:.1f}s, 发现 {len(prev_results)} 只候选")
        else:
            print("\n[对比基准] 无法找到上一交易日，标记为首次扫描")

    # 4. 更新已有选股的跟踪状态
    tracker = PickTracker()
    stats = tracker.update_tracking()
    if stats['exited'] > 0:
        print(f"  跟踪更新: {stats['exited']} 只已退出, {stats['still_active']} 只仍活跃")
        sys.stdout.flush()

    # 5. 扫描目标日期
    print(f"\n[2/2] 批量加载数据并扫描 ({effective_scan_date})...")
    sys.stdout.flush()
    scan_start = (datetime.strptime(effective_scan_date, '%Y-%m-%d') - timedelta(days=200)).strftime('%Y-%m-%d')
    t1 = datetime.now()

    regime_cache, name_map = _get_regime_and_name(dl, effective_scan_date)
    results = _scan_core(dl, codes, regime_cache, name_map, scan_start, effective_scan_date, verbose=True)
    scan_elapsed = (datetime.now() - t1).total_seconds()

    # 打印结果
    _print_results(results, scan_elapsed, prev_codes)

    # 保存结果（使用带日期的文件名）
    signals_file = _signals_filepath(script_dir, effective_scan_date)
    if results:
        pd.DataFrame(results).to_csv(signals_file, index=False, encoding='utf-8-sig')
        print(f"✅ 保存至 {os.path.basename(signals_file)}")
        # Record picks for tracking
        n_tracked = tracker.record_picks(pd.DataFrame(results), pick_date=effective_scan_date)
        print(f"✅ 已记录 {n_tracked} 只股票用于跟踪")

    bs.logout()

if __name__ == '__main__':
    main()
