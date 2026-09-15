# 周／月用量曲线实施计划

> 按用户全局协作约定，使用当前会话原生多代理协调；不使用 Superpowers 标准 SDD / executing-plans 执行框架。已确认原生协作工具可用，未确认账户 Ultra 档位。

**目标：** 便签增加日／周／月切换，自然周按小时、自然月按天绘制 Token 与美元费用曲线，支持前后翻页及逐点查看。

**架构：** reader 在去重、排除自动审批模型后同时生成日与小时桶；独立 periods 模块确定日历边界、合并模型计数、补齐已过去的空桶。Tk Canvas 绘制两个坐标图，与模型明细一起滚动，不引入依赖。

**技术：** 项目 `.venv` Python 3.13、标准库、Tkinter。

**规格：** 本轮用户请求与已说明的自然周（周一开始）、自然月口径。延续已授权的自动审批模型排除、缓存／输入／输出分类和当前刊例价估算。

## 约束

- 周内每个点对应真实本地小时，使用带 UTC 偏移的小时键，夏令时重复小时不能合并。
- 月内每个点对应本地日期。空桶为 0；未来桶不画成已发生的 0；当前桶提示尚未结束。
- 费用有未知报价时显示已知价小计与标记，不伪装为完整费用。
- 沿用 `.venv/Scripts/python.exe` 执行所有测试；不新增依赖，不改全局环境。
- 保留原有每日界面、价格编辑、置顶、缩放及自动刷新。

## 任务 1：读取器小时汇总（委派）

文件：`usage_note/reader.py`、`tests/test_reader.py`。

接口：`scan()` 保留 `days` 并新增 `hours: dict[str, dict[str, dict]]`，小时键为本地小时起点 `datetime.isoformat()`，例如 `2026-09-13T10:00:00-05:00`；行结构与 days 相同。

- [x] 先增加同小时、多小时、跨日、去重及排除模型回归，运行并确认失败。
- [x] 在现有 `_covered_events` 后、`EXCLUDED_MODELS` 过滤后聚合日和小时，保持增量读取机制。
- [x] 运行 `.venv/Scripts/python.exe -m unittest discover -s tests -p test_reader.py -v`，确认小时合计与日合计一致。

## 任务 2：日历区间、曲线与主界面（主代理）

文件：新建 `usage_note/periods.py`、`usage_note/charts.py`、`tests/test_periods.py`；修改 `usage_note/ui.py`、`tests/test_ui_layout.py`、`app.py`。

接口：`period_bounds(anchor, mode)` 返回开始日、结束日（不含）；`move_period(anchor, mode, amount)` 返回新锚点；`period_models(data, anchor, mode)` 合并 daily 行；`period_buckets(data, anchor, mode, now=None, tz=None)` 返回 `{at, end, models, current}` 列表，周使用 hours、月使用 days。

- [x] 为跨年周、闰年二月、月份翻页、空桶补齐、未来截断及小时／日总量一致性增加单元测试。
- [x] 实现本地日期边界与 UTC 小时步进；累加 input/cached/output/cache_write/reasoning/requests，max_request_input 取最大值，元数据列表并集。
- [x] Tk 主窗增加日／周／月按钮；导航按选中单位翻页，返回今日／本周／本月；汇总与模型明细统一使用 period_models。
- [x] 将两个曲线 Canvas 放在现有滚动内容顶部，每个点通过现有 summarize_day 计算 token 与美元，支持悬停提示；绘制过去空桶，保留未来区域空白。
- [x] CLI `--date` 同步过滤 hours，避免日期报告混入其他日期。
- [x] 运行新增单元与现有测试，GUI 验证日／周／月切换、默认／最小尺寸、空数据及部分报价。

## 任务 3：集成、审查与交付

文件：`README.md`、`docs/verification.md`。

- [x] 真实日志验证每个区间的曲线 Token／美元之和与顶部统计一致，并确认所有桶排除 codex-auto-review。
- [x] 检查本轮完整变更、运行完整 unittest 与 compileall；修复发现的问题后只重复相关检查。
- [x] 重启该便签实例以加载变更，实机检查周／月曲线并记录验证结果；更新用法文档。

该目录不是 Git 仓库，不创建提交或 PR。

## 追加规格：额度类别

用户截图明确要求区分通用周额度、GPT-5.3-Codex-Spark 的 5 小时与周额度。实际日志验证通用 ID 为 `codex`，Spark 为 `codex_bengalfox`。按额度 ID 各自保存最新快照，界面展示分组、实际窗口时长、剩余比例、重置时间和各自快照时间。其他 ID 单列；缺失窗口不补造，缺失比例显示未知。

- [x] 读取代理新增 `latest_rate_limits_by_id` 及独立 `quotas.py` 分组函数，涵盖同文件、跨文件、无 info 事件与缺失 ID 回归。
- [x] 主界面按独立额度组展示；Tk 回归检查三条不同窗口及比例。
