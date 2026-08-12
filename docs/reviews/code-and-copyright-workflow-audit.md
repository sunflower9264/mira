# “代码与软著”工作流审查与整改记录

## 1. 审查对象

- 部署 App：`代码与软著`
- App ID：`app_5fd5f63153f5418b857634fd76a4c57b`
- 部署数据库：`deploy/mira-server/backend/data/mira-deploy.sqlite`
- 审查基线：`已发布 v14`，22 个节点、28 条边、19 个 generate 节点
- 整改结果：`已发布 v15`，13 个节点、26 条边、10 个 generate 节点
- 当前状态：`published / public / cloneable`

本记录只讨论 Mira 工作流的产品与工程质量，不构成软件著作权登记法律意见，也不承诺登记审批结果。

## 2. 结论摘要

v14 基线流程的主要问题不是单个提示词写得不够好，而是以下问题叠加：

1. 大部分 generate 节点都会额外执行一次 ask_user preflight，完整流程产生约 18–20 次隐藏模型调用。
2. PRD、工程蓝图、代码校验、代码证据、代码材料和多级汇总节点内容高度重复，上游输入逐级膨胀。
3. `完整软著资料包内容生成` 没有直接连接模板、代码证据和登记汇总，依赖 Agent session 记忆间接取得材料。
4. JSON Schema 体积很大，但数组普遍没有 `minItems` / `maxItems`，数量要求仍停留在提示词中。
5. artifact 只做浅层文件校验，Files 接口随后又扫描整个 workspace，导致缓存和临时文件进入交付列表。
6. 截图脚本把 Chromium profile 写入截图目录，历史截图 ZIP 因此包含大量 `.chromium` 文件。
7. 源码生成明确排除 lockfile，截图阶段固定执行 `npm install`，构建结果不可复现。
8. 模板虽然上传成功，但最终生成的 DOCX 没有继承模板结构，也没有嵌入真实截图。
9. 历史输出中多次出现 Unicode replacement character `U+FFFD（�）`，现有契约不会阻止污染结果进入下游。

整改原则是减少节点和模型决策次数，把可验证约束下沉到 graph Schema、运行时校验和文件 manifest；提示词只保留当前节点的任务、输入边界和交付要求。

## 3. 运行与产物证据

- 历史运行 13 次：8 success、3 failed、1 waiting、1 cancelled。
- 最近成功运行约 28–67 分钟，最新成功运行耗时约 67 分钟。
- 历史出现 19 次输出契约自动修复，其中 17 次为 JSON `Extra data`。
- 12 次运行、61 个步骤的输出包含 `�`；最新成功运行仍有 4 个污染节点。
- 最新截图 ZIP 含 265 个文件，其中大量为 `.chromium` profile，总体约 5.5 MB。
- 最新资料包内生成的 11 个 DOCX 均只有极少基础 OOXML part，未继承正式模板的复杂结构。
- 正式说明书模板约 967 KB，包含 47 个媒体文件；生成说明书约 6 KB，未嵌入截图。
- 最新生成页面直接展示“SQLite 演示后台”“真实 SQLite 后端”“模拟数据”等工程证明文案，不适合作为正式产品截图。

## 4. 现有节点逐项审查

| 现有节点 | 结论 | 整改动作 |
|---|---|---|
| 输入行业或软件想法 | 保留 | 简化 label 和 placeholder，仍使用唯一 text user_input |
| 行业场景拆解 | 重复 | 与“软件方向发散”合并为“候选软件方向” |
| 软件方向发散 | 重复 | 合并后固定输出 3 个可实现候选，与 ask_user 每组最多 3 个真实选项的契约一致 |
| 方向选择与 PRD Brief | 过载 | 改为只负责方向与视觉选择，并执行第一次人工确认 |
| 软件 PRD 成稿 | 内容重复 | 与工程蓝图合并为产品与实现规格草案 |
| 代码工程生成蓝图 | 内容重复 | 合并进规格草案，不再重复转述 PRD |
| 生成产品视觉风格方案 | 确认位置不佳 | 合并到第一次确认，使用 2–3 个真实 design-md 候选 |
| 软著业务与文档规划 | 可保留能力 | 重写为“软著材料规格”，只规划材料章节和待填写字段，不读取模板文件 |
| 生成产品源码包 | 保留 | 缩短提示词；要求 lockfile、真实构建、运行检查和业务化 UI 文案 |
| 代码包结构校验 | 校验不足 | 与代码证据、代码材料、代码汇总合并为一次代码审计 |
| 软著模板文件 | 保留 | 只直接连接资料包生成和资料包验收，避免旧样例内容污染规格节点 |
| 代码证据自动分析 | 重复 | 合并进“代码审计与证据” |
| 登记业务与模板汇总 | 中转冗余 | 合并进“软著材料规格” |
| 代码材料自动生成 | 重复 | 合并进代码审计，最终由模板资料包节点排版 |
| 说明书截图材料整理 | 中转冗余 | 删除；资料包节点直接读取 screenshot manifest |
| 代码侧材料汇总 | 中转冗余 | 删除；使用显式直接入边代替透传节点 |
| 说明书内容生成 | 与最终排版割裂 | 删除；资料包节点基于模板直接生成说明书 |
| 说明书质量复核 | 检查对象不完整 | 合并到最终资料包验收，检查真实 DOCX 和截图关系 |
| 完整软著资料包内容生成 | 保留 | 直接连接模板、最终规格、代码证据、源码和截图，不依赖 session 记忆 |
| 资料包一致性复核 | 保留 | 实际解包逐文件检查，并输出结构化验收结果 |
| 展示最终交付 | 保留 | 不持久化限时签名 URL；展示状态、hash 并引导到“文件”页下载 |
| 产品预览运行与截图采集 | 保留 | 删除门店、菜品等旧项目路由偏好，按真实 route map 截图 |

## 5. 模板审查

当前素材节点包含 7 个 DOCX 和 1 个 TXT。模板的版式可以复用，但样例正文不能作为新材料事实来源。

| 模板 | 审查结论 | 生成要求 |
|---|---|---|
| 模板-申请表.docx | 字段占位基本可用 | 保留字段布局；未知登记事实写作 `[待填写：字段名]` |
| 模板-功能特点.docx | 字段可用，内容需重写 | 功能、技术特点和页数只取自最终规格、源码与排版结果 |
| 模板-源代码.docx | 结构简单 | 只写入实际生成源码，不包含依赖和构建产物 |
| 模板-说明书.docx | 含电视端、C#/.NET、通用数据库等旧样例和大量旧媒体 | 保留样式、章节和版式；删除样例正文与旧截图，嵌入本次真实截图 |
| 模板-设计文档.docx | 含大段通用数据库规范、时序数据库等无关内容 | 保留版式；正文只描述当前软件模块、数据对象、流程与页面 |
| 模板-产品测试功能表.docx | 大量机械 CRUD 句式 | 测试项必须包含具体操作、前置条件和可观察结果 |
| 模板-非嵌入式软件环境表.docx | 包含示例硬件说明 | 未确认环境保持显式待填写，不把容器环境伪装成用户真实环境 |
| 模板-账号.txt | 占位结构可用 | 保持 TXT 格式；账号、密码和地址未确认时保留待填写 |

“严格复用模板”在本次整改中定义为：复制原模板后编辑，保留样式、页面设置、表格、编号、页眉页脚等结构；旧样例正文、旧项目名称和旧截图必须删除或替换。

模板基线 SHA-256：

| Upload ID | 文件 | SHA-256 |
|---|---|---|
| `upl_2acf08c2ccc04f32889d2b54c20d286c` | 模板-申请表.docx | `42f42aef791ad94e36ed025b40c30c33775c01bc05f32ee2a94c0a7f24e47ffa` |
| `upl_0cc355250f8b47bc8f99cdd74542165f` | 模板-功能特点.docx | `99fe12b48969207ee6acd52782d8c15c7e2121b47adc70c69f8e3e3649325639` |
| `upl_202cf869a8614bc69d3a1368b128be7f` | 模板-源代码.docx | `6cf02290301715ad7d4b9b4fb174860a196eae2c28ff86149143b0f1704faf3a` |
| `upl_8270f45f286c4b1c98b403ccc6a8ca4f` | 模板-说明书.docx | `5359621749d8deefbfb205177e68fe7cbe4b64e23664bc38eccdae6152783c46` |
| `upl_c987e49fb9cd40b28b5b666750960c54` | 模板-设计文档.docx | `a5102e460e2fa97a8ace5c0b836132da2d63f94beffeeb7e370d6a09fd208630` |
| `upl_e61fa277d4bb40a19574a6bfb316d9f9` | 模板-产品测试功能表.docx | `7079ec8e40b739f8fc2e8cf2f012b80560441c45995a6b5e139290942fd0b552` |
| `upl_fc88ea55620d4350851335673b3276bc` | 模板-非嵌入式软件环境表.docx | `e36df9a1104822db74589b5963677a773d6a8d7cabb1609baa1c3eb2d789f896` |
| `upl_86abe75aecb24175bcd229f04bfe8a51` | 模板-账号.txt | `427d21e9888fd3ebff1fdb8847b0f5b6772348d073ad753324a169e42762466c` |

## 6. 目标工作流

```text
输入软件想法
→ 候选软件方向
→ 方向与视觉选择【确认 1】
→ 产品与实现规格草案
→ 规格确认与定稿【确认 2】
├→ 软著材料规格
└→ 生成产品源码包 ← 方向与视觉选择
   → 代码审计与证据
   → 运行预览与截图

最终规格 + 模板 + 材料规格 + 源码包 + 代码证据 + 截图
→ 生成软著资料包
→ 资料包验收
→ 最终交付
```

目标 graph 共 13 个节点、26 条边。只有“方向与视觉选择”和“规格确认与定稿”允许 ask_user preflight；其余 generate 节点关闭 preflight。用户确认的 `selected_visual` 通过显式入边直接交给源码节点，不依赖跨分支 Agent session 记忆。

### 6.1 Prompt 规则

- 普通 JSON 节点约 140–470 字，artifact 节点约 400–760 字。
- 不重复声明“不要询问用户”；由 `ask_user_enabled` 控制。
- 不在提示词中复述完整上游 schema。
- 数量、枚举、必填、字符串长度和最大产物数写入 output contract。
- 源码生成只实现最终规格，不重新发散产品方向。
- 最终页面不重新判断验收结论，也不生成或改写下载链接。

### 6.2 交付物

运行结果的“文件”页只展示三个顶层 artifact：

1. 产品源码包：`product-source.zip`
2. 产品预览截图包：`product-preview-screenshots.zip`
3. 软件著作权资料包：`software-copyright-package.zip`

每个 artifact 必须带服务端计算的大小、SHA-256 和完整性状态。资料包内部包含 7 个模板派生 DOCX、1 个账号 TXT、`package-manifest.json` 和生成报告。

资料包验收结果中，8 个模板派生文件记录 `source_template`；`package-manifest.json` 和 `生成报告.md` 固定记录 `origin=generated`，不要求模型虚构模板来源。

## 7. 通用代码整改

### 7.1 ask_user 控制

- `GenerateNode` 新增 `ask_user_enabled?: boolean`。
- 缺省或 `true` 保持现有自动 preflight；`false` 直接执行节点。
- 编辑器提供“运行前允许追问”开关。
- graph validation 和 workflow lint 拒绝非 bool 或非 generate 使用该字段。

### 7.2 字符完整性

- 在 preflight、JSON、HTML、自由文本和 artifact 文本成员中检查 `U+FFFD`。
- generate 首次失败走现有一次 contract repair；修复后仍损坏则节点失败。
- ZIP/TAR 和 OOXML 文本成员采用严格解码和有界扫描，不能通过删除 `�` 冒充修复；上限为 10,000 个成员、64 MiB 文本/XML、512 MiB 压缩文件和 1 GiB 总展开量。
- `.zip`、`.docx`、`.pptx`、`.xlsx` 必须是真实 ZIP；`.tar`、`.gz`、`.tgz` 必须是 tarfile 可读取的 TAR，普通单文件 gzip 不在支持范围内。

### 7.3 Artifact 完整性

- 服务端在成功校验时记录 relative path、size、SHA-256、kind 和 manifest version。
- 执行 `max_count`，拒绝重复路径、上传暂存目录，以及 archive traversal、绝对路径、链接和特殊文件。
- 三个顶层交付节点统一使用 `artifact_kind=zip`，只接受 `.zip`，并在提示词中固定交付文件名。
- Files、Trace 和下载只认成功 artifact contract Step，不再枚举 workspace。
- Run 结束和下载前复验 hash；新版下载 token 必须绑定 SHA-256，同路径新版 `modified/invalid` 不得被旧声明遮蔽；旧声明产物标记为 `legacy_unverified`。Run 成功终态使用条件更新，不能覆盖并发取消。

### 7.4 截图与可复现构建

- Chromium profile 位于独立临时目录，不进入截图包。
- 存在 `package-lock.json` 时使用 `npm ci`。
- ZIP 解包拒绝规范化后重名成员，并逐成员读取以稳定拦截 CRC 或压缩流损坏。
- 每个 route 在 Chromium 前检查最终 HTTP 状态；404/500 不生成截图，写入 manifest failure 并使 CLI 非零退出。
- graph 调用截图工具时显式传 `--min-screenshots 5`；截图不足或任一路由失败时 `manifest.ok=false` 且 CLI 非零退出，同时仍保留诊断 ZIP。
- screenshot manifest 和 capture log 只记录相对路径或占位路径，覆盖常见 runtime 根目录且不改写 URL。
- 源码包必须包含 lockfile，不包含 `node_modules`、`.next`、缓存或密钥。
- 截图范围来自代码审计生成的真实 route map，最多 10 页。
- Codex CLI 目标版本为 `0.147.0`，发布前执行中文结构化输出 smoke test。

## 8. 测试与验收记录

- 目标 graph 文件 SHA-256：`12cf55d719eed73096151e4a8799b0754cc3b1ad5ce2f1d179c4c945d9ca20aa`
- 目标 graph canonical SHA-256：`a49a06918eaac118490626ec75d0f0203005b66f32cc0054a533197f8b29080d`，计算口径为 `jq -cS` 且不含末尾换行
- 发布前备份 Version ID：`ver_e35a89394c644cbe87bd9fe9d92211c6`
- 发布前备份目录：`deploy/mira-server/backups/code-copyright-v15-20260810-154000`
- 私有验证 App ID：`app_1404d4e2cbaa4fc183a4c4172dde8e31`，验收和产物备份后已删除
- 私有验证 Run ID：`run_c8cc9a247f104d3bb8355b57f255be12`，随私有 App 删除，ID 仅作审计记录
- 私有验证版本：`已发布 v5`，Version ID `ver_a6a4fe5f3fe648d2a8f625926dad1317`，随私有 App 删除
- 正式发布版本：`已发布 v15`，Version ID `ver_bbc26109c1e648b4a2d70538baefada2`
- 正式 smoke Run ID：`run_60efda7f915b427590f883f9b487b652`，到达第一次预期人工确认后主动取消
- 源码包 SHA-256：`a03ea36c2ebb32e244b7716eceea5a67916224bc6822590720d8712da3b4cbb1`
- 截图包 SHA-256：`a610fe05933ffb9f81a13f085801240e915c52f8c0dac3b7bfb03af669c83e2e`
- 软著资料包 SHA-256：`28f17dd8ab1bb8b344722ee64a6e2bd9c323738b8e56eaac679750d0e9876ba9`

### 8.1 自动化与部署验证

- 后端全量：`494 passed, 17 skipped, 1 warning`；唯一 warning 是 passlib 使用即将在 Python 3.13 移除的 `crypt`。
- 后端 `compileall`、前端 `typecheck`、前端生产构建和 `git diff --check` 均通过。
- 目标 graph 定向测试：8 项通过；发布前与落库后 lint 均为 0 error、0 warning、0 info。
- 部署健康检查通过；runtime 中 Codex CLI 为 `0.147.0`。
- 中文严格 JSON Schema smoke 返回 `{"status":"ok","message":"运行正常"}`。
- Office helper 源码与安装文件 SHA-256 均为 `537780efb6996e7025117e3e53c834817fea0906d0b556c24f4340398ef2073d`。
- 隔离 smoke 确认专用账号没有 Docker 组、不能连接 Docker socket、不能读取仓库标记、不能给父进程发信号，同时能把真实 DOCX 转成 1 页 PDF。

### 8.2 私有验证过程

首次部署后重跑 `run_ca4fcc86e57a414c901aa847b7756dcf` 因上游 `stream disconnected before completion` 失败；同配置中文结构化短调用随即通过，重试后未再复现。

中间候选 `run_24469e6bd7d8425cb15f246ffd0858a3` 被工作流验收正确拒绝，发现 04/05 页眉占位符、07 环境表旧样例、生成报告与实测不一致。独立复核进一步发现旧作者、公司、示例标题、WPS/customXml 元数据和 manifest 内部路径。对应要求被压缩为资料包节点的一次程序化终检，两个尚未产出最终包的中间 Run 随后主动取消。

最终私有 Run 的工作流验收为 `ready_for_delivery=true`，只有以下非阻断项：真实权利人、日期、运行环境和账号仍需申请方填写；源代码模板本身没有可继承的 styleId 集合。独立验收未发现 blocker。

### 8.3 最终产物

Files API 恰好返回三个 `artifact_contract` 产物，`truncated=false` 且全部 `integrity=verified`：

| 产物 | 大小 | SHA-256 |
|---|---:|---|
| `product-source.zip` | 96,715 B | `a03ea36c2ebb32e244b7716eceea5a67916224bc6822590720d8712da3b4cbb1` |
| `product-preview-screenshots.zip` | 1,091,790 B | `a610fe05933ffb9f81a13f085801240e915c52f8c0dac3b7bfb03af669c83e2e` |
| `software-copyright-package.zip` | 1,253,772 B | `28f17dd8ab1bb8b344722ee64a6e2bd9c323738b8e56eaac679750d0e9876ba9` |

删除私有验证 App 前，三件最终候选已复制到 `deploy/mira-server/backups/code-copyright-v15-20260810-154000/validation-artifacts/` 并重新核对 SHA-256。

宿主 LibreOffice 独立转换与 `pdfinfo` 页数结果：申请表 2 页、功能特点 2 页、源代码 80 页、软件说明书 7 页、设计文档 5 页、产品测试功能表 1 页、环境表 1 页。说明书有 9 个有效图片关系和 9 个媒体文件，逐张 SHA-256 与本次截图完全一致。

最终资料包顶层恰好 10 个文件；无 U+FFFD、双花括号占位符、旧项目内容、旧作者/公司/WPS 元数据、外部关系、宏、OLE 或内部 runtime 路径。截图包含 9 张 `1440×1000` PNG、`manifest.ok=true`、无 `.chromium`。源码包包含 `package-lock.json`，无 `node_modules`、`.next`、缓存、运行数据库、日志或秘密文件，16 项 runtime 检查全部通过。

内部 manifest 的 `outputs` 记录 8 个模板派生文件，没有记录 `生成报告.md` 的 size/hash；外层 artifact SHA-256 已覆盖报告完整性，因此按非阻断覆盖提示保留。manifest 自身不记录自哈希是合理的。

验收标准与结果：

- graph 仅两个节点允许 `ask_user`；正式 smoke 的第一次确认包含完整 context、2 组真实选择和每组 1 个推荐项：通过。
- 源码包包含 lockfile、真实 SQLite 读写、构建和 HTTP 验证记录：通过。
- 截图包至少包含 5 个真实页面 PNG，且不存在 `.chromium`：通过，实际 9 张。
- 说明书包含真实图片关系和媒体文件，且继承模板结构：通过。
- 所有交付物不包含 `�`、未处理的 `{{...}}` 或旧模板业务内容：通过。
- Files 页只显示三个声明产物，完整性状态均为 `verified`：通过。
- 资料包存在 high severity 问题时，最终页不得显示“可交付”：中间候选验证通过；最终候选无 high 且显示可交付。

### 8.4 最终双轴审查

以 `HEAD`（`9af709e000aec67906d39c818702f07fa63b0a53`）为固定点，对全部未提交 diff 和 untracked 文件执行最终独立审查：

- Standards：未发现 AGENTS.md 硬性违规。初审唯一判断项是 `run_artifacts.py` 两处路径规范化逻辑可能漂移；已提取共享 `_workspace_artifact_location()`，保留 legacy/versioned 各自的绝对路径与文件存在性语义，复审通过。
- Spec：未发现原始需求缺失、范围膨胀或疑似误实现；逐节点审查、整体流程、输出约束、必要代码整改、本地文档和精简提示词均已覆盖。
- 收尾修改后定向执行 artifact、Trace 和 Run executor 共 86 项测试，全部通过；`git diff --check` 通过。
- 收尾 helper 已同步到部署后端；源码与部署副本 SHA-256 一致，公开入口和后端直连健康检查均通过。

剩余运维提示：部署启动时报告 `data/runtime` 约 5.8 GB，主文件系统可用空间约 1.5 GB（99% 已使用）。本次未执行宽泛或不可审计的清理；后续应按已结束 Run/Session 建立 scoped home 与 npm/cache 的定向保留策略，以免大体积 artifact 运行受磁盘空间影响。

## 9. 发布与回滚

发布已按短维护窗口执行：

1. 已备份部署 SQLite、目标 App graph 和 8 个上传素材。
2. 已部署源码、Codex runtime、Office 隔离 helper，并通过 health 与 smoke。
3. 已在私有 App 完成下游真实生成、工作流验收和独立验收，并发布私有 v5。
4. 已对正式 App 预检、下架 v14、PATCH 全量 graph、复检并发布 `public / cloneable` v15。
5. 已在正式 App 创建 13 节点/26 边 snapshot 的 smoke Run，验证到第一次预期人工确认后主动取消。
6. 已备份最终候选产物并删除私有验证 App；其 AppVersion、Run 和 workspace 已不可从 UI 恢复，正式 v15 与发布前数据库备份保留。

若发布失败，使用发布前 AppVersion 的 graph 重新 PATCH 并发布。旧 Run 使用自己的 graph 快照，不随 App graph 回滚或更新。
