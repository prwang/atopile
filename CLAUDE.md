# CLAUDE.md — /kicad_wksp 项目指引

## 协作规则

- **禁止不透明的 auto-memory**：不要写入 `~/.claude/.../memory/` 等用户不可见的持久化记忆。所有跨会话需要记住的偏好、决策、约束，一律写进本文件（`./CLAUDE.md`），随项目可见、可审、可版本控制。
- **后台进程必须回收**：每次 `git commit` 前、以及每次向用户交付（结束回合）前，**不得留下任何在跑或挂死的后台进程/监控 shell**（`ato build`、pytest、`until … sleep` 轮询、被自动 background 的任务等）。规则：① 用 `run_in_background` 起的活就要等它跑完或显式 kill；② 监控用 `until <cond>; do sleep N; done` 一次性等到位，别留轮询；③ 交付/commit 前跑一次 `ps` 自检，确认无 `/tmp` 工作区或 ato/pytest 残留再继续。教训：PATH 顺序错配（`~/.local/bin/ato` 旧版排在 venv 前）会让 BuildQueue spawn 旧 worker、orchestrator 崩在 pydantic/sqlite 但主进程挂死空转——这类僵尸最易遗漏。
- **长期文档 = 单一现状定义，禁止补丁摞补丁**：BACKLOG.md / CLAUDE.md 等长期文档是「我与未来 agent 的唯一权威」，误读后果严重——**歧义本身就是 bug**。每条事实只能存在**一份最新、精确、自包含、无歧义**的定义。**禁止**：① 用删除线（`~~…~~`）保留旧错误文字与新结论并列；② 把同一事实写成「旧结论（日期A）→ 更正（日期B）→ 再更正（日期C）」的日期补丁层叠。**更正即就地重写**那条定义本身，使读者无需追溯历史即可读到唯一正确版本。允许且仅允许保留一处**简短的「曾因 X 踩坑」教训行**（帮助理解为何如此规定），但绝不能让过时结论与现行结论并存、互相矛盾。写入前自检：若一个新读者只读这一段、不看 git 历史，会不会被误导？会则重写。

## 编码与测试纪律（写代码/测试前必读）

这些是 fork 内已验证的方法纪律（B/C/C3 全程照此做），新章节（D–F）必须沿用：

- **双向接口 pinning（consumer-oracle）**：写消费者前，先用**消费者的真接口**把上游验收钉死——契约从"下游实际读什么"反推，不凭空发明。已用：B→C 用现役 `LayoutSync._generate_net_map` 当 oracle（IR 重建必 == 实测函数）、C→E 用真 router 当 oracle；D→E/F 同理。
- **tests-first 严格 xfail 棘轮（S0）**：实现前先把验收契约写成测试，全部 `strict-xfail`（符号/导入探针守门，如 `_C3_LANDED`）；实现靠**移除/失活 xfail** 翻绿（模块就位即 inactive）。**特征未落地却 XPASS = 测试 bug，不是进度**；半落地（改名只改一半）应保持红灯。协议 + 接口 delta 写在契约测试的模块 docstring 里。
- **代码 = 文档 SSOT（禁熵增）**：完成的设计/不变量写进代码（模块/测试 docstring、同名测试、schema），BACKLOG 只留"结论 + 代码指针"，不重复维护细节。
- **测试必须真咬（防"绿但失义"）**：① 自然语料快照对"按位置绑定"类 bug 大多失明——必配变异/corrupter 自检（语义动则 IR 动，否则红）；② 对抗性断言用**按构造定答案**的最小 fixture（如 C3.9 inter-room），不靠"跑了就绿"；③ 负向测试证明 schema/校验真会拒；④ **不静默砍覆盖**——覆盖被裁（top-N、不重试、跳过、移到别处）必 `log`/注释说明，删旧测试必把其不变量迁走。
- **显式不静默（loud-or-nothing，S5a）**：不可表示/不支持的输入必须响亮（警告或抛错），绝不产出静默半成品；未知 token/键、裸 net 名、无稳定地址 net 一律报。

## 项目背景与已定决策

**项目**：在 atopile + KiCad 10 + KiCadRoutingTools 之上构建确定性文本化布局工作流（`.ato` = 电路事实来源，`layout.yaml` = 布局事实来源，KiCad 文件为派生产物，结构化 JSON 诊断供 PCB Layout SKILL 迭代）。

- 架构总览见下节（其活性内容已折叠自原可行性调研报告，该报告 2026-06-19 退役、不再维护）；任务清单：`BACKLOG.md`（同仓）；atopile monorepo 自身说明见 `OPERATING.md`。
- **已定决策**（2026-06-12）：
  - 不 fork KiCad 10；
  - fork 基线 = atopile 仓库 HEAD（最后 commit 2026-03，bugfix，稳定）；
  - Python 3.14 开发环境：原定 miniconda，**已修正为 `uv sync` 即可**（zig 编译器由 pip 构建依赖 `ziglang==0.15.1` 提供，Python 3.14 用 uv 托管版）；克隆必须 `git fetch --tags`（可走上游 https），否则 setuptools-scm 产出非 SemVer 版本号、`ato` CLI 启动即崩；
  - **room 载体 = 每 footprint 的 `sheetname`**（值 = ato 地址前缀），atopile 建零个/碰零个 group（曾计划用 KiCad named group 当 room 来源，C3 2026-06-15 否决——group 是 KiCad 通用选择原语、无 provenance 槽，不堪当来源；详见 BACKLOG §C3）；
  - **v10 方言全面迁移提级为 P0.1/P0.2，高于原 P1**（长痛不如短痛，不做长期读 shim）：P0.1 = 测试底座（语义视图快照 + net 绑定 corrupter，防 false negative），P0.2 = 迁移本体，**阶梯式 S0–S7**（2026-06-12 重排：验收测试先行 S0，pyzig 所有权联修 S1 最先动 zig，net 模型 S4 一次翻 19 个 xfail 开关；每步带"转绿/保持绿"双清单，带红灯不得进下一步），写方言目标 = v10，完成后 examples/fixtures flag-day 升级。详见 BACKLOG §P0.2。
  - **P0.1 已完成**（2026-06-12）：88 通过 + 26 strict-xfail（xfail 即 P0.2 的验收开关）。变异自检证实:自然语料快照对"按位置绑定"类 bug 大多失明（4 个里 3 个照常通过），corrupter 是必需件而非锦上添花。
  - **P0.2 S0–S4 已完成**（2026-06-12）：v10 读侧闭环（net 编号按名排序合成,Python 模型零变化）,转绿 21 开关,剩 8 xfail = S5 的 7 + S7 的 DRC oracle。
  - **GUI 演示范围决策**（2026-06-13）：① 一切不支持构造**不得静默失败**——S5a 未知键响亮化机制兜底（警告/strict 抛错）；② S5b 保真集 = 最小集（group + rule area placement + 既有清单）,teardrop/蛇形等长/via padstack 仅警告（schema 补全立 P1+ 条目）,GUI 演示脚本回避这三类操作；③ S7 增设"GUI 编辑回环验收"门（模拟编辑→受管重写→无损 + DRC）,**未绿不演示**。详见 BACKLOG §P0.2 S5/S7。

## 架构总览（数据流 + 模块图；实现细节以 code 为 SSOT）

**可行性结论**（静态调研 + 运行时验证，2026-06）：**不 fork KiCad 10**——room placement
rule area / group / component-class 赋值所需的全部 KiCad 对象均可由外部生成、且经
`kicad-cli pcb upgrade` 完整 round-trip（实测）。方案 = **fork atopile**（加 layout sidecar 层）
+ **vendor KiCadRoutingTools**（加分阶段执行 + 结构化诊断；现为 submodule `vendor/KiCadRoutingTools`）。

**数据流**：`.ato`（电路真相）+ `layout.yaml`（布局真相）→ `ato build` 生成派生
`.kicad_pcb` + `layout_ir.json` → KiCadRoutingTools 分阶段布线 → 结构化 JSON 诊断 →
PCB Layout SKILL 改文本 → 重建。命令式反馈（"这条没布通、空间没了，怎么办"）活在文件**外**的
build→诊断→SKILL 改 plan→重建闭环里，**不在** `layout.yaml` 内（理由见 `layout_plan.py` 模块 docstring）。

**atopile build pipeline**（SSOT = `src/atopile/build_steps.py`）：实例化 app（ANTLR→Zig
TypeGraph→实例图）→ load_pcb → pick_parts → prepare_nets → update_pcb（transformer + room
sheetname 同步）→ `generate_layout_plan`（§D：placement rule area + `<t>.layout_plan.json`）→ bom/manifest。

**layout sidecar 模块**（`src/faebryk/exporters/pcb/layout/`，各带契约测试，细节读 code/docstring）：
- `layout_sync.py` — 从板回读 room（sheetname）布局（`pull_room_layout`，`_get_room_name:68`）。
- `layout_plan.py` — `layout.yaml` 意图模型（D2 `rooms`/`route_stages`；D-Tier2 `BundleStage` 判别联合；D-Tier3 桶①已落地：`Placement`+`resolve_placement`+`resolve_component_pose`（文本/reuse 优先级）、`Room.polygon`、`board` 段 `BoardOutline`/`Stackup`/`StackupLayer`+`stackup_layers`/`outline_bounds`、impedance→stackup 硬依赖校验）。
- `rule_area.py` — 每 room 一个 placement zone（D3，**只** `enabled`+`sheetname`，见约束 §source_type；D-Tier3：`Room.polygon`/rotation（CCW 绕首点）/layers 已接线生效，不再静默忽略）。
- `room_ops.py` — 强制 via / room copy / `pad_board_xy` 坐标变换（room 局部→板坐标，含 rotate；§C 已实现）。
- `bundle_geometry.py` — D-Tier2 bundle 横截面 offset 几何 SSOT（已落地；`generate_layout_plan` 经 `bundle_artifact` 注入 `<t>.layout_plan.json`，契约 `test_bundle_contract.py` + e2e `test_bundle_build.py`）。
- `libs/kicad/layout_ir.py` — `layout_ir.json` = **bridge②**（ato 地址 → net/pad/room）；布线与诊断按地址定位，不解析 KiCad 文件。

**布线器**（submodule `vendor/KiCadRoutingTools`，跑在 **system python3**，非 venv——rust 内核 ext 未为 venv 构建）：
`route.py:batch_route` / `route_diff.py:batch_route_diff_pairs`（+ E-Tier2 新增 `route_bundle.py:batch_route_bundle`）；
`return_results=True` 结构化结果 + stdout `JSON_SUMMARY` + `BlockingInfo`；`build_router.py` 下载预编译二进制。
**跨 stage 前序铜 = 不可撕的硬障碍**（白送的优先级锁），优先级由 stage 顺序表达（BACKLOG 关键事实 16）。

**诊断（§F，规划中）**：聚合 `results_data` + `BlockingInfo` + `kicad-cli pcb drc --format json`
→ 经 layout_ir 用 uuid/地址反查 → `diagnostics.json`。风格基线可借鉴沙盒参考 clone：kicad-happy 的
`rule_id`/`severity`/`report_context`/`confidence` schema、Ki-Stack 的"改动后渲染 + DRC 验证"流程。

## 关键实测约束（改代码前必读）

- KiCad 解析器遇未知 S-expression token 直接报错——自有元数据只能放 footprint `(property ...)` 或 sidecar 文件，禁止发明自定义字段。
- **`ZonePlacement.source_type` / `source` 是 fileformats schema 的错建字段（建模了 KiCad 的内存/protobuf 形态，非文件语法）——写出即 SEGFAULT kicad-cli loader【2026-06-16 D3.5 隔离 + KiCad 源码核实】**：
  - **KiCad 文件语法**（parser `pcb_io_kicad_sexpr_parser.cpp:8659 case T_placement`、formatter `pcb_io_kicad_sexpr.cpp:3113`）：`(placement (enabled yes|no) <唯一 source 子项>)`，source 子项 = `(sheetname "X")` | `(component_class "X")` | `(group "X")` **三选一**。**无 `source_type` token、无 `source` token**——source TYPE 由"出现哪个子项的 token 名"决定（parser 读到 `sheetname` 即 `SetPlacementAreaSourceType(SHEETNAME)` 并把值存进 source），即 (type, source) 在文件里**融合成一个"名=type、值=source"的 token**。
  - `pcb.zig:824 ZonePlacement` 却拆成独立 `source_type` + `source` 字段——那是 KiCad **内存模型**（`m_placementAreaSourceType`/`m_placementAreaSource`）和 **IPC protobuf**（`zone.cpp:267 set_placement_source_type`）的形状，**不是 S-expr 文件形状**。schema 把两条通道混了。
  - 实测：带 `source_type` 的 placement → `kicad-cli pcb upgrade --force` 返回 -11（SIGSEGV）；去掉即 rc=0。**静默 footgun**：`kicad.dumps` 照常成功、KiCad 事后才死。
  - **规则**：生成 placement rule area **只**设 `enabled` + `sheetname`（= SHEETNAME 源的完整正确表达，KiCad 由 sheetname 子项推出 source type，且其默认本就是 SHEETNAME）；**绝不**设 `source_type`/`source`。这不是"绕过有副作用"——是唯一正确表达，删之零功能损失、纯获不崩。D3 `rule_area.py` 已守，回归 `test_generated_placement_has_no_source_type`。`(fill ...)` 子句可省（KiCad load 自合成默认；为 upgrade 零 diff 才显式写规范形）。
  - **对 D5 的硬约束**：component_class placement **不能**靠 `source_type=COMPONENT_CLASS`+`source`（会序列化成文件非法的 `(source_type component_class)(source X)` → 同样崩）；须走 `(component_class "X")` token，即**先修 fileformats schema**（加 `component_class` 字段或把 type+source 融成单 token），是 D5 前置。
- **layout.yaml 里 ato 地址带实例下标 `[N]`（如 `sub_chains[0].r_chain[0]`）——YAML flow 序列 `nets: [a[0]]` 会 ParserError；用 block 列表（`nets:`\n`  - a[0]`）或给 flow 项加引号【2026-06-16 D2.6 实测】**。loud-or-nothing 不破：flow+方括号 → 响亮 ParserError；裸保留字/数字样 token（`on`/`yes`/`0x10`）→ YAML 静默 coerce 成 bool/int，但被 pydantic str 字段响亮 ValidationError 拒（已验四例全 loud）。唯一理论静默误解析向量（`a: b`→dict、`a #x`→截断、`[` 打头）需 ato 地址不可能含的字符（空格/冒号空格/前导方括号），故无静默腐化洞。
- KiCad 重存会清理 net-0 悬空铜（连带空 group）——写入铜层的几何必须挂真实 net；引导/标记几何放 User.x 图层（User.1=引导走廊、User.2=禁布区，与布线器约定一致）。
- **确定性由 route/zone 插入序承载**：atopile 不再建 group（room 载体 = footprint sheetname），无成员表可乱序（曾因 atopile group members 排序不稳定加过 A1/A3 排序修复，C3 后随 group 方案一并退役；BACKLOG §C3）。
- **UUID 不透明 ⟹ 确定性 = 语义等价，不是字节等价【2026-06-15 重定，旧"增量稳态/字节一致/必须入库"结论作废】**：atopile 的 UUID（footprint/pad/…）= 纯 uuid4 随机（"FBRK" 后缀已删，§G uuid 不透明原则）。**推论（用户定调）**：uuid 既然不透明、无意义，就**不能 desire 字节等价**——任何断言 `read_bytes()==baseline` 的测试都是范畴错误，必须改为**语义等价**（`semantic_view`：位置 + 连通性按 net 名 + 结构，剔除 uuid/net 编号）。已改：`test_group_determinism.py`、`test_room_migration_e2e.py::test_C3_3` 三处 byte 断言 → semantic。**进而**：生成态 `.kicad_pcb` 不再需要作为"字节锚"入库（CI **可**两次从零构建比 semantic_view）；仍入库者只剩**输入态**（如 `examples/layout_reuse/layout/sub/sub.kicad_pcb` = 被复用的源布局）与 **parser 语料样本**。注意 `semantic_view` 的 group 成员仍用 member uuid 表达（残留），完整 oracle 应改按成员地址。
- **手工命名 group 的内容曾被每次构建静默删除**（上游 48fe6e18 清理范围过宽）——fork 先修（`_is_managed_group`），**C3 后更彻底**：atopile 碰零个 group，手工 group 按构造永存，`_is_managed_group` 已删（BACKLOG A4/§C3，回归 `test_group_determinism.py::test_manual_edits_preserved`）。
- **方言门【2026-06-13 S7 写侧已切，旧"写 v9"结论作废】**：写方言 = **v10**（`pcb.zig:12-14` 注释 "S7 flag day (2026-06-13): write dialect is now v10"，`KICAD_PCB_VERSION=20260206`，`dumps` 无条件 stamp `pcb.version`，pcb.zig:1500）。读 v9/v10、写恒 v10（upgrade-on-write）。**旧纪律（管理板不跑 upgrade、不在 GUI 保存）已解除**。**唯一遗留 = S7-(b) fixture flag-day**：已入库的 example/fixture 板尚未重生成成 v10+sheetname 语义——其中 `fileformats/kicad/v10/pcb/layout_reuse_top.kicad_pcb` 仍是 **C3 前快照**（带 4 个 atopile 建的 group）；但**已无任何测试把 group 当 room 源读**——fixture 重生成是收尾工作、**非 gate**。收尾原则（2026-06-15 用户定调）：**atopile 不存在任何 ato→group 映射路径**——
group 在测试里**只有一种合法语义 = 用户手动 grouping、回读保真**（= `test_manual_edits_preserved` A4，
按构造注入用户 group 并断言存活；parser corpus 可留含 group 的板，但语义是"用户内容回环"，非 atopile 产物）。
曾经的一次性迁移证明（读 `pcb.groups` 当 room 源的 corpus 测试 `test_C3_1_corpus_groups_are_address_prefix_recoverable`）**已退役**（`test_room_migration_contract.py` 注释记其移除——保留它会延续 C3 已废除的 ato→group 耦合）；room 测试一律读 sheetname（现役 `test_C3_1_inline_room_equals_address_prefix_grouping`）。（C3 符号清理已完成：
`_get_room_name` 就位于 `layout_sync.py:68`，`_get_group_name`/`transformer._add_group`/`is_marked`/
`_is_managed_group` 均已从代码删除。）
- **`kicad.loads` Path 缓存【✅ 2026-06-12 P0.2 S1 已修】**——现按 (mtime_ns, size) 指纹失效；重写后重读拿到新解析,未变文件返回同一对象(shared-object 语义保留)。
- **pyzig 所有权 use-after-free【✅ 2026-06-12 P0.2 S1 已修】**:历史症状=包装被 GC 后 `.kicad_pcb` 等子对象悬空、内存复用后静默读出另一块板的数据。现子对象包装持 owner 强引用链(child→parent→root),`loads(...).kicad_pcb` 写法安全;`kicad.loads` Path 缓存同提交加 (mtime_ns, size) 指纹失效,`kicad.dumps(obj, path)` 回写缓存保持 dump→load 同对象。回归测试 `test/libs/kicad/test_pyzig_ownership.py`。
- **v10 文件没有顶层 net 表**(比"去编号"更彻底):net 只存在于引用处 `(net "名")`,zone 的 `net_name` 冗余字段也没了,无网 zone 省略 net 子句——v10 读侧 `pcb.nets` 只能由引用扫描合成,合成序必须与容器迭代序无关(BACKLOG M0/T4)。
- **EasyEDA API 403 真相【2026-06-13 实测纠正，旧记"限流 403"是错的/不完整】**：
  `easyeda.com/api/products/...` 前面是 **AWS CloudFront WAF**，至少两条独立规则同时生效，
  旧笔记把两者混为"限流"，且漏了主因：
  1. **User-Agent 允许/拒绝名单**（确定性，与速率无关）：实测同一时刻同一 URL，
     `curl/*`、node(undici) 默认 UA → 200；`Mozilla/*`、`python-requests/*`、
     以及 atopile 自带 `easyeda2kicad` 库**硬编码的 `User-Agent: easyeda2kicad v<版本>`**
     （`.venv/.../easyeda2kicad/easyeda/easyeda_api.py:24`）→ **403**。
     即 atopile 取件被它自己的 UA 字符串确定性挡掉，**与限流无关**。
     一行可修：把该 UA 改成被放行的值（实测 `requests` + `User-Agent: curl/8.5.0` → 200，返回真 JSON）。
  2. **按 IP 的速率/信誉规则**：突发请求（调试连发、或一次构建批量取多件）会触发 CloudFront，
     此后**即使是被放行的 UA 也短时全 403**，响应体是 CloudFront 的
     `403 ERROR / Request blocked / too much traffic` HTML 页（非 JSON → `requests.json()`
     报 `Expecting value: line 1 column 1`，正是历史上误判为"空 JSON/限流"的症状）。
  - 故"helper 能跑、atopile 不能"= helper 用了放行 UA 且只发一次；atopile 两条都踩。
  - **已固定的应对策略（D1，`src/faebryk/libs/picker/easyeda_resilient.py` = 行为权威）**：
    `ResilientEasyedaApi`（drop-in 子类 `easyeda2kicad.EasyedaApi`），三件套——
    ① **放行 UA**：`ALLOWED_USER_AGENT = "curl/8.5.0"`，治规则 1（确定性，必需）；
    ② **proactive 限流**：每个 GET（含首发、含 happy path）前先 sleep
       `uniform(0, INITIAL_JITTER_S=0.3s)`，把冷启动串行取件从一开始就摊开——不是只在 403 后才退避；
    ③ **reactive 退避**：WAF block（403 或本该 JSON 却 HTML body）按 full-jitter 指数退避重试
       （`BASE=0.5s`,`MAX=8s`,`MAX_ATTEMPTS=5`）；真 200 `success:false`（无此件）立即返回不重试。
  - **不需要全局 broker/跨进程限流（已论证，2026-06-16）**：QPS 限制是 CloudFront **按 IP** 的——
    即跨进程/跨 case 全局共享一个预算。但 atopile 取件**本就串行**：测试无 xdist（`addopts` 无 `-n`、
    `pytest-xdist` 非依赖）、e2e build 经 `run_live` 的 `process.wait()` 串行（每 case 跑完才下一个）、
    单次 build 内 `get_raw` 是 `@once` 记忆化且无 ThreadPool 扇出 → 任一时刻至多一个 build 子进程、
    一条串行 GET 流。无并发可协调 ⟹ proactive 单进程 spacing 已足够，**不建文件锁/常驻进程**
    （后者还违反"后台进程必须回收"纪律）。仅当未来真并发取件（`-n auto`/同 IP 并行 CI）才需重谈。
  - **download-once 缓存（offline 测试基石）**：`FBRK_PARTS_NO_REFRESH=y`（`part_lifecycle.py` 的
    `PARTS_NO_REFRESH` ConfigFlag）令磁盘缓存权威、永不按 1 天 TTL 重取——warm build/全测试套件
    **零网络调用**（故上面的限流/退避**只**作用于真冷取路径）。测试 conftest/`_build` 已注入该 flag；
    需要时 seed `test/common/resources/easyeda-cache` 到工程 `build/cache/parts/easyeda`。
  - **实测验证（2026-06-16）**：15 件真实 LCSC 冷取串行跑 → 15/15 成功、WAF block 0 次、
    总 4.9s（0.32s/件均，含 jitter）。结论：UA 是确定性主治、proactive jitter 是突发保险（warm 零成本）。
  - **对 S7 验收的影响**：EasyEDA 取件**不是**不可逾越的环境限流——UA 修复后本沙盒可取件，
    E2E/examples/BOM 验收可在本地跑（注意退避，避免连发触发速率规则）。
- atopile **不生成 .kicad_sch**，但 footprint `sheetname`/`sheetfile` 是独立持久字段、KiCad `pcb upgrade` 逐字保真（C3 实测 `test_C3_4`）——多通道/room 走 **sheetname**（rule area `(placement (sheetname))`），不走 group。注意 KiCad 拥有并重写 footprint `path` 成 UUID，故 atopile **不写 path**。
- `kicad-cli pcb drc --format json` 无结构化 net 字段（net 名嵌在描述文本）——诊断层用 items[].uuid 经 layout_ir 反查。
- KiCadRoutingTools 已有结构化结果（`return_results=True`、stdout `JSON_SUMMARY`、`BlockingInfo` dataclass）——诊断层是聚合+映射，不是新建结果 API。

## 沙盒环境现状

- `/kicad_wksp` 下：**`atopile` = fork 主仓**（KiCadRoutingTools 现为其 submodule `atopile/vendor/KiCadRoutingTools`，URL `git@github.com:prwang/KiCadRoutingTools.git`）；仅供参考的旁支 clone：`packages`、`ReplicateLayout`、`kicad-happy`、`Ki-Stack`、`kicad`（GitLab 浅克隆）。
- 已安装：kicad-cli 10.0.3（PPA `kicad/kicad-10.0-releases`）、rust router 预编译二进制（`build_router.py` 自动下载）、python3-numpy/scipy/shapely（apt）。
- **atopile = 唯一一份,fork 的 venv**（2026-06-14 去熵）：bootstrap 的 `uv tool install atopile 0.15.7`（曾在 `~/.local/bin/ato`）**已 `uv tool uninstall atopile` 卸除**——它是 PATH footgun 源头（旧版排在 venv 前→挂死 build "Picking parts" + 旧 schema 重建 `build_history.db`）。现在**没有任何 `ato` 在 PATH 上**；fork 一律显式调用 `/kicad_wksp/atopile/.venv/bin/ato` 或 `/kicad_wksp/atopile/.venv/bin/python -m atopile`（subprocess 构建需把该 `.venv/bin` 前置进子进程 PATH，见 BACKLOG §B B1b）。要装回:`uv tool install --python 3.14 atopile`（可逆）。
- 运行时验证用例已固化为测试（`test_group_determinism` / `test_room_migration_e2e` / `test_rule_area_contract` / `test_router_smoke_batch_route` 等）+ 入库的 `examples/layout_reuse`（被复用的源布局）；旧 `probe/layout_reuse` 手工验证工程已于 2026-06-19 退役删除（其验证项均已迁入上述测试）。
