# Code Review Report

## Scope

### Scope Resolution

- Review branch: `feat/checkpoint-dev-time-dialog-width`
- Review ref: `refs/review/feat/checkpoint-dev-time-dialog-width`
- Review ref existed: 是（`git rev-parse --verify` → `2b449acd90612bec352f505d26039300fe29062c`）
- Starting point: `2b449ac`（review ref 指向点，非 merge-base）
- Review range: `2b449ac..HEAD`（`HEAD` = `1a39643`），本区间仅 1 个提交 `1a39643`
- First review: 否（本分支第 2 次审查；首次范围 `master...2b449ac`）
- Uncommitted changes included: 否（`git status --porcelain` 为空，工作区干净）
- Previous persistent report: `docs/agent-tracking/feat-checkpoint-dev-time-dialog-width/code-review/review-report-1-2b449ac-APPROVE.md`

### Scope Determination

本地 review ref 存在且指向 `2b449ac`，判定为增量审查，范围取 `2b449ac..1a39643`。该区间改动量为单一提交、4 个文件、+121 行，无需回退到全量分支 diff（`master...HEAD`）。工作区无未提交改动，无 staged 改动，故无追加 diff。上一轮持久化报告已读取：其 Comment Tracking 为空（无 open 条目），Address Findings 仅 1 条且用户决议为 `ACCEPT`，本轮不再重复上报该历史问题。

## Confirmed Scope

```
 .../code-review/review-report-1-2b449ac-APPROVE.md | 105 +++++++++++++++++++++
 web/frontend/src/App.css                           |  11 +++
 web/frontend/src/App.tsx                           |   1 +
 web/frontend/src/components/CheckpointPanel.tsx    |   4 +
 4 files changed, 121 insertions(+)
```

其中第 1 个文件（上一轮 review 报告的归档）为工作流产物，与本次代码改动无关；其进入提交与仓库既有约定一致（对照 `f577653`，其余分支的 review 报告同样随特性提交一并入库），不构成问题。实际受审代码为后 3 个文件。

## Review Report

[LOW] 曲线名截断丢失的是豆名尾段，且移动端无任何途径看到全名
**File**: web/frontend/src/App.css:319（`.checkpoint-name`）、web/frontend/src/components/CheckpointPanel.tsx:46
**Issue**: 曲线名形如 `[YYYY-MM-DD HH:MM <豆名>]`（`data/sqlite/session_repo.py:206` 的 `get_display_name` 固定前缀为 19 字符），前缀本身已占掉标题栏大部分可用于收缩的宽度，可留给豆名的仅剩一小段，而 `text-overflow: ellipsis` 截断的是**尾部**——恰好是区分同名豆不同批次的后缀（如 `G1` / `G2`）。同时该 span 位于 `.checkpoint-header` 之内，而 header 设有 `user-select: none`（App.css:301），移动端既无 hover 可触发 `title`，也无法长按选中文本，因此一旦截断，全名在该设备上不可恢复。实现在提交说明中已说明"未加 title（手机无 hover）"，该取舍对移动端成立，但未覆盖"截断后信息不可恢复"这一后果。
**Impact Scenario**: 375px 宽手机上 `.app`（`max-width: 480px`、`padding: 12px`）内容宽约 351px，`.checkpoint-panel`（`padding: 8px 12px`）内 header 可用约 327px；扣掉箭头（约 14px）、`Checkpoints` 标题（16px 粗体，约 97px）与两个 8px 间距后，曲线名可用约 200px。13px 下固定前缀 `[2026-08-22 15:25 ` 约占 120–136px，仅剩约 65–80px 给豆名，即约 5–6 个中文字。用户加载豆名为「埃塞俄比亚耶加雪菲日晒G2」的理想曲线时，手机端标题栏显示为 `[2026-08-22 15:25 埃塞俄…`，`G2` 这一区分后缀被吃掉；与已被覆盖的短名场景（用户实测用的 `[2026-08-22 15:25 G2]` 完全放得下）不同，长中文豆名路径未被验证过。若本项目实际豆名普遍为 6 字以上中文，本条的严重度应上调为 MEDIUM。
**Fix**: 二选一（均不改变"同行、小一号、更淡"的既定要求）：
  1. 至少补 `title` 属性，桌面端可悬停看全名（移动端无副作用）；
  2. 若移动端必须能看到全名，则把截断位置从尾部挪到前缀——例如在 `.checkpoint-name` 上用 `direction: rtl` + `text-align: left` 让省略号出现在日期侧，或对前缀用固定宽度 + 自身截断，把宽度优先让给豆名。

  # BAD
  ```tsx
  {curveName !== '' && <span className="checkpoint-name">{curveName}</span>}
  ```

  # GOOD（方案 1，最小改动）
  ```tsx
  {curveName !== '' && (
    <span className="checkpoint-name" title={curveName}>{curveName}</span>
  )}
  ```

### 补充：本次审查要求重点核查的 5 项结论

1. **`cachedCurveName` 与当前列表是否严格绑定** —— 成立，未发现错配窗口。
   全仓 `setCheckpoints` 仅 3 处调用（App.tsx:192、201、226），每一处都与 `setCachedCurveName` 成对出现，且值来自同一次事件：
   - 192–193 行：服务端无曲线（`!name`）→ 同时清空列表与名字；
   - 201–202 行：拉取成功 → 同时写入 `cps` 与 `name`（同一闭包捕获的同一个 `name`）；
   - 226–227 行：localStorage 恢复 → 同时取 `s.checkpoints` 与 `s.cachedCurveName ?? ''`（二者由同一次 `writeSession` 原子写入，见 243–281 行）。
   因此不存在"名字变了、列表没变"或反向的组合。
   各分支路径核对：
   - **roasting 冻结**：App.tsx:188 在 `status.state === 'roasting'` 时直接 return，既不重拉列表也不改名字，二者同步冻结。期间桌面端切换理想曲线（`status.curve_name` 已变为新值）时，手机端继续显示旧列表 + 旧名字，二者仍互相一致——这正是选用 `cachedCurveName` 而非 `status.curve_name` 的价值所在，若用后者会在整个烘焙期间显示新名字配旧列表。烘焙结束后（state 变 idle）该 effect 恢复执行，因 `name !== cachedCurveName` 触发重拉并同时更新两者。论断成立。
   - **曲线切换/清空**：走 189–195 与 196–203 两条路径，均成对更新。
   - **拉取失败**：200 行显式"保持旧缓存与缓存名"，二者一起保留，不会单边更新。（另核对 `api.ts:127` 的 `getCheckpoints` 内部 `try/catch` 全覆盖、永不 reject，故 `checkpointsFetchingRef` 不会因异常永久卡死，196 行的在途守卫不存在卡死风险。）
   - **localStorage 恢复**：仅 `status.state === 'roasting' && s != null && !s.ended` 时走恢复分支（App.tsx:212），此时恢复的是一对；旧版 localStorage 缺 `cachedCurveName` 字段时 `?? ''` 退化为"有列表无名字"（列表照常渲染、名字不渲染），属优雅降级而非错配。
   - **effect 执行顺序**：187 行的拉取 effect 声明在 208 行的恢复 effect 之前，两者可能在同一次 commit 中先后 setState；但恢复分支只在 roasting 触发，而拉取 effect 在 roasting 下已提前 return，二者不会在同一次提交中同时写入，无竞态。

2. **`curveName` 设为必填 prop** —— 判断正确，无需改为可选。
   `CheckpointPanel` 全仓仅 1 处调用（App.tsx:560），`web/frontend` 下无任何测试文件（`*.test.*` / `*.spec.*` 均不存在），故不存在"未来调用方/测试被迫补参"的现实成本。反之为可选会引入 `curveName?: string` 带来的 `undefined` 分支，或需要默认值 `''`，都会稀释"空串 = 无"这一单一表示。必填 + 空串哨兵与组件内既有约定一致（对照 72/90 行 `cp.value !== ''`）。

3. **flex 收缩方案** —— 正确。
   `.checkpoint-header` 为 `display: flex` 且无 `flex-wrap`（单行）；作为 flex item，`<span>` 会被 blockify 为块级，`overflow: hidden` + `text-overflow: ellipsis` 得以生效；`min-width: 0` 是必需项（flex item 默认 `min-width: auto`，配合 `white-space: nowrap` 会拒绝收缩到内容宽度以下，不加则 ellipsis 永不出现）。两个兄弟节点上的 `flex-shrink: 0` 使收缩全部落在曲线名上，语义正确。
   **点击区域不受影响**：header 高度由最高的子项决定（16px 标题），13px 的名字不会撑高也不会压缩 header，`.checkpoint-header:active` 的整行点按范围与改动前一致；名字本身也在 header 内，点按它会照常触发展开/折叠。
   **极小宽度表现**：`curveName === ''` 时该 span 整体不渲染，不会留下一个 8px 的 `gap` 空洞（若用"始终渲染空文本"的写法才会有此问题，此处写法正确）。仅在 header 可用宽度小于"箭头 + 标题"（约 120px，对应视口 < 160px）时才会溢出——实际不可达（`.app` 桌面端 `max-width: 480px`，手机最小视口 320px）。`gap: 8px` 下名称被压到极小宽度时表现为 `[202…`，属可接受的降级，不再单独计为问题。

4. **13px + `--text-dim` 的可读性/触控** —— 达到要求，不是问题。
   `--text-dim: #9a9aa8`（index.css:8）在 `--panel: #1f1f27`（index.css:4）上的对比度约为 **5.9:1**，超过 WCAG AA 正文阈值 4.5:1，也满足大字号 3:1。13px 与该变量组合在项目中已有先例：`.temp-label { font-size: 13px; color: var(--text-dim); }`（App.css:178–181），本提交与既有视觉体系一致。触控方面，该文字是纯展示、非独立触控目标，实际点按目标是整个 header 行（含 8px 上下内边距），13px 不影响触控命中；页面其余输入控件也未因本次改动低于 16px（iOS 聚焦缩放阈值），无新增影响。

5. **是否加 `title`** —— 取舍方向合理（移动端确无 hover），但后果未完全盘清；见上方 LOW。移动端不可恢复全名的另一条通道（长按选中复制）也因 `.checkpoint-header { user-select: none }`（App.css:301）而关闭，建议至少补 `title`（桌面端零成本收益、移动端零副作用）。

### 补充：未覆盖路径（非缺陷，供确认）

`midRoastReload`（`status.state === 'roasting' && t0 == null`，App.tsx:474）状态下，`checkpoints` 恒为 `null`（187 行 effect 在 roasting 下提前 return，localStorage 恢复分支又要求 `s != null`，而 `s != null` 时 `t0` 会被恢复、该状态不成立），故 CheckpointPanel 不渲染，曲线名也一并不可见。即：**烘焙进行中，在一台没有本地会话的终端上打开页面（第二台手机 / 清了浏览器数据 / 换浏览器），整个烘焙期间看不到曲线名**，页面只显示"烘焙进程由其他终端控制"横幅（App.tsx:517）。该限制源于 checkpoint 面板本身在 roasting 期不拉取列表（为避免"新名字配旧列表"，属有意设计，非本次提交引入），本提交只是继承了它；由于用户已用真实曲线端到端确认过主路径（先进 idle、后入豆），此处不作为 finding，仅提示确认：若"第二台手机中途打开"是实际使用场景，则该需求在主路径之外仍未满足。

### 注释核查（本轮 diff 范围内）

- `CheckpointPanel.tsx:11` `/** 当前列表对应的理想曲线名；空串 = 无（不渲染） */` —— 与 46 行实现、与 `session.ts:32` 的 `cachedCurveName: string` 类型一致，说明语义而非复述代码，无问题。
- `App.css:318` `/* 曲线名：紧跟标题，窄屏下省略而非撑破布局 */` —— 与 319–326 行实现一致，无问题。
- 未发现与 HEAD 状态不符的注释、过期 TODO/FIXME 或必要性冗余注释。

### 已验证但无需上报的项

- 必填 prop 变更未波及其它调用点：`git grep CheckpointPanel` 仅命中 `App.tsx:24/560` 与组件自身；无测试文件、无 mock/fixture。
- `/api/status` 确实下发 `curve_name`：`ui/camera_realtime_window.py:1291` 提供，`web/backend/server.py:44–55` 为整体 dict 透传（不白名单过滤字段），`web/frontend/src/api.ts:17` 类型已声明。功能前提成立。（附带发现、不在本次 diff 范围内故不计为问题：`server.py:46` 的 docstring 字段清单未同步 `curve_name`。）
- 后续路径 `ui/camera_realtime_window.py:587`（`.slog` 加载）下 `ideal_curve_name` 为文件 basename，可能比 `get_display_name` 的格式更长，会加剧上方 LOW 的截断，但机制相同，不重复计数。
- 上一轮报告 Address Findings 第 1 条（`dev_seconds` 为负 → 前端显示 `(00:00)`）用户决议为 `ACCEPT`，且该代码路径不在本次区间内，按规则不再上报；经核对 `core/checkpoint.py` 本轮无改动，决议仍然适用。

## Comment Tracking

- 无

（本轮新增/变更注释均已核查，未发现必要性问题、与 HEAD 状态不符的问题或过期 TODO/FIXME。上一轮 Comment Tracking 为空，无 open 条目需回溯。）

## Review Summary

| Severity | Count | Status |
|----------|-------|--------|
| CRITICAL | 0     | pass   |
| HIGH     | 0     | pass   |
| MEDIUM   | 0     | pass   |
| LOW      | 1     | note   |

Verdict: APPROVE

---
⚠️ NOTICE TO CALLING AGENT: Do NOT apply any fixes from this review automatically.
Present this review to the user first and wait for explicit instruction on which
issues to address. The user must approve changes before any code is modified.
Present the full review output verbatim to the user. Do NOT summarize, 
compress, or reformat any findings. Paste the complete text as-is.
---

## Address Findings

| Issue # | Issue Short Description | User Decision | Details |
| ------- | ----------------------- | ------------- | ------- |
| 1 | 曲线名尾段截断（丢豆名后缀）且移动端无恢复全名的途径 | ACCEPT | 用户决策：接受现状，不补 `title` 属性。改为调整 `session_repo.get_display_name` 的字段顺序为 `[豆名 日期 时间]`（豆名前置），使尾部截断仅损失日期/时间而非区分同批次的豆名后缀，与本条 Fix 方案 2「把宽度优先让给豆名」的目标一致。移动端仍无法恢复被截断的日期/时间全额，属可接受降级；`flex-shrink: 0` 的标题与箭头不受影响。 |
