# Node.js 升级替代方案

## 当前状态

- 当前Node版本：v18.17.1
- 目标版本：v20.x（Next.js 16和Prisma 7推荐）
- 问题：网络问题导致Homebrew安装缓慢

## 方案对比

### 方案1：使用Node 18继续开发（临时方案）✅

虽然有引擎警告，但可以暂时继续开发：

```bash
# 安装兼容版本的Prisma
npm install prisma@5.22.0 @prisma/client@5.22.0

# 或者忽略引擎检查
npm install --force prisma @prisma/client
```

**优点**：
- 立即可用
- 基本功能都能运行
- 适合开发和测试

**缺点**：
- 会有警告信息
- 某些新特性可能不支持
- 生产环境不推荐

### 方案2：手动下载安装Node 20

如果Homebrew持续失败：

```bash
# 访问Node.js官网下载
# https://nodejs.org/en/download/
# 下载 macOS Installer (.pkg)

# 或者使用curl下载
curl -O https://nodejs.org/dist/v20.18.1/node-v20.18.1.pkg
sudo installer -pkg node-v20.18.1.pkg -target /
```

### 方案3：使用n版本管理器

```bash
# 安装n
npm install -g n

# 安装Node 20
sudo n 20

# 验证
node --version
```

### 方案4：使用fnm（Fast Node Manager）

```bash
# 安装fnm
curl -fsSL https://fnm.vercel.app/install | bash

# 重新加载shell
source ~/.zshrc

# 安装Node 20
fnm install 20
fnm use 20

# 设为默认
fnm default 20
```

### 方案5：等待Homebrew完成（当前进行中）

Homebrew正在后台安装，虽然慢但可能会成功。

## 推荐操作步骤

### 立即行动（推荐）

使用Node 18 + 兼容版本的Prisma先开始开发：

```bash
# 1. 安装Prisma 5.x（兼容Node 18）
npm install prisma@5.22.0 @prisma/client@5.22.0

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 配置数据库

# 3. 生成Prisma客户端
npx prisma generate

# 4. 推送数据库schema
npx prisma db push

# 5. 启动开发服务器
npm run dev
```

### 后续升级

等网络条件好转或使用其他方案升级Node后：

```bash
# 卸载旧版Prisma
npm uninstall prisma @prisma/client

# 安装最新版
npm install prisma@latest @prisma/client@latest

# 重新生成客户端
npm run prisma:generate
```

## 检查当前安装状态

```bash
# 检查Node版本
node --version

# 检查npm版本
npm --version

# 检查Homebrew进程
ps aux | grep brew

# 查看Homebrew日志
tail -f /tmp/claude/-Users-andy-code-invest-community/tasks/bc2fa62.output
```

## 数据库配置建议

### 使用Docker PostgreSQL（推荐）

```bash
# 启动PostgreSQL容器
docker run -d \
  --name invest-postgres \
  -e POSTGRES_USER=invest \
  -e POSTGRES_PASSWORD=invest123 \
  -e POSTGRES_DB=invest_community \
  -p 5432:5432 \
  postgres:15-alpine

# 配置.env
echo 'DATABASE_URL="postgresql://invest:invest123@localhost:5432/invest_community"' > .env
```

### 使用Supabase（云数据库）

1. 访问 https://supabase.com
2. 创建新项目
3. 获取连接字符串
4. 配置到 .env 文件

```env
DATABASE_URL="postgresql://postgres:[YOUR-PASSWORD]@db.xxx.supabase.co:5432/postgres"
```

## 当前项目可以做的事情

即使Node还在升级，你现在就可以：

### 1. 配置数据库
准备PostgreSQL数据库（Docker或Supabase）

### 2. 阅读文档
- [README.md](README.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [TODO.md](TODO.md)
- [SERVICE_IMPLEMENTATION.md](SERVICE_IMPLEMENTATION.md)
- [QUICKSTART.md](QUICKSTART.md)

### 3. 规划UI设计
思考页面布局、交互流程

### 4. 选择行情数据源
- QOS：https://qos.findata.top/
- iTick：https://www.itick.cn/
- AKShare：https://akshare.akfamily.xyz/

### 5. 准备测试数据
准备一些公司代码、事件数据用于测试

## 验证安装

等Node升级完成后：

```bash
# 验证版本
node --version  # 应该显示 v20.x.x
npm --version   # 应该显示 10.x.x

# 安装依赖
npm install

# 生成Prisma客户端
npm run prisma:generate

# 启动开发服务器
npm run dev

# 访问
open http://localhost:3000
```

## 故障排查

### 问题1：npm install 失败

```bash
# 清理缓存
npm cache clean --force
rm -rf node_modules package-lock.json
npm install
```

### 问题2：Prisma生成失败

```bash
# 检查数据库连接
npx prisma db pull

# 重新生成
npx prisma generate --schema=./prisma/schema.prisma
```

### 问题3：端口占用

```bash
# 查找占用3000端口的进程
lsof -ti:3000

# 杀死进程
kill -9 $(lsof -ti:3000)
```

## 下一步行动

1. **优先级1**：选择一个Node升级方案并执行
2. **优先级2**：安装Prisma并配置数据库
3. **优先级3**：测试API端点
4. **优先级4**：实现Service层逻辑
5. **优先级5**：开发前端页面

## 联系和支持

遇到问题可以：
- 查看错误日志
- 检查环境变量配置
- 参考官方文档
- 查看项目文档目录
