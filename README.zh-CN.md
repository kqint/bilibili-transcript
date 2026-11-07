# B 站视频转文本

把 B 站视频的任意片段转成干净的文本，并生成可直接在浏览器中阅读的单文件 HTML 页面。
工具会优先查找官方字幕或 AI 字幕；如果没有可用字幕，则只下载指定时间范围的音频，
使用本地 faster-whisper 进行语音识别。

## 功能

- 支持 `20:40`、`1:02:03` 或纯秒数选择时间范围。
- 支持官方字幕提取，可选用 `SESSDATA` Cookie。
- 无字幕时自动回退到本地语音识别，只下载所需音频片段。
- faster-whisper 支持 CUDA 加速，并自动回退到 CPU。
- 可编辑的术语表，用于修正语音识别中的人名和专有名词。
- 输出纯文本和精致的 HTML 阅读页（深色模式、复制按钮、时间点跳转）。
- 界面和文档标签支持 `en`、`zh-CN` 两种语言。

## 两套工作流

| 工作流 | 入口 | 输出 | 适用场景 |
| --- | --- | --- | --- |
| 字幕文本（v1） | `workflow/bili_transcript.py` | `.txt` + `.html` | 精确时间范围、快速阅读、保留时间轴 |
| 结构化文档（v2） | `workflow/bili_to_doc.py` | `.doc.md` + `.doc.html` | 笔记、文章、分章节的文档 |

两者共用字幕查询、音频切片、语音识别、术语表和语言文件。v2 参考了
[bilibili-to-doc](https://github.com/programmerloverun/bilibili-to-doc) 的思路：
优先用 `yt-dlp` 配合浏览器 Cookie 获取 B 站 AI 字幕，其次查询播放器接口，
最后才回退到本地语音识别。

## 环境要求

- Python 3.10 或更高版本。
- `PATH` 中可用的 `ffmpeg`。
- 可选：NVIDIA 显卡，可显著加快识别速度。

## 安装

```powershell
python -m pip install -r workflow/requirements.txt
```

如需 GPU 加速，再安装 CUDA 运行时：

```powershell
python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

## 使用

```powershell
python workflow/bili_transcript.py "https://www.bilibili.com/video/BV1c1WAzXEGE" 20:40 21:36
```

输出文件位于 `output/`：

- `BV1c1WAzXEGE_20m40s-21m36s.txt` - 全文和时间轴。
- `BV1c1WAzXEGE_20m40s-21m36s.html` - 阅读页面，用浏览器打开即可。

也可以使用封装脚本：

```powershell
.\run.ps1 "https://www.bilibili.com/video/BV1c1WAzXEGE" 20:40 21:36
```

## 版本二：结构化文档

参考：https://github.com/programmerloverun/bilibili-to-doc

```powershell
python workflow/bili_to_doc.py "https://www.bilibili.com/video/BV1c1WAzXEGE" `
  --start 20:40 --end 21:36 `
  --outline workflow/outline.example.json
```

会生成 `output/BV1c1WAzXEGE_20m40s-21m36s.doc.md` 和 `.doc.html`。文档包含元信息、
目录、章节标题、正文、时间轴和原视频链接。

该版本独有的参数：

| 参数 | 说明 |
| --- | --- |
| `--start`、`--end` | 时间范围；都不传则整理整个视频。 |
| `--outline FILE` | 章节大纲 JSON：`[{"start":"20:40","title":"..."}]`。 |
| `--cookies-from-browser` | `chrome`、`edge`、`firefox`、`safari`、`auto` 或 `none`。 |
| `--cookies FILE` | 浏览器导出的 Netscape 格式 `cookies.txt`。 |
| `--no-cookies` | 跳过 AI 字幕，直接使用接口或 ASR。 |
| `--no-asr` | 不回退到语音识别，直接失败。 |

可以手动编辑生成的 `.doc.md` 来润色标题、补充表格或代码块，然后重新生成 HTML：

```powershell
python workflow/render_doc.py "output/BV1c1WAzXEGE_20m40s-21m36s.doc.md"
```

`SKILL.md` 把同样的流程定义为 agent skill，包含润色步骤。`run-doc.ps1` 是对应的 PowerShell 封装：

```powershell
.\run-doc.ps1 "<URL>" 20:40 21:36 --outline workflow/outline.example.json
```


### 获取 AI 字幕所需的 Cookie

B 站只向已登录账号开放 AI 字幕。其中真正起作用的是 `SESSDATA`（登录凭证），
`DedeUserID` 可选，`buvid3`/`buvid4` 会自动生成。最省事、最可靠的做法是导出
`bilibili.com` 的全部 Cookie。

#### 方法一：导出 cookies.txt（推荐）

1. 在 Chrome 安装扩展 `Get cookies.txt LOCALLY`。
2. 登录 bilibili.com。
3. 点击扩展图标，导出 `bilibili.com` 的 Cookie。
4. 保存为 `cookies.txt`，运行时通过 `--cookies` 传入。

```powershell
python workflow/bili_to_doc.py "<URL>" --start 20:40 --end 21:36 --cookies "cookies.txt"
```

#### 方法二：只复制 SESSDATA

1. 登录 bilibili.com，按 `F12`。
2. 打开 Application（应用）-> Cookies -> `https://www.bilibili.com`。
3. 复制 `SESSDATA` 的值。
4. 用 `--sessdata` 传入，或设置环境变量 `BILI_SESSDATA`。

```powershell
python workflow/bili_to_doc.py "<URL>" --start 20:40 --end 21:36 --sessdata "<值>"
# 或
$env:BILI_SESSDATA = "<值>"
```

也支持最小化的 Netscape Cookie 文件（字段之间用制表符分隔）：

```text
# Netscape HTTP Cookie File
.bilibili.com	TRUE	/	TRUE	2147483647	SESSDATA	<值>
```

#### 方法三：直接读取浏览器 Cookie

```powershell
python workflow/bili_to_doc.py "<URL>" --cookies-from-browser chrome
```

可选值：`chrome`、`edge`、`firefox`、`safari`、`auto`、`none`。Chrome 127+ 启用了
应用绑定加密，且运行时会锁定 Cookie 数据库，因此常报
`Could not copy Chrome cookie database`。请关闭 Chrome 后重试，或改用方法一。

#### 验证 Cookie

```powershell
python workflow/bili_to_doc.py "<URL>" --start 20:40 --end 21:36 --cookies "cookies.txt" --no-asr
```

若能获取 AI 字幕，命令会正常结束，文档头部显示「B 站 AI 字幕」。退出码 2 表示
该视频没有可用的 AI 字幕；此时去掉 `--no-asr` 重跑即可回退到本地语音识别。

即使没有字幕，Cookie 也能解锁更高码率的 DASH 音频，从而提升识别效果。
`cookies.txt` 等同于账号登录凭证，请勿提交或分享；它已被列入 `.gitignore`。

## 参数

| 参数 | 说明 |
| --- | --- |
| `--page N` | 选择多 P 视频的分 P（从 1 开始）。 |
| `--model NAME` | Whisper 模型，默认 `large-v3`；性能较弱时可用 `small`/`medium`。 |
| `--device auto\|cpu\|cuda` | 识别设备，默认 `auto`。 |
| `--language zh` | 语音语言提示。 |
| `--prompt TEXT` | 初始提示，用于引导人名和术语。 |
| `--hotwords TEXT` | 空格分隔的热词。 |
| `--glossary PATH` | 替换词表 JSON，默认读取 `glossary.json`。 |
| `--sessdata VALUE` | B 站 `SESSDATA` Cookie，也可从环境变量 `BILI_SESSDATA` 读取。 |
| `--no-asr` | 仅使用官方字幕，不回退到语音识别。 |
| `--force-asr` | 忽略官方字幕，强制使用语音识别。 |
| `--out-dir PATH` | 输出目录，默认 `output/`。 |
| `--name STEM` | 自定义输出文件名。 |
| `--keep-audio` | 保留提取的音频片段到输出目录。 |
| `--ui-lang en\|zh-CN` | 界面语言，默认 `zh-CN`。 |

## 工作流程

1. 解析 BV 号，获取 `aid`/`cid`、标题和分 P 信息。
2. 请求播放器接口查询字幕轨。携带有效的 `SESSDATA` Cookie 时，多数视频可返回
   官方字幕或 AI 字幕。
3. 若无可用字幕，请求可播放流，用 `ffmpeg` 只提取指定秒数的 16 kHz 单声道 WAV。
4. 使用 faster-whisper 转写，随后统一标点并应用术语表。
5. 写出文本文件和 HTML 页面。

## 术语表

`glossary.json` 把识别错误的词映射为正确写法：

```json
{
  "replace": {
    "新闻公作者": "新闻工作者",
    "田竄": "田端"
  }
}
```

条目应尽量短且具体，避免误伤正常文本。

## 获取 SESSDATA

1. 在浏览器中登录 bilibili.com。
2. 打开开发者工具，进入 Application、Cookies、`https://www.bilibili.com`。
3. 复制 `SESSDATA` 的值。

然后设置环境变量：

```powershell
$env:BILI_SESSDATA = "你的值"
```

或直接传入 `--sessdata "你的值"`。

即使没有 Cookie，工具也能通过语音识别完成工作。

## 常见问题

- 提示 `Library cublas64_12.dll is not found`：安装上面的 CUDA 运行时，或使用
  `--device cpu`。
- 首次运行较慢：Whisper 模型只会下载一次并由 Hugging Face 缓存。下载慢时可设置
  `HF_ENDPOINT=https://hf-mirror.com`。
- 提示 `ffmpeg not found`：安装 ffmpeg 并确保它在 `PATH` 中。
- 人名识别错误：添加术语表条目，或传入 `--prompt` / `--hotwords`。
- 结尾句子不完整：把结束时间向后多取几秒。

## 项目结构

```
bili_transcript.py    v1 CLI：字幕文本 + HTML
bili_to_doc.py        v2 CLI：结构化 Markdown 文档 + HTML
render_doc.py         把 .doc.md 重新渲染为带样式的 HTML
requirements.txt      Python 依赖
glossary.json         语音识别修正词表
locales/              界面文案（en、zh-CN）
output/               生成的文本和文档
run.ps1               v1 的 PowerShell 封装脚本
SKILL.md              v2 工作流的 agent skill 定义
```

## 说明

有官方字幕时会原样使用。语音识别结果存在误差，尤其是专有名词，发布前请人工校对。
