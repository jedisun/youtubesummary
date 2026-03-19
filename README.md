# YouTube Summary

## 功能概览

这个项目用于在本地环境中完成 YouTube 视频或本地媒体文件的语音转写与中文摘要整理。

当前处理链路为：

1. `yt-dlp` 下载 YouTube 媒体
2. `faster-whisper` 在本地执行语音转写
3. OpenAI Responses API 生成按时间段组织的中文摘要
4. 将摘要报告与转写文本写入 `downloads/`

## 部署说明

系统依赖：

- Python 3.12+
- `ffmpeg`

推荐部署步骤：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
```

## OpenAI API Key 配置

摘要阶段依赖 OpenAI Responses API，因此运行前必须配置 `OPENAI_API_KEY`。

当前终端临时生效：

```bash
export OPENAI_API_KEY="你的 OpenAI API Key"
```

如果希望每次打开 shell 自动可用，可以写入 `~/.bashrc`：

```bash
echo 'export OPENAI_API_KEY="你的 OpenAI API Key"' >> ~/.bashrc
source ~/.bashrc
```

检查变量是否已生效：

```bash
echo "$OPENAI_API_KEY"
```

使用约束：

- 不要把 API Key 写入代码、`README.md`、`design.md` 或 Git 提交
- 不要把 API Key 作为命令行参数传入脚本
- 如果怀疑泄漏，应立即在 OpenAI 平台轮换该 Key
- 若账户额度不足，摘要阶段会返回 `429 insufficient_quota`

确认依赖：

```bash
ffmpeg -version
python3 yt_asr_summary.py --help
```

## 使用方法

分析 YouTube 视频：

```bash
python3 yt_asr_summary.py "https://www.youtube.com/watch?v=j2q07GuDG_Y"
```

分析本地媒体文件：

```bash
python3 yt_asr_summary.py --media-file /path/to/video.mp4
```

调整时间窗口：

```bash
python3 yt_asr_summary.py "https://www.youtube.com/watch?v=j2q07GuDG_Y" \
  --time-window-seconds 240
```

## 输出内容

- `downloads/media/` 保存下载后的媒体文件
- `downloads/reports/` 保存 Markdown 摘要报告
- `downloads/transcripts/` 保存带元信息的转写文本

摘要报告包含：

- 总处理时长
- 下载、转写、摘要分阶段耗时
- `input_tokens`、`output_tokens`、`total_tokens`
- 按时间段组织的主题、内容摘要、重要观点

## 当前限制

- 摘要阶段依赖 OpenAI API，不是完全本地化
- 长视频下载和转写耗时可能达到几十分钟
- 摘要质量依赖音频质量与转写准确度
- YouTube 某些视频可能存在下载限制
- 时间窗口越小，摘要更细，但 token 成本会更高
