/**
 * FinQuery AI — Frontend Controller
 * Connects the UI with FastAPI Text-to-SQL backend.
 */

document.addEventListener("DOMContentLoaded", () => {
  function newSessionId() {
    return "session-" + Math.random().toString(36).substring(2, 9);
  }

  let sessionId = newSessionId();
  document.getElementById("session-badge").textContent = `Session: ${sessionId.substring(0, 12)}`;

  // Bumped on every new query submission AND on resetSessionState() (customer
  // switch). A response is only rendered if its captured epoch still matches
  // the current one — discards stale/out-of-order responses so a slow reply
  // from a previous entity/query can't overwrite what's on screen now.
  let requestEpoch = 0;
  let requestInFlight = false;

  let currentSQL = "";
  let currentResultData = null;
  let currentEntityId = null;
  const DEMO_PASSWORD = "1234"; // Shared, public demo gate — not a real credential. No auth in this build.

  const WELCOME_HTML = `
    <div class="message-bubble bot-bubble welcome-message">
      <div class="bot-avatar">FQ</div>
      <div class="message-content">
        <p><strong>Welcome to FinQuery AI!</strong></p>
        <p>Ask any question in plain English about bank transactions, balances, counterparties, or reconciliation. Every number is deterministically computed in <strong>MySQL</strong> and verified against the result before you see it — account numbers and UTRs are always masked.</p>
      </div>
    </div>
  `;
  const ANSWER_PLACEHOLDER = "Submit a query on the left to inspect real-time deterministic financial insights.";

  // DOM Elements
  const queryForm = document.getElementById("query-form");
  const queryInput = document.getElementById("user-query-input");
  const dryRunToggle = document.getElementById("dry-run-toggle");
  const chatStream = document.getElementById("chat-stream");
  const answerCard = document.getElementById("answer-card");
  const answerText = document.getElementById("answer-text");
  const groundedTag = document.getElementById("grounded-tag");
  const timingTag = document.getElementById("timing-tag");
  const entityTagsRow = document.getElementById("entity-tags-row");
  const confidenceBadge = document.getElementById("confidence-badge");
  const confText = document.getElementById("conf-text");
  const anomalyContainer = document.getElementById("anomaly-container");
  const anomalyList = document.getElementById("anomaly-list");
  const tableWrapper = document.getElementById("table-wrapper");
  const tablePlaceholder = document.getElementById("table-placeholder");
  const dataTable = document.getElementById("result-data-table");
  const tableHead = document.getElementById("table-head");
  const tableBody = document.getElementById("table-body");
  const rowCountBadge = document.getElementById("row-count-badge");
  const executedSqlCode = document.getElementById("executed-sql-code");
  const validationPill = document.getElementById("validation-pill");
  const metaTables = document.getElementById("meta-tables");
  const metaLatency = document.getElementById("meta-latency");
  const metaLlm = document.getElementById("meta-llm");
  const metaModel = document.getElementById("meta-model");
  const providerName = value => ({openrouter:"OpenRouter",ollama:"Ollama",openai:"OpenAI",groq:"Groq"}[value] || value || "Unavailable");
  const metaRetries = document.getElementById("meta-retries");
  const metaTokens = document.getElementById("meta-tokens");
  const metaColumns = document.getElementById("meta-columns");
  const metaRecords = document.getElementById("meta-records");
  const lineageSummaryEl = document.getElementById("lineage-summary");
  const confidenceReasonsCard = document.getElementById("confidence-reasons-card");
  const confidenceReasonsList = document.getElementById("confidence-reasons-list");
  const btnConfWhy = document.getElementById("btn-conf-why");
  const followupRow = document.getElementById("followup-row");
  const followupChips = document.getElementById("followup-chips");
  const accuracyBadge = document.getElementById("accuracy-badge");
  const accuracyText = document.getElementById("accuracy-text");
  const btnExportCsv = document.getElementById("btn-export-csv");
  const btnCopySql = document.getElementById("btn-copy-sql");
  const btnClearChat = document.getElementById("btn-clear-chat");
  const btnSchemaModal = document.getElementById("btn-schema-modal");
  const schemaModal = document.getElementById("schema-modal");
  const btnCloseModal = document.getElementById("btn-close-modal");
  const schemaModalContent = document.getElementById("schema-modal-content");
  const dbStatusText = document.getElementById("db-status-text");
  const activeModelText = document.getElementById("active-model-text");
  const lockScreen = document.getElementById("lock-screen");
  const lockEntitySelect = document.getElementById("lock-entity-select");
  const lockPasswordInput = document.getElementById("lock-password-input");
  const lockError = document.getElementById("lock-error");
  const btnLockSubmit = document.getElementById("btn-lock-submit");
  const entityLockedText = document.getElementById("entity-locked-text");
  const btnSwitchCustomer = document.getElementById("btn-switch-customer");

  // Initial Health Check
  fetchHealth();
  fetchEntities();
  fetchAccuracy();

  // Lock Screen — demo gate only (see comment in index.html). Populates the
  // dropdown from /api/entities (already fetched above), then on submit
  // just checks the shared password and records which entity to scope to.
  btnLockSubmit.addEventListener("click", attemptUnlock);
  lockPasswordInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") attemptUnlock();
  });

  function attemptUnlock() {
    if (lockPasswordInput.value !== DEMO_PASSWORD) {
      lockError.textContent = "Incorrect password.";
      lockError.classList.remove("hidden");
      return;
    }
    currentEntityId = lockEntitySelect.value || null;
    const label = lockEntitySelect.options[lockEntitySelect.selectedIndex].textContent;
    entityLockedText.textContent = label;
    lockError.classList.add("hidden");
    lockPasswordInput.value = "";
    lockScreen.classList.add("hidden");
    queryInput.focus();
  }

  // Reset all per-session UI/state back to its fresh-load defaults and mint
  // a new session_id, so a customer switch can never leak the previous
  // customer's chat, dashboard, or backend conversation_history into the
  // new one.
  function resetSessionState() {
    requestEpoch++; // discard any in-flight request's response
    requestInFlight = false;
    document.getElementById("btn-submit-query").disabled = false;

    sessionId = newSessionId();
    document.getElementById("session-badge").textContent = `Session: ${sessionId.substring(0, 12)}`;

    chatStream.innerHTML = WELCOME_HTML;

    currentSQL = "";
    currentResultData = null;

    answerText.textContent = ANSWER_PLACEHOLDER;
    timingTag.textContent = "0.0 ms";
    groundedTag.classList.add("hidden");
    answerCard.classList.remove("ungrounded");
    entityTagsRow.innerHTML = "";
    entityTagsRow.classList.add("hidden");
    followupChips.innerHTML = "";
    followupRow.classList.add("hidden");
    confidenceBadge.classList.add("hidden");
    confidenceReasonsList.innerHTML = "";
    confidenceReasonsCard.classList.add("hidden");
    anomalyList.innerHTML = "";
    anomalyContainer.classList.add("hidden");

    executedSqlCode.textContent = "-- Executed SQL query will appear here";
    validationPill.textContent = "Read-Only Enforced";
    validationPill.className = "pill-badge valid";
    lineageSummaryEl.textContent = "";
    lineageSummaryEl.classList.add("hidden");
    metaTables.textContent = "—";
    metaLatency.textContent = "0 ms";
    metaLlm.textContent = "Not requested";
    metaModel.textContent = "Not requested";
    metaRetries.textContent = "0";
    metaTokens.textContent = "0 / 0";
    metaColumns.textContent = "—";
    metaColumns.title = "";
    metaRecords.textContent = "0";

    tableHead.innerHTML = "";
    tableBody.innerHTML = "";
    tablePlaceholder.classList.remove("hidden");
    dataTable.classList.add("hidden");
    rowCountBadge.textContent = "0 rows";

    btnExportCsv.disabled = true;
    btnCopySql.disabled = true;

  }

  btnSwitchCustomer.addEventListener("click", () => {
    // Best-effort — drop the outgoing customer's backend conversation
    // history. Fire-and-forget: the frontend reset below doesn't depend on
    // this succeeding, since resetSessionState() also mints a brand-new
    // session_id that the backend has never seen.
    fetch(`/api/history?session_id=${sessionId}`, { method: "DELETE" }).catch(() => {});

    resetSessionState();

    lockScreen.classList.remove("hidden");
    lockPasswordInput.value = "";
    lockError.classList.add("hidden");
    lockPasswordInput.focus();
  });

  // Confidence "why?" toggle
  btnConfWhy.addEventListener("click", () => {
    confidenceReasonsCard.classList.toggle("hidden");
  });

  // Query Form Submit
  queryForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = queryInput.value.trim();
    if (!query || requestInFlight) return;
    requestInFlight = true;
    document.getElementById("btn-submit-query").disabled = true;

    const dryRun = dryRunToggle.checked;

    // Captured now so a response that comes back after a newer query (or a
    // customer switch, which also bumps requestEpoch) can be recognized as
    // stale and discarded instead of overwriting what's currently on screen.
    const myEpoch = ++requestEpoch;
    const requestSessionId = sessionId;

    // Append user message bubble
    appendMessage(query, "user");
    queryInput.value = "";

    // Show loading in bot bubble and answer card
    const botMsgId = appendMessage("Running Text-to-SQL pipeline...", "bot", true);
    answerText.innerHTML = `<div class="loader-spinner"></div>`;
    timingTag.textContent = "Processing...";
    confidenceBadge.classList.add("hidden");
    confidenceReasonsCard.classList.add("hidden");
    groundedTag.classList.add("hidden");
    answerCard.classList.remove("ungrounded");
    dataTable.classList.add("hidden");
    tablePlaceholder.classList.remove("hidden");
    rowCountBadge.textContent = "Waiting for result";
    btnExportCsv.disabled = true;
    btnCopySql.disabled = true;

    try {
      const startTime = performance.now();
      const response = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: query,
          session_id: requestSessionId,
          dry_run: dryRun,
          entity_id: currentEntityId,
        }),
      });

      if (!response.ok) {
        const errData = await response.json();
        throw new Error(errData.detail || "Query failed");
      }

      const data = await response.json();
      const clientDuration = Math.round(performance.now() - startTime);

      if (myEpoch !== requestEpoch) return; // a newer query or a session switch superseded this one

      updateBotMessage(botMsgId, data.answer || "Query executed successfully.");
      renderDashboard(data, clientDuration);
    } catch (err) {
      if (myEpoch !== requestEpoch) return;
      updateBotMessage(botMsgId, `⚠️ Error: ${err.message}`);
      answerText.textContent = `Error processing query: ${err.message}`;
      timingTag.textContent = "Error";
    } finally {
      if (myEpoch === requestEpoch) {
        requestInFlight = false;
        document.getElementById("btn-submit-query").disabled = false;
      }
    }
  });

  // Render Dashboard
  function renderDashboard(data, clientDuration) {
    currentSQL = data.extracted_sql || "";
    currentResultData = data.query_result;

    // Answer & Timing
    answerText.textContent = data.answer || "No response generated.";
    timingTag.textContent = `${data.total_time_ms ? data.total_time_ms.toFixed(1) : clientDuration} ms`;

    const grounding = data.grounding_status || "not_evaluated";
    answerCard.classList.remove("ungrounded");
    groundedTag.classList.toggle("hidden", grounding === "not_evaluated");
    if (grounding === "passed") {
      groundedTag.className = "grounded-tag ok";
      groundedTag.textContent = "✓ Explanation numbers checked";
      groundedTag.title = "Numbers match returned values; this is not a semantic accuracy guarantee.";
    } else if (grounding === "fallback" || grounding === "template") {
      groundedTag.className = "grounded-tag fallback";
      groundedTag.textContent = "Result summary · Explanation replaced";
      groundedTag.title = data.fallback_reason || "Showing values directly from the query result.";
    }

    // Entity Tags
    entityTagsRow.innerHTML = "";
    if (data.resolved_entities) {
      let hasEntities = false;
      if (data.resolved_entities.counterparty) {
        const v = data.resolved_entities.counterparty;
        const tag = document.createElement("span");
        tag.className = "entity-tag";
        tag.textContent = `Counterparty: ${v.name} (${Math.round(v.match_score || 100)}% match)`;
        entityTagsRow.appendChild(tag);
        hasEntities = true;
      }
      if (data.resolved_entities.bank) {
        const b = data.resolved_entities.bank;
        const tag = document.createElement("span");
        tag.className = "entity-tag";
        tag.textContent = `Bank: ${b.name} (${b.code})`;
        entityTagsRow.appendChild(tag);
        hasEntities = true;
      }
      if (data.resolved_entities.dates && data.resolved_entities.dates.start_date) {
        const d = data.resolved_entities.dates;
        const tag = document.createElement("span");
        tag.className = "entity-tag";
        tag.textContent = `Dates: ${d.start_date} → ${d.end_date}`;
        entityTagsRow.appendChild(tag);
        hasEntities = true;
      }
      entityTagsRow.classList.toggle("hidden", !hasEntities);
    }

    // Confidence Badge + reasoning behind the score
    if (data.confidence) {
      confidenceBadge.className = `confidence-pill ${data.confidence.level.toLowerCase()}`;
      confText.textContent = `${data.confidence.level} query confidence`;
      confidenceBadge.classList.remove("hidden");
      confidenceBadge.title = "Heuristic query checks; not a probability of correctness. Explanation verification is separate.";

      confidenceReasonsList.innerHTML = "";
      (data.confidence.reasons || []).forEach((reason) => {
        const li = document.createElement("li");
        li.textContent = reason;
        confidenceReasonsList.appendChild(li);
      });
      if (!data.confidence.reasons || data.confidence.reasons.length === 0) {
        confidenceReasonsCard.classList.add("hidden");
      } else {
        // HIGH confidence's justification is shown immediately, no click
        // needed; MEDIUM/LOW stay collapsed behind the "?" toggle.
        confidenceReasonsCard.classList.toggle("hidden", data.confidence.level !== "HIGH");
      }
    } else {
      confidenceBadge.classList.add("hidden");
      confidenceReasonsCard.classList.add("hidden");
    }

    // Anomaly Detection
    if (data.anomalies && data.anomalies.length > 0) {
      anomalyList.innerHTML = "";
      data.anomalies.forEach((a) => {
        const item = document.createElement("div");
        item.className = "anomaly-item";
        item.innerHTML = a.message.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
        anomalyList.appendChild(item);
      });
      anomalyContainer.classList.remove("hidden");
    } else {
      anomalyContainer.classList.add("hidden");
    }

    // SQL Audit & Lineage
    executedSqlCode.textContent = data.extracted_sql || "-- No SQL extracted (e.g. clarification needed)";
    validationPill.textContent = data.sql_valid ? "Read-Only Enforced" : (data.validation_error ? "Validation Failed" : "Dry-Run");
    validationPill.className = `pill-badge ${data.sql_valid ? "valid" : "error"}`;

    if (data.lineage_summary) {
      lineageSummaryEl.textContent = data.lineage_summary;
      lineageSummaryEl.classList.remove("hidden");
    } else {
      lineageSummaryEl.textContent = "";
      lineageSummaryEl.classList.add("hidden");
    }

    if (data.query_result) {
      metaTables.textContent = (data.query_result.tables_touched || []).join(", ") || "None";
      metaLatency.textContent = `${data.query_result.execution_time_ms ? data.query_result.execution_time_ms.toFixed(1) : 0} ms`;
      metaColumns.textContent = (data.query_result.columns || []).join(", ") || "—";
      metaColumns.title = metaColumns.textContent;
      metaRecords.textContent = `${data.query_result.row_count || 0}`;
    } else {
      metaTables.textContent = "—";
      metaLatency.textContent = "—";
      metaColumns.textContent = "—";
      metaRecords.textContent = "0";
    }
    const noModel = data.direct_response_kind || data.llm_provider === "dry_run" || (!data.llm_provider && !data.llm_model);
    metaLlm.textContent = noModel ? "No model call" : providerName(data.llm_provider);
    metaModel.textContent = noModel ? "Not used" : (data.llm_model || "Unavailable");
    metaRetries.textContent = `${data.retries || 0}`;
    metaTokens.textContent = `${data.prompt_tokens || 0} / ${data.completion_tokens || 0}`;

    // Follow-up Suggestions
    followupChips.innerHTML = "";
    if (data.suggestions && data.suggestions.length > 0) {
      data.suggestions.forEach((s) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "followup-chip";
        chip.textContent = s;
        chip.addEventListener("click", () => {
          queryInput.value = s;
          queryForm.dispatchEvent(new Event("submit"));
        });
        followupChips.appendChild(chip);
      });
      followupRow.classList.remove("hidden");
    } else {
      followupRow.classList.add("hidden");
    }

    // Enable/Disable Action Buttons
    btnExportCsv.disabled = !data.extracted_sql || !data.query_result || !data.query_result.success;
    btnCopySql.disabled = !data.extracted_sql;

    // Table Grid
    if (data.query_result && data.query_result.success && data.query_result.rows.length > 0) {
      renderTable(data.query_result.columns, data.query_result.rows);
      rowCountBadge.textContent = `${data.query_result.row_count} row${data.query_result.row_count !== 1 ? "s" : ""}`;
      tablePlaceholder.classList.add("hidden");
      dataTable.classList.remove("hidden");
    } else {
      tablePlaceholder.classList.remove("hidden");
      dataTable.classList.add("hidden");
      rowCountBadge.textContent = "0 rows";
    }
  }

  // Render Table
  function renderTable(columns, rows) {
    tableHead.innerHTML = "";
    tableBody.innerHTML = "";

    // Header
    const trHead = document.createElement("tr");
    columns.forEach((col) => {
      const th = document.createElement("th");
      th.textContent = col;
      trHead.appendChild(th);
    });
    tableHead.appendChild(trHead);

    // Body
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((col) => {
        const td = document.createElement("td");
        let val = typeof row === "object" && !Array.isArray(row) ? row[col] : row;
        if (typeof val === "number") {
          const c = col.toLowerCase();
          if (c.includes("amount") || c.includes("spend") || c.includes("total") || c.includes("balance")) {
            val = val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
          } else {
            val = val.toLocaleString();
          }
        }
        td.textContent = val !== null && val !== undefined ? val : "—";
        tr.appendChild(td);
      });
      tableBody.appendChild(tr);
    });
  }

  // Append Chat Message
  function appendMessage(text, sender, isLoading = false) {
    const msgId = "msg-" + Math.random().toString(36).substring(2, 9);
    const bubble = document.createElement("div");
    bubble.className = `message-bubble ${sender}-bubble`;
    bubble.id = msgId;

    if (sender === "user") {
      bubble.innerHTML = `<div class="user-content">${escapeHtml(text)}</div>`;
    } else {
      bubble.innerHTML = `
        <div class="bot-avatar">FQ</div>
        <div class="message-content">
          ${isLoading ? '<div class="loader-spinner" style="width:16px;height:16px;margin:0"></div>' : escapeHtml(text)}
        </div>
      `;
    }

    chatStream.appendChild(bubble);
    chatStream.scrollTop = chatStream.scrollHeight;
    return msgId;
  }

  function updateBotMessage(msgId, text) {
    const bubble = document.getElementById(msgId);
    if (bubble) {
      const content = bubble.querySelector(".message-content");
      if (content) {
        content.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
      }
    }
  }

  // CSV Export Button
  btnExportCsv.addEventListener("click", async () => {
    if (!currentSQL) return;
    btnExportCsv.disabled = true;
    btnExportCsv.textContent = "Exporting...";

    try {
      const resp = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sql: currentSQL,
          filename: `finquery_export_${Date.now()}.csv`,
        }),
      });

      if (!resp.ok) throw new Error("Export failed");

      const blob = await resp.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `finquery_export_${Date.now()}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (err) {
      alert("CSV export error: " + err.message);
    } finally {
      btnExportCsv.disabled = false;
      btnExportCsv.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg> Export CSV`;
    }
  });

  // Copy SQL Button
  btnCopySql.addEventListener("click", () => {
    if (!currentSQL) return;
    navigator.clipboard.writeText(currentSQL).then(() => {
      const orig = btnCopySql.innerHTML;
      btnCopySql.textContent = "Copied! ✓";
      setTimeout(() => { btnCopySql.innerHTML = orig; }, 1500);
    });
  });

  // Clear Chat History
  btnClearChat.addEventListener("click", async () => {
    if (!confirm("Reset current conversation session?")) return;
    await fetch(`/api/history?session_id=${sessionId}`, { method: "DELETE" });
    chatStream.innerHTML = `
      <div class="message-bubble bot-bubble welcome-message">
        <div class="bot-avatar">FQ</div>
        <div class="message-content">
          <p>Conversation history cleared. Ready for your next financial question!</p>
        </div>
      </div>
    `;
  });

  // Schema Modal
  btnSchemaModal.addEventListener("click", async () => {
    schemaModal.classList.remove("hidden");
    schemaModalContent.innerHTML = `<div class="loader-spinner"></div>`;
    try {
      const resp = await fetch("/api/schema");
      const data = await resp.json();
      renderSchemaModal(data);
    } catch (err) {
      schemaModalContent.innerHTML = `<p style="color:var(--accent-red)">Failed to load schema: ${err.message}</p>`;
    }
  });

  btnCloseModal.addEventListener("click", () => {
    schemaModal.classList.add("hidden");
  });

  schemaModal.addEventListener("click", (e) => {
    if (e.target === schemaModal) schemaModal.classList.add("hidden");
  });

  function renderSchemaModal(schema) {
    let html = "";
    schema.tables.forEach((t) => {
      html += `
        <div class="schema-table-box">
          <div class="schema-table-title">
            <span>📦 ${t.table_name}</span>
            <span style="font-size:0.75rem;color:var(--text-muted)">${t.row_count} rows</span>
          </div>
          <div class="schema-cols-grid">
            ${t.columns.map((c) => `
              <div class="schema-col-pill">
                <span>${c.name}</span>
                <span class="schema-col-type">${c.type}</span>
              </div>
            `).join("")}
          </div>
        </div>
      `;
    });

    if (schema.views && schema.views.length > 0) {
      html += `
        <div class="schema-table-box">
          <div class="schema-table-title"><span>👁️ Analytical Views (${schema.views.length})</span></div>
          <div class="schema-cols-grid">
            ${schema.views.map((v) => `<div class="schema-col-pill"><span>${v}</span></div>`).join("")}
          </div>
        </div>
      `;
    }
    schemaModalContent.innerHTML = html;
  }

  // Fetch Health
  async function fetchHealth() {
    try {
      const resp = await fetch("/api/health");
      const data = await resp.json();
      activeModelText.textContent = `${providerName(data.llm_provider)} · ${data.llm_model || "Model unavailable"}`;
      activeModelText.title = "Configured provider and model; request details appear in the audit panel.";
      if (data.status === "ok") {
        dbStatusText.textContent = `MySQL: ${data.total_rows} Rows (${data.tables} Tables)`;

      }
    } catch (e) {
      dbStatusText.textContent = "DB Status: Offline";
    }
  }

  // Fetch Entities — populates the lock screen's "Customer" dropdown. No
  // real login in this build (see README); the lock screen is a UX gate
  // that decides which customer's data a session is scoped to, not auth.
  async function fetchEntities() {
    try {
      const resp = await fetch("/api/entities");
      const data = await resp.json();
      (data.entities || []).forEach((e) => {
        const opt = document.createElement("option");
        opt.value = e.entity_id;
        const shortId = e.entity_id.substring(0, 8);
        const acctLabel = e.account_count === 1 ? "1 account" : `${e.account_count} accounts`;
        opt.textContent = `${shortId}… — ${acctLabel} (${e.banks})`;
        lockEntitySelect.appendChild(opt);
      });
    } catch (e) {
      // Non-fatal — the app still works fully unscoped without this.
    }
  }

  // Fetch Accuracy — summary of the last `python benchmark.py` gold-set run.
  async function fetchAccuracy() {
    try {
      const resp = await fetch("/api/accuracy");
      const data = await resp.json();
      if (data.available) {
        accuracyText.textContent = `${data.pass_rate}% gold-set (${data.passed}/${data.total_queries})`;
        accuracyBadge.title = `From the last benchmark.py run — ${data.total_tokens_in}/${data.total_tokens_out} tokens, $${(data.total_cost_usd || 0).toFixed(6)} total`;
        accuracyBadge.classList.remove("hidden");
      }
    } catch (e) {
      // Non-fatal — benchmark_results.json may not exist yet.
    }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
});
