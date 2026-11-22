import { api } from '../api.js';

export async function renderReconcile(el) {
  const data = await api('/api/reports');
  const summary = await api('/api/cursors/summary');
  const totals = summary.totals || {};
  el.innerHTML = `
    <div class="card">
      <h2>隔离对账</h2>
      <div class="row"><button id="run">生成对账报告</button></div>
      <table>
        <thead><tr><th>时间</th><th>摘要</th><th>待复核</th><th>放行</th><th>回炉</th><th>废弃</th><th>错位</th></tr></thead>
        <tbody>
          ${(data.items || []).map((r) => `
            <tr>
              <td>${r.created_at}</td>
              <td>${r.summary}</td>
              <td>${r.open_rows}</td>
              <td>${r.released}</td>
              <td>${r.reworking}</td>
              <td>${r.abandoned}</td>
              <td><span class="chip ${r.mismatch ? 'bad' : 'ok'}">${r.mismatch}</span></td>
            </tr>`).join('') || '<tr><td colspan="7" class="muted">暂无报告</td></tr>'}
        </tbody>
      </table>
    </div>
    <div class="card">
      <h2>游标摘要</h2>
      <p class="muted">
        工程形态：<b>${summary.mode}</b> · 唯一读引用：<b>${summary.read_ref}</b> ·
        禁双开：${summary.dual_open ? '否' : '是'} ·
        芯片/次数/落点全对齐：<span class="chip ${summary.all_aligned ? 'ok' : 'bad'}">${summary.all_aligned ? '是' : '否'}</span>
      </p>
      <div class="stat-strip">
        <div class="stat"><b>${totals.sources ?? 0}</b><span class="muted">源站</span></div>
        <div class="stat"><b>${totals.landed ?? 0}</b><span class="muted">有落点</span></div>
        <div class="stat"><b>${totals.headless ?? 0}</b><span class="muted">无落点</span></div>
        <div class="stat"><b>${totals.re_pull_count ?? 0}</b><span class="muted">重抽次数</span></div>
        <div class="stat"><b>${totals.blocked_dup_count ?? 0}</b><span class="muted">拦截重复</span></div>
      </div>
      <table>
        <thead><tr><th>源站</th><th>断点芯片</th><th>读引用</th><th>落点页键</th><th>末条目键</th><th>指纹</th><th>版本</th><th>重抽次数</th><th>拦截重复</th></tr></thead>
        <tbody>
          ${(summary.sources || []).map((s) => `
            <tr>
              <td>${s.source_name}</td>
              <td><span class="${s.has_landing ? 'chip ok' : 'chip bad'}">${s.chip}</span></td>
              <td class="muted">${s.read_ref || '无引用'}</td>
              <td>${s.page_key || '无落点行'}</td>
              <td>${s.last_item_key || '—'}</td>
              <td class="muted">${s.fingerprint_short ? s.fingerprint_short + '…' : '—'}</td>
              <td>${s.version || '—'}</td>
              <td><b>${s.re_pull_count}</b></td>
              <td>${s.blocked_dup_count}</td>
            </tr>`).join('')}
        </tbody>
      </table>
      <p class="muted">串行锁占用：${(summary.locks || []).length ? summary.locks.map((l) => `${l.source_name}(${l.holder})`).join(' · ') : '空闲（无争抢写定）'}</p>
    </div>`;
  el.querySelector('#run').onclick = async () => {
    await api('/api/reports/run', { method: 'POST', body: '{}' });
    await renderReconcile(el);
  };
}
