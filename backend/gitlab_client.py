"""GitLab API 客户端"""

import logging
import re
import time
from typing import List, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class GitLabAPIError(Exception):
    """GitLab API 错误"""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"[{status_code}] {message}")


class RateLimiter:
    """自适应速率限制器"""
    
    def __init__(self, min_interval: float = 0.1, max_interval: float = 60.0):
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.current_interval = min_interval
        self.last_request_time = 0.0
        self.consecutive_429s = 0
    
    def wait(self) -> None:
        """等待适当时间后再发送请求"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.current_interval:
            time.sleep(self.current_interval - elapsed)
        self.last_request_time = time.time()
    
    def on_success(self, response: requests.Response) -> None:
        """请求成功后调整速率"""
        self.consecutive_429s = 0
        # 根据剩余配额调整
        remaining = response.headers.get('RateLimit-Remaining')
        limit = response.headers.get('RateLimit-Limit')
        if remaining and limit:
            try:
                ratio = int(remaining) / int(limit)
                if ratio > 0.5:
                    # 配额充足，加快速度
                    self.current_interval = max(self.min_interval, self.current_interval * 0.8)
                elif ratio < 0.2:
                    # 配额紧张，减慢速度
                    self.current_interval = min(self.max_interval, self.current_interval * 1.5)
            except (ValueError, ZeroDivisionError):
                pass
    
    def on_rate_limited(self, response: requests.Response) -> float:
        """被限流时计算等待时间"""
        self.consecutive_429s += 1
        
        # 优先使用 Retry-After header
        retry_after = response.headers.get('Retry-After')
        if retry_after:
            try:
                wait_time = float(retry_after)
                logger.warning(f"触发速率限制，等待 {wait_time:.1f} 秒 (Retry-After)")
                return wait_time
            except ValueError:
                pass
        
        # 使用 RateLimit-Reset header
        reset_time = response.headers.get('RateLimit-Reset')
        if reset_time:
            try:
                wait_time = max(0, int(reset_time) - time.time())
                logger.warning(f"触发速率限制，等待 {wait_time:.1f} 秒 (RateLimit-Reset)")
                return wait_time
            except ValueError:
                pass
        
        # 指数退避
        wait_time = min(self.max_interval, (2 ** self.consecutive_429s) * self.min_interval)
        logger.warning(f"触发速率限制，等待 {wait_time:.1f} 秒 (指数退避)")
        self.current_interval = min(self.max_interval, self.current_interval * 2)
        return wait_time


class GitLabClient:
    """GitLab API 客户端"""
    
    def __init__(
        self, 
        gitlab_url: str, 
        private_token: str, 
        timeout: int = 30, 
        all_projects: bool = False,
        group: Optional[str] = None,
        namespace_pattern: Optional[str] = None,
        project_pattern: Optional[str] = None
    ):
        self._validate_url(gitlab_url)
        self._validate_token(private_token)
        
        self.gitlab_url = gitlab_url.rstrip('/')
        self.private_token = private_token
        self.timeout = timeout
        self.all_projects = all_projects
        self.group = group
        self.namespace_pattern = self._compile_pattern(namespace_pattern)
        self.project_pattern = self._compile_pattern(project_pattern)
        self.session = self._create_session()
        self.rate_limiter = RateLimiter()
    
    @staticmethod
    def _compile_pattern(pattern: Optional[str]) -> Optional[re.Pattern]:
        """编译正则表达式模式"""
        if not pattern:
            return None
        try:
            return re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"无效的正则表达式 '{pattern}': {e}")
    
    @staticmethod
    def _validate_url(url: str) -> None:
        if not url:
            raise ValueError("GitLab URL 不能为空")
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"无效的 GitLab URL: {url}")
        if parsed.scheme not in ('http', 'https'):
            raise ValueError(f"URL 必须使用 http 或 https 协议: {url}")
    
    @staticmethod
    def _validate_token(token: str) -> None:
        if not token or not token.strip():
            raise ValueError("Private Token 不能为空")
    
    def _create_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({"PRIVATE-TOKEN": self.private_token})
        return session

    def _handle_error_response(self, response: requests.Response, context: str) -> None:
        """处理错误响应，提供详细错误信息"""
        status = response.status_code
        try:
            error_body = response.json()
            error_msg = error_body.get("message") or error_body.get("error") or str(error_body)
        except Exception:
            error_msg = response.text[:200] if response.text else "无响应内容"
        
        if status == 401:
            raise GitLabAPIError(status, f"认证失败: Token 无效或已过期 ({context})")
        elif status == 403:
            raise GitLabAPIError(status, f"权限不足: Token 缺少必要权限，请确保有 read_api 权限 ({context})")
        elif status == 404:
            raise GitLabAPIError(status, f"资源不存在: {context}")
        else:
            raise GitLabAPIError(status, f"{context} - {error_msg}")
    
    def _request(
        self, 
        endpoint: str, 
        params: Optional[dict] = None, 
        context: str = "API 请求", 
        max_pages: int = 0,
        stop_at_sha: Optional[str] = None
    ) -> List[dict]:
        """
        发送 API 请求，支持完整分页
        
        Args:
            endpoint: API 端点
            params: 请求参数
            context: 上下文描述（用于日志）
            max_pages: 最大页数限制，0 表示无限制（获取全部数据）
            stop_at_sha: 遇到此 commit SHA 时停止（用于增量获取）
        
        Returns:
            所有分页数据的列表
        """
        url = f"{self.gitlab_url}/api/v4{endpoint}"
        results = []
        page = 1
        per_page = 100
        
        while True:
            if max_pages > 0 and page > max_pages:
                logger.info(f"已达到指定的最大页数限制 ({max_pages}): {context}")
                break
            
            request_params = {"page": page, "per_page": per_page}
            if params:
                request_params.update(params)
            
            try:
                self.rate_limiter.wait()
                logger.debug(f"请求: {url}, 页码: {page}")
                response = self.session.get(url, params=request_params, timeout=self.timeout)
                
                # 处理速率限制
                if response.status_code == 429:
                    wait_time = self.rate_limiter.on_rate_limited(response)
                    time.sleep(wait_time)
                    continue  # 重试当前页
                
                self.rate_limiter.on_success(response)
            except requests.exceptions.Timeout:
                raise GitLabAPIError(0, f"请求超时 ({self.timeout}s): {context}")
            except requests.exceptions.ConnectionError as e:
                raise GitLabAPIError(0, f"连接失败: {e}")
            
            if response.status_code != 200:
                self._handle_error_response(response, context)
            
            data = response.json()
            if not data:
                break
            
            # 检查是否需要在特定 SHA 处停止（增量模式）
            # GitLab API 返回的 commits 按时间倒序排列，stop_at_sha 之前的都是新提交
            if stop_at_sha:
                found_stop_sha = False
                for item in data:
                    if item.get("id") == stop_at_sha:
                        logger.debug(f"遇到已处理的 commit {stop_at_sha}，停止获取")
                        found_stop_sha = True
                        break
                    results.append(item)
                if found_stop_sha:
                    # 已找到停止点，不再获取后续页面
                    break
            else:
                results.extend(data)
            
            # 检查是否还有下一页
            total_pages = response.headers.get('x-total-pages')
            if total_pages:
                if page >= int(total_pages):
                    break
            elif len(data) < per_page:
                # 如果没有 total_pages header，通过返回数量判断
                break
            
            page += 1
        
        return results

    def get_projects(self) -> List[dict]:
        """
        获取项目列表
        
        - all_projects=False (默认): 获取当前用户有成员身份的项目
        - all_projects=True: 获取所有可见项目（需要足够权限，管理员可获取全部）
        - group: 指定 group 路径时，只获取该 group 下的项目
        """
        logger.info("正在获取项目列表...")
        
        # 如果指定了 group，使用 group API
        if self.group:
            logger.info(f"模式: 获取 group '{self.group}' 下的项目")
            projects = self._get_group_projects(self.group)
        elif self.all_projects:
            params = {}
            logger.info("模式: 获取所有可见项目")
            projects = self._request("/projects", params, "获取项目列表")
        else:
            params = {"membership": "true"}
            logger.info("模式: 获取用户所属项目 (membership=true)")
            projects = self._request("/projects", params, "获取项目列表")
        
        # 应用过滤器
        projects = self._filter_projects(projects)
        
        if not projects:
            logger.warning("未获取到任何项目，请检查:")
            logger.warning("  1. Token 是否有 read_api 权限")
            logger.warning("  2. 用户是否有项目成员身份")
            if self.all_projects:
                logger.warning("  3. 使用 --all 参数需要足够的权限查看项目")
            if self.group:
                logger.warning(f"  4. group '{self.group}' 是否存在且有访问权限")
        else:
            logger.info(f"共获取到 {len(projects)} 个项目")
        
        return projects
    
    def _get_group_projects(self, group_path: str) -> List[dict]:
        """获取指定 group 下的所有项目（包括子 group）"""
        from urllib.parse import quote
        encoded_path = quote(group_path, safe='')
        
        try:
            return self._request(
                f"/groups/{encoded_path}/projects",
                {"include_subgroups": "true"},
                f"获取 group '{group_path}' 项目列表"
            )
        except GitLabAPIError as e:
            if e.status_code == 404:
                logger.error(f"Group '{group_path}' 不存在")
            raise
    
    def _filter_projects(self, projects: List[dict]) -> List[dict]:
        """根据 namespace 和项目名称过滤项目"""
        if not self.namespace_pattern and not self.project_pattern:
            return projects
        
        filtered = []
        for project in projects:
            namespace = project.get("namespace", {}).get("full_path", "")
            name = project.get("name", "")
            
            # namespace 过滤
            if self.namespace_pattern and not self.namespace_pattern.search(namespace):
                continue
            
            # 项目名称过滤
            if self.project_pattern and not self.project_pattern.search(name):
                continue
            
            filtered.append(project)
        
        if len(filtered) < len(projects):
            logger.info(f"过滤后剩余 {len(filtered)}/{len(projects)} 个项目")
        
        return filtered
    
    def get_commits(
        self, 
        project_id: int,
        project_name: str,
        branch: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        stop_at_sha: Optional[str] = None
    ) -> List[dict]:
        """
        获取项目提交记录
        
        Args:
            project_id: 项目 ID
            project_name: 项目名称（用于日志）
            branch: 指定分支
            since: 开始时间
            until: 结束时间
            stop_at_sha: 遇到此 SHA 时停止（用于增量获取）
        """
        params = {"with_stats": "true"}
        if branch:
            params["ref_name"] = branch
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        
        try:
            return self._request(
                f"/projects/{project_id}/repository/commits", 
                params,
                f"获取项目 '{project_name}' 提交记录",
                stop_at_sha=stop_at_sha
            )
        except GitLabAPIError as e:
            if e.status_code == 403:
                logger.warning(f"跳过项目 '{project_name}': 无权限访问仓库")
            elif e.status_code == 404:
                logger.warning(f"跳过项目 '{project_name}': 仓库为空或不存在")
            else:
                logger.warning(f"跳过项目 '{project_name}': {e.message}")
            return []
