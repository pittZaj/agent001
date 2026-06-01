import yaml
from pathlib import Path
from typing import Dict, Any
from loguru import logger

# 加载配置
CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

def load_config() -> Dict[str, Any]:
    """加载配置文件"""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    logger.info(f"配置已加载: {CONFIG_PATH}")
    return config


CONFIG = load_config()
