#!/bin/bash
# 弱转强策略 · 每日自动化流程
# 功能: 扫描选股 -> 更新跟踪 -> 生成报告
#
# 用法:
#   ./daily_run.sh                    # 完整流程（扫描 + 跟踪 + 报告）
#   ./daily_run.sh --scan             # 仅扫描选股
#   ./daily_run.sh --scan --date 2026-04-20  # 指定日期扫描
#   ./daily_run.sh --track            # 仅更新跟踪
#   ./daily_run.sh --report           # 仅生成报告
#   ./daily_run.sh --optimize         # 参数优化（坐标下降）
#   ./daily_run.sh --walkforward      # Walk-Forward 验证
#   ./daily_run.sh --scorecard        # 跟踪 + 报告（不扫描）
#   ./daily_run.sh --monitor          # 每日监控（异常检测）
#   ./daily_run.sh --weekly-optimize  # 每周四层优化
#   ./daily_run.sh --adaptive         # 完整自适应流程（监控 + 优化）
#
# 建议配合 cron 每天收盘后运行:
#   0 16 * * 1-5 /path/to/daily_run.sh

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

TODAY=$(date '+%Y-%m-%d')
LOGFILE="${SCRIPT_DIR}/daily_run.log"
LOOKBACK=90
OPT_ROUNDS=10  # 从 3 增加到 10 轮
OPT_SAMPLE=200
TRAIN_WINDOW=180
TEST_WINDOW=60

PY="python3"
SCAN_DATE=""  # 扫描日期参数

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --date)
            SCAN_DATE="$2"
            shift 2
            ;;
        *)
            break
            ;;
    esac
done

START_TIME=""  # 开始时间变量

# 剔除 ANSI 颜色码的函数（使用真正的 ESC 字符）
strip_colors() {
    local esc=$(printf '\033')
    sed "s/${esc}\[[0-9;]*m//g"
}

# 带颜色的日志输出：终端保留颜色，日志文件剔除颜色
log_color() {
    local line="$*"
    echo "$line"  # 终端显示（保留颜色）
    echo "$line" | strip_colors >> "$LOGFILE"  # 日志文件（无颜色）
}

log() {
    local line="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$line" | tee -a "$LOGFILE"
}

# 锁获取函数
acquire_lock() {
    local lock_name="$1"
    local timeout="${2:-60}"

    # 创建临时文件用于捕获子进程输出
    local output_file=$(mktemp)

    # 启动锁子进程（输出到临时文件）
    # 使用 -u 禁用 Python stdout 缓冲
    python3 -u "${SCRIPT_DIR}/process_lock.py" --acquire "${lock_name}" --timeout "${timeout}" > "$output_file" 2>&1 &
    LOCK_PID=$!

    # 等待锁获取确认（轮询检查输出文件）
    # 使用秒数计数，每次 sleep 0.5
    local start_time=$(date '+%s')
    local max_wait=$((timeout + 5))  # 比 Python timeout 多一点
    local current_time
    local elapsed

    while true; do
        # 计算已等待时间（秒）
        current_time=$(date '+%s')
        elapsed=$((current_time - start_time))

        # 超时检查
        if [ $elapsed -ge $max_wait ]; then
            rm -f "$output_file"
            log "  ✗ 等待锁确认超时 (${elapsed}秒)"
            # 清理进程和残留文件
            if kill -0 "$LOCK_PID" 2>/dev/null; then
                kill "$LOCK_PID" 2>/dev/null
                wait "$LOCK_PID" 2>/dev/null
            fi
            rm -f "${SCRIPT_DIR}/.locks/${lock_name}.lock" 2>/dev/null
            unset LOCK_PID
            end_run "fail"
            exit 1
        fi

        # 检查进程是否已退出
        if ! kill -0 "$LOCK_PID" 2>/dev/null; then
            # 进程已退出，读取输出判断原因
            wait "$LOCK_PID" 2>/dev/null
            local exit_code=$?
            local result=$(cat "$output_file" 2>/dev/null)
            rm -f "$output_file"

            if [[ "$result" == *"LOCK_TIMEOUT"* ]] || [ $exit_code -eq 1 ]; then
                log "  ✗ 获取锁超时，可能有其他实例正在运行"
            elif [[ "$result" == *"LOCK_ACQUIRED"* ]]; then
                # 进程已退出但确实获取了锁（不应发生，但处理）
                log "  ✗ 锁进程意外退出（获取锁后退出）"
            else
                log "  ✗ 锁进程异常退出 (exit=${exit_code}, output=${result})"
            fi
            unset LOCK_PID
            end_run "fail"
            exit 1
        fi

        # 检查是否已输出 LOCK_ACQUIRED
        local result=$(cat "$output_file" 2>/dev/null)
        if [[ "$result" == *"LOCK_ACQUIRED"* ]]; then
            rm -f "$output_file"
            log "  锁已获取 (PID=${LOCK_PID}, 等待${elapsed}秒)"
            return 0  # 成功
        fi

        # 检查是否已输出 LOCK_TIMEOUT（进程可能还在等待但已超时）
        if [[ "$result" == *"LOCK_TIMEOUT"* ]]; then
            rm -f "$output_file"
            log "  ✗ 获取锁超时，可能有其他实例正在运行"
            unset LOCK_PID
            end_run "fail"
            exit 1
        fi

        # 等待一小段时间再检查
        sleep 0.5
    done
}

# 检查锁进程是否仍然存活（运行期间调用）
check_lock_alive() {
    if [ -n "${LOCK_PID:-}" ] && ! kill -0 "$LOCK_PID" 2>/dev/null; then
        log "  ✗ 锁进程已意外退出 (PID=${LOCK_PID})"
        # 清理残留锁文件
        python3 "${SCRIPT_DIR}/process_lock.py" --release daily_run 2>/dev/null || \
            rm -f "${SCRIPT_DIR}/.locks/daily_run.lock" 2>/dev/null
        unset LOCK_PID
        end_run "fail"
        exit 1
    fi
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

# 错误处理函数：捕获错误并记录到 daily_monitor_log
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

start_run() {
    acquire_lock "daily_run" 60

    START_TIME=$(date '+%Y-%m-%d %H:%M:%S')
    START_SECONDS=$(date '+%s')
    echo "" >> "$LOGFILE"
    log "═══════════════════════════════════════════════════════"
    log "  弱转强策略 · 每日自动化流程"
    if [ -n "$SCAN_DATE" ]; then
        log "  指定日期: $SCAN_DATE"
    else
        log "  日期: $TODAY"
    fi
    log "═══════════════════════════════════════════════════════"
}

end_run() {
    local status=$1
    local end_seconds=$(date '+%s')

    # Guard：START_SECONDS 可能未定义（acquire_lock 失败时）
    if [ -n "${START_SECONDS:-}" ]; then
        local elapsed=$((end_seconds - START_SECONDS))
        local hours=$((elapsed / 3600))
        local minutes=$((elapsed % 3600 / 60))
        local seconds=$((elapsed % 60))
        log "═══════════════════════════════════════════════════════"
        if [ "$status" = "ok" ]; then
            log "  执行完成: 成功"
        else
            log "  执行完成: 失败"
        fi
        log "  结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
        log "  运行耗时: ${hours}小时${minutes}分钟${seconds}秒"
    else
        log "═══════════════════════════════════════════════════════"
        if [ "$status" = "ok" ]; then
            log "  执行完成: 成功"
        else
            log "  执行完成: 失败"
        fi
        log "  结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
        log "  运行耗时: 未知（锁获取阶段失败）"
    fi
    log "═══════════════════════════════════════════════════════"
    echo "" >> "$LOGFILE"

    release_lock
}

print_summary() {
    log "──────────────── 选股摘要 ────────────────"

    local signals_file
    if [ -n "$SCAN_DATE" ]; then
        signals_file="${SCAN_DATE//-/}_today_signals.xlsx"
    else
        signals_file=$(ls -t *_today_signals.xlsx 2>/dev/null | head -1)
    fi

    if [ ! -f "$signals_file" ]; then
        log "  当日无候选信号文件"
        log ""
        log "  完整报告见: tracking_report.md"
        return
    fi

    # 使用Python读取xlsx并统计（shell无法直接读取xlsx）
    python3 -c "
import pandas as pd
import sys
from datetime import datetime

df = pd.read_excel('$signals_file', engine='openpyxl')
count = len(df)
new_count = (df['是否新增'] == '是').sum()
repeat_count = count - new_count
ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
print(f'[{ts}]   文件: $signals_file')
print(f'[{ts}]   当日候选股: {count} 只 (新增 {new_count}, 延续 {repeat_count})')
" 2>/dev/null | while IFS= read -r line; do
        log_color "$line"
    done

    log ""
    log "  TOP 20:"

    # 使用Python格式化输出（直接输出到终端，避免 echo 破坏 ANSI 码）
    python3 -c "
import sys
import unicodedata
import pandas as pd
from datetime import datetime

ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
prefix = f'[{ts}]   '

# ANSI 颜色代码
RED = '\\033[91m'    # 红色（正值/涨幅）
GREEN = '\\033[92m'  # 绿色（负值/跌幅）
RESET = '\\033[0m'   # 重置颜色

def display_width(s):
    import re
    s = re.sub(r'\x1b\[[0-9;]*m', '', str(s))
    w = 0
    for ch in s:
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ('W', 'F') else 1
    return w

def pad(s, width, align='left'):
    cur = display_width(s)
    pad_len = width - cur
    if pad_len <= 0:
        return s
    return s + ' ' * pad_len if align == 'left' else ' ' * pad_len + s

def color_pct(value):
    '''根据正负值返回带颜色的百分比字符串'''
    if value > 0:
        return f'{RED}{value:+.1f}%{RESET}'
    elif value < 0:
        return f'{GREEN}{value:+.1f}%{RESET}'
    else:
        return f'{value:.1f}%'

# 读取xlsx
df = pd.read_excel('$signals_file', engine='openpyxl')
rows = df.head(20)

# 检查是否有行业列
has_industry = '行业' in df.columns

# 列宽定义（显示宽度）
if has_industry:
    cols = ['#', '代码', '名称', '现价', '涨幅', '回调', '行业', '评分', '信号', '新增']
    widths = [3, 7, 10, 8, 8, 8, 18, 5, 8, 3]
else:
    cols = ['#', '代码', '名称', '现价', '涨幅', '回调', '评分', '信号', '新增']
    widths = [4, 7, 10, 8, 8, 8, 5, 8, 3]

# 表头
header_line = prefix + ' '.join([pad(c, widths[i]) for i, c in enumerate(cols)])
sys.stdout.write(header_line + '\\n')

# 数据行
for i, (_, row) in enumerate(rows.iterrows(), 1):
    code = str(row.get('代码', row.get('code', '')))
    name = str(row.get('名称', row.get('name', '')))[:4]
    close = row.get('现价', row.get('close', 0))
    pct_chg = row.get('涨幅', row.get('pct_chg', 0))
    pct = float(pct_chg) * 100 if pct_chg else 0
    cons_dd = row.get('回调', row.get('cons_dd', 0))
    dd = float(cons_dd) * 100 if cons_dd else 0
    score = str(row.get('评分', row.get('score', '')))
    signal = str(row.get('信号', row.get('signal', '')))
    is_new = str(row.get('是否新增', '否'))
    mark = '★' if is_new == '是' else ''

    # 涨幅直接构建带颜色的版本
    pct_colored = color_pct(pct)

    if has_industry:
        industry = str(row.get('行业', row.get('industry', '')))
        parts = industry.split('、')
        ind_display = parts[0].strip() if parts else industry
        data = [str(i), code, name, f'{close:.2f}', pct_colored, f'{dd:.1f}%', ind_display, score, signal, mark]
    else:
        data = [str(i), code, name, f'{close:.2f}', pct_colored, f'{dd:.1f}%', score, signal, mark]

    line = prefix + ' '.join([pad(d, widths[j]) for j, d in enumerate(data)])
    sys.stdout.write(line + '\\n')

# 同时输出无颜色版本到日志文件
import re
def strip_ansi(s):
    return re.sub(r'\x1b\[[0-9;]*m', '', s)

with open('$LOGFILE', 'a', encoding='utf-8') as f:
    f.write(strip_ansi(header_line) + '\\n')
    for i, (_, row) in enumerate(rows.iterrows(), 1):
        code = str(row.get('代码', row.get('code', '')))
        name = str(row.get('名称', row.get('name', '')))[:4]
        close = row.get('现价', row.get('close', 0))
        pct_chg = row.get('涨幅', row.get('pct_chg', 0))
        pct = float(pct_chg) * 100 if pct_chg else 0
        cons_dd = row.get('回调', row.get('cons_dd', 0))
        dd = float(cons_dd) * 100 if cons_dd else 0
        score = str(row.get('评分', row.get('score', '')))
        signal = str(row.get('信号', row.get('signal', '')))
        is_new = str(row.get('是否新增', '否'))
        mark = '★' if is_new == '是' else ''
        if has_industry:
            industry = str(row.get('行业', row.get('industry', '')))
            parts = industry.split('、')
            ind_display = parts[0].strip() if parts else industry
            data = [str(i), code, name, f'{close:.2f}', f'{pct:+.1f}%', f'{dd:.1f}%', ind_display, score, signal, mark]
        else:
            data = [str(i), code, name, f'{close:.2f}', f'{pct:+.1f}%', f'{dd:.1f}%', score, signal, mark]
        line = prefix + ' '.join([pad(d, widths[j]) for j, d in enumerate(data)])
        f.write(line + '\\n')
" 2>/dev/null
    log ""
    log "  完整报告见: tracking_report.md"
}


run_scan() {
    log "─────────────── [1/3] 扫描选股 ───────────────"
    check_lock_alive  # 确保锁进程仍然存活
    local cmd="$PY daily_scanner.py"
    if [ -n "$SCAN_DATE" ]; then
        cmd="$cmd --date $SCAN_DATE"
    fi
    $cmd 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    # 检查管道第一个命令的返回值（PIPESTATUS[0] 是实际命令的返回值）
    local exit_code=${PIPESTATUS[0]}
    if [ $exit_code -ne 0 ]; then
        log "  ✗ 扫描失败 (exit code: $exit_code)"
        return $exit_code
    fi
    log "──────────────── 扫描完成 ─────────────────"
}

run_track() {
    log "─────────────── [2/3] 更新跟踪 ───────────────"
    check_lock_alive  # 确保锁进程仍然存活
    $PY pick_tracker.py --action update 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    local exit_code=${PIPESTATUS[0]}
    if [ $exit_code -ne 0 ]; then
        log "  ✗ 跟踪失败 (exit code: $exit_code)"
        return $exit_code
    fi
    log "────────────── 跟踪更新完成 ────────────────"
}

run_report() {
    log "─────────────── [3/3] 生成报告 ───────────────"
    check_lock_alive  # 确保锁进程仍然存活
    $PY generate_scorecard_report.py --lookback $LOOKBACK --output tracking_report.md 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    local exit_code=${PIPESTATUS[0]}
    if [ $exit_code -ne 0 ]; then
        log "  ✗ 报告生成失败 (exit code: $exit_code)"
        return $exit_code
    fi
    if [ -f tracking_report.md ]; then
        log "  报告已保存: tracking_report.md"
        log ""
        log "────────────── 报告内容 ────────────────"
        # 使用 Python 渲染 markdown 表格为对齐格式（带颜色）
        python3 -c "
import unicodedata
import re
import sys
from datetime import datetime

ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
prefix = f'[{ts}]   '

# ANSI 颜色代码
RED = '\033[91m'    # 红色（正值/盈利）
GREEN = '\033[92m'  # 绿色（负值/亏损）
RESET = '\033[0m'   # 重置颜色

def strip_ansi(s):
    '''移除 ANSI 颜色码'''
    return re.sub(r'\033\[[0-9;]+m', '', s)

def display_width(s):
    '''计算显示宽度（忽略 ANSI 码）'''
    s = strip_ansi(s)
    w = 0
    for ch in s:
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ('W', 'F') else 1
    return w

def pad(s, width):
    '''填充到指定宽度（考虑 ANSI 码不影响宽度）'''
    cur = display_width(s)
    return s + ' ' * (width - cur)

def color_value(s):
    '''根据数值正负添加颜色'''
    s_clean = strip_ansi(s)
    # 匹配带正负号的百分比数值，如 +3.38% 或 -5.21%
    match = re.match(r'^([+-]?\d+\.?\d*)%$', s_clean)
    if match:
        val = float(match.group(1))
        if val > 0:
            return f'{RED}{s_clean}{RESET}'
        elif val < 0:
            return f'{GREEN}{s_clean}{RESET}'
    return s

def render_markdown_table(content):
    lines = content.strip().split('\\n')
    output = []
    table_rows = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('|') and stripped.endswith('|'):
            if re.match(r'^\\|\\s*[-:]+\\s*\\|.*\\|$', stripped):
                continue
            cells = [c.strip() for c in stripped.split('|')[1:-1]]
            table_rows.append(cells)
        else:
            if table_rows:
                widths = []
                for col_idx in range(len(table_rows[0])):
                    max_w = max(display_width(row[col_idx]) for row in table_rows)
                    widths.append(max_w + 2)
                for row in table_rows:
                    # 对数值单元格添加颜色
                    colored_cells = []
                    for i, cell in enumerate(row):
                        colored_cell = color_value(cell)
                        colored_cells.append(pad(colored_cell, widths[i]))
                    output.append(prefix + '| ' + ' | '.join(colored_cells) + ' |')
                output.append('')
                table_rows = []
            if stripped:
                output.append(prefix + stripped)
    if table_rows:
        widths = []
        for col_idx in range(len(table_rows[0])):
            max_w = max(display_width(row[col_idx]) for row in table_rows)
            widths.append(max_w + 2)
        for row in table_rows:
            colored_cells = []
            for i, cell in enumerate(row):
                colored_cell = color_value(cell)
                colored_cells.append(pad(colored_cell, widths[i]))
            output.append(prefix + '| ' + ' | '.join(colored_cells) + ' |')
    return output

with open('tracking_report.md', 'r', encoding='utf-8') as f:
    content = f.read()

for line in render_markdown_table(content):
    sys.stdout.write(line + '\\n')
"
        log "──────────────────────────────────────────"
    fi
    log "───────────── 报告生成完成 ───────────────"
}

run_scorecard() {
    log "─────────────── 生成成绩单 ───────────────"
    $PY pick_tracker.py --action scorecard --lookback $LOOKBACK 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"  # 终端显示（保留颜色）
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"  # 日志文件（无颜色）
    done
    log "────────────── 成绩单完成 ────────────────"
}

run_optimize() {
    log "────────── 参数优化（坐标下降） ──────────"
    check_lock_alive
    $PY strategy_optimizer.py --mode coordinate \
        --rounds $OPT_ROUNDS --sample $OPT_SAMPLE 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    local exit_code=${PIPESTATUS[0]}
    if [ $exit_code -ne 0 ]; then
        log "  ✗ 参数优化失败 (exit code: $exit_code)"
        return $exit_code
    fi
    log "─────────────── 优化完成 ─────────────────"
}

run_walkforward() {
    log "─────────── Walk-Forward 验证 ────────────"
    check_lock_alive
    $PY strategy_optimizer.py --mode walkforward \
        --train-window $TRAIN_WINDOW --test-window $TEST_WINDOW --sample $OPT_SAMPLE 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    local exit_code=${PIPESTATUS[0]}
    if [ $exit_code -ne 0 ]; then
        log "  ✗ Walk-Forward验证失败 (exit code: $exit_code)"
        return $exit_code
    fi
    log "─────────── Walk-Forward 完成 ────────────"
}

run_monitor() {
    log "─────────────── 每日监控 ───────────────"
    check_lock_alive
    $PY adaptive_engine.py --mode daily ${SCAN_DATE:+--date $SCAN_DATE} 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    local exit_code=${PIPESTATUS[0]}
    if [ $exit_code -ne 0 ]; then
        log "  ✗ 监控失败 (exit code: $exit_code)"
        return $exit_code
    fi
    log "────────────── 监控完成 ────────────────"
}

run_weekly_optimize() {
    log "─────────────── 每周优化 ───────────────"
    check_lock_alive
    $PY adaptive_engine.py --mode weekly ${SCAN_DATE:+--date $SCAN_DATE} 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    local exit_code=${PIPESTATUS[0]}
    if [ $exit_code -ne 0 ]; then
        log "  ✗ 每周优化失败 (exit code: $exit_code)"
        return $exit_code
    fi
    log "────────────── 优化完成 ────────────────"
}

run_adaptive_status() {
    log "─────────────── 自适应状态 ───────────────"
    $PY adaptive_engine.py --mode status 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    log "────────────── 状态查询完成 ───────────────"
}

run_change_status() {
    log "─────────────── 变更管理状态 ───────────────"
    $PY change_manager.py --mode status 2>&1 | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    log "────────────── 状态查询完成 ───────────────"
}

run_change_history() {
    log "─────────────── 变更历史 ───────────────"
    $PY change_manager.py --mode history --days $LOOKBACK 2>&1 | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    log "────────────── 历史查询完成 ───────────────"
}

run_batch_trace() {
    local batch_id="$1"
    log "─────────────── 批次追溯 ───────────────"
    $PY change_manager.py --mode trace --batch-id "$batch_id" 2>&1 | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    log "────────────── 追溯完成 ───────────────"
}

run_rollback_monitor() {
    log "─────────────── 主动回滚监控 ───────────────"
    $PY change_manager.py --mode monitor 2>&1 | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"
    done
    log "────────────── 监控完成 ───────────────"
}

# ── Main ──
# 设置错误陷阱
trap 'handle_error $? "${BASH_COMMAND}" ${LINENO}' ERR

start_run

CMD="${1:-all}"

# 检查是否是周四（每周优化日）
IS_THURSDAY=$(date +%u)  # 1=周一, 4=周四, 7=周日

case "$CMD" in
    --scan)       run_scan; print_summary; end_run "ok" ;;
    --track)      run_track; run_monitor; end_run "ok" ;;
    --report)     run_report; print_summary; end_run "ok" ;;
    --scorecard)  run_scorecard && run_report && print_summary && end_run "ok" ;;
    --optimize)   run_optimize; end_run "ok" ;;
    --walkforward) run_walkforward; end_run "ok" ;;
    --monitor)    run_monitor; end_run "ok" ;;
    --weekly-optimize) run_weekly_optimize; end_run "ok" ;;
    --adaptive)   run_monitor && run_weekly_optimize && run_adaptive_status; end_run "ok" ;;
    --status)     run_adaptive_status; end_run "ok" ;;
    --change-status)   run_change_status; end_run "ok" ;;
    --change-history)  run_change_history; end_run "ok" ;;
    --batch-trace)     run_batch_trace "$2"; end_run "ok" ;;
    --rollback-monitor) run_rollback_monitor; end_run "ok" ;;
    all|"")
        run_scan
        echo "" >> "$LOGFILE"
        run_track
        echo "" >> "$LOGFILE"
        run_monitor  # 每日监控
        echo "" >> "$LOGFILE"
        # 周四自动执行每周优化
        if [ "$IS_THURSDAY" = "4" ]; then
            run_weekly_optimize
            echo "" >> "$LOGFILE"
        fi
        run_report
        echo "" >> "$LOGFILE"
        print_summary
        end_run "ok"
        ;;
    *)
        echo "用法: $0 [--date YYYY-MM-DD] [--scan|--track|--report|--scorecard|--optimize|--walkforward|--monitor|--weekly-optimize|--adaptive|--status|all]"
        end_run "fail"
        exit 1
        ;;
esac
