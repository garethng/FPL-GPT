FROM python:3.9-slim

WORKDIR /app

# 创建数据目录
RUN mkdir -p /app/data

# 复制项目文件
COPY requirements.txt .
COPY main.py .
COPY __init__.py .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir mcp-server fpl

# 创建.env文件的挂载点
RUN touch .env

# 暴露端口
EXPOSE 8000

# 启动脚本
COPY <<EOF /app/entrypoint.sh
#!/bin/sh
# 确保数据目录存在
mkdir -p /app/data

# 修改数据库路径环境变量
export DATABASE_URL="sqlite:///\${DB_PATH:-/app/data/fpl.db}"

# 启动MCP服务器
exec python main.py
EOF

RUN chmod +x /app/entrypoint.sh

# 启动容器
ENTRYPOINT ["/app/entrypoint.sh"]
