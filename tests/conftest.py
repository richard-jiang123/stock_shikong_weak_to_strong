#!/usr/bin/env python3
"""
pytest 共享 fixtures
"""
import pytest
import tempfile
import sqlite3
import os


@pytest.fixture
def tmp_db():
    """临时数据库 fixture（包含所有必要表结构）"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    # 创建表结构（与生产环境一致）
    conn = sqlite3.connect(db_path)

    # stock_daily
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily (
            date TEXT,
            code TEXT,
            close REAL
        )
    """)

    # trading_day_cache
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trading_day_cache (
            date TEXT PRIMARY KEY,
            is_trading_day INTEGER,
            checked_at TEXT
        )
    """)

    # market_regime
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_regime (
            regime_date TEXT PRIMARY KEY,
            regime_type TEXT,
            activity_coefficient REAL,
            index_close REAL,
            index_ma5 REAL,
            index_ma20 REAL,
            consecutive_days INTEGER
        )
    """)

    # signal_status
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_status (
            signal_type TEXT PRIMARY KEY,
            display_name TEXT,
            status_level TEXT DEFAULT 'active',
            weight_multiplier REAL DEFAULT 1.0,
            live_win_rate REAL,
            live_avg_win_pct REAL,
            live_avg_loss_pct REAL,
            live_expectancy REAL,
            live_expectancy_lb REAL,
            live_sample_count INTEGER,
            last_check_date TEXT
        )
    """)

    # pick_tracking（用于期望值计算）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pick_tracking (
            id INTEGER PRIMARY KEY,
            signal_type TEXT,
            status TEXT,
            final_pnl_pct REAL
        )
    """)

    # daily_monitor_log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_monitor_log (
            id INTEGER PRIMARY KEY,
            monitor_date TEXT,
            alert_type TEXT,
            alert_detail TEXT,
            severity TEXT,
            action_taken TEXT,
            created_at TEXT
        )
    """)

    # sandbox_config
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sandbox_config (
            id INTEGER PRIMARY KEY,
            optimize_id INTEGER,
            batch_id TEXT NOT NULL,
            param_key TEXT NOT NULL,
            sandbox_value TEXT NOT NULL,
            current_value TEXT,
            optimize_type TEXT NOT NULL,
            status TEXT DEFAULT 'staged',
            staged_at TEXT DEFAULT CURRENT_TIMESTAMP,
            validation_started_at TEXT,
            validated_at TEXT,
            applied_at TEXT,
            rejected_at TEXT,
            rejection_reason TEXT,
            rollback_triggered INTEGER DEFAULT 0,
            rollback_at TEXT,
            rollback_reason TEXT,
            UNIQUE(param_key, batch_id)
        )
    """)

    # optimization_history
    conn.execute("""
        CREATE TABLE IF NOT EXISTS optimization_history (
            id INTEGER PRIMARY KEY,
            optimize_date TEXT,
            optimize_type TEXT,
            param_key TEXT,
            old_value TEXT,
            new_value TEXT,
            batch_id TEXT,
            trigger_reason TEXT,
            sandbox_test_result TEXT,
            created_at TEXT
        )
    """)

    # critical_process_state
    conn.execute("""
        CREATE TABLE IF NOT EXISTS critical_process_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_key TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL DEFAULT 'handling',
            alerts_total INTEGER DEFAULT 0,
            alerts_processed INTEGER DEFAULT 0,
            changes_applied INTEGER DEFAULT 0,
            error_detail TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_critical_period_status ON critical_process_state(period_key, status)")

    # strategy_config（AdaptiveEngine.__init__ 需要）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategy_config (
            param_key TEXT PRIMARY KEY,
            param_value TEXT,
            description TEXT,
            category TEXT,
            updated_at TEXT
        )
    """)

    # param_snapshot（ChangeManager.__init__ 需要）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS param_snapshot (
            id INTEGER PRIMARY KEY,
            snapshot_date TEXT NOT NULL,
            snapshot_type TEXT,
            batch_id TEXT,
            trigger_reason TEXT,
            params_json TEXT,
            signal_status_json TEXT,
            environment_json TEXT,
            is_restored INTEGER DEFAULT 0,
            restored_at TEXT,
            restore_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_date ON param_snapshot(snapshot_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_batch ON param_snapshot(batch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_type ON param_snapshot(snapshot_type)")

    # stock_meta（DataLayer._create_adaptive_tables 需要）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_meta (
            code TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            list_date TEXT
        )
    """)

    # index_daily（用于市场环境判断）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_daily (
            date TEXT,
            code TEXT,
            close REAL,
            volume REAL,
            PRIMARY KEY (date, code)
        )
    """)

    conn.commit()
    conn.close()

    yield db_path

    # 清理
    os.unlink(db_path)