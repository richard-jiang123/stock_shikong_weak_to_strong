#!/usr/bin/env python3
"""
选股跟踪模型：记录每日选股，模拟后续表现，生成成绩单。
"""
import os
import sys
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer
from strategy_config import StrategyConfig

# ANSI 颜色代码
RED = '\033[91m'    # 红色（正值/盈利）
GREEN = '\033[92m'  # 绿色（负值/亏损）
RESET = '\033[0m'   # 重置颜色

def color_pnl(value, suffix='%'):
    """根据正负值返回带颜色的盈亏字符串"""
    if value > 0:
        return f"{RED}{value:+.2f}{suffix}{RESET}"
    elif value < 0:
        return f"{GREEN}{value:+.2f}{suffix}{RESET}"
    else:
        return f"{value:.2f}{suffix}"


class PickTracker:
    """Track daily picks and simulate their post-pick performance."""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data.db')
        self.dl = get_data_layer()
        self.cfg = StrategyConfig(self.db_path)
        self._ensure_tables()
        self._migrate_pick_tracking_scores()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self):
        with self._get_conn() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS pick_tracking (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                pick_date     TEXT NOT NULL,
                code          TEXT NOT NULL,
                signal_type   TEXT NOT NULL,
                score         REAL,
                wave_gain     REAL,
                cons_dd       REAL,
                vol_ratio     REAL,
                entry_price   REAL,
                stop_loss     REAL,
                cons_low      REAL,
                market_regime TEXT,
                index_code    TEXT,
                name          TEXT,
                status        TEXT DEFAULT 'active',
                exit_date     TEXT,
                exit_price    REAL,
                exit_reason   TEXT,
                hold_days     INTEGER,
                max_price     REAL,
                min_price     REAL,
                final_pnl_pct REAL,
                max_pnl_pct   REAL,
                max_dd_pct    REAL,
                score_wave_gain REAL,
                score_shallow_dd REAL,
                score_day_gain REAL,
                score_volume REAL,
                score_ma_bull REAL,
                score_sector REAL,
                score_signal_bonus REAL,
                score_base REAL DEFAULT 5,
                created_at    TEXT DEFAULT (datetime('now')),
                UNIQUE(pick_date, code)
            )''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_pick_date ON pick_tracking(pick_date)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_pick_status ON pick_tracking(status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_pick_signal ON pick_tracking(signal_type)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_pick_regime ON pick_tracking(market_regime)')

            conn.execute('''CREATE TABLE IF NOT EXISTS scorecard (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date  TEXT NOT NULL,
                metric_key   TEXT NOT NULL,
                metric_value REAL,
                sample_size  INTEGER,
                UNIQUE(report_date, metric_key)
            )''')

    def _migrate_pick_tracking_scores(self):
        """迁移：为现有 pick_tracking 表添加评分字段"""
        with self._get_conn() as conn:
            columns = conn.execute("PRAGMA table_info(pick_tracking)").fetchall()
            col_names = [c[1] for c in columns]

            migrations = [
                ('score_wave_gain', 'REAL'),
                ('score_shallow_dd', 'REAL'),
                ('score_day_gain', 'REAL'),
                ('score_volume', 'REAL'),
                ('score_ma_bull', 'REAL'),
                ('score_sector', 'REAL'),
                ('score_signal_bonus', 'REAL'),
                ('score_base', 'REAL DEFAULT 5'),
            ]

            for col_name, col_type in migrations:
                if col_name not in col_names:
                    conn.execute(f"ALTER TABLE pick_tracking ADD COLUMN {col_name} {col_type}")

    # ── Record picks ──────────────────────────────────────────────

    def record_picks(self, picks_df, pick_date=None):
        """
        Record daily scanner picks into pick_tracking table.

        Args:
            picks_df: DataFrame from today_signals.csv or in-memory results.
                      Expected columns: code, signal, score, wave_gain, cons_dd,
                                        vol_ratio, entry, stop_loss, market_regime, index, name, reasons
            pick_date: Date string 'YYYY-MM-DD', defaults to today

        Returns:
            Number of picks recorded
        """
        if pick_date is None:
            pick_date = datetime.now().strftime('%Y-%m-%d')

        if isinstance(picks_df, pd.DataFrame) and picks_df.empty:
            return 0

        count = 0
        with self._get_conn() as conn:
            if isinstance(picks_df, pd.DataFrame):
                rows = picks_df.to_dict('records')
            else:
                rows = picks_df

            for row in rows:
                # Convert code back to baostock format
                # 支持中文和英文表头
                code_str = str(row.get('代码', row.get('code', '')))
                if '.' not in code_str:
                    # Zero-pad to 6 digits
                    code_str = code_str.zfill(6)
                    if code_str.startswith('6'):
                        code = f'sh.{code_str}'
                    else:
                        code = f'sz.{code_str}'
                else:
                    code = code_str

                conn.execute('''INSERT OR REPLACE INTO pick_tracking
                    (pick_date, code, signal_type, score, wave_gain, cons_dd, vol_ratio,
                     entry_price, stop_loss, cons_low, market_regime, index_code, name, status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active')''',
                    (pick_date, code,
                     row.get('信号', row.get('signal', '')),
                     float(row.get('评分', row.get('score', 0))),
                     float(row.get('波段涨幅', row.get('wave_gain', 0))),
                     float(row.get('回调', row.get('cons_dd', 0))),
                     float(row.get('量比', row.get('vol_ratio', 0))),
                     float(row.get('入场价', row.get('entry', 0))),
                     float(row.get('止损位', row.get('stop_loss', 0))),
                     float(row.get('cons_low', 0)),
                     row.get('市场环境', row.get('market_regime', '')),
                     row.get('指数', row.get('index', '')),
                     row.get('名称', row.get('name', ''))))
                count += 1
        return count

    def get_previous_picks(self):
        """
        Get stock codes from the most recent previous pick date.

        Returns:
            set of code strings in short format (e.g. '002384'), matching
            the format used by daily_scanner.py for comparison.
        """
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT DISTINCT pick_date FROM pick_tracking ORDER BY pick_date DESC LIMIT 2")
            rows = cur.fetchall()
            if len(rows) < 1:
                return set()
            # The most recent pick date might be today (already recorded by a previous run),
            # so we take the second-most-recent if available, otherwise the only one.
            if len(rows) == 2:
                prev_date = rows[1][0]
            else:
                prev_date = rows[0][0]
            cur = conn.execute(
                "SELECT code FROM pick_tracking WHERE pick_date = ?", (prev_date,))
            # Strip exchange prefix (e.g. 'sz.002384' -> '002384') for comparison
            return set(r[0].split('.')[1] if '.' in r[0] else r[0] for r in cur.fetchall())

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
            result[code] = group.reset_index(drop=True)
        return result

    # ── Update tracking ───────────────────────────────────────────

    def update_tracking(self, end_date=None):
        """
        For all 'active' picks, fetch subsequent daily data and update
        performance metrics using exit rules.

        Exit priority (same as backtest):
          1. Stop loss: daily low <= stop_loss
          2. Trailing stop: hold_days > 2 AND drawdown_from_peak > trailing_stop_pct AND total_gain > trailing_min_gain
          3. Time exit: hold_days >= max_hold_days
          4. Data end: no more data (leave active)

        Args:
            end_date: Date string 'YYYY-MM-DD', defaults to latest date in DB

        Returns:
            Dict: {'updated': N, 'exited': M, 'still_active': K}
        """
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')

        with self._get_conn() as conn:
            rows = conn.execute(
                'SELECT * FROM pick_tracking WHERE status = ?', ('active',)
            ).fetchall()

        if not rows:
            return {'updated': 0, 'exited': 0, 'still_active': 0}

        # Collect unique codes for batch loading
        codes = list(set(r['code'] for r in rows))
        min_date = min(r['pick_date'] for r in rows)

        # Get future data for all codes from local DB only (no API call)
        future_data = self._get_kline_batch_local(codes, min_date, end_date)

        updated = 0
        exited = 0
        still_active = 0

        for row in rows:
            code = row['code']
            if code not in future_data:
                still_active += 1
                continue

            df = future_data[code]
            # Find rows from pick_date onwards
            df_future = df[df['date'] >= row['pick_date']].reset_index(drop=True)
            if len(df_future) < 2:
                still_active += 1
                continue

            result = self._simulate_exit(row, df_future)
            updated += 1

            with self._get_conn() as conn:
                if result['exited']:
                    exited += 1
                    conn.execute('''UPDATE pick_tracking SET
                        status=?, exit_date=?, exit_price=?, exit_reason=?,
                        hold_days=?, max_price=?, min_price=?,
                        final_pnl_pct=?, max_pnl_pct=?, max_dd_pct=?
                        WHERE pick_date=? AND code=?''',
                        ('exited', result['exit_date'], result['exit_price'], result['exit_reason'],
                         result['hold_days'], result['max_price'], result['min_price'],
                         result['final_pnl_pct'], result['max_pnl_pct'], result['max_dd_pct'],
                         row['pick_date'], code))
                else:
                    still_active += 1
                    conn.execute('''UPDATE pick_tracking SET
                        hold_days=?, max_price=?, min_price=?,
                        final_pnl_pct=?, max_pnl_pct=?, max_dd_pct=?
                        WHERE pick_date=? AND code=?''',
                        (result['hold_days'], result['max_price'], result['min_price'],
                         result['final_pnl_pct'], result['max_pnl_pct'], result['max_dd_pct'],
                         row['pick_date'], code))

        return {'updated': updated, 'exited': exited, 'still_active': still_active}

    def _simulate_exit(self, pick, df):
        """
        Apply exit rules to a single pick's subsequent data.

        Returns dict with exit info and performance metrics.
        """
        entry_price = pick['entry_price']
        stop_loss = pick['stop_loss']
        pick_date = pick['pick_date']
        max_hold = int(self.cfg.get('max_hold_days'))
        trailing_pct = self.cfg.get('trailing_stop_pct')
        trailing_min_gain = self.cfg.get('trailing_min_gain')

        peak_price = entry_price
        min_price = entry_price
        max_price = entry_price
        exit_triggered = False
        exit_date = None
        exit_price = entry_price
        exit_reason = 'data_end'
        hold_days = 0

        # Skip the first row (it's the pick date itself)
        for i in range(1, len(df)):
            row = df.iloc[i]
            hold_days += 1
            close = row['close']
            low = row['low']
            high = row['high']
            date = row['date']

            peak_price = max(peak_price, high)
            max_price = max(max_price, close, high)
            min_price = min(min_price, low)

            # Rule 1: Stop loss
            if low <= stop_loss:
                exit_triggered = True
                exit_date = str(date)
                exit_price = stop_loss
                exit_reason = 'stop_loss'
                break

            # Rule 2: Trailing stop (after day 2, drawdown from peak > trailing_pct, total gain > trailing_min_gain)
            if hold_days > 2:
                drawdown_from_peak = (peak_price - close) / peak_price
                total_gain = (peak_price - entry_price) / entry_price
                if drawdown_from_peak > trailing_pct and total_gain > trailing_min_gain:
                    exit_triggered = True
                    exit_date = str(date)
                    exit_price = close
                    exit_reason = 'trailing_stop'
                    break

            # Rule 3: Time exit
            if hold_days >= max_hold:
                exit_triggered = True
                exit_date = str(date)
                exit_price = close
                exit_reason = 'time_exit'
                break

        final_pnl = (exit_price - entry_price) / entry_price
        max_pnl = (max_price - entry_price) / entry_price
        max_dd = (min_price - entry_price) / entry_price

        return {
            'exited': exit_triggered,
            'exit_date': exit_date,
            'exit_price': exit_price,
            'exit_reason': exit_reason,
            'hold_days': hold_days,
            'max_price': max_price,
            'min_price': min_price,
            'final_pnl_pct': final_pnl,
            'max_pnl_pct': max_pnl,
            'max_dd_pct': max_dd,
        }

    # ── Scorecard ─────────────────────────────────────────────────

    def get_scorecard(self, pick_date=None, lookback_days=90):
        """
        Generate a scorecard report for picks within the lookback window.

        Returns a dict with sections:
            summary, by_signal_type, by_market_regime, by_score_quartile,
            top_performers, worst_performers, score_predictive_power
        """
        if pick_date is None:
            pick_date = datetime.now().strftime('%Y-%m-%d')

        start_date = (datetime.strptime(pick_date, '%Y-%m-%d') - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        with self._get_conn() as conn:
            df = pd.read_sql(
                'SELECT * FROM pick_tracking WHERE pick_date >= ? AND pick_date <= ?',
                conn, params=(start_date, pick_date)
            )

        if df.empty:
            return None

        # Only consider exited picks for performance metrics
        exited = df[df['status'] == 'exited'].copy()
        all_picks = df.copy()

        if exited.empty:
            return {'summary': {'total_picks': len(all_picks), 'exited': 0, 'still_active': len(all_picks)},
                    'message': 'No picks have exited yet. Wait for more data.'}

        wins = exited[exited['final_pnl_pct'] > 0]
        losses = exited[exited['final_pnl_pct'] <= 0]
        win_rate = len(wins) / len(exited) * 100

        scorecard = {
            'summary': {
                'total_picks': len(all_picks),
                'exited': len(exited),
                'still_active': len(all_picks) - len(exited),
                'win_rate': round(win_rate, 1),
                'avg_pnl': round(exited['final_pnl_pct'].mean() * 100, 2),
                'avg_hold_days': round(exited['hold_days'].mean(), 1),
                'max_pnl': round(exited['final_pnl_pct'].max() * 100, 2),
                'min_pnl': round(exited['final_pnl_pct'].min() * 100, 2),
            },
        }

        # By signal type
        by_signal = {}
        for sig in exited['signal_type'].unique():
            sub = exited[exited['signal_type'] == sig]
            by_signal[sig] = {
                'count': len(sub),
                'win_rate': round((sub['final_pnl_pct'] > 0).mean() * 100, 1),
                'avg_pnl': round(sub['final_pnl_pct'].mean() * 100, 2),
                'avg_hold': round(sub['hold_days'].mean(), 1),
            }
        scorecard['by_signal_type'] = by_signal

        # By market regime
        by_regime = {}
        regime_map = {'bull': '上升期', 'range': '震荡期', 'bear': '退潮期'}
        for regime in ['bull', 'range', 'bear']:
            sub = exited[exited['market_regime'] == regime]
            if len(sub) > 0:
                by_regime[regime_map.get(regime, regime)] = {
                    'count': len(sub),
                    'win_rate': round((sub['final_pnl_pct'] > 0).mean() * 100, 1),
                    'avg_pnl': round(sub['final_pnl_pct'].mean() * 100, 2),
                }
        scorecard['by_market_regime'] = by_regime

        # By score quartile
        by_quartile = {}
        labels = ['Q1 (top 25%)', 'Q2', 'Q3', 'Q4 (bottom 25%)']
        try:
            exited_with_q = exited.copy()
            exited_with_q['quartile'] = pd.qcut(exited_with_q['score'], 4, labels=labels, duplicates='drop')
            for q in labels:
                sub = exited_with_q[exited_with_q['quartile'] == q]
                if len(sub) > 0:
                    by_quartile[q] = {
                        'count': len(sub),
                        'win_rate': round((sub['final_pnl_pct'] > 0).mean() * 100, 1),
                        'avg_pnl': round(sub['final_pnl_pct'].mean() * 100, 2),
                    }
        except Exception:
            pass
        scorecard['by_score_quartile'] = by_quartile

        # Score predictive power (Spearman-like rank correlation)
        try:
            corr = exited['score'].corr(exited['final_pnl_pct'], method='spearman')
            scorecard['score_predictive_power'] = round(corr, 3)
        except Exception:
            scorecard['score_predictive_power'] = None

        # Top/worst performers
        top5 = exited.nlargest(5, 'final_pnl_pct')[['code', 'name', 'signal_type', 'score', 'final_pnl_pct', 'hold_days']]
        worst5 = exited.nsmallest(5, 'final_pnl_pct')[['code', 'name', 'signal_type', 'score', 'final_pnl_pct', 'hold_days']]
        scorecard['top_performers'] = top5.to_dict('records')
        scorecard['worst_performers'] = worst5.to_dict('records')

        return scorecard

    def save_scorecard(self, scorecard_dict, report_date=None):
        """Persist scorecard metrics to the scorecard table."""
        if report_date is None:
            report_date = datetime.now().strftime('%Y-%m-%d')

        if scorecard_dict is None or 'summary' not in scorecard_dict:
            return

        metrics = []
        s = scorecard_dict['summary']
        for key in ['total_picks', 'exited', 'still_active', 'win_rate', 'avg_pnl', 'avg_hold_days']:
            if key in s:
                metrics.append((report_date, key, float(s[key]), int(s.get('exited', 0))))

        for sig_type, data in scorecard_dict.get('by_signal_type', {}).items():
            metrics.append((report_date, f'signal_{sig_type}_wr', data['win_rate'], data['count']))
            metrics.append((report_date, f'signal_{sig_type}_pnl', data['avg_pnl'], data['count']))

        for regime, data in scorecard_dict.get('by_market_regime', {}).items():
            metrics.append((report_date, f'regime_{regime}_wr', data['win_rate'], data['count']))

        if scorecard_dict.get('score_predictive_power') is not None:
            metrics.append((report_date, 'score_corr', scorecard_dict['score_predictive_power'], s.get('exited', 0)))

        with self._get_conn() as conn:
            for report_date_key, metric_key, metric_value, sample_size in metrics:
                conn.execute('''INSERT OR REPLACE INTO scorecard (report_date, metric_key, metric_value, sample_size)
                    VALUES (?,?,?,?)''', (report_date_key, metric_key, metric_value, sample_size))


# ── CLI: standalone update & report ───────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='选股跟踪模型')
    parser.add_argument('--action', choices=['update', 'scorecard', 'both'], default='both')
    parser.add_argument('--date', default=None, help='Date YYYY-MM-DD (default: today)')
    parser.add_argument('--lookback', type=int, default=90, help='Lookback days for scorecard')
    args = parser.parse_args()

    tracker = PickTracker()
    today = args.date or datetime.now().strftime('%Y-%m-%d')

    if args.action in ('update', 'both'):
        print(f'\n更新选股跟踪 (截至 {today})...')
        stats = tracker.update_tracking(end_date=today)
        print(f"  已更新 {stats['updated']} 只, 退出 {stats['exited']} 只, 仍活跃 {stats['still_active']} 只")

    if args.action in ('scorecard', 'both'):
        print(f'\n生成成绩单 (回溯 {args.lookback} 天)...')
        sc = tracker.get_scorecard(pick_date=today, lookback_days=args.lookback)
        tracker.save_scorecard(sc, report_date=today)

        if sc and 'summary' in sc:
            s = sc['summary']
            print(f"\n{'='*50}")
            print(f"选股成绩单 (截至 {today})")
            print(f"{'='*50}")
            print(f"  总选股: {s.get('total_picks', 0)} 只")
            print(f"  已退出: {s.get('exited', 0)} 只 | 仍活跃: {s.get('still_active', 0)} 只")
            if s.get('exited', 0) > 0:
                print(f"  胜率: {s.get('win_rate', 0):.1f}%")
                print(f"  平均盈亏: {color_pnl(s.get('avg_pnl', 0))}")
                print(f"  平均持仓: {s.get('avg_hold_days', 0):.1f} 天")
                print(f"  最大盈利: {color_pnl(s.get('max_pnl', 0))}")
                print(f"  最大亏损: {color_pnl(s.get('min_pnl', 0))}")
            if sc.get('score_predictive_power') is not None:
                corr = sc['score_predictive_power']
                print(f"\n  评分预测力 (Spearman相关): {corr:+.3f}")
                if abs(corr) > 0.3:
                    print(f"    -> 评分体系有效")
                else:
                    print(f"    -> 评分体系预测力不足")
            if 'by_signal_type' in sc:
                print(f"\n  按信号类型:")
                for sig, data in sc['by_signal_type'].items():
                    print(f"    {sig}: {data['count']}笔 胜率{data['win_rate']:.1f}% 平均{color_pnl(data['avg_pnl'])}")
            if 'by_market_regime' in sc:
                print(f"\n  按市场环境:")
                for regime, data in sc['by_market_regime'].items():
                    print(f"    {regime}: {data['count']}笔 胜率{data['win_rate']:.1f}% 平均{color_pnl(data['avg_pnl'])}")
        elif sc and 'message' in sc:
            print(f"  {sc['message']}")
        else:
            print(f"  回溯期内无选股记录")
