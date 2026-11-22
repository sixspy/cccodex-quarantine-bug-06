import { api } from '../api.js';

export async function renderBins(el) {
  const bins = await api('/api/bins');
  const rows = await api('/api/rows');
  const summary = await api('/api/cursors/summary');
  const totals = summary.totals || {};
  el.innerHTML = `
    <div class="card">
      <h2>隔离舱</h2>
      <table>
        <thead><tr><th>舱号</th><th>标题</th><th>状态</th><th>占用/容量</th><th>待复核</th></tr></thead>
        <tbody>
          ${(bins.items || []).map((b) => `
            <tr>
              <td>${b.bin_code}</td>
              <td>${b.title}</td>
              <td><span class="chip">${b.status}</span></td>
              <td>${b.row_count}/${b.capacity}</td>
              <td>${b.pending_count}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>
    <div class="card" id="repullCard">
      <h2>舱卡 · 游标重抽计数</h2>
      <p class="muted">重抽次数只随“按断点重抽”成功单递增；无断点落点行时重抽请求被拒，次数不涨。重复回灌被拦截计数同卡亮明。</p>
      <div class="stat-strip">
        <div class="stat"><b>${totals.landed ?? 0}</b><span class="muted">已落断点源站</span></div>
        <div class="stat"><b>${totals.headless ?? 0}</b><span class="muted">无落点源站</span></div>
        <div class="stat"><b>${totals.re_pull_count ?? 0}</b><span class="muted">累计重抽次数</span></div>
        <div class="stat"><b>${totals.blocked_dup_count ?? 0}</b><span class="muted">拦截重复灌舱</span></div>
        <div class="stat"><b>${totals.archived_heads ?? 0}</b><span class="muted">归档旧钉点</span></div>
      </div>
      <table>
        <thead><tr><th>源站</th><th>断点芯片</th><th>重抽次数</th><th>拦截重复</th><th>落点页键</th></tr></thead>
        <tbody>
          ${(summary.sources || []).map((s) => `
            <tr>
              <td>${s.source_name}</td>
              <td><span class="${s.has_landing ? 'chip ok' : 'chip bad'}">${s.chip}</span></td>
              <td><b>${s.re_pull_count}</b></td>
              <td>${s.blocked_dup_count}</td>
              <td class="muted">${s.page_key || '无落点行'}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>
    <div class="card">
      <h2>舱内行</h2>
      <table>
        <thead><tr><th>舱</th><th>条目键</th><th>质量</th><th>原因</th><th>状态</th></tr></thead>
        <tbody>
          ${(rows.items || []).map((r) => `
            <tr>
              <td>${r.bin_code}</td>
              <td>${r.item_key}</td>
              <td>${r.quality}</td>
              <td>${r.reason}</td>
              <td><span class="chip">${r.status}</span></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}
