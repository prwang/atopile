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
| **D-Tier3** | **自包含：摆放（room 相对坐标）+ 板框 outline + 完整叠层** | ⬜ **关键路径**（完整叠层 = 差分阻抗硬依赖）；测试计划已定（31 strict-xfail，见 §D-Tier3 测试计划）；net-class/pour/keepout/silk 进 §F |
| **增量执行** | **route_stages `--up-to` 断点 + stage name 唯一** | ⬜ 增量可调试，配 §F 诊断闭环 |
| **Tier0** | **corridor-as-data（纯 schema 旁支，零 router 改动）** | ⬜ 低成本、可独立做 |
| E–F | 路由 fork + 诊断闭环——**章节字母 = 执行序** | ⬜（C3+D 已解锁 E；**E-Tier2 = bundle 路由实现**） |
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

---

## 未完成任务

执行序：C3+D（✅）→ **D-Tier2 契约冻结 → E（E1→E2→E-Tier2 bundle）→ F**。**D-Tier2 必须先于 E-Tier2**
（共冻 `batch_route_bundle` 契约）。D-Tier3（摆放 room 相对坐标 + 板框 outline + **完整叠层**）= 生成式 flow
**关键路径**、与路由主线并行做——其中完整叠层是 D-Tier2 差分阻抗的硬依赖，**不可后置/不可退化为仅层数**。增量执行
（`--up-to` 断点）配 §F；Tier0（corridor，零 router 改动）、D5（component_class，需 fileformats 前置）、以及 §F 的
板级 net-class/DRC 表、铜皮 pour、非 placement 禁布、丝印（缺失须 loud、不缺省）可与主线并行/择机做，不挡关键路径。

### P0.2 S7 flag-day 终验剩余（写 v10 代码已落 + 单测/e2e determinism 已绿）
- [ ] examples/fixtures 工程 `.kicad_pcb` 一次性 v9→v10 升级提交；build→build→diff 确认
  增量稳态（事实 6）。
- [ ] BOM / 制造产物 / DRC smoke。

### D5. component class placement（后置）
component_class 源的 placement rule area。**前置 = 修 fileformats schema**：走 `(component_class "X")`
token，**不是** `ZonePlacement.source_type`/`source`（错建的内存/protobuf 字段，写出即 SEGFAULT KiCad——
见「关键事实」placement 条 + 回归 `test_generated_placement_has_no_source_type`）。须给 zig `ZonePlacement`
加 `component_class` 字段（或把 type+source 融成单 token），sheetname/component_class/group 三源各自一个 token。
另需写 `.kicad_pro`（事实 10 之外的通道）。

### D-Tier2. bundle 总线传输（mixed single/diff）+ `batch_route_bundle` 契约【先于 E-Tier2】
**状态（2026-06-20）**：**桶①（D 内 model + geometry）已落地翻绿**——`layout_plan.py` 加 `BundleStage`/
`SingleLane`/`DiffLane`/`Trunk`/`TrunkVertex`/`SpacingOverride`/`Breakout`/`BundleRouteConfig`/`RipUpBudget` +
判别联合 `route_stages: list[RouteStage | BundleStage]`（`_stage_kind` 判别器，现存 single/diff 不变）+
`resolve_nets` bundle 分支；新模块 `bundle_geometry.py`（`cross_section_offsets` 几何 SSOT + `bundle_artifact`）。
契约测试 `test_bundle_contract.py` 桶① 21 项全绿、桶② 2 项仍 strict-xfail。**桶②（`batch_route_bundle` 实现 +
breakout 扇出）= E-Tier2，用户 2026-06-20 拍板留到 §E**（实际路由实现复杂）；契约已由桶② 棘轮冻死、E 照此实现。
**D 侧集成已闭环（2026-06-25）**：`build_steps.generate_layout_plan`（D4）现对每个 bundle stage 调
`bundle_artifact(stage, ir)`，把**算好的 cross-section offset + 分段 trunk + breakout 顺序 + resolved_nets**
写进 `<t>.layout_plan.json`（plain single/diff stage 仍走 `model_dump`）——几何 SSOT（`bundle_geometry`）即落盘
内容、E1 不再二次推导。e2e 由 `examples/sata_bundle`（host⇄device 的 SATA TX/RX 双 diff lane、NE→S trunk
带 neck-down 过渡段；自带本地件+复用板，全离线构建）+ `test/end_to_end/test_bundle_build.py` 钉死：S0 棘轮
`_BUNDLE_BUILD_WIRED` AST 探 `generate_layout_plan` 是否引用 `bundle_artifact`（撤线即 strict-xfail），且断言
产物 offset **== `cross_section_offsets` SSOT**（证明注入而非重算）。
**问题**：现 `route_stages` 的最小单元 = 一条 net 整条路由到完成，无法表达"两端 fanout、中间一条 bus
平行同形走线"这类布线意图（手工布线核心模式：BGA 先扇出成两端都接受的公共顺序，中段整把 bus 平行拉过去、
零调序；或两远端先到近 checkpoint 再汇合）。`pitch` 标量也错——DDR byte lane 是 **single+diff 混编**的
异构横截面（8 根 DQ 单端 + 2 对时钟 diff，线宽/阻抗/对内 gap 各异）。
**抽象**：bundle = `centerline`（trunk 轴）+ **有序 lanes**（每 lane 单端或差分、各带宽度）+ **两端 breakout**
（发散区）。一次 `batch_route_bundle` 调用把 breakout A + trunk + breakout B 全路由完（成员 pad-to-pad 连通，故
**不需要** partial-route/route-to-point）；搜索只在两端 breakout（pad→槽位、按序避交叉）+ trunk 过渡段（见下）。
**层级约束模型（不展平、不替代）**：L0 单 net=track；L1 **diff lane** = 两 net 的内在耦合约束（对内 gap/skew/极性
`fix_polarity`）= **原样复用 `route_diff` 机理**；L2 **bundle** = 有序 lanes + spacing profile + breakout。沿线任一
点**所有在场层级约束同时成立** ⟹ diff lane 在 bundle 里**绝不退化成两条独立平行单端**，始终成对带耦合。bundle 只在
外层加"顺序 + lane 间距"，无权解散 L1。
**trunk = 刚性段 / 过渡段 序列（profile 沿线可变）**：`lanes`（成员/**顺序**/每 diff lane 对内 gap·width）是 bundle
全局不变量；可变的只是 **spacing**，按 centerline **顶点分段**——相邻顶点 spacing 相等=**刚性段**（offset 给死、无搜索），
不等=**过渡段**（顺序不变、间距 morph，转弯 neck-down 即此）。
**过渡段 = trunk 内唯一 auto-route 处，且两层约束同时在**：两端 cross-section（profile-A/B 的 offset）都给死，只搜中间
morph 路径（范围被两端钉死、很小）；其中**外层 bundle 顺序/不交叉 + 内层每 diff lane 耦合不散**必须同时生效。
= router 现有 `diff_pair_centerline_setback`（pad 附近受约束聚/散）从 pad 边推广到中段、并叠 bundle 顺序约束——非新发明。
**显式几何**路线（2026-06-19 用户拍板）：刚性段 offset 由 D 纯函数累加得出、确定可单测；过渡段两端给死、只 E 搜 morph。
**D/E co-design（consumer-oracle）**：bundle 的 config 字段只能钉在下游 `batch_route_bundle` 真接受的 kwargs
上，而该 entry **今天 router 不存在** ⟹ D-Tier2 的核心交付之一 = **先定义并 strict-xfail 钉死该 entry 契约**
（D/E 边界），D 与 E 都照同一份冻结契约实现，杜绝"先做 E 再倒逼 schema 重构"。tests-first S0 棘轮。
**与现有 D2 实现的兼容性（改动面——bundle 是模型重构，非纯增量；实现前必读）**：
- `route_stages: list[RouteStage]` → **判别联合** `list[RouteStage | BundleStage]`：现 `RouteStage`
  （`layout_plan.py:180`）必填 `mode` 且 `extra="forbid"`，`type: bundle` 塞不进现模型——须加 `kind`/`type`
  判别字段或拆出 `BundleStage` 类；`RouteStage`(single/diff) 语义不变（不破 D1–D4）。
- `LayoutPlan.resolve_nets`（`layout_plan.py:219`）现假设 `stage.nets`，bundle 无 `.nets` 会 AttributeError——
  须按 stage 类型分支：bundle 把 `lanes` **按序**摊平成成员 net（diff lane P/N 都出），仍是"地址→net 名"纯
  委托 bridge②、不引入变换（守 `resolve_nets` 现 docstring 不变量）。
- bundle 的 router override **不是** `GridRouteOverride`（那钉在 batch_route/diff 两入口）——bundle 钉
  `batch_route_bundle`，须新建 bundle config 模型（per-bundle + per-lane 两级），T-B1 独立 drift guard。
- `LayoutPlan` 加 `anchors` 字段（D-t2.1，可后置）。
- [x] `build_steps.generate_layout_plan`（D4）写 `<t>.layout_plan.json` 时对 bundle stage 调 `bundle_artifact`
  注入**算好的 offset**（2026-06-25 落地，e2e `test_bundle_build.py`；plain stage 仍 `model_dump`）。
- 与 D3 rule area / placement **正交**：bundle 是布线侧，不碰 placement（仍只 enabled+sheetname，事实 10）。
- [x] **D-t2.2** `bundle` stage 类型（与 `mode: single|diff` 并列的第三类 `type: bundle`）：
  - `lanes`（**有序、bundle 全局不变量**：每条 `{net: addr}` 或 `{diff: [p,n], gap, width?, impedance?}`）；
  - `trunk.centerline` = **带 spacing 的顶点序列**（每顶点 `{at: [x,y], spacing}`，≥2 点）——相邻顶点 spacing
    相等=刚性段、不等=过渡段；可选 `spacing_overrides`（`{after: <lane>, gap}` 调单条 lane 间距）；
  - `config`（bundle 默认，lane 可覆盖）、`rip_up`（**per-stage 的 stage 内 rip-up 预算旋钮** =
    `max_rip_up_count`/`ripped_route_avoidance_cost`/`_radius`；跨 stage 锁是白送的硬障碍、不在此表达，见「关键事实」16）、
    `breakouts`（**正好 2 个**，`{at: <room 地址>}` 缺省 order 由 ato 引脚序派生 / `{at, order: [按 escape 序排的成员]}`
    显式覆盖）。
  loud 校验（S5a）：lanes≥1、diff lane 正好 2 net、gap/width/spacing>0、centerline≥2 顶点、`spacing_overrides.after`
  指真实 lane、breakout.at 是真实 room、显式 order 必是 bundle 成员的一个排列、两端 order 自洽。docstring 记：
  fanout 语义=把两端原生序搬成 trunk 公共序（trunk 零交叉）；diff lane 全程不解散（L1 内在约束）。
- [x] **D-t2.2b** 纯函数 `(lanes, segment_spacing) → [(net, signed_offset, width, kind, diff_partner?, polarity?)]`
  **逐刚性段**算偏移（默认以 centerline 居中累加），并输出**过渡段的两端 offset 边界**（A/B profile）供 E morph。
  bundle 几何 SSOT。测：混编横截面**手算 offset 当 oracle**的最小 fixture（按构造定答案）+ 变异自检（动一条 lane
  宽度/gap/段 spacing，下游对应 offset 必变，否则红）。
- [x] **D-t2.3（契约已冻；实现 = E-Tier2）** 冻结 `batch_route_bundle` 契约（**D/E 边界，strict-xfail oracle，先于 E**）：入参 = **分段 trunk**
  （centerline 顶点序列 + 每段 spacing；刚性段成员 offset 给死、过渡段两端 offset 给死）+ 有序成员表（net/offset/
  width/kind，diff 项带 P/N 配对 + 极性）+ 两端 breakout（part + order）+ 共享几何 kwargs（track_width/clearance/
  via_*）；`return_results` 结构按 `batch_route` 同构、**逐成员**报 routed/blocked。钉 = 像 D2 `GridRouteOverride`
  那样 AST 取真 entry 签名做 union drift guard（E-Tier2 实现后翻绿）。
- [x] **D-t2.4** `resolve_nets`/anchor 解析扩展：bundle 成员**按序**经 bridge② 解析；diff lane 的 P/N 都要解析。
- [ ] **D-t2.1（可分离旁支）** `anchors`：命名几何点/线，供"两远端单线打 checkpoint 再汇合"这类**非 bundle**
  用例（route-to-point）。与 bundle 正交，可后置；bundle 自带 trunk 端点不依赖它。

**测试计划与判据（D-Tier2；tests-first S0，先全红、实现靠移除/失活 xfail 翻绿）**——三桶分明：标注 D 内可自测项
+ 判据，与"E 读取实现的接口模板"（后者须 docstring 写全协议 + 接口 delta，= 本仓"协议写在契约测试 docstring"纪律）。
共用 mixed DDR fixture：8 单端 DQ + 2 对时钟 diff + 一处转角 neck-down 分段（刚性—过渡—刚性）。
**棘轮已落地（2026-06-19）+ 桶① 已实现翻绿（2026-06-20）**：`test/exporters/pcb/layout/test_bundle_contract.py`
（T-A1–T-A5 + T-B1/T-B2；冻结的接口名 + 横截面约定 + result schema 全写在模块 docstring）。桶①gated on
`_DT2_LANDED`（layout_plan 的 bundle 符号 + 新 `bundle_geometry` 模块）= **现 21 项全绿**；桶②additionally gated
on `_E_TIER2_LANDED`（AST 探 router `route_bundle.batch_route_bundle`，缺则红、router repo 全缺则响亮 skip）=
**仍 2 项 strict-xfail，待 E-Tier2**。负向用例与 base-bundle 正向控制**成对**（落地前控制即抛 → 干净 XFAIL，杜绝
D2 把 `type: bundle` 当未知 stage 拒掉造成的 wrong-reason XPASS）。桶① `- [x]` = 已翻绿，桶② `- [ ]` 待 E-Tier2。
- **桶① D 内可自测（无 router / 无 KiCad；纯函数 + schema + 真 build IR fixture）**
  - [x] **T-A1 schema 正向**：fixture 解析成 typed model。判据：lanes 顺序/类型、分段 spacing、breakout order
    逐字段断言 == 期望（非"跑通即绿"）。
  - [x] **T-A2 schema 负向（loud-or-nothing，逐条独立 case 证明会拒）**：diff lane≠2 net / gap·width·spacing≤0 /
    centerline<2 顶点 / `spacing_overrides.after` 悬空 / 显式 order 非成员排列 / 两端 order 不自洽 / 未知键
    (`extra=forbid`)。判据：每畸形输入抛**指定异常类型 + 消息含定位**，且对应合法输入不抛（正负成对）。
  - [x] **T-A3 `profile→offset` 纯函数（几何 SSOT，最关键）**：① 按构造 oracle——**手算** mixed 横截面 offset 表
    == 输出（容差）；② **变异自检（必配，防失明）**——动一条 lane width/gap/段 spacing，对应下游 offset 必变
    （断言 before≠after），不变 = 测试 bug；③ 居中不变量——profile 关于 centerline 对称（或按 reference 规则）；
    ④ 过渡段输出两端 offset 边界 A/B。判据：== 手算 且 变异传播 且 居中成立。
  - [x] **T-A4 `resolve_nets`（consumer-oracle，真 build IR 的 `signal_nets`，无 router）**：bundle 成员**按序**
    解析 == bridge②；diff lane 的 P/N 都解析；缺失地址响亮（`LayoutPlanError`）。判据：有序列表逐项 == ir 期望
    + 负例抛错。
  - [x] **T-A5 artifact schema（D 产 `<t>.layout_plan.json`）= E1 的【文件】接口模板**：产物含分段 trunk + 算好的
    offset / 有序成员表 / breakout order / resolved nets；JSON schema 校验 + 关键字段非空。**`.schema.json` +
    docstring = E1 读取的文件接口 SSOT，须写全（这是 E 开始时照着读的模板之一）。**
- **桶② D/E 契约（strict-xfail，E 实现前恒红）= E 读取实现的【Python】接口模板（须 docstring 写全签名 + result schema）**
  - [ ] **T-B1 `batch_route_bundle` 签名 drift guard**：AST 取真 entry 签名，断言 bundle config 字段 ⊆ entry
    kwargs union（同 D2 `GridRouteOverride` 钉法）。E 未建 entry → red → strict-xfail；E 建后翻绿。模块 docstring
    写全签名 delta（分段 trunk / 成员表 / breakout 怎么传）。
  - [ ] **T-B2 调用 + 结果 shape**：构造契约输入（分段 trunk / 成员表 / breakout）→ 断言 `return_results` 结构
    （逐成员 routed/blocked，按 `batch_route` 同构）。strict-xfail 至 E-Tier2。docstring 写全 result schema。
- **桶③ 端到端（E-Tier2 落地后，属 §E DoD，不在 D）**：真 router 跑 mixed DDR bundle，重读输出板 `semantic_view`
  验：刚性段 offset 落位 == plan、**过渡段 diff 全程不散（P/N 间距 == gap±tol）**、breakout 按 order 零 trunk 交叉。

### D-Tier3. 自包含摆放 + 自包含性审计（layout.yaml 为生成式 flow 唯一权威）
**原则（用户拍板 2026-06-19）**：在生成式 / agent 文本优先工作流里，`.ato`（电路）+ `layout.yaml`（布局）必须
**自包含、是唯一权威**；**任何一项事实都不得以「只能 GUI 编辑 .kicad_pcb」或「只能 reuse 既有 .kicad_pcb」为唯一
输入来源**——「为避免 GUI 必须先用 GUI」的循环依赖即设计缺陷。.kicad_pcb 是**派生产物**，reuse 仅作可选优化、
不得是任何事实的唯一通道。

**摆放语义（本节修，关键路径）**：
- **坐标系 = room 相对**（用户拍板）：元件坐标相对其 room 的 origin/anchor，移动 room 整块跟随、块可复用；
  可留一个板绝对坐标逃逸口。
- `Room` 几何扩展：加 `polygon: [[x,y],...]`（loud：≥3 点、非自交）作为 `origin/size` 之外的第三种几何；KiCad
  placement rule area 本就是 `Polygon` zone（`rule_area.py:128` 现只喂 4 个 bbox 角点），改动小。**并修一个现存
  S5a 隐患**：`Room.rotation/layers/anchor`（`layout_plan.py:164-166`）现被解析却被 D3 完全忽略
  （`_make_placement_rule_area` 只吃 bbox + 全信号层，`rule_area.py:83`）——要么接线生效（rotation→旋转矩形多边形、
  layers/side→zone 层），要么删除，不留"声明却静默忽略"。
- 新增 `placements` 段，按 **ato 地址** key（与全文一致，不用 designator）：`{component: <addr>, at: [x,y],
  rotation: <deg>, side: F|B}`，room 相对。这是把 footprint `(at x y rot)` + layer 写进派生 .kicad_pcb 的**文本权威**，
  取代受管 footprint 现在 build 时的自动网格摊开（`transformer.py:176` 默认 `(0,0,0)` → `:2013-2080` 按 parent
  聚类、10mm 步进；非任何文本源可控）。须定 `placements` 与 reuse 的**优先级**（文本给值即覆盖 reuse，缺省回退 reuse）。

**板级自包含（本节修，关键路径——不可后置）**：纯文本端到端跑一块板，下游（E router / F DRC / 制造）消费的板级
通用事实必须有文本 schema。layout.yaml 加**板级 `board` 段**（与 `rooms`/`placements`/`route_stages` 平级）：
- [ ] `board.outline`：`{origin, size}` 或 `polygon: [[x,y],...]`（loud：≥3 点、非自交）。**无 outline 现在是静默灾难**
  ——router 在无界空间布线、铜溢出板外（`obstacle_map.py:393-395` 无 board_bounds 直接 `return`），F 的边界间距无可查、
  板不可制造。必给 schema。
- [ ] `board.stackup`：**完整叠层，不止层数**——有序 copper 层名 + 每介电层厚度/材料/Er。**为何完整、不可退化为"只给
  层数"**：差分对受控阻抗依赖真实叠层（`route.py:256-258`：`impedance` 模式无 stackup 即 warn 退回固定线宽 =
  **阻抗失控**）；**阻抗失败 = 板子失败 ⟹ D-Tier2 里差分对的分层耦合/阻抗规则全部白做**。故完整叠层是 **D-Tier2 的
  硬依赖**，与铜层数同级 must-do、**不得倒退为仅层数**。
- [ ] **单一层数权威**：`board.stackup` 的 copper 层集是唯一真相，**同时**喂 (a) atopile 生成板的 layer 表 与
  (b) `route_stages.layers` 的默认。**landmine（必防，记给 E1）**：atopile 现默认板 = **2 层**（仅 F.Cu/B.Cu signal，
  `config.py`），router 无 `layers` 入参时默认 **4 层** `DEFAULT_4_LAYER_STACK`（F.Cu/In1.Cu/In2.Cu/B.Cu，
  `route.py:229-232`，且"层不可自动探测"）——两者不一致会让 router 往不存在的 In1/In2.Cu 布线。E1 必须由
  `board.stackup` 显式传 `layers`，严禁吃 router 4 层默认。
- **D sign-off 判据（loud-or-nothing）**：纯文本管线消费的每一项板级事实，**要么有文本 schema，要么缺失时响亮**
  （warn/拒），**禁静默默认**。outline + 完整 stackup 本节给 schema；其余（审计表"进 §F"行）缺失时必 loud。

**自包含性审计 — REUSE-ONLY / 无文本通道事实清单（= 设计缺陷，逐条立项）**【源码实测 2026-06-19】：
| 事实 | 现状（唯一来源） | 证据 | 归属 |
| --- | --- | --- | --- |
| 元件摆放坐标 x/y | 新件 build 时自动摊开；已有件从板读回 | `transformer.py:176/1499/2013-2080`；`layout_ir.py:149` | **本 D-Tier3 修**（`placements`） |
| 元件旋转（per-instance） | 同上，无 per-instance 文本通道 | `layout_ir.py:149`（读 `fp.at.r`） | **本 D-Tier3 修** |
| 元件正反面 side | 库 footprint 默认层 / 复用件从板读 | `transformer.py:1872`（`lib_fp.layer`） | **本 D-Tier3 修**（`placements.side`） |
| 板框 Edge.Cuts 几何 | 只能 GUI 画 / build-server agent 工具 / reuse；无文本源字段 | `tool_layout.py:112` 读、`tool_definitions.py:321` 仅 server-agent API；`config.py:458` 的 "Edge.Cuts" 只是层定义非几何 | **本 D-Tier3 修**（`board.outline`） |
| 叠层 stackup（**完整**：层名+厚度/材料/Er） | 只能 KiCad PCB setup 配；atopile 只读不写 | `src/atopile/server/domains/cost_estimation.py:278-283` 仅读；阻抗依赖 `route.py:256-258` | **本 D-Tier3 修**（`board.stackup`，完整，差分阻抗硬依赖） |
| 布线 design rule（clearance/线宽/via/impedance） | 已是文本可授权 | `route_stages.config` = `GridRouteOverride`（D2） | **已覆盖** |
| 铜走线 tracks / vias | reuse 或手工 | `layout_sync.py:252-286` | **§E route_stages 从文本生成**（fork 核心目标，非遗留缺陷） |
| 板级 net-class / DRC 规则表（KiCad DRC 对齐） | 只能 KiCad Design Rules 配；atopile 源无 authoring | `grep net_class/design_rule src/atopile` = 空 | **进 §F**（DRC 对齐；缺失须 loud，不缺省） |
| 铜皮 pour / 灌铜 zone 几何 | reuse-only（§E 只布线不灌铜） | `room_ops.py:296-307` | **进 §F**（缺失须 loud，不缺省） |
| 禁布/rule area（非 placement 类） | placement rule area 由 §D 生成；其它禁布 zone reuse-only | §D rule_area.py（placement 类） | placement 类已有；其它 **进 §F**（不缺省） |
| 丝印 / 文字 | reuse-only | `layout_sync.py:288-304` | **进 §F**（缺失须 loud，不缺省） |

排期：D-Tier3 = 生成式 flow 必备、**关键路径**，含三件 must-do——摆放（room 相对坐标）+ 板框 outline + **完整叠层**
（差分阻抗硬依赖，不可退化为仅层数）；铜走线由 §E 覆盖；板级 net-class/DRC 表、铜皮 pour、非 placement 禁布、丝印
全部**进 §F**，且 **loud-or-nothing：缺失必响亮，禁静默默认**。

**测试计划与判据（D-Tier3；tests-first S0，先全红，实现靠移除/失活 xfail 翻绿）——共 31 个 strict-xfail
（桶① 29 + 桶② 2）；桶③ e2e 属后续 DoD、不计入。** 棘轮文件（待建）：`test/exporters/pcb/layout/
test_placement_contract.py`（摆放 + room 几何）+ `test/exporters/pcb/layout/test_board_section_contract.py`
（板框 + 叠层）。门控 `_DT3_LANDED` = 全部 D-Tier3 符号导入（`Placement` 模型 / `board` 段 `BoardOutline`·
`Stackup`·`StackupLayer` / `Room.polygon` 字段 / room 相对解析纯函数 `resolve_placement` / 叠层→层表纯函数
`stackup_layers`）；半落地（改名只改一半）保持红。负向用例一律与正向控制**成对**（落地前控制即抛 → 干净 XFAIL，
杜绝 wrong-reason XPASS）。三件 must-do（摆放 / outline / 完整叠层）各自 schema 正负 + 纯函数 oracle + 变异自检。

- **桶①（D 内可自测：schema + 纯函数 + rule_area 几何 + 真 build IR fixture；无 router / 无 KiCad）= 29**

  *摆放（room 相对坐标）— 10*
  - [ ] **TP1** `placements` 正向（逐字段）：`{component: <addr>, at:[x,y], rotation, side:F|B}` 解析成 typed
    model，地址 / at / rotation / side 逐字段断言（非"跑通即绿"）。— 1
  - [ ] **TP2** `placements` 负向（loud-or-nothing，逐条 + 正向控制成对）：未知键(`extra=forbid`) / `side`∉{F,B} /
    缺 `component` / 缺 `at` / `at` 非二元。每例抛指定异常 + 消息含定位，合法控制不抛。— 5
  - [ ] **TP3** room 相对→板绝对 纯函数 oracle：`resolve_placement` 把 room 相对 at 经 room origin/anchor 合成板
    绝对坐标 == **手算**（按构造定答案）。— 1
  - [ ] **TP4** 变异自检（防失明，必配）：动 room origin/anchor，成员板绝对坐标必变（before≠after）；不变 = 测试 bug。— 1
  - [ ] **TP5** 板绝对逃逸口：标注绝对坐标的 placement 原样落该坐标、不经 room 合成。— 1
  - [ ] **TP6** placements vs reuse 优先级（消费者-oracle，纯函数判定）：文本给值即覆盖 reuse、缺省回退 reuse
    （钉死 `transformer.py:176/2013-2080` 自动网格摊开被文本权威取代的优先级）。— 1

  *room 几何 + 静默忽略隐患修复 — 6*
  - [ ] **TR1** `Room.polygon` 正向 + rule area：polygon（≥3 点）解析；`generate_rule_areas` 产的 zone == 该 polygon
    （非 bbox 四角；扩 `rule_area.py:128` 的 `Polygon`、`:83` `_make_placement_rule_area`）。— 1
  - [ ] **TR2** room 几何 负向（loud）：polygon<3 点 / polygon 自交 / `origin+size` 与 `polygon` 多重几何并存（互斥）。— 3
  - [ ] **TR3** `Room.rotation` 接线生效（不再静默忽略）：rotation≠0 的 room 产**旋转后的** polygon rule area——钉死
    `layout_plan.py:164-166`（rotation/layers/anchor 现被解析却被 `rule_area.py:83` 完全忽略的 S5a 隐患）。— 1
  - [ ] **TR4** `Room.layers`/`side` 接线生效：显式 layers 产对应 zone 层（非 `_copper_layers` 全信号层默认，
    `rule_area.py:47-48`）。要么接线生效、要么删字段，不留"声明却静默忽略"。— 1

  *板框 outline — 4*
  - [ ] **TB1** `board.outline` 正向（逐字段）：`{origin,size}` 与 `polygon: [[x,y],...]` 两形都解析。— 1
  - [ ] **TB2** `board.outline` 负向（loud）：polygon<3 点 / 自交。— 2
  - [ ] **TB3** outline 缺失→响亮（sign-off）：管线消费无 outline 的 plan 必 loud——钉 `obstacle_map.py:393-395`
    无 board_bounds 即无界布线、铜溢板外、板不可制造的静默灾难。— 1

  *完整叠层 stackup（含"两层/四层默认来路不明"bug 取证）— 9*
  - [ ] **TS1** `board.stackup` 正向（逐字段）：有序 copper 层名 + 每介电层 thickness/material/Er，逐字段断言
    （**完整**叠层，非仅层数）。— 1
  - [ ] **TS2** `board.stackup` 负向（loud）：只给层数无介电（不完整=退化为仅层数，**显式拒**）/ thickness≤0 /
    层名重复 / copper<2。— 4
  - [ ] **TS3** 叠层→层表 单一权威 纯函数 oracle：`stackup_layers(stackup)` == 有序 copper 层集（== 手算）。这是
    层数的**唯一真相**（桶②两条权威棘轮都派生自它）。— 1
  - [ ] **TS4** stackup 缺失→响亮（sign-off）：管线消费无 stackup 的 plan 必 loud。— 1
  - [ ] **TS5** 阻抗硬依赖：route_stage 处 impedance 模式但无 `board.stackup` → 必 loud——钉 `route.py:256-258`
    （impedance 无 stackup 静默退回固定线宽 = 阻抗失控；= D-Tier2 差分阻抗的分层耦合/阻抗规则白做）。— 1
  - [ ] **TS-LM** 层数默认 divergence 取证（AST/值，D 内，**bug 现状锁定**）：钉死 atopile 默认 copper =
    `[F.Cu, B.Cu]`（2 层，`config.py:374,380`）≠ router 默认 `DEFAULT_4_LAYER_STACK = ['F.Cu','In1.Cu','In2.Cu',
    'B.Cu']`（4 层，`routing_constants.py:8`；`route.py:231` 无 `layers` 入参即取之），且二者**无共享来源**。锁死两处
    "来路不明"魔数位置，为桶②两条权威棘轮提供取证（魔数被静默改即红）。— 1

- **桶②（D/build·D/E 边界，consumer-oracle，strict-xfail 至下游落地）= 2 —— 即用户要求"之前的两层/四层 bug 现在
  测试必须红"的两条棘轮**
  - [ ] **TS-AUTH-A** 板生成层表权威（gated on `_DT3_BUILD_LANDED`）：断言 atopile **实际生成板**的 copper 层表
    == `stackup_layers(board.stackup)`（断**接线后的板**、非"schema 解析通过"——`board.stackup` 仅解析不足以翻绿），
    **杀掉 `config.py:374,380` 硬编码 2 层默认**。**现红**（无 stackup 权威）；权威接线落地后翻绿。— 1
  - [ ] **TS-AUTH-B** router 层表权威（gated on `_E1_LANDED`）：E1 把 `stackup_layers(board.stackup)` 当 `layers`
    传给 router，**绝不吃** `route.py:231` 的 4 层默认。**现红**（E1 未建）；E1 落地后翻绿。— 1
  > **bug 覆盖闭环**：TS-LM（现绿、取证两处魔数分叉）+ TS-AUTH-A/B（现红、钉死修复后单一权威）合起即"两层/四层默认
  > 来路不明"bug 的完整覆盖——修复门控 = "层数只有 `board.stackup` **一个**权威源、atopile 板表与 router `layers`
  > 都由 `stackup_layers()` 派生 ⟹ 物理上不可能再分叉"。权威落地前 TS-AUTH-A/B **必为红**（用户要求）。

- **桶③（端到端，属后续 DoD，不在 D-Tier3 单元）**：纯文本（无 reuse）端到端构建一块板——`board.outline` + 完整
  `board.stackup` + `placements` 全文本给定 → 生成 .kicad_pcb → 重读 `semantic_view` 验层表/板框/摆放落位 == plan
  （含层表 == stackup 权威，板上无 In1/In2.Cu 幽灵层）。

### 增量执行 / 断点调试（route_stages 按号停-取-回退）
**动机（用户）**：`layout.yaml` 是顺序文件，必须**增量可调试**——可只执行到第 N 号 stage、取该部分结果，由 agent/人
检查→回退→改→重跑，确保整体布线优先级完整落实。这与「关键事实」16 的跨 stage 硬障碍天然契合：每 stage 产物 = 可
**续跑的 checkpoint**。
- [ ] build 加 `--up-to <stage-name|index>`：只跑 `route_stages[0..k]`、停下，写出**部分板 + `route_report.json`**
  （F 诊断闭环的输入）。
- [ ] **强制 stage `name` 唯一**（`layout_plan.py` 加 loud 校验）——按名寻址断点的前提；同时支持 1-based index 寻址。
- [ ] checkpoint 续跑语义写进文档：改**更早** stage 须从该步重跑（铜累积）；改**更晚** stage 可从断点续。每 stage
  的结构化诊断喂 SKILL/agent，构成 `layout_plan.py` docstring「FORM」节描述的 build→诊断→改 plan→重建 外层 loop。

### Tier0. corridor-as-data（纯 schema 旁支，零 router 改动，低成本先行）
现 `guide_corridor_enabled` 只开关、引导线几何藏在板上 User.1（§C 边界事实 4）。把路径搬进 plan：stage 加
`corridor: [[x,y],...]`，build 画到 User.1 再置 `guide_corridor_enabled`。软牵引（可被阻挡绕开、不停在
checkpoint），但"把 bus 沿空走廊牵引、缩小搜索空间"这个值零 router 改动即得。仅 `batch_route` 单端入口收
`guide_corridor_*`（差分入口无，见 §D2 mode 互斥）。
- [ ] stage `corridor` 字段（block-list 坐标）+ build 画 User.1 polyline + 置 `guide_corridor_enabled`。
- [ ] loud：corridor ≥2 点；仅 single mode 可用（diff stage 给 corridor 即响亮拒）。

### E.【需 §C + §D】KiCadRoutingTools fork
E1 需 §D 的 plan + 板上 rule area，且用 §C1 的 forced via 当布线阶段能力。E3 全程并行先搭。
**E1 dispatch 从一开始按「single / diff / bundle」三类 stage 设计**（D-Tier2 契约冻结后填 bundle 实现，勿事后改）。
- [ ] **E1** `layout_plan_runner.py`：route_stages → **按 stage 类型**展开成 `batch_route`/
  `batch_route_diff_pairs`/`batch_route_bundle` 的 **kwargs**（字段名 = router 入口入参，**非** GridRouteConfig；
  翻译在 router 内部，见 D2 纠偏）→ 多次路由调用，阶段间 pcb_data 累积。产 `route_report.json`。
  - **按 stage 类型分派入口（§C 边界事实 1/设计决策，不合并算法）**：single → `route.py:batch_route`、
    diff → `route_diff.py:batch_route_diff_pairs`、**bundle → `batch_route_bundle`（D-t2.3 契约，E-Tier2 实现）**；
    聚合按共有键 `failed`/`successful`/`total_vias` 归一、各类型键各自解析。类型来自 `route_stages`（D2/D-t2.2），
    不自动猜测。
  - **容忍缺失 `JSON_SUMMARY`**（边界事实 2）：已被 §C 完全连通的 net 不再路由，应在路由前从
    batch 输入剔除；聚合不得假设每条输入 net 都有 summary。
  - **不为 §C 预置几何加 lock**（边界事实 2）：它们是已连通铜、router 自动不动。跨 stage 的前序铜同理是
    不可撕硬障碍（锁定模型见「关键事实」16），E1 无需为任何已布几何加锁。
  - **GridRouteOverride → kwargs 原样展开（按 mode 校验）**：`RouteStage.config`（D2 的 override，键 ⊆
    两入口入参 union，**非 GridRouteConfig**，见 D2 纠偏）原样展开成 `batch_route`（单端）/
    `batch_route_diff_pairs`（差分）的同名 kwargs——E1 不维护映射表（**翻译在 router 内部**，如
    `impedance`→`impedance_target`，E1 不碰）。**键合法性已由 D2 parse 期 mode-aware 校验保证**
    （D2 自测 ④：错 mode 键 parse 即响亮），故 E1 按 stage `mode` 选入口后可信地展开 `**override`；
    E1 不重复校验。新增 router 字段只需 D2 override 放行。
  - **按 stage 启用 §C/§D 的 User 层约束**（边界事实 4）：stage 可置 `guide_corridor_enabled`
    （读 §C 的 User.1 引导，**注：仅 `batch_route` 单端入口收，差分入口无此 kwarg**）/ `keepout_enabled`
    （读 §D 的 User.2 room 边界，两入口都收），默认关、由 route_stages 显式开——这是把 §C/§D 几何变成
    布线约束的唯一通道。
- [ ] **E2** 阶段锁定 = **依赖 router 既有的跨 stage 硬障碍，非新建锁**（锁定模型见「关键事实」16）：
  跨 stage 的前序铜对后续 stage 天然不可撕，故 bundle/有序流水线的"前一条线占了空间、后面用不了"**已是硬保证**，
  E2 **不需要**给 `Segment`/`Via` 加 `_metadata`、不需要给 `rip_up_net` 加跨 stage 守卫（那是在解一个不存在的问题）。
  E2 的真实交付收窄为**暴露 stage 内 rip-up 旋钮**：把 `max_rip_up_count`/`ripped_route_avoidance_cost`/`_radius`
  （`routing_config.py:60/83-84`）做成 per-stage config，让某 stage 内部也可"几乎不重排"。
  - **D-t2.2 的 `lock` / 旧设想的 `lock_after` 一律重定义为"per-stage 的 stage 内 rip-up 预算旋钮"**（值 = 上面三
    kwarg），**不**表示跨 stage 锁（跨 stage 锁是白送的）。
  - **不为 §C 预置几何加 lock**（边界事实 2）：已连通铜 router 自动不动。
- [ ] **E-Tier2 `batch_route_bundle`**（实现 D-t2.3 冻结契约，混编 + 分段平行传输）：
  - **刚性段**：单端按给定 offset 平行铺、diff 成对按 offset 铺并走差分机理（耦合/`fix_polarity`）——offset 由 D
    给死，**不搜索**；diff lane 不可退化成两条独立单端（丢耦合/极性）。
  - **过渡段**：两端 cross-section（offset）给死，中间 morph 路径**有界 auto-route**，**两层约束同时在**：外层 bundle
    顺序/不交叉 + 内层每 diff lane 耦合不散。= 把 `diff_pair_centerline_setback`（pad 附近受约束聚/散）推广到中段、
    叠 bundle 顺序约束。
  逐成员结构化结果（routed/blocked）。
- [ ] **E-Tier2 breakout 扇出**：两端各把成员 pad→trunk 槽位按 order（缺省派生 / 显式覆盖）路由、局部化交叉、
  零 trunk 交叉；复用/扩展 `bga_fanout.py`/`qfn_fanout.py`（现为独立脚本，非 router 参数——需收进 entry）。
- [ ] **E3** 独立回归台（全程并行先搭 = §E 的 S0 底座）：无需装 KiCad，预编译 Rust 二进制 +
  内置测试板 + numpy/scipy/shapely；失败注入 + 报告 schema 校验。
  - 最小切片已提前到 §C【✅】：`test/exporters/pcb/layout/test_router_smoke_batch_route.py`
    钉死差分对 C→E 接口（`return_results=True` 四元组 / `results_data` 几何字段 / `JSON_SUMMARY`
    schema）。整台 E3（失败注入 + 多板矩阵）仍待此处。
  - **待补：单端 `route.py:batch_route` 的 JSON_SUMMARY schema 校验**（边界事实 1）——薄片只验
    差分对键集，单端走 `routed_single`/`failed_single`，须扩第二条 schema 校验防静默失配。

### F.【最后做，需 §E route_report + B 的 ir 反查】诊断闭环
- [ ] **F-route** `ato route --plan layout.yaml --stage <name>`：进程内调 §E1
  （`return_results=True`），入口按 net 类型分派（§C 边界事实 1，勿写裸 `batch_route` 当唯一入口）。
- [ ] **F-diag** `ato diagnose` → diagnostics.json：聚合 route_report.json + `kicad-cli pcb drc
  --format json`（uuid 经 B 反查，事实 11）+ rule-area 命中测试；字段 stage/room/ato_path/
  constraint(JSON-pointer)/reason/blocking_nets/failed_endpoints/suggestions。schema 参考 kicad-happy。
  - **via/几何总数勿信 `JSON_SUMMARY.total_vias`**（边界事实 3）：只计 router 本次新增，板上真实
    总数须**重读输出 .kicad_pcb** 数。
- [ ] **F-drc-rules** 板级 net-class / DRC 规则表文本化（从 D-Tier3 自包含审计移入）：让 KiCad DRC 有可对齐的
  规则源（clearance/线宽类/via 类），而非吃 KiCad 工程默认。**loud-or-nothing：规则缺失时响亮（warn/拒），禁静默
  默认**——否则 DRC"绿"是假绿（按内置默认而非设计意图判）。布线侧 design rule 已在 `route_stages.config`，此处是
  **板级 DRC 对齐**（F 的 DRC 闭环消费），与布线侧不重复。
- [ ] **F-fill** 铜皮 pour / 灌铜 zone 文本化（从审计移入；§E 只布线不灌铜，pour 现 reuse-only
  `room_ops.py:296-307`）：地/电源覆铜的文本权威 + 重灌。缺失须 loud（不静默产无铜皮板）。
- [ ] **F-keepout** 非 placement 类禁布 / rule area 文本化（从审计移入）：placement 类已由 §D 生成；其它禁布
  zone 现 reuse-only。缺失须 loud。
- [ ] **F-silk** 丝印 / 文字文本化（从审计移入；现 reuse-only `layout_sync.py:288-304`）：低优先，但仍 **不缺省、
  缺失须 loud**（不静默产无丝印板当成已完成）。

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
