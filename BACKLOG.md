# BACKLOG — 文本化布局工作流（deterministic text-first layout layer）

## 背景

在 atopile + KiCad 10 + KiCadRoutingTools 之上构建确定性、以文本为唯一事实来源的
PCB 布局布线工作流：`.ato` = 电路事实源，`layout.yaml` = 布局意图事实源，KiCad 工程
为派生产物；工具输出结构化 JSON 诊断，供 **PCB Layout SKILL** 迭代。调研/验证证据见
`/kicad_wksp/KicadDecisions.md`（含文件:行号引用）。

**本文档纪律（不允许熵增）**：只保留 ① 已定决策 ② 改代码前必读的当前有效事实 ③ 未完成
任务。**已完成功能按 atopile 约定文档写在代码里**（模块 docstring / 同名测试 / schema =
SSOT），BACKLOG 只留"结论 + 代码指针"，绝不复述实现细节或调试 narrative。

## 已定决策

- 不 fork KiCad 10（所需对象已实测可外部生成并完整往返）。
- fork 基线 = 本仓库 HEAD；同时 fork KiCadRoutingTools（§E）。
- Python 3.14 开发环境 = **`uv sync`**（`ziglang==0.15.1` 由 pip 构建依赖提供）。克隆须
  `git fetch --tags` 否则 setuptools-scm 产非 SemVer 版本号、`ato` CLI 启动即崩。
- room 来源固定 = KiCad named group（sheet 路线不存在，component class 后置）。
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
| D–F | 功能开发——**章节字母 = 执行序** | ⬜（C3 已解锁） |
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
   C3 ─► D layout.yaml + rule area（room via (placement (sheetname)))
                       │
   C1 + D(plan+rule area) ─► E 路由 fork（E1 plan_runner→E2 锁定）─► F 诊断闭环
E3 回归台（全程并行先搭 = §E 的 S0 底座；最小切片【✅ 已提前到 §C】当 router-oracle）
```

**C3 为何在 D 前（已完成的背景）**：room=KiCad group 有不可修补的所有权缺陷——group 是通用选择原语
（`pcb_group.h:44`），名不唯一、复制即碰撞、无 provenance 槽（逼出 uuid 塞名 hack）。KiCad 多通道事实源
是每 footprint 的 sheet 标识，group 只是派生投影。C3 改回本意：room = footprint `sheetname`。**详见 §C3。**

**双向钉死纪律**（写消费者前先用消费者的真接口把上游验收钉死）：B→C 用
`_generate_net_map` consumer-oracle（已钉）；C→E 用真 router consumer-oracle（E3 薄片已提前）。

---

## 关键事实与约束（改代码前必读；全部实测，证据见 KicadDecisions.md）

### 文件方言与解析器

1. **写方言 = v10**（S7 后）：`PcbFile.dumps`（`core/zig/src/sexp/kicad/pcb.zig`）恒写 v10、
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
8. **`keep_net_names` 默认随 `frozen`**（`config.py:594,623-624`）：常态每次 build 重 derive
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
14. **~~rule area 需 zig 改加 `group` 源~~【作废：C3 改走 sheetname 源】**：room rule area 改用
    `(placement (sheetname ...))`，`ZonePlacement.sheetname`（`pcb.zig:828`）已存在 → **不需要改 zig**。
    原计划的 `group` 枚举/字段（`pcb.zig:322/824`）随 group 方案一并弃用（见 §C3）。
15. **EasyEDA 取件 = CloudFront WAF（非"限流"）【✅ D1】**：(a) UA 拒绝名单——`easyeda2kicad`
    硬编码 UA / `python-requests` / `Mozilla` 全 403，`curl/*`、node UA 放行；(b) 按 IP 速率——
    突发后即便放行 UA 也短时全 403（响应体 `Request blocked` HTML → `r.json()` 抛
    `Expecting value: line 1 column 1`）。修复见 `easyeda_resilient.py`；warm build 走 1 天缓存零调用。

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
  （教训"pull≠sync 增量稳态"由 e2e determinism 测试钉；PATH footgun 已记 `/kicad_wksp/CLAUDE.md`。）
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
   （`KiCadRoutingTools/routing_config.py:117-123`）：`guide_corridor_enabled`（读 User.1 引导线，
   把 net 沿走廊牵引）+ `keepout_enabled`（读 User.2 禁布多边形，挡走线）。故 §C 的 User.1 引导
   / §D 的 User.2 room 边界**可被 E1 显式启用为一等布线约束**（默认关、按 stage 开，见 §D2/§E1）——
   非仅可视件。这两路与 §D3 的 placement rule area（KiCad 自身的分组/DRC，另一机制）不要混淆。
- **设计决策（双入口非缺陷）**：两入口 = 两套真算法共享同一 Rust 网格内核
  （`grid_router:GridObstacleMap/GridRouter`）。差分 = `PoseRouter` 位姿法 + `diff_pair_gap`
  恒定间距 + `centerline_setback`（pad 附近自动 fanout/打散，对应 decoupling 抽头 / 连接器
  pitch）+ `fix_polarity` + `length/time_matching` + `gnd_via`；单端 = `route_multipoint_main`/
  `power_nets`。行业惯例（Altium 亦分差分/单线两器），**非 bug**。**不合并算法（合并=倒退，丢
  耦合/极性/等长/位姿/centerline）**；mode 由 `route_stages` 显式声明（§D2/§E1）。
- E3 薄片仅验**差分对** schema（`KiCadRoutingTools/tests/test_router_smoke_batch_route.py`）；
  单端 schema 待 §E3 补。

---

## 未完成任务

执行序 = 章节字母：C/D 并行（✅ C 已完成）→ E 依赖 C+D → F 最后。

### P0.2 S7 flag-day 终验剩余（写 v10 代码已落 + 单测/e2e determinism 已绿）
- [ ] examples/fixtures/probe 工程 `.kicad_pcb` 一次性 v9→v10 升级提交；build→build→diff 确认
  增量稳态（事实 6）。
- [ ] BOM / 制造产物 / DRC smoke。
- [ ] 改写 `/kicad_wksp/CLAUDE.md` 与 `KicadDecisions.md` 的"单向门/只读不存"约束为
  "已迁移，v9 只读、写即升级 v10"（事实 1）。

### C3. room 去 group 化：迁移到 sheetname【✅ 2026-06-15】

**决策**：room 不再 = KiCad group。atopile **建零个/删零个 group**（用户 group 完全不碰 → A4 问题按
构造消失）。room 载体 = 每 footprint 的 `sheetname`(+`sheetfile`)（值 = `_get_group_name` = ato 地址前缀；
**不写 `path`**——KiCad 拥有并重写它成 UUID，事实 10）；route/via/zone 归属 = **内部 net**（所有 pad 都属
该 room footprint 的 net），inter-room net 不归任一 room。rule area 走 `(placement (sheetname))`，无 zig 改动。

**协议 + 接口 delta = 代码 SSOT**（勿在此重复维护）：
- `test/exporters/pcb/layout/test_room_migration_contract.py` 模块 docstring = 协议全文 + 接口 delta +
  棘轮说明；契约 C3.1/C3.4/C3.5a/C3.5b/C3.9/C3.10（7 passed/3 skipped）。
- `test/end_to_end/test_room_migration_e2e.py`：C3.2（建组数=0+sheetname）、C3.3（pull 路径字节确定性）。
- 实现：`layout_ir.py`（`rooms`，docstring SSOT）+ `layout_ir.schema.json`（v2）+ `layout_sync.py`
  （`sync_rooms`/`pull_room_layout`/`_calculate_room_offset`/`_clean_room`，删 `_is_managed_group`）+
  `room_ops.py`（`copy_room_layout` 只复制本 room）+ `build_steps.py`/`cli/kicad_ipc.py`（room API）。

**实施期与计划的偏差（as-built，须知）**：
1. **committed fixtures 未迁移**（原 C3.7 取消）：改 v9/v10 .kicad_pcb 会撞方言纪律（S7 前不重写受管板）。
   改为 **C3.1 corpus 直接从 pcb 读旧 group** 比对 address 前缀 → 真数据覆盖不变、零 fixture 改动；
   fixtures 仍带旧 group = **故意保留**（验证 atopile 仍能读 group-bearing 板）。
2. **semantic_view 不改**（保 I3 oracle 独立）；快照零漂移（未动 fixture）。
3. **`test_gui_edit_roundtrip` 不改**：它对 fixture（仍带 group）做 GUI move 断言 group 保持 = 合法 GUI
   不变量，与 C3 无关 → 4 passed 原样。原「防绿失义」改它的计划随 fixture 不迁移而**作废**。
4. **`test_group_determinism.py` 改造保留（非删）**：fresh-determinism→C3.3、upgrade-group→C3.4 删去；
   **增量 determinism + A4 手工保留** = C3 未覆盖的 A 不变量，留下升级到 room 世界（防覆盖蒸发）。
5. `transformer.py` `_add_group`/`is_marked`/`gen_uuid(mark)` **已删**（2026-06-15，§G uuid 不透明
   原则）——atopile 无 ato→group 映射、uuid 不塞 flag。
6. offset 等价不单钉契约层（inline 无法忠实复现 sub-address+源 pcb 解析，**显式不静默**）：由 C3.3 增量
   build 覆盖（错 offset 在 pull 后落位显形）。

**回归（2026-06-15，用户要求查 A/B 倒退，无倒退）**：B 全绿（layout_ir 契约 + fileformats 语料 + 快照
零漂移）；fast 全量 231 passed/8 skipped；A 增量 determinism + A4 手工保留 + C3 e2e 真构建全绿（EasyEDA
403 仅环境速率，退避后转绿）。

**cascade（C3 回开 B 与 C）**：B 的 `layout_ir.groups`→`rooms`（C3.5）、C 的 room_ops 随新 IR 形状
（C3.6）。已完成块 §B（I7）/§C（room 表示）相应标注「由 C3 取代」。

### D.【需 C3：room=sheetname】layout.yaml 加载 + KiCad 10 对象生成

**与上游接口**：
- `layout_ir.py`：`layout_ir(pcb)→dict`——C3 后读 `rooms`（address 前缀派生）+ `components{addr→at/pads}`
  算 room 包围盒；`signal_nets(app)→{信号地址:net名}` = **桥②**（地址→net 名，禁裸 net 名）；`LayoutIRError`。
- B build 产物 `<t>.layout_ir.json`（含桥②，build_steps.py "layout-ir" 步骤）——D 直接消费。
- 不变量依赖：I4（无稳定地址 net 响亮）、I6（地址前缀层级）、**I7′（room=address 前缀派生，C3）**。
- **room rule area 走 `(placement (sheetname "<addr>"))`——无 zig 改动**（事实 10/14）；footprint
  sheetname/path 由 C3 写好。**P-uuid 不再阻塞 D**（room 无 group uuid；gen_uuid mark hack 已删，§G）。

> **命名空间澄清**：net 名（电气网络，v10 唯一键）、room 名（= ato 地址，元件分组，C3 后载体 = sheetname
> 而非 group）、uuid（对象身份）是三个互不相干的命名空间。

- [ ] **D1** layout.yaml 路径配置。**落点**：`src/atopile/config.py` 给 `BuildTargetPaths`
  （非 BuildTargetConfig——路径字段自动绝对化，对齐既有 `paths.layout`=.kicad_pcb）加
  `layout_config: Path | None = None` + 在 `__init__`/`make_paths_absolute`（~299-320）相对
  project root 解析。**E·F 怎么用**：build 步骤经 `config.build.paths.layout_config` 取。
  **自测**：`test/test_config.py` 仿 `test_roundtrip` 加一例（build 带 `layout_config:
  ./layout.yaml` → 解析为绝对路径；缺省 = None）。

- [ ] **D2** layout.yaml 解析 + 校验。**落点**：`src/faebryk/exporters/pcb/layout/layout_plan.py`
  ——pydantic 模型 `LayoutPlan{rooms: list[Room], route_stages: list[RouteStage]}`，
  `Room{module(=group 名/ato 地址), origin, rotation, size, source:"group", layers, anchor}`
  （**全用 ato 地址不用位号**），`RouteStage{name, nets:[ato 地址], mode: "diff"|"single",
  config: GridRouteOverride}`。`GridRouteOverride` = `routing_config.py:31-124` `GridRouteConfig`
  字段的**可选子集**（字段名 1:1，见 §E1；YAML 键 == router kwargs，零翻译层）。
  **吃 B**：net 引用经桥②（`signal_nets`）地址→net 名解析；裸 net 名 / 无稳定地址 net（I4）
  **响亮抛**（对齐 S5a，事实 2/8）；未知 yaml 键响亮（不静默吞，S5a 纪律）。
  **E·F 怎么用**：`route_stages` 是 E1 的唯一输入；`mode` 显式驱动 §C 边界事实 1 的双入口分派
  （耦合 vs 独立，非自动猜）。**自测**：`test/exporters/pcb/layout/test_layout_plan_contract.py`
  strict-xfail 棘轮（同 B/C）：① 模型解析 + 未知键/裸 net 名/无地址 net 响亮；② **消费者-oracle**：
  yaml 里每个 net 地址引用解析出的 net 名 == 现役 `signal_nets`（在 `examples/layout_reuse` 上）；
  ③ GridRouteOverride 子集键 ⊆ GridRouteConfig 字段（防 yaml 写出 router 不认的键，漂移即红）。

- [ ] **D3** rule area + room 边界几何生成器（**前置 = C3 完成；无 zig 改动**）。**落点**：
  `src/faebryk/exporters/pcb/layout/rule_area.py` ——`generate_rule_areas(pcb, plan, ir)`：每 room
  按 origin/size/成员包围盒生成 `Zone(keepout=..., placement=ZonePlacement(sheetname="<地址>",
  enabled=True), polygon=...)`（**sheetname 源，非 group**；C3 已给 footprint 打好同名 sheetname），
  经 `kicad.insert(pcb,"zones",pcb.zones,zone)` 插入（**复用 `transformer.py:909-966 insert_zone`
  范式**）。可选：按 stage 额外吐 User.2 禁布多边形（喂 E1 `keepout_enabled`，§C 边界事实 4）。
  **约束**：禁自定义 token（事实 3）；引导/标记几何只放 User.x（事实 9）；铜层几何挂真实 net（事实 9）。
  **E·F 怎么用**：rule area 落板供 KiCad placement/DRC + F-diag rule-area 命中测试；User.2 边界供 E1
  约束 room 内布线。**自测**：`test/exporters/pcb/layout/test_rule_area_contract.py`：① 构造→
  `kicad.dumps`→`loads` 后 `(placement (sheetname "<地址>"))` 逐字存活；② polygon == room origin/size；
  ③ **`kicad-cli pcb upgrade --force` 逐字保真 + `drc` 跑通**（KiCad 真吃下去，>16 字节 room 名，复用 C3.4）。

- [ ] **D4** CLI + build 步骤接线。**落点**：(a) `src/atopile/cli/layout.py` 新 Typer 子 app
  `resolve|emit`（`resolve` = layout.yaml × IR → 解析后 plan json；`emit` = 把 rule area 落板），
  在 `cli/cli.py` 经 `app.add_typer(layout.layout_app, name="layout")` 注册（仿 create/serve）；
  (b) `src/atopile/build_steps.py` `@muster.register("layout-plan", dependencies=[<sync 后的步骤>],
  produces_artifact=True)`，函数收 `ctx: BuildStepContext`（`ctx.require_app()/require_pcb()`），
  读 `config.build.paths.layout_config`，调 D2/D3，写 `<output_base>.layout_plan.json`（resolved
  plan，供 E1）+ 把 rule area 写回板。**自测**：`test/end_to_end/test_layout_plan_build.py`
  仿 `test_layout_ir_build.py`（subprocess `[sys.executable,"-m","atopile","build","-v"]`，PATH 前置
  venv bin 防 footgun，断言 "Build successful! 🚀"）：① artifact 良构（rooms/route_stages 解析齐）；
  ② 板上有 group rule area；③ **build→build→diff 逐字节稳**（事实 6）；④ DRC 干净。

- [ ] **D5（后置）** component class 支持（需写 `.kicad_pro`，事实 10 之外的通道）。

**§D 覆盖补强（审计 2026-06-15，对照「编码与测试纪律」补三处）**：现有 D1–D4 自测已含棘轮 +
consumer-oracle（`signal_nets`）+ GridRouteOverride 漂移钉 + KiCad 交叉验证 + build→build 字节稳；补：
- **D2**：room 几何字段**校验**钉——`size`≤0 / 缺 `origin` / 非法 `mode`（非 diff|single）必响亮（loud-or-nothing，
  非仅"解析成功"）；mode 解析后驱动 §C 双入口分派的取值集封闭。
- **D3**：① **派生包围盒**用例（`size` 省略 → polygon 由 IR 成员 pad 实测包住，不只测显式 origin/size 这条）；
  ② **多 room**用例（N room → N 个 sheetname 各对、互不串）——按构造定答案，呼应 C3.10 防"单 room 假绿"。
- **D4**：**re-emit 幂等**钉——对已落 rule area 的板再 `emit` 一次：zone 数不增、字节稳（比 build→build 更尖，
  直接咬"重复落盘不去重"这类 bug）。

> **D 的产物 = E/F 的全部输入**：`<t>.layout_plan.json`（resolved route_stages + room 元数据）
> 给 E1；板上 rule area + User.1/User.2 几何给 E1（约束）+ F-diag（命中测试）；net 反查仍走
> B 的 `<t>.layout_ir.json`（事实 11，F-diag）。E1 把 RouteStage.config 的 GridRouteOverride
> 子集**原样**展开成 `batch_route`/`batch_route_diff_pairs` 的 kwargs（字段名同名，§E1）。

### E.【需 §C + §D】KiCadRoutingTools fork
E1 需 §D 的 plan + 板上 rule area，且用 §C1 的 forced via 当布线阶段能力。E3 全程并行先搭。
- [ ] **E1** `layout_plan_runner.py`：route_stages → `GridRouteConfig`（字段与 YAML 一一对应，
  `routing_config.py:31-124`）→ 多次路由调用，阶段间 pcb_data 累积。产 `route_report.json`。
  - **按 net 类型分派入口（§C 边界事实 1/设计决策，不合并算法）**：单端 → `route.py:batch_route`、
    差分对 → `route_diff.py:batch_route_diff_pairs`；聚合按共有键 `failed`/`successful`/`total_vias`
    归一、分类型键各自解析。mode 来自 `route_stages`（D2），不自动猜测。
  - **容忍缺失 `JSON_SUMMARY`**（边界事实 2）：已被 §C 完全连通的 net 不再路由，应在路由前从
    batch 输入剔除；聚合不得假设每条输入 net 都有 summary。
  - **不为 §C 预置几何加 lock**（边界事实 2）：它们是已连通铜、router 自动不动；E2 锁定面向
    *本阶段新布*的几何。
  - **GridRouteOverride → kwargs 零翻译**：`RouteStage.config`（D2 的 GridRouteConfig 字段子集）原样
    展开成 `batch_route`/`batch_route_diff_pairs` 的同名 kwargs（两入口签名即 GridRouteConfig 全字段）——
    E1 不维护映射表，新增 router 字段只需 D2 的 GridRouteOverride 放行。
  - **按 stage 启用 §C/§D 的 User 层约束**（边界事实 4）：stage 可置 `guide_corridor_enabled`
    （读 §C 的 User.1 引导）/ `keepout_enabled`（读 §D 的 User.2 room 边界），默认关、由 route_stages
    显式开——这是把 §C/§D 几何变成布线约束的唯一通道。
- [ ] **E2** 几何所有权/阶段锁定：`Segment`/`Via` 加 `_metadata`（内存态）；`rip_up_net`
  （`rip_up_reroute.py:60-72`）加阶段守卫；`lock_after` 阶段后续不可撕。
- [ ] **E3** 独立回归台（全程并行先搭 = §E 的 S0 底座）：无需装 KiCad，预编译 Rust 二进制 +
  内置测试板 + numpy/scipy/shapely；失败注入 + 报告 schema 校验。
  - 最小切片已提前到 §C【✅】：`KiCadRoutingTools/tests/test_router_smoke_batch_route.py`
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
3. **`_generate_net_map` 并列判据死代码**（`layout_sync.py:238`）：
   `mapping_counts[src][tgt] > max(values())` 自增后恒 false → 注释写"最频映射"实为"首次胜"。
   当前 pull 迭代序确定故未发病，歧义映射下是休眠隐患。修法：并列取字典序最小 tgt + 迭代按
   src_addr/pad 排序。**注**：B/C 的消费者-oracle 以现役 `_generate_net_map` 为真值；此处硬化改
   行为须同步更新 oracle 期望（两者绑定）。
