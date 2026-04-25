#!/usr/bin/env python3
"""
每日监控模块
职责：检测异常，预警通知，环境感知，不做主动调整
"""
import sys
import os
from datetime import datetime, timedelta  # timedelta 用于 _check_market_regime
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer, StockDataLayer
from strategy_config import StrategyConfig
from signal_constants import SIGNAL_TYPE_MAPPING


def calculate_expectancy(win_rate, avg_win, avg_loss):
    """
    计算期望值

    Args:
        win_rate: 胜率（0-1）
        avg_win: 平均盈利百分比（如 0.10 表示 10%）
        avg_loss: 平均亏损百分比（如 0.05 表示 5%）

    Returns:
        期望值（正值表示盈利预期）
    """
    return avg_win * win_rate - avg_loss * (1 - win_rate)


def wilson_expectancy_lower_bound(win_rate, avg_win, avg_loss, n, z=1.96):
    """
    Wilson置信区间下界期望值（保守估计）

    Args:
        win_rate: 胜率
        avg_win: 平均盈利
        avg_loss: 平均亏损
        n: 样本量
        z: 置信水平（1.96 = 95%）

    Returns:
        保守期望值估计
    """
    if n == 0:
        return 0.0

    p = win_rate
    # Wilson下界胜率
    denominator = 1 + z**2 / n
    p_lower = (p + z**2 / (2 * n) - z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denominator

    # 使用下界胜率计算期望值
    return avg_win * p_lower - avg_loss * (1 - p_lower)


class DailyMonitor:
    """每日监控器"""

    def __init__(self, db_path=None):
        if db_path is None:
            self.dl = get_data_layer()
        else:
            self.dl = StockDataLayer(db_path)
        self.cfg = StrategyConfig(db_path)

    def run(self, monitor_date=None):
        """
        运行每日监控

        Args:
            monitor_date: 监控日期，默认今天

        Returns:
            alerts: 预警列表
        """
        if monitor_date is None:
            monitor_date = datetime.now().strftime('%Y-%m-%d')

        alerts = []

        # 1. 检查信号期望值
        signal_alerts = self._check_signal_expectancy(monitor_date)
        alerts.extend(signal_alerts)

        # 2. 检查市场环境
        regime_alert = self._check_market_regime(monitor_date)
        if regime_alert:
            alerts.append(regime_alert)

        # 3. 更新 signal_status 表
        self._update_signal_status()

        # 4. 写入监控日志
        self._write_monitor_log(alerts, monitor_date)

        return alerts

    def _check_signal_expectancy(self, monitor_date):
        """检查各信号期望值"""
        alerts = []

        # 从 pick_tracking 获取各信号的退出数据
        # 注意：使用 TRIM(signal_type) 防止数据库中的尾随空格导致匹配失败
        with self.dl._get_conn() as conn:
            for signal_type in SIGNAL_TYPE_MAPPING.keys():
                rows = conn.execute("""
                    SELECT final_pnl_pct FROM pick_tracking
                    WHERE TRIM(signal_type)=? AND status='exited'
                """, (SIGNAL_TYPE_MAPPING[signal_type],)).fetchall()

                if len(rows) < 5:
                    # 样本不足，info级别
                    alerts.append({
                        'type': 'signal_sample_low',
                        'detail': f'{signal_type}: 仅{len(rows)}笔退出样本',
                        'severity': 'info'
                    })
                    continue

                pnls = [r[0] for r in rows]
                win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
                avg_win = np.mean([p for p in pnls if p > 0]) if any(p > 0 for p in pnls) else 0
                avg_loss = abs(np.mean([p for p in pnls if p < 0])) if any(p < 0 for p in pnls) else 0

                expectancy_lb = wilson_expectancy_lower_bound(win_rate, avg_win, avg_loss, len(pnls))

                if expectancy_lb < 0:
                    alerts.append({
                        'type': 'signal_expectancy_low',
                        'detail': f'{signal_type}: Wilson下界期望值={expectancy_lb:.2%}',
                        'severity': 'warning'
                    })

        return alerts

    def _check_market_regime(self, monitor_date):
        """检查市场环境"""
        # 获取上证指数数据（使用 get_index_kline 查询 index_daily 表）
        index_data = self.dl.get_index_kline('sh.000001',
            start_date=(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
            end_date=monitor_date
        )

        if index_data is None or len(index_data) < 20:
            return None

        regime, coeff, consecutive = self._get_market_regime_smoothed(index_data)

        # 更新 market_regime 表（传递 index_data 避免重复查询）
        self._update_market_regime_table(monitor_date, regime, coeff, consecutive, index_data)

        if regime == 'bear' and consecutive >= 5:
            return {
                'type': 'market_bear',
                'detail': f'退潮期已持续{consecutive}天，活跃度系数={coeff}',
                'severity': 'info'
            }

        return None

    def _get_market_regime_smoothed(self, index_data, new_value_weight=0.3):
        """增强的市场环境判断（连续天数+平滑处理）"""
        # 注意：datetime, timedelta 已在文件头部导入

        current = index_data['close'].iloc[-1]
        ma5 = index_data['close'].rolling(5).mean().iloc[-1]
        ma20 = index_data['close'].rolling(20).mean().iloc[-1]

        # 原始判断
        if current > ma5 > ma20:
            raw_regime = 'bull'
            raw_coeff = 1.0
        elif current > ma20 * 0.95:
            raw_regime = 'range'
            raw_coeff = 0.7
        else:
            raw_regime = 'bear'
            raw_coeff = 0.3

        # 连续天数计算
        regime_history = []
        for i in range(-5, 0):
            if i >= -len(index_data):
                c = index_data['close'].iloc[i]
                m5 = index_data['close'].rolling(5).mean().iloc[i]
                m20 = index_data['close'].rolling(20).mean().iloc[i]
                if c > m5 > m20:
                    regime_history.append('bull')
                elif c > m20 * 0.95:
                    regime_history.append('range')
                else:
                    regime_history.append('bear')

        consecutive_days = sum(1 for r in regime_history if r == raw_regime)

        # 连续5天确认才生效
        if consecutive_days >= 5:
            confirmed_regime = raw_regime
        else:
            # 从 market_regime 表读取上一状态
            prev = self._get_previous_regime()
            confirmed_regime = prev if prev else raw_regime

        # 平滑处理
        prev_coeff = self._get_previous_activity_coefficient()
        if prev_coeff:
            smoothed_coeff = prev_coeff * (1 - new_value_weight) + raw_coeff * new_value_weight
        else:
            smoothed_coeff = raw_coeff

        return confirmed_regime, smoothed_coeff, consecutive_days

    def _get_previous_regime(self):
        """获取上一交易日市场环境"""
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT regime_type FROM market_regime
                ORDER BY regime_date DESC LIMIT 1
            """).fetchone()
            return row[0] if row else None

    def _get_previous_activity_coefficient(self):
        """获取上一交易日活跃度系数"""
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT activity_coefficient FROM market_regime
                ORDER BY regime_date DESC LIMIT 1
            """).fetchone()
            return row[0] if row else None

    def _update_market_regime_table(self, date, regime, coeff, consecutive, index_data=None):
        """更新 market_regime 表"""
        if index_data is None or len(index_data) == 0:
            return

        close = index_data['close'].iloc[-1]
        ma5 = index_data['close'].rolling(5).mean().iloc[-1]
        ma20 = index_data['close'].rolling(20).mean().iloc[-1]

        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO market_regime
                (regime_date, regime_type, activity_coefficient, index_close, index_ma5, index_ma20, consecutive_days)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (date, regime, coeff, close, ma5, ma20, consecutive))

    def _update_signal_status(self):
        """更新 signal_status 表的 live_* 字段"""
        with self.dl._get_conn() as conn:
            for signal_type, display_name in SIGNAL_TYPE_MAPPING.items():
                # 获取该信号的退出数据
                # 注意：使用 TRIM(signal_type) 防止数据库中的尾随空格导致匹配失败
                rows = conn.execute("""
                    SELECT final_pnl_pct FROM pick_tracking
                    WHERE TRIM(signal_type)=? AND status='exited'
                """, (display_name,)).fetchall()

                if len(rows) == 0:
                    continue

                pnls = [r[0] for r in rows]
                win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
                avg_win = np.mean([p for p in pnls if p > 0]) if any(p > 0 for p in pnls) else 0
                avg_loss = abs(np.mean([p for p in pnls if p < 0])) if any(p < 0 for p in pnls) else 0

                expectancy = calculate_expectancy(win_rate, avg_win, avg_loss)
                expectancy_lb = wilson_expectancy_lower_bound(win_rate, avg_win, avg_loss, len(pnls))

                conn.execute("""
                    UPDATE signal_status SET
                        live_win_rate=?,
                        live_avg_win_pct=?,
                        live_avg_loss_pct=?,
                        live_expectancy=?,
                        live_expectancy_lb=?,
                        live_sample_count=?,
                        last_check_date=datetime('now')
                    WHERE signal_type=?
                """, (win_rate, avg_win, avg_loss, expectancy, expectancy_lb, len(pnls), signal_type))

    def _write_monitor_log(self, alerts, monitor_date):
        """写入监控日志"""
        with self.dl._get_conn() as conn:
            for alert in alerts:
                conn.execute("""
                    INSERT INTO daily_monitor_log
                    (monitor_date, alert_type, alert_detail, severity, action_taken)
                    VALUES (?, ?, ?, ?, 'logged')
                """, (monitor_date, alert['type'], alert['detail'], alert['severity']))

    def print_summary(self, alerts, monitor_date=None):
        """打印监控摘要"""
        date_str = monitor_date if monitor_date else datetime.now().strftime('%Y-%m-%d')
        print(f"\n[每日监控] {date_str}")

        if not alerts:
            print("  OK 无异常预警")
            return

        for alert in alerts:
            severity = alert['severity']
            if severity == 'critical':
                print(f"  X [{severity}] {alert['detail']}")
            elif severity == 'warning':
                print(f"  ! [{severity}] {alert['detail']}")
            else:
                print(f"  i [{severity}] {alert['detail']}")


if __name__ == '__main__':
    monitor = DailyMonitor()
    alerts = monitor.run()
    monitor.print_summary(alerts)