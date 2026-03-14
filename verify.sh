#!/bin/bash
# ============================================================
# 离线验证脚本 - 使用 Mock GitLab 验证所有功能
# ============================================================

set -e

MOCK_URL="http://mock-gitlab:8080"
MOCK_TOKEN="glpat-mock-token-for-testing"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'
PASS=0
FAIL=0

run_test() {
    local desc="$1"
    shift
    echo -e "\n${YELLOW}▶ 测试: ${desc}${NC}"
    echo "  命令: docker compose run --rm gitlab-stats $*"
    if output=$(docker compose run --rm gitlab-stats "$@" 2>&1); then
        echo "$output"
        echo -e "${GREEN}  ✓ 通过${NC}"
        PASS=$((PASS + 1))
        return 0
    else
        echo "$output"
        echo -e "${RED}  ✗ 失败${NC}"
        FAIL=$((FAIL + 1))
        return 1
    fi
}

run_test_expect_fail() {
    local desc="$1"
    shift
    echo -e "\n${YELLOW}▶ 测试: ${desc}${NC}"
    echo "  命令: docker compose run --rm $*"
    if output=$(docker compose run --rm "$@" 2>&1); then
        echo "$output"
        echo -e "${RED}  ✗ 应该失败但成功了${NC}"
        FAIL=$((FAIL + 1))
        return 1
    else
        echo "$output"
        echo -e "${GREEN}  ✓ 通过 (预期失败)${NC}"
        PASS=$((PASS + 1))
        return 0
    fi
}

echo "============================================================"
echo " GitLab 代码统计工具 - 离线验证"
echo "============================================================"

# 构建
echo -e "\n${YELLOW}▶ 构建镜像...${NC}"
docker compose build --quiet

# 启动 mock 服务
echo -e "\n${YELLOW}▶ 启动 Mock GitLab 服务...${NC}"
docker compose up -d mock-gitlab
echo "  等待服务就绪..."
sleep 3

echo ""
echo "============================================================"
echo " 1. 基础功能测试"
echo "============================================================"

run_test "帮助信息" \
    --help

run_test "全量统计（所有项目）" \
    --url "$MOCK_URL" --token "$MOCK_TOKEN"

run_test "详细日志模式" \
    --url "$MOCK_URL" --token "$MOCK_TOKEN" --verbose

echo ""
echo "============================================================"
echo " 2. CSV 导出测试"
echo "============================================================"

run_test "导出 CSV" \
    --url "$MOCK_URL" --token "$MOCK_TOKEN" -o /app/output/stats.csv

echo -e "\n${YELLOW}▶ 验证 CSV 文件内容:${NC}"
if [ -f "./output/stats.csv" ]; then
    cat ./output/stats.csv
    echo -e "${GREEN}  ✓ CSV 文件已生成${NC}"
    PASS=$((PASS + 1))
else
    echo -e "${RED}  ✗ CSV 文件未找到${NC}"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "============================================================"
echo " 3. 日期范围测试"
echo "============================================================"

run_test "指定日期范围" \
    --url "$MOCK_URL" --token "$MOCK_TOKEN" --since 2025-03-05 --until 2025-03-12

run_test_expect_fail "日期范围校验 (since > until)" \
    gitlab-stats --url "$MOCK_URL" --token "$MOCK_TOKEN" --since 2025-12-31 --until 2025-01-01

echo ""
echo "============================================================"
echo " 4. 项目过滤测试"
echo "============================================================"

run_test "按 namespace 过滤 (backend)" \
    --url "$MOCK_URL" --token "$MOCK_TOKEN" --namespace "backend"

run_test "按项目名称过滤 (app)" \
    --url "$MOCK_URL" --token "$MOCK_TOKEN" --project-pattern "app"

run_test "组合过滤 (backend + gateway)" \
    --url "$MOCK_URL" --token "$MOCK_TOKEN" --namespace "backend" --project-pattern "gateway"

echo ""
echo "============================================================"
echo " 5. 增量模式测试"
echo "============================================================"

run_test "增量模式 - 首次运行（全量）" \
    --url "$MOCK_URL" --token "$MOCK_TOKEN" --incremental --cache-dir /app/output

run_test "增量模式 - 第二次运行（应无新提交）" \
    --url "$MOCK_URL" --token "$MOCK_TOKEN" --incremental --cache-dir /app/output

run_test "清除缓存后重新统计" \
    --url "$MOCK_URL" --token "$MOCK_TOKEN" --incremental --clear-cache --cache-dir /app/output

echo ""
echo "============================================================"
echo " 6. 错误处理测试"
echo "============================================================"

run_test_expect_fail "无效 Token" \
    gitlab-stats --url "$MOCK_URL" --token "invalid-token"

run_test_expect_fail "缺少 URL（清空环境变量）" \
    -e GITLAB_URL="" gitlab-stats --token "$MOCK_TOKEN"

echo ""
echo "============================================================"
echo " 7. 单元测试"
echo "============================================================"

echo -e "\n${YELLOW}▶ 运行 pytest...${NC}"
if docker run --rm -v "$(pwd)/backend:/app" -w /app python:3.11-slim \
    sh -c "pip install -q pytest requests urllib3 && python -m pytest tests/ -v --tb=short" 2>&1; then
    echo -e "${GREEN}  ✓ 单元测试通过${NC}"
    PASS=$((PASS + 1))
else
    echo -e "${RED}  ✗ 单元测试失败${NC}"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "============================================================"
echo " 验证结果汇总"
echo "============================================================"
echo -e " ${GREEN}通过: ${PASS}${NC}"
echo -e " ${RED}失败: ${FAIL}${NC}"
echo "============================================================"

# 清理
echo -e "\n${YELLOW}▶ 清理...${NC}"
docker compose down

if [ "$FAIL" -gt 0 ]; then
    echo -e "\n${RED}验证未全部通过！${NC}"
    exit 1
else
    echo -e "\n${GREEN}所有验证通过！${NC}"
    exit 0
fi
