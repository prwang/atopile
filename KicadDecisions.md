# KiCad 文本化布局工作流 — 可行性调研报告

## 0. 背景与目标

我们计划构建一条**确定性的、以文本为唯一事实来源（single source of truth）的 PCB 布局布线工作流**：

- `.ato`（atopile 语言）描述电路连接，作为电路的事实来源；
- `layout.yaml` 描述布局意图（room 划分、坐标、布线阶段、关键过孔等），作为布局的事实来源；
- KiCad 工程文件由以上两者生成，是派生产物；
- 布局/布线工具向上输出**机器可读的结构化诊断**（JSON），使自动化的 **PCB Layout SKILL**
  能够以"修改文本 → 重新生成 → 读取诊断 → 再修改"的方式快速迭代，而不必依赖人工看图。

该方案以三个开源项目为基础：**atopile**（电路 DSL 与编译器）、**KiCad 10**（布局/DRC/制造输出）、
**KiCadRoutingTools**（批处理自动布线）。在前期方案评估中，以下关键问题尚未确认、需要进入代码验证：

1. atopile 的模块层次能否稳定映射到 KiCad 10 的 groups / placement rule areas（即 KiCad 10 第一方的
   "room"与多通道布局机制）？
2. 能否**不修改 KiCad 本体**，由外部工具直接生成这些对象，并保证 KiCad 读写后不丢失（round-trip）？
3. KiCadRoutingTools 能否以最小改动支持分阶段布线（staged routing）与强制过孔（forced via）？
4. 布线/DRC 结果能否映射回 `layout.yaml` 中的具体约束条目，形成可自动化的反馈闭环？
5. 如果必须修改 KiCad，缺口具体是哪个 API / 序列化字段 / CLI 命令？

本报告通过**静态代码调研**（atopile、KiCadRoutingTools、KiCad 10 源码及 4 个参考项目）与
**运行时验证**（真实执行 `ato build`、`kicad-cli`、自动布线器）回答上述问题。

**调研环境**：Ubuntu 24.04；KiCad 10.0.3（官方 PPA）；atopile 0.15.7（PyPI，Python 3.14）；
KiCadRoutingTools HEAD + rust router v0.15.x 预编译二进制。
所有运行时验证步骤见附录，均可复现。

---

## 1. 结论

### 1.1 五个关键问题的答案

| # | 问题 | 答案 | 关键证据 |
|---|------|------|----------|
| 1 | ato 模块路径能否稳定映射到 KiCad footprints/nets/pads？ | **能** | 每个 footprint 带 `atopile_address` 属性（如 `sub_chains[0].r_chain[0]`），由 `Node.get_full_name(include_uuid=False)` 生成（`src/faebryk/core/node.py:1520-1556`）；实测该属性经 KiCad 10 重新保存后完整保留 |
| 2 | 能否不改 KiCad，直接生成 KiCad 10 groups + placement rule areas？ | **能** | 实测：手写 `(zone ... (placement (enabled yes) (group "sub_chains[0]")))` 注入 .kicad_pcb 后，`kicad-cli pcb upgrade`（无界面加载+重存）逐字保留；groups 同样完整往返 |
| 3 | KiCadRoutingTools 能否最小修改支持分阶段布线 + 强制过孔？ | **分阶段布线几乎零修改；强制过孔需要在 fork 中实现** | `batch_route(pcb_data=..., return_results=True)` 已存在（`route.py:171, 212-213`）；多次调用 + net 过滤即为分阶段布线。强制过孔无一等支持，需按 §3.3 的分段布线方案实现 |
| 4 | 布线/DRC 结果能否映射回 layout.yaml 的具体约束？ | **能，需一个薄聚合层** | 布线器已输出结构化 `JSON_SUMMARY`（逐 pad 失败坐标+位号）、`BlockingInfo` 数据类、`return_results` 字典；DRC JSON 含 uuid+坐标；缺的只是 uuid/net → ato 地址 → 约束条目的映射层（纯外部代码，无需改上游） |
| 5 | 如果必须改 KiCad，缺的具体是什么？ | **第一阶段不缺。** 仅有两个边界：(a) Repeat Layout（多通道布局复制）只有 GUI 入口（`pcbnew/tools/multichannel_tool.cpp`），无 CLI/IPC——复制逻辑自行实现（参考 ReplicateLayout 插件的坐标变换算法）；(b) DRC JSON 报告无结构化 net 字段（net 名只出现在描述文本中）——由外部诊断层用 uuid 反查解决 |

### 1.2 总体决策

```
不 fork KiCad 10。                       ← 所需全部对象可由外部生成且完整往返
Fork atopile（增加 layout sidecar 层）。  ← 已有约 80% 基础设施：地址属性、groups、确定性 UUID
Fork KiCadRoutingTools（增加计划执行器）。← 已有约 70% 基础设施：进程内 API、结构化结果、JSON 输出
```

前期方案评估中两个"待验证假设"现已确认：

- KiCadRoutingTools 的 `return_results=True` **确实存在**（`route.py:212-213`，返回
  `results_data` 字典，构造见 `route.py:779-787`）；
- atopile 模块层次**不会**映射为 KiCad hierarchical sheets——atopile **不生成 .kicad_sch
  原理图文件**，只生成 .kicad_pcb。因此 KiCad 10 多通道布局的通道来源**只能走 named groups /
  component classes 路线**，hierarchical-sheet 路线在 atopile 工作流中不存在。

### 1.3 对前期方案预估的重要修正

1. **删除"ato module → KiCad hierarchical sheet"映射方案**（原预估的首选路径）。
   没有原理图文件，sheet path 无从谈起。named groups 是唯一现实入口，且 atopile 已经在生成它们。
2. **atopile 已写 groups，且 group UUID 从模块地址确定性派生**。
   实测观察：group `sub_chains[0]` 的 UUID `b0790473-7562-5f63-6861-696e735b305d` 中
   `7375625f636861696e735b305d` 正是字符串 "sub_chains[0]" 的 ASCII 十六进制
   （实现为 `kicad.gen_uuid(group_name)`，`layout_sync.py:126-128`）。
   原预估假设需要从零实现 group 生成器，实际是增强现有机制。
3. **KiCad 的文件解析器是严格的**：遇到未知 token 直接报解析错误
   （`pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:1388`）。
   因此**不能在 .kicad_pcb 中发明自定义字段**存放我们的元数据；元数据只能放进 footprint 的
   `(property ...)`（已实测可完整往返）或独立的 sidecar 文件。
4. **KiCad 重新保存时会清理无网络的悬空铜**：实测挂在 net 0 上的注入线段（及只包含它的 group）
   在 `kicad-cli pcb upgrade` 后被删除；同一线段挂真实 net 则完整保留。
   因此生成器写入的任何引导/标记几何不能放在 net 0 的铜层上——应使用 User.x 图层
   （布线器的引导走廊约定正是 User.1，禁布区是 User.2）。

---

## 2. atopile 调研结果（fork 对象之一）

版本：仓库 HEAD（最近一次提交为 2026 年 3 月的 bugfix，状态稳定；要求 Python 3.14，
开发环境用 miniconda 安装即可）。运行时验证使用 PyPI 0.15.7，关键行为与 HEAD 代码一致。

### 2.1 构建流水线（实测 + 代码）

```
ato build
  → 配置加载 (ato.yaml, src/atopile/config.py:559-682)
  → 构建步骤 DAG (src/atopile/build_steps.py:299-380)：
     实例化 app（ANTLR 解析 → Zig TypeGraph → 实例图）
     → load_pcb（读取已有 .kicad_pcb）
     → pick_parts（元件选型）→ prepare_nets（位号、net 命名）
     → update_pcb（PCB 变换器 + group 同步）→ 序列化 → .kicad_pcb
     → BOM / manifest / 制造数据
```

### 2.2 已确认事实

| 调研问题 | 结论 | 证据 |
|---|---|---|
| 模块实例路径是否稳定？ | **是**。`get_full_name(include_uuid=False)`，由实例层次确定性派生 | `faebryk/core/node.py:1520-1556`；位号分配前按它排序（`faebryk/libs/app/designators.py:119`） |
| 位号 / net 名的生成与持久化 | 位号默认每次构建重排；`keep_designators` / `keep_net_names` 配置项开启后，从已有 .kicad_pcb 按 `atopile_address` 回读复用 | `designators.py:24-93`，`build_steps.py:680-685` |
| 原理图输出是层次化还是扁平？ | **不生成 .kicad_sch**。仅输出 .kicad_pcb + BOM + 制造数据 | `faebryk/exporters/` 下无原理图写出器；实测构建产物确认 |
| 现有 layout 同步 group 如何存储 | KiCad group，名称 = 模块地址，UUID = `gen_uuid(地址)`（确定性）；成员 = `atopile_address` 前缀匹配的 footprint UUID | `faebryk/exporters/pcb/layout/layout_sync.py:101-155`；实测验证 |
| 重新构建是否保留手工编辑 | **修正（2026-06-12）**：散置的 tracks/vias/zones/rule areas 保留，但**手工命名 group 的成员内容会被每次构建删除**（commit 48fe6e18 的清理把所有非 atopile 命名组当作已删除组）。原实测样本是悬空成员组，未覆盖该路径。已在 fork 修复（`_is_managed_group`，BACKLOG A4）+ 回归测试 | 实测复现：组内注入线段被删仅剩组壳；修复后保留 |
| 是否有编译器中间表示导出 | **无**。只有 BOM JSON / 参数 JSON / PCB 摘要。`layout_ir.json` 需新增 | `faebryk/exporters/bom/`、`parameters_to_file.py` |
| ato.yaml 构建目标是否可扩展 | 是。`BuildTargetConfig`（Pydantic）增加 `layout_config: Path` 字段即可按构建目标挂 layout.yaml | `config.py:559-682` |
| KiCad 插件是否可复用 | 是。`kicad-ipc layout-sync` 动作（`src/atopile/kicad_plugin/lib.py:50-173`）；子布局复用走 `atopile_subaddresses` 属性 + `has_subpcb` 特征（`src/atopile/layout.py:54-162`） | 实测属性值 `[layout/sub/sub.kicad_pcb:r_chain[0]]` |
| S-expression 引擎 | Zig 类型化模型（`faebryk/libs/kicad/fileformats.py`）。**类型模型之外的字段在重写时丢失**——为 .kicad_pcb 增加 rule area placement 字段需扩展 Zig schema（仓库内有 `sexp` 开发文档）或对输出文件做后处理注入 | `fileformats.py:37-150` |
| 输出文件格式版本 | `(version 20241229)`（KiCad 9 格式）。KiCad 10 正常读取并可升级 | 实测 |

### 2.3 发现的缺陷（fork 时顺带修复）

- **group 成员列表顺序不确定**：连续两次构建的 .kicad_pcb 仅成员列表顺序不同（其余 100% 一致）。
  序列化前排序即可修复；不修则破坏文本 diff 的确定性。

### 2.4 改动插入点

```
新构建步骤：@muster.register("layout-ir", dependencies=[update_pcb])
  → 遍历实例图 + footprint 映射 → build/builds/<t>/layout_ir.json
新构建步骤：layout-emit
  → 读 layout.yaml → 写 placement rule areas / 补充 groups / User 层引导几何
新 CLI：src/atopile/cli/cli.py 注册 `ato layout` / `ato route` / `ato diagnose` 子命令
配置：BuildTargetConfig.layout_config: Path | None
确定性修复：layout_sync.py 排序 group 成员
```

---

## 3. KiCadRoutingTools 调研结果（fork 对象之二）

### 3.1 已确认事实

| 调研问题 | 结论 | 证据 |
|---|---|---|
| CLI 与 KiCad 插件是否共用引擎？ | 是。共用 `GridRouteConfig` 配置类 + 障碍图构建 + Rust A* 内核（`grid_router`）；插件用 `build_pcb_data_from_board` 免文件读写 | `route.py:95-214`、`kicad_routing_plugin/action_plugin.py:65-150`、`routing_config.py:31-124` |
| 是否有结构化结果 API？ | **有**。`batch_route(..., return_results=True)` 返回 `results_data`（逐 net 的线段/过孔/路径/迭代数；失败时含阻塞单元等）；另在标准输出打印 `JSON_SUMMARY:` 行（实测：含逐 pad 失败的位号/pad 号/坐标、撕线重布对、层交换数、迭代数） | `route.py:212-213, 779-787`；`single_ended_routing.py:886-893, 998-1004`；实测运行 |
| 解析/写出是否保留未知字段？ | **是**。采用"外科手术式"文本编辑：读入原文，定位修改线段/过孔，新内容追加在文件尾部右括号前，不做全量重写 | `output_writer.py:67-114` |
| 是否解析 groups / rule areas / 属性？ | 禁布区 rule areas 解析（`kicad_parser.py:1330-1384`）；**placement rule areas 与 groups 不解析，但写出时不破坏** | 同上 |
| 引导走廊是软约束还是硬约束？ | **软约束（尽力而为）**。User.1 折线 → 路径引导；某个引导点不可达时布线照样成功，不加过孔、不报失败 | `single_ended_routing.py:1011-1053` |
| 过孔在哪里产生 | 路径化简时在层切换处生成 `Via` 数据类（x,y,size,drill,layers,net_id,uuid） | `kicad_parser.py:57-67`、`single_ended_routing.py:950-965` |
| pad→强制过孔→pad 分段是否可行？ | **可行，无需重写布线内核**：①预先把 Via 插入 `pcb_data.vias`；②任意坐标点可作为网格目标（吸附到 0.1mm 网格）；③两段布线后合并结果；④已有 `rip_up_reroute.py` 证明增量修改 pcb_data 是支持的模式 | `single_ended_loop.py`、`rip_up_reroute.py:24-130` |
| 是否有所有权 / 锁定机制？ | **无**。线段/过孔无元数据字段；`rip_up_net` 只按 net_id 删除，不区分阶段；`(locked yes)` 属性不解析 | `rip_up_reroute.py:60-72` |
| 阻塞分析的粒度 | `BlockingInfo` 数据类：net 名、阻塞计数、线/孔单元数、独占单元数、近源/近目标单元数——结构化，可直接序列化为 JSON | `blocking_analysis.py:24, 63-74` |
| routing_diagnostics.py 是什么 | 纯文本的参数调整建议（提高撕线上限、降低线宽等），非结构化 | `routing_diagnostics.py:19-107` |
| 能否无 KiCad 独立运行？ | **能**。`build_router.py` 自动下载预编译 Rust 二进制；`kicad_files/` 内置 7 块测试板 | 实测：interf_u_unrouted.kicad_pcb 布线成功 |
| KiCad 9/10 兼容 | 透明支持。版本自动检测（`kicad_parser.py:23-31`）；KiCad 10 的 `(net "name")` 格式与过孔 tenting 字段均已处理 | `output_writer.py:84`、`kicad_writer.py:125-128` |

### 3.2 运行时验证实录

```bash
python3 build_router.py        # 自动下载预编译 grid_router.so，无需 Rust 工具链
python3 route.py kicad_files/interf_u_unrouted.kicad_pcb out.kicad_pcb 'GND' --stats
# → 标准输出末尾打印 JSON_SUMMARY: {"routed_single": [...], "failed_multipoint":
#    [{"net_name":"GND","failed_pads":[{"component_ref":"C4","pad_number":"2",
#    "x":155.688,"y":52.197}, ...]}], "total_iterations": 227, ...}
```

依赖仅 python3-numpy/scipy/shapely（apt 可装）。

### 3.3 改动插入点

```
layout_plan_runner.py（新模块）：
  解析 layout.yaml 的 route_stages → 逐阶段调用 batch_route(pcb_data=..., return_results=True)
  阶段之间 pcb_data 原地累积（前一阶段几何自动成为障碍——这是现状行为）
  GridRouteConfig 即 YAML 阶段参数的目标数据类（字段一一对应，无需翻译层）
强制过孔 / 硬引导点（最小改动方案）：
  1. 按坐标+层预插 Via 进 pcb_data.vias（net_id 取目标 net）
  2. 把单条 net 分解为 pad→过孔@入层、过孔@出层→pad 两个子布线（网格目标点已支持）
  3. 子结果合并、打上所属阶段标记
所有权：线段/过孔增加 _metadata 字典字段（内存态，不写入文件）；
  rip_up_net 增加阶段守卫：删除非本阶段几何时报错
诊断：routing_report.py 聚合 return_results + BlockingInfo + JSON_SUMMARY
  + kicad-cli DRC JSON → 按 layout_ir.json 反查 ato 地址 → diagnostics.json
```

---

## 4. KiCad 10 调研结果（不 fork，仅对接）

### 4.1 序列化格式（源码 + 实测双重验证）

**Placement rule area** = ZONE 对象（`m_isRuleArea=true`），写出代码
`pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:3104-3135`，
解析代码同目录 `..._parser.cpp:8659-8712`：

```lisp
(zone (net 0) (layers "F.Cu" "B.Cu") (uuid ...) (name "room_codec0")
  (hatch edge 0.5)
  (keepout (tracks allowed) (vias allowed) (pads allowed)
           (copperpour not_allowed) (footprints allowed))
  (placement (enabled yes)
    (group "sub_chains[0]"))        ; 或 (sheetname "...") / (component_class "...")
  (fill ...)
  (polygon (pts (xy 90 90) (xy 130 90) (xy 130 120) (xy 90 120))))
```

✅ **实测**：上述结构由外部脚本写入 atopile 生成的板文件后，`kicad-cli pcb drc` 正常解析，
`kicad-cli pcb upgrade` 重新保存后 `(placement (enabled yes) (group "sub_chains[0]"))` 逐字保留。

**Group**（写出 `pcb_io_kicad_sexpr.cpp:2640-2659`，解析 `:7371-7455`）：

```lisp
(group "name" (uuid ...) (locked no)
  (lib_id "Lib/DesignBlockName")    ; 可选，v20250513+，design block 链接
  (members "uuid1" "uuid2" ...))
```

✅ 实测完整往返（注意 §1.3 第 4 条的 net-0 清理行为）。

**Component class**：类定义存于工程文件（project 级，由 `COMPONENT_CLASS_MANAGER` 管理，
不在 .kicad_pcb）；板文件内只有按 footprint 的赋值
`(component_classes (class "X"))`（`pcb_io_kicad_sexpr.cpp:1350-1357`）。
→ 若用 component class 作为 room 来源，必须同时写 `.kicad_pro`。**首选 group 来源，绕开此问题。**

**Design block**：以库目录形式存在（.kicad_sch + .kicad_pcb 成对），板内通过 group 的
`lib_id` 字段链接。可外部生成，但第一阶段不需要（atopile 的子布局机制已覆盖布局复用）。

### 4.2 能力边界

| 能力 | 状态 |
|---|---|
| 外部生成 groups / placement rule areas / component class 赋值并完整往返 | ✅ 已实测 |
| `kicad-cli pcb drc --format json` | ✅ 字段：type、severity、description、items[]{uuid, pos, description}；**无结构化 net 字段**（net 名嵌在描述文本中，如 `Pad 1 [sub_chains[2]-unnamed[0]-1] of R9`）→ 诊断层用 uuid 映射解决 |
| `kicad-cli pcb upgrade` | ✅ 等价于无界面的"加载+重新保存"，是往返测试的标准手段 |
| Repeat Layout（多通道布局复制）无界面调用 | ❌ 仅 GUI 工具动作（`pcbnew/tools/multichannel_tool.cpp`，需锚点 footprint 与交互上下文）。**决策：不依赖；复制逻辑自行实现**（ReplicateLayout 插件的锚点变换算法约 100 行可移植） |
| IPC API（protobuf） | 可创建/修改 Group、Zone（含 `RuleAreaSettings.placement_source_type/placement_source`，见 `api/proto/board/board_types.proto`）。适合实时编辑场景；持久化仍以文件生成为主 |
| 旧版 SWIG Python 绑定（pcbnew 模块） | KiCad 10 已移除板级操纵 API。**禁用此路线**（ReplicateLayout 插件依赖它，只借鉴算法不复用代码） |
| 解析器容错性 | **严格**：未知 token 抛解析错误（`..._parser.cpp:1388`）。自有元数据只能走 `(property ...)` 或 sidecar 文件 |
| 当前板文件格式版本 | `SEXPR_BOARD_FILE_VERSION = 20260603`；groups 自 20200811、component classes 自 20240928、placement rule areas 自 20241009、group lib_id 自 20250513 |

---

## 5. 数据格式草案

### 5.1 `layout.yaml`（布局事实来源，挂在 ato.yaml 构建目标上）

```yaml
coord:
  unit: mm
  origin: board

rooms:
  codec0:
    module: "sub_chains[0]"          # atopile 地址前缀（= group 名）
    origin: [20, 30]
    rotation: 0
    size: [28, 18]
    source: group                    # 第一阶段固定 group（无 sheet；class 需写 .kicad_pro）
    layers: [F.Cu, In1.Cu, In2.Cu, B.Cu]
    anchor: "sub_chains[0].r_chain[0]"   # 用 ato 地址，不用位号（位号可能重排）

route_stages:
  - name: critical
    rooms: [codec0]
    nets: ["sub_chains[0]-*"]        # net 名通配（布线器已支持）
    order: explicit                  # explicit | mps | inside_out | original
    layers: [F.Cu, In1.Cu]
    track_width: 0.10
    clearance: 0.10
    via: { size: 0.30, drill: 0.15 }
    lock_after: true
    constraints:
      - net: "MCLK"
        waypoints:
          - at: [8.0, 2.0]           # room 局部坐标；执行器换算为板坐标
            coord: room
            via_to: In1.Cu
            hard: true               # hard → 分段布线；false → 引导走廊
  - name: slow
    nets: ["I2C_*", "RESET*"]
    order: mps
    layers: [In2.Cu, B.Cu]
    obstacles: [critical]            # 前一阶段几何锁定

ownership:
  human_locked: [mechanical_outline, connector_placement]
```

### 5.2 `layout_ir.json`（`ato build` 新产物；PCB Layout SKILL 可凭它直接定位对象，无需解析 KiCad 文件）

```json
{
  "version": 1,
  "build": "top",
  "modules": {
    "sub_chains[0]": {
      "type": "layout_reuse.ato:Sub",
      "group_uuid": "b0790473-7562-...",
      "components": {
        "sub_chains[0].r_chain[0]": {
          "refdes": "R1", "footprint_uuid": "...", "fp": "R0402",
          "pads": {"1": {"net": "unnamed[0]", "xy": [20.1, 30.2], "layer": "F.Cu"}}
        }
      },
      "nets": ["sub_chains[0]-unnamed[0]", "..."]
    }
  },
  "nets": {"sub_chains[0]-unnamed[0]": {"kicad_net": 2, "pads": ["R1.2", "R2.1"]}}
}
```

### 5.3 `route_report.json` / `diagnostics.json`

route_report = 布线器 `results_data` + `JSON_SUMMARY` 的逐阶段聚合（布线器侧产出）。
diagnostics 在其上做地址映射（atopile 侧产出）：

```json
{
  "stage": "critical", "room": "codec0", "status": "failed",
  "summary": {"routed": 7, "failed": 1, "vias": 11, "iterations": 88123},
  "failures": [{
    "net": "MCLK", "ato_path": "sub_chains[0].codec.mclk",
    "constraint": "route_stages[0].constraints[0].waypoints[0]",
    "reason": "blocked_before_forced_via",
    "blocking_nets": [{"net": "I2S_BCLK", "track_cells": 41, "via_cells": 2,
                       "unique_cells": 38, "near_target_cells": 12}],
    "failed_endpoints": [{"refdes": "U1", "pad": "12", "xy": [24.1, 32.7]}],
    "suggestions": [{"file": "layout.yaml",
      "path": "/route_stages/0/constraints/0/waypoints/0/at", "op": "nudge", "delta": [0.5, -0.3]}]
  }],
  "drc": {"source": "kicad-cli", "violations": [{"type": "clearance", "severity": "error",
           "uuid": "...", "xy": [28.2, 32.1], "net": "MCLK", "room": "codec0"}]}
}
```

（`blocking_nets` 字段 = `BlockingInfo` 数据类直接序列化；`failed_endpoints` =
`JSON_SUMMARY.failed_pads` 原样；DRC 的 `net`/`room` 由映射层根据 uuid 与
rule-area 多边形反算补充。）

---

## 6. 实施计划

### 第一阶段 — 最小垂直切片（仅 atopile fork + 对接代码，不动布线器）

1. atopile fork：
   - `layout_sync.py` 排序 group 成员（确定性修复，1 行）。
   - 新构建步骤 `layout-ir` → 输出 §5.2 的 `layout_ir.json`。
   - 新构建步骤 `layout-emit`：读 `layout.yaml`，生成 placement rule areas
     （Zig sexp schema 扩展 `placement` 字段；过渡期可对输出文件后处理注入，已实测 KiCad 接受）。
   - `BuildTargetConfig.layout_config` 字段 + `ato layout` CLI 子命令。
2. 测试板：直接用 `examples/layout_reuse`（3 个重复 Sub 模块，天然的多通道用例）。
3. 验收：`ato build` 两次 → .kicad_pcb 字节级一致；`kicad-cli pcb upgrade` 往返后
   rule areas/groups/属性完整保留；`kicad-cli pcb drc --format json` 正常运行。
   （以上路径全部已在本调研中手工验证，第一阶段只是产品化。）

### 第二阶段 — 分阶段布线 + 诊断闭环（KiCadRoutingTools fork）

1. `layout_plan_runner.py`：YAML 阶段 → 多次 `batch_route(pcb_data, return_results=True)`，
   阶段序列内 pcb_data 累积；`GridRouteConfig` 直接由阶段字典构造。
2. `routing_report.py`：聚合 results_data + BlockingInfo + DRC JSON → `route_report.json`。
3. atopile 侧 `ato route --stage X` / `ato diagnose`：调用执行器，按 layout_ir 映射出
   `diagnostics.json`。
4. 测试：无 KiCad 依赖，用 `kicad_files/` 样板 + layout_reuse 板回归；
   断言报告 schema + 失败注入用例（故意用禁布区堵死一条 net，检查 blocking_nets 指向正确）。

### 第三阶段 — 硬引导点 / 强制过孔 / 几何所有权

1. 线段/过孔增加 `_metadata`；`rip_up_net` 增加阶段守卫。
2. 强制过孔 = 预插 Via + pad→过孔→pad 分段布线（§3.3）；引导点坐标 room 局部 → 板坐标变换
   （`board_xy = origin + rotate(local, rotation)`，可直接移植 ReplicateLayout 的
   `rotate_around_point` 算法，`replicate_layout.py:47-66`）。
3. 多 room 布局复制（替代 GUI Repeat Layout）：源 room 几何 + 锚点变换 + net 重映射
   （net 名按 atopile 地址前缀替换，比 ReplicateLayout 的 sheet 路径相似度匹配更简单可靠）。
4. 测试：强制过孔落点断言；跨阶段撕线被拒断言；room 复制后 DRC 无违例。

---

## 7. 已定决策与遗留问题

**已定决策**

- **fork 基线 = atopile 仓库 HEAD**（最近一次提交为 2026 年 3 月的 bugfix，稳定）。
  Python 3.14 开发环境用 miniconda 安装解决。
- 不 fork KiCad 10；room 来源固定用 named group。

**遗留问题（需后续决策或上游沟通）**

1. **Zig schema 扩展 vs 后处理注入的切换时点**：后者立刻可用（本调研验证路径），
   前者更干净但要动 Zig 绑定层。建议第一阶段后处理、第二阶段进 schema。
2. **net 命名稳定性**：当前 net 名如 `sub_chains[0]-unnamed[0]` 含自动编号，模块改动可能漂移。
   layout.yaml 引用 net 建议优先走"ato 接口地址 → net"间接引用（layout_ir 提供映射）；
   待第一阶段实测漂移程度后决定是否完全禁止裸 net 名。
3. **是否给 KiCad 上游提 DRC JSON 增强补丁**（结构化 net / rule-area 上下文字段）：
   外部映射可用但多一步；若长期维护，提 PR 是低成本高回报项。
4. **参考项目的可借鉴点**：kicad-happy 的检查结果 schema（rule_id/severity/report_context/
   confidence）可作为 diagnostics.json 的风格基线；Ki-Stack 的"改动后渲染+DRC 验证"流程
   值得纳入 PCB Layout SKILL 的操作规范。

---

## 附录：运行时验证清单（全部可复现）

| 验证项 | 命令 | 结果 |
|---|---|---|
| ato 构建 | `ato build`（examples/layout_reuse 副本） | 成功；产出 groups + atopile_address/atopile_subaddresses 属性 |
| 重复构建确定性 | 构建两次后 diff | 仅 group 成员顺序漂移，其余字节级一致 |
| 手工编辑保留 | 注入线段+group+rule area → `ato build` | 全部保留 |
| rule area 往返 | 注入 placement zone → `kicad-cli pcb upgrade --force` | `(placement (enabled yes) (group ...))` 逐字保留 |
| net-0 清理行为 | net 0 线段 → upgrade | 被 KiCad 删除（连带空 group）；挂真实 net 则保留 |
| DRC JSON | `kicad-cli pcb drc --format json` | type/severity/items{uuid,pos}；net 名仅在描述文本中 |
| 布线器独立运行 | `build_router.py` + `route.py <板> out.kicad_pcb 'GND' --stats` | 预编译二进制自动下载；标准输出打印结构化 JSON_SUMMARY |
