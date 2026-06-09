# Gradio Web 控制台重启指南

## 问题描述

**错误信息**：
```
❌ 上传失败: Cannot copy out of meta tensor; no data! Please use torch.nn.Module.to_empty() instead of torch.nn.Module.to() when moving module from meta to a different device.
```

**原因**：
1. `sentence-transformers` 5.5.1 版本在加载某些模型时存在设备迁移问题
2. 之前的 Web 进程可能在模型加载时缓存了错误状态

**解决方案**：
1. ✅ 已将 `sentence-transformers` 降级到 3.4.1（稳定版本）
2. ✅ 提供了完整的重启脚本

---

## 重启方法（推荐）

### 方法 1：使用重启脚本（最简单）⭐

```bash
cd /mnt/data3/clip/LangGraph/agent
bash restart_web.sh
```

**说明**：
- 自动停止旧进程
- 激活 conda 环境
- 验证依赖版本
- 前台启动 Web（可实时查看日志）

**停止服务**：按 `Ctrl+C`

---

### 方法 2：手动重启（灵活）

#### 第 1 步：停止旧进程

```bash
# 查找 Web 进程
ps aux | grep "python.*web/app.py" | grep -v grep

# 停止进程（替换为实际 PID）
kill <PID>

# 或者一键停止
pkill -f "python.*web/app.py"
```

#### 第 2 步：激活环境并启动

```bash
# 激活 conda 环境
source /root/anaconda3/bin/activate agent

# 进入目录
cd /mnt/data3/clip/LangGraph/agent/agent

# 前台启动（推荐，可看日志）
python web/app.py
```

**停止服务**：按 `Ctrl+C`

---

### 方法 3：后台启动（服务器模式）

```bash
cd /mnt/data3/clip/LangGraph/agent/agent
source /root/anaconda3/bin/activate agent

# 后台启动
nohup python web/app.py > web/web.log 2>&1 &
echo $! > web/web.pid

# 查看日志
tail -f web/web.log

# 停止服务
kill $(cat web/web.pid)
```

---

## 验证是否成功

### 1. 检查进程是否运行

```bash
ps aux | grep "python.*web/app.py" | grep -v grep
```

应该看到类似：
```
root     1234567  1.2  0.5  ...  python web/app.py
```

### 2. 访问 Web 界面

浏览器打开：`http://<服务器IP>:7860`

### 3. 测试 Word 文档上传

1. 打开 **Tab7 知识库管理**
2. 点击「选择文件」，上传一个 Word 文档
3. 填写标题和分类
4. 点击「上传」

**预期结果**：
```
✅ 上传成功
文档ID: abc12345-...
分块数: 50
分块策略: fixed_size
```

---

## 常见问题

### Q1: 启动时报 "Address already in use"

**原因**：7860 端口被占用

**解决方案**：
```bash
# 查找占用端口的进程
lsof -i:7860

# 停止进程
kill <PID>

# 或者使用其他端口
export AOA_WEB_PORT=7861
python web/app.py
```

### Q2: 启动后立即退出

**原因**：可能是依赖问题

**解决方案**：
```bash
# 检查依赖版本
pip list | grep -E "sentence-transformers|torch|transformers"

# 应该看到：
# sentence-transformers  3.4.1
# torch                  2.8.0
# transformers           4.57.6

# 如果版本不对，重新安装
pip install 'sentence-transformers>=3.0,<4.0' -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q3: 模型加载失败

**原因**：模型路径不存在或 GPU 不可用

**解决方案**：
```bash
# 检查模型路径
ls -lh /mnt/data3/clip/LangGraph/VLLM/BGE-M3/

# 检查 GPU
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}')"

# 应该输出：CUDA: True
```

### Q4: 上传 PDF 正常，Word 还是报错

**原因**：`unstructured[doc]` 依赖未安装

**解决方案**：
```bash
source /root/anaconda3/bin/activate agent
pip install "unstructured[doc]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

---

## 依赖版本记录（2026-06-09 修复）

| 依赖 | 版本 | 说明 |
|------|------|------|
| sentence-transformers | 3.4.1 | ✅ 已从 5.5.1 降级（修复 meta tensor 错误） |
| torch | 2.8.0+cu128 | ✅ 钉版本 |
| transformers | 4.57.6 | ✅ 钉版本 |
| FlagEmbedding | 1.4.0 | ✅ 正常 |
| python-docx | 1.2.0 | ✅ Word 支持 |
| unstructured | 0.18.32 | ✅ 文档解析 |

---

## 启动日志示例（正常）

```
2026-06-09 09:30:15.123 | INFO     | skills.kb.service:__init__:45 - 初始化 KB Service: collection=safety_regulations, device=cuda:0
2026-06-09 09:30:19.856 | INFO     | skills.kb.service:__init__:52 - KB Service 初始化完成
Running on local URL:  http://0.0.0.0:7860

To create a public link, set `share=True` in `launch()`.
```

**关键指标**：
- ✅ KB Service 初始化成功（约 4-5 秒）
- ✅ 显示 `Running on local URL`
- ✅ 无报错信息

---

## 总结

**根本原因**：`sentence-transformers` 5.5.1 版本的 meta tensor 迁移 bug

**修复方案**：降级到 3.4.1 稳定版本

**重启指令**（推荐）：
```bash
cd /mnt/data3/clip/LangGraph/agent
bash restart_web.sh
```

**验证方法**：上传 Word 文档到知识库，应显示"✅ 上传成功"

---

**文档维护**: Claude Opus 4.8  
**修复日期**: 2026-06-09  
**版本**: V1.0
