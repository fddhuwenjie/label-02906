# 使用官方 Python 多架构基础镜像 (支持 ARM64 和 AMD64)
FROM --platform=$BUILDPLATFORM python:3.11-slim AS builder

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装依赖到指定目录
RUN pip install --no-cache-dir --target=/app/deps -r requirements.txt

# 运行阶段 - 使用多架构镜像
FROM python:3.11-slim

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/deps

# 创建非 root 用户
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# 从 builder 阶段复制依赖
COPY --from=builder /app/deps /app/deps

# 复制应用代码
COPY gitlab_stats.py .

# 切换到非 root 用户
USER appuser

# 默认入口
ENTRYPOINT ["python", "gitlab_stats.py"]
