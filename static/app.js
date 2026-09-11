const state = { applicationId: null, interview: null, profiles: [] };

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
const json = async (response) => {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || '请求失败，请稍后重试。');
  return data;
};

async function checkHealth() {
  try {
    const data = await json(await fetch('/api/health'));
    $('health-text').textContent = data.mock_llm ? '本地演示模式' : `模型已连接 · ${data.model}`;
  } catch (_) { $('health-text').textContent = '服务连接异常'; }
}

function showStatus(text, isError = false) { $('form-status').textContent = text; $('form-status').style.color = isError ? '#b3472d' : ''; }
function fileLabel(inputId, defaultText) {
  const input = $(inputId);
  input.addEventListener('change', () => {
    const label = input.closest('.upload-zone');
    label.querySelector('strong').textContent = input.files[0]?.name || defaultText;
  });
}

async function analyze() {
  const resumeText = $('resume-text').value.trim();
  const jdText = $('jd-text').value.trim();
  const resumeFile = $('resume-file').files[0];
  const jdFile = $('jd-file').files[0];
  if (!resumeText && !resumeFile) return showStatus('请先提供简历内容或文件。', true);
  if (!jdText && !jdFile) return showStatus('请先提供职位描述或文件。', true);
  const form = new FormData();
  form.append('resume_text', resumeText); form.append('jd_text', jdText);
  if ($('profile-select').value) form.append('profile_id', $('profile-select').value);
  if (resumeFile) form.append('resume_file', resumeFile); if (jdFile) form.append('jd_file', jdFile);
  const button = $('analyze-btn'); button.disabled = true; showStatus('正在解析并分析……');
  try { renderApplication(await json(await fetch('/api/analyze', { method: 'POST', body: form }))); showStatus('分析完成。'); await loadHistory(); }
  catch (error) { showStatus(error.message, true); }
  finally { button.disabled = false; }
}

function renderApplication(data) {
  state.applicationId = data.id; state.interview = null;
  $('results').classList.remove('hidden'); $('job-title').textContent = data.job.title || '目标职位'; $('analysis-conclusion').textContent = data.analysis.conclusion;
  const sourceLabel = { deepseek: 'DeepSeek 分析', deepseek_repaired: 'DeepSeek 分析 · 自动修复格式', local_fallback: '本地规则分析 · DeepSeek 输出异常已兜底' }[data.analysis.analysis_source] || '分析完成';
  $('analysis-source').textContent = data.analysis.warning ? `${sourceLabel}：${data.analysis.warning}` : sourceLabel;
  $('analysis-source').classList.toggle('warning', data.analysis.analysis_source === 'local_fallback'); $('analysis-source').classList.remove('hidden');
  $('score-value').textContent = data.analysis.overall_score; $('matched-count').textContent = data.analysis.matched_requirements.length; $('gap-count').textContent = data.analysis.missing_requirements.length; $('next-count').textContent = data.analysis.next_steps.length;
  $('strengths').innerHTML = data.analysis.strengths.length ? data.analysis.strengths.map((item) => insightHtml(item, false)).join('') : '<div class="empty-state">暂未识别明显优势。</div>';
  $('gaps').innerHTML = data.analysis.gaps.length ? data.analysis.gaps.map((item) => insightHtml(item, true)).join('') : '<div class="empty-state">暂未发现明显缺口。</div>';
  $('recommendations').innerHTML = data.analysis.recommendations.length ? data.analysis.recommendations.map(recommendationHtml).join('') : '<div class="empty-state">暂无建议。</div>';
  $('retrieved-experiences').innerHTML = data.retrieved_experiences?.length ? data.retrieved_experiences.map(retrievedHtml).join('') : '<div class="empty-state">本次没有检索到可用的独立经历。</div>';
  $('next-steps-list').innerHTML = data.analysis.next_steps.map((item) => `<li>${escapeHtml(item)}</li>`).join('');
  $('rewrite-output').classList.add('hidden'); $('rewrite-output').innerHTML = ''; $('interview-output').innerHTML = '<div class="empty-state">完成分析后，开始一轮 5 题模拟面试。</div>';
  $('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function evidenceHtml(items) { return items?.length ? `<div class="evidence">“${escapeHtml(items[0].text)}”</div>` : ''; }
function insightHtml(item, orange) { return `<div class="insight ${orange ? 'orange' : ''}"><div class="insight-title">${escapeHtml(item.title)}</div><p class="insight-detail">${escapeHtml(item.detail)}</p>${evidenceHtml(item.evidence)}</div>`; }
function recommendationHtml(item) { return `<div class="recommendation"><span class="priority ${item.priority}">${item.priority === 'high' ? '优先' : item.priority === 'medium' ? '建议' : '可选'}</span><div><strong>${escapeHtml(item.action)}</strong><p>${escapeHtml(item.rationale)}</p>${item.needs_confirmation ? '<div class="confirm">需要你确认真实经历后再使用</div>' : evidenceHtml(item.evidence)}</div></div>`; }
function retrievedHtml(item) { return `<div class="retrieved-item"><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.text)}</p><div class="retrieved-meta"><span>${escapeHtml(item.kind)}</span><span>相关度 ${(item.score * 100).toFixed(1)}%</span></div></div>`; }

async function rewrite() {
  if (!state.applicationId) return;
  const button = $('rewrite-btn'); button.disabled = true; button.textContent = '正在生成……';
  try {
    const data = await json(await fetch('/api/rewrite', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ application_id: state.applicationId }) }));
    $('rewrite-output').classList.remove('hidden'); $('rewrite-output').innerHTML = `<p class="rewrite-summary">${escapeHtml(data.summary)}</p>${data.bullets.map((item) => `<div class="rewrite-item"><small>原始经历：${escapeHtml(item.original)}</small><p>${escapeHtml(item.rewritten)}</p>${item.needs_confirmation ? '<div class="needs-confirmation">需要补充或确认真实数据</div>' : ''}</div>`).join('')}`;
  } catch (error) { $('rewrite-output').classList.remove('hidden'); $('rewrite-output').innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`; }
  finally { button.disabled = false; button.innerHTML = '生成定制化简历 <span>↗</span>'; }
}

async function startInterview() {
  if (!state.applicationId) return;
  const button = $('interview-start-btn'); button.disabled = true;
  try { state.interview = await json(await fetch('/api/interview/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ application_id: state.applicationId, count: 5 }) })); renderQuestion(); }
  catch (error) { $('interview-output').innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`; }
  finally { button.disabled = false; }
}

function renderQuestion(feedback = '') {
  const interview = state.interview; const current = interview?.current_question;
  if (!current) { $('interview-output').innerHTML = '<div class="empty-state">本轮面试已完成。请根据反馈继续准备。</div>'; return; }
  const answered = interview.answered || 0;
  $('interview-output').innerHTML = `${feedback}<div class="question-box"><div class="question-count">问题 ${answered + 1} / ${interview.questions.length} · ${escapeHtml(current.focus)}</div><div class="question-text">${escapeHtml(current.question)}</div><textarea id="answer-text" placeholder="用具体背景、行动和结果组织你的回答……"></textarea><div class="answer-row"><button id="answer-btn" class="secondary-button">提交回答 <span>→</span></button></div></div>`;
  $('answer-btn').addEventListener('click', answerInterview);
}

async function answerInterview() {
  const answer = $('answer-text').value.trim(); if (!answer) return;
  const button = $('answer-btn'); button.disabled = true;
  try {
    const current = state.interview.current_question; const data = await json(await fetch(`/api/interview/${state.interview.session_id}/answer`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question_id: current.id, answer }) }));
    state.interview.answered = (state.interview.answered || 0) + 1; state.interview.current_question = data.next_question;
    const feedback = `<div class="feedback"><strong>本题评分 <span class="score">${data.score}/10</span></strong><p>${escapeHtml(data.strengths.join(' '))}</p><p>${escapeHtml(data.improvements.join(' '))}</p>${data.follow_up ? `<p>追问：${escapeHtml(data.follow_up)}</p>` : ''}</div>`;
    if (data.completed) $('interview-output').innerHTML = `${feedback}<div class="empty-state">本轮面试已完成。</div>`; else renderQuestion(feedback);
  } catch (error) { $('interview-output').insertAdjacentHTML('afterbegin', `<div class="empty-state">${escapeHtml(error.message)}</div>`); button.disabled = false; }
}

async function loadHistory() {
  try {
    const items = await json(await fetch('/api/history'));
    $('history-list').innerHTML = items.length ? items.map((item) => `<div class="history-item"><div><strong>${escapeHtml(item.title)}</strong><small>${new Date(item.created_at).toLocaleString()} · ${escapeHtml(item.conclusion)}</small></div><span class="history-score">${item.score}</span></div>`).join('') : '<div class="empty-state">还没有分析记录。</div>';
  } catch (_) { $('history-list').innerHTML = '<div class="empty-state">历史记录暂时不可用。</div>'; }
}

async function loadProfiles() {
  try {
    state.profiles = await json(await fetch('/api/profiles'));
    const select = $('profile-select');
    const selected = select.value;
    select.innerHTML = '<option value="">分析时自动创建</option>' + state.profiles.map((profile) => `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.name)} · ${profile.experience_count} 条经历</option>`).join('');
    if (state.profiles.some((profile) => profile.id === selected)) select.value = selected;
  } catch (_) { /* profiles are optional for a first local run */ }
}

async function createProfile() {
  const name = window.prompt('给这个候选人档案起个名字：', '我的求职档案');
  if (!name?.trim()) return;
  try {
    const profile = await json(await fetch('/api/profiles', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim() }) }));
    await loadProfiles(); $('profile-select').value = profile.id;
  } catch (error) { showStatus(error.message, true); }
}

async function addExperience() {
  const profileId = $('profile-select').value;
  if (!profileId) return showStatus('请先选择一个候选人档案。', true);
  const title = window.prompt('经历标题，例如：数据分析平台');
  if (!title?.trim()) return;
  const text = window.prompt('经历内容：请写清楚你做了什么、使用了什么技术、结果如何。');
  if (!text?.trim()) return;
  try {
    await json(await fetch(`/api/profiles/${profileId}/experiences`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'project', title: title.trim(), text: text.trim() }) }));
    showStatus('经历已保存并建立向量索引。'); await loadProfiles(); $('profile-select').value = profileId;
  } catch (error) { showStatus(error.message, true); }
}

fileLabel('resume-file', '上传文件'); fileLabel('jd-file', '上传文件');
$('analyze-btn').addEventListener('click', analyze); $('rewrite-btn').addEventListener('click', rewrite); $('interview-start-btn').addEventListener('click', startInterview); $('refresh-history').addEventListener('click', loadHistory);
$('new-profile-btn').addEventListener('click', createProfile);
$('add-experience-btn').addEventListener('click', addExperience);
checkHealth(); loadHistory(); loadProfiles();
