# 系统邮件一键领取验收报告

更新：2026-10-06（本次工件 UTC 日期为 2026-10-07）。状态：**partial；残留奖励自动收尾与返回主城已实机验证，新的完整批量领取成功及重复运行仍待验收**。

前期导航样本来自用户确认的等级 20 测试角色；入口兼容及本次收尾画面来自用户切换的等级 45 邮件测试角色。`test_mail` alias 仅为日志/工件字段，不认证角色身份。历史批领提交的原始结果仍为 uncertain；本次接手只处理残留奖励，领取 0、关闭奖励及邮件 2，程序与截图均确认主城。下文按时间保留历史记录；最新结论见末尾“主聊天接手：安全收尾修正与实机恢复”。

## 任务范围与安全约束

- 只处理系统邮件中的附件；不进入私聊、不删除邮件、不进入其他业务。
- “更多”及“邮件”入口均由当帧真实 Maa 识别授权；每次输入后重截图并重新识别。
- 一键阅读最多提交一次；结果不确定时不重试。本轮 `claim_action_count` 始终为 0。
- 导航、检查、关闭共享设备锁、停止检查、截止时间和动作预算。批量 Pipeline 资源包通过提交前静态校验，当前配置只含零输入任务。

## 实现与测试

- `src/sgbltx_helper/mail_bulk.py` 实现有限状态导航、无奖励检查、关闭以及不确定状态停止。
- `runner.py` 接入独立 `mail_bulk_claim` 资源并检查 Pipeline bundle。页面和执行资源隔离于旧单封邮件任务。
- 当前真实画面校准新增 `city_castle_walls.png`、`more_menu_title.png`、`mail_menu_entry.png`、`inbox_bulk_button_disabled.png`。真实 Maa 回放分别检查主城、菜单、邮件页与单封奖励负样本，以及金色可用按钮与灰色禁用按钮边界。
- 新增回归覆盖主城新画面、菜单身份/入口、禁用按钮状态与负样本。fake backend 测试只验证状态机，不作为领取证据。

完整验证命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
```

结果：`142 passed in 25.98s`，退出码 0。Maa 截图回放的独立命令与结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_recognition.py::TestMailBulkRealScreenshots -p no:cacheprovider -o addopts= -q
```

最近结果：`7 passed in 6.69s`。这只证明当前模板在已有实机截图上识别有效，不是批量领取成功证据。

## 实机运行记录

设备 `emulator-5554`，包名 `com.sgbltx.goodgame`，截图分辨率 `1080×1920`。账号身份依据用户确认。以下工件保留原始截图、事件与运行结果；不覆盖早期运行记录。

| run id | 操作和结果 | 输入计数 | 工件 |
|---|---|---:|---|
| `dryrun-20261006T081100Z-179196` | 当前主城 dry-run；城池模板 `0.983`、更多图标 `0.983` 命中，账号冲突未命中；零输入。| 0 | `artifacts/runs/dryrun-20261006T081100Z-179196/before.png`、`events.jsonl`、`inspection.json`。|
| `run-20261006T081132Z-173092` | 点开更多一次。结果文件当时因菜单模板尚未登记而保守为 `needs_attention/postcondition_not_met`；真实 after 截图确认“更多”菜单确已展开。保留原始结果不改写。| 1 导航 | `before.png`、`after.png`、`events.jsonl`、`result.json` 均在 `artifacts/runs/run-20261006T081132Z-173092/`。|
| `dryrun-20261006T081619Z-185052` | 更多菜单身份与邮件入口 Maa 得分均为 `1.000`；dry-run 零输入，入口候选已验证。| 0 | `artifacts/runs/dryrun-20261006T081619Z-185052/before.png`、`events.jsonl`、`inspection.json`。|
| `run-20261006T081653Z-150896` | 点开系统邮件一次；真实 after 确认为邮件列表。此时空状态模板还未登记，旧结果保守返回 `needs_attention/outcome_uncertain`。事件显示 `claim_request_submitted=false`、`claim_action_count=0`，未点一键阅读。| 1 导航 | `before.png`、`after.png`、`events.jsonl`、`result.json` 均在 `artifacts/runs/run-20261006T081653Z-150896/`。|
| `dryrun-20261006T082354Z-183344` | 邮件页身份命中；当前列表未命中可领取附件图标。零输入。| 0 | `artifacts/runs/dryrun-20261006T082354Z-183344/before.png`、`events.jsonl`、`inspection.json`。|
| `run-20261006T082432Z-157276` | 从邮件列表关闭窗口并返回主城；结果 `nothing_to_claim`。`claim_action_count=0`、`dismiss_action_count=1`、`cleanup_verified=true`。| 1 关闭 | `before.png`、`after.png`、`events.jsonl`、`result.json` 均在 `artifacts/runs/run-20261006T082432Z-157276/`。|
| `run-20261006T082541Z-173348` | 从主城重复执行：导航到邮件，识别禁用灰色一键阅读，关闭并返回主城；结果 `nothing_to_claim`。| 共 3；导航 2、领取 0、关闭 1 | `before.png`、`after.png`、`events.jsonl`、`result.json` 均在 `artifacts/runs/run-20261006T082541Z-173348/`。|

### 邮件可领取状态的证据边界

在 `run-20261006T081653Z-150896/after.png` 中，可见邮件条目均显示红色“已失效”，一键阅读按钮为灰色。基于该原图新增 `inbox_bulk_button_disabled.png`，实机 Maa 对灰色状态命中；旧系统邮件列表中的金色可用按钮样本不命中灰态模板。程序据此不提交一键阅读，并能返回 `nothing_to_claim`。

该证据说明当前一键阅读入口不可用，不证明游戏按钮在有效奖励条件下会领取哪些附件，也不证明邮箱中所有邮件都经过独立逐项检查。灰态样本不是批量领取成功画面。

## 验收结论

| 项目 | 状态 |
|---|---|
| 授权角色样本 | 早期等级 20 角色由用户确认授权；本次等级 45 邮件测试样本来自用户切换的测试角色，alias 不作认证。|
| 主城→更多→邮件导航 | 通过；两次导航动作后真实截图识别到下一页面。|
| dry-run 零输入 | 通过；主城、菜单和邮件页 dry-run 均无输入。|
| 一键阅读真实领取 | **未验证；当前灰态，没有提交领取动作。**|
| 领取后奖励与邮件状态 | **未验证；没有批量奖励弹窗。**|
| 关闭邮件并返回主城 | 通过；列表关闭 X 命中并由后置截图确认主城。|
| 再次运行不重复领取 | 通过当前无奖励路径；重复运行 `claim_action_count=0`，总 `action_count=3`（含导航和关闭）。|
| 一键阅读覆盖范围 | 未验证；不宣称处理所有邮箱附件。|
| fake/mock | 未作为实机证据；只用于状态机回归。|

旧单封“太守奖励”实机结果在 `reports/phase1-report.md` 中单独保留，不作为本报告批量成功证据。Maa 仍会打印方向查询 ADB 子命令错误；本轮截图、识别及输入任务完成，但该环境错误尚未诊断。

## 2026-10-06 补充复审：固定测试档案与 B1—B3

本次按复审只处理固定测试档案及 B1—B3。没有连接模拟器、打开/点击游戏或重复运行灰态邮箱。当前仍无新的有效批量奖励实机结果，阶段保持 `partial`。

### 本机档案

- 新增本机忽略档案 `config/local-test.json`：`task_id=mail_bulk_claim`、`profile_id=local-test-mail`、`account_alias=test_mail`；ADB `C:\leidian\LDPlayer14\adb.exe`、设备 `emulator-5554`、包名 `com.sgbltx.goodgame`、分辨率 `1080x1920`。离线解析命令成功，字段输出与上列一致。
- `config/local.json` 未覆盖；档案使用公共 `artifacts/` 与设备锁。别名仅用于日志/工件元数据，不是认证或账号稳定身份。`.gitignore` 忽略 `config/local-test.json`。
- README 和环境说明中的批量命令已改为显式使用 `config/local-test.json`。

### B1：Controller 点击作业确认

- `MaaBackend.click` 在 Controller `post_click` 已提交后，如等待因停止/超时中断，会保留该 Controller Job 并最多等待 10 秒确认完成；确认不到时抛 `MaaStopUnconfirmed`，沿用 runner 的设备禁用标记。未调用 `Tasker.post_stop` 作为 Controller click 的取消方法。
- 已安装 MaaFw 5.14.2 的 Controller 作业接口经离线检查为独立 `Job`，可检查 `done`/`status`；没有验证到通用 Controller click 取消接口。若 native Job 长时间阻塞，Python 侧只能有界等待并标记未确认，不能声称已硬取消原生动作。
- 成功确认测试等待真实 Job 的 `done` 状态；未确认测试用有限窗口验证抛错，且不销毁/伪造 Job 完成。相关测试见 `tests/test_control.py::TestMaaStopConfirmation`。

### B2：后续识别错误保留运行状态

- 邮件导航后识别后续控件时发生 `MaaTimeout`、`MaaStopped` 或 `MaaError`，会在任务结果中保留此前导航输入计数与分类，而非按零输入生成结果。
- 已提交领取后关闭控件识别超时，结果仍记录 `claim_verified`、累计输入、未完成收尾和 `input_error=timeout`。`MaaStopUnconfirmed` 不被 mail_bulk 吞掉；runner 结果保留其上下文、计数，并写设备禁用标记。未知提交情况下 `action_count` 标为上界。
- 对应真实 runner 路径回归覆盖“先导航一次，再识别异常”及“领取后收尾识别异常”；停止未确认覆盖上下文和计数。fake backend 仅用于状态/异常路径回归，不作实机证据。

### B3：奖励内容与面板身份分离

- `reward_popup.identity` 仍用标题、关闭提示和面板边缘确认面板身份；`claim_verified` 改为至少命中一个 `mail_bulk.json::reward_items` 中 `verified=true` 的奖励物品模板。边框或关闭文字单独命中不能确认奖励。
- 新登记 6 个奖励物品图样（金币、宝箱、科研、勋章、银两、粮草），模板裁剪自两张真实单封邮件领取成功截图，使用 ROI `[120,820,840,240]` 和阈值 `0.97`。真实 Maa 离线回放命中两张单封结果。
- 无物品负样本是从真实单封奖励截图复制并遮去物品展示 ROI 的面板控制图：真实 Maa 仍识别到弹窗身份及面板边缘，但奖励物品匹配为空。它是合成控制负样本，不是游戏中实拍的无奖励弹窗。
- 批量奖励是否使用上述物品、按钮效果和批后状态仍未验证；未匹配登记物品时不得报告领取已确认。

### 验证命令与结果

离线测试档案解析及两份资源 JSON 解析成功；这不连接设备：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -c "import json; from pathlib import Path; from sgbltx_helper.contracts import load_config, RunMode; c=load_config(json.loads(Path('config/local-test.json').read_text(encoding='utf-8')),RunMode.DRY_RUN).request; print(json.dumps({'task_id':c.task_id,'profile_id':c.profile.profile_id,'account_alias':c.profile.account_alias,'serial':c.profile.serial,'package_name':c.profile.package_name,'resolution':c.profile.expected_resolution,'artifacts_root':c.artifacts_root},ensure_ascii=False))"
```

输出：`{"task_id":"mail_bulk_claim","profile_id":"local-test-mail","account_alias":"test_mail","serial":"emulator-5554","package_name":"com.sgbltx.goodgame","resolution":[1080,1920],"artifacts_root":"artifacts"}`。`mail_bulk.json` 与 `pages.json` 均通过 PowerShell JSON 解析。

定向回归命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_control.py::TestMaaStopConfirmation tests/test_mail_bulk.py tests/test_recognition.py::TestMailBulkRealScreenshots -p no:cacheprovider -o addopts= -q
```

结果：`44 passed in 12.38s`，退出码 0。

完整测试命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
```

结果：`151 passed in 27.72s`，退出码 0。该结果含离线 fake 状态机回归与本机真实截图 Maa 回放；它不代表本轮连接设备或完成批量领取。

### 新测试档案当前画面检查

用户切换到另一测试账号并手动进入“更多 → 邮件”后，本机只读 dry-run `dryrun-20261006T094103Z-188892` 识别邮件列表标题与系统页签（均 `1.000`），未命中当前截图中的附件候选图标（`0.147`），`claim_attempted=false`，没有输入。截图上可见邮件信封及“一键阅读”呈灰态。此结果只描述本次截图可见区域，不证明邮箱其他未显示/未滚动邮件均无奖励。截图、事件及检查结果保存在该 run 工件目录。

## 2026-10-06 主城入口兼容修正

按 `docs/reviews/2026-10-06-mail-bulk-entry-review.md` 仅修复主城入口识别与对应素材/说明；B1—B3、锁、停止确认、累计计数和领取契约未改。样本显示均为 `1080×1920`，没有分辨率或缩放变化。旧主城大图模板包含雾效、红色动态标记和城景背景；旧 More 模板含周边地图/状态像素。失配不是仅凭“冬季”推断，故没有降阈值放行。

### 新判据、素材与回放分数

主城页面仍采用既有 identity AND 语义，不新增 schema 或多个 `main_city` 页面：

| 条件 | 模板裁剪原图位置 | 搜索 ROI | 阈值 | 用途 |
|---|---:|---:|---:|---|
| 固定顶部资源 HUD | `main_city_resource_coin.png` `[429,132,43,51]` | `[400,110,130,100]` | `0.97` | 主城身份支持特征；单独不授权页面/输入。|
| 稳定未遮挡城墙局部 | `main_city_wall_marker.png` `[608,1060,64,64]` | `[440,850,360,420]` | `0.97` | 与 HUD 同时命中才成立，弹窗遮住此处即拒绝。|
| More 操作图形中心 | `more_action_icon_core.png` `[990,1660,40,40]` | `[940,1630,140,115]` | `0.97` | 独立的当前画面操作目标；裁剪避开通知点和地图背景，点击使用 Maa 返回的匹配框中心。|

用已安装 Maa 5.14.2 回放四张旧/新主城原图（旧样本含等级 20，不同时间的主城状态；新图显示等级 45）：

| 原图 | HUD | 城墙 | More 目标 | 分类 |
|---|---:|---:|---:|---|
| `dryrun-20261006T081100Z-179196/before.png` | 1.000 | 0.989340 | 1.000 | `main_city` |
| `dryrun-20261006T080328Z-179112/before.png` | 1.000 | 0.988208 | 1.000 | `main_city` |
| `dryrun-20261006T050617Z-179348/before.png` | 1.000 | 0.989942 | 1.000 | `main_city` |
| `dryrun-20261006T093831Z-188456/before.png` | 1.000 | 1.000 | 1.000 | `main_city` |

负样本均不分类为 `main_city`：当前邮件页（城墙 `0.202`、More `0.439`）、更多菜单（`0.432`、`0.402`）、历史灰态邮件（`0.230`、`0.343`）、奖励页（`0.351`、`0.461`）。冲突页城墙为 `0.310`，页面不分类为主城且 `account_conflict` blocker 命中；背景 More 图形自身分数 `0.998`，因此 blocker 优先和城墙身份条件是必须的，控件命中不单独授权点击。合成遮挡控制只保留真实 HUD/More 图块并遮掉城墙区域，分类为未知并选择 `stop_unknown`。它是控制样本，不宣称覆盖所有弹窗。

这组素材在自身裁剪来源外的三张旧主城上也通过；未使用降阈值、固定坐标兜底、全屏搜索或未验收的图标变体。旧 `city_castle_walls.png` 保留为历史素材，不再作为当前主城身份条件。

### 回归与命令

先加入新主城和 More 控件回归后，旧配置按预期复现：`093831` 主城身份失败、More 节点最高分 `0.851434` 低于 `0.97`；定向命令结果 `2 failed, 3 passed`。实现后同一入口回归通过：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_recognition.py::TestMailBulkRealScreenshots -p no:cacheprovider -o addopts= -q
```

结果：`14 passed in 14.05s`，退出码 0。该类回放覆盖旧主城、新主城、当前邮件、更多菜单、奖励、冲突及仅留 HUD/More 图块的遮挡控制；缺失本地原图的测试按现有规则 skip，本次这些指定截图均存在。

完整测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
```

结果：`157 passed in 34.10s`，退出码 0。

### 当前测试角色实机验证

profile `local-test-mail` / alias `test_mail` 只是工件元数据；新截图 UI 显示等级 45。此前等级 20 截图属于历史样本；等级和 alias 均未用作认证条件。

- 当前邮件列表只读检查：`dryrun-20261006T102318Z-190240`，识别为 `mail_inbox`，`no_pending=true`、可领附件候选未命中（`0.147`）、`claim_attempted=false`，事件标记只读 backend；不代表未显示/未滚动邮件均已检查。
- 关闭当前打开的邮件窗口以回到主城：`run-20261006T102434Z-175528`，`action_count=1`（关闭 1，导航 0，领取 0），`cleanup_verified=true`，后置 `final_page_id=main_city`。
- 新主城只读 dry-run：`dryrun-20261006T102612Z-188052`，两个页面身份分数均约 `1.000`，列出已登记的 More 候选，`claim_attempted=false`，零输入。此 dry-run 候选来自页面状态与配置，不是对操作控件的额外当帧匹配；More 当帧命中和导航由后续 execute 验证。
- 受控完整入口运行：`run-20261006T102653Z-179480` 返回 `nothing_to_claim`，`action_count=3`（导航 2、关闭 1、领取 0），`claim_request_submitted=false`、`cleanup_verified=true`、`final_page_id=main_city`。`events.jsonl` 依次记录 More、邮件、邮件关闭三次输入和三次页面后置识别；`before.png`/`after.png` 可核对主城，`result.json` 保留计数。没有点击灰色“一键阅读”。
- 合并两个 execute 工件的总输入为 4：导航 2、关闭 2、领取 0；其中明确要求的主城→邮件完整路径单独为导航 2、关闭 1、领取 0。dry-run 输入均为 0。

Maa 每次仍打印 `dumpsys input | grep ...` 方向查询 ADB 子命令错误，但本轮截图/识别/受控输入均完成；此环境错误未在本任务诊断。入口兼容已验证，当前灰态无待领路径已实机通过；成功批量领取、奖励内容及有效奖励后重复运行仍未验证，阶段保持 `partial`。

## 后续验收条件

等待系统邮件出现真实可批领奖励。先 dry-run 确认身份、邮件页与可领状态，再执行一次批量领取；成功后必须保留奖励前后截图、邮件列表状态、关闭后的主城、`events.jsonl` 和 `result.json`。之后从主城重复运行，并确认 `claim_action_count=0`。在这些条件完成前，阶段维持 `partial`，不能把本轮无奖励检查写成领取闭环。



## 2026-10-06 本轮批量有效奖励验收前置检查

本轮只通过正式管理网页执行了一次只读检查，没有运行 execute，也没有输入游戏。网页使用档案 `local-test-mail`（alias `test_mail`，仅工件元数据）、任务 `mail_bulk_claim`；状态接口确认初始无活动作业，结束时 `active_job=null`。档案设备为 `emulator-5554`、包名 `com.sgbltx.goodgame`、分辨率 `1080×1920`。管理页测试档案已选中、批量邮件模块开启、单封模块关闭。

- 浏览器“只读检查”返回 `main_city`，事件显示使用 `ReadOnlyBackend`，`claim_attempted=false`，未触发导航/领取/关闭输入。
- 工件：`artifacts/runs/dryrun-20261006T150021Z-183208/`，含 `before.png`、`events.jsonl`、`inspection.json`。对应 dashboard job id：`3b326e5d-9065-4d32-9a76-705f6f5f7e5b`。检查命令/接口：浏览器打开 `http://127.0.0.1:8782/` 后手动点击“只读检查”；随后 GET `/api/state` 确认 `active_job=null`。
- 识别到主城固定 HUD `1.000`、城墙 `0.999793`；账号冲突模板未命中 `0.180386`。该结果只说明本次主城起点可识别，不检查未打开的邮箱，不能据此断言存在或不存在有效邮件。
- 本轮没有提交领取，因此没有新奖励、奖励弹窗、批后邮件状态、领取后返回主城或重复执行证据。阶段仍为 `partial`，既有历史工件未改写。

继续实机验收前需要用户准备：至少一封未领取、未失效且确有附件的**系统邮件**（优先两封以验证批量处理），人工确认附件内容且“一键阅读”处于可用状态，然后返回主城；请不要手动点“一键阅读”或单封领取。准备完成后告知我即可继续。本轮没有证明邮箱中无奖励，只是不能从主城只读截图验证邮箱内容。

### 2026-10-06 用户准备后的邮件页只读确认

用户提供当前系统邮件列表截图并表示已准备。通过正式网页再次只读检查当前画面：`dryrun-20261006T150841Z-183208`，dashboard job id `28dc05ef-c69f-4832-9fff-f688f88f0957`。结果 `page_id=mail_inbox`、`claimable=true`、`no_pending=false`、`claim_attempted=false`；附件候选 `mail_attachment_seal.png` Maa 得分 `0.998006`，一键阅读列为可用候选。工件目录 `artifacts/runs/dryrun-20261006T150841Z-183208/` 保留 `before.png`、`events.jsonl`、`inspection.json`。执行后 GET `/api/state` 为 `active_job=null`。

该 dry-run 是当前邮件页的零输入识别，不能替代从主城起点的正式流程，也不把候选图标命中数解释为已核验的附件邮件总数。按验收路径，仍需从主城开始自动导航。当前画面停留在邮件页；用户需关闭邮件窗口回到主城，且不要手动点“一键阅读”或单封邮件领取。完成后方可提交本轮唯一的一键阅读请求。

### 2026-10-06 唯一一次批量领取提交：结果不确定，保留原件

用户准备后，网页从主城执行 `mail_bulk_claim` 一次。档案 `local-test-mail`，设备 `emulator-5554`，分辨率 `1080×1920`。dashboard job id `e3963197-f890-4ce9-82e2-84af98acefcc`，request id `07833f1f-6393-4e36-936a-a3d4f894868e`，run id `run-20261006T151602Z-183208`。初始页面 `main_city`；events 记录导航 2 次、`bulk_read` 提交 1 次、关闭 0 次，总输入 `action_count=3`。

原始结果文件保留为 `artifacts/runs/run-20261006T151602Z-183208/result.json`：`needs_attention/outcome_uncertain`，`claim_request_submitted=true`，`claim_action_count=1`，`claim_verified=false`，`post_batch_state_verified=false`，`cleanup_verified=false`，`final_page_id=mail_inbox`。未改写原始结果。job 已结束，GET `/api/state` 显示 `active_job=null`。

证据路径：`before.png` 是主城起点；`step-0009.png` 是点击前系统邮件列表（截图中可见 3 封未失效邮件及金色一键阅读）；`step-0011.png` 是领取输入后的列表过渡帧；`step-0012.png` 与 `after.png` 显示“获得奖励”面板，画面可见黄金 40、竞技币 800、银两 20万、粮草 30万。`events.jsonl` 记录领取后邮箱状态观察先后出现 `claimable=true`、随后 `no_pending=true`，但该次流程没有识别已登记奖励内容，最终 `claim_verified` 和 `post_batch_state_verified` 仍为 false；没有提交关闭输入，未确认回到主城。

这是实机执行及其真实截图，不是 mock；奖励画面仅是人工离线查看截图的事实，不替代 runner 自动成功判据。领取提交后，自动化流程因原结果不确定而停止：未在 runner 中再次运行、未重复领取、未修改结果文件。界面后续收尾记录见下节。批量邮件仍为 `partial`；成功 `completed/claim_confirmed`、自动后置状态及无奖励重复运行都尚未验收。下次若继续，需先离线校准并验证本次真实奖励内容素材；因本次奖励状态可能已消耗，自动 `completed` 的实机验收必须等待另一封新的有效奖励。

### 2026-10-06 手动收尾补充：奖励弹窗与邮件窗口已关闭

按用户补充的准确操作说明，在不重新运行领取、不触碰“一键阅读”的情况下完成界面收尾。实际屏幕在首个空白点击后仍显示一张“战功 10万”奖励面板，因此逐帧确认并额外点空白一次；第二次后回到邮件列表，屏幕中可见邮件均呈灰态且一键阅读灰化。随后点击邮件窗口左上红色 X，最新截图显示已回到主城。

此次补充输入单独保存在 `artifacts/runs/cleanup-20261006T1516Z-manual-close/`：`before.png`（奖励面板初态）、`reward-dismissed.png`（第一轮关闭后显示战功奖励）、`reward-dismissed-2.png`（奖励提示关闭后邮件列表）、`after-mail-close.png`（返回主城）、`cleanup-events.jsonl`（两次奖励空白关闭和一次红色 X 的坐标/顺序）。这是用户授权的直接界面收尾记录，不属于 runner 的原始 `events.jsonl`。

原始 run `run-20261006T151602Z-183208/result.json` 及其 detail 仍保持 `needs_attention/outcome_uncertain`、`dismiss_action_count=0`、`cleanup_verified=false`；未将手动截图改成 runner 的自动成功结果。原始执行输入为导航 2、领取 1、关闭 0；补充收尾另有奖励弹窗关闭 2、邮件 X 关闭 1。合计实际界面输入 6，其中导航 2、领取 1、奖励关闭 2、邮件关闭 1。返回主城由 `after-mail-close.png` 的实机截图作视觉确认，未另跑 dry-run；批量业务的自动奖励识别、自动收尾后置验证和无奖励重复运行仍未通过，本阶段仍为 `partial`。


## 2026-10-06 批量奖励前景识别与连续收尾修复

按 `docs/reviews/2026-10-06-mail-bulk-reward-review.md` 和用户指定流程仅修复 `mail_bulk_claim`。不改写原来的 `run-20261006T151602Z-183208/result.json`，也不把人工收尾结果合并成 runner 成功。

### 修改与判据

- `classification.py`：完整匹配的前景 `target` 优先于仍可见的背景 `entry`；前景 target 部分命中时返回歧义，阻止对背景邮件列表操作。账号冲突 blocker 仍按原规则优先处理。
- `resource/mail_bulk/pages.json`：批量奖励页身份改为奖励标题与“点击任意区域关闭”提示 AND；不再依赖邮件背景标题或易失配的面板边缘。
- `resource/mail_bulk/mail_bulk.json`：奖励内容使用独立物品素材；关闭目标改为“点击任意区域关闭”提示，模板中心为受控点击目标，不再使用单封邮件详情 X。`max_reward_popups=6`，每次关闭后重新截图、重新分类和识别物品/关闭提示；同一未变化奖励画面不会重复点击，超过上限则保留不确定状态。
- `mail_bulk.py`：只允许一键阅读提交一次；领取后每个奖励面板需识别至少一个登记物品才能关闭。可处理连续、内容不同的奖励面板；所有奖励关闭后必须确认邮件列表无可领状态，再点击邮件红 X 并重截图确认主城，完成才写入 `claim_confirmed`、`post_batch_state_verified=true`、`cleanup_verified=true` 与 `final_page_id=main_city`。
- 新增批量素材：`bulk_reward_title.png`、`bulk_reward_dismiss_hint.png`、`bulk_reward_gold.png`、`bulk_reward_competition.png`、`bulk_reward_silver.png`、`bulk_reward_grain.png`、`bulk_reward_merit.png`。详见 `docs/assets.md`。素材均裁自已有实机原图，原始文件保持不变。

### 真实 Maa 截图离线回放

使用 MaaFramework 5.14.2 的真实 Resource/TemplateMatch API，未创建 Controller、未连接设备、未发送输入：

| 原图 | 新分类 | 标题/关闭提示 | 独立物品结果 | 关闭目标结果 |
|---|---|---|---|---|
| `artifacts/runs/run-20261006T151602Z-183208/step-0012.png`（奖励1） | `reward_popup`；邮件背景身份不再作为操作页 | 均 `1.000` | 黄金、竞技币、银两、粮草均命中 `1.000` | 提示命中 `1.000`；旧单封详情 X 得分 `0.386552`，未命中 |
| `artifacts/runs/cleanup-20261006T1516Z-manual-close/reward-dismissed.png`（奖励2） | `reward_popup` | 均 `1.000` | 战功物品命中 `1.000` | 提示命中 `1.000`；旧单封详情 X 得分 `0.386539`，未命中 |
| `artifacts/runs/cleanup-20261006T1516Z-manual-close/reward-dismissed-2.png`（奖励全关后的列表） | `mail_inbox` | 标题 `0.111702`、关闭提示 `0.150895`，均未命中 | 无奖励物品命中 | 邮件红 X 命中 `1.000`，仅由 `mail_inbox` 状态允许使用 |

遮去真实奖励内容、保留标题/关闭提示的控制负样本仍分类为奖励弹窗，但独立物品匹配为空；因此不能确认领取，也不能按配置继续关闭。遮去关闭提示但保留奖励标题和邮件背景的部分前景样本返回歧义，不授权背景邮件操作。以上“无物品”是合成控制负样本，不是游戏实拍的空奖励弹窗。

### 回归与命令

先写回归后实现，定向回归先复现 4 个失败（前景分类两例、物品匹配、双弹窗状态机）；修复后定向通过，补入前景部分命中与有界/重复点击状态回归后通过。完整测试命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
```

结果：`208 passed in 44.85s`，退出码 0。真实截图回放使用安装的 Maa 识别；连续弹窗状态机用 fake backend 验证状态转移、单次领取、动作计数、未变化画面拒绝重复关闭及上限分支，fake 不作为实机证据。

### 手动收尾日志审计

原始 `cleanup-events.jsonl` 原样保留 4 行。前两行是两次奖励空白关闭；第三行邮件红 X 与 `after-mail-close.png` 的主城截图对应。第四行时间为 `15:38:02Z`，晚于唯一可见的邮件关闭后主城截图，且没有对应新截图或 runner 事件；现有证据不能确认该行是否又执行了一次点击。该额外行不计入已确认的实际输入，原人工收尾中可由事件和主城截图相互支持的输入为 3 次（奖励关闭 2、邮件 X 1）；加上 runner 原始 3 次（导航2、领取1），证据支持的总输入为 6。逐行说明见同目录 `cleanup-events-audit.md`。不删除或修改原始记录；可能存在的第 4 次人工点击仍记为无法核实。

### 本轮实机状态与验收边界

管理服务 `GET /api/state` 只读返回 `active_job=null`、`job=null`，配置档案 `local-test-mail` 可用；本轮未提交 dry-run 或 execute。Computer Use 浏览器动作在打开本地网页前被安全层拦截，原因是无法可靠确认 Chrome 当前地址；按限制没有改用另一种浏览器输入方式，也没有操作游戏。因此没有新的领取前/后截图、自动关闭记录或重复运行证据；旧用户截图不能代替本次运行前 fresh screenshot。

当前结论：离线批量奖励识别与自动收尾修复已验证；第一次 runner uncertain 结果和后续人工回主城截图均保留；真实批量领取自动 `completed/claim_confirmed`、自动返回主城及重复运行 `nothing_to_claim/claim_action_count=0` 仍未验证，阶段保持 `partial`。继续实机验收需在浏览器能安全打开管理网页后，对确认的 `local-test-mail` 先执行只读 dry-run；只有它当前识别为已验证主城且能 fresh 导航到含附件的系统邮件列表，才允许提交一次批量领取。

## 2026-10-06 关闭奖励后的过渡等待与新奖励布局

本轮按 `docs/handoffs/codex-luna-mail-bulk-dismiss-wait-prompt.md` 收窄处理。保留现有前景奖励识别、关闭提示素材、动作预算和一次批领策略；只对关闭后短暂保留的旧奖励帧加入受全局截止时间与停止信号约束的只读等待。旧帧期间不会再次点击；识别到另一张已登记奖励或邮件列表才继续。等待耗尽时保留领取/关闭计数并返回不确定，不触发第二次一键阅读。

### 过渡等待回归

先写回归并复现：`A → 关闭 → 旧 A 帧 → B → 无待领列表 → 主城` 原先只读 3 帧便因签名不变而失败，奖励 B、列表和主城未被读取。修复后状态机只在等待期间截图，覆盖以下分支：

- `A → 旧 A → B → 列表 → 主城`：最终 `completed`，领取 1、奖励关闭 2、邮件关闭 1。
- `A → 旧 A → 列表 → 主城`：最终 `completed`，旧帧期间不增加输入。
- A 持续不变：在有界窗口后退出，仅提交一次奖励关闭。
- 等待期间停止或共享截止时间耗尽：不再输入，保留累计领取/关闭计数并分别返回取消/超时。

定向命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_mail_bulk.py -p no:cacheprovider -o addopts= -q
```

结果：`38 passed in 6.47s`，退出码 0。上述状态机用 fake backend 控制帧序列，证明状态转换、等待边界和输入计数；不作为设备执行证据。

### 新奖励截图与素材

本轮从实机工件 `artifacts/runs/run-20261007T025546Z-201920/after.png` 看到了“获得奖励”前景及四种内容：英雄技锦囊·伍 ×1、战旗宝箱 ×26、心得宝箱 ×4、梦境之尘 ×25。此前已登记奖励素材对这张新布局全部不命中，实机运行因此安全停止在未知奖励页，没有点击关闭提示。

新增四个素材和 ROI 见 `docs/assets.md` 与 `resource/mail_bulk/mail_bulk.json`。使用已安装 Maa 5.14.2 的真实 Resource/TemplateMatch 离线回放：来源截图四项分数均 `1.000`；旧奖励 `step-0012.png` 的最高对应分数为 `0.569163`，另一旧奖励 `reward-dismissed.png` 最高 `0.165770`，奖励关闭后列表 `reward-dismissed-2.png` 最高 `0.242586`，均低于 `0.97` 阈值。新素材只在这张来源图和上述负样本上验证；数量变化及其他奖励布局仍未验证。素材命中不改写本次 `claim_verified=false`。

定向回放命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_recognition.py::TestMailBulkRealScreenshots::test_batch_reward_items_and_dismiss_hint_match_real_popups tests/test_recognition.py::TestMailBulkRealScreenshots::test_new_reward_layout_templates_do_not_match_previous_reward_states -p no:cacheprovider -o addopts= -q
```

结果：先按旧配置复现 `2 failed`（新奖励四种物品均未被登记）；加入素材后 `2 passed in 3.09s`，退出码 0。测试覆盖旧奖励正样本、这张新奖励正样本、奖励关闭后的邮件列表负样本，以及批量奖励面板遮去物品后的无物品负样本。

### CLI 实机尝试与原始结果

实机操作使用项目原有 Maa CLI 和 `config/local-test.json`，档案 `local-test-mail`、别名 `test_mail`、设备 `emulator-5554`、包 `com.sgbltx.goodgame`、分辨率 `1080×1920`。别名只用于工件元数据。本轮未通过管理网页提交作业；网页到 runner 的实机操作链仍未验证。

- `dryrun-20261007T025225Z-202488`：零输入识别为 `main_city`。截图前景有“攻城令发布”提示遮层；第一次 execute `run-20261007T025343Z-198616` 仅点 More 一次，未确认菜单展开，`claim_action_count=0`。工件返回 `needs_attention/postcondition_not_met`，没有领取。
- `dryrun-20261007T025504Z-202252`：在上一提示已消失后重新只读确认 `main_city`，零输入。
- `run-20261007T025546Z-201920`：从主城导航两次，系统邮件列表识别为可领后提交“一键阅读”一次。奖励实物出现在 `after.png`，但旧素材未命中，原始 `result.json` 返回 `needs_attention/outcome_uncertain`：`action_count=3`、`navigation_action_count=2`、`claim_action_count=1`、`dismiss_action_count=0`、`claim_request_submitted=true`、`claim_verified=false`、`post_batch_state_verified=false`、`cleanup_verified=false`、`final_page_id=reward_popup`。events 中有唯一一条 `bulk_read` 输入。

保留目录 `artifacts/runs/run-20261007T025546Z-201920/` 内原始 `before.png`、`after.png`、`events.jsonl`、`result.json`；另两次 dry-run 保留各自 `before.png`、`events.jsonl`、`inspection.json`。第一次未导航成功的 execute 也原样保留于 `artifacts/runs/run-20261007T025343Z-198616/`。没有改写这些结果，没有再次提交领取，也没有手动关闭奖励或邮件。由于该唯一领取提交仍是不确定结果，按交接要求停止；本轮没有奖励逐张关闭、批后邮件状态、自动回到主城或重复运行 `claim_action_count=0` 的证据。

### 本轮验证命令及结论

定向状态机与 Maa 截图回放通过，完整套件结果：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
```

结果：`214 passed in 59.41s`，退出码 0。另有一次初次全套执行在 `test_bulk_persistent_old_reward_waits_to_window_end_without_second_close` 失败：该测试的 0.04 秒窗口短于首次截图分类耗时，无法证明后续轮询。将测试窗口设为 0.2 秒后，针对性用例和完整套件通过；生产等待仍受 `transition_wait_seconds` 与整轮截止时间共同限制。当前阶段仍是 `partial`：新奖励素材可离线识别，关闭后过渡等待已由状态机回归覆盖；自动奖励关闭、邮件关闭、主城后置确认以及重复运行零领取，仍待新的可控有效奖励条件下完成。既有实机领取结果保留为 `outcome_uncertain`，不得作为成功结果。



## 主聊天接手：安全收尾修正与实机恢复（2026-10-06 本地 / 2026-10-07 UTC）

### 原因与修正

用户授权主聊天接手修复并恢复当前奖励弹窗。本轮不再次点击一键阅读、不修改此前 uncertain 工件。

1. **奖励内容与安全关闭耦合**：原流程在物品模板未命中时直接停止，虽然弹窗标题及关闭提示均可识别。现要求奖励页面和关闭控件均已验证，匹配成立才授权关闭；物品未命中仅使 `reward_evidence_complete=false`（本轮内保持），增加 `unverified_reward_popup_count`，阻止 `completed`，不阻止已验证控件收尾。
2. **奖励过渡动画被立即拒绝**：第一次恢复关闭后画面中旧物品正在淡出，下一张“远古回响”正在出现，标题命中但关闭提示被动画遮挡。真实 Maa 回放显示 `ambiguous_pages=[mail_inbox,reward_popup]`。现对此类已知奖励部分前景及未变化旧帧只读等待，不授权背景按钮输入；等待同时受单步窗口、共享截止时间和停止信号约束。其他未知/冲突状态仍停止。
3. **恢复模式禁止新领取**：从已有奖励弹窗启动即设 `cleanup_only=true`，奖励关闭后只关闭邮件回主城，即使列表出现新待领邮件也不调用一键阅读。恢复不能证明旧任务领取成功。
4. **终态阻断检查**：独立复审发现背景主城与顶号提示同时命中时，旧终态分支可能标记收尾或领取成功。统一在动作后返回边界拦截 blocker/ambiguous，再进入分支。三条假后端路径先复现失败后修复，通过时均返回 `account_conflict` 且 `cleanup_verified=false`。

### 本轮真实工件（全部原样保留）

- `dryrun-20261007T033821Z-203164`：首次新鲜截图确认用户所示四种物品奖励弹窗，零输入。
- `run-20261007T034436Z-198188`：首次恢复只提交 1 次奖励关闭，领取 0；下一奖励过渡帧被识别为部分前景，结果 `unknown_page`、`cleanup_verified=false`。这份失败结果未改写。
- `dryrun-20261007T034854Z-191796`：重新获取稳定画面，当前“远古回响”奖励弹窗标题得分 1.000，关闭提示 0.982，零输入。
- **`run-20261007T034925Z-202688`**：既有 CLI/runner 自动识别并关闭“远古回响”，观察到邮件列表后识别红 X 并关闭，后置观察 `main_city`。导航 0、领取 0、关闭 2，总输入 2；`cleanup_only=true`、`cleanup_verified=true`、`final_page_id=main_city`。物品模板未命中，`reward_evidence_complete=false`、`unverified_reward_popup_count=1`，所以结果保留 `needs_attention/outcome_uncertain`，消息明确为“已自动关闭运行前的奖励弹窗及邮件并返回主城；未提交新领取，不确认此前领取结果”。

实机回城证据：`artifacts/runs/run-20261007T034925Z-202688/after.png`；点击与后置识别见同目录 `events.jsonl`，结果见 `result.json`。两个恢复任务合计奖励关闭 2、邮件关闭 1、领取 0。没有人工坐标收尾，也没有网页输入；调用现有 Maa CLI。最后的终态阻断加固在实机恢复之后提交，以回归与独立复审验证，未再操作游戏。

真实 Maa 无 Controller 回放记录：`artifacts/reviews/safe-cleanup-recognition.json`。它确认失败过渡帧为邮件/奖励部分前景、稳定“远古回响”为 `reward_popup`、回城截图为 `main_city` 且无 blocker。

### 验证与结论

新增 8 条回归（5 条收尾/过渡用例，3 条终态冲突参数路径），更新 1 条旧“无物品即停止”预期；先确认缺陷回归失败，再修复。验证命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q tests/test_mail_bulk.py
# 46 passed in 7.59s
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
# 222 passed in 50.34s，退出码 0
```

独立只读代码复审已确认终态阻断修复覆盖全部三个复现路径，未发现此次变更新增问题。

**自动收尾与实机返回主城已通过；完整批量邮件业务仍为 partial。** 下次有新的有效奖励时，验证一次主城→一键阅读→全部奖励关闭→邮件 X→主城的完整任务及重复运行零领取。此次恢复不替代新的完整领取验收，不修改历史 `run-20261007T025546Z-201920` 或其他 uncertain 结果。

本地管理服务已恢复在 http://127.0.0.1:8782/；状态接口确认 `active_job=null`。服务加载最终修正代码，没有由网页提交设备检查或游戏任务。运行 PID 与日志保存在 `artifacts/dashboard-runtime/safe-cleanup-server.*`。


## 2026-10-21 实机批量领取

前置修正（均先写失败测试）：主城身份改为固定界面（同日常/封地）；更多菜单“邮件”入口模板改为信封下半部 + 文字（`mail_menu_entry_lower.png`），避开未读红点，搜索区留余量（实机菜单上移约 5 像素，旧模板 0.743）；奖励物品改为在整条奖励横幅内搜索（四件奖励时黄金在第 3 格，原固定格位 ROI 漏检）。

| 运行 | 结果 |
| --- | --- |
| `run-20261021T104419Z-14976` | 点“更多”1 次，菜单已展开但邮件入口未命中（红点与上移），零额外输入停止 |
| `run-20261021T104652Z-29824` | 从已展开菜单继续：打开邮件 → 一键阅读 1 次 → 等待奖励动画 → 奖励弹窗（银两 18万、粮草 27万、黄金 40、竞技币 800）关闭 1 次 → 列表确认无待领（`post_batch_state_verified=true`）→ 关闭邮件 → 主城（`cleanup_verified=true`）。当时物品模板仍按固定格位，`claim_verified=false`，原始结果保留 `needs_attention/outcome_uncertain`；修正后同一弹窗离线命中黄金 0.990、竞技币 0.984 |
| `run-20261021T105009Z-23052` | 复跑 `nothing_to_claim`：列表无待领，关闭邮件回主城，领取 0 |

状态：实机领取、批后列表与回城收尾已跑通，复跑零领取已确认；物品识别修正后的单次 `completed` 待下次有新邮件附件时复核。


## 2026-10-21 单次运行完整闭环验收

| 运行 | 结果 |
| --- | --- |
| `run-20261021T145750Z-42540` | **`completed/claim_confirmed`**：主城 → 更多 → 邮件（新的太守奖励等附件邮件）→ 一键阅读 1 次 → 奖励弹窗命中黄金、竞技币 → 关闭 1 次 → 列表确认无待领 → 关闭邮件 → 主城。导航 2、领取 1、关闭 2；`claim_verified`、`reward_evidence_complete`、`post_batch_state_verified`、`cleanup_verified` 均为 true |
| `run-20261021T145818Z-28872` | 复跑 `nothing_to_claim`：领取 0，回到主城 |

状态：**已实机验收**。
