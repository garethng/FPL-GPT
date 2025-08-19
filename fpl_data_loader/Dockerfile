FROM python:3.9-slim

WORKDIR /app

# 安装cron
RUN sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list && apt-get update && apt-get -y install --no-install-recommends cron && rm -rf /var/lib/apt/lists/*

# 创建数据目录
RUN mkdir -p /app/data

# 复制需要的文件
COPY ./fpl_data_loader/requirements.txt .
COPY ./fpl_data_loader/main.py .
COPY ./fpl ./fpl

# 安装依赖
RUN pip config set global.index-url http://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install ./fpl

# 创建.env文件的挂载点
RUN touch .env

# 设置cron任务 - 每天凌晨3点执行
RUN echo "0 3 * * * cd /app && DB_PATH=\${DB_PATH:-/app/data/fpl.db} python main.py >> /var/log/cron.log 2>&1" > /etc/cron.d/fpl-cron
RUN chmod 0644 /etc/cron.d/fpl-cron
RUN crontab /etc/cron.d/fpl-cron

# 创建日志文件
RUN touch /var/log/cron.log

# 启动脚本
COPY <<EOF /app/entrypoint.sh
#!/bin/sh
# 确保数据目录存在
mkdir -p /app/data

# 启动时先执行一次数据更新
DB_PATH=\${DB_PATH:-/app/data/fpl.db} python /app/main.py

# 启动cron服务并保持容器运行
cron

# 输出日志到标准输出以便查看
tail -f /var/log/cron.log
EOF

RUN chmod +x /app/entrypoint.sh

# 启动容器
ENTRYPOINT ["/app/entrypoint.sh"]
