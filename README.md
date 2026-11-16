# TrueNorth

**A governed AI financial agent for Canadian accounts.**

TrueNorth answers questions about Canadian registered accounts (TFSA, RRSP, FHSA) and works with a member's account data. It doesn't guess. Every limit and calculation comes from a database rather than the model's memory, every answer from the CRA guide is backed by an exact quote that's checked in code, and every tool call the agent makes is masked for PII and written to an audit log.

It started as a hand-built guardrailed RAG assistant over the CRA's TFSA guide. It's now a production-style system: a LangGraph agent calling tools over MCP, semantic search over pgvector, a governed Postgres data layer fed by a Databricks lakehouse, CI/CD with tests on every push, and infrastructure as code deploying to AWS ECS Fargate.

![TrueNorth answering a question with a cited source](docs/screenshot.png)

## Demo



https://github.com/user-attachments/assets/480aa0d3-8f36-4615-89d1-a26f72ce4dac



---

## What it does

- **Refuses risky advice.** A few-shot classifier separates "what are the rules?" (safe) from "what should I do with my money?" (personal investment advice), then routes or refuses.
- **Answers only from the source.** Retrieval over the CRA TFSA guide, a calibrated relevance threshold, and generation constrained to retrieved context.
- **Proves its citations.** Each citation must include an exact quote, verified against the cited page in plain Python, with no LLM involved.
- **Calculates from data, not memory.** TFSA contribution room is computed in Python and SQL from annual limits, contributions and withdrawals stored in Postgres.
- **Acts as an agent.** A LangGraph ReAct agent discovers and calls tools exposed by an MCP server: list members, get accounts, calculate TFSA room, search CRA guidance. It calls independent tools in parallel.
- **Searches by meaning, not keywords.** Asked "What happens if I put too much money in my TFSA?", it finds the guide's rules on the "excess TFSA amount" (page 18), quotes them exactly and passes citation verification, even though the question never uses those words. The Q&A pipeline does this with hand-written NumPy vector search; the agent's tool does it over pgvector.
- **Reads from a lakehouse.** A Databricks medallion pipeline (bronze → silver → gold) builds curated tables of contribution limits and Bank of Canada policy rates, then syncs them into Postgres for the agent.
- **Governs every tool call.** PII is masked before anything reaches the LLM, each request binds a member-scoped context for row-level security, and every call is written to an append-only audit log.
- **Ships like production.** GitHub Actions runs the test suite on every push and pull request. Pushes to `main` then build a Docker image, push it to ECR and apply Terraform to deploy on ECS Fargate.

## Architecture

```
                    ┌──────────────────────────── AWS ─────────────────────────────┐
 Streamlit UI ─HTTP─▶  FastAPI (/ask, /members, /health) on ECS Fargate            │
                    │        │                                                     │
                    │        ▼                                                     │
                    │   orchestrate()                                              │
                    │   guardrail → escalation review → route                      │
                    │   → retrieve → threshold → grounded answer                   │
                    │   → quote verification → answer grading → needs_review       │
                    └──────────────────────────────────────────────────────────────┘

 LangGraph agent ──MCP (stdio)──▶  MCP server: "Canadian Financial Intelligence"
                                     ├─ mcp_list_members
                                     ├─ mcp_get_member_accounts
                                     ├─ mcp_calculate_tfsa_room
                                     └─ mcp_search_cra_knowledge
                                              │
                                   governance layer (every call)
                                   mask_pii → set_rls_context → log_agent_action
                                              │
                                              ▼
                                  PostgreSQL + pgvector (Neon)
               members · accounts · transactions · annual_limits
               penalty_rates · product_catalog · agent_audit_log
               cra_knowledge_base.documents (173 embedded chunks)
               dim_tfsa_limits · fct_boc_policy_rates   ◀── synced from gold
                                              ▲
                                              │
              Databricks lakehouse: bronze → silver → gold
              CRA limits · Bank of Canada rates · synthetic core banking
```

### The two entry points

**1. The guardrailed Q&A pipeline** (`app/pipeline.py`, served by `app/api.py`) handles questions about the rules:

1. **Guardrail.** Labels the question `safe`, `violation` or `out_of_scope`.
2. **Escalation review.** A second model reviews the label and rates its confidence. A low rating flags the request for human review.
3. **Routing.** Violations get a refusal and a referral to a licensed advisor. Out-of-scope questions get a redirect.
4. **Retrieval.** The question is embedded and scored against 173 pre-normalized chunks of the CRA guide with one matrix multiply.
5. **Relevance threshold.** Below 0.55, it says it couldn't find the answer instead of calling the model.
6. **Grounded generation.** It answers only from the retrieved context, with an exact quote for every citation.
7. **Citation verification.** Each quote is checked against the cited page in code.
8. **Answer grading.** A separate model judges whether the answer is fully supported by the context.
9. **Review decision.** `needs_review` is set if any check fails or comes back low.

**2. The agent** (`app/agent.py` + `app/mcp_server.py`) handles questions about a member's own accounts, for example: *"Member 1 may have over-contributed. Calculate their 2025 room and explain what happens with an excess TFSA amount."* The agent discovers the MCP tools at runtime, chooses which to call, and combines the member's data with CRA guidance in one answer. Every call passes through the governance layer.

| MCP tool | What it does |
|---|---|
| `mcp_list_members` | Lists members with PII masked (admin scope) |
| `mcp_get_member_accounts` | Returns a member's accounts with account IDs masked |
| `mcp_calculate_tfsa_room` | Deterministic contribution-room calculation from limits and transactions |
| `mcp_search_cra_knowledge` | Semantic search over the CRA guide in pgvector, filtered by the same 0.55 relevance threshold as the Q&A pipeline |

## Governance

Regulated finance needs more than a good answer. It needs to show what the system touched and why.

| Control | How it works | Where |
|---|---|---|
| PII masking | Recursively masks sensitive keys (SIN, email, phone, address, birth year), regex-redacts SIN and email patterns in free text, and truncates account IDs to their last four digits, all before data reaches the LLM | `app/governance.py` |
| Row-level security context | Each tool call binds `app.current_member_id` to the transaction with `set_config`, so Postgres RLS policies can scope rows to the acting member | `app/governance.py` |
| Audit trail | Every tool call, successful or failed, writes session ID, actor, tool name, masked input, output summary and status to `agent_audit_log` | `app/governance.py`, `app/mcp_server.py` |
| Advice guardrail | Personal investment advice is refused and referred to a licensed advisor | `app/guardrail.py` |
| Human review flag | Low confidence, failed quote checks or low grades set `needs_review` | `app/pipeline.py` |

## Data model

The agent's operational store is Postgres on Neon, with the pgvector extension. The rules tables carry a `source` and `source_page` column, so every number the agent uses traces back to a CRA document.

| Table | Holds |
|---|---|
| `annual_limits` | Yearly dollar limits by account type (TFSA 2009–2025) |
| `penalty_rates` | Penalty rules, such as the over-contribution tax |
| `product_catalog` | Fictional savings, GIC and managed-portfolio products |
| `members` | Members with birth year, province and residency year |
| `accounts` | Member accounts linked to products |
| `transactions` | Contributions and withdrawals |
| `agent_audit_log` | Append-only record of every agent tool call |
| `cra_knowledge_base.documents` | 173 chunks of the CRA guide with 3,072-dimension embeddings |
| `dim_tfsa_limits`, `fct_boc_policy_rates` | Gold tables synced from the Databricks lakehouse |

## Data platform (Databricks)

The structured data comes from a medallion pipeline in a companion repo, [canadian-finance-data-platform](https://github.com/macsamuel123/canadian-finance-data-platform).

| Layer | What happens |
|---|---|
| **Bronze** | Raw CRA limits and Bank of Canada policy rates, plus a reproducible synthetic core-banking dataset: 500 members and 100,000 transactions, dated only within each member's eligibility window |
| **Silver** | Cleaned and typed data, and a member dimension with **SCD Type 2** history built with a single idempotent Delta `MERGE` |
| **Gold** | Curated tables the agent reads: `dim_tfsa_limits` and `fct_boc_policy_rates` |
| **Serving** | Gold tables sync to Postgres with types taken from the Spark schema, loaded into a staging table and swapped in one transaction, so re-runs never duplicate rows and the agent never reads a half-loaded table |

Every notebook ends with validation checks that fail the run if the data breaks a rule downstream layers depend on. Synthetic members use IDs from 1000 up, so they never touch the six planted test members.

### TFSA room, tested against planted cases

Contribution room is the trickiest rule in the domain, so it's calculated deterministically and tested against six seeded members, each built to trip a specific rule:

| Case | What it tests |
|---|---|
| `over_contributor` | Contributions above room produce a negative result (over-contributed) |
| `withdrawal_trap` | A withdrawal does not restore room in the same year |
| `withdrawal_restored` | A withdrawal restores room on January 1 of the following year |
| `new_adult` | Room starts the year the member turns 18 |
| `newcomer` | Room starts the year the member became a resident |
| `maxed_out` | Contributions exactly equal to room produce zero |

## CI/CD and infrastructure

```
push / PR ──▶ test job: pytest (PII masking + TFSA room cases against a seeded database)
                 │ on push to main only
                 ▼
          deploy job: docker build → push to ECR (tagged by commit SHA)
                      → terraform apply → ECS Fargate service
```

Terraform (`infra/`) provisions the VPC, public subnet, internet gateway, security group (port 8000), IAM execution role, CloudWatch log group, ECS cluster, task definition and service. Terraform state is stored remotely, and secrets come from SSM Parameter Store at runtime, never from the image or the repository.

## Evaluation

The Q&A pipeline was evaluated on a hand-built set of 15 cases: 7 safe questions (4 with a required fact), 4 violations and 4 out-of-scope questions, including 2 deliberately tricky ones.

| Metric | Result |
|---|---|
| Guardrail accuracy | 15/15 |
| Answers containing the required fact | 4/4 |
| Citations verified against source text | 7/7 |
| Answers graded "high" support | 6/7 |
| Flagged for human review | 0/15 |

The tricky cases test intent rather than keywords. "Can I hold crypto ETFs in a TFSA?" asks what the rules allow, so it's safe. "Should I move my RRSP savings into my TFSA?" asks what to do with your own money, so it's a violation. The guardrail got both right.

Caveats, stated plainly:

- 15/15 on 15 cases shows the system works on these cases, not that it's flawless.
- LLM outputs aren't fully deterministic, so results vary slightly between runs.
- The LLM evals run manually (`python -m evals.run_evals`). CI runs the deterministic tests only, to keep pipelines fast and free of API cost.

## Design decisions

**The agent never does math.** LLMs are unreliable at arithmetic and at remembering yearly limits. Room calculations live in tested Python and SQL, and the agent only decides *which* tool to call.

**Tools over MCP, not hardcoded functions.** The agent discovers tools at runtime from an MCP server, so the same tools can serve any MCP client, and governance is enforced at the server where no client can skip it.

**Governance at the tool boundary.** Masking, RLS context and audit logging wrap every tool. They are not left to the prompt, because a prompt can be ignored and a function call can't.

**Two retrieval paths, one threshold.** The Q&A pipeline uses hand-written NumPy search (built to understand retrieval from first principles). The agent's tool uses pgvector's cosine distance operator. Both apply the same calibrated 0.55 cutoff, so the agent and the pipeline agree on what counts as relevant.

**No vector index, on purpose.** With 173 chunks, exact search is instant and perfectly accurate. pgvector's approximate indexes also cap plain vectors at 2,000 dimensions, so adding one would mean re-embedding at lower dimensions for no real gain at this size.

**Pre-normalized vectors.** In the NumPy path, embeddings are normalized at index time, so cosine similarity is a plain dot product, and one matrix multiply scores a question against every chunk.

**A calibrated relevance threshold.** The 0.55 cutoff was measured, not guessed:

| Group | Top score range |
|---|---|
| Questions the guide answers | 0.627 to 0.748 |
| Finance questions the guide doesn't cover | 0.141 to 0.243, plus one outlier at 0.540 |
| Off-topic questions | 0.064 to 0.117 |

**Quote-backed citations.** Asking the model to "cite only relevant pages" didn't work. Requiring an exact quote per citation fixed over-citing and made citations checkable in code.

**Two independent answer checks.** Quote verification is cheap and certain but only checks quotes. The LLM grader catches unsupported claims without a citation. Either failing triggers review.

## What broke and how I fixed it

**The escalation advisor answered the wrong question.** Its reasoning kept mentioning a lost debit card that wasn't in the question. The real question was never appended to the message list, so the model answered the last few-shot example. The real question is now always the final message.

**API calls hung indefinitely.** A reasoning model with no timeout left requests waiting forever. A 60-second timeout turned silent hangs into readable errors.

**Embedding cost 173 times more than it should have.** I called the batch embedding function inside a loop, so every pass re-embedded all 173 chunks. The fix was one batch call, then `zip` to pair each chunk with its vector.

**Citations pointed at the wrong page.** The context was one run-on string where labels and text blurred together. Separating each chunk into its own labeled block fixed it.

**The grader saw the error but rated it "high".** When I changed an answer from $7,000 to $10,000, the grader named the contradiction but still rated it "high". It read "confidence" as confidence in its own judgment. Defining each level exactly fixed it.

**The UI showed an error on every successful answer.** The error branch was an `else` attached to a `try`, which Python runs when *no* exception occurs. I rewrote the UI as a flat sequence of guards.

**The UI and API disagreed on the response shape.** The API wrapped its output in an extra `{"result": ...}` layer. FastAPI's `/docs` page showed the mismatch immediately.

**Terraform rejected the config.** Stray backticks left in resource identifiers broke `terraform apply` in CI. I removed them and pinned the Terraform version so local and CI runs match.

**The agent failed with "Connection closed".** The MCP server runs as a child process, so its crash was invisible from the agent's traceback. Running the server on its own showed it exiting instantly: an edit had deleted the `mcp.run()` entry point. A working stdio server stays open waiting for input, and that's now my first check whenever an MCP connection drops.

**Every embedding insert would have failed.** The first pgvector table was `VECTOR(1536)`, but `text-embedding-3-large` produces 3,072 dimensions. The loader now asserts every embedding's dimension before inserting, so a mismatch fails fast with a clear message.

**The gold sync quietly duplicated data.** It used `ON CONFLICT DO NOTHING`, but the target tables had no unique constraint, so nothing ever conflicted and every run appended a full copy. It also stored decimals and dates as text, because pandas turns them into generic objects. The sync now reads column types from the Spark schema and swaps a fully loaded staging table in one transaction.

## Project structure

```
app/
  config.py        Settings, API clients, model names, file paths
  llm.py           One JSON-mode LLM call with error handling
  guardrail.py     Question classifier and escalation reviewer
  ingest.py        PDF → chunks → embeddings → JSON store
  rag.py           Retrieval, generation, citation verification, grading
  pipeline.py      orchestrate(): the guardrailed Q&A pipeline
  api.py           FastAPI: POST /ask, GET /members, GET /health
  db.py            Postgres connection and schema
  tools.py         TFSA room calculator, member and account lookups
  governance.py    PII masking, RLS context, audit logging
  mcp_server.py    MCP server exposing governed tools
  agent.py         LangGraph ReAct agent using the MCP tools
ui/app.py          Streamlit front end
script/seed.py     Seeds limits, products and the six planted test members
script/load_pgvector.py  Loads the embedded CRA chunks into pgvector
tests/             PII masking and TFSA room tests (run in CI)
evals/             15-case eval set, runner, threshold calibration
infra/             Terraform for AWS ECS Fargate
.github/workflows/ CI/CD pipeline
notebooks/         The original Colab prototype
```

## Tech stack

| Layer | Tools |
|---|---|
| LLMs | DeepSeek (pipeline: classification, generation, grading); GPT-4o (agent) |
| Embeddings | OpenAI `text-embedding-3-large` |
| Agent | LangGraph, Model Context Protocol (FastMCP), `langchain-mcp-adapters` |
| Retrieval | pgvector (agent), NumPy vector search (hand-written, Q&A pipeline), pypdf, LangChain text splitter |
| Data | PostgreSQL on Neon (psycopg), Databricks (PySpark, Delta Lake, Unity Catalog) |
| API and UI | FastAPI, Pydantic, Uvicorn, Streamlit |
| Testing | pytest, custom eval harness |
| Infrastructure | Docker, Terraform, AWS (ECR, ECS Fargate, VPC, IAM, SSM Parameter Store, CloudWatch) |
| CI/CD | GitHub Actions |

## Running it locally

**1. Set environment variables** in a `.env` file at the repository root:

```
DEEPSEEK_API_KEY=...
OPENAI_API_KEY=...
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

**2. Install dependencies**

```bash
pip install -r requirements.txt
```

**3. Create and seed the database, then load the CRA chunks into pgvector**

```bash
python -m script.seed
python -m app.ingest              # only if data/tfsa_store.json is missing
python -m script.load_pgvector
```

**4. Run the Q&A pipeline**

```bash
uvicorn app.api:app --reload      # API on :8000, docs at /docs
streamlit run ui/app.py           # UI on :8501
```

**5. Run the agent**

```bash
python -m app.agent
```

**6. Test and evaluate**

```bash
pytest tests/                     # deterministic tests (what CI runs)
python -m evals.run_evals         # LLM evals, about 45 calls
```

## Limitations

- The room calculator still reads the seeded `annual_limits` table. The gold `dim_tfsa_limits` table reaches Postgres, but the tool doesn't read it yet.
- The Q&A pipeline still retrieves from a JSON store, while the agent uses pgvector.
- `needs_review` raises a flag, but there's no review queue yet.
- The eval set is small and hand-written.
- Member data is synthetic. No real personal or financial data is used.

## Roadmap

- Point the room calculator at the gold `dim_tfsa_limits` table, so the lakehouse is the single source of truth
- Move the Q&A pipeline's retrieval onto pgvector too
- An agent tool that uses `fct_boc_policy_rates`, for questions about interest rates
- Langfuse tracing for every request and tool call, with cost per request
- Human-in-the-loop approval in the LangGraph agent
- A larger eval set, plus agent evals in CI
- A load balancer and stable public URL

---

Built by **Fortune Macsamuel** · [LinkedIn](#) · [GitHub](https://github.com/macsamuel123)
