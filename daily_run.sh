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
OPT_ROUNDS=3
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

start_run() {
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
    log "═══════════════════════════════════════════════════════"
    echo "" >> "$LOGFILE"
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
print(f'{ts}   文件: $signals_file')
print(f'{ts}   当日候选股: {count} 只 (新增 {new_count}, 延续 {repeat_count})')
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
    local cmd="$PY daily_scanner.py"
    if [ -n "$SCAN_DATE" ]; then
        cmd="$cmd --date $SCAN_DATE"
    fi
    $cmd 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"  # 终端显示（保留颜色）
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"  # 日志文件（无颜色）
    done
    log "──────────────── 扫描完成 ─────────────────"
}

run_track() {
    log "─────────────── [2/3] 更新跟踪 ───────────────"
    $PY pick_tracker.py --action update 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"  # 终端显示（保留颜色）
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"  # 日志文件（无颜色）
    done
    log "────────────── 跟踪更新完成 ────────────────"
}

run_report() {
    log "─────────────── [3/3] 生成报告 ───────────────"
    $PY generate_scorecard_report.py --lookback $LOOKBACK --output tracking_report.md 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"  # 终端显示
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"  # 日志文件
    done
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
    $PY strategy_optimizer.py --mode coordinate \
        --rounds $OPT_ROUNDS --sample $OPT_SAMPLE 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"  # 终端显示
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"  # 日志文件
    done
    log "─────────────── 优化完成 ─────────────────"
}

run_walkforward() {
    log "─────────── Walk-Forward 验证 ────────────"
    $PY strategy_optimizer.py --mode walkforward \
        --train-window $TRAIN_WINDOW --test-window $TEST_WINDOW --sample $OPT_SAMPLE 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"  # 终端显示
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"  # 日志文件
    done
    log "─────────── Walk-Forward 完成 ────────────"
}

run_monitor() {
    log "─────────────── 每日监控 ───────────────"
    $PY adaptive_engine.py --mode daily ${SCAN_DATE:+--date $SCAN_DATE} 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"  # 终端显示
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"  # 日志文件
    done
    log "────────────── 监控完成 ────────────────"
}

run_weekly_optimize() {
    log "─────────────── 每周优化 ───────────────"
    $PY adaptive_engine.py --mode weekly ${SCAN_DATE:+--date $SCAN_DATE} 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"  # 终端显示
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"  # 日志文件
    done
    log "────────────── 优化完成 ────────────────"
}

run_adaptive_status() {
    log "─────────────── 自适应状态 ───────────────"
    $PY adaptive_engine.py --mode status 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line"  # 终端显示
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | strip_colors >> "$LOGFILE"  # 日志文件
    done
    log "────────────── 状态查询完成 ───────────────"
}

# ── Main ──
start_run

CMD="${1:-all}"
case "$CMD" in
    --scan)       run_scan; print_summary; end_run "ok" ;;
    --track)      run_track; end_run "ok" ;;
    --report)     run_report; print_summary; end_run "ok" ;;
    --scorecard)  run_scorecard && run_report && print_summary && end_run "ok" ;;
    --optimize)   run_optimize; end_run "ok" ;;
    --walkforward) run_walkforward; end_run "ok" ;;
    --monitor)    run_monitor; end_run "ok" ;;
    --weekly-optimize) run_weekly_optimize; end_run "ok" ;;
    --adaptive)   run_monitor && run_weekly_optimize && run_adaptive_status; end_run "ok" ;;
    --status)     run_adaptive_status; end_run "ok" ;;
    all|"")
        run_scan
        echo "" >> "$LOGFILE"
        run_track
        echo "" >> "$LOGFILE"
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
