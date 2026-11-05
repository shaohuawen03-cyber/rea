# 小红书 / 抖音 登录态（cookie）导出

两个平台对未登录请求基本只回空数据。要稳定下载，必须先导出浏览器里的 cookie。

## 方式一：浏览器扩展（最快）

1. Chrome / Edge 装扩展 **Get cookies.txt LOCALLY**（开源，不上传数据）。
2. 打开并登录 `xiaohongshu.com`（或 `douyin.com`），确认首页能正常刷出内容。
3. 点扩展 → Export → 选 Netscape 格式，保存为：
   - `~/.config/cookies/xhs.txt`
   - `~/.config/cookies/douyin.txt`
4. 加权限，避免被别的进程读到：`chmod 600 ~/.config/cookies/*.txt`

## 方式二：yt-dlp 自带

```bash
# 直接从本机 Chrome 读（只在本机跑得通，沙箱里没有浏览器）
yt-dlp --cookies-from-browser chrome "<链接>"
# Firefox / Edge / Safari 同理；Linux 上 Chrome 可能需要 keyring
```

## 使用

```bash
media-fetch "<链接>" --cookies ~/.config/cookies/xhs.txt
media-fetch "<链接>" --cookies ~/.config/cookies/douyin.txt
```

`media-fetch` 会把 cookie 透传给 yt-dlp / gallery-dl；批量时用 `media-batch links.txt --cookies <文件>` 即可全程复用。

## 注意事项

- **cookie = 账号**。不要提交到 git、不要贴到聊天里、不要放进共享文件夹。泄露等于把账号交出去。
- 有效期通常几周。出现 `Fresh cookies are needed`、登录态失效、或下载到的都是低清预览图时，重新导出。
- 同一个 cookie 不要在多台机器上高频并发使用，容易触发风控。
- 登录态只用于**自己有权访问的内容**；付费、私密、需要授权的笔记不要抓。
