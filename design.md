# YouTube 无字幕视频摘要工具（本地 Codex 快速验证版）

## 1. 需求

目标：在本地 Codex 环境中，快速验证一个工具链是否可行，满足以下能力：

* 输入：一个 YouTube 视频链接，或一个本地视频/音频文件
* 处理：

  1. 下载视频的音频/媒体文件
  2. 对无字幕视频进行本地语音转写
  3. 按时间段将转写结果整理为中文内容摘要
* 输出：终端中打印摘要结果，并将时间轴摘要、token 用量、处理时长写入同一个 Markdown 报告文件

约束：

* **不依赖 YouTube 原始字幕**
* **优先本地部署、快速跑通**
* **先验证单视频闭环，不做前端、不做批处理**

---

## 2. 方案结论

采用以下最小可行链路：

**yt-dlp → faster-whisper → OpenAI Responses API**

对应职责：

* `yt-dlp`：负责从 YouTube URL 下载最佳可用音频
* `faster-whisper`：负责本地 ASR（语音转文字）
* `OpenAI Responses API`：负责把长转写内容整理成按时间段组织的中文摘要

这是当前最适合本地快速验证的方案，因为它：

* 不依赖视频原始字幕
* 补齐前置依赖后可快速部署
* 结构清晰，后续容易扩展成 CLI / API / MCP 工具

当前执行边界：

* 本地执行：媒体下载、音频处理、本地 ASR 转写、文件写入
* 云端执行：使用 OpenAI Responses API 生成中文摘要
* 当前默认推理配置：`faster-whisper small + cpu + int8`

---

## 3. 项目范围（本阶段）

本阶段只验证以下闭环：

1. 输入一个 YouTube URL
2. 成功下载媒体文件
3. 成功转写为文本
4. 成功输出中文摘要
5. 成功写出摘要报告文件

**本阶段不做：**

* 说话人分离
* 说话人分离
* 播放列表批量处理
* Web UI / 前端
* 数据库存储
* 多视频任务调度

---

## 4. 环境准备

建议使用 Python 虚拟环境。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -U "yt-dlp[default]" faster-whisper openai
export OPENAI_API_KEY="你的key"
```

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -U "yt-dlp[default]" faster-whisper openai
setx OPENAI_API_KEY "你的key"
```

系统依赖：

* 需要安装 `ffmpeg`
* Linux/macOS 下建议先确认 `ffmpeg -version` 可执行
* 当前环境如果缺少 `ffmpeg`，下载和转写链路不能视为可部署

密钥要求：

* `OPENAI_API_KEY` 是摘要阶段调用 OpenAI Responses API 的必需凭证
* 仅通过环境变量读取，不写入脚本、文档、日志或版本库
* 错误输出只提示“缺少 API Key”，不打印 Key 内容
* 若账户 quota 不足，摘要阶段会返回 `429 insufficient_quota`

---

## 5. 推荐默认参数

### CPU 环境（先求跑通）

* Whisper 模型：`small`
* device：`cpu`
* compute_type：`int8`
* 适合当前环境的默认验证路径

### NVIDIA GPU 环境（提升质量/速度）

* Whisper 模型：`distil-large-v3`
* device：`cuda`
* compute_type：`float16`

---

## 6. 推荐目录结构

```text
youtube-video-summary/
├─ README.md
├─ requirements.txt
├─ yt_asr_summary.py
├─ src/
│  └─ youtubesummary/
│     ├─ __init__.py
│     ├─ cli.py
│     └─ pipeline.py
└─ downloads/
   ├─ media/
   ├─ reports/
   └─ transcripts/
```

---

## 7. requirements.txt

```txt
yt-dlp[default]
faster-whisper
openai
```

说明：

* `ffmpeg` 不属于 Python 包，需要单独安装
* 若后续启用 `.env` 文件，必须加入 `.gitignore`

---

## 8. 核心脚本说明

当前入口与模块拆分：

```text
yt_asr_summary.py
src/youtubesummary/cli.py
src/youtubesummary/pipeline.py
```

职责拆分如下：

### `yt_asr_summary.py`

* 顶层兼容入口
* 保留 `python3 yt_asr_summary.py ...` 的现有调用方式
* 实际逻辑委托给 `src/youtubesummary/cli.py`

### `src/youtubesummary/cli.py`

* 解析命令行参数
* 串联下载、转写、摘要、落盘流程
* 负责终端输出与退出码

### `download_media(url)`

* 输入 YouTube URL
* 使用 `yt-dlp` 下载最佳音频
* 返回本地媒体文件路径

### `resolve_input(url, media_file)`

* URL 模式：先下载，再转写
* `--media-file` 模式：直接使用本地文件，不经过 `yt-dlp`

### `transcribe_file(media_path, ...)`

* 使用 `faster-whisper` 转写媒体文件
* 输出带时间戳的 segment 列表与完整 transcript 文本

### `group_segments_by_time(...)`

* 按固定时间窗口聚合转写片段
* 例如每 180 秒聚合为一个时间段
* 每段保留起止时间与原始文本

### `write_transcript(...)`

* 将转写原文写入 `transcripts/*.transcript.txt`
* 记录来源、媒体路径、模型参数、字符数等元信息

### `summarize_text(segments, ...)`

* 将转写内容按时间窗口切段
* 调用 OpenAI Responses API 对每个时间段分别摘要
* 输出每段的主题、内容摘要、重要观点
* 最终按时间顺序拼接，不做过度压缩
* 记录每次 API 调用的 token 用量与摘要模型名称

### `track_usage(response, ...)`

* 从 OpenAI 响应中提取 `input_tokens`、`output_tokens`、`total_tokens`
* 打印单次调用用量
* 汇总整次任务的 token 消耗，便于成本监视

### `main()`

* 串联整个流程
* 在终端打印最终结果
* 统计整次任务处理时长
* 默认写出 `reports/*.summary.md` 报告文件
* 默认写出 `transcripts/*.transcript.txt` 转写文件

---

## 9. 运行方式

基础运行：

```bash
python yt_asr_summary.py "https://www.youtube.com/watch?v=xxxxxxxxxxx"
```

指定 CPU 参数：

```bash
python yt_asr_summary.py "https://www.youtube.com/watch?v=xxxxxxxxxxx" \
  --model-name small \
  --device cpu \
  --compute-type int8 \
  --summary-model gpt-5-mini
```

指定 GPU 参数：

```bash
python yt_asr_summary.py "https://www.youtube.com/watch?v=xxxxxxxxxxx" \
  --model-name distil-large-v3 \
  --device cuda \
  --compute-type float16 \
  --summary-model gpt-5-mini
```

当前 Linux 环境建议显式使用：

```bash
python3 yt_asr_summary.py "https://www.youtube.com/watch?v=xxxxxxxxxxx"
```

分析本地媒体文件：

```bash
python3 yt_asr_summary.py --media-file /path/to/video.mp4
```

指定报告输出路径：

```bash
python3 yt_asr_summary.py "https://www.youtube.com/watch?v=xxxxxxxxxxx" \
  --output-file downloads/result.summary.md
```

时间段窗口参数建议：

```bash
python3 yt_asr_summary.py "https://www.youtube.com/watch?v=xxxxxxxxxxx" \
  --time-window-seconds 180
```

默认输出命名规则：

```text
YouTube:
  downloads/media/{timestamp}_{video_id}.media.{ext}
  downloads/reports/{timestamp}_{video_id}.summary.md
  downloads/transcripts/{timestamp}_{video_id}.transcript.txt

Local file:
  downloads/reports/{timestamp}_{original_filename}.summary.md
  downloads/transcripts/{timestamp}_{original_filename}.transcript.txt
```

---

## 10. 预期输出

终端输出分为四步：

```text
[1/4] downloaded: downloads/media/{timestamp}_{video_id}.media.webm
[2/4] transcript chars: 18342
[3/4] summary:
[4/4] usage: input_tokens=..., output_tokens=..., total_tokens=...
[report] written: downloads/reports/{timestamp}_{id}.summary.md
[transcript] written: downloads/transcripts/{timestamp}_{id}.transcript.txt
```

摘要建议格式：

```md
# 视频内容摘要

## 00:00 - 03:00
### 主题
...

### 内容摘要
...

### 重要观点
- ...
- ...

## 03:00 - 06:00
### 主题
...

### 内容摘要
...

### 重要观点
- ...
- ...
```

---

## 11. 验证通过标准

满足以下条件即可视为方案可行：

* 可以处理**无字幕视频**
* 可以稳定下载媒体文件
* 也可以直接分析本地媒体文件
* 可以输出可读的完整转写
* 可以得到按时间段组织的结构化中文摘要
* 可以输出本次摘要调用的 token 用量统计
* 可以输出包含摘要与性能指标的 Markdown 报告
* 可以输出带元信息的 transcript 文本文件
* 全流程在本地 Codex 环境可重复执行

说明：

* 当前验证通过的是“本地转写 + 云端摘要”模式
* 若要完全本地化，还需将摘要环节替换为本地大模型

---

## 12. 已知边界

当前方案的边界包括：

* 超长视频会导致转写和摘要耗时明显增加
* 音质差、多人重叠发言会影响转写质量
* 某些视频可能下载受限
* 摘要质量依赖转写质量
* 时间窗口过大时会让单段摘要过于粗糙，时间窗口过小时会让报告过碎
* API 成本会随 transcript 长度增长，需通过 token 统计持续观测
* 首次使用某个 Whisper 模型时，需要先下载模型并缓存到本地

---

## 13. 下一阶段可扩展项

在 PoC 跑通后，可继续扩展：

1. 支持自适应时间窗口或按主题断点自动分段
2. 在报告中增加下载耗时、转写耗时、摘要耗时拆分
3. 增加 URL 批量处理
4. 封装为 CLI 工具
5. 封装为 FastAPI 服务
6. 封装为 Codex / MCP 可调用工具
7. 增加失败重试与日志记录
8. 增加按任务持久化的 token / 成本报表

---

## 14. 当前推荐结论

**先做可运行原型，再逐步按 `src/` 结构工程化。**

当前最优先执行顺序：

1. 建立虚拟环境
2. 安装 `yt-dlp`、`faster-whisper`、`openai`
3. 编写 CLI 入口与 `src/` 下核心模块
4. 用 1~2 个无字幕 YouTube 视频做验证
5. 观察下载、转写、摘要三个阶段是否都成功

只要这条链路能跑通，就说明该方案适合作为后续产品化/工程化的基础版本。

---

## 15. 当前实现状态

当前工作区已实现：

* `README.md`
* `yt_asr_summary.py`
* `src/youtubesummary/cli.py`
* `src/youtubesummary/pipeline.py`
* `requirements.txt`
* `.venv` Python 虚拟环境
* `faster-whisper-small` 本地缓存
* OpenAI API 摘要链路

已验证能力：

* 可以处理 YouTube Shorts 链接
* 可以输出终端摘要
* 可以生成 `reports/*.summary.md` 报告
* 可以生成 `transcripts/*.transcript.txt` 转写文本
* 可以记录 `input_tokens`、`output_tokens`、`total_tokens`
* 可以记录整次任务 `elapsed_seconds`
* 可以记录 `download_seconds`、`transcribe_seconds`、`summarize_seconds`
* 可以通过 `--media-file` 直接分析本地媒体文件
* 可以在 `small + cpu + int8` 配置下稳定完成本地转写

当前正在推进的摘要优化方向：

* 从“总览式总结”升级为“按时间段组织的内容摘要”
* 每个时间段输出主题、内容摘要、重要观点
* 对长视频保留关键观点与关键数字，不因压缩而省略

最近一次验证样例：

* 输入：`https://www.youtube.com/shorts/X7ER4JaaLII`
* 输出媒体：`downloads/media/20260318-221808_X7ER4JaaLII.media.webm`
* 输出报告：`downloads/reports/20260318-221808_X7ER4JaaLII.summary.md`
* 输出转写：`downloads/transcripts/20260318-221808_X7ER4JaaLII.transcript.txt`
