# GitLab 代码统计工具

统计 GitLab 项目提交人员的代码量，自动过滤 merge commits。

## How to Run

### Docker 启动

```bash
# 构建并运行
docker compose up --build -d

# 运行统计（使用环境变量）
docker compose run --rm gitlab-stats --url https://gitlab.example.com --token glpat-xxx

# 或者使用 .env 文件配置
cp .env.example .env
# 编辑 .env 文件设置 GITLAB_URL 和 GITLAB_TOKEN
docker compose run --rm gitlab-stats --url $GITLAB_URL --token $GITLAB_TOKEN

# 导出 CSV 到 output 目录
docker compose run --rm gitlab-stats --url https://gitlab.example.com --token glpat-xxx -o /app/output/stats.csv
```

### 本地启动

```bash
# 进入 backend 目录
cd backend

# 安装依赖
pip install -r requirements.txt

# 运行脚本
python gitlab_stats.py --url https://gitlab.example.com --token glpat-xxx

# 指定时间范围
python gitlab_stats.py --url https://gitlab.example.com --token glpat-xxx --since 2025-01-01 --until 2025-12-31

# 导出 CSV
python gitlab_stats.py --url https://gitlab.example.com --token glpat-xxx -o stats.csv
```

## Services

| 服务 | 说明 | 端口 |
|------|------|------|
| gitlab-stats | GitLab 代码统计 CLI 工具 | N/A (CLI) |

## 测试账号

本工具需要 GitLab Private Token 进行认证：

1. 登录你的 GitLab 实例
2. 进入 Settings → Access Tokens
3. 创建一个具有 `read_api` 权限的 Token
4. 使用该 Token 作为 `--token` 参数

示例 Token 格式：`glpat-xxxxxxxxxxxxxxxxxxxx`

## 题目内容

### 需求描述

开发一个 GitLab 代码统计脚本，用于统计所有项目提交人员的代码量，并过滤 merge commits。

### 功能要求

1. 连接 GitLab API 获取项目列表
2. 遍历项目获取提交记录
3. 统计每个作者的代码新增/删除行数
4. 过滤 merge commits（parent_ids > 1）
5. 支持按时间范围筛选
6. 支持导出 CSV 格式

### 技术要求

- Python 3.11+
- 支持 Docker 部署
- 跨平台镜像（ARM64/AMD64）

---

## 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--url` | 是 | GitLab 服务器地址 |
| `--token` | 是 | GitLab Private Token (需要 read_api 权限) |
| `--all` | 否 | 统计所有可见项目，而非仅用户所属项目 |
| `--branch` | 否 | 指定分支，默认统计默认分支 |
| `--since` | 否 | 开始日期 (YYYY-MM-DD) |
| `--until` | 否 | 结束日期 (YYYY-MM-DD) |
| `-o, --output` | 否 | 导出 CSV 文件路径 |
| `--timeout` | 否 | 请求超时时间，默认 30 秒 |
| `-v, --verbose` | 否 | 显示详细日志 |

## 输出示例

```
====================================================================================================
作者                 邮箱                                 新增       删除       净增     提交   项目数
====================================================================================================
张三                 zhangsan@example.com               12580      3420      9160      156        8
李四                 lisi@example.com                    8920      2100      6820       98        5
====================================================================================================
合计                                                    21500      5520     15980      254
====================================================================================================
```
