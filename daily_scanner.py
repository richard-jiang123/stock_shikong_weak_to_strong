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
import unicodedata
warnings = __import__('warnings'); warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer
from strategy_config import StrategyConfig
from pick_tracker import PickTracker
from normalizer import ScoreNormalizer

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
        sig = '阳包阴 '
    elif tp > CONFIG['weak_strong_threshold'] and df.iloc[ti-1]['close'] < df.iloc[ti-1]['open']:
        sig = '大阳反转'
    elif df.iloc[ti-1]['pct_chg'] > 0.08 and df.iloc[ti-1]['close'] < df.iloc[ti-1]['high']*0.97 and tp > 0.02:
        sig = '烂板次日'
    if not sig: return None
    # === 评分（拆分为 score_details）===
    score_base = 5
    score_wave_gain = 0
    score_shallow_dd = 0
    score_day_gain = 0
    score_volume = 0
    score_ma_bull = 0
    score_signal_bonus = 0
    reasons = []

    # 波段涨幅评分
    if wg > 0.30: score_wave_gain = 20; reasons.append(f"一波{wg*100:.0f}%")
    elif wg > 0.20: score_wave_gain = 10; reasons.append(f"一波{wg*100:.0f}%")

    # 回调深度评分
    if dd < 0.08: score_shallow_dd = 15; reasons.append(f"浅调{dd*100:.0f}%")
    elif dd < 0.15: score_shallow_dd = 10; reasons.append(f"调{dd*100:.0f}%")

    # 当日涨幅评分
    if tp > 0.07: score_day_gain = 15
    elif tp > 0.05: score_day_gain = 10
    elif tp > 0.03: score_day_gain = 5

    # 放量评分
    vr = df.iloc[ti]['volume'] / max(df.iloc[ti]['volume_ma5'], 1)
    if vr > 2: score_volume = 10; reasons.append(f"放量{vr:.1f}x")
    elif vr > 1.5: score_volume = 5

    # 多头排列评分
    if df.iloc[ti]['ma5'] > df.iloc[ti]['ma10'] > df.iloc[ti]['ma20']: score_ma_bull = 10; reasons.append("多头")
    elif df.iloc[ti]['ma5'] > df.iloc[ti]['ma10']: score_ma_bull = 5

    # 异动信号额外加分
    if sig == '异动不跌': score_signal_bonus = 10

    # 计算总评分
    total_score = score_base + score_wave_gain + score_shallow_dd + score_day_gain + score_volume + score_ma_bull + score_signal_bonus

    return {
        'sig': sig,
        'score': total_score,
        'score_details': {
            'score_base': score_base,
            'score_wave_gain': score_wave_gain,
            'score_shallow_dd': score_shallow_dd,
            'score_day_gain': score_day_gain,
            'score_volume': score_volume,
            'score_ma_bull': score_ma_bull,
            'score_signal_bonus': score_signal_bonus,
        },
        'reasons': ' | '.join(reasons),
        'wg': wg, 'dd': dd, 'tp': tp, 'vr': vr,
        'sl': mn*0.98, 'ep': df.iloc[ti]['close'], 'cons_low': mn
    }


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
    """Build dated XLSX file path: YYYYMMDD_today_signals.xlsx"""
    return os.path.join(script_dir, date_str.replace('-', '') + '_today_signals.xlsx')


def _get_industry_map(dl):
    """从数据库获取股票行业分类"""
    return dl.get_industry_map()


def _compute_sector_momentum(codes_in_sector, kline_cache, lookback=10):
    """计算板块动量：板块内股票最近lookback天的平均涨跌幅

    Args:
        codes_in_sector: 板块内股票代码列表
        kline_cache: K线数据缓存
        lookback: 回看天数

    Returns:
        (avg_momentum, is_strong): 平均动量和是否强势板块
    """
    if len(codes_in_sector) == 0:
        return 0.0, False

    returns = []
    for code in codes_in_sector[:20]:  # 取前20只代表股票
        if code not in kline_cache:
            continue
        df = kline_cache[code]
        if df is not None and len(df) >= lookback:
            # 最近lookback天的平均涨跌幅
            avg_ret = df.iloc[-lookback:]['pct_chg'].mean()
            returns.append(avg_ret)

    if not returns:
        return 0.0, False

    avg_momentum = np.mean(returns)
    # 强势板块定义：平均涨幅 > 0.5%/天
    is_strong = avg_momentum > 0.005
    return avg_momentum, is_strong


def _scan_core(dl, codes, regime_cache, name_map, industry_map, start_date, end_date, verbose=True):
    """
    Run pattern scan for data up to end_date. Returns list of result dicts.
    Optimized: batch load data first, then fast iteration.
    Includes sector momentum scoring and score normalization.
    """
    # 提前过滤ST股票
    filtered_codes = [c for c in codes if 'ST' not in name_map.get(c, '').upper()]
    if verbose:
        print(f"  扫描 {len(filtered_codes)} 只股票（已过滤ST）")
        sys.stdout.flush()

    # 批量加载K线数据
    kline_cache = dl.get_kline_batch(filtered_codes, start_date, end_date)

    # 按行业分组股票代码
    industry_groups = {}
    for code in filtered_codes:
        industry = industry_map.get(code, '其他')
        if industry not in industry_groups:
            industry_groups[industry] = []
        industry_groups[industry].append(code)

    # 计算各行业板块动量缓存
    sector_momentum_cache = {}
    if verbose and len(industry_groups) > 0:
        print(f"  计算板块动量: {len(industry_groups)} 个行业")
        sys.stdout.flush()

    for industry, group_codes in industry_groups.items():
        momentum, is_strong = _compute_sector_momentum(group_codes, kline_cache, lookback=10)
        sector_momentum_cache[industry] = {'momentum': momentum, 'strong': is_strong}

    # 初始化归一化器和权重（复用已有数据层实例）
    normalizer = ScoreNormalizer(data_layer=dl)
    cfg = StrategyConfig()
    weights = cfg.get_weights()

    results = []
    for i, code in enumerate(filtered_codes):
        if code not in kline_cache:
            continue
        df = kline_cache[code]
        r = detect_pattern(df)
        if r:
            last = df.iloc[-1]
            index_code = dl.code_to_index(code).split('.')[1]
            industry = industry_map.get(code, '')

            # 获取板块动量状态
            sector_info = sector_momentum_cache.get(industry, {'momentum': 0, 'strong': False})
            sector_strong = sector_info['strong']

            # 获取评分明细
            score_details = r.get('score_details', {})
            score_sector = 5 if sector_strong else 0

            # 构建归一化输入（使用 day_gain 对应数据库字段 score_day_gain）
            scores_dict = {
                'day_gain': score_details.get('score_day_gain', 0),
                'wave_gain': score_details.get('score_wave_gain', 0),
                'shallow_dd': score_details.get('score_shallow_dd', 0),
                'volume': score_details.get('score_volume', 0),
                'ma_bull': score_details.get('score_ma_bull', 0),
                'sector': score_sector,
                'signal_bonus': score_details.get('score_signal_bonus', 0),
            }

            # 应用归一化
            normalized_score, norm_meta = normalizer.normalize_scores(scores_dict, weights)
            score_base = score_details.get('score_base', 5)
            total_score = score_base + normalized_score

            # 更新原因字符串
            reasons = r['reasons']
            if sector_strong:
                if reasons:
                    reasons = reasons + ' | 强势板块'
                else:
                    reasons = '强势板块'

            results.append({
                '代码': code.split('.')[1], '名称': name_map.get(code, ''),
                '现价': last['close'],
                '涨幅': last['pct_chg'], '信号': r['sig'],
                '评分': total_score,
                'score_normalized': normalized_score,
                'score_raw': r['score'] - score_base,  # 原始总分不含 base
                'normalization_meta': norm_meta,
                '波段涨幅': r['wg'],
                '回调': r['dd'], '量比': r['vr'],
                '止损位': r['sl'], '入场价': r['ep'],
                '指数': index_code,
                '市场环境': {'bull': '上升期', 'range': '震荡期', 'bear': '退潮期'}.get(
                    regime_cache.get(dl.code_to_index(code), 'range'), '震荡期'),
                '行业': industry,
                '板块强势': sector_strong,
                '原因': reasons,
                # 评分明细字段（用于 pick_tracker 记录）
                'score_base': score_base,
                'score_day_gain': score_details.get('score_day_gain', 0),
                'score_wave_gain': score_details.get('score_wave_gain', 0),
                'score_shallow_dd': score_details.get('score_shallow_dd', 0),
                'score_volume': score_details.get('score_volume', 0),
                'score_ma_bull': score_details.get('score_ma_bull', 0),
                'score_sector': score_sector,
                'score_signal_bonus': score_details.get('score_signal_bonus', 0),
            })
        if verbose and ((i+1) % 500 == 0 or i + 1 == len(filtered_codes)):
            print(f"  扫描 {i+1}/{len(filtered_codes)} | 命中 {len(results)}")
            sys.stdout.flush()

    results.sort(key=lambda x: x['评分'], reverse=True)
    return results


def _display_width(s):
    """计算字符串在等宽字体终端下的显示宽度"""
    width = 0
    for ch in s:
        if ch == '\t':
            width += 8 - (width % 8)   # 简单处理制表符
        else:
            # East Asian Width 为 W 或 F 的字符占2个宽度
            ea = unicodedata.east_asian_width(ch)
            width += 2 if ea in ('W', 'F') else 1
    return width

def _pad_str(s, width, align='<'):
    """将字符串 s 填充到给定显示宽度"""
    cur = _display_width(s)
    if cur >= width:
        return s
    pad = width - cur
    if align == '<':
        return s + ' ' * pad
    elif align == '>':
        return ' ' * pad + s
    else:   # '^'
        left = pad // 2
        return ' ' * left + s + ' ' * (pad - left)

# ANSI 颜色代码
RED = '\033[91m'    # 红色（正值/涨幅）
GREEN = '\033[92m'  # 绿色（负值/跌幅）
RESET = '\033[0m'   # 重置颜色

def _color_pct(value, suffix='%'):
    """根据正负值返回带颜色的百分比字符串"""
    if value > 0:
        return f"{RED}{value:+.1f}{suffix}{RESET}"
    elif value < 0:
        return f"{GREEN}{value:+.1f}{suffix}{RESET}"
    else:
        return f"{value:.1f}{suffix}"

def _print_results(results, scan_elapsed, prev_codes, verbose=True):
    """Print scan results and compare with previous day's picks."""
    # 标记是否新增
    for r in results:
        r['是否新增'] = '是' if (prev_codes is not None and r['代码'] not in prev_codes) else '否'

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

    # ---------- 表格列宽定义 (显示宽度) ----------
    COL_WIDTHS = {
        'idx': 3,       # 序号
        'code': 7,      # 代码
        'name': 10,     # 名称 (截取前4个字符)
        'close': 8,     # 现价
        'pct': 8,       # 涨幅(含%)
        'cons_dd': 8,   # 回调幅度(含%)
        'industry': 18, # 行业
        'score': 5,     # 评分
        'signal': 8,    # 信号
        'tag': 3,       # 是否新增标记
    }
    SEP = '  '         # 列间隔

    # 打印表头
    headers = [
        _pad_str(" #", COL_WIDTHS['idx'], '<'),
        _pad_str("代码", COL_WIDTHS['code'], '<'),
        _pad_str("名称", COL_WIDTHS['name'], '<'),
        _pad_str("现价", COL_WIDTHS['close'], '>'),
        _pad_str("涨幅", COL_WIDTHS['pct'], '>'),
        _pad_str("回调", COL_WIDTHS['cons_dd'], '>'),
        _pad_str("行业", COL_WIDTHS['industry'], '<'),
        _pad_str("评分", COL_WIDTHS['score'], '>'),
        _pad_str("信号", COL_WIDTHS['signal'], '<'),
        _pad_str("新增", COL_WIDTHS['tag'], '<'),
    ]
    print()
    print(SEP.join(headers))
    print('-' * (sum(COL_WIDTHS.values()) + len(SEP) * (len(COL_WIDTHS) - 1)))

    # 数据行 (最多20条)
    for i, c in enumerate(results[:20]):
        # 标记：★ 占2宽度，空格补足为两个空格以保证列宽一致
        tag = '★' if c['是否新增'] == '是' else '  '

        code_str = str(c['代码'])

        name_str = c.get('名称', '')[:4]   # 截取前4字符

        # 行业：取第一项（按顿号分隔）
        industry = c.get('行业', '')
        parts = industry.split('、')
        industry_str = parts[0].strip() if parts else industry

        # 构建各字段（对齐后）
        idx_str = _pad_str(f"{i+1:>2}", COL_WIDTHS['idx'], '>')
        code_str = _pad_str(code_str, COL_WIDTHS['code'], '<')
        name_str = _pad_str(name_str, COL_WIDTHS['name'], '<')
        close_str = _pad_str(f"{c['现价']:.2f}", COL_WIDTHS['close'], '>')

        # 涨幅直接构建带颜色的版本，pad() 会根据 display_width 正确计算填充（忽略 ANSI 码）
        pct_val = c['涨幅'] * 100
        pct_colored = _color_pct(pct_val)
        pct_str = _pad_str(pct_colored, COL_WIDTHS['pct'], '>')

        cons_dd_str = _pad_str(f"{c['回调']*100:.1f}%", COL_WIDTHS['cons_dd'], '>')
        industry_str = _pad_str(industry_str, COL_WIDTHS['industry'], '<')
        score_str = _pad_str(f"{c['评分']}", COL_WIDTHS['score'], '>')
        signal_str = _pad_str(c['信号'], COL_WIDTHS['signal'], '<')

        row = SEP.join([
            idx_str, code_str, name_str, close_str,
            pct_str, cons_dd_str, industry_str, score_str, signal_str, tag
        ])
        print(row)

    # 详细分析部分（无需严格对齐列，保留原样即可）
    print()
    print("=" * 52)
    print("TOP 10 详细分析")
    print("=" * 52)
    print()
    for i, c in enumerate(results[:10]):
        if c['是否新增'] == '是':
            tag = '[新增]'
        elif prev_codes is None:
            tag = '[首次]'
        else:
            tag = '[延续]'

        code_str = str(c['代码'])
        name = c.get('名称', '')
        industry = c.get('行业', '')
        parts = industry.split('、')
        ind_display = parts[0].strip() if parts else industry

        # 涨幅带颜色
        pct_val = c['涨幅'] * 100
        pct_colored = _color_pct(pct_val)

        print(f"  {i+1:>2}. {code_str} {name} {tag} [{ind_display}]")
        print(f"      现价: {c['现价']:.2f} | 涨幅: {pct_colored}")
        print(f"      信号: {c['信号']} | 评分: {c['评分']}")
        print(f"      波段涨幅: {c['波段涨幅']*100:.0f}% | 回调幅度: {c['回调']*100:.0f}% | 量比: {c['量比']:.1f}x")
        print(f"      止损位: {c['止损位']:.2f}")
        if c.get('原因'):
            print(f"      原因: {c['原因']}")
        print()

    if prev_codes is None:
        print(f"  (首次扫描，无历史对比)")
    else:
        print(f"  新增: {new_count} 只 | 延续: {repeat_count} 只")
    print()


def _load_prev_file(filepath):
    """Load stock codes from previous day's file (CSV or XLSX). Returns set or None."""
    try:
        if not os.path.exists(filepath):
            return None
        # 兼容 CSV 和 XLSX 格式
        if filepath.endswith('.xlsx'):
            df = pd.read_excel(filepath, engine='openpyxl')
        else:
            df = pd.read_csv(filepath)
        # 支持中文和英文表头
        code_col = '代码' if '代码' in df.columns else 'code'
        if code_col not in df.columns:
            return None
        return set(df[code_col].astype(str).tolist())
    except Exception:
        return None


def _get_regime_and_name(dl, target_date):
    """Get market regime cache, name map and industry map for a specific date."""
    regime_cache = {}
    for code, name in dl.INDEX_CODES.items():
        regime = dl.get_market_regime(target_date, code)
        regime_cache[code] = regime

    with dl._get_conn() as conn:
        name_df = pd.read_sql("SELECT code, name, industry FROM stock_meta", conn)
    name_map = dict(zip(name_df['code'], name_df['name']))
    industry_map = dict(zip(name_df['code'], name_df['industry'])) if 'industry' in name_df.columns else {}

    return regime_cache, name_map, industry_map


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
    is_ready, max_date, status_info = dl.is_all_updated(effective_scan_date)
    reason = status_info.get('reason', 'unknown')

    if reason == 'data_ready':
        print(f"  数据已是最新（最新 {max_date}），跳过增量更新")
    elif reason == 'non_trading_day':
        print(f"  数据已是最新（最新 {max_date}，确认 {effective_scan_date} 为非交易日），跳过增量更新")
        # 非交易日 → 调整扫描日期为上一交易日
        effective_scan_date = max_date
        print(f"  调整扫描日期为上一交易日: {effective_scan_date}")
    elif reason == 'data_not_updated':
        print(f"  数据源尚未更新（最新 {max_date}），等待数据更新...")
        # 数据未更新 → 调整扫描日期为数据库最大日期
        effective_scan_date = max_date
        print(f"  调整扫描日期为数据库最新日期: {effective_scan_date}")
    else:
        # need_update → 执行增量更新
        t0 = datetime.now()
        # 先检查并更新落后股票
        lagging_result = dl.update_lagging_stocks(verbose=True)
        if lagging_result['lagging_count'] > 0:
            print(f"  落后股票更新完成: {lagging_result['updated']} 只，新增 {lagging_result['new_rows']} 行")
        # 然后执行正常增量更新
        dl.batch_update(codes, verbose=True, total=len(codes))
        print(f"  个股增量耗时: {(datetime.now()-t0).total_seconds()/60:.1f} 分钟")

    # 更新大盘指数数据（移到完整性检查前）
    print("\n更新大盘指数数据...")
    dl.update_index_data()
    sys.stdout.flush()

    # 检查数据完整性（bs.login() 已在前面的 batch_update 中执行）
    is_complete, missing_info = dl.ensure_data_complete(effective_scan_date)
    if not is_complete:
        print("\n✗ 数据完整性检查失败，终止扫描")
        bs.logout()
        sys.exit(1)

    # 3. 确定对比基准（上一期选股结果）
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # 获取上一期选股文件路径
    prev_scan_date = _get_real_prev_trading_date(effective_scan_date)
    prev_file = _signals_filepath(script_dir, prev_scan_date) if prev_scan_date else None

    prev_codes = None
    if prev_file and os.path.exists(prev_file):
        prev_codes = _load_prev_file(prev_file)
        if prev_codes is not None:
            print(f"\n[对比基准] 已加载上期选股文件 {os.path.basename(prev_file)}: {len(prev_codes)} 只")
        else:
            prev_codes = None

    if prev_codes is None:
        # 上一期文件不存在，需要先扫描上一期
        if prev_scan_date:
            print(f"\n[对比基准] 无上期选股文件，先扫描 {prev_scan_date} 作为基准...")
            prev_regime, prev_names, prev_industry = _get_regime_and_name(dl, prev_scan_date)
            scan_start = (datetime.strptime(effective_scan_date, '%Y-%m-%d') - timedelta(days=200)).strftime('%Y-%m-%d')
            t_scan = datetime.now()
            prev_results = _scan_core(dl, codes, prev_regime, prev_names, prev_industry, scan_start, prev_scan_date, verbose=True)
            prev_elapsed = (datetime.now() - t_scan).total_seconds()
            prev_codes = set(r['代码'] for r in prev_results)
            # 保存上一期的结果
            prev_file = _signals_filepath(script_dir, prev_scan_date)
            if prev_results:
                pd.DataFrame(prev_results).to_excel(prev_file, index=False, engine='openpyxl')
                print(f"  已保存 {os.path.basename(prev_file)} ({len(prev_results)} 只)")
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

    regime_cache, name_map, industry_map = _get_regime_and_name(dl, effective_scan_date)
    results = _scan_core(dl, codes, regime_cache, name_map, industry_map, scan_start, effective_scan_date, verbose=True)
    scan_elapsed = (datetime.now() - t1).total_seconds()

    # 打印结果
    _print_results(results, scan_elapsed, prev_codes)

    # 保存结果（使用带日期的文件名）
    signals_file = _signals_filepath(script_dir, effective_scan_date)
    if results:
        pd.DataFrame(results).to_excel(signals_file, index=False, engine='openpyxl')
        print(f"✅ 保存至 {os.path.basename(signals_file)}")
        # Record picks for tracking
        n_tracked = tracker.record_picks(pd.DataFrame(results), pick_date=effective_scan_date)
        print(f"✅ 已记录 {n_tracked} 只股票用于跟踪")

    bs.logout()

if __name__ == '__main__':
    main()
