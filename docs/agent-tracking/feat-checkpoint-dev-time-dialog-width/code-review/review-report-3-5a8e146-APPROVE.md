# Code Review Report

## Scope

### Scope Resolution

- Review branch: `feat/checkpoint-dev-time-dialog-width`
- Review ref: `refs/review/feat/checkpoint-dev-time-dialog-width`
- Review ref existed: 是（`git rev-parse --verify` → `1a39643ac1f06815ab3b9fdd6b4853a18bc28f7e`）
- Starting point: `1a39643`（review ref 指向点，非 merge-base）
- Review range: `1a39643..HEAD`（`HEAD` = `5a8e146`），本区间仅 1 个提交 `5a8e146`
- First review: 否（本分支第 3 次审查；第 1 次范围 `master...2b449ac`，第 2 次范围 `2b449ac..1a39643`）
- Uncommitted changes included: 否（`git status --porcelain` 为空，工作区干净，无 staged 改动）
- Previous persistent report: `docs/agent-tracking/feat-checkpoint-dev-time-dialog-width/code-review/review-report-2-1a39643-APPROVE.md`

### Scope Determination

本地 review ref 存在，且指向 `1a39643`——与上一轮持久化报告 `review-report-2-1a39643-APPROVE.md` 的 review-completion HEAD 一致，判定为增量审查，范围取 `1a39643..5a8e146`。该区间仅含 1 个提交、2 个文件、+132/-4 行，无需回退到全量分支 diff（`master...HEAD`）。工作区无未提交改动、无 staged 改动，故无追加 diff。

上一轮持久化报告已读取：

- `Comment Tracking` 为空（无 open 条目），本轮无需回溯历史注释问题。
- `Address Findings` 仅 1 条，用户决议为 `ACCEPT`。按规则（ACCEPT 条目不再因"底层模式仍存在"而上报）。其 `Details` 记录的用户决策方案正是"调整 `session_repo.get_display_name` 的字段顺序为 `[豆名 日期 时间]`"，与本次提交 `5a8e146` 的实际改动完全吻合，即本轮改动是该决议的落地实施，不构成新问题。

## Confirmed Scope

```
 data/sqlite/session_repo.py                                      |   8 +-
 docs/agent-tracking/feat-checkpoint-dev-time-dialog-width/code-review/review-report-2-1a39643-APPROVE.md | 128 +++++++++++++++++++++
 2 files changed, 132 insertions(+), 4 deletions(-)
```

其中第 2 个文件（上一轮 review 报告的归档）为工作流产物，与本次代码改动无关；其进入提交与仓库既有约定一致（对照 `f577653`、`1a39643`，其余分支/轮次的 review 报告同样随特性提交一并入库），不构成问题。实际受审代码仅为 `data/sqlite/session_repo.py`。

## Review Report

### 结论：本轮未发现需要上报的问题

`get_display_name` 的改动为纯粹的字段顺序调整，功能语义在全部输入分支上与该函数的历史行为等价；该返回值在全仓范围内仅作**展示与命名**用途，无任何消费方按位置解析、比较、排序或以之为唯一键。具体核查如下。

### 核查项 1：`get_display_name` 消费方全量盘点

全仓搜索 `get_display_name` / `display_name`（`.py` 及 `.ts/.tsx`），真实消费点共 5 处，逐一核对结论：

| # | 调用点 | 用途 | 新顺序下是否受影响 |
|---|--------|------|-------------------|
| 1 | `ui/camera_realtime_window.py:604` → `source_name=display_name` | 存入 `ideal_data['name']`（`camera_realtime_window.py:680`）；`ideal_curve_name`（624 行）经 `get_status`（1291 行）作为 IPC `curve_name` 下发；`ideal_file_label`（627 行）桌面标签 | 不受影响，为本次改动的**目标**路径 |
| 2 | `ui/camera_realtime_window.py:758` | `f"文件: {data['name']}"` 纯文本展示 | 不受影响 |
| 3 | `ui/slog_viewer.py:522-524` | `source_identity` + 窗口标题 `Slog Viewer - {display_name}` | 不受影响 |
| 4 | `ui/slog_comparer.py:576` | `slog_data['name']` → 列表标签（387 行）、图例标签（885/889 行） | 不受影响 |
| 5 | `ui/dashboard.py:235` | 仅在 `notes` 为空时写入 DB `notes` 字段 | 不受影响，详见核查项 2 |

无关命中（已排除）：`ui/bean_manager.py:263`、`ui/qt/bean_manager.py:305` 的 `display_name` 是咖啡豆名（`bean.get('name')`），与 `get_display_name` 无关。

**关键判定**：全仓不存在任何对 `get_display_name` 返回值做 `split` / 正则 / 切片 / 位置取值的代码。前端侧亦已核查——`web/frontend/src` 中 `curve_name` / `curveName` 的全部出现为：`api.ts:17` 类型声明、`App.tsx:189/196` 的**字符串相等比较**（`name === cachedCurveName` 的缓存失效判定）、`App.tsx:562` 透传、`CheckpointPanel.tsx:12/27/46` 纯渲染。无解析、无排序、无分组。

### 核查项 2：`ui/dashboard.py:235` 写入 `notes` 与新旧顺序混存

- **无重复嵌套风险**：`dashboard.py:234` 的守卫是 `if not session.get('notes', '').strip()`，即**仅在 `notes` 为空时**才写入 `get_display_name` 的结果。`notes` 已是 `[旧格式]` 时不会被再次拼接，不存在"括号套括号"。
- **无解析方**：`notes` 的读取点共 2 处，均为纯文本展示——`ui/dashboard.py:171`（`name = s.get('notes','') or s.get('session_id','')`，作为右侧原始数据树 `text=name`）、`ui/recognition_window.py:1207`（拼进窗口标题）。无按 `notes` 去重、无按 `notes` 排序、无按 `notes` 匹配。
- **无 DB 约束**：`data/sqlite/schema.py` 中 `notes` 为 `TEXT NOT NULL DEFAULT ''`，其上无 `UNIQUE`、无索引（现有索引仅 `bean_id` / `created_at` / result / event 相关）。
- **混存的实际后果**：仅表现为 Dashboard 右侧"原始数据"列表中，早于本次改动产生的回退记录显示 `[2026-08-22 15:25 豆名]`、之后产生的显示 `[豆名 2026-08-22 15:25]`。二者均为完整可读字符串，不产生错误、不改变任何排序（该列表由 `list_filtered` 按 `roast_date DESC, roast_time DESC, created_at DESC` 排序，与 `notes` 内容无关）。属纯外观差异，**无需迁移**，作为设计取舍接受。
- **另一条写入路径不受影响**：`camera_realtime_window.py:1504` 的 `default_name` 是 `datetime.now().strftime('%Y%m%d_%H%M%S')`（时间戳），并非从显示名派生，因此"保存会话 → `notes` = 时间戳 → 再读 `notes`"不存在显示名回灌或二次包裹的链路。

### 核查项 3：空值与部分字段缺失的等价性

新实现为 `parts = [p for p in (bean_name or '', roast_date or '', roast_time or '') if p]`，旧实现为 `(roast_date or '', roast_time or '', bean_name or '')` 过滤。**两个元组的元素集合完全相同，仅排列顺序不同**，因此：

- `parts` 是否为空、`parts` 的成员构成、`' '.join(parts)` 的长度集合，在任意缺失组合下均与旧实现一致；
- 三者全空 → `parts == []` → 仍走 `row['notes'] or session_id` 回退分支，行为完全等价；
- `bean_name` 为 NULL（LEFT JOIN 未命中）→ `row['bean_name'] or ''` → 被 `if p` 过滤，与旧实现一致；
- 仅 `bean_name` 有值（日期/时间空）→ 旧 `[豆名]`，新 `[豆名]`；仅日期/时间有值 → 旧新同为 `[日期 时间]`。

即：**唯一发生变化的场景是 >= 2 个字段同时非空时的排列顺序**，无任何分支行为漂移。同步更新的 docstring（`session_repo.py:191`）与实现一致。

### 核查项 4：是否依赖显示名的稳定排序

不依赖。全仓无按显示名排序/分组/去重的代码：

- `ui/slog_comparer.py:613-618` 的 `_sort_slogs` 排序键为 `(is_favorite, roast_date, roast_time)`，取自 session 字段而非 `name`；
- `ui/widgets/session_grid_widget.py:108-118` 的会话表格使用 `roast_date` / `roast_time` / `bean_name` / `variety` 等**独立列**，并**不**使用 `get_display_name`，故界面排序观感不受本次改动影响；
- `data/sqlite/session_repo.py:135/178` 的两种列表排序均基于 `created_at` / `roast_date` / `roast_time`；
- 前端无任何排序逻辑作用于 `curveName`（仅相等比较）。

操作员在"同一列表内混排新旧格式"上可能感知到的差异，已在核查项 2 中说明，属外观而非排序语义变化。

### 核查项 5：`.slog` basename 路径与 DB 显示名混用

二者写入**同一个**字段，无逻辑分支：

- `ui/camera_realtime_window.py:587/590`（`.slog` 加载）→ `os.path.basename(path)`；
- `ui/camera_realtime_window.py:624/627`（DB 会话加载）→ `get_display_name`。

两条路径互斥（一次只可能走其中一条），均赋给 `self.ideal_curve_name` 与 `self.ideal_file_label`，下游 `get_status`（1291 行）只做原样下发；`_clear_ideal_slog`（708 行）统一清空。全仓无对 `ideal_curve_name` 的比较、解析或分支判断（仅 105/587/624/708/1291 的赋值与读取）。因此不存在"混用导致分支错误"的风险。

需要指出（**机制相同、且在本次 diff 范围之外，不计为 finding**）：`.slog` 路径的曲线名不加 `[豆名 日期 时间]` 包装，其 basename 通常为日期前缀（如 `20260822_咖啡豆名.slog`），在移动端同行尾部省略号截断下的表现与上一轮已决议 `ACCEPT` 的 LOW 属同一机制；该路径自 2026-06-15（`0584e349`）起即存在，非本次改动引入。按 ACCEPT 规则不重复上报。

### 已验证但无需上报的项（范围外提示，供确认）

- **`ui/slog_viewer.py:481/484` 的导出默认文件名含非法字符**：`default_name = self.source_identity or "export"`，而 `source_identity` 在 `load_from_session_id` 路径下为 `get_display_name` 的返回值（523 行），其 `roast_time` 段格式为 `f"{hour}:{minute}"`（`ui/slog_viewer.py:611`），即文件名形如 `[豆名 2026-08-22 15:25].slog`，其中 `:` 在 Windows 上为非法字符，用户在"导出"时会遭遇系统文件名校验报错，需手工改名。**该缺陷在旧顺序 `[2026-08-22 15:25 豆名]` 下同样存在（冒号只是位置不同，未消失）**，相关两行分别可追溯至 `41fdc73`（基线）与 `0584e349`（2026-06-15），均早于本分支，故**不属于本次改动引入**，按规则不计入本轮 finding。此处仅作为相邻观察记录，是否单独修复由用户决定。
- 注释核查（本轮 diff 范围内）：`data/sqlite/session_repo.py:191` 的 docstring 已随实现同步为 `[bean_name yyyy-mm-dd hh:mm]`，与 203-206 行实现一致，说明语义而非复述代码，无必要性问题；未发现与 HEAD 状态不符的注释、过期 TODO/FIXME。
- 测试覆盖：全仓无任何测试文件引用 `get_display_name` / `display_name`，故本次变更无测试断言需要同步（亦无测试可回归该格式约定）。
- 上一轮 `Address Findings` 第 1 条（曲线名尾段截断丢豆名后缀）用户决议 `ACCEPT` 并指定以"豆名前置"实施，本轮实现与决议一致；该条已关闭，按 ACCEPT 规则不再上报，也不再计入本轮 LOW 计数。

## Comment Tracking

- 无

（上一轮 Comment Tracking 为空，无 open 条目需回溯。本轮 diff 范围内仅 1 处 docstring 变更，已核查其与 HEAD 实现一致，未发现必要性冗余、与当前状态不符或过期 TODO/FIXME。）

## Review Summary

| Severity | Count | Status |
|----------|-------|--------|
| CRITICAL | 0     | pass   |
| HIGH     | 0     | pass   |
| MEDIUM   | 0     | pass   |
| LOW      | 0     | pass   |

Verdict: APPROVE

---
⚠️ NOTICE TO CALLING AGENT: Do NOT apply any fixes from this review automatically.
Present this review to the user first and wait for explicit instruction on which
issues to address. The user must approve changes before any code is modified.
Present the full review output verbatim to the user. Do NOT summarize, 
compress, or reformat any findings. Paste the complete text as-is.
---
