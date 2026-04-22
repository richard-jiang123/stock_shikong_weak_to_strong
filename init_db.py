#!/usr/bin/env python3
"""首次全量初始化本地数据库"""
import baostock as bs
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import get_data_layer

print("正在首次初始化全市场数据，预计 90-100 分钟...")
print("初始化完成后，每日增量更新仅需 2-5 分钟\n")

bs.login()
dl = get_data_layer()
stock_list = dl.update_stock_list()
codes = stock_list['code'].tolist()
dl.batch_update(codes, verbose=True, total=len(codes))

stats = dl.get_cache_stats()
print(f"\n✅ 初始化完成!")
print(f"  有数据的股票: {stats['stocks_with_data']}")
print(f"  总K线记录: {stats['total_rows']:,}")
print(f"  日期范围: {stats['date_from']} ~ {stats['date_to']}")

bs.logout()
