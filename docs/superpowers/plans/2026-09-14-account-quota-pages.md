# Account quota pages implementation plan

> 依用户 AGENTS.md，使用 Codex 原生协作工具委派独立模块，主代理整合；不使用 SDD / Inline 框架。原生协作可用，无法确认 Ultra 档位。

**Goal:** 额度按账号左右翻页，当前登录账号为第一页，其余按最早重置时间升序。

**Architecture:** 通过本机 codex app-server stdio 的 account/read 与 account/rateLimits/read 查询已登录账号。已实测响应包含 accountId，可以和本地 auth.json 的账号 ID 比对；持久化只保存 ID 的 SHA256、邮箱、套餐、时间、额度窗口。不将无账号 ID 的历史日志归给任何账号。

**Tech stack:** 项目 .venv Python 标准库、Tkinter、已有本机 codex.exe；无新增依赖。

**Spec:** 用户本轮要求和补充“左右翻账号”。只调整额度区域的账号范围，日/周/月模型 Token/刊例价仍为全部本机日志。账号切换必须在 Codex 中进行；便签翻页只查看快照。

## Task 1: 可信当前账号额度读取（原生代理）

Files: usage_note/account.py、usage_note/account_limits.py、tests/test_account_limits.py。

Interface: `read_account_identity(home: Path) -> dict` 现有账号元数据 + 可选 `key` 为账号 ID SHA256。`fetch_account_limits(home: Path, *, timeout=15) -> dict` 返回 `{key,email,plan,at,limits}`，limits 兼容 reader.latest_rate_limits_by_id（snake_case 额度、at）。失败抛 `AccountLimitsError`，错误不可含原始凭据/服务响应。CLI 路径先 PATH，再用户 LocalAppData/OpenAI/Codex/bin 下已安装版本。每次请求创建隐藏 stdio 子进程，限定总超时，退出时清理。

- [x] 假 stdio 服务/注入通道测试握手、响应筛选、超时、清理、账号 ID/前后身份不一致丢弃、窗口字段归一化、凭据字段白名单。
- [x] 实现 account/read(refreshToken=false) 前后夹住 rateLimits/read(excludeResetCreditDetails=true)；响应 accountId 与本地身份匹配才返回。不得启动任务、发送消息、切换账号或消费重置。
- [x] 验证模块；不要在测试里请求真实账号。

## Task 2: 快照保存与排序（主代理）

Files: usage_note/account_store.py、tests/test_account_store.py。

Interface: `AccountStore(path)`，`remember(snapshot)` 原子保存白名单快照，`pages(current:dict, now:datetime) -> list[dict]` 返回账号页，当前账号占位也在第一；其他账号按所有已记录窗口中最早重置时间（包括已到期时间）升序，未知时间排最后，key 作为稳定平局。过期窗口标记“已到重置时间”，不得把旧百分比当成新周期额度。

- [x] 测试当前优先、排序、未知/过期时间、跨重启保存、多个额度 ID、不存凭据、损坏文件读取提示。
- [x] 快照仅接受成功归属的结果；失败保留该账号上次快照和时间。

## Task 3: UI 与调度整合（主代理）

Files: usage_note/ui.py、tests/test_ui_layout.py、README.md、docs/verification.md。

- [x] 使用独立账号页状态和按钮，当前第一、翻页一次只显示一账号。登录变化回第一页；普通刷新保留正在看的账号。显示当前/历史、更新时间和快照状态。
- [x] 当前账号额度每 60 秒查询一次；手动刷新和检测到登录切换立即查询。统计扫描不受额度查询失败影响。仅默认桌面 UI 创建该服务；--report 继续不含账号、不联网。
- [x] 账号查询失败显示该账号的缓存或明确“尚无快照”；原始日志额度独立作为未归属记录可展开查看，不混入任何账号页。
- [x] 验证多账号翻页、排序变化与选中保留、空数据、长邮箱、过期状态，以及现有窗口/曲线回归。
- [x] 审查修复：同一账号优先显示已核对快照里的服务端套餐，防止本地登录缓存覆盖。
- [x] 审查修复：刷新线程改为非 daemon，正常关闭时允许有界请求完成 finally 清理，不留下孤儿服务；关闭后也不再写快照，避免旧实例覆盖新缓存。两个进程级退出回归通过。
- [x] 审查完整变更并修复两项问题，110 项检查通过；真实查询并启动新版，确认使用已保存的 958x1000 窗口位置。实际界面为当前账号第 1 / 1 页，三类额度齐全；其他账号需要之后各登录并刷新一次收录。
