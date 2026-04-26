# shikong_fufei 系统问题修复计划（修订版 v3）

> 计划创建时间：2026-04-26
> 第一次修订：修正进程锁 TOCTOU、遗漏 critical 路径、目标函数语义错误
> 第二次修订：修正 stdin.read() 设计缺陷、emergency_apply 缺失、目标函数替换说明
> 第三次修订：统一 release_lock 实现、修正 Walk-Forward 应用逻辑、end_run START_SECONDS guard、smooth_objective @staticmethod、emergency_apply 同步 optimization_history
> 第四次修订：smooth_objective 改用二次惩罚方案，负期望值惩罚更强，添加 score 下界 -0.5

---

## 修订记录摘要

| 版本 | 修正内容 |
|------|----------|
| v1 | 初版 |
| v1.1 | P0-1 改用 CLI 模式；P0-4 新增；P1-4 修正 leaky_relu 语义 |
| v2 | P0-1 改用 sleep loop（stdin.read() 有 TOCTOU）；P0-4 新增 emergency_apply；P1-4 明确替换操作 |
| v3 | P0-1/P1-5 统一 release_lock；P2-7 改用 emergency_apply；end_run 加 START_SECONDS guard；smooth_objective 改 @staticmethod；emergency_apply 同步 optimization_history |
| v3.1 | smooth_objective 改用二次惩罚（负期望值惩罚更强）；添加 score 下界 -0.5 |

---

## 一、问题验证结果（修订后）

| 问题ID | 文件 | 行号 | 问题描述 | 状态 |
|--------|------|------|----------|------|
| P0-1 | daily_run.sh + process_lock.py | - | 进程锁未被调用，且方案需重构 | 确认 |
| P0-2 | weekly_optimizer.py | 323, 511, 523, 526 | cfg.set() 直接写入生产参数 | 确认 |
| P0-3 | weekly_optimizer.py | 459-463 | UPDATE signal_status 直接写库 | 确认 |
| **P0-4** | **adaptive_engine.py** | **238, 273** | **critical 预警路径直接写库** | **新增** |
| P1-4 | strategy_optimizer.py | 265 | 目标函数硬截断，leaky_relu 对 max_dd 无效 | 确认 |
| P1-5 | daily_run.sh | - | 无失败通知机制，release_lock 缺 guard | 确认 |
| P1-6 | weekly_optimizer.py | 44-48 | 评分权重梯度消失 | 确认 |
| P2-7 | strategy_optimizer.py | - | Walk-Forward 结果未应用，方案需明确 | 确认 |
| P2-8 | daily_run.sh | 29 | OPT_ROUNDS=3 过少 | 确认 |
| P2-9 | strategy_optimizer.py | 216 | random.seed(42) 固定，同天多次执行采样相同 | 确认 |

---

## 二、修复顺序（按依赖关系）

```
Phase 1（基础设施）: P0-1 进程锁重构（修改 process_lock.py + daily_run.sh）
Phase 2（沙盒机制修复）: P0-2 + P0-3 + P0-4（评分层、信号层、环境层、critical路径）
Phase 3（监控增强）: P1-5 失败通知机制（含 release_lock guard）
Phase 4（优化算法改进）: P1-4 + P1-6 + P2-8 + P2-9
Phase 5（流程完善）: P2-7 Walk-Forward 应用（明确实现路径）
```

---

## 三、详细修复方案

### P0-1：进程锁方案重构（修订版 v2）

**问题根因：** 
1. 原方案 `< /dev/null` 导致 stdin 立即 EOF，锁在脚本运行前就被释放
2. 子进程 `sys.stdin.read()` 因 stdin 是 /dev/null 立即返回空字符串，不会阻塞
3. 执行时序：子进程获取锁 → stdin.read() 返回空 → with 退出 → 锁释放 → sleep 2 还在睡 → 第二个实例可获取锁

**正确修复方案：子进程用 sleep loop 保持存活，父进程用 kill 终止**

**Step 1: 修改 process_lock.py CLI（--acquire 用 sleep loop）**

```python
# process_lock.py 新增 CLI 功能（文件末尾）

if __name__ == '__main__':
    import argparse
    import time
    parser = argparse.ArgumentParser(description='进程锁管理')
    parser.add_argument('--acquire', metavar='NAME', help='获取锁')
    parser.add_argument('--release', metavar='NAME', help='释放锁')
    parser.add_argument('--timeout', type=int, default=30, help='等待超时秒数')
    parser.add_argument('--status', metavar='NAME', help='检查锁状态')
    args = parser.parse_args()
    
    if args.acquire:
        try:
            with file_lock(args.acquire, timeout=args.timeout):
                print('LOCK_ACQUIRED')
                sys.stdout.flush()
                # 用 sleep loop 保持进程存活，锁不释放
                # 父进程用 kill 终止时，OS 自动释放 fcntl.flock
                while True:
                    time.sleep(1)
        except TimeoutError:
            print('LOCK_TIMEOUT')
            sys.exit(1)
    
    elif args.release:
        # 释放锁：检查并清理锁文件
        lock_path = os.path.join(LOCK_DIR, f'{args.release}.lock')
        if os.path.exists(lock_path):
            if not is_locked(args.release):
                os.remove(lock_path)
                print('LOCK_RELEASED')
            else:
                print('LOCK_STILL_HELD')
        else:
            print('LOCK_NOT_FOUND')
    
    elif args.status:
        locked = is_locked(args.status)
        info = get_lock_info(args.status)
        print(f'locked={locked}')
        if info:
            print(f'pid={info["pid"]}')
            print(f'time={info["time"]}')
```

**关键说明：**
- `fcntl.flock` 是内核级锁，进程退出时自动解锁（即使 kill -9 也会释放）
- `while True: time.sleep(1)` 保持进程存活，锁持续持有
- 父进程 `kill $LOCK_PID` 发送 SIGTERM，子进程退出，OS 自动释放锁

**Step 2: 修改 daily_run.sh（无 stdin 重定向）**

```bash
# 锁获取函数
acquire_lock() {
    local lock_name="$1"
    local timeout="${2:-60}"
    
    # 启动锁子进程（不重定向 stdin）
    python3 "${SCRIPT_DIR}/process_lock.py" --acquire "${lock_name}" --timeout "${timeout}" &
    LOCK_PID=$!
    
    # 等待子进程启动并检查存活
    sleep 1
    
    # 用 kill -0 检查进程存活（不发信号，只检查）
    if ! kill -0 "$LOCK_PID" 2>/dev/null; then
        # 进程已退出，等待获取退出码
        wait "$LOCK_PID" 2>/dev/null
        local exit_code=$?
        if [ $exit_code -eq 1 ]; then
            log "  ✗ 获取锁超时，可能有其他实例正在运行"
        else
            log "  ✗ 锁进程异常退出 (exit=${exit_code})"
        fi
        end_run "fail"
        exit 1
    fi
    
    log "  锁已获取 (PID=${LOCK_PID})"
}

# 锁释放函数（带 guard、进程检查和文件清理）
release_lock() {
    if [ -n "${LOCK_PID:-}" ]; then
        if ps -p "${LOCK_PID}" > /dev/null 2>&1; then
            kill "${LOCK_PID}" 2>/dev/null
            wait "${LOCK_PID}" 2>/dev/null
        fi
        # 先尝试 Python 清理，失败则直接删除残留文件
        python3 "${SCRIPT_DIR}/process_lock.py" --release daily_run 2>/dev/null || \
            rm -f "${SCRIPT_DIR}/.locks/daily_run.lock" 2>/dev/null
        unset LOCK_PID  # 防止重复释放
    fi
}

# 在 start_run() 开头调用
start_run() {
    acquire_lock "daily_run" 60
    
    START_TIME=$(date '+%Y-%m-%d %H:%M:%S')
    START_SECONDS=$(date '+%s')
    # ... 原有逻辑
}

# 在 end_run() 末尾调用
end_run() {
    local status=$1
    local end_seconds=$(date '+%s')

    # Guard：START_SECONDS 可能未定义（acquire_lock 失败时）
    if [ -n "${START_SECONDS:-}" ]; then
        local elapsed=$((end_seconds - START_SECONDS))
        # ... 正常显示耗时
    else
        log "  运行耗时: 未知（锁获取阶段失败）"
    fi

    # ... 原有统计逻辑

    release_lock

    # ... 输出完成信息
}
```

**时序验证：**
```
1. 父进程启动子进程 python3 process_lock.py --acquire &
2. 子进程获取 file_lock，打印 LOCK_ACQUIRED
3. 子进程进入 while True: time.sleep(1)，持续持有锁
4. 父进程 sleep 1，然后用 kill -0 检查子进程存活
5. 若存活 → 锁持有成功，继续执行
6. 脚本结束时父进程 kill $LOCK_PID
7. 子进程收到 SIGTERM 退出，OS 自动释放 fcntl.flock
8. release_lock 清理残留锁文件
```

---

### P0-2：每周优化绕过 sandbox 隔离

**问题根因：** 第323、511、523、526行直接调用 `cfg.set()` 写入生产参数。

**正确流程：** 
```
save_snapshot() → stage_change() → validate_batch() → commit_change() → apply
```

**完整修复步骤：**

**Step 1: 删除 _optimize_score_weights_layer() 第321-323行的 cfg.set() 调用**

```python
# weekly_optimizer.py 第320-323行改为：
# 删除直接写入，变更已在第148-155行暂存到 sandbox_config
# 不再调用 self.cfg.set(weight_key, change['new'])
```

**Step 2: 删除 _optimize_environment_layer() 第511、523、526行的 cfg.set() 调用**

```python
# weekly_optimizer.py 第505-526行改为：
# 删除 self.cfg.set('bull_threshold', new_coeff)
# 删除 self.cfg.set('bear_threshold', new_coeff)
# 删除 self.cfg.set('activity_coefficient', regime_data['activity_coefficient'])
# 变更已在第173-180行暂存到 sandbox_config
```

**Step 3: 修改 adaptive_engine.py 的 run_weekly()，明确完整流程**

```python
def run_weekly(self, optimize_date=None, layers=None):
    # ... 前置检查
    
    # 1. 执行四层优化（变更暂存到 sandbox_config）
    optimization_results = self.weekly_optimizer.run(optimize_date, layers)
    batch_id = optimization_results.get('batch_id')
    
    # 2. 执行沙盒验证
    sandbox_validation = self.sandbox_validator.validate_batch(batch_id)
    
    # 3. 应用通过验证的变更（内部调用 commit_change）
    applied = self.sandbox_validator.apply_passed_changes(batch_id)
    
    # 4. 明确记录 commit_change 调用
    # apply_passed_changes 内部流程：
    #   for item in passed_items:
    #       self.change_mgr.commit_change(item['id'])
    #           → 写入 strategy_config / signal_status（生产参数）
    #           → 标记 sandbox_config.status = 'applied'
    
    return {
        'optimization_results': optimization_results,
        'sandbox_validation': sandbox_validation,
        'applied': applied,
        'rejected': sandbox_validation.get('failed', 0),
    }
```

---

### P0-3：信号层直接写库

**问题根因：** 第456-463行直接 `UPDATE signal_status` 修改生产状态。

**修复步骤：**

**Step 1: 删除 _optimize_signal_status_layer() 第456-463行的直接写库代码**

```python
# weekly_optimizer.py 第456-463行改为：
# 删除以下代码：
#     with self.dl._get_conn() as conn:
#         conn.execute("""
#             UPDATE signal_status SET
#                 status_level=?, weight_multiplier=?, last_check_date=?
#             WHERE signal_type=?
#         """, (new_status, weight_mult, optimize_date, signal_type))

# 信号状态变更已在第162-167行暂存到 sandbox_config
# 由 sandbox_validator.apply_passed_changes() → change_mgr.commit_change() 应用
```

---

### P0-4：critical 预警路径直接写库（修订版 v2）

**问题根因：** `adaptive_engine.py` 第238和273行在处理 critical 预警时直接写生产参数。

**代码位置：**
- 第238-246行：`_handle_signal_critical()` 中的 `UPDATE signal_status`
- 第273行：`_handle_market_critical()` 中的 `self.cfg.set('activity_coefficient', new_coeff)`

**关键问题：** `sandbox_validator.validate_batch()` 使用 3 周验证窗口，刚创建的变更几乎不会有足够数据，返回 `insufficient_data`，`apply_passed_changes()` 不会应用任何变更。

**解决方案：新增 emergency_apply 方法**

**Step 1: 在 sandbox_validator.py 增加 emergency_apply 方法**

```python
# sandbox_validator.py 新增方法

def emergency_apply_changes(self, batch_id: str) -> dict:
    """
    紧急应用（绕过验证，直接 commit 所有 staged 变更）
    
    用于 critical 预警等紧急场景，不等待 3 周验证窗口。
    
    Args:
        batch_id: 批次ID
    
    Returns:
        dict: {'applied': int, 'details': list}
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
        self.dl._get_conn().execute("""
            UPDATE optimization_history
            SET sandbox_test_result='emergency_applied'
            WHERE batch_id=? AND sandbox_test_result='pending'
        """, (batch_id,))
    
    return {
        'applied': applied,
        'details': details,
        'reason': 'emergency_bypass_validation',
    }
```

**Step 2: 修改 _handle_signal_critical() 使用 emergency_apply**

```python
def _handle_signal_critical(self, alert, monitor_date):
    """处理信号期望值 critical 预警（通过沙盒流程）"""
    from signal_constants import SIGNAL_TYPE_MAPPING, get_weight_multiplier
    from daily_monitor import wilson_expectancy_lower_bound
    
    handled = False
    
    # 生成紧急批次 ID
    batch_id = self.change_mgr.generate_batch_id(monitor_date.replace('-', '') + '-crit')
    
    # 保存快照（变更前）
    self.change_mgr.save_snapshot('signal_critical', batch_id)
    
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
            # 获取当前状态
            with self.dl._get_conn() as conn:
                current_status = conn.execute("""
                    SELECT status_level FROM signal_status WHERE signal_type=?
                """, (signal_type,)).fetchone()
                current_status = current_status[0] if current_status else 'active'
            
            # 暂存变更到 sandbox_config（不直接写库）
            self.change_mgr.stage_change(
                optimize_type='signal_status',
                param_key=signal_type,
                new_value='disabled',
                batch_id=batch_id,
                current_value=current_status
            )
            
            # 记录到优化历史
            with self.dl._get_conn() as conn:
                conn.execute("""
                    INSERT INTO optimization_history
                    (optimize_date, optimize_type, param_key, old_value, new_value,
                     batch_id, trigger_reason, sandbox_test_result, created_at)
                    VALUES (?, 'signal_critical', ?, ?, ?, ?, 'auto_disable_expectancy', 'pending', datetime('now'))
                """, (monitor_date, signal_type, current_status, 'disabled', batch_id))
            
            handled = True
    
    # 紧急应用（绕过 3 周验证）
    if handled:
        result = self.sandbox_validator.emergency_apply_changes(batch_id)
        self._notify_critical(f"信号 critical 预警处理: {result['applied']} 项变更已紧急应用")
    
    return handled
```

**Step 3: 修改 _handle_market_critical() 使用 emergency_apply**

```python
def _handle_market_critical(self, alert, monitor_date):
    """处理市场环境 critical 预警（通过沙盒流程）"""
    # 生成紧急批次 ID（带 -crit 后缀）
    batch_id = self.change_mgr.generate_batch_id(monitor_date.replace('-', '') + '-crit')
    
    # 保存快照
    self.change_mgr.save_snapshot('market_critical', batch_id)
    
    # 获取当前值
    current_coeff = self.cfg.get('activity_coefficient')
    new_coeff = max(0.2, current_coeff * 0.8)
    
    # 暂存变更（不直接写库）
    self.change_mgr.stage_change(
        optimize_type='strategy_config',
        param_key='activity_coefficient',
        new_value=new_coeff,
        batch_id=batch_id,
        current_value=current_coeff
    )
    
    # 记录到优化历史
    with self.dl._get_conn() as conn:
        conn.execute("""
            INSERT INTO optimization_history
            (optimize_date, optimize_type, param_key, old_value, new_value,
             batch_id, trigger_reason, sandbox_test_result, created_at)
            VALUES (?, 'market_critical', ?, ?, ?, ?, 'activity_coefficient_reduce', 'pending', datetime('now'))
        """, (monitor_date, 'activity_coefficient', current_coeff, new_coeff, batch_id))
    
    # 紧急应用（绕过验证）
    result = self.sandbox_validator.emergency_apply_changes(batch_id)
    self._notify_critical(f"市场 critical 预警处理: 活跃度系数 {current_coeff:.2f} -> {new_coeff:.2f}")
    
    return True
```

**关键说明：**
- `emergency_apply_changes()` 不调用 `_evaluate_validation()`，直接 commit
- 变更仍记录在 `optimization_history` 和 `sandbox_config`，可追溯
- `param_snapshot` 保留变更前状态，可回滚

---

### P1-4：目标函数硬截断问题（修订版 v2）

**问题根因：** 
1. `max(0, expectancy * 10 + 0.5)` 负期望值时梯度为零
2. 原方案的 `leaky_relu(1 + max_dd)` 语义错误——max_dd 是负值（如 -0.15），1+max_dd=0.85 是正值，leaky_relu 与 max 效果相同
3. softplus 在 threshold 处导数不连续

**参数说明：**
- `expectancy`: 小数形式（如 0.042 表示 4.2%），`expectancy * 10 + 0.5` = 0.92（正值）
- 当 `expectancy = -0.1`（-10%），`expectancy * 10 + 0.5` = -0.5（负值）
- `max_dd`: 负值（如 -0.15 表示 -15%），`1 + max_dd` = 0.85

**修复方案：删除第 265-269 行的 obj = ... 代码块，替换为 smooth_objective 函数调用**

**Step 1: 在 strategy_optimizer.py 添加 smooth_objective 静态方法**

```python
# strategy_optimizer.py 在类方法区域添加（evaluate_params 方法前）

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
```

**Step 2: 删除第 265-269 行的硬截断代码，替换为方法调用**

**原代码（删除）：**
```python
# strategy_optimizer.py 第 265-269 行（删除整个代码块）
obj = (0.35 * max(0, expectancy * 10 + 0.5)
       + 0.25 * win_rate
       + 0.20 * max(0, 1 + max_dd)
       + 0.10 * max(0, min(sharpe / 3, 1))
       + 0.10 * min(total / 100, 1))
```

**新代码（替换）：**
```python
# strategy_optimizer.py 第 265 行附近（替换为）
obj = self.smooth_objective(expectancy, win_rate, max_dd, sharpe, total)
```

---

### P1-5：失败通知机制（含 release_lock guard）

**修复步骤：**

**Step 1: 创建错误捕获函数**

```bash
handle_error() {
    local exit_code=$1
    local command_name="${2:-unknown}"
    local error_line="${3:-unknown}"
    
    log "  ✗ 错误: ${command_name} 失败 (exit=${exit_code}, line=${error_line})"
    
    # 写入 daily_monitor_log
    python3 -c "
import sys
sys.path.insert(0, '${SCRIPT_DIR}')
from data_layer import get_data_layer
dl = get_data_layer()
with dl._get_conn() as conn:
    conn.execute('''
        INSERT INTO daily_monitor_log
        (monitor_date, alert_type, alert_detail, severity, action_taken, created_at)
        VALUES (?, 'cron_failure', ?, 'critical', 'script_exit', datetime('now'))
    ''', ('${TODAY}', 'command=${command_name} exit_code=${exit_code} line=${error_line}'))
" 2>/dev/null
    
    # 释放锁（带 guard）
    release_lock
    
    end_run "fail"
    exit ${exit_code}
}

# 设置 trap
trap 'handle_error $? "${BASH_COMMAND}" ${LINENO}' ERR
```

**Step 2: release_lock 函数（已统一在 P0-1，此处删除重复定义）**

> **注意：** release_lock 已在 P0-1（行 146-155）统一实现。
> P1-5 仅保留 handle_error 调用，不再重复定义 release_lock。

---

### P1-6：评分权重"梯度消失"

**修复步骤：**（保持原方案，已在第一次修订中正确）

```python
def adjust_score_weight(current_weight, correlation, base_weight=1.0, momentum=0.3, history=None):
    MAX_ADJUSTMENT = 0.20
    MIN_DEVIATION = 0.05  # 最小偏离保护
    
    if correlation > 0.3:
        adjustment = min(MAX_ADJUSTMENT, correlation * 0.5)
        new_weight = current_weight * (1 + adjustment)
    elif correlation < -0.2:
        adjustment = min(MAX_ADJUSTMENT, abs(correlation) * 0.5)
        new_weight = current_weight * (1 - adjustment)
    else:
        delta = current_weight - base_weight
        if abs(delta) > MIN_DEVIATION:
            # 动量机制：减缓回归速度
            regression_factor = 0.05
            new_weight = current_weight - delta * regression_factor
        else:
            new_weight = current_weight
    
    return new_weight
```

---

### P2-7：Walk-Forward 结果应用（明确实现路径）

**问题根因：** walkforward 模式只保存 JSON，无应用机制。

**设计方案选择：**

> **采用方案 A：CLI 直接调用沙盒流程**
> - 添加 `--auto-apply` 参数
> - CLI 调用 `change_mgr.stage_change()` + `sandbox_validator.validate_batch()` + `apply_passed_changes()`
> - 不依赖后续调度

**完整实现：**

**Step 1: 修改 strategy_optimizer.py CLI**

```python
# strategy_optimizer.py 第559-566行附近
parser.add_argument('--auto-apply', action='store_true',
                    help='自动应用推荐参数（通过沙盒验证）')

# 第578-605行 walk_forward 分支修改
if args.mode == 'walkforward':
    result = optimizer.walk_forward_optimize(...)
    
    if result:
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
            
            for key, value in recommended.items():
                if abs(value - current.get(key, 0)) > 1e-6:
                    change_mgr.stage_change(
                        optimize_type='strategy_config',
                        param_key=key,
                        new_value=value,
                        batch_id=batch_id,
                        current_value=current.get(key)
                    )
            
            # 3. Walk-Forward 已是 OOS 测试，直接紧急应用
            #    （不需要再用 3 周沙盒窗口重新验证）
            applied = validator.emergency_apply_changes(batch_id)
            print(f"\n已应用 {applied['applied']} 项推荐参数（Walk-Forward OOS 验证通过）")
```

---

### P2-8：坐标下降轮数过少

**修复步骤：**

```bash
# daily_run.sh 第29行
OPT_ROUNDS=10  # 增加到10轮
```

---

### P2-9：采样偏差（修订：加入时间戳和PID）

**问题根因：** 同一天多次执行采样相同。

**修复步骤：**

```python
import hashlib
import os
from datetime import datetime

def get_dynamic_seed(start_date: str) -> int:
    """根据日期 + 时间 + PID 生成动态种子"""
    timestamp = datetime.now().strftime('%H%M%S')
    pid = os.getpid()
    
    hash_input = f"{start_date}_{timestamp}_{pid}"
    hash_val = hashlib.md5(hash_input.encode()).hexdigest()
    return int(hash_val[:8], 16) % 10000

# 第216行改为：
seed = get_dynamic_seed(start_date)
random.seed(seed)
codes = random.sample(codes, sample_size)
```

---

### 环境层沙盒策略说明

**当前设计：** `weekly_optimizer.py:83` 定义 `environment` 层 `requires_sandbox: False`

**设计意图分析：**
- 环境参数（activity_coefficient, bull/bear_threshold）随市场状态快速变化
- 每日监控会自动调整，绕过沙盒可能是设计意图（紧急响应）

**修复决策：**
- **environment 层也走沙盒流程**（P0-2 修复已包含）
- critical 预警路径（P0-4）使用紧急批次，放宽验证条件但记录完整流程
- 修改 `requires_sandbox: True`（建议）

```python
# weekly_optimizer.py 第79-84行
'environment': {
    'description': '环境层：调整市场环境系数',
    'method': 'regime_based',
    'frequency': 'weekly',
    'requires_sandbox': True,  # 修改为 True
},
```

---

## 四、测试验证总表

| 问题ID | 测试方法 | 验证标准 |
|--------|----------|----------|
| P0-1 | 并发启动两个实例 | 第二个实例应超时退出 |
| P0-2 | 运行每周优化 | cfg.get() 应未立即改变 |
| P0-3 | 运行每周优化 | signal_status 应未立即改变 |
| P0-4 | 模拟 critical 预警 | 变更应走沙盒流程并记录 |
| P1-4 | 测试负期望值边界 | 目标函数应有小梯度而非零 |
| P1-5 | 模拟失败 + 未获取锁时失败 | daily_monitor_log 应有记录，release_lock 不报错 |
| P1-6 | 多轮优化测试权重 | 权重应保留最小偏离 |
| P2-7 | --auto-apply 模式 | 参数应通过沙盒应用 |
| P2-8 | 检查优化日志 Round 数 | 应有 10 轮以上 |
| P2-9 | 同一天多次运行 | 采样股票应不同 |

---

## 五、关键修改文件

| 文件 | 修改内容 |
|------|----------|
| process_lock.py | 增加 --acquire/--release CLI 模式 |
| daily_run.sh | 进程锁 CLI 调用、失败通知、release_lock guard、轮数调整 |
| weekly_optimizer.py | 删除 cfg.set()、删除 UPDATE signal_status、修改 requires_sandbox |
| strategy_optimizer.py | 目标函数平滑、Walk-Forward auto-apply、动态种子 |
| adaptive_engine.py | critical 预警走沙盒、每周优化完整流程 |

---

## 六、风险评估与回滚策略

**高风险修改：**
- P0-2/P0-3/P0-4：沙盒机制重构，可能影响优化流程
- P0-1：进程锁重构，需测试并发场景

**回滚策略：**
- 使用 `change_manager.rollback_batch()` 回滚任何变更
- 保持 `param_snapshot` 作为恢复点
- critical 预警变更记录在 optimization_history，可追溯

---

*计划创建时间：2026-04-26*
*审查修订时间：2026-04-26*