FROM python:3.12-slim

WORKDIR /app

# 创建数据目录
RUN mkdir -p /app/data

# 复制项目文件
COPY ./mcp_server/requirements.txt .
COPY ./mcp_server/main.py .
COPY ./mcp_server/__init__.py .
COPY ./fpl ./fpl

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple --trusted-host pypi.tuna.tsinghua.edu.cn
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
RUN pip config set global.trusted-host pypi.aliyun.com
RUN pip install mcp
RUN pip install ./fpl
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
export DB_PATH="sqlite:///\${DB_PATH:-/app/data/fpl.db}"

# 启动MCP服务器
exec python main.py
EOF

RUN chmod +x /app/entrypoint.sh

# 启动容器
ENTRYPOINT ["/app/entrypoint.sh"]
