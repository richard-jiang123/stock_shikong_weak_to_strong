#!/usr/bin/env python3
"""
弱转强策略 · 本地数据层
SQLite 缓存 + 增量更新，替代每次全量拉取 baostock
"""
import sqlite3
import os
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
            ''')

    def update_stock_list(self):
        """更新股票列表"""
        rs = bs.query_all_stock(day=datetime.now().strftime('%Y-%m-%d'))
        df = rs.get_data()
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

    def get_last_date(self, code):
        """获取某股票最后一条数据的日期"""
        with self._get_conn() as conn:
            cur = conn.execute("SELECT MAX(date) FROM stock_daily WHERE code=?", (code,))
            row = cur.fetchone()
            return row[0] if row and row[0] else None

    def fetch_from_api(self, code, start_date, end_date):
        """从 baostock 获取数据"""
        try:
            rs = bs.query_history_k_data_plus(code,
                "date,open,high,low,close,volume,amount,turn",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="2")
            df = rs.get_data()
            if df is None or len(df) < 5:
                return None
            for c in ['open','high','low','close','volume','amount','turn']:
                df[c] = df[c].astype(float)
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
            df = df.sort_values('date').reset_index(drop=True)

            # 计算指标
            df['pct_chg'] = df['close'].pct_change()
            df['ma5'] = df['close'].rolling(5).mean()
            df['ma10'] = df['close'].rolling(10).mean()
            df['ma20'] = df['close'].rolling(20).mean()
            df['volume_ma5'] = df['volume'].rolling(5).mean()
            df['amplitude'] = (df['high'] - df['low']) / df['close']

            return df
        except Exception as e:
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
                "INSERT OR REPLACE INTO stock_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
        if force_full:
            start_date = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
        else:
            last_date = self.get_last_date(code)
            if last_date:
                # 增量：从最后日期之后开始
                last_dt = datetime.strptime(last_date, '%Y-%m-%d')
                start_date = (last_dt + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                # 首次：拉200天
                start_date = (datetime.now() - timedelta(days=200)).strftime('%Y-%m-%d')

        end_date = datetime.now().strftime('%Y-%m-%d')

        # 如果增量窗口太小或首次，拉完整数据
        if not last_date or (datetime.now() - datetime.strptime(last_date, '%Y-%m-%d')).days > 10:
            start_date = (datetime.now() - timedelta(days=200)).strftime('%Y-%m-%d')
            force_fetch = True
        else:
            force_fetch = False

        df = self.fetch_from_api(code, start_date, end_date)
        if df is not None:
            return self.save_to_db(code, df)
        return 0

    def batch_update(self, codes, verbose=False, total=None):
        """批量增量更新"""
        updated = 0
        new_rows = 0
        for i, code in enumerate(codes):
            n = self.update_incremental(code)
            new_rows += n
            updated += 1
            if verbose and total:
                if (i + 1) % 500 == 0 or i + 1 == total:
                    print(f"  更新进度 {i+1}/{total} | 新增 {new_rows} 行")
        return updated, new_rows

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
