# 数据完整性检查功能设计

日期: 2026-04-24

## 概述

增量更新数据库后，自动检查所有个股和指数数据是否完整。不完整则重试更新，最多3次尝试。仍缺失则提示用户是否继续，等待30秒，无选择则退出程序。

## 触发位置

`daily_scanner.py` 扫描前，替换现有简单验证逻辑（第318-333行）。

调用位置：`batch_update` 完成后，扫描执行前。

**注意**：调用方（daily_scanner.py）需确保 `bs.login()` 已执行，`ensure_data_complete` 内部不再调用登录/登出。

## 完整性定义

- **股票完整性**：
  - 已有 update_log 记录且 `last_date < target_date` 的股票
  - stock_meta 中存在但无 update_log 记录的股票（新上市/遗漏初始化，且未退市）
- **指数完整性**：查询 `index_daily` 表，确认已有指数均有 `target_date` 数据
  - 必须：sh.000001, sh.000300, sz.399001, sz.399006
  - 可选：sh.000688（科创50，数据源可能不支持，仅警告不阻塞）

## 实现方案

在 `data_layer.py` 新增方法 `ensure_data_complete`。

### 方法签名

```python
def ensure_data_complete(self, target_date, max_retries=3, timeout_seconds=30, verbose=True):
    """
    确保数据完整性：检查股票和指数数据，缺失则重试，失败则提示用户。

    前置条件：调用方需确保 baostock 已登录（bs.login() 已执行）。

    Args:
        target_date: 目标日期 'YYYY-MM-DD'
        max_retries: 最大重试次数，默认3
        timeout_seconds: 用户选择超时秒数，默认30（仅交互模式有效）
        verbose: 是否打印详细日志

    Returns:
        (is_complete, missing_info):
            - is_complete: True表示数据完整或用户选择继续
            - missing_info: dict包含缺失详情
              {
                  'stocks': [(code, last_date), ...],           # 缺失股票（last_date过期）
                  'no_record_stocks': ['sh.600xxx', ...],       # stock_meta中无update_log记录的股票（未退市）
                  'indexes': ['sh.000001', ...],                # 缺失必须指数
                  'optional_indexes_missing': ['sh.000688']      # 缺失可选指数（仅警告）
              }
    """
```

### 核心流程

```
1. 检查股票完整性：
   a. 查询 update_log.last_date < target_date 的股票
   b. 查询 stock_meta 中无 update_log 记录的股票（遗漏初始化，且未退市）

2. 检查指数完整性：
   - 前4个指数必须有数据
   - 科创50 (sh.000688) 缺失仅警告，不阻塞

3. 如果完整 → 返回 (True, {})

4. 如果缺失 → 补充数据：
   - 股票：对每只缺失股票调用 update_incremental(code)
   - 指数：对缺失指数调用 _update_index_single(code)

5. 重试最多 max_retries 次

6. 仍缺失 → 用户交互：
   if sys.stdin.isatty():
       交互模式：提示用户 y/n，30秒超时退出
   else:
       非交互模式（cron）：直接退出
```

### 查询缺失数据的 SQL

```python
# 1a. 查询 update_log.last_date < target_date 的股票
with self._get_conn() as conn:
    lagging_stocks = conn.execute("""
        SELECT code, last_date FROM update_log
        WHERE last_date < ?
        ORDER BY last_date ASC
    """, (target_date,)).fetchall()

# 1b. 查询 stock_meta 中无 update_log 记录的股票（排除已退市）
with self._get_conn() as conn:
    no_record_stocks = conn.execute("""
        SELECT sm.code FROM stock_meta sm
        LEFT JOIN update_log ul ON sm.code = ul.code
        WHERE ul.code IS NULL AND sm.delist_date IS NULL
    """).fetchall()

# 2. 查询缺失指数（前4个必须，科创50可选）
REQUIRED_INDEXES = ['sh.000001', 'sh.000300', 'sz.399001', 'sz.399006']
OPTIONAL_INDEXES = ['sh.000688']  # 科创50

with self._get_conn() as conn:
    missing_required = []
    missing_optional = []
    for code in REQUIRED_INDEXES:
        has_data = conn.execute("""
            SELECT 1 FROM index_daily WHERE code=? AND date=? LIMIT 1
        """, (code, target_date)).fetchone()
        if not has_data:
            missing_required.append(code)
    for code in OPTIONAL_INDEXES:
        has_data = conn.execute("""
            SELECT 1 FROM index_daily WHERE code=? AND date=? LIMIT 1
        """, (code, target_date)).fetchone()
        if not has_data:
            missing_optional.append(code)
```

### 用户交互

区分交互模式与非交互模式（cron）：

```python
import sys

# 非交互模式直接退出
if not sys.stdin.isatty():
    print("\n非交互模式，数据不完整，终止程序")
    print(f"  缺失股票: {len(missing_stocks)} 只")
    print(f"  缺失指数: {missing_indexes}")
    return False, missing_info

# 交互模式：30秒超时
# Unix: 使用 select（高效）
# Windows: 使用 threading.Timer（兼容性）
if sys.platform == 'win32':
    import threading
    print("\n数据不完整，是否继续？[y/n] 30秒后自动退出...")
    print(f"  缺失股票: {len(missing_stocks)} 只")
    print(f"  缺失指数: {missing_indexes}")
    sys.stdout.flush()

    # 使用列表包装以便在闭包中修改
    result = {'answer': None, 'timeout': False}

    def timeout_handler():
        print(f"\n超时 {timeout_seconds} 秒，终止程序")
        result['timeout'] = True

    timer = threading.Timer(timeout_seconds, timeout_handler)
    timer.start()
    try:
        result['answer'] = input().strip().lower()
    except EOFError:
        # stdin 关闭时
        result['answer'] = ''
    timer.cancel()

    if result['timeout']:
        return False, missing_info
    if result['answer'] != 'y':
        print("用户选择终止")
        return False, missing_info
    print("用户选择继续，可能影响扫描结果准确性")
    return True, missing_info
else:
    # Unix: select 方式
    import select
    print("\n数据不完整，是否继续？[y/n] 30秒后自动退出...")
    print(f"  缺失股票: {len(missing_stocks)} 只")
    print(f"  缺失指数: {missing_indexes}")
    sys.stdout.flush()

    ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    if not ready:
        print(f"\n超时 {timeout_seconds} 秒，终止程序")
        return False, missing_info

    answer = sys.stdin.readline().strip().lower()
    if answer != 'y':
        print("用户选择终止")
        return False, missing_info

    print("用户选择继续，可能影响扫描结果准确性")
    return True, missing_info
```

## daily_scanner.py 改动

替换第317-333行现有验证逻辑，同时调整指数更新时机：

```python
# 原代码（第317-333行）：
# with dl._get_conn() as conn:
#     final_cnt = conn.execute(...).fetchone()[0]
# if final_cnt < 100:
#     print(...)
#     sys.exit(1)

# 新代码：
# 1. 先更新指数数据（移到验证前）
print("\n更新大盘指数数据...")
dl.update_index_data()
sys.stdout.flush()

# 2. 检查数据完整性（bs.login() 已在 batch_update 前执行）
is_complete, missing_info = dl.ensure_data_complete(effective_scan_date)
if not is_complete:
    print("\n✗ 数据完整性检查失败，终止扫描")
    bs.logout()
    sys.exit(1)
```

**改动要点**：
- 指数更新移到完整性检查之前（确保指数数据已更新再检查）
- `bs.login()` 由 `daily_scanner.py` 在 `batch_update` 前统一管理
- `bs.logout()` 在完整性检查失败后调用

## 补充数据逻辑

缺失股票的补充方式：

```python
# 单独更新缺失股票（而非 batch_update）
for code, last_date in missing_stocks:
    rows, status = self.update_incremental(code)
    if status == 'success' and rows > 0:
        updated_count += 1
    elif status == 'empty':
        empty_count += 1  # 数据源尚未更新，不计为失败
    else:
        failed_count += 1

# 单独更新缺失指数
for code in missing_indexes:
    self._update_index_single(code)
```

**为什么不用 batch_update**：
- `batch_update` 是批量更新全量股票，有会话管理和锁机制
- 缺失股票通常只有少量（几十只），单独更新更高效
- 避免 `batch_update` 内部的 `bs.login()` 调用冲突

## 错误处理

- `update_incremental` 返回 'empty'：数据源尚未更新，不计入失败，继续重试
- `update_incremental` 返回 'error'：计入失败，但继续流程
- `_update_index_single` 失败：继续重试
- 科创50缺失：仅警告，不阻塞流程
- 用户选择终止：返回 `False`，调用方负责退出

## 日志输出

```
[数据完整性检查]
  目标日期: 2026-04-24
  检查范围: 股票 4383 只 + 指数 5 个

  缺失股票（last_date过期）: 50 只
  无记录股票（需初始化）: 3 只
  缺失指数（必须）: 上证指数 (sh.000001)
  缺失指数（可选）: 科创50 (sh.000688) - 仅警告，不阻塞

[补充数据] 尝试 (1/3)...
  更新缺失股票: 成功 45 只, 数据源未更新 5 只, 失败 0 只
  更新缺失指数: sh.000001 ✓

[重试后检查]
  缺失股票: 5 只（数据源未更新）
  缺失指数（必须）: 无

[最终状态]
  缺失: 5 只股票, 0 个必须指数
  ⚠ 科创50 (sh.000688) 无数据，已跳过（数据源可能不支持）

[用户交互]
  数据不完整，是否继续？[y/n] 30秒后自动退出...
  缺失股票: 5 只
  缺失指数: 无
```

## 注意事项

- **登录管理**：`ensure_data_complete` 不调用 `bs.login()/logout()`，由调用方统一管理
- **用户交互**：仅交互模式（TTY）触发，cron 等非交互模式直接退出
- **科创50**：缺失仅警告，不阻塞流程（数据源兼容性问题）
- **跨平台**：Unix 用 `select`，Windows 用 `threading.Timer`
- **无记录股票**：检查 `stock_meta` 中无 `update_log` 的股票（且未退市），防止遗漏新上市股票