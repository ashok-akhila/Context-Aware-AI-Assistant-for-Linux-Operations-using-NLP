const chatLog = document.getElementById('chat-log');
const input = document.getElementById('query-input');
const sendBtn = document.getElementById('send-btn');
const confirmTpl = document.getElementById('tpl-confirm-card');

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function addMessage(label, bodyText, cls) {
  const wrap = el('div', `msg ${cls || ''}`);
  wrap.appendChild(el('div', 'msg-label', label));
  wrap.appendChild(el('div', 'msg-body', bodyText));
  chatLog.appendChild(wrap);
  chatLog.scrollTop = chatLog.scrollHeight;
  return wrap;
}

function addBlock(parent, labelText, bodyText) {
  const block = el('div', 'msg-block');
  block.appendChild(el('div', 'msg-block-label', labelText));
  const body = el('div', 'msg-body', bodyText);
  block.appendChild(body);
  parent.appendChild(block);
  chatLog.scrollTop = chatLog.scrollHeight;
  return block;
}

// ---------------- context panel polling ----------------

async function refreshContext() {
  try {
    const res = await fetch('/api/context');
    if (!res.ok) throw new Error('bad status');
    const ctx = await res.json();
    document.getElementById('conn-dot').classList.remove('offline');

    document.getElementById('ctx-host').innerHTML = `
      <div class="ctx-label">host</div>
      <div class="ctx-value">${ctx.hostname}</div>
      <div class="ctx-value" style="color:var(--text-dim);font-size:11px;margin-top:3px;">
        ${ctx.distro.pretty_name} · ${ctx.kernel_version}${ctx.is_wsl ? ' · WSL' : ''}
      </div>`;

    const cpuPct = ctx.cpu.usage_percent;
    const cpuFill = document.getElementById('cpu-fill');
    cpuFill.style.width = Math.min(cpuPct, 100) + '%';
    cpuFill.className = 'meter-fill' + (cpuPct > 85 ? ' danger' : cpuPct > 60 ? ' warn' : '');
    document.getElementById('cpu-value').textContent =
      `${cpuPct}% · ${ctx.cpu.core_count_logical} cores`;

    const memPct = ctx.memory.used_percent;
    const memFill = document.getElementById('mem-fill');
    memFill.style.width = Math.min(memPct, 100) + '%';
    memFill.className = 'meter-fill' + (memPct > 85 ? ' danger' : memPct > 60 ? ' warn' : '');
    document.getElementById('mem-value').textContent =
      `${ctx.memory.used_gb} / ${ctx.memory.total_gb} GB`;

    document.getElementById('user-value').textContent =
      `${ctx.user.username}${ctx.user.is_root ? ' (root)' : ctx.user.has_sudo_capability ? ' (sudo)' : ''}`;
  } catch (e) {
    document.getElementById('conn-dot').classList.add('offline');
  }
}

document.getElementById('refresh-ctx').addEventListener('click', refreshContext);
refreshContext();
setInterval(refreshContext, 8000);

// ---------------- chat / query flow ----------------

async function handleSend() {
  const query = input.value.trim();
  if (!query) return;
  input.value = '';
  sendBtn.disabled = true;

  addMessage('you', query, 'msg-user');
  const thinking = addMessage('context aware linux', 'Collecting real system data…', 'msg-system');

  try {
    const res = await fetch('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    const data = await res.json();
    thinking.remove();

    if (!res.ok) {
      const msg = addMessage('context aware linux', data.error || 'Something went wrong.', 'msg-system');
      if (data.context_collected) {
        addBlock(msg, 'context collected before failure', JSON.stringify(data.context_collected, null, 2));
      }
      return;
    }

    const llm = data.llm_result;
    const msg = addMessage('context aware linux', llm.explanation, 'msg-system');

    if (llm.action === 'clarify') {
      addBlock(msg, 'clarifying question', llm.clarifying_question);
    } else {
      if (llm.recommendation) {
        addBlock(msg, 'recommendation', llm.recommendation);
      }
      if (llm.command) {
        renderConfirmCard(msg, llm);
      }
    }
  } catch (e) {
    thinking.remove();
    addMessage('context aware linux', `Request failed: ${e.message}`, 'msg-system');
  } finally {
    sendBtn.disabled = false;
  }
}

function renderConfirmCard(parent, llm) {
  const node = confirmTpl.content.cloneNode(true);
  const card = node.querySelector('.confirm-card');

  node.querySelector('.confirm-cmd').textContent = llm.command;
  const badge = node.querySelector('.risk-badge');
  badge.textContent = llm.risk_level;
  badge.classList.add('risk-' + llm.risk_level);
  node.querySelector('.confirm-effect').textContent = llm.recommendation || llm.explanation;

  const confirmBtn = node.querySelector('.btn-confirm');
  const cancelBtn = node.querySelector('.btn-cancel');

  if (!llm.requires_confirmation) {
    confirmBtn.textContent = 'Run (read-only)';
  }

  confirmBtn.addEventListener('click', async () => {
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    confirmBtn.textContent = 'Running…';
    await runCommand(parent, llm.command, true);
    card.remove();
  });

  cancelBtn.addEventListener('click', () => {
    addBlock(parent, 'cancelled', 'Command was not executed.');
    card.remove();
  });

  parent.appendChild(node);
  chatLog.scrollTop = chatLog.scrollHeight;
}

async function runCommand(parent, command, confirmed) {
  try {
    const res = await fetch('/api/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command, confirmed }),
    });
    const data = await res.json();

    const block = el('div', 'msg-block');
    block.appendChild(el('div', 'msg-block-label', data.success ? 'executed · success' : 'executed · failed'));

    const out = el('div', `result-block ${data.success ? 'ok' : 'err'}`);
    out.textContent = [
      `$ ${data.command}`,
      data.stdout ? data.stdout.trim() : '',
      data.stderr ? '[stderr]\n' + data.stderr.trim() : '',
      `exit code: ${data.return_code}`,
    ].filter(Boolean).join('\n\n');
    block.appendChild(out);
    parent.appendChild(block);

    if (data.ai_error_analysis && !data.ai_error_analysis.error) {
      addBlock(parent, 'ai explanation of the error', data.ai_error_analysis.explanation);
      if (data.ai_error_analysis.recommendation) {
        addBlock(parent, 'recommendation', data.ai_error_analysis.recommendation);
      }
    } else if (data.block_reason) {
      addBlock(parent, 'blocked', data.block_reason);
    }

    chatLog.scrollTop = chatLog.scrollHeight;
  } catch (e) {
    addBlock(parent, 'error', `Execution request failed: ${e.message}`);
  }
}

sendBtn.addEventListener('click', handleSend);
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') handleSend();
});
