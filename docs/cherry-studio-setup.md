# Zotero 智能文献助手 — Cherry Studio 配置指南

**[English](./cherry-studio-setup-en.md)** | **中文**

本指南帮助你用自然语言在 Cherry Studio 中搜索、阅读、管理 Zotero 文献库。
零代码基础可操作。

**耗时：** 10–15 分钟。配置一次，永久使用。

---

## 配置后可以做什么

- "帮我找关于城市绿地与公共健康的论文"
- "这篇论文的研究方法是什么？"
- "帮我把这 5 篇论文总结成文献综述"
- "我写的这段话能引用哪些论文？"
- "把这个 DOI 加到我的文库：10.1016/j.cities.2025.105902"
- "根据我的阅读记录推荐接下来读什么"
- "检查一下系统是否正常"

---

## 第 1 步：安装 Python

### 先检查是否已安装

打开终端（Windows: `cmd`，macOS: `终端`），输入：

```
python --version
```

如果显示 **Python 3.11 或更高版本**（如 3.12、3.13），跳到第 2 步。

### 没装的话

**Windows：**
1. 打开 https://www.python.org/downloads/ 下载最新版
2. 运行安装程序，**第一页务必勾选 "Add python.exe to PATH"**
3. 点 "Install Now"，完成后关掉终端重新打开

**macOS：**
```
brew install python
```

---

## 第 2 步：安装 zra-mcp

打开终端，运行：

```
pip install zra-mcp
```

> 如果提示找不到 pip，试试 `pip3 install zra-mcp`（macOS 常见）。
> 首次运行会自动下载约 347MB 的 AI 模型，仅需下载一次。

### 验证安装

```
pip show zra-mcp
```

显示包信息即表示安装成功。

### 国内用户：先配置镜像

模型托管在 HuggingFace，国内可能较慢。先设置镜像：

**macOS / Linux（终端）：**
```
export HF_ENDPOINT=https://hf-mirror.com
```

**Windows（cmd）：**
```
set HF_ENDPOINT=https://hf-mirror.com
```

然后运行 `pip install zra-mcp`。之后第 3 步会写入 `.env` 文件，就不需要每次手动设置了。

---

## 第 3 步：配置 Zotero

### 3.1 开启 Zotero 本地 API

1. 打开 **Zotero 桌面端**（需要 7.0 或更高版本）
2. **编辑 → 设置 → 高级**（macOS: Zotero → 首选项 → 高级）
3. 勾选 **"Allow other applications on this computer to communicate with Zotero"**
4. 验证：浏览器打开 http://localhost:23119/api/，应该看到 JSON 格式的文字

### 3.2 创建配置文件（.env）

选一个文件夹存放配置——比如 `D:\zotero-ai`（Windows）或 `~/zotero-ai`（macOS）。

在该文件夹下创建一个名为 **`.env`** 的文件，内容如下：

```
ZOTERO_LOCAL=true
HF_ENDPOINT=https://hf-mirror.com
```

> 如果你还想通过 AI **添加论文、写笔记、管理标签**，需要先获取 Zotero API Key：
> 1. 打开 https://www.zotero.org/settings/keys 并登录
> 2. 点 "Create new private key"，勾选 "Allow write access"
> 3. 复制生成的 key，记下页面顶部的 userID 数字
> 4. 在 `.env` 文件中追加两行：
> ```
> ZOTERO_LIBRARY_ID=12345678
> ZOTERO_API_KEY=你的key
> ```
> （把 `12345678` 换成你的 userID 数字）

**如何创建 .env 文件：**
- **Windows：** 在文件夹里右键 → 新建 → 文本文档，改名为 `.env`（删掉 `.txt` 后缀），用记事本编辑
- **macOS：** 终端运行 `cd ~/zotero-ai && echo "ZOTERO_LOCAL=true" > .env`

---

## 第 4 步：连接 Cherry Studio

### 4.1 安装 Cherry Studio

从 https://cherry-ai.com/ 下载并安装。

### 4.2 配置 AI 模型

打开 **Cherry Studio → 设置 → 模型服务**，添加一个模型及其 API Key。

推荐模型：
- **DeepSeek-V3** — 性价比最高，中文优秀
- **Claude Sonnet** — 工具调用最可靠
- **GPT-4o** — 综合稳定

### 4.3 添加 MCP 服务器

打开 **Cherry Studio → 设置 → MCP 服务器**，点击 **添加**，填写：

| 字段 | 值 |
|------|-----|
| 名称 | `zra-mcp` |
| 描述 | `Zotero Research Assistant` |
| 命令 | `zra-mcp` |
| 参数 | *（留空）* |
| 环境变量 | *（留空——已在 .env 文件中设置）* |

点击 **保存**。

> **源码安装？** 如果你是 clone 源码而不是 pip 安装的，填写方式不同：
> - **命令：** Python 的完整路径（如 `D:\project\.venv\Scripts\python.exe` 或 `/home/user/project/.venv/bin/python`）
> - **参数：** `-m`，`project_a_mcp.server`

> **需要用 JSON 导入？** 如果 Cherry Studio 要求粘贴 JSON，使用以下格式：
> ```json
> {
>   "mcpServers": {
>     "zra-mcp": {
>       "name": "zra-mcp",
>       "description": "Zotero Research Assistant",
>       "baseUrl": "",
>       "command": "zra-mcp",
>       "args": [],
>       "env": {},
>       "isActive": true
>     }
>   }
> }
> ```
> 源码安装：把 `"command": "zra-mcp"` 替换为 Python 路径，并在 `"args"` 中添加 `["-m", "project_a_mcp.server"]`。

### 4.4 验证连接

1. 确保 **Zotero 桌面端正在运行**
2. 打开 Cherry Studio，新建对话
3. 输入：**"列出我 Zotero 库里的所有分组"**
4. 如果 AI 返回了你的分组列表——配置成功！

**如果连不上？**
- Zotero 是否在运行？（窗口必须开着，不是最小化到托盘）
- 添加 MCP 服务器后是否重启了 Cherry Studio？
- 在终端直接运行 `zra-mcp` 看是否有报错

---

## 日常使用

1. 打开 Zotero 桌面端
2. 打开 Cherry Studio，正常对话

MCP 服务自动启动，索引自动同步。不需要开终端。

> **首次使用注意：** 初次索引需要时间——大约每 100 篇论文 3–10 分钟。
> 它在后台自动跑，索引未完成时搜索可能不全，属正常现象。

---

## 使用技巧

### 搜索
| 你说 | 效果 |
|------|------|
| "帮我找关于引力模型的论文" | 按主题搜索你的文库 |
| "显示 2020-2024 年标记了方法论的论文" | 按年份和标签筛选 |
| "找跟这篇类似的论文" | 语义相似度搜索 |
| "在线搜索城市绿地的最新英文论文" | 搜索 OpenAlex + CrossRef + Semantic Scholar |

### 阅读
| 你说 | 效果 |
|------|------|
| "这篇论文讲了什么？" | 返回元数据 + 摘要 |
| "这篇论文里关于研究方法的段落" | 在论文 PDF 内搜索 |
| "显示这篇论文的全文" | 返回完整文本（最多 50 页） |
| "找我所有标注里关于 GIS 的内容" | 跨论文搜索你的批注 |

### 写作与整理
| 你说 | 效果 |
|------|------|
| "把这个 DOI 加到我的文库" | 导入论文元数据 + 下载 PDF |
| "我写的这段话能引用哪些文献" | 匹配文库中的引用建议 |
| "导出这些论文的 BibTeX" | 格式化参考文献 |
| "给这几篇论文打上核心文献标签" | 批量标签（先预览再确认） |
| "把这几篇论文总结成文献综述" | 生成综述材料 |
| "推荐我接下来读什么" | 个性化推荐 |

---

## 常见问题

**Q: 搜不到刚加的论文？**
告诉 AI "同步索引"。新加的 PDF 需要先索引。

**Q: 无法添加论文 / 写笔记？**
需要配置 Zotero API Key——见第 3.2 步。没有的话只能搜索和阅读。

**Q: 提示"Connection refused"？**
Zotero 桌面端必须正在运行。检查第 3.1 步。

**Q: 模型下载很慢？**
设置 HF 镜像——见第 2 步。或者找已下载好的朋友拷贝模型文件夹：
```
~/.cache/huggingface/hub/models--skatzR--USER-BGE-M3-ONNX-INT8/
```

**Q: 怎么检查系统是否正常？**
对 AI 说 "检查系统健康"。

**Q: 如何更新？**
```
pip install --upgrade zra-mcp
```
然后重启 Cherry Studio。
