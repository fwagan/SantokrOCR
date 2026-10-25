# Code Review Report

## Scope

### Scope Resolution

- Review branch: `fix/modbus-auto-reconnect`
- Review ref: `refs/review/fix/modbus-auto-reconnect`
- Review ref existed: Yes (resolved to `705b883`)
- Starting point: `2e3e86c` (merge-base with `master`)
- Review range: `705b883..HEAD` for the incremental re-review (single commit `c1713da`),
  plus a full `2e3e86c...HEAD` scan as explicitly requested by the caller
- First review: No (previous reports #1, #2, #3, all APPROVE)
- Uncommitted changes included: No (`git status --short` empty except an untracked
  `docs/agent-tracking/feat-session-info-dialog/` directory belonging to another branch)
- Previous persistent report: `review-report-#3-705b883-APPROVE.md`
  (Address Findings: #1 FIX, #2 FIX, #3 ACCEPT)

### Scope Determination

The local review ref resolved to `705b883`, the commit covered by review #3, so the actual
new delta is `c1713da` (27 lines in `ui/camera_realtime_window.py` + the committed
report #3). The full-range `2e3e86c...HEAD` re-scan found no code outside the files already
reviewed in reports #1-#3 (`core/modbus_reader.py`, `core/temperature_source.py`,
`ui/camera_realtime_window.py`). `Development/tests/test_modbus_reconnect.py` is gitignored
local verification context, not part of the diff. There were no staged or unstaged changes.

## Confirmed Scope

```
# Incremental delta 705b883..HEAD
 .../review-report-#3-705b883-APPROVE.md            | 196 +++++++++++++++++++++
 ui/camera_realtime_window.py                       |  27 ++-
 2 files changed, 216 insertions(+), 7 deletions(-)

# Full branch 2e3e86c...HEAD
 core/modbus_reader.py                              | 271 +++++++++++++++-------
 core/temperature_source.py                         |   5 -
 .../review-report-#1-7ca327e-APPROVE.md            | 148 +++++++++++
 .../review-report-#2-aece581-APPROVE.md            | 154 +++++++++++
 .../review-report-#3-705b883-APPROVE.md            | 196 +++++++++++++++++++++
 ui/camera_realtime_window.py                       |  52 +++-
 6 files changed, 709 insertions(+), 117 deletions(-)
```

## Review Report

## Verified behavior (commit `c1713da`)

- **Prior finding #1 (duplicate timestamps after a tolerated disconnect) — addressed.**
  `_process_charge` (`ui/camera_realtime_window.py:1048-1064`) now keeps only frames with
  `timestamp >= charge_real - _EXTRA_RECORD_SECONDS` before assembling the batch, and
  recomputes the fill prefix from the first kept frame. The boundary is correct in every
  enumerable shape, worked through with `interval=0.25`, `shift = charge - 5.0`:
  - *Contiguous full window* (20 frames, oldest exactly `charge-5.0`): `>=` keeps all 20,
    `first_ts = 0` → `fill_count = 0` → rows `0.0 … 4.75, 5.0`. Identical to before.
  - *Contiguous partial window* (k frames): `first_ts = 5 - k*interval`,
    `fill_count = round(5/interval - k) = full_window_frames - k`; same value as the old
    `full - len(window)` (checked for `interval` 0.25 and 0.3, i.e. integral and
    non-integral `5/interval`). No behavior change.
  - *Empty window*: full 20 fill frames, unchanged (`:1063-1064`).
  - *Missing slot at the start / long tolerated gap*: stale frames are dropped and the
    prefix is restored from the first survivor, so the timeline still starts at 0 with no
    negative clamp (`_remap_result` `:1129-1130` is now unreachable for window frames).
  - *Missing slot(s) in the middle or at the end*: those frames stay dropped, producing an
    interior gap (e.g. `4.0 → 4.5`) instead of a duplicate row; `interp1d`
    (`ui/statistics_panel.py:521`) interpolates across it rather than returning NaN.
  - *Jitter (oldest frame at `charge-5.02`)*: dropped; first kept maps to `0.01`,
    `fill_count = round(0.04) = 0`; timestamps remain strictly increasing.
- **No fill/window collision is possible.** For `first_ts = window[0] - shift`,
  `round(x) <= x + 0.5` gives `(fill_count-1)*interval <= first_ts - 0.5*interval`, so the
  last fill frame is always at least half an interval before the first kept frame. The
  `min(full_window_frames, …)` cap can only reduce the count. `first_ts < 5.0` always holds
  because every window frame precedes the charge frame, so the cap is practically a no-op.
- **Uniqueness/strict monotonicity confirmed for the one-gap scenario.** Window frames carry
  source timestamps rounded to 3 decimals (`core/modbus_reader.py:333`), `shift` is a
  3-decimal value, and `_remap_result` re-rounds to 3; adjacent retained frames are one
  interval apart, so no two rows collapse onto the same timestamp. Non-negative values mean
  `-0.0` cannot be produced (the filter guarantees `ts >= 0` before rounding).
- **`_charged_pending` / state machine — unchanged and consistent.** The flag is still set in
  `_apply_charge_start` (`:1471`), cleared in `_process_charge` (`:1053`) and
  `_reset_web_state` (`:1514`); the disconnect branch still leaves the window frozen and the
  flag set, and the next valid frame becomes the charge frame. `_invalid_frame_count` is
  reset on the first valid frame (`:1001`).
- **Fill temperature source matches its comment.** `:1068-1077` iterates the filtered
  `window`, so the "first clean window frame" is now also a frame that survives the filter;
  falling back to `charge_result` when the window (or all survivors) are `?` is consistent.
- **Event offset, turnaround cache and frame numbering unaffected.** The charge event is
  still added at `_EXTRA_RECORD_SECONDS` (`:1091`), `_compute_event_base` (`:1148`) still
  yields 5.0, and `_update_turnaround_cache` (`:1159-1174`) only reads event offsets.
  Frame numbers come from `enumerate(frames)` (`:1084-1086`), so
  `UNIQUE(session_id, frame)` remains satisfied regardless of the new row count.
- **Docstrings now match behavior.** `_on_result_ui` (`:980-981`) states the 1/3-of-window
  threshold, and `_process_charge` (`:1038-1039`) states that invalid frames consume no
  window slot and that older-than-5s frames are dropped. The previously flagged snippet
  (`等待入豆时清空滚动窗口`) no longer exists anywhere in `ui/` or `core/`.
- **Tests** (`Development/tests/test_modbus_reconnect.py`, gitignored):
  `test_process_charge_drops_stale_window_frames` covers the stale-drop + interior/end gap
  path and asserts sortedness, uniqueness, first `original_timestamp` and the preserved gap;
  `test_process_charge_empty_window_fills_full_prefix` covers the empty-window path. Not
  covered is "partial window that still needs a positive fill prefix", but that path is
  mathematically identical to the pre-fix formula (verified above) and cannot collide.
- **Full range `2e3e86c...HEAD` re-scan**: no code changes beyond what reports #1-#3
  reviewed. The previously accepted LOW (non-atomic `_PYMODBUS_SAVED_LEVEL`, user decision
  ACCEPT) is untouched by this commit and is **not** re-raised.
- **Report #3 accuracy**: Address Findings entries #1/#2 describe what `c1713da` actually
  implements; entry #3 records the ACCEPT decision. Two metadata defects remain — see
  findings 1 and 2.

## Findings

[LOW] review-report-#3 的 Comment Tracking 仍保留 "commit 待生成 / 下一提交" 占位
**File**: docs/agent-tracking/fix-modbus-auto-reconnect/code-review/review-report-#3-705b883-APPROVE.md:177-178
**Issue**: `Status: resolved（修复已应用，commit 待生成）` 与 `Resolved commit: 下一提交`
在报告写入时是准确的，但 `c1713da` 正是应用修复并提交该报告的 commit，占位符已过期。
**Impact Scenario**: 后续 review 或人工追溯会看到一个悬空的 "下一提交"，无法定位修复落点；
实际上 `c1713da` 同时包含 docstring 修复与报告文件，就是 resolving commit。
**Fix**: 把 `Status` 改为 `resolved（c1713da）`、`Resolved commit` 改为 `c1713da`
（由 caller 决定是否顺带 amend/追加提交修改）。

[LOW] review-report-#3 存在两个 Address Findings 表，第二个为空且标题不一致
**File**: docs/agent-tracking/fix-modbus-auto-reconnect/code-review/review-report-#3-705b883-APPROVE.md:180-196
**Issue**: 第 182-186 行是 caller 已填写的表格（FIX/FIX/ACCEPT），第 190-196 行是未填充的
模板残留表，且两表的 Issue 描述措辞不同（如 "#3 保存/恢复非原子" vs "#3 保存/设置非原子"）。
**Impact Scenario**: 下一次 review 按规则读取 "Address Findings" 时可能命中空的第二张表，
把已决策的三条 finding 误判为未决定而重复提出，或按不同措辞匹配到不同条目。
**Fix**: 删除第 190-196 行的空表，只保留已填充的一张。

## Review Summary

| Severity | Count | Status |
|----------|-------|--------|
| CRITICAL | 0     | pass   |
| HIGH     | 0     | pass   |
| MEDIUM   | 0     | pass   |
| LOW      | 2     | note   |

Verdict: APPROVE

---
⚠️ NOTICE TO CALLING AGENT: Do NOT apply any fixes from this review automatically.
Present this review to the user first and wait for explicit instruction on which
issues to address. The user must approve changes before any code is modified.
Present the full review output verbatim to the user. Do NOT summarize, 
compress, or reformat any findings. Paste the complete text as-is.
---

## Comment Tracking

- File: `ui/camera_realtime_window.py`
- Matching comment snippet: `- comm_disconnected：只更新状态显示，不写入任何结果数据；等待入豆时清空滚动窗口`
- Issue description: docstring described unconditional clearing; HEAD clears only after the 1/3-window threshold.
- Flagged commit: `705b883`
- Status: resolved（`c1713da` 已改为 "连续无效帧超过窗口容量 1/3 才清空滚动窗口"）
- Resolved commit: `c1713da`

## Address Findings

| Issue # | Issue Short Description | User Decision | Details |
| ------- | ----------------------- | ------------- | ------- |
| 1 | review-report #3 Comment Tracking 的 "Resolved commit: 下一提交" 占位未更新为 `c1713da` | FIX | 改为 `Status: resolved（c1713da）`、`Resolved commit: c1713da` |
| 2 | review-report #3 含重复且措辞不一致的空 Address Findings 表 | FIX | 删除模板残留的空表，仅保留已决策表 |
