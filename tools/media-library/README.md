# 素材下载工具箱（小红书 / 抖音 / YouTube / B站 / IG …）

把「一条链接」变成「原文件 + 元数据 + 可追溯记录」的最小工具集。
落盘目录结构与云盘素材库一一对应，抓完直接同步/拖拽即可。

```
tools/media-library/
├── install.sh      # 一键安装（venv + yt-dlp + gallery-dl + ffmpeg）
├── media-fetch     # 主入口：链接 → 视频/图文
├── media-batch     # 批量：links.txt 逐行读，失败单独记录
├── github-fetch    # 备用通道：网络只放行 GitHub 时，从仓库直取原始素材
└── docs/cookies.md # 小红书 / 抖音 登录态导出
```

## 安装

```bash
bash tools/media-library/install.sh
export PATH="$HOME/.local/media-toolkit/bin:$PATH"   # 建议写进 ~/.bashrc
```

装在 `~/.local/media-toolkit`（venv + 脚本软链），不动系统 Python、不装系统包。
ffmpeg 由 `imageio-ffmpeg` 自带，合并音视频 / 抽封面 / 转码都够用。
安装脚本会把系统 CA 合并进 certifi（幂等），否则公司代理/云沙箱下 TLS 会直接失败。

可选：把国内专用下载器源码一并克隆（不 pip 安装，避免污染环境）

```bash
MEDIA_TOOLKIT_VENDORS=1 bash tools/media-library/install.sh
# → ~/.local/media-toolkit/src/{xhs-downloader,tiktok-downloader,douyin-downloader,media-crawler}
```

| 项目 | 能力 | 备注 |
|---|---|---|
| `yt-dlp/yt-dlp` | 视频主力，含小红书图文笔记 | pip 安装，唯一必装 |
| `mikf/gallery-dl` | 图文/相册主力（IG / Pinterest / 微博 / 堆糖） | pip 安装 |
| `JoeanAmier/XHS-Downloader` | 小红书专用：无水印原图、批量主页 | 需 cookie |
| `JoeanAmier/TikTokDownloader` | 抖音 / TikTok 双端批量 | 需 cookie |
| `jiji262/douyin-downloader` | 抖音单条 / 主页批量、图集、音乐 | 需 cookie |
| `NanmiCoder/MediaCrawler` | 关键词级爬取（小红书/抖音/B站/微博） | 重，按需 |

## 用法

```bash
media-fetch "https://www.xiaohongshu.com/explore/xxxx"            # 单条，自动判断视频/图文
media-fetch "<链接1>" "<链接2>"                                    # 批量
media-fetch "<链接>" --cookies ~/.config/cookies/xhs.txt          # 带登录态
media-fetch "<链接>" --proxy http://127.0.0.1:7890                # 走代理
media-fetch "<链接>" --probe                                       # 只解析，不落盘
media-fetch "<链接>" --images                                      # 强制 gallery-dl
media-fetch "<链接>" --quality 720 --sleep 4                       # 限清晰度、限速防封
media-batch links.txt --cookies ~/.config/cookies/douyin.txt       # 批量文件
```

产物结构（与云盘一致）：

```
~/media-raw/01_小红书素材/<作者>/<日期>_<标题>_[<笔记ID>]/
├── xxxx.mp4 | 01.jpg 02.jpg …   原文件
├── *.info.json                  yt-dlp 抓到的完整元数据
└── meta.json                    本次采集记录（来源链接、工具、文件清单、字节数）
```

平台 → 目录映射：`01_小红书素材` `02_抖音素材` `03_YouTube素材` `04_其他平台素材` `05_B站素材` `06_快手素材` `07_TikTok素材` `08_Instagram素材`。

## 三条硬规则

1. **小红书 / 抖音必须带 cookie**：未登录基本只返回空数据或 `Fresh cookies are needed`。见 [docs/cookies.md](docs/cookies.md)。
2. **出口网络要通**：报 `TLS/SSL connection has been closed (EOF)` 就是被网络策略拦了，不是工具坏了 —— 换网络或加 `--proxy`。
3. **风控**：单 IP 高频批量会触发验证，每次间隔 3–5 秒；别开 20 并发。素材只做研究 / 参考 / 二创底稿，发布用二创，不直接搬运。

## github-fetch：网络受限时的备用通道

当出口只放行 `github.com` / `api.github.com` / `codeload.github.com`（很多云沙箱、公司内网如此），
国内平台和 YouTube 全部连不上，但仍可从社区仓库直取真实素材：

```bash
export GH_TOKEN=...                     # 只读即可；不带也行，但会被限流
github-fetch sangeetapatel/BubuandDudu "img/Bubu-Dudu-Loves-Full-Screen.mp4"
github-fetch jarvan703/yierbubu20260530 --include '\.(jpg|png)$' --zip
```

产物落到 `04_其他平台素材/GitHub/<owner>__<repo>/`，`meta.json` 带 commit、逐文件 sha256、许可证提醒。
这类素材是**社区转载**，不是平台原帖：来源可追溯，但不是官方原链接，商用前必须自己再过一遍版权。
