# MCP Server — freelance-radar tools

## Запуск

```bash
# Установка fastmcp
pip install fastmcp

# Запуск MCP сервера (stdio transport)
python -m bot.mcp_server.server
```

## Инструменты

| Tool | Описание |
|---|---|
| `mcp_analyze_brief` | Анализ ТЗ → тип проекта, требования, полнота |
| `mcp_recall_cases` | Поиск похожих кейсов в истории |
| `mcp_estimate_project` | Декомпозиция → часы → цена |
| `mcp_critique_draft` | Проверка отклика на шаблонность |
| `mcp_get_freelancer_profile` | Профиль фрилансера |
| `mcp_get_competitor_stats` | Статистика конкурентов (заглушка) |

## Feature-флаги

- `USE_AGENT_V2=false` → старая логика (DeepSeek напрямую)
- `USE_AGENT_V2=true, USE_MCP_AGENT=false` → pipeline (этапы 1-5)
- `USE_AGENT_V2=true, USE_MCP_AGENT=true` → MCP-агент (этап 6)
