# FPL-GPT

Fantasy Premier League数据分析和API服务

## 项目结构

- `fpl_data_loader`: 从FPL API获取数据并存储到SQLite数据库
- `mcp_server`: 提供API服务，用于查询FPL数据
- `analysis`: 数据分析脚本

## 如何使用Docker部署

### 准备工作

1. 创建`.env`文件，包含以下内容：

```
FPL_EMAIL=your_email@example.com
FPL_PASSWORD=your_password
```

### 启动服务

```bash
docker-compose up -d
```

这将启动两个服务：
- `fpl-data-loader`: 每天自动更新FPL数据
- `mcp-server`: 提供API服务，可通过http://localhost:8000访问

### SQLite数据库共享

本项目使用Docker命名卷来共享SQLite数据库，确保两个容器可以安全地访问同一个数据库文件：

- 数据库文件存储在命名卷`fpl-database`中
- 两个容器都将此卷挂载到`/app/data`目录
- 使用环境变量`DB_PATH`指定数据库路径

这种方式解决了以下问题：
1. 路径一致性：两个容器使用相同的数据库路径
2. 数据持久化：即使容器被删除，数据库仍然保留在命名卷中
3. 并发访问：fpl-data-loader主要是写入操作，mcp-server主要是读取操作

### 手动更新数据

如果需要手动更新数据，可以执行：

```bash
docker exec fpl-gpt_fpl-data-loader_1 python /app/main.py
```
