/**
 * 公司/标的实体
 */
export interface Company {
  id: string;
  code: string; // 股票代码
  name: string; // 公司名称
  market: 'A股' | '港股' | '美股'; // 市场
  industry?: string; // 行业
  description?: string; // 描述
  createdAt: Date;
  updatedAt: Date;
}

/**
 * 事件实体
 */
export interface Event {
  id: string;
  companyId: string;
  title: string; // 事件标题
  description?: string; // 事件描述
  eventDate: Date; // 事件日期
  eventType: string; // 事件类型（财报、分红、重组等）
  impact?: 'positive' | 'negative' | 'neutral'; // 影响
  source?: string; // 来源
  createdAt: Date;
  updatedAt: Date;
}

/**
 * 交易记录实体
 */
export interface Trade {
  id: string;
  companyId: string;
  tradeType: 'buy' | 'sell'; // 交易类型
  quantity: number; // 数量
  price: number; // 价格
  amount: number; // 金额
  fee?: number; // 手续费
  tradeDate: Date; // 交易日期
  note?: string; // 备注
  createdAt: Date;
  updatedAt: Date;
}

/**
 * K线数据实体
 */
export interface Kline {
  id: string;
  companyId: string;
  interval: string; // 时间间隔（1d, 1w, 1M等）
  openTime: Date; // 开盘时间
  open: number; // 开盘价
  high: number; // 最高价
  low: number; // 最低价
  close: number; // 收盘价
  volume: number; // 成交量
  closeTime: Date; // 收盘时间
  createdAt: Date;
}

/**
 * 实时行情数据
 */
export interface Quote {
  symbol: string;
  name: string;
  price: number; // 当前价
  change: number; // 涨跌额
  changePercent: number; // 涨跌幅
  open: number; // 开盘价
  high: number; // 最高价
  low: number; // 最低价
  volume: number; // 成交量
  amount: number; // 成交额
  timestamp: Date;
}
