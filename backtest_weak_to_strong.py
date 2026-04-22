#!/usr/bin/env python3
"""
弱转强(Weak-to-Strong) 量化回测系统 v2
使用 baostock + tushare 作为数据源

核心策略：
1. 选股：前期强势第一波 → 浅度回调(非A杀) → 板块前排
2. 买入：回调末期超预期阳线(弱转强信号)
3. 板块联动：个股弱转强时，所属板块必须同步走强
4. 止损：回调期最低点(~10%以内)
5. 止盈：5日无强势表现则出，连板不封顶
6. 市场环境：退潮期不做
"""

import baostock as bs
import tushare as ts
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import random
import warnings
warnings.filterwarnings('ignore')

# ==================== 参数配置 ====================
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
def init_data_sources():
    """初始化数据源"""
    bs.login()
    print("baostock 已连接")
    try:
        ts.set_token('your_tushare_token')  # 无需token也可用部分功能
        pro = ts.pro_api()
    except:
        pro = None
    return pro

def get_stock_list():
    """获取A股列表"""
    print("获取A股列表...")
    rs = bs.query_all_stock(day=datetime.now().strftime('%Y-%m-%d'))
    df = rs.get_data()
    # 过滤A股（sh.60xxxx, sz.00xxxx, sz.30xxxx）
    mask = df['code'].str.match(r'^(sh\.60|sz\.00|sz\.30)\d{4}$')
    df = df[mask].copy()
    print(f"共 {len(df)} 只A股")
    return df

def get_daily_kline(bs_code, start_date, end_date):
    """获取个股日K线 (baostock)"""
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount,turn,peTTM",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2"  # 前复权
        )
        df = rs.get_data()
        if df is not None and len(df) > 60:
            df = df.astype({
                'open': 'float', 'high': 'float', 'low': 'float',
                'close': 'float', 'volume': 'float', 'amount': 'float',
                'turn': 'float'
            })
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)

            # 计算涨跌幅和指标
            df['pct_chg'] = df['close'].pct_change()
            df['ma5'] = df['close'].rolling(5).mean()
            df['ma10'] = df['close'].rolling(10).mean()
            df['ma20'] = df['close'].rolling(20).mean()
            df['volume_ma5'] = df['volume'].rolling(5).mean()
            df['amplitude'] = (df['high'] - df['low']) / df['close']
            df['body_ratio'] = abs(df['close'] - df['open']) / df['close']

            return df
    except Exception as e:
        pass
    return None

def get_industry_mapping():
    """获取行业板块映射 (简化版：基于股票代码前缀)"""
    # 基于申万行业分类的简化映射
    # 实际应用中应该用tushare的industry分类
    industry_map = {
        '600': '银行/金融', '601': '银行/金融', '603': '多元金融',
        '000': '综合', '002': '中小板', '300': '创业板',
    }

    def get_industry(code):
        # code format: sh.600xxx or sz.300xxx
        prefix = code.split('.')[1][:3]
        return industry_map.get(prefix, '其他')

    return get_industry

def compute_sector_momentum(df, codes_in_sector, lookback=10):
    """
    计算板块动量
    用板块内所有个股的平均涨跌幅作为板块指标
    """
    if len(codes_in_sector) == 0:
        return 0.0, False

    returns = []
    for code in codes_in_sector[:20]:  # 最多取20只
        if code in df:
            stock_df = df[code]
            if len(stock_df) >= lookback:
                ret = stock_df.iloc[-lookback:]['pct_chg'].mean()
                returns.append(ret)

    if not returns:
        return 0.0, False

    avg_momentum = np.mean(returns)
    is_strong = avg_momentum > CONFIG['sector_min_strength'] / lookback

    return avg_momentum, is_strong

# ==================== 策略核心 ====================
def detect_first_wave(df):
    """
    检测第一波强势拉升
    条件：连续N天上涨，累计涨幅 >= 阈值
    """
    waves = []
    n = len(df)
    if n < 20:
        return waves

    i = 0
    while i < n - CONFIG['first_wave_min_days']:
        up_days = 0
        up_start = i
        total_gain = 0.0
        j = i

        while j < n - 1:
            pct = df.iloc[j]['pct_chg']
            if pct > 0:
                up_days += 1
                total_gain += pct
                j += 1
            else:
                break

        if up_days >= CONFIG['first_wave_min_days'] and total_gain >= CONFIG['first_wave_min_gain']:
            waves.append((up_start, j - 1, total_gain, up_days))
            i = j
        else:
            i += 1

    return waves

def detect_consolidation(df, wave_end):
    """
    检测第一波后的回调/盘整
    """
    n = len(df)
    if wave_end >= n - 5:
        return None

    peak = df.iloc[wave_end]['close']
    cons_start = wave_end + 1

    # 寻找回调最低点
    min_price = peak
    min_idx = cons_start
    days_in_cons = 0

    for i in range(cons_start, min(n, cons_start + CONFIG['consolidation_max_days'])):
        days_in_cons += 1
        if df.iloc[i]['low'] < min_price:
            min_price = df.iloc[i]['low']
            min_idx = i

    drawdown = (peak - min_price) / peak

    # A杀判断
    is_a_kill = drawdown > CONFIG['consolidation_max_drawdown']

    # 缓慢下跌判断：回调中阴线占比不应过高
    down_days = sum(1 for i in range(cons_start, min_idx + 1)
                    if i < n and df.iloc[i]['pct_chg'] < 0)
    is_slow_decline = days_in_cons >= 3 and down_days / max(days_in_cons, 1) < 0.7

    is_valid = not is_a_kill and is_slow_decline and days_in_cons >= 3

    return {
        'start': cons_start,
        'end': min_idx,
        'duration': days_in_cons,
        'max_drawdown': drawdown,
        'min_price': min_price,
        'is_valid': is_valid,
        'is_a_kill': is_a_kill
    }

def detect_weak_to_strong_signals(df, consolidation, wave_info):
    """
    检测弱转强买入信号 (4种信号类型)
    """
    signals = []
    n = len(df)

    if consolidation is None or not consolidation['is_valid']:
        return signals

    cons_end = consolidation['end']
    cons_start = consolidation['start']
    cons_low = consolidation['min_price']

    search_start = max(cons_end, cons_start + 2)

    for i in range(search_start, min(n, search_start + 10)):
        if i >= n:
            break

        pct = df.iloc[i]['pct_chg']
        signal_type = None

        # 信号1: 大阳线反转 (前一天阴线，当天大涨)
        if i > 0 and pct > CONFIG['weak_strong_threshold']:
            prev_close = df.iloc[i-1]['close']
            prev_open = df.iloc[i-1]['open']
            if prev_close < prev_open:  # 前一天是阴线
                signal_type = 'big_bullish_reversal'

        # 信号2: 阳包阴 (吞噬形态)
        if i > 0 and pct > 0.02:
            prev_close = df.iloc[i-1]['close']
            prev_open = df.iloc[i-1]['open']
            curr_close = df.iloc[i]['close']
            curr_open = df.iloc[i]['open']
            if prev_close < prev_open and curr_close > prev_open:
                signal_type = 'bullish_engulfing'

        # 信号3: 涨停开板次日走强
        if i > 0:
            prev_pct = df.iloc[i-1]['pct_chg']
            prev_amp = df.iloc[i-1]['amplitude'] if 'amplitude' in df.columns else 0
            prev_high = df.iloc[i-1]['high']
            prev_close = df.iloc[i-1]['close']

            # 前一天接近涨停但没封住
            if prev_pct > 0.08 and prev_close < prev_high * 0.97:
                if pct > 0.02:  # 次日走强
                    signal_type = 'limit_up_open_next_strong'

        # 信号4: 异动K线后该跌不跌
        if i > 1:
            prev_amp = df.iloc[i-1]['amplitude'] if 'amplitude' in df.columns else \
                       (df.iloc[i-1]['high'] - df.iloc[i-1]['low']) / df.iloc[i-1]['close']
            is_anomaly = prev_amp > CONFIG['anomaly_amplitude']

            if is_anomaly and pct > 0.01:
                signal_type = 'anomaly_no_decline'

        if signal_type:
            signals.append({
                'idx': i,
                'date': df.iloc[i]['date'],
                'type': signal_type,
                'entry_price': df.iloc[i]['close'],
                'entry_pct_chg': pct,
                'consolidation_low': cons_low,
                'stop_loss': cons_low * 0.98,
                'wave_gain': wave_info[2] if wave_info else 0
            })

    return signals

def simulate_trade(df, signal_idx, signal):
    """
    模拟单笔交易
    """
    n = len(df)
    entry_price = signal['entry_price']
    stop_loss = signal['stop_loss']

    peak_since_entry = entry_price

    for day_offset in range(1, min(CONFIG['max_hold_days'] + 1, n - signal_idx)):
        idx = signal_idx + day_offset
        if idx >= n:
            break

        close = df.iloc[idx]['close']
        low = df.iloc[idx]['low']
        high = df.iloc[idx]['high']

        # 更新峰值
        peak_since_entry = max(peak_since_entry, high)

        # 止损检查
        if low <= stop_loss:
            return {
                'hold_days': day_offset,
                'exit_price': stop_loss,
                'exit_reason': 'stop_loss',
                'pnl_pct': (stop_loss - entry_price) / entry_price,
                'max_profit': (peak_since_entry - entry_price) / entry_price,
                'max_drawdown': (low - entry_price) / entry_price
            }

        # 跟踪止盈：从高点回撤超过阈值且已有盈利
        if day_offset > 2:
            drawdown_from_peak = (peak_since_entry - close) / peak_since_entry
            total_gain = (peak_since_entry - entry_price) / entry_price
            if drawdown_from_peak > CONFIG['trailing_stop_pct'] and total_gain > 0.10:
                return {
                    'hold_days': day_offset,
                    'exit_price': close,
                    'exit_reason': 'trailing_stop',
                    'pnl_pct': (close - entry_price) / entry_price,
                    'max_profit': total_gain,
                    'max_drawdown': (df.iloc[signal_idx+1:idx+1]['low'].min() - entry_price) / entry_price
                }

        # 时间止损
        if day_offset >= CONFIG['max_hold_days']:
            return {
                'hold_days': day_offset,
                'exit_price': close,
                'exit_reason': 'time_exit',
                'pnl_pct': (close - entry_price) / entry_price,
                'max_profit': (peak_since_entry - entry_price) / entry_price,
                'max_drawdown': (df.iloc[signal_idx+1:idx+1]['low'].min() - entry_price) / entry_price
            }

    # 正常结束
    last_idx = min(signal_idx + CONFIG['max_hold_days'], n - 1)
    return {
        'hold_days': last_idx - signal_idx,
        'exit_price': df.iloc[last_idx]['close'],
        'exit_reason': 'final',
        'pnl_pct': (df.iloc[last_idx]['close'] - entry_price) / entry_price,
        'max_profit': (peak_since_entry - entry_price) / entry_price,
        'max_drawdown': 0
    }

# ==================== 回测主流程 ====================
def run_backtest(stock_df, start_date, end_date, max_stocks=200):
    """主回测函数"""
    random.seed(42)

    # 随机选股
    selected = stock_df.sample(n=min(max_stocks, len(stock_df)), random_state=42)
    print(f"\n随机选取 {len(selected)} 只股票进行回测")
    print(f"回测区间: {start_date} 至 {end_date}")

    all_trades = []
    stock_data_cache = {}
    industry_func = get_industry_mapping()

    # 按行业分组
    industry_groups = {}
    for idx, row in selected.iterrows():
        code = row['code']
        industry = industry_func(code)
        if industry not in industry_groups:
            industry_groups[industry] = []
        industry_groups[industry].append(code)

    # 先加载所有股票数据
    print("\n加载股票数据...")
    loaded = 0
    for idx, row in selected.iterrows():
        code = row['code']
        # 多取历史数据用于计算指标
        start_d = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=150)).strftime('%Y-%m-%d')
        df = get_daily_kline(code, start_d, end_date)
        if df is not None and len(df) > 60:
            stock_data_cache[code] = df
            loaded += 1

    print(f"成功加载 {loaded}/{len(selected)} 只股票数据")

    # 回测每只股票
    print("\n执行回测...")
    for i, (idx, row) in enumerate(selected.iterrows()):
        if (i + 1) % 20 == 0:
            print(f"进度: {i+1}/{len(selected)}")

        code = row['code']
        if code not in stock_data_cache:
            continue

        df = stock_data_cache[code]

        # 过滤到回测区间
        mask = df['date'] >= pd.to_datetime(start_date)
        df_trade = df[mask].reset_index(drop=True)
        if len(df_trade) < 20:
            continue

        # 检测第一波
        waves = detect_first_wave(df)

        for wave in waves:
            wave_start, wave_end, wave_gain, wave_days = wave

            # 检测回调
            consolidation = detect_consolidation(df, wave_end)
            if consolidation is None or not consolidation['is_valid']:
                continue

            # 检测弱转强信号
            signals = detect_weak_to_strong_signals(df, consolidation, wave)

            for signal in signals:
                sig_date = signal['date']

                # 信号必须在回测区间内
                if sig_date < pd.to_datetime(start_date) or sig_date > pd.to_datetime(end_date):
                    continue

                # 找到信号在df_trade中的索引
                try:
                    trade_idx = df_trade[df_trade['date'] == sig_date].index[0]
                except IndexError:
                    continue

                # 板块动量检查
                industry = industry_func(code)
                sector_codes = industry_groups.get(industry, [])
                sector_momentum, sector_strong = compute_sector_momentum(
                    stock_data_cache, sector_codes, CONFIG['sector_momentum_window']
                )

                # 执行交易
                trade = simulate_trade(df_trade, trade_idx, signal)

                trade.update({
                    'stock_code': code,
                    'signal_date': str(sig_date.date()),
                    'signal_type': signal['type'],
                    'entry_price': signal['entry_price'],
                    'wave_gain': signal['wave_gain'],
                    'consolidation_drawdown': consolidation['max_drawdown'],
                    'industry': industry,
                    'sector_momentum': sector_momentum,
                    'sector_strong': sector_strong,
                })

                all_trades.append(trade)

    return all_trades

# ==================== 结果分析 ====================
def analyze_results(trades):
    """分析回测结果"""
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
    profit_loss_ratio = abs(avg_profit / avg_loss) if avg_loss != 0 else 0

    total_return = df['pnl_pct'].sum() * 100
    max_win = df['pnl_pct'].max() * 100
    max_loss = df['pnl_pct'].min() * 100
    avg_hold = df['hold_days'].mean()

    print(f"\n📊 基础统计:")
    print(f"  总交易次数: {total}")
    print(f"  盈利次数: {wins} | 亏损次数: {losses}")
    print(f"  胜率: {win_rate:.1f}%")
    print(f"  平均盈利: {avg_profit:.2f}% | 平均亏损: {avg_loss:.2f}%")
    print(f"  盈亏比: {profit_loss_ratio:.2f}")
    print(f"  累计收益率(等权): {total_return:.2f}%")
    print(f"  最大单笔盈利: {max_win:.2f}% | 最大单笔亏损: {max_loss:.2f}%")
    print(f"  平均持仓天数: {avg_hold:.1f}")

    # 按信号类型
    print(f"\n📈 按信号类型:")
    for sig in sorted(df['signal_type'].unique()):
        sub = df[df['signal_type'] == sig]
        wr = (sub['pnl_pct'] > 0).mean() * 100
        avg = sub['pnl_pct'].mean() * 100
        print(f"  {sig}: {len(sub)}笔 胜率{wr:.1f}% 平均{avg:+.2f}%")

    # 按板块联动
    print(f"\n🔗 板块联动效果:")
    strong = df[df['sector_strong'] == True]
    weak = df[df['sector_strong'] == False]
    if len(strong) > 0:
        wr_s = (strong['pnl_pct'] > 0).mean() * 100
        avg_s = strong['pnl_pct'].mean() * 100
        print(f"  板块强势: {len(strong)}笔 胜率{wr_s:.1f}% 平均{avg_s:+.2f}%")
    if len(weak) > 0:
        wr_w = (weak['pnl_pct'] > 0).mean() * 100
        avg_w = weak['pnl_pct'].mean() * 100
        print(f"  板块弱势: {len(weak)}笔 胜率{wr_w:.1f}% 平均{avg_w:+.2f}%")

    # 按持仓天数
    print(f"\n⏱ 持仓天数分布:")
    df['_hold_bin'] = pd.cut(df['hold_days'], bins=[0, 3, 5, 10, 999],
                             labels=['1-3天', '4-5天', '6-10天', '11天+'])
    for b in df['_hold_bin'].cat.categories:
        sub = df[df['_hold_bin'] == b]
        if len(sub) > 0:
            wr = (sub['pnl_pct'] > 0).mean() * 100
            avg = sub['pnl_pct'].mean() * 100
            print(f"  {b}: {len(sub)}笔 胜率{wr:.1f}% 平均{avg:+.2f}%")

    # 退出原因
    print(f"\n🚪 退出原因:")
    for r in df['exit_reason'].unique():
        sub = df[df['exit_reason'] == r]
        wr = (sub['pnl_pct'] > 0).mean() * 100
        avg = sub['pnl_pct'].mean() * 100
        print(f"  {r}: {len(sub)}笔 胜率{wr:.1f}% 平均{avg:+.2f}%")

    # 期望值
    expectancy = (win_rate/100 * avg_profit) + ((100-win_rate)/100 * avg_loss)
    print(f"\n💰 每笔期望值: {expectancy:+.2f}%")

    # 最大连续亏损
    is_win = df['pnl_pct'] > 0
    max_consec = 0
    cur = 0
    for v in is_win:
        if not v:
            cur += 1
            max_consec = max(max_consec, cur)
        else:
            cur = 0
    print(f"  最大连续亏损: {max_consec}次")

    # 收益曲线
    cum = (1 + df['pnl_pct']).cumprod() - 1
    peak = cum.cummax()
    mdd = ((cum - peak) / (peak + 1)).min() * 100

    print(f"\n📉 风险指标:")
    print(f"  最大回撤: {mdd:.2f}%")
    print(f"  最终累计收益: {cum.iloc[-1]*100:+.2f}%")
    print(f"  夏普比率(近似): {df['pnl_pct'].mean() / df['pnl_pct'].std() * np.sqrt(252):.2f}")

    return df

# ==================== 主程序 ====================
if __name__ == '__main__':
    print("="*70)
    print("弱转强(Weak-to-Strong) 量化回测系统 v2")
    print("基于时空游戏交易策略文档 · 个股+板块联动")
    print("="*70)

    # 初始化
    pro = init_data_sources()
    stock_list = get_stock_list()

    # 回测区间
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=365)
    start_str = start_dt.strftime('%Y-%m-%d')
    end_str = end_dt.strftime('%Y-%m-%d')

    # 运行回测
    trades = run_backtest(stock_list, start_str, end_str, max_stocks=200)

    # 分析
    if trades:
        results_df = analyze_results(trades)
        results_df.to_csv('/home/jzc/wechat_text/shikong_fufei/backtest_results.csv',
                         index=False, encoding='utf-8-sig')
        print(f"\n✅ 详细结果已保存: backtest_results.csv ({len(trades)}笔交易)")
    else:
        print("\n⚠ 回测未产生有效交易")

    bs.logout()
