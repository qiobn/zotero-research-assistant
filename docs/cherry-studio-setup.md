# Zotero 智能文献助手 -- Cherry Studio 配置指南

本指南面向零代码基础的用户。跟着步骤走，大约 15-20 分钟即可完成全部配置。
配置一次后无需任何维护，每次打开 Cherry Studio 直接使用。


========================================
你将获得什么
========================================

配置完成后，你可以在 Cherry Studio 中用自然语言操作你的 Zotero 文献库：

  - "帮我找关于城市公共服务可达性的论文"
  - "这篇论文里哪里讨论了研究方法？"
  - "我正在写的这段话能引用哪些文献？"
  - "帮我把这个 DOI 的论文加到库里"
  - "帮我总结这几篇论文写个文献综述"
  - "我的论点是XX，库里有哪些支持/反驳的证据？"
  - "推荐一下我接下来应该读什么"
  - "分析一下这几篇论文应该打什么标签"


========================================
整体流程
========================================

第 1 步  安装基础工具（Python、uv、Git）     约 5 分钟
第 2 步  下载项目并安装依赖                  约 3 分钟
第 3 步  配置 Zotero 连接                    约 2 分钟
第 4 步  首次建立索引                        约 5-15 分钟（取决于库大小）
第 5 步  在 Cherry Studio 中连接             约 3 分钟
第 6 步  开始使用


========================================
准备工作：确认你的用户名和推荐安装位置
========================================

后续步骤需要用到你的系统用户名，先确认一下：

Windows：按 Win+R，输入 cmd 回车，在弹出的黑窗口中输入：

    echo %USERNAME%

会显示类似 zhangsan。你的用户目录就是 C:\Users\zhangsan

macOS：打开"终端"（在启动台搜索"终端"），输入：

    whoami

会显示类似 zhangsan。你的用户目录就是 /Users/zhangsan

注意：记住你的用户名，后面会多次用到。本指南所有示例以 zhangsan 为例，
你需要替换成自己的。

推荐安装位置：
  Windows :  C:\Users\zhangsan\zotero-research-agent
  macOS   :  /Users/zhangsan/zotero-research-agent

路径注意事项：安装路径不能包含中文、空格或特殊符号（如"我的文档"、
"Program Files"），否则会导致运行出错。


========================================
第 1 步：安装基础工具
========================================

-------- 1.1 安装 Python（3.11 或更高版本） --------

检查是否已安装：

Windows（在 cmd 中）：
    python --version

    如果提示"不是内部或外部命令"，试试 python3 --version 或 py --version。
    都不行就是没装。

macOS（在终端中）：
    python3 --version

如果显示 Python 3.11.x 或更高（如 3.12、3.13），跳到 1.2。否则需要安装：

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


-------- 1.2 安装 uv（Python 包管理器） --------

macOS / Linux（终端中运行）：
    curl -LsSf https://astral.sh/uv/install.sh | sh

Windows（按 Win+R 输入 powershell 回车，在 PowerShell 中运行）：
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

安装完成后关闭终端并重新打开，验证：
    uv --version

应显示版本号（如 uv 0.7.x）。如果提示"未找到命令"，关闭所有终端窗口重新打开再试。


-------- 1.3 安装 Git（如果没有） --------

    git --version

如果提示未安装：
  Windows：访问 https://git-scm.com/downloads/win ，下载安装，全部默认下一步即可
  macOS：终端运行 xcode-select --install，在弹窗中点"安装"


========================================
国内网络注意事项
========================================

以下步骤可能受网络环境影响，提前了解可以避免卡住：

1. git clone（从 GitHub 下载项目）
   - 如果 GitHub 访问慢或超时：
     方案A：开代理后再运行 git clone
     方案B：让能访问的人下载 zip 发给你，解压到推荐位置即可
     方案C：用 Gitee 等国内镜像（如果有的话）

2. 安装 uv
   - astral.sh 域名国内偶尔不通
   - 替代方案：先跳过 uv，直接用 pip：
     python -m pip install uv

3. 下载嵌入模型（约 2.3GB，最容易卡住的一步）
   - HuggingFace 在国内经常无法直连
   - 解决方案（二选一）：
     a. 设置镜像（推荐）：
        macOS:   export HF_ENDPOINT=https://hf-mirror.com
        Windows: set HF_ENDPOINT=https://hf-mirror.com
        设置后再运行安装命令
     b. 找已经下载好的人拷贝模型文件夹（见第 2.2 步说明）

4. 日常使用时的在线搜索
   - OpenAlex、CrossRef、Semantic Scholar 这些学术 API 国内通常可直连，不需要代理
   - LLM 模型 API：选 DeepSeek 或 Qwen 不需要代理；选 OpenAI 或 Claude 需要

总结：如果你的网络能正常访问 GitHub，基本只需要在下载模型时设置一下
HF_ENDPOINT 镜像就行。如果 GitHub 也访问不了，让能访问的人帮你把项目
文件夹和模型文件夹拷过来即可。


========================================
第 2 步：下载项目并安装依赖
========================================

-------- 2.1 下载项目 --------

macOS（终端中运行）：
    cd ~
    git clone https://github.com/qiobn/zotero-research-agent.git
    cd zotero-research-agent

    项目会下载到 /Users/zhangsan/zotero-research-agent

Windows（cmd 中运行）：
    cd %USERPROFILE%
    git clone https://github.com/qiobn/zotero-research-agent.git
    cd zotero-research-agent

    项目会下载到 C:\Users\zhangsan\zotero-research-agent


-------- 2.2 创建虚拟环境并安装依赖 --------

在上一步的终端中继续运行（不要关闭）：

    uv venv .venv --python 3.13

    如果报错 No interpreter found for Python 3.13，
    把 3.13 换成你安装的版本号（如 3.12 或 3.11）。

然后安装依赖：

    uv pip install -e .

等待完成。首次运行时会自动下载嵌入模型（约 2.3GB），可能需要几分钟。

    下载太慢？ 问师兄/师姐拷贝模型文件夹，放到以下位置：
    macOS:   /Users/zhangsan/.cache/huggingface/hub/models--BAAI--bge-m3/
    Windows: C:\Users\zhangsan\.cache\huggingface\hub\models--BAAI--bge-m3\
    拷贝完后重新运行 uv pip install -e . 即可跳过下载。


-------- 2.3 验证安装 --------

macOS:
    source .venv/bin/activate
    python -c "from project_a_mcp.server import mcp; print('安装成功')"

Windows:
    .venv\Scripts\activate
    python -c "from project_a_mcp.server import mcp; print('安装成功')"

如果输出"安装成功"，说明一切就绪。如果报错，检查上面的步骤是否有遗漏。


========================================
第 3 步：配置 Zotero 连接
========================================

-------- 3.1 开启 Zotero 本地 API --------

1. 打开 Zotero 桌面端（需要 Zotero 7 或更高版本）
2. 菜单栏 -- 编辑 -- 设置 -- 高级
3. 勾选 "Allow other applications on this computer to communicate with Zotero"
4. 验证：在浏览器地址栏输入 http://localhost:23119/api/ 回车
   - 如果看到一段 JSON 格式的文字 -- 开启成功
   - 如果看到"无法连接" -- 确认 Zotero 正在运行，且步骤 3 已勾选


-------- 3.2 获取 Zotero Web API Key（可选，启用写操作） --------

如果只需要搜索和阅读论文，这一步可以跳过。
如果还想通过 AI 添加论文、写笔记、管理标签，则需要配置。

1. 浏览器打开 https://www.zotero.org/settings/keys
2. 登录你的 Zotero 账号
3. 点 "Create new private key"
4. Key Description 随便填（如 research-assistant）
5. 勾选 Allow library access -- Allow write access
6. 点 Save Key，复制生成的 key（一长串字母数字，如 aB3xYz9...）
7. Library ID：在同一个页面顶部 userID 旁边的数字（如 12345678）


-------- 3.3 创建配置文件 --------

macOS / Linux：
    cd ~/zotero-research-agent
    cp .env.example .env

Windows：
    cd %USERPROFILE%\zotero-research-agent
    copy .env.example .env

然后用文本编辑器打开 .env 文件：
  Windows：在文件管理器中找到 C:\Users\zhangsan\zotero-research-agent 文件夹，
           右键 .env 文件 -- 打开方式 -- 记事本
  macOS：终端运行 open -e ~/zotero-research-agent/.env

找到以下几行，修改为你的信息：

    ZOTERO_LOCAL=true
    ZOTERO_LIBRARY_ID=12345678
    ZOTERO_API_KEY=aB3xYz9kLmN...

说明：
  - ZOTERO_LIBRARY_ID：填你在 3.2 步骤 7 中看到的数字
  - ZOTERO_API_KEY：填你在 3.2 步骤 6 中复制的 key。如果跳过了 3.2 就留空
  - 其他所有配置不要修改，保持默认

保存并关闭。


========================================
第 4 步：首次建立索引
========================================

这一步会读取你 Zotero 库中所有 PDF 的全文，切成小段并生成语义向量，
存入本地数据库。只需做一次，之后每次使用时会自动增量同步。

确保 Zotero 桌面端正在运行，然后：

macOS / Linux：
    cd ~/zotero-research-agent
    source .venv/bin/activate
    python scripts/index_library.py

Windows：
    cd %USERPROFILE%\zotero-research-agent
    .venv\Scripts\activate
    python scripts/index_library.py

    Windows 常见问题：如果 .venv\Scripts\activate 报错"无法加载文件...
    因为在此系统上禁止运行脚本"，请用 PowerShell 运行以下命令后重试：
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

你会看到类似输出：

    Indexing: Processing paper 1/150 ...
    Indexing: Processing paper 2/150 ...
    ...
    Done: 150 added, 0 updated, 0 skipped, 0 failed

耗时参考：100 篇论文约 3-5 分钟，500 篇约 10-15 分钟。


========================================
第 5 步：在 Cherry Studio 中连接
========================================

-------- 5.1 安装 Cherry Studio --------

如果还没安装，从 https://cherry-ai.com/ 下载对应系统版本并安装。


-------- 5.2 配置 LLM（大语言模型） --------

Cherry Studio 需要一个 AI 模型来驱动对话。
在 Cherry Studio -- 设置 -- 模型服务 中配置你选择的模型和 API Key。

推荐模型：
  DeepSeek-V3     性价比高，中文好     https://platform.deepseek.com/
  Qwen2.5-72B     中文最强             https://dashscope.aliyun.com/
  Claude Sonnet   工具调用最准         https://console.anthropic.com/
  GPT-4o          综合稳定             https://platform.openai.com/


-------- 5.3 添加 MCP 服务器 --------

1. 打开 Cherry Studio -- 设置 -- MCP 服务器
2. 点击"添加"，切换到 JSON 模式
3. 粘贴以下 JSON（选你的操作系统，把 zhangsan 替换为你的用户名）：

macOS 示例：

    {
      "mcpServers": {
        "zra-mcp": {
          "command": "/Users/zhangsan/zotero-research-agent/.venv/bin/python",
          "args": ["-m", "project_a_mcp.server"],
          "cwd": "/Users/zhangsan/zotero-research-agent"
        }
      }
    }

Windows 示例：

    {
      "mcpServers": {
        "zra-mcp": {
          "command": "C:\\Users\\zhangsan\\zotero-research-agent\\.venv\\Scripts\\python.exe",
          "args": ["-m", "project_a_mcp.server"],
          "cwd": "C:\\Users\\zhangsan\\zotero-research-agent"
        }
      }
    }

!! 必须修改的地方：把 zhangsan 替换为你自己的用户名 !!

路径验证（粘贴前先确认 command 指向的 python 文件存在）：
  macOS:   终端运行  ls /Users/zhangsan/zotero-research-agent/.venv/bin/python
  Windows: cmd 运行  dir C:\Users\zhangsan\zotero-research-agent\.venv\Scripts\python.exe

如果显示文件信息则路径正确。如果提示"没有那个文件"，说明项目安装位置不同。

快速获取正确路径：在项目目录下运行以下命令：
  macOS:   echo "$(pwd)/.venv/bin/python"
  Windows: echo %cd%\.venv\Scripts\python.exe

4. 保存配置


-------- 5.4 验证连接 --------

确保 Zotero 桌面端正在运行，然后在 Cherry Studio 中新建一个对话，输入：

    列出我 Zotero 库里的所有收藏夹

如果 AI 返回了你的收藏夹列表，恭喜，配置完成！

连接失败排查：
  1. Zotero 是否在运行？
  2. JSON 配置中的路径是否正确？（Windows 注意用 \\ 双反斜杠）
  3. Cherry Studio 是否已重启？（修改 MCP 配置后建议重启）
  4. 终端进入项目目录手动运行 python -m project_a_mcp.server 看是否有报错


========================================
日常使用
========================================

-------- 每次使用前 --------

1. 打开 Zotero 桌面端（保持运行）
2. 打开 Cherry Studio，正常对话即可

不需要打开终端、不需要手动启动服务、不需要手动同步索引。
Cherry Studio 会自动启动 MCP 服务，服务启动时会自动做增量同步。


-------- 使用场景和示例 --------

[搜索文献]

    帮我找关于"15分钟城市"的论文
    找一下 2020 年之后关于步行可达性的文献
    搜索标记了"方法论"标签的论文
    找跟这篇论文类似的其他论文
    在线搜索关于 urban green infrastructure 的最新文献
    帮我搜知网上关于"地理探测器"的中文论文

[阅读论文]

    这篇论文讲了什么？
    这篇论文里关于"研究方法"的部分说了什么？
    给我看看这篇论文的目录结构
    我在所有论文上标注过关于"GIS"的内容，帮我找找

[文献综述与论据]

    帮我综合这几篇论文写一个关于"研究方法演变"的文献综述
    我的论点是"公共服务设施分布不均"，帮我从库里找支持和反驳的证据
    把这 5 篇论文中关于"数据来源"的信息提取出来对比一下

[写论文时找引用]

    我正在写论文，下面这段话需要引用支撑：
    "步行可达性是衡量城市生活质量的重要指标..."
    帮我从库里找合适的引用。
    找到引用后，帮我导出 BibTeX。

[添加新论文]

    帮我把这篇论文加到库里：10.1016/j.cities.2025.105902
    添加这篇 arXiv 论文：2301.00001

[管理文献库]

    给这几篇论文打上"核心文献"标签
    创建一个叫"毕业论文参考"的收藏夹
    检查一下有没有重复的论文
    给这篇论文写一条阅读笔记

[阅读状态与推荐]

    我最近都读了哪些论文？哪些还没读？
    根据我最近的阅读，推荐一下我接下来应该读什么
    分析一下这几篇论文应该打什么标签


-------- 使用技巧 --------

1. 先搜后读：先搜索找到论文，再针对具体某篇提问
2. 引用工作流：把你写的段落粘给 AI -- 选择合适的引用 -- 导出 BibTeX
3. 写操作是安全的：添加论文、写笔记、改标签等操作默认只做预览，
   AI 会让你确认后才真正执行
4. 中英文混搜：可以用中文搜英文论文，反之亦然
5. 不需要手动同步：如果刚加了论文想立刻搜到，可以说"同步一下索引"
6. 文献综述：选定几篇论文后让 AI 写综述，AI 会按主题串联而非逐篇总结
7. 论据搜索：写 Discussion 时，把论点告诉 AI，它会自动分类支持/反驳证据


========================================
可用功能一览（28 个工具）
========================================

搜索与发现
  search_papers           在本地库中搜索论文（关键词+语义混合）
  search_online_literature 在线搜索英文文献（OpenAlex+CrossRef+S2）
  search_cnki_literature  知网中文文献搜索（需额外配置）
  find_related_literature 给定一篇论文，找相关文献（多策略并行）
  expand_citation_network 引文网络展开（谁引了它/它引了谁）
  find_similar_papers     找本地库中语义相似的论文
  browse_library          浏览收藏夹、标签、最近添加
  find_duplicates         查找重复论文
  merge_duplicates        合并重复项

阅读与批注
  get_paper               获取论文元数据和摘要
  get_paper_content       阅读论文内容（语义搜索/按页/全文/目录）
  search_annotations      跨论文搜索你的批注和高亮
  create_annotation       在 PDF 上创建高亮批注

写作辅助
  suggest_citations       为你的草稿段落推荐引用
  export_bibliography     导出 BibTeX 或格式化引用
  add_paper               通过 DOI/arXiv/ISBN/URL 添加论文
  cnki_add_to_zotero      从知网直接导入论文到 Zotero

管理文献库
  add_note                给论文添加阅读笔记
  edit_tags               批量管理标签
  manage_collections      创建和管理收藏夹

智能分析（本项目特色）
  reading_status          分析阅读进度（深度阅读/浏览过/未读）
  recommend_papers        根据你的阅读习惯推荐论文
  generate_review_note    多论文文献综述生成（按主题串联，非逐篇总结）
  suggest_tags            智能推荐标签（方法论/领域/数据类型）
  find_arguments          论据搜索器（找支持/反驳你论点的证据）

知网专用（可选，需额外配置）
  cnki_paper_detail       获取知网论文详细信息
  cnki_navigate_pages     知网搜索结果翻页

系统维护
  sync_index              同步向量索引（通常自动运行）


========================================
常见问题
========================================

Q: 搜不到我刚加的论文？
A: 对 AI 说"同步一下索引"即可。正常情况下每次启动 MCP 服务会自动同步。

Q: 添加论文/写笔记时报错？
A: 需要在 .env 里配置 ZOTERO_API_KEY（第 3.2 步）。
   没有 API Key 时只能使用搜索和阅读功能。

Q: 索引时报错 "Connection refused"？
A: 确保 Zotero 桌面端正在运行，且已开启本地 API（第 3.1 步）。

Q: 安装依赖时下载很慢？
A: 嵌入模型（bge-m3）约 2.3GB，首次下载较慢。两个解决办法：
   1. 问师兄/师姐拷贝模型文件夹到你的机器上（见第 2.2 步）
   2. 设置镜像：
      macOS:   export HF_ENDPOINT=https://hf-mirror.com
      Windows: set HF_ENDPOINT=https://hf-mirror.com
      然后重新安装

Q: Windows 上 .venv\Scripts\activate 报错"禁止运行脚本"？
A: 在 PowerShell 中运行一次：
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   然后重新执行。

Q: Windows 上 python 命令提示"不是内部或外部命令"？
A: 试试 python3 --version 或 py --version。
   都不行说明安装 Python 时没勾选 "Add python.exe to PATH"，重新安装并勾选。

Q: Cherry Studio 里 AI 没有调用工具？
A: 检查：
   1. 对话设置中是否已开启 MCP 工具调用
   2. 模型是否支持 Function Calling（推荐的四个模型都支持）
   3. MCP 服务器状态是否显示为"已连接"

Q: 师兄/师姐更新了代码，我怎么同步？
A: 在项目目录下拉取最新代码即可：
   macOS:   cd ~/zotero-research-agent && git pull
   Windows: cd %USERPROFILE%\zotero-research-agent && git pull
   然后重启 Cherry Studio 的对话即可生效。
   如果更新后提示缺少依赖，额外运行一次：
   macOS:   .venv/bin/pip install -e .
   Windows: .venv\Scripts\pip install -e .

Q: 换了电脑怎么办？
A: 在新电脑上重新走一遍第 1-5 步即可（约 15 分钟）。
   你的 Zotero 文献库通过 Zotero 账号同步，索引会从本地 PDF 重新生成。


========================================
关于知网（CNKI）检索模块
========================================

知网检索模块默认处于关闭状态，不影响其他所有功能的正常使用。

-------- 为什么默认关闭 --------

  - 知网没有公开 API，该模块通过浏览器自动化实现，稳定性较差
  - 需要额外安装 Playwright 浏览器自动化依赖
  - 需要你手动登录知网并保持 Chrome 浏览器在后台运行
  - 知网可能随时变更页面结构导致功能失效
  - 操作不当可能触发知网反爬机制

-------- 如果你需要搜索中文文献 --------

大多数情况下，在线英文搜索（OpenAlex/CrossRef/S2）已经能覆盖主要的中英文
学术论文。如果你确实需要搜索知网上的中文期刊论文，直接在对话中问 AI：

    "知网检索模块怎么启动？"

AI 会根据你的操作系统给出完整的启动步骤指引，包括：
  1. 安装额外依赖
  2. 启动带调试端口的 Chrome 浏览器
  3. 登录知网账号
  4. 修改配置文件启用模块

-------- 风险提示 --------

  - 该模块依赖浏览器自动化，不如其他功能稳定
  - 知网页面结构变化可能导致功能临时失效
  - 频繁调用可能触发知网验证码或临时封禁
  - 建议仅在确实需要时启用，用完后可随时关闭（在 .env 中设 CNKI_ENABLED=false）
