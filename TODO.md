# Future Extensions / 项目后续扩展方向

This file records possible long-term extension points for the project. It is not
the checklist for the current implementation and completed development steps are
not added or removed here.

本文件用于记录当前项目未来可能扩展的方向，不作为本轮开发任务清单，也不随每个
已完成步骤频繁增删。

## Conversation and Agent Intelligence / 对话与 Agent 能力

- Introduce LangGraph or another explicit state graph when the workflow requires
  more agents, branching, retries, or human approval.
- Expand pending clarification into typed states for geography, metric, year, and
  aggregation semantics.
- Add automatic Schema Retrieval expansion when the first Text-to-SQL attempt
  reports insufficient schema.
- Add conversation summarization when histories become too long for the context window.

## Schema Retrieval and Data Quality / Schema 检索与数据质量

- Add Census terminology synonyms and metric aliases to improve retrieval recall.
- Introduce configurable relevance weights for estimate fields, table titles,
  topics, universe, and field paths.
- Add regression datasets for population, income, housing, age, race, and geography.
- Support additional Census years and detect schema changes during Catalog rebuilds.

## SQL Safety and Snowflake / SQL 安全与 Snowflake

- Add a bounded correction loop when generated SQL fails validation.
- Add cost controls such as warehouse limits, query-cost inspection, and per-user quotas.
- Extend the validator for more verified aggregation semantics and geographic joins.
- Add bounded retries for transient Gemini and Snowflake failures while avoiding
  retries for validation or permission errors.

## History and Storage / 历史记录与存储

- Add per-user file locks and sequence numbers if concurrent JSONL writes are required.
- Preserve separate request-start and completion timestamps for long-running chats.
- Move history to SQLite or another transactional store when atomic updates,
  multiple processes, or cross-instance access are required.
- Add history retention, export, deletion, and privacy-management controls.

## Authentication and Session Lifecycle / 身份验证与会话生命周期

- Replace username-only login with a real identity provider or password-based login.
- Add heartbeat, expiry, disconnect cleanup, and administrator unlock for stale sessions.
- Replace the in-memory online-user registry with shared storage before multi-instance deployment.
- Add roles and permissions for administrators, analysts, and read-only users.

## Observability and Operations / 可观测性与运维

- Add structured logs and correlation IDs across Intent, Retrieval, SQL, and Answer stages.
- Record latency, token usage, validation failures, Snowflake query IDs, and result sizes.
- Add health checks for the Schema Catalog, Gemini API, and Snowflake connectivity.
- Add automated deployment checks without logging API keys, passwords, SQL results,
  or private conversation content.

## User Experience / 用户体验

- Display query progress without exposing internal prompts or unsafe SQL details.
- Add optional source fields, generated-SQL inspection, downloads, and simple charts.
- Add user controls for history length, refresh behavior, and conversation deletion.
- Improve multilingual questions and answers while preserving exact Schema identifiers.
