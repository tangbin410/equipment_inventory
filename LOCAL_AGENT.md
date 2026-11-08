# Local Inventory AI Agent

This service upgrades AI analysis from backend-embedded DeepSeek calls to a standalone local agent.

## Start

1. Configure DeepSeek with `scripts/windows/configure_ai_env.bat`.
2. Enable local agent routing with `scripts/windows/enable_local_agent.bat`.
3. Start the local agent:

   ```powershell
   .\scripts\windows\start_local_agent.bat
   ```

4. Restart the backend.
5. In PC admin, open AI model analysis and click the model availability check button.

## Runtime URLs

- Local agent health: `http://127.0.0.1:9000/health`
- Local agent entrypoint: `http://127.0.0.1:9000/analyze`
- Backend routing env:

  ```env
  INVENTORY_AI_AGENT_URL=http://127.0.0.1:9000/analyze
  ```

## Request Contract

Health check:

```json
{"type": "health_check", "message": "ping"}
```

Analysis:

```json
{"prompt_version": "three-way-v4-20261015", "input_summary": "", "data_sources": ["资产", "综资", "现场"], "rows": []}
```

The analysis response must contain:

```json
{"analysis_items": []}
```

Each item uses these fields:

```json
{
  "问题类型": "",
  "涉及机房": "",
  "涉及设备": "",
  "风险等级": "高/中/低",
  "判断依据": "",
  "整改建议": "",
  "优先级": "P1/P2/P3"
}
```

## Extension Points

- `local_agent/rules.py`: local deterministic inventory rules.
- `local_agent/llm_client.py`: DeepSeek client.
- `local_agent/app.py`: request routing and prompt orchestration.

## 三方比对与整改闭环规则

业务规则统一保存在 `local_agent/prompts/inventory_three_way.rules.md`，由独立 Agent 和后端直连模式的分析、对话共同加载。规则版本由 `/health` 的 `business_rules_version` 和分析/对话的 `context_meta` 报告。

规则要求先核对综资与现场实际条码，再按资产条码与资产信息表核对；以地点编码限定范围，型号由管理员作整体判断，多条码全部人工填写，原资产/综资系统修改后上传核对，三方一致后由管理员验收。三方比对分析和关联已完成任务的对话已接入同一三方引擎：输入包括最新资产快照、本轮综资基线、现场有效反馈、第一步条码核对、名称/地点结论及型号人工判断。标识字段统一脱敏，并通过上下文白名单和截断元数据传递。未选任务或只有旧盘点上下文时，仍按实际来源声明，不能宣称已执行完整三方核对。

完整业务规则、代码缺口与实施顺序见 `docs/三方比对与整改闭环规则及逻辑检查-2026-10-15.md`。三方确定性规则和验收门槛由 `backend/three_way.py` 执行，AI 不写入业务状态。分析内容版本包含源数据和人工判断变化，无需重盘即可生成更新后的报告。
