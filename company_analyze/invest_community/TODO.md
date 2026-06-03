# 下一步任务清单

## 当前状态
✅ Next.js 15项目基础架构已搭建完成
✅ Service层架构已创建（5个核心服务）
✅ API Routes已创建（完整的RESTful API）
✅ 数据库Schema已定义（Prisma）
✅ 类型定义已完成
⏳ Node.js正在升级到v20（通过Homebrew）

## 立即需要完成的任务

### 1. 完成Node.js升级
等待Homebrew安装完成，然后：
```bash
# 检查安装状态
brew list node@20

# 配置PATH
echo 'export PATH="/opt/homebrew/opt/node@20/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# 验证版本
node --version  # 应该显示 v20.x.x
```

### 2. 安装Prisma并初始化数据库
```bash
# 安装Prisma（Node 20安装后）
npm install prisma @prisma/client

# 生成Prisma客户端
npm run prisma:generate

# 配置数据库连接
# 复制 .env.example 为 .env
cp .env.example .env
# 编辑 .env 文件，配置真实的PostgreSQL连接字符串

# 创建数据库表
npm run prisma:migrate
# 或者使用 db push（开发环境快速同步）
npm run prisma:push
```

### 3. 更新Service层实现
当前Service层方法都是TODO占位，需要实现实际的数据库操作：

#### service/company.service.ts
```typescript
import { prisma } from '@/lib/prisma';

async getAllCompanies() {
  try {
    return await prisma.company.findMany({
      orderBy: { createdAt: 'desc' }
    });
  } catch (error) {
    this.handleError(error, '获取公司列表失败');
  }
}

async getCompanyById(id: string) {
  try {
    return await prisma.company.findUnique({
      where: { id },
      include: {
        events: true,
        trades: true
      }
    });
  } catch (error) {
    this.handleError(error, '获取公司详情失败');
  }
}

// 类似地实现其他方法...
```

#### service/event.service.ts
实现事件的CRUD操作和查询

#### service/trade.service.ts
实现交易记录管理和盈亏计算

#### service/market.service.ts
对接行情数据API（QOS/iTick等）

### 4. 测试API端点
```bash
# 启动开发服务器
npm run dev

# 测试API（使用curl或Postman）
# 创建公司
curl -X POST http://localhost:3000/api/companies \
  -H "Content-Type: application/json" \
  -d '{"code":"600000","name":"浦发银行","market":"A股"}'

# 获取公司列表
curl http://localhost:3000/api/companies

# 获取公司详情
curl http://localhost:3000/api/companies/{id}
```

## 短期开发任务（1-2周）

### 前端页面开发
- [ ] 公司列表页面
- [ ] 公司详情页面
- [ ] 事件管理页面
- [ ] 交易记录页面
- [ ] 日历视图组件

### 功能开发
- [ ] 表单验证（使用zod）
- [ ] 错误提示组件
- [ ] Loading状态
- [ ] 分页功能
- [ ] 搜索功能
- [ ] 筛选功能

### UI组件集成
```bash
# 安装shadcn/ui
npx shadcn-ui@latest init

# 添加常用组件
npx shadcn-ui@latest add button
npx shadcn-ui@latest add input
npx shadcn-ui@latest add table
npx shadcn-ui@latest add dialog
npx shadcn-ui@latest add calendar
```

## 中期开发任务（2-4周）

### K线图集成
```bash
# 安装Lightweight Charts
npm install lightweight-charts

# 创建K线图组件
# components/KLineChart.tsx
```

### 行情数据对接
- [ ] 选择行情数据API（QOS/iTick/AKShare）
- [ ] 实现API适配器
- [ ] 数据同步定时任务
- [ ] K线数据存储优化

### 日历组件
```bash
# 安装日历库
npm install react-big-calendar date-fns

# 创建日历视图
# app/calendar/page.tsx
```

### 状态管理
```bash
# 安装Zustand
npm install zustand

# 创建stores
# lib/stores/
```

## 长期开发任务（1-2个月）

### 用户系统
```bash
# 安装NextAuth
npm install next-auth @auth/prisma-adapter

# 配置认证
# app/api/auth/[...nextauth]/route.ts
```

### 数据分析功能
- [ ] 持仓盈亏统计
- [ ] 交易分析报告
- [ ] 收益率曲线
- [ ] 回测功能

### 实时功能
- [ ] WebSocket实时行情推送
- [ ] 实时事件通知
- [ ] 协作功能（多用户）

### 数据导入导出
- [ ] Excel导入
- [ ] CSV导出
- [ ] PDF报表生成

### 性能优化
- [ ] Redis缓存集成
- [ ] 数据库查询优化
- [ ] 图片CDN
- [ ] 代码分割

### 移动端
- [ ] 响应式设计优化
- [ ] PWA支持
- [ ] 触摸手势

## 部署准备

### 环境配置
- [ ] 生产环境数据库（Supabase/Neon）
- [ ] Redis缓存（Upstash）
- [ ] 行情API配置
- [ ] 环境变量配置

### 部署平台
- [ ] Vercel部署配置
- [ ] 域名配置
- [ ] SSL证书
- [ ] CI/CD流程

## 技术债务
- [ ] 添加单元测试
- [ ] 添加E2E测试
- [ ] 代码规范检查（ESLint）
- [ ] 类型安全检查（TypeScript strict mode）
- [ ] API文档生成（Swagger）

## 文档完善
- [ ] API使用文档
- [ ] 组件文档
- [ ] 部署文档
- [ ] 贡献指南

## 注意事项

1. **数据库选择**
   - 开发环境可以用Docker运行PostgreSQL
   - 生产环境推荐Supabase（免费额度）或Neon

2. **行情API选择**
   - 免费测试：AKShare（Python库，需要额外服务）
   - 推荐：QOS或iTick（有免费额度，支持WebSocket）

3. **性能考虑**
   - K线数据量大，考虑使用TimescaleDB扩展
   - 实时行情使用Redis缓存
   - 静态资源使用CDN

4. **成本控制**
   - Vercel免费额度足够开发测试
   - Supabase免费版可支持小规模使用
   - 行情API注意免费额度限制
