# 快速启动指南

## 当前项目状态

✅ **已完成**
- Next.js 15 项目初始化
- TypeScript配置
- Tailwind CSS配置
- Service层架构（5个核心服务）
- API Routes（完整RESTful API）
- Prisma Schema定义
- 项目文档

⏳ **进行中**
- Node.js升级到v20（Homebrew正在安装）

❌ **待完成**
- Prisma客户端安装
- 数据库连接配置
- Service层实现
- 前端页面开发

## 当前等待的任务

### Node.js升级正在进行
Homebrew正在安装Node 20，可以运行以下命令检查进度：

```bash
# 检查brew进程
ps aux | grep brew

# 查看安装输出
tail -f /tmp/claude/-Users-andy-code-invest-community/tasks/b928529.output
```

## 立即可以做的事情

### 1. 查看项目文档
```bash
# 阅读README
cat README.md

# 阅读架构文档
cat ARCHITECTURE.md

# 阅读任务清单
cat TODO.md
```

### 2. 准备数据库环境

#### 选项A：使用Docker（推荐）
```bash
# 安装Docker后运行PostgreSQL
docker run -d \
  --name invest-postgres \
  -e POSTGRES_USER=invest \
  -e POSTGRES_PASSWORD=invest123 \
  -e POSTGRES_DB=invest_community \
  -p 5432:5432 \
  postgres:15-alpine

# 更新.env文件
echo 'DATABASE_URL="postgresql://invest:invest123@localhost:5432/invest_community"' > .env
```

#### 选项B：使用云数据库（Supabase）
1. 访问 https://supabase.com
2. 创建新项目
3. 获取数据库连接字符串
4. 配置到.env文件

### 3. 编辑器配置

推荐VS Code扩展：
- Prisma
- ES7+ React/Redux/React-Native snippets
- Tailwind CSS IntelliSense
- ESLint
- Prettier

## Node升级完成后的步骤

### 1. 配置Node 20
```bash
# 添加到PATH
echo 'export PATH="/opt/homebrew/opt/node@20/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# 验证版本
node --version
npm --version
```

### 2. 安装Prisma
```bash
npm install prisma @prisma/client
```

### 3. 配置数据库
```bash
# 复制环境变量模板
cp .env.example .env

# 编辑.env文件，配置数据库连接
nano .env
```

### 4. 初始化数据库
```bash
# 生成Prisma客户端
npm run prisma:generate

# 推送schema到数据库
npm run prisma:push

# 或者创建migration
npm run prisma:migrate
```

### 5. 启动开发服务器
```bash
npm run dev
```

访问 http://localhost:3000

### 6. 测试API

创建测试数据：
```bash
# 创建公司
curl -X POST http://localhost:3000/api/companies \
  -H "Content-Type: application/json" \
  -d '{
    "code": "600000",
    "name": "浦发银行",
    "market": "A股",
    "industry": "银行"
  }'

# 获取公司列表
curl http://localhost:3000/api/companies
```

## 项目结构速览

```
invest_community/
├── app/                      # Next.js应用
│   ├── api/                 # API路由
│   │   ├── companies/      # 公司API
│   │   ├── events/         # 事件API
│   │   ├── market/         # 行情API
│   │   └── trades/         # 交易API
│   ├── layout.tsx          # 根布局
│   └── page.tsx            # 首页
├── service/                 # 服务层
│   ├── company.service.ts  # 公司服务
│   ├── event.service.ts    # 事件服务
│   ├── market.service.ts   # 行情服务
│   └── trade.service.ts    # 交易服务
├── lib/                     # 工具库
│   ├── prisma.ts           # Prisma客户端
│   ├── types.ts            # 类型定义
│   └── api-types.ts        # API类型
├── prisma/
│   └── schema.prisma       # 数据库Schema
└── components/             # React组件（待开发）
```

## API端点清单

### 公司管理
- `GET /api/companies` - 获取所有公司
- `POST /api/companies` - 创建公司
- `GET /api/companies/[id]` - 获取公司详情
- `PUT /api/companies/[id]` - 更新公司
- `DELETE /api/companies/[id]` - 删除公司

### 事件管理
- `GET /api/events` - 获取事件列表
- `GET /api/events?companyId=xxx` - 获取公司事件
- `POST /api/events` - 创建事件
- `PUT /api/events/[id]` - 更新事件
- `DELETE /api/events/[id]` - 删除事件

### 行情数据
- `GET /api/market/kline?symbol=xxx&interval=1d` - K线数据
- `GET /api/market/quote?symbol=xxx` - 实时行情
- `GET /api/market/quote?symbols=xxx,yyy` - 批量行情

### 交易��录
- `GET /api/trades` - 获取交易记录
- `GET /api/trades?companyId=xxx` - 获取公司交易
- `POST /api/trades` - 创建交易
- `PUT /api/trades/[id]` - 更新交易
- `DELETE /api/trades/[id]` - 删除交易

## 常见问题

### Q: Node版本不兼容怎么办？
A: 当前正在通过Homebrew安装Node 20。等待安装完成后配置PATH。

### Q: 数据库如何配置？
A: 推荐使用Docker本地运行PostgreSQL，或使用Supabase云数据库。

### Q: 如何查看数据库？
A: 运行 `npm run prisma:studio` 启动Prisma Studio可视化界面。

### Q: API返回404？
A: 确保已启动开发服务器 `npm run dev`，并检查URL路径是否正确。

### Q: Service方法返回空数组？
A: 当前Service层是框架代码，需要实现Prisma查询逻辑。参考TODO.md。

## 下一步计划

1. ✅ 等待Node升级完成
2. ⬜ 安装Prisma依赖
3. ⬜ 配置数据库连接
4. ⬜ 实现Service层逻辑
5. ⬜ 开发前端页面
6. ⬜ 集成K线图组件

详细任务请参考 [TODO.md](./TODO.md)

## 获取帮助

- 📖 项目架构：查看 [ARCHITECTURE.md](./ARCHITECTURE.md)
- 📋 技术选型：查看 [技术文档](./技术文档)
- ✅ 任务清单：查看 [TODO.md](./TODO.md)
- 🔧 API文档：启动后访问各个endpoint测试

## 贡献

欢迎提交Issue和Pull Request！
