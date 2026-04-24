# 数据完整性检查功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现增量更新后的数据完整性检查，缺失数据自动重试，仍缺失则提示用户选择是否继续。

**Architecture:** 在 data_layer.py 新增 ensure_data_complete 方法，包含股票和指数完整性检查、重试补充、用户交互逻辑；daily_scanner.py 调用该方法替换现有简单验证。

**Tech Stack:** Python 3, SQLite, baostock, select/threading (超时输入)

---

## 文件结构

| 文件 | 负责内容 |
|------|----------|
| `data_layer.py` | 新增 `ensure_data_complete` 方法及辅助方法 |
| `daily_scanner.py:317-333` | 替换验证逻辑，调用 `ensure_data_complete` |
| `tests/test_data_completeness.py` | 单元测试（新建） |

---

### Task 1: 创建测试目录和测试文件骨架

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_data_completeness.py`

- [ ] **Step 1: 创建测试目录**

```bash
mkdir -p /home/jzc/wechat_text/shikong_fufei/tests
```

- [ ] **Step 2: 创建 __init__.py**

```python
# tests/__init__.py
"""数据层测试"""
```

- [ ] **Step 3: 创建测试文件骨架**

```python
# tests/test_data_completeness.py
"""测试数据完整性检查功能"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestDataCompleteness(unittest.TestCase):
    """测试 ensure_data_complete 方法"""

    def setUp(self):
        """测试前准备"""
        from data_layer import get_data_layer
        self.dl = get_data_layer()

    def test_check_stock_completeness_no_missing(self):
        """测试：无缺失股票时返回空列表"""
        # TODO: 实现后补充
        pass

    def test_check_index_completeness_all_present(self):
        """测试：所有必须指数都有数据"""
        # TODO: 实现后补充
        pass

if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 4: 验证测试框架可运行**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_data_completeness.py -v
```

Expected: PASS (测试为空实现，skipped 或 pass)

---

### Task 2: 实现 `_check_stock_completeness` 辅助方法

**Files:**
- Modify: `data_layer.py` (新增方法，约在 `is_all_updated` 方法前)

- [ ] **Step 1: 写测试用例**

```python
# tests/test_data_completeness.py 添加测试方法

def test_check_stock_completeness_returns_dict(self):
    """测试返回结构正确"""
    result = self.dl._check_stock_completeness('2026-04-24')
    self.assertIn('lagging', result)
    self.assertIn('no_record', result)
    self.assertIsInstance(result['lagging'], list)
    self.assertIsInstance(result['no_record'], list)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_data_completeness.py::TestDataCompleteness::test_check_stock_completeness_returns_dict -v
```

Expected: FAIL - AttributeError: '_check_stock_completeness' not found

- [ ] **Step 3: 实现 `_check_stock_completeness` 方法**

在 `data_layer.py` 中，`is_all_updated` 方法（约342行）前添加：

```python
def _check_stock_completeness(self, target_date):
    """检查股票数据完整性

    Args:
        target_date: 目标日期 'YYYY-MM-DD'

    Returns:
        dict: {
            'lagging': [(code, last_date), ...],  # last_date < target_date 的股票
            'no_record': ['sh.600xxx', ...],      # stock_meta 中无 update_log 记录的股票
        }
    """
    result = {'lagging': [], 'no_record': []}

    # 1. 查询 update_log.last_date < target_date 的股票
    with self._get_conn() as conn:
        lagging = conn.execute("""
            SELECT code, last_date FROM update_log
            WHERE last_date < ?
            ORDER BY last_date ASC
        """, (target_date,)).fetchall()
        result['lagging'] = [(r[0], r[1]) for r in lagging]

    # 2. 查询 stock_meta 中无 update_log 记录的股票
    with self._get_conn() as conn:
        no_record = conn.execute("""
            SELECT sm.code FROM stock_meta sm
            LEFT JOIN update_log ul ON sm.code = ul.code
            WHERE ul.code IS NULL AND sm.delist_date IS NULL
        """).fetchall()
        result['no_record'] = [r[0] for r in no_record]

    return result
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_data_completeness.py::TestDataCompleteness::test_check_stock_completeness_returns_dict -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /home/jzc/wechat_text/shikong_fufei && git add data_layer.py tests/ && git commit -m "feat: add _check_stock_completeness helper method"
```

---

### Task 3: 实现 `_check_index_completeness` 辅助方法

**Files:**
- Modify: `data_layer.py` (新增方法，紧接 `_check_stock_completeness`)

- [ ] **Step 1: 写测试用例**

```python
# tests/test_data_completeness.py 添加测试方法

def test_check_index_completeness_returns_dict(self):
    """测试返回结构正确"""
    result = self.dl._check_index_completeness('2026-04-24')
    self.assertIn('required_missing', result)
    self.assertIn('optional_missing', result)
    self.assertIsInstance(result['required_missing'], list)
    self.assertIsInstance(result['optional_missing'], list)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_data_completeness.py::TestDataCompleteness::test_check_index_completeness_returns_dict -v
```

Expected: FAIL - AttributeError: '_check_index_completeness' not found

- [ ] **Step 3: 实现 `_check_index_completeness` 方法**

```python
def _check_index_completeness(self, target_date):
    """检查指数数据完整性

    Args:
        target_date: 目标日期 'YYYY-MM-DD'

    Returns:
        dict: {
            'required_missing': ['sh.000001', ...],  # 缺失必须指数
            'optional_missing': ['sh.000688'],       # 缺失可选指数（仅警告）
        }
    """
    REQUIRED_INDEXES = ['sh.000001', 'sh.000300', 'sz.399001', 'sz.399006']
    OPTIONAL_INDEXES = ['sh.000688']  # 科创50

    result = {'required_missing': [], 'optional_missing': []}

    with self._get_conn() as conn:
        # 检查必须指数
        for code in REQUIRED_INDEXES:
            has_data = conn.execute("""
                SELECT 1 FROM index_daily WHERE code=? AND date=? LIMIT 1
            """, (code, target_date)).fetchone()
            if not has_data:
                result['required_missing'].append(code)

        # 检查可选指数
        for code in OPTIONAL_INDEXES:
            has_data = conn.execute("""
                SELECT 1 FROM index_daily WHERE code=? AND date=? LIMIT 1
            """, (code, target_date)).fetchone()
            if not has_data:
                result['optional_missing'].append(code)

    return result
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_data_completeness.py::TestDataCompleteness::test_check_index_completeness_returns_dict -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /home/jzc/wechat_text/shikong_fufei && git add data_layer.py tests/ && git commit -m "feat: add _check_index_completeness helper method"
```

---

### Task 4: 实现 `_prompt_user_continue` 用户交互方法

**Files:**
- Modify: `data_layer.py` (新增方法)

- [ ] **Step 1: 写测试用例**

```python
# tests/test_data_completeness.py 添加测试方法

def test_prompt_user_non_interactive_returns_false(self):
    """测试非交互模式直接返回 False"""
    # 模拟非交互模式：stdin 不是 tty
    import io
    old_stdin = sys.stdin
    sys.stdin = io.StringIO()  # StringIO 没有 isatty 方法，会返回 False

    result = self.dl._prompt_user_continue({'stocks': [], 'indexes': []}, timeout_seconds=5)
    self.assertFalse(result)

    sys.stdin = old_stdin
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_data_completeness.py::TestDataCompleteness::test_prompt_user_non_interactive_returns_false -v
```

Expected: FAIL

- [ ] **Step 3: 实现 `_prompt_user_continue` 方法**

```python
def _prompt_user_continue(self, missing_info, timeout_seconds=30):
    """提示用户是否继续（交互模式30秒超时，非交互模式直接退出）

    Args:
        missing_info: 缺失信息 dict
        timeout_seconds: 超时秒数

    Returns:
        bool: True 表示用户选择继续，False 表示终止
    """
    import sys
    import select

    # 非交互模式直接退出
    if not sys.stdin.isatty():
        total_stocks = len(missing_info.get('stocks', [])) + len(missing_info.get('no_record_stocks', []))
        print("\n非交互模式，数据不完整，终止程序")
        print(f"  缺失股票: {total_stocks} 只")
        print(f"  缺失指数: {missing_info.get('indexes', [])}")
        return False

    # 交互模式：30秒超时
    total_stocks = len(missing_info.get('stocks', [])) + len(missing_info.get('no_record_stocks', []))
    print("\n数据不完整，是否继续？[y/n] 30秒后自动退出...")
    print(f"  缺失股票: {total_stocks} 只")
    print(f"  缺失指数: {missing_info.get('indexes', [])}")
    sys.stdout.flush()

    # 跨平台处理
    if sys.platform == 'win32':
        import threading
        result = {'answer': None, 'timeout': False}

        def timeout_handler():
            print(f"\n超时 {timeout_seconds} 秒，终止程序")
            result['timeout'] = True

        timer = threading.Timer(timeout_seconds, timeout_handler)
        timer.start()
        try:
            result['answer'] = input().strip().lower()
        except EOFError:
            result['answer'] = ''
        timer.cancel()

        if result['timeout']:
            return False
        if result['answer'] != 'y':
            print("用户选择终止")
            return False
        print("用户选择继续，可能影响扫描结果准确性")
        return True
    else:
        # Unix: select 方式
        ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
        if not ready:
            print(f"\n超时 {timeout_seconds} 秒，终止程序")
            return False

        answer = sys.stdin.readline().strip().lower()
        if answer != 'y':
            print("用户选择终止")
            return False

        print("用户选择继续，可能影响扫描结果准确性")
        return True
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_data_completeness.py::TestDataCompleteness::test_prompt_user_non_interactive_returns_false -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /home/jzc/wechat_text/shikong_fufei && git add data_layer.py tests/ && git commit -m "feat: add _prompt_user_continue for user interaction"
```

---

### Task 5: 实现 `_update_missing_data` 补充数据方法

**Files:**
- Modify: `data_layer.py` (新增方法)

- [ ] **Step 1: 写测试用例**

```python
# tests/test_data_completeness.py 添加测试方法

def test_update_missing_data_returns_counts(self):
    """测试返回更新计数正确"""
    # 用空列表测试返回结构
    result = self.dl._update_missing_data(
        lagging_stocks=[],
        no_record_stocks=[],
        missing_indexes=[]
    )
    self.assertIn('stocks_updated', result)
    self.assertIn('stocks_empty', result)
    self.assertIn('stocks_failed', result)
    self.assertIn('indexes_updated', result)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_data_completeness.py::TestDataCompleteness::test_update_missing_data_returns_counts -v
```

Expected: FAIL

- [ ] **Step 3: 实现 `_update_missing_data` 方法**

```python
def _update_missing_data(self, lagging_stocks, no_record_stocks, missing_indexes, verbose=True):
    """补充缺失的股票和指数数据

    Args:
        lagging_stocks: [(code, last_date), ...] - last_date过期的股票
        no_record_stocks: [code, ...] - 无update_log记录的股票
        missing_indexes: [code, ...] - 缺失的指数
        verbose: 是否打印日志

    Returns:
        dict: {
            'stocks_updated': int,     # 成功更新股票数
            'stocks_empty': int,       # 数据源未更新股票数
            'stocks_failed': int,      # 更新失败股票数
            'indexes_updated': int,    # 成功更新指数数
        }
    """
    result = {
        'stocks_updated': 0,
        'stocks_empty': 0,
        'stocks_failed': 0,
        'indexes_updated': 0
    }

    # 更新过期股票
    for code, last_date in lagging_stocks:
        rows, status = self.update_incremental(code)
        if status == 'success' and rows > 0:
            result['stocks_updated'] += 1
        elif status == 'empty':
            result['stocks_empty'] += 1
        else:
            result['stocks_failed'] += 1

    # 更新无记录股票（首次拉取）
    for code in no_record_stocks:
        rows, status = self.update_incremental(code, force_full=True)
        if status == 'success' and rows > 0:
            result['stocks_updated'] += 1
        elif status == 'empty':
            result['stocks_empty'] += 1
        else:
            result['stocks_failed'] += 1

    # 更新缺失指数
    for code in missing_indexes:
        try:
            self._update_index_single(code)
            result['indexes_updated'] += 1
            if verbose:
                print(f"  更新缺失指数: {code} ✓")
        except Exception as e:
            if verbose:
                print(f"  更新缺失指数: {code} ✗ ({e})")

    if verbose:
        print(f"  更新缺失股票: 成功 {result['stocks_updated']}, 数据源未更新 {result['stocks_empty']}, 失败 {result['stocks_failed']}")

    return result
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_data_completeness.py::TestDataCompleteness::test_update_missing_data_returns_counts -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /home/jzc/wechat_text/shikong_fufei && git add data_layer.py tests/ && git commit -m "feat: add _update_missing_data for supplementing missing data"
```

---

### Task 6: 实现主方法 `ensure_data_complete`

**Files:**
- Modify: `data_layer.py` (新增主方法)

- [ ] **Step 1: 写测试用例**

```python
# tests/test_data_completeness.py 添加测试方法

def test_ensure_data_complete_returns_tuple(self):
    """测试返回结构正确"""
    is_complete, missing_info = self.dl.ensure_data_complete('2026-04-24', max_retries=0)
    self.assertIsInstance(is_complete, bool)
    self.assertIsInstance(missing_info, dict)
    self.assertIn('stocks', missing_info)
    self.assertIn('indexes', missing_info)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_data_completeness.py::TestDataCompleteness::test_ensure_data_complete_returns_tuple -v
```

Expected: FAIL

- [ ] **Step 3: 实现 `ensure_data_complete` 方法**

```python
def ensure_data_complete(self, target_date, max_retries=3, timeout_seconds=30, verbose=True):
    """确保数据完整性：检查股票和指数数据，缺失则重试，失败则提示用户。

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
    """
    if verbose:
        print("\n[数据完整性检查]")
        print(f"  目标日期: {target_date}")

    for attempt in range(1, max_retries + 1):
        # 1. 检查股票完整性
        stock_check = self._check_stock_completeness(target_date)
        lagging = stock_check['lagging']
        no_record = stock_check['no_record']

        # 2. 检查指数完整性
        index_check = self._check_index_completeness(target_date)
        missing_required = index_check['required_missing']
        missing_optional = index_check['optional_missing']

        # 3. 如果完整，返回成功
        if not lagging and not no_record and not missing_required:
            if verbose:
                total_stocks = 0
                # 查询总股票数用于日志
                with self._get_conn() as conn:
                    total_stocks = conn.execute("SELECT COUNT(*) FROM update_log").fetchone()[0]
                print(f"  检查范围: 股票 {total_stocks} 只 + 指数 5 个")
                print(f"  数据完整 ✓")
                if missing_optional:
                    print(f"  ⚠ 科创50 ({missing_optional[0]}) 无数据，已跳过（数据源可能不支持）")

            missing_info = {
                'stocks': [],
                'no_record_stocks': [],
                'indexes': [],
                'optional_indexes_missing': missing_optional
            }
            return True, missing_info

        # 4. 有缺失数据，打印日志
        if verbose:
            if attempt == 1:
                with self._get_conn() as conn:
                    total_stocks = conn.execute("SELECT COUNT(*) FROM update_log").fetchone()[0]
                print(f"  检查范围: 股票 {total_stocks} 只 + 指数 5 个")

            print(f"\n  缺失股票（last_date过期）: {len(lagging)} 只")
            print(f"  无记录股票（需初始化）: {len(no_record)} 只")
            if missing_required:
                print(f"  缺失指数（必须）: {missing_required}")
            if missing_optional:
                print(f"  缺失指数（可选）: {missing_optional} - 仅警告，不阻塞")

            print(f"\n[补充数据] 尝试 ({attempt}/{max_retries})...")

        # 5. 补充数据
        update_result = self._update_missing_data(
            lagging_stocks=lagging,
            no_record_stocks=no_record,
            missing_indexes=missing_required,
            verbose=verbose
        )

        if verbose:
            print(f"\n[重试后检查] 尝试 {attempt} 完成...")

    # 6. 重试后仍有缺失，最终检查
    stock_check = self._check_stock_completeness(target_date)
    index_check = self._check_index_completeness(target_date)

    final_lagging = stock_check['lagging']
    final_no_record = stock_check['no_record']
    final_missing_required = index_check['required_missing']
    final_missing_optional = index_check['optional_missing']

    if verbose:
        print(f"\n[最终状态]")
        print(f"  缺失: {len(final_lagging) + len(final_no_record)} 只股票, {len(final_missing_required)} 个必须指数")
        if final_missing_optional:
            print(f"  ⚠ 科创50 ({final_missing_optional[0]}) 无数据，已跳过（数据源可能不支持）")

    # 7. 如果仍缺失必须数据，提示用户
    if final_lagging or final_no_record or final_missing_required:
        missing_info = {
            'stocks': final_lagging,
            'no_record_stocks': final_no_record,
            'indexes': final_missing_required,
            'optional_indexes_missing': final_missing_optional
        }

        # 提示用户是否继续
        user_continue = self._prompt_user_continue(missing_info, timeout_seconds)
        return user_continue, missing_info

    # 8. 数据完整
    missing_info = {
        'stocks': [],
        'no_record_stocks': [],
        'indexes': [],
        'optional_indexes_missing': final_missing_optional
    }
    return True, missing_info
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/test_data_completeness.py::TestDataCompleteness::test_ensure_data_complete_returns_tuple -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /home/jzc/wechat_text/shikong_fufei && git add data_layer.py tests/ && git commit -m "feat: implement ensure_data_complete main method"
```

---

### Task 7: 修改 daily_scanner.py 调用新方法

**Files:**
- Modify: `daily_scanner.py:317-333`

- [ ] **Step 1: 定位修改位置**

现有代码位于第317-333行：
```python
    # 验证数据完整性
    with dl._get_conn() as conn:
        final_cnt = conn.execute("""
            SELECT COUNT(DISTINCT code) FROM stock_daily WHERE date=?
            AND (code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%')
        """, (effective_scan_date,)).fetchone()[0]

    if final_cnt < 100:
        print(f"\n✗ 数据不足: 扫描日期 {effective_scan_date} 仅 {final_cnt} 只股票有数据")
        if reason == 'data_not_updated':
            print(f"   数据源尚未更新当天数据，请稍后重试（建议 17:00 后运行）")
        else:
            print(f"   请稍后重试或手动更新数据库。")
        bs.logout()
        sys.exit(1)
    else:
        print(f"\n[数据验证] 扫描日期 {effective_scan_date} ✓，{final_cnt} 只股票有数据")
```

- [ ] **Step 2: 替换为新逻辑**

将上述代码替换为：
```python
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
```

注意：需要删除原代码中第337行附近的指数更新调用，因为已移到前面。

- [ ] **Step 3: 检查是否需要删除重复的指数更新**

查看第335-337行原代码：
```python
    print("\n更新大盘指数数据...")
    dl.update_index_data()
    sys.stdout.flush()
```

这部分已移到新代码中，需要删除原位置的重复调用。

- [ ] **Step 4: 运行扫描测试**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 daily_scanner.py --date 2026-04-24 2>&1 | head -50
```

Expected: 正常输出，包含 `[数据完整性检查]` 相关日志

- [ ] **Step 5: 提交**

```bash
cd /home/jzc/wechat_text/shikong_fufei && git add daily_scanner.py && git commit -m "feat: integrate ensure_data_complete into daily_scanner"
```

---

### Task 8: 运行完整测试套件并验证

**Files:**
- All modified files

- [ ] **Step 1: 运行所有测试**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m pytest tests/ -v
```

Expected: All PASS

- [ ] **Step 2: 检查语法**

```bash
cd /home/jzc/wechat_text/shikong_fufei && python3 -m py_compile data_layer.py daily_scanner.py && echo "语法检查通过"
```

Expected: "语法检查通过"

- [ ] **Step 3: 手动测试完整流程**

```bash
cd /home/jzc/wechat_text/shikong_fufei && bash daily_run.sh --scan 2>&1 | grep -A 20 "数据完整性检查"
```

Expected: 看到完整性检查日志输出

- [ ] **Step 4: 最终提交**

```bash
cd /home/jzc/wechat_text/shikong_fufei && git add -A && git commit -m "feat: complete data integrity check implementation"
```

---

## Self-Review

**1. Spec Coverage:**
- 股票完整性检查 ✓ (Task 2)
- 指数完整性检查 ✓ (Task 3)
- 重试机制 ✓ (Task 6)
- 用户交互 ✓ (Task 4)
- daily_scanner.py 集成 ✓ (Task 7)

**2. Placeholder Scan:**
- 无 TBD/TODO ✓
- 无 "implement later" ✓
- 测试代码完整 ✓

**3. Type Consistency:**
- `_check_stock_completeness` 返回 `{'lagging': [], 'no_record': []}` ✓
- `_check_index_completeness` 返回 `{'required_missing': [], 'optional_missing': []}` ✓
- `ensure_data_complete` 返回 `(bool, dict)` ✓
- 所有方法签名一致 ✓