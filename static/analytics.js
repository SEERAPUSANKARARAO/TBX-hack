document.addEventListener('DOMContentLoaded', () => {
  const panel = document.getElementById('analytics-panel');
  const chat = document.querySelector('.main-workspace');
  const chatTab = document.getElementById('tab-chat');
  const analyticsTab = document.getElementById('tab-analytics');
  let requests = [], generation = 0;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
  const label = value => value.replaceAll('_', ' ');
  panel.innerHTML = `<div class="an-heading"><div><span class="an-eyebrow">OBSERVABILITY</span><h2>Platform analytics</h2><p>Understand every question, model and response.</p></div><div class="an-filters"><label>Period<select id="an-period"><option value="1">Last 24 hours</option><option value="7" selected>Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select></label><label>Model<select id="an-model"><option value="">All models</option></select></label><button id="an-refresh" type="button">Refresh</button></div></div>
    <p id="an-status" role="status"></p><div id="an-content"></div>
    <section class="an-card an-benchmark"><h3>Offline evaluation</h3><p>Latest saved benchmark • separate from live traffic and period filters</p><div id="an-benchmark">Loading benchmark…</div></section>
    <dialog id="an-detail"><button type="button" id="an-close">Close</button><h3>Request detail</h3><div id="an-detail-body"></div></dialog>`;
  function activate(show) {
    panel.hidden = !show;
    chat.classList.toggle('hidden', show);
    chatTab.setAttribute('aria-pressed', String(!show));
    analyticsTab.setAttribute('aria-pressed', String(show));
    if (show) refresh();
  }
  chatTab.onclick = () => activate(false);
  analyticsTab.onclick = () => activate(true);
  document.getElementById('an-refresh').onclick = refresh;
  document.getElementById('an-period').onchange = refresh;
  document.getElementById('an-model').onchange = render;
  document.getElementById('an-close').onclick = () => document.getElementById('an-detail').close();
  async function refresh() {
    const current = ++generation;
    const status = document.getElementById('an-status');
    status.textContent = 'Loading recorded requests…';
    try {
      const res = await fetch(`/api/analytics?days=${document.getElementById('an-period').value}`);
      if (!res.ok) throw new Error('Analytics could not be loaded. Please retry.');
      const data = await res.json();
      if (current !== generation) return;
      requests = data.requests;
      const model = document.getElementById('an-model'), selected = model.value;
      model.innerHTML = '<option value="">All models</option>' + [...new Set(requests.map(r => r.model))].sort().map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
      if ([...model.options].some(o => o.value === selected)) model.value = selected;
      status.textContent = `Updated ${new Date(data.generated_at).toLocaleTimeString()} • Chart dates in UTC • All customer scopes • Dry runs excluded${data.truncated ? ' • Showing latest 10,000 requests only; metrics are partial' : ''}`;
      render();
    } catch (error) {
      if (current !== generation) return;
      status.textContent = error.message;
      document.getElementById('an-content').innerHTML = '<div class="an-empty">Analytics unavailable. Your chat remains available.</div>';
    }
    try {
      const res = await fetch('/api/accuracy');
      if (!res.ok) throw new Error();
      const data = await res.json();
      document.getElementById('an-benchmark').innerHTML = data.available
        ? `<strong>${fmt(data.pass_rate)}% passed</strong> <span>(${data.passed}/${data.total_queries} cases)</span>` + bars(Object.entries(data.by_kind || {}).map(([k,v]) => [label(k), v.total ? v.pass / v.total * 100 : 0]), 'percent passed')
        : '<div class="an-empty">No saved evaluation available. Run the benchmark to populate this view.</div>';
    } catch { document.getElementById('an-benchmark').textContent = 'Benchmark unavailable.'; }
  }
  function bars(entries, unit, color = '#34d399') {
    if (!entries.length) return '<div class="an-empty">No observations in this period.</div>';
    const maximum = Math.max(...entries.map(e => e[1]), 1);
    return `<div class="an-bars">${entries.map(([name,value]) => `<div class="an-bar"><div><span>${esc(name)}</span><strong>${fmt(value)} <small>${esc(unit)}</small></strong></div><div class="an-track"><i style="width:${Math.max(0,value/maximum*100)}%;background:${color}"></i></div></div>`).join('')}</div>`;
  }
  function dailyChart(entries, series, unit) {
    if (!entries.length) return '<div class="an-empty">Ask a question in Chat to start collecting telemetry.</div>';
    const w=640, h=220, left=55, right=620, top=20, bottom=178;
    const max=Math.max(1,...entries.flatMap(e=>series.map(s=>e[s.key] || 0)));
    const x=i=>left+(right-left)*(entries.length===1 ? .5 : i/(entries.length-1));
    const y=v=>bottom-(v/max)*(bottom-top);
    let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Daily ${esc(unit)}"><title>Daily ${esc(unit)}; exact values available below</title>`;
    for(let i=0;i<=4;i++){const v=max*i/4;svg+=`<line x1="${left}" x2="${right}" y1="${y(v)}" y2="${y(v)}" stroke="#263245"/><text x="${left-8}" y="${y(v)+4}" text-anchor="end">${fmt(v)}</text>`;}
    series.forEach(s=>{svg+=`<polyline points="${entries.map((e,i)=>`${x(i)},${y(e[s.key]||0)}`).join(' ')}" fill="none" stroke="${s.color}" stroke-width="2.5"/>`;entries.forEach((e,i)=>{svg+=`<circle cx="${x(i)}" cy="${y(e[s.key]||0)}" r="3" fill="${s.color}"><title>${esc(e.day)}: ${esc(s.name)} ${fmt(e[s.key]||0)} ${esc(unit)}</title></circle>`;});});
    [0,...(entries.length>1?[entries.length-1]:[])].forEach(i=>{svg+=`<text x="${x(i)}" y="205" text-anchor="middle">${entries[i].day.slice(5)}</text>`;});
    svg+='</svg>';
    return `<div class="an-legend">${series.map(s=>`<span><i style="background:${s.color}"></i>${s.name}</span>`).join('')}</div>${svg}<details><summary>View daily values</summary><div class="an-table-wrap"><table><thead><tr><th>Date (UTC)</th>${series.map(s=>`<th>${s.name} (${unit})</th>`).join('')}</tr></thead><tbody>${entries.map(e=>`<tr><td>${e.day}</td>${series.map(s=>`<td>${fmt(e[s.key]||0)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`;
  }
  function render() {
    const selected = document.getElementById('an-model').value;
    const rows = requests.filter(r=>!selected || r.model===selected);
    const sum = key => rows.reduce((a,r)=>a+(r[key]||0),0);
    const p = (values,q) => {const sorted=[...values].sort((a,b)=>a-b);return sorted.length ? sorted[Math.max(0,Math.ceil(sorted.length*q)-1)] : null;};
    const count = key => {const map={};rows.forEach(r=>map[r[key]]=(map[r[key]]||0)+1);return Object.entries(map).sort((a,b)=>b[1]-a[1]);};
    const days = {};
    rows.forEach(r=>{const day=r.timestamp.slice(0,10);(days[day] ||= []).push(r);});
    const daily=Object.keys(days).sort().map(day=>({day, count:days[day].length, input:days[day].reduce((a,r)=>a+(r.input_tokens||0),0), output:days[day].reduce((a,r)=>a+(r.output_tokens||0),0), p50:p(days[day].map(r=>r.duration_ms),.5)/1000,p95:p(days[day].map(r=>r.duration_ms),.95)/1000}));
    const missing = rows.filter(r=>r.input_tokens===null).length;
    const cards=[['Questions',fmt(rows.length),'Recorded requests'],['Conversations',fmt(new Set(rows.map(r=>r.session_id)).size),'Session IDs; reload starts a new session'],['Reported tokens',fmt(sum('input_tokens')+sum('output_tokens')),`${missing} requests with unavailable usage`],['p95 response time',rows.length?`${fmt(p(rows.map(r=>r.duration_ms),.95)/1000)} s`:'—','Server pipeline, including synthesis'],['SQL repair retries',fmt(sum('retries')),'HTTP retries not included'],['API cost','Unavailable','Billing telemetry not connected'],['Unique users','Unavailable','No persistent user identity'],['Answered',fmt(rows.filter(r=>r.outcome==='answered').length),'Delivered result; not verified accuracy']];
    const card=(title,subtitle,body)=>`<section class="an-card"><h3>${title}</h3><p>${subtitle}</p>${body}</section>`;
    document.getElementById('an-content').innerHTML = `<div class="an-kpis">${cards.map(([name,value,note])=>`<article><span>${name}</span><strong>${value}</strong><small>${note}</small></article>`).join('')}</div>${!rows.length?'<div class="an-empty an-start">No recorded traffic yet. Use the Chat tab, then refresh. Historical requests are not backfilled.</div>':''}<div class="an-grid">`+
      card('Question volume','Daily requests • days with recorded traffic',dailyChart(daily,[{key:'count',name:'Questions',color:'#34d399'}],'requests'))+
      card('Response latency','Daily p50 and p95 • small samples may be identical',dailyChart(daily,[{key:'p50',name:'p50',color:'#34d399'},{key:'p95',name:'p95',color:'#a78bfa'}],'seconds'))+
      card('Token consumption','Reported usage • missing usage is excluded',dailyChart(daily,[{key:'input',name:'Input',color:'#38bdf8'},{key:'output',name:'Output',color:'#a78bfa'}],'tokens'))+
      card('Question outcomes','Clarifications and blocked requests are shown separately',bars(count('outcome').map(([k,v])=>[label(k),v]),'requests'))+
      card('Model usage','Configured model per request; direct responses use no model',bars(count('model'),'requests','#38bdf8'))+
      card('Numeric grounding checks','Passing this check does not prove semantic correctness',bars(count('grounding').map(([k,v])=>[label(k),v]),'requests','#a78bfa'))+
      '</div>'+card('Recent requests','Latest 100 matching requests • select a row for recorded metadata',`<div class="an-table-wrap"><table><thead><tr><th>Time (UTC)</th><th>Model</th><th>Outcome</th><th>Latency</th><th>Tokens in / out</th><th>Details</th></tr></thead><tbody>${rows.slice(0,100).map(r=>`<tr><td>${esc(r.timestamp.slice(0,19).replace('T',' '))}</td><td>${esc(r.model)}</td><td>${esc(label(r.outcome))}</td><td>${fmt(r.duration_ms/1000)} s</td><td>${r.input_tokens===null?'—':fmt(r.input_tokens)} / ${r.output_tokens===null?'—':fmt(r.output_tokens)}</td><td><button type="button" data-request="${esc(r.id)}">Inspect</button></td></tr>`).join('') || '<tr><td colspan="6">No requests yet.</td></tr>'}</tbody></table></div>`);
    panel.querySelectorAll('[data-request]').forEach(button=>button.onclick=()=>{
      const row=rows.find(r=>r.id===button.dataset.request);
      document.getElementById('an-detail-body').innerHTML=`<dl>${Object.entries(row).map(([key,value])=>`<dt>${esc(label(key))}</dt><dd>${esc(value??'Unavailable')}</dd>`).join('')}</dl><p>Prompt text, SQL and financial records are not stored in analytics.</p>`;
      document.getElementById('an-detail').showModal();
    });
  }
});
