FROM python:3.12-slim

WORKDIR /app

# 设置环境变量，不生成 pyc 文件且 stdout 不缓冲
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 更新和安装基础组件
# CZSC 使用 PyPI pinned wheel；保留 build-essential 处理少数源码包。
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY server /app/server

# 容器内需要数据目录
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 8001

# 启动命令
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8001"]
