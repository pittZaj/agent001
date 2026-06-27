#!/usr/bin/env python3
"""预下载 NLTK 数据（安装/运维一次性执行，避免上传 .doc 时临时下载）。

用法（VLLM 环境）:
    cd /var/lib/ksom/var/lib/LangGraph/agent
    python -m skills.kb.setup_nltk

数据默认保存到 ~/nltk_data，或设置环境变量 NLTK_DATA 指定目录。
"""
from __future__ import annotations

import ssl

# 内网/自签证书环境
ssl._create_default_https_context = ssl._create_unverified_context

from skills.kb.document_loader import warmup_nltk_data


def main() -> None:
    warmup_nltk_data()
    print("NLTK 数据已就绪（punkt_tab / tagger 等）")


if __name__ == "__main__":
    main()
