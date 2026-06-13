# BACKLOG — 文本化布局工作流（deterministic text-first layout layer）

## 背景

我们在 atopile + KiCad 10 + KiCadRoutingTools 之上构建一条确定性的、以文本为唯一事实来源的
PCB 布局布线工作流：`.ato` 描述电路（电路事实来源），`layout.yaml` 描述布局意图
（room 划分、坐标、布线阶段、关键过孔——布局事实来源），KiCad 工程文件为派生产物；
布局/布线工具输出机器可读的结构化诊断（JSON），使自动化的 **PCB Layout SKILL** 能以
"修改文本 → 重新生成 → 读取诊断 → 再修改"的方式快速迭代。

调研证据、运行时验证记录、数据格式草案与对前期预估的修正过程见
`/kicad_wksp/KicadDecisions.md`（自包含，含文件:行号级引用）。本清单只保留**当前有效的
事实**与**未完成的任务**；已完成项压缩为结论+指针。

## 已定决策

- 不 fork KiCad 10（所需对象已实测可外部生成并完整往返）。
- fork 基线 = 本仓库 HEAD（最近提交 2026-03 bugfix，稳定）；同时 fork KiCadRoutingTools（§E）。
- Python 3.14 开发环境用 miniconda 安装。
- room 来源固定 = KiCad named group（sheet 路线不存在，component class 后置）。
- **（2026-06-12）v10 方言全面迁移提级为 P0.1/P0.2，高于原 P1**：长痛不如短痛——
  不做"读 v10 写 v9"的长期 shim，目标写方言 = v10；先补测试底座（P0.1），再动模型（P0.2）。
  P0.2 完成前维持"v9 方言 + KiCad 10 只读不存"纪律。

## 优先级总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 确定性与基础修复（§A） | ✅ 完成 |
| **P0.1** | **v10 迁移测试底座（§P0.1，原 §G 前半）** | ✅ 完成（88 通过 + 26 strict-xfail 待 P0.2 转绿） |
| **P0.2** | **v10 方言迁移本体（§P0.2）** | ⬜ 下一步 |
| P1 | layout_ir 导出（§B）、layout.yaml + rule area（§C）、布线执行器（§E1/E2/E5） | ⬜ |
| P2 | 诊断闭环（§D）、硬引导点/room 复制（§E3/E4） | ⬜ |

---

## 关键事实与约束（fact 先行——改代码前必读；全部实测，证据见 KicadDecisions.md）

### 文件方言与解析器

1. **方言门现状（S4 后更新）**：v10（`(version 20260206)`）读侧已支持（S4），
   "单向门"的"读不回"半边已拆除；但 atopile 写方言仍 = v9（S7 flag day 才切），
   v10 板重写会保持 v10。纪律维持到 S7：管理中的板子**仍不跑 upgrade、不在
   KiCad 10 GUI 中保存**——混方言管理（增量稳态、E2E）未在 v10 上验证，
   S7 一次性切换并终验。
2. **v9→v10 断裂点实测完整清单**（逐错误迭代修补至整文件可解析，2026-06-12）——
   这是 P0.2 的需求清单：
   - ① **net 模型彻底重构（比"去编号"更彻底，2026-06-12 语料生成时实测修正）**：
     v10 文件**没有顶层 net 表**——net 只存在于引用处（pad/segment/via/zone 一律
     `(net "名")`），编号和表都从文件中消失；zone 的冗余 `net_name` 字段没了；
     无网络的 zone（keepout）直接省略 net 子句。推论：读侧合成编号没有"表序"可依，
     顺序契约必须自定义（M0 决策；T4 已钉死两条底线：引用序无关性 + 跨进程确定性）；
     in-memory 的 `pcb.nets` 列表在 v10 读入时只能由引用扫描合成。
   - ② **盘面处理字段嵌套化**（tenting 族）：`(tenting front back)` →
     `(tenting (front yes) (back yes))`，涉及 via/pad 的 padstack 字段——局部形状变化。
   - ③ **其余 v10 新键被 Zig 静默跳过**（plot 参数、`duplicate_pad_numbers_are_jumpers`、
     `locked` 移除、层 id 重编、…）：不挡读，但**写回丢失**。完整清单不靠人工枚举——
     由 T2 在 v10 语料上的 `raw == dump` 字节保真 diff 自动产出（见 P0.1）。
   - 工作量标定：只读 shim 2–5 天（已弃选）；全面迁移周级（`Net{number,name}` 模型
     + 21 个 Python 消费方）。
3. **解析器对未知内容的行为不对称**：KiCad 对未知 token 直接报错
   （`pcb_io_kicad_sexpr_parser.cpp:1388`）→ 自有元数据只能放 footprint `(property ...)`
   或 sidecar 文件，禁止发明自定义 S-expression token；Zig 则静默跳过未知键且写回丢失
   → schema 必须完整覆盖目标方言。
4. **`kicad.loads` 按 Path 缓存【✅ S1 已修】**：缓存现按 (mtime_ns, size) 指纹失效，
   `kicad.dumps(obj, path)` 回写缓存——dump→load 返回同一对象（进程内工具链可依赖）。
   **4b. pyzig 所有权 use-after-free【✅ S1 已修】**：历史症状=丢弃 `PcbFile` 包装后
   `.kicad_pcb` 等子对象悬空且不崩溃，内存复用后静默读出另一块板的数据。
   现子对象包装持 owner 强引用链（child→parent→root），`loads(...).kicad_pcb`
   写法安全；两件事按互锁要求同一提交修复，回归测试
   `test/libs/kicad/test_pyzig_ownership.py`（含变异自检记录）。
5. **version 守卫【✅ S2 已补】**：`kicad.loads` 对超过 `PCB_MAX_SUPPORTED_VERSION`
   （现 20260206）的板子抛 `kicad.UnsupportedKicadVersion` 可读报错（含双版本号
   与指引）。Zig 错误归因仍偶有指错类/缺位置（遗留，未挡路）。

### 确定性边界

6. **UUID 随机生成、靠回读稳定**：`gen_uuid`（`fileformats.py:150-165`）= uuid4 +
   "FBRK" hex 后缀；首次生成即随机，仅靠从已有 .kicad_pcb 回读保持稳定。确定性目标 =
   **增量稳态**（同一工作副本连续构建字节一致），非 clean-checkout 可重现。推论：
   `.kicad_pcb` 必须入库；CI 确定性测试必须"在已有布局上 build→build→diff"，
   不能"两次从零构建比对"。
7. **`gen_uuid(mark)` 对 >16 字符名字产出超长非法 uuid**（`UUID = str` 无校验，
   实测 20 字符名 → 40 hex）。现状 group 名通常较短未触雷；生成长地址组前必须先修。
8. **`keep_net_names` 默认随 `frozen`**（`config.py:594,623-624`）：常态下 net 名每次
   构建重 derive——net 名漂移的现存源头（关联 C2 与遗留问题 2）。

### KiCad 行为

9. **net-0 悬空铜被 KiCad 重存清理**（连带空 group）：写铜层的几何必须挂真实 net；
   引导/标记几何放 User.x 层（User.1=引导走廊、User.2=禁布区，与布线器约定一致）。
10. **atopile 不生成 .kicad_sch**：sheet 路线不存在；多通道布局走 named groups。
11. **DRC JSON 无结构化 net 字段**（net 名嵌在描述文本）：诊断层用 items[].uuid 经
    layout_ir 反查。
12. SWIG Python 绑定完全禁用（KiCad 10 已移除板级 API）；Repeat Layout 仅 GUI 入口。

### 周边

13. **KiCadRoutingTools 已有结构化结果**（`return_results=True`、stdout `JSON_SUMMARY`、
    `BlockingInfo`）：诊断层是聚合+映射。其解析器独立、已兼容 v9/v10，不解析 groups——
    不受本仓库迁移影响。
14. **C3b 范围已缩小**：Zig schema 已有 `ZonePlacement`（`gen/sexp/pcb.pyi:937`），
    缺的只是 `source_type` 枚举的 `group` 值与 `(group "...")` 子句。
15. **EasyEDA API 会限流 403**：fresh 构建选型依赖它；离线靠工程级
    `build/cache/parts/easyeda`（probe 已有）——CI 必须预置 fixture 或离线开关。

---

## A. P0 — 确定性与基础修复【✅ 2026-06-12 完成】

详细成因分析、修复半径核查与验证记录见 git 历史（分支 `fix/p0-layout-determinism`）
与 KicadDecisions.md。回归测试：`test/end_to_end/test_group_determinism.py`
（4 用例，全部经"破坏修复→测试变红"变异验证）。

- [x] **A1. group 成员排序漂移**：真实源在 `pull_group_layout`（set 迭代序追加，
  PYTHONHASHSEED 随机），非 BACKLOG 原计划所指的 sync_groups（HEAD 已排序）。
  修复：pull 末尾全量排序去重（约 3 行），单点覆盖构建 + KiCad 插件 IPC 两路径。
  交付边界 = 增量稳态确定性（见事实 6）。
- [x] **A2. keep_designators**：前提过时——HEAD 默认已 True（`config.py:592`），
  无代码可改；降级为测试固化（新增实例后 R1–R9 不变，实测通过）。
- [x] **A4.（新发现）手工命名 group 内容被每次构建静默删除**：上游 48fe6e18 清理范围
  过宽。修复：`_is_managed_group()`（atopile 自管组 uuid 后缀 = 组名 hex），仅清理自管组；
  删除模块实例后 pulled 几何仍正常清理（8→6 段验证）。
- [~] **A3. CI 接线** → 并入 P0.1 的 T9（测试本体已交付）。

---

## P0.1 — v10 迁移测试底座【✅ 2026-06-12 完成】

**目标**：把"现在的正确行为"钉成可执行的地面，使 P0.2 每一步改动都有红/绿信号。

**核心原则（防 false negative）**：测试的多样性必须由我们的 harness 制造
（corrupter / 置换 / PYTHONHASHSEED），**不得指望 KiCad 产物天然提供**——
天然语料里 net 表序 = 编号序 = 首次引用序三者对齐，按位置绑定的 loader 能通过
全部天然往返测试。T8 自检用实验证实了这一点（见下）。盲随机 fuzz 不解决该问题
（语法垃圾只测错误路径），明确不做（§F）。

交付清单（测试基线：**88 passed + 26 strict-xfail**，xfail 即 P0.2 的验收开关）：

- [x] **T0. dev 环境**：`uv sync` 即可——`ziglang==0.15.1` 是 pip 构建依赖，
  Python 3.14 用 uv 托管版（miniconda 不需要，原决策据此修正）。
  注意：克隆需 `git fetch --tags`（上游 https），否则 setuptools-scm 产出非
  SemVer 版本号、`ato` CLI 启动即崩。`ato dev test` 入口验证可用。
- [x] **T1. v10 语料**：`v10/pcb/` 4 块板（test / layout_reuse_top /
  interf_u_unrouted 为 (v9,v10) 配对 + lvds_converter_dualclk 为**原生 KiCad 10
  保存**的 v10），v9 侧同步扩充 2 块真实板；来源与再生成命令见 `v10/README.md`
  （大板因体积排除，已记录——no silent caps）。
- [x] **T2. 全语料四道闸门**（`test_fileformats_corpus.py`）：parse / 幂等 /
  **无数据丢失**（schema 无关 sexp 树多重集 diff，`libs/test/sexp_tree.py`，输出
  逐字段丢失清单 = M3 的输入）/ 字节保真（仅 atopile 自产板——实测 KiCad 写出
  的文件有浮点格式与字段序差异，逐字节对齐是非目标，完整性审计改由"无数据丢失"
  闸门承担）。**新发现：v9 上 schema 也丢数据**（interf_u 实测丢 pad
  `pintype`/`pinfunction` 724 处、`sheetfile`、`aux_axis_origin`、title_block
  `rev`）——M3 范围含 v9 缺口，对应 xfail(strict) 已标。
- [x] **T3. 语义视图快照**：`libs/kicad/semantic_view.py`（B1 layout_ir 底座）+
  4 份快照入库。严格解析：悬空引用/引用与表名不一致/重复名一律 raise
  （`NetResolutionError`），杜绝静默误绑。
- [x] **T4. corrupter**（`test_net_binding_corruption.py`，24 用例）：保语义
  （表置换/全局一致重编号/节段重排→视图必须不变）+ 破语义（仅表内名字互换/
  悬空引用/zone 双键不一致/重复名→必须 raise 或视图变化）+ harness 自检
  （重排版本身视图中性）。**契约修正**：v10 没有 net 表（事实 2①），"合成编号==
  表序"不成立；钉死的两条底线改为 (i) 视图与引用顺序无关 (ii) 跨进程
  （PYTHONHASHSEED=0/1）dump 字节一致——均为 strict-xfail，M1 落地时转绿。
- [x] **T5. net 名 property 测试**（hypothesis 已是 dev 依赖，derandomize 保 CI
  确定）：150 生成用例 + 11 显式恶意用例（引号/反斜杠/换行/emoji/255 长名/
  形如 sexp 的名字）全过——zig writer 转义现状无 bug。
- [x] **T6. transformer net 单测**（8 用例）：编号空洞复用（{0,1,3}→2→4）、
  removed 编号不回收（生成器快照语义钉死）、remove_net 断开 pad/segment/via、
  rename_net 传播、get_net 按名（重编号后仍命中 + KeyError）。钉死两个 v9 怪癖
  待 M5 移除：remove_net 对 zone 要求 number+name 双匹配（stale name 的 zone
  残留连接）；`_get_net_number` 未知名静默→0（typo=静默断铜）。
- [x] **T7. layout_sync net 单测**（5 用例）：`_get_net_number` 按名/未知名→0、
  `_generate_net_map` 按 pad 拓扑映射（两侧编号故意错开）/忽略未连接 pad、
  `_sync_routes` 重映射+偏移+新 uuid+源板不动。
- [x] **T8. 变异自检（已执行并回滚，结果记录）**：
  ① 植入"按表位置绑定"→ **4 个自然语料快照测试 3 个照常通过**（密集有序表下
  bug 隐形——false-negative 风险实锤），corrupter 套件 4 failed + 8 errors 大面积
  红，唯一例外 layout_reuse_top 快照变红（atopile 板的 net 表有编号空洞，
  天然非密集——不可依赖的运气）；② 植入"密集计数式编号合成"→ T6 两用例红。
  结论：安全网对两类目标 bug 均有效，且 corrupter 是必需的（快照不够）。
- [x] **T9. CI 接线**：pytest.yml 增加 kicad-cli 10 安装步骤（PPA，
  continue-on-error——缺席时相关测试 skipif 降级，不红 CI）；EasyEDA 离线化改为
  **fixture 方案**：`test/common/resources/easyeda-cache/`（28K，.step 模型实测
  非构建必需已剔除），E2E fixture 自动播种到工程副本，构建零网络依赖。

**P0.1 期间新发现**（已并入上方事实清单）：事实 2① net 表整体消失（修正原
"去编号"认知）；事实 4b pyzig use-after-free 与 Path 缓存互锁。

## P0.2 — v10 方言迁移本体（2026-06-12 依 P0.1 结果重排为阶梯式）

**写方言目标 = v10（20260206）**；读侧兼容 v5–v10。**flag day**：仓库内全部
examples/fixtures/probe 工程的 `.kicad_pcb` 一次性升级提交。迁移完成后
"单向门"约束解除——KiCad 10 GUI 保存不再损坏管理中的板子（迁移的最大收益之一）。

**执行纪律（本次重排的原因——验收不许压到最后）**：
1. 每步自带两张清单：**本步转绿**（strict-xfail 棘轮：转绿没摘标记 → XPASS 报错，
   机制上防"忘了收敛"）与**本步保持绿**（全量既有套件）。两张清单写进 commit message。
2. **带着红灯不得进下一步**；没有现成开关可翻的步骤（S1/S2/S3）必须在同一
   commit 自带新测试。
3. 26 个 P0.1 开关的归属账（S0 后还会新增 oracle 开关）：
   S4 翻 19（parse×4 + 幂等×4 + 快照×4 + 跨方言×3 + T4 契约×4），
   S5 翻 7（no-data-loss×5 + 字节保真 v10×2），S6 反转 2 个钉死怪癖，S7 翻 oracle。

- [x] **S0. 验收测试先行**【✅ 2026-06-12，`test/libs/kicad/test_v10_acceptance.py`，
  3 个 strict-xfail：合成规则契约（S4 翻）、DRC oracle（S7 翻）、KiCad 重存语义
  回环（S4/S5 翻）；S6 反转清单写进模块 docstring】：
  - kicad-cli oracle 测试（strict-xfail + skipif 无 kicad-cli）：我们写出的 v10 产物
    `kicad-cli pcb drc` 跑通；KiCad 10 重存（upgrade --force 幂等）→ 我们回读语义
    视图不变——"KiCad 认不认"从第一天起就是看得见的红灯而不是最后的惊喜；
  - M0 合成编号规则契约单测（按名排序、"" 恒 0，strict-xfail）——规则先于实现钉死；
  - 列出 S6 要**反转**的两个 T6/T7 钉死测试（zone 双键、未知名→0），反转动作
    与对应迁移改动同 commit。
- [x] **S1. M3b：pyzig 所有权 + loads 缓存联修**【✅ 2026-06-12】：
  子对象包装持 owner 强引用链（child→parent→root，getset 三类 getter +
  MutableList 及其元素全覆盖）；`kicad.loads` Path 缓存按 (mtime_ns, size)
  指纹失效，`kicad.dumps(obj, path)` 回写缓存保持 dump→load 同对象语义。
  自带测试 `test/libs/kicad/test_pyzig_ownership.py`（6 用例），且经变异自检：
  撤掉 zig 修复重编译后 use-after-free 用例确实变红。
  保持绿实测：libs/kicad+exporters 95 通过、core graph/zig 85 通过、E2E 4/4。
- [x] **S2. M4a：version 守卫**【✅ 2026-06-12】：`kicad.loads` 对 PcbFile 按
  `PCB_MAX_SUPPORTED_VERSION`（现 20241229，S4 提至 20260206）前置检查，
  超限抛 `kicad.UnsupportedKicadVersion`（含双版本号 + 勿 upgrade/勿新 GUI 重存
  指引 + 文件名）。自带 `test/libs/kicad/test_version_guard.py`（7 用例，
  含"v10 语料必须报守卫错而非裸解析错"的口径测试）；corpus v10 xfail reason
  已改指守卫。
- [x] **S3. M2：tenting 族嵌套化**【✅ 2026-06-12】：统一 `Tenting{front,back,none}`
  模型（setup/pad/via 三处共用，替代 E_tenting/PadTenting/ViaTenting）；读侧
  双形状免改（裸 token 走 bare-symbol-bool 路径、嵌套走 kv 路径）；写侧新增
  `SexpField.dual_bool`/`.v9_only` 标记 + `structure.write_dialect` 全局
  （`PcbFile.dumps` 按 `version >= 20250000` 选方言，defer 复位）——v9 写裸
  token、v10 写 `(front yes)(back no)` 嵌套、pad 级 "none" 不进 v10。
  自带 `test/libs/kicad/test_tenting_dialect.py`（10 用例）；v9 corpus 字节
  保真不回归实测通过。S4 复用同一套方言基建（v9_only 即 net 表/net_name 的
  抑制机制）。
- [x] **S4. M0+M1：net 模型重构**【✅ 2026-06-12】。实现形态（与原计划的差异）：
  - Python 可见模型**不变**（`segment.net` 仍 int、`pad.net` 仍 `Net{number,name}`）
    ——编号 = 进程内句柄由 zig 层兑现，21 个 Python 消费方在 S6 前零改动；
  - 读侧：`PcbFile.loads` 对 v10（version sniff ≥20250000）预扫描全部
    `(net "名")` 引用 → 按名排序合成编号（"" 恒 0、1..n 稠密）→ decode 时
    `net_ref` 标记字段经 `structure.read_net_names` 把名解析回编号 →
    `pcb.nets` 合成表落地；v9 路径零开销不变；
  - 写侧：`net_ref` 字段在 v10 方言写解析名（永不写编号）、net 0 整条省略
    （keepout zone）；net 表/`net_name`/pad net 名第二位 = `v9_only` 抑制；
  - 连带修复：`Property.at/layer` 可选（KiCad 10 写裸 `(property ki_fp_filters …)`，
    曾挡住 lvds 解析）；v9 layout_reuse fixture 补回悬空的 manual segment 并
    重新生成 v10 配对（原 fixture 的 group 成员悬空，upgrade 时被 KiCad 清掉）。
  *已转绿（21 个）*：corpus parse×4 + 幂等×4 + 语义快照×4（v10 REGEN，test
  板 v9/v10 快照逐字节相同）+ 跨方言等价×3 + T4 v10 契约×4 + S0 合成规则
  契约 + S0 KiCad 重存语义回环 oracle（提前于预期转绿，棘轮捕获 XPASS 后摘标）。
  *保持绿*：libs/kicad+exporters 133 通过 + 8 xfail（= S5 的 7 + S7 的 DRC
  oracle，账目精确）；core graph/zig 85；E2E ×4。
  *T8 变异自检（已做）*：植入"按出现序编号"（去排序+线性查找）→ **唯一**报警的
  是 S0 合成规则契约（语义视图按名解析，对一致重编号天然失明）——印证"规则先于
  实现钉死"的必要性；恢复后全绿。
- [x] **S5. M3：静默丢弃键补全**（断裂点③，file-by-file burndown）
  **【✅ 2026-06-13 完成】** S5a 提交 `9fd47a51`（未知键响亮化 + 4 测试，
  机械化清单与 data_loss 审计零误差吻合）；S5b 提交本次：schema 补全后
  全语料 unknown=0 / loss=0，7 个 strict-xfail 翻绿（5 no-data-loss +
  2 字节保真 v10），v10 fixture 由我们的 writer 重生（DRC rc=0、version
  20260206），`test/libs/kicad/ test/exporters/pcb/` = 144 passed + 1 xfailed
  （仅剩 S7 DRC oracle）。norm_atom 增「引号串≡裸符号」归一化。
  - **S5a 未知键响亮化（2026-06-13 新增，机制先行，no-silent-failure 保底）**：
    decode 匹配循环目前对不认识的键直接落空（structure.zig 键匹配 inline for
    无 else 分支）——加 unmatched 检测，未匹配键按（结构名, 键名）收进诊断
    sink（与 `read_net_names` 同一全局模式）；Python 侧 loads 后可查询，
    默认 logger.warning，strict 模式抛错。一石二鸟：① 任何未建模构造
    （含未来 KiCad 版本新键）从静默丢失降级为响亮警告——schema 没补到的
    部分用户至少能看见；② 对全语料跑一遍即**机械化产出 S5b 的丢失清单**
    （替代手工盘点，手工盘点对"语料里没有的构造"天然失明）。
    自带测试：注入未知键 → 断言警告/strict 抛错；"全 v9+v10 语料零警告"
    即 S5b 的完成判据。
  - **S5b 清单 burndown**：以 S5a 机械化清单驱动（已知 v10 侧：plot 参数、
    `duplicate_pad_numbers_are_jumpers`、`locked` 移除、层 id 重编；v9 侧：
    pad `pintype`/`pinfunction`、`sheetfile`、`aux_axis_origin`、`rev`）。
    范围决策（2026-06-13，用户拍板=最小集）：**保真集** = 既有 7 开关清单
    + group + rule area placement（room 工作流自有依赖）；**警告集** =
    teardrop / generated 蛇形等长 / via padstack——仅 S5a 响亮警告，
    schema v10 形变补全见 P1+ 条目「GUI 高级布线构造保真」；GUI 演示
    脚本只用普通走线+过孔+组，回避警告集操作。
  *本步转绿（7 个，可逐文件分 commit）*：no-data-loss×5（v9-interf_u 可先行，
  与 v10 无依赖）+ 字节保真 v10×2——**注意**：v10 fixture 现为 kicad-cli 写出，
  逐字节对齐其排版是非目标；转绿方式 = schema 补全后用**我们的 writer 重新生成**
  这两份 v10 fixture（oracle 确认 KiCad 10 读取无损后替换），raw==dump 即自然成立。
  *保持绿*：全量(此时 corpus 应零 xfail)。
- [x] **S6. M5：Python 消费方迁移**【✅ 2026-06-13】（三个子步逐个全绿后进下一个）：
  - S6a transformer：remove_net 改按 net 句柄单键匹配 zone（net_name 是
    v9-only 冗余字段，v10 缺席会让旧双键漏断 zone）→ 反转
    `test_remove_net_skips_zone_with_stale_name` →
    `test_remove_net_disconnects_zone_despite_stale_name`，增 absent-net_name
    用例。提交 `02bd5998`。
  - S6b layout_sync：`_get_net_number` 未知名从静默→0 改 **raise KeyError**
    （两个调用点均 `in net_map` 守卫，名必存在；""→0 仍走正常路径）→ 反转
    `test_get_net_number_silently_maps_unknown_to_zero` →
    `test_get_net_number_raises_on_unknown`，钉死 ""→0 边界。提交 `0d3a516b`。
  - S6c pcb_manager：`_extract_zone` 增 `net_names_by_number` 回退（v10 zone
    无 net_name 时由合成表补标签，双方言一致）；既有 layout_server 35 测试
    保持绿。**GUI 编辑回环验收门提前落地**（计划允许）：
    `test/layout_server/test_gui_edit_roundtrip.py`——v10 fixture 经受管
    move→save→重读：① 方言保持 20260206；② 编辑持久；③ groups/zones/nets/
    其余 footprint/每个 pad 的 net 全部无损；④ 分组成员保持；⑤ kicad-cli DRC
    跑通；⑥ 警告集构造（teardrops 内未建模键）响亮上报而非静默丢失。
    4 测试全绿（含 DRC，本机有 kicad-cli）。
  *保持绿实测*：test/layout_server + test/exporters/pcb + test/libs/kicad
  = 184 passed + 1 skipped（node drag 闭环）+ 1 xfailed（仅剩 S7 DRC oracle）；
  v8/v9 fixture 干净。E2E/examples 取件 403 的真因见下「EasyEDA 取件韧性」
  条（**非简单限流**，是 CloudFront WAF 的 UA 拒绝名单 + 速率规则两条），非本步代码问题。
  **GUI 演示门已绿**（S7 终验前即可演示 fidelity-set 手工编辑全流程）。
- [ ] **S7. M4b+M6：版本号 bump + flag day + 终验**：写出 `(version 20260206)`；
  examples/fixtures/probe 一次性升级提交（A1 增量稳态依赖回读，升级后跑
  build→build→diff 确认 v10 稳态成立）；E2E ×4 在 v10 上绿；
  S0 的 oracle 开关全部转绿；BOM/制造产物/DRC smoke；KiCadRoutingTools 不动
  （事实 13）；改写 CLAUDE.md/KicadDecisions.md"单向门"约束为
  "已迁移，v9 只读兼容"。
  - **GUI 编辑回环验收（2026-06-13 新增，演示前置门）**：模拟编辑回环单测
    ——向 v10 fixture 注入手工布线构造集（= S5b 保真集：segment/arc/via/
    zone/locked 标记/手工命名 group/rule area placement）→ `kicad.loads`
    → 受管重写（pcb_manager 同步路径）→ dump → 语义视图不变 + 注入对象
    逐一无损 + kicad-cli DRC 跑通；另注入警告集构造（teardrop/generated/
    padstack 的 v10 形）断言 S5a **响亮警告而非静默丢失**。验证的是
    "KiCad **改了**板子后 rebuild 不吃改动"（重存 oracle 只验"原样重存"，
    编辑≠重存）。测试可在 S6c 提前落地：dumps 方言随文件 version（S3
    机制），v10 fixture 重写已是 v10，不依赖 flag day。
    **此门未绿不演示 GUI 全流程**；演示脚本回避警告集操作。

- [x] **D1. EasyEDA 取件韧性（end-to-end demo 可用性需求，S7 之后）**
  【✅ 2026-06-13 实现，提交 `f5b707fe`（实现）+ `2a5f5777`（诊断/立项）】
  `ResilientEasyedaApi`（drop-in 子类，4 处调用点全换）：① 注入放行 UA
  `curl/8.5.0`（确定性 403→200，**实测真网** C125116→LTST-S326KGJRKT 经 Python 取到）；
  ② 全 jitter 指数退避重试（403/HTML 体视为可重试，success:false 不重试、
  耗尽返回 {} 而非 JSONDecodeError）；③ warm build 仍走 1 天缓存零调用；
  ④ 回归测试 `test/libs/picker/test_easyeda_resilient.py`（打桩 transport，不打真网，
  5 绿）+ 全 picker 套件 36 绿。不动 vendored easyeda2kicad（仓外、reinstall 易失）。
  （原始立项记录如下，保留为诊断档案。）
  （2026-06-13 立项，源于"EasyEDA 403 真相"实测纠正，详见 `/kicad_wksp/CLAUDE.md`）：
  end-to-end demo（`ato build` 冷启动取件 → 选型 → 布局 → 布线 → 制造产物）
  要能在本沙盒/CI 跑通，必须先解决取件被 CloudFront WAF 挡的两条独立原因——
  **旧记"EasyEDA 限流 403"是错的/不完整**，真相是：
  1. **UA 拒绝名单（主因，确定性）**：`easyeda2kicad` 库硬编码
     `User-Agent: easyeda2kicad v<版本>`（`easyeda_api.py:24`），与
     `python-requests/*`、`Mozilla/*` 一样在 WAF 黑名单里，**任何速率下都 403**；
     `curl/*`、node 默认 UA 被放行。实测同一时刻 `requests`+`curl/8.5.0` UA → 200 真 JSON。
  2. **按 IP 速率/信誉规则（次因）**：取件路径**完全无自我限速**——
     `get_raw`（lcsc.py:491）逐件**串行**取（无并发、无间隔、无退避、无重试、
     无超时、无 jitter、无 semaphore；easyeda2kicad 原始 GET 同样裸奔），
     唯一缓解是 1 天缓存（`build/cache/parts/easyeda`，warm build 零调用）。
     冷启动会把 N 个未缓存件**背靠背连发**，触发 CloudFront 速率规则后
     即便放行 UA 也短时全 403，响应体是 `Request blocked / too much traffic`
     的 HTML（非 JSON → `r.json()` 抛 `Expecting value: line 1 column 1`，
     正是历史误判为"空 JSON/限流"的症状）。
  *验收*：① easyeda2kicad 取件注入被放行 UA（curl/node 形），把确定性 403 转 200；
  ② 取件包一层 **随机指数退避 + 重试**（对 403 与非 JSON "too much traffic" 体均视为
  可重试，带 jitter、上限、超时），冷启动不再因突发被 WAF 拒；③ warm build 仍走缓存零调用；
  ④ 回归测试钉死 UA 与退避（用打桩的 transport/响应，不打真网）。
  **此条不阻塞 S7 代码**（S7 方言验收可用已缓存件/离线跑），但
  **是 S7 之后 demo 端到端可用性的前置**。

**工作量**：周级（S4+S6 为主）。**回滚策略**：P0.1 底座全部以名字为基准，对
v9/v10 双方言对称——迁移分支若需中止，底座资产无一作废；S1/S2 与方言无关，
无论如何保留。

### 调试日志 BUG-1：测试运行污染版本控制 fixture v8/pcb/test.kicad_pcb

**纪律**：本节为"先记录后行动"的调试日志。每个假设标 `[OPEN]`/`[REJECTED]`/
`[CONFIRMED]`，附可复现证据。禁止凭记忆下结论。

**状态（2026-06-13）**：根因 `[CONFIRMED]` = H3。已隔离复现并定位写回点，
修复 = test_server.py `app` fixture 改用临时副本（见下）。

**现象**：
- 跑 `pytest test/ -k "pcb or transform or layout or footprint or fileformat"`
  后，工作区 `test/common/resources/fileformats/kicad/v8/pcb/test.kicad_pcb`
  被改（`git diff --stat` = 231 ins / 221 del）。
- 具体改动：`logos:faebryk_logo` footprint 从 `(layer F.Cu)(at 10 20 0)`
  → `(layer B.Cu)(at 20 40 90)`，各 property 加 `(justify mirror)`、
  `(at .. 90)`——即一次 **flip（翻面）操作的产物**。
- 连带 `test_semantic_snapshot[v8-test]` 失败（视图漂移出已提交快照）。
- 文件 mtime 落在该广选 run 期间（实测 12:21:22）。
- 已用 `git checkout HEAD -- <v8>` 还原；还原后当前代码解析正确、
  byte_fidelity=True、8/8 快照通过——确认 S5b schema 改动**无辜**（见 H1）。

**复现命令**：
```
git status --short <v8>            # 干净
pytest test/ -k "pcb or transform or layout or footprint or fileformat" -q
git status --short <v8>            # 显示 M
```

**假设表**：
- **H1 `[REJECTED]`** S5b schema 改动破坏 v8 解析。
  证据：还原 fixture 后，当前代码读 logo at=[10,20,0]/F.Cu 正确、
  byte_fidelity=True、`test_semantic_snapshot` 8/8 通过。失败是脏 fixture
  导致，非解析器。
- **H2 `[REJECTED]`** `test_pcb_manager.py::manager_v8` fixture 直接
  `load(源路径)` + 某测试 `save()` 写回。
  证据：单跑 `test/layout_server/test_pcb_manager.py` → 25 passed，
  跑后 `git status` v8 **干净**。且 `PcbManager.load()` 走
  `kicad.loads(PcbFile, text)`（文本载入，不进 Path 缓存）、
  `dispatch_action` 不落盘、唯一 `save()`（test_save_roundtrip）用
  `shutil.copy2` 临时副本。
- **H3 `[CONFIRMED]`** `test/layout_server/test_server.py` 用
  `create_app(TEST_PCB=源路径)`，`POST /api/execute-action` 的
  flip/rotate/move 端点在 dispatch 后调 `service.save_and_broadcast()`
  → `PcbManager.save()` → `kicad.dumps(_pcb_file, _path)` 写回源路径。
  确认证据：(a) `server.py:62` execute-action handler 在 flip/rotate/move
  后调 `await service.save_and_broadcast(...)`，`PcbManager.save()`
  写 `self._path`，而 `app` fixture 把 `_path` 设成 v8 **源路径**；
  (b) **隔离复现**：v8 还原干净 → 单跑 `pytest test/layout_server/test_server.py`
  （10 passed）→ `git status` 显示 v8 `M`；
  diff 正是 logo flip 到 B.Cu + at 20 40 90 + justify mirror，
  与三个 execute-action 端点（rotate 90 / move 10 20 / flip）逐项吻合。
- **H1/H2 不再需要 H4**：H3 已 CONFIRMED 且独立复现，H4 关闭为不需要。
- **同类潜伏（已记录，未触发）** `test_frontend_drag_closed_loop.py:93`
  把 server 直接架在源 `examples/esp32_minimal/.../esp32_minimal.kicad_pcb`
  上做 drag 闭环（会 save 回源）。本沙盒无 node/puppeteer 故 skip，未污染；
  属同一 bug 类（server 指向源路径）。修复一并放 S6c（layout_server 测试隔离）：
  改用 `tmp_path` 副本启动 server。

**修复（已定，根因 CONFIRMED）**：`test_server.py` 的 `app` fixture 把
源 `TEST_PCB` 用 `shutil.copy2` 拷到 `tmp_path` 临时副本再传给
`create_app`（对齐 `test_pcb_manager.py::test_save_roundtrip` 的模式），
使 execute-action 的 save() 落到临时副本而非版本控制 fixture。
属正当的测试隔离修复，非范围蔓延（save()→_path 的写回路径本身正确，
bug 仅在于测试把 _path 指向源 fixture）。

**与 P0.2 的关联**：本 bug 与 S5b schema 无关（H1 已否），但
①会反复污染 v8 造成 S5b/快照测试假红；②S6c 正要迁移 layout_server
消费方；③save()→源路径的写回正是 S7 GUI 编辑回环要验的真实路径
（测试把它指向源 fixture 而非工作副本才是 bug）。

### P1+ 候选 — GUI 高级布线构造保真（2026-06-13 立项，源于 S5b 范围决策）

teardrop / generated 蛇形等长 / via padstack 的 v10 子键形变补全
（schema 已有 KiCad 9 形模型，缺 v10 验证与形变适配）。P0.2 内仅由
S5a 响亮警告兜底，GUI 演示脚本回避这三类操作。触发条件：演示反馈
需要、或用户板子警告频发。验收 = 把这三类构造从 S7 回环测试的
"警告集"挪进"保真集"。

---

## B. P1 — layout_ir.json 导出（紧随 P0.2；提取器已由 T3 提前交付）

- [ ] **B1. 新构建步骤 `layout-ir`**
  `@muster.register("layout-ir", dependencies=[update_pcb])`（`build_steps.py`）。
  遍历实例图 + footprint 映射，输出 `build/builds/<target>/layout_ir.json`：
  模块地址 → {type, group_uuid, components{地址 → 位号/footprint_uuid/pads{net, xy, layer}},
  nets}；net → {kicad_net, pads}。
  地址来源：`Node.get_full_name(include_uuid=False)`（`faebryk/core/node.py:1520-1556`，
  已确认稳定）。**实现基底 = T3 的语义视图提取器**（一鱼两吃）。
  验收：PCB Layout SKILL 仅凭 layout_ir.json 即可解析位号/net/pad 坐标，无需读 .kicad_pcb。

- [ ] **B2. schema 固化与版本号**
  layout_ir.json 增加 `version` 字段 + JSON Schema 文件入库（诊断层与布线执行器都依赖它）。

## C. P1 — layout.yaml 加载 + KiCad 10 对象生成

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
  - C3b（正式）：扩展 Zig sexp schema——范围已缩小（事实 14），只缺 `source_type`
    的 `group` 枚举值与 `(group "...")` 子句；若 P0.2 已完成，直接在 v10 schema 上做。
  约束：禁止自定义 token（事实 3）；引导/标记几何只放 User.x 层（事实 9）；
  写铜层的几何必须挂真实 net（事实 9）。

- [ ] **C4. `ato layout` CLI 子命令**
  `cli/cli.py` 注册：`ato layout resolve`（产出 layout_ir）/ `ato layout emit`（产出 rule areas）。
  验收（第一阶段整体）：`examples/layout_reuse`（3 个重复 Sub 模块）上
  `ato build` 两次字节级一致；DRC JSON 正常运行。
  注意进程内连续读写的 loads 缓存地雷（事实 4，M5 处理后解除）。

- [ ] **C5.（后置）component class 支持**
  需同时写 `.kicad_pro`（类定义在工程级）。group 来源已够用，仅当需要基于 class 的
  DRC 规则时再做。

## D. P2 — 诊断闭环（本仓库侧）

- [ ] **D1. `ato route --plan layout.yaml --stage <name>`**
  调用 KiCadRoutingTools fork 的计划执行器（§E1），通过 Python API 进程内调用
  （`batch_route(pcb_data=..., return_results=True)`），不走子进程解析标准输出。

- [ ] **D2. `ato diagnose` → diagnostics.json**
  聚合三路输入并映射回文本源：
  1. 执行器的 route_report.json（逐 net 线段/过孔/迭代数/失败 + 阻塞分析）；
  2. `kicad-cli pcb drc --format json`（无结构化 net 字段，用 items[].uuid 经
     layout_ir 反查，事实 11）；
  3. rule-area 多边形命中测试 → 给违例标注所属 room。
  输出字段：stage/room/ato_path/constraint（指向 layout.yaml 条目的 JSON-pointer）/
  reason/blocking_nets/failed_endpoints/suggestions。
  风格基线：kicad-happy 项目的检查结果 schema（rule_id/severity/report_context/confidence）。

## E. KiCadRoutingTools fork（仓库：KiCadRoutingTools）

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
- ❌ **盲随机 fuzz（语法级）**：迁移期的危险类 bug 是"合法文件被静默误绑"，不是
  "非法输入导致崩溃"；随机语法垃圾只锻炼错误路径，性价比低。该风险类由 T4
  corrupter（结构化、确定性、可入 CI）+ T5 property 测试覆盖。zig 解析器的
  honggfuzz/AFL 留作远期健壮性候选，不在 P0.1。
- ❌ 生成 KiCad 原生 design block 库（atopile 的 `atopile_subaddresses` 机制已覆盖
  布局复用；group 的 `lib_id` 链接字段存在，留作第三阶段可选项）。
- ❌ 调用 GUI 的 Repeat Layout / Generate Placement Rule Areas（无法脱离界面运行）。
- ⏸ clean-checkout 可重现性（地址派生 UUID）：半径大（碰撞处理、KiCad 唯一性预期），
  增量稳态已满足工作流需要（事实 6）。
- ⏸ 向 KiCad 上游提 DRC JSON 增强补丁（结构化 net / rule-area 上下文字段）——
  外部映射可用；第二阶段跑通后评估。
- ⏸ 修改 `.ato` 语法——布局语义全部走 sidecar 文件，不动语言本体。

## 遗留问题

1. C3a（后处理注入）→ C3b（Zig schema 扩展）的切换时点（范围已缩小，见事实 14；
   P0.2 后直接在 v10 schema 上做 C3b 可能跳过 C3a）。
2. net 名漂移程度（自动编号 `unnamed[N]` 在模块改动下的稳定性，关联事实 8）——
   第一阶段实测后决定 layout.yaml 是否完全禁止裸 net 名引用。
   **注意与 P0.2 的耦合**：v10 后 net 名是文件内唯一键，名字漂移从"显示问题"升级为
   "几何归属漂移"——M7 验收需加一条：稳态重建下全部 net 名字节级稳定。
3. `gen_uuid` 长名字 bug（事实 7）的修复时点——生成长地址组（C3）前必须修。
4. 是否向 KiCad 上游提 DRC JSON 增强补丁（见 §F）。
