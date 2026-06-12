# BACKLOG — 文本化布局工作流（deterministic text-first layout layer）

## 背景

我们在 atopile + KiCad 10 + KiCadRoutingTools 之上构建一条确定性的、以文本为唯一事实来源的
PCB 布局布线工作流：`.ato` 描述电路（电路事实来源），`layout.yaml` 描述布局意图
（room 划分、坐标、布线阶段、关键过孔——布局事实来源），KiCad 工程文件为派生产物；
布局/布线工具输出机器可读的结构化诊断（JSON），使自动化的 **PCB Layout SKILL** 能以
"修改文本 → 重新生成 → 读取诊断 → 再修改"的方式快速迭代。

本清单由前期方案预估经代码级调研修正后得出。调研证据、运行时验证记录与数据格式草案见
`/kicad_wksp/KicadDecisions.md`（自包含，含文件:行号级引用）。

**总体决策**：不 fork KiCad 10（所需对象已验证可外部生成并完整往返）；
fork 本仓库（atopile）与 KiCadRoutingTools。KiCadRoutingTools 侧任务单列在 §E
（同一计划，不同仓库）。

**已定决策**：fork 基线 = 本仓库 HEAD（最近提交为 2026 年 3 月 bugfix，稳定）；
Python 3.14 开发环境用 miniconda 安装。

## 对前期预估的修正摘要

| 前期预估 | 修正 | 依据（详见 KicadDecisions.md） |
|---|---|---|
| 首选 "ato module → KiCad hierarchical sheet" 映射 | **删除该方案**。atopile 不生成 .kicad_sch 原理图，sheet 路线不存在；多通道布局的来源固定走 **named groups** | 实测构建产物只有 .kicad_pcb |
| 需要从零实现 group 生成器 | **降级为增强现有机制**。atopile 已写 groups（名称=模块地址；UUID 仅**后缀**由名字派生，前缀是 uuid4 随机数（`fileformats.py:150-165`），稳定性靠从已有 .kicad_pcb 回读，非完全确定性派生） | 代码级复核修正了此前"确定性派生"的误判 |
| "待验证：布线器是否有结构化结果 API" | **已证实存在**（`return_results=True`，`route.py:212-213`），且标准输出另有 `JSON_SUMMARY` 结构化行（含逐 pad 失败坐标）。诊断层从"新建结果 API"降级为"聚合+地址映射" | 实测运行 `route.py` |
| 直接在 .kicad_pcb 写自定义字段存元数据 | **禁止**。KiCad 解析器对未知 token 直接报解析错误（`pcb_io_kicad_sexpr_parser.cpp:1388`）。元数据只能走 footprint `(property ...)`（已实测可完整往返）或独立 sidecar 文件 | KiCad 源码 + 往返实测 |
| 可能依赖 KiCad 的 Repeat Layout（多通道布局复制） | **不依赖**。该功能只有 GUI 入口，无 CLI/IPC。room 复制自行实现（ReplicateLayout 插件的锚点变换算法可移植，`replicate_layout.py:47-66`） | KiCad 源码 `multichannel_tool.cpp` |
| room 来源可选 sheet/class/group | **首选 group**。component class 的类定义存于工程文件（.kicad_pro），板内只有按 footprint 的赋值；sheet 不存在 | KiCad 源码 |
| （未预估） | **新增三项约束**：group 成员排序不稳定（破坏字节级确定性）；net-0 悬空铜会被 KiCad 重存清理（引导几何必须放 User 图层）；DRC JSON 无结构化 net 字段（需 uuid 反查层） | 实测发现 |
| 谨慎使用旧版 SWIG Python 绑定 | **完全禁用**。KiCad 10 已移除板级操纵 API | KiCad 源码 |

---

## A. P0 — 确定性与基础修复（第一阶段前置，本仓库）

- [x] **A1. group 成员排序**（2026-06-12 代码级复核 + probe 复现后修订；**已修复并验证**：
  `layout_sync.py:pull_group_layout` 末尾全量排序去重；变异验证确认回归测试可捕获）

  **成因（已复现）**：原计划写"sync_groups 序列化前排序"——该排序在 HEAD **已存在**
  （`layout_sync.py:149`，`sorted(current_members)`，commit eae6548a 引入）。真实漂移源是
  `pull_group_layout`（`layout_sync.py:478-480`）：`new_group_uuids` 是 str UUID 的
  **set 差集**，随后按 set 迭代序逐个 `append` 到已排序的 members 末尾；Python 字符串哈希
  按进程随机（PYTHONHASHSEED），故追加顺序每次构建不同。

  **触发条件**：pull 仅在某 group 的 footprint 全部为新增时运行（`build_steps.py:798-813`，
  即首次构建 / 新增模块实例的那次构建）；下一次构建 sync_groups 全量重排 → 相邻两次构建
  仅 members 顺序不同。**稳态（无新增）的连续构建在 HEAD 已字节级一致**
  （probe/layout_reuse 实测 diff 为空）；复现方法：副本工程删除 top.kicad_pcb 后连续构建
  两次，diff 仅剩 `(members ...)` 重排——与 KicadDecisions.md §2.3 的实测现象完全吻合。

  **修复点**：不在 sync_groups，在 `pull_group_layout` 末尾——追加后对 members 全量排序去重
  （使 pull 的终态 = 下次 sync_groups 的重排结果，构建序列立即收敛），约 3 行：
  `kicad.clear_and_set(group, "members", group.members, sorted(set(group.members) | new_group_uuids))`。
  两个调用方（`build_steps.py:813` 构建路径、`cli/kicad_ipc.py:142` KiCad 插件路径）共用此
  方法，单点修复即可。

  **半径 / 可能 break 的部分（已逐一核查，均不依赖 members 顺序）**：
  - KiCad 本体：members 语义为集合，GUI 与 `kicad-cli pcb upgrade` 不依赖顺序；
  - 仓库内消费方：`layout_sync.py` 自身（set 运算）、`kicad_ipc.py:132`（issubset）、
    `layout_server/pcb_manager.py:610-633`（递归展开+去重）与 `:706-724`（分桶），
    顺序最多影响 UI 列表展示序；
  - KiCadRoutingTools 不解析 groups（外科手术式文本写出），不受影响；
  - 仓库现有测试零处引用 LayoutSync——无需改测试，但也意味着当前无回归覆盖（见验证）。
  - 存量已提交布局不会产生一次性 churn：稳态文件本就是 sync_groups 排好序的。

  **明确的范围边界（实测新发现）**：`gen_uuid`（`fileformats.py:150-165`）= `uuid4` 随机数
  + "FBRK" hex 后缀；footprint/pad/pull 复制元素的 UUID 首次生成即随机，仅靠从已有
  .kicad_pcb 回读保持稳定（两次独立 fresh build 实测相差 330 行，全部是 UUID 值）。
  因此 A1 交付的是**增量稳态确定性**（同一工作副本连续构建一致），不是 clean-checkout
  可重现性；后者需地址派生 UUID，半径大得多（碰撞处理、KiCad 唯一性预期），明确不做。
  推论：`.kicad_pcb` 必须入库随仓库走，CI 确定性测试必须是"在已有布局上 build→build→diff"，
  不能是"两次从零构建比对"。

  **验证方案**：
  1. 回归测试（新增）：fixture PCB 上跑 `sync_groups`+`pull_group_layout`，断言
     members 已排序；并以 `PYTHONHASHSEED=0/1` 两个子进程各跑一遍，断言序列化字节一致
     （直接命中哈希随机化成因）；
  2. E2E（并入 A3 的 CI 用例，三种状态都要覆盖）：(a) 空布局首建→再建→diff 为空
     （今日唯一失败的场景，已复现）；(b) 稳态连续两建 diff 为空（防回归）；
     (c) 新增一个模块实例→建两次→diff 为空（pull 增量路径）；
  3. 插件路径：`kicad-ipc` layout-sync 动作执行两次，文件一致；
  4. `kicad-cli pcb upgrade` 往返后 groups/members 保留（A3 既有项扩展）。

- [x] **A2. `keep_designators` 回归测试（前提已过时，从"改默认值"降级为"验证固化"）**
  **已固化**：`test_group_determinism.py::test_steady_state_and_incremental_add_deterministic`
  断言新增实例后原有位号原样保留（实测 R1–R9 不变，新实例得 R10–R12）。
  代码级复核：HEAD 的默认值**已经是开启**（`config.py:592`
  `keep_designators: bool | None = Field(default=True)`），load-pcb 步骤回读已有位号
  （`build_steps.py:636-637`）；`attach_random_designators`（`designators.py:24-93`）只给
  **无位号**的新组件补号，且按模块地址 natsorted（`designators.py:50-52`），本身确定。
  原任务"fork 内改默认"无事可做；改为把验收固化成测试。
  验收不变：增删无关组件后，已有位号不变。
  注意：`keep_net_names` 默认 None→随 `frozen`（`config.py:594,623-624`），即常态下 net 名
  每次构建重derive——这才是命名漂移的现存源头，关联 C2 与遗留问题 2，A2 不处理。

- [~] **A3. 验证项固化为 CI 测试**（测试已写好并本地验证；CI 接线未做）
  **已交付** `test/end_to_end/test_group_determinism.py`（4 个用例，全部经"破坏修复→
  测试变红"变异验证）：
  1. fresh 构建×2 字节一致（PYTHONHASHSEED=0/1 强制不同哈希序，直接命中 A1 成因）；
  2. 稳态×2 + 新增实例后×2 字节一致 + 位号保持（A2）；
  3. 注入手工线段/命名 group/placement rule area 后重建保留 + 稳态字节一致（A4）；
  4. `kicad-cli pcb upgrade` 往返 group membership 保留（**文本级比对**，原因见下方新约束）。
  **剩余（CI 接线）**：
  - CI 镜像安装 kicad-cli 10（官方 PPA `kicad/kicad-10.0-releases`，
    `--no-install-recommends` 跳过元件库）；
  - **EasyEDA API 依赖**：fresh 构建需选型，API 会限流 403（沙盒实测）。需预置
    `build/cache/parts/easyeda` fixture 或离线选型开关，否则 CI 必然间歇红。

- [x] **A4.（新发现并修复）手工命名 group 的内容被每次构建静默删除**
  **成因**：commit 48fe6e18 的 group 清理把"所有命名 group − atopile 组"当作已删除组，
  `_clean_group` 删除其全部非 footprint 成员（线段/过孔/zone）。调研时"非 atopile groups
  全部保留"的实测样本恰为悬空成员组,未覆盖"组内有真实元素"的路径（实测复现：组内线段
  被删，仅剩组壳 + 悬空引用）。
  **修复**：`_is_managed_group()`——atopile 自管组的 uuid 后缀 = 组名的 hex
  （`gen_uuid(mark)` 约定，对任意名字长度成立），仅对自管组做删除清理。
  **原意保留**：删除模块实例后其组内 pulled 几何仍被清理（实测 8→6 段）；
  空组壳留待 KiCad 重存回收（与修复前行为一致）。

## B. P0 — layout_ir.json 导出（第一阶段，本仓库）

- [ ] **B1. 新构建步骤 `layout-ir`**
  `@muster.register("layout-ir", dependencies=[update_pcb])`（`build_steps.py`）。
  遍历实例图 + footprint 映射，输出 `build/builds/<target>/layout_ir.json`：
  模块地址 → {type, group_uuid, components{地址 → 位号/footprint_uuid/pads{net, xy, layer}},
  nets}；net → {kicad_net, pads}。
  地址来源：`Node.get_full_name(include_uuid=False)`（`faebryk/core/node.py:1520-1556`，
  已确认稳定）。
  验收：PCB Layout SKILL 仅凭 layout_ir.json 即可解析位号/net/pad 坐标，无需读 .kicad_pcb。

- [ ] **B2. schema 固化与版本号**
  layout_ir.json 增加 `version` 字段 + JSON Schema 文件入库（诊断层与布线执行器都依赖它）。

## C. P1 — layout.yaml 加载 + KiCad 10 对象生成（第一阶段，本仓库）

- [ ] **C1. `BuildTargetConfig.layout_config: Path | None`**（`config.py:559-682`，Pydantic 字段）
  按构建目标挂 layout.yaml；缺省约定 `<layout>/<target>/layout.yaml`。

- [ ] **C2. layout.yaml 解析器 + room 解析**
  rooms：`module`（= atopile 地址前缀 = group 名）、origin/rotation/size、
  `source: group`（第一阶段固定）、layers、anchor（**用 ato 地址，不用位号**——位号可重排）。
  net 引用优先走"接口地址 → net"间接映射（layout_ir 提供），裸 net 名通配仅作降级
  （net 名含自动编号如 `unnamed[0]`，可能漂移，见遗留问题）。

- [ ] **C3. placement rule area 生成器**
  按 room 生成 ZONE：`(zone ... (keepout ...) (placement (enabled yes) (group "<模块地址>"))
  (polygon ...))`——该结构已实测被 KiCad 10 完整往返、逐字保留。
  实现分两步：
  - C3a（过渡）：对序列化输出做后处理注入（立即可用，调研即此路径）。
  - C3b（正式）：扩展 Zig sexp schema 的 zone `placement` 字段（仓库内 `sexp` 开发文档为指引），
    走类型化模型。注意 Zig 类型模型**会丢弃 schema 之外的字段**，schema 必须完整覆盖 zone 语法。
  约束：**禁止发明自定义 S-expression token**；引导/标记几何只放 User.x 图层
  （User.1=引导走廊、User.2=禁布区，与布线器约定一致）；
  **任何写入铜层的几何必须挂真实 net**（net-0 悬空铜会被 KiCad 重存清理）。

- [ ] **C4. `ato layout` CLI 子命令**
  `cli/cli.py` 注册：`ato layout resolve`（产出 layout_ir）/ `ato layout emit`（产出 rule areas）。
  验收（第一阶段整体）：`examples/layout_reuse`（3 个重复 Sub 模块）上
  `ato build` 两次字节级一致；`kicad-cli pcb upgrade` 往返保留；DRC JSON 正常运行。

- [ ] **C5.（后置）component class 支持**
  需同时写 `.kicad_pro`（类定义在工程级）。group 来源已够用，仅当需要基于 class 的
  DRC 规则时再做。

## D. P1 — 诊断闭环（第二阶段，本仓库侧）

- [ ] **D1. `ato route --plan layout.yaml --stage <name>`**
  调用 KiCadRoutingTools fork 的计划执行器（§E1），通过 Python API 进程内调用
  （`batch_route(pcb_data=..., return_results=True)`），不走子进程解析标准输出。

- [ ] **D2. `ato diagnose` → diagnostics.json**
  聚合三路输入并映射回文本源：
  1. 执行器的 route_report.json（逐 net 线段/过孔/迭代数/失败 + 阻塞分析）；
  2. `kicad-cli pcb drc --format json`（**注意：违例条目无结构化 net 字段，net 名嵌在
     描述文本中，必须用 items[].uuid 经 layout_ir 反查**）；
  3. rule-area 多边形命中测试 → 给违例标注所属 room。
  输出字段：stage/room/ato_path/constraint（指向 layout.yaml 条目的 JSON-pointer）/
  reason/blocking_nets/failed_endpoints/suggestions。
  风格基线：kicad-happy 项目的检查结果 schema（rule_id/severity/report_context/confidence）。

## E. KiCadRoutingTools fork（第二、三阶段，仓库：KiCadRoutingTools）

- [ ] **E1. `layout_plan_runner.py`（P1）**
  解析 layout.yaml 的 route_stages → 逐阶段构造 `GridRouteConfig`（数据类字段与 YAML
  一一对应，无需翻译层，`routing_config.py:31-124`）→ 多次调用 `batch_route`，
  阶段间 pcb_data 原地累积（前一阶段几何天然成为障碍）。net 排序（mps/inside_out/original）、
  层代价、禁布区均为现成参数，**无需改布线内核**。
  产出 `route_report.json`：results_data + `BlockingInfo`（数据类直接序列化）+ JSON_SUMMARY 聚合。

- [ ] **E2. 几何所有权 / 阶段锁定（P1）**
  `Segment`/`Via` 增加 `_metadata: dict`（内存态，不写入文件）；
  `rip_up_net`（`rip_up_reroute.py:60-72`，现状只按 net_id 删除）增加阶段守卫：
  删除非本阶段几何时报错。`lock_after: true` 的阶段在后续阶段中不可被撕线。

- [ ] **E3. 硬引导点 / 强制过孔（P2）**
  现状：引导走廊为软约束（引导点不可达时静默跳过，不加过孔、不报失败，
  `single_ended_routing.py:1011-1053`）。**不改 A\* 内核**，用分段方案实现：
  1. 按 room 局部坐标 → 板坐标变换（`board_xy = origin + rotate(local, rotation)`）
     预插 `Via` 进 `pcb_data.vias`（net_id = 目标 net）；
  2. 将该 net 分解为 pad→过孔@入层、过孔@出层→pad 两个子布线——任意网格点作为
     起点/终点已被支持（吸附 0.1mm 网格）；
  3. 子结果合并并打上所属阶段标记；
  4. `hard: false` 的引导点继续走现有引导走廊。
  验收：强制过孔落点误差 ≤ 1 个网格；任一段失败时报告 `blocked_before_forced_via`
  并附阻塞分析。

- [ ] **E4. room 布局复制（P2，替代 GUI 的 Repeat Layout）**
  源 room 几何（线段/过孔/敷铜）+ 锚点变换 + net 重映射。
  net 匹配按 atopile 地址前缀替换（比 ReplicateLayout 插件的 sheet 路径相似度匹配
  更简单可靠）；坐标变换移植 `ReplicateLayout/replicate_layout.py:47-66`（含翻面处理），
  **只借鉴算法不复用代码**（该插件依赖的 SWIG 绑定在 KiCad 10 已移除）。

- [ ] **E5. 独立回归测试（P1）**
  现状已满足基础条件：无需安装 KiCad（`build_router.py` 自动下载预编译 Rust 二进制），
  `kicad_files/` 内置 7 块测试板；依赖仅 numpy/scipy/shapely。
  补充：计划执行器的失败注入用例（故意用禁布区堵死一条 net，断言阻塞分析指向正确）
  + 报告 JSON schema 校验。

## F. 不做 / 暂缓

- ❌ fork KiCad 10（所需对象已实测可外部生成并完整往返）。
- ❌ 依赖旧版 SWIG Python 绑定（KiCad 10 已移除）。
- ❌ 生成 KiCad 原生 design block 库（atopile 的 `atopile_subaddresses` 机制已覆盖
  布局复用；group 的 `lib_id` 链接字段存在，留作第三阶段可选项）。
- ❌ 调用 GUI 的 Repeat Layout / Generate Placement Rule Areas（无法脱离界面运行）。
- ⏸ 向 KiCad 上游提 DRC JSON 增强补丁（结构化 net / rule-area 上下文字段）——
  外部映射可用；第二阶段跑通后评估。
- ⏸ 修改 `.ato` 语法——布局语义全部走 sidecar 文件，不动语言本体。

## 执行 P0 时新发现的约束（2026-06-12，影响后续各项）

1. **`kicad-cli pcb upgrade` 是单向门**：升级后的文件用 KiCad 10 当前格式（如嵌套
   `(tenting (front yes))`），atopile 的 Zig schema（钉在 20241229）**无法解析**——
   对 atopile 仍管理的板子绝不能跑 upgrade（KiCad 10 直接读 v9 格式无须升级）。
   调研只验证过"KiCad 读 atopile 输出"，反向是本次实测发现。
2. **`kicad.loads` 按 Path 无失效缓存**（`fileformats.py:117-122`，"object returned is
   shared"）：同进程内重读已重写的文件会拿到陈旧解析。对 B1/C/D 的进程内工具链
   （`ato layout` / `ato diagnose` 连续读写同一文件）是真实地雷；测试中用
   `loads(类型, path.read_text())` 绕开。
3. **C3b 工作量缩小**：Zig schema 已有 `ZonePlacement`（enabled/source_type/source/
   sheetname，`gen/sexp/pcb.pyi:937`），`(placement (enabled yes))` 已实测经构建保留；
   缺的只是 `source_type` 枚举的 `group` 值与 `(group "...")` 子句。
4. **`gen_uuid(mark)` 对 >16 字符的名字产出超长非法 uuid 字符串**（`UUID = str` 无校验，
   实测 20 字符名 → 40 hex"uuid"）。现状 group 名 = 顶层模块地址通常较短未触雷；
   生成长地址组前必须先修。

## 遗留问题

1. C3a（后处理注入）→ C3b（Zig schema 扩展）的切换时点（注意上方新约束 3：范围已缩小）。
2. net 名漂移程度（自动编号 `unnamed[N]` 在模块改动下的稳定性）——第一阶段实测后决定
   layout.yaml 是否完全禁止裸 net 名引用。
3. 是否向 KiCad 上游提 DRC JSON 增强补丁（见 §F）。
