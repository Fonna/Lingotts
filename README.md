# TedLib 🎬

本地英语学习平台：Python 标准库 HTTP 服务 + HTML/JS 前端，媒体与代码分离。

## 启动

双击 `启动TED播放器.bat`，或执行：

```powershell
uv run ted_server.py
# 打开 http://127.0.0.1:8765/index.html
```

需要 Python 3.12+。默认只监听本机。请通过 HTTP 服务访问页面。

## 页面与项目结构

```text
Youtube/
├── TED/                         媒体根目录，保留现有视频文件夹
│   └── <folder>/
│       ├── <media>.webm/.mp4/.m4a/.wav
│       └── transcript.txt       带时间戳原文
└── TedLib/
    ├── catalog/<id>.json         每视频一份权威元数据，随 Git 保存
    ├── index.html               学习中心
    ├── library.html             动态视频库、搜索、分类
    ├── player.html              所有视频共用的播放器
    ├── transcript.html          所有视频共用的转录阅读页
    ├── manage.html              扫描目录、导入和编辑视频
    ├── vocab.html / review.html  生词本、间隔复习
    ├── assets/                  共享样式与页面逻辑
    ├── video_catalog.py         目录汇总、校验、导入
    ├── ted_server.py            API、媒体路由与 Range 请求
    ├── scripts/import_video.py  命令行导入、校验
    └── data/                    生词、复习、本地词典
```

## 新增视频：只导入内容

1. 在 `TED/` 下创建目录，放入媒体；有带时间戳的字幕时，可同时放入 `transcript.txt`。
2. 打开“内容管理”，点“重新扫描目录”，选择“待导入”的目录。
3. 确认媒体文件，补充标题、讲者、分类、标签与摘要，点击“确认导入”。字幕路径可留空（默认 `transcript.txt`），也可指定稍后准备的文件名。
4. 打开或刷新视频库，新卡片会自动出现。没有字幕时显示“待转录 · 可播放”；有字幕时启用同步字幕、点词学习与转录阅读。

不再复制视频卡片，不再编辑 `VIDEO_REGISTRY`，不再逐视频制作 `transcript_viewer.html`。现存独立转录文件可以保留作历史资料。

导入会读取字幕头部的标题、讲者、来源、时长。只有一个主要媒体候选时自动选择；多个候选需要确认。发布时间未知时允许留空，不编造日期。媒体必须已存放在服务器的 `TED/` 目录；管理界面不负责下载、上传或自动转录。没有字幕也能导入、播放和保存续播位置。字幕准备好后放到指定路径，刷新页面即自动接入，同一视频 ID 与原有记录保留，无需再次导入。已有字幕内容仍需通过格式校验。

旧媒体目录中的 `meta.json` 仅作首次导入来源。导入后只编辑管理页或 `catalog/<id>.json`，旧文件不会参与运行时目录汇总。目录通过 `/api/videos` 自动生成，无需手工维护第二份清单；修改数据后刷新相关页面即可。

命令行也可导入：

```powershell
python scripts/import_video.py "新视频目录"
python scripts/import_video.py "新视频目录" --metadata "补充信息.json"
python scripts/import_video.py --check
```

补充信息文件是对象，可包含下面的内容字段，不包含 `id`、`aliases`、`schema_version`。命令出错返回非零状态。

## 元数据规范与迁移

`catalog/<id>.json` 使用 `schema_version: 1`。文件名与固定 ID 一致。

| 字段 | 规则 |
| --- | --- |
| `id` | 首次导入生成的 32 位 UUID；修改标题或目录时保留 |
| `folder` | 媒体根目录下的相对路径；保留真实 Unicode 字符 |
| `aliases` | 历史目录别名；移动目录时自动追加，不删除已有别名 |
| `title` | 必填标题，最多 255 字符 |
| `media_file` | 视频目录内相对路径，媒体文件必须存在 |
| `transcript_file` | 视频目录内相对路径；可留空，默认 `transcript.txt`，文件可稍后补充 |
| `speaker` / `category` / `language` | 讲者、分类、语言；语言默认 en |
| `tags` | 文本列表，最多 30 项，每项最多 80 字符 |
| `published` / `downloaded` | YYYY-MM-DD；允许未知日期留空 |
| `youtube` | 可选 HTTP/HTTPS 来源链接，兼容非 YouTube 内容 |
| `summary_zh` / `summary_en` | 双语摘要，各最多 6000 字符 |
| `duration_s` | 非负有限秒数；首次导入由字幕信息推导 |

媒体格式支持 mp4、webm、mov、m4a、mp3、wav、ogg；能否解码取决于浏览器与实际编码。媒体 URL 由服务端逐路径分量编码，不能手工替换弯引号、全角竖线等字符。

字幕格式保持：

```text
Title: Title | Speaker | TED
Source: https://example.org/video
Detected language: en (p=1.00)
Duration: 123.4s

[4.1s -> 8.4s] The original English sentence.
```

提供字幕时头部可选，时间戳正文必需。开始时间必须非递减，结束时间必须大于开始时间。片段标识为 `视频ID:segment-序号`，修改标题和目录不改变片段标识；片段标识依赖字幕顺序；重排字幕时需处理已有学习任务的关联。

7 个原有视频已经迁移。原有 `player.html?video=<目录名>&t=<秒数>` 链接、生词出处与浏览器续播记录通过别名继续使用；新链接使用固定 ID。读取生词时返回统一 ID，评分记录与原始生词文件不因读取被改写；重新标记旧生词不会生成重复记录。

移动或重命名媒体目录后，在管理页选择原来的视频，修改“媒体目录路径”并保存。即使旧路径不存在，也会显示该视频以便修复。命令行等价操作：

```powershell
python scripts/import_video.py "新目录" --id "原有固定ID"
```

不要通过重新导入生成另一个 ID。缺失文件、错误 JSON、无效时间戳和身份冲突会列为校验问题，不会让其他正常视频消失。元数据以临时文件 + 原子替换保存；网页写入限制为本机同源 JSON 请求。并行使用 CLI 与网页写入同一条记录暂不支持。

## 学习功能

- 播放器同步字幕，点词查询音标、英汉释义并标记生词；生词本和复习页可跳回原句。
- 查词弹窗、生词本、复习卡均有“🔊 读音”按钮，翻面前后都能听单词。再次点击停止；切换单词、关闭弹窗或离开页面会取消旧朗读，朗读时暂停当前视频。
- 读音优先使用浏览器英语语音，并优先选择本地语音；浏览器没有英语语音或朗读失败时，自动使用 Windows 自带英语语音生成音频。备用方案不需要外部 API 或下载依赖，要求系统已安装英语语音包；非 Windows 环境需要浏览器提供英语语音。语音不可用或播放被阻止时显示可重试提示。
- 本地词典 `data/ecdict.sqlite` 约 5.8 万词条，现有字幕不同单词约 98% 可精确匹配。来源与许可证见 `data/ECDICT-LICENSE.txt`。重建：`python scripts/build_dictionary.py`。
- 可选语境解析调用火山方舟 `doubao-seed-2.1-turbo`。启动环境配置 `ARK_API_KEY`（或 `AGENT_PLAN_KEY`）；密钥仅服务端读取。同词同句缓存至 `data/analysis_cache.json`。离线核心播放、查词和复习不依赖 AI。
- 复习按单词去重，原句与出处保留。认识按简化 SM-2 安排（1 天、6 天、之后乘难度系数）；模糊次日再看，不认识 10 分钟后再看。
- 生词存于 `data/vocab.json`，复习存于 `data/review.json`，续播存于当前浏览器 `localStorage` 的 `ted:last`。

目录 API 的 `transcript_status` 由当前文件推导：`missing`（待转录）或 `ready`（已就绪），不写入第二份状态清单。缺失字幕时 `/api/transcript` 返回空片段列表和待转录状态，播放器与转录页给出明确提示。

## API 与验证

| API | 用途 |
| --- | --- |
| GET `/api/videos` | 自动目录与校验问题 |
| GET `/api/video?video=<id或旧别名>` | 单视频元数据与媒体 URL |
| GET `/api/transcript?video=<id或旧别名>` | 原文片段与跳转时间 |
| GET `/api/video-folders` | 已导入和待导入目录 |
| POST `/api/videos` | `{folder, metadata, id?}` 导入或编辑 |
| `/api/vocab` / `/api/review` | 生词与复习 |
| `/api/dictionary` / `/api/word-analysis` | 本地释义与语境解析 |
| GET `/api/pronunciation?word=<单词>` | Windows 备用英语读音（WAV，内存缓存最近 128 词） |

```powershell
python -m unittest discover -s tests -v
node --test tests/test_pronunciation.js
python scripts/import_video.py --check
python tests/browser_fixture.py
# 浏览器测试使用 http://127.0.0.1:8767，独立目录和学习数据。
# 测试结束后 Ctrl+C 关闭服务，自动清理测试目录。
```

媒体路由 `/TED/` 指向媒体根目录，支持 Range 请求；解析路径后检查目录边界，阻止路径越界。网页导入支持一级媒体目录扫描，嵌套目录可通过 CLI 导入。

## 许可证

项目使用 MIT 许可证；ECDICT 数据许可与来源单列于 `data/ECDICT-LICENSE.txt`。媒体与字幕沿用各自来源的使用条件，公开分发前需核查授权。
