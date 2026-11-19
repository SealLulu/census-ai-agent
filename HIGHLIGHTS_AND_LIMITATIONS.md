# Highlights

1. **Staged and modular agent workflow**

   - The application separates intent and rewrite, Schema Retrieval, Text-to-SQL, SQL validation, Snowflake execution, and grounded answer generation into independent stages.
   - Each component has a narrow responsibility, making failures easier to isolate, test, and replace.

2. **Focused Schema Retrieval**

   - A local SQLite FTS5 Catalog narrows more than 17,000 Census field definitions to compact, year-aware schema context before an LLM is called.
   - Metric groups, table semantics, geographic filters, and relationship paths reduce token usage and schema hallucination without hard-coding the entire dataset into a prompt.

3. **Schema-bound Text-to-SQL safety**

   - Generated SQL passes through a dedicated validator that permits only one read-only `SELECT` and checks tables, columns, joins, wildcards, and result limits against retrieved schema.
   - The data service validates the SQL again immediately before Snowflake execution and applies statement timeouts and bounded result sizes.

4. **Data-verified table relationships**

   - A separate verification process checks candidate Census relationships against Snowflake for null keys, uniqueness, and source-key coverage.
   - Only verified relationships may be used by the SQL Agent; unresolved relationships fail closed instead of being trusted implicitly.

5. **Context-aware follow-ups and clarification**

   - The Intent Agent classifies requests as `NEW`, `FOLLOW_UP`, or `INVALID` and rewrites dependent follow-ups into standalone questions for downstream stages.
   - Underspecified questions use a user-isolated pending clarification chain, allowing the Agent to request missing information instead of guessing.

6. **Stage-aware checkpoint recovery**

   - The workflow records its last completed stage and resumes an immediately repeated request after an eligible model timeout instead of blindly rerunning the complete pipeline.
   - Intent and SQL checkpoints may be reused; Snowflake rows remain in memory only, so a resumed data-stage request safely executes the persisted SQL again.

7. **Graceful degradation and fail-closed behavior**

   - Model availability, quota, schema, SQL, Snowflake, and persistence failures return concise user-facing messages while technical details remain in server logs.
   - Unsupported or statistically unsafe requests are rejected or clarified rather than answered with invented schema, invalid aggregation, or an empty response.

8. **User-isolated state and concurrency control**

   - History files, pending clarification state, browser state, and active chat identifiers are isolated by user so concurrent users do not share conversation data.
   - In the current single-instance deployment, one username owns one browser session and one active chat workflow at a time; logout or inactivity releases that ownership.

9. **Responsive chat experience**

   - Transient progress updates show request acceptance, Intent completion, and data readiness, followed by streamed answer generation.
   - Input is disabled while a request is active, preventing duplicate submissions and conflicting history writes.

10. **Bounded and recoverable history persistence**

    - JSONL history supports tail-only loading, `chat_id`-based refresh deduplication, exact completed-answer reuse, and separate original and rewritten questions.
    - Generated SQL is persisted for recovery, while Snowflake result rows are never written to history.

11. **Risk-focused automated testing**

    - Tests cover Intent guardrails, ambiguity, multi-year and composite metrics, geography handling, physical-table validation, Session ownership, history reuse, and checkpoint recovery.
    - The suite emphasizes failure boundaries and correctness risks in addition to successful query paths.

# Limitations

1. **Free-model reliability**

   - **Constraint:** The free Gemini tier can be affected by demand spikes, quotas, timeouts, limited context, and inconsistent instruction or structured-output compliance. A complete question may require separate Intent, Text-to-SQL, and Answer calls, increasing this exposure.
   - **Current mitigation:** The application uses bounded model fallback, classified error handling, structured-result validation, local Schema Retrieval, fail-closed decisions, and stage-aware recovery instead of silently accepting malformed output or restarting the entire pipeline.
   - **Future plan:** Move critical stages to models with stable capacity, select models independently by task, build a repeatable accuracy and instruction-compliance evaluation set, and replace more common decisions with deterministic logic where appropriate.

2. **Limited city-level geography support**

   - **Constraint:** The current Snowflake CBG dataset does not provide a verified Place-to-CBG mapping for cities such as Seattle or Bellevue. Substituting King County or aggregating CBG medians would change the requested geography or produce statistically invalid results.
   - **Current mitigation:** Verified state, county, and CBG queries remain on the Snowflake path, while unsupported city requests fail closed rather than returning county-level or invented city results.
   - **Future plan:** Add `geography_level` routing and official state/place FIPS resolution, initially supporting Place-level population and median household income. To preserve Snowflake as the required data source, ingest official ACS 5-Year Place data into Snowflake and catalog it instead of relying on an unverified LLM mapping or runtime-only external API.

3. **Incomplete statistical aggregation coverage**

   - **Constraint:** Some CBG metrics, including medians, rates, and percentages, cannot be safely summed or averaged into state, county, or city results without published aggregates, numerators and denominators, or a valid weighting method.
   - **Current mitigation:** Metric metadata and SQL guardrails block unsupported aggregation and return a clear limitation instead of generating a plausible but statistically incorrect answer.
   - **Future plan:** Extend the Catalog with aggregation semantics, verified higher-level tables, numerator/denominator relationships, weighting rules, and Margin-of-Error handling.

4. **Process-local Session ownership**

   - **Constraint:** Login ownership and active-chat locks currently live in one application process. Streamlit cannot always observe a browser closing, so takeover uses whether the latest history activity is older than ten minutes; this is intentionally simple but is not an exact Session heartbeat and cannot support multiple instances.
   - **Current mitigation:** A thread-safe registry permits one browser Session and one active Chat workflow per user, explicit logout releases ownership, and the inactivity rule prevents abandoned in-memory ownership from blocking a user indefinitely.
   - **Future plan:** Move login and active-chat ownership into separate Redis locks keyed by `user_id`, `session_id`, and `chat_id`, with atomic acquire/release, heartbeat-renewed TTLs, and compare-and-delete protection. A browser identifier may aid instance recognition but must not replace authenticated server-side Session ownership.

5. **File-based history and limited retrieval**

   - **Constraint:** Per-user JSONL is inexpensive for a single-instance deployment but provides limited concurrency, recovery, indexing, and search. Only the latest 50 records enter the working context, and reusable answers currently require an exact normalized-text match.
   - **Current mitigation:** User-separated files, tail-only loading, `chat_id` deduplication, bounded pending state, and SQL-only checkpoint persistence keep state small and reduce cross-user or refresh-related contamination. Conservative exact matching avoids unsafe semantic cache hits.
   - **Future plan:** Move conversations, messages, Sessions, and checkpoints to a transactional database such as PostgreSQL; add pagination, backups, retention, full-text search, and optionally vector retrieval. Similar-answer reuse must also match structured attributes such as year, geography, metric, Catalog version, and data-source version rather than relying on embedding distance alone.

6. **Single-pass Retrieval and SQL generation**

   - **Constraint:** The current pipeline performs one bounded Schema Retrieval and Text-to-SQL attempt. Automatically widening the schema or correcting rejected SQL requires more than a retry: each correction policy must be evaluated against false-positive fields, invalid joins, non-additive metrics, latency, and query cost before it can be trusted in production.
   - **Current mitigation:** Metric-group expansion, verified relationships, strict SQL validation, and fail-closed responses prioritize correctness when the first attempt lacks sufficient evidence. The application does not let a model bypass validation merely to increase its apparent answer rate.
   - **Future plan:** Build a representative Census evaluation set, then introduce a strictly bounded retrieve-generate-validate correction loop. Expansion and correction decisions will be measured for answer coverage, SQL validity, statistical correctness, latency, and Snowflake cost before deployment.

7. **Production identity and access management**

   - **Constraint:** The current allowlisted username flow is suitable for a controlled evaluation deployment but is not a production identity system. Adding real authentication requires an external identity provider, secure token and cookie handling, account recovery, role policy, revocation, auditability, and deployment-specific security validation.
   - **Current mitigation:** Access is limited to configured usernames; histories and pending state are isolated per user; and Session ownership prevents the same username from concurrently controlling multiple browser Sessions in the current single-instance environment.
   - **Future plan:** Integrate an OAuth/OIDC identity provider and validate the complete login, logout, expiry, revocation, role, and recovery lifecycle. Authorization and audit behavior must be tested across browser restarts, service restarts, and multiple application instances before replacing the controlled-demo login.

8. **Production observability and capacity controls**

   - **Constraint:** Warning and error logs provide failure evidence, but production operation also requires trustworthy cross-stage telemetry, health signals, alert thresholds, retention policies, and capacity baselines. These cannot be completed responsibly without representative traffic and access to the deployed model and Snowflake usage metrics.
   - **Current mitigation:** Failures are classified by subsystem, technical details remain server-side, queries and result sizes are bounded, and automated tests plus manual integration tests cover critical paths without logging credentials or returned Census rows.
   - **Future plan:** Add correlation IDs, stage latency and error metrics, token and Snowflake consumption tracking, dependency health checks, per-user quotas, and alerts. Calibrate thresholds through load and failure-injection testing so monitoring reflects real service behavior instead of arbitrary limits.
