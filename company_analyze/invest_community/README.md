# 投资社区平台

基于 Next.js 15 全栈开发的股票投资管理系统。

## 技术栈

- **前端框架**: Next.js 15 (App Router)
- **UI**: Tailwind CSS
- **后端**: Next.js API Routes
- **数据库**: PostgreSQL
- **ORM**: Prisma
- **语言**: TypeScript

## 项目结构

```
invest_community/
├── app/                    # Next.js App Router
│   ├── api/               # API路由
│   │   ├── companies/    # 公司管理API
│   │   ├── events/       # 事件管理API
│   │   ├── market/       # 行情数据API
│   │   └── trades/       # 交易记录API
│   ├── layout.tsx        # 根布局
│   ├── page.tsx          # 首页
│   └── globals.css       # 全局样式
├── service/               # 服务层
│   ├── base.service.ts   # 基础服务类
│   ├── company.service.ts # 公司服务
│   ├── event.service.ts  # 事件服务
│   ├── market.service.ts # 行情服务
│   ├── trade.service.ts  # 交易服务
│   └── index.ts          # 统一导出
├── lib/                   # 工具库
│   ├── prisma.ts         # Prisma客户端
│   ├── types.ts          # 类型定义
│   └── api-types.ts      # API类型定义
├── prisma/                # Prisma配置
│   └── schema.prisma     # 数据库模型
├── components/            # React组件
└── public/               # 静态资源

```

## 功能模块

### 1. 公司/标的管理
- 添加、编辑、删除公司信息
- 支持A股、港股、美股
- 行业分类管理

### 2. 事件管理
- 记录公司重要事件（财报、分红、重组等）
- 事件日历视图
- 事件标注在K线图上

### 3. 行情数据
- K线数据查询
- 实时行情获取
- 历史数据同步

### 4. 交易记录
- 买入/卖出记录
- 持仓盈亏计算
- 交易历史查询

## 快速开始

### 1. 安装依赖

```bash
npm install
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并配置数据库连接：

```env
DATABASE_URL="postgresql://user:password@localhost:5432/invest_community"
```

### 3. 初始化数据库

```bash
# 生成Prisma客户端
npm run prisma:generate

# 运行数据库迁移
npm run prisma:migrate

# 或使用db push（开发环境）
npm run prisma:push
```

### 4. 启动开发服务器

```bash
npm run dev
```

访问 [http://localhost:3000](http://localhost:3000)

## API文档

### 公司管理

- `GET /api/companies` - 获取所有公司
- `POST /api/companies` - 创建公司
- `GET /api/companies/[id]` - 获取公司详情
- `PUT /api/companies/[id]` - 更新公司
- `DELETE /api/companies/[id]` - 删除公司

### 事件管理

- `GET /api/events` - 获取事件列表
- `POST /api/events` - 创建事件
- `PUT /api/events/[id]` - 更新事件
- `DELETE /api/events/[id]` - 删除事件

### 行情数据

- `GET /api/market/kline?symbol=...&interval=...` - 获取K线数据
- `GET /api/market/quote?symbol=...` - 获取实时行情
- `GET /api/market/quote?symbols=...` - 批量获取行情

### 交易记录

- `GET /api/trades` - 获取交易记录
- `POST /api/trades` - 创建交易记录
- `PUT /api/trades/[id]` - 更新交易记录
- `DELETE /api/trades/[id]` - 删除交易记录

## 数据库模型

- **Company**: 公司/标的信息
- **Event**: 事件记录
- **Trade**: 交易记录
- **Kline**: K线数据

## 开发计划

详见 [技术文档](./技术文档)

## 部署

### Vercel部署（推荐）

```bash
npm run build
vercel deploy
```

### Docker部署

```bash
# TODO: 添加Dockerfile
```

## 许可证

ISC
