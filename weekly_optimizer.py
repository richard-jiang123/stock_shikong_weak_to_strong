#!/usr/bin/env python3
"""
每周四层优化调度模块
职责：每周执行参数层/评分层/信号层/环境层优化
"""
import os
import sys
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer, StockDataLayer
from strategy_config import StrategyConfig
from strategy_optimizer import StrategyOptimizer
from signal_constants import SIGNAL_TYPE_MAPPING, get_weight_multiplier
from normalizer import ScoreNormalizer, SCORE_DIMENSIONS
from change_manager import ChangeManager


def adjust_score_weight(current_weight, correlation, base_weight=1.0, momentum=0.3, history=None):
    """
    根据评分-盈亏相关性动态调整权重

    Args:
        current_weight: 当前权重值
        correlation: Spearman相关系数（score vs final_pnl）
        base_weight: 基准权重（默认1.0），用于回归锚点
        momentum: 动量参数（保留接口兼容性，当前实现未使用）
        history: 历史权重列表（保留接口兼容性，当前实现未使用）

    Returns:
        新权重值（调整幅度限制在 ±20%）
    """
    # 调整幅度限制
    MAX_ADJUSTMENT = 0.20
    MIN_DEVIATION = 0.05  # 最小偏离保护

    if correlation > 0.3:
        # 强正相关 → 加权（最多增加20%）
        adjustment = min(MAX_ADJUSTMENT, correlation * 0.5)
        new_weight = current_weight * (1 + adjustment)
    elif correlation < -0.2:
        # 负相关 → 减权（最多减少20%）
        adjustment = min(MAX_ADJUSTMENT, abs(correlation) * 0.5)
        new_weight = current_weight * (1 - adjustment)
    else:
        # 弱相关 → 动量机制减缓回归速度
        delta = current_weight - base_weight
        if abs(delta) > MIN_DEVIATION:
            # 动量机制：减缓回归速度
            regression_factor = 0.05
            new_weight = current_weight - delta * regression_factor
        else:
            # 偏离不足 MIN_DEVIATION，保持当前权重
            new_weight = current_weight

    return new_weight


class WeeklyOptimizer:
    """每周四层优化调度器"""

    # 四层优化配置
    OPTIMIZATION_LAYERS = {
        'params': {
            'description': '参数层：调整策略参数（first_wave_min_gain等）',
            'method': 'coordinate_descent',
            'frequency': 'weekly',
            'requires_sandbox': True,
        },
        'score_weights': {
            'description': '评分层：调整评分权重系数',
            'method': 'correlation_based',
            'frequency': 'weekly',
            'requires_sandbox': True,
        },
        'signal_status': {
            'description': '信号层：调整信号状态（active/warning/disabled）',
            'method': 'expectancy_based',
            'frequency': 'weekly',
            'requires_sandbox': True,
        },
        'environment': {
            'description': '环境层：调整市场环境系数',
            'method': 'regime_based',
            'frequency': 'weekly',
            'requires_sandbox': True,  # 环境参数走沙盒流程
        },
    }

    # 信号状态调整阈值
    SIGNAL_THRESHOLDS = {
        'disable_expectancy_lb': -0.02,  # Wilson下界期望值低于此值则考虑禁用
        'warning_expectancy_lb': -0.01,  # 低于此值则进入warning状态
        'min_sample_for_disable': 15,     # 禁用需要的最小样本数
        'min_sample_for_warning': 10,     # warning需要的最小样本数
        'recovery_expectancy_lb': 0.01,   # 恢复到active的阈值
    }

    def __init__(self, db_path=None):
        if db_path is None:
            self.dl = get_data_layer()
        else:
            self.dl = StockDataLayer(db_path)
        self.cfg = StrategyConfig(db_path)
        self.optimizer = StrategyOptimizer(db_path)
        self.change_mgr = ChangeManager(db_path)

    def run(self, optimize_date=None, layers=None):
        """
        执行四层优化

        Args:
            optimize_date: 优化日期，默认今天
            layers: 要优化的层列表，默认全部四层

        Returns:
            dict: 各层优化结果
        """
        if optimize_date is None:
            optimize_date = datetime.now().strftime('%Y-%m-%d')

        if layers is None:
            layers = ['params', 'score_weights', 'signal_status', 'environment']

        # 生成批次ID并保存快照
        batch_id = self.change_mgr.generate_batch_id(optimize_date.replace('-', ''))
        snapshot_id = self.change_mgr.save_snapshot(
            trigger_reason='weekly_optimize',
            batch_id=batch_id
        )
        results = {}
        results['batch_id'] = batch_id
        results['snapshot_id'] = snapshot_id

        # 1. 参数层优化
        if 'params' in layers:
            results['params'] = self._optimize_params_layer(optimize_date)
            # 暂存参数变更
            for key, change in results['params'].get('changes', {}).items():
                self.change_mgr.stage_change(
                    optimize_type='strategy_config',
                    param_key=key,
                    new_value=change['new'],
                    batch_id=batch_id,
                    current_value=change['old']
                )

        # 2. 评分层优化
        if 'score_weights' in layers:
            results['score_weights'] = self._optimize_score_weights_layer(optimize_date)
            # 暂存权重变更
            for key, change in results['score_weights'].get('weight_changes', {}).items():
                self.change_mgr.stage_change(
                    optimize_type='strategy_config',
                    param_key=key,
                    new_value=change['new'],
                    batch_id=batch_id,
                    current_value=change['old']
                )

        # 3. 信号层优化
        if 'signal_status' in layers:
            results['signal_status'] = self._optimize_signal_status_layer(optimize_date)
            # 暂存信号状态变更
            for signal_type, change in results['signal_status'].get('status_changes', {}).items():
                self.change_mgr.stage_change(
                    optimize_type='signal_status',
                    param_key=signal_type,
                    new_value=change['new_status'],
                    batch_id=batch_id,
                    current_value=change['old_status']
                )

        # 4. 环境层优化
        if 'environment' in layers:
            results['environment'] = self._optimize_environment_layer(optimize_date)
            # 暂存环境参数变更
            for key, change in results['environment'].get('threshold_updates', {}).items():
                self.change_mgr.stage_change(
                    optimize_type='strategy_config',
                    param_key=key,
                    new_value=change['new'],
                    batch_id=batch_id,
                    current_value=change['old']
                )
            # 暂存 activity_coefficient（从 market_regime 获取）
            if results['environment'].get('activity_coefficient') is not None:
                current_coeff = self.cfg.get('activity_coefficient')
                new_coeff = results['environment']['activity_coefficient']
                if abs(new_coeff - current_coeff) > 0.01:
                    self.change_mgr.stage_change(
                        optimize_type='strategy_config',
                        param_key='activity_coefficient',
                        new_value=new_coeff,
                        batch_id=batch_id,
                        current_value=current_coeff
                    )

        # 记录优化历史
        self._record_optimization_history(results, optimize_date)

        return results

    def _optimize_params_layer(self, optimize_date):
        """
        参数层优化：使用 StrategyOptimizer 进行坐标下降

        Returns:
            dict: {
                'optimized': bool,
                'best_params': dict,
                'metrics': dict,
                'changes': dict,
            }
        """
        # 获取股票池
        with self.dl._get_conn() as conn:
            codes = [r[0] for r in conn.execute(
                "SELECT DISTINCT code FROM stock_daily WHERE code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%'"
            ).fetchall()]

        if len(codes) < 50:
            return {'optimized': False, 'reason': 'insufficient_stock_pool'}

        # 使用一年的数据进行优化
        end_date = optimize_date
        start_date = (datetime.strptime(optimize_date, '%Y-%m-%d') - timedelta(days=365)).strftime('%Y-%m-%d')

        # 执行坐标下降优化
        best_params, history = self.optimizer.coordinate_descent(
            start_date, end_date, codes,
            max_rounds=3, sample_size=200
        )

        if best_params is None:
            return {'optimized': False, 'reason': 'no_valid_optimization'}

        # 计算参数变化
        current_params = self.cfg.get_dict()
        changes = {}
        for key in best_params:
            if key in current_params and abs(best_params[key] - current_params[key]) > 1e-6:
                changes[key] = {
                    'old': current_params[key],
                    'new': best_params[key],
                    'change_pct': (best_params[key] - current_params[key]) / max(abs(current_params[key]), 1e-6) * 100
                }

        # 获取最终指标
        final_metrics = history[-1]['metrics'] if history else None

        return {
            'optimized': True,
            'best_params': best_params,
            'metrics': final_metrics,
            'changes': changes,
        }

    def _optimize_score_weights_layer(self, optimize_date):
        """
        评分层优化：根据 score-pnl 相关性调整权重

        Returns:
            dict: {
                'adjusted': bool,
                'weight_changes': dict,
                'correlations': dict,
                'history_meta': dict,
            }
        """
        # 1. 检查历史统计置信度
        normalizer = ScoreNormalizer(data_layer=self.dl)  # 复用已有数据层实例
        history_stats, meta = normalizer.get_history_stats()

        if meta['confidence'] == 'low':
            return {
                'adjusted': False,
                'reason': f'样本不足({meta["n"]}笔)，暂不调整权重',
                'history_meta': meta,
            }

        # 2. 获取各评分项与盈亏的相关性
        correlations = self._compute_score_correlations()

        if correlations is None:
            return {'adjusted': False, 'reason': 'insufficient_correlation_data', 'history_meta': meta}

        # 3. 获取当前权重
        current_weights = self.cfg.get_weights()

        # 4. 权重键名到相关性键名的映射
        # 注意：weight_strong_gain 对应 score_day_gain（数据库字段名）
        WEIGHT_TO_SCORE = {
            'weight_strong_gain': 'score_day_gain',  # 特殊映射
        }

        # 5. 调整权重
        weight_changes = {}
        for weight_key in current_weights:
            # 找对应的相关性指标
            score_key = WEIGHT_TO_SCORE.get(weight_key, weight_key.replace('weight_', 'score_'))
            if score_key in correlations:
                correlation = correlations[score_key]
                old_weight = current_weights[weight_key]
                new_weight = adjust_score_weight(old_weight, correlation)

                if abs(new_weight - old_weight) > 0.01:
                    weight_changes[weight_key] = {
                        'old': old_weight,
                        'new': new_weight,
                        'correlation': correlation,
                    }

        # 6. 验证归一化效果（如果有调整）
        if weight_changes:
            # 模拟一只"平均股票"，验证归一化后总分是否稳定
            avg_scores = {dim: history_stats.get(f'avg_{dim}', 10) for dim in SCORE_DIMENSIONS}

            # 更新权重
            updated_weights = current_weights.copy()
            for k, v in weight_changes.items():
                updated_weights[k] = v['new']

            normalized_avg, verify_meta = normalizer.normalize_scores(avg_scores, updated_weights)

            expected_avg = sum(history_stats.get(f'avg_{dim}', 10) for dim in SCORE_DIMENSIONS)
            deviation = abs(normalized_avg - expected_avg)

            if deviation > 5:  # 允许5分偏差
                return {
                    'adjusted': False,
                    'reason': f'归一化验证失败：偏差{deviation:.1f}分',
                    'weight_changes': weight_changes,
                    'history_meta': meta,
                }

            # 权重变更已通过 stage_change() 暂存到 sandbox_config

        return {
            'adjusted': len(weight_changes) > 0,
            'weight_changes': weight_changes,
            'correlations': correlations,
            'history_meta': meta,
        }

    def _compute_score_correlations(self):
        """
        计算各评分项与最终盈亏的 Spearman 相关性

        Returns:
            dict: {score_key: correlation}
        """
        with self.dl._get_conn() as conn:
            # 只使用已退出的选股数据
            df_data = conn.execute("""
                SELECT score_wave_gain, score_shallow_dd, score_day_gain,
                       score_volume, score_ma_bull, score_sector,
                       score_signal_bonus, score, final_pnl_pct
                FROM pick_tracking
                WHERE status='exited' AND final_pnl_pct IS NOT NULL
            """).fetchall()

        if len(df_data) < 30:
            return None

        # 转换为 DataFrame
        import pandas as pd
        df = pd.DataFrame(df_data, columns=[
            'score_wave_gain', 'score_shallow_dd', 'score_day_gain',
            'score_volume', 'score_ma_bull', 'score_sector',
            'score_signal_bonus', 'score', 'final_pnl_pct'
        ])

        # 计算各评分项的相关性
        correlations = {}
        score_cols = ['score_wave_gain', 'score_shallow_dd', 'score_day_gain',
                      'score_volume', 'score_ma_bull', 'score_sector',
                      'score_signal_bonus', 'score']

        for col in score_cols:
            if col in df.columns and df[col].notna().sum() > 10:
                corr = df[col].corr(df['final_pnl_pct'], method='spearman')
                correlations[col] = corr

        return correlations

    def _optimize_signal_status_layer(self, optimize_date):
        """
        信号层优化：根据期望值调整信号状态

        Returns:
            dict: {
                'status_changes': dict,
                'expectancy_metrics': dict,
            }
        """
        status_changes = {}
        expectancy_metrics = {}

        for signal_type in SIGNAL_TYPE_MAPPING.keys():
            # 获取该信号的退出数据
            display_name = SIGNAL_TYPE_MAPPING[signal_type]

            with self.dl._get_conn() as conn:
                # 获取当前状态
                current_status = conn.execute("""
                    SELECT status_level, weight_multiplier, live_expectancy_lb, live_sample_count
                    FROM signal_status WHERE signal_type=?
                """, (signal_type,)).fetchone()

                # 获取退出样本
                rows = conn.execute("""
                    SELECT final_pnl_pct FROM pick_tracking
                    WHERE TRIM(signal_type)=? AND status='exited'
                """, (display_name,)).fetchall()

            if current_status is None:
                continue

            pnls = [r[0] for r in rows]
            sample_count = len(pnls)

            if sample_count < self.SIGNAL_THRESHOLDS['min_sample_for_warning']:
                expectancy_metrics[signal_type] = {
                    'sample_count': sample_count,
                    'status': current_status[0],
                    'action': 'insufficient_sample',
                }
                continue

            # 计算期望值
            win_rate = sum(1 for p in pnls if p > 0) / sample_count
            avg_win = np.mean([p for p in pnls if p > 0]) if any(p > 0 for p in pnls) else 0
            avg_loss = abs(np.mean([p for p in pnls if p < 0])) if any(p < 0 for p in pnls) else 0

            from daily_monitor import wilson_expectancy_lower_bound
            expectancy_lb = wilson_expectancy_lower_bound(win_rate, avg_win, avg_loss, sample_count)

            current_status_level = current_status[0]

            # 判断是否需要调整状态
            new_status = None
            action = None

            if expectancy_lb < self.SIGNAL_THRESHOLDS['disable_expectancy_lb']:
                if sample_count >= self.SIGNAL_THRESHOLDS['min_sample_for_disable']:
                    if current_status_level != 'disabled':
                        new_status = 'disabled'
                        action = 'disable'
            elif expectancy_lb < self.SIGNAL_THRESHOLDS['warning_expectancy_lb']:
                if sample_count >= self.SIGNAL_THRESHOLDS['min_sample_for_warning']:
                    if current_status_level == 'active':
                        new_status = 'warning'
                        action = 'warning'
            elif current_status_level in ('warning', 'disabled'):
                # 检查是否可以恢复
                if expectancy_lb >= self.SIGNAL_THRESHOLDS['recovery_expectancy_lb']:
                    new_status = 'active'
                    action = 'recovery'

            if new_status and new_status != current_status_level:
                status_changes[signal_type] = {
                    'old_status': current_status_level,
                    'new_status': new_status,
                    'expectancy_lb': expectancy_lb,
                    'sample_count': sample_count,
                    'action': action,
                }

            expectancy_metrics[signal_type] = {
                'sample_count': sample_count,
                'win_rate': win_rate,
                'expectancy_lb': expectancy_lb,
                'status': current_status_level,
                'action': action or 'maintain',
            }

        return {
            'status_changes': status_changes,
            'expectancy_metrics': expectancy_metrics,
        }

    def _optimize_environment_layer(self, optimize_date):
        """
        环境层优化：根据市场环境调整活跃度系数

        Returns:
            dict: {
                'regime_type': str,
                'activity_coefficient': float,
                'threshold_updates': dict,
            }
        """
        # 获取当前市场环境
        regime_data = self._get_current_regime(optimize_date)

        if regime_data is None:
            return {'adjusted': False, 'reason': 'no_regime_data'}

        regime_type = regime_data['regime_type']
        consecutive_days = regime_data['consecutive_days']

        # 环境阈值配置
        threshold_updates = {}

        if regime_type == 'bull':
            # 上升期：增加活跃度系数
            current_coeff = self.cfg.get('bull_threshold')
            # 连续确认天数越长，系数越高（最高1.2）
            new_coeff = min(1.2, current_coeff + consecutive_days * 0.02)
            if abs(new_coeff - current_coeff) > 0.01:
                threshold_updates['bull_threshold'] = {
                    'old': current_coeff,
                    'new': new_coeff,
                }

        elif regime_type == 'bear':
            # 退潮期：降低活跃度系数
            current_coeff = self.cfg.get('bear_threshold')
            # 连续确认天数越长，系数越低（最低0.2）
            new_coeff = max(0.2, current_coeff - consecutive_days * 0.02)
            if abs(new_coeff - current_coeff) > 0.01:
                threshold_updates['bear_threshold'] = {
                    'old': current_coeff,
                    'new': new_coeff,
                }

        # activity_coefficient 由 regime 数据提供，已通过返回值传递

        return {
            'adjusted': len(threshold_updates) > 0,
            'regime_type': regime_type,
            'activity_coefficient': regime_data['activity_coefficient'],
            'consecutive_days': consecutive_days,
            'threshold_updates': threshold_updates,
        }

    def _get_current_regime(self, optimize_date):
        """获取当前市场环境状态"""
        with self.dl._get_conn() as conn:
            row = conn.execute("""
                SELECT regime_type, activity_coefficient, consecutive_days
                FROM market_regime
                WHERE regime_date <= ?
                ORDER BY regime_date DESC LIMIT 1
            """, (optimize_date,)).fetchone()

        if row is None:
            return None

        return {
            'regime_type': row[0],
            'activity_coefficient': row[1],
            'consecutive_days': row[2] or 0,
        }

    def _record_optimization_history(self, results, optimize_date):
        """记录优化历史"""
        batch_id = results.get('batch_id')
        snapshot_id = results.get('snapshot_id')
        with self.dl._get_conn() as conn:
            for layer, result in results.items():
                # Skip batch_id and snapshot_id entries
                if layer in ('batch_id', 'snapshot_id'):
                    continue
                if result.get('optimized') or result.get('adjusted'):
                    # 记录主要参数变化
                    if layer == 'params':
                        for key, change in result.get('changes', {}).items():
                            conn.execute("""
                                INSERT INTO optimization_history
                                (optimize_date, optimize_type, param_key, old_value, new_value, batch_id, snapshot_id, trigger_reason, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, 'weekly_optimize', datetime('now'))
                            """, (optimize_date, layer, key, change['old'], change['new'], batch_id, snapshot_id))

                    elif layer == 'score_weights':
                        for key, change in result.get('weight_changes', {}).items():
                            conn.execute("""
                                INSERT INTO optimization_history
                                (optimize_date, optimize_type, param_key, old_value, new_value, batch_id, snapshot_id, trigger_reason, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, 'weekly_optimize', datetime('now'))
                            """, (optimize_date, layer, key, change['old'], change['new'], batch_id, snapshot_id))

                    elif layer == 'signal_status':
                        for signal_type, change in result.get('status_changes', {}).items():
                            conn.execute("""
                                INSERT INTO optimization_history
                                (optimize_date, optimize_type, param_key, old_value, new_value, batch_id, snapshot_id, trigger_reason, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, 'weekly_optimize', datetime('now'))
                            """, (optimize_date, layer, signal_type, change['old_status'], change['new_status'], batch_id, snapshot_id))

                    elif layer == 'environment':
                        for key, change in result.get('threshold_updates', {}).items():
                            conn.execute("""
                                INSERT INTO optimization_history
                                (optimize_date, optimize_type, param_key, old_value, new_value, batch_id, snapshot_id, trigger_reason, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, 'weekly_optimize', datetime('now'))
                            """, (optimize_date, layer, key, change['old'], change['new'], batch_id, snapshot_id))

    def print_summary(self, results, optimize_date=None):
        """打印优化摘要"""
        date_str = optimize_date if optimize_date else datetime.now().strftime('%Y-%m-%d')
        batch_id = results.get('batch_id')
        print(f"\n[每周优化] {date_str}")
        if batch_id:
            print(f"  批次ID: {batch_id}  快照ID: {results.get('snapshot_id')}")

        for layer, result in results.items():
            # Skip batch_id and snapshot_id entries
            if layer in ('batch_id', 'snapshot_id'):
                continue
            layer_desc = self.OPTIMIZATION_LAYERS[layer]['description']
            print(f"\n  [{layer}] {layer_desc}")

            if layer == 'params':
                if result.get('optimized'):
                    metrics = result.get('metrics', {})
                    if metrics:
                        print(f"    胜率={metrics['win_rate']:.1f}% 期望={metrics['expectancy']:+.2f}%")
                    changes = result.get('changes', {})
                    for key, change in changes.items():
                        print(f"    {key}: {change['old']:.4f} -> {change['new']:.4f} ({change['change_pct']:+.1f}%)")
                else:
                    print(f"    未优化: {result.get('reason', 'unknown')}")

            elif layer == 'score_weights':
                if result.get('adjusted'):
                    correlations = result.get('correlations', {})
                    changes = result.get('weight_changes', {})
                    print(f"    相关性: {len(correlations)} 项评估")
                    for key, change in changes.items():
                        print(f"    {key}: {change['old']:.2f} -> {change['new']:.2f} (corr={change['correlation']:.3f})")
                else:
                    print(f"    未调整")

            elif layer == 'signal_status':
                changes = result.get('status_changes', {})
                metrics = result.get('expectancy_metrics', {})
                print(f"    信号状态: {len(changes)} 项调整")
                for signal_type, change in changes.items():
                    print(f"    {signal_type}: {change['old_status']} -> {change['new_status']} (exp_lb={change['expectancy_lb']:.2%})")
                for signal_type, m in metrics.items():
                    if m.get('action') != 'maintain':
                        print(f"    {signal_type}: 样本{m['sample_count']} 状态{m['status']} {m['action']}")

            elif layer == 'environment':
                regime = result.get('regime_type', 'unknown')
                coeff = result.get('activity_coefficient', 1.0)
                days = result.get('consecutive_days', 0)
                print(f"    环境: {regime} 连续{days}天 活跃度={coeff:.2f}")
                changes = result.get('threshold_updates', {})
                for key, change in changes.items():
                    print(f"    {key}: {change['old']:.2f} -> {change['new']:.2f}")


if __name__ == '__main__':
    optimizer = WeeklyOptimizer()
    results = optimizer.run()
    optimizer.print_summary(results)