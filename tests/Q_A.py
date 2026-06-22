import os
import re
import json
import asyncio
import aiohttp
import numpy as np
import pandas as pd
from tqdm import tqdm
from sklearn.cluster import KMeans
from sklearn.metrics import pairwise_distances

# ******************** 配置部分 ********************
CONFIG = {
    # 公司模型API配置
    "OLLAMA_API_URL": "http://192.168.1.90:11434/api/generate",
    "EMBED_API_URL": "http://192.168.1.90:11434/api/embeddings",  # 假设公司提供嵌入服务
    "HEADERS": {"Content-Type": "application/json"},

    # 处理参数
    "CHUNK_TARGET_SIZE": 500,
    "QA_PROMPT_TEMPLATE": """请根据以下文本生成3个问答对：
    要求：
    1. 问题类型包括定义解释、操作步骤、数值信息
    2. 答案必须直接来自文本内容
    3. 使用严格JSON格式：[{"question":"...","answer":"..."}]

    文本内容：
    {text}
    """,
    "MAX_RETRY": 3,
    "REQUEST_TIMEOUT": 30
}


# ******************** 处理器类 ********************
class DocumentProcessor:
    def __init__(self):
        self.chunk_cache = {}  # 缓存分块结果

    async def process_file(self, file_path):
        text = self.read_file(file_path)
        chunks = await self.smart_split(text)
        qa_pairs = await self.generate_qa(chunks)
        return self.filter_qa(qa_pairs)

    def read_file(self, file_path):
        # 保持原有文件读取逻辑
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()

    async def smart_split(self, text):
        """基于规则分块+语义合并"""
        # 第一阶段：基础分块
        initial_chunks = self.rule_based_split(text)

        # 第二阶段：语义合并
        final_chunks = []
        current_chunk = ""
        for chunk in initial_chunks:
            if len(current_chunk) + len(chunk) < CONFIG['CHUNK_TARGET_SIZE']:
                current_chunk += "\n" + chunk
            else:
                final_chunks.append(current_chunk.strip())
                current_chunk = chunk
        if current_chunk:
            final_chunks.append(current_chunk)
        return final_chunks

    def rule_based_split(self, text):
        """纯规则分块策略"""
        # 1. 按章节分割
        chunks = re.split(r'\n第[一二三四五六七八九十]+章\s+', text)
        # 2. 按二级标题分割
        sub_chunks = []
        for chunk in chunks:
            sub_chunks.extend(re.split(r'\n\d+\.\d+\s+', chunk))
        # 3. 按段落分割
        final_chunks = []
        for chunk in sub_chunks:
            final_chunks.extend(re.split(r'\n{2,}', chunk))
        return [c.strip() for c in final_chunks if c.strip()]

    async def generate_qa(self, chunks):
        """批量生成问答对"""
        async with aiohttp.ClientSession() as session:
            tasks = [self.process_chunk(session, chunk) for chunk in chunks]
            results = await asyncio.gather(*tasks)
        return [qa for sublist in results for qa in sublist]

    async def process_chunk(self, session, chunk):
        """处理单个文本块"""
        for retry in range(CONFIG['MAX_RETRY']):
            try:
                # 生成问答对
                qa_response = await self.ollama_request(
                    session,
                    CONFIG['QA_PROMPT_TEMPLATE'].format(text=chunk[:2000])
                )

                # 质量验证
                valid_qa = []
                for item in qa_response:
                    if self.validate_qa(item, chunk):
                        valid_qa.append(item)
                return valid_qa

            except Exception as e:
                print(f"块处理失败（尝试{retry + 1}次）: {str(e)}")
        return []

    def validate_qa(self, qa, context):
        """基于规则的QA验证"""
        # 1. 答案必须在原文中
        if qa['answer'] not in context:
            return False
        # 2. 问题必须包含疑问词
        question_words = ['什么', '如何', '哪些', '是否', '怎样', '为什么']
        if not any(word in qa['question'] for word in question_words):
            return False
        # 3. 答案长度限制
        return 10 < len(qa['answer']) < 500

    async def ollama_request(self, session, prompt):
        """处理Ollama API请求"""
        payload = {
            "model": "qwen:7b",  # 根据实际模型名称调整
            "prompt": prompt,
            "format": "json",
            "stream": False
        }

        async with session.post(
                CONFIG['OLLAMA_API_URL'],
                json=payload,
                headers=CONFIG['HEADERS'],
                timeout=CONFIG['REQUEST_TIMEOUT']
        ) as resp:
            if resp.status == 200:
                response = await resp.json()
                return self.parse_response(response.get('response', ''))
            return []

    def parse_response(self, response_text):
        """解析API响应"""
        try:
            # 提取JSON部分
            json_str = re.search(r'\[.*\]', response_text, re.DOTALL).group()
            return json.loads(json_str)
        except Exception as e:
            print(f"解析失败：{str(e)}")
            return []

    def filter_qa(self, qa_pairs):
        """去重过滤"""
        seen = set()
        unique_qa = []
        for qa in qa_pairs:
            key = (qa['question'], qa['answer'])
            if key not in seen:
                seen.add(key)
                unique_qa.append(qa)
        return unique_qa


# ******************** 执行入口 ********************
async def main(input_file):
    processor = DocumentProcessor()
    qa_pairs = await processor.process_file(input_file)

    df = pd.DataFrame(qa_pairs)
    output_file = os.path.splitext(input_file)[0] + "_QA.csv"
    df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"生成完成！有效问答数量：{len(df)}")


if __name__ == "__main__":
    input_path = input("请输入要处理的文件完整路径：")
    asyncio.run(main(input_path))
