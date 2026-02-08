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

# 使用配置文件
python gitlab_stats.py --config ~/.gitlab-stats.json

# 增量统计（只获取新提交，适合大型实例）
python gitlab_stats.py --url https://gitlab.example.com --token glpat-xxx --incremental
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

帮我直接用gitlab的api 写个python脚本 用于统计所有项目 的提交人员的代码量，要过滤merge 等非代码提交的结点

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
| `--url` | 是* | GitLab 服务器地址 |
| `--token` | 是* | GitLab Private Token (需要 read_api 权限) |
| `--config, -c` | 否 | 配置文件路径 |
| `--all` | 否 | 统计所有可见项目，而非仅用户所属项目 |
| `--branch` | 否 | 指定分支，默认统计默认分支 |
| `--since` | 否 | 开始日期 (YYYY-MM-DD) |
| `--until` | 否 | 结束日期 (YYYY-MM-DD) |
| `-o, --output` | 否 | 导出 CSV 文件路径 |
| `--timeout` | 否 | 请求超时时间，默认 30 秒 |
| `--incremental, -i` | 否 | 增量统计模式，只获取上次之后的新提交 |
| `--clear-cache` | 否 | 清除缓存后重新全量统计 |
| `--cache-dir` | 否 | 缓存文件目录，默认当前目录 |
| `-v, --verbose` | 否 | 显示详细日志 |

*可通过配置文件或环境变量 (GITLAB_URL, GITLAB_TOKEN) 提供

## 配置文件

支持 JSON 格式的配置文件，避免每次输入重复参数：

```json
{
  "url": "https://gitlab.example.com",
  "token": "glpat-xxxxxxxxxxxxxxxxxxxx",
  "all_projects": false,
  "timeout": 30
}
```

配置文件查找顺序：
1. `--config` 指定的路径
2. 当前目录 `.gitlab-stats.json`
3. 用户目录 `~/.gitlab-stats.json`

优先级：命令行参数 > 配置文件 > 环境变量

## 增量统计

对于大型 GitLab 实例，每次全量拉取效率较低。使用 `--incremental` 参数启用增量模式：

```bash
# 首次运行（全量）
python gitlab_stats.py --url https://gitlab.example.com --token glpat-xxx --incremental

# 后续运行（只获取新提交）
python gitlab_stats.py --url https://gitlab.example.com --token glpat-xxx --incremental

# 清除缓存重新全量统计
python gitlab_stats.py --url https://gitlab.example.com --token glpat-xxx --incremental --clear-cache
```

增量模式会在当前目录生成 `.gitlab-stats-cache.json` 缓存文件，记录每个项目最后处理的 commit SHA。

## 输出示例

终端输出：
```
2025-02-08 10:30:15 - INFO - 正在获取项目列表...
2025-02-08 10:30:16 - INFO - 模式: 获取用户所属项目 (membership=true)
2025-02-08 10:30:17 - INFO - 共获取到 8 个项目
2025-02-08 10:30:17 - INFO - [1/8] 正在统计项目: frontend-app
2025-02-08 10:30:18 - INFO -   有效提交: 89, 过滤 merge: 12
2025-02-08 10:30:18 - INFO - [2/8] 正在统计项目: backend-api
2025-02-08 10:30:19 - INFO -   有效提交: 156, 过滤 merge: 23
...

====================================================================================================
作者                 邮箱                                 新增       删除       净增     提交   项目数
====================================================================================================
张三                 zhangsan@example.com               12580      3420      9160      156        8
李四                 lisi@example.com                    8920      2100      6820       98        5
王五                 wangwu@example.com                  5430      1280      4150       67        3
====================================================================================================
合计                                                    26930      6800     20130      321
====================================================================================================
```

CSV 输出 (stats.csv)：
```csv
作者,邮箱,新增行数,删除行数,净增行数,提交次数,参与项目数,参与项目
张三,zhangsan@example.com,12580,3420,9160,156,8,backend-api; frontend-app; ...
李四,lisi@example.com,8920,2100,6820,98,5,backend-api; common-lib; ...
王五,wangwu@example.com,5430,1280,4150,67,3,frontend-app; mobile-app; ...
```

## 权限说明

| 场景 | 所需权限 | 参数 |
|------|----------|------|
| 统计用户所属项目 | read_api | 默认 |
| 统计所有可见项目 | read_api + 项目可见权限 | --all |
| 统计全部项目（含私有） | admin 权限 | --all |
