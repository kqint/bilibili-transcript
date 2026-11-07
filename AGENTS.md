# 项目理解

## 目标

把 B 站视频指定时间范围内的字幕提取为文本或结构化文档，并生成易读的 HTML；无官方字幕时使用本地语音识别。

## 结构

- `workflow/bili_transcript.py`：v1 入口，负责解析 BV、查询字幕、下载音频片段、ASR、输出 TXT/HTML。
- `workflow/bili_to_doc.py`：v2 入口，优先用 yt-dlp + 浏览器 Cookie 获取 AI 字幕，回退播放器接口和 ASR，输出分章节的 `.doc.md` / `.doc.html`。
- `workflow/render_doc.py`：把编辑后的 `.doc.md` 重新渲染为带样式的 HTML。
- `workflow/outline.example.json`：v2 章节大纲示例。
- `SKILL.md`：v2 的 agent skill 流程说明。
- `locales/en.json`、`locales/zh-CN.json`：界面与文档标签，禁止在代码里硬编码用户可见文本。
- `glossary.json`：ASR 专有名词修正表，键为错误写法，值为正确写法。
- `output/`：生成结果，不提交。

## 约定

- 依赖：`requests`、`faster-whisper`、`zhconv`、`markdown`，可选 `yt-dlp`，外部依赖 `ffmpeg`。
- ASR 默认 `large-v3`，CUDA 失败时自动回退 CPU。
- 时间参数支持 `HH:MM:SS`、`MM:SS` 和纯秒数。
- 官方/AI 字幕通过 `SESSDATA` 或浏览器 Cookie 获取，缺失时静默回退 ASR。
- v2 的章节可由 `--outline` 指定，未指定时按停顿自动分章。
- 新增用户可见文案必须同时更新两个语言文件。
