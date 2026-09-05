Treating this challenge purely as a semantic search/RAG problem is the primary failure mode in financial AI. Financial questions demand zero-hallucination, deterministic arithmetic, and auditable data lineage.

The winning approach is a **Deterministic Text-to-SQL Engine with a Lightweight LLM Orchestrator**, backed by a columnar analytical database (such as DuckDB) and rigid verification guardrails.

---

## 1. System Architecture & Technical Flow

```
[User Query] 
     │
     ▼
[Context & Entity Resolver] ─── (Fuzzy Match Vendors, Resolve Relative Dates, History)
     │
     ▼
[Lightweight LLM: SQL Generator] ─── (Schema Definition, DDL, Few-shot Examples)
     │
     ▼
[SQL Guardrail & Linter] ─── (Syntax Check, Read-Only Validation, Schema Verification)
     │
     ├─► [DuckDB Execution Engine] ──► [Raw Records / Aggregate Results]
     │                                            │
     ▼                                            ▼
[LLM Response & Insight Synthesizer] ◄── [Statistical Anomaly Detector (Bonus)]
     │
     ▼
[Frontend UI] ──► (Plain Language Answer + Breakdown Table + SQL Trace + CSV Export)

```

### End-to-End Pipeline Stages

1. **Context & Entity Resolution**:
* Preprocesses queries to ground ambiguous mentions before LLM generation.
* Uses fuzzy matching (e.g., `RapidFuzz`) against the provided Vendor List and Chart of Accounts to normalize terms (e.g., "AWS" $\rightarrow$ "Amazon Web Services Inc.").


* Resolves relative date expressions ("last month", "Q3") into explicit ISO date bounds (`start_date`, `end_date`) using runtime metadata.


2. **Constrained SQL Generation**:
* Prompts a compact, code-optimized model to output strictly valid SQL queries.
* Enforces schema constraints by injecting the table schema, primary keys, and data dictionary.




3. **Validation & Deterministic Execution**:
* Validates the generated SQL through an AST parser (e.g., `sqlglot`) to block destructive commands (`UPDATE`, `DROP`, `DELETE`) and ensure only read-only `SELECT` queries execute.
* Runs the sanitized SQL against **DuckDB** (or SQLite), offloading 100% of the mathematical aggregation ($SUM, COUNT, AVG, GROUP\ BY$) to the deterministic database engine.




4. **Synthesizer & Verifiable Presentation**:
* Hands both the user query and the exact SQL query output to the LLM to write a concise, conversational explanation.


* Generates a companion UI payload containing the tabular breakdown, the raw query trace, and download triggers.





---

## 2. Technical Stack & Model Strategy (Targeting 20% Model Efficiency)

The hackathon penalizes bloated LLMs. The goal is achieving high SQL generation accuracy with minimal token overhead, low latency, and small parameter footprints.

| Component | Recommended Tool | Rationale |
| --- | --- | --- |
| **LLM Engine** | **Qwen-2.5-Coder-7B-Instruct** or **Llama-3.1-8B-Instruct** | Tops benchmarks for Text-to-SQL at low parameter counts; runnable locally via Ollama/vLLM or cheaply via Groq/API.

 |
| **Fallback / Benchmark** | **Claude 3.5 Haiku** or **GPT-4o-mini** | Low-cost, fast API options to benchmark against your lightweight local/open-source model.

 |
| **In-Memory Analytical DB** | **DuckDB** | Fast vectorized execution on CSV/Parquet data; requires zero infrastructure overhead compared to PostgreSQL. |
| **Backend Framework** | **FastAPI** + **LangGraph** (or Pydantic) | Typed structured outputs, native async support, and stateful multi-turn history handling.

 |
| **Frontend** | **Streamlit** (Rapid) or **Next.js + Tailwind** | Streamlit enables rapid delivery of interactive dataframes, CSV downloads, and collapsible query traces.

 |

---

## 3. Implementation Roadmap

### Phase 1: Data Ingestion & Normalization

* Ingest all starter files into an embedded DuckDB database: `transactions`, `vendor_payouts`, `reconciliation_status`, `chart_of_accounts`, and `vendor_list`.


* Create optimized views with indexed dates and normalized string columns.
* Pre-build lookup dictionaries:
* Unique vendor names and aliases.
* Reconciliation statuses (`reconciled`, `unreconciled`, `pending`).


* Expense/payout categories.



### Phase 2: Schema Injection & Prompt Architecture

* Design a system prompt incorporating:
* Minimal DDL (table structures, types, foreign key relationships).
* 3–5 targeted few-shot examples showing complex financial logic (e.g., joining payouts with reconciliation status, date diff calculations).


* Explicit instructions: *"Compute values using SQL aggregates. Do not calculate numbers mentally. Return only executable SQL."*




### Phase 3: Guardrails, Explainability, & Multi-Turn State

* **Multi-Turn Memory**: Store conversation history as structured tuples: `(User Query, Generated SQL, Table Results, Summary Answer)`. When the user asks "How does that compare to the month before?", pass the prior SQL AST so the model modifies the `WHERE date` clause rather than starting from scratch.


* **Hallucination & Empty-State Guardrail**:
* If the SQL returns 0 rows, evaluate whether the filter conditions were too restrictive.
* If the user mentions an unrecognized vendor or entity, skip SQL execution entirely and clarify: *"I couldn't find vendor 'XYZ' in the database. Did you mean vendor 'ABC'?"*



* **Explainability Drawer**: Build an expandable UI accordion showing:
* The executed SQL query.


* Execution time.
* Data lineage (which tables were touched).





### Phase 4: Bonus Implementations

* **Anomaly Flagging**: After running a vendor spend query, execute a secondary baseline check:

$$\text{Payout Threshold} = \mu_{\text{historical}} + 2.5 \cdot \sigma_{\text{historical}}$$



If any transaction in the queried window exceeds this threshold, attach an alert: *"Note: Transaction #842 ($18,400) to Acme Corp is 210% higher than their 6-month average ($5,900)."*

* **Confidence Signalling**: Compute a confidence index ($0–100\%$) based on:
* Vendor name fuzzy match distance ($\ge 90\% \rightarrow \text{High}$).
* SQL execution success on first pass vs. retry.
* Prompt ambiguity score.




* **Export Feature**: Add a one-click button in the UI: `Export Results to CSV` using DuckDB's native `COPY ... TO 'export.csv'`.



---

## 4. Evaluation Criteria Alignment Strategy

```
                          HACKATHON SCORING WEIGHTS
  ┌─────────────────────────────────┬──────┬──────┬──────┬──────┐
  │ Accuracy & Grounding (30%)      │████████████████████│ 30%  │[cite: 1]
  │ Model Efficiency (20%)          │█████████████▌      │ 20%  │[cite: 1]
  │ NLU (15%)                       │██████████          │ 15%  │[cite: 1]
  │ Functionality (15%)             │██████████          │ 15%  │[cite: 1]
  │ User Experience (10%)           │███████             │ 10%  │[cite: 1]
  │ Presentation & Impact (10%)     │███████             │ 10%  │[cite: 1]
  └─────────────────────────────────┴──────┴──────┴──────┴──────┘

```

* **Accuracy & Grounding (30%)**: Guarantee that the LLM is prohibited from performing raw arithmetic; every displayed number maps 1:1 to an SQL cell result.


* **Model Efficiency (20%)**: Run an 8B or smaller parameter model, presenting benchmark numbers (Tokens per Second, Memory footprint, and Accuracy against a 30-question eval set).


* **NLU & Multi-Turn (15%)**: Demonstrate multi-turn filtering, pronoun resolution ("how much of *that* was unreconciled?"), and handling unstructured typos.


* **Verifiable UX (10%)**: Present a split layout: Chat on the left, Dynamic Data Grid + Audit Log + CSV Download on the right.



---

## 5. Hackathon Execution Schedule (48-Hour Plan)

* **Hours 0 – 6: Foundation & Ingestion**
* Profile provided starter datasets, build DuckDB schemas, and seed initial data.


* Write the basic SQL generation prompt with few-shot schema linking.


* **Hours 6 – 18: Core Pipeline & Execution Loop**
* Build the LangGraph/FastAPI execution pipeline (User Input $\rightarrow$ Prompt $\rightarrow$ SQL $\rightarrow$ DuckDB $\rightarrow$ JSON Output).


* Implement query sanitization and auto-repair (feed SQL syntax errors back to the LLM once for self-correction).


* **Hours 18 – 28: Multi-Turn Memory & Guardrails**
* Add session memory for follow-up questions.


* Implement date parsers and fuzzy vendor matchers.
* Wire up explicit fallback responses for missing data or unanswerable queries.




* **Hours 28 – 38: Frontend & Bonuses**
* Build UI with breakdown tables, CSV export, and explainability tabs.


* Add statistical anomaly detection and confidence badges.




* **Hours 38 – 48: Testing, Benchmarks, & Presentation Prep**
* Run 25–30 diverse test queries across Spend, Payouts, and Reconciliation.


* Compile the evaluation note on model choice and token efficiency.


* Finalize the presentation deck focusing on architecture, trust, and auditability.