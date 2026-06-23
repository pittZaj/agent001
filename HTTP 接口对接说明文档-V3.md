# 智能体对话 HTTP 接口对接说明文档

> 面向**综合管理平台**前端/后端对接文档。
> 适用场景：在综合管理平台中嵌入「类 ChatGPT 悬浮聊天框」，支持用户发送 **纯文本 / 文本+图片 / 纯图片** 三种形式的消息。
>
> - **文档版本**：v1.2
> - **最后更新**：2026-06-17
> - **维护方**：智能体平台组
> - **服务名**：KSAgent（基于 LangGraph + Qwen3-VL）

> 📌 **v1.2 关键变更**：后端已上线**短期记忆（多轮上下文）**能力。
> 请求/响应的**字段格式没有任何变化**，但 `session_id` 的**含义变了**——它现在同时是
> "记忆会话键"：**相同 `session_id` 的多次请求会自动记住上文、多轮连贯**。
> 这意味着 `session_id` 的取值规则比以前更重要（用错会串话/串记忆），详见 [第 3.5 节](#35-sessionid-与短期记忆务必理解)。

---

## 一、一句话总结

对接**只需要一个接口**：`POST /api/v1/chat`。

它同时兼容三种输入：

| 输入形式 | 走的链路 | 典型用途 |
|---|---|---|
| 纯文本 | `text`（Plan-Execute，查平台告警/统计/规章等） | "今天有哪些告警""统计告警类型并画图""未戴安全帽怎么处罚" |
| 文本 + 图片 | `multimodal`（VLM 看图回答） | （上传现场照片）"图里有人没戴安全帽吗？" |
| 纯图片 | `multimodal`（VLM 自动描述+安全合规分析） | 用户只丢一张图不打字 |

> **无需为图片单独再开接口**。早期的 `/api/v1/judge` 是「YOLO 结果 + 图 → 固定四属性判断」的专用复判接口，返回结构固定，**不适合**通用聊天框，对接聊天框请用 `/api/v1/chat`。

---

## 二、服务地址

| 环境 | Base URL | 说明 |
|---|---|---|
| 当前部署机 | `http://192.168.1.90:8001` | 局域网内访问 |
| 本机自测 | `http://127.0.0.1:8001` | 服务所在机器上 |

- **协议**：HTTP（暂未启用 HTTPS）
- **鉴权**：当前**无鉴权**（局域网内部服务）。如需对外网暴露，请在网关层加鉴权，不要直接公网暴露。
- **CORS**：已全开（`allow_origins=["*"]`），前端可直接跨域调用。

> ⚠️ 安全提示：该服务当前未做身份认证与访问控制，仅适合在受信任的内网中由综合管理平台后端转发调用。**不建议**让浏览器前端直连公网，建议由综合管理平台后端做一层代理转发。

---

## 三、核心接口：POST /api/v1/chat

### 3.1 请求

- **URL**：`POST /api/v1/chat`
- **Content-Type**：`application/json`

**请求体字段**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | string | ✅ | **会话 ID，同时是短期记忆的会话键（= 后端 thread_id）**。相同 `session_id` 的多次请求会**累积上下文、自动多轮连贯**；不同 `session_id` 相互隔离。**每个独立对话务必用唯一且稳定的 id（推荐 UUID）**，切勿用常量或跨对话复用（否则会串话/串记忆）。详见 [3.5](#35-sessionid-与短期记忆务必理解)。 |
| `message` | string | ⚠️ | 用户文本消息。纯图片场景可不传。 |
| `images` | string[] | ⚠️ | 图片列表，每项支持三种格式：① `data:image/jpeg;base64,xxx`（data URL，**推荐**）；② 裸 base64 字符串（默认按 jpeg 处理）；③ `http(s)://...` 图片直链。可传多张。 |
| `stream` | bool | ❌ | 是否流式输出（SSE）。**已启用**：传 `true` 返回 `text/event-stream` 流式响应（边想边出），不传或 `false` 仍按原 JSON 一次性返回。详见 [第 3.4 节](#34-流式输出sse). |

**约束**：`message` 与 `images` **至少有一个非空**，否则返回 400。

### 3.2 响应

**HTTP 200，响应体字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | 原样回传请求的 session_id |
| `response` | string | **给用户展示的回复正文**（Markdown 格式，可能含 `![](data:image/png;base64,...)` 内嵌图表） |
| `modality` | string | 本次实际走的链路：`text`（纯文本数据查询）/ `multimodal`（看图对话） |
| `plan` | array | 调试用：本次拆解出的任务步骤列表（纯文本链路才有内容，multimodal 为空） |
| `tool_calls` | array | 调试用：工具调用的中间结果（前端展示一般可忽略） |
| `elapsed_ms` | int | 本次处理耗时（毫秒） |

> 前端聊天框**只需渲染 `response` 字段**（建议按 Markdown 渲染，以正确显示加粗、列表、内嵌的统计图表）。`plan`/`tool_calls` 是给开发调试看的，可不展示。

### 3.3 错误响应

| HTTP 状态码 | 响应体 | 含义 |
|---|---|---|
| 400 | `{"detail":"message 与 images 不能同时为空"}` | 请求既无文本又无图片 |
| 500 | `{"detail":"<错误信息>"}` | 服务端处理异常（如 LLM/MCP 不可用） |

---

### 3.4 流式输出（SSE）

把请求体里的 `stream` 设为 `true`，接口改为 **Server-Sent Events** 流式返回（`Content-Type: text/event-stream`），让用户"边想边看"，体验更接近 ChatGPT。

> 不传 `stream` 或传 `false` 时，行为与 3.2 完全一致（一次性返回 JSON），**向后兼容**，老前端无需改动。

**事件类型**（每条 SSE 形如 `event: <类型>\ndata: <JSON>\n\n`）：

| event | data 字段 | 含义 | 前端怎么用 |
|---|---|---|---|
| `status` | `{stage, message}` | 进度事件（规划/查平台/统计/画图…） | 显示在"思考中…"位置，体现实时进展 |
| `token` | `{text}` | 增量答案文本（一个或几个字） | **追加**拼接到回复气泡（逐字显示） |
| `done` | `{session_id, response, modality, plan, tool_calls, elapsed_ms}` | 最终完整结果，**与 3.2 的非流式 JSON 同构** | 用 `response` 覆盖渲染（含内嵌图表），收起进度 |
| `error` | `{detail}` | 服务端异常 | 兜底提示 |

**重要说明（务必理解，否则会误解"为什么有时没逐字效果"）**：

- **看图对话 / 简单问答**：由大模型逐字生成 → 会有连续的 `token` 事件，逐字效果明显。
- **统计 / 画图 / 查告警列表**：最终回复由后端**确定性**地拼装（含统计报告 + base64 图表），**没有逐字 token**；这类请求主要通过 `status` 事件体现进度（"正在查询平台 AI 告警…""正在生成图表…"），最终一次性在 `done` 里给出完整 `response`。
- 无论哪种路径，**`done` 事件一定会有**，且其 `response` 就是给用户展示的最终正文。**最稳妥的前端策略**：实时拼 `token`、展示 `status`，但**以 `done.response` 为准做最终渲染**（尤其图表内嵌在 `done` 里）。

**curl 示例**：

```bash
curl -N -X POST http://192.168.1.90:8001/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"u1","message":"统计每种告警类型的数量并画柱状图","stream":true}'
```

返回（节选，`\n\n` 分隔每条事件）：

```
event: status
data: {"stage": "planning", "message": "正在理解你的问题并规划任务…"}

event: status
data: {"stage": "executing", "message": "正在执行步骤 1/2：查询平台 AI 告警…"}

event: status
data: {"stage": "summarizing", "message": "正在统计分析并生成图表…"}

event: done
data: {"session_id":"u1","response":"## 📊 告警统计报告 ...![](data:image/png;base64,iVBOR...)","modality":"text","plan":[...],"tool_calls":[...],"elapsed_ms":14820}
```

**前端对接示例（JavaScript，fetch + ReadableStream）**：

```javascript
async function askAgentStream(sessionId, text, { onStatus, onToken, onDone, onError }) {
  const resp = await fetch("http://192.168.1.90:8001/api/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: text, stream: true }),
  });
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const blocks = buf.split("\n\n");
    buf = blocks.pop();                       // 末尾可能是半条，留到下次
    for (const block of blocks) {
      const ev = (block.match(/^event: (.*)$/m) || [])[1];
      const dataLine = (block.match(/^data: (.*)$/m) || [])[1];
      if (!ev || !dataLine) continue;          // 跳过 ": ping" 心跳行
      const data = JSON.parse(dataLine);
      if (ev === "status") onStatus?.(data);
      else if (ev === "token") onToken?.(data.text);   // 追加拼接
      else if (ev === "done") onDone?.(data);          // 以 data.response 为准最终渲染
      else if (ev === "error") onError?.(data);
    }
  }
}
```

> 注：SSE 流里可能夹带 `: ping - <时间>` 这类**心跳注释行**（用于保活），不是事件，按上面的解析逻辑会自然跳过，前端无需特殊处理。带图片的多模态请求同样支持 `stream:true`（走 VLM 看图，逐字 `token` 效果明显）。

---

### 3.5 `session_id` 与短期记忆（务必理解）

后端已上线**短期记忆（会话级多轮上下文）**。`session_id` 是这套记忆的**唯一会话键**：

- **相同 `session_id`** 的多次请求 → 后端按该 id 自动加载历史、追加本轮，**多轮连贯**（用户可以追问"它呢""上面那条的详情""再画成饼图"）。
- **不同 `session_id`** → 记忆完全隔离，互不影响。
- **空 / 不传 `session_id`** → 不挂记忆，按一次性无状态调用处理（与旧行为一致）。

#### 取值规则（最重要，用错会串话）

| 做法 | 是否正确 | 说明 |
|---|---|---|
| 每个用户的每一段独立对话用一个**唯一且稳定**的 id（如 `用户ID + 会话UUID`） | ✅ 正确 | 同一段对话多轮复用同一个 id；开新对话就换新 id |
| 用固定常量（如 `"web"`、`"default"`、`"1"`） | ❌ 禁止 | **所有用户/对话会共享同一份记忆**，造成串话、隐私泄露 |
| 每次请求都生成一个新随机 id | ⚠️ 退化 | 等于永远没有记忆（每轮都是新会话），多轮追问会"失忆" |
| 跨不同业务对话复用同一个 id | ❌ 禁止 | 旧对话内容会污染新对话 |

> 推荐：综合管理平台为每个"对话窗口/会话"分配一个 UUID 作为 `session_id`，在该窗口存续期间所有请求都带它；用户"新建对话"时换一个新 UUID。这正是本平台 7860 调试页（ChatGPT 风格侧栏）的做法。

#### 记忆的边界与限制（如实告知，避免误期望）

1. **短期、非持久**：记忆存在服务进程内存中，**服务重启即清空**。不是长期用户画像，也不跨重启保留。
2. **会话级，非用户级**：只认 `session_id`，后端不做"同一用户的多个会话"聚合，也不学习用户长期偏好。
3. **自动瘦身防 token 膨胀**：后端只保留最近约 10 轮、并对历史做字符上限截断、剥离历史里的 base64 图表占位。**很久以前的内容可能不再被"记得"**——这是为响应速度做的取舍。
4. **达一定轮数建议新开会话**：单会话累积过多轮后，过早的上下文会被截断且响应变慢。建议前端在长对话时引导用户"新建对话"（换新 `session_id`）。
5. **看图（多模态）轮次**：图片本身不进记忆（只记"用户发过一张图"+ 模型的文字回答），避免图片字节撑爆历史。

#### 调用方无需改造的部分

- **请求/响应字段格式完全不变**：仍是 `{session_id, message, images?, stream?}` → `{session_id, response, modality, plan, tool_calls, elapsed_ms}`。
- **不需要前端自己拼接历史**进 `message`（旧文档曾建议的临时做法**已不再需要**）。后端已自动维护上下文，前端只管把"当前这一句"发过来即可。

---

## 四、调用示例

### 4.1 三种输入形式（curl）

**① 纯文本**

```bash
curl -X POST http://192.168.1.90:8001/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
        "session_id": "user_123",
        "message": "统计每种告警类型的数量并画柱状图"
      }'
```

返回（节选）：

```json
{
  "session_id": "user_123",
  "response": "## 📊 告警统计报告\n**告警总数**：14830 条 ...\n![统计图表](data:image/png;base64,iVBOR...)",
  "modality": "text",
  "plan": [{"task": "aggregate_alarms", "args": {...}, "status": "done"}],
  "tool_calls": [...],
  "elapsed_ms": 14143
}
```

**② 文本 + 图片**

```bash
curl -X POST http://192.168.1.90:8001/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
        "session_id": "user_123",
        "message": "图里的人戴安全帽了吗？",
        "images": ["data:image/jpeg;base64,/9j/4AAQSkZJRg..."]
      }'
```

返回（节选）：

```json
{
  "session_id": "user_123",
  "response": "图中人物**戴了安全帽**。判断依据：...",
  "modality": "multimodal",
  "plan": [],
  "tool_calls": [],
  "elapsed_ms": 5070
}
```

**③ 纯图片（不传 message）**

```bash
curl -X POST http://192.168.1.90:8001/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
        "session_id": "user_123",
        "images": ["data:image/jpeg;base64,/9j/4AAQSkZJRg..."]
      }'
```

返回（节选）：纯图无文字时，模型会自动描述图片内容并做安全合规分析。

### 4.1.1 多轮对话（短期记忆，相同 session_id）

只要**两次请求带同一个 `session_id`**，第二轮就能"记得"第一轮，无需前端拼历史：

```bash
# 第 1 轮
curl -X POST http://192.168.1.90:8001/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "conv-7f3a9e", "message": "统计未戴安全帽的告警有多少条"}'

# 第 2 轮（同一 session_id，可直接追问，无需再说"未戴安全帽"）
curl -X POST http://192.168.1.90:8001/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "conv-7f3a9e", "message": "把它画成柱状图"}'
```

> 第 2 轮里的"它"，后端会结合第 1 轮上下文理解为"未戴安全帽的告警统计"。
> 若第 2 轮换了一个新的 `session_id`，则"它"无指代对象，模型可能答非所问——这正是
> "每段对话用稳定 id、换对话才换 id"的原因。


### 4.2 后端转发示例（Python，推荐综合管理平台后端这样接）

```python
import base64
import requests

CHAT_API = "http://192.168.1.90:8001/api/v1/chat"

def ask_agent(session_id: str, text: str = "", image_bytes_list: list[bytes] | None = None):
    """综合管理平台后端：把前端聊天框的输入转发给智能体。

    text:             用户输入的文字（可空）
    image_bytes_list: 用户上传的图片二进制列表（可空）
    """
    images = []
    for b in (image_bytes_list or []):
        b64 = base64.b64encode(b).decode()
        images.append(f"data:image/jpeg;base64,{b64}")

    payload = {"session_id": session_id, "message": text, "images": images}
    resp = requests.post(CHAT_API, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["response"]   # 直接拿去渲染到聊天框
```

### 4.3 前端转发示例（JavaScript / fetch）

```javascript
async function askAgent(sessionId, text, files /* File[] 或 [] */) {
  // 把上传的图片文件转成 data URL
  const images = await Promise.all(
    (files || []).map(
      (f) =>
        new Promise((resolve) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result); // data:image/...;base64,xxx
          reader.readAsDataURL(f);
        })
    )
  );

  const resp = await fetch("http://192.168.1.90:8001/api/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: text, images }),
  });
  const data = await resp.json();
  return data.response; // 建议用 Markdown 渲染器展示
}
```

---

## 五、对接注意事项（重要）

1. **响应正文是 Markdown**：`response` 字段含 Markdown 语法（加粗、列表、表格），统计类问题还会内嵌 `![](data:image/png;base64,...)` 图表。前端请用 Markdown 渲染器（如 `marked` / `markdown-it`）展示，否则用户看到的是源码符号。

2. **已支持多轮上下文（短期记忆）**：相同 `session_id` 的多次请求会自动多轮连贯，**无需**前端再把历史拼进 `message`。请务必给每段独立对话分配**唯一且稳定**的 `session_id`，新建对话换新 id，切勿用常量或跨对话复用（详见 [3.5](#35-sessionid-与短期记忆务必理解)）。记忆是**短期、进程内、会话级**的：服务重启清空、只保留最近若干轮、不构成长期用户画像。

3. **响应耗时差异较大，超时建议 ≥ 120s**：
   - 纯文本简单查询：约 5–15 秒
   - 看图对话：约 5–15 秒
   - **首次**触发规章知识库（RAG）查询：约 60 秒（需加载向量模型，**仅首次**，之后会快）
   - 建议客户端超时设 120 秒，并在前端展示"思考中…"loading 态。
   - **流式（`stream:true`）可显著改善观感**：哪怕总耗时不变，用户能立即看到"正在查询平台…"等进度与逐字答案，不再干等。建议长耗时场景优先用流式（见 3.4）。

4. **图片格式与大小**：
   - 推荐传 `data:image/...;base64,...` 形式（前端 `FileReader.readAsDataURL` 直接得到）。
   - 支持多张图（`images` 数组）。
   - 图片越大、张数越多，base64 体积越大、VLM 推理越慢，建议前端上传前做适当压缩（如长边 ≤ 1280px）。

5. **带图必走 VLM 看图**：只要 `images` 非空，本次就走多模态看图链路（`modality=multimodal`），**不会**去查平台告警数据库。即"上传图片 = 让模型看这张图"。若用户既想查库又想看图，请分两次发送。

6. **错误兜底**：前端应对 4xx/5xx 做兜底提示（如"智能助手暂时不可用，请稍后重试"），避免把原始 `detail` 直接抛给终端用户。

---

## 六、健康检查

部署/运维可用健康检查接口确认服务可用：

```bash
curl http://192.168.1.90:8001/health
# {"status":"ok","version":"0.1.0","llm_available":true}
```

- `status`：`ok`（LLM 后端可达）/ `degraded`（LLM 后端不可达，对话会失败）
- `llm_available`：底层 Qwen3-VL（vLLM）是否在线

---

## 七、服务启停（运维参考）

```bash
source /root/anaconda3/bin/activate agent
cd /mnt/data3/clip/LangGraph/agent
python main.py        # 前台启动，监听 0.0.0.0:8001

# 后台启动
nohup python main.py > /tmp/ksagent_api.log 2>&1 &

#一键重启
cd /mnt/data3/clip/LangGraph/agent
bash restart_api.sh
```

依赖的外部服务：
- **vLLM**（Qwen3-VL-4B-Instruct-FP8）：`http://127.0.0.1:8004/v1`（看图对话与规划都依赖它）
- **MCP Server**（真实平台）：`http://192.168.1.199:6620/mcp`（纯文本查告警/设备等数据靠它）
- **Qdrant**（向量库，仅规章查询用）：`6333` 端口

---

## 八、附：接口能力速查

| 我想… | 这样调用 |
|---|---|
| 查今天/某类告警、统计、画图、查规章 | 纯文本：`{"session_id","message"}` |
| 让模型看现场照片判断是否违规 | 文本+图：`{"session_id","message","images":[...]}` |
| 用户只丢图不打字 | 纯图：`{"session_id","images":[...]}` |
| 让多轮连贯（追问"它呢""再画一个"） | 两次请求带**同一个 `session_id`**（见 3.5）；新建对话换新 id |
| 让回复边想边出（流式） | 任意请求体加 `"stream": true`，按 SSE 解析（见 3.4） |
| 确认服务是否在线 | `GET /health` |
