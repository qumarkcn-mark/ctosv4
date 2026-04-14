"""
富途数据接口
===========
对接 futu-api，支持 A股/港股/美股 K线数据获取。
需要本地运行 FutuOpenD 网关。
"""
from futu import (
    OpenQuoteContext, KLType, AuType,
    RET_OK
)

from Common.CEnum import AUTYPE, DATA_FIELD, KL_TYPE
from Common.CTime import CTime
from Common.func_util import kltype_lt_day
from KLine.KLine_Unit import CKLine_Unit

from .CommonStockAPI import CCommonStockApi


# 富途K线级别映射
KL_TYPE_MAP = {
    KL_TYPE.K_1M: KLType.K_1M,
    KL_TYPE.K_3M: KLType.K_3M,
    KL_TYPE.K_5M: KLType.K_5M,
    KL_TYPE.K_15M: KLType.K_15M,
    KL_TYPE.K_30M: KLType.K_30M,
    KL_TYPE.K_60M: KLType.K_60M,
    KL_TYPE.K_DAY: KLType.K_DAY,
    KL_TYPE.K_WEEK: KLType.K_WEEK,
    KL_TYPE.K_MON: KLType.K_MON,
    KL_TYPE.K_QUARTER: KLType.K_QUARTER,
    KL_TYPE.K_YEAR: KLType.K_YEAR,
}

# 富途复权类型映射
AUTYPE_MAP = {
    AUTYPE.QFQ: AuType.QFQ,
    AUTYPE.HFQ: AuType.HFQ,
    AUTYPE.NONE: AuType.NONE,
}


def parse_futu_time(time_str: str) -> CTime:
    """
    解析富途返回的时间字符串
    日线及以上: "2024-01-02"
    分钟线: "2024-01-02 10:30:00"
    """
    if len(time_str) == 10:
        year = int(time_str[:4])
        month = int(time_str[5:7])
        day = int(time_str[8:10])
        hour = minute = 0
    elif len(time_str) >= 19:
        year = int(time_str[:4])
        month = int(time_str[5:7])
        day = int(time_str[8:10])
        hour = int(time_str[11:13])
        minute = int(time_str[14:16])
    else:
        raise Exception(f"未知的富途时间格式: {time_str}")
    return CTime(year, month, day, hour, minute)


class CFutuStock(CCommonStockApi):
    """
    富途数据接口。

    使用前需要：
    1. 安装 FutuOpenD 并启动（默认端口 11111）
    2. pip install futu-api

    股票代码格式：
    - A股: SH.600000, SZ.000001
    - 港股: HK.00700
    - 美股: US.AAPL
    """
    quote_ctx = None

    def __init__(self, code, k_type=KL_TYPE.K_DAY, begin_date=None, end_date=None, autype=AUTYPE.QFQ):
        super(CFutuStock, self).__init__(code, k_type, begin_date, end_date, autype)

    def get_kl_data(self):
        kl_type = KL_TYPE_MAP.get(self.k_type)
        if kl_type is None:
            raise Exception(f"富途不支持的K线级别: {self.k_type}")

        au_type = AUTYPE_MAP.get(self.autype, AuType.QFQ)

        # 获取历史K线
        ret, data, _ = self.quote_ctx.request_history_kline(
            code=self.code,
            start=self.begin_date,
            end=self.end_date,
            ktype=kl_type,
            autype=au_type,
            max_count=10000,
        )

        if ret != RET_OK:
            raise Exception(f"富途获取数据失败: {data}")

        for _, row in data.iterrows():
            time_key = parse_futu_time(row['time_key'])

            item_dict = {
                DATA_FIELD.FIELD_TIME: time_key,
                DATA_FIELD.FIELD_OPEN: float(row['open']),
                DATA_FIELD.FIELD_HIGH: float(row['high']),
                DATA_FIELD.FIELD_LOW: float(row['low']),
                DATA_FIELD.FIELD_CLOSE: float(row['close']),
            }

            # 日线及以上级别附带成交量/成交额/换手率
            if not kltype_lt_day(self.k_type):
                item_dict[DATA_FIELD.FIELD_VOLUME] = float(row.get('volume', 0))
                item_dict[DATA_FIELD.FIELD_TURNOVER] = float(row.get('turnover', 0))
                if 'turnover_rate' in row:
                    item_dict[DATA_FIELD.FIELD_TURNRATE] = float(row.get('turnover_rate', 0))

            yield CKLine_Unit(item_dict)

    def SetBasciInfo(self):
        self.name = self.code
        self.is_stock = True

    @classmethod
    def do_init(cls):
        if cls.quote_ctx is None:
            cls.quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
            print(f"[Futu] 已连接 FutuOpenD (127.0.0.1:11111)")

    @classmethod
    def do_close(cls):
        if cls.quote_ctx is not None:
            cls.quote_ctx.close()
            cls.quote_ctx = None
            print(f"[Futu] 已断开连接")
