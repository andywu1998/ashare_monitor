# Service层实现指南

## 概述

当前项目已经创建了Service层的框架代码，但实际的数据库操作需要在Node升级和Prisma安装完成后实现。

## 文件说明

### 框架文件（当前使用）
- `service/company.service.ts` - 框架代码，方法返回TODO
- `service/event.service.ts` - 框架代码
- `service/market.service.ts` - 框架代码
- `service/trade.service.ts` - 框架代码

### 实现参考文件（已创建）
- `service/company.service.impl.ts` - 完整实现示例
- `service/event.service.impl.ts` - 完整实现示例

## 如何切换到完整实现

### 方法1：替换文件内容

等Node升级和Prisma安装完成后：

```bash
# 备份原文件
cp service/company.service.ts service/company.service.backup.ts
cp service/event.service.ts service/event.service.backup.ts

# 使用完整实现替换
cp service/company.service.impl.ts service/company.service.ts
cp service/event.service.impl.ts service/event.service.ts
```

### 方法2：手动复制实现

打开 `.impl.ts` 文件，复制具体的方法实现到对应的 `.service.ts` 文件中。

## 实现示例对比

### 框架代码（当前）

```typescript
async getAllCompanies() {
  try {
    // TODO: 实现数据库查询
    return [];
  } catch (error) {
    this.handleError(error, '获取公司列表失败');
  }
}
```

### 完整实现（参考）

```typescript
async getAllCompanies(): Promise<Company[]> {
  try {
    const companies = await prisma.company.findMany({
      orderBy: { createdAt: 'desc' },
      include: {
        _count: {
          select: {
            events: true,
            trades: true,
            klines: true
          }
        }
      }
    });
    return companies as any;
  } catch (error) {
    this.handleError(error, '获取公司列表失败');
  }
}
```

## CompanyService 完整功能

### 已实现的方法

1. **getAllCompanies()** - 获取所有公司，包含关联数据计数
2. **getCompanyById(id)** - 获取公司详情，包含最近事件和交易
3. **getCompanyByCode(code)** - 根据股票代码获取公司
4. **createCompany(data)** - 创建公司，自动检查代码重复
5. **updateCompany(id, data)** - 更新公司信息
6. **deleteCompany(id)** - 删除公司（级联删除关联数据）
7. **searchCompanies(keyword)** - 搜索公司（按代码或名称）
8. **getCompaniesByMarket(market)** - 按市场筛选
9. **getCompaniesByIndustry(industry)** - 按行业筛选

### 使用示例

```typescript
import { companyService } from '@/service';

// 创建公司
const company = await companyService.createCompany({
  code: '600000',
  name: '浦发银行',
  market: 'A股',
  industry: '银行'
});

// 获取所有公司
const companies = await companyService.getAllCompanies();

// 搜索公司
const results = await companyService.searchCompanies('浦发');
```

## EventService 完整功能

### 已实现的方法

1. **getAllEvents()** - 获取所有事件，包含公司信息
2. **getEventsByCompanyId(companyId)** - 获取公司的所有事件
3. **getEventsByDateRange(start, end)** - 按日期范围查询
4. **getEventsByType(eventType)** - 按事件类型筛选
5. **createEvent(data)** - 创建事件，自动验证公司存在
6. **updateEvent(id, data)** - 更新事件
7. **deleteEvent(id)** - 删除事件
8. **getUpcomingEvents(days)** - 获取即将发生的事件

### 使用示例

```typescript
import { eventService } from '@/service';

// 创建事件
const event = await eventService.createEvent({
  companyId: 'xxx',
  title: '2024年Q4财报发布',
  eventDate: '2024-01-30',
  eventType: '财报',
  impact: 'positive'
});

// 获取即将发生的事件
const upcoming = await eventService.getUpcomingEvents(30);
```

## TradeService 待实现

需要实现以下方法：

```typescript
// 交易记录CRUD
async getAllTrades()
async getTradesByCompanyId(companyId)
async createTrade(data)
async updateTrade(id, data)
async deleteTrade(id)

// 持仓计算
async calculateProfitLoss(companyId) {
  // 1. 获取该公司的所有交易记录
  // 2. 计算总成本（买入金额 + 手续费）
  // 3. 计算当前市值（持仓数量 × 当前价格）
  // 4. 计算盈亏和盈亏率
}

// 持仓统计
async getPositions() {
  // 统计所有持仓
}
```

## MarketService 待实现

需要对接外部行情API：

```typescript
// K线数据
async getKlineData(symbol, interval, startTime?, endTime?) {
  // 1. 先查询数据库缓存
  // 2. 如果没有，调用外部API
  // 3. 存储到数据库
  // 4. 返回数据
}

// 实时行情
async getRealtimeQuote(symbol) {
  // 1. 调用外部API（QOS/iTick/Finnhub）
  // 2. 缓存到Redis（可选）
  // 3. 返回数据
}

// 批量行情
async getBatchQuotes(symbols) {
  // 并行调用多个API
}

// 数据同步
async syncHistoricalData(symbol, startDate, endDate) {
  // 定时任务调用，批量同步历史数据
}
```

### 行情API集成示例

```typescript
// 使用fetch调用外部API
const response = await fetch(`https://api.example.com/quote?symbol=${symbol}`, {
  headers: {
    'Authorization': `Bearer ${process.env.API_KEY}`
  }
});

const data = await response.json();

// 存储到数据库
await prisma.kline.create({
  data: {
    companyId: company.id,
    interval: '1d',
    openTime: data.openTime,
    open: data.open,
    high: data.high,
    low: data.low,
    close: data.close,
    volume: data.volume,
    closeTime: data.closeTime
  }
});
```

## Prisma查询技巧

### 1. 包含关联数据

```typescript
const company = await prisma.company.findUnique({
  where: { id },
  include: {
    events: true,
    trades: true
  }
});
```

### 2. 计数

```typescript
const company = await prisma.company.findMany({
  include: {
    _count: {
      select: {
        events: true,
        trades: true
      }
    }
  }
});
```

### 3. 筛选和排序

```typescript
const events = await prisma.event.findMany({
  where: {
    eventDate: {
      gte: startDate,
      lte: endDate
    }
  },
  orderBy: {
    eventDate: 'desc'
  }
});
```

### 4. 模糊搜索

```typescript
const companies = await prisma.company.findMany({
  where: {
    OR: [
      { code: { contains: keyword, mode: 'insensitive' } },
      { name: { contains: keyword, mode: 'insensitive' } }
    ]
  }
});
```

### 5. 分页

```typescript
const companies = await prisma.company.findMany({
  skip: (page - 1) * limit,
  take: limit,
  orderBy: { createdAt: 'desc' }
});
```

## 错误处理

所有Service方法都继承了基类的错误处理：

```typescript
protected handleError(error: unknown, message: string): never {
  console.error(`${message}:`, error);
  if (error instanceof Error) {
    throw new Error(`${message}: ${error.message}`);
  }
  throw new Error(message);
}
```

调用时会自动处理：

```typescript
try {
  const company = await companyService.createCompany(data);
} catch (error) {
  // error是被包装过的Error对象
  console.error(error.message);
}
```

## 测试Service

```typescript
// 测试文件: service/__tests__/company.service.test.ts

import { companyService } from '../company.service';

describe('CompanyService', () => {
  it('should create a company', async () => {
    const company = await companyService.createCompany({
      code: 'TEST001',
      name: '测试公司',
      market: 'A股'
    });

    expect(company.code).toBe('TEST001');
    expect(company.name).toBe('测试公司');
  });

  it('should get all companies', async () => {
    const companies = await companyService.getAllCompanies();
    expect(Array.isArray(companies)).toBe(true);
  });
});
```

## 下一步

1. ✅ 等待Node升级完成
2. ⬜ 安装Prisma: `npm install prisma @prisma/client`
3. ⬜ 生成Prisma客户端: `npm run prisma:generate`
4. ⬜ 配置数据库连接
5. ⬜ 推送Schema: `npm run prisma:push`
6. ⬜ 替换Service实现（使用.impl.ts文件）
7. ⬜ 测试API端点
8. ⬜ 实现TradeService和MarketService

## 参考资源

- [Prisma文档](https://www.prisma.io/docs)
- [Prisma查询示例](https://www.prisma.io/docs/concepts/components/prisma-client/crud)
- [Next.js API Routes](https://nextjs.org/docs/app/building-your-application/routing/route-handlers)
