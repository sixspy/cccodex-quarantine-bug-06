import { api } from '../api.js';

function chipClass(cursor) {
  return cursor && cursor.has_landing ? 'chip ok' : 'chip bad';
}

function chipText(cursor) {
  return cursor && cursor.has_landing ? cursor.chip : '仍整页重抽';
}

export async function renderCaptures(el) {
  const data = await api('/api/batches');
  const bins = await api('/api/bins');
  const openBin = (bins.items || []).find((b) => b.status === '开放');
  const summary = await api('/api/cursors/summary');
  const sources = summary.sources || [];
  el.innerHTML = `
    <div class="card">
      <h2>抓取批次</h2>
      <p class="muted">读轨唯一引用：<b>${summary.read_ref}</b>（${summary.mode}，禁双开：${summary.dual_open ? '否' : '是'}）</p>
      <div class="row">
        <input id="src" placeholder="来源名" />
        <input id="note" placeholder="备注" />
        <button id="create">新建批次</button>
      </div>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>批号</th><th>来源</th><th>断点芯片</th><th>重抽次数</th><th>断点落点（页键/末键/指纹）</th><th>状态</th><th>条目/坏件</th><th>操作</th></tr></thead>
        <tbody>
          ${(data.items || []).map((b) => `
            <tr>
              <td>${b.batch_code}</td>
              <td>${b.source_name}</td>
              <td><span class="${chipClass(b.cursor)}">${chipText(b.cursor)}</span></td>
              <td>${(b.cursor && b.cursor.has_landing) ? b.cursor.re_pull_count : '—'}</td>
              <td class="muted">${(b.cursor && b.cursor.has_landing)
                ? `${b.cursor.page_key} / ${b.cursor.last_item_key} / ${b.cursor.fingerprint_short}…`
                : '无落点行'}</td>
              <td><span class="chip">${b.status}</span></td>
              <td>${b.item_count} / <span class="chip bad">${b.bad_count}</span></td>
              <td class="row">
                <button data-open="${b.id}">详情</button>
                <button data-close="${b.id}">关闭</button>
                ${openBin ? `<button data-auto="${b.id}">坏件入舱</button>` : ''}
              </td>
            </tr>`).join('')}
        </tbody>
      </table>
      <div id="detail" class="grid" style="margin-top:12px"></div>
    </div>
    <div class="card" id="cursorCard">
      <h2>游标断点队列（旁挂舱卡）</h2>
      <p class="muted">断点后台一次提交写定；失败会吐白话。芯片由落点行推导，无落点行只允许亮“仍整页重抽”。</p>
      <table>
        <thead><tr><th>源站</th><th>断点芯片</th><th>读引用</th><th>落点页键</th><th>末条目键</th><th>断点指纹</th><th>版本</th><th>重抽次数</th><th>拦截重复</th></tr></thead>
        <tbody>
          ${sources.map((s) => `
            <tr>
              <td>${s.source_name}</td>
              <td><span class="${s.has_landing ? 'chip ok' : 'chip bad'}">${s.chip}</span></td>
              <td class="muted">${s.has_landing ? s.read_ref : '无引用'}</td>
              <td>${s.page_key || '无落点行'}</td>
              <td>${s.last_item_key || '—'}</td>
              <td class="muted">${s.fingerprint_short ? s.fingerprint_short + '…' : '—'}</td>
              <td>${s.version || '—'}</td>
              <td><b>${s.re_pull_count}</b></td>
              <td>${s.blocked_dup_count}</td>
            </tr>`).join('')}
        </tbody>
      </table>
      <div class="row" style="margin-top:10px">
        <input id="cp_src" placeholder="源站名" />
        <input id="cp_page" placeholder="当刻页键" />
        <input id="cp_last" placeholder="当刻页末条目键" />
        <input id="cp_fp" placeholder="推进时填当前头指纹（首钉留空）" />
        <button id="cp_commit">落断点（单事务写定）</button>
        <span id="cp_msg" class="muted"></span>
      </div>
      <div class="row" style="margin-top:8px">
        <input id="rp_src" placeholder="重抽源站名" />
        <input id="rp_batch" placeholder="灌入批次ID" />
        <input id="rp_fp" placeholder="队列头指纹" />
        <input id="rp_keys" placeholder="重抽条目键，逗号分隔" />
        <button id="rp_run">按断点重抽当刻页</button>
        <span id="rp_msg" class="muted"></span>
      </div>
    </div>`;
  const flash = (id, msg, bad) => {
    const node = el.querySelector(id);
    node.textContent = msg;
    node.className = bad ? 'err' : 'muted';
  };
  el.querySelector('#create').onclick = async () => {
    await api('/api/batches', {
      method: 'POST',
      body: JSON.stringify({
        source_name: el.querySelector('#src').value,
        note: el.querySelector('#note').value,
      }),
    });
    await renderCaptures(el);
  };
  el.querySelectorAll('[data-close]').forEach((btn) => {
    btn.onclick = async () => {
      await api(`/api/batches/${btn.dataset.close}/close`, { method: 'POST', body: '{}' });
      await renderCaptures(el);
    };
  });
  el.querySelectorAll('[data-auto]').forEach((btn) => {
    btn.onclick = async () => {
      if (!openBin) return;
      await api('/api/quarantine/auto-enter', {
        method: 'POST',
        body: JSON.stringify({ batch_id: Number(btn.dataset.auto), bin_id: openBin.id }),
      });
      await renderCaptures(el);
    };
  });
  el.querySelector('#cp_commit').onclick = async () => {
    try {
      const r = await api('/api/cursors/commit', {
        method: 'POST',
        body: JSON.stringify({
          source_name: el.querySelector('#cp_src').value,
          page_key: el.querySelector('#cp_page').value,
          last_item_key: el.querySelector('#cp_last').value,
          expect_fingerprint: el.querySelector('#cp_fp').value,
        }),
      });
      flash('#cp_msg', `已写定 v${r.version} 指纹 ${r.fingerprint_short}…`, false);
    } catch (e) {
      flash('#cp_msg', e.message, true);
    }
    await renderCaptures(el);
  };
  el.querySelector('#rp_run').onclick = async () => {
    try {
      const keys = el.querySelector('#rp_keys').value.split(',').map((k) => k.trim()).filter(Boolean);
      const r = await api('/api/cursors/repull', {
        method: 'POST',
        body: JSON.stringify({
          source_name: el.querySelector('#rp_src').value,
          page_key: (sources.find((s) => s.source_name === el.querySelector('#rp_src').value) || {}).page_key,
          fingerprint: el.querySelector('#rp_fp').value,
          batch_id: Number(el.querySelector('#rp_batch').value || 0),
          items: keys.map((k) => ({ item_key: k, payload: '{}', quality: '合格' })),
        }),
      });
      flash('#rp_msg', `新灌 ${r.pulled} 条，拦截重复 ${r.blocked_dup} 条；累计重抽 ${r.re_pull_count} 次`, false);
    } catch (e) {
      flash('#rp_msg', e.message, true);
    }
    await renderCaptures(el);
  };
  el.querySelectorAll('[data-open]').forEach((btn) => {
    btn.onclick = async () => {
      const d = await api(`/api/batches/${btn.dataset.open}`);
      const box = el.querySelector('#detail');
      box.innerHTML = `
        <h3>${d.batch_code} 条目</h3>
        <div class="row">
          <input id="ikey" placeholder="条目键" />
          <select id="iq">
            <option>合格</option><option>缺字段</option><option>编码空</option><option>重复键</option>
          </select>
          <button id="addItem">追加条目</button>
        </div>
        <table>
          <thead><tr><th>键</th><th>质量</th><th>状态</th></tr></thead>
          <tbody>
            ${(d.items || []).map((i) => `
              <tr>
                <td>${i.item_key}</td>
                <td><span class="chip ${i.quality === '合格' ? 'ok' : 'bad'}">${i.quality}</span></td>
                <td>${i.status}</td>
              </tr>`).join('')}
          </tbody>
        </table>`;
      box.querySelector('#addItem').onclick = async () => {
        await api(`/api/batches/${d.id}/items`, {
          method: 'POST',
          body: JSON.stringify({
            item_key: box.querySelector('#ikey').value,
            quality: box.querySelector('#iq').value,
            payload: '{}',
          }),
        });
        btn.click();
      };
    };
  });
}
