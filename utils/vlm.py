import base64
from pathlib import Path
from typing import Dict, Any
from langchain_openai import ChatOpenAI
from loguru import logger

from utils import CONFIG


class VLMClient:
    """Qwen3-VL 多模态大模型客户端"""

    def __init__(self):
        llm_config = CONFIG["llm"]
        self.client = ChatOpenAI(
            base_url=llm_config["base_url"],
            api_key=llm_config["api_key"],
            model=llm_config["model"],
            temperature=llm_config["temperature"],
            max_tokens=llm_config["max_tokens"],
            timeout=llm_config["timeout"],
        )
        logger.info(f"VLM 客户端初始化完成: {llm_config['model']}")

    def judge_image(
        self,
        image_path: str | None = None,
        image_base64: str | None = None,
        prompt: str = "请判断图中人员是否存在以下行为：抽烟、未戴安全帽、接打电话、未戴口罩。",
        yolo_result: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        多模态图像判断

        Args:
            image_path: 图片路径
            image_base64: 图片 base64（优先级高于 image_path）
            prompt: 判断提示词
            yolo_result: YOLO 检测结果（可选，用于辅助判断）

        Returns:
            {"verdict": {...}, "reasoning": "...", "confidence": 0.9}
        """
        # 准备图片
        if image_base64:
            if not image_base64.startswith("data:image"):
                image_url = f"data:image/jpeg;base64,{image_base64}"
            else:
                image_url = image_base64
        elif image_path:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            image_url = f"data:image/jpeg;base64,{b64}"
        else:
            raise ValueError("必须提供 image_path 或 image_base64")

        # 构建提示词
        full_prompt = prompt
        if yolo_result:
            full_prompt += f"\n\nYOLO 检测结果：{yolo_result}"

        full_prompt += (
            "\n\n请按以下 JSON 格式返回判断结果：\n"
            '{"smoking": 0, "helmet": 1, "phone": 0, "mask": 2, "reasoning": "..."}\n'
            "其中 0=否, 1=是, 2=不确定"
        )

        # 调用 VLM
        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": full_prompt},
            ],
        }]

        try:
            response = self.client.invoke(messages)
            content = response.content

            # 解析 JSON（简单实现，生产环境需要更健壮的解析）
            import json
            import re

            # 尝试提取 JSON
            json_match = re.search(r'\{[^}]+\}', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return {
                    "verdict": {
                        "smoking": int(result.get("smoking", 2)),
                        "helmet": int(result.get("helmet", 2)),
                        "phone": int(result.get("phone", 2)),
                        "mask": int(result.get("mask", 2)),
                    },
                    "reasoning": result.get("reasoning", content),
                    "confidence": 0.85,  # 简化实现，实际需要从模型输出解析
                }
            else:
                # 降级：返回原始文本
                return {
                    "verdict": {"smoking": 2, "helmet": 2, "phone": 2, "mask": 2},
                    "reasoning": content,
                    "confidence": 0.5,
                }

        except Exception as e:
            logger.error(f"VLM 调用失败: {e}")
            raise


# 全局单例
_vlm_client = None

def get_vlm_client() -> VLMClient:
    """获取 VLM 客户端单例"""
    global _vlm_client
    if _vlm_client is None:
        _vlm_client = VLMClient()
    return _vlm_client
