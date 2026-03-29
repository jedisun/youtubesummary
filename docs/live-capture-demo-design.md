# YouTube 直播抓流验证 Demo 设计

## 1. 目标

本设计文档只关注直播方案中的“输入层验证”。

目标是在当前环境中验证以下问题：

1. 是否能够从 YouTube 直播链接稳定解析出可用音频流
2. 是否能够使用 `ffmpeg` 在单次限时抓取中按固定时长切出音频 chunk
3. 在有限时长内，是否能够稳定产出可供后续转写使用的音频文件

本阶段**不接入**：

- `faster-whisper`
- OpenAI 摘要
- token 统计
- 直播状态恢复全量实现

---

## 2. 方案结论

采用以下最小验证链路：

**YouTube Live URL -> yt-dlp 解析直播流 -> ffmpeg 单次限时切片 -> 本地 chunk 文件**

职责划分：

- `yt-dlp`：处理 YouTube 直播页面解析，拿到实际媒体流地址
- `ffmpeg`：在单次限时抓取中读取流并按固定长度切成音频片段
- `live_capture_demo.py`：负责流程编排、日志记录、运行时控制和结果汇总

---

## 3. 验证范围

验证脚本需要具备以下能力：

1. 接收一个 YouTube 直播链接
2. 解析直播流地址
3. 启动 `ffmpeg` 单次限时切片
4. 在指定时长后由 `ffmpeg -t` 自动结束采集
5. 输出 chunk 数量、总时长、输出目录、日志文件路径

---

## 4. 关键设计

### 4.1 输入参数

建议支持：

- `url`：YouTube 直播链接
- `--output-dir`：输出目录，默认 `downloads/live`
- `--chunk-seconds`：切片时长，默认 `30`
- `--run-seconds`：总运行时长，默认 `600`
- `--audio-format`：输出音频格式，默认 `wav`

### 4.2 流获取方式

不直接让 `yt-dlp` 持续下载完整文件，而是先解析直播直链。

优先方式：

1. 使用 Python `yt_dlp` 在 `download=False` 模式下提取信息
2. 优先从提取结果中选择纯音频流
3. 如果直播源没有纯音频流，则回退到最低码率的可播放 HLS 变体
4. 把直链交给 `ffmpeg`

### 4.3 切片方式

`ffmpeg` 使用 `segment muxer` 切片，推荐参数：

- 使用 `-t` 做单次限时抓取
- 音频转为单声道 `16kHz`
- 输出固定时长 chunk
- 文件名使用递增编号，避免验证阶段引入额外不确定性

输出路径建议：

```text
downloads/live/{run_id}/
├─ chunks/
├─ logs/
├─ state/
├─ transcripts/
└─ reports/
```

其中：

- `run_id = {timestamp}_{video_id}`
- 例如：`downloads/live/20260323-083045_fN9uYWCjQaw/`

chunk 文件名建议：

```text
chunk_00001.wav
```

这样做的目的：

1. 同一次直播验证的所有产物都天然归档在一个目录中
2. `chunk_count` 只统计当前 `run_dir/chunks/`，不会混入历史文件
3. 后续转写、窗口构建、摘要脚本都只处理当前 `run_dir`
4. 复现问题时，可以直接保留整个 `run_dir`

补充约束：

- `stamp` 的作用域是“单次脚本运行”
- `stamp` 只能在入口脚本创建一次
- 后续所有目录与文件组织都只能复用这个 `stamp`
- 工具函数禁止在运行过程中再次生成新的 `stamp`

---

## 5. 验收标准

通过标准：

1. 在单次限时抓取内稳定生成多个 chunk 文件
2. 稳定生成多个 chunk 文件
3. 日志中无持续性 fatal 错误
4. 至少随机抽查 1 个 chunk 可被 `ffprobe` 正常识别

建议观测指标：

- `chunk_count`
- `captured_seconds`
- `wall_clock_seconds`
- `ffmpeg_exit_code`
- `stream_url_resolved`

---

## 6. 已知边界

- 直播流地址可能过期
- 不同直播源质量与分片方式不同
- 网络抖动可能导致中途断流
- 第一版 demo 不处理自动续连到新解析 URL
- 第一版 demo 不验证长时间常驻 `ffmpeg` 进程的稳定性
- 第一版 demo 只验证抓流与切片，不验证后续转写和摘要质量

---

## 7. Chunk 状态模型

后续接入自动轮询、转写和摘要时，不使用单个布尔标志描述系统状态，而是使用 **per-chunk 状态表**。

推荐最小结构：

```json
{
  "chunk_id": "20260320-120001_chunk_00001",
  "chunk_path": "downloads/live/chunks/20260320-120001_chunk_00001.wav",
  "created_at": "2026-03-20T12:00:01+08:00",
  "status": "completed",
  "updated_at": "2026-03-20T12:00:35+08:00",
  "retry_count": 0,
  "error": null
}
```

### 7.1 状态定义

- `writing`
  - Capture 正在写这个 chunk 文件
- `completed`
  - chunk 已写完，内容稳定，可以进入后续处理
- `processing`
  - 某个处理流程已经领取这个 chunk，正在处理
- `processed`
  - chunk 已处理成功，当前阶段可理解为“转写完成并已写入缓冲”
- `interrupted`
  - chunk 曾进入 `processing`，但因崩溃、超时或异常退出而中断
- `failed`
  - 处理过程明确失败，当前这次尝试已结束

### 7.2 状态迁移

正常路径：

```text
writing -> completed -> processing -> processed
```

异常路径：

```text
processing -> failed
processing -> interrupted
interrupted -> completed
failed -> completed
```

迁移条件：

- `writing -> completed`
  - Capture 成功完成 chunk 写入
- `completed -> processing`
  - Scheduler / Watcher 选择最旧待处理 chunk 并分配给 worker
- `processing -> processed`
  - worker 成功完成处理并提交结果
- `processing -> failed`
  - worker 明确返回错误并写回状态
- `processing -> interrupted`
  - `updated_at` 超过超时阈值仍未刷新
- `interrupted -> completed`
  - 系统决定重试
- `failed -> completed`
  - 满足重试策略或人工触发重试

### 7.3 为什么不直接维护两个内存队列

可以把状态理解成两个逻辑队列：

- Queue 1：`status == completed`
- Queue 2：`status == processing`

但实现上不推荐维护两个独立内存队列，而推荐维护一张持久化状态表：

- 避免程序崩溃后队列内容丢失
- 可以恢复“哪个 chunk 已完成、哪个处理中、哪个中断”
- 方便最旧优先和重试逻辑

### 7.4 非正常中断如何判定

`interrupted` 不是依赖程序退出前主动写回，而是通过超时判断：

1. worker 开始处理 chunk 时：
  - `status = processing`
  - 更新 `updated_at`
2. 启动独立定时器，每 10 秒刷新一次 `updated_at`
3. 如果程序崩溃或流程异常跳出：
   - 定时器停止
   - 状态会停留在 `processing`
4. 恢复时检查：
   - `status == processing`
   - 且 `now - updated_at > processing_timeout`
   - 则改为 `interrupted`

推荐第一版心跳与超时规则：

```text
heartbeat_interval = 10s
processing_timeout = 30s
```

也就是：

- 正常情况下，每 10 秒至少刷新一次 `updated_at`
- 如果连续 30 秒没有刷新，认为该 chunk 的处理流程已异常中断

### 7.5 转写结果分类

在接入真实转写后，`processed` 只表示“流程处理成功结束”，并不等于“该 chunk 一定有可用语音内容”。

因此每个 chunk 在 `processed` 状态下，还需要额外记录：

- `segment_count`
  - Whisper 实际识别出的 segment 数量
- `char_count`
  - 转写文本字符数，用于判断内容密度
- `transcript_status`
  - 对转写结果的内容分类
- `summary_eligible`
  - 该 chunk 是否应该进入后续摘要窗口

推荐最小结构补充如下：

```json
{
  "segment_count": 3,
  "char_count": 87,
  "transcript_status": "normal",
  "summary_eligible": true
}
```

分类规则：

- `failed`
  - 处理流程失败，由 `status = failed` 和 `error` 表示
- `empty`
  - 处理成功，但没有识别到有效语音
  - 判定条件：`status = processed` 且 `segment_count = 0` 且 `char_count = 0`
- `low_content`
  - 处理成功，但内容很少，可能是噪声、短口播、广告尾句或识别质量较差
  - 判定条件：`status = processed` 且 `char_count < 20` 且不满足 `empty`
- `normal`
  - 处理成功，且内容达到可用阈值
  - 判定条件：`status = processed` 且 `char_count >= 20`

摘要过滤规则：

- `summary_eligible = true`
  - 仅当 `transcript_status = normal`
- `summary_eligible = false`
  - 当 `transcript_status = empty` 或 `low_content`
  - 这些 chunk 仍然保留在状态表和 transcript 中，用于排障和质量统计
  - 但默认不进入后续摘要窗口

这个分类机制的作用：

1. 帮助判断 chunk 是否值得进入后续摘要
2. 区分“没有语音内容”和“处理失败”
3. 为广告段、纯音乐段、过场段的识别提供基础统计

### 7.6 窗口构建过滤规则

在进入摘要阶段前，先进行窗口构建。窗口构建遵循两个原则：

1. 时间轴按 **所有 `processed` chunk 的顺序** 推进
2. 窗口正文只包含 `summary_eligible = true` 的 chunk 文本

这样做的目的：

- 即使中间夹杂广告、纯音乐、空白段，直播时间轴仍然连续
- 低价值 chunk 不会污染后续摘要窗口
- 状态表和 transcript 仍然完整保留全部 chunk，便于排障

第一版窗口构建规则：

- `chunk_seconds = 30`
- `window_seconds = 300`
- 每个窗口最多容纳 10 个 chunk
- 如果某个窗口内没有任何 `summary_eligible = true` 的 chunk，则该窗口直接跳过，不进入摘要

### 7.7 最小摘要链路

在窗口构建验证通过后，最小摘要链路如下：

```text
chunks_state.json + live.transcript.txt
  -> build_live_windows()
  -> summarize_live_windows()
  -> live.summary.md
```

当前版本约束：

- 只对已经构建完成的窗口做摘要
- 使用 `state/summary_state.json` 持久化已摘要窗口
- 同一个窗口只调用一次模型，后续运行只追加新窗口
- `live.summary.md` 每次都由 `summary_state.json` 重新渲染，不解析历史 Markdown 反推状态

当前版本目标：

1. 验证 `summary_eligible` 过滤规则能够正确传递到摘要输入
2. 验证窗口级摘要能生成结构化 Markdown 报告
3. 验证能够记录 token 用量
4. 验证再次运行时不会重复摘要已完成窗口

---

## 8. 与主项目的关系

该 demo 是直播模式的前置验证，不替代主流程设计。

验证通过后，再继续实现：

1. chunk 轮询
2. 增量转写
3. 窗口级摘要
4. 状态恢复

对应代码建议放在：

- `scripts/live_capture_demo.py`
- `scripts/live_transcribe_demo.py`

后续若验证通过，再把能力迁移到：

- `src/youtubesummary/live/`

---

## 9. 后续流水线时序图

在直播抓流 demo 验证通过后，后续完整直播模式建议采用如下流水线：

```text
YouTube Live
  -> yt-dlp 解析直播流
  -> ffmpeg 抓取并切片
  -> downloads/live/chunks/*.wav
  -> Chunk Watcher
  -> Transcribe Queue
  -> Whisper Transcriber
  -> Transcript Buffer / transcript.txt
  -> Window Builder
  -> Summary Queue
  -> LLM Summarizer
  -> summary.md / state.json
```

### 9.1 时序图

```text
图 A：直播流 -> chunk -> 转写

YouTube         Capture         ChunkDir        Watcher        Transcriber      State/Out
   │               │               │               │               │               │
   │ resolve_stream()              │               │               │               │
   │──────────────>│               │               │               │               │
   │<──────────────│               │               │               │               │
   │ stream_url    │               │               │               │               │
   │               │ write_chunk(chunk_path)       │               │               │
   │               │──────────────>│               │               │               │
   │               │<──────────────│               │               │               │
   │               │ chunk_written │               │               │               │
   │               │               │ scan_chunks() │               │               │
   │               │               │──────────────>│               │               │
   │               │               │<──────────────│               │               │
   │               │               │ new_chunk_list│               │               │
   │               │               │ is_chunk_stable(chunk_path)   │               │
   │               │               │──────────────>│               │               │
   │               │               │<──────────────│               │               │
   │               │               │ stable=true   │               │               │
   │               │               │               │ update_chunk_state(stable)     │
   │               │               │               │───────────────────────────────>│
   │               │               │               │<───────────────────────────────│
   │               │               │               │ ok            │               │
   │               │               │               │ enqueue_transcribe(chunk_path) │
   │               │               │               │──────────────>│               │
   │               │               │               │<──────────────│               │
   │               │               │               │ accepted      │               │
   │               │               │               │               │ read_chunk(chunk_path)
   │               │               │<───────────────────────────────│               │
   │               │               │──────────────>│               │               │
   │               │               │ chunk_bytes   │               │               │
   │               │               │               │               │ transcribe_file(chunk_path)
   │               │               │<───────────────────────────────│               │
   │               │               │──────────────>│               │               │
   │               │               │ segment_list  │               │               │
   │               │               │               │ update_chunk_state(transcribed)│
   │               │               │               │───────────────────────────────>│
   │               │               │               │<───────────────────────────────│
   │               │               │               │ ok            │               │

图 B：transcript -> window -> summary -> report

Buffer          Window          Summarizer       State/Out
  │               │               │               │
  │ build_window()│               │               │
  │──────────────>│               │               │
  │<──────────────│               │               │
  │ window_or_none│               │               │
  │               │ enqueue_summary(window)       │
  │               │──────────────>│               │
  │               │<──────────────│               │
  │               │ accepted      │               │
  │               │               │ summarize_window(window)
  │               │<──────────────│               │
  │               │──────────────>│               │
  │               │ summary_block │               │
  │               │ append_summary_block(summary) │
  │               │──────────────────────────────>│
  │               │<──────────────────────────────│
  │               │ written       │               │
  │ append_transcript_text()      │               │
  │──────────────────────────────>│               │
  │<──────────────────────────────│               │
  │ written       │               │               │
  │               │ update_window_state(done)     │
  │               │──────────────────────────────>│
  │               │<──────────────────────────────│
  │               │ ok            │               │
```

### 9.2 时序图说明

- `Watcher` 只负责发现并提交 chunk，不等待摘要完成
- `Transcriber` 完成后只更新 transcript 和状态，不直接触发总结历史全文
- `Summarizer` 只处理已完成的时间窗口，不按 chunk 逐个做摘要
- `StateStore` 是恢复点，记录 chunk 与 window 的推进状态

### 9.3 分层职责

```text
层 1: Capture
  负责把直播流变成 chunk 文件

层 2: Watcher
  负责发现新 chunk，并判断文件是否已经写完

层 3: Transcribe
  负责把单个 chunk 转成 transcript segment

层 4: Window
  负责把多个 transcript segment 聚合成 5 分钟窗口

层 5: Summarize
  负责对完整窗口做一次摘要，不重复总结历史窗口

层 6: Persist
  负责写 transcript、summary、state
```

### 9.4 避免阻塞的基本原则

```text
Watcher 不等摘要
Watcher 只负责发现并入转写队列

Transcriber 不等摘要
Transcriber 完成后只更新 transcript buffer 和 state

Summarizer 只处理窗口
不按 chunk 摘要
```

### 9.5 两个核心队列

```text
Queue A: Transcribe Queue
  输入: stable chunk
  输出: transcript segment

Queue B: Summary Queue
  输入: completed time window
  输出: timeline summary block
```

### 9.6 推荐默认时间粒度

```text
chunk: 30s
summary window: 300s
watch poll: 2-5s
```

该状态图用于后续直播正式方案的实现设计，不属于当前 demo 的执行范围。
