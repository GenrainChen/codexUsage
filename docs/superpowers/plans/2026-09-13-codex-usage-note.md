# Codex 用量便签实施计划

> 使用当前会话的 Codex 原生多代理工具协调和并行执行；不进入 Superpowers Subagent-Driven / Inline 框架。当前可确认原生多代理能力，无法直接确认 Ultra 档位。

**目标：** Windows 可置顶便签显示本机 Codex 每日、每模型的输入／缓存／输出 token 和美元刊例价估算。

**架构：** Python 标准库读取本地 Codex JSONL，按事件时间的本机时区汇总。Tkinter 提供可拖动缩放、置顶和自动刷新的桌面界面；读取后台执行，保持交互流畅。报价有明确来源，未知模型支持手动报价。

**技术：** 项目独立 `.venv` 中 Python 3.13、Tkinter、unittest、JSON；不安装全局依赖。

**规格：** 用户本轮需求：常驻屏幕、查看每日模型和 token、分输入／缓存／输出、美元刊例价。默认桌面便签，可浏览历史日期；金额并非订阅扣款。

## 共同约束

- 仅只读 sessions/archived_sessions 必要元数据；不读取凭据、不上传对话。
- 缓存输入是总输入的子集，普通输入 = 总输入 - 缓存输入；reasoning 不重复加到 output。
- 不能将累计用量事件直接相加；处理重复事件、模型切换、跨日、累计重置及分叉复制。
- 日期使用事件时间换算至电脑本地时区。
- 不将未知模型价格映射到其他模型。当前标准 API 刊例价估算，不声称精确账单。
- 先写有意义的计数／计价测试并观察失败，然后实现。

## 任务 1：读取与聚合（数据代理）

文件：`usage_note/reader.py`、`tests/test_reader.py`。

接口：`UsageReader(codex_home: Path).scan() -> dict`，返回 `days`（日期到模型到 input/cached/output 整数）、`latest_rate_limits`、`latest_event_at`、`files_scanned`、`warnings`。每个模型可附 `requests`。input 表示未缓存输入。

- [x] 调查本机 JSONL 必要结构，记录重复和累计字段语义。
- [x] 用临时目录的手工事件测试累计差分、重复、日期、缓存、模型切换、截断末行、分叉及归档。
- [x] 运行 `.venv/Scripts/python.exe -m unittest discover -s tests -p test_reader.py -v`，确认预期失败。
- [x] 实现增量读取（文件大小／mtime 缓存），只解析必要元数据；再跑测试。

## 任务 2：报价与计费（主代理，报价代理提供来源）

文件：`prices.json`、`usage_note/pricing.py`、`tests/test_pricing.py`。

接口：`PriceBook(path, overrides_path).quote(model, tokens) -> dict`，含 `input_usd/cached_usd/output_usd/total_usd`（未知为 null）、`rates`、`source`。`set_override(model, input, cached, output)` 保存本地报价。

- [x] 核实实际模型官方公开价格，记录 URL 和日期。
- [x] 手工验算：1M 普通输入 × $2、2M 缓存 × $0.2、0.5M 输出 × $8 = $6.4；未知价返回 null。
- [x] 测试失败后实现报价、合法非负金额校验及本地覆盖保存；重跑测试。

## 任务 3：便签界面（界面代理）

文件：`usage_note/ui.py`；消费 reader 和 pricing 的上述接口。

- [x] 以暖白纸张、深墨色、橙色强调构建约 420×680 桌面便签。
- [x] 显示日期切换、今日总量和已知价格小计、三种 token／费用、每日模型明细。
- [x] 提供置顶、手动刷新、30 秒刷新、价格编辑、日期选择；后台读取并展示异常和数据时间。
- [x] 真实数据启动检查布局与交互，覆盖空数据和未知价格。

## 任务 4：集成与交付（主代理）

文件：`app.py`、`start.vbs`、`README.md`、桌面快捷方式。

- [x] 接入模块和本地设置；启动器始终使用项目 `.venv` 的 pythonw。
- [x] 执行完整 unittest 和真实数据只读扫描，核对关键总数。
- [x] 独立代码审查计数准确性与启动可靠性并修正。
- [x] 启动可见便签并检查，创建桌面快捷方式，说明计费口径与使用方式。
