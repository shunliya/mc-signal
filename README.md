# 信令服务器公网部署指南

你们在不同网络，局域网IP无法互访。信令服务器需要部署到公网。

## 方案选择

| 方案 | 难度 | 费用 | 延迟 |
|------|------|------|------|
| 方案A: Railway | 简单 | 免费 | 低 |
| 方案B: 用已有的 natpierce | 最简单 | 免费 | 一般 |

---

## 方案A：Railway 免费部署（推荐）

### 1. 准备
- 注册 GitHub 账号: https://github.com
- 把这3个文件上传到 GitHub 新仓库:
  - signal_server.py
  - requirements.txt
  - Procfile

### 2. 部署
- 打开 https://railway.app
- 用 GitHub 登录
- New Project → Deploy from GitHub → 选你的仓库
- 自动部署，30秒完成

### 3. 获取地址
Railway 会生成地址如: `xxx.railway.app`
你和朋友在「信令服务器」栏填: `wss://xxx.railway.app`

注意是 `wss://` 不是 `ws://`（Railway 用 HTTPS）

---

## 方案B：用 natpierce 暴露端口（你已有）

你电脑上已有 natpierce.exe。添加一条映射:

- 本地IP: 127.0.0.1
- 本地端口: 9876
- 类型: TCP
- 远程端口: 自动分配

natpierce 会给你一个公网地址，朋友填那个即可。
