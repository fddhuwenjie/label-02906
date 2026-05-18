"""
离线 Mock GitLab API 服务
模拟一个家庭网络团队的 GitLab 实例，包含多个项目和提交记录
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ============================================================
# Mock 数据：模拟一个小团队的 GitLab 仓库
# ============================================================

PROJECTS = [
    {
        "id": 1,
        "name": "home-gateway",
        "path": "home-gateway",
        "namespace": {"id": 1, "name": "smart-home", "full_path": "smart-home/backend"},
        "default_branch": "main",
        "web_url": "http://mock-gitlab/smart-home/backend/home-gateway",
    },
    {
        "id": 2,
        "name": "mobile-app",
        "path": "mobile-app",
        "namespace": {"id": 2, "name": "smart-home", "full_path": "smart-home/frontend"},
        "default_branch": "main",
        "web_url": "http://mock-gitlab/smart-home/frontend/mobile-app",
    },
    {
        "id": 3,
        "name": "device-firmware",
        "path": "device-firmware",
        "namespace": {"id": 1, "name": "smart-home", "full_path": "smart-home/backend"},
        "default_branch": "main",
        "web_url": "http://mock-gitlab/smart-home/backend/device-firmware",
    },
    {
        "id": 4,
        "name": "docs",
        "path": "docs",
        "namespace": {"id": 3, "name": "smart-home", "full_path": "smart-home/docs"},
        "default_branch": "main",
        "web_url": "http://mock-gitlab/smart-home/docs/docs",
    },
]

# 每个项目的提交记录（按时间倒序）
COMMITS = {
    1: [  # home-gateway
        {
            "id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            "title": "feat: add MQTT broker support",
            "message": "feat: add MQTT broker support\n\nIntegrate mosquitto for IoT device communication",
            "author_name": "张三",
            "author_email": "zhangsan@example.com",
            "created_at": "2025-03-10T10:00:00.000+08:00",
            "parent_ids": ["b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3"],
            "stats": {"additions": 320, "deletions": 45, "total": 365},
        },
        {
            "id": "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3",
            "title": "Merge branch 'feature/zigbee' into 'main'",
            "message": "Merge branch 'feature/zigbee' into 'main'",
            "author_name": "张三",
            "author_email": "zhangsan@example.com",
            "created_at": "2025-03-08T15:30:00.000+08:00",
            "parent_ids": [
                "c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
                "d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5",
            ],
            "stats": {"additions": 500, "deletions": 100, "total": 600},
        },
        {
            "id": "c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
            "title": "fix: resolve DNS resolution timeout",
            "message": "fix: resolve DNS resolution timeout\n\nIncrease timeout to 10s for slow networks",
            "author_name": "李四",
            "author_email": "lisi@example.com",
            "created_at": "2025-03-05T09:20:00.000+08:00",
            "parent_ids": ["d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5"],
            "stats": {"additions": 25, "deletions": 8, "total": 33},
        },
        {
            "id": "d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5",
            "title": "Revert \"feat: experimental wifi6 support\"",
            "message": "Revert \"feat: experimental wifi6 support\"\n\nThis reverts commit e5f6a1b2.",
            "author_name": "张三",
            "author_email": "zhangsan@example.com",
            "created_at": "2025-03-03T14:00:00.000+08:00",
            "parent_ids": ["e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6"],
            "stats": {"additions": 0, "deletions": 150, "total": 150},
        },
        {
            "id": "e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6",
            "title": "feat: add DHCP server module",
            "message": "feat: add DHCP server module\n\nSupport dynamic IP allocation for LAN devices",
            "author_name": "李四",
            "author_email": "lisi@example.com",
            "created_at": "2025-03-01T11:00:00.000+08:00",
            "parent_ids": ["f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1"],
            "stats": {"additions": 210, "deletions": 30, "total": 240},
        },
    ],
    2: [  # mobile-app
        {
            "id": "f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1",
            "title": "feat: device control dashboard",
            "message": "feat: device control dashboard\n\nAdd real-time device status monitoring",
            "author_name": "王五",
            "author_email": "wangwu@example.com",
            "created_at": "2025-03-12T16:00:00.000+08:00",
            "parent_ids": ["a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a100"],
            "stats": {"additions": 450, "deletions": 80, "total": 530},
        },
        {
            "id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a100",
            "title": "fix: bluetooth connection stability",
            "message": "fix: bluetooth connection stability\n\n(cherry picked from commit abc123def456)",
            "author_name": "王五",
            "author_email": "wangwu@example.com",
            "created_at": "2025-03-09T10:30:00.000+08:00",
            "parent_ids": ["b1c2d3e4f5a6b1c2d3e4f5a6b1c2d3e4f5a6b1c2"],
            "stats": {"additions": 35, "deletions": 12, "total": 47},
        },
        {
            "id": "b1c2d3e4f5a6b1c2d3e4f5a6b1c2d3e4f5a6b1c2",
            "title": "feat: add WiFi configuration page",
            "message": "feat: add WiFi configuration page",
            "author_name": "赵六",
            "author_email": "zhaoliu@example.com",
            "created_at": "2025-03-06T13:45:00.000+08:00",
            "parent_ids": ["c1d2e3f4a5b6c1d2e3f4a5b6c1d2e3f4a5b6c1d2"],
            "stats": {"additions": 180, "deletions": 20, "total": 200},
        },
        {
            "id": "c1d2e3f4a5b6c1d2e3f4a5b6c1d2e3f4a5b6c1d2",
            "title": "Squashed commit of the following:",
            "message": "Squashed commit of the following:\n\ncommit aaa\ncommit bbb\ncommit ccc",
            "author_name": "赵六",
            "author_email": "zhaoliu@example.com",
            "created_at": "2025-03-04T09:00:00.000+08:00",
            "parent_ids": ["d1e2f3a4b5c6d1e2f3a4b5c6d1e2f3a4b5c6d1e2"],
            "stats": {"additions": 600, "deletions": 200, "total": 800},
        },
    ],
    3: [  # device-firmware
        {
            "id": "d1e2f3a4b5c6d1e2f3a4b5c6d1e2f3a4b5c6d1e2",
            "title": "feat: OTA update mechanism",
            "message": "feat: OTA update mechanism\n\nSupport over-the-air firmware updates",
            "author_name": "张三",
            "author_email": "zhangsan@example.com",
            "created_at": "2025-03-11T08:00:00.000+08:00",
            "parent_ids": ["e1f2a3b4c5d6e1f2a3b4c5d6e1f2a3b4c5d6e1f2"],
            "stats": {"additions": 520, "deletions": 60, "total": 580},
        },
        {
            "id": "e1f2a3b4c5d6e1f2a3b4c5d6e1f2a3b4c5d6e1f2",
            "title": "fix: memory leak in sensor polling",
            "message": "fix: memory leak in sensor polling",
            "author_name": "李四",
            "author_email": "lisi@example.com",
            "created_at": "2025-03-07T17:00:00.000+08:00",
            "parent_ids": ["f1a2b3c4d5e6f1a2b3c4d5e6f1a2b3c4d5e6f1a2"],
            "stats": {"additions": 15, "deletions": 42, "total": 57},
        },
        {
            "id": "f1a2b3c4d5e6f1a2b3c4d5e6f1a2b3c4d5e6f1a2",
            "title": "refactor: extract HAL abstraction layer",
            "message": "refactor: extract HAL abstraction layer",
            "author_name": "张三",
            "author_email": "zhangsan@example.com",
            "created_at": "2025-03-02T12:00:00.000+08:00",
            "parent_ids": ["0000000000000000000000000000000000000001"],
            "stats": {"additions": 380, "deletions": 290, "total": 670},
        },
    ],
    4: [  # docs
        {
            "id": "aaa1bbb2ccc3ddd4eee5fff6aaa1bbb2ccc3ddd4",
            "title": "docs: update API reference",
            "message": "docs: update API reference",
            "author_name": "赵六",
            "author_email": "zhaoliu@example.com",
            "created_at": "2025-03-13T09:00:00.000+08:00",
            "parent_ids": ["bbb1ccc2ddd3eee4fff5aaa6bbb1ccc2ddd3eee4"],
            "stats": {"additions": 95, "deletions": 30, "total": 125},
        },
        {
            "id": "bbb1ccc2ddd3eee4fff5aaa6bbb1ccc2ddd3eee4",
            "title": "docs: add network topology diagram",
            "message": "docs: add network topology diagram",
            "author_name": "王五",
            "author_email": "wangwu@example.com",
            "created_at": "2025-03-04T11:00:00.000+08:00",
            "parent_ids": ["0000000000000000000000000000000000000002"],
            "stats": {"additions": 60, "deletions": 0, "total": 60},
        },
    ],
}

VALID_TOKEN = "glpat-mock-token-for-testing"


class MockGitLabHandler(BaseHTTPRequestHandler):
    """Mock GitLab API 请求处理器"""

    def log_message(self, format, *args):
        """自定义日志格式"""
        print(f"[MockGitLab] {args[0]}")

    def _check_auth(self) -> bool:
        token = self.headers.get("PRIVATE-TOKEN", "")
        if token != VALID_TOKEN:
            self._send_json(401, {"message": "401 Unauthorized"})
            return False
        return True

    def _send_json(self, status: int, data, total_pages: int = 1):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("x-total-pages", str(total_pages))
        self.send_header("RateLimit-Limit", "600")
        self.send_header("RateLimit-Remaining", "580")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _parse_params(self) -> dict:
        parsed = urlparse(self.path)
        return {k: v[0] for k, v in parse_qs(parsed.query).items()}

    def do_GET(self):
        if not self._check_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path
        params = self._parse_params()

        # GET /api/v4/projects
        if path == "/api/v4/projects":
            self._handle_projects(params)

        # GET /api/v4/groups/:path/projects
        elif path.startswith("/api/v4/groups/") and path.endswith("/projects"):
            group_path = path.replace("/api/v4/groups/", "").replace("/projects", "")
            self._handle_group_projects(group_path, params)

        # GET /api/v4/projects/:id/repository/commits
        elif "/repository/commits" in path:
            parts = path.split("/")
            try:
                project_id = int(parts[4])
            except (IndexError, ValueError):
                self._send_json(404, {"message": "Not Found"})
                return
            self._handle_commits(project_id, params)

        else:
            self._send_json(404, {"message": "Not Found"})

    def _handle_projects(self, params: dict):
        """处理项目列表请求"""
        membership = params.get("membership", "false")
        # membership 模式返回全部（mock 中所有项目都属于用户）
        self._send_json(200, PROJECTS)

    def _handle_group_projects(self, group_path: str, params: dict):
        """处理 group 项目列表请求"""
        from urllib.parse import unquote
        group_path = unquote(group_path)

        filtered = [
            p for p in PROJECTS
            if p["namespace"]["full_path"].startswith(group_path)
        ]

        if not filtered and not any(
            p["namespace"]["full_path"].startswith(group_path) for p in PROJECTS
        ):
            self._send_json(404, {"message": f"Group '{group_path}' not found"})
            return

        self._send_json(200, filtered)

    def _handle_commits(self, project_id: int, params: dict):
        """处理提交记录请求"""
        commits = COMMITS.get(project_id, [])

        if not commits:
            self._send_json(200, [])
            return

        # 时间范围过滤
        since = params.get("since")
        until = params.get("until")

        if since or until:
            from datetime import datetime, timezone, timedelta

            def parse_time(s):
                """解析各种 ISO 格式时间字符串"""
                s = s.replace("Z", "+00:00")
                try:
                    return datetime.fromisoformat(s)
                except ValueError:
                    # 无时区信息时当作 UTC
                    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

            def ensure_tz(dt):
                """确保 datetime 有时区信息"""
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone(timedelta(hours=8)))
                return dt

            filtered = []
            for c in commits:
                ct = ensure_tz(parse_time(c["created_at"]))
                if since:
                    st = ensure_tz(parse_time(since))
                    if ct < st:
                        continue
                if until:
                    ut = ensure_tz(parse_time(until))
                    if ct > ut:
                        continue
                filtered.append(c)
            commits = filtered

        self._send_json(200, commits)


def main():
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = HTTPServer(("0.0.0.0", port), MockGitLabHandler)
    print(f"[MockGitLab] Mock GitLab API 服务启动在 http://0.0.0.0:{port}")
    print(f"[MockGitLab] Token: {VALID_TOKEN}")
    print(f"[MockGitLab] 项目数: {len(PROJECTS)}")
    print(f"[MockGitLab] 总提交数: {sum(len(v) for v in COMMITS.values())}")
    print(f"[MockGitLab]")
    print(f"[MockGitLab] 模拟数据概览:")
    print(f"[MockGitLab]   smart-home/backend/home-gateway  - 5 commits (含 1 merge, 1 revert)")
    print(f"[MockGitLab]   smart-home/frontend/mobile-app   - 4 commits (含 1 cherry-pick, 1 squash)")
    print(f"[MockGitLab]   smart-home/backend/device-firmware - 3 commits (全部有效)")
    print(f"[MockGitLab]   smart-home/docs/docs             - 2 commits (全部有效)")
    print(f"[MockGitLab]")
    print(f"[MockGitLab] 预期过滤结果:")
    print(f"[MockGitLab]   张三: 3 有效提交 (additions=1220, deletions=395)")
    print(f"[MockGitLab]   李四: 3 有效提交 (additions=250, deletions=80)")
    print(f"[MockGitLab]   王五: 2 有效提交 (additions=510, deletions=80)")
    print(f"[MockGitLab]   赵六: 2 有效提交 (additions=275, deletions=50)")
    server.serve_forever()


if __name__ == "__main__":
    main()
