# Zotero 智能文献助手 -- Cherry Studio 配置指南

**[English](./cherry-studio-setup-en.md)** | **中文**

---

本指南面向零代码基础的用户。跟着步骤走，大约 10-15 分钟即可完成全部配置。
配置一次后无需任何维护，每次打开 Cherry Studio 直接使用。


## 你将获得什么

配置完成后，你可以在 Cherry Studio 中用自然语言操作你的 Zotero 文献库：

  - "帮我找关于城市公共服务可达性的论文"
  - "这篇论文里哪里讨论了研究方法？"
  - "我正在写的这段话能引用哪些文献？"
  - "帮我把这个 DOI 的论文加到库里"
  - "帮我总结这几篇论文写个文献综述"
  - "我的论点是XX，库里有哪些支持/反驳的证据？"
  - "推荐一下我接下来应该读什么"
  - "分析一下这几篇论文应该打什么标签"
  - "系统正常吗？检查一下连接和索引"


## 整体流程

第 1 步  安装 Python                         约 3 分钟
第 2 步  安装本项目                           约 2 分钟
第 3 步  配置 Zotero 连接                    约 2 分钟
第 4 步  在 Cherry Studio 中连接             约 3 分钟
第 5 步  开始使用


## 第 1 步：安装 Python（3.11 或更高版本）

检查是否已安装：

Windows（按 Win+R 输入 cmd 回车，在弹出窗口中运行）：
    python --version

    如果提示"不是内部或外部命令"，试试：
    python3 --version
    或
    py --version
    都不行就是没装。

macOS（打开"终端"，在启动台搜索"终端"）：
    python3 --version

如果显示 Python 3.11.x 或更高（如 3.12、3.13），跳到第 2 步。否则需要安装：

  Windows：
    1. 访问 https://www.python.org/downloads/
    2. 点击下载最新版
    3. 运行安装程序
       !! 第一个页面务必勾选底部的 "Add python.exe to PATH" !!
    4. 点 "Install Now"
    5. 安装完成后关闭并重新打开 cmd，再次运行 python --version 验证

  macOS：
    1. 如果已有 Homebrew：终端运行 brew install python
    2. 如果没有 Homebrew：先运行以下命令安装（会提示输入密码，输入时不会显示，正常现象）：
       /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    3. 然后运行 brew install python


## 第 2 步：安装本项目

### 2.1 先配置国内镜像（重要）

安装过程中需要下载约 347MB 的 AI 模型（ONNX INT8），该模型托管在 HuggingFace（国内
无法直连）。请在安装前先运行以下命令设置镜像：

macOS / Linux（终端中运行）：
    export HF_ENDPOINT=https://hf-mirror.com

Windows（cmd 中运行）：
    set HF_ENDPOINT=https://hf-mirror.com

注意：这个设置只在当前终端窗口有效。如果你关闭了终端再打开，需要重新设置。
后面第 3 步会把它写入配置文件，之后就不需要每次手动设置了。


### 2.2 安装

在同一个终端窗口中继续运行（不要关闭，保持上面的镜像设置生效）：

    pip install zra-mcp

注意：
  - 如果 pip 命令提示"未找到"，试试 pip3 install zra-mcp
  - macOS 用户通常需要用 pip3 而非 pip
  - 如果 pip 和 pip3 都找不到，说明第 1 步的 Python 没装好

如果需要知网检索功能（可选）：

    pip install "zra-mcp[cnki]"
    （或 pip3 install "zra-mcp[cnki]"）


### 2.3 验证安装

    pip show zra-mcp

如果显示包信息（名称、版本、位置），说明安装成功。

首次运行 zra-mcp 时会自动下载模型（镜像下约 3-5 分钟），下载完成后
后续启动不再需要下载。

如果镜像也很慢，可以直接拷贝别人已下载好的模型文件夹到以下位置：
    macOS:   ~/.cache/huggingface/hub/models--skatzR--USER-BGE-M3-ONNX-INT8/
    Windows: C:\Users\你的用户名\.cache\huggingface\hub\models--skatzR--USER-BGE-M3-ONNX-INT8\


## 第 3 步：配置 Zotero 连接

### 3.1 开启 Zotero 本地 API

1. 打开 Zotero 桌面端（需要 Zotero 7 或更高版本）
2. 菜单栏 -- 编辑 -- 设置（macOS: Zotero -- 首选项） -- 高级
3. 勾选 "Allow other applications on this computer to communicate with Zotero"
   （允许其他应用通过本地 API 访问 Zotero）
4. 验证：在浏览器地址栏输入 http://localhost:23119/api/ 回车
   - 如果看到一段 JSON 格式的文字 -- 开启成功
   - 如果看到"无法连接" -- 确认 Zotero 正在运行，且步骤 3 已勾选


### 3.2 获取 Zotero Web API Key（可选，启用写操作）

如果只需要搜索和阅读论文，这一步可以跳过。
如果还想通过 AI 添加论文、写笔记、管理标签，则需要配置。

1. 浏览器打开 https://www.zotero.org/settings/keys
2. 登录你的 Zotero 账号
3. 点 "Create new private key"
4. Key Description 随便填（如 research-assistant）
5. 勾选 Allow library access -- Allow write access
6. 点 Save Key，复制生成的 key（一长串字母数字，如 aB3xYz9...）
7. Library ID：在同一个页面顶部 userID 旁边的数字（如 12345678）


### 3.3 创建配置文件

pip 安装的用户需要在一个固定位置创建 .env 文件。推荐做法：

选择一个你喜欢的工作目录（不含中文和空格），例如：
  Windows: D:\research-tools\  或  E:\zotero-ai\
  macOS:   ~/research-tools/

在该目录下创建一个名为 .env 的文件（纯文本文件），内容如下：

    ZOTERO_LOCAL=true
    ZOTERO_LIBRARY_ID=12345678
    ZOTERO_API_KEY=aB3xYz9kLmN...
    HF_ENDPOINT=https://hf-mirror.com

说明：
  - ZOTERO_LOCAL=true：必填，表示使用本地 Zotero
  - ZOTERO_LIBRARY_ID：填你在 3.2 步骤 7 中看到的数字
  - ZOTERO_API_KEY：填你在 3.2 步骤 6 中复制的 key。如果跳过了 3.2 就留空
  - HF_ENDPOINT：国内镜像地址，加速模型下载（写入 .env 后无需每次手动设置）
  - 如果只读使用（搜索和阅读），只需写 ZOTERO_LOCAL=true 和 HF_ENDPOINT 两行即可

Windows 创建 .env 文件的方法：
  1. 打开你选定的目录
  2. 右键 -- 新建 -- 文本文档
  3. 把文件名改成 .env（注意去掉 .txt 后缀）
     如果看不到后缀，先在文件管理器中：查看 -- 勾选"文件扩展名"
  4. 右键 -- 打开方式 -- 记事本，输入上面的内容，保存

macOS 创建 .env 文件：
  在终端中运行（替换路径为你的工作目录）：
    cd ~/research-tools
    echo "ZOTERO_LOCAL=true" > .env

保存并关闭。记住你的 .env 文件所在目录，后面配置 Cherry Studio 时要用到。


## 第 4 步：在 Cherry Studio 中连接

### 4.1 安装 Cherry Studio

如果还没安装，从 https://cherry-ai.com/ 下载对应系统版本并安装。


### 4.2 配置 LLM（大语言模型）

Cherry Studio 需要一个 AI 模型来驱动对话。
在 Cherry Studio -- 设置 -- 模型服务 中配置你选择的模型和 API Key。

推荐模型：
  DeepSeek-V3     性价比高，中文好     https://platform.deepseek.com/
  Qwen2.5-72B     中文最强             https://dashscope.aliyun.com/
  Claude Sonnet   工具调用最准         https://console.anthropic.com/
  GPT-4o          综合稳定             https://platform.openai.com/


### 4.3 添加 MCP 服务器

1. 打开 Cherry Studio -- 设置 -- MCP 服务器
2. 点击"添加"，切换到 JSON 模式
