#!/usr/bin/env python3
"""
弱转强(Weak-to-Strong) 量化回测系统 v3
使用本地 SQLite 缓存数据，大幅提速
"""

import baostock as bs
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import random
import os
import sys
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer

CONFIG = {
    'first_wave_min_days': 3,
    'first_wave_min_gain': 0.15,
    'consolidation_max_days': 15,
    'consolidation_max_drawdown': 0.20,
    'stop_loss_pct': 0.10,
    'max_hold_days': 20,
    'quick_exit_days': 5,
    'weak_strong_threshold': 0.03,
    'sector_momentum_window': 10,
    'sector_min_strength': 0.05,
    'limit_up_pct': 0.095,
    'big_candle_pct': 0.05,
    'anomaly_amplitude': 0.06,
    'trailing_stop_pct': 0.08,
}

# ==================== 数据获取 ====================
def get_industry_mapping():
    industry_map = {
        '600': '银行/金融', '601': '银行/金融', '603': '多元金融',
        '000': '综合', '002': '中小板', '300': '创业板',
    }
    def get_industry(code):
        prefix = code.split('.')[1][:3]
        return industry_map.get(prefix, '其他')
    return get_industry

def compute_sector_momentum(codes_in_sector, get_kline_func, lookback=10):
    if len(codes_in_sector) == 0:
        return 0.0, False
    returns = []
    for code in codes_in_sector[:20]:
        df = get_kline_func(code)
        if df is not None and len(df) >= lookback:
            ret = df.iloc[-lookback:]['pct_chg'].mean()
            returns.append(ret)
    if not returns:
        return 0.0, False
    avg_momentum = np.mean(returns)
    is_strong = avg_momentum > CONFIG['sector_min_strength'] / lookback
    return avg_momentum, is_strong

# ==================== 策略核心 ====================
def detect_first_wave(df):
    waves = []
    n = len(df)
    if n < 20: return waves
    i = 0
    while i < n - CONFIG['first_wave_min_days']:
        up_days, total_gain, j = 0, 0.0, i
        while j < n - 1:
            pct = df.iloc[j]['pct_chg']
            if pct > 0: up_days += 1; total_gain += pct; j += 1
            else: break
        if up_days >= CONFIG['first_wave_min_days'] and total_gain >= CONFIG['first_wave_min_gain']:
            waves.append((i, j - 1, total_gain, up_days)); i = j
        else: i += 1
    return waves

def detect_consolidation(df, wave_end):
    n = len(df)
    if wave_end >= n - 5: return None
    peak = df.iloc[wave_end]['close']
    cons_start = wave_end + 1
    min_price, min_idx, days_in_cons = peak, cons_start, 0
    for i in range(cons_start, min(n, cons_start + CONFIG['consolidation_max_days'])):
        days_in_cons += 1
        if df.iloc[i]['low'] < min_price: min_price = df.iloc[i]['low']; min_idx = i
    drawdown = (peak - min_price) / peak
    is_a_kill = drawdown > CONFIG['consolidation_max_drawdown']
    down_days = sum(1 for i in range(cons_start, min_idx + 1) if i < n and df.iloc[i]['pct_chg'] < 0)
    is_slow_decline = days_in_cons >= 3 and down_days / max(days_in_cons, 1) < 0.7
    is_valid = not is_a_kill and is_slow_decline and days_in_cons >= 3
    return {'start': cons_start, 'end': min_idx, 'duration': days_in_cons,
            'max_drawdown': drawdown, 'min_price': min_price, 'is_valid': is_valid, 'is_a_kill': is_a_kill}

def detect_weak_to_strong_signals(df, consolidation, wave_info):
    signals = []
    n = len(df)
    if consolidation is None or not consolidation['is_valid']: return signals
    cons_end = consolidation['end']
    cons_start = consolidation['start']
    cons_low = consolidation['min_price']
    search_start = max(cons_end, cons_start + 2)
    for i in range(search_start, min(n, search_start + 10)):
        if i >= n: break
        pct = df.iloc[i]['pct_chg']
        signal_type = None
        if i > 0 and pct > CONFIG['weak_strong_threshold']:
            if df.iloc[i-1]['close'] < df.iloc[i-1]['open']:
                signal_type = 'big_bullish_reversal'
        if i > 0 and pct > 0.02:
            if df.iloc[i-1]['close'] < df.iloc[i-1]['open'] and df.iloc[i]['close'] > df.iloc[i-1]['open']:
                signal_type = 'bullish_engulfing'
        if i > 0:
            prev_pct = df.iloc[i-1]['pct_chg']
            prev_high = df.iloc[i-1]['high']
            prev_close = df.iloc[i-1]['close']
            if prev_pct > 0.08 and prev_close < prev_high * 0.97 and pct > 0.02:
                signal_type = 'limit_up_open_next_strong'
        if i > 1:
            prev_amp = df.iloc[i-1]['amplitude'] if 'amplitude' in df.columns else \
                       (df.iloc[i-1]['high'] - df.iloc[i-1]['low']) / df.iloc[i-1]['close']
            if prev_amp > CONFIG['anomaly_amplitude'] and pct > 0.01:
                signal_type = 'anomaly_no_decline'
        if signal_type:
            signals.append({
                'idx': i, 'date': df.iloc[i]['date'], 'type': signal_type,
                'entry_price': df.iloc[i]['close'], 'entry_pct_chg': pct,
                'consolidation_low': cons_low,
                'stop_loss': cons_low * 0.98,
                'wave_gain': wave_info[2] if wave_info else 0
            })
    return signals

def simulate_trade(df, signal_idx, signal):
    n = len(df)
    entry_price = signal['entry_price']
    stop_loss = signal['stop_loss']
    peak_since_entry = entry_price
    for day_offset in range(1, min(CONFIG['max_hold_days'] + 1, n - signal_idx)):
        idx = signal_idx + day_offset
        if idx >= n: break
        close, low, high = df.iloc[idx]['close'], df.iloc[idx]['low'], df.iloc[idx]['high']
        peak_since_entry = max(peak_since_entry, high)
        if low <= stop_loss:
            return {'hold_days': day_offset, 'exit_price': stop_loss, 'exit_reason': 'stop_loss',
                    'pnl_pct': (stop_loss - entry_price) / entry_price,
                    'max_profit': (peak_since_entry - entry_price) / entry_price,
                    'max_drawdown': (low - entry_price) / entry_price}
        if day_offset > 2:
            drawdown_from_peak = (peak_since_entry - close) / peak_since_entry
            total_gain = (peak_since_entry - entry_price) / entry_price
            if drawdown_from_peak > CONFIG['trailing_stop_pct'] and total_gain > 0.10:
                return {'hold_days': day_offset, 'exit_price': close, 'exit_reason': 'trailing_stop',
                        'pnl_pct': (close - entry_price) / entry_price, 'max_profit': total_gain,
                        'max_drawdown': (df.iloc[signal_idx+1:idx+1]['low'].min() - entry_price) / entry_price}
        if day_offset >= CONFIG['max_hold_days']:
            return {'hold_days': day_offset, 'exit_price': close, 'exit_reason': 'time_exit',
                    'pnl_pct': (close - entry_price) / entry_price,
                    'max_profit': (peak_since_entry - entry_price) / entry_price,
                    'max_drawdown': (df.iloc[signal_idx+1:idx+1]['low'].min() - entry_price) / entry_price}
    last_idx = min(signal_idx + CONFIG['max_hold_days'], n - 1)
    return {'hold_days': last_idx - signal_idx, 'exit_price': df.iloc[last_idx]['close'],
            'exit_reason': 'final',
            'pnl_pct': (df.iloc[last_idx]['close'] - entry_price) / entry_price,
            'max_profit': (peak_since_entry - entry_price) / entry_price, 'max_drawdown': 0}

# ==================== 回测主流程 ====================
def run_backtest(dl, codes, start_date, end_date, max_stocks=None):
    if max_stocks:
        random.seed(42)
        selected_codes = random.sample(codes, min(max_stocks, len(codes)))
    else:
        selected_codes = codes
    print(f"\n使用 {len(selected_codes)} 只股票进行回测")
    print(f"回测区间: {start_date} 至 {end_date}")

    all_trades = []
    industry_func = get_industry_mapping()
    industry_groups = {}
    for code in selected_codes:
        industry = industry_func(code)
        if industry not in industry_groups: industry_groups[industry] = []
        industry_groups[industry].append(code)

    # 从本地批量加载数据
    print("\n从本地批量加载股票数据...")
    fetch_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=150)).strftime('%Y-%m-%d')
    stock_data_cache = dl.get_kline_batch(selected_codes, fetch_start, end_date)
    print(f"成功加载 {len(stock_data_cache)}/{len(selected_codes)} 只股票数据")

    # 板块动量缓存
    sector_cache = {}
    def get_sector_momentum_cached(industry):
        if industry not in sector_cache:
            sector_codes = industry_groups.get(industry, [])
            def get_kline(c):
                return stock_data_cache.get(c)
            sector_cache[industry] = compute_sector_momentum(sector_codes, get_kline, CONFIG['sector_momentum_window'])
        return sector_cache[industry]

    print("\n执行回测...")
    for i, code in enumerate(selected_codes):
        if (i + 1) % 20 == 0:
            print(f"进度: {i+1}/{len(selected_codes)}")
        if code not in stock_data_cache: continue
        df = stock_data_cache[code]
        mask = df['date'] >= pd.to_datetime(start_date)
        df_trade = df[mask].reset_index(drop=True)
        if len(df_trade) < 20: continue

        waves = detect_first_wave(df)
        for wave in waves:
            wave_start, wave_end, wave_gain, wave_days = wave
            consolidation = detect_consolidation(df, wave_end)
            if consolidation is None or not consolidation['is_valid']: continue
            signals = detect_weak_to_strong_signals(df, consolidation, wave)
            for signal in signals:
                sig_date = signal['date']
                if sig_date < pd.to_datetime(start_date) or sig_date > pd.to_datetime(end_date): continue
                try:
                    trade_idx = df_trade[df_trade['date'] == sig_date].index[0]
                except IndexError: continue

                industry = industry_func(code)
                sector_momentum, sector_strong = get_sector_momentum_cached(industry)
                trade = simulate_trade(df_trade, trade_idx, signal)
                trade.update({
                    'stock_code': code, 'signal_date': str(sig_date.date()),
                    'signal_type': signal['type'], 'entry_price': signal['entry_price'],
                    'wave_gain': signal['wave_gain'],
                    'consolidation_drawdown': consolidation['max_drawdown'],
                    'industry': industry, 'sector_momentum': sector_momentum,
                    'sector_strong': sector_strong,
                })
                all_trades.append(trade)
    return all_trades

# ==================== 结果分析 ====================
def analyze_results(trades):
    if not trades:
        print("\n⚠ 没有产生交易信号！")
        return pd.DataFrame()
    df = pd.DataFrame(trades)
    print("\n" + "="*70)
    print("弱转强策略回测结果")
    print("="*70)
    total = len(df)
    wins = len(df[df['pnl_pct'] > 0])
    losses = len(df[df['pnl_pct'] <= 0])
    win_rate = wins / total * 100 if total > 0 else 0
    avg_profit = df[df['pnl_pct'] > 0]['pnl_pct'].mean() * 100 if wins > 0 else 0
    avg_loss = df[df['pnl_pct'] <= 0]['pnl_pct'].mean() * 100 if losses > 0 else 0
    pl_ratio = abs(avg_profit / avg_loss) if avg_loss != 0 else 0
    total_return = df['pnl_pct'].sum() * 100
    max_win = df['pnl_pct'].max() * 100
    max_loss = df['pnl_pct'].min() * 100
    avg_hold = df['hold_days'].mean()
    print(f"\n📊 基础统计:")
    print(f"  总交易次数: {total}")
    print(f"  盈利次数: {wins} | 亏损次数: {losses}")
    print(f"  胜率: {win_rate:.1f}%")
    print(f"  平均盈利: {avg_profit:.2f}% | 平均亏损: {avg_loss:.2f}%")
    print(f"  盈亏比: {pl_ratio:.2f}")
    print(f"  累计收益率(等权): {total_return:.2f}%")
    print(f"  最大单笔盈利: {max_win:.2f}% | 最大单笔亏损: {max_loss:.2f}%")
    print(f"  平均持仓天数: {avg_hold:.1f}")
    print(f"\n📈 按信号类型:")
    for sig in sorted(df['signal_type'].unique()):
        sub = df[df['signal_type'] == sig]
        print(f"  {sig}: {len(sub)}笔 胜率{(sub['pnl_pct']>0).mean()*100:.1f}% 平均{sub['pnl_pct'].mean()*100:+.2f}%")
    print(f"\n🔗 板块联动效果:")
    for label, mask in [('板块强势', df['sector_strong']==True), ('板块弱势', df['sector_strong']==False)]:
        sub = df[mask]
        if len(sub) > 0:
            print(f"  {label}: {len(sub)}笔 胜率{(sub['pnl_pct']>0).mean()*100:.1f}% 平均{sub['pnl_pct'].mean()*100:+.2f}%")
    print(f"\n⏱ 持仓天数分布:")
    df['_hold_bin'] = pd.cut(df['hold_days'], bins=[0,3,5,10,999], labels=['1-3天','4-5天','6-10天','11天+'])
    for b in df['_hold_bin'].cat.categories:
        sub = df[df['_hold_bin']==b]
        if len(sub) > 0:
            print(f"  {b}: {len(sub)}笔 胜率{(sub['pnl_pct']>0).mean()*100:.1f}% 平均{sub['pnl_pct'].mean()*100:+.2f}%")
    print(f"\n🚪 退出原因:")
    for r in df['exit_reason'].unique():
        sub = df[df['exit_reason']==r]
        print(f"  {r}: {len(sub)}笔 胜率{(sub['pnl_pct']>0).mean()*100:.1f}% 平均{sub['pnl_pct'].mean()*100:+.2f}%")
    expectancy = (win_rate/100*avg_profit) + ((100-win_rate)/100*avg_loss)
    print(f"\n💰 每笔期望值: {expectancy:+.2f}%")
    is_win = df['pnl_pct'] > 0
    max_consec, cur = 0, 0
    for v in is_win:
        if not v: cur += 1; max_consec = max(max_consec, cur)
        else: cur = 0
    print(f"  最大连续亏损: {max_consec}次")
    cum = (1 + df['pnl_pct']).cumprod() - 1
    peak = cum.cummax()
    mdd = ((cum - peak) / (peak + 1)).min() * 100
    print(f"\n📉 风险指标:")
    print(f"  最大回撤: {mdd:.2f}%")
    print(f"  最终累计收益: {cum.iloc[-1]*100:+.2f}%")
    print(f"  夏普比率(近似): {df['pnl_pct'].mean()/df['pnl_pct'].std()*np.sqrt(252):.2f}")
    return df

# ==================== 主程序 ====================
if __name__ == '__main__':
    print("="*70)
    print("弱转强(Weak-to-Strong) 量化回测系统 v3（本地缓存）")
    print("="*70)

    bs.login()
    dl = get_data_layer()

    # 确保有数据
    stats = dl.get_cache_stats()
    if stats['stocks_with_data'] == 0:
        print("\n本地无数据，先初始化...")
        stock_list = dl.update_stock_list()
        codes = stock_list['code'].tolist()
        dl.batch_update(codes, verbose=True, total=len(codes))
    else:
        print("\n增量更新数据...")
        try:
            stock_list = dl.update_stock_list()
            codes = stock_list['code'].tolist()
            dl.batch_update(codes, verbose=True, total=len(codes))
        except Exception:
            print("  API更新失败，使用本地已有数据...")
            stock_list = dl.get_stock_list()
            codes = stock_list['code'].tolist()

    stats = dl.get_cache_stats()
    print(f"\n数据状态: {stats['stocks_with_data']} 只有数据, {stats['total_rows']:,} 行")

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=365)
    start_str = start_dt.strftime('%Y-%m-%d')
    end_str = end_dt.strftime('%Y-%m-%d')

    trades = run_backtest(dl, codes, start_str, end_str)

    if trades:
        results_df = analyze_results(trades)
        results_df.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_results.csv'),
                         index=False, encoding='utf-8-sig')
        print(f"\n✅ 详细结果已保存: backtest_results.csv ({len(trades)}笔交易)")
    else:
        print("\n⚠ 回测未产生有效交易")

    bs.logout()
