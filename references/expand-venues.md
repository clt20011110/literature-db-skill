# 扩增 venue 指南

用于把新的期刊/会议纳入本地数据库，或补齐已有 venue 的年份和字段。一个 venue 对应一份采集手册，数据统一写入配置的数据库。添加手册、注册配置、完成枚举和完成 metadata 入库是不同的结果。

## 可以直接交给 Codex 的任务

> 使用 $literature-db，按 references/expand-venues.md 将【期刊/会议名称或官方链接】纳入数据库。范围为【起止年份；未指定则从 2015 年或创刊/创会年份中的较晚者，到当前已公开内容】，收录【主会研究论文/指定类型】。先检查已有 registry、手册、数据库和历史进度；复用已有数据，补齐标题、作者、摘要、DOI、文章页和可发现的论文 PDF 链接。完成官方枚举、字段提取、staging 校验、单写入者合并、对账和检索索引更新，并报告逐年覆盖和缺失字段。无法访问时保存断点和原因。

如需限定验收条件，在任务后补充，例如“PDF 不可得时保留已核实的文章页链接并报告缺失”“只采集链接，不下载 PDF”。这些条件只适用于本次指定范围，不能沿用其他 venue 曾获准的例外。

如授权委派，可补充“将重复采集和整理委派给 Luna Max；每个 venue 由一个 worker 负责，主任务串行合并”。任务应传递 venue、年份、数据库绝对路径、手册、独立 run 目录和交付文件；浏览器会话无法共享时，主任务保存页面证据供 worker 处理。详见 [委派与运行模式](modes.md)。

## 1. 判断从哪里开始

先读 [历史与复用](history-and-reuse.md)，检查 `<db-home>/registry/venues/<venue-id>.yml`、catalog、watermark、expected manifests、recipe 和 run receipts。

| 现状 | 应做的工作 |
| --- | --- |
| 已有论文和手册 | 按 [增量模式](modes.md#mode-2-incremental-update) 补新增年份、当前期次或指定缺失字段 |
| registry 已有，尚未收录 | 复用 venue ID，发现官方来源，建立手册，再初始化采集 |
| registry 已有但只有数量/ID 清单 | 复用枚举结果作为范围线索，继续详情提取；不能报告为 metadata 已完成 |
| registry 没有 | 核实身份与官方来源，新增配置和手册，再采集 |
| 曾被停用或范围有歧义 | 查明停用原因、同名会议、分会/workshop 等边界；只在影响本次收录时询问用户 |

保持稳定的小写连字符 ID。核对别名、更名、ISSN 和历届会议名称，避免给同一 venue 创建第二个 ID。默认起点受 `active_from` 限制；双年会议、未举办年份以及尚未公开的届次应单独解释，不算已采集空年份。

## 2. 注册与配置：区分仓库源配置和运行配置

- `config/litdb/venues/*.yml`：随代码分发的源配置；虽然后缀是 `.yml`，当前内容由 JSON 解析器读取，文件顶层是 venue 数组。
- `<db-home>/registry/venues/<venue-id>.yml`：实际采集命令读取的运行配置；每个文件是一个完整 JSON 对象。
- `references/venues/<venue-id>.md`：经官方页面验证的采集方法。只修改手册不会自动注册 venue；只修改源配置也不会同步到已安装数据库。

已有 ID 时保留运行配置中已核实的域名、路径限定和收录规则。全新 ID 至少明确 `id`、`canonical_name`、`venue_type`、`profiles`、`active_from`、`publisher_family`、`allowed_domains`，并依据来源补充别名、ISSN、日历、入口、主轨与排除规则。可参考同出版平台配置，但不能把另一会议的年份、链接或排除规则直接当作事实。

完整字段和默认值以 [registry.py](../tools/litdb/registry.py) 的 `REQUIRED_FIELDS` 和 `_normalize_venue` 为准；`load_source_venues()` 返回规范化后的对象。新增运行配置时只原子写入本次目标条目，并维护 `registry/venue_registry.yml` 的 ID/数量汇总；保留已有条目和 campaign 历史。不要手工向 SQLite 插入 venue 或论文，venue 表由 metadata 合并维护。

**当前实现限制：** `registry validate --strict` 和 `init` 内部的严格安装固定检查 107 个 venue（48 个会议、59 个期刊）。

- 已注册 venue 的首次采集不改变这个数量，可以沿用严格校验。
- 本地新增运行条目时用 `registry validate` 做结构校验；数量超出原始基线不代表新增条目无效。
- 若把全新 venue 加入仓库源配置，需一并更新 [constants.py](../tools/litdb/constants.py) 的三个 `EXPECTED_*` 数量、[registry 测试](../tests/litdb/test_registry.py) 和当前候选数量说明，再在临时空目录验证初始化。历史发行快照的数量不能随之改写。
- 当前没有 `registry add` 或增量同步命令。由 Codex 定向维护上述配置；不要为同步一个条目在已有数据库上重新运行 `init`，它会重写 registry 和默认配置。

`crawl_from` 当前校验要求为 `2015`；晚于 2015 年创办的 venue 用 `active_from` 和采集范围表达。运行配置中的 `status` 仍用 `UNSEEN`，实际任务进度记录在 campaign/run/catalog 状态中，不能把它改为 `ACTIVE` 来表示收录完成。

## 3. 核实来源并建立手册

从官方期刊 archive/issue 或会议 proceedings 开始，检查最早目标年份、中间年份、最近完整年份和当前公开内容。核实分页终点、年份归属、论文详情入口、原生 ID、文献类型和 PDF 行为。先用少量真实页面验证字段提取，再扩大批量。

复用同平台的适配器前检查其 `--help`、输入格式和 venue 假设。CLI 提供校验和入库，并不是通用自动爬虫。公开稳定 HTML/API 可用脚本；动态页面和依赖登录的内容使用正常授权浏览器。对登录、验证码、访问拒绝和限流保存断点，不尝试绕过。

验证后按 [模板](venues/template.md) 创建手册，登记到 [venue 索引](venue-index.md)。明确：

- 官方入口、域名与必要的路径限定，跨平台或更名时期的处理。
- 年份/卷期/会期归属，research/main track 的包含和排除规则。
- 列表字段、详情字段、去重身份、PDF 与 slides/supplement 的区别。
- 可复现的分页与断点方式、适配器路径、真实最后验证日期。

仅为实际观察到且属于任务范围的官方来源扩充 allowlist；不要为了让校验通过放宽整个共享文件托管域名。历史 `legacy-evidence://` 标签只能说明旧来源关系，不能冒充本次网络观察。

## 4. 枚举、提取和准备入库文件

在独立 `<db-home>/runs/<run-id>/venues/<venue-id>/` 中保存本次产物。逐年完成官方列表枚举，保存 ID 集合、数量、分页终点、来源 URL 和观察时间，再补齐详情。所有枚举项都应归入 metadata、带理由的 exclusion 或未解决队列；未解决项不能静默删除。

| 产物 | 当前接口要求 |
| --- | --- |
| `expected/<year>.jsonl` 或 `.jsonl.gz` | 年份命名文件；每行至少有 `source_native_id`、`year`，建议同时保留 venue 和枚举来源。覆盖此次对账范围，含需解释的排除项 |
| `metadata_staging.jsonl` | 每行一个拟收录记录，按下述字段契约输出 |
| `metadata_exclusions.jsonl` | 排除记录及理由、来源。无排除项时使用空文件 |
| `waterline_evidence.json` | 当前官方枚举的完成证据；严格验证和 merge 都会检查 |
| checkpoint / unresolved / receipt | 已完成年份、页码/游标、失败原因、待补字段和续跑入口；放在本次 run 内 |

`bootstrap plan` 只读取已有 expected 清单，不负责联网枚举；首次发现前提示 `BLOCKED: no expected identities` 意味着尚未准备清单。不要从已经成功抓到的详情反推 expected 全集，否则漏抓的论文会被隐藏。

staging 使用 `schema_version: literature-metadata-staging-v1`，包括 `venue_id`、`source_native_id`、`title`、按原顺序排列的 `authors`、`year`、`document_type`、`inclusion_decision: include`、`landing_url`、`source_url`、`observed_at`、`abstract`、`publication_date`、`doi`、`pdf_url`、`pdf_discovery_status`、`field_provenance` 和 `missing_fields`。缺失摘要、日期或 DOI 需要结构化的已核实原因，不能用虚构内容填满。

必需 provenance 覆盖 `source_native_id`、`title`、`authors`、`year`、`document_type`、`landing_url`、`abstract`、`doi`、`publication_date`、`pdf_discovery_status`；每项至少包含真实 `source_url` 和 `observed_at`。复用旧字段时保留原观察时间。PDF 不可得仍须保留文章页，并准确区分 `not_visible`、`access_restricted` 等状态。允许状态、缺失/排除原因和校验逻辑见 [metadata_pipeline.py](../tools/litdb/metadata_pipeline.py)；结构示例见 [pipeline 测试](../tests/litdb/test_metadata_pipeline.py)，测试中的论文值和日期不能作为采集数据复制。

水位证据需包含 `venue_id`、`status`（`PASS` / `NO_CHANGE` / `UPDATED`）、`drift_status`（`NO_DRIFT` / `WITHIN_THRESHOLD`）、`enumeration_complete: true`、`observed_at`、`source_urls`、`source_item_set_sha256`、`current_source_item_count`、`new_ids` 和 `missing_ids`。哈希从实际枚举集合计算并记录排序/序列化方式；所有状态必须由证据支持。当前校验要求观察时间在 7 天内；过期需重新观察，不能只改时间戳。

## 5. 校验、单写入者合并和对账

以下命令在 skill 根目录执行。先将变量中的占位值替换为真实 venue 和本次 run；数据库位置遵守 `LITDB_HOME`，也可把 `litdb_home` 设置为已选择的绝对路径。

```bash
litdb_home="${LITDB_HOME:-$PWD/data/literature-db}"
litdb_venue="<venue-id>"
litdb_run="$litdb_home/runs/<run-id>/venues/$litdb_venue"
litdb_expected="$litdb_run/expected"

python3 tools/litdb.py registry validate --home "$litdb_home"
python3 tools/litdb.py staging validate --home "$litdb_home" \
  --venue "$litdb_venue" --run "$litdb_run" \
  --input "$litdb_run/metadata_staging.jsonl" \
  --exclusions "$litdb_run/metadata_exclusions.jsonl" \
  --expected-root "$litdb_expected" --strict
python3 tools/litdb.py merge venue --home "$litdb_home" \
  --venue "$litdb_venue" --run "$litdb_run" \
  --input "$litdb_run/metadata_staging.jsonl" \
  --exclusions "$litdb_run/metadata_exclusions.jsonl" \
  --expected-root "$litdb_expected" --single-writer --verify-before-commit
python3 tools/litdb.py reconcile venue --home "$litdb_home" \
  --venue "$litdb_venue" --run "$litdb_run" \
  --expected-root "$litdb_expected" --strict
```

逐条检查返回码和报告，任何一步失败都先修复，不要继续执行后续步骤。所有 worker 只交付各自 staging，由一个写入者按 venue 串行合并。

当前 staging 校验要求该 `expected-root` 中的所有 ID 都由本次 staging 或 exclusions 解释，这条 merge 路径仍把 watermark 的 `mode` 写为 `initialize`。默认做法是分批采集、保存断点，最终汇总完整范围的 staging/exclusions 后一次合并。已有 venue 可以复用旧字段和原 provenance，补上本次新增或修正内容；不要把少量新增行直接对全历史 expected 校验。需要其他增量方式时，先核对该 venue 手册和适配器契约。

**实现中的水位顺序：** `merge` 在事务内写入 `update_watermark`，不是等 `reconcile` 后才写。若把本批子集单独合并，它还会以本批的年份/身份等覆盖全局水位，可能造成水位回退；后续对账失败不会回滚已提交事务。因此不要把分批 merge 的水位当作整个 venue 的完整进度；已有分批提交应在汇总完整范围后重新校验、合并和对账。只有最终对账通过才宣告完成，失败时记录已提交事实和修复入口，不手工伪造状态。campaign 是否自动转为 `ACTIVE` 还取决于原状态链；以实际 merge/reconcile receipts 为准。

通过本次范围对账后，把已核实的 expected 文件按年份合并到 `<db-home>/manifests/expected/<venue-id>/`，保留旧版证据；单年或增量清单不能覆盖该 venue 其他年份的完整集合。命令不会自动把 `--expected-root` 指向的文件发布到此目录。随后显式执行全 venue 累计对账和索引更新：

```bash
python3 tools/litdb.py reconcile venue --home "$litdb_home" \
  --venue "$litdb_venue" --run "$litdb_run/final-reconcile" \
  --expected-root "$litdb_home/manifests/expected/$litdb_venue" --strict
python3 tools/litdb.py search index --home "$litdb_home"
python3 tools/litdb.py search status --home "$litdb_home"
```

只有完整 expected 集合的最终报告通过，才可称全 venue 对账通过；对账遇到 `extra` 不一定返回失败，仍须解释这些 ID。仅对子集得到 `PASS` 不能证明其他年份完整。索引成功且 `stale: false` 后，用该 venue 的一篇真实标题验证检索结果。

## 6. 交付与后续更新

报告实际收录年份/水位、官方枚举数、收录来源项数、canonical 论文数、排除数、重复数和未解决数；分别统计摘要、DOI、文章页和论文 PDF 链接覆盖。合并 receipt 中的表级 inserted/updated 计数不等于新增论文数，应结合对账和 canonical 数据报告。

列明已注册、枚举完成、metadata 已入库、对账通过、已进入搜索各阶段；“PDF 链接已收集”“链接已实测”和“PDF 已下载”分开报告。只完成部分年份、存在阻断或尚未公开内容时如实记录范围，不将整个 venue 标成完成。

更新 venue 手册的真实验证日期、续跑方法和未解决项。可提交手册、配置、适配器与相应测试；数据库、原始页面、会话和临时采集输出留在本地。只有任务包含发布数据版本时，才按 [数据包说明](../data/README.md) 重建完整快照、校验并上传新的 Release，不改写历史数据包或将数据库提交进 Git。
