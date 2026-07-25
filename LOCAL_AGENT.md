# Local Inventory AI Agent

This service upgrades AI analysis from backend-embedded DeepSeek calls to a standalone local agent.

## Start

1. Configure DeepSeek with `configure_ai_env.bat`.
2. Enable local agent routing with `enable_local_agent.bat`.
3. Start the local agent:

   ```powershell
   .\start_local_agent.bat
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
{"prompt_version": "inventory-ai-v1", "input_summary": "", "rows": []}
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
