#!/usr/bin/env bash
# 素材下载工具箱 · 一键安装
#
# 用途：把「链接 → 视频 / 图文原文件」这件事变成一条命令：media-fetch "<链接>"
# 组成：
#   1. yt-dlp          —— 视频主力（YouTube / B站 / 抖音 / 小红书 / 微博 / TikTok / IG …）
#   2. gallery-dl      —— 图文主力（小红书笔记多图、Instagram、Pinterest、微博 …）
#   3. imageio-ffmpeg  —— 自带 ffmpeg，用于合并音视频、转码、抽封面（免 apt / brew）
#   4. 可选第三方项目   —— XHS-Downloader / TikTokDownloader / 抖音批量下载（按需克隆）
#
# 设计约束：不装系统包、不动 PATH 以外的全局环境；全部装在 ~/.local/media-toolkit。
set -euo pipefail

ROOT="${MEDIA_TOOLKIT_HOME:-$HOME/.local/media-toolkit}"
VENV="$ROOT/venv"
BIN="$ROOT/bin"
SRC="$ROOT/src"
mkdir -p "$BIN" "$SRC"

say() { printf '\033[1;36m[media-toolkit]\033[0m %s\n' "$*"; }

# ---------- 1. Python 虚拟环境 ----------
if [ ! -x "$VENV/bin/python" ]; then
  say "创建 venv：$VENV"
  python3 -m venv "$VENV"
fi
say "安装 yt-dlp / gallery-dl / imageio-ffmpeg"
"$VENV/bin/pip" -q install --upgrade pip
"$VENV/bin/pip" -q install --upgrade yt-dlp gallery-dl imageio-ffmpeg

# ---------- 1.5 合并系统 CA ----------
# certifi 只带公共根证书；公司/云沙箱的中间代理证书在系统仓库里。
# 合并一次，yt-dlp / gallery-dl 才能正常走代理出网（幂等，带标记）。
if [ "${MEDIA_TOOLKIT_MERGE_CA:-1}" = "1" ] && [ -f /etc/ssl/certs/ca-certificates.crt ]; then
  CERTIFI=$("$VENV/bin/python" -c 'import certifi;print(certifi.where())' 2>/dev/null || true)
  if [ -n "$CERTIFI" ] && ! grep -q "media-toolkit:system-ca" "$CERTIFI"; then
    { printf '\n# media-toolkit:system-ca\n'; cat /etc/ssl/certs/ca-certificates.crt; } >> "$CERTIFI"
    say "已把系统 CA 合并进 certifi：$CERTIFI"
  fi
fi

# ---------- 2. 命令入口 ----------
ln -sf "$VENV/bin/yt-dlp" "$BIN/yt-dlp"
ln -sf "$VENV/bin/gallery-dl" "$BIN/gallery-dl"
install -m 0755 "$(dirname "$0")/media-fetch" "$BIN/media-fetch"
install -m 0755 "$(dirname "$0")/media-batch" "$BIN/media-batch" 2>/dev/null || true
install -m 0755 "$(dirname "$0")/github-fetch" "$BIN/github-fetch"

say "版本：yt-dlp $("$BIN/yt-dlp" --version) / gallery-dl $("$BIN/gallery-dl" --version)"
say "ffmpeg：$("$VENV/bin/python" -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')"

# ---------- 3. 可选：第三方专用下载器 ----------
# 国内平台风控严，官方 CLI 有时解析不到；这几个项目是社区维护的专用方案。
# 只克隆源码，不做 pip 安装，避免污染环境；用法见各自 README。
if [ "${MEDIA_TOOLKIT_VENDORS:-0}" = "1" ]; then
  clone() { # $1=repo $2=dir
    if [ -d "$SRC/$2/.git" ]; then say "已存在：$2"; else say "克隆 $1"; git clone --depth 1 "https://github.com/$1.git" "$SRC/$2"; fi
  }
  clone JoeanAmier/XHS-Downloader          xhs-downloader
  clone JoeanAmier/TikTokDownloader        tiktok-downloader   # 抖音/TikTok 双端
  clone jiji262/douyin-downloader          douyin-downloader   # 抖音单条/主页批量
  clone NanmiCoder/MediaCrawler            media-crawler       # 小红书/抖音/B站/微博 关键词爬取
fi

cat <<EOF

安装完成。把 $BIN 加进 PATH 即可全局使用：

  export PATH="$BIN:\$PATH"

  media-fetch "https://www.xiaohongshu.com/explore/xxxxx"       # 单条（自动判断视频/图文）
  media-fetch "<链接1>" "<链接2>" --cookies ~/.config/cookies/xhs.txt
  media-fetch "<链接>" --probe                                   # 只解析不下载

注意：小红书 / 抖音必须带登录 cookie，否则只返回空数据。导出方法见 docs/cookies.md。
EOF
