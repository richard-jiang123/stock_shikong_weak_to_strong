#!/usr/bin/env python3
"""
多数据源支持模块

支持多个数据源相互备份：
1. baostock - 主数据源（免费，稳定，历史K线完整，但更新较慢 ~17:00）
2. tencent - 实时行情源（免费，盘中实时更新，用于补充当日数据）

自动切换逻辑：
- baostock历史数据优先
- 当日数据未更新时，使用腾讯实时行情补充当日数据
- 数据源状态监控和自动恢复
"""
import time
import requests
import pandas as pd
import baostock as bs
import re
from datetime import datetime, timedelta
from abc import ABC, abstractmethod


class DataSourceBase(ABC):
    """数据源抽象基类"""

    @abstractmethod
    def login(self):
        """登录/初始化"""
        pass

    @abstractmethod
    def logout(self):
        """注销/清理"""
        pass

    @abstractmethod
    def get_stock_list(self, date=None):
        """获取股票列表"""
        pass

    @abstractmethod
    def get_kline(self, code, start_date, end_date):
        """
        获取K线数据

        Returns:
            (df, status): DataFrame和状态('success'|'empty'|'error')
        """
        pass

    @abstractmethod
    def is_available(self):
        """检查数据源是否可用"""
        pass

    @property
    @abstractmethod
    def name(self):
        """数据源名称"""
        pass


class BaostockSource(DataSourceBase):
    """baostock数据源 - 主数据源，提供完整历史K线"""

    def __init__(self):
        self._logged_in = False

    @property
    def name(self):
        return 'baostock'

    def login(self):
        if not self._logged_in:
            lg = bs.login()
            self._logged_in = lg.error_code == '0'
        return self._logged_in

    def logout(self):
        if self._logged_in:
            bs.logout()
            self._logged_in = False

    def get_stock_list(self, date=None):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        rs = bs.query_all_stock(day=date)
        df = rs.get_data()
        if df is None or len(df) == 0:
            return None, 'empty'
        return df, 'success'

    def get_kline(self, code, start_date, end_date):
        if not self._logged_in:
            self.login()

        rs = bs.query_history_k_data_plus(code,
            "date,open,high,low,close,volume,amount,turn",
            start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="2")

        if rs.error_code != '0':
            return None, 'error'

        df = rs.get_data()
        if df is None or len(df) == 0:
            return None, 'empty'

        # 标准化数据格式
        for c in ['open','high','low','close','volume','amount','turn']:
            if c in df.columns:
                df[c] = df[c].astype(float)
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        df = df.sort_values('date').reset_index(drop=True)

        return df, 'success'

    def is_available(self):
        """检查baostock是否可用"""
        if not self._logged_in:
            self.login()
        # 尝试获取一只股票的数据
        df, status = self.get_kline('sh.000001',
            datetime.now().strftime('%Y-%m-%d'),
            datetime.now().strftime('%Y-%m-%d'))
        # 即使返回empty也表示API可用（只是数据未更新）
        return status in ('success', 'empty')


class TencentSource(DataSourceBase):
    """
    腾讯实时行情数据源 - 用于补充当日数据

    特点：
    - 只提供实时行情，不是历史K线
    - 盘中实时更新，数据最新
    - 用于在baostock当日数据未更新时补充当日数据
    """

    def __init__(self):
        self.base_url = "http://qt.gtimg.cn/q="
        self._data_cache = {}  # 缓存批量查询结果

    @property
    def name(self):
        return 'tencent'

    def login(self):
        # 无需登录
        return True

    def logout(self):
        # 无需注销
        self._data_cache.clear()

    def _code_to_tencent(self, code):
        """
        将股票代码转换为腾讯格式
        baostock: sh.600000, sz.000001
        tencent: sh600000, sz000001
        """
        return code.replace('.', '')

    def _parse_tencent_data(self, raw_string):
        """
        解析腾讯返回的数据格式

        格式: v_sh600000="1~浦发银行~600000~9.45~9.54~9.53~848590~..."

        字段索引（~分隔）：
        - 1: 名称
        - 2: 代码
        - 3: 当前价格
        - 4: 昨收
        - 5: 今开
        - 6: 成交量（手）
        - 30: 日期时间 (20260424161422)
        - 31: 涨跌额
        - 32: 涨跌幅(%)
        - 33: 最高
        - 34: 最低
        - 36: 成交量（手）
        - 37: 成交额（万元）
        - 38: 换手率(%)

        Returns:
            dict: 标准化的行情数据
        """
        # 提取引号内的数据
        match = re.search(r'="([^"]+)"', raw_string)
        if not match:
            return None

        parts = match.group(1).split('~')
        if len(parts) < 39:
            return None

        try:
            # 解析日期时间
            datetime_str = parts[30] if len(parts) > 30 else ''
            if datetime_str:
                date = datetime_str[:8]  # 20260424
                date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"  # 2026-04-24
            else:
                date = datetime.now().strftime('%Y-%m-%d')

            return {
                'date': date,
                'name': parts[1],
                'code': parts[2],
                'close': float(parts[3]) if parts[3] else 0,      # 当前价
                'pre_close': float(parts[4]) if parts[4] else 0,  # 昨收
                'open': float(parts[5]) if parts[5] else 0,       # 今开
                'volume': float(parts[6]) if parts[6] else 0,     # 成交量(手)
                'high': float(parts[33]) if len(parts) > 33 and parts[33] else 0,
                'low': float(parts[34]) if len(parts) > 34 and parts[34] else 0,
                'amount': float(parts[37]) * 10000 if len(parts) > 37 and parts[37] else 0,  # 万元转元
                'turn': float(parts[38]) if len(parts) > 38 and parts[38] else 0,  # 换手率%
                'pct_chg': float(parts[32]) if len(parts) > 32 and parts[32] else 0,  # 涨跌幅
            }
        except (ValueError, IndexError):
            return None

    def get_stock_list(self, date=None):
        """
        获取股票列表 - 腾讯不支持此功能
        返回empty，依赖baostock获取股票列表
        """
        return None, 'empty'

    def get_realtime_quote(self, codes):
        """
        批量获取实时行情

        Args:
            codes: 股票代码列表（baostock格式：sh.600000, sz.000001）

        Returns:
            dict: {code: quote_data}
        """
        if not codes:
            return {}

        # 转换代码格式
        tencent_codes = [self._code_to_tencent(c) for c in codes]

        # 分批请求（每批最多500个）
        batch_size = 500
        results = {}

        for i in range(0, len(tencent_codes), batch_size):
            batch = tencent_codes[i:i+batch_size]
            url = self.base_url + ','.join(batch)

            try:
                r = requests.get(url, timeout=10)
                if r.status_code != 200:
                    continue

                # 解析每只股票的数据
                lines = r.text.strip().split('\n')
                for line in lines:
                    if not line.startswith('v_'):
                        continue

                    # 从原始代码提取
                    match = re.match(r'v_(sh|sz)(\d+)=', line)
                    if not match:
                        continue

                    market = match.group(1)
                    code_num = match.group(2)
                    original_code = f"{market}.{code_num}"

                    data = self._parse_tencent_data(line)
                    if data:
                        results[original_code] = data

            except Exception as e:
                continue

        return results

    def get_kline(self, code, start_date, end_date):
        """
        获取K线数据 - 腾讯只提供实时行情

        如果end_date是今天，返回当日实时行情数据
        否则返回empty（历史数据需要baostock）
        """
        today = datetime.now().strftime('%Y-%m-%d')

        # 只有当end_date是今天时才返回数据
        if end_date != today:
            return None, 'empty'

        # 获取实时行情
        quotes = self.get_realtime_quote([code])
        if code not in quotes:
            return None, 'empty'

        q = quotes[code]
        # 转换为DataFrame格式
        df = pd.DataFrame([{
            'date': q['date'],
            'open': q['open'],
            'high': q['high'],
            'low': q['low'],
            'close': q['close'],
            'volume': q['volume'] * 100,  # 手转股（baostock单位是股）
            'amount': q['amount'],
            'turn': q['turn'],
            'pct_chg': q['pct_chg'] / 100,  # 百分比转小数
        }])

        return df, 'success'

    def is_available(self):
        """检查腾讯接口是否可用"""
        try:
            quotes = self.get_realtime_quote(['sh.600000'])
            return 'sh.600000' in quotes
        except Exception:
            return False


class MultiSourceManager:
    """多数据源管理器 - 智能切换baostock和腾讯数据源"""

    def __init__(self, primary='baostock', realtime='tencent'):
        self.sources = {
            'baostock': BaostockSource(),
            'tencent': TencentSource(),
        }
        self.primary_name = primary
        self.realtime_name = realtime
        self.primary = self.sources[primary]
        self.realtime = self.sources[realtime]

        # 数据源状态
        self.primary_available = True
        self.realtime_available = True
        self.last_check_time = None

    def login(self):
        """初始化所有数据源"""
        self.primary.login()
        self.realtime.login()
        self._check_availability()

    def logout(self):
        """清理所有数据源"""
        self.primary.logout()
        self.realtime.logout()

    def _check_availability(self):
        """检查数据源可用性"""
        now = datetime.now()
        # 每小时检查一次
        if self.last_check_time and (now - self.last_check_time).seconds < 3600:
            return

        self.primary_available = self.primary.is_available()
        self.realtime_available = self.realtime.is_available()
        self.last_check_time = now

        print(f"数据源状态: {self.primary_name}={self.primary_available}, {self.realtime_name}={self.realtime_available}")

    def get_stock_list(self, date=None):
        """获取股票列表 - 只使用baostock"""
        if self.primary_available:
            df, status = self.primary.get_stock_list(date)
            if status == 'success':
                return df, 'success', self.primary_name
        return None, 'error', None

    def get_kline(self, code, start_date, end_date):
        """
        获取K线数据，智能补充当日数据

        策略：
        1. 先用baostock获取历史数据（start_date到end_date前一天）
        2. 如果end_date是今天且baostock无当日数据，用腾讯补充当日数据

        Returns:
            (df, status, source_info): DataFrame, 状态, 来源信息
        """
        today = datetime.now().strftime('%Y-%m-%d')
        df_combined = None
        sources_used = []

        # 1. 从baostock获取数据
        if self.primary_available:
            df_bs, status_bs = self.primary.get_kline(code, start_date, end_date)

            if status_bs == 'success' and df_bs is not None:
                df_combined = df_bs
                sources_used.append('baostock')

                # 检查是否已有当日数据
                dates_in_df = df_bs['date'].tolist()
                if today in dates_in_df:
                    # baostock已有当日数据，直接返回
                    return df_combined, 'success', 'baostock'

            elif status_bs == 'empty':
                # baostock返回空（可能是数据未更新）
                # 继续尝试腾讯补充当日数据
                pass

        # 2. 用腾讯补充当日数据（如果end_date包含今天）
        if end_date == today and self.realtime_available:
            df_rt, status_rt = self.realtime.get_kline(code, today, today)

            if status_rt == 'success' and df_rt is not None:
                sources_used.append('tencent')

                # 合并数据
                if df_combined is not None:
                    # 检查是否已存在当日数据（避免重复）
                    if today not in df_combined['date'].tolist():
                        df_combined = pd.concat([df_combined, df_rt], ignore_index=True)
                        df_combined = df_combined.sort_values('date').reset_index(drop=True)
                else:
                    # 只有腾讯数据（可能是单日查询）
                    df_combined = df_rt

        # 返回结果
        if df_combined is not None and len(df_combined) > 0:
            source_info = '+'.join(sources_used) if len(sources_used) > 1 else sources_used[0]
            return df_combined, 'success', source_info

        return None, 'empty', None

    def get_kline_with_realtime_supplement(self, code, start_date, end_date):
        """
        获取K线数据，并强制用腾讯补充当日数据

        用于数据更新场景：
        - baostock历史数据完整
        - 当日数据用腾讯实时行情补充

        Returns:
            (df, status, has_realtime): DataFrame, 状态, 是否包含实时数据
        """
        today = datetime.now().strftime('%Y-%m-%d')
        has_realtime = False

        # 1. 从baostock获取历史数据（到今天之前）
        if self.primary_available:
            # 获取到昨天的数据
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            df_bs, status_bs = self.primary.get_kline(code, start_date, yesterday)

            if status_bs == 'success' and df_bs is not None:
                df_combined = df_bs
            else:
                df_combined = None

        # 2. 用腾讯获取当日实时数据
        if end_date >= today and self.realtime_available:
            df_rt, status_rt = self.realtime.get_kline(code, today, today)

            if status_rt == 'success' and df_rt is not None:
                has_realtime = True
                if df_combined is not None:
                    df_combined = pd.concat([df_combined, df_rt], ignore_index=True)
                    df_combined = df_combined.sort_values('date').reset_index(drop=True)
                else:
                    df_combined = df_rt

        if df_combined is not None and len(df_combined) > 0:
            return df_combined, 'success', has_realtime

        return None, 'empty', False

    def batch_get_realtime_quotes(self, codes):
        """
        批量获取实时行情

        Args:
            codes: 股票代码列表

        Returns:
            dict: {code: quote_data}
        """
        if not self.realtime_available:
            return {}

        return self.realtime.get_realtime_quote(codes)

    def get_kline_batch(self, codes, start_date, end_date):
        """
        批量获取K线数据

        Returns:
            dict: {code: DataFrame}
        """
        results = {}

        for code in codes:
            df, status, source = self.get_kline(code, start_date, end_date)
            if status == 'success' and df is not None:
                results[code] = df

        return results


# 全局实例
_multi_source = None

def get_multi_source():
    global _multi_source
    if _multi_source is None:
        _multi_source = MultiSourceManager()
    return _multi_source


if __name__ == '__main__':
    print("="*60)
    print("多数据源测试")
    print("="*60)

    manager = get_multi_source()
    manager.login()

    # 测试1: 获取历史+当日数据
    print("\n[测试1] 获取K线数据（历史+当日补充）")
    test_codes = ['sh.600000', 'sz.000001', 'sh.000001']

    for code in test_codes:
        df, status, source = manager.get_kline(code, '2026-04-22', '2026-04-24')
        rows = 0 if df is None else len(df)
        print(f"  {code}: rows={rows}, source={source}")
        if df is not None and len(df) > 0:
            print(f"    最新日期: {df['date'].iloc[-1]}")

    # 测试2: 批量获取实时行情
    print("\n[测试2] 批量获取实时行情")
    quotes = manager.batch_get_realtime_quotes(['sh.600000', 'sz.000001', 'sh.000001'])
    for code, q in quotes.items():
        print(f"  {code}: 价格={q['close']}, 涨幅={q['pct_chg']}%, 换手={q['turn']}%")

    # 测试3: 数据源可用性
    print(f"\n[数据源状态]")
    print(f"  baostock: {manager.primary_available}")
    print(f"  tencent: {manager.realtime_available}")

    manager.logout()
    print("\n测试完成")