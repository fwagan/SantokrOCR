# Code Review Report

## Scope

### Scope Resolution

- Review branch: `feat/checkpoint-dev-time-dialog-width`
- Review ref: `refs/review/feat/checkpoint-dev-time-dialog-width`
- Review ref existed: 否（`git rev-parse --verify` 返回 exit 128）
- Starting point: `git merge-base master HEAD` = `f577653d808683cbfb3c1fca4a1918bd03adbd88`
- Review range: `f577653...HEAD`（`master...HEAD`），本分支仅 1 个提交 `2b449ac`
- First review: 是
- Uncommitted changes included: 否（`git status --porcelain` 为空，工作区干净）
- Previous persistent report: 无（`docs/agent-tracking/feat-checkpoint-dev-time-dialog-width/code-review/` 不存在）

### Scope Determination

本地 review ref 不存在 → 判定为本分支首次审查，范围取完整分支 diff `master...HEAD`（即提交 `2b449ac` 全量改动）。
工作区无未提交改动，无需追加 `git diff` / `git diff --staged`。无历史持久化报告，故无历史 Comment Tracking / Address Findings 需要回溯。

## Confirmed Scope

```
 core/checkpoint.py                              | 34 ++++++++++++++++++++++++-
 ui/ideal_curve_dialog.py                        |  2 +-
 web/frontend/src/api.ts                         |  2 ++
 web/frontend/src/checkpoint.ts                  |  3 ++-
 web/frontend/src/components/CheckpointPanel.tsx |  8 +++++-
 5 files changed, 45 insertions(+), 4 deletions(-)
```

## Review Report

[LOW] 事件时间倒置时发展时间显示为 `(00:00)`，而不是不显示
**File**: core/checkpoint.py:46
**Issue**: `_development_seconds` 用「烘焙结束 time − 一爆开始 time」直接相减，未对结果做非负校验。模块内其它派生数值（`delta`）由「相邻两元素在同一次排序后相减」得到，天然非负；而 `dev_seconds` 是该模块唯一「跨元素、非相邻」相减的派生值，因此是唯一可能为负的量。前端 `formatMmSs`（web/frontend/src/checkpoint.ts:161-165）对负数做 `Math.max(0, …)` 钳制，于是负值不会显示成 `(-00:45)` 也不会被隐藏，而是显示成 `(00:00)`——语义上恰好等于「发展时间 0 秒」，属于显示错误信息而非仅缺失信息。
**Impact Scenario**: 理想曲线来源会话中操作员先标记「烘焙结束」、后补标「一爆开始」（移动端事件按钮是线性的，无时间顺序约束），该会话随后被选为理想曲线。此时 `times['烘焙结束'] - times['一爆开始'] < 0`，手机端「烘焙结束」这一未达成行显示为 `烘焙结束 186.0℃ (00:00)`，用户会理解为「理想曲线的发展时间为 0 秒」，而真实数据是倒置/异常。当前没有任何一层拦截该输入。
**Fix**: 在返回前对差值为负的情况返回 `None`（不展示优于展示错误值）。

  # BAD
  ```python
      roast_end = times.get(EventType.ROAST_END)
      if roast_end is None:
          return None
      return roast_end - times[EventType.FC_START]
  ```

  # GOOD
  ```python
      roast_end = times.get(EventType.ROAST_END)
      if roast_end is None:
          return None
      dev = roast_end - times[EventType.FC_START]
      return dev if dev >= 0 else None
  ```

### 补充：重点问题核查结论（本次审查要求重点核查的 5 项）

1. **`_development_seconds` 边界处理** — 基本完备，仅 1 处缺口（上条 LOW）。
   - 缺事件：`FC_START` 缺失 / `FC_END` 存在 / `ROAST_END` 缺失 三种情形分别由 41 行、43-45 行显式返回 `None`，与需求三者条件一一对应，正确。
   - 重复同类事件：取时间最早的一条（入参已按时间升序，"首个"= 最早），与 docstring 声明一致；`build_checkpoints` 会给每一条 `烘焙结束` 都挂上同一个 `dev_seconds`，在"同类事件唯一"的前提下无影响（会话状态机通常不会产生重复 `烘焙结束`）。
   - 畸形 events（非 dict 元素）：`ev.get` 会抛 `AttributeError`，但第 72 行 `ev.get('type')` 的集合推导式在此之前就会先抛，属既有行为，本次未引入新的失败面。
   - `time` 缺失：回退 `0.0`，与既有排序键 `float(e.get('time', 0.0))`（81 行）及 `delta` 语义一致，未引入不一致。
   - `time` 非数值：排序键（81 行）会对**全部**事件调用 `float()`，早于 `_development_seconds` 抛错，因此该函数不会产生排序键之外的新异常路径。
   - `time` 倒置：见上条 LOW。

2. **`StrEnum` 成员与普通 `str` 的集合/字典比较** — 可靠，已实测确认（Python 3.13.11）：
   `hash(EventType.ROAST_END) == hash('烘焙结束') == 7877248941453167265`，`EventType.ROAST_END == '烘焙结束'` 为 `True`；`'烘焙结束' in frozenset({EventType.ROAST_END})`、`EventType.ROAST_END in {'烘焙结束': 1.0}`、`frozenset({EventType.CHARGE}).issubset({'入豆'})` 全部为 `True`。因此 `_DEV_TIME_EVENT_TYPES` 的 `in` 判定、`times` 字典的成员查键、以及 107 行 `ev_type == EventType.ROAST_END` 均无隐患。（注：若将来把 `data.types.EventType` 从 `StrEnum` 降级为 `class X(str, Enum)`，`Enum.__hash__` 会变为按**成员名**取哈希，这些比较会静默失效——但同文件既有的 `_REQUIRED_CHECKPOINT_EVENT_TYPES`/`_MANUAL_CHECKPOINT_EVENT_TYPES` 已采用同一模式，本提交与该约定保持一致。）

3. **旧后端不下发 `dev_seconds`（`undefined`）** — 行为正确。`undefined != null` 为 `false`（松散比较下 `null == undefined` 成立），故条件渲染整体跳过，不会渲染空括号，也不会调用 `formatMmSs(undefined)`。额外核对了两条旧数据入口：localStorage 恢复路径（App.tsx:226 从 `session.ts` 的 `checkpoints: unknown[]` 强转 `Checkpoint[]`）同样可能带回缺字段的旧列表，行为一致安全。

4. **遗漏消费方** — 未发现。全仓 `dev_seconds` 仅出现在本提交的 4 个文件中；`Checkpoint` 字面量的构造点不存在（`Checkpoint` 类型只在 `getCheckpoints()` 的 JSON 断言处出现，无 mock/fixture 文件）；桌面端 `ideal_checkpoints` 只经 `_ipc_get_checkpoints` 下发（ui/camera_realtime_window.py:1294-1296），未落库、未写入 .slog；整字典断言的测试仅 `Development/tests/test_checkpoint.py`（未纳入提交）且已同步更新。`web/frontend` 无测试文件，故不存在其它受新增必填字段影响的断言。

5. **弹窗宽度** — 处理妥当。`minsize(700, 400)` 与 938 不冲突（938 > 700，仅表示用户可手动缩小）；父窗口 `CameraRealtimeWindow` 的 `minsize(1200, 800)`、实际宽度 `min(3200, screen×0.9)`，均大于 938，故居中计算 `px + (pw - w) // 2` 不会把对话框推到屏幕左外。居中逻辑未做屏幕边界钳制（`utils/screen_utils.center_window` 有 `max(0, …)`，此处没有），但这是既有模式，且父窗口最小宽度已保证不会触发，宽度 +188px 未使其恶化。

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

## Comment Tracking

（本次审查范围内的新增/变更注释均在 diff 内，未发现必要性问题、与 HEAD 状态不符的问题或过期 TODO/FIXME，故无条目。）

- 无

## Address Findings

| Issue # | Issue Short Description | User Decision | Details |
| ------- | ----------------------- | ------------- | ------- |
| 1 | 事件时间倒置时 dev_seconds 为负，前端钳制后显示 `(00:00)` | ACCEPT | 用户决策：保留现状，不加非负守卫。触发前提为理想曲线源会话中「烘焙结束」time 早于「一爆开始」（移动端事件按钮线性、无顺序约束，该状态可达），非纯理论问题；但后果限于理想曲线派生的提示文案，影响范围可控。 |
