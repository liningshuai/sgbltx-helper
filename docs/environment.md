# 环境与设备记录

更新：2026-10-06。历史入口样本包含等级 20 主城；本次用户切换的邮件测试角色截图显示等级 45。档案 alias `test_mail` 仅作工件元数据，不证明账号身份。新主城已用固定 HUD + 城墙可见条件识别，实机 dry-run 与主城→更多→邮件→关闭→主城验证通过；当前可见邮件没有附件候选，一键阅读灰态，批量正向领取仍未验证。游戏版本和渠道未在当前目标页面重新确认。

## 已核实环境

- 系统：Windows 11 专业版，版本 `10.0.26100`，Build `26100`。
- Python：项目虚拟环境 `Python 3.13.14`。
- MaaFramework Python 绑定：项目锁定 `MaaFw==5.14.2`。Python 模块未暴露 `__version__`，版本依据 `pyproject.toml` 与 `requirements.lock.txt`。
- 雷电 ADB：`C:\leidian\LDPlayer14\adb.exe`，ADB `1.0.41`，版本 `34.0.4-10411341`。
- 本轮只读设备发现：1 台设备，地址 `emulator-5554`，设备名 `雷电模拟器-LDPlayer`；Maa 报告 `screencap_methods=64`、`input_methods=18446744073709551607`。
- 项目档案及本轮截图事件记录的游戏包名为 `com.sgbltx.goodgame`，分辨率为 `1080x1920`。
- `resource/pages.json` 记录的游戏版本 `7.3`、versionCode `10` 是既有页面目录元数据，不是本轮重新核验结果；渠道字段为空。当前账号稳定性依据用户确认，游戏角色与匿名别名 `main` 的对应关系未由程序独立核验。

## 本轮实际检查

```powershell
.\.venv\Scripts\python.exe --version
& 'C:\leidian\LDPlayer14\adb.exe' version
.\.venv\Scripts\python.exe -m sgbltx_helper devices
.\.venv\Scripts\python.exe -m sgbltx_helper inspect --profile config/local.json
.\.venv\Scripts\python.exe -m sgbltx_helper inspect --profile config/local.json
.\.venv\Scripts\python.exe -m sgbltx_helper inspect --profile config/local.json
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local.json --task claim_primary --dry-run
```

所有 inspect/dry-run 都只读连接、截图和识别，没有启动游戏或发出输入。首次 inspect（run_id `dryrun-20261005T133916Z-155900`）命中 `account_conflict`。用户手动恢复并进入邮件后，第三次 inspect（run_id `dryrun-20261005T141255Z-157536`）保存了邮件全屏截图；当时目录尚未登记该页，因此分类为未知。添加基于该画面的邮件标题与附件候选素材后，Maa 实机 dry-run（run_id `dryrun-20261005T142237Z-168288`）识别到邮件页和首条邮件附件候选，二者 score 均为 `1.000`，同时保持零输入。恢复后主城负样本见 run_id `dryrun-20261005T140643Z-166296`，邮件页未误命中。Maa 方向信息 ADB 子命令错误仍出现在 inspect 日志，但截图和模板识别成功；该错误未影响已观察结果，原因尚未诊断。

## 邮件领取实测补充

- 用户手动打开标题为“国庆同庆，三国献礼！”的系统邮件。领取前 `dryrun-20261005T150309Z-155852` 零输入识别到详情页和按钮；一次 execute `run-20261005T150342Z-154560` 审计到一个 Click。后置实机画面出现奖励内容及“点击任意区域关闭”；见 `reports/phase1-report.md`。
- 首次 execute 的 `result.json` 因成功特征尚未登记而保守返回 `needs_attention/outcome_uncertain`。随后基于真实截图添加成功状态模板，并用实机截图回放验证。第二次 execute `run-20261005T151113Z-155752` 命中已领取标志，`nothing_to_claim`、`action_count=0`，未提交 Pipeline。
- 用户后来手动打开另一封“太守奖励”邮件；新主题以实机截图 `dryrun-20261006T011905Z-164888/before.png` 登记。前置 dry-run `dryrun-20261006T014052Z-173164` 命中标题与可领按钮。真实 execute `run-20261006T014134Z-173084` 返回 `completed/claim_confirmed`、`action_count=1`；同封重复运行 `run-20261006T014320Z-151748` 返回 `nothing_to_claim`、`action_count=0`。
- M1 当前页面与 Pipeline 条件统一为阈值 `0.97`；当前 Pipeline 入口只针对 `太守奖励`，旧国庆主题不匹配当前执行入口。完整测试最近一次结果 `111 passed in 15.90s`。
- 复现过的 Maa stderr 方向查询命令错误仍会在每轮连接日志中出现；当前截图、识别和控制任务均完成，但该原生命令错误尚未诊断。
- 不自动登录或抢号；如出现账号冲突，立即停止并由用户恢复。

## 批量邮件任务的实机前置条件

`mail_bulk_claim` 已加入本地 CLI。历史等级 20 主城/邮件样本来自用户当时确认的授权测试角色；本次入口兼容实测使用用户切换后的邮件测试角色，截图显示等级 45。两组截图分别用于 UI 识别验证，alias 不作为账号证明。新主城→更多→邮件→关闭→主城路径已实机验证；本次可见邮件列表无附件候选且按钮灰态，领取动作数为 0。批量领取最多提交一次；结果不确定时不重试。任务范围限系统邮件附件，不读取私聊、不删除邮件。

批量任务专用本机档案 `config/local-test.json` 使用 `profile_id=local-test-mail`、`account_alias=test_mail`，设备为 `emulator-5554`、包名 `com.sgbltx.goodgame`、分辨率 `1080x1920`。它只区分工件元数据，不认证角色身份；仍共用公共 `artifacts/` 与设备锁，不创建独立锁目录。`config/local.json` 保持原样。配置文件被 `.gitignore` 忽略。

本轮补充：Controller 点击在停止/超时后等待其自身 Job 完成确认，最长 10 秒；未确认时触发既有设备禁用标记。SDK 未提供已验证的通用 Controller 点击取消接口，因此该等待不能硬取消原生调用。批量后续控件识别失败会保留此前输入计数与领取状态。奖励弹窗必须识别登记过的奖励物品模板才算 `claim_verified`；边框和关闭提示只用于弹窗身份/收尾，不单独证明奖励。

目前截图只读识别结果与未满足条件记录见 `reports/mail-bulk-report.md`。可先运行批量 dry-run 检查当前页面；dry-run 不导航且不输入：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local-test.json --task mail_bulk_claim --dry-run
```

仍需等待新的可领取奖励，才能完成成功批量领取和奖励后置确认实测。批量奖励识别素材仅来自已成功领取的单封邮件截图回放；批量弹窗覆盖尚未实机验证。不要重复操作已知灰态页面凑记录。

入口兼容实测：`dryrun-20261006T102612Z-188052` 对当前主城零输入识别成功；`run-20261006T102653Z-179480` 从主城导航至邮件并关闭返回主城，`action_count=3`、导航 2、领取 0、关闭 1、`cleanup_verified=true`。该次状态为无待领，不证明所有未显示邮件均已逐项检查。

## 日常任务奖励模块（2026-10-07，历史扫描记录；partial）

沿用 `config/local-test.json` / `local-test-mail`、`emulator-5554`、包名 `com.sgbltx.goodgame`、1080×1920；alias 只是工件元数据。新鲜检查 `dryrun-20261007T050442Z-197612` 与授权测试角色主城样本一致，未识别账号冲突。页面调查记录：从主城左下任务入口进入“政务”中心，再选“日常任务”；页签动画曾使首张后置图仍显示旧政务主体，因此增加有界只读过渡等待。随后识别到“今日已完成 0/23”。

受控扫描 `run-20261007T060747Z-180324` 从已打开的日常任务页开始，滚动 4 次、点击已验证左上返回箭头 1 次，`action_count=5`（导航 0、领取 0、关闭 1、滚动 4），`cleanup_verified=true`，最终识别 `main_city`。五张页面截图均在该 run 工件中；首屏计数模板识别到 0/23，所见任务均未完成。完整列表底部边界及活跃宝箱没有被证明，因此结果是 `needs_attention/partial_progress`，不能解释成无待领。

本轮新增 Controller `post_swipe` 适配，继续使用 Maa Job 有界等待及未确认停止处理；未采用 ADB 输入。网页模块默认关闭，开启不会启动任务。尚未验证任务行已完成/已领取识别、活跃宝箱达标状态、完整列表边界和真实领取后置。SDK 方向信息 ADB 子命令错误仍会输出，但实机截图、识别、滚动与回城均成功；原因尚未诊断。

### 一键领取补充（2026-10-07）

新鲜 Maa dry-run `dryrun-20261007T080439Z-196708` 捕获日常任务页，读取“今日已完成 4/23”，并识别到四项绿色行按钮和顶部绿色“一键领取”。`4/23` 只作为完成数记录。随后新鲜截图 `dryrun-20261007T082025Z-177336` 及 `dryrun-20261007T083623Z-199088` 显示未授权的“攻城令发布/前往”公告覆盖列表；最新 dry-run 将该前景列为 `unrelated_announcement` blocker，两项模板均命中 1.000，领取候选为未验证，输入为 0。

截至 `run-20261007T093435Z-203824` 之前，代码仅允许在日常页身份、四个已登记来源项目身份和四个当帧绿色状态，以及顶部“一键领取”同时命中时提交一次。实际点击以 Maa 返回框中心为准。批后要求四个来源身份仍可重新定位、绿色领取状态均消失且顶部批量按钮不可用；奖励弹窗用标题与关闭提示识别，可在物品未登记时安全关闭，但不单独证明领取。随后自动关闭任务页并确认主城。当时领取与重复运行尚未执行，原因是新鲜截图被公告遮挡。

2026-10-07 后续实测更正：已按用户要求增加随机“攻城令发布/前往”公告容错。仅在日常任务页身份及双条件公告 blocker 同时成立时，execute 可点击一次已验证标题装饰空白目标 `[350,245]`，立刻重截图；dry-run 仍零输入。工作区 `AGENTS.md` 记载所有后续任务需按底层页面分别验证空白关闭目标，不能复用未经校准的坐标。

新鲜实机领取 run `run-20261007T093435Z-203824` 提交过一次批量领取，但前景奖励窗口因关闭提示模板分数不足而未识别，最终 `needs_attention/outcome_uncertain`。结果工件保留，未重试。修复奖励模板与页面优先级后，run `run-20261007T100238Z-203128` 从奖励窗口只执行 cleanup-only，关闭奖励与日常窗口共 2 输入并验证 `main_city`；未再次领取。奖励物品模板未命中，领取结果及来源项目批后状态仍不确定，因此 daily_rewards_claim 保持 partial，不执行重复运行验收。

## 日常奖励循环实现状态（2026-10-07，替代旧四项目门槛）

当前代码按已验证日常任务页顶部“一键领取”绿/灰控件状态运行；旧四个来源标题和行按钮素材仅保留为历史采集资料，不再要求它们同时出现。绿态每轮只提交一次，奖励前景需重新识别并逐张安全关闭，稳定日常页重新识别为绿态才允许下一轮，最多 5 轮；灰态停止循环，安全回城并通过新截图识别主城。起始灰态且确认返回主城时为 `nothing_to_claim`。未知或绿灰同时命中不当作灰态。

灰态按钮素材由旧真实 0/23 截图裁剪，Maa 5.14.2 离线回放分数与被遮挡背景的限制详见 `docs/assets.md`。这次实现尚未获得新的有效奖励实机正向闭环；旧 `run-20261007T093435Z-203824` uncertain 与 cleanup-only `run-20261007T100238Z-203128` 仍保持原样，不能覆盖为成功。

最新只读实机样本 `dryrun-20261007T125433Z-196872` 显示日常页被随机“守城令发布 / 前往”公告遮挡。旧标题模板低于阈值，新增此真实标题变体并保留“前往”AND 条件；空白关闭目标在该背景页另行验证。dry-run 没有输入。公告处理不授权“前往”，只允许日常页专属空白点按一次并重新识别。
