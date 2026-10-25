# Code Review Report

## Scope

### Scope Resolution

- Review branch: feat/session-info-dialog
- Review ref: refs/review/feat/session-info-dialog
- Review ref existed: no（`git rev-parse --verify refs/review/feat/session-info-dialog` → fatal: Needed a single revision）
- Starting point: `git merge-base master HEAD` = 2e3e86ca68a2c735cb1bc0c26eacd48e556c0751
- Review range: `2e3e86ca68a2c735cb1bc0c26eacd48e556c0751...HEAD`（HEAD = 24037ae）
- First review: yes
- Uncommitted changes included: no（`git status --porcelain` 为空）
- Previous persistent report: none

### Scope Determination

分支 `feat/session-info-dialog` 上不存在本地评审标记 `refs/review/feat/session-info-dialog`，判定为本分支首次评审，范围取 `merge-base(master, HEAD)...HEAD`。`master..HEAD` 仅含 1 个提交（24037ae），工作区与暂存区均干净，无额外的未提交改动需要并入。

## Confirmed Scope

```
 ui/camera_realtime_window.py |  95 ++++++----
 ui/session_info_dialog.py    | 404 +++++++++++++++++++++++++++++++++++++++++++
 utils/numeric.py             |  19 ++
 3 files changed, 482 insertions(+), 36 deletions(-)
```

## Review Report

### [LOW] `_selected_bean()` 以空串匹配「未选中」，raw 会话可能被挂到无名生豆上

**File**: `ui/session_info_dialog.py:254-256`（影响 `_collect()` 的 `ui/session_info_dialog.py:316-318`）

**Issue**: `_selected_bean()` 用 `b.get('name') == self._name_var.get()` 做等值匹配，而下拉框未选中任何项时 `_name_var` 就是空串 `''`。`_load_beans()` 在填 `bean_combo['values']` 时有意过滤掉 name 为空的生豆（`if b.get('name')`），但 `self._beans` 保留了它们，于是「未选中」与「名字为空的生豆」在等值比较里撞在一起，`_selected_bean()` 返回的不是 `None`。

**Impact Scenario**: 生豆表存在一条 `name=''` 的记录时（`ui/bean_manager.py:114-118` 的「修改」分支不做空名过滤——新增分支 `:106` 会跳过空名，修改分支不会，把一条已有生豆的名字清空后保存即产生该记录）→ 打开弹窗、不选生豆、勾选「保存为原始数据」、点确定 → 本应写入 `bean_id=NULL` 的 raw 会话被静默写成 `bean_id=该无名生豆的 id`，并且 density/moisture 的「与生豆默认值比较」也按这条生豆取值。后果：Dashboard 按生豆筛选时该会话被归到一条界面上看不见的生豆。非 raw 模式因要求生豆名而不受影响。

**Fix**: 空串视为未选中。

  # BAD
  def _selected_bean(self):
      name = self._name_var.get()
      return next((b for b in self._beans if b.get('name') == name), None)

  # GOOD
  def _selected_bean(self):
      name = self._name_var.get().strip()
      if not name:
          return None
      return next((b for b in self._beans if (b.get('name') or '') == name), None)

### [LOW] `_load_beans()` 吞掉所有异常且不记录日志，DB 故障与「没有生豆」不可区分

**File**: `ui/session_info_dialog.py:237-241`

**Issue**: `except Exception: all_beans = []` 既不记录也不上抛。生豆仓库读取失败（DB 被锁、schema 异常、路径不可用等）与「用户还没有任何生豆」在界面上完全一样。

**Impact Scenario**: `list_all()` 抛异常时下拉框静默为空，用户填好其余字段点确定，只会看到「请检查以下字段: 生豆名称」——把问题指向了错误的字段（真实原因是仓库读取失败），且日志里没有任何线索可供排查。同样写法在 `ui/slog_viewer.py:419-425` 已存在，但本弹窗是新增代码。

**Fix**: 至少补一条日志；更好的是在弹窗内给出一行提示。

  # BAD
  try:
      all_beans = self._bean_repo.list_all()
  except Exception:
      all_beans = []

  # GOOD
  try:
      all_beans = self._bean_repo.list_all()
  except Exception as e:
      logger.warning("加载生豆列表失败: %s", e)
      all_beans = []

### [LOW] 新增的公共 `try_float` 与两处既有私有副本重复；面板逻辑是第三份拷贝

**File**: `utils/numeric.py:8`、`data/serializers/slog.py:32`、`ui/slog_viewer.py:631`

**Issue**: 本次新增的公共 `try_float` 与 `data/serializers/slog.py:_try_float`、`ui/slog_viewer.py:_try_float` 语义完全相同（None / 空串 / 不可解析 → None），三处各自服务「保存会话」的一条路径。

**Impact Scenario**: 语义一旦演化（例如要显式拒绝 `nan`/`inf`，或把纯空白也算作空），只会改到其中一处，同一个应用里不同保存路径的结果就不一致；本次已经把实现放到了 `utils/numeric.py` 这个公共位置，再留两个私有副本只会让后续修改持续漏掉。同类重复还包括：弹窗的失重率计算（`:281-288`）与 override 规则（`:328-333`），分别是 `ui/slog_viewer.py:152-163`、`:404-410`（以及 Qt 版 `ui/qt/slog_viewer.py`）的第三份实现。

**Fix**: 两处私有副本改为 `from utils.numeric import try_float`（已核对 `'  '` / `None` / 不可解析三种输入的行为等价）。面板级重复不要求本次处理，仅建议记录，后续统一「烘焙信息表单」时一并抽取。

### 针对提问点的核查结论（未构成 finding）

- **`_validate()` / `_collect()`**：`_collect` 输出的键与 `_do_save` 取用的键完全对应，无缺键；`_validate` 读 `self._is_raw_var` 与 `_collect` 同源，不存在「校验模式 ≠ 落库模式」的矛盾；数值格式校验只对非空值生效，raw 模式「全可空」的语义与约束一致。唯一小瑕疵：`green_weight` 填了非数值内容且非 raw 模式时，会同时列出「生豆重量（须为数值）」和「生豆重量」两条同义错误（`:363-369`），属提示文案重复，未单列。
- **`_do_save` 闭包**：`sid` 在每次调用时重新取号，配合 `execute_with_lock` 的异常回滚（`data/sqlite/connection.py:104-107`），失败重试不会残留半条会话、也不会复用失败事务；异常被 `except` 捕获后 `raise` 继续冒泡给弹窗，未被吞掉（`ui/camera_realtime_window.py:1547-1552`）；`saved=True` 与 `_do_save` 成功严格绑定，成功路径上 `sid` 必然已赋值，`get_display_name(sid)` 不会取到旧值或空串。`events`/`self.results` 在确定时读取，与旧实现的取值时机一致，非本次引入。
- **`_load_ideal_session` 的 `SqliteBeanRepository().get_by_id(bean_id)`**：`bean_id is None` / 查不到 / 已软删除三种情况都不抛错、也不会写入错误数据。`get_by_id`（`data/sqlite/bean_repo.py:71-78`）确实不过滤 `is_deleted`，会取出已软删除生豆的 name，但该 name 随后在 `_apply_default_bean()`（`:247-252`）里按名字与 combo 值比对，而 combo 值来自 `list_all()`（已排除软删除/停用豆），匹配不上就保持未选中，与 `_apply_default_bean` 文档写明的「不存在或缺货则保持未选中」一致。代价仅是预选静默失效、需用户手动选豆，故未作为问题列出。
- **资源泄漏 / 线程安全 / 重复代码**：弹窗销毁时 Tk 会自动释放 `grab`（`grab_release` 无需显式调用），`destroy()` 后 StringVar/Entry/Text 随窗口释放，调用方仅持有局部 `dlg` 引用，无窗口或变量泄漏。`<Return>` 守卫正确：已核对 `text.tcl` 的 `bind Text <Return>` 只插入换行、不返回 `break`，因此事件仍会到达 Toplevel 绑定，由 `focus_get() is self._notes_text` 拦下，该注释与 HEAD 代码一致。未发现线程安全问题（`_do_save` 在 Tk 主线程执行，DB 层自带连接锁）。重复代码见上一条 finding。

## Comment Tracking

本轮回合无评论类问题。

## Address Findings

| Issue # | Issue Short Description | User Decision | Details |
| ------- | ----------------------- | ------------- | ------- |
| 1 | `_selected_bean()` 空串匹配到无名生豆 |  |  |
| 2 | `_load_beans()` 静默吞掉异常 |  |  |
| 3 | `try_float` / 面板逻辑三份重复 |  |  |
