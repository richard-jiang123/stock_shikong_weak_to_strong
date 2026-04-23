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

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOGFILE"
}

start_run() {
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
    log "═══════════════════════════════════════════════════════"
    if [ "$status" = "ok" ]; then
        log "  执行完成: 成功"
    else
        log "  执行完成: 失败"
    fi
    log "  结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
    log "═══════════════════════════════════════════════════════"
    echo "" >> "$LOGFILE"
}

print_summary() {
    log "──────────────── 选股摘要 ────────────────"

    local signals_file
    if [ -n "$SCAN_DATE" ]; then
        signals_file="${SCAN_DATE//-/}_today_signals.csv"
    else
        signals_file=$(ls -t *_today_signals.csv 2>/dev/null | head -1)
    fi

    if [ ! -f "$signals_file" ]; then
        log "  当日无候选信号文件"
        log ""
        log "  完整报告见: tracking_report.md"
        return
    fi

    local count new_count repeat_count
    count=$(tail -n +2 "$signals_file" 2>/dev/null | wc -l)
    new_count=$(tail -n +2 "$signals_file" 2>/dev/null | awk -F',' '{print $NF}' | grep -c '是')
    repeat_count=$((count - new_count))
    log "  文件: $signals_file"
    log "  当日候选股: $count 只 (新增 $new_count, 延续 $repeat_count)"
    log ""
    log "  TOP 10:"

    # 生成临时数据文件（序号,代码,名称,分数,信号,新增标记）
    local tmpfile=$(mktemp)
    local rank=0
    tail -n +2 "$signals_file" | head -10 | while IFS=',' read -r code name close pct signal score rest; do
        rank=$((rank + 1))
        is_new=$(echo "$rest" | awk -F',' '{print $NF}')
        mark=" "
        [ "$is_new" = "是" ] && mark="★"
        echo "$rank|$code|$name|$score|$signal|$mark" >> "$tmpfile"
    done

    # 使用 column 自动对齐（-t 表格，-s 指定分隔符，-o 指定输出分隔符）
    # 注意：Ubuntu 20.04 的 column 可能不支持 -o，但 -t -s 可用，输出默认以空格分隔对齐
    if command -v column >/dev/null; then
        # 先用 -t -s 对齐，再用 sed 添加表头（可选）
        (echo "#|代码|名称|分|信号|新增"; cat "$tmpfile") | column -t -s '|' | while read line; do
            echo "[$(date '+%Y-%m-%d %H:%M:%S')]   $line" | tee -a "$LOGFILE"
        done
    else
        # 降级：制表符分隔
        (echo "#	代码	名称	分	信号	新增"; cat "$tmpfile") | tr '|' '\t' | while read line; do
            echo "[$(date '+%Y-%m-%d %H:%M:%S')]   $line" | tee -a "$LOGFILE"
        done
    fi

    rm -f "$tmpfile"
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
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | tee -a "$LOGFILE"
    done
    log "──────────────── 扫描完成 ─────────────────"
}

run_track() {
    log "─────────────── [2/3] 更新跟踪 ───────────────"
    $PY pick_tracker.py --action update 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | tee -a "$LOGFILE"
    done
    log "────────────── 跟踪更新完成 ────────────────"
}

run_report() {
    log "─────────────── [3/3] 生成报告 ───────────────"
    $PY generate_scorecard_report.py --lookback $LOOKBACK --output tracking_report.md 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | tee -a "$LOGFILE"
    done
    if [ -f tracking_report.md ]; then
        log "  报告已保存: tracking_report.md"
        log ""
        log "────────────── 报告内容 ────────────────"
        while IFS= read -r line; do
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | tee -a "$LOGFILE"
        done < tracking_report.md
        log "────────────────────────────────────"
    fi
    log "────────────── 报告生成完成 ────────────────"
}

run_scorecard() {
    log "─────────────── 生成成绩单 ───────────────"
    $PY pick_tracker.py --action scorecard --lookback $LOOKBACK 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | tee -a "$LOGFILE"
    done
    log "────────────── 成绩单完成 ────────────────"
}

run_optimize() {
    log "─────────────── 参数优化（坐标下降） ───────────────"
    $PY strategy_optimizer.py --mode coordinate \
        --rounds $OPT_ROUNDS --sample $OPT_SAMPLE 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | tee -a "$LOGFILE"
    done
    log "─────────────── 优化完成 ────────────────"
}

run_walkforward() {
    log "─────────────── Walk-Forward 验证 ───────────────"
    $PY strategy_optimizer.py --mode walkforward \
        --train-window $TRAIN_WINDOW --test-window $TEST_WINDOW --sample $OPT_SAMPLE 2>&1 | grep -vE "^\[Errno|接收数据异常|^login|^logout" | while IFS= read -r line; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | tee -a "$LOGFILE"
    done
    log "─────────────── Walk-Forward 完成 ───────────────"
}

# ── Main ──
start_run

CMD="${1:-all}"
case "$CMD" in
    --scan)       run_scan ;;
    --track)      run_track ;;
    --report)     run_report ;;
    --scorecard)  run_scorecard && run_report ;;
    --optimize)   run_optimize ;;
    --walkforward) run_walkforward ;;
    all|"")
        run_scan
        echo | tee -a "$LOGFILE"
        run_track
        echo | tee -a "$LOGFILE"
        run_report
        echo | tee -a "$LOGFILE"
        print_summary
        end_run "ok"
        ;;
    *)
        echo "用法: $0 [--date YYYY-MM-DD] [--scan|--track|--report|--scorecard|--optimize|--walkforward|all]"
        end_run "fail"
        exit 1
        ;;
esac
