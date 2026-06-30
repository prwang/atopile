# BACKLOG — 文本化布局工作流（deterministic text-first layout layer）

## 背景

在 atopile + KiCad 10 + KiCadRoutingTools 之上构建确定性、以文本为唯一事实来源的
PCB 布局布线工作流：`.ato` = 电路事实源，`layout.yaml` = 布局意图事实源，KiCad 工程
为派生产物；工具输出结构化 JSON 诊断，供 **PCB Layout SKILL** 迭代。架构总览见
`CLAUDE.md`「架构总览」；关键事实均自包含于下文，证据 = 各条就地标注的 KiCad 源 / 代码
文件:行号（原可行性调研报告 KicadDecisions.md 已于 2026-06-19 退役，活性内容已折叠入此两处）。

**本文档纪律（不允许熵增）**：只保留 ① 已定决策 ② 改代码前必读的当前有效事实 ③ 未完成
任务。**已完成功能按 atopile 约定文档写在代码里**（模块 docstring / 同名测试 / schema =
SSOT），BACKLOG 只留"结论 + 代码指针"，绝不复述实现细节或调试 narrative。

## 已定决策

- 不 fork KiCad 10（所需对象已实测可外部生成并完整往返）。
- fork 基线 = 本仓库 HEAD；同时 fork KiCadRoutingTools（§E）。
- Python 3.14 开发环境 = **`uv sync`**（`ziglang==0.15.1` 由 pip 构建依赖提供）。克隆须
  `git fetch --tags` 否则 setuptools-scm 产非 SemVer 版本号、`ato` CLI 启动即崩。
- **room 来源 = footprint `sheetname`【C3 改定，旧"= KiCad named group"作废】**：group 有不可修补的
  所有权缺陷（详见 §C3）；atopile 建零个 group。component class 源后置（D5，须先修 fileformats schema）。
- **v10-only 路线（2026-06-13 用户拍板 Option B，upgrade-on-write）**：本分支目标方言 = v10；
  **前向兼容 v9 写出不做**（v9 仅只读，读入即在写出时升级为 v10）。推论：(i) 无"写 v9"代码
  路径；(ii) net 名是文件内唯一键，名漂移 = 几何归属漂移；(iii) S7 后"单向门/只读不存"纪律
  **已解除**——KiCad 10 GUI 保存不再损坏受管板子。

## 优先级总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 确定性与基础修复（§A） | ✅ |
| P0.1 | v10 迁移测试底座（T0–T9） | ✅ |
| P0.2 | v10 方言迁移本体（S0–S6 + D1 + BUG-1/2） | 🔶 仅剩 S7 flag-day 验收 |
| B | layout_ir（文本↔几何唯一接口） | ✅ |
| C | room 几何（forced via / room 复制） | ✅ |
| **C3** | **group→sheetname 迁移（room 去 group 化）** | ✅ 2026-06-15（见 §C3 代码指针） |
| **D** | **layout.yaml 加载 + placement rule area（D1–D4）** | ✅ 2026-06-16（见 §D 代码指针）；D5 后置 |
| **D-Tier2** | **bundle 总线传输 schema + geometry（桶①）** | ✅ 桶① 2026-06-20（model+geometry）+ D 侧 build 集成 2026-06-25（`generate_layout_plan` 调 `bundle_artifact`，e2e `examples/sata_bundle`）；桶② `batch_route_bundle` 契约 strict-xfail，实现移 §E E-Tier2（用户拍板） |
| **D-Tier3** | **自包含：摆放（room 相对坐标）+ 板框 outline + 完整叠层** | 🟢 桶① + 桶② TS-AUTH-A + placements→transformer 已落地（2026-06-26 / 2026-06-28）：摆放 schema+`apply_placements` build 消费 / `Room.polygon`+rotation+layers / `board.outline`+完整 `stackup` / impedance→stackup 硬依赖 / **单一层数权威板侧** `config` 派生自 `stackup_layers`（杀 2 层硬编码）；契约 `test_placement_contract.py`+`test_placement_apply_contract.py`+`test_board_section_contract.py`。**TS-AUTH-B（router 层表）✅ 2026-06-28（E1 由 `stackup_layers` 传 `layers`）**；剩 **桶③=§E DoD**（纯文本 e2e）；net-class/pour/keepout/silk 进 §F |
| **增量执行** | **route_stages `--up-to` 断点 + stage name 唯一** | ✅ 2026-06-28：stage name 唯一 + `--up-to`（name/1-based index，越界 loud，写部分板+`route_report.json`）落在 E1 runner |
| **Tier0** | **corridor-as-data（纯 schema 旁支，零 router 改动）** | ✅ 2026-06-28（`corridor.py` / `RouteStage.corridor`） |
| E–F | 路由 fork + 诊断闭环——**章节字母 = 执行序** | 🔶 **E1 ✅ 2026-06-28**（runner+`route_report.json`+`--up-to`+TS-AUTH-B）+ **E-Tier2 bundle ✅ 2026-06-29**（`batch_route_bundle` 平行总线+直连扇出+e2e；E2 rip-up 旋钮按用户拍板取消）；剩 E3 回归台 / §E DoD 桶③ 自包含建板 / §F |
| G | 不做 / 暂缓 | — |

### 依赖与关键路径（v10 数据模型推导；**章节字母 = 执行序**）

v10 三条硬约束逼出排序：① 无顶层 net 表 + 随机 FBRK uuid → **唯一稳定键 = ato 地址**；
② **net 名 = net 唯一键**，名漂移 = 几何归属漂移；③ 引导/铜层几何分层挂 net（事实 9）。

```
A 确定性基础（✅）
B layout_ir（✅ 基石）── addr↔uuid↔net名 桥表，下游全依赖
├─► C room 几何（✅ forced via / room 复制）
└─► C3 group→sheetname 迁移（✅ 2026-06-15）room = footprint sheetname，atopile 不建/不删 group
                       │
   C3 ─► D layout.yaml + rule area（✅ 2026-06-16；room via (placement (sheetname)))
                       │
   C1 + D(plan+rule area) ─► E 路由 fork（E1 plan_runner→E2 锁定）─► F 诊断闭环
E3 回归台（全程并行先搭 = §E 的 S0 底座；最小切片【✅ 已提前到 §C】当 router-oracle）

   D ─► D-Tier2 契约冻结（bundle schema + batch_route_bundle oracle，先 xfail）
            │  （consumer-oracle：D 的 bundle schema 钉在尚不存在的 router entry 上，
            │    故 D 与 E 必须共冻同一契约——不能先做 E 再倒逼 schema 重构）
            └─► E-Tier2 实现该契约（batch_route_bundle + breakout 扇出；跨 stage 锁是 router 白送的硬障碍，
                 见「关键事实」16，E2 只暴露 stage 内 rip-up 旋钮、不新建锁）
   D ─► D-Tier3 桶① + TS-AUTH-A（schema + 纯函数 + rule_area 几何 + config 板表←stackup，✅）
            └─► 剩余下游（棘轮留 D，contract-first）：TS-AUTH-B → §E1（router layers←stackup）；桶③ e2e → §E DoD
   E1 dispatch 从一开始就按「single / diff / bundle」三类 stage 设计（勿事后改）
```

**C3 为何在 D 前（已完成的背景）**：room=KiCad group 有不可修补的所有权缺陷——group 是通用选择原语
（`pcb_group.h:44`），名不唯一、复制即碰撞、无 provenance 槽（逼出 uuid 塞名 hack）。KiCad 多通道事实源
是每 footprint 的 sheet 标识，group 只是派生投影。C3 改回本意：room = footprint `sheetname`。**详见 §C3。**

**双向钉死纪律**（写消费者前先用消费者的真接口把上游验收钉死）：B→C 用
`_generate_net_map` consumer-oracle（已钉）；C→E 用真 router consumer-oracle（E3 薄片已提前）。
**D-Tier2→E-Tier2 方向反转（契约先行 co-design）**：`batch_route_bundle` 今天不存在，无现成接口可钉，故由
D 先定义并 strict-xfail 钉死契约、E 照此实现（不能先做 E 再倒逼 schema 重构，见 §D-Tier2）。

---

## 关键事实与约束（改代码前必读；全部实测，证据 = 各条就地标注的 KiCad 源 / 代码 文件:行号）

### 文件方言与解析器

1. **写方言 = v10**（S7 后）：`PcbFile.dumps`（`src/faebryk/core/zig/src/sexp/kicad/pcb.zig`）恒写 v10、
   stamp `version=20260206`；v9/v5 可读，读入后写出即升级 v10（upgrade-on-write）。
   `KICAD_PCB_VERSION/KICAD_FP_VERSION = 20260206`（`pcb.zig:12-15`）。
2. **v10 net 模型**：v10 文件**无顶层 net 表**——net 只在引用处 `(net "名")`；无 `net_name`
   冗余；keepout zone 省略 net 子句。`pcb.nets` 读入时由引用扫描**按名排序合成编号**
   （"" 恒 0、1..n 稠密，引用序无关、跨进程确定）。编号 = 进程内句柄，Python 可见模型不变
   （`segment.net:int`、`pad.net:Net{number,name}`）。推论：**未被引用的 net 不持久化**——
   `insert_net` 必须随后绑定 pad/routing 才留存。
3. **解析器对未知内容**：KiCad 遇未知 token 直接报错（`pcb_io_kicad_sexpr_parser.cpp:1388`）→
   自有元数据只能放 footprint `(property ...)` 或 sidecar，禁发明自定义 token。Zig 侧未知键
   已改为响亮警告/strict 抛错（`fileformats.py` `UnknownSexpKeys`/`last_unknown_keys`）。
4. **`kicad.loads` 缓存 + pyzig 所有权【✅ S1】**：缓存按 (mtime_ns, size) 失效，
   `kicad.dumps(obj, path)` 回写缓存；子对象包装持 owner 强引用链，`loads(...).kicad_pcb` 安全。
5. **version 守卫【✅ S2】**：超 `PCB_MAX_SUPPORTED_VERSION`(20260206) 抛
   `kicad.UnsupportedKicadVersion`。

### 确定性边界

6. **UUID 不透明 ⟹ 确定性 = 语义等价（非字节）【2026-06-15 重定，旧"uuid4+FBRK 后缀 / 增量稳态
   字节一致 / .kicad_pcb 必须入库"作废】**：`gen_uuid`（`fileformats.py`）= 纯 uuid4、无 mark
   （§G/事实 7）。uuid 既随机又无意义 ⟹ **不可 desire 字节等价**，确定性测试一律用 `semantic_view`
   （位置+net 名连通性+结构，剔 uuid/net 编号）；生成态 `.kicad_pcb` 不必作字节锚入库（CI 可两次
   从零构建比 semantic）。完整推论见 §G。
7. **uuid 不透明 = 不塞元数据/不读 flag（FBRK 侧信道已删，2026-06-15）**：`gen_uuid`
   （`fileformats.py`）现无 `mark` 参数、纯返回 uuid4；`transformer.py` 的 `gen_uuid(mark)`/
   `is_marked`/`_add_group` 已清。旧"变长-mark 塞进定长 uuid 静默畸形"的根因（往 uuid 塞数据）随之
   消失。provenance 走 `atopile_address` property + `FBRK:notouch` fp_text。常驻不变量见 §G。
8. **`keep_net_names` 默认随 `frozen`**（`config.py:605-606,639-640`）：常态每次 build 重 derive
   net 名——名字漂移源头。v10 下名字漂移 = 几何归属漂移（事实 2）。

### KiCad 行为

9. **net-0 悬空铜被 KiCad 重存清理**（连带空 group）：写铜层几何必须挂真实 net；引导/标记
   几何放 User.x（User.1=引导走廊、User.2=禁布区）。
10. **atopile 不生成 .kicad_sch，但 `sheetname` 仍可用（旧结论已纠正，见 §C3）**：room/多通道
    **不走 group**，走**每 footprint 的合成 `sheetname`(+`sheetfile`)**（从 ato 层级派生）+ rule area
    `(placement (sheetname "<addr>"))`。**实测（2026-06-14，已落 `test_C3_4`）**：31 字节 room 名给无对应
    .kicad_sch 的 footprint 打 `sheetname`/`sheetfile` + zone placement，`kicad-cli pcb upgrade --force`
    重存（v20260206）**`sheetname`/`sheetfile` 逐字保真**、placement 保真、`drc` rc 不变。
    **关键修正：`path` 不保真**——KiCad 拥有 sheet-instance path，upgrade 时把合成的 `path` 重写成全新
    UUID（实测）。故 **room 键 = `sheetname`（非 `path`），atopile 不写 `path`**（写了也被 KiCad 改且引入
    UUID 不确定性）。zig schema 已建模 `sheetname`/`sheetfile`（`pcb.zig:690-692`）、`ZonePlacement.sheetname`
    （`pcb.zig:828`），故 **rule area 走 sheetname 不需要改 zig**。
11. **DRC JSON 无结构化 net 字段**（net 名嵌描述文本）：诊断层用 items[].uuid 经 layout_ir 反查。
12. **SWIG Python 绑定全禁**（KiCad 10 移除板级 API）；Repeat Layout 仅 GUI 入口。

### 周边

13. **KiCadRoutingTools 已有结构化结果**（`return_results=True`、`JSON_SUMMARY`、`BlockingInfo`）：
    诊断层是聚合+映射；其解析器独立、v9/v10 兼容、不解析 groups——不受本仓库迁移影响。
14. **room rule area 走 sheetname 源，不改 zig**：room rule area 用 `(placement (sheetname ...))`，
    `ZonePlacement.sheetname`（`pcb.zig:828`）已存在 → 不需要改 zig。（曾计划给 zig 加 `group` 枚举/字段
    `pcb.zig:322/824` 当 room 源，C3 改走 sheetname 后弃用——group 无 provenance 槽，见 §C3。）
15. **EasyEDA 取件 = CloudFront WAF（非"限流"）【✅ D1】**：(a) UA 拒绝名单——`easyeda2kicad`
    硬编码 UA / `python-requests` / `Mozilla` 全 403，`curl/*`、node UA 放行；(b) 按 IP 速率——
    突发后即便放行 UA 也短时全 403（响应体 `Request blocked` HTML → `r.json()` 抛
    `Expecting value: line 1 column 1`）。修复见 `easyeda_resilient.py`；warm build 走 1 天缓存零调用。
16. **router 锁定模型 = 跨 stage 硬障碍（白送）+ stage 内软互撕（唯一可调）+ 无 per-net 锁**【源码实测
    2026-06-19】：E1 把每个 route stage 作为**独立** `batch_route`/`batch_route_diff_pairs`/`batch_route_bundle`
    调用、逐 stage 累积 pcb_data。① **跨 stage = 硬障碍、撕不动**：建障碍图时凡不在本次 `nets_to_route` 的
    segment/via/pad 一律当硬障碍（`obstacle_map.py:82-120`：segment :82-96 / via :98- / pad :114-），rip-up 只动本次 `net_ids` 内的 net
    （`rip_up_reroute.py`）——前序 stage 的铜对后续 stage 天然不可撕。故"有序流水线 = 布线优先级**硬保证**"
    无需在 router 加任何锁，**把要保护的 net 放进更早 stage 即不可撕**。② **stage 内（同一次 batch）兄弟 net
    可软互撕**，且这是**唯一可调**项：`max_rip_up_count`（默认 3）/`ripped_route_avoidance_cost`/`_radius`
    （`routing_config.py:60/83-84`）。③ router **无任何 per-net `lock/fixed/frozen` 原语**（已搜证）。
    **推论**：layout.yaml 表达"优先级/不可撕"的正确手段 = **stage 拆分与排序**，不是 per-net 标志；想让一条线
    连兄弟都不动 → 给它单独的更早 stage。

---

## 已完成（结论 + 代码指针；细节看代码）

> 下列各项的实现/契约/不变量已全部写进源码（docstring / 同名测试 / schema）。BACKLOG 只留
> "源码里看不到的结论 + 指针"。要细节请读指针文件，**不要回来这里找**。

### §A 确定性基础【✅ 2026-06-12】
group 成员排序漂移、keep_designators、手工命名 group 被静默删除——全修。
指针：`test/end_to_end/test_group_determinism.py`（变异验证过）；修复 = `pull_group_layout`
末尾全量排序、`_is_managed_group()`（仅清 uuid 后缀=组名 hex 的自管组）。

### P0.1 v10 迁移测试底座（T0–T9）【✅ 2026-06-12】
原则：测试多样性由 harness 制造（corrupter / PYTHONHASHSEED），不靠天然语料（天然语料表序=
编号序，按位置绑定的 loader 能蒙混；T8 变异自检实证 corrupter 必需）。
指针：`libs/test/sexp_tree.py`、`test_fileformats_corpus.py`、`semantic_view.py`、
`test_net_binding_corruption.py`、`test_net_name_properties.py`、CI `.github/workflows/pytest.yml`、
离线 fixture `test/common/resources/{fileformats/kicad/v10,easyeda-cache}/`。

### P0.2 v10 方言迁移 S0–S6 + D1【✅ 2026-06-13】
v10-only（upgrade-on-write）落地：写方言=v10、版本守卫、tenting 嵌套化、net 模型按名合成、
未知键响亮化 + schema 补全、Python 消费方迁移、EasyEDA 取件韧性。
指针（测试即 SSOT）：`test_v10_acceptance.py`、`test_pyzig_ownership.py`、`test_version_guard.py`、
`test_tenting_dialect.py`、`test_unknown_key_loudness.py`、`test_gui_edit_roundtrip.py`（GUI 编辑
回环门）、`easyeda_resilient.py`+`test_easyeda_resilient.py`。
两个 CONFIRMED+FIXED bug 已带回归测试，调试 narrative 不留 BACKLOG：
- **BUG-2** v10 读侧不回填 pad net 名 → rebuild 丢 room 布线：修在 `pcb.zig PcbFile.loads`
  合成 net 表后回填 `pad.net.name`，回归 `test_v10_acceptance.py::test_v10_read_backfills_pad_net_names`。
  （教训"pull≠sync 增量稳态"由 e2e determinism 测试钉；PATH footgun 已记 `CLAUDE.md`。）
- **BUG-1** 测试写回源 fixture：`app` fixture 改 `shutil.copy2` 到 tmp 副本。

### §B layout_ir（文本↔几何唯一接口）【✅ 2026-06-14 全绿；I7 将由 C3 取代为 I7′】
不变量 **I1–I8 + Igeo + B2 全部 = `test/libs/kicad/test_layout_ir_contract.py` 的同名测试**
（**I7「group 成员==sync_groups」由 C3 取代为 I7′「room=atopile_address 前缀派生」**，IR 去 `groups{}`）；
IR 形状/语义 = `src/faebryk/libs/kicad/layout_ir.py` 模块 docstring；
schema = `src/faebryk/libs/kicad/layout_ir.schema.json`（draft 2020-12，随包发布）；
build 步骤产 `build/builds/<t>/<t>.layout_ir.json`（`build_steps.py` 注册 "layout-ir"）。
源码里看不到的结论：
- net 名解析复用 `semantic_view._NetTable`（I3 与第二读者同源，非独立 derive）。
- **bridge②（`signal_nets`, I4b）是唯一需 graph 的部分**，pcb-only IR 不含——故 §C 不阻塞于它。
- **消费者-oracle**：仅用 IR 重建 `_generate_net_map` 必 == 现役（`layout_sync.py`）——C2 重写
  须守此等价（与遗留问题 3 绑定）。

### §C room 几何（forced via / room 复制）【✅ 2026-06-14 全绿；room 表示由 C3 改（C3.6 回开）】
实现 = `src/faebryk/exporters/pcb/layout/room_ops.py`（模块 docstring 即行为权威）；
（room_ops 已 address 派生，逻辑不改；仅随 C3 的新 IR 形状保持兼容，契约测试钉新边界 C3.6。）
契约/DoD = `test/exporters/pcb/layout/test_room_ops_contract.py`（8 测：Tier-1 纯函数 6 +
Tier-2 真 router 2）。冻结 API：`pad_board_xy`、`insert_forced_via→ForcedVia`、
`address_prefix_map`、`room_net_map`（==现役 `_generate_net_map`）、`copy_room_layout→RoomCopy`。
源码里看不到的结论：
- forced via 三件铜（pad→via@in / 跨层 via / via@out→pad）全挂真实 net（非 0，事实 9），
  引导线只在 User.1；C2 复制段未映射 net→0（KiCad 落盘清理，忠实不静默错连）。
- **§B 现实检验通过**：三纯函数一次实现即对，`layout_ir` 零改动。

#### C→E/F 边界事实（源码外——router 不在 atopile 内，§E/§F 必读）
真 router = consumer-oracle（实测 system python3，`grid_router.so` 非本 venv）：
1. **两入口、键集不同**：单端 net → `route.py:batch_route`（`JSON_SUMMARY` 键
   `routed_single`/`failed_single`）；差分对（net 带 `_P/_N`、`P/N`、`+/-`）→
   `route_diff.py:batch_route_diff_pairs`（`routed_diff_pairs`/`failed_diff_pairs`）。共有键
   `failed`/`successful`/`total_vias`。**喂错入口 → 不打印 `JSON_SUMMARY`**。
2. **只路由未连通 net**：已连通 net → `failed=1` 无输出，或 "nothing to route" 不打 summary。
   故无"预置 via 当软 waypoint 绕行"模式——forced via 被"尊重" = 它已使 net 连通、router 不动它；
   C2 复制段同理（普通已连通铜）自动保留，**§E 无需 lock/preserve §C 预置几何**。
3. **`total_vias` 只计 router 新增 via**，不含预置 forced via——存活/板上总数须**重读输出板**。
4. **User.* 默认 router 不读**（只读铜层）——但 router 有原生开关
   （`vendor/KiCadRoutingTools/routing_config.py:117-123`）：`guide_corridor_enabled`（读 User.1 引导线，
   把 net 沿走廊牵引）+ `keepout_enabled`（读 User.2 禁布多边形，挡走线）。故 §C 的 User.1 引导
   / §D 的 User.2 room 边界**可被 E1 显式启用为一等布线约束**（默认关、按 stage 开，见 §D/§E1）——
   非仅可视件。这两路与 §D 的 placement rule area（KiCad 自身的分组/DRC，另一机制）不要混淆。
- **设计决策（双入口非缺陷）**：两入口 = 两套真算法共享同一 Rust 网格内核
  （`grid_router:GridObstacleMap/GridRouter`）。差分 = `PoseRouter` 位姿法 + `diff_pair_gap`
  恒定间距 + `centerline_setback`（pad 附近自动 fanout/打散，对应 decoupling 抽头 / 连接器
  pitch）+ `fix_polarity` + `length/time_matching` + `gnd_via`；单端 = `route_multipoint_main`/
  `power_nets`。行业惯例（Altium 亦分差分/单线两器），**非 bug**。**不合并算法（合并=倒退，丢
  耦合/极性/等长/位姿/centerline）**；mode 由 `route_stages` 显式声明（§D/§E1）。
- E3 薄片仅验**差分对** schema（`test/exporters/pcb/layout/test_router_smoke_batch_route.py`，shell-out 至 system py3 跑 `vendor/KiCadRoutingTools`）；
  单端 schema 待 §E3 补。

### §C3 room 去 group 化（room = footprint sheetname）【✅ 2026-06-15】
决策：room 不再 = KiCad group。atopile **建零个/删零个 group**（用户 group 按构造永存，A4 失效模式消失）；
room 载体 = footprint `sheetname`(+`sheetfile`)（值 = ato 地址前缀，`_get_room_name` 派生；**不写 `path`**——
KiCad 拥有并重写它，事实 10）；route/via/zone 归属 = 内部 net；rule area 走 `(placement (sheetname))`，无 zig 改动。
协议 + 接口 delta = 代码 SSOT：
- 契约 `test/exporters/pcb/layout/test_room_migration_contract.py`（模块 docstring = 协议全文 + 棘轮）；
  e2e `test/end_to_end/test_room_migration_e2e.py`（建组数=0 / pull 语义确定性）。
- 实现 `layout_ir.py`（`rooms` 派生）+ `layout_sync.py`（`sync_rooms`/`pull_room_layout`/`_clean_room`，
  删 `_is_managed_group`）+ `room_ops.py`（`copy_room_layout` 只复制本 room）+ `build_steps.py`/`cli/kicad_ipc.py`。
源码里看不到的结论：
- committed fixtures 仍含旧 group，但**已无任何测试把 group 当 room 源读**：曾经的一次性迁移证明
  `test_C3_1_corpus_groups_are_address_prefix_recoverable`（从 pcb 读旧 group 比对地址前缀）**已退役**
  （`test_room_migration_contract.py` 注释记其移除——保留会延续 C3 已废除的 ato→group 耦合）；现役
  `test_C3_1_inline_room_equals_address_prefix_grouping` 只读 sheetname 派生的 `ir['rooms']`。含 group 的板
  仅由 parser corpus（`test_fileformats_corpus`）当不透明用户内容回环覆盖。
- `transformer._add_group`/`is_marked`/`gen_uuid(mark)` 已删（§G uuid 不透明）——atopile 无 ato→group 映射。
- cascade：§B I7→I7′（IR 去 `groups{}`）、§C room_ops 随新 IR 形状（契约钉 C3.6）。

### §D layout.yaml 加载 + KiCad placement rule area（D1–D4）【✅ 2026-06-16】
layout.yaml = 布局意图源（.ato 电路源 / .kicad_pcb 几何源的对等源）。D1 路径配置 → D2 解析校验成 `LayoutPlan`
→ D3 每 room 落 placement rule area → D4 build 步骤接线 + 产 `<t>.layout_plan.json`（供 E1）。
**架构（声明式 rooms + 有序 route_stages 流水线、为何用 yaml 非 .tcl）= `layout_plan.py` 模块 docstring「FORM」节（SSOT）。**
实现：
- `src/atopile/config.py`（`BuildTargetPaths.layout_config`，D1）。
- `src/faebryk/exporters/pcb/layout/layout_plan.py`（`LayoutPlan/Room/RouteStage/GridRouteOverride` +
  `load_layout_plan`/`resolve_nets`，D2；模块 docstring = 协议全文）。
- `src/faebryk/exporters/pcb/layout/rule_area.py`（`generate_rule_areas`，D3）。
- `src/atopile/build_steps.py`（`generate_layout_plan` 步骤，挂入 `generate_default`，D4）。
契约：`test/test_config.py`（D1）、`test_layout_plan_contract.py`（D2）、`test_rule_area_contract.py`（D3）、
`test/end_to_end/test_layout_plan_build.py`（D4）。
源码里看不到的结论：
- **`GridRouteOverride` 的 oracle = 两 router 入口入参 union，NOT `GridRouteConfig`**【2026-06-16 独立验证纠偏】：
  `route.py:batch_route`/`route_diff.py:batch_route_diff_pairs` 收扁平 kwargs，内部才建 `GridRouteConfig`
  并改名（`impedance`→`impedance_target` 等）。mode 互斥键（`guide_corridor_*` 单端 /
  `diff_pair_*`·`fix_polarity`·`gnd_via_*` 差分）在 **D2 parse 期按 mode 拒错键**，E1 不重复校验。漂移钉 =
  `test_layout_plan_contract` 的 union drift guard + mode 一致性自测（AST 取真 router 签名，漂移即红）。
- 实施期查出并修的两个真缺陷：① placement 写 `source_type`/`source` → **SEGFAULT KiCad loader**（错建的内存/
  protobuf 字段，文件语法无此 token；见「关键事实」placement 条 + 回归 `test_generated_placement_has_no_source_type`）；
  ② 显式 origin/size 的未知 room module 曾**静默落空 rule area**（S5a 违规）→ 改为两 geometry mode 都响亮
  （回归 `test_unknown_room_module_is_loud`）。
- D 的产物 = E/F 输入：`<t>.layout_plan.json`（resolved route_stages + room 元数据）给 E1；板上 rule area 供
  KiCad placement/DRC + F-diag 命中测试。E1 把 `RouteStage.config` 原样展开成入口 kwargs（翻译在 router 内）。

### §D-Tier2 bundle 总线传输（mixed single/diff schema + 几何）【✅ 桶① 2026-06-20；D 侧 build 集成 2026-06-25】
bundle = 有序 lanes（single/diff）+ 分段 trunk + 两端 breakout 的布线意图单元（flat `route_stages` 表达不了）。
桶①（D 内 model + 几何 SSOT + build 注入）已落地；桶②（`batch_route_bundle` 实现 + breakout 扇出）= **§E-Tier2**
（契约已 strict-xfail 冻死，实现见未完成 §E-Tier2；协议全文 = `test_bundle_contract.py` 模块 docstring SSOT）。
指针：`layout_plan.py`（`BundleStage`/`SingleLane`/`DiffLane`/`Trunk`/`Breakout`/`RipUpBudget`，模块 docstring = 协议）、
`bundle_geometry.py`（`cross_section_offsets` 几何 SSOT + `bundle_artifact`）、`build_steps.generate_layout_plan`
（bundle stage 调 `bundle_artifact` 注入算好的 offset，plain stage 走 model_dump）；契约 `test_bundle_contract.py`
（桶① 全绿、桶② strict-xfail 待 E-Tier2）；e2e `examples/sata_bundle` + `test/end_to_end/test_bundle_build.py`。
源码外结论：差分对全程不解散（L1 内在耦合），bundle 只在外层加顺序+lane 间距；过渡段两端 offset 给死、只 E 搜 morph。

### §D-Tier3 自包含摆放 + 板级 `board` 段【✅ 桶① 2026-06-26；桶② TS-AUTH-A 2026-06-28】
生成式 / agent flow 里 `.ato`（电路）+ `layout.yaml`（布局）必须自包含、是唯一权威；`.kicad_pcb` 是派生产物、
reuse 仅可选优化、不得是任何事实的唯一通道（「为避免 GUI 必须先用 GUI」= 设计缺陷）。已落地：
- 摆放：`Placement`/`resolve_placement`（room 相对解析 + `absolute` 板绝对逃逸口）/`resolve_component_pose`
  （placements>reuse 优先级；文本给值即覆盖、缺省回退 reuse、皆无则 loud），坐标系 = room 相对（块可复用）。
- room 几何：`Room.polygon`（≥3 点、非自交，dependency-free 自交检测）+ rotation（绕首点 CCW）+ per-room layers
  接线生效（`rule_area._room_boundary`/`_rotate`；修了"声明却被 D3 静默忽略"的 S5a 隐患——要么生效要么删字段）。
- `board` 段：`BoardOutline`（origin/size 或 polygon）/`Stackup`/`StackupLayer` + 纯函数 `stackup_layers`/`outline_bounds`；
  impedance→stackup **硬依赖** plan 级校验（无 stackup 即 loud，钉 `route.py:256-258` 静默退固定线宽 = 阻抗失控）。
- **单一层数权威（板侧已闭环，TS-AUTH-A 绿）**：`config._stackup_copper_names`/`_copper_layer_table` 由
  `stackup_layers(board.stackup)` 派生 fresh board 的 copper 层表（KiCad 编号 F.Cu=0/内层 1../B.Cu=31），杀
  `config.py` 旧 2 层硬编码——board 与 router 层数同源，物理上不可再分叉。
指针：`layout_plan.py`、`rule_area.py`、`config.py`（`ensure_layout`/`_stackup_copper_names`/`_copper_layer_table`）；
契约 `test_placement_contract.py`（TP/TR）、`test_board_section_contract.py`（TB/TS/TS-AUTH-A 绿、TS-LM′ 单一权威锁）。
源码外结论（仍未完成、归 §E）：**路由侧**层数权威（E1 把 `stackup_layers` 当 `layers` 传 router、不吃 4 层默认）=
**TS-AUTH-B = §E1**；纯文本无 reuse 端到端建板 = **桶③ = §E DoD**；reuse-only 的板级 net-class/pour/keepout/silk
（缺失须 loud、不缺省）= **§F**（F-drc-rules/F-fill/F-keepout/F-silk）。

### Tier0 corridor-as-data【✅ 2026-06-28】
single stage 的 `corridor: [[x,y],...]` 文本字段 → build 画 User.1 polyline + 置 `guide_corridor_enabled`，零 router
改动（复用 router 原生 guide reader，§C 边界事实 4）；软牵引、非硬 checkpoint。指针：`corridor.py`（`draw_corridors`，
idempotent）、`RouteStage.corridor`（`layout_plan.py`，single-only + ≥2 点 loud）、`build_steps.generate_layout_plan`；
契约 `test_corridor_contract.py`。

### route_stage `name` 唯一【✅ 2026-06-28】
stage name 唯一性 loud 校验 = `--up-to` 断点按名寻址的前提，且杜绝 `resolve_nets` 同名 stage 静默覆盖。指针：
`LayoutPlan._validate_unique_stage_names`（`layout_plan.py`）；契约
`test_layout_plan_contract.py::test_duplicate_stage_name_is_loud`（+ 正向控制 `test_distinct_stage_names_pass`）。

### placements → transformer build 时消费【✅ 2026-06-28】
纯函数权威 `resolve_component_pose`（文本>reuse 优先级，§D-Tier3 已落）现已接进 build：`apply_placements` 把每个
文本 placement 命中的受管 footprint（按 `atopile_address` property 匹配）移到解析后的位姿，**覆盖** transformer 的
自动 10mm 网格摊开（`transformer.py:176`/`:2013-2080`）——room 相对经 room origin 复合、`absolute` 逐字落、
rotation+side 经 transformer 的翻转感知 `move_fp` 应用；命中不存在的 footprint = loud。在 `generate_layout_plan` 里于
`layout_ir` 之前调用，故 room 派生 bbox 反映最终位置。指针：`placement.py`（`apply_placements`/`_room_for`，room =
component 地址最长前缀的 room）、`build_steps.generate_layout_plan`；契约 `test_placement_apply_contract.py`（PA1-6）。

### §E1 route runner（route_stages → 路由调用 + route_report.json）【✅ 2026-06-28】
`layout_plan_runner.py`：纯 `build_invocations`（route_stages → `StageInvocation` 列表，零 subprocess/零 router import）
+ `run_route_stages`（按 stage 驱动 invoker、聚合、写 `route_report.json`）+ `default_subprocess_invoker`（shell 到
system python3 跑 router、解析 `JSON_SUMMARY`，早返=None）。落地契约：按 stage 类型分派（single→`route.batch_route`／
diff→`route_diff.batch_route_diff_pairs`，union tag 先于 `RouteStage.mode`）；config 逐字展开（仅 explicitly-set，余走
router 默认、不漏 None、不重校 D2 已校的 mode 键）；**层表唯一权威** = `stackup_layers(board.stackup)`，绝不吃
`route.py:230` 四层默认（**翻绿 TS-AUTH-B**）；缺 board/stackup 或 per-stage `config.layers` 一律 loud（单一权威）；
跨 stage 板累积（前序铜=白送硬障碍，不加锁）；容忍缺 `JSON_SUMMARY`（早返=零布线、不 KeyError）；聚合 common
标量键（含 `total_time`/`total_iterations`）从 summary dict 求和、per-type 列表分桶；`--up-to <name|1-based index>`
断点（写部分板+report，越界/未知 loud、bundle 在 slice 外不报，`report.up_to` 归一为 stage 名）。
指针：`layout_plan_runner.py`、契约 `test_layout_plan_runner_contract.py`（R1-R14 纯 + E15/E16/E17 e2e；
bundle 分派 R2/R2b/R2c/R2d/R2e + bundle e2e E18 见下「§E-Tier2」）；TS-AUTH-B 棘轮探针改指 runner
（`test_board_section_contract.py::_e1_passes_stackup_to_router`，旧指 build_steps——路由非 build step，已纠）。
源码外结论（诚实记限制）：
- E1 是**库**：现由 e2e 测试消费，CLI `ato route` 走 §F（路由非 `ato build` 步骤，故不进 build pipeline）。
- **bundle 分派**（`batch_route_bundle`）= **§E-Tier2 已落地**（见下），E1 经 `_bundle_invocation` 展开
  `bundle_artifact` 成几何 payload、成员名经 bridge② 解析、breakout `at`→`part`+kicad 序。
- e2e（E15/E16/E17/E18）gated on system python3 + router 板；**本沙盒 scipy 在场、真跑过**（LVDS 2 层板，diff 对
  真布通 `successful>=1`，bundle 真铺 3 成员铜）；无单端 fixture 板，故 E15 用单端入口路由 diff 对的一条线并容忍早返。
- **per-stage 层子集化 = 有意非目标**（单一权威）：per-stage `config.layers` 响亮拒；将来若需按 stage 限层另议。

### §E-Tier2 bundle 路由（`batch_route_bundle` + runner 分派 + e2e）【✅ 2026-06-29】
`vendor/KiCadRoutingTools/route_bundle.py:batch_route_bundle`：把冻结的 bundle 契约（分段 trunk + 有序成员 offset
表 + 2 breakout）变成**平行总线**——每成员一条沿 centerline 的 offset 轨。trunk = **确定性几何、零 A***：横截面
按每 vertex 的 `spacing` **重排**（`_repack_offsets` 镜像 `bundle_geometry.cross_section_offsets`），成员宽度与每
diff 对的 intra gap 恒定（bundle-global 不变量）；刚性段不变、过渡段 morph 但 **L1 耦合不散**（只动 inter-lane
spacing、绝不动对内 gap）。成员输入 offset 当可信入口 profile，per-vertex offset = 输入 + 重排 DELTA（刚性=输入逐字、
过渡=加 morph 增量，P/N 同增量故 gap 恒定）。breakout 扇出（pad→trunk 端）仅在有板时跑。逐成员 routed/blocked 结果 +
stdout `JSON_SUMMARY`（含 `members[*].polyline`/`routed_members`/`failed_members`/标量键）。geometry-only 模式（无
input_file）= 纯 python、无 rust/无 parser，故 D-Tier2 契约直接驱动它；有板模式惰性导入 parser/writer。
runner 侧：`build_invocations` 经 `_bundle_invocation` 分派 bundle（entry/module=`route_bundle`/`batch_route_bundle`、
config 逐字展开、层表←stackup、跨 stage 板累积）；`default_subprocess_invoker` 按 stage 类型选调用约定（bundle =
geometry-driven，trunk/members/breakouts 在 KW，input/output 为关键字参数）。
指针：`route_bundle.py`、`layout_plan_runner.py:_bundle_invocation`；契约 `test_bundle_contract.py`（T-B1 AST 漂移钉 /
T-B2 result-shape / T-B3 平行总线 / T-B3b offset→轨变异自检 / T-B4 刚性 diff 耦合 / T-B6 过渡 morph 钉 SSOT+刚性段不
morph+对内 gap 恒定）+ `test_layout_plan_runner_contract.py`（R2/R2b 分派 / R2c config / R2d 层冲突 / R2e 聚合 /
E18 e2e：LVDS 板真跑、route_report by_type['bundle']、板真增铜）。
源码外结论（诚实记限制）：
- **breakout 扇出 = 直连**（pad→较近 trunk 端的直线段），**非** obstacle-aware A*；breakout `order` 排列与
  `spacing_overrides` **尚未被几何消费**（仅 schema/校验存在）；交叉局部化/扇出优化 = 后续。
- **逐成员失败路径未被测试穷尽**：trunk 几何永成功 ⇒ routed=True；有板而缺 pad 时 `blocked` 记串但不翻 routed=False、
  不计 failed。真正的 failed 计数路径（无效几何）未被 fixture 触发。
- **过渡 morph 保真**：用「输入 offset + 重排 DELTA」模型，刚性段精确、过渡按 cross_section_offsets 语义重排（对内
  gap 恒定）；曲折 centerline 顶点法线用相邻段角平分线（直线 trunk 精确，强弯角 miter 近似）。

---

## 未完成任务

**本节自上而下 = 执行序**（章节字母 = 关键路径顺序）。已完成前置（C3+D / D-Tier2 桶①② / D-Tier3 桶①·TS-AUTH-A /
Tier0 corridor / stage-name 唯一 / placements→transformer / **§E1 runner+TS-AUTH-B+`--up-to`** /
**§E-Tier2 bundle 路由+e2e**）全见上「已完成」。
**剩余关键路径 = §E DoD（桶③ 纯文本自包含建板 e2e）→ §F**。

contract-first 下游棘轮的契约留在各自 D 测试文件、实现**就地落进 §E**，故**不再另列 D 残块**：
桶② = **§E-Tier2 ✅**；**TS-AUTH-B（router 层表）✅ 落在 §E1**；桶③ 纯文本 e2e = **§E DoD**（路由半边已由
E18 bundle e2e 证；剩自包含建板半边）。

真正不在关键路径上的旁支（P0.2-S7 终验、D5 component_class）收进 §E/§F **之后**的「旁支任务」节、**故意不编 E/F
序号**（编入会假称其在关键路径上）。

### E.【需 §C + §D】KiCadRoutingTools fork
E1 需 §D 的 plan + 板上 rule area，且用 §C1 的 forced via 当布线阶段能力。E3 全程并行先搭。
**E1 dispatch 从一开始按「single / diff / bundle」三类 stage 设计**（D-Tier2 契约冻结后填 bundle 实现，勿事后改）。
- [x] **E1** `layout_plan_runner.py`【✅ 2026-06-28，见上「已完成 §E1」】：route_stages → 按 stage 类型分派的
  路由调用（single→`batch_route`／diff→`batch_route_diff_pairs`／bundle→`route_bundle.batch_route_bundle` 见 E-Tier2）+
  `route_report.json`；config 逐字展开、层表 ← `stackup_layers`（**翻绿 TS-AUTH-B**）、`--up-to` 断点、跨 stage
  板累积不加锁、容忍缺 `JSON_SUMMARY`、按类型聚合。契约 `test_layout_plan_runner_contract.py`。
- [x] **E-Tier2 `batch_route_bundle` + breakout 扇出 + e2e**【✅ 2026-06-29，见上「已完成 §E-Tier2」】：平行总线
  确定性几何（per-vertex 重排 = `cross_section_offsets` 镜像，刚性段不变/过渡 morph、diff 对内 gap 恒定不散 L1）+
  直连 breakout 扇出（有板时 pad→trunk 端）+ 逐成员 routed/blocked + `JSON_SUMMARY`；runner `_bundle_invocation`
  分派 + `default_subprocess_invoker` bundle 调用约定。契约 `test_bundle_contract.py` T-B1..T-B6 +
  `test_layout_plan_runner_contract.py` R2/R2b/R2c/R2d/R2e + E18 e2e（LVDS 板真跑）。
  - 限制（诚实记，见上「§E-Tier2」）：breakout = 直连非 A*、`order` 排列/`spacing_overrides` 未消费、逐成员失败
    路径未被测试穷尽、强弯 centerline 法线 miter 近似。交叉局部化/扇出优化 = 后续按需。
  - **E2 阶段内 rip-up 旋钮 = 不做（用户 2026-06-29 拍板：非必要、与「关键事实」16 重复）**：跨 stage 前序铜本就是
    router 白送的不可撕硬障碍（优先级由 stage 顺序表达），bundle/流水线的"前线占位、后线绕行"已是硬保证。
    `BundleStage.rip_up`（`RipUpBudget`）schema 已在（D-Tier2 落），如将来真需 stage 内"几乎不重排"再把
    `max_rip_up_count`/`ripped_route_avoidance_cost`/`_radius` 接进 config——无新建跨 stage 锁的需求。
- [ ] **E3** 独立回归台（全程并行先搭 = §E 的 S0 底座）：无需装 KiCad，预编译 Rust 二进制 +
  内置测试板 + numpy/scipy/shapely；失败注入 + 报告 schema 校验。
  - 最小切片已提前到 §C【✅】：`test/exporters/pcb/layout/test_router_smoke_batch_route.py`
    钉死差分对 C→E 接口（`return_results=True` 四元组 / `results_data` 几何字段 / `JSON_SUMMARY`
    schema）。整台 E3（失败注入 + 多板矩阵）仍待此处。
  - **待补：单端 `route.py:batch_route` 的 JSON_SUMMARY schema 校验**（边界事实 1）——薄片只验
    差分对键集，单端走 `routed_single`/`failed_single`，须扩第二条 schema 校验防静默失配。
- [ ] **§E DoD — 桶③ 纯文本 e2e（翻绿 D-Tier3 桶③棘轮）**：无 reuse，`board.outline` + 完整 `board.stackup` +
  `placements` 全文本给定 → 生成 .kicad_pcb → 重读 `semantic_view` 验层表 / 板框 / 摆放落位 == plan（板上无
  In1/In2.Cu 幽灵层）。证明 `.ato`+`layout.yaml` 自包含建板，是 §E 路由落地后的完工判据。
  - **路由半边已证**（§E-Tier2 E18 bundle e2e：真板真布线 + `route_report.json` + 板真增铜）；**剩自包含建板半边**
    = `ato build` 从纯文本（无 reuse 源板）产出带 outline+完整 stackup 的可布线 .kicad_pcb（build 侧，较重、
    gated not_in_ci），与路由 runner 解耦。

### F.【最后做，需 §E route_report + B 的 ir 反查】诊断闭环 + 板级规则文本化

§E 已落（E1 route runner + E-Tier2 bundle，`route_report.json` 在场）。§F 是收官章：
build→route→**diagnose**→(SKILL 改文本)→重建 的反馈闭环——项目的立身前提（"命令式反馈活在文件外的闭环里"）。
把一次布线运行 + KiCad DRC 变成结构化、按 ato 地址索引的 `diagnostics.json`，并把现为 reuse-only 的
板级 authoring 关切（DRC 规则 / 灌铜 / 禁布 / 丝印）文本化，使 DRC"绿"反映设计意图而非 KiCad 默认。

**用户拍板（2026-06-30）**：scope = **全做**（F-route…F-silk）；cause depth = **含 blocking cause**
（改 vendored router 暴露"为何没布通"，不只"哪条/哪里"）。

#### §F 是什么（各件）
1. **F-route** — `ato route` CLI：从某 build 的 config 跑 §E1 runner，产出布通板 + `route_report.json`。薄壳。
2. **F-diag** — `ato diagnose` → `diagnostics.json`：聚合 `route_report.json`（失败身份 + blocking cause）+
   `kicad-cli` DRC + rule-area 命中测试，反查回 ato 地址/room。
3. **F-drc-rules** — 板级 net-class/DRC 规则（clearance、线宽类、via 类）文本化，让 DRC 判设计意图。缺失须 loud。
4. **F-fill / F-keepout / F-silk** — 灌铜 pour、非 placement 禁布、丝印（各 reuse-only）文本化。缺失须 loud。

#### 前置评估
**已满足 ✅**
- **`route_report.json` + 进程内 runner**：`run_route_stages(plan, ir, *, input_board, workdir, invoker=…,
  report_path=…)`（`layout_plan_runner.py:375`）可进程内调；每 stage 完整 `JSON_SUMMARY` 逐字留在 `stages[].summary`
  （`:114`）。失败**身份**已在：`failed_single`/`failed_diff_pairs`/`failed_members`（名）+ `failed_multipoint`
  （逐 pad `component_ref`/`pad_number`/`x`/`y`）。
- **CLI 框架**：Typer app `src/atopile/cli/cli.py:51`，命令显式注册 `:204-218`。模板 = `src/atopile/cli/view.py`
  （config bootstrap：`config.apply_options(...)` + `config.select_build(name)`）。输入派生自
  `config.build.paths.{layout, output_base, layout_config}`（`config.py:296`）；plan =
  `output_base.with_suffix(".layout_plan.json")`，ir = `…".layout_ir.json"`。
- **DRC 钩子**：`run_drc(pcb: Path) -> C_kicad_drc_report_file`（`src/faebryk/libs/kicad/drc.py:13`），typed
  （`other_fileformats.py:49`）：`violations[]` 带 `description`/`severity`/`type`/`items[].uuid`+`items[].pos`。
  kicad-cli 10.0.3 在场；DRC oracle 测试已绿（CLAUDE.md "S7 DRC oracle xfail" 是**过时**笔记——顺手纠）。
- **rule-area 命中测试**：受管 zone 名 `rule_area_<sheetname>`，带显式 `polygon`（`rule_area.py:118`）；
  point-in-polygon → room/ato 地址，**无需 uuid**。
- **net 名 → ato 地址**：IR `nets`（net → `["<addr>.<pad>"]`）+ `signal_nets`（addr → net），`layout_ir.py:154,175`。

**§F 必须补的缺口**
- **G1 — "为何"只在 stdout 且被丢弃。** `BlockingInfo`（`blocking_analysis.py:63`：blocking net + frontier/track/
  via/near-endpoint cell 数）、net-history `top_blockers`、`analyze_static_blockers`（pad/track/zone）都是
  **print-only**；runner driver 只 regex 抽那一行 `JSON_SUMMARY:`、其余丢弃（`layout_plan_runner.py:324`）。
  `return_results=True` **无助**——它是 geometry-to-apply、无诊断。→ **必须改 vendored router** 发结构化诊断行 +
  教 runner 捕获。
- **G2 — 无 uuid → ato 地址反查。** IR 把 `footprint_uuid` + pad `uuid` 作为按地址索引的正向值存、不发反向索引；
  **track/via/zone uuid 根本不在 IR**（它们在布线时生成、晚于 build 期 IR）。DRC 按 uuid 引项 → §F 主用 net 名
  （DRC 描述文本嵌 net 名）+ 坐标命中测试（rule-area polygon），footprint/pad-uuid 自建反向索引当 best-effort。
  无需改 IR schema。
- **G3 — `total_vias` 只是本次新增 via**，非板上总数（`route.py:700`）。§F 重读输出 `.kicad_pcb`（`pcb.vias`）取真值。
- **ZonePlacement SEGFAULT 约束（锁死）**：placement zone **只**设 `enabled`+`sheetname`，绝不设
  `source_type`/`source`（SIGSEGV kicad-cli）。F-keepout/F-fill emit 前须核其 zone 文法不重蹈。

#### 推荐执行序（按依赖排，每件 tests-first 严格 xfail 棘轮）
> 全程沿项目纪律：S0 严格 xfail（import/AST 探针守门）；consumer-oracle pinning（消费者真接口定契约）；
> loud-or-nothing（S5a）；code=SSOT（不变量进 docstring，BACKLOG 只留结论+指针）；semantic-view 非字节等价；uuid 不透明。

- [ ] **F1 — `ato route`（薄壳 CLI）**：新 `src/atopile/cli/route.py` + 注册 `cli.py:204-218`。仿 `view.py`
  bootstrap config，从 `config.build.paths` 派生 plan/board/ir，调 `run_route_stages(...)`，打印摘要；
  `--stage`/`--up-to` 透传。契约：build→route e2e（仿 `test/end_to_end/test_layout_plan_build.py` `_build`/`run_live`，
  `@slow @not_in_ci @skipif(no kicad-cli/py3)`）。
- [ ] **F2 — router 诊断暴露（vendored submodule；consumer-oracle、契约先行）**：在 `vendor/KiCadRoutingTools` 把
  结构化失败 cause 折进**新** stdout 行 `JSON_DIAG: {…}`（与 `JSON_SUMMARY` 分开，免污染 runner `by_type` rollup）：
  逐失败 net → `{net_name, blocked_by:[{net,blocked_count,near_target,near_source}], static:{pads,tracks,zones},
  history:[events]}`，源自 `BlockingInfo`（`blocking_analysis.py`）+ `RoutingState.net_history`（`routing_state.py:112`）。
  再教 `default_subprocess_invoker`（`layout_plan_runner.py:283`）也抓 `JSON_DIAG`、挂到新 `StageResult.diag` →
  `route_report.json`。诊断**消费者**（F3/F4）冻结 `JSON_DIAG` 形状、router emit 之（同 D-Tier2→E-Tier2 方向反转）。
  router 改在 feature 分支 + 父仓 gitlink bump（同 `route_bundle.py`）。
- [ ] **F3 — bridge② 反查解析器（纯 helper）**：新模块（如 `src/faebryk/libs/kicad/layout_ir_resolve.py` 或
  `diagnostics/` 包）：给 IR + 板，建 `{footprint_uuid→addr, pad_uuid→(addr,pad)}` 反向、`net 名→[addr.pad]`
  （从 `nets`）、`coord→room`（rule-area polygon 命中）。纯、venv 单测（伪造 IR）。= F-diag 依赖的关联引擎。
- [ ] **F4 — `ato diagnose` → `diagnostics.json`（核心交付）**：新 `src/atopile/cli/diagnose.py` + `diagnostics`
  builder。聚合：route_report 失败 + F2 blocking cause + `run_drc()` violations + 重读板（G3 真 via/几何总数）；经 F3
  关联回 ato 地址/room；产 findings。**schema** = kicad-happy `make_finding` 改造
  （`/kicad_wksp/kicad-happy/skills/kicad/scripts/finding_schema.py:21`）：`rule_id`、`severity∈{error,warning,info}`、
  `confidence∈{deterministic,heuristic,…}`、`evidence_source`、`report_context{section,impact,standard_ref}`、
  `components`、`nets`、`recommendation` —— 加 §F 字段：`stage`、`room`、`ato_path`、`constraint`（指 layout.yaml 的
  JSON-pointer）、`reason`、`blocking_nets`、`failed_endpoints`、`suggestions`。deterministic `sort_findings` 稳照。
  DRC 对 未布线-vs-已布线 板分新 vs 既存（Ki-Stack 套路）。契约按**按构造定答案** fixture（已知不可布通 → finding 指名
  blocker），非"跑了没报错"。
- [ ] **F5 — F-drc-rules（板 authoring）**：板级 net-class/DRC 规则源文本化，使 DRC 判设计意图；规则缺失 loud
  （warn/拒），绝不静默吃 KiCad 默认。使 F-diag 的 DRC 权威。emit 前核 zone/规则文法对照 SEGFAULT 约束。
- [ ] **F6/F7/F8 — F-fill / F-keepout / F-silk（板 authoring）**：灌铜 pour（现 reuse-only `room_ops.py:296`）、
  非 placement 禁布（placement 类已 §D 出）、丝印（reuse-only `layout_sync.py:288`）文本化。各：文本权威 + emit +
  缺失 loud。本 tranche 内低优先；F-silk 最后。

#### 关键文件
- 新 CLI：`src/atopile/cli/route.py`、`src/atopile/cli/diagnose.py`；注册 `cli.py:204-218`（+ import `:23-36`）。
- 新 lib：`diagnostics` builder + `layout_ir_resolve`（反向关联）；复用 `run_route_stages`（`layout_plan_runner.py`）、
  `run_drc`（`libs/kicad/drc.py`）、IR（`libs/kicad/layout_ir.py`）、rule-area polygon（`exporters/pcb/layout/rule_area.py`）。
- vendored router（submodule、feature 分支）：`blocking_analysis.py`、`routing_state.py`、`route.py`、`route_diff.py`、
  `route_bundle.py`（emit `JSON_DIAG`）+ runner `layout_plan_runner.py`（捕获）。
- 板 authoring：net-class/DRC-规则 + pour + keepout + silk emitter（新），受 `rule_area.py`/CLAUDE.md 的
  ZonePlacement 约束指引。
- 仪式：`BACKLOG.md` §F + `CLAUDE.md` sidecar 列；新契约测试 `test/exporters/pcb/layout/` + `test/end_to_end/`。

#### 验收
- **单元（venv，无 router/板）**：F3 反查器对伪造 IR（uuid/net/coord → addr/room，含 track-uuid 不可解的兜底）；
  F4 finding builder 对 canned route_report + DRC report（deterministic `sort_findings` 快照）。
- **router 侧（system python3）**：F2 `JSON_DIAG` 形状由 shell-out 在已知阻塞 fixture（§C/§E LVDS 板 + 故意不可布通
  net）钉死；断言 `blocked_by` 指名真 blocker。
- **e2e（`@slow @not_in_ci @skipif(no kicad-cli/py3)`）**：build 一例 → `ato route` → `ato diagnose`；断言
  `diagnostics.json` 把强制失败/DRC 违规关联到正确 `ato_path`/`room`，且分清既存 vs 新 DRC。沙盒可跑
  （kicad-cli 10.0.3 + scipy 在场）。

#### 诚实记限制（落地时写）
- uuid→地址仅 footprint/pad（自建反向）；track/via/zone DRC 项按坐标命中 + net 名关联、非 uuid（track/via uuid
  从不进 build 期 IR）。
- blocking cause = router frontier/static 分析（启发式排名），原样暴露。
- 板 authoring 半边（F5–F8）：首交付 = 文本→板 emit + 缺失 loud；DRC 规则对 KiCad 完整规则文法的覆盖是增量。

#### 开放技术次决策（已给推荐、不挡）
- 独立 `JSON_DIAG` 行 vs 扩 `JSON_SUMMARY` → **独立**（保 runner 标量/列表 rollup 干净，免再像 `members` 那样膨胀 report）。
- diagnostics.json finding schema → **采 kicad-happy `make_finding` + §F 字段**（上）。
- F3 落点 → 小纯模块比扩 `layout_ir.py` 干净；二者皆可。

### 旁支任务（可并行 / 不挡关键路径；故意不编 E/F 序号）
与 §E/§F 无硬依赖、可择机做；保留各自原标识（不塞进 E/F 编号，以免假称在关键路径上）。

- [ ] **P0.2-S7 flag-day 终验剩余**（写 v10 代码已落 + 单测/e2e determinism 已绿；**需可跑全量 example build 的环境**——
  本仓库 sandbox 缺 router scipy 等 build 依赖，无法在此验，故未做、不可盲改已提交输入板）：
  - examples/fixtures 的 v9 输入板（esp32_minimal / layout_reuse·sub / led_badge×3 / test-project×2 / faebryk example
    共 8 块；sata_bundle 已是 v10）一次性 v9→v10 升级提交（load+dump 即升级，`kicad-cli` 在场）；build→build→diff 确认
    增量稳态（事实 6，复用 `test_group_determinism.py`+`semantic_view`，现仅覆盖 layout_reuse，需小幅参数化到其余）。
    **勿动** `fileformats/kicad/v8|v9` 语料板（它们是有意保留的旧版只读输入）。
  - BOM / 制造产物 / DRC smoke：BOM+DRC 已在默认 build 跑、mfg-data 在 `all` target；缺的只是一个断言
    `.bom.csv`/`.bom.json`/`.gerber.zip`/`.pick_and_place.csv` 产出且 BOM 非空的 smoke e2e（复用现有 `_build` fixture）。
- [ ] **D5 component_class placement**（component_class 源的 placement rule area；与 sheetname 源并存）：
  - **token 已实测安全（2026-06-28）**：`(placement (enabled yes) (component_class "X"))` 经 `kicad-cli pcb upgrade --force`
    加载 rc=0、逐字回写——是合法 KiCad-10 文法，**不属** `source_type`/`source` 的 SEGFAULT 类（那两个是错建的内存/
    protobuf 字段、文件无此 token、写出即崩，见 §D 结论 + 回归 `test_generated_placement_has_no_source_type`）。
  - 余下实现（5 件，非快速 fact&pointer）：① 给 zig `ZonePlacement` 加 `component_class: ?str = null` 字段（镜像现有
    `sheetname`，自动 build-on-import 重编，`E_zone_placement_source_type` 枚举已有该成员）；② layout.yaml/`Room` 加
    component_class 源字段；③ `rule_area` 按源 emit `(component_class "X")`；④ **写 `.kicad_pro` 声明 class 成员归属**
    （atopile 现不写 .kicad_pro——`set_kicad_netlist_path_in_project` 是死代码，模型 `C_kicad_project_file` 在；属净新通道）；
    ⑤ 加 `(component_class)` 往返 + kicad-cli ingest 测试。

### G. 不做 / 暂缓
- ❌ **hack uuid = 绝对禁止**（不可逾越原则）：uuid 是 128bit 不透明 id，atopile **既不往里写
  任何非标内容、也不从里读任何 flag**。旧 `gen_uuid(mark="FBRK")` 写 + `is_marked` 读的 FBRK
  uuid 侧信道**已从代码彻底删除**（`fileformats.py gen_uuid` 无 mark 参数、纯 uuid4；
  `transformer.py` `gen_uuid`/`is_marked`/`_add_group` 已清）。provenance/所有权只走合法载体：
  footprint `atopile_address` property（受管判定）+ `FBRK:notouch` fp_text（用户锁）。
  *（这是原"遗留问题 3 gen_uuid 变长-mark 溢出"的终局：根因 = 把元数据塞进 uuid，根治 = 不塞，
  故整条从遗留问题移到此处作为常驻不变量。）*
  - **推论（不可 desire 字节等价）**：uuid 不透明 ⟹ 两次 build 可字节不同而语义相同 ⟹ **任何
    断言字节等价的测试都是范畴错误，必须改语义等价**（`semantic_view`：位置+net 名连通性+结构，
    剔 uuid/net 编号）。已改 `test_group_determinism.py` + `test_room_migration_e2e.py::test_C3_3`
    三处 byte 断言。**再推论**：生成态 `.kicad_pcb` 不必作字节锚入库——CI 可两次从零构建比 semantic；
    仅**输入态**布局（`examples/layout_reuse/.../sub.kicad_pcb`）+ parser 语料样本仍入库。
    残留：`semantic_view` group 成员仍按 uuid 表达，完整 oracle 应改按成员地址（未排期）。
- ❌ fork KiCad 10；❌ 旧 SWIG 绑定；❌ **v9 写出/双向方言 shim**（v10-only 决策）；
  ❌ 盲随机语法 fuzz（危险类 bug = "合法文件静默误绑"，由 corrupter + property 测试覆盖）；
  ❌ KiCad 原生 design block 库；❌ 调 GUI Repeat Layout。
- ⏸ clean-checkout 可重现（增量稳态已够，事实 6）；⏸ 向 KiCad 上游提 DRC JSON 增强补丁；
  ⏸ 改 `.ato` 语法（布局走 sidecar）。
- ⏸ **§P1+ GUI 高级布线构造保真**：teardrop / generated 蛇形等长 / via padstack 的 v10 子键
  形变补全（schema 有 KiCad 9 形模型，缺 v10 验证）；现由 S5a 响亮警告兜底、演示脚本回避。
  验收 = 从"警告集"挪进"保真集"。

## 遗留问题（未完成 / 硬化项）
1. **net 名漂移**（自动编号 `unnamed[N]`，事实 8）：v10 下名字 = 唯一键，漂移 = 几何归属漂移。
   已焊进 B 的 I4（无稳定地址 net 响亮标记、不可引用）+ 验收"稳态重建下全部 net 名字节稳定"。
2. 是否向 KiCad 上游提 DRC JSON 增强补丁（见 §G）。
3. **`_generate_net_map` 并列判据死代码**（`layout_sync.py:183`，函数定义 `:116`）：
   `mapping_counts[src][tgt] > max(values())` 自增后恒 false → 注释写"最频映射"实为"首次胜"。
   当前 pull 迭代序确定故未发病，歧义映射下是休眠隐患。修法：并列取字典序最小 tgt + 迭代按
   src_addr/pad 排序。**注**：B/C 的消费者-oracle 以现役 `_generate_net_map` 为真值；此处硬化改
   行为须同步更新 oracle 期望（两者绑定）。
