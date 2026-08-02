# 本地 Agent 管控说明

本文档说明本地 agent 的 prompt、上下文管控、受控工具和后续能力开放路径。

## 1. Prompt 和规则写在哪里

- 系统角色 prompt：`local_agent/prompts/inventory_analysis.system.md`
- 用户任务 prompt：`local_agent/prompts/inventory_analysis.user.md`
- 本地确定性规则：`local_agent/rules.py`
- Prompt 装配入口：`local_agent/prompt_loader.py`
- Agent 服务入口：`local_agent/app.py`

建议写法：

- `system.md` 写身份、边界、禁止事项、输出格式。
- `user.md` 写本次分析目标、分析重点、输入占位符。
- `rules.py` 写确定性规则，例如缺条码、未盘集中、异常状态集中、重复资产条码。

## 2. 上下文怎么管控

上下文统一经过 `local_agent/context_policy.py` 处理后再进入模型。

当前策略：

- 字段白名单：只允许盘点分析需要的任务、机房、设备、条码、状态、时间、备注等字段。
- 行数限制：最多发送 `800` 行，超出部分截断。
- 文本限制：普通字段最多 `160` 字，现场备注最多 `220` 字。
- 注入清洗：现场备注里类似“忽略以上规则”“system prompt”“tool call”的内容会替换为 `[已移除可疑指令]`。
- 审计摘要：生成 `context_meta`，记录策略版本、发送行数、截断数量、删除字段数量、清洗备注数量和 payload hash。

后端会把 `context_meta` 保存到 `ai_analysis_results.context_meta`，审计日志也会记录本次 AI 分析的上下文摘要。

## 3. 受控工具注册表

工具只允许从 `local_agent/tools/registry.py` 注册表调用，模型不能随便读文件、查数据库或写业务数据。

工具分两类：

- `read_only`：只读工具，可以用于预检、规则分析、统计摘要。
- `approval_required`：涉及上传、下发、导出、整改、闭环的工具，必须管理员确认后才能执行。

当前已登记能力：

| 工具 | 类型 | 状态 | 业务能力 |
|---|---|---|---|
| `inventory.run_rules` | read_only | 已启用 | 预检、定期分析已完成盘点数据 |
| `inventory.precheck` | read_only | 已启用 | 自动上传机房清单后的预检 |
| `analysis.analyze_completed_rooms` | read_only | 已启用 | 定期分析已完成盘点的机房资产 |
| `inventory.upload_room_list` | approval_required | 未启用 | 自动上传机房清单 |
| `inventory.dispatch_room_tasks` | approval_required | 未启用 | 下发机房任务 |
| `report.export_weekly_inventory` | approval_required | 未启用 | 定期导出本周资产盘点报告 |
| `adjustment.create_review_draft` | approval_required | 未启用 | 输出整改意见管理员审核 |
| `adjustment.dispatch_confirmed_task` | approval_required | 未启用 | 审核无误后下发资产整改任务 |
| `closure.track_adjustment_loop` | approval_required | 未启用 | 跟进异常资产闭环 |

当前第一阶段已由后端审批接口承接整改闭环：AI 分析成功后生成待审核草稿，管理员审核后下发给现场人员，现场人员在小程序确认完成。模型和 DeepSeek 仍不能直接执行写库、下发或闭环动作。

## 4. 推荐逐步开放顺序

1. 先完善 `inventory.precheck`，让 agent 能对上传前清单做格式、片区、重复条码预检。
2. 再开放 `report.export_weekly_inventory` 的管理员确认入口，只允许导出到固定目录或后台下载。
3. 再做 `adjustment.create_review_draft`，只生成整改草稿，不直接下发。
4. 管理员审核通过后，再接 `adjustment.dispatch_confirmed_task` 下发到小程序。
5. 最后接 `closure.track_adjustment_loop`，跟踪整改状态、超时和复核结果。

原则：模型只给建议，所有写入、导出、下发、闭环动作都必须经过后台授权入口。
