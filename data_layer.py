#!/usr/bin/env python3
"""
弱转强策略 · 本地数据层
SQLite 缓存 + 增量更新，支持多数据源备份

数据源策略：
- baostock: 主数据源，提供完整历史K线（更新时间 ~17:00）
- tencent: 实时行情源，补充当日数据（盘中实时更新）
"""
import sqlite3
import os
import time
import baostock as bs
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from contextlib import contextmanager
import warnings
warnings.filterwarnings('ignore')

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data.db')

class StockDataLayer:
    """本地数据层：缓存K线数据，增量更新，支持实时行情补充"""

    # 核心指数代码（baostock 格式，带点分隔符）
    INDEX_CODES = {
        'sh.000001': '上证指数',
        'sh.000300': '沪深300',
        'sz.399001': '深证成指',
        'sz.399006': '创业板指',
        'sh.000688': '科创50',
    }

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self._init_db()
        self._create_adaptive_tables()  # 创建自适应系统表
        self._migrate_adaptive_tables()  # 迁移缺失字段

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=50000")
        return conn

    def _init_db(self):
        """初始化数据库表"""
        with self._get_conn() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS stock_meta (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    ipo_date TEXT,
                    delist_date TEXT,
                    industry TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS stock_daily (
                    code TEXT,
                    date TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    turn REAL,
                    pct_chg REAL,
                    ma5 REAL,
                    ma10 REAL,
                    ma20 REAL,
                    volume_ma5 REAL,
                    amplitude REAL,
                    PRIMARY KEY (code, date)
                );

                CREATE INDEX IF NOT EXISTS idx_daily_code ON stock_daily(code);
                CREATE INDEX IF NOT EXISTS idx_daily_date ON stock_daily(date);

                CREATE TABLE IF NOT EXISTS update_log (
                    code TEXT PRIMARY KEY,
                    last_date TEXT,
                    row_count INTEGER,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS index_daily (
                    code TEXT,
                    date TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    pct_chg REAL,
                    ma5 REAL,
                    ma10 REAL,
                    ma20 REAL,
                    PRIMARY KEY (code, date)
                );

                CREATE INDEX IF NOT EXISTS idx_index_date ON index_daily(date);

                CREATE TABLE IF NOT EXISTS update_session (
                    id INTEGER PRIMARY KEY,
                    started_at TEXT,
                    target_date TEXT,
                    total_codes INTEGER,
                    progress INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running',
                    finished_at TEXT
                );
            ''')

            # 为已存在的数据库添加 industry 字段
            try:
                conn.execute("ALTER TABLE stock_meta ADD COLUMN industry TEXT")
            except sqlite3.OperationalError:
                pass  # 字段已存在

    def _create_adaptive_tables(self):
        """创建自适应系统所需的新表"""

        with self._get_conn() as conn:
            # signal_status 表
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
                    live_sample_count INTEGER DEFAULT 0,
                    live_observation_weeks INTEGER DEFAULT 0,
                    confidence_level TEXT DEFAULT 'unknown',
                    min_sample_threshold INTEGER DEFAULT 10,
                    last_check_date TEXT,
                    disable_reason TEXT,
                    can_auto_disable INTEGER DEFAULT 0
                )
            """)

            # optimization_history 表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS optimization_history (
                    id INTEGER PRIMARY KEY,
                    optimize_date TEXT,
                    optimize_type TEXT,
                    param_key TEXT,
                    old_value REAL,
                    new_value REAL,
                    sandbox_test_result TEXT,
                    weeks_passed INTEGER DEFAULT 0,
                    apply_date TEXT,
                    backtest_train_sharpe REAL,
                    backtest_oos_sharpe REAL,
                    backtest_win_rate REAL,
                    backtest_expectancy REAL,
                    live_win_rate REAL,
                    live_expectancy REAL,
                    rollback_needed INTEGER DEFAULT 0,
                    rollback_date TEXT,
                    validation_started_at TEXT,
                    created_at TEXT
                )
            """)

            # market_regime 表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_regime (
                    id INTEGER PRIMARY KEY,
                    regime_date TEXT NOT NULL,
                    regime_type TEXT,
                    activity_coefficient REAL,
                    index_close REAL,
                    index_ma5 REAL,
                    index_ma20 REAL,
                    consecutive_days INTEGER,
                    created_at TEXT,
                    UNIQUE(regime_date)
                )
            """)

            # daily_monitor_log 表
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

            # trading_day_cache 表：缓存交易日检查结果，避免重复 API 调用
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trading_day_cache (
                    date TEXT PRIMARY KEY,
                    is_trading_day INTEGER,
                    checked_at TEXT,
                    data_available INTEGER
                )
            """)

            # critical_process_state 表：记录 critical 处理状态，用于恢复机制
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_critical_status ON critical_process_state(status)")

            # 初始化四种信号的默认状态
            from signal_constants import SIGNAL_TYPE_MAPPING
            for signal_type, display_name in SIGNAL_TYPE_MAPPING.items():
                conn.execute("""
                    INSERT OR IGNORE INTO signal_status (signal_type, display_name, status_level, weight_multiplier)
                    VALUES (?, ?, 'active', 1.0)
                """, (signal_type, display_name))

    def _migrate_adaptive_tables(self):
        """迁移：为现有自适应表添加缺失字段"""
        with self._get_conn() as conn:
            # 检查 optimization_history 是否缺少 weeks_passed
            cols = [c[1] for c in conn.execute("PRAGMA table_info(optimization_history)").fetchall()]
            if 'weeks_passed' not in cols:
                conn.execute("ALTER TABLE optimization_history ADD COLUMN weeks_passed INTEGER DEFAULT 0")
                print("  迁移: optimization_history 添加 weeks_passed 字段")

    def update_stock_list(self):
        """更新股票列表，失败时回退到本地缓存。假设 baostock 已登录。"""
        for attempt in range(5):
            try:
                rs = bs.query_all_stock(day=datetime.now().strftime('%Y-%m-%d'))
                df = rs.get_data()
                if df is not None and 'code' in df.columns and len(df) > 100:
                    break
            except Exception as e:
                pass
            time.sleep(2)
        else:
            # API 失败，使用本地缓存
            with self._get_conn() as conn:
                df = pd.read_sql("SELECT code, name, industry FROM stock_meta", conn)
            if not df.empty:
                print(f"  API不可用，使用本地缓存: {len(df)} 只")
                return df
            raise RuntimeError("无法获取股票列表，API和本地缓存均不可用")

        mask = df['code'].str.match(r'^(sh\.60|sz\.00|sz\.30)\d{4}$')
        df = df[mask].copy()

        # 获取行业分类数据
        industry_map = self._fetch_industry_data()

        with self._get_conn() as conn:
            for _, row in df.iterrows():
                code = row['code']
                industry = industry_map.get(code, '')
                conn.execute(
                    "INSERT OR REPLACE INTO stock_meta (code, name, ipo_date, delist_date, industry, updated_at) VALUES (?,?,?,?,?,?)",
                    (code, row.get('code_name',''), row.get('ipoDate',''), row.get('delistDate',''),
                     industry, datetime.now().strftime('%Y-%m-%d %H:%M'))
                )
        print(f"  股票列表已更新: {len(df)} 只（行业: {len(industry_map)} 只有数据）")
        return df

    def _fetch_industry_data(self):
        """从 baostock 获取股票行业分类数据"""
        industry_map = {}
        try:
            rs = bs.query_stock_industry()
            data = rs.get_data()
            if data is not None and len(data) > 0:
                for _, row in data.iterrows():
                    code = row['code']
                    industry = row.get('industry', '')
                    if industry:
                        # 提取行业名称（去掉代码前缀如C33、J66等：字母+2位数字）
                        if len(industry) > 3 and industry[0].isupper() and industry[1:3].isdigit():
                            industry_name = industry[3:]
                        elif len(industry) > 2 and industry[0].isupper() and industry[1].isdigit():
                            industry_name = industry[2:]
                        else:
                            industry_name = industry
                        industry_map[code] = industry_name[:10]  # 截取前10字符
        except Exception as e:
            print(f"  获取行业数据失败: {e}")
        return industry_map

    def update_industry_data(self):
        """单独更新行业数据（用于补充已有股票的行业信息）"""
        industry_map = self._fetch_industry_data()
        if not industry_map:
            print("  无行业数据，跳过更新")
            return 0

        with self._get_conn() as conn:
            updated = 0
            for code, industry in industry_map.items():
                result = conn.execute(
                    "UPDATE stock_meta SET industry=? WHERE code=?",
                    (industry, code)
                )
                if result.rowcount > 0:
                    updated += 1
        print(f"  行业数据已更新: {updated} 只")
        return updated

    def get_industry_map(self):
        """从数据库获取行业映射"""
        with self._get_conn() as conn:
            df = pd.read_sql("SELECT code, industry FROM stock_meta WHERE industry IS NOT NULL", conn)
        return dict(zip(df['code'], df['industry']))

    def _get_incomplete_count(self, target_date):
        """检查有多少股票的数据未达到目标日期（最后一条数据早于目标日期）。
        如果大量股票未更新到目标日期，说明上一次 batch_update 可能中断了。"""
        with self._get_conn() as conn:
            # 查询每只股票的最新日期，找出早于目标日期的股票数
            cur = conn.execute("""
                SELECT COUNT(*) FROM (
                    SELECT code, MAX(date) as last_date
                    FROM stock_daily
                    GROUP BY code
                    HAVING last_date < ?
                )
            """, (target_date,))
            return cur.fetchone()[0]

    def get_lagging_stocks(self, threshold_date=None, limit=None):
        """
        获取数据落后的股票列表。

        Args:
            threshold_date: 阈值日期，低于此日期的股票被视为落后。默认为数据库最大日期-1天
            limit: 返回的最大股票数，None 表示返回全部

        Returns:
            list of (code, last_date) tuples
        """
        with self._get_conn() as conn:
            # 获取数据库中的最大日期
            max_date = conn.execute("""
                SELECT MAX(date) FROM stock_daily
                WHERE code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%'
            """).fetchone()[0]

            if max_date is None:
                return []

            if threshold_date is None:
                # 默认：落后于最大日期超过1天的股票
                threshold_date = max_date

            # 查询落后的股票
            sql = """
                SELECT code, MAX(date) as last_date
                FROM stock_daily
                WHERE code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%'
                GROUP BY code
                HAVING last_date < ?
                ORDER BY last_date ASC
            """
            if limit:
                sql += f" LIMIT {limit}"

            cur = conn.execute(sql, (threshold_date,))
            return cur.fetchall()

    def update_lagging_stocks(self, verbose=True):
        """
        优先更新数据落后的股票。

        Returns:
            dict: {'updated': 更新的股票数, 'new_rows': 新增的行数, 'lagging_count': 落后的股票总数}
        """
        lagging_stocks = self.get_lagging_stocks()
        if not lagging_stocks:
            if verbose:
                print("  无落后股票，数据完整")
            return {'updated': 0, 'new_rows': 0, 'lagging_count': 0}

        if verbose:
            # 显示落后股票的日期分布
            date_dist = {}
            for _, last_date in lagging_stocks:
                date_dist[last_date] = date_dist.get(last_date, 0) + 1
            print(f"  发现 {len(lagging_stocks)} 只落后股票:")
            for date in sorted(date_dist.keys()):
                print(f"    {date}: {date_dist[date]} 只")

        # 提取股票代码
        codes = [code for code, _ in lagging_stocks]

        # 执行批量更新（不使用续传，从头开始更新落后股票）
        updated, new_rows = self.batch_update(codes, verbose=verbose, total=len(codes))

        return {'updated': updated, 'new_rows': new_rows, 'lagging_count': len(lagging_stocks)}

    def supplement_today_data(self, codes=None, verbose=True):
        """
        使用腾讯实时行情补充当日数据

        场景：baostock当日数据未更新（16:00-17:00期间），用腾讯实时行情补充

        Args:
            codes: 要补充的股票代码列表，None表示补充所有股票
            verbose: 是否打印进度

        Returns:
            dict: {'supplemented': 补充的股票数, 'total': 总股票数}
        """
        from data_source import get_multi_source

        today = datetime.now().strftime('%Y-%m-%d')

        # 检查当前时间是否适合补充（盘中或刚收盘）
        now = datetime.now()
        hour = now.hour
        # 9:30-17:00 可以补充（盘中实时数据有效）
        if hour < 9 or (hour == 9 and now.minute < 30):
            if verbose:
                print(f"  当前时间 {now.strftime('%H:%M')}，盘中数据未开始，跳过实时补充")
            return {'supplemented': 0, 'total': 0}
        if hour >= 18:
            # 18:00后，baostock应该已更新，不需要补充
            if verbose:
                print(f"  当前时间 {now.strftime('%H:%M')}，baostock应已更新，跳过实时补充")
            return {'supplemented': 0, 'total': 0}

        # 检查数据库中哪些股票缺少当日数据
        with self._get_conn() as conn:
            if codes is None:
                # 查询所有股票
                codes = [r[0] for r in conn.execute("""
                    SELECT DISTINCT code FROM stock_daily
                    WHERE code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%'
                """).fetchall()]

            # 找出缺少当日数据的股票
            missing_codes = []
            for code in codes[:5000]:  # 限制一次最多5000只
                cur = conn.execute("""
                    SELECT COUNT(*) FROM stock_daily WHERE code=? AND date=?
                """, (code, today))
                if cur.fetchone()[0] == 0:
                    missing_codes.append(code)

        if not missing_codes:
            if verbose:
                print(f"  所有股票已有当日数据，无需补充")
            return {'supplemented': 0, 'total': len(codes)}

        if verbose:
            print(f"  发现 {len(missing_codes)} 只股票缺少当日数据，使用腾讯实时行情补充")

        # 批量获取腾讯实时行情
        manager = get_multi_source()
        manager.login()

        quotes = manager.batch_get_realtime_quotes(missing_codes)

        # 保存到数据库
        supplemented = 0
        for code in missing_codes:
            if code not in quotes:
                continue

            q = quotes[code]
            # 转换为数据库格式
            row = (
                code,
                q['date'],
                q['open'],
                q['high'],
                q['low'],
                q['close'],
                q['volume'] * 100,  # 手转股
                q['amount'],
                q['turn'] / 100 if q['turn'] else None,  # 百分比转小数
                q['pct_chg'] / 100 if q['pct_chg'] else None,  # 百分比转小数
                None,  # ma5 - 实时数据无法计算
                None,  # ma10
                None,  # ma20
                None,  # volume_ma5
                (q['high'] - q['low']) / q['close'] if q['close'] > 0 else None,  # amplitude
            )

            with self._get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO stock_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    row
                )
                # 更新日志
                conn.execute(
                    "INSERT OR REPLACE INTO update_log (code, last_date, row_count, updated_at) VALUES (?,?,?,?)",
                    (code, q['date'], 1, datetime.now().strftime('%Y-%m-%d %H:%M'))
                )
            supplemented += 1

        manager.logout()

        if verbose:
            print(f"  实时补充完成: {supplemented}/{len(missing_codes)} 只")

        return {'supplemented': supplemented, 'total': len(codes)}

    def _check_stock_completeness(self, target_date):
        """检查股票数据完整性和数据质量

        Args:
            target_date: 目标日期 'YYYY-MM-DD'

        Returns:
            dict: {
                'lagging': [(code, last_date), ...],  # last_date < target_date 的股票
                'no_record': ['sh.600xxx', ...],      # stock_meta 中无 update_log 记录的股票
                'bad_quality': ['sh.600xxx', ...],    # pct_chg=NULL 或 MA=NULL 的股票（需要重新更新）
            }
        """
        result = {'lagging': [], 'no_record': [], 'bad_quality': []}

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

        # 3. 检查目标日期数据质量：pct_chg=NULL 或 ma5=NULL 的股票
        #    这些股票需要重新更新（可能是之前的增量更新问题）
        with self._get_conn() as conn:
            bad_quality = conn.execute("""
                SELECT code FROM stock_daily
                WHERE date = ?
                AND (code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%')
                AND (pct_chg IS NULL OR ma5 IS NULL)
            """, (target_date,)).fetchall()
            result['bad_quality'] = [r[0] for r in bad_quality]

        return result

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
            # 1. 检查股票完整性（包括数据质量）
            stock_check = self._check_stock_completeness(target_date)
            lagging = stock_check['lagging']
            no_record = stock_check['no_record']
            bad_quality = stock_check['bad_quality']

            # 2. 检查指数完整性
            index_check = self._check_index_completeness(target_date)
            missing_required = index_check['required_missing']
            missing_optional = index_check['optional_missing']

            # 3. 如果完整，返回成功
            if not lagging and not no_record and not bad_quality and not missing_required:
                if verbose:
                    with self._get_conn() as conn:
                        total_stocks = conn.execute("SELECT COUNT(*) FROM update_log").fetchone()[0]
                    print(f"  检查范围: 股票 {total_stocks} 只 + 指数 5 个")
                    print(f"  数据完整 ✓")
                    if missing_optional:
                        print(f"  ⚠ 科创50 ({missing_optional[0]}) 无数据，已跳过（数据源可能不支持）")

                missing_info = {
                    'stocks': [],
                    'no_record_stocks': [],
                    'bad_quality_stocks': [],
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
                print(f"  数据异常股票（pct/MA为空）: {len(bad_quality)} 只")
                if missing_required:
                    print(f"  缺失指数（必须）: {missing_required}")
                if missing_optional:
                    print(f"  缺失指数（可选）: {missing_optional} - 仅警告，不阻塞")

                print(f"\n[补充数据] 尝试 ({attempt}/{max_retries})...")

            # 5. 补充数据（包括修复数据质量问题）
            self._update_missing_data(
                lagging_stocks=lagging,
                no_record_stocks=no_record,
                bad_quality_stocks=bad_quality,
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
        final_bad_quality = stock_check['bad_quality']
        final_missing_required = index_check['required_missing']
        final_missing_optional = index_check['optional_missing']

        if verbose:
            print(f"\n[最终状态]")
            print(f"  缺失: {len(final_lagging) + len(final_no_record)} 只股票, {len(final_bad_quality)} 只数据异常, {len(final_missing_required)} 个必须指数")
            if final_missing_optional:
                print(f"  ⚠ 科创50 ({final_missing_optional[0]}) 无数据，已跳过（数据源可能不支持）")

        # 7. 如果仍缺失必须数据，提示用户
        if final_lagging or final_no_record or final_bad_quality or final_missing_required:
            missing_info = {
                'stocks': final_lagging,
                'no_record_stocks': final_no_record,
                'bad_quality_stocks': final_bad_quality,
                'indexes': final_missing_required,
                'optional_indexes_missing': final_missing_optional
            }

            user_continue = self._prompt_user_continue(missing_info, timeout_seconds)
            return user_continue, missing_info

        # 8. 数据完整
        missing_info = {
            'stocks': [],
            'no_record_stocks': [],
            'bad_quality_stocks': [],
            'indexes': [],
            'optional_indexes_missing': final_missing_optional
        }
        return True, missing_info

    def _update_missing_data(self, lagging_stocks, no_record_stocks, bad_quality_stocks, missing_indexes, verbose=True):
        """补充缺失的股票和指数数据

        Args:
            lagging_stocks: [(code, last_date), ...] - last_date过期的股票
            no_record_stocks: [code, ...] - 无update_log记录的股票
            bad_quality_stocks: [code, ...] - pct_chg/MA为空的股票（需删除旧数据重新更新）
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

        # 修复数据质量异常股票（删除旧数据重新更新）
        for code in bad_quality_stocks:
            # 删除目标日期的旧数据
            with self._get_conn() as conn:
                conn.execute("DELETE FROM stock_daily WHERE code=? AND date>=?",
                            (code, (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')))
                conn.execute("DELETE FROM update_log WHERE code=?", (code,))
                conn.commit()
            # 重新更新（force_full获取足够历史数据）
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

    def is_all_updated(self, target_date=None):
        """
        检查是否所有股票的数据都已更新到目标日期。

        返回 (is_ready, max_date, status_info):
            - is_ready: True 表示数据已准备好（可以是目标日期或非交易日）
            - max_date: 数据库中最新日期
            - status_info: dict 包含:
                - 'reason': 'data_ready' | 'non_trading_day' | 'data_not_updated' | 'need_update'
                - 'sample_checked': 采样数量
                - 'incomplete_count': 未更新的股票数
        """
        if target_date is None:
            target_date = datetime.now().strftime('%Y-%m-%d')
        with self._get_conn() as conn:
            # 只检查股票数据（排除指数）
            cur = conn.execute("""
                SELECT MAX(date) FROM stock_daily
                WHERE code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%'
            """)
            max_date = cur.fetchone()[0]
            if max_date is None:
                return False, None, {'reason': 'need_update', 'sample_checked': 0, 'incomplete_count': 0}

            # 如果最大日期 >= 目标日期，做两步检查
            if max_date >= target_date:
                stock_cnt = conn.execute("""
                    SELECT COUNT(DISTINCT code) FROM stock_daily WHERE date=?
                    AND (code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%')
                """, (target_date,)).fetchone()[0]
                # 至少 100 只股票有数据
                if stock_cnt < 100:
                    return False, max_date, {'reason': 'need_update', 'sample_checked': stock_cnt, 'incomplete_count': 0}
                # 检查是否有大量股票未达到目标日期（更新中断检测）
                incomplete = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT code, MAX(date) as last_date
                        FROM stock_daily
                        WHERE code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%'
                        GROUP BY code
                        HAVING last_date < ?
                    )
                """, (target_date,)).fetchone()[0]
                # 如果超过 5% 的股票未更新到目标日期，说明上次更新中断
                total_with_data = conn.execute("""
                    SELECT COUNT(DISTINCT code) FROM stock_daily
                    WHERE code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%'
                """).fetchone()[0]
                if total_with_data > 0 and incomplete > max(10, total_with_data * 0.05):
                    return False, max_date, {'reason': 'need_update', 'sample_checked': stock_cnt, 'incomplete_count': incomplete}
                return True, max_date, {'reason': 'data_ready', 'sample_checked': stock_cnt, 'incomplete_count': incomplete}

            # 还没到目标日期，采样检查确认是"非交易日"还是"数据未更新"
            cur = conn.execute("""
                SELECT code FROM stock_daily WHERE date=?
                AND (code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%')
                LIMIT 5
            """, (max_date,))
            sample = [r[0] for r in cur.fetchall()]

            # 先检查缓存，避免重复 API 调用
            cached = conn.execute("""
                SELECT is_trading_day, data_available, checked_at
                FROM trading_day_cache WHERE date=?
            """, (target_date,)).fetchone()

            if cached:
                is_trading_day = cached[0]
                data_available = cached[1]
                checked_at = cached[2]
                # 缓存有效条件：当天检查的，或者检查时间在收盘后（17:00后）
                checked_dt = datetime.strptime(checked_at, '%Y-%m-%d %H:%M:%S')
                is_same_day = checked_dt.strftime('%Y-%m-%d') == datetime.now().strftime('%Y-%m-%d')
                is_checked_after_close = checked_dt.hour >= 17

                if is_same_day or is_checked_after_close:
                    # 使用缓存结果
                    if is_trading_day == 0:
                        # 非交易日
                        return True, max_date, {'reason': 'non_trading_day', 'sample_checked': len(sample), 'incomplete_count': 0, 'cached': True}
                    elif data_available == 1:
                        # 交易日且数据可用 → 需要更新
                        return False, max_date, {'reason': 'need_update', 'sample_checked': len(sample), 'incomplete_count': 0, 'cached': True}
                    else:
                        # 交易日但数据不可用
                        return False, max_date, {'reason': 'data_not_updated', 'sample_checked': len(sample), 'incomplete_count': 0, 'cached': True}

        if len(sample) < 2:
            return False, max_date, {'reason': 'need_update', 'sample_checked': 0, 'incomplete_count': 0}

        # 检查当前时间是否在收盘后不久（数据可能未更新）
        now = datetime.now()
        is_early_after_close = now.hour < 17  # 17:00前视为数据可能未更新

        # 假设 baostock 已在外部登录
        # 采样检查目标日期是否有数据
        has_target_data = False
        api_error_count = 0
        for code in sample:
            rs = bs.query_history_k_data_plus(code, "date",
                start_date=target_date, end_date=target_date, frequency="d")
            # 检查API是否成功
            if rs.error_code != '0':
                api_error_count += 1
                continue
            data = rs.get_data()
            if data is not None and len(data) > 0:
                has_target_data = True
                break

        # 同时检查大盘指数是否有数据（更可靠的判断）
        index_rs = bs.query_history_k_data_plus('sh.000001', "date",
            start_date=target_date, end_date=target_date, frequency="d")
        index_has_data = (index_rs.error_code == '0' and
                          index_rs.get_data() is not None and
                          len(index_rs.get_data()) > 0)

        if has_target_data or index_has_data:
            # 目标日期有数据 → 交易日，数据可用 → 需要增量更新
            self._cache_trading_day(target_date, is_trading_day=True, data_available=True)
            return False, max_date, {'reason': 'need_update', 'sample_checked': len(sample), 'incomplete_count': 0}
        elif api_error_count >= 3:
            # API多次失败 → 数据源可能不可用，视为数据未更新（不缓存）
            return False, max_date, {'reason': 'data_not_updated', 'sample_checked': len(sample), 'incomplete_count': 0}
        elif is_early_after_close:
            # 时间较早（17:00前），数据源可能未更新，视为数据未更新而非非交易日（不缓存）
            return False, max_date, {'reason': 'data_not_updated', 'sample_checked': len(sample), 'incomplete_count': 0}
        else:
            # 目标日期无数据且API正常，时间较晚 → 非交易日
            # 还需检查 max_date 是否完整
            with self._get_conn() as conn:
                max_date_cnt = conn.execute("""
                    SELECT COUNT(DISTINCT code) FROM stock_daily WHERE date=?
                    AND (code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%')
                """, (max_date,)).fetchone()[0]
                if max_date_cnt < 100:
                    # 最大日期数据不完整，需要重新批量更新
                    return False, max_date, {'reason': 'need_update', 'sample_checked': max_date_cnt, 'incomplete_count': 0}
            # 缓存非交易日结果
            self._cache_trading_day(target_date, is_trading_day=False, data_available=False)
            return True, max_date, {'reason': 'non_trading_day', 'sample_checked': len(sample), 'incomplete_count': 0}

    def _cache_trading_day(self, date, is_trading_day, data_available):
        """缓存交易日检查结果"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trading_day_cache
                (date, is_trading_day, checked_at, data_available)
                VALUES (?, ?, datetime('now'), ?)
            """, (date, 1 if is_trading_day else 0, 1 if data_available else 0))

    def get_last_date(self, code):
        """获取某股票最后一条数据的日期"""
        with self._get_conn() as conn:
            cur = conn.execute("SELECT MAX(date) FROM stock_daily WHERE code=?", (code,))
            row = cur.fetchone()
            return row[0] if row and row[0] else None

    def fetch_from_api(self, code, start_date, end_date, max_retries=3):
        """从 baostock 获取数据，支持重试（假设已登录）

        返回:
            (df, status):
                - df: DataFrame 或 None
                - status: 'success' | 'empty' | 'error'
                    - 'success': 成功获取数据
                    - 'empty': API正常但无数据（数据未更新或非交易日）
                    - 'error': API调用失败
        """
        for attempt in range(max_retries):
            try:
                # 请求包含 pctChg 字段，直接使用 baostock 计算的涨跌幅
                rs = bs.query_history_k_data_plus(code,
                    "date,open,high,low,close,pctChg,volume,amount,turn",
                    start_date=start_date, end_date=end_date,
                    frequency="d", adjustflag="2")
                df = rs.get_data()

                # 检查API调用是否成功
                if rs.error_code != '0':
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                    return None, 'error'

                if df is None or len(df) == 0:
                    # API调用成功但无数据 → 数据未更新或非交易日
                    return None, 'empty'

                for c in ['open','high','low','close','volume','amount','turn']:
                    df[c] = df[c].astype(float)
                # pctChg 是百分比格式，转换为小数
                df['pct_chg'] = df['pctChg'].astype(float) / 100
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                df = df.sort_values('date').reset_index(drop=True)

                # 计算MA指标（至少需要20行才能计算ma20）
                if len(df) >= 20:
                    df['ma5'] = df['close'].rolling(5).mean()
                    df['ma10'] = df['close'].rolling(10).mean()
                    df['ma20'] = df['close'].rolling(20).mean()
                    df['volume_ma5'] = df['volume'].rolling(5).mean()
                    df['amplitude'] = (df['high'] - df['low']) / df['close']
                elif len(df) >= 5:
                    # 有足够数据计算ma5但不够ma20
                    df['ma5'] = df['close'].rolling(5).mean()
                    df['ma10'] = df['close'].rolling(10).mean() if len(df) >= 10 else None
                    df['ma20'] = None
                    df['volume_ma5'] = df['volume'].rolling(5).mean()
                    df['amplitude'] = (df['high'] - df['low']) / df['close']
                else:
                    # 数据不足时，只保留基础数据
                    df['ma5'] = None
                    df['ma10'] = None
                    df['ma20'] = None
                    df['volume_ma5'] = None
                    df['amplitude'] = (df['high'] - df['low']) / df['close']

                return df, 'success'
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return None, 'error'
        return None, 'error'

    def save_to_db(self, code, df):
        """保存数据到数据库"""
        if df is None or len(df) == 0:
            return 0

        with self._get_conn() as conn:
            rows = []
            for _, row in df.iterrows():
                rows.append((
                    code, row['date'], row['open'], row['high'], row['low'],
                    row['close'], row['volume'], row['amount'], row['turn'],
                    row['pct_chg'], row['ma5'], row['ma10'], row['ma20'],
                    row['volume_ma5'], row['amplitude']
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO stock_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows
            )

            # 更新日志
            conn.execute(
                "INSERT OR REPLACE INTO update_log (code, last_date, row_count, updated_at) VALUES (?,?,?,?)",
                (code, df['date'].iloc[-1], len(df), datetime.now().strftime('%Y-%m-%d %H:%M'))
            )
        return len(df)

    def update_incremental(self, code, force_full=False):
        """增量更新单只股票数据

        改进：增量更新时往前回退30天获取历史数据，确保能计算MA指标。

        返回:
            (rows_added, status):
                - rows_added: 新增的行数
                - status: 'success' | 'empty' | 'error'
                    - 'success': 成功更新数据
                    - 'empty': 数据已是最新或数据未更新
                    - 'error': API调用失败
        """
        # 需要至少20个交易日计算MA20，多回退确保足够
        MA_LOOKBACK = 30

        end_date = datetime.now().strftime('%Y-%m-%d')

        if force_full:
            start_date = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
        else:
            last_date = self.get_last_date(code)
            if last_date:
                # 如果已经是今天的数据，跳过
                if last_date == end_date:
                    return 0, 'empty'
                last_dt = datetime.strptime(last_date, '%Y-%m-%d')
                # 往前回退30天，确保能计算MA指标
                start_date = (last_dt - timedelta(days=MA_LOOKBACK)).strftime('%Y-%m-%d')
            else:
                # 首次：拉200天
                start_date = (datetime.now() - timedelta(days=200)).strftime('%Y-%m-%d')

        # 确保 start_date <= end_date
        if start_date > end_date:
            return 0, 'empty'

        df, fetch_status = self.fetch_from_api(code, start_date, end_date)
        if df is not None:
            rows = self.save_to_db(code, df)
            return rows, 'success'
        return 0, fetch_status

    def batch_update(self, codes, verbose=False, total=None):
        """批量增量更新，支持断点续传和 API 恢复等待。

        改进：区分 'empty'（数据未更新）和 'error'（API失败），
              只将 'error' 计入失败计数。
        """
        from process_lock import file_lock, is_locked, get_lock_info

        target_date = datetime.now().strftime('%Y-%m-%d')

        # 确保 baostock 已登录
        bs.login()

        # 检查是否已有进程在运行batch_update
        if is_locked('batch_update'):
            lock_info = get_lock_info('batch_update')
            print(f"  ⚠ batch_update 已被进程 {lock_info['pid']} 锁定 ({lock_info['time']})")
            print(f"  跳过本次更新，避免并发冲突")
            bs.logout()
            return 0, 0

        # 使用文件锁保护整个批量更新过程
        with file_lock('batch_update', timeout=60):
            # 清理超过24小时的残留会话（可能已死的进程）
            with self._get_conn() as conn:
                conn.execute("""
                    UPDATE update_session SET status='expired'
                    WHERE status='running' AND started_at < ?
                """, ((datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S'),))

                # 清理旧的interrupted状态（超过24小时）
                conn.execute("""
                    UPDATE update_session SET status='expired'
                    WHERE status='interrupted' AND started_at < ?
                """, ((datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S'),))

            # 查找最近一次中断的会话（只查找当天的）
            resume_from = 0
            with self._get_conn() as conn:
                cur = conn.execute("""
                    SELECT progress FROM update_session
                    WHERE status='interrupted'
                    AND target_date = ?
                    AND started_at >= ?
                    ORDER BY id DESC LIMIT 1
                """, (target_date, (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')))
                row = cur.fetchone()
                if row and row[0] > 0:
                    resume_from = row[0]

            # 创建新会话
            with self._get_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO update_session (started_at, target_date, total_codes, progress, status) VALUES (?,?,?,?,?)",
                    (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), target_date, len(codes), resume_from, 'running'))
                session_id = cur.lastrowid

            updated = 0
            new_rows = 0
            total_codes = len(codes)
            consecutive_errors = 0  # 只计数真正的API错误
            consecutive_empty = 0   # 计数数据未更新（用于检测是否需要提前退出）
            max_consecutive_errors = 100  # 真正失败阈值
            api_wait_interval = 10  # API恢复等待间隔（秒）
            api_wait_threshold = 20  # 达到此阈值后开始等待

            if resume_from > 0:
                print(f"  检测到上次中断，从第 {resume_from + 1}/{total_codes} 只续传...")

            for i, code in enumerate(codes):
                if i < resume_from:
                    continue
                n, status = self.update_incremental(code)
                if status == 'success':
                    new_rows += n
                    consecutive_errors = 0
                    consecutive_empty = 0
                elif status == 'empty':
                    # 数据未更新或非交易日，不计入失败
                    consecutive_empty += 1
                elif status == 'error':
                    # API真正失败
                    consecutive_errors += 1
                    consecutive_empty += 1
                    # 当连续错误达到阈值时，等待一段时间让 API 恢复
                    if consecutive_errors == api_wait_threshold:
                        print(f"  API 连续错误 {consecutive_errors} 次，等待 {api_wait_interval} 秒...")
                        time.sleep(api_wait_interval)
                    elif consecutive_errors == api_wait_threshold * 2:
                        print(f"  API 连续错误 {consecutive_errors} 次，再次等待 {api_wait_interval} 秒...")
                        time.sleep(api_wait_interval)
                updated += 1
                # 每50只记录一次进度
                if (i + 1) % 50 == 0:
                    with self._get_conn() as conn:
                        conn.execute("UPDATE update_session SET progress=? WHERE id=?", (i + 1, session_id))
                if verbose and total:
                    if (i + 1) % 500 == 0 or i + 1 == total:
                        print(f"  更新进度 {i+1}/{total} | 新增 {new_rows} 行")
                # 最终失败阈值（只针对真正的API错误）
                if consecutive_errors >= max_consecutive_errors:
                    print(f"\n  ✗ API 连续错误 {max_consecutive_errors} 次，停止更新（baostock 不可用）")
                    with self._get_conn() as conn:
                        conn.execute(
                            "UPDATE update_session SET status='interrupted', finished_at=? WHERE id=?",
                            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session_id))
                    bs.logout()
                    return updated, new_rows
                # 如果大量连续 empty，说明数据未更新，可以提前退出
                if consecutive_empty >= 500:
                    print(f"\n  ○ 连续 {consecutive_empty} 只股票数据未更新，可能数据源尚未更新，提前退出")
                    with self._get_conn() as conn:
                        conn.execute(
                            "UPDATE update_session SET status='completed', finished_at=? WHERE id=?",
                            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session_id))
                    bs.logout()
                    return updated, new_rows

        # 标记完成
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE update_session SET status='completed', finished_at=? WHERE id=?",
                (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session_id))

        bs.logout()
        return updated, new_rows

    def get_kline_batch(self, codes, start_date=None, end_date=None):
        """
        批量获取多只股票的K线数据
        一次SQL查询读取所有股票数据，比逐只查询快很多
        """
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=200)).strftime('%Y-%m-%d')

        with self._get_conn() as conn:
            df = pd.read_sql(
                "SELECT * FROM stock_daily WHERE code IN ({}) AND date>=? AND date<=? ORDER BY code, date".format(
                    ','.join('?' * len(codes))),
                conn, params=tuple(codes) + (start_date, end_date),
                parse_dates=['date']
            )

        # 按股票分组，快速返回
        result = {}
        for code, group in df.groupby('code'):
            if len(group) >= 60:  # 保持原有要求，策略需要足够历史数据
                result[code] = group.reset_index(drop=True)

        return result

    def get_kline(self, code, start_date=None, end_date=None):
        """
        获取K线数据
        优先从本地读取，如果数据不足则增量更新后再读
        """
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=200)).strftime('%Y-%m-%d')

        with self._get_conn() as conn:
            df = pd.read_sql(
                "SELECT * FROM stock_daily WHERE code=? AND date>=? AND date<=? ORDER BY date",
                conn, params=(code, start_date, end_date),
                parse_dates=['date']
            )

        if len(df) < 60:
            # 数据不足，增量更新
            self.update_incremental(code)
            with self._get_conn() as conn:
                df = pd.read_sql(
                    "SELECT * FROM stock_daily WHERE code=? AND date>=? AND date<=? ORDER BY date",
                    conn, params=(code, start_date, end_date),
                    parse_dates=['date']
                )

        if len(df) < 60:
            return None

        return df

    def get_stock_list(self):
        """获取本地股票列表"""
        with self._get_conn() as conn:
            df = pd.read_sql("SELECT code, name FROM stock_meta", conn)
        return df

    # ==================== 指数数据 ====================

    def update_index_data(self):
        """更新所有指数数据（增量）"""
        for code, name in self.INDEX_CODES.items():
            self._update_index_single(code)
            print(f"  指数已更新: {name} ({code})")

    def _update_index_single(self, code):
        """增量更新单个指数数据"""
        end_date = datetime.now().strftime('%Y-%m-%d')
        with self._get_conn() as conn:
            last_date = conn.execute("SELECT MAX(date) FROM index_daily WHERE code=?", (code,)).fetchone()[0]

        # 增量更新时，往前回退30天以便计算MA指标（考虑非交易日）
        MA_LOOKBACK = 30  # 需要至少20个交易日计算MA20，多回退确保足够
        if last_date:
            if last_date == end_date:
                return 0
            last_dt = datetime.strptime(last_date, '%Y-%m-%d')
            # 回退足够天数来计算MA
            start_date = (last_dt - timedelta(days=MA_LOOKBACK)).strftime('%Y-%m-%d')
        else:
            start_date = (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d')

        if start_date > end_date:
            return 0

        try:
            rs = bs.query_history_k_data_plus(code,
                "date,open,high,low,close,volume,amount",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="3")
            df = rs.get_data()
            if df is None or len(df) == 0:
                return 0
            for c in ['open','high','low','close','volume','amount']:
                df[c] = df[c].astype(float)
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
            df = df.sort_values('date').reset_index(drop=True)

            # 计算指标：数据不足时设为 None
            df['pct_chg'] = df['close'].pct_change()
            if len(df) >= 5:
                df['ma5'] = df['close'].rolling(5).mean()
                df['ma10'] = df['close'].rolling(10).mean()
                df['ma20'] = df['close'].rolling(20).mean()
            else:
                df['ma5'] = None
                df['ma10'] = None
                df['ma20'] = None

            with self._get_conn() as conn:
                rows = []
                for _, row in df.iterrows():
                    rows.append((
                        code, row['date'], row['open'], row['high'], row['low'],
                        row['close'], row['volume'], row['amount'],
                        row['pct_chg'], row['ma5'], row['ma10'], row['ma20']
                    ))
                conn.executemany(
                    "INSERT OR REPLACE INTO index_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows
                )
            return len(df)
        except Exception:
            return 0

    def get_index_kline(self, code, start_date=None, end_date=None):
        """获取指数K线数据"""
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d')

        with self._get_conn() as conn:
            df = pd.read_sql(
                "SELECT * FROM index_daily WHERE code=? AND date>=? AND date<=? ORDER BY date",
                conn, params=(code, start_date, end_date),
                parse_dates=['date']
            )
        return df if len(df) > 0 else None

    def get_market_regime(self, date, index_code='sh000300', lookback=20):
        """
        判断指定日期某指数对应的市场环境：上升/震荡/退潮

        基于指定指数的 MA 关系判断：
        - 退潮期(bear): 收盘价在MA20下方 且 MA5 < MA20（趋势向下）
        - 上升期(bull): 收盘价在MA20上方 且 MA5 > MA10（趋势向上）
        - 震荡期(range): 其他情况

        Args:
            date: 日期字符串 'YYYY-MM-DD'
            index_code: 指数代码，默认 sh000300 (沪深300)
            lookback: 回看天数，默认20

        Returns:
            'bull' (上升期), 'range' (震荡期), 'bear' (退潮期)
        """
        end_date = date
        start_date = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=lookback * 2)).strftime('%Y-%m-%d')
        df = self.get_index_kline(index_code, start_date, end_date)
        if df is None or len(df) < lookback:
            return 'range'

        latest = df.iloc[-1]
        close = latest['close']
        ma5 = latest.get('ma5', 0)
        ma10 = latest.get('ma10', 0)
        ma20 = latest.get('ma20', 0)

        if ma20 > 0 and close < ma20 and ma5 < ma20:
            return 'bear'
        elif ma20 > 0 and close > ma20 and ma5 > ma10:
            return 'bull'
        else:
            return 'range'

    def code_to_index(self, code):
        """
        根据股票代码返回对应的大盘指数代码
        主板 → 沪深300, 创业板 → 创业板指, 科创板 → 科创50
        """
        prefix = code.split('.')[1]
        if prefix.startswith('30'):
            return 'sz.399006'   # 创业板指
        elif prefix.startswith('68'):
            return 'sh.000688'   # 科创50
        else:
            return 'sh.000300'   # 沪深300（主板）

    def get_cache_stats(self):
        """获取缓存统计"""
        with self._get_conn() as conn:
            total_stocks = conn.execute("SELECT COUNT(*) FROM stock_meta").fetchone()[0]
            total_rows = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
            date_range = conn.execute("SELECT MIN(date), MAX(date) FROM stock_daily").fetchone()
            stocks_with_data = conn.execute("SELECT COUNT(DISTINCT code) FROM stock_daily").fetchone()[0]

        return {
            'total_stocks': total_stocks,
            'stocks_with_data': stocks_with_data,
            'total_rows': total_rows,
            'date_from': date_range[0] if date_range else 'N/A',
            'date_to': date_range[1] if date_range else 'N/A',
        }

    def close(self):
        """关闭连接（SQLite 自动管理，此方法保留兼容性）"""
        pass


# 全局实例
_data_layer = None

def get_data_layer():
    global _data_layer
    if _data_layer is None:
        _data_layer = StockDataLayer()
    return _data_layer

def init_data_layer():
    """初始化数据层（调用方使用）"""
    dl = get_data_layer()
    stats = dl.get_cache_stats()
    print(f"\n数据缓存状态:")
    print(f"  股票总数: {stats['total_stocks']}")
    print(f"  有数据的股票: {stats['stocks_with_data']}")
    print(f"  总K线记录: {stats['total_rows']:,}")
    print(f"  日期范围: {stats['date_from']} ~ {stats['date_to']}")
    return dl

if __name__ == '__main__':
    bs.login()
    dl = init_data_layer()

    # 全量初始化
    if dl.get_cache_stats()['total_stocks'] == 0:
        print("\n首次初始化，拉取全市场数据...")
        stock_list = dl.update_stock_list()
        codes = stock_list['code'].tolist()
        dl.batch_update(codes, verbose=True, total=len(codes))
    else:
        print("\n增量更新...")
        stock_list = dl.update_stock_list()
        codes = stock_list['code'].tolist()
        dl.batch_update(codes, verbose=True, total=len(codes))

    stats = dl.get_cache_stats()
    print(f"\n更新完成:")
    print(f"  有数据的股票: {stats['stocks_with_data']}")
    print(f"  总K线记录: {stats['total_rows']:,}")
    print(f"  日期范围: {stats['date_from']} ~ {stats['date_to']}")

    bs.logout()
