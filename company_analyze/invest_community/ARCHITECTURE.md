# 投资社区平台 - 架构说明

## 架构设计

本项目采用 Next.js 15 全栈一体化架构，前后端代码在同一个项目中。

### 分层架构

```
┌─────────────────────────────────────┐
│         前端 (React/Next.js)         │
│  - 页面组件 (app/page.tsx)          │
│  - UI组件 (components/)             │
│  - 客户端交互                        │
└─────────────────────────────────────┘
                 ↓ HTTP
┌─────────────────────────────────────┐
│         API层 (app/api/)            │
│  - REST API路由                      │
│  - 请求验证                          │
│  - 响应格式化                        │
└─────────────────────────────────────┘
                 ↓
┌─────────────────────────────────────┐
│         服务层 (service/)            │
│  - 业务逻辑                          │
│  - 数据处理                          │
│  - 外部API调用                       │
└─────────────────────────────────────┘
                 ↓
┌─────────────────────────────────────┐
│       数据层 (Prisma + DB)          │
│  - ORM操作                          │
│  - 数据持久化                        │
│  - 查询优化                          │
└─────────────────────────────────────┘
```

## 目录结构详解

### 1. app/ - Next.js应用目录

#### app/api/ - API路由层
负责HTTP请求处理，参数验证，调用服务层

- `companies/` - 公司管理API
  - `route.ts` - GET(列表) / POST(创建)
  - `[id]/route.ts` - GET(详情) / PUT(更新) / DELETE(删除)

- `events/` - 事件管理API
  - `route.ts` - GET(列表/筛选) / POST(创建)
  - `[id]/route.ts` - PUT(更新) / DELETE(删除)

- `market/` - 行情数据API
  - `kline/route.ts` - GET K线数据
  - `quote/route.ts` - GET 实时行情

- `trades/` - 交易记录API
  - `route.ts` - GET(列表) / POST(创建)
  - `[id]/route.ts` - PUT(更新) / DELETE(删除)

#### app/ - 页面文件
- `layout.tsx` - 根布局（全局HTML结构）
- `page.tsx` - 首页
- `globals.css` - 全局样式

### 2. service/ - 服务层

业务逻辑核心，负责数据处理和业务规则

- `base.service.ts` - 基础服务类
  - 统一错误处理
  - 公共方法

- `company.service.ts` - 公司服务
  - CRUD操作
  - 公司信息管理

- `event.service.ts` - 事件服务
  - 事件管理
  - 日期范围查询
  - 公司事件关联

- `market.service.ts` - 行情服务
  - K线数据获取
  - 实时行情查询
  - 历史数据同步
  - 对接外部行情API

- `trade.service.ts` - 交易服务
  - 交易记录管理
  - 持仓盈亏计算

- `index.ts` - 统一导出

### 3. lib/ - 工具库

公共工具和类型定义

- `prisma.ts` - Prisma客户端单例
- `types.ts` - 数据实体类型定义
- `api-types.ts` - API接口类型定义

### 4. prisma/ - 数据库配置

- `schema.prisma` - 数据库模型定义
  - Company - 公司表
  - Event - 事件表
  - Trade - 交易表
  - Kline - K线数据表

### 5. components/ - React组件

UI组件库（待开发）
- 日历组件
- K线图组件
- 表单组件
- 列表组件

## 数据流

### 1. 读取数据流程
```
用户请求 → API Route → Service层 → Prisma → PostgreSQL
                                          ↓
用户响应 ← API Route ← Service层 ← 数据库查询结果
```

### 2. 写入数据流程
```
用户提交 → API Route(验证) → Service层(业务逻辑) → Prisma → PostgreSQL
                                                          ↓
用户响应 ← API Route ← Service层 ← 数据库写入结果
```

### 3. 行情数据流程
```
定时任务 → Market Service → 外部行情API → 数据处理 → Prisma → PostgreSQL
                                                              ↓
前端请求 → API Route → Market Service → Prisma → 返回缓存/数据库数据
```

## 服务层设计模式

### 单例模式
所有Service类采用单例模式，确保全局唯一实例

```typescript
class CompanyService extends BaseService {
  private static instance: CompanyService;

  static getInstance(): CompanyService {
    if (!CompanyService.instance) {
      CompanyService.instance = new CompanyService();
    }
    return CompanyService.instance;
  }
}
```

### 错误处理
统一的错误处理机制

```typescript
protected handleError(error: unknown, message: string): never {
  console.error(`${message}:`, error);
  if (error instanceof Error) {
    throw new Error(`${message}: ${error.message}`);
  }
  throw new Error(message);
}
```

## API响应格式

统一的API响应格式

```typescript
// 成功响应
{
  success: true,
  data: {...}
}

// 错误响应
{
  success: false,
  error: "错误信息"
}
```

## 数据库关系

```
Company (公司)
  ├── events[] (多个事件)
  ├── trades[] (多个交易记录)
  └── klines[] (多个K线数据)

级联删除：删除公司时，自动删除关联的事件、交易、K线数据
```

## 扩展性设计

### 1. 添加新的业务模块
1. 在 `prisma/schema.prisma` 添加数据模型
2. 在 `service/` 创建对应服务类
3. 在 `app/api/` 创建API路由
4. 在 `lib/types.ts` 添加类型定义

### 2. 接入新的行情数据源
在 `market.service.ts` 中添加新的数据源适配器：
```typescript
async fetchFromNewSource(symbol: string) {
  // 实现新数据源的接入逻辑
}
```

### 3. 添加缓存层
可以在Service层添加Redis缓存：
```typescript
async getCachedData(key: string) {
  // 先查Redis
  // 如果没有，查数据库并写入缓存
}
```

## 性能优化建议

1. **数据库查询优化**
   - 使用Prisma的索引配置
   - 分页查询大量数据
   - 使用select减少返回字段

2. **API响应优化**
   - 添加Redis缓存热点数据
   - 使用Next.js的ISR增量静态生成
   - 实现数据预加载

3. **K线数据优化**
   - 使用TimescaleDB存储时序数据
   - 实现数据聚合缓存
   - 按需加载历史数据

## 安全性考虑

1. **输入验证**
   - API层参数验证
   - SQL注入防护（Prisma自动处理）

2. **身份认证**（待实现）
   - NextAuth.js集成
   - JWT Token认证

3. **权限控制**（待实现）
   - 基于角色的访问控制(RBAC)

## 部署架构

```
Vercel/Cloud Platform
├── Next.js应用
├── API Routes (Serverless Functions)
└── Static Assets

外部服务
├── PostgreSQL数据库 (Supabase/Neon)
├── Redis缓存 (Upstash)
└── 行情API服务 (QOS/iTick)
```

## 待实现功能

- [ ] 用户认证系统
- [ ] K线图组件集成
- [ ] 日历视图组件
- [ ] 数据导入导出
- [ ] 定时任务（行情数据同步）
- [ ] WebSocket实时推送
- [ ] 移动端适配
