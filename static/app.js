/**
 * FinQuery AI — Frontend Controller
 * Connects the UI with FastAPI Text-to-SQL backend.
 */

document.addEventListener("DOMContentLoaded", () => {
  const sessionId = "session-" + Math.random().toString(36).substring(2, 9);
  document.getElementById("session-badge").textContent = `Session: ${sessionId.substring(0, 12)}`;

  let currentSQL = "";
  let currentResultData = null;

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
  const metaRetries = document.getElementById("meta-retries");
  const metaTokens = document.getElementById("meta-tokens");
  const btnExportCsv = document.getElementById("btn-export-csv");
  const btnCopySql = document.getElementById("btn-copy-sql");
  const btnClearChat = document.getElementById("btn-clear-chat");
  const btnSchemaModal = document.getElementById("btn-schema-modal");
  const schemaModal = document.getElementById("schema-modal");
  const btnCloseModal = document.getElementById("btn-close-modal");
  const schemaModalContent = document.getElementById("schema-modal-content");
  const dbStatusText = document.getElementById("db-status-text");
  const activeModelText = document.getElementById("active-model-text");
  const entitySelect = document.getElementById("entity-select");

  // Initial Health Check
  fetchHealth();
  fetchEntities();

  // Prompt Chips
  document.querySelectorAll(".prompt-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      queryInput.value = chip.dataset.query;
      queryForm.dispatchEvent(new Event("submit"));
    });
  });

  // Query Form Submit
  queryForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = queryInput.value.trim();
    if (!query) return;

    const dryRun = dryRunToggle.checked;

    // Append user message bubble
    appendMessage(query, "user");
    queryInput.value = "";

    // Show loading in bot bubble and answer card
    const botMsgId = appendMessage("Running Text-to-SQL pipeline...", "bot", true);
    answerText.innerHTML = `<div class="loader-spinner"></div>`;
    timingTag.textContent = "Processing...";

    try {
      const startTime = performance.now();
      const response = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: query,
          session_id: sessionId,
          dry_run: dryRun,
          entity_id: entitySelect.value || null,
        }),
      });

      if (!response.ok) {
        const errData = await response.json();
        throw new Error(errData.detail || "Query failed");
      }

      const data = await response.json();
      const clientDuration = Math.round(performance.now() - startTime);

      updateBotMessage(botMsgId, data.answer || "Query executed successfully.");
      renderDashboard(data, clientDuration);
    } catch (err) {
      updateBotMessage(botMsgId, `⚠️ Error: ${err.message}`);
      answerText.textContent = `Error processing query: ${err.message}`;
      timingTag.textContent = "Error";
    }
  });

  // Render Dashboard
  function renderDashboard(data, clientDuration) {
    currentSQL = data.extracted_sql || "";
    currentResultData = data.query_result;

    // Answer & Timing
    answerText.textContent = data.answer || "No response generated.";
    timingTag.textContent = `${data.total_time_ms ? data.total_time_ms.toFixed(1) : clientDuration} ms`;

    // Grounded/verified indicator — reflects whether the synthesized answer's
    // numbers were verified against the actual query result, or a fallback
    // template had to be substituted because they weren't traceable.
    if (data.query_result && data.query_result.success && data.query_result.row_count > 0) {
      groundedTag.classList.remove("hidden");
      answerCard.classList.toggle("ungrounded", !data.numbers_grounded);
      if (data.numbers_grounded) {
        groundedTag.className = "grounded-tag ok";
        groundedTag.textContent = "✓ Numbers verified against result";
      } else {
        groundedTag.className = "grounded-tag fallback";
        groundedTag.textContent = "⚠ Fallback answer (figure unverifiable)";
      }
    } else {
      groundedTag.classList.add("hidden");
      answerCard.classList.remove("ungrounded");
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

    // Confidence Badge
    if (data.confidence) {
      confidenceBadge.className = `confidence-pill ${data.confidence.level.toLowerCase()}`;
      confText.textContent = `${data.confidence.score}% ${data.confidence.level} Confidence`;
      confidenceBadge.classList.remove("hidden");
    } else {
      confidenceBadge.classList.add("hidden");
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

    if (data.query_result) {
      metaTables.textContent = (data.query_result.tables_touched || []).join(", ") || "None";
      metaLatency.textContent = `${data.query_result.execution_time_ms ? data.query_result.execution_time_ms.toFixed(1) : 0} ms`;
    } else {
      metaTables.textContent = "—";
      metaLatency.textContent = "—";
    }
    metaLlm.textContent = data.llm_model || data.llm_provider || "Ollama";
    metaRetries.textContent = `${data.retries || 0}`;
    metaTokens.textContent = `${data.prompt_tokens || 0} / ${data.completion_tokens || 0}`;

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
      if (data.status === "ok") {
        dbStatusText.textContent = `MySQL: ${data.total_rows} Rows (${data.tables} Tables)`;
        activeModelText.textContent = data.llm_model || data.llm_provider;
      }
    } catch (e) {
      dbStatusText.textContent = "DB Status: Offline";
    }
  }

  // Fetch Entities — populates the "Customer" dropdown. No login in this
  // build (see README), so this is how a demo user picks which customer's
  // data to scope questions to; it's a usability convenience, not auth.
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
        entitySelect.appendChild(opt);
      });
    } catch (e) {
      // Non-fatal — the app still works fully unscoped without this.
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
