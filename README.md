# Gemini CLI -> Langfuse 可观测性追踪插件 (Gemini CLI Extension)

本项目为 **Google Gemini CLI** 官方扩展插件，提供全自动、全生命周期的 LLM 会话追踪与可观测性集成。

---

## 1. 特性

- 🚀 **即装即用**：支持通过 `gemini extensions install` 一键安装，自动注册生命周期 Hook。
- 🔑 **交互式密钥管理**：安装时自动引导输入 Langfuse 密钥，私钥自动保存在系统安全钥匙串（Keychain）中。
- 🎯 **项目隔离 (Per-Project)**：支持在项目根目录下放置 `langfuse.json`，自动覆盖全局配置，多项目互不串台。
- 📊 **深度追踪**：
  - 完整会话 Trace 树（Turn -> Generation -> Tool Calls）。
  - 精确统计 Input / Output / Cached / Thoughts Token 用量。
  - 自动捕获思维链（Chain-of-Thought / Thoughts）。
  - 记录所有 CLI 工具调用（Bash 命令、文件读写、搜索等）的入参与返回值。

---

## 2. 安装方法（面向其他用户）

### 方式 A：通过 Git 仓库一键安装（最推荐）

当将本目录推送到 GitHub 后，任何人只需在终端执行一行命令：

```bash
gemini extensions install https://github.com/<你的用户名>/gemini-langfuse
```

CLI 会在终端中**自动弹出交互式配置项**：
1. `Langfuse Base URL`：输入服务端地址（如 `http://localhost:3000` 或 `https://cloud.langfuse.com`）。
2. `Langfuse Public Key`：输入公钥（`pk-lf-...`）。
3. `Langfuse Secret Key`：输入私钥（`sk-lf-...`，安全存入操作系统 Keychain）。

### 方式 B：本地安装 / 源码安装

```bash
# 1. 下载或克隆代码到本地
git clone https://github.com/<你的用户名>/gemini-langfuse.git ~/gemini-langfuse

# 2. 本地链接到 Gemini CLI 扩展管理器
gemini extensions link ~/gemini-langfuse

# 3. 配置密钥
gemini extensions config gemini-langfuse
```

---

## 3. 环境准备

本插件依赖 Python 3 及 `langfuse` SDK：
```bash
pip install "langfuse>=4.0.0"
```

---

## 4. 日常配置与管理命令

```bash
# 查看已安装扩展
gemini extensions list

# 重新配置或修改密钥
gemini extensions config gemini-langfuse

# 临时禁用插件
gemini extensions disable gemini-langfuse

# 重新启用插件
gemini extensions enable gemini-langfuse

# 卸载插件
gemini extensions uninstall gemini-langfuse
```

---

## 5. 项目级配置覆盖 (`langfuse.json`)

如果你在某个特定项目中需要上报到专属的 Langfuse 空间，只需在该项目根目录下新建 `langfuse.json`：

```json
{
  "publicKey": "pk-lf-项目专属公钥",
  "secretKey": "sk-lf-项目专属私钥",
  "baseUrl": "http://localhost:3000",
  "enabled": true
}
```
*优先级：项目 `langfuse.json` > 扩展全局配置 (`gemini extensions config`)*

---

## 6. 扩展目录结构与原理

```text
gemini-langfuse/
├── gemini-extension.json       # 官方扩展清单文件（声明插件元数据及交互式配置项）
├── hooks/
│   └── hooks.json              # 钩子声明（通过 ${extensionPath} 动态定位执行路径）
├── langfuse_hook.py            # Hook 执行入口（Fail-open 容错设计，严格 JSON 通信）
├── transcript_parser.py        # Gemini CLI 原生 session-*.jsonl 会话日志解析器
├── tracer.py                   # Langfuse v4 OpenTelemetry 链路导出引擎
├── state_manager.py            # 增量追踪状态管理（防重复打点）
├── sync.py                     # 独立运维 CLI（历史补录、连通性检查）
├── test_parser.py              # 本地测试工具
└── README.md
```
