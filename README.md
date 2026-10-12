# 三国兵临天下辅助（本地奖励领取原型）

这是一个本地 Python + MaaFramework 工具，含邮件任务、日常任务奖励模块和本机管理网页。`claim_primary` 是“太守奖励”单封原型；`mail_bulk_claim` 是系统邮件“一键阅读”批量流程；`daily_rewards_claim` 按顶部原生绿/灰“一键领取”状态循环、逐张关闭奖励并返回主城，真实有效奖励闭环仍待实机验收，状态为 partial。

源码仓库：[`liningshuai/sgbltx-helper`](https://github.com/liningshuai/sgbltx-helper)，私有。仓库包含代码、测试、识别模板与实现报告；本机设备配置、运行截图、日志、虚拟环境及构建缓存不上传。克隆后需按 `config/example.json` 创建自己的本机配置。依赖历史实机截图的离线回放测试在缺少本机工件时会跳过，不能以跳过结果代替实机验收。

## 环境

- Windows 10/11，Python 3.13（当前验证环境）。
- 雷电模拟器及可用的 ADB；设备序列号、ADB 路径、分辨率和包名由本地档案指定。
- MaaFramework Python 绑定固定为 `MaaFw==5.14.2`。

在项目根目录创建隔离环境并按锁文件安装：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

锁文件使用项目相对 editable 安装项 `-e .`，因此命令须从本目录执行。也可按 `pyproject.toml` 安装运行与开发依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 当前业务范围与人工步骤

本原型**不是完整无人值守日常**：

- 先由用户确认正确角色已登录并选择设备；本程序不自动登录、不抢号。
- `claim_primary` 仍要求用户手动从“更多 → 邮件”打开当前配置的“太守奖励”详情，不从主城导航。`mail_bulk_claim` 的流程为主城→更多→邮件→系统页签→一键阅读一次→逐张识别并关闭奖励弹窗→确认邮件列表→邮件窗口红色 X→截图确认主城。批量奖励及连续弹窗素材已有真实截图回放，但本轮没有完成 runner 自动领取实机验收。
- `daily_rewards_claim` 只按已验证的日常页身份和顶部绿色/灰色“一键领取”控件决定是否领取。每轮最多提交一次，奖励逐张重新识别后安全关闭，页面仍为绿色时才允许下一轮；单次运行最多 5 轮，灰态后返回并确认主城。固定四行、任务名称和完成计数不作为门槛。未知奖品仍尝试关闭，但不确认领取成功。不逐项点击，不执行“前往”、购买、升级、派兵或战斗。网页模块默认关闭，开关本身不会运行游戏任务。
- dry-run 只读取当前画面，不输入、不导航。
- execute 仅在此邮件详情身份和可领按钮同时命中时提交一次 Click。成功弹窗已出现后再次运行会返回 `nothing_to_claim`，不会提交 Pipeline。
- 遇到账号冲突、陌生页面或状态不确定时停止。当前样本只覆盖 `1080x1920` 的已测界面。

批量邮件任务仍处于实机验收阶段。主城识别采用固定资源 HUD 与稳定城墙局部的 AND 判据，并独立识别 More 图形中心。确认奖励弹窗身份和关闭提示后，即使物品模板未命中也可安全收尾，但保留领取证据不完整，不报告 `completed`。关闭后的旧帧和奖励前景动画只读等待，受单步窗口、整轮截止时间和停止信号限制；未知页面及顶号提示不授权输入。若任务启动时已在奖励弹窗，则进入 `cleanup_only`：只关闭奖励及邮件回主城，即使列表有新邮件也不点一键阅读。实机收尾 `run-20261007T034925Z-202688` 为领取 0、关闭 2、`cleanup_verified=true`，最终截图识别为 `main_city`；此前领取结果仍不确定，新一轮完整批领及重复运行未验收。等级与 alias 仅作样本/工件元数据，不用于认证。详情见 [`reports/mail-bulk-report.md`](reports/mail-bulk-report.md)。

“太守奖励”新邮件已在成功模板预先登记后完成实机闭环：execute 自动返回 `completed/claim_confirmed`、action_count=1；同封邮件再次运行返回 `nothing_to_claim`、action_count=0。此前“国庆同庆，三国献礼！”首次领取的原始 `result.json` 仍保留 `needs_attention/outcome_uncertain`，没有改写。单封人工定位领取验收已完成；主城导航、其他邮件和无人值守日常仍不在本原型范围。详情见 [`reports/phase1-report.md`](reports/phase1-report.md)。

## 本地管理网页

启动后默认打开 `http://127.0.0.1:8782/`，服务只监听本机回环地址。默认端口选为 8782，因为当前 Windows 的 TCP 排除区间包含 8765；服务不会尝试占用受限端口。网页可以保存模块开关、只读检查、手动启动已登记任务、请求停止，并查看运行结果和截图。网页启动、刷新、状态轮询和模块开关保存都不会连接设备或发送游戏输入；只有用户明确点击任务按钮才调用现有运行器。

在项目目录双击 [start-dashboard.cmd](start-dashboard.cmd)，或在 PowerShell 执行：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper dashboard
```

停止服务请回到启动窗口按 `Ctrl+C`。若任务正在运行，服务会请求同一个运行器停止，并等待其返回终态；不会强杀线程或清除设备禁用标记。需要自定义端口或不自动开浏览器时：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper dashboard --port 8782 --no-browser
```

默认测试档案为 `local-test-mail`。档案别名只是元数据，不认证游戏角色；启动时设备状态显示“尚未检查”。批量模块始终标记“有效奖励批领待实机验收”；网页开发验收使用假执行器和已有工件，不代表批量领取业务完成。设置保存在被忽略的 `config/dashboard.local.json`，不覆盖两个设备档案。

常规测试命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
```

## 命令

本机配置放在被忽略的 `config/local.json`。先用只读 dry-run 检查当前画面：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local.json --task claim_primary --dry-run
```

仅当 dry-run 在正确的邮件详情页同时识别指定主题及绿色“领取”按钮、没有账号冲突或其他阻断时，才执行：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local.json --task claim_primary --execute
```

不要对已领取邮件或未知状态重复执行。若真实奖励条件不存在，可只做 dry-run 和离线测试，并将成功 execute 验收保留为未验证。

遇到重复登录或身份不明时停止并由用户处理。若运行结果为 `stop_unconfirmed`，对应设备会被本地禁用，并在 `artifacts/locks/` 留下 `.stop-unconfirmed.json` 标记。先人工确认上次任务确已停止并完成设备恢复，再调用 `RunLock.clear_stop_unconfirmed(lock_dir, device_serial)` 清除标记；仅重试命令不会解除禁用。

测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

批量邮件使用单独的被忽略档案 `config/local-test.json`（`profile_id=local-test-mail`、`account_alias=test_mail`），设备仍为 `emulator-5554`。匿名别名只作工件元数据，不认证游戏身份；档案与其他任务共用 `artifacts/` 和设备锁，同一设备仍互斥。确认正确角色已登录后再 dry-run：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local-test.json --task mail_bulk_claim --dry-run
```

批量 execute 命令（仅在角色身份确认、dry-run 页身份明确且动作素材均通过验证后运行）：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local-test.json --task mail_bulk_claim --execute
```

批量任务限制为系统邮件“一键阅读”一次，不逐封打开、不处理私聊、不删除邮件。每步输入后重新截图；批领请求一旦提交，即使结果不确定也不会重试。识别不到批量后空状态时不会返回 `nothing_to_claim`；领取确认后若收尾未确认，结果保留领取证据并返回 `needs_attention`。验证状态见 [`reports/mail-bulk-report.md`](reports/mail-bulk-report.md)。

## 日常任务奖励（有效奖励实机闭环待验收）

模块使用 `daily_rewards_claim`，在网页中需显式开启后，再单独点击只读检查或运行。执行从主城时会按已识别入口导航；每轮绿色一键领取最多提交一次，待本轮奖励出现、逐张关闭并重新识别日常页后，只有顶部仍明确绿色才可进入下一轮。最多五轮；灰色按钮停止循环并安全回城，起始灰态才返回 `nothing_to_claim`。已登记随机军令公告变体作为阻断，只有底层日常页与完整公告身份明确、空白目标命中时才点空白一次；不点“前往”。遇到未知页面、绿灰同时命中、其他阻断或不完整奖励流程，不会盲点或重试。物品名称未知不影响已确认的控件流程。CLI 调用也会显式启动：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local-test.json --task daily_rewards_claim --dry-run
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local-test.json --task daily_rewards_claim --execute
```

领取只依赖当前日常页身份及顶部明确绿色的一键领取；四个来源项目、任务名称与行内按钮均不是门槛。每个已提交轮次必须观察到奖励前景、关闭提示并安全关闭，重新识别日常页绿/灰状态；最后确认灰态并返回主城才报告 `completed`。物品匹配作为附加记录，未知物品不会阻止上述闭环成功。`4/23` 是任务完成计数，不用于领取成功判断。

奖励关闭后只读等待：像素变化、动画和部分前景特征闪烁均不授权再次关闭。连续已知奖励须有旧物品身份消失与新身份正命中的更替证据；相同或未知内容须先观察到明确露出的日常页。若新旧奖励无法区分，则有界等待后保留不确定结果，不重复点击。A→B→A 不被全轮内容去重拦截。启动时已在奖励弹窗只执行 `cleanup_only`，不进入新的领取轮次。

遇到随机“攻城令发布/前往”公告时，程序绝不点击“前往”。在日常任务页上，已登记公告身份和标题左侧的空白装饰横线；execute 仅在公告明确命中且该空白模板命中时尝试点击一次，随后重新截图，只有公告消失且日常页身份仍成立才继续。dry-run 始终零输入。其他页面需要各自验证空白目标；没有经过验证的目标时会停止，不猜坐标。完整容错规则见仓库 [`AGENTS.md`](AGENTS.md)，实机与未验收状态见 [`reports/daily-rewards-report.md`](reports/daily-rewards-report.md)。

2026-10-07 首次批量日常领取提交一次后结果为 `needs_attention/outcome_uncertain`；原始结果保留且未重试。另一次 cleanup-only 已关闭奖励弹窗与日常页并验证回到主城，领取动作数为 0。首次结果缺少来源项目批后状态，故重复运行领取 0 验证尚未执行，模块仍为 partial。

主聊天复审补充（2026-10-08）：公告部分命中也会阻止背景操作。绿色未命中不等于灰态，需正向灰态识别。停止、超时、点击前复核或截图失败形成终态后不再发送输入；终态台账先归一再深拷贝，返回对象与落盘结果一致。修复经过离线回归，尚无本轮新的实机正向领取证据；历史 uncertain 结果保留。

网页模块卡片显示一键领取循环、日常页公告跳过限制和部分验收状态；当前作业及历史详情显示流程已确认轮次、批后状态、返回主城和公告处理，物品名称不影响确认计数。`cleanup_only` 明确显示为仅收尾，不改写先前领取结果。模块仍默认关闭。

旧 8782 服务仍运行旧的模块注册信息，需要重新启动才能读取新模块。本轮自动启动被审批拦截；可双击 [start-dashboard-daily.cmd](start-dashboard-daily.cmd) 在 `http://127.0.0.1:8783/` 启动更新后的助手。此入口只启动本机网页，不连接设备；保留启动窗口，按 `Ctrl+C` 停止。原入口和默认 CLI 端口仍为 8782。
