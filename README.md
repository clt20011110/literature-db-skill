# Literature DB Skill

一个独立的 Codex 文献 skill：按期刊/会议采集和增量更新官方元数据，在本地检索标题与摘要，并导出检索结果。无需 ARIS 或 API key。相关论文检索推荐先多路召回，再由本地 Kev 对有限候选重排与保守筛选。

- **187,158 篇论文、27 个已收录期刊/会议**；Bioinformatics 新增 10,127 篇 canonical works，目录范围为 2015 至当前公开的 2026 内容（不是完整 2026 年度），摘要 10,125 篇，DOI 和 PDF 链接各 10,127 条。完整数据库随 GitHub Release `v1.2.0` 提供，已完成严格对账和独立安装验证。
- 历史 `v1.1.0` 保留 177,031 篇/26 个 venue；它在 `v1.0.0` 的 127,256 篇/22 个 venue 基线上新增 CVPR 21,482 篇、ICCV 8,691 篇、ECCV 9,416 篇和 ACL 10,186 篇，`v1.0.1` 仅修复安装器。数据版本扩增元数据和实际观察到的 PDF 链接，没有下载论文 PDF；缺失字段保留来源说明。
- **一个 venue 一份采集手册**，统一入口是 [`SKILL.md`](SKILL.md)。入口、分页、收录范围、字段/PDF 规则和历史异常在 [`references/venues/`](references/venues/) 中维护。
- 本地工作台提供关键词、语义、混合检索，支持 venue/年份筛选；可导出所选或当前展示结果为 **CSV、JSON、BibTeX、RIS**。
- CLI `search discover` 合并多条主题表达的 zvec 检索与显式词组匹配，去重后仅将有限候选交给本地 Kev。原有 `search query` 与工作台仍可使用。

## 安装

目录 CLI 需要 Python 3.10+，zvec 检索与工作台还需要 Node.js 22+、npm 和 Git。当前支持 macOS/Linux；搜索服务使用 POSIX 文件锁，Windows 原生环境暂不支持。目录 CLI 的 Python 运行时使用标准库。可选的 Kev 模型服务有独立的 Python/模型依赖，应在另一个环境安装；见[多路召回与 Kev 配置](references/multi-retrieval.md)。所有 `discover` 模式都需要先由 `search index` 生成就绪的派生查询快照（`papers.sqlite` 和 `state.json`）；快照就绪后，`--retriever keyword` 查询阶段无需 Node/zvec 运行时，但仍需本地 Kev 服务。目前没有独立的 keyword-only 初始化命令。

将仓库克隆为 Codex 的一个 skill：

```bash
git clone https://github.com/clt20011110/literature-db-skill.git "${CODEX_HOME:-$HOME/.codex}/skills/literature-db"
cd "${CODEX_HOME:-$HOME/.codex}/skills/literature-db"
```

下载完整数据库的安装命令和校验说明见 [`data/README.md`](data/README.md)。数据库文件较大，通过版本化 release 安装，不放入 Git 历史。安装后位于 `data/literature-db/catalog.sqlite`；manifest 与 SHA-256 校验绑定同一个版本。

```bash
npm ci --prefix tools/search_runtime --no-audit --no-fund
python3 tools/litdb.py search index
python3 tools/litdb.py search start --open
```

首次建索引会下载嵌入模型，随后在本机 CPU 上推理。完整建索引需要一定时间和磁盘空间；中断后重新运行 `search index` 可继续。新会话发现 skill 后，可以直接请求：

> 使用 $literature-db，查找用图神经网络优化芯片布局和拥塞的相关论文。

如已有同名 skill/数据库，先选择新的安装目录或做好备份；数据安装器不会默默覆盖已有数据库。

## 本地检索与导出

工作台默认地址为 `http://127.0.0.1:8765`，仅监听本机。支持中文和英文，论文主要为英文；用准确的英文技术概念检索通常更好。助手默认用 `search discover` 保留中文原意，补充英文技术表达，并显式选择词组、同义词和缩写；多路召回去重后，由本地 Kev 重排有限候选，再读摘要筛选。Kev 默认地址为 `http://127.0.0.1:8019`，可由 `LITDB_KEV_URL` 或 `--kev-url` 指定本机回环地址。详细配置见[多路召回指南](references/multi-retrieval.md)。

1. 输入研究主题，选择会场、年份和返回条数，开始检索。
2. 勾选论文或使用“全选当前结果”。
3. 选择 CSV / JSON / BibTeX / RIS，点击“导出所选”或“导出当前展示”。

导出包括完整的**已存储摘要**、作者、venue/年份、DOI、文章页和 PDF 链接，按检索顺序去重。缺失字段保持为空。导出范围是当前返回的最多 50 篇，不代表整个数据库或全部潜在匹配。CSV 使用 UTF-8 BOM 并防止单元格被当成公式；JSON 保留结构化作者和多届会议信息。PDF 链接不代表已下载全文或当前可访问。

终端检索：

```bash
python3 tools/litdb.py search discover "用图神经网络优化芯片布局和拥塞" \
  --retrieval-query "graph neural network chip placement congestion" \
  --retrieval-query "GNN placement congestion prediction" \
  --keyword "graph neural network" --keyword "GNN" --keyword "placement" --keyword "congestion" \
  --venue dac --venue iccad --year-from 2020 --candidate-limit 50 --limit 12 --format json
python3 tools/litdb.py search query "graph neural network chip placement congestion" --venue dac --venue iccad --year-from 2020 --limit 12 --format json
python3 tools/litdb.py search query "privacy preserving federated learning" --mode keyword
python3 tools/litdb.py search status
python3 tools/litdb.py search stop
```

`discover --retriever` 可选 `combined`（默认）、`zvec`、`keyword`；`combined` 与 `keyword` 必须给出非空的 `--keyword` 字面词组。词组按规范化后的连续 token 匹配，多个词组之间是 OR；助手应在位置参数主题中保留必要约束，供 Kev 判断。`zvec` 和 `combined` 模式会以原主题参与召回，`--retrieval-query` 可重复添加保持主题范围的表达；`keyword` 模式仅以显式词组召回，原主题用于 Kev 判定。最多向 Kev 提交 `--candidate-limit` 篇去重候选（默认 50，范围 1–500），最多输出 `--limit` 篇（默认 10，范围 1–50）；候选数上限必须至少等于输出数上限。召回使用 RRF（k=60），平均各 zvec 查询的排名贡献，再加上关键词路的排名贡献；重排分数为 `p(direct) + 0.5 × p(background)`。JSON 的 `retrieved_by` 保留召回来源，`filtered` 数组保留被筛除的候选。Kev 只处理候选的已存储标题与摘要，没有对全库逐篇推理。`--kev-timeout`（默认 180 秒）是包括兼容回退在内的整个重排阶段时限。

Kev 的选择分布是尚未针对文献相关性校准的模型置信度，不能视为相关性事实。有摘要的候选仅当 `unrelated` 胜出且达到 `--unrelated-threshold`（默认 0.60，范围 0.5–1）才移除；保留的低置信度候选标记 `uncertain`，需读摘要判断。缺少摘要的候选即使被强烈判为 `unrelated` 也保留并标为 `uncertain`。空结果表示本次有限候选没有被接受，不代表全库没有相关论文。所有结果仍仅基于元数据，不能声称已经阅读全文。

原有 `query --mode` 可选 `hybrid`、`semantic`、`keyword`，工作台继续使用原有检索。检索排序是匹配分数，不是相关性概率；检索标题和摘要不能代替阅读全文。

## 数据目录与更新

默认数据库目录相对于 skill 自身解析，从任意工作目录使用绝对脚本路径也有效。优先级为 `--home` > `LITDB_HOME` > `<skill-root>/data/literature-db`。搜索只读源 catalog，派生 lookup、文本、向量、模型和日志写入 `<db-home>/search/`。

历史 `v1.1.0` 数据集覆盖 26 个 venue；当前 `v1.2.0` 加入 Bioinformatics 后包含 27 个 venue、187,158 篇 canonical works。源配置 registry 有 108 个候选 venue（48 个会议、60 个期刊）；候选数量不表示这些 venue 全部已经收录。保留 `v1.1.0` 的 177,031 篇/26 个 venue 作为历史冻结基线。当前字段覆盖见 [数据库快照统计](references/catalog-snapshot.md)。详细采集方式见 [venue 索引](references/venue-index.md)、[采集/增量模式](references/modes.md)、[维护命令](references/maintenance.md)。各 venue 的实际年份、水位和缺失字段以数据库/manifest 为准。

更新流程：读取对应手册和已有水位 → 枚举官方新增年份/期次 → 提取并保存字段来源 → 校验 staging → 单写入者事务合并（同时写入数据库水位）→ reconciliation 通过后确认完成 → `search index`。公开稳定 HTML/API 可由脚本采集；动态页面和登录依赖页面使用正常授权浏览器。遇到访问限制保存断点，不绕过限制。

需要收录更多期刊/会议时，按 [扩增 venue 指南](references/expand-venues.md) 操作：包含可直接交给 Codex 的任务示例、已有候选与全新 venue 的不同步骤、registry 限制、采集文件格式、合并命令和完成标准。

历史原始网页、浏览器会话、机器路径和临时任务文件不在发行包内。匿名的 `legacy-evidence://` 引用用于保留来源关系，不能当作可直接打开的文件或新鲜网络证据。论文摘要等内容的权利仍属于各自权利人；本仓库不包含论文 PDF 全文。

## 实现与验证

```text
SKILL.md                     Codex 入口
references/venues/*.md      每个期刊/会议的采集手册
config/litdb/                来源与范围 registry
scripts/                    全量数据库打包与安全安装
data/                       发行 manifest、校验与数据说明
tools/litdb.py              稳定 CLI
tools/litdb/                 元数据控制器、检索与导出服务
tools/browser/              可复用浏览器采集器
tools/search_runtime/       固定版本本地向量检索运行时
tests/                      采集、入库、搜索、导出与安装测试
```

```bash
python3 -m unittest discover -s tests/litdb
python3 -m unittest tools.test_nature_staging_prepare tools.test_nature_finalize
python3 tools/test_iclr_pdf_discovery.py
node --test tools/search_runtime/filter-batches.test.mjs tests/browser/*.test.mjs
```

搜索使用固定版本 `@zvec/zvec-grep@0.2.2`。安装时 `patch-zvec.mjs` 修复大范围 venue/年份筛选：使用 native IN 和分批候选集合，合并原始分数后执行原排名融合。它会拒绝未知版本或源码形态。默认模型 `local/potion-multilingual-128m`；`LITDB_MODEL_CACHE` 可指定缓存位置。切换模型需显式 `search index --rebuild --model local/...`。

故障定位：先看 `search status`，再看 `<db-home>/search/state.json`、`index_progress.json` 和 `engine.log`。端口占用时为 `search start` 指定 `--port`。所有本地 HTTP 请求保留同源限制。
