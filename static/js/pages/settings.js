import { api } from '../api.js';

export async function renderSettings(el) {
  const data = await api('/api/settings');
  const summary = await api('/api/cursors/summary');
  const totals = summary.totals || {};
  el.innerHTML = `
    <div class="card">
      <h2>台参数</h2>
      <table>
        <thead><tr><th>项</th><th>说明</th><th>值</th><th></th></tr></thead>
        <tbody>
          ${(data.items || []).map((s) => `
            <tr>
              <td>${s.key}</td>
              <td>${s.label}</td>
              <td><input data-key="${s.key}" value="${s.value}" /></td>
              <td><button data-save="${s.key}">保存</button></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>
    <div class="card">
      <h2>游标断点口径（只读）</h2>
      <p class="hint-box">本卡只读，断点写定入口仅在“抓取批次 → 游标断点队列”，避免双开。</p>
      <table>
        <tbody>
          <tr><th>工程形态</th><td>${summary.mode}</td></tr>
          <tr><th>唯一读引用</th><td>${summary.read_ref}</td></tr>
          <tr><th>断点芯片口径</th><td>有队列头落点行 → ${summary.chip_ok}；无落点行 → ${summary.chip_bad}（芯片不允许脱离落点行单独亮）</td></tr>
          <tr><th>重抽次数口径</th><td>仅“按断点重抽”成功单 +1；无落点/指纹偏/页键不符一律拒绝且不计数</td></tr>
          <tr><th>指纹口径</th><td>sha256(源站|页键|末条目键|前指纹)；推进必须携带当前队列头指纹，不符即拒</td></tr>
          <tr><th>串行口径</th><td>同源站断点经 cursor_locks 串行 + 队列头唯一索引兜底，争抢时后者整体回滚</td></tr>
          <tr><th>汇总量</th><td>源站 ${totals.sources} · 有落点 ${totals.landed} · 无落点 ${totals.headless} · 重抽 ${totals.re_pull_count} · 拦截重复 ${totals.blocked_dup_count} · 归档钉点 ${totals.archived_heads}</td></tr>
        </tbody>
      </table>
      <table style="margin-top:10px">
        <thead><tr><th>源站</th><th>芯片</th><th>落点页键</th><th>指纹</th><th>重抽次数</th><th>拦截重复</th></tr></thead>
        <tbody>
          ${(summary.sources || []).map((s) => `
            <tr>
              <td>${s.source_name}</td>
              <td><span class="${s.has_landing ? 'chip ok' : 'chip bad'}">${s.chip}</span></td>
              <td>${s.page_key || '无落点行'}</td>
              <td class="muted">${s.fingerprint_short ? s.fingerprint_short + '…' : '—'}</td>
              <td>${s.re_pull_count}</td>
              <td>${s.blocked_dup_count}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  el.querySelectorAll('[data-save]').forEach((btn) => {
    btn.onclick = async () => {
      const input = el.querySelector(`input[data-key="${btn.dataset.save}"]`);
      await api('/api/settings/update', {
        method: 'POST',
        body: JSON.stringify({ key: btn.dataset.save, value: input.value }),
      });
      await renderSettings(el);
    };
  });
}
