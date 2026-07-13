# Zotero 智能文献助手 -- Cherry Studio 配置指南

**[English](./cherry-studio-setup-en.md)** | **中文**

---

本指南面向零代码基础的用户。跟着步骤走，大约 10-15 分钟即可完成全部配置。
配置一次后无需任何维护，每次打开 Cherry Studio 直接使用。


你将获得什么

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


整体流程

第 1 步  安装 Python                         约 3 分钟
第 2 步  安装本项目                           约 2 分钟
第 3 步  配置 Zotero 连接                    约 2 分钟
第 4 步  在 Cherry Studio 中连接             约 3 分钟
第 5 步  开始使用


第 1 步：安装 Python（3.11 或更高版本）

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


第 2 步：安装本项目

-------- 2.1 先配置国内镜像（重要） --------

安装过程中需要下载约 347MB 的 AI 模型（ONNX INT8），该模型托管在 HuggingFace（国内
无法直连）。请在安装前先运行以下命令设置镜像：

macOS / Linux（终端中运行）：
    export HF_ENDPOINT=https://hf-mirror.com

Windows（cmd 中运行）：
    set HF_ENDPOINT=https://hf-mirror.com

注意：这个设置只在当前终端窗口有效。如果你关闭了终端再打开，需要重新设置。
后面第 3 步会把它写入配置文件，之后就不需要每次手动设置了。


-------- 2.2 安装 --------

在同一个终端窗口中继续运行（不要关闭，保持上面的镜像设置生效）：

    pip install zra-mcp

注意：
  - 如果 pip 命令提示"未找到"，试试 pip3 install zra-mcp
  - macOS 用户通常需要用 pip3 而非 pip
  - 如果 pip 和 pip3 都找不到，说明第 1 步的 Python 没装好

如果需要知网检索功能（可选）：

    pip install "zra-mcp[cnki]"
    （或 pip3 install "zra-mcp[cnki]"）


-------- 2.3 验证安装 --------

    pip show zra-mcp

如果显示包信息（名称、版本、位置），说明安装成功。

首次运行 zra-mcp 时会自动下载模型（镜像下约 3-5 分钟），下载完成后
后续启动不再需要下载。

如果镜像也很慢，可以直接拷贝别人已下载好的模型文件夹到以下位置：
    macOS:   ~/.cache/huggingface/hub/models--skatzR--USER-BGE-M3-ONNX-INT8/
    Windows: C:\Users\你的用户名\.cache\huggingface\hub\models--skatzR--USER-BGE-M3-ONNX-INT8\


第 3 步：配置 Zotero 连接

-------- 3.1 开启 Zotero 本地 API --------

1. 打开 Zotero 桌面端（需要 Zotero 7 或更高版本）
2. 菜单栏 -- 编辑 -- 设置（macOS: Zotero -- 首选项） -- 高级
3. 勾选 "Allow other applications on this computer to communicate with Zotero"
   （允许其他应用通过本地 API 访问 Zotero）
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


第 4 步：在 Cherry Studio 中连接

-------- 4.1 安装 Cherry Studio --------

如果还没安装，从 https://cherry-ai.com/ 下载对应系统版本并安装。


-------- 4.2 配置 LLM（大语言模型） --------

Cherry Studio 需要一个 AI 模型来驱动对话。
在 Cherry Studio -- 设置 -- 模型服务 中配置你选择的模型和 API Key。

推荐模型：
  DeepSeek-V3     性价比高，中文好     https://platform.deepseek.com/
  Qwen2.5-72B     中文最强             https://dashscope.aliyun.com/
  Claude Sonnet   工具调用最准         https://console.anthropic.com/
  GPT-4o          综合稳定             https://platform.openai.com/


-------- 4.3 添加 MCP 服务器 --------

1. 打开 Cherry Studio -- 设置 -- MCP 服务器
2. 点击"添加"，切换到 JSON 模式
3. 在 Cherry Studio 中点击"添加 MCP 服务器"，填写：
   - 名称：zra-mcp
   - 描述：Zotero Research Assistant
   - 命令：zra-mcp
   - 参数：留空
   - 环境变量：CHROMA_PERSIST_DIR = 你的 .chroma_db 目录路径
   （Cherry Studio 会自动生成正确的配置格式）

4. 保存配置


-------- 4.4 验证连接 --------

确保 Zotero 桌面端正在运行，然后在 Cherry Studio 中新建一个对话，输入：

    列出我 Zotero 库里的所有收藏夹

如果 AI 返回了你的收藏夹列表，恭喜，配置完成！

连接失败排查：
  1. Zotero 是否在运行？
  2. .env 文件是否在正确位置？CHROMA_PERSIST_DIR 是否正确？
  3. Cherry Studio 是否已重启？（修改 MCP 配置后建议重启）
  4. 在终端/cmd 中直接运行 zra-mcp 看是否有报错信息
  5. 如果提示找不到 zra-mcp 命令，关闭终端重新打开再试（pip 安装后需要重启终端）


日常使用

-------- 每次使用前 --------

1. 打开 Zotero 桌面端（保持运行）
2. 打开 Cherry Studio，正常对话即可

不需要打开终端、不需要手动启动服务、不需要手动同步索引。
Cherry Studio 会自动启动 MCP 服务，服务启动时会自动做增量同步。

【重要：第一次使用请耐心等待索引建立】
第一次启动时，系统需要解析你文献库里的每一篇 PDF 并计算语义向量，
这个过程比较耗时——文献越多越久（几百篇可能需要几十分钟，甚至更久，
取决于电脑性能）。好在它在后台自动进行，你可以：
  - 让它挂在后台慢慢跑，期间正常用电脑做别的事；
  - 索引未建完时，语义搜索可能还搜不全，属正常现象，建完即可；
  - 想确认进度，对 AI 说"检查我的索引质量"或"系统正常吗"。
索引只在第一次（或文献有变动时）需要等待，之后启动都是秒级的增量同步。


【关于图表：默认怎么处理，以及要不要追求"精准识表"】
先说结论：默认配置已经够用，绝大多数人不用改。

为什么这么设计——这是当前的技术现实：
  - "从 PDF 里把表格精准还原成行列结构"在学术论文（尤其中文三线表、无框表）
    上至今没有又快又准的纯文本方案，本质上是个"看图"的视觉识别问题。
  - 用几何/线框去硬猜，结果很差：实测会把多栏正文、参考文献误判成几十栏的
    假表，反而污染检索。所以本项目默认不做这种"伪结构化"。

默认做法（开箱即用、构建快）：
  - 表格：记录它在哪、标题是什么、以及标题下方的原始内容（数值仍可被搜到），
    但不拆成规整的行列；
  - 图：只记录它在哪、标题大致说了什么（不识别图像内容）；
  - 正文里"如表3所示/如图2所示"会自动链接到对应的表/图。
  - 好处：建索引快；代价：表格不是规整的结构化数据。

如果你确实需要"精准的表格结构"（例如要让 AI 精确读取表中每一行每一列的数值）：
  - 可以用专门的视觉文档解析器先把 PDF 转成带表格的 Markdown/HTML，再作为
    笔记/附件交给本工具索引。可选工具：MinerU（学术/中文/复杂表格最佳）、Docling、Marker。
  - 取舍：这类方案靠视觉模型，准确率高，但更重、更慢——首次构建时间会显著增加
    （可能从几十分钟变成数小时，取决于文献量和电脑性能），且要额外装依赖。
  - 一句话：要"信息更全/表结构更准"，就得牺牲"初始构建速度"。是否值得，由你
    根据自己的研究是否高度依赖表格数据来决定。


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
    这篇论文的图表讲了什么？

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

[系统诊断]

    系统正常吗？
    我的索引质量怎么样？
    这篇论文能被正确检索到吗？


-------- 使用技巧 --------

1. 先搜后读：先搜索找到论文，再针对具体某篇提问
2. 引用工作流：把你写的段落粘给 AI -- 选择合适的引用 -- 导出 BibTeX
3. 写操作是安全的：添加论文、写笔记、改标签等操作默认只做预览，
   AI 会让你确认后才真正执行
4. 中英文混搜：可以用中文搜英文论文，反之亦然
5. 不需要手动同步：如果刚加了论文想立刻搜到，可以说"同步一下索引"
6. 文献综述：选定几篇论文后让 AI 写综述，AI 会按主题串联而非逐篇总结
7. 论据搜索：写 Discussion 时，把论点告诉 AI，它会自动分类支持/反驳证据
8. 系统诊断：遇到问题时说"检查系统健康"，AI 会自动诊断并给出修复建议


可用功能一览（36 个工具，CNKI 未启用时为 32 个）

搜索与发现
  search_papers           在本地库中搜索论文（关键词+语义混合）
  search_online_literature 在线搜索英文文献（OpenAlex+CrossRef+S2）
  search_cnki_literature  知网中文文献搜索（需额外配置）
  find_related_literature 给定一篇论文，找相关文献（语料优先+多策略并行）
  expand_citation_network 引文网络展开（谁引了它/它引了谁）
  find_similar_papers     找本地库中语义相似的论文
  browse_library          浏览收藏夹、标签、最近添加
  find_duplicates         查找重复论文
  merge_duplicates        合并重复项
  cnki_paper_detail       获取知网论文详细信息
  cnki_navigate_pages     知网搜索结果翻页

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

智能分析
  reading_status          分析阅读进度（深度阅读/浏览过/未读）
  recommend_papers        根据你的阅读习惯推荐论文
  generate_review_note    多论文文献综述生成（按主题串联，非逐篇总结）
  generate_reading_note   单篇论文结构化阅读笔记
  suggest_tags            智能推荐标签（方法论/领域/数据类型）
  find_arguments          论据搜索器（找支持/反驳你论点的证据）

系统维护与诊断
  sync_index              同步向量索引（通常自动运行）
  check_health            系统健康检查（连接、索引、模型、配置诊断）
  inspect_index           索引质量检视（chunk 统计、章节分布、乱码检测）
  test_recall             召回率自测（验证论文能否被正确检索到）


常见问题

Q: 搜不到我刚加的论文？
A: 对 AI 说"同步一下索引"即可。正常情况下每次启动 MCP 服务会自动同步。

Q: 添加论文/写笔记时报错？
A: 需要在 .env 里配置 ZOTERO_API_KEY（第 3.2 步）。
   没有 API Key 时只能使用搜索和阅读功能。

Q: 索引时报错 "Connection refused"？
A: 确保 Zotero 桌面端正在运行，且已开启本地 API（第 3.1 步）。

Q: 安装时 pip 命令找不到？
A: 试试 pip3 install zra-mcp。
   macOS 用户通常需要用 pip3。
   Windows 用户如果 pip 和 pip3 都不行，说明安装 Python 时没勾选
   "Add python.exe to PATH"，重新安装并勾选。

Q: 安装依赖时下载很慢？
A: 嵌入模型（bge-m3）约 347MB，首次下载较慢。两个解决办法：
   1. 设置镜像：
      macOS:   export HF_ENDPOINT=https://hf-mirror.com
      Windows: set HF_ENDPOINT=https://hf-mirror.com
      然后重新运行 zra-mcp
   2. 找已有模型的人拷贝模型文件夹到你的机器上（见第 2 步说明）

Q: Windows 上 python 命令提示"不是内部或外部命令"？
A: 试试 python3 --version 或 py --version。
   都不行说明安装 Python 时没勾选 "Add python.exe to PATH"，重新安装并勾选。

Q: Cherry Studio 里 AI 没有调用工具？
A: 检查：
   1. 对话设置中是否已开启 MCP 工具调用
   2. 模型是否支持 Function Calling（推荐的四个模型都支持）
   3. MCP 服务器状态是否显示为"已连接"

Q: 项目更新了怎么升级？
A: 运行：pip install --upgrade zra-mcp
   （或 pip3 install --upgrade zra-mcp）
   然后重启 Cherry Studio 的对话即可生效。

Q: 换了电脑怎么办？
A: 在新电脑上重新走一遍第 1-4 步即可（约 10 分钟）。
   你的 Zotero 文献库通过 Zotero 账号同步，索引会自动重新生成。

Q: 搜索结果不好 / 系统不正常？
A: 对 AI 说"检查系统健康"，它会自动诊断连接、索引和配置问题并给出修复建议。
   也可以说"检查我的索引质量"来查看向量数据库的详细状态。


关于知网（CNKI）检索模块

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


附录 A：从源码安装（开发者）

如果你是开发者或需要自定义修改代码，可以选择源码安装：

    git clone https://github.com/qiobn/zotero-research-assistant.git
    cd zotero-research-assistant
    uv venv .venv --python 3.13
    uv pip install -e .

没有安装 uv？先安装：
    macOS:   curl -LsSf https://astral.sh/uv/install.sh | sh
    Windows: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

源码安装用户的 Cherry Studio MCP 配置需要使用完整 Python 路径：

macOS：

    {
      "mcpServers": {
        "zra-mcp": {
          "name": "zra-mcp",
          "isActive": true,
          "command": "/你的项目路径/zotero-research-assistant/.venv/bin/python",
          "args": ["-m", "project_a_mcp.server"],
          "cwd": "/你的项目路径/zotero-research-assistant"
        }
      }
    }

Windows：

    {
      "mcpServers": {
        "zra-mcp": {
          "name": "zra-mcp",
          "isActive": true,
          "command": "D:\\你的项目路径\\zotero-research-assistant\\.venv\\Scripts\\python.exe",
          "args": ["-m", "project_a_mcp.server"],
          "cwd": "D:\\你的项目路径\\zotero-research-assistant"
        }
      }
    }

获取完整路径（在项目目录中运行）：
  macOS:   echo "$(pwd)/.venv/bin/python"
  Windows: echo %cd%\.venv\Scripts\python.exe


附录 B：网络补充说明

本指南已默认引导使用国内镜像，正常情况下不会遇到网络问题。
以下是一些补充信息：

1. pip install（安装本项目）
   - PyPI 国内通常可以正常访问
   - 如果慢可以用清华镜像：pip install -i https://pypi.tuna.tsinghua.edu.cn/simple zra-mcp

2. 下载嵌入模型（约 347MB）
   - 已在第 2 步和 .env 中配置了 hf-mirror.com 镜像，正常 3-5 分钟可完成
   - 如果镜像也不通，找已下载好的人拷贝模型文件夹（见第 2 步说明）

3. 日常使用时的在线搜索
   - OpenAlex、CrossRef、Semantic Scholar 这些学术 API 国内通常可直连，不需要代理
   - LLM 模型 API：选 DeepSeek 或 Qwen 不需要代理；选 OpenAI 或 Claude 需要

总结：pip 安装通常没问题，主要在下载模型时设置一下 HF_ENDPOINT 镜像即可。
