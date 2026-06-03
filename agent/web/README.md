# Agent-of-Agent Web 控制台

Gradio 4.44 实现的任务管理面板。**与 autoctl.sh 共用同一套后台流程**——
Web 提交 = 调 `bash autoctl.sh start`，所以页面关闭/刷新都不影响后台运行。

## 功能

| Tab | 用途 |
|---|---|
| 1. 新建任务 | 表单填好 → 渲染为 spec.md → 后台启动流水线 |
| 2. 任务列表 | 所有 job 一览，点击刷新自动同步 REGISTER.json |
| 3. 任务详情 | 看 pipeline.log + run.log 最后 200 行 |
| 4. 归档发布 | 把通过的 job 发布到 FastAPI 注册表 `/agents/<name>/chat` |

## 启动

```bash
conda activate agent

# 前台（开发用）
bash /mnt/data3/clip/LangGraph/agent/agent/web/start_web.sh

# 后台（生产用）
BACKGROUND=1 bash /mnt/data3/clip/LangGraph/agent/agent/web/start_web.sh
# 停止
kill $(cat /mnt/data3/clip/LangGraph/agent/agent/web/web.pid)
```

默认端口 7860，可用 `AOA_WEB_PORT=7861 bash start_web.sh` 覆盖。

## Token 来源

- 优先页面表单中的 `ANTHROPIC_AUTH_TOKEN`
- 回退到环境变量（启动 web 之前 `export ANTHROPIC_AUTH_TOKEN=...`）
- 勾 `dry-run` 时跳过 Claude，用模板兜底（**只用来联调**）

## 文件

- `app.py` — Gradio Blocks
- `job_manager.py` — `web/jobs.db` 任务状态
- `start_web.sh`
- `jobs.db` — 由 job_manager 自动创建
- `web.log` / `web.pid` — 后台模式落盘
