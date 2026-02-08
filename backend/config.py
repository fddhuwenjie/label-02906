"""配置文件管理"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 默认配置文件路径
DEFAULT_CONFIG_PATHS = [
    Path.home() / ".gitlab-stats.json",
    Path(".gitlab-stats.json"),
]


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    加载配置文件
    
    优先级: 指定路径 > 当前目录 > 用户目录
    """
    paths_to_check = []
    
    if config_path:
        paths_to_check.append(Path(config_path))
    
    paths_to_check.extend(reversed(DEFAULT_CONFIG_PATHS))
    
    for path in paths_to_check:
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    logger.info(f"已加载配置文件: {path}")
                    return config
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"配置文件读取失败 {path}: {e}")
    
    return {}


def save_config(config: Dict[str, Any], config_path: Optional[str] = None) -> None:
    """保存配置到文件"""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATHS[1]
    
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info(f"配置已保存到: {path}")
    except IOError as e:
        logger.warning(f"配置保存失败: {e}")
