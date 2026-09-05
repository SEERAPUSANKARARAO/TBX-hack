/* Charts use backend-validated result specifications. No prose parsing or requery. */
window.ResultCharts = (() => {
  const colors=['#34d399','#38bdf8','#a78bfa','#fbbf24'];
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const format=(v,unit)=>v===null?'Unavailable':`${Number(v).toLocaleString(undefined,{maximumFractionDigits:unit==='count'?0:2})}${unit==='percent'?'%':''}`;
  const unitLabel=u=>({amount:'Amount · dataset currency units',count:'Count',percent:'Percent'}[u]);
  function clear(){const panel=document.getElementById('result-chart-card');if(panel){panel.hidden=true;panel.replaceChildren();}}
  function render(data){
    clear(); const spec=data.visualization, panel=document.getElementById('result-chart-card');
    if(!spec || !['bar','line','kpi'].includes(spec.kind))return;
    const series=spec.series||[],rows=spec.rows||[];
    if(!series.length||!rows.length)return;
    const dates=data.resolved_entities?.dates;
    const scope=data.entity_scope_label || document.getElementById('entity-locked-text')?.textContent || 'Current customer scope';
    panel.hidden=false;
    panel.innerHTML=`<details open><summary>Visual breakdown <span>${esc(spec.title)}</span></summary><div class="rc-content"><p class="rc-scope">${esc(scope)}${dates?.start_date?` · ${esc(dates.start_date)} → ${esc(dates.end_date||'')}`:''}</p><div class="rc-controls"></div><div class="rc-plot"></div><div class="rc-metrics"></div><p class="rc-notes">${(spec.notes||[]).map(esc).join(' ')}</p><p class="rc-notes">Exact returned values and supporting records remain in the table below.</p></div></details>`;
    const plot=panel.querySelector('.rc-plot');
    const metricHTML=items=>`<div class="rc-kpis">${items.map(s=>`<div><span>${esc(s.label)}</span><strong>${esc(format(s.value,s.unit))}</strong><small>${esc(unitLabel(s.unit))}</small></div>`).join('')}</div>`;
    panel.querySelector('.rc-metrics').innerHTML=metricHTML(spec.metrics||[]);
    if(spec.kind==='kpi'){plot.innerHTML=metricHTML(series.map(s=>({...s,value:rows[0][s.key]})));return;}
    const units=[...new Set(series.map(s=>s.unit))];
    const controls=panel.querySelector('.rc-controls');
    controls.innerHTML=`<label>Measure <select class="rc-measure">${units.map(u=>`<option value="${esc(u)}">${esc(unitLabel(u))}</option>`).join('')}</select></label><label>View <select class="rc-view"><option value="${spec.kind}">${spec.kind==='line'?'Line':'Bars'}</option>${spec.kind==='line'?'<option value="bar">Bars</option>':''}</select></label>`;
    const draw=()=>{
      const selected=series.filter(s=>s.unit===controls.querySelector('.rc-measure').value);
      const values=rows.flatMap(r=>selected.map(s=>r[s.key]===null?null:Number(r[s.key]))).filter(v=>v!==null&&Number.isFinite(v));
      if(!values.length){plot.textContent='No numeric observations for this measure.';return;}
      const low=Math.min(0,...values),high=Math.max(0,...values),range=high-low||1;
      const legend=`<div class="rc-legend">${selected.map((s,i)=>`<span><i style="background:${colors[i%colors.length]}"></i>${esc(s.label)}</span>`).join('')}</div>`;
      const line=controls.querySelector('.rc-view').value==='line';
      const W=760,L=line?75:215,R=700,T=26,H=line?260:Math.max(130,rows.length*(selected.length*24+22)+55),B=H-40;
      const x=v=>L+(v-low)/range*(R-L),y=v=>B-(v-low)/range*(B-T);
      let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(spec.title)}"><title>${esc(spec.title)}; exact data in the table below</title>`;
      for(let i=0;i<=4;i++){const v=low+range*i/4;svg+=line?`<line x1="${L}" x2="${R}" y1="${y(v)}" y2="${y(v)}" class="rc-grid"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end">${esc(format(v,selected[0].unit))}</text>`:`<line x1="${x(v)}" x2="${x(v)}" y1="${T}" y2="${B}" class="rc-grid"/><text x="${x(v)}" y="${H-10}" text-anchor="middle">${esc(format(v,selected[0].unit))}</text>`;}
      svg+=line?`<line x1="${L}" x2="${R}" y1="${y(0)}" y2="${y(0)}" class="rc-zero"/>`:`<line x1="${x(0)}" x2="${x(0)}" y1="${T}" y2="${B}" class="rc-zero"/>`;
      if(line){
        const pointX=i=>L+i/Math.max(1,rows.length-1)*(R-L);
        selected.forEach((s,j)=>{
          let segment=[];
          const flush=()=>{if(segment.length)svg+=`<polyline points="${segment.join(' ')}" fill="none" stroke="${colors[j%4]}" stroke-width="2"/>`;segment=[];};
          rows.forEach((r,i)=>{if(r[s.key]===null){flush();return;}const v=Number(r[s.key]);segment.push(`${pointX(i)},${y(v)}`);svg+=`<circle tabindex="0" cx="${pointX(i)}" cy="${y(v)}" r="4" fill="${colors[j%4]}"><title>${esc(r[spec.category])} · ${esc(s.label)}: ${esc(r[s.key])}</title></circle>`;});flush();
        });
        [0,rows.length-1].forEach(i=>svg+=`<text x="${pointX(i)}" y="${H-12}" text-anchor="middle">${esc(rows[i][spec.category])}</text>`);
      }else rows.forEach((r,i)=>{
        const base=T+i*(selected.length*24+22);
        svg+=`<text x="${L-12}" y="${base+13}" text-anchor="end"><title>${esc(r[spec.category])}</title>${esc(String(r[spec.category]).slice(0,26))}</text>`;
        selected.forEach((s,j)=>{const v=r[s.key]===null?null:Number(r[s.key]),yy=base+j*24;
          if(v===null){svg+=`<text x="${L+10}" y="${yy+13}">Unavailable</text>`;return;}
          svg+=`<rect tabindex="0" x="${Math.min(x(0),x(v))}" y="${yy}" width="${Math.abs(x(v)-x(0))}" height="16" rx="3" fill="${colors[j%4]}"><title>${esc(r[spec.category])} · ${esc(s.label)}: ${esc(r[s.key])}</title></rect>`;
        });
      });
      plot.innerHTML=legend+svg+'</svg>';
    };
    controls.querySelectorAll('select').forEach(s=>s.onchange=draw);draw();
  }
  return {clear,render};
})();
