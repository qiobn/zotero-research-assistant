# Zotero Research Assistant — 完整工具测试提示词

> 将以下提示词逐条发送给接入 MCP 的大模型（如 Cherry Studio），观察工具调用是否正确、返回结果是否合理。
> 标注 ⚡ 的为写操作，默认 dry-run（confirm=false），安全预览后再决定是否执行。

---

## 0. ADMIN — sync_index

### 场景 0-1：首次同步 / 增量同步
```
请同步一下我的 Zotero 向量索引，让语义搜索能检索到最新添加的论文。
```
**预期**：调用 `sync_index(force_rebuild=false)`，返回 added/updated/skipped 数量。

### 场景 0-2：强制全量重建
```
我的索引好像有问题，搜索结果不太对，请强制完全重建向量索引。
```
**预期**：调用 `sync_index(force_rebuild=true)`，耗时较长，返回全量重建结果。

---

## 1. DISCOVER — search_papers

### 场景 1-1：基础主题搜索
```
帮我在库里找一下跟"15分钟城市"相关的论文。
```
**预期**：调用 `search_papers(query="15分钟城市")`，返回相关论文列表。

### 场景 1-2：带年份过滤
```
找一下我库里 2020 年之后发表的关于城市公共服务设施可达性的论文。
```
**预期**：调用 `search_papers(query="城市公共服务设施可达性", year_from=2020)`。

### 场景 1-3：带标签过滤
```
搜索我库里标记了 "methodology" 标签的、关于 GIS 空间分析的文献。
```
**预期**：调用 `search_papers(query="GIS 空间分析", tags_include=["methodology"])`。

### 场景 1-4：排除标签 + 限制数量
```
在我的库中搜索关于 urban resilience 的文献，排除掉标记了"已读"的，只要前 5 篇。
```
**预期**：调用 `search_papers(query="urban resilience", tags_exclude=["已读"], limit=5)`。

### 场景 1-5：集合内搜索
```
在我 "毕业论文" 这个收藏夹里，搜索跟步行可达性相关的论文。
```
**预期**：先调用 `browse_library(scope="collections")` 获取 collection_key，再调用 `search_papers(query="步行可达性", collection_key=...)`。

---

## 2. DISCOVER — find_similar_papers

### 场景 2-1：找相似论文
```
我很喜欢这篇论文（key: ABC12345），帮我找找库里有没有类似的论文。
```
**预期**：调用 `find_similar_papers(item_key="ABC12345")`。

### 场景 2-2：搜索后找相似（工具链）
```
先帮我搜一下关于 "walkability index" 的论文，然后找出跟第一篇最相似的其他论文。
```
**预期**：先 `search_papers`，再用返回的第一个 item_key 调用 `find_similar_papers`。

---

## 3. DISCOVER — browse_library

### 场景 3-1：查看所有收藏夹
```
列出我 Zotero 库里所有的收藏夹。
```
**预期**：调用 `browse_library(scope="collections")`。

### 场景 3-2：查看所有标签
```
我的库里现在都有哪些标签？
```
**预期**：调用 `browse_library(scope="tags")`。

### 场景 3-3：查看最近添加的论文
```
看看我最近添加了哪些论文。
```
**预期**：调用 `browse_library(scope="recent")`。

### 场景 3-4：查看某个收藏夹内的论文
```
列出我 "城市形态" 这个收藏夹里有哪些论文。
```
**预期**：先获取 collection_key，再调用 `browse_library(scope="collection_items", collection_key=...)`。

---

## 4. DISCOVER — find_duplicates

### 场景 4-1：检查重复
```
检查一下我的库里有没有重复的论文。
```
**预期**：调用 `find_duplicates()`，返回按 DOI/标题匹配的重复组。

---

## 5. DISCOVER — merge_duplicates ⚡

### 场景 5-1：预览合并（dry-run）
```
刚才找到的重复论文，帮我把 KEY_A 作为主条目，合并 KEY_B 和 KEY_C，先预览看看会做什么。
```
**预期**：调用 `merge_duplicates(keeper_key="KEY_A", duplicate_keys=["KEY_B", "KEY_C"], confirm=false)`。

### 场景 5-2：确认合并
```
确认合并，执行吧。
```
**预期**：调用 `merge_duplicates(..., confirm=true)`。

---

## 6. READ — get_paper

### 场景 6-1：查看论文元数据
```
帮我看看 item key 是 ABC12345 的这篇论文的详细信息。
```
**预期**：调用 `get_paper(item_key="ABC12345")`，返回标题、作者、摘要、DOI、标签等。

### 场景 6-2：搜索后查看详情（工具链）
```
搜索 "land use mix"，然后帮我看看第一篇的详细元数据和摘要。
```
**预期**：先 `search_papers`，再 `get_paper`。

---

## 7. READ — get_paper_content

### 场景 7-1：语义查询论文内容
```
论文 ABC12345 里关于"研究方法"的部分说了什么？
```
**预期**：调用 `get_paper_content(item_key="ABC12345", query="研究方法")`。

### 场景 7-2：查看论文目录结构
```
这篇论文 ABC12345 的目录结构是什么样的？有哪些章节？
```
**预期**：调用 `get_paper_content(item_key="ABC12345", mode="outline")`。

### 场景 7-3：读取完整论文
```
把论文 ABC12345 的完整内容给我看看。
```
**预期**：调用 `get_paper_content(item_key="ABC12345", mode="fulltext")`。

### 场景 7-4：查看特定页
```
论文 ABC12345 的第 5 页写了什么？
```
**预期**：调用 `get_paper_content(item_key="ABC12345", page=5)`。

### 场景 7-5：内容 + 我的批注
```
帮我看看论文 ABC12345 里关于"数据来源"的内容，顺便也把我在这篇论文上做的批注一起显示出来。
```
**预期**：调用 `get_paper_content(item_key="ABC12345", query="数据来源", include_annotations=true)`。

### 场景 7-6：查看论文开头
```
让我看看论文 ABC12345 的引言部分。
```
**预期**：调用 `get_paper_content(item_key="ABC12345")`（无 query/page，返回前几段）。

---

## 8. READ — search_annotations

### 场景 8-1：跨论文搜索批注
```
我之前在某篇论文里标注过关于 "gravity model" 的内容，帮我找找是哪篇。
```
**预期**：调用 `search_annotations(query="gravity model")`。

### 场景 8-2：搜索我的阅读笔记
```
搜索我在所有论文中关于"研究局限性"的批注和高亮。
```
**预期**：调用 `search_annotations(query="研究局限性")`。

---

## 9. READ — create_annotation ⚡

### 场景 9-1：预览创建高亮
```
帮我在论文 ABC12345 的第 3 页高亮这段文字："The 15-minute city concept proposes that..."，备注是"核心定义"。先预览一下。
```
**预期**：调用 `create_annotation(item_key="ABC12345", text="The 15-minute city concept proposes that...", page=2, comment="核心定义", confirm=false)`。注意 page 是 0-based，第 3 页 = page=2。

### 场景 9-2：确认创建
```
确认创建这条高亮批注。
```
**预期**：调用 `create_annotation(..., confirm=true)`。

### 场景 9-3：带颜色和标签
```
在论文 ABC12345 第 1 页用红色高亮 "This is a critical finding"，标签打上 "重要发现"。
```
**预期**：调用 `create_annotation(item_key="ABC12345", text="This is a critical finding", page=0, color="#ff6666", tags=["重要发现"])`。

---

## 10. WRITE — suggest_citations

### 场景 10-1：为自己写的段落推荐引用
```
我正在写论文，下面这段话需要引用支撑，帮我从库里找合适的文献：

"步行可达性是衡量城市生活质量的重要指标，15分钟城市理念强调居民应在步行范围内获取日常服务。近年来的研究表明，土地利用混合度与步行出行意愿呈正相关。"
```
**预期**：调用 `suggest_citations(draft_text="步行可达性是衡量城市生活质量的重要指标...")`，返回匹配的库内论文及支撑段落。

### 场景 10-2：英文段落引用建议
```
I need citations for this paragraph:

"Urban green spaces have been shown to provide significant mental health benefits to residents, particularly in high-density neighborhoods where access to nature is limited."
```
**预期**：调用 `suggest_citations(draft_text=...)`。

---

## 11. WRITE — export_bibliography

### 场景 11-1：导出 BibTeX
```
帮我把这几篇论文导出为 BibTeX 格式：KEY_A, KEY_B, KEY_C。
```
**预期**：调用 `export_bibliography(item_keys=["KEY_A", "KEY_B", "KEY_C"], format="bibtex")`。

### 场景 11-2：导出纯文本引用
```
帮我把 KEY_A 和 KEY_B 导出为纯文本引用格式。
```
**预期**：调用 `export_bibliography(item_keys=["KEY_A", "KEY_B"], format="citation")`。

### 场景 11-3：搜索后批量导出（工具链）
```
帮我找出关于 "urban heat island" 的论文，然后把前 3 篇的 BibTeX 导出给我。
```
**预期**：先 `search_papers`，提取前 3 个 key，再 `export_bibliography`。

---

## 12. WRITE — add_paper ⚡

### 场景 12-1：通过 DOI 添加
```
帮我把这篇论文加入 Zotero：10.1016/j.cities.2025.105902
```
**预期**：调用 `add_paper(identifier="10.1016/j.cities.2025.105902", confirm=false)` 预览。

### 场景 12-2：通过 arXiv ID 添加
```
添加这篇 arXiv 论文到我的库里：2301.00001
```
**预期**：调用 `add_paper(identifier="2301.00001")` 预览。

### 场景 12-3：通过 ScienceDirect URL 添加
```
帮我把这篇论文加入 Zotero：https://www.sciencedirect.com/science/article/pii/S0264275125006055
```
**预期**：调用 `add_paper(identifier="https://www.sciencedirect.com/science/article/pii/S0264275125006055")`，内部从 URL 解析 PII 并通过 CrossRef 获取 DOI，成功返回元数据预览。

### 场景 12-4：通过 Springer URL 添加
```
添加这篇论文：https://link.springer.com/article/10.1007/s11069-024-07001-1
```
**预期**：内部从 URL 提取 DOI `10.1007/s11069-024-07001-1`，成功返回预览。

### 场景 12-5：添加并指定收藏夹和标签
```
把 DOI 10.1016/j.ufug.2023.127892 添加到我的 "绿色空间" 收藏夹，标签打 "待读" 和 "综述"。
```
**预期**：先获取"绿色空间"收藏夹的 collection_key，再调用 `add_paper(identifier="10.1016/j.ufug.2023.127892", collection_key=..., tags=["待读", "综述"])`。

### 场景 12-6：确认添加
```
预览没问题，确认添加。
```
**预期**：调用 `add_paper(..., confirm=true)`，实际写入 Zotero。

### 场景 12-7：通过 BibTeX 添加
```
帮我把这条 BibTeX 记录加到库里：

@article{wang2024walkability,
  title={Walkability and Urban Vitality: Evidence from Chinese Cities},
  author={Wang, Lei and Zhang, Yan},
  journal={Cities},
  volume={148},
  pages={104856},
  year={2024},
  doi={10.1016/j.cities.2024.104856}
}
```
**预期**：调用 `add_paper(identifier="@article{wang2024walkability,...}")`。

---

## 13. MANAGE — add_note ⚡

### 场景 13-1：添加阅读笔记（预览）
```
帮我给论文 ABC12345 写一条阅读笔记，标题是"方法论总结"，内容是：
"本文采用空间句法分析城市网络拓扑结构，结合 POI 数据构建步行可达性指数。样本覆盖 15 个中国城市。"
```
**预期**：调用 `add_note(item_key="ABC12345", title="方法论总结", content="本文采用空间句法...", confirm=false)` 预览。

### 场景 13-2：确认写入
```
没问题，确认保存这条笔记。
```
**预期**：调用 `add_note(..., confirm=true)`。

### 场景 13-3：带标签的笔记
```
给论文 ABC12345 加一条笔记，标题"关键发现"，内容"15分钟可达性覆盖率与居民满意度显著相关(p<0.01)"，标签打上 "统计结果"。
```
**预期**：调用 `add_note(..., tags=["统计结果"])`。

---

## 14. MANAGE — edit_tags ⚡

### 场景 14-1：批量加标签（预览）
```
帮我给这三篇论文 KEY_A、KEY_B、KEY_C 都打上 "核心文献" 和 "方法论" 标签。
```
**预期**：调用 `edit_tags(item_keys=["KEY_A","KEY_B","KEY_C"], add=["核心文献","方法论"], confirm=false)` 预览。

### 场景 14-2：移除标签
```
把论文 KEY_A 的 "待读" 标签去掉，加上 "已读"。
```
**预期**：调用 `edit_tags(item_keys=["KEY_A"], add=["已读"], remove=["待读"])`。

### 场景 14-3：搜索后批量打标签（工具链）
```
搜索所有关于 "remote sensing" 的论文，然后给它们都打上 "遥感" 标签。
```
**预期**：先 `search_papers`，收集所有 key，再 `edit_tags`。

---

## 15. MANAGE — manage_collections ⚡

### 场景 15-1：创建收藏夹（预览）
```
帮我创建一个叫 "毕业论文参考文献" 的收藏夹。
```
**预期**：调用 `manage_collections(action="create", name="毕业论文参考文献", confirm=false)` 预览。

### 场景 15-2：创建子收藏夹
```
在 "毕业论文参考文献" 下面创建一个子文件夹叫 "方法论文献"。
```
**预期**：先获取父收藏夹 key，再调用 `manage_collections(action="create", name="方法论文献", parent_key=...)`。

### 场景 15-3：添加论文到收藏夹
```
把论文 KEY_A 和 KEY_B 添加到 "核心文献" 收藏夹。
```
**预期**：先获取 collection_key，再调用 `manage_collections(action="add_items", collection_key=..., item_keys=["KEY_A","KEY_B"])`。

### 场景 15-4：从收藏夹移除论文
```
把论文 KEY_C 从 "待整理" 收藏夹中移除。
```
**预期**：调用 `manage_collections(action="remove_items", collection_key=..., item_keys=["KEY_C"])`。

---

## 16. 综合工具链场景

### 场景 16-1：完整文献调研流程
```
我正在做关于 "urban green space and mental health" 的文献综述。请帮我：
1. 搜索库里相关的论文
2. 把最相关的那篇的摘要和目录给我看看
3. 找找跟那篇类似的其他论文
```
**预期**：依次调用 `search_papers` → `get_paper` + `get_paper_content(mode="outline")` → `find_similar_papers`。

### 场景 16-2：阅读 + 笔记 + 标签
```
帮我看看论文 ABC12345 里关于"实验设计"的内容，然后帮我写一条笔记总结关键方法，并给这篇文章打上 "实验研究" 标签。
```
**预期**：`get_paper_content(query="实验设计")` → `add_note` → `edit_tags`。

### 场景 16-3：写作支持全流程
```
我正在写关于城市热岛效应的论文，下面是我的一段初稿：

"城市热岛效应导致城市核心区温度显著高于周边郊区，绿化覆盖率被认为是最有效的缓解手段之一。研究表明，增加 10% 的城市绿化面积可降低局部气温 0.5-1.5°C。"

请帮我找合适的引用文献，然后导出 BibTeX。
```
**预期**：`suggest_citations` → 用户确认选择 → `export_bibliography`。

### 场景 16-4：库管理全流程
```
帮我检查库里有没有重复论文，如果有的话帮我处理一下。然后看看最近添加的论文，帮我把它们归类到合适的收藏夹。
```
**预期**：`find_duplicates` → `merge_duplicates`（如需要） → `browse_library(scope="recent")` → `manage_collections(action="add_items")`。

### 场景 16-5：新论文入库全流程
```
我找到一篇新论文 https://doi.org/10.1016/j.landurbplan.2024.105012，帮我：
1. 添加到 Zotero
2. 放到 "城市规划" 收藏夹
3. 打上 "2024" 和 "景观规划" 标签
4. 同步一下索引让后续搜索能找到它
```
**预期**：`add_paper(confirm=false)` → `add_paper(confirm=true)` → `manage_collections(action="add_items")` → `edit_tags` → `sync_index`。

---

## 注意事项

1. **item_key 替换**：上述提示词中的 `ABC12345`、`KEY_A` 等需替换为你库里实际的 item key。可先用 `browse_library(scope="recent")` 或 `search_papers` 获取真实 key。
2. **写操作安全**：所有 ⚡ 标记的工具默认 dry-run，确认无误后再让 AI 执行 confirm=true。
3. **先同步再搜索**：如果是第一次使用语义搜索功能，先执行场景 0-1 同步索引。
4. **观察要点**：
   - 工具是否被正确选择（不应出现 search_papers 和 find_similar_papers 混用的情况）
   - 参数是否正确传递（年份、标签格式等）
   - 工具链是否正确串联（前一个工具的 item_key 传给后一个）
   - 错误信息是否结构化、可读
5. **常见问题排查**：
   - 搜索无结果 → 检查是否已 sync_index
   - 写操作报错 → 检查 ZOTERO_API_KEY 是否配置
   - URL 添加失败 → 检查网络连接，出版商网站是否可访问
