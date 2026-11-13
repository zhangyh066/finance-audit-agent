const $ = selector => document.querySelector(selector);
const state = { user: null, sampleMode: false, view: 'overview', document: null, imagePreview: null, query: '', status: '', policyId: null };
const titles = { overview: '工作台', cases: '报销单据', policies: '制度规则', tools: '工具链', audit: '审计日志', settings: '账号与权限', case: '单据详情', form: '单据填写' };
const statusNames = { draft: '草稿', pending_review: '待审核', returned: '已退回', approved: '已通过', rejected: '已驳回' };
const roleNames = { employee: '报销员工', finance: '财务审核', admin: '制度管理员', auditor: '审计查看' };
const actionNames = { 'case.created': '创建单据', 'case.updated': '修改单据', 'case.submitted': '提交并执行规则', 'case.approved': '人工复核通过', 'case.returned': '退回补充', 'case.rejected': '驳回单据', 'user.created': '创建账号', 'user.deactivated': '停用账号', 'user.activated': '启用账号', 'workspace.initialized': '初始化工作空间' };
const fieldNames = { expense_type: '费用类型', department: '部门', cost_center_code: '成本中心', amount: '金额（元）', invoice_date: '发票日期', invoice_code: '发票代码', invoice_number: '发票号码', seller_name: '销售方', buyer_name: '购买方', city: '消费城市', description: '报销事由' };
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const text = value => typeof value === 'object' && value !== null ? JSON.stringify(value, null, 2) : String(value ?? '—');
const money = value => `¥${Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}`;
const dateTime = value => value ? esc(value.replace('T', ' ').slice(0, 19)) : '—';
let toastTimer;

function toast(message, error = false) {
  const element = $('#toast');
  element.textContent = message;
  element.className = `toast${error ? ' error' : ''}`;
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 4500);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const content = await response.text();
  let data = {};
  try { data = content ? JSON.parse(content) : {}; } catch { data = { detail: content }; }
  if (response.status === 401 && path !== '/api/auth/login') showLogin();
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
  return data;
}

function showLogin() {
  state.user = null;
  state.document = null;
  $('#app-shell').hidden = true;
  $('#login-view').hidden = false;
  $('#login-form [name=password]').value = '';
}

async function start() {
  const session = await api('/api/me');
  state.user = session.user;
  state.sampleMode = session.sample_mode;
  $('#login-view').hidden = true;
  $('#app-shell').hidden = false;
  $('#user-name').textContent = state.user.display_name;
  $('#user-role').textContent = session.role_name;
  $('#environment-label').textContent = state.sampleMode ? '本地样例库' : '本地业务库';
  $('#environment-copy').textContent = state.sampleMode ? '当前展示合成样例数据，用于功能验证' : '业务数据保存在当前本地数据库';
  document.querySelectorAll('[data-nav-role]').forEach(element => {
    element.hidden = !element.dataset.navRole.split(' ').includes(state.user.role);
  });
  await navigate('overview');
}

async function navigate(view, id = null) {
  state.view = view;
  document.querySelectorAll('[data-view]').forEach(element => element.classList.toggle('active', element.dataset.view === (view === 'case' || view === 'form' ? 'cases' : view)));
  $('#current-section').textContent = titles[view] || '工作台';
  $('#page-title').textContent = titles[view] || '工作台';
  $('#page').innerHTML = '<div class="loading">正在加载真实业务数据…</div>';
  try {
    if (view === 'overview') await renderOverview();
    if (view === 'cases') await renderCases();
    if (view === 'policies') await renderPolicies();
    if (view === 'tools') await renderTools();
    if (view === 'audit') await renderAudit();
    if (view === 'settings') await renderSettings();
    if (view === 'case') await renderCase(id);
    if (view === 'form') await renderForm(id);
  } catch (error) {
    $('#page').innerHTML = `<div class="card error-box">${esc(error.message)}</div>`;
    toast(error.message, true);
  }
}

function statusBadge(status) {
  return `<span class="status status-${esc(status)}">${esc(statusNames[status] || status)}</span>`;
}

function caseTable(rows) {
  if (!rows.length) return '<div class="empty">当前没有符合条件的单据</div>';
  return `<div class="table-wrap"><table><thead><tr><th>单据编号 / 事由</th><th>报销人</th><th>费用类型</th><th>金额</th><th>状态</th><th>更新时间</th></tr></thead><tbody>${rows.map(row => `<tr>
    <td class="wrap"><button class="link" data-case="${row.id}">${esc(row.case_no)}</button><br><span class="muted">${esc(row.description || '未填写事由')}</span></td>
    <td>${esc(row.owner_name)}</td><td>${esc(row.expense_type)}</td><td><b>${money(row.amount_yuan)}</b></td>
    <td>${statusBadge(row.status)}</td><td>${dateTime(row.updated_at)}</td></tr>`).join('')}</tbody></table></div>`;
}

function bindCaseLinks() {
  document.querySelectorAll('[data-case]').forEach(element => { element.onclick = () => navigate('case', element.dataset.case); });
}

async function renderOverview() {
  const data = await api('/api/overview');
  const roleAction = state.user.role === 'employee' ? '<button class="button primary" id="new-case">新建报销单</button>' : '';
  $('#page').innerHTML = `<div class="section-head"><div><h2>今日业务概览</h2><p class="muted">基于当前本地数据库实时汇总，非外部业务指标。</p></div>${roleAction}</div>
    <div class="grid metrics">
      ${metric('可见单据', data.total, '按当前角色的数据权限统计')}
      ${metric('待审核', data.counts.pending_review, '自动检查之后等待人工处理')}
      ${metric('已通过', data.counts.approved, '人工审核通过，不代表已付款')}
      ${metric('已审批金额', money(data.approved_amount_yuan), '当前可见单据的审批金额')}
    </div>
    <div class="grid two-col"><section class="card"><div class="section-head"><h3>最近单据</h3><button class="button small" id="all-cases">查看全部</button></div>${caseTable(data.recent)}</section>
    <section class="card"><div class="section-head"><h3>成本中心预算</h3><span class="code-label">已审批占用</span></div>
      ${data.budgets.map(row => `<div class="budget-row"><div class="budget-line"><div><b>${esc(row.name)}</b><small>${esc(row.code)} · 可用 ${money(row.available_yuan)}</small></div><span>${(row.usage * 100).toFixed(1)}%</span></div><div class="bar"><i class="${row.usage > .8 ? 'high' : ''}" style="width:${Math.min(100, row.usage * 100)}%"></i></div>
      ${state.user.role === 'admin' ? `<div class="section-actions" style="margin-top:9px"><input class="budget-edit" id="budget-${esc(row.code)}" type="number" min="0" step="0.01" value="${row.budget_yuan}"><button class="button small" data-budget="${esc(row.code)}">更新预算</button></div>` : ''}</div>`).join('')}
      <p class="fine-print">待审核金额不预占预算；审批通过时重新检查可用余额。</p></section></div>`;
  if ($('#new-case')) $('#new-case').onclick = () => navigate('form');
  $('#all-cases').onclick = () => navigate('cases');
  bindCaseLinks();
  document.querySelectorAll('[data-budget]').forEach(button => { button.onclick = async () => {
    try {
      await api(`/api/cost-centers/${button.dataset.budget}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ amount: $(`#budget-${button.dataset.budget}`).value }) });
      toast('预算已更新并记录审计日志'); await renderOverview();
    } catch (error) { toast(error.message, true); }
  }; });
}

function metric(label, value, note) {
  return `<section class="card metric-card"><div class="metric-label">${label}</div><div class="metric-value">${value}</div><div class="metric-note">${note}</div></section>`;
}

async function renderCases() {
  const data = await api(`/api/cases?status=${encodeURIComponent(state.status)}&q=${encodeURIComponent(state.query)}`);
  $('#page').innerHTML = `<div class="section-head"><div><h2>报销单据</h2><p class="muted">草稿 → 提交校验 → 人工审核 → 通过 / 退回 / 驳回</p></div><div class="section-actions"><a class="button" href="/api/export/cases.csv">导出 CSV</a>${state.user.role === 'employee' ? '<button class="button primary" id="new-case">新建报销单</button>' : ''}</div></div>
  <section class="card"><form id="case-filter" class="filter"><label class="search">搜索编号、事由或报销人<input name="q" value="${esc(state.query)}" placeholder="输入关键词"></label><label>流程状态<select name="status"><option value="">全部状态</option>${Object.entries(statusNames).map(([key, value]) => `<option value="${key}" ${state.status === key ? 'selected' : ''}>${value}</option>`).join('')}</select></label><button class="button primary" type="submit">查询</button></form></section>
  <section class="card"><div class="section-head"><h3>单据列表</h3><span class="muted">共 ${data.items.length} 条</span></div>${caseTable(data.items)}</section>`;
  if ($('#new-case')) $('#new-case').onclick = () => navigate('form');
  $('#case-filter').onsubmit = event => { event.preventDefault(); state.query = new FormData(event.target).get('q'); state.status = new FormData(event.target).get('status'); renderCases(); };
  bindCaseLinks();
}

function checkRows(evaluation) {
  if (!evaluation) return '<div class="empty">尚未提交，提交后执行当前发布的制度版本。</div>';
  return evaluation.checks.map(check => `<div class="rule-row ${check.passed ? '' : check.severity}">
    <span class="rule-icon">${check.passed ? '✓' : '!'}</span><div><div class="rule-name">${esc(check.rule_name)}</div><div class="rule-meta">${esc(check.rule_code)} · ${esc(check.severity === 'error' ? '阻断' : '关注')}<br>${esc(check.message)}</div></div>
    <div class="rule-values"><code>实际：${esc(text(check.actual))}</code><code>依据：${esc(text(check.expected))}</code></div></div>`).join('');
}

function historyRows(history) {
  return `<div class="timeline">${history.map(item => `<div class="timeline-item"><b>${esc(actionNames[item.action] || item.action)}</b> <span class="time">${dateTime(item.created_at)}</span><p>${esc(item.actor_name || '系统')} · ${esc(statusNames[item.from_status] || '—')} → ${esc(statusNames[item.to_status] || '—')}</p>${item.detail.comment ? `<p>意见：${esc(item.detail.comment)}</p>` : ''}${item.detail.policy_version ? `<p>制度版本：${esc(item.detail.policy_version)} · 阻断 ${item.detail.errors} · 关注 ${item.detail.warnings}</p>` : ''}</div>`).join('')}</div>`;
}

async function renderCase(id) {
  const data = await api(`/api/cases/${id}`);
  const evaluation = data.evaluation;
  const errors = evaluation ? evaluation.checks.filter(item => !item.passed && item.severity === 'error') : [];
  const warnings = evaluation ? evaluation.checks.filter(item => !item.passed && item.severity === 'warning') : [];
  const editable = state.user.role === 'employee' && ['draft', 'returned'].includes(data.status);
  const reviewable = ['finance', 'admin'].includes(state.user.role) && data.status === 'pending_review';
  $('#page').innerHTML = `<button class="button text" id="back-cases">← 返回单据列表</button>
    <section class="card"><div class="detail-head"><div><p class="eyebrow">EXPENSE DOCUMENT</p><h2>${esc(data.description || data.case_no)}</h2><div class="section-actions">${statusBadge(data.status)}<span class="code-label">${esc(data.case_no)}</span></div></div><div class="detail-amount"><b>${money(data.amount_yuan)}</b><small>${esc(data.owner_name)} · ${esc(data.expense_type)}</small></div></div>
    ${editable ? '<div class="form-footer"><span class="form-note">草稿和退回单可以修改，重新提交会执行最新发布制度。</span><button class="button primary" id="edit-case">修改单据</button></div>' : ''}</section>
    ${evaluation ? `<div class="${errors.length ? 'error-box' : warnings.length ? 'warning-box' : 'info-box'}">本次执行制度 <b>${esc(data.policy.name)} v${esc(data.policy.version)}</b>，共 ${evaluation.checks.length} 项适用检查：${errors.length} 项阻断，${warnings.length} 项关注。引擎：${esc(evaluation.engine_version)}</div>` : ''}
    <div class="grid two-col"><section class="card"><div class="section-head"><h3>规则检查结果</h3><span class="code-label">逐项可解释</span></div>${checkRows(evaluation)}</section>
    <section class="card"><h3>单据信息</h3><div class="info-grid">${['department', 'cost_center_code', 'invoice_number', 'invoice_code', 'invoice_date', 'city', 'seller_name', 'buyer_name'].map(key => `<div class="info-item"><label>${fieldNames[key]}</label><div>${esc(data[key] || '—')}</div></div>`).join('')}</div>
    ${data.document ? `<details><summary>查看导入文本与文件指纹</summary><p class="fine-print">${esc(data.document.filename)} · ${esc(data.document.parser)}<br>SHA-256：${esc(data.document.sha256)}<br>原文件不保存，提取文本保留用于复核。</p><pre>${esc(data.document.extracted_text)}</pre></details>` : '<p class="warning-box">未关联导入单据。</p>'}</section></div>
    <div class="grid two-col"><section class="card"><div class="section-head"><h3>会计建议分录</h3><span class="status status-draft">仅建议 · 未过账</span></div><table class="ledger"><thead><tr><th>方向</th><th>科目</th><th>金额</th></tr></thead><tbody>${data.accounting_suggestion.entries.map(entry => `<tr><td class="${entry.direction}">${entry.direction === 'debit' ? '借' : '贷'}</td><td>${esc(entry.code)} ${esc(entry.name)}</td><td>${money(data.amount_yuan)}</td></tr>`).join('')}</tbody></table><p class="fine-print">这是基于费用类型的映射建议，不是真实会计凭证；无银行、税务或 ERP 过账连接。</p></section>
    <section class="card"><h3>操作时间线</h3>${historyRows(data.history)}</section></div>
    ${reviewable ? `<section class="card"><div class="section-head"><h3>人工审核</h3><span class="code-label">最终决定由人作出</span></div><label>审核意见<textarea id="review-comment" placeholder="退回或驳回必须填写；有警告时通过也必须填写复核原因"></textarea></label><div class="form-footer"><span class="form-note">${errors.length ? '存在阻断问题，不能直接通过。' : warnings.length ? '存在关注事项，通过时必须说明复核依据。' : '当前无阻断问题，可进行人工判断。'}</span><div class="section-actions"><button class="button" data-review="return">退回补充</button><button class="button danger" data-review="reject">驳回</button><button class="button success" data-review="approve">复核通过</button></div></div></section>` : ''}
    <section class="card"><div class="section-head"><h3>工具调用记录</h3><span class="code-label">输入 / 输出可回查</span></div>${data.tools.length ? `<div class="table-wrap"><table><thead><tr><th>工具</th><th>执行人</th><th>耗时</th><th>记录</th></tr></thead><tbody>${data.tools.map(tool => `<tr><td>${esc(tool.tool_name)}</td><td>${esc(tool.actor_name || '规则引擎')}</td><td>${tool.latency_ms} ms</td><td><details><summary>${tool.success ? '执行成功' : '执行失败'}</summary><pre>输入：${esc(text(tool.input))}\n输出：${esc(text(tool.output))}</pre></details></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">尚无工具调用</div>'}</section>`;
  $('#back-cases').onclick = () => navigate('cases');
  if ($('#edit-case')) $('#edit-case').onclick = () => navigate('form', id);
  document.querySelectorAll('[data-review]').forEach(button => { button.onclick = async () => {
    button.disabled = true;
    try {
      await api(`/api/cases/${id}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: button.dataset.review, comment: $('#review-comment').value }) });
      toast('审核结果已写入，规则与审批记录已保存'); await renderCase(id);
    } catch (error) { toast(error.message, true); button.disabled = false; }
  }; });
}

async function renderForm(id = null) {
  const existing = id ? await api(`/api/cases/${id}`) : null;
  const overview = await api('/api/overview');
  state.document = existing?.document ? { id: existing.document.id, filename: existing.document.filename, text: existing.document.extracted_text, parser: existing.document.parser, sha256: existing.document.sha256, needs_confirmation: false } : null;
  state.imagePreview = null;
  const field = (name, type = 'text', wide = false) => `<label class="${wide ? 'wide' : ''}">${fieldNames[name]}${type === 'textarea' ? `<textarea name="${name}" maxlength="2000">${esc(existing?.[name] || '')}</textarea>` : `<input name="${name}" type="${type}" ${type === 'number' ? 'min="0" step="0.01"' : ''} ${name === 'invoice_date' ? `max="${new Date().toISOString().slice(0, 10)}"` : ''} value="${esc(existing?.[name === 'amount' ? 'amount_yuan' : name] || (name === 'buyer_name' ? '示例科技有限公司（虚构）' : ''))}"></label>`}`;
  $('#page').innerHTML = `<div class="section-head"><div><h2>${existing ? `修改 ${esc(existing.case_no)}` : '新建报销单'}</h2><p class="muted">先导入并核对单据，再保存草稿或提交审核。</p></div><button class="button" id="cancel-form">返回</button></div>
  <div class="grid two-col"><section class="card"><h3>1. 导入单据</h3><label>本地文件<input id="invoice-file" type="file" accept=".json,.txt,.csv,.docx,.pdf,.png,.jpg,.jpeg,.bmp,.tif,.tiff"></label><p class="fine-print">支持 JSON / CSV / TXT / DOCX / PDF / 图片，最大 2MB。MinerU JSON 直接读取已有结果，不调用 API。</p><div id="import-result"></div></section>
  <section class="card"><h3>2. 确认字段</h3><form id="case-form"><div class="form-grid">
    <label>费用类型<select name="expense_type">${['住宿费', '交通费', '业务招待费', '办公费'].map(value => `<option ${existing?.expense_type === value ? 'selected' : ''}>${value}</option>`).join('')}</select></label>
    <label>成本中心<select name="cost_center_code"><option value="">请选择</option>${overview.budgets.map(row => `<option value="${esc(row.code)}" ${existing?.cost_center_code === row.code ? 'selected' : ''}>${esc(row.code)} · ${esc(row.name)}</option>`).join('')}</select></label>
    ${field('amount', 'number')}${field('invoice_date', 'date')}${field('invoice_number')}${field('invoice_code')}${field('city')}${field('department')}${field('seller_name')}${field('buyer_name')}${field('description', 'textarea', true)}
    </div><div class="form-footer"><span class="form-note">保存或提交时会记录字段确认。审批不自动触发付款或入账。</span><div class="section-actions"><button class="button" type="submit">保存草稿</button><button class="button primary" type="button" id="save-submit">保存并提交</button></div></div></form></section></div>`;
  $('#cancel-form').onclick = () => existing ? navigate('case', id) : navigate('cases');
  $('#invoice-file').onchange = event => importInvoice(event.target.files[0]);
  $('#case-form').onsubmit = async event => { event.preventDefault(); await saveCase(id, false); };
  $('#save-submit').onclick = async () => { await saveCase(id, true); };
}

async function importInvoice(file) {
  if (!file) return;
  const target = $('#import-result');
  target.innerHTML = '<div class="info-box">正在本地解析，未调用外部接口…</div>';
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
    const result = await api('/api/documents/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ filename: file.name, content_base64: btoa(binary), kind: 'invoice' }) });
    state.document = result;
    Object.entries(result.fields).forEach(([key, value]) => { const input = $(`[name="${key}"]`); if (input) input.value = value; });
    state.imagePreview = file.type.startsWith('image/') ? `data:${file.type};base64,${btoa(binary)}` : null;
    target.innerHTML = `<div class="import-result"><b>${esc(result.filename)}</b><p class="fine-print">${esc(result.parser)} · 文件指纹 ${esc(result.sha256.slice(0, 16))}…</p>${result.needs_confirmation ? '<div class="warning-box">OCR 可能错位或误识别。请对照原图逐项修正右侧字段，保存时记录人工确认。</div>' : '<div class="info-box">请确认右侧提取字段；保存时记录字段确认。</div>'}${state.imagePreview ? `<img class="preview-image" src="${state.imagePreview}" alt="本次选择的本地单据预览">` : ''}<details><summary>查看提取文本</summary><pre>${esc(result.text)}</pre></details></div>`;
    toast('单据已在本地解析，请核对字段');
  } catch (error) { target.innerHTML = `<div class="error-box">${esc(error.message)}</div>`; toast(error.message, true); }
}

async function saveCase(id, submit) {
  const form = new FormData($('#case-form'));
  const payload = Object.fromEntries(form.entries());
  const documentId = state.document?.document_id || state.document?.id || '';
  try {
    if (documentId) await api(`/api/documents/${documentId}/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    payload.document_id = documentId;
    const saved = await api(id ? `/api/cases/${id}` : '/api/cases', { method: id ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const caseId = id || saved.id;
    if (submit) await api(`/api/cases/${caseId}/submit`, { method: 'POST' });
    state.document = null;
    state.imagePreview = null;
    toast(submit ? '已提交，制度检查结果与字段确认均已保存' : '草稿已保存');
    await navigate('case', caseId);
  } catch (error) { toast(error.message, true); }
}

async function renderPolicies() {
  const data = await api('/api/policies');
  const selected = state.policyId || data.items.find(item => item.is_current)?.id || data.items[0]?.id;
  state.policyId = selected;
  const policy = selected ? await api(`/api/policies/${selected}`) : null;
  $('#page').innerHTML = `<div class="section-head"><div><h2>制度规则与版本</h2><p class="muted">草稿经管理员确认后发布；历史单据保留各自使用的版本。</p></div>${state.user.role === 'admin' ? '<button class="button primary" id="import-policy">导入新制度</button>' : ''}</div>
  <div class="policy-list">${data.items.map(item => `<button class="policy-tab ${item.id === selected ? 'active' : ''}" data-policy="${item.id}"><b>${esc(item.name)}</b><small>v${esc(item.version)} · ${item.rule_count} 条 · ${item.is_current ? '当前发布' : item.status === 'draft' ? '草稿' : '历史版本'}</small></button>`).join('')}</div>
  ${policy ? `<section class="card"><div class="section-head"><div><h3>${esc(policy.name)} <span class="code-label">v${esc(policy.version)}</span></h3><p class="muted">来源：${esc(policy.source_name)} · ${policy.status === 'published' ? '已发布' : '草稿待确认'}</p></div>${policy.status === 'draft' && state.user.role === 'admin' ? '<button class="button primary" id="publish-policy">确认并发布</button>' : ''}</div>
    <div class="table-wrap"><table><thead><tr><th>规则编码 / 名称</th><th>适用范围</th><th>字段 / 操作</th><th>参数</th><th>级别</th></tr></thead><tbody>${policy.rules.map(rule => `<tr><td><b>${esc(rule.code)}</b><br>${esc(rule.name)}</td><td>${esc(rule.expense_type === '*' ? '全部费用' : rule.expense_type)}</td><td class="code-label">${esc(rule.field)} / ${esc(rule.operator)}</td><td class="wrap"><code>${esc(text(rule.value))}</code></td><td>${rule.severity === 'error' ? '阻断' : '关注'}</td></tr>`).join('')}</tbody></table></div></section>` : ''}
  <section class="card" id="policy-import-card" hidden><h3>导入制度并预览规则</h3><p class="muted">使用项目提供的结构化文本 DSL 或 JSON。不会把任意自然语言直接变成可执行规则；发布前必须逐项确认。</p><input id="policy-file" type="file" accept=".txt,.md,.json,.docx,.pdf"><div id="policy-preview"></div></section>`;
  document.querySelectorAll('[data-policy]').forEach(button => { button.onclick = () => { state.policyId = button.dataset.policy; renderPolicies(); }; });
  if ($('#publish-policy')) $('#publish-policy').onclick = async () => { try { await api(`/api/policies/${selected}/publish`, { method: 'POST' }); toast('制度版本已发布'); state.policyId = selected; renderPolicies(); } catch (error) { toast(error.message, true); } };
  if ($('#import-policy')) $('#import-policy').onclick = () => { $('#policy-import-card').hidden = false; $('#policy-file').focus(); };
  if ($('#policy-file')) $('#policy-file').onchange = event => importPolicy(event.target.files[0]);
}

async function importPolicy(file) {
  if (!file) return;
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
    const result = await api('/api/documents/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ filename: file.name, content_base64: btoa(binary), kind: 'policy' }) });
    state.policyId = result.policy.id;
    toast('制度已导入为草稿，请在上方逐项确认后发布');
    await renderPolicies();
  } catch (error) { $('#policy-preview').innerHTML = `<div class="error-box">${esc(error.message)}</div>`; toast(error.message, true); }
}

async function renderTools() {
  const data = await api('/api/tools');
  const budgets = (await api('/api/overview')).budgets;
  $('#page').innerHTML = `<div class="section-head"><div><h2>可追踪的本地工具链</h2><p class="muted">每次执行都记录名称、参数、结果、耗时与成功状态。</p></div><span class="code-label">READ-ONLY TOOLS</span></div>
  <div class="tool-grid"><section class="card tool-card"><h3>城市等级与住宿限额</h3><label>城市<input id="tool-city" value="上海"></label><button class="button" id="run-city">查询</button><div class="tool-result" id="city-result">等待执行</div></section>
  <section class="card tool-card"><h3>日期差计算</h3><label>开始日期<input id="tool-start" type="date" value="2026-11-01"></label><label>结束日期<input id="tool-end" type="date" value="2026-11-12"></label><button class="button" id="run-date">计算自然日</button><div class="tool-result" id="date-result">等待执行</div></section>
  <section class="card tool-card"><h3>成本中心预算</h3><label>成本中心<select id="tool-budget">${budgets.map(row => `<option value="${esc(row.code)}">${esc(row.name)}</option>`).join('')}</select></label><button class="button" id="run-budget">查询余额</button><div class="tool-result" id="budget-result">等待执行</div></section></div>
  <section class="card"><h3>已注册工具</h3><div class="tool-list">${data.items.map(tool => `<code title="${esc(tool.description)}">${esc(tool.name)}</code>`).join('')}</div><p class="muted" style="margin-top:18px">本地 MCP 标准输入输出服务支持 <code>initialize</code>、<code>tools/list</code> 和 <code>tools/call</code>。它不是完整的 MCP 平台，不提供远程开放服务。</p><pre>${esc(data.mcp_command)}</pre><p class="fine-print">${esc(data.mcp_scope)}</p></section>`;
  const run = async (name, args, target) => {
    $(target).textContent = '执行中…';
    try { const result = await api('/api/tools/call', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, arguments: args }) }); $(target).textContent = text(result.result); }
    catch (error) { $(target).textContent = error.message; toast(error.message, true); }
  };
  $('#run-city').onclick = () => run('city.lookup', { city: $('#tool-city').value }, '#city-result');
  $('#run-date').onclick = () => run('date.diff', { start: $('#tool-start').value, end: $('#tool-end').value }, '#date-result');
  $('#run-budget').onclick = () => run('budget.status', { cost_center_code: $('#tool-budget').value }, '#budget-result');
}

async function renderAudit() {
  const data = await api('/api/audit');
  $('#page').innerHTML = `<div class="section-head"><div><h2>审计日志</h2><p class="muted">覆盖单据、制度、文件确认和预算变更；当前页面只读。</p></div><a class="button" href="/api/export/audit.csv">导出 CSV</a></div>
  <section class="card"><div class="table-wrap"><table><thead><tr><th>时间</th><th>单据 / 对象</th><th>操作人</th><th>动作</th><th>状态变化</th><th>详情</th></tr></thead><tbody>${data.items.map(row => `<tr><td>${dateTime(row.created_at)}</td><td>${esc(row.case_no || row.detail.policy_id || row.detail.document_id || row.detail.cost_center_code || '系统')}</td><td>${esc(row.actor_name || '系统')}</td><td>${esc(actionNames[row.action] || row.action)}</td><td>${esc(statusNames[row.from_status] || '—')} → ${esc(statusNames[row.to_status] || '—')}</td><td><details><summary>查看记录</summary><pre>${esc(text(row.detail))}</pre></details></td></tr>`).join('')}</tbody></table></div></section>`;
}

async function renderSettings() {
  const [users, overview] = await Promise.all([api('/api/users'), api('/api/overview')]);
  $('#page').innerHTML = `<div class="section-head"><div><h2>账号与基础数据</h2><p class="muted">账号、角色和成本中心由管理员维护，变更写入审计日志。</p></div></div>
  <div class="grid two-col"><section class="card"><div class="section-head"><h3>本地账号</h3><span class="code-label">${users.items.length} 个账号</span></div><div class="table-wrap"><table><thead><tr><th>账号</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>${users.items.map(user => `<tr><td><b>${esc(user.display_name)}</b><br><span class="code-label">${esc(user.username)}</span></td><td>${esc(roleNames[user.role])}</td><td>${user.active ? '启用' : '已停用'}</td><td>${user.id === state.user.id ? '<span class="muted">当前账号</span>' : `<button class="button small" data-active-user="${user.id}" data-active="${user.active}">${user.active ? '停用' : '启用'}</button>`}</td></tr>`).join('')}</tbody></table></div></section>
  <section class="card"><h3>创建账号</h3><form id="user-create" class="form-grid"><label>登录账号<input name="username" required pattern="[A-Za-z][A-Za-z0-9._\-]{2,31}" placeholder="例如 zhangsan"></label><label>显示名称<input name="display_name" required minlength="2" maxlength="60"></label><label>角色<select name="role"><option value="employee">报销员工</option><option value="finance">财务审核</option><option value="auditor">审计查看</option><option value="admin">制度管理员</option></select></label><label>初始密码<input name="password" type="password" autocomplete="new-password" required minlength="12"></label><div class="wide"><p class="fine-print">密码至少 12 位并同时包含字母、数字。请通过安全方式交给使用人。</p><button class="button primary" type="submit">创建账号</button></div></form></section></div>
  <div class="grid two-col"><section class="card"><h3>成本中心</h3><div class="table-wrap"><table><thead><tr><th>编码</th><th>名称</th><th>预算</th></tr></thead><tbody>${overview.budgets.map(row => `<tr><td class="code-label">${esc(row.code)}</td><td>${esc(row.name)}</td><td>${money(row.budget_yuan)}</td></tr>`).join('')}</tbody></table></div><p class="fine-print">预算调整在“工作台”的成本中心卡片中完成。</p></section>
  <section class="card"><h3>新增成本中心</h3><form id="center-create" class="form-grid"><label>编码<input name="code" required minlength="3" maxlength="32" placeholder="例如 CC-LEGAL"></label><label>名称<input name="name" required minlength="2" maxlength="80"></label><label class="wide">初始预算（元）<input name="amount" type="number" min="0" step="0.01" value="0" required></label><div class="wide"><button class="button primary" type="submit">创建成本中心</button></div></form></section></div>`;
  $('#user-create').onsubmit = async event => {
    event.preventDefault();
    const button = event.target.querySelector('button[type=submit]');
    button.disabled = true;
    try {
      await api('/api/users', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(new FormData(event.target).entries())) });
      toast('账号已创建，密码未写入日志'); await renderSettings();
    } catch (error) { toast(error.message, true); button.disabled = false; }
  };
  document.querySelectorAll('[data-active-user]').forEach(button => { button.onclick = async () => {
    try {
      await api(`/api/users/${button.dataset.activeUser}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: button.dataset.active !== 'true' }) });
      toast('账号状态已更新，停用账号的会话已失效'); await renderSettings();
    } catch (error) { toast(error.message, true); }
  }; });
  $('#center-create').onsubmit = async event => {
    event.preventDefault();
    try {
      await api('/api/cost-centers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(new FormData(event.target).entries())) });
      toast('成本中心已创建'); await renderSettings();
    } catch (error) { toast(error.message, true); }
  };
}

$('#login-form').onsubmit = async event => {
  event.preventDefault();
  const button = event.target.querySelector('button[type=submit]');
  button.disabled = true;
  button.textContent = '正在验证…';
  try {
    const form = new FormData(event.target);
    await api('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: form.get('username'), password: form.get('password') }) });
    await start();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = '登录'; }
};
$('#logout').onclick = async () => { await api('/api/auth/logout', { method: 'POST' }); $('#login-form').reset(); showLogin(); };
document.querySelectorAll('[data-view]').forEach(button => { button.onclick = () => navigate(button.dataset.view); });
start().catch(() => showLogin());
