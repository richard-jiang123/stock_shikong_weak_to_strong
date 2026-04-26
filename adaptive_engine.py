#!/usr/bin/env python3
"""
自适应引擎核心控制器
职责：调度 daily_monitor + weekly_optimizer，处理 critical 预警
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer, StockDataLayer
from strategy_config import StrategyConfig
from daily_monitor import DailyMonitor
from weekly_optimizer import WeeklyOptimizer
from sandbox_validator import SandboxValidator
from change_manager import ChangeManager


class AdaptiveEngine:
    """自适应引擎核心控制器"""

    # 预警级别定义
    SEVERITY_LEVELS = ['info', 'warning', 'critical']

    # critical 预警处理配置
    CRITICAL_CONFIG = {
        'auto_disable_threshold': -0.05,   # 期望值低于此值自动禁用信号
        'min_sample_for_critical': 20,      # critical判断最小样本数
        'notification_methods': ['log', 'print'],  # 通知方式
    }

    def __init__(self, db_path=None):
        if db_path is None:
            self.dl = get_data_layer()
        else:
            self.dl = StockDataLayer(db_path)
        self.cfg = StrategyConfig(db_path)
        self.monitor = DailyMonitor(db_path)
        self.weekly_optimizer = WeeklyOptimizer(db_path)
        self.sandbox_validator = SandboxValidator(db_path)
        self.change_mgr = ChangeManager(db_path)

    def run_daily(self, monitor_date=None):
        """
        运行每日监控

        Args:
            monitor_date: 监控日期，默认今天

        Returns:
            dict: {
                'alerts': list,
                'critical_handled': int,
                'status': 'ok' | 'warning' | 'critical',
            }
        """
        if monitor_date is None:
            monitor_date = datetime.now().strftime('%Y-%m-%d')

        # 检查今天是否已经处理过 critical 预警（防止多次执行重复处理）
        critical_already_handled = self._check_critical_already_handled(monitor_date)

        # 执行每日监控（监控本身可以多次执行，数据更新时需要重新计算）
        alerts = self.monitor.run(monitor_date)

        # 处理 critical 预警（仅在首次执行时处理）
        critical_alerts = [a for a in alerts if a['severity'] == 'critical']
        critical_handled = 0

        if not critical_already_handled:
            for alert in critical_alerts:
                handled = self._handle_critical_alert(alert, monitor_date)
                if handled:
                    critical_handled += 1
                    # 记录已处理标记
                    self._mark_critical_handled(monitor_date, alert['type'])

        # 确定整体状态
        if critical_alerts:
            status = 'critical'
        elif any(a['severity'] == 'warning' for a in alerts):
            status = 'warning'
        else:
            status = 'ok'

        # 主动回滚监控
        rollback_result = self.change_mgr.monitor_and_rollback()

        if rollback_result['rollback_triggered'] > 0:
            self._notify_rollback_result(rollback_result)

        return {
            'alerts': alerts,
            'critical_handled': critical_handled,
            'status': status,
            'rollback_monitor': rollback_result,
        }

    def run_weekly(self, optimize_date=None, layers=None):
        """
        运行每周优化

        完整沙盒流程：
        1. 执行四层优化（变更暂存到 sandbox_config）
        2. 执行沙盒验证（sandbox_validator.validate_batch）
        3. 应用通过验证的变更（sandbox_validator.apply_passed_changes）
           - apply_passed_changes 内部调用 commit_change
           - commit_change 写入 strategy_config / signal_status（生产参数）
           - 标记 sandbox_config.status = 'applied'

        Args:
            optimize_date: 优化日期，默认今天
            layers: 要优化的层列表

        Returns:
            dict: {
                'optimization_results': dict,
                'sandbox_validation': dict,
                'applied': int,
                'rejected': int,
            }
        """
        if optimize_date is None:
            optimize_date = datetime.now().strftime('%Y-%m-%d')

        # 判断是否是周四（每周优化日）
        dt = datetime.strptime(optimize_date, '%Y-%m-%d')
        is_thursday = dt.weekday() == 3

        if not is_thursday:
            return {
                'optimization_results': None,
                'sandbox_validation': None,
                'applied': 0,
                'rejected': 0,
                'reason': 'not_thursday',
            }

        # 检查今天是否已经执行过优化（防止多次执行）
        if self._check_optimization_already_run(optimize_date):
            return {
                'optimization_results': None,
                'sandbox_validation': None,
                'applied': 0,
                'rejected': 0,
                'reason': 'already_run_today',
            }

        # 检查今天是否已经有新创建的 pending 记录（优化已跑但未验证完成）
        if self._check_has_today_pending(optimize_date):
            return {
                'optimization_results': None,
                'sandbox_validation': None,
                'applied': 0,
                'rejected': 0,
                'reason': 'pending_validation_in_progress',
            }

        # 1. 执行四层优化（变更暂存到 sandbox_config）
        optimization_results = self.weekly_optimizer.run(optimize_date, layers)
        batch_id = optimization_results.get('batch_id')

        # 2. 执行沙盒验证（使用 batch_id）
        sandbox_validation = self.sandbox_validator.validate_batch(batch_id)

        # 3. 应用通过验证的变更（内部调用 commit_change）
        # apply_passed_changes 流程：
        #   for item in passed_items:
        #       self.change_mgr.commit_change(item['id'])
        #           -> 写入 strategy_config / signal_status（生产参数）
        #           -> 标记 sandbox_config.status = 'applied'
        applied_result = self.sandbox_validator.apply_passed_changes(batch_id)

        # 统计结果
        applied = applied_result.get('applied', 0)
        rejected = sandbox_validation.get('failed', 0)

        return {
            'optimization_results': optimization_results,
            'sandbox_validation': sandbox_validation,
            'applied': applied,
            'rejected': rejected,
        }

    def _handle_critical_alert(self, alert, monitor_date):
        """
        处理 critical 级别预警

        Args:
            alert: 预警信息 dict
            monitor_date: 监控日期

        Returns:
            bool: 是否成功处理
        """
        alert_type = alert['type']
        detail = alert['detail']

        if alert_type == 'signal_expectancy_low':
            # 信号期望值过低 -> 考虑禁用
            return self._handle_signal_critical(alert, monitor_date)

        elif alert_type == 'market_bear':
            # 市场退潮期 -> 降低活跃度系数
            return self._handle_market_critical(alert, monitor_date)

        else:
            # 其他类型 -> 仅记录
            self._log_critical(alert, monitor_date)
            return True

    def _handle_signal_critical(self, alert, monitor_date):
        """
        处理信号期望值 critical 预警（通过沙盒流程）

        流程：生成批次ID -> 保存快照 -> 暂存变更 -> 紧急应用
        """
        from signal_constants import SIGNAL_TYPE_MAPPING, get_weight_multiplier
        from daily_monitor import wilson_expectancy_lower_bound
        import numpy as np

        handled = False

        # 1. 生成紧急批次 ID
        date_str = monitor_date.replace('-', '') + '-crit'
        batch_id = self.change_mgr.generate_batch_id(date_str)

        # 2. 保存快照（变更前状态）
        self.change_mgr.save_snapshot(
            trigger_reason='signal_critical',
            batch_id=batch_id,
            snapshot_type='pre_change'
        )

        # 3. 遍历信号类型，检查期望值下界
        for signal_type in SIGNAL_TYPE_MAPPING.keys():
            display_name = SIGNAL_TYPE_MAPPING[signal_type]

            with self.dl._get_conn() as conn:
                rows = conn.execute("""
                    SELECT final_pnl_pct FROM pick_tracking
                    WHERE TRIM(signal_type)=? AND status='exited'
                """, (display_name,)).fetchall()

            pnls = [r[0] for r in rows]
            sample_count = len(pnls)

            if sample_count < self.CRITICAL_CONFIG['min_sample_for_critical']:
                continue

            win_rate = sum(1 for p in pnls if p > 0) / sample_count
            avg_win = np.mean([p for p in pnls if p > 0]) if any(p > 0 for p in pnls) else 0
            avg_loss = abs(np.mean([p for p in pnls if p < 0])) if any(p < 0 for p in pnls) else 0
            expectancy_lb = wilson_expectancy_lower_bound(win_rate, avg_win, avg_loss, sample_count)

            if expectancy_lb < self.CRITICAL_CONFIG['auto_disable_threshold']:
                # 3a. 获取当前状态
                with self.dl._get_conn() as conn:
                    current_status = conn.execute("""
                        SELECT status_level FROM signal_status WHERE signal_type=?
                    """, (signal_type,)).fetchone()
                    current_status = current_status[0] if current_status else 'active'

                # 3b. 暂存变更到 sandbox_config（不直接写库）
                self.change_mgr.stage_change(
                    optimize_type='signal_status',
                    param_key=signal_type,
                    new_value='disabled',
                    batch_id=batch_id,
                    current_value=current_status
                )

                # 3c. 记录到优化历史
                with self.dl._get_conn() as conn:
                    conn.execute("""
                        INSERT INTO optimization_history
                        (optimize_date, optimize_type, param_key, old_value, new_value,
                         batch_id, trigger_reason, sandbox_test_result, created_at)
                        VALUES (?, 'signal_critical', ?, ?, ?, ?, 'auto_disable_expectancy', 'pending', datetime('now'))
                    """, (monitor_date, signal_type, current_status, 'disabled', batch_id))

                handled = True

        # 4. 紧急应用（绕过 3 周验证）
        if handled:
            result = self.sandbox_validator.emergency_apply_changes(batch_id)
            self._notify_critical(
                f"信号 critical 预警处理: {result['applied']} 项变更已紧急应用 (批次 {batch_id})"
            )

        return handled

    def _handle_market_critical(self, alert, monitor_date):
        """
        处理市场环境 critical 预警（通过沙盒流程）

        流程：生成批次ID -> 保存快照 -> 暂存变更 -> 紧急应用
        """
        # 1. 生成紧急批次 ID（带 -crit 后缀）
        date_str = monitor_date.replace('-', '') + '-crit'
        batch_id = self.change_mgr.generate_batch_id(date_str)

        # 2. 保存快照（变更前状态）
        self.change_mgr.save_snapshot(
            trigger_reason='market_critical',
            batch_id=batch_id,
            snapshot_type='pre_change'
        )

        # 3. 获取当前值并计算新值
        current_coeff = self.cfg.get('activity_coefficient')
        new_coeff = max(0.2, current_coeff * 0.8)

        # 4. 暂存变更（不直接写库）
        self.change_mgr.stage_change(
            optimize_type='strategy_config',
            param_key='activity_coefficient',
            new_value=new_coeff,
            batch_id=batch_id,
            current_value=current_coeff
        )

        # 5. 记录到优化历史（状态为 pending，由 emergency_apply 更新）
        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT INTO optimization_history
                (optimize_date, optimize_type, param_key, old_value, new_value,
                 batch_id, trigger_reason, sandbox_test_result, created_at)
                VALUES (?, 'market_critical', ?, ?, ?, ?, 'activity_coefficient_reduce', 'pending', datetime('now'))
            """, (monitor_date, 'activity_coefficient', current_coeff, new_coeff, batch_id))

        # 6. 紧急应用（绕过 3 周验证窗口）
        result = self.sandbox_validator.emergency_apply_changes(batch_id)

        # 7. 通知处理结果
        self._notify_critical(
            f"市场 critical 预警处理: 活跃度系数 {current_coeff:.2f} -> {new_coeff:.2f} "
            f"(批次 {batch_id}, 紧急应用 {result['applied']} 项)"
        )

        return True

    def _log_critical(self, alert, monitor_date):
        """记录 critical 预警"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT INTO daily_monitor_log
                (monitor_date, alert_type, alert_detail, severity, action_taken, created_at)
                VALUES (?, ?, ?, ?, 'logged', datetime('now'))
            """, (monitor_date, alert['type'], alert['detail'], 'critical'))

    def _check_critical_already_handled(self, monitor_date):
        """检查今天是否已经处理过 critical 预警"""
        with self.dl._get_conn() as conn:
            # 检查今天是否有 action_taken='handled' 的记录
            count = conn.execute("""
                SELECT COUNT(*) FROM daily_monitor_log
                WHERE monitor_date=? AND action_taken='handled'
            """, (monitor_date,)).fetchone()[0]
        return count > 0

    def _mark_critical_handled(self, monitor_date, alert_type):
        """标记今天已处理某个类型的 critical 预警"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                INSERT INTO daily_monitor_log
                (monitor_date, alert_type, alert_detail, severity, action_taken, created_at)
                VALUES (?, ?, '', 'critical', 'handled', datetime('now'))
            """, (monitor_date, alert_type))

    def _notify_critical(self, message):
        """发送 critical 通知"""
        for method in self.CRITICAL_CONFIG['notification_methods']:
            if method == 'print':
                print(f"\n[CRITICAL] {message}")
            elif method == 'log':
                with self.dl._get_conn() as conn:
                    conn.execute("""
                        INSERT INTO daily_monitor_log
                        (monitor_date, alert_type, alert_detail, severity, action_taken, created_at)
                        VALUES (?, 'critical_action', ?, 'critical', ?, datetime('now'))
                    """, (datetime.now().strftime('%Y-%m-%d'), message, 'notified'))

    def _apply_optimization(self, optimization_detail):
        """应用已验证通过的优化"""
        optimize_id = optimization_detail.get('optimize_id')
        if not optimize_id:
            return

        # 从 optimization_history 获取变更详情
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT optimize_type, param_key, new_value
                FROM optimization_history WHERE id=?
            """, (optimize_id,)).fetchone()

        if row is None:
            return

        optimize_type = row[0]
        param_key = row[1]
        new_value = row[2]

        # 实际应用变更
        if optimize_type in ('params', 'score_weights', 'environment'):
            self.cfg.set(param_key, new_value)
        elif optimize_type == 'signal_status':
            from signal_constants import get_weight_multiplier
            weight_mult = get_weight_multiplier(new_value)
            with self.dl._get_conn() as conn:
                conn.execute("""
                    UPDATE signal_status SET
                        status_level=?, weight_multiplier=?, last_check_date=datetime('now')
                    WHERE signal_type=?
                """, (new_value, weight_mult, param_key))

        # 标记为已应用
        self.sandbox_validator.mark_as_applied(optimize_id)

    def _check_optimization_already_run(self, optimize_date):
        """检查今天是否已经执行过每周优化（已完成验证的记录）"""
        with self.dl._get_conn() as conn:
            # 检查今天是否有优化记录（非 pending 状态）
            count = conn.execute("""
                SELECT COUNT(*) FROM optimization_history
                WHERE optimize_date=? AND sandbox_test_result != 'pending'
            """, (optimize_date,)).fetchone()[0]
        return count > 0

    def _check_has_today_pending(self, optimize_date):
        """检查今天是否有 pending 状态的优化记录（正在验证中）"""
        with self.dl._get_conn() as conn:
            count = conn.execute("""
                SELECT COUNT(*) FROM optimization_history
                WHERE optimize_date=? AND sandbox_test_result = 'pending'
            """, (optimize_date,)).fetchone()[0]
        return count > 0

    def _notify_rollback_result(self, rollback_result):
        """通知回滚结果"""
        for detail in rollback_result['details']:
            if detail['should_rollback']:
                batch_id = detail['batch_id']
                reason = detail['reason']

                print(f"\n[自动回滚] 批次 {batch_id}")
                print(f"  原因: {reason}")

    def get_status_summary(self):
        """
        获取当前系统状态摘要

        Returns:
            dict: {
                'signal_status': dict,
                'environment_status': dict,
                'pending_optimizations': int,
                'last_monitor_date': str,
                'last_optimize_date': str,
            }
        """
        summary = {}

        # 信号状态
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT signal_type, display_name, status_level, weight_multiplier,
                       live_expectancy_lb, live_sample_count
                FROM signal_status
            """).fetchall()

        signal_status = {}
        for row in rows:
            signal_status[row[0]] = {
                'display_name': row[1],
                'status': row[2],
                'weight_multiplier': row[3],
                'expectancy_lb': row[4],
                'sample_count': row[5],
            }
        summary['signal_status'] = signal_status

        # 环境状态
        activity_coeff = self.cfg.get('activity_coefficient')
        bull_threshold = self.cfg.get('bull_threshold')
        bear_threshold = self.cfg.get('bear_threshold')

        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT regime_date, regime_type, activity_coefficient, consecutive_days
                FROM market_regime
                ORDER BY regime_date DESC LIMIT 1
            """).fetchone()

        if row:
            summary['environment_status'] = {
                'regime_date': row[0],
                'regime_type': row[1],
                'activity_coefficient': row[2],
                'consecutive_days': row[3],
            }
        else:
            summary['environment_status'] = {
                'regime_type': 'unknown',
                'activity_coefficient': activity_coeff,
            }

        # 待验证优化数量
        pending = self.sandbox_validator.get_pending_optimizations()
        summary['pending_optimizations'] = len([p for p in pending if p['status'] == 'pending'])

        # 最后监控/优化日期
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT MAX(monitor_date) FROM daily_monitor_log
            """).fetchone()
            summary['last_monitor_date'] = row[0] if row else None

            row = conn.execute("""
                SELECT MAX(optimize_date) FROM optimization_history
            """).fetchone()
            summary['last_optimize_date'] = row[0] if row else None

        return summary

    def print_status_summary(self):
        """打印系统状态摘要"""
        summary = self.get_status_summary()

        print("\n" + "="*60)
        print("自适应引擎状态摘要")
        print("="*60)

        # 信号状态
        print("\n[信号状态]")
        for signal_type, data in summary['signal_status'].items():
            status = data['status']
            weight = data['weight_multiplier']
            exp_lb = data['expectancy_lb']
            samples = data['sample_count']

            status_symbol = {'active': '', 'warning': '!', 'disabled': 'X'}
            symbol = status_symbol.get(status, '?')

            print(f"  {symbol} {signal_type}: {status} 权重={weight:.1f} 样本={samples}")
            if exp_lb is not None:
                print(f"    期望值下界: {exp_lb:.2%}")

        # 环境状态
        print("\n[市场环境]")
        env = summary['environment_status']
        regime = env['regime_type']
        coeff = env['activity_coefficient']
        days = env.get('consecutive_days', 0)

        regime_cn = {'bull': '上升期', 'range': '震荡期', 'bear': '退潮期'}
        print(f"  环境: {regime_cn.get(regime, regime)} 连续{days}天")
        print(f"  活跃度系数: {coeff:.2f}")

        # 待处理
        pending = summary['pending_optimizations']
        if pending > 0:
            print(f"\n[待验证] {pending} 项优化待确认")

        # 最后运行日期
        if summary['last_monitor_date']:
            print(f"\n最后监控: {summary['last_monitor_date']}")
        if summary['last_optimize_date']:
            print(f"最后优化: {summary['last_optimize_date']}")

        print("\n" + "="*60)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='自适应引擎')
    parser.add_argument('--mode', choices=['daily', 'weekly', 'status'], default='status')
    parser.add_argument('--date', default=None, help='指定日期 YYYY-MM-DD')
    args = parser.parse_args()

    engine = AdaptiveEngine()

    if args.mode == 'daily':
        result = engine.run_daily(args.date)
        engine.monitor.print_summary(result['alerts'], args.date)
        print(f"\n整体状态: {result['status']}")
        print(f"Critical处理: {result['critical_handled']}")

    elif args.mode == 'weekly':
        result = engine.run_weekly(args.date)
        if result['optimization_results']:
            engine.weekly_optimizer.print_summary(result['optimization_results'], args.date)
            print(f"\n应用: {result['applied']}, 拒绝: {result['rejected']}")
        else:
            print(f"\n未执行优化: {result['reason']}")

    elif args.mode == 'status':
        engine.print_status_summary()