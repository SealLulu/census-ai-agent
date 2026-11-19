# Project Reflection

For a concise feature summary, see
[Highlights](HIGHLIGHTS_AND_LIMITATIONS.md#highlights). Detailed constraints,
current safeguards, and extension plans are documented in
[Limitations](HIGHLIGHTS_AND_LIMITATIONS.md#limitations).

## Development Process

I treated the assignment as a small production system rather than a prompt
connected directly to a database. I first established a thin end-to-end path:
validated startup, Streamlit input, an LLM call, a Snowflake query, and a
rendered answer. I then separated that path into explicit stages and hardened
the boundaries where a plausible but unsupported answer could be introduced.

The work progressed in short vertical iterations:

1. Establish Snowflake connectivity, configuration validation, login, and the
   basic chat experience.
2. Separate Intent and rewriting, Text-to-SQL, data access, and grounded answer
   generation.
3. Add local Schema Retrieval so the SQL model receives focused Census metadata
   instead of the complete schema.
4. Add verified relationships, SQL validation, geographic safeguards, and
   conservative aggregation rules.
5. Add follow-ups, clarification state, per-user history, streaming, graceful
   failures, and stage-aware recovery.
6. Convert observed failures into regression tests and retest the deployed
   workflow with representative Census questions.

Under the time constraint, I prioritized grounded correctness and visible
failure behavior over maximizing apparent question coverage. When schema,
geography, relationship, or aggregation evidence is insufficient, the system
clarifies or fails closed instead of guessing.

## Key Architectural Decisions

### 1. Explicit staged workflow

The request path is:

```text
Intent and Rewrite -> Schema Retrieval -> Text-to-SQL -> SQL Validation
-> Snowflake Query -> Grounded Answer Generation
```

I used explicit orchestration in `chat.py` rather than one large Agent or an
additional workflow framework. Each stage has a narrow contract and can be
tested or replaced independently. Invalid requests stop at Intent, unresolved
ambiguity returns a clarification, insufficient schema stops before Snowflake,
and rejected SQL never reaches the connector.

### 2. Hybrid user-state storage

Short-lived Session ownership, active-chat locks, and pending UI state live in
process memory. Durable conversation history and checkpoints use per-user JSONL
files. This keeps a single Streamlit instance operationally simple while
preserving user separation and surviving normal page reruns.

The design is intentionally conservative: one browser Session and one active
Chat are allowed per username. Snowflake result rows are never persisted;
history stores only the SQL and final answer. After an eligible Answer-model
timeout, the workflow can reuse Intent and SQL checkpoints but queries
Snowflake again.

### 3. Retrieval-limited Text-to-SQL context

I compared two schema strategies:

1. Send the complete Census schema to a large model. This provides broad
   visibility but exceeded the practical free-model context and cost budget,
   added many irrelevant fields, and increased hallucination risk.
2. Retrieve a compact schema subset locally, then constrain SQL generation and
   validation to that evidence.

I selected the second approach. A local SQLite FTS5 Catalog stores more than
17,000 field definitions plus table semantics and relationships. Retrieval is
year-aware and returns focused metric groups, geographic filters, aggregation
semantics, and verified JOIN paths.

Text-to-SQL is not trusted as self-validating. A separate Snowflake AST
validator permits one read-only `SELECT`, checks physical tables and columns,
blocks unverified JOINs and wildcards, and enforces bounded results. The data
service validates again immediately before execution.

### 4. Conversation continuity through rewriting

The Intent Agent uses recent history to rewrite dependent follow-ups into
standalone questions. Downstream stages receive the rewritten question, while
the UI preserves the user's original text. This avoids asking every Agent to
interpret the entire conversation independently.

Underspecified requests use a pending clarification chain instead of a guessed
interpretation. The user can resolve that chain or start a new question, and
pending state remains isolated by user.

### 5. Graceful degradation and bounded recovery

Model availability, quota, schema, validation, Snowflake, and persistence
failures are converted into concise user-facing messages; technical details
remain server-side. The workflow records its last completed stage so an
immediately repeated question after a selected transient model failure can
resume from Intent or SQL instead of restarting every model call.

Recovery is deliberately narrow. It applies only to the immediately previous,
exactly matching question and eligible model failures. Logical rejections, data
errors, quota failures, and older records do not reuse checkpoints.

## Known Limitations and Unresolved Failure Modes

The main boundaries are summarized below. The linked
[Limitations](HIGHLIGHTS_AND_LIMITATIONS.md#limitations) section explains their
causes, current mitigations, and extension plans in more detail.

- The deployed free Gemini tier has limited quota, context, and availability;
  paid multi-provider access was not available for this implementation.
- The current Snowflake CBG path lacks a verified Place mapping for cities such
  as Seattle. The system refuses to substitute a county or average CBG medians.
- Some medians, percentages, rates, and margins of error cannot be safely
  aggregated without richer metric semantics or published higher-level data.
- Process-local Session locks and JSONL history are suitable for one instance,
  not horizontal deployment or transactional multi-process access.
- Retrieval and Text-to-SQL currently use one bounded attempt. Automatic
  correction needs evaluation to ensure it improves coverage without weakening
  JOIN, aggregation, latency, or cost controls.
- The allowlisted username flow is a controlled-demo mechanism, not production
  identity and access management.

## Testing Strategy

I used three complementary test layers: deterministic automated tests,
component/workflow integration tests, and manual deployed-UI scenarios.

### Automated tests

The project uses Python's standard `unittest` framework and currently has 38
passing tests, runnable with:

```powershell
python -m unittest discover -v
```

The tests focus on high-risk correctness boundaries rather than only happy
paths:

- Intent guardrails, ambiguity, malformed model output, and deterministic
  fallbacks.
- Real-Catalog Schema Retrieval for population, income, age, tenure, years,
  multiple metrics, and supported or unsupported geographies.
- Physical Snowflake table validation, unverified relationship rejection, and
  separation of indicator IDs from table names.
- Per-user history behavior, pending-state reconstruction, exact-answer reuse,
  Session takeover, and stage-aware timeout recovery.

Unit tests use controlled model and data-service doubles where deterministic
failure behavior matters. Workflow tests exercise the orchestration across
Intent, Retrieval, SQL, Snowflake, and Answer boundaries without requiring a
live external call on every test run.

### Manual component and end-to-end tests

I also ran the provided SQL Agent, fixed Snowflake query, and complete
data-source scripts with real credentials. For deployed-page testing, I used a
query matrix spanning basic retrieval, comparison, cross-year queries,
multi-field calculations, geography levels, sorting, and expected safe
rejections. Representative examples include:

| Sample question | Main behavior checked |
| --- | --- |
| `What was the total population of California in 2020?` | Basic population metric and state filtering |
| `Compare the population of California and Texas in 2020.` | Multi-state filtering and comparison |
| `How did the population of King County, Washington change between 2019 and 2020?` | County geography and cross-year retrieval |
| `What percentage of Washington state's population was aged 65 or older in 2020?` | Complete numerator fields, denominator, and percentage calculation |
| `What was the median household income in Seattle, Washington in 2020?` | Unsupported Place mapping must fail closed rather than substitute King County |

Additional manual cases covered owner- versus renter-occupied households,
top-ten county ordering, multi-metric questions, and CBG-level income ranking.
For successful queries, I inspected the retrieved schema, generated SQL,
Snowflake result shape, and whether the final answer was supported by those
rows. For unsupported cases, I verified that the UI returned a clear limitation
instead of an empty or fabricated answer.

I tested multi-turn continuity with this sequence:

```text
What was the population of California in 2020?
How about Texas?
Which state had more people, and by how much?
```

This checks that the original message remains visible, the second and third
messages are rewritten with the required state, metric, and year context, and
the final comparison is grounded in retrieved data rather than copied numeric
claims from history.

### Future Test Coverage

With more time, I would add golden question-to-schema and question-to-SQL
datasets, prompt-regression scoring, property-based SQL safety tests,
concurrency and history-corruption tests, browser automation, load tests, and
injected Gemini/Snowflake timeout and quota failures. I would run the same
versioned evaluation set against every supported model and Catalog version
before deployment.

## What I Would Improve with More Time and Resources

1. **Evaluation:** Build a versioned Census evaluation set covering metrics,
   years, geography, ambiguity, follow-ups, and unsafe aggregations. Measure
   coverage, SQL validity, groundedness, latency, and cost.
2. **Model providers:** Add a provider-neutral LLM adapter and select separate
   Intent, Text-to-SQL, and Answer models using measured accuracy, reliability,
   latency, and price.
3. **Pipeline correction:** Add one strictly bounded
   retrieve-generate-validate correction cycle without making the validator
   bypassable.
4. **City support:** Ingest official ACS Place data into Snowflake and add
   verified city routing instead of deriving city statistics from CBG medians.
5. **State and storage:** Replace JSONL and process-local ownership with
   PostgreSQL for conversation state and Redis for heartbeat-based Session and
   active-chat locks.
6. **Identity and operations:** Add OAuth/OIDC, structured telemetry,
   dependency health checks, capacity controls, and calibrated alerts.
7. **Verification:** Add browser end-to-end automation, concurrency and
   recovery tests, load testing, and model/Snowflake failure injection in a
   CI-safe integration environment.
