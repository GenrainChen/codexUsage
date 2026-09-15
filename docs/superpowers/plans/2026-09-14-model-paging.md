# 模型翻页与当前账号 Implementation Plan

> 执行方式：按用户 AGENTS.md，由主代理通过 Codex 原生协作工具委派独立模块并整合。不采用 Superpowers SDD / Inline 框架；本会话可使用原生协作工具，无法确认 Ultra 档位。

**Goal:** 模型明细一次显示一张卡、左右翻页；日志额度区仅展示当前登录邮箱及可读取的套餐，不拆分多个账号。

**Architecture:** 保留 Tkinter、既有统计与三类额度分组。账号模块只读当前 Codex home 的 auth.json，提取展示元数据，在刷新线程内读取；不保存或输出凭据。分页保留当前模型身份，自动刷新重新排序时不跳卡。

**Tech Stack:** Python 标准库、项目 .venv、Tkinter。

**Spec:** 本轮用户请求及后续简化：只显示当前账号，不做多账号区分；保留东八区、周月曲线、auto-review 排除口径。

## Task 1: 当前账号只读元数据（原生子代理）

Files: 新建 usage_note/account.py、tests/test_account.py。

Interface: `read_current_account(codex_home: Path) -> dict` 返回 `status`（signed_in/api_key/unavailable）、可选 email 与 plan；禁止返回或持久化任何 token、原始 JWT、账号 ID。

- [x] 写并运行用假 JWT 的测试：邮箱/套餐、切换后重读、损坏/缺失登录文件、API key 模式、字段白名单。
- [x] 实现本地安全读取；只提取邮箱和套餐，未知套餐不猜 X5 / X20。
- [x] 使用 `.venv/Scripts/python.exe -m unittest discover -s tests -p test_account.py -v` 验证。

## Task 2: 模型卡片分页与账号展示（主代理）

Files: usage_note/ui.py、tests/test_ui_layout.py。

- [x] 增加模型前后页按钮与 `1 / N`，每次只渲染一个模型，首尾按钮禁用，无数据为 `0 / 0`。
- [x] 保留当前模型名作为选中状态，刷新重排时不跳页；切换日期导致模型消失则回到第一张。
- [x] 在额度标题下显示当前登录邮箱、套餐；长邮箱换行。额度仍是未按账号归属的历史快照，保留一句简洁说明。
- [x] 在刷新线程读取账号并更新展示；离线登录信息不可用不阻塞统计。`--report` 保持纯计量输出，不增加邮箱。
- [x] 验证分页/重排/空数据/首尾状态，以及账号切换、最窄窗口文字可读。

## Task 3: 整合与交付（主代理）

Files: README.md、docs/verification.md。

- [x] 修正文档关于 auth.json 的说明，只读取邮箱/套餐元数据，不联网、不写回登录文件、不保存凭据。
- [x] 运行全部含 GUI 的回归及 compileall，复核单卡展示与当前账号文案。
- [x] 重启便签，检查真实界面。旧窗口两次无法激活，改为只重启本项目进程；新版窗口已实际验证左右翻页。无 Git 仓库，不提交或创建 PR。
