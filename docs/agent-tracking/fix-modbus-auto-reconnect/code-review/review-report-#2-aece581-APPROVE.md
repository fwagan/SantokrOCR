# Code Review Report

## Scope

### Scope Resolution

- Review branch: `fix/modbus-auto-reconnect`
- Review ref: `refs/review/fix/modbus-auto-reconnect`
- Review ref existed: Yes (resolved to `7ca327e`)
- Starting point: `2e3e86c` (merge-base with `master`)
- Review range: `2e3e86c...HEAD` (both `7ca327e` and `aece581`) — the caller explicitly
  requested the full branch range for this re-review; the ref pointed at `7ca327e`, so the
  follow-up commit is the actual delta
- First review: No (previous report `review-report-#1-7ca327e-APPROVE.md`, verdict APPROVE)
- Uncommitted changes included: No (`git status --short` empty)
- Previous persistent report: `review-report-#1-7ca327e-APPROVE.md` (Address Findings: all 4 = FIX)

### Scope Determination

The local review ref resolved to `7ca327e`, but the caller asked for a full re-review over
`2e3e86c...HEAD` with emphasis on whether the amend-created follow-up commit `aece581`
resolves the four prior findings. Both commits were therefore reviewed, with the prior
report's `Address Findings` (all `FIX`) used as historical context. The gitignored design
spec (`docs/superpowers/specs/2026-10-25-modbus-auto-reconnect-design.md`) and the gitignored
tests (`Development/tests/test_modbus_reconnect.py`) were read as context, not as diff scope.
No staged/unstaged changes were present.

## Confirmed Scope

```
 core/modbus_reader.py                              | 251 ++++++++++++---------
 core/temperature_source.py                         |   5 -
 .../review-report-#1-7ca327e-APPROVE.md            | 148 ++++++++++++
 ui/camera_realtime_window.py                       |  19 +-
 4 files changed, 312 insertions(+), 111 deletions(-)
```

Follow-up-only delta (`7ca327e..HEAD`): 4 files, +160 / -12.

## Review Report

## Verified behavior (follow-up commit `aece581`)

- **Prior finding 1 (MEDIUM, frozen `waiting_charge` window) — resolved.** `_on_result_ui`
  now clears `_rolling_window` on a `comm_disconnected` placeholder when
  `_roast_state == "waiting_charge"` (`ui/camera_realtime_window.py:986-991`). The clearing
  runs before the placeholder early-return and only in the waiting state. With an empty
  window, `_process_charge` takes the fill path (`ui/camera_realtime_window.py:1044-1060`):
  `full_window_frames = max(1, round(5.0/interval))` fill frames are emitted at
  `roast_ts = i*interval` and the charge frame maps to exactly `_EXTRA_RECORD_SECONDS`
  (5.0). For interval 0.25/1.0 this yields strictly increasing 0.00..4.75/0.00..4.00 plus
  the 5.0 charge row — no duplicate t=0 rows and no non-monotonic axis, so
  `resample_data` / `np.interp` / DB writes are safe. Fill temperature falls back to the
  charge frame when the window is empty.
- **Prior finding 2 (LOW, reconnect after stop) — resolved for the reconnect path.**
  `core/modbus_reader.py:152-154` now requires `not self._stop_event.is_set()`. The only other
  `_connect()` call is the initial connect at `core/modbus_reader.py:131`, before the loop;
  it is pre-existing and its client is always closed by `_run_loop`'s `finally`. No new path
  can create a client after stop.
- **Prior finding 3 (LOW, dead `temperature_diff` / `reset_temperature_tracking`) —
  resolved.** The realtime check is gone (`ui/camera_realtime_window.py:278`), the reset call
  is gone from `_clear_all_data` (`ui/camera_realtime_window.py:901-915`), and the base hook
  is deleted (`core/temperature_source.py`, now ends at `is_stopped`). Repo-wide grep finds no
  remaining `reset_temperature_tracking` reference in code. Post-processing `temperature_diff`
  handling is intact in `ui/recognition_window.py:286-350,1444`, `ui/statistics_panel.py:478`,
  `ui/data_table.py:120-121`, `ui/frame_viewer.py:828-830`, `data/types.py:50`,
  `data/sqlite/result_repo.py:15-32`, `data/sqlite/schema.py:83`.
- **Prior finding 4 (LOW, log spam) — only partially resolved; see finding below.** Our own
  `_create_client` / `_read_temperature` exceptions are now DEBUG
  (`core/modbus_reader.py:261,296`) and disconnect/reconnect transitions log one WARNING each
  (`:227,230,237`). However pymodbus 3.13.1 emits its own ERROR/WARNING lines per retry and per
  failed read; see finding 1 of this report.
- **Committed `Address Findings` table matches the code** for entries 1, 2 and 3. Entry 4's
  description matches the changes actually made, but its implication of "one WARNING per
  transition" is not what the runtime produces (see finding 1).
- No comment issues were found in the current diff. The updated `_on_result_ui` docstring
  (`:979`) and the new inline comment (`:987`) accurately describe HEAD; the new comment in
  `_create_client` (`:260`) accurately scopes itself to our logger.

## Findings

[LOW] pymodbus 内部日志仍按每次读取/重连刷 ERROR/WARNING，finding 4 只解决了自有日志
**File**: core/modbus_reader.py:145-160, 233-238
**Issue**: 本次只降级了本模块的 logger，但实际刷屏来自 pymodbus 自身：读超时路径在
`pymodbus/transaction/transaction.py:152`（同步路径）每次 `read_input_registers` 失败都
`Log.error("No response received after N retries...")`；建连失败在
`pymodbus/transport/transport.py:252` 打 WARNING，而在 Windows 上端口不存在时实测直接输出
`ERROR pymodbus.logging: could not open port ...`。桌面应用未配置 logging，Python 的
`lastResort` handler 会把 WARNING/ERROR 打到 stderr（已实测：无 basicConfig 时
`Log.error(...)` 仍可见），因此断连期间的控制台噪音基本未减少。另外 `_on_reconnected()`
在 `_connect()`（仅打开串口）成功时即触发，并不等待首帧读取成功；当适配器仍在枚举但设备
不应答时，会形成“已重新连接 / 连接断开”成对 WARNING 的周期性重复。
**Impact Scenario**: USB-RS485 适配器仍被系统识别、但传感器板掉电（`connect()` 成功、读超时），
采样间隔 1s 时每秒输出一条 pymodbus `No response received` ERROR，并每约 4s 交替输出两条
自有 WARNING；10 分钟断连仍有约 600 行 stderr 日志，与 finding 4 描述的场景一致，状态栏也会
在“已重新连接/连接断开”之间闪烁。
**Fix**: 在 DISCONNECTED 状态抑制/过滤 `pymodbus` logger（例如临时
`logging.getLogger("pymodbus").setLevel(logging.CRITICAL)`），或统一在应用入口配置日志并
按 logger 名过滤；若同时希望消除状态闪烁，可将 `_on_reconnected()` 推迟到重连后首帧读取成功。

[LOW] 单次瞬时读失败也会清空滚动窗口，丢弃最多 5s 真实入豆前数据
**File**: ui/camera_realtime_window.py:986-991, 1024-1068
**Issue**: `comm_disconnected` 占位帧并非只在达到 3 次失败阈值后才发出——每次读取失败都会
`result['comm_disconnected'] = True`（core/modbus_reader.py:172-181）。因此 `waiting_charge`
期间只要出现一次超时/异常（未达断连阈值），窗口就被整体清空。重连/恢复后 `_process_charge`
只能用剩余帧 + 常量温度填充，丢掉的真实帧无法恢复。
**Impact Scenario**: 采样间隔 0.25s，t=98.0 出现一次 CRC/超时（`_comm_failures=1`，
`_client1` 仍有效），窗口内 93.25~98.0 的 20 个有效帧被清空；随后读取恢复，t=99.5 收到入豆
帧时窗口只有 5 帧，`_process_charge` 用 t=98.25 的温度填充 0~3.5s，图表与数据库中入豆后的
前 3.5 秒变成一段实际不存在的平线。
**Fix**: 区分“已确认断连”与“单次失败”后再清空（例如占位帧增加 `comm_unavailable` 之类字段，
仅当 `_client1 is None`/阈值已达到时为 True）；或采用上轮评审的方案 B——在
`_process_charge` 中丢弃 `timestamp < charge_real - _EXTRA_RECORD_SECONDS` 的窗口帧，可在不
丢弃新鲜数据的前提下保证时间轴单调。

[LOW] 设计文档与代码不一致（`temperature_source.py` 改为删除基类钩子）
**File**: docs/superpowers/specs/2026-10-25-modbus-auto-reconnect-design.md:190,214（gitignored）
**Issue**: 设计文档 §7 写的是“删除 `reset_temperature_tracking()` 覆写（继承基类空实现）”，§9 明确把
`core/temperature_source.py` 列为“不改”；但 follow-up 按 Address Findings 把基类空实现也
删除了。文档未同步。
**Impact Scenario**: 后续按 spec 实施/排查的人会认为基类仍提供 `reset_temperature_tracking()`，
可能重新引入无效调用，或对“为什么 UI 不再重置温差状态”产生误解。
**Fix**: 更新 spec §7/§9（以及同目录 plan 文档中的旧代码片段）以反映基类钩子已删除。

## Review Summary

| Severity | Count | Status |
|----------|-------|--------|
| CRITICAL | 0     | pass   |
| HIGH     | 0     | pass   |
| MEDIUM   | 0     | pass   |
| LOW      | 3     | note   |

Verdict: APPROVE

---
⚠️ NOTICE TO CALLING AGENT: Do NOT apply any fixes from this review automatically.
Present this review to the user first and wait for explicit instruction on which
issues to address. The user must approve changes before any code is modified.
Present the full review output verbatim to the user. Do NOT summarize, 
compress, or reformat any findings. Paste the complete text as-is.
---

## Comment Tracking

- No comment issues found in this review.

## Address Findings

| Issue # | Issue Short Description | User Decision | Details |
| ------- | ----------------------- | ------------- | ------- |
| 1 | pymodbus 内部日志仍按读取/重连周期刷 ERROR/WARNING（finding 4 仅部分解决） | FIX | `_on_reconnected` 推迟到重连后首个读取成功；断连期间将 `pymodbus.logging` 临时提到 CRITICAL，恢复/停止时还原原级别 |
| 2 | 单次瞬时读失败也清空滚动窗口，丢弃最多 5s 入豆前真实数据 | FIX | 增加连续无效帧计数，仅在超过窗口容量 1/3 时清空 `_rolling_window`；有效帧到达后计数归零 |
| 3 | 设计文档未同步基类 `reset_temperature_tracking` 删除 | DEFER | spec 为 gitignored 内部文档，暂不同步；后续需要时再更新 §7/§9 |
