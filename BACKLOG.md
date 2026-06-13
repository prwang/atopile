# BACKLOG — 文本化布局工作流（deterministic text-first layout layer）

## 背景

在 atopile + KiCad 10 + KiCadRoutingTools 之上构建确定性、以文本为唯一事实来源的
PCB 布局布线工作流：`.ato` = 电路事实源，`layout.yaml` = 布局意图事实源，KiCad 工程
为派生产物；工具输出结构化 JSON 诊断，供 **PCB Layout SKILL** 迭代。调研/验证证据见
`/kicad_wksp/KicadDecisions.md`（含文件:行号引用）。本清单只保留**当前有效事实**与
**未完成任务**；已完成项压缩为结论 + 代码指针。

## 已定决策

- 不 fork KiCad 10（所需对象已实测可外部生成并完整往返）。
- fork 基线 = 本仓库 HEAD；同时 fork KiCadRoutingTools（§E）。
- Python 3.14 开发环境 = **`uv sync`**（`ziglang==0.15.1` 由 pip 构建依赖提供；
  ~~miniconda~~ 不需要——原决策已纠正，见 P0.1 T0）。克隆须 `git fetch --tags`
  否则 setuptools-scm 产非 SemVer 版本号、`ato` CLI 启动即崩。
- room 来源固定 = KiCad named group（sheet 路线不存在，component class 后置）。
- **v10-only 路线（2026-06-13 用户拍板 Option B，upgrade-on-write）**：本分支目标
  方言 = v10；**前向兼容 v9 写出不做**（v9 仅只读，读入即在写出时升级为 v10）。
  理由：头号目标是 room 局部自动布线，维护 v9 写出是长期负担。
  → 推论（贯穿下文）：(i) 不再有"写 v9"代码路径；(ii) net 名是文件内唯一键，
  名字漂移 = 几何归属漂移（非显示问题）；(iii) S7 后"单向门/只读不存"纪律**已解除**
  ——KiCad 10 GUI 保存不再损坏受管板子。

## 优先级总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 确定性与基础修复（§A） | ✅ |
| P0.1 | v10 迁移测试底座（§P0.1，任务编号 T0–T9，**全部已完成**） | ✅ |
| P0.2 | v10 方言迁移本体（§P0.2，S0–S6 + D1 + BUG-2 完成） | 🔶 S7 剩 flag-day 升级/smoke |
| P1+ | 功能开发（§B/§C/§D/§E）——**真实排序见下方"依赖与关键路径"，非按字母** | ⬜ |

> 命名澄清：**T0–T9 = P0.1 已完成的测试底座任务**（T1 = v10 语料 fixtures，T3 = 语义视图
> = layout_ir 提取器底座）。往后的功能开发**没有 T 编号**，落在 §B/§C/§D/§E；其中
> layout_ir 是 **B1**，不是"T1"。

### 依赖与关键路径（v10 数据模型推导，覆盖字母序）

v10 模型有三条硬约束逼出真实排序：① 无顶层 net 表 + 随机 FBRK uuid → **唯一稳定键是 ato
地址**（不是 uuid/net 编号）；② **net 名 = net 唯一键**，名漂移 = 几何归属漂移（事实 2/8，
BUG-2 同源面）；③ 引导/铜层几何分层与挂 net 规则（事实 9）。由此：

```
B (layout_ir)  ← 关键路径基石，最先做、最便宜（提取器 T3 已交付）
│  提供 addr↔uuid↔net(name) 桥表；下游全部依赖它
├─► C2 (room/net 解析，net 走 ir 间接，禁裸名)
│   └─► [前置: 遗留#3 gen_uuid 长名修复] ─► C3 (rule area placement group) ─► C4 (CLI+验收)
├─► E3 (室局部→板坐标 forced via)
└─► E4 (room 复制 = 文本版 pull_group_layout，按地址前缀重映射 net，继承 BUG-2 教训)
                                   │
C(plan) + 板上 rule area ─────────►  E1 (plan_runner) + E2 (阶段锁定) ──► D (诊断闭环)

E5 (独立回归台) ‖ 与 E 全程并行，应尽早搭 = E 的 S0 纪律底座
```

**推荐落地序**：B1→B2 ⇒ C1→C2 ⇒（遗留#3）⇒ C3→C4 ⇒ E5‖E1→E2 ⇒ E3/E4 ⇒ D。
即原"P1/P2"的字母分组**不代表执行序**——B 必须先行解锁四个下游，D 必须最后（吃 E 的
route_report + ir 反查）。

---

## 关键事实与约束（改代码前必读；全部实测，证据见 KicadDecisions.md）

### 文件方言与解析器

1. **写方言 = v10（S7 flag day 后，提交 `b3c0d2aa`）**：`PcbFile.dumps`
   （`core/zig/src/sexp/kicad/pcb.zig`）恒写 v10 并 stamp `version=20260206`；
   v9/v5 仍可读，读入后写出即升级为 v10（upgrade-on-write）。`KICAD_PCB_VERSION`
   / `KICAD_FP_VERSION = 20260206`（`pcb.zig:12-15`）。
2. **v10 net 模型**（durable fact，源于 P0.2 实测）：v10 文件**无顶层 net 表**——net 只在
   引用处 `(net "名")`（pad/segment/via/zone）；无 `net_name` 冗余；keepout zone 省略
   net 子句。`pcb.nets` 在读入时由引用扫描**按名排序合成编号**（"" 恒 0、1..n 稠密，
   引用序无关、跨进程确定）。**编号 = 进程内句柄**，Python 可见模型不变
   （`segment.net:int`、`pad.net:Net{number,name}`）。
   推论：**未被引用的 net 不会被持久化**（v10 无 net 表存放它）——`insert_net` 必须随后
   绑定 pad/routing 才会留存。
3. **解析器对未知内容不对称**：KiCad 遇未知 token 直接报错
   （`pcb_io_kicad_sexpr_parser.cpp:1388`）→ 自有元数据只能放 footprint `(property ...)`
   或 sidecar，禁止发明自定义 token；Zig 历史上静默跳过未知键且写回丢失，**S5a 已改为
   响亮警告/strict 抛错**（`fileformats.py` `UnknownSexpKeys`/`last_unknown_keys`，
   `test/libs/kicad/test_unknown_key_loudness.py`）。schema 必须完整覆盖 v10。
4. **`kicad.loads` Path 缓存 + pyzig 所有权【✅ S1，`test_pyzig_ownership.py`】**：缓存按
   (mtime_ns, size) 失效，`kicad.dumps(obj, path)` 回写缓存（dump→load 同对象）；子对象
   包装持 owner 强引用链（child→parent→root），`loads(...).kicad_pcb` 安全。
5. **version 守卫【✅ S2，`test_version_guard.py`】**：超 `PCB_MAX_SUPPORTED_VERSION`
   （20260206）抛 `kicad.UnsupportedKicadVersion`（含双版本号 + 指引）。

### 确定性边界

6. **UUID 随机、靠回读稳定**：`gen_uuid`（`fileformats.py:150-165`）= uuid4 + "FBRK"
   后缀；确定性目标 = **增量稳态**（同一工作副本连续 build 字节一致），非 clean-checkout
   可重现。`.kicad_pcb` 必须入库；确定性测试 = "在已有布局上 build→build→diff"。
7. **`gen_uuid(mark)` 对 >16 字符名字产超长非法 uuid**（`UUID=str` 无校验）。生成长地址
   组（C3）前必修。
8. **`keep_net_names` 默认随 `frozen`**（`config.py:594,623-624`）：常态每次 build 重 derive
   net 名——名字漂移源头。**v10 下名字漂移 = 几何归属漂移**（事实 2），M7/遗留问题 2 验收
   须含"稳态重建下全部 net 名字节稳定"。

### KiCad 行为

9. **net-0 悬空铜被 KiCad 重存清理**（连带空 group）：写铜层几何必须挂真实 net；引导/标记
   几何放 User.x（User.1=引导走廊、User.2=禁布区）。
10. **atopile 不生成 .kicad_sch**：多通道布局走 named groups。
11. **DRC JSON 无结构化 net 字段**（net 名嵌描述文本）：诊断层用 items[].uuid 经 layout_ir 反查。
12. **SWIG Python 绑定全禁**（KiCad 10 移除板级 API）；Repeat Layout 仅 GUI 入口。

### 周边

13. **KiCadRoutingTools 已有结构化结果**（`return_results=True`、`JSON_SUMMARY`、
    `BlockingInfo`）：诊断层是聚合+映射；其解析器独立、v9/v10 兼容、不解析 groups——
    不受本仓库迁移影响。
14. **C3b 范围已缩小**：Zig schema 已有 `ZonePlacement`（`gen/sexp/pcb.pyi`），只缺
    `source_type` 的 `group` 枚举值与 `(group "...")` 子句；直接在 v10 schema 上做。
15. **EasyEDA 取件 = CloudFront WAF（非"限流"，旧记已纠正）【✅ D1，`f5b707fe`】**：
    两条独立规则——(a) **UA 拒绝名单**：`easyeda2kicad` 硬编码 UA、`python-requests`、
    `Mozilla` 全 403；`curl/*`、node UA 放行。(b) **按 IP 速率规则**：突发触发后即便放行 UA
    也短时全 403（响应体 `Request blocked` HTML → `r.json()` 抛 `Expecting value: line 1
    column 1`，即历史误判的"空 JSON"）。修复 = `faebryk/libs/picker/easyeda_resilient.py`
    `ResilientEasyedaApi`（放行 UA + jitter 指数退避，4 处调用点全换）。warm build 走
    1 天缓存（`build/cache/parts/easyeda`）零调用。

---

## A. P0 — 确定性与基础修复【✅ 2026-06-12】

回归：`test/end_to_end/test_group_determinism.py`（变异验证过）。

- **A1** group 成员排序漂移：真因在 `pull_group_layout`（set 迭代序追加），非 sync_groups。
  修复 = pull 末尾全量排序去重。
- **A2** keep_designators：HEAD 默认已 True（`config.py:592`）；降级为测试固化。
- **A4** 手工命名 group 被每次 build 静默删除（上游 48fe6e18 清理过宽）：修复 =
  `_is_managed_group()`（仅清理 uuid 后缀=组名 hex 的自管组）。
- **A3** CI 接线 → 并入 P0.1 T9。

## P0.1 — v10 迁移测试底座【✅ 2026-06-12】

基线 88 passed + 26 strict-xfail（xfail 即 P0.2 验收开关，已随 S4–S7 转绿）。
核心原则：测试多样性由 harness 制造（corrupter / PYTHONHASHSEED），不靠 KiCad 天然语料
（天然语料表序=编号序=首引序对齐，按位置绑定的 loader 能蒙混；T8 变异自检实证）。

交付（测试文件即指针）：
- **T0** dev 环境 = `uv sync`（纠正原 miniconda 决策）。
- **T1** v10 语料 `test/common/resources/fileformats/kicad/v10/pcb/`（含原生 KiCad 10 保存
  的 `lvds_converter_dualclk` + (v9,v10) 配对）；来源见 `v10/README.md`。
- **T2** 全语料四闸门 `test_fileformats_corpus.py`（parse / 幂等 / 无数据丢失 / 字节保真）；
  schema 无关 diff `libs/test/sexp_tree.py`。
- **T3** 语义视图快照 `libs/kicad/semantic_view.py`（= B1 layout_ir 底座）+ 入库快照。
- **T4** corrupter `test_net_binding_corruption.py`（24 用例，保语义/破语义/harness 自检）。
- **T5** net 名 property 测试 `test_net_name_properties.py`（zig writer 转义无 bug）。
- **T6** transformer net 单测 `test_transformer_nets.py`。
- **T7** layout_sync net 单测 `test_layout_sync_nets.py`。
- **T8** 变异自检（已执行回滚，结论：安全网对两类目标 bug 有效，corrupter 必需）。
- **T9** CI：pytest.yml 装 kicad-cli 10（continue-on-error + skipif）；EasyEDA 离线 fixture
  `test/common/resources/easyeda-cache/`（E2E 自动播种，构建零网络）。

## P0.2 — v10 方言迁移本体【S0–S6 + D1 ✅；S7 待 BUG-2】

执行纪律：每步带"转绿（strict-xfail 棘轮）+ 保持绿"两清单进 commit message；带红灯不进下一步。

- **S0** 验收测试先行【✅ `test/libs/kicad/test_v10_acceptance.py`】：合成规则契约 / DRC oracle /
  KiCad 重存语义回环 三个 oracle + S6 反转清单。
- **S1** pyzig 所有权 + loads 缓存联修【✅ 事实 4，`test_pyzig_ownership.py`】。
- **S2** version 守卫【✅ 事实 5，`test_version_guard.py`】。
- **S3** tenting 族嵌套化【✅ 统一 `Tenting{front,back,none}` + `write_dialect`/`dual_bool`/
  `v9_only` 方言基建，`test_tenting_dialect.py`】。
- **S4** net 模型重构【✅ 事实 2；读侧按名合成编号、写侧 net_ref 写名、net 表/net_name =
  `v9_only` 抑制；Python 模型零变化】。
- **S5** 静默丢弃键补全【✅ S5a `9fd47a51`（未知键响亮化，事实 3）+ S5b（schema 补全，全语料
  unknown=0/loss=0；v10 fixture 由我方 writer 重生、DRC rc=0）】。范围决策（最小集）：
  **保真集** = 既有 7 开关 + group + rule area placement；**警告集** = teardrop/蛇形等长/
  via padstack（仅 S5a 警告，见 §P1+）。
- **S6** Python 消费方迁移【✅】：
  - S6a `02bd5998` transformer.remove_net 按 net 句柄单键匹配 zone（net_name 是 v9-only）。
  - S6b `0d3a516b` layout_sync.`_get_net_number` 未知名改 raise（守卫保证名必存在）。
  - S6c `663baa6f` pcb_manager.`_extract_zone` 用合成表回退补 v10 zone 标签；
    **GUI 编辑回环验收门**已落地并绿 `test/layout_server/test_gui_edit_roundtrip.py`
    （受管 move→save→重读：方言保持/编辑持久/groups/zones/nets/pad net 无损/DRC/警告集响亮）。
- **D1** EasyEDA 取件韧性【✅ 事实 15，`easyeda_resilient.py` + `test_easyeda_resilient.py`】。
- **附带修复**（D1 解封 e2e 后暴露）：footprint `tags` 类型对齐 `list(str)`【`1fdaab18`，
  S5b 引入的 `?str` 与 footprint.zig `list(str)` 冲突，crash `insert_footprint`】；
  BUG-1 测试隔离【见下】。

### ⬜ S7. flag day 终验（代码已落，BUG-2 已修，剩余验收项）

代码已完成（写 v10 + 版本 bump，`b3c0d2aa`；单测 354 passed；真实 `ato build` 出 v10 板、
`test_pcb_export`/`test_net_naming` 11 passed、DRC 干净）。**剩余验收**：
- [x] **BUG-2 已修**（v10 读回填 pad net 名，见下）——增量稳态阻断已解除。
- [x] E2E room-reuse 确定性绿：`test_group_determinism`（4）+ `test_net_name_determinism`（1）
  = 5 passed；pull→rebuild→rebuild 逐字节一致、恒 24 net-ref。
- [ ] examples/fixtures/probe 工程 `.kicad_pcb` 一次性升级提交（v9→v10）；跑 build→build→diff
  确认增量稳态（事实 6）。
- [ ] BOM/制造产物/DRC smoke。
- [ ] 改写 `/kicad_wksp/CLAUDE.md` 与 `KicadDecisions.md` 的"单向门/只读不存"约束为
  "已迁移，v9 只读、写即升级 v10"（事实 1）。
- 已绿子项：S0 DRC oracle（`b3c0d2aa` 翻绿，v9 输入升级 v10 后 kicad-cli DRC 通过）；
  GUI 编辑回环门（S6c 提前落地）。

### 调试日志 BUG-2：rebuild 丢失 pull 进来的 room 布线 net（pull≠sync）【✅ CONFIRMED+FIXED 2026-06-13】

根因 = **v10 读侧不回填 pad net 名**（`Net.name` 是 `v9_only` 字段，v10 pad 的 `(net "名")`
经 net_ref 只解析出编号、`name` 留 None）。下游 rebuild 时
`libs/nets.bind_fbrk_nets_to_kicad_nets` 靠 `pad.net.name` 把 fbrk net 重新绑回已存 kicad
net；名缺失→绑定静默失败→`apply_design` 把该 net 当新 net `insert_net`（拿新编号、改绑
pads）、把读回合成的旧同名 net 判为"设计中不存在"→`remove_net`→`remove_net` 按编号断开
routing（`transformer.py:1963-1966`）→ 引用旧编号的 segment 落 net 0、v10 写出时省略。
pad 因被 apply_design 重设而存活，**只有 pull 进来的 segment 掉 net**。

修复：`pcb.zig` `PcbFile.loads` 合成 net 表后，遍历 footprint pads 把 `pad.net.name` 按
合成编号回填（`names.items[number-1]`，"" = 0），使内存模型方言无关。回归测试
`test_v10_acceptance.py::test_v10_read_backfills_pad_net_names`。

**实测纠偏（原假设表全错，留作教训）**：原记"net 分配非确定/18-24 run-to-run 跳变/pull 随机
sync 确定"——**全部错**。实测（examples/layout_reuse + 缓存，venv ato 直跑，注意 PATH 必须
让 venv ato 在 `~/.local/bin` 之前，否则 BuildQueue 会 spawn 装好的旧版 0.15.7 worker、
版本错配崩在 pydantic/sqlite schema）：
- **确定性的单步回归**，与 PYTHONHASHSEED 无关：fresh pull 恒 24 net-ref（hs=0/1/2/7 全同）；
  **第一次 rebuild 恒掉到 18**；其后 rebuild 恒 18。即 pull 确定、sync 也确定，但 **pull≠sync**
  （违反 A1 增量稳态）。掉的 6 = 3 个 sub × 2 条 intra-sub chain net 的 segment 引用
  （inter-sub net 无布线故不显形，但其 net 同样被 renumber，只是无 segment 可掉）。
- H1（`_generate_net_map` 首次胜并列判据）`[REJECTED]`：pull 本身确定，与此无关。
  （注：该并列判据 `mapping_counts[src][tgt] > max(values())` 自增后恒 false = 死代码 ≈
  "首次胜"，是真实但**休眠**的隐患，留 §遗留问题。）
- H2/H3/H4 `[REJECTED]`：非随机 set 迭代、非 pull/sync 编号不一致、非 pre-existing
  （bug 由 S7 写 v10 引入：v9 pad 自带名，v10 才暴露未回填）。

修复后验收：3 个 e2e 全绿（`test_group_determinism.py` 4 个 + `test_net_name_determinism.py`
1 个 = 5 passed in 247s）；pull/rebuild/rebuild 三连逐字节一致且恒 24 ref；
`test/libs/kicad`+`exporters/pcb`+`layout_server` 181 passed。

### 调试日志 BUG-1：测试污染版本控制 fixture v8/pcb/test.kicad_pcb 【✅ CONFIRMED+FIXED】

根因 H3（`test_server.py` `app` fixture 用源 `TEST_PCB` 路径构造 `create_app`，execute-action
端点 flip/rotate/move → `service.save_and_broadcast` → `PcbManager.save()` 写回源 fixture）。
隔离复现：单跑 `test_server.py` → v8 显 M（flip diff）。修复 = `app` fixture 改用 `shutil.copy2`
到 `tmp_path` 副本（对齐 `test_pcb_manager::test_save_roundtrip`）。同类潜伏已记录：
`test_frontend_drag_closed_loop.py:93` 把 server 架在源 esp32 路径（本沙盒 skip），S6c/后续
改 tmp 副本。

### §P1+ 候选 — GUI 高级布线构造保真

teardrop / generated 蛇形等长 / via padstack 的 v10 子键形变补全（schema 有 KiCad 9 形模型，
缺 v10 验证）。P0.2 内由 S5a 响亮警告兜底，演示脚本回避。验收 = 从"警告集"挪进"保真集"。

---

## B. 【关键路径基石，先做】layout_ir.json 导出（提取器已由 T3 交付）

- [ ] **B1** 构建步骤 `layout-ir`（`@muster.register("layout-ir", dependencies=[update_pcb])`，
  `build_steps.py`）：遍历实例图 + footprint 映射 → `build/builds/<target>/layout_ir.json`
  （模块地址 → {type, group_uuid, components{地址→位号/footprint_uuid/pads{net,xy,layer}}, nets}）。
  地址 = `Node.get_full_name(include_uuid=False)`（`faebryk/core/node.py:1520-1556`）。
  实现基底 = T3 语义视图提取器。
- [ ] **B2** schema 固化 + version 字段 + JSON Schema 入库。

## C. layout.yaml 加载 + KiCad 10 对象生成（依赖 B）

- [ ] **C1** `BuildTargetConfig.layout_config: Path | None`（`config.py:559-682`）。
- [ ] **C2** layout.yaml + room 解析：rooms = module（=group 名）/origin/rotation/size/
  `source: group`/layers/anchor（**用 ato 地址不用位号**）。net 引用走"接口地址→net"间接映射
  （layout_ir 提供）；**v10 下禁止裸 net 名引用**（事实 2/8：net 名是唯一键，漂移=几何漂移；
  原"降级允许裸名"在 v10-only 下取消，见遗留问题 2）。
- [ ] **C3** placement rule area 生成器：按 room 生成 `(zone ... (keepout ...)
  (placement (enabled yes)(group "<地址>"))(polygon ...))`（已实测 KiCad 10 完整往返）。
  **直接走 C3b（v10 schema 扩展，事实 14）——C3a 后处理注入已取消**（v10-only 下无 v9 写出
  路径可后处理，且 schema 缺口仅 group 枚举，不值得过渡 hack）。约束：禁自定义 token（事实 3）、
  引导几何只放 User.x（事实 9）、铜层几何挂真实 net（事实 9）。
- [ ] **C4** `ato layout resolve|emit` CLI（`cli/cli.py`）。验收：`examples/layout_reuse` 上
  `ato build` 两次字节级一致（依赖 BUG-2 修复）+ DRC 正常。
- [ ] **C5（后置）** component class 支持（需写 `.kicad_pro`）。

## D. 诊断闭环（最后做，依赖 E 的 route_report + B 的 ir 反查）

- [ ] **D-route** `ato route --plan layout.yaml --stage <name>`：进程内调 KiCadRoutingTools
  fork 执行器（§E1，`batch_route(..., return_results=True)`）。
- [ ] **D-diag** `ato diagnose` → diagnostics.json：聚合 route_report.json + `kicad-cli pcb drc
  --format json`（uuid 经 layout_ir 反查，事实 11）+ rule-area 命中测试；字段 stage/room/
  ato_path/constraint(JSON-pointer)/reason/blocking_nets/failed_endpoints/suggestions。
  schema 基线参考 kicad-happy。

## E. KiCadRoutingTools fork（E1 依赖 C 的 plan + 板上 rule area；E5 全程并行先搭）

- [ ] **E1** `layout_plan_runner.py`：route_stages → `GridRouteConfig`（字段与 YAML 一一对应，
  `routing_config.py:31-124`）→ 多次 `batch_route`，阶段间 pcb_data 累积。产 `route_report.json`。
- [ ] **E2** 几何所有权/阶段锁定：`Segment`/`Via` 加 `_metadata`（内存态）；`rip_up_net`
  （`rip_up_reroute.py:60-72`）加阶段守卫；`lock_after` 阶段后续不可撕。
- [ ] **E3** 硬引导点/强制过孔（不改 A\* 内核，分段方案）：room 局部→板坐标
  `board_xy = origin + rotate(local, rotation)` 预插 Via → net 分解为 pad→via@入层、
  via@出层→pad 两段。验收：落点误差 ≤1 网格；失败报 `blocked_before_forced_via` + 阻塞分析。
- [ ] **E4** room 布局复制（替代 GUI Repeat Layout）：源几何 + 锚点变换 + net 重映射（按 ato
  地址前缀替换）；坐标变换借鉴 `ReplicateLayout/replicate_layout.py:47-66`（只借算法，SWIG 已移除）。
- [ ] **E5** 独立回归（无需装 KiCad，预编译 Rust 二进制 + 内置测试板 + numpy/scipy/shapely）：
  失败注入 + 报告 schema 校验。

## F. 不做 / 暂缓

- ❌ fork KiCad 10；❌ 旧 SWIG 绑定；❌ **v9 写出/双向方言 shim**（v10-only 决策）；
  ❌ 盲随机语法 fuzz（危险类 bug 是"合法文件静默误绑"，由 T4 corrupter + T5 property 覆盖）；
  ❌ KiCad 原生 design block 库（`atopile_subaddresses` 已覆盖）；❌ 调 GUI Repeat Layout。
- ⏸ clean-checkout 可重现（地址派生 UUID，半径大；增量稳态已够，事实 6）；
  ⏸ 向 KiCad 上游提 DRC JSON 增强补丁；⏸ 改 `.ato` 语法（布局走 sidecar）。

## 遗留问题

1. **C3a 已取消**（见 §C3）——v10-only 下直接 C3b。
2. **net 名漂移**（自动编号 `unnamed[N]`，关联事实 8）：v10 下名字 = 唯一键，漂移 = 几何归属
   漂移。验收须加"稳态重建下全部 net 名字节稳定"。**与 BUG-2 同源风险**（net→几何绑定稳定性）。
3. `gen_uuid` 长名字 bug（事实 7）：生成长地址组（C3）前必修。
4. 是否向 KiCad 上游提 DRC JSON 增强补丁（见 §F）。
5. **`_generate_net_map` 并列判据死代码**（`layout_sync.py:238`）：
   `mapping_counts[src][tgt] > max(values())` 自增后恒 false → 注释写"最频映射"实为"首次胜"。
   当前 pull 迭代序确定故未发病（BUG-2 排查中 `[REJECTED]` 为根因），但歧义映射下是休眠隐患。
   修法：并列取字典序最小 tgt + 迭代按 src_addr/pad 排序。无歧义复用场景不阻塞，留作硬化项。
