# Code Review Report

## Scope

### Scope Resolution

- Review branch: `refactor/remove-camera-mode`
- Review ref: `refs/review/refactor/remove-camera-mode`
- Review ref existed: Yes (points to `4fc8770`)
- Starting point: HEAD = `8268189`（该提交内容已在 report-1 作为未提交改动审查过）
- Review range: working tree（未提交）diff，仅 `ui/camera_realtime_window.py`
- First review: No（report-1 存在，零 findings，APPROVE）
- Uncommitted changes included: Yes（本审查的唯一范围）
- Previous persistent report: `review-report-1-4fc8770-APPROVE.md`

### Scope Determination

用户明确要求审查"实时识别窗口左侧面板纵向布局调整"的未提交改动（`git diff`），仅涉及 `ui/camera_realtime_window.py`。已提交的 HEAD `8268189`（移除 camera 模式）内容已在 report-1 审查过。最终范围由 `git diff` 确认：仅 `ui/camera_realtime_window.py` 的 working tree 改动。

## Confirmed Scope

```
 ui/camera_realtime_window.py | 131 ++++++----------------------------------
```
（+46 / -85，左侧面板纵向布局合并：删 `_modbus_status_panel`+`_realtime_status_frame`，新增 `_left_status_panel` + `_build_left_status_panel`，PanedWindow 左权重 45→35）

## Review Report

### 审查结论

本改动把实时识别窗口左侧容器从"温度读取器面板 + 底部三列实时值状态条"两个冗余组件合并为单一纵向面板（指示灯 → 空行 → 豆温/风温/ROR 大字值），并把 PanedWindow 左侧权重 45→35。对用户指定的 5 项重点核查逐项验证，**未发现 bug**。仅 1 个 LOW 级布局观感问题（面板内容顶部对齐、下方留出大片空白）。其余全部通过。

### 逐项核查确认

1. **`_value_row` 闭包与 var 引用链** — ✅ 正确。
   - 内层函数 `_value_row` 捕获 `parent`/`title_font`/`value_font`，均在 `_build_left_status_panel` 函数体内定义，且 `_value_row` 在函数内同步调用（非延迟绑定），闭包无问题。
   - `_bean_temp_var`/`_air_temp_var`/`_ror_var` 在 `_create_ui`（第 217 行）创建。`_update_realtime_status` 的调用点（第 975 行 `_on_result_ui`）与 `_reset_status_display` 的调用点（第 854/907 行）均为运行时路径，发生在窗口创建之后，var 必已存在，无 AttributeError。已用无头构造 + 调用三个方法实测通过。

2. **`_update_modbus_status` 移除 frame.pack / frame 参数** — ✅ 正确。
   - `update_channel` 签名改为 `(indicator, text_label, key, label)`，不再接收/调用 `frame.pack(...)`。原重复 pack 会用 `pady=(0,12)/0` 覆盖 `_build_left_status_panel` 里 ch2 的 `pady=(0,24)`"空行"，现已被彻底移除，24px 留白得以保留——这正是本次改动目的，验证通过。
   - 无残留未用变量：`temp_str` 仍作为"是否有数据"的判定条件被使用（`if temp_str is not None`）。
   - 指示灯行布局仍由 `_build_left_status_panel` 的 pack 保证（ch1 `pady=(0,6)`、ch2 `pady=(0,24)`），`_update_modbus_status` 只 configure 颜色与文案，不重新 pack，无破坏。

3. **无悬空引用** — ✅ 干净。
   - 全仓库 grep 确认对 `_create_realtime_status` / `_realtime_status_frame` / `_modbus_status_panel` / `_build_modbus_status_panel` / `_ch1_frame` / `_ch2_frame` 的引用为 0（唯一命中是上一份 review 报告文档，非代码）。
   - 无测试文件引用已删方法。

4. **布局逻辑** — ✅ 顺序与权重正确。
   - 无头测试 `winfo_children` 实测：`_left_status_panel` 下 5 个 Frame，pack 顺序 = ch1 指示灯 → ch2 指示灯 → 豆温 → 风温 → ROR，与规格一致。
   - "空行"由 ch2 frame 底部 `pady=(0,24)` 实现，位于指示灯与首行大字值之间，符合规格。
   - 权重 35/55（合计 90，为相对比例）：左侧约占 39%，相比原 45/55（约 45%）确已收窄，符合"左侧太大"的用户反馈。无需凑满 100。

5. **编译与运行** — ✅ 通过。
   - `python -m py_compile ui/camera_realtime_window.py` 通过。
   - 无头构造（Tk root → `CameraRealtimeWindow` → `update` → `_update_modbus_status` / `_reset_status_display` / `_update_realtime_status` → destroy）全程无 AttributeError，三个 var 值正确更新（bean=123.4, air=98.7, ror=--.-）。
   - 窗口最小尺寸 800px 高度下内容（约 586px 内容 + LabelFrame 内边距）可容纳，无裁剪风险。

### 发现的问题

#### [LOW] 左侧面板大字值行顶部对齐，面板下方留出大片空白
- **File:** `ui/camera_realtime_window.py:335-348`（`_build_left_status_panel` 的 `_value_row`）
- **Issue:** 三个值行 `row.pack(fill="x", pady=(8,2))` 未带 `expand`，而 `_left_status_panel` 本身 `pack(fill="both", expand=True)`（第 218 行）会撑满左侧容器高度。面板内没有任何子控件纵向 expand，内容整体顶部对齐。
- **Impact Scenario:** 在大屏/最大化窗口下实测（窗口高 1900px）：面板实际高度 1800px，内容请求高度仅 586px，ROR 大字值下方留下约 1200px 空白。旧布局中三个大字值位于底部状态条、视觉上锚定在底部；合并后浮在面板顶部，下方大片空洞，监测面板观感失衡。
- **Fix:** 让值行纵向铺满面板。例如给每个 `row.pack(...)` 加 `expand=True`（行内大字 Label 已是 `expand=True, fill="both"`，44pt 文本会在扩展空间内垂直居中），或改为 `row.pack(fill="both", expand=True)`：

```python
def _value_row(label_text, var, color):
    row = ttk.Frame(parent)
    row.pack(fill="both", expand=True, pady=(8, 2))
    ttk.Label(row, text=label_text, font=title_font,
              foreground=color, anchor="center").pack(pady=(6, 0))
    ttk.Label(row, textvariable=var, font=value_font,
              foreground=color, anchor="center").pack(expand=True, fill="both")
```

### 附加确认（非问题）

- **ch2（风温）指示灯在运行中显示红色"未连接"**：`_update_modbus_status` 的 `update_channel` 仅对 `key=='temp1'` 检查 `_latest_result`，ch2 在"启用+已配置端口"时恒为"未连接"。但这属既有逻辑（本 diff 未改动该分支），且与事实一致——`core/modbus_reader.py` 中 temp2 是"预留"通道（第 154-155 行 `temp2_value = None` 恒不读取，`_build_result` 将 temp2 固定为 "0.0"）。故 ch2 显示"未连接"恰好反映真实状态，不构成新 bug。风温大字值恒显 "0.0" 亦为预留通道的既有行为，非本次改动引入。
- 模块 docstring 第 6 行、`_create_ui`/`_build_left_status_panel`/`update_channel` 注释均已同步更新，与实际布局一致，无陈旧误导性注释。

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

- 本轮无 comment 类问题。report-1 无 open 条目带入。
- 本轮 diff 中改动的注释（模块 docstring 布局行、`_create_ui` 左侧容器注释、`_build_left_status_panel` / `update_channel` docstring、ch2 "空行" 注释、指示灯"已连接"注释）逐一核对与实际实现一致，未记录为 open 问题。
- `_reset_status_display` docstring 仍写"重置实时状态栏显示"（第 297 行），其中"状态栏"为旧布局措辞（值已移入左侧面板）。属极轻微陈旧措辞、无误导性，未列为 finding。

## Address Findings

| Issue # | Issue Short Description | User Decision | Details |
| ------- | ----------------------- | ------------- | ------- |
| 1 | LOW: 左侧面板大字值行顶部对齐，面板下方大片空白（`_value_row` 缺纵向 expand） | ACCEPT | 用户接受面板下方空白 |
