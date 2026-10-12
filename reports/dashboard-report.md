# 本地管理网页交付记录

日期：2026-10-06

## 实现内容

- 提供仅监听 `127.0.0.1` 的本机控制台，含模块开关、只读检查、手动运行、停止、历史结果、事件与截图查看；单封模块默认关闭，批量邮件模块默认开启并标注“有效奖励批领待实机验收”。
- 作业启动使用 request_id 幂等。D1 修复在 `app.js` 第一次 await 前同步写入待提交状态，按钮启用条件和事件处理同时检查；网络层重试复用同一个 request_id，结果不确定时保留请求并提供同 ID 恢复入口，刷新只恢复状态而不自动再提交。
- D2 在活动作业中显示当前作业 progress，只有服务端按 dashboard_job_id 与 run_started 工件精确关联时才显示计数；最终结果显示 result 审计字段。未知、零和已记录上界分别呈现，分类计数没有记录时显示“未记录”。
- D3 将配置 JSON 根、profile/device/budget 结构及必要字段校验限定在档案可用性检查内；坏档案报告为不可用，不影响状态接口及其他档案。偏好档案 ID 先校验字符串类型再检查白名单。
- D4 将历史 run 目录、JSON、事件和截图读取约束到已登记 artifacts 根及 run 目录内；目录链接越界会跳过或受控报告，不影响正常记录列表。
- 历史事件显示真实 node 与允许的 detail 关键字段。保留既有 runner 的任务、锁、预算与结果契约。
- 增加 `dashboard` CLI、本地 `start-dashboard.cmd` 一键启动入口和 README 使用说明。设置保存在被忽略的 `config/dashboard.local.json`，网页没有修改设备档案。

## 地址与启动

原本地服务已在代码更新后正常重启：`http://127.0.0.1:8782/`。启动入口为项目目录的 `start-dashboard.cmd`；也可从项目目录执行：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper dashboard
```

服务通过服务终端的 `Ctrl+C` 正常停止；有活动任务时会先请求运行器停止并等待终态，不强杀线程。端口冲突会报告，不终止占用进程。

重启后的只读 `GET /api/state` 返回 `active_job: null`、`device_status: not_checked`，批量邮件开关开启、单封模块关闭。未向真实 8782 服务提交任务。

## D1—D4 验证

针对性命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -p no:cacheprovider -o addopts= -q
node tests\dashboard_app_probe.cjs src\sgbltx_helper\dashboard\static\app.js
```

输出：`38 passed in 5.64s`；Node 实际 app.js VM 探针输出 `{"rapidSubmissionCount":1,"retryRequestIdsSame":true,"refreshDoesNotSubmit":true,"progressShowsUnknown":true,"finalAuditZeroPreserved":true}`。Node 假 DOM 探针不是浏览器点测；D3、D4 的链接及损坏配置回归都使用临时数据，不触碰正式 artifacts。

完整测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
```

结果：`195 passed in 38.98s`，退出码 0。

## 临时服务浏览器检查

使用独立临时服务 `127.0.0.1:12372`、临时设置/工件和假 executor，在 Codex 内置浏览器检查实际网页行为：

- 启动 POST 人为延迟期间，运行按钮立即禁用；活动页显示已记录输入 2 / 上限 20、导航 2，未提供的领取/关闭分类显示“未记录”。
- 点停止后页面显示 `stop_requested`，停止按钮禁用；刷新后同一作业与进度仍可见，不自动重复提交。假执行器调用计数为 1。
- 临时完成标志触发后页面显示 `cancelled`，保留导航 2、领取 0、关闭 0 和审计输入 2。
- 临时浏览器标签和服务均已关闭。此点测覆盖了取消终态，没有模拟 stop_unconfirmed；没有连接 Maa、设备或真实游戏。Node VM 探针另测了响应丢失后的相同 request_id 恢复，不代替浏览器请求不确定路径点测。

## 历史基线与业务状态

早期网页检查记录的 `179 passed` 和上一轮 `195 passed` 均为历史基线；本轮最新全套结果见下方 R1—R3 验证记录。批量邮件保持部分验收：已有无奖励路径证据，仍缺有效奖励条件下的真实批量领取成功验证。管理页与假执行器验收不构成游戏领取证据。

## 上一轮验证边界（R1—R3 新验证见下文）

- 上一轮作业启动/网络丢失的 same-ID 恢复只有 Node 实际 app.js 探针覆盖；当时浏览器点测覆盖延迟提交、停止、刷新和取消终态。下文 R1—R3 补充了临时浏览器响应丢失、跨实例恢复与 `stop_unconfirmed` 文案检查。
- 网页可以对登记任务发起真实只读检查或执行。运行前仍需确认角色与设备；此轮未通过网页检查或操作设备。
- 有效奖励的批量领取仍待实机验收；历史单封邮件结果和临时假执行器结果都不能替代该验收。

## 2026-10-06 补充修正：管理首版 R1—R3

### R1：服务实例绑定恢复请求

- `dashboard.server` 每次启动生成新的随机 `instance_id`，`GET /api/state` 返回该值。它只用于区分服务进程，不替代本机 token / Host / Origin 检查，也不是认证凭据。
- 网页提交和 sessionStorage 中的原始 payload 都带 `instance_id`。同一服务实例的网络重试保持原 ID；刷新不自动 POST。发现服务实例变化或收到 `stale_instance` 后，网页把记录标成“旧实例、结果无法确认”，禁用恢复按钮。
- 增加“放弃恢复记录”入口。它只清除本浏览器的恢复状态，不代表旧动作撤销或未发生；新作业仍需另行显式启动。
- 服务端在 `JobManager.start` 前核对实例 ID，旧请求返回 HTTP 409 / `stale_instance`，不会进入新 JobManager。
- HTTP 回归真实发送首次 POST 后关闭客户端连接，等待假 executor 完成，重建服务，再携旧 ID 提交：新服务明确拒绝，执行器总调用数仍为 1。既有同实例同 request_id 幂等、响应丢失重试和快速双击测试保留。

### R2：终态上界与输入不确定性

- `app.js` 终态根据 `detail.action_count_is_upper_bound`、`detail.input_outcome_uncertain` 区分“审计输入 N”和“输入计数上界 N（实际输入未确认）”；缺值仍显示未记录，0 保留为 0。
- 分类字段显示“导航记录 / 领取记录 / 关闭记录”，表达已记录的分类计数，不把它们包装为成功动作。
- 实际 app.js Node VM 回归输入 `needs_attention / stop_unconfirmed / action_count=1 / upper_bound=true / uncertain=true`，断言页面显示上界及未确认文案；也覆盖普通完成、审计 0、未知分类和活动进度。

### R3：非有限预算隔离

- 合同整数解析窄范围捕获 `OverflowError` 并转成配置契约错误；没有截断 `1e999`，也没有用默认预算继续执行。
- 临时 HTTP 回归分别把原始 JSON 的 `timeout_seconds`、`max_recoveries`、`max_actions` 写为 `1e999`：每例 `/api/state` 都返回 200，坏档案不可用、健康档案可用，提交坏档案被拒绝且 fake executor 调用数为 0。未读取或改写正式配置。

### 验证记录

针对性命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -p no:cacheprovider -o addopts= -q
node tests\dashboard_app_probe.cjs src\sgbltx_helper\dashboard\static\app.js
```

输出：`42 passed in 8.22s`；Node 探针：`{"rapidSubmissionCount":1,"retryRequestIdsSame":true,"refreshDoesNotSubmit":true,"progressShowsUnknown":true,"finalAuditZeroPreserved":true,"upperBoundExplained":true,"oldInstanceNotResubmitted":true,"abandonRecoveryAvailable":true}`。

完整命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
```

结果：`199 passed in 41.53s`，退出码 0。完整套件包含调用实际 `app.js` 的 Node VM 回归；它不是浏览器检查。

临时浏览器检查使用 `tests/dashboard_revision_browser_server.py` 在随机回环端口运行两个服务实例、临时设置/工件和 fake executor。实际页面先经历双响应丢失，随后服务重建；状态轮询将待恢复请求标为旧实例、禁用恢复，并显示结果无法确认。点击“放弃恢复记录”后页面说明该操作不撤销旧动作、恢复区收起；重新加载以取得新服务 token 后，另行显式启动假任务，页面显示 `stop_unconfirmed` 的输入计数上界文案。fake executor 总调用数为 2（第一实例已结束的作业 1 次，清除记录后显式新作业 1 次）；跨实例旧请求没有触发第二次调用。临时标签及服务已关闭，无端口残留。未访问游戏或 8782 提交任务。

### 正式服务

按原服务会话正常 Ctrl+C 停止后，使用以下命令重新启动：

```powershell
.\.venv\Scripts\python.exe -u -m sgbltx_helper dashboard --no-browser
```

只读状态接口确认 8782 已监听、`active_job: null`、`device_status: not_checked`，批量邮件开关开启、单封开关关闭。地址仍为 `http://127.0.0.1:8782/`。没有清除设备禁用标记或提交真实任务。

批量邮件有效奖励领取仍待实机验收；本次修复和 fake executor 证据不改变该业务状态。

## 2026-10-07 日常奖励模块复审补充

日常模块明确显示四项已登记来源、原生一键领取路线、仅在日常页校准过的攻城令公告跳过规则，以及 partial 状态。没有加入任务自动完成或活跃宝箱能力。

作业和历史详情分别显示领取确认、来源批后状态、返回主城、公告处理、cleanup-only 标记及分类输入计数；独立收尾不改变原领取结果。活动进度覆盖日常导航、领取、关闭、公告跳过。实际 app.js Node 回归验证读取历史不会启动游戏作业。

根聊天完整测试：`258 passed in 98.78s`，退出码 0；定向 dashboard 进度/实际 app.js 回归 `4 passed, 40 deselected in 0.33s`。新增日常安全回归七项通过。没有操作真实游戏。

旧 8782 服务的模块注册仍只有邮件。自动审批拦截停止该旧服务与另行启动 8783 新服务，未提供具体原因；均未执行。已准备 `start-dashboard-daily.cmd`，用户可在本机启动 8783 新界面。等待启动后的浏览器验收；不能把源码更新说成当前服务已更新。

侧边 8782 页已刷新，实际读取日常 cleanup-only 历史记录确认新静态详情正常显示：领取记录 0、关闭 2、领取未确认、回主城已确认。截图 `artifacts/dashboard-runtime/daily-review-history.jpg`。没有提交任何网页游戏任务；新模块卡片仍待加载新服务。

## 日常原生一键循环文案与轮次计数补充（2026-10-07）

本轮更新了 `dashboard/registry.py`、`dashboard/jobs.py` 和 `dashboard/static/app.js`：日常模块不再标成固定四项来源，显示顶部一键领取可按新鲜绿态循环至灰态、每次最多 5 轮；作业/历史详情显示批领轮次及已确认、不确定、进行中数量。实际 app.js Node 探针退出码 0；Dashboard 进度 pytest `4 passed, 40 deselected`；全套 pytest `267 passed in 94.53s`。

8783 的只读状态仍显示服务进程 PID `192816` 已加载旧模块文案和旧 registry（没有 `max_batch_rounds`）。没有提交网页任务、停止或强杀此进程。本轮未进行浏览器点测；需从原服务终端正常重启后，新页面文案才会生效。
