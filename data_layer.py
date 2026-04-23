#!/usr/bin/env python3
"""
弱转强策略 · 本地数据层
SQLite 缓存 + 增量更新，替代每次全量拉取 baostock
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
    """本地数据层：缓存K线数据，增量更新"""

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
                df = pd.read_sql("SELECT code, name FROM stock_meta", conn)
            if not df.empty:
                print(f"  API不可用，使用本地缓存: {len(df)} 只")
                return df
            raise RuntimeError("无法获取股票列表，API和本地缓存均不可用")

        mask = df['code'].str.match(r'^(sh\.60|sz\.00|sz\.30)\d{4}$')
        df = df[mask].copy()

        with self._get_conn() as conn:
            for _, row in df.iterrows():
                conn.execute(
                    "INSERT OR REPLACE INTO stock_meta (code, name, ipo_date, delist_date, updated_at) VALUES (?,?,?,?,?)",
                    (row['code'], row.get('code_name',''), row.get('ipoDate',''), row.get('delistDate',''),
                     datetime.now().strftime('%Y-%m-%d %H:%M'))
                )
        print(f"  股票列表已更新: {len(df)} 只")
        return df

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

    def is_all_updated(self, target_date=None):
        """
        检查是否所有股票的数据都已更新到目标日期。
        只检查股票数据（排除指数 sh/sz.000xxx, sh/sz.399xxx）。
        1. 先看股票数据的最大日期
        2. 如果最大日期 >= 目标日期，还需：
           a. 检查该日期是否有足够多的股票数据（≥100只）
           b. 检查是否还有大量股票未达到目标日期（上次更新中断检测）
        3. 如果最大日期 < 目标日期，采样检查确认是否真的是非交易日
           a. 如果非交易日 → 还需检查最大日期是否完整
        返回 (is_ready, max_date, sample_checked)。
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
                return False, None, 0
            # 如果最大日期 >= 目标日期，做两步检查
            if max_date >= target_date:
                stock_cnt = conn.execute("""
                    SELECT COUNT(DISTINCT code) FROM stock_daily WHERE date=?
                    AND (code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%')
                """, (target_date,)).fetchone()[0]
                # 至少 100 只股票有数据
                if stock_cnt < 100:
                    return False, max_date, stock_cnt
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
                    return False, max_date, stock_cnt
                return True, max_date, stock_cnt
            # 还没到目标日期，但可能是非交易日。采样检查5只活跃股票。
            cur = conn.execute("""
                SELECT code FROM stock_daily WHERE date=?
                AND (code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%')
                LIMIT 5
            """, (max_date,))
            sample = [r[0] for r in cur.fetchall()]
        if len(sample) < 2:
            return False, max_date, 0

        # 假设 baostock 已在外部登录
        has_today = False
        for code in sample:
            rs = bs.query_history_k_data_plus(code, "date",
                start_date=target_date, end_date=target_date, frequency="d")
            if rs.get_data() is not None and len(rs.get_data()) > 0:
                has_today = True
                break

        if has_today:
            # 今天确实是交易日，需要增量更新
            return False, max_date, len(sample)
        else:
            # 今天是非交易日 → 还需检查 max_date 是否完整
            with self._get_conn() as conn:
                max_date_cnt = conn.execute("""
                    SELECT COUNT(DISTINCT code) FROM stock_daily WHERE date=?
                    AND (code LIKE 'sh.60%' OR code LIKE 'sz.00%' OR code LIKE 'sz.30%')
                """, (max_date,)).fetchone()[0]
                if max_date_cnt < 100:
                    # 最大日期数据不完整，需要重新批量更新
                    return False, max_date, max_date_cnt
            return True, max_date, len(sample)

    def get_last_date(self, code):
        """获取某股票最后一条数据的日期"""
        with self._get_conn() as conn:
            cur = conn.execute("SELECT MAX(date) FROM stock_daily WHERE code=?", (code,))
            row = cur.fetchone()
            return row[0] if row and row[0] else None

    def fetch_from_api(self, code, start_date, end_date, max_retries=3):
        """从 baostock 获取数据，支持重试（假设已登录）"""
        for attempt in range(max_retries):
            try:
                rs = bs.query_history_k_data_plus(code,
                    "date,open,high,low,close,volume,amount,turn",
                    start_date=start_date, end_date=end_date,
                    frequency="d", adjustflag="2")
                df = rs.get_data()

                if df is None or len(df) == 0:
                    if attempt < max_retries - 1:
                        time.sleep(0.5)
                        continue
                    return None

                for c in ['open','high','low','close','volume','amount','turn']:
                    df[c] = df[c].astype(float)
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                df = df.sort_values('date').reset_index(drop=True)

                # 计算指标（至少需要5行才能计算ma5等指标，但如果数据不足就只保留基础数据）
                if len(df) >= 5:
                    df['pct_chg'] = df['close'].pct_change()
                    df['ma5'] = df['close'].rolling(5).mean()
                    df['ma10'] = df['close'].rolling(10).mean()
                    df['ma20'] = df['close'].rolling(20).mean()
                    df['volume_ma5'] = df['volume'].rolling(5).mean()
                    df['amplitude'] = (df['high'] - df['low']) / df['close']
                else:
                    # 数据不足时，只计算 pct_chg，其他指标设为 NaN
                    df['pct_chg'] = df['close'].pct_change()
                    df['ma5'] = None
                    df['ma10'] = None
                    df['ma20'] = None
                    df['volume_ma5'] = None
                    df['amplitude'] = (df['high'] - df['low']) / df['close']

                return df
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return None
        return None

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
        """增量更新单只股票数据"""
        end_date = datetime.now().strftime('%Y-%m-%d')

        if force_full:
            start_date = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
        else:
            last_date = self.get_last_date(code)
            if last_date:
                # 如果已经是今天的数据，跳过
                if last_date == end_date:
                    return 0
                last_dt = datetime.strptime(last_date, '%Y-%m-%d')
                start_date = (last_dt + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                # 首次：拉200天
                start_date = (datetime.now() - timedelta(days=200)).strftime('%Y-%m-%d')

        # 确保 start_date <= end_date
        if start_date > end_date:
            return 0

        df = self.fetch_from_api(code, start_date, end_date)
        if df is not None:
            return self.save_to_db(code, df)
        return 0

    def batch_update(self, codes, verbose=False, total=None):
        """批量增量更新，支持断点续传和 API 恢复等待。"""
        target_date = datetime.now().strftime('%Y-%m-%d')

        # 清理旧的运行中会话
        with self._get_conn() as conn:
            conn.execute("UPDATE update_session SET status='interrupted' WHERE status='running'")

        # 查找最近一次中断的会话，尝试续传
        resume_from = 0
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT progress FROM update_session WHERE status='interrupted' ORDER BY id DESC LIMIT 1")
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
        consecutive_failures = 0
        max_consecutive_failures = 100  # 连续失败阈值
        api_wait_interval = 10  # API恢复等待间隔（秒）
        api_wait_threshold = 20  # 达到此阈值后开始等待

        if resume_from > 0:
            print(f"  检测到上次中断，从第 {resume_from + 1}/{total_codes} 只续传...")

        for i, code in enumerate(codes):
            if i < resume_from:
                continue
            n = self.update_incremental(code)
            if n > 0:
                new_rows += n
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                # 当连续失败达到阈值时，等待一段时间让 API 恢复
                if consecutive_failures == api_wait_threshold:
                    print(f"  API 连续失败 {consecutive_failures} 次，等待 {api_wait_interval} 秒...")
                    time.sleep(api_wait_interval)
                elif consecutive_failures == api_wait_threshold * 2:
                    print(f"  API 连续失败 {consecutive_failures} 次，再次等待 {api_wait_interval} 秒...")
                    time.sleep(api_wait_interval)
            updated += 1
            # 每50只记录一次进度
            if (i + 1) % 50 == 0:
                with self._get_conn() as conn:
                    conn.execute("UPDATE update_session SET progress=? WHERE id=?", (i + 1, session_id))
            if verbose and total:
                if (i + 1) % 500 == 0 or i + 1 == total:
                    print(f"  更新进度 {i+1}/{total} | 新增 {new_rows} 行")
            # 最终失败阈值
            if consecutive_failures >= max_consecutive_failures:
                print(f"\n  ✗ API 连续失败 {max_consecutive_failures} 次，停止更新（baostock 不可用）")
                with self._get_conn() as conn:
                    conn.execute(
                        "UPDATE update_session SET status='interrupted', finished_at=? WHERE id=?",
                        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session_id))
                return updated, new_rows

        # 标记完成
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE update_session SET status='completed', finished_at=? WHERE id=?",
                (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session_id))

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

        if last_date:
            if last_date == end_date:
                return 0
            last_dt = datetime.strptime(last_date, '%Y-%m-%d')
            start_date = (last_dt + timedelta(days=1)).strftime('%Y-%m-%d')
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
            if df is None or len(df) < 5:
                return 0
            for c in ['open','high','low','close','volume','amount']:
                df[c] = df[c].astype(float)
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
            df = df.sort_values('date').reset_index(drop=True)
            df['pct_chg'] = df['close'].pct_change()
            df['ma5'] = df['close'].rolling(5).mean()
            df['ma10'] = df['close'].rolling(10).mean()
            df['ma20'] = df['close'].rolling(20).mean()

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
