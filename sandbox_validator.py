#!/usr/bin/env python3
"""
沙盒验证模块
职责：验证优化结果在滚动窗口内的表现，防止一次性调整导致系统性风险
"""
import os
import sys
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer, StockDataLayer
from strategy_config import StrategyConfig
from signal_constants import SANDBOX_STATUS
from change_manager import ChangeManager


# 沙盒验证配置
SANDBOX_VALIDATION_CONFIG = {
    # 滚动窗口配置
    'validation_window_weeks': 3,      # 验证窗口：3周
    'min_validation_trades': 10,       # 最小验证交易数

    # 通过/失败阈值
    'pass_expectancy_threshold': 0.005, # 期望值阈值（通过）
    'pass_win_rate_threshold': 50,      # 胜率阈值（通过）
    'fail_expectancy_threshold': -0.02, # 期望值阈值（失败）
    'fail_win_rate_threshold': 40,      # 胜率阈值（失败）

    # 比较阈值
    'improvement_threshold': 0.002,     # 相比基准提升阈值

    # 状态管理
    'status_values': SANDBOX_STATUS,
    'max_pending_days': 21,             # pending状态最大天数
}


class SandboxValidator:
    """沙盒验证器"""

    def __init__(self, db_path=None):
        if db_path is None:
            self.dl = get_data_layer()
        else:
            self.dl = StockDataLayer(db_path)
        self.cfg = StrategyConfig(db_path)
        self.config = SANDBOX_VALIDATION_CONFIG
        self.change_mgr = ChangeManager(db_path)

    def validate_optimization(self, optimize_id=None, optimize_date=None, optimize_type=None):
        """
        验证单个优化结果

        Args:
            optimize_id: optimization_history 表的记录ID
            optimize_date: 优化日期
            optimize_type: 优化类型（params/score_weights/signal_status/environment）

        Returns:
            dict: {
                'status': 'passed' | 'failed' | 'pending',
                'metrics': dict,
                'comparison': dict,
                'decision': str,
            }
        """
        # 查找待验证的优化记录
        if optimize_id:
            with self.dl._get_conn() as conn:
                row = conn.execute("""
                    SELECT id, optimize_date, optimize_type, param_key, old_value, new_value,
                           sandbox_test_result, validation_started_at
                    FROM optimization_history WHERE id=?
                """, (optimize_id,)).fetchone()
        elif optimize_date and optimize_type:
            with self.dl._get_conn() as conn:
                row = conn.execute("""
                    SELECT id, optimize_date, optimize_type, param_key, old_value, new_value,
                           sandbox_test_result, validation_started_at
                    FROM optimization_history
                    WHERE optimize_date=? AND optimize_type=? AND sandbox_test_result='pending'
                    ORDER BY id DESC LIMIT 1
                """, (optimize_date, optimize_type)).fetchone()
        else:
            # 自动查找所有待验证记录
            return self._batch_validate_pending()

        if row is None:
            return {'status': 'not_found'}

        optimize_id = row[0]
        optimize_date = row[1]
        optimize_type = row[2]
        param_key = row[3]
        old_value = row[4]
        new_value = row[5]
        current_status = row[6]
        validation_started = row[7]

        # 检查是否已经 applied（防止重复应用）
        if current_status == SANDBOX_STATUS['APPLIED']:
            return {
                'status': 'already_applied',
                'optimize_id': optimize_id,
                'decision': 'skip',
            }

        # 检查是否在 pending 状态超过最大天数
        if validation_started:
            start_dt = datetime.strptime(validation_started, '%Y-%m-%d %H:%M:%S')
            days_pending = (datetime.now() - start_dt).days
            if days_pending > self.config['max_pending_days']:
                # 超时 → 自动失败
                self._update_status(optimize_id, SANDBOX_STATUS['FAILED'], 'timeout')
                return {
                    'status': 'failed',
                    'reason': 'pending_timeout',
                    'days_pending': days_pending,
                    'decision': 'reject',
                }

        # 计算验证窗口
        validation_end = datetime.now().strftime('%Y-%m-%d')
        validation_start = (datetime.now() - timedelta(weeks=self.config['validation_window_weeks'])).strftime('%Y-%m-%d')

        # 执行验证评估
        validation_result = self._evaluate_validation(
            optimize_type, param_key, new_value,
            validation_start, validation_end
        )

        if validation_result is None:
            # 数据不足 → 继续 pending
            if not validation_started:
                self._update_validation_started(optimize_id)
            return {
                'status': 'pending',
                'reason': 'insufficient_data',
                'decision': 'continue_monitoring',
            }

        # 判断通过/失败
        decision = self._make_decision(validation_result)

        if decision == 'pass':
            self._update_status(optimize_id, SANDBOX_STATUS['PASSED'], 'validation_passed')
            return {
                'status': 'passed',
                'metrics': validation_result['metrics'],
                'comparison': validation_result['comparison'],
                'decision': 'apply',
                'optimize_id': optimize_id,
            }
        elif decision == 'fail':
            self._update_status(optimize_id, SANDBOX_STATUS['FAILED'], 'validation_failed')
            # 回滚参数
            self._rollback_param(optimize_type, param_key, old_value)
            return {
                'status': 'failed',
                'metrics': validation_result['metrics'],
                'comparison': validation_result['comparison'],
                'decision': 'reject_and_rollback',
                'optimize_id': optimize_id,
            }
        else:
            # 继续观察
            if not validation_started:
                self._update_validation_started(optimize_id)
            return {
                'status': 'pending',
                'metrics': validation_result['metrics'],
                'decision': 'continue_monitoring',
            }

    def validate_batch(self, batch_id=None):
        """
        验证批次变更（使用ChangeManager）

        Args:
            batch_id: 批次ID，如果为None则验证所有待处理批次

        Returns:
            dict: {
                'status': 'batch_complete' | 'no_pending',
                'validated': int,
                'passed': int,
                'failed': int,
                'details': list
            }
        """
        if batch_id:
            staged = self.change_mgr.get_staged_params(batch_id)
        else:
            all_batches = self.change_mgr.get_all_staged_batches()
            staged = []
            for batch in all_batches:
                staged.extend(self.change_mgr.get_staged_params(batch['batch_id']))

        if not staged:
            return {'status': 'no_pending', 'validated': 0}

        # 更新状态为 validating
        for item in staged:
            self.change_mgr.update_status(item['id'], 'validating')

        # 执行验证逻辑
        results = []
        for item in staged:
            validation = self._validate_single_sandbox(item)

            if validation['passed']:
                self.change_mgr.update_status(item['id'], 'passed')
            else:
                self.change_mgr.reject_change(item['id'], validation['reason'])

            results.append({
                'sandbox_id': item['id'],
                'param_key': item['param_key'],
                'passed': validation['passed'],
                'reason': validation.get('reason'),
            })

        passed_count = sum(1 for r in results if r['passed'])

        return {
            'status': 'batch_complete',
            'validated': len(results),
            'passed': passed_count,
            'failed': len(results) - passed_count,
            'details': results,
        }

    def _validate_single_sandbox(self, item):
        """
        验证单个沙盒变更

        Args:
            item: sandbox_config记录 (from get_staged_params)

        Returns:
            dict: {'passed': bool, 'reason': str}
        """
        optimize_type = item['optimize_type']
        param_key = item['param_key']
        new_value = item['sandbox_value']

        # 计算验证窗口
        validation_end = datetime.now().strftime('%Y-%m-%d')
        validation_start = (datetime.now() - timedelta(weeks=self.config['validation_window_weeks'])).strftime('%Y-%m-%d')

        # 使用现有的 _evaluate_validation 方法
        validation_result = self._evaluate_validation(
            optimize_type, param_key, new_value,
            validation_start, validation_end
        )

        if validation_result is None:
            # 数据不足，保持 pending 状态
            return {'passed': False, 'reason': 'insufficient_data'}

        decision = self._make_decision(validation_result)

        if decision == 'pass':
            return {'passed': True, 'reason': 'validation_passed'}
        elif decision == 'fail':
            return {'passed': False, 'reason': 'validation_failed'}
        else:
            return {'passed': False, 'reason': 'continue_monitoring'}

    def apply_passed_changes(self, batch_id: str):
        """
        应用通过验证的变更

        Args:
            batch_id: 批次ID

        Returns:
            dict: {'applied': int}
        """
        staged = self.change_mgr.get_staged_params(batch_id)

        applied = 0
        for item in staged:
            if item['status'] == 'passed':
                if self.change_mgr.commit_change(item['id']):
                    applied += 1

        return {'applied': applied}

    def emergency_apply_changes(self, batch_id: str) -> dict:
        """
        紧急应用（绕过验证，直接 commit 所有 staged 变更）

        用于 critical 预警等紧急场景，不等待 3 周验证窗口。

        Args:
            batch_id: 批次ID

        Returns:
            dict: {'applied': int, 'details': list, 'reason': 'emergency_bypass_validation'}
        """
        staged = self.change_mgr.get_staged_params(batch_id)
        applied = 0
        details = []

        for item in staged:
            # 强制标记为 passed
            self.change_mgr.update_status(item['id'], 'passed')

            # 直接 commit（写入生产参数）
            if self.change_mgr.commit_change(item['id']):
                applied += 1
                details.append({
                    'param_key': item['param_key'],
                    'old_value': item['current_value'],
                    'new_value': item['sandbox_value'],
                    'status': 'emergency_applied',
                })

        # 批量更新 optimization_history（循环外一次性执行）
        if applied > 0:
            with self.dl._get_conn() as conn:
                conn.execute("""
                    UPDATE optimization_history
                    SET sandbox_test_result='emergency_applied'
                    WHERE batch_id=? AND sandbox_test_result='pending'
                """, (batch_id,))

        return {
            'applied': applied,
            'details': details,
            'reason': 'emergency_bypass_validation',
        }

    def _batch_validate_pending(self):
        """批量验证所有待验证记录"""
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT id, optimize_date, optimize_type, param_key, old_value, new_value
                FROM optimization_history
                WHERE sandbox_test_result = 'pending' OR sandbox_test_result IS NULL
                ORDER BY optimize_date DESC
            """).fetchall()

        if not rows:
            return {'status': 'no_pending', 'validated': 0}

        results = []
        for row in rows:
            result = self.validate_optimization(optimize_id=row[0])
            results.append(result)

        passed = sum(1 for r in results if r.get('status') == 'passed')
        failed = sum(1 for r in results if r.get('status') == 'failed')
        pending = sum(1 for r in results if r.get('status') == 'pending')

        return {
            'status': 'batch_complete',
            'validated': len(results),
            'passed': passed,
            'failed': failed,
            'pending': pending,
            'details': results,
        }

    def _evaluate_validation(self, optimize_type, param_key, new_value, start_date, end_date):
        """
        评估验证窗口内的表现

        Args:
            optimize_type: 优化类型
            param_key: 参数键
            new_value: 新参数值
            start_date: 验证开始日期
            end_date: 验证结束日期

        Returns:
            dict: {
                'metrics': {win_rate, expectancy, trade_count},
                'comparison': {baseline_win_rate, baseline_expectancy, improvement},
            }
        """
        # 获取验证窗口内的选股数据
        with self.dl._get_conn() as conn:
            # 根据参数类型选择验证方法
            if optimize_type == 'params':
                # 参数层：评估整体胜率和期望值
                trades = conn.execute("""
                    SELECT final_pnl_pct FROM pick_tracking
                    WHERE status='exited'
                    AND exit_date >= ? AND exit_date <= ?
                """, (start_date, end_date)).fetchall()

            elif optimize_type == 'score_weights':
                # 评分层：评估该评分项对应的信号表现
                score_key = param_key.replace('weight_', '')
                trades = conn.execute("""
                    SELECT final_pnl_pct, score FROM pick_tracking
                    WHERE status='exited'
                    AND exit_date >= ? AND exit_date <= ?
                    AND score IS NOT NULL
                """, (start_date, end_date)).fetchall()

            elif optimize_type == 'signal_status':
                # 信号层：评估该信号的表现
                from signal_constants import SIGNAL_TYPE_MAPPING
                display_name = SIGNAL_TYPE_MAPPING.get(param_key, param_key)
                trades = conn.execute("""
                    SELECT final_pnl_pct FROM pick_tracking
                    WHERE status='exited'
                    AND TRIM(signal_type)=?
                    AND exit_date >= ? AND exit_date <= ?
                """, (display_name, start_date, end_date)).fetchall()

            elif optimize_type == 'environment':
                # 环境层：评估整体表现（同params）
                trades = conn.execute("""
                    SELECT final_pnl_pct FROM pick_tracking
                    WHERE status='exited'
                    AND exit_date >= ? AND exit_date <= ?
                """, (start_date, end_date)).fetchall()

            else:
                return None

        if len(trades) < self.config['min_validation_trades']:
            return None

        pnls = [t[0] for t in trades]
        trade_count = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / trade_count * 100
        expectancy = np.mean(pnls)

        # 获取基准表现（使用旧参数的历史数据）
        baseline_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(weeks=self.config['validation_window_weeks'])).strftime('%Y-%m-%d')
        baseline_end = start_date

        with self.dl._get_conn() as conn:
            baseline_trades = conn.execute("""
                SELECT final_pnl_pct FROM pick_tracking
                WHERE status='exited'
                AND exit_date >= ? AND exit_date <= ?
            """, (baseline_start, baseline_end)).fetchall()

        if baseline_trades:
            baseline_pnls = [t[0] for t in baseline_trades]
            baseline_win_rate = sum(1 for p in baseline_pnls if p > 0) / len(baseline_pnls) * 100
            baseline_expectancy = np.mean(baseline_pnls)
        else:
            baseline_win_rate = 50
            baseline_expectancy = 0

        improvement = expectancy - baseline_expectancy

        return {
            'metrics': {
                'win_rate': win_rate,
                'expectancy': expectancy,
                'trade_count': trade_count,
            },
            'comparison': {
                'baseline_win_rate': baseline_win_rate,
                'baseline_expectancy': baseline_expectancy,
                'improvement': improvement,
            },
        }

    def _make_decision(self, validation_result):
        """
        根据验证结果做出决策

        Returns:
            'pass' | 'fail' | 'continue'
        """
        metrics = validation_result['metrics']
        comparison = validation_result['comparison']

        win_rate = metrics['win_rate']
        expectancy = metrics['expectancy']
        improvement = comparison['improvement']

        # 通过条件
        if (expectancy >= self.config['pass_expectancy_threshold'] and
            win_rate >= self.config['pass_win_rate_threshold'] and
            improvement >= self.config['improvement_threshold']):
            return 'pass'

        # 失败条件
        if (expectancy <= self.config['fail_expectancy_threshold'] or
            win_rate <= self.config['fail_win_rate_threshold']):
            return 'fail'

        # 继续观察
        return 'continue'

    def _update_status(self, optimize_id, status, reason):
        """更新优化记录的验证状态"""
        with self.dl._get_conn() as conn:
            # 只更新状态，不设置 apply_date（apply_date 在 mark_as_applied 时设置）
            conn.execute("""
                UPDATE optimization_history SET
                    sandbox_test_result=?
                WHERE id=?
            """, (status, optimize_id))

    def _update_validation_started(self, optimize_id):
        """更新验证开始时间"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                UPDATE optimization_history SET
                    sandbox_test_result='pending',
                    validation_started_at=datetime('now')
                WHERE id=?
            """, (optimize_id,))

    def _rollback_param(self, optimize_type, param_key, old_value):
        """回滚参数到旧值"""
        if optimize_type in ('params', 'score_weights', 'environment'):
            self.cfg.set(param_key, old_value)
        elif optimize_type == 'signal_status':
            from signal_constants import get_weight_multiplier
            weight_mult = get_weight_multiplier(old_value)
            with self.dl._get_conn() as conn:
                conn.execute("""
                    UPDATE signal_status SET
                        status_level=?, weight_multiplier=?
                    WHERE signal_type=?
                """, (old_value, weight_mult, param_key))

    def mark_as_applied(self, optimize_id):
        """标记优化已应用"""
        with self.dl._get_conn() as conn:
            conn.execute("""
                UPDATE optimization_history SET
                    sandbox_test_result=?,
                    apply_date=datetime('now')
                WHERE id=? AND sandbox_test_result=?
            """, (SANDBOX_STATUS['APPLIED'], optimize_id, SANDBOX_STATUS['PASSED']))

    def get_pending_optimizations(self):
        """获取所有待验证的优化记录（仅 pending 和 NULL 状态）"""
        with self.dl._get_conn() as conn:
            rows = conn.execute("""
                SELECT id, optimize_date, optimize_type, param_key, old_value, new_value,
                       sandbox_test_result, validation_started_at
                FROM optimization_history
                WHERE sandbox_test_result = 'pending' OR sandbox_test_result IS NULL
                ORDER BY optimize_date DESC
            """).fetchall()

        results = []
        for row in rows:
            results.append({
                'id': row[0],
                'optimize_date': row[1],
                'optimize_type': row[2],
                'param_key': row[3],
                'old_value': row[4],
                'new_value': row[5],
                'status': row[6] or 'pending',
                'validation_started': row[7],
            })

        return results

    def print_summary(self, pending_list=None):
        """打印待验证优化摘要"""
        if pending_list is None:
            pending_list = self.get_pending_optimizations()

        if not pending_list:
            print("\n[沙盒验证] 无待验证优化")
            return

        print(f"\n[沙盒验证] {len(pending_list)} 项待验证")

        for item in pending_list:
            status = item['status']
            date = item['optimize_date']
            type_ = item['optimize_type']
            key = item['param_key']
            old = item['old_value']
            new = item['new_value']

            if status == 'passed':
                print(f"  [passed] {date} [{type_}] {key}: {old} -> {new}")
            elif status == 'pending':
                started = item['validation_started']
                days = (datetime.now() - datetime.strptime(started, '%Y-%m-%d %H:%M:%S')).days if started else 0
                print(f"  [pending] {date} [{type_}] {key}: {old} -> {new} (观察{days}天)")
            else:
                print(f"  [{status}] {date} [{type_}] {key}: {old} -> {new}")


if __name__ == '__main__':
    validator = SandboxValidator()
    result = validator.validate_optimization()
    validator.print_summary()
    print(f"\n验证结果: {result}")