#!/usr/bin/env python3
"""
策略参数优化模型：坐标下降法 + Walk-Forward 验证。
"""
import os
import sys
import json
import random
import itertools
import sqlite3
import hashlib
from datetime import datetime, timedelta
from copy import deepcopy

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer
from strategy_config import StrategyConfig


def get_dynamic_seed(start_date: str) -> int:
    """根据日期 + 时间 + PID 生成动态种子"""
    timestamp = datetime.now().strftime('%H%M%S')
    pid = os.getpid()

    hash_input = f"{start_date}_{timestamp}_{pid}"
    hash_val = hashlib.md5(hash_input.encode()).hexdigest()
    return int(hash_val[:8], 16) % 10000


class StrategyOptimizer:
    """Parameter optimization with walk-forward validation."""

    # Parameters to optimize and their search ranges
    OPTIMIZATION_PARAMS = {
        'first_wave_min_days':      {'type': 'int',   'low': 2,    'high': 5,    'category': 'entry'},
        'first_wave_min_gain':      {'type': 'float', 'low': 0.10, 'high': 0.25, 'category': 'entry'},
        'consolidation_max_days':   {'type': 'int',   'low': 8,    'high': 20,   'category': 'entry'},
        'consolidation_max_drawdown': {'type': 'float', 'low': 0.10, 'high': 0.30, 'category': 'entry'},
        'weak_strong_threshold':    {'type': 'float', 'low': 0.02, 'high': 0.05, 'category': 'entry'},
        'anomaly_amplitude':        {'type': 'float', 'low': 0.04, 'high': 0.08, 'category': 'entry'},
        'stop_loss_buffer':         {'type': 'float', 'low': 0.01, 'high': 0.05, 'category': 'exit'},
        'trailing_stop_pct':        {'type': 'float', 'low': 0.05, 'high': 0.12, 'category': 'exit'},
        'trailing_min_gain':        {'type': 'float', 'low': 0.05, 'high': 0.15, 'category': 'exit'},
        'max_hold_days':            {'type': 'int',   'low': 10,   'high': 30,   'category': 'exit'},
    }

    def __init__(self, db_path=None):
        self.db_path = db_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data.db')
        self.dl = get_data_layer()
        self.cfg = StrategyConfig(self.db_path)

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Strategy Detection & Simulation (parameterized) ─────────

    def _detect_pattern_with_params(self, df, params):
        """
        Run the same 3-phase detection as daily_scanner.detect_pattern,
        but with customizable parameters.
        """
        n = len(df)
        if n < 20:
            return []

        first_wave_min_days = int(params.get('first_wave_min_days', 3))
        first_wave_min_gain = params.get('first_wave_min_gain', 0.15)
        consolidation_max_days = int(params.get('consolidation_max_days', 15))
        consolidation_max_drawdown = params.get('consolidation_max_drawdown', 0.20)
        weak_strong_threshold = params.get('weak_strong_threshold', 0.03)
        anomaly_amplitude = params.get('anomaly_amplitude', 0.06)
        stop_loss_buffer = params.get('stop_loss_buffer', 0.02)
        max_hold_days = int(params.get('max_hold_days', 20))
        trailing_stop_pct = params.get('trailing_stop_pct', 0.08)
        trailing_min_gain = params.get('trailing_min_gain', 0.10)

        # Phase 1: Detect waves
        waves = []
        i = 0
        while i < n - first_wave_min_days:
            up, gain, j = 0, 0.0, i
            while j < n - 1:
                p = df.iloc[j]['pct_chg']
                if p > 0:
                    up += 1
                    gain += p
                    j += 1
                else:
                    break
            if up >= first_wave_min_days and gain >= first_wave_min_gain:
                waves.append((i, j - 1, gain, up))
                i = j
            else:
                i += 1

        signals = []
        for ws, we, wg, wd in waves:
            # Phase 2: Detect consolidation
            if we >= n - 5:
                continue
            peak = df.iloc[we]['close']
            cs = we + 1
            mn, mi, days = peak, cs, 0
            for k in range(cs, min(n, cs + consolidation_max_days)):
                days += 1
                if df.iloc[k]['low'] < mn:
                    mn = df.iloc[k]['low']
                    mi = k
            dd = (peak - mn) / peak
            if dd > consolidation_max_drawdown:
                continue
            dn = sum(1 for k in range(cs, mi + 1) if k < n and df.iloc[k]['pct_chg'] < 0)
            if days < 3 or dn / max(days, 1) >= 0.7:
                continue

            # Phase 3: Search for signals in a window after consolidation (not just last day)
            se = mi
            ss = cs
            search_start = max(se, ss + 2)
            search_end = min(n, search_start + 11)
            for ti in range(search_start, search_end):
                if ti >= n:
                    break
                tp = df.iloc[ti]['pct_chg']
                sig = None
                if ti > 0 and df.iloc[ti - 1]['amplitude'] > anomaly_amplitude and tp > 0.01:
                    sig = 'anomaly_no_decline'
                elif sig is None and ti > 0 and tp > 0.02 and df.iloc[ti - 1]['close'] < df.iloc[ti - 1]['open'] and df.iloc[ti]['close'] > df.iloc[ti - 1]['open']:
                    sig = 'bullish_engulfing'
                elif sig is None and ti > 0 and tp > weak_strong_threshold and df.iloc[ti - 1]['close'] < df.iloc[ti - 1]['open']:
                    sig = 'big_bullish_reversal'
                elif sig is None and ti > 0 and df.iloc[ti - 1]['pct_chg'] > 0.08 and df.iloc[ti - 1]['close'] < df.iloc[ti - 1]['high'] * 0.97 and tp > 0.02:
                    sig = 'limit_up_open_next_strong'

                if sig:
                    signals.append({
                        'idx': ti, 'type': sig,
                        'entry_price': df.iloc[ti]['close'],
                        'stop_loss': mn * (1 - stop_loss_buffer),
                        'cons_low': mn,
                        'wave_gain': wg,
                        'cons_dd': dd,
                    })

        return signals

    def _simulate_trade_with_params(self, df, signal_idx, signal, params):
        """Simulate trade with customizable exit parameters."""
        max_hold_days = int(params.get('max_hold_days', 20))
        trailing_stop_pct = params.get('trailing_stop_pct', 0.08)
        trailing_min_gain = params.get('trailing_min_gain', 0.10)

        entry_price = signal['entry_price']
        stop_loss = signal['stop_loss']
        peak_price = entry_price
        n = len(df)

        for day_offset in range(1, max_hold_days + 1):
            idx = signal_idx + day_offset
            if idx >= n:
                break
            close = df.iloc[idx]['close']
            low = df.iloc[idx]['low']
            high = df.iloc[idx]['high']
            peak_price = max(peak_price, high)

            # Stop loss
            if low <= stop_loss:
                return {'hold_days': day_offset, 'exit_price': stop_loss, 'exit_reason': 'stop_loss',
                        'pnl_pct': (stop_loss - entry_price) / entry_price,
                        'max_profit': (peak_price - entry_price) / entry_price,
                        'max_drawdown': (low - entry_price) / entry_price}

            # Trailing stop
            if day_offset > 2:
                drawdown_from_peak = (peak_price - close) / peak_price
                total_gain = (peak_price - entry_price) / entry_price
                if drawdown_from_peak > trailing_stop_pct and total_gain > trailing_min_gain:
                    return {'hold_days': day_offset, 'exit_price': close, 'exit_reason': 'trailing_stop',
                            'pnl_pct': (close - entry_price) / entry_price,
                            'max_profit': total_gain,
                            'max_drawdown': (df.iloc[signal_idx + 1:idx + 1]['low'].min() - entry_price) / entry_price}

            # Time exit
            if day_offset >= max_hold_days:
                return {'hold_days': day_offset, 'exit_price': close, 'exit_reason': 'time_exit',
                        'pnl_pct': (close - entry_price) / entry_price,
                        'max_profit': (peak_price - entry_price) / entry_price,
                        'max_drawdown': (df.iloc[signal_idx + 1:idx + 1]['low'].min() - entry_price) / entry_price}

        last_idx = min(signal_idx + max_hold_days, n - 1)
        return {'hold_days': last_idx - signal_idx, 'exit_price': df.iloc[last_idx]['close'],
                'exit_reason': 'final',
                'pnl_pct': (df.iloc[last_idx]['close'] - entry_price) / entry_price,
                'max_profit': (peak_price - entry_price) / entry_price,
                'max_drawdown': 0}

    def _get_kline_batch_local(self, codes, start_date, end_date):
        """Query stock_daily from local DB only, no API fallback."""
        if not codes:
            return {}
        with self._get_conn() as conn:
            placeholders = ','.join('?' * len(codes))
            df = pd.read_sql(
                f"SELECT * FROM stock_daily WHERE code IN ({placeholders}) AND date>=? AND date<=? ORDER BY code, date",
                conn, params=tuple(codes) + (start_date, end_date),
                parse_dates=['date']
            )
        result = {}
        for code, group in df.groupby('code'):
            if len(group) >= 60:
                result[code] = group.reset_index(drop=True)
        return result

    # ── Evaluation ───────────────────────────────────────────────

    @staticmethod
    def smooth_objective(expectancy, win_rate, max_dd, sharpe, total):
        """
        平滑目标函数，负期望值时加强惩罚力度

        Args:
            expectancy: 期望值（小数，如 0.042）
            win_rate: 胜率（0-1）
            max_dd: 最大回撤（负值小数，如 -0.15）
            sharpe: Sharpe 比率
            total: 交易数

        Returns:
            objective_score: 目标得分
        """
        # 期望值贡献：正值线性增长，负值二次惩罚
        exp_contrib = expectancy * 10 + 0.5
        if exp_contrib >= 0:
            exp_score = exp_contrib  # 正期望值：线性得分
        else:
            # 负期望值：二次惩罚（惩罚力度更强，优化方向更明确）
            # 例如 exp_contrib=-0.5 → exp_score = -0.5 * 0.5 = -0.25
            # 相比原方案 -0.5 * 0.2 = -0.1 惩罚更强
            exp_score = exp_contrib * abs(exp_contrib)

        # 最大回撤：max_dd 是负值
        dd_base = 1 + max_dd
        if dd_base > 0:
            dd_score = dd_base
        else:
            # 回撤超 100%（极端情况），给予负惩罚
            dd_score = dd_base * 0.5

        # Sharpe：负值二次惩罚
        sharpe_norm = sharpe / 3
        if sharpe_norm > 1:
            sharpe_score = 1.0 + (sharpe_norm - 1) * 0.1
        elif sharpe_norm < 0:
            sharpe_score = sharpe_norm * abs(sharpe_norm)  # 二次惩罚
        else:
            sharpe_score = sharpe_norm

        score = (
            0.35 * exp_score
            + 0.25 * win_rate
            + 0.20 * dd_score
            + 0.10 * sharpe_score
            + 0.10 * min(total / 100, 1.0)
        )

        # 设置下界：避免极端负值导致数值不稳定
        return max(score, -0.5)

    def evaluate_params(self, params_dict, start_date, end_date, codes, sample_size=200):
        """
        Run backtest with given params, return objective score and metrics.
        """
        if len(codes) > sample_size:
            seed = get_dynamic_seed(start_date)
            random.seed(seed)
            codes = random.sample(codes, sample_size)

        # Load data from local DB only
        fetch_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=150)).strftime('%Y-%m-%d')
        kline = self._get_kline_batch_local(codes, fetch_start, end_date)

        trades = []
        for code in codes:
            if code not in kline:
                continue
            df = kline[code]
            mask = df['date'] >= pd.to_datetime(start_date)
            df_trade = df[mask].reset_index(drop=True)
            if len(df_trade) < 20:
                continue

            signals = self._detect_pattern_with_params(df, params_dict)
            for sig in signals:
                sig_date_raw = df.iloc[sig['idx']]['date']
                sig_date = str(sig_date_raw)
                if sig_date < start_date or sig_date > end_date:
                    continue
                try:
                    trade_idx = df_trade[df_trade['date'] == pd.to_datetime(sig_date)].index[0]
                except IndexError:
                    continue

                trade = self._simulate_trade_with_params(df_trade, trade_idx, sig, params_dict)
                trades.append(trade)

        if len(trades) < 10:
            return None

        df = pd.DataFrame(trades)
        total = len(df)
        wins = len(df[df['pnl_pct'] > 0])
        win_rate = wins / total
        avg_profit = df[df['pnl_pct'] > 0]['pnl_pct'].mean() if wins > 0 else 0
        avg_loss = df[df['pnl_pct'] <= 0]['pnl_pct'].mean() if total - wins > 0 else 0
        expectancy = win_rate * avg_profit + (1 - win_rate) * avg_loss
        sharpe = df['pnl_pct'].mean() / df['pnl_pct'].std() * np.sqrt(252) if df['pnl_pct'].std() > 0 else 0

        # Max drawdown
        cum = (1 + df['pnl_pct']).cumprod() - 1
        peak = cum.cummax()
        max_dd = ((cum - peak) / (peak + 1)).min() if len(cum) > 0 else 0

        # Objective score (using smooth objective function)
        obj = self.smooth_objective(expectancy, win_rate, max_dd, sharpe, total)

        return {
            'objective_score': obj,
            'win_rate': win_rate * 100,
            'expectancy': expectancy * 100,
            'max_drawdown': max_dd * 100,
            'total_trades': total,
            'sharpe': sharpe,
        }

    # ── Coordinate Descent ──────────────────────────────────────

    def coordinate_descent(self, start_date, end_date, codes, base_params=None, max_rounds=3, sample_size=200):
        """
        Coordinate descent: optimize one parameter at a time.
        """
        if base_params is None:
            base_params = self.cfg.get_dict()

        param_keys = list(self.OPTIMIZATION_PARAMS.keys())
        best_params = dict(base_params)
        best_result = self.evaluate_params(best_params, start_date, end_date, codes, sample_size)

        if best_result is None:
            return None, None

        history = [{'params': dict(best_params), 'metrics': best_result, 'round': 0}]

        for round_num in range(1, max_rounds + 1):
            improved = False
            for key in param_keys:
                spec = self.OPTIMIZATION_PARAMS[key]
                n_points = 7
                if spec['type'] == 'int':
                    test_values = [int(v) for v in np.linspace(spec['low'], spec['high'], n_points)]
                else:
                    test_values = [float(v) for v in np.linspace(spec['low'], spec['high'], n_points)]

                local_best = best_result
                local_best_val = best_params[key]

                for val in test_values:
                    test_params = dict(best_params)
                    test_params[key] = val
                    result = self.evaluate_params(test_params, start_date, end_date, codes, sample_size)
                    if result and result['objective_score'] > local_best['objective_score']:
                        local_best = result
                        local_best_val = val

                if local_best_val != best_params[key]:
                    best_params[key] = local_best_val
                    best_result = local_best
                    improved = True

            history.append({'params': dict(best_params), 'metrics': best_result, 'round': round_num})
            if not improved:
                break

        return best_params, history

    # ── Walk-Forward Validation ─────────────────────────────────

    def walk_forward_optimize(self, codes,
                              train_window=180,
                              test_window=60,
                              step=30,
                              max_rounds=3,
                              sample_size=200):
        """
        Walk-forward optimization with coordinate descent.

        Returns aggregate OOS results and recommended parameters.
        """
        # Determine date range from DB
        with self._get_conn() as conn:
            row = conn.execute('SELECT MIN(date), MAX(date) FROM stock_daily').fetchone()
            db_start = row[0]
            db_end = row[1]

        if not db_start or not db_end:
            return None

        # Generate windows
        windows = []
        current_train_start = datetime.strptime(db_start, '%Y-%m-%d')
        while True:
            train_start = current_train_start.strftime('%Y-%m-%d')
            train_end = (current_train_start + timedelta(days=train_window - 1)).strftime('%Y-%m-%d')
            test_start = (current_train_start + timedelta(days=train_window)).strftime('%Y-%m-%d')
            test_end = (current_train_start + timedelta(days=train_window + test_window - 1)).strftime('%Y-%m-%d')

            if test_end > db_end:
                break
            if datetime.strptime(train_end, '%Y-%m-%d') > datetime.strptime(db_end, '%Y-%m-%d'):
                break

            windows.append({
                'train_start': train_start,
                'train_end': train_end,
                'test_start': test_start,
                'test_end': test_end,
            })
            current_train_start += timedelta(days=step)

        if not windows:
            return None

        print(f'  Walk-Forward: {len(windows)} 个窗口')
        print(f'  训练窗口: {train_window}天, 测试窗口: {test_window}天, 步进: {step}天')
        print()

        window_results = []
        all_test_trades = []

        for i, w in enumerate(windows):
            print(f'  窗口 {i + 1}/{len(windows)}: [{w["train_start"]} -> {w["train_end"]}] 测试 [{w["test_start"]} -> {w["test_end"]}]')

            # Optimize on training period
            best_params, history = self.coordinate_descent(
                w['train_start'], w['train_end'], codes,
                max_rounds=max_rounds, sample_size=sample_size
            )

            if best_params is None:
                print(f'    训练期无有效信号')
                continue

            train_metrics = history[-1]['metrics'] if history else None
            print(f'    训练期: 胜率={train_metrics["win_rate"]:.1f}% 期望={train_metrics["expectancy"]:+.2f}%')

            # Evaluate on out-of-sample test period
            test_result = self.evaluate_params(
                best_params, w['test_start'], w['test_end'], codes, sample_size=sample_size
            )

            if test_result:
                print(f'    测试期: 胜率={test_result["win_rate"]:.1f}% 期望={test_result["expectancy"]:+.2f}% 交易数={test_result["total_trades"]}')
            else:
                print(f'    测试期: 无有效信号')
                test_result = {'objective_score': 0, 'win_rate': 0, 'expectancy': 0,
                               'max_drawdown': 0, 'total_trades': 0, 'sharpe': 0}

            window_results.append({
                'train_start': w['train_start'], 'train_end': w['train_end'],
                'test_start': w['test_start'], 'test_end': w['test_end'],
                'best_params': best_params,
                'train_metrics': train_metrics,
                'test_metrics': test_result,
            })

        if not window_results:
            return None

        # Aggregate OOS results
        oos_wr = np.mean([w['test_metrics']['win_rate'] for w in window_results])
        oos_expectancy = np.mean([w['test_metrics']['expectancy'] for w in window_results])
        oos_dd = np.mean([w['test_metrics']['max_drawdown'] for w in window_results])
        oos_sharpe = np.mean([w['test_metrics'].get('sharpe', 0) for w in window_results])
        oos_trades = sum(w['test_metrics']['total_trades'] for w in window_results)

        # Find recommended params: use the params from the window with best OOS score
        best_window = max(window_results, key=lambda w: w['test_metrics']['objective_score'])
        recommended_params = best_window['best_params']

        # Baseline comparison
        baseline_params = self.cfg.get_dict()
        baseline_result = self.evaluate_params(
            baseline_params, db_start, db_end, codes, sample_size=sample_size
        )

        result = {
            'window_results': window_results,
            'oos_aggregate': {
                'win_rate': oos_wr,
                'expectancy': oos_expectancy,
                'max_drawdown': oos_dd,
                'sharpe': oos_sharpe,
                'total_trades': oos_trades,
                'n_windows': len(window_results),
            },
            'recommended_params': recommended_params,
            'baseline_comparison': baseline_result,
            'best_window_params': best_window['best_params'],
        }

        return result

    # ── Grid Search (for small param sets) ───────────────────────

    def grid_search(self, param_keys, start_date, end_date, codes, n_points=5, sample_size=200):
        """
        Grid search over specified parameters.
        WARNING: exponential growth. Recommend max 4-5 params.
        """
        grids = {}
        for key in param_keys:
            if key not in self.OPTIMIZATION_PARAMS:
                continue
            spec = self.OPTIMIZATION_PARAMS[key]
            if spec['type'] == 'int':
                grids[key] = [int(v) for v in np.linspace(spec['low'], spec['high'], n_points)]
            else:
                grids[key] = [float(v) for v in np.linspace(spec['low'], spec['high'], n_points)]

        base_params = self.cfg.get_dict()
        keys = list(grids.keys())
        values = [grids[k] for k in keys]
        combinations = list(itertools.product(*values))

        print(f'  Grid search: {len(combinations)} 组合 ({len(keys)} 参数 x {n_points} 点)')

        results = []
        for i, combo in enumerate(combinations):
            params = dict(base_params)
            for k, v in zip(keys, combo):
                params[k] = v
            result = self.evaluate_params(params, start_date, end_date, codes, sample_size)
            if result:
                result['params'] = {k: v for k, v in zip(keys, combo)}
                results.append(result)
            if (i + 1) % 50 == 0:
                print(f'    进度: {i + 1}/{len(combinations)}')

        results.sort(key=lambda x: x['objective_score'], reverse=True)
        return results

    # ── Save Results ─────────────────────────────────────────────

    def save_results(self, result, output_dir=None):
        """Save optimization results to CSV and JSON."""
        if output_dir is None:
            output_dir = os.path.dirname(os.path.abspath(__file__))

        # Save recommended params
        summary = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'recommended_params': result['recommended_params'],
            'oos_aggregate': result['oos_aggregate'],
            'baseline': result.get('baseline_comparison'),
            'n_windows': result['oos_aggregate']['n_windows'],
        }
        json_path = os.path.join(output_dir, 'optimization_summary.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
        print(f'\n优化摘要: {json_path}')

        # Save window results CSV
        rows = []
        for w in result['window_results']:
            row = {
                'train_start': w['train_start'], 'train_end': w['train_end'],
                'test_start': w['test_start'], 'test_end': w['test_end'],
            }
            if w.get('train_metrics'):
                for k, v in w['train_metrics'].items():
                    row[f'train_{k}'] = v
            if w.get('test_metrics'):
                for k, v in w['test_metrics'].items():
                    row[f'test_{k}'] = v
            for k, v in w['best_params'].items():
                row[f'param_{k}'] = v
            rows.append(row)

        csv_path = os.path.join(output_dir, 'optimization_results.csv')
        pd.DataFrame(rows).to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f'窗口结果: {csv_path}')


# ── CLI ──────────────────────────────────────────────────────────

def print_param_comparison(baseline, recommended):
    """Print side-by-side parameter comparison."""
    print(f'\n{"="*60}')
    print('参数对比 (推荐 vs 基准)')
    print(f'{"="*60}')
    print(f'{"参数":<30} {"基准":>10} {"推荐":>10} {"变化":>10}')
    print(f'{"-"*60}')
    for key in sorted(recommended.keys()):
        if key in StrategyConfig.DEFAULTS:
            base_val = baseline.get(key, recommended[key])
            rec_val = recommended[key]
            diff = rec_val - base_val
            if abs(diff) > 1e-6:
                print(f'{key:<30} {base_val:>10.4f} {rec_val:>10.4f} {diff:>+10.4f}')
    print(f'{"="*60}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='策略参数优化')
    parser.add_argument('--mode', choices=['coordinate', 'walkforward', 'grid'], default='walkforward')
    parser.add_argument('--sample', type=int, default=200, help='Stock sample size')
    parser.add_argument('--rounds', type=int, default=3, help='Max coordinate descent rounds')
    parser.add_argument('--train-window', type=int, default=180, help='Training window days')
    parser.add_argument('--test-window', type=int, default=60, help='Test window days')
    parser.add_argument('--step', type=int, default=30, help='Walk-forward step days')
    parser.add_argument('--auto-apply', action='store_true',
                        help='自动应用推荐参数（通过沙盒验证）')
    args = parser.parse_args()

    optimizer = StrategyOptimizer()

    # Get codes
    with optimizer._get_conn() as conn:
        codes = [r[0] for r in conn.execute('SELECT DISTINCT code FROM stock_daily').fetchall()]

    print(f'股票池: {len(codes)} 只')
    print(f'优化模式: {args.mode}')
    print()

    if args.mode == 'walkforward':
        result = optimizer.walk_forward_optimize(
            codes,
            train_window=args.train_window,
            test_window=args.test_window,
            step=args.step,
            max_rounds=args.rounds,
            sample_size=args.sample,
        )
        if result:
            print(f'\n{"="*60}')
            print(f'Walk-Forward 汇总')
            print(f'{"="*60}')
            agg = result['oos_aggregate']
            print(f'  窗口数: {agg["n_windows"]}')
            print(f'  OOS 胜率: {agg["win_rate"]:.1f}%')
            print(f'  OOS 期望: {agg["expectancy"]:+.2f}%')
            print(f'  OOS 最大回撤: {agg["max_drawdown"]:.2f}%')
            print(f'  OOS 总交易: {agg["total_trades"]}')

            if result.get('baseline_comparison'):
                bl = result['baseline_comparison']
                print(f'\n  基准 (全量): 胜率={bl["win_rate"]:.1f}% 期望={bl["expectancy"]:+.2f}%')

            print_param_comparison(optimizer.cfg.get_dict(), result['recommended_params'])
            optimizer.save_results(result)

            if args.auto_apply:
                # 调用沙盒流程应用参数
                from change_manager import ChangeManager
                from sandbox_validator import SandboxValidator

                change_mgr = ChangeManager()
                validator = SandboxValidator()

                # 1. 生成批次 ID 并保存快照
                batch_id = change_mgr.generate_batch_id()
                change_mgr.save_snapshot('walkforward_optimize', batch_id)

                # 2. 暂存推荐参数
                recommended = result['recommended_params']
                current = optimizer.cfg.get_dict()

                staged_count = 0
                for key, value in recommended.items():
                    if abs(value - current.get(key, 0)) > 1e-6:
                        change_mgr.stage_change(
                            optimize_type='strategy_config',
                            param_key=key,
                            new_value=value,
                            batch_id=batch_id,
                            current_value=current.get(key)
                        )
                        staged_count += 1

                if staged_count == 0:
                    print('\n推荐参数与当前配置相同，无需应用')
                else:
                    # 3. Walk-Forward 已是 OOS 测试，直接紧急应用
                    #    （不需要再用 3 周沙盒窗口重新验证）
                    applied = validator.emergency_apply_changes(batch_id)
                    print(f"\n已应用 {applied['applied']} 项推荐参数（Walk-Forward OOS 验证通过）")
        else:
            print('Walk-Forward 优化未产生有效结果')

    elif args.mode == 'coordinate':
        # Use a 1-year window for coordinate descent
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        print(f'回测区间: {start_date} 至 {end_date}')
        print()

        best_params, history = optimizer.coordinate_descent(
            start_date, end_date, codes,
            max_rounds=args.rounds, sample_size=args.sample,
        )

        if best_params and history:
            print(f'\n{"="*60}')
            print('坐标下降结果')
            print(f'{"="*60}')
            for h in history:
                m = h['metrics']
                print(f'  Round {h["round"]}: 胜率={m["win_rate"]:.1f}% 期望={m["expectancy"]:+.2f}% 得分={m["objective_score"]:.4f}')

            print_param_comparison(optimizer.cfg.get_dict(), best_params)
            # Apply recommended params
            optimizer.cfg.set_batch(best_params)
            print('\n已应用推荐参数到数据库')

    elif args.mode == 'grid':
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        # Grid search on top 4 params
        top_params = ['first_wave_min_gain', 'consolidation_max_drawdown',
                      'trailing_stop_pct', 'max_hold_days']

        results = optimizer.grid_search(
            top_params, start_date, end_date, codes,
            n_points=5, sample_size=args.sample,
        )

        if results:
            print(f'\n{"="*60}')
            print('Top 5 参数组合')
            print(f'{"="*60}')
            for i, r in enumerate(results[:5]):
                print(f'  #{i + 1}: 胜率={r["win_rate"]:.1f}% 期望={r["expectancy"]:+.2f}% 得分={r["objective_score"]:.4f}')
                for k, v in r['params'].items():
                    print(f'       {k} = {v}')
                print()
