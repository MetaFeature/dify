import { AdminApi, AdminApiError } from './admin-api.js'
import { authFailureView, campusDay, parseRosterCsv, rosterPayload, slotEnded } from './admin-domain.js'

const PAGE_SIZE = 50

const api = new AdminApi()
const elements = {
  authView: requiredElement('#auth-view', HTMLElement),
  authMessage: requiredElement('#auth-message', HTMLElement),
  authAction: requiredElement('#auth-action', HTMLButtonElement),
  adminView: requiredElement('#admin-view', HTMLElement),
  message: requiredElement('#admin-message', HTMLElement),
  tabs: [...document.querySelectorAll('.tab')].filter(tab => tab instanceof HTMLButtonElement),
  slotDay: requiredElement('#slot-day', HTMLInputElement),
  slotTable: requiredElement('#slot-table', HTMLElement),
  studentTable: requiredElement('#student-table', HTMLElement),
  studentDetail: requiredElement('#student-detail', HTMLElement),
  studentCreateForm: requiredElement('#student-create-form', HTMLFormElement),
  studentsPrev: requiredElement('#students-prev', HTMLButtonElement),
  studentsNext: requiredElement('#students-next', HTMLButtonElement),
  studentsPage: requiredElement('#students-page', HTMLElement),
  rosterFile: requiredElement('#roster-file', HTMLInputElement),
  rosterText: requiredElement('#roster-text', HTMLTextAreaElement),
  rosterPreview: requiredElement('#roster-preview', HTMLButtonElement),
  rosterConfirm: requiredElement('#roster-confirm', HTMLButtonElement),
  rosterReport: requiredElement('#roster-report', HTMLElement),
  adminAddForm: requiredElement('#admin-add-form', HTMLFormElement),
  adminTable: requiredElement('#admin-table', HTMLElement),
  manualTrack: requiredElement('#manual-track', HTMLSelectElement),
  manualForm: requiredElement('#manual-form', HTMLFormElement),
  manualChapterId: requiredElement('#manual-chapter-id', HTMLInputElement),
  manualTitle: requiredElement('#manual-title', HTMLInputElement),
  manualFile: requiredElement('#manual-file', HTMLInputElement),
  manualHtml: requiredElement('#manual-html', HTMLTextAreaElement),
  manualCancel: requiredElement('#manual-cancel', HTMLButtonElement),
  manualReport: requiredElement('#manual-report', HTMLElement),
  manualTable: requiredElement('#manual-table', HTMLElement),
}

let studentOffset = 0
/** @type {import('./admin-domain.js').RosterRow[] | null} */
let pendingRoster = null

for (const tab of elements.tabs) {
  tab.addEventListener('click', () => {
    for (const other of elements.tabs)
      other.classList.toggle('active', other === tab)
    for (const panel of document.querySelectorAll('.tab-panel')) {
      if (panel instanceof HTMLElement)
        panel.hidden = panel.id !== `tab-${tab.dataset.tab}`
    }
  })
}

elements.slotDay.addEventListener('change', loadSlots)

elements.slotTable.addEventListener('click', async (event) => {
  const button = event.target instanceof Element ? event.target.closest('[data-slot-starts]') : null
  if (!(button instanceof HTMLButtonElement))
    return
  const startsAt = button.getAttribute('data-slot-starts') || ''
  const input = elements.slotTable.querySelector(`input[data-capacity-for="${CSS.escape(startsAt)}"]`)
  if (!(input instanceof HTMLInputElement))
    return
  const capacity = Number.parseInt(input.value, 10)
  if (!Number.isInteger(capacity) || capacity < 0) {
    showMessage('容量必须是不小于 0 的整数。', true)
    return
  }
  button.disabled = true
  try {
    const change = await api.setSlotCapacity(startsAt, capacity)
    const over = change.confirmed > change.capacity
    showMessage(
      `容量已从 ${change.previous_capacity} 调整为 ${change.capacity}。`
      + (over ? ` 注意：当前已确认 ${change.confirmed} 人超过新容量，现有预约保留，仅停止接受新预约。` : ''),
      false,
    )
    await loadSlots()
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    button.disabled = false
  }
})

elements.studentCreateForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  const form = new FormData(elements.studentCreateForm)
  const cohort = String(form.get('cohort') || '').trim()
  try {
    await api.createStudent({
      student_number: String(form.get('studentNumber')).trim(),
      display_name: String(form.get('displayName')).trim(),
      ...(cohort ? { cohort } : {}),
      password: String(form.get('password')),
    })
    elements.studentCreateForm.reset()
    showMessage('学生已添加。', false)
    await loadStudents()
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
})

elements.studentsPrev.addEventListener('click', () => {
  studentOffset = Math.max(0, studentOffset - PAGE_SIZE)
  loadStudents()
})
elements.studentsNext.addEventListener('click', () => {
  studentOffset += PAGE_SIZE
  loadStudents()
})

elements.studentTable.addEventListener('click', async (event) => {
  const button = event.target instanceof Element ? event.target.closest('[data-action]') : null
  if (!(button instanceof HTMLButtonElement))
    return
  const studentNumber = button.getAttribute('data-student') || ''
  const action = button.getAttribute('data-action')
  button.disabled = true
  try {
    if (action === 'suspend' || action === 'activate') {
      const suspend = action === 'suspend'
      if (suspend && !window.confirm(`确定停用学生 ${studentNumber} 吗？停用后无法登录，其工作区与数据保留。`))
        return
      await api.setStudentStatus(studentNumber, suspend ? 'suspended' : 'active')
      showMessage(suspend ? '学生已停用。' : '学生已启用。', false)
      await loadStudents()
    }
    else if (action === 'reset-password') {
      const password = window.prompt(`输入学生 ${studentNumber} 的新密码：`)
      if (!password)
        return
      await api.resetStudentPassword(studentNumber, password)
      showMessage('密码已重置，该学生的登录会话已全部退出。', false)
      await loadStudents()
    }
    else if (action === 'adjust-allowance') {
      const delta = window.prompt(`输入学生 ${studentNumber} 的额度调整金额（美元，可为负数）：`)
      if (!delta)
        return
      const reason = window.prompt('输入调整原因：')
      if (!reason)
        return
      await api.adjustAllowance(studentNumber, {
        delta_usd: delta.trim(),
        reason: reason.trim(),
        request_id: crypto.randomUUID(),
      })
      showMessage('额度已调整。', false)
    }
    else if (action === 'detail') {
      const detail = await api.studentDetail(studentNumber)
      renderStudentDetail(detail)
    }
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    button.disabled = false
  }
})

elements.rosterFile.addEventListener('change', async () => {
  const file = elements.rosterFile.files && elements.rosterFile.files[0]
  if (file)
    elements.rosterText.value = await file.text()
})

elements.rosterPreview.addEventListener('click', async () => {
  pendingRoster = null
  elements.rosterConfirm.hidden = true
  const { rows, errors } = parseRosterCsv(elements.rosterText.value)
  if (errors.length || !rows.length) {
    renderRosterErrors(errors.length ? errors : ['没有可导入的数据行'])
    return
  }
  elements.rosterPreview.disabled = true
  try {
    const preview = await api.previewRoster(rosterPayload(rows))
    pendingRoster = rows
    elements.rosterReport.innerHTML = ''
    const summary = document.createElement('p')
    summary.className = 'preview-summary'
    summary.innerHTML = `将新增 <b>${preview.created}</b> 名学生，更新 <b>${preview.updated}</b> 名学生，`
      + `其中 <b>${preview.password_resets}</b> 名学生的密码将被重置（其登录会话将全部退出）。请确认后导入。`
    elements.rosterReport.append(summary)
    elements.rosterConfirm.hidden = false
  }
  catch (error) {
    renderRosterErrors([messageFor(error)])
  }
  finally {
    elements.rosterPreview.disabled = false
  }
})

elements.rosterConfirm.addEventListener('click', async () => {
  if (!pendingRoster)
    return
  elements.rosterConfirm.disabled = true
  try {
    const outcome = await api.syncRoster(rosterPayload(pendingRoster))
    elements.rosterReport.innerHTML = ''
    showMessage(
      `导入完成：新增 ${outcome.created} 人，更新 ${outcome.updated} 人，重置密码 ${outcome.password_resets} 人。`,
      false,
    )
    pendingRoster = null
    elements.rosterConfirm.hidden = true
    await loadStudents()
  }
  catch (error) {
    renderRosterErrors([messageFor(error)])
  }
  finally {
    elements.rosterConfirm.disabled = false
  }
})

elements.adminAddForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  const form = new FormData(elements.adminAddForm)
  try {
    await api.addAdministrator(String(form.get('accountId')).trim())
    elements.adminAddForm.reset()
    showMessage('管理员已授权。', false)
    await loadAdministrators()
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
})

elements.adminTable.addEventListener('click', async (event) => {
  const button = event.target instanceof Element ? event.target.closest('[data-revoke]') : null
  if (!(button instanceof HTMLButtonElement))
    return
  const accountId = button.getAttribute('data-revoke') || ''
  if (!window.confirm('确定撤销该管理员的权限吗？'))
    return
  button.disabled = true
  try {
    await api.removeAdministrator(accountId)
    showMessage('管理员权限已撤销。', false)
    await loadAdministrators()
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    button.disabled = false
  }
})

async function loadSlots() {
  elements.slotTable.textContent = '正在读取时段…'
  try {
    const { data, server_now: serverNow } = await api.listSlots(elements.slotDay.value)
    const now = new Date(serverNow)
    const table = document.createElement('table')
    table.innerHTML = '<thead><tr><th>时段</th><th>容量</th><th>已确认</th><th>候补</th><th>状态</th><th></th></tr></thead>'
    const body = document.createElement('tbody')
    for (const slot of data) {
      const ended = slotEnded(slot, now)
      const running = !ended && new Date(slot.starts_at).getTime() <= now.getTime()
      const row = document.createElement('tr')
      const state = ended ? '<span class="badge ended">已结束</span>' : running ? '<span class="badge running">进行中</span>' : ''
      const closed = slot.capacity === 0 ? ' <span class="badge suspended">已关闭</span>' : ''
      row.innerHTML = `
        <td>${formatTime(slot.starts_at)} – ${formatTime(slot.ends_at)}</td>
        <td><input class="capacity-input" type="number" min="0" step="1" data-capacity-for="${slot.starts_at}" ${ended ? 'disabled' : ''}></td>
        <td>${slot.confirmed}</td>
        <td>${slot.waitlisted}</td>
        <td>${state}${closed}</td>
        <td><button class="secondary compact" type="button" data-slot-starts="${slot.starts_at}" ${ended ? 'disabled' : ''}>保存</button></td>`
      const input = row.querySelector('input')
      if (input instanceof HTMLInputElement)
        input.value = String(slot.capacity)
      body.append(row)
    }
    table.append(body)
    elements.slotTable.replaceChildren(table)
  }
  catch (error) {
    elements.slotTable.textContent = messageFor(error)
  }
}

async function loadStudents() {
  elements.studentTable.textContent = '正在读取学生列表…'
  elements.studentDetail.hidden = true
  try {
    const { data } = await api.listStudents(PAGE_SIZE, studentOffset)
    elements.studentsPage.textContent = `第 ${Math.floor(studentOffset / PAGE_SIZE) + 1} 页`
    elements.studentsPrev.disabled = studentOffset === 0
    elements.studentsNext.disabled = data.length < PAGE_SIZE
    if (!data.length) {
      elements.studentTable.textContent = '本页没有学生。'
      return
    }
    const table = document.createElement('table')
    table.innerHTML = '<thead><tr><th>学号</th><th>姓名</th><th>班级</th><th>状态</th><th>凭据</th><th>操作</th></tr></thead>'
    const body = document.createElement('tbody')
    for (const student of data) {
      const row = document.createElement('tr')
      const active = student.status === 'active'
      const credentialBadges = [
        student.has_credential ? '<span class="badge credential">平台密码</span>' : '',
        student.virtual_identity ? '<span class="badge virtual">虚拟</span>' : '',
      ].join(' ')
      row.innerHTML = `
        <td>${escapeHtml(student.student_number)}</td>
        <td>${escapeHtml(student.display_name)}</td>
        <td>${escapeHtml(student.cohort || '—')}</td>
        <td><span class="badge ${active ? 'active' : 'suspended'}">${active ? '启用' : '已停用'}</span></td>
        <td>${credentialBadges || '—'}</td>
        <td>
          <button class="secondary compact" type="button" data-action="${active ? 'suspend' : 'activate'}" data-student="${escapeHtml(student.student_number)}">${active ? '停用' : '启用'}</button>
          <button class="secondary compact" type="button" data-action="reset-password" data-student="${escapeHtml(student.student_number)}">重置密码</button>
          <button class="secondary compact" type="button" data-action="adjust-allowance" data-student="${escapeHtml(student.student_number)}">额度调整</button>
          <button class="secondary compact" type="button" data-action="detail" data-student="${escapeHtml(student.student_number)}">详情</button>
        </td>`
      body.append(row)
    }
    table.append(body)
    elements.studentTable.replaceChildren(table)
  }
  catch (error) {
    elements.studentTable.textContent = messageFor(error)
  }
}

/** @param {Record<string, unknown>} detail */
function renderStudentDetail(detail) {
  const allowance = /** @type {{ remaining_usd?: string, used_usd?: string, total_usd?: string } | null} */ (detail.allowance)
  elements.studentDetail.innerHTML = `
    <b>${escapeHtml(String(detail.display_name))}</b>（${escapeHtml(String(detail.student_number))}）<br>
    工作区：${escapeHtml(String(detail.workspace_id || '未开通'))}<br>
    额度：${allowance ? `剩余 $${allowance.remaining_usd} / 总额 $${allowance.total_usd}（已用 $${allowance.used_usd}）` : '未开通'}`
  elements.studentDetail.hidden = false
}

async function loadAdministrators() {
  elements.adminTable.textContent = '正在读取管理员列表…'
  try {
    const { data } = await api.listAdministrators()
    const table = document.createElement('table')
    table.innerHTML = '<thead><tr><th>姓名</th><th>账号 ID</th><th></th></tr></thead>'
    const body = document.createElement('tbody')
    for (const admin of data) {
      const row = document.createElement('tr')
      row.innerHTML = `
        <td>${escapeHtml(admin.display_name)}</td>
        <td>${escapeHtml(admin.account_id)}</td>
        <td><button class="secondary compact danger" type="button" data-revoke="${escapeHtml(admin.account_id)}">撤销</button></td>`
      body.append(row)
    }
    table.append(body)
    elements.adminTable.replaceChildren(table)
  }
  catch (error) {
    elements.adminTable.textContent = messageFor(error)
  }
}

/** @param {string} value */
function escapeHtml(value) {
  return value.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char] || char)
}

/** @param {string} text @param {boolean} error */
function showMessage(text, error) {
  elements.message.textContent = text
  elements.message.classList.toggle('error', error)
  elements.message.hidden = false
}

/** @param {string[]} errors */
function renderRosterErrors(errors) {
  const list = document.createElement('ul')
  for (const item of errors) {
    const entry = document.createElement('li')
    entry.textContent = item
    list.append(entry)
  }
  elements.rosterReport.replaceChildren(list)
}

/** @param {unknown} error */
function messageFor(error) {
  return error instanceof AdminApiError ? error.message : '操作失败，请稍后重试。'
}

/** @param {string} value */
function formatTime(value) {
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' }).format(new Date(value))
}

/**
 * @template {Element} T
 * @param {string} selector
 * @param {{ new(): T }} type
 * @returns {T}
 */
function requiredElement(selector, type) {
  const element = document.querySelector(selector)
  if (!(element instanceof type))
    throw new Error(`Missing required admin element: ${selector}`)
  return element
}

/** @param {number} status */
async function showAuthRecovery(status) {
  let accountLabel = null
  if (status === 403) {
    try {
      const account = await api.currentAccount()
      accountLabel = account.name || account.email || null
    }
    catch {
      // Naming the account is a convenience; recovery must work without it.
    }
  }
  const view = authFailureView(status, accountLabel)
  elements.authMessage.textContent = view.message
  elements.authAction.textContent = view.actionLabel
  elements.authAction.onclick = async () => {
    elements.authAction.disabled = true
    if (view.action === 'reload') {
      window.location.reload()
      return
    }
    if (view.action === 'logout-then-signin') {
      // Dify bounces an authenticated browser from /signin back to this root,
      // so the session must end before sending them to sign in again.
      try {
        await api.logout()
      }
      catch {
        // Cookies may already be gone; sign-in is still the right next step.
      }
    }
    window.location.assign('/signin')
  }
  elements.authView.hidden = false
}

async function bootstrap() {
  elements.slotDay.value = campusDay(new Date())
  try {
    await api.listAdministrators()
  }
  catch (error) {
    await showAuthRecovery(error instanceof AdminApiError ? error.status : 0)
    return
  }
  elements.adminView.hidden = false
  await Promise.all([loadSlots(), loadStudents(), loadAdministrators(), loadManualChapters()])
}

bootstrap()

elements.manualTrack.addEventListener('change', () => {
  resetManualForm()
  void loadManualChapters()
})

elements.manualFile.addEventListener('change', async () => {
  const file = elements.manualFile.files && elements.manualFile.files[0]
  if (!file)
    return
  elements.manualHtml.value = await file.text()
  if (!elements.manualTitle.value)
    elements.manualTitle.value = file.name.replace(/\.html?$/i, '')
})

elements.manualCancel.addEventListener('click', resetManualForm)

elements.manualForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  const payload = { title: elements.manualTitle.value.trim(), body_html: elements.manualHtml.value }
  const chapterId = elements.manualChapterId.value
  elements.manualForm.querySelectorAll('button').forEach((button) => { button.disabled = true })
  try {
    const saved = chapterId
      ? await api.updateManualChapter(chapterId, payload)
      : await api.createManualChapter(elements.manualTrack.value, payload)
    renderSanitizeReport(saved.removed)
    resetManualForm()
    showMessage(chapterId ? '章节已更新。' : '章节已添加，当前是草稿。', false)
    await loadManualChapters()
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    elements.manualForm.querySelectorAll('button').forEach((button) => { button.disabled = false })
  }
})

elements.manualTable.addEventListener('click', async (event) => {
  const button = event.target instanceof Element ? event.target.closest('button[data-manual-action]') : null
  if (!(button instanceof HTMLButtonElement))
    return
  const chapterId = button.getAttribute('data-chapter') || ''
  const action = button.getAttribute('data-manual-action')
  button.disabled = true
  try {
    if (action === 'publish' || action === 'unpublish') {
      await api.setManualChapterStatus(chapterId, action === 'publish' ? 'published' : 'draft')
      showMessage(action === 'publish' ? '章节已发布，学生现在能看到。' : '章节已撤回为草稿。', false)
    }
    else if (action === 'up' || action === 'down') {
      await api.moveManualChapter(chapterId, Number(button.getAttribute('data-position')))
    }
    else if (action === 'delete') {
      if (!window.confirm('确定删除这个章节吗？删除后无法恢复。'))
        return
      await api.deleteManualChapter(chapterId)
      showMessage('章节已删除。', false)
    }
    else if (action === 'edit') {
      await startEditingChapter(chapterId)
      return
    }
    await loadManualChapters()
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    button.disabled = false
  }
})

/**
 * Load one chapter into the form so it can be replaced.
 *
 * The list omits body_html, so this reads the chapter on its own; opening the
 * form empty would let a save wipe the chapter.
 *
 * @param {string} chapterId
 */
async function startEditingChapter(chapterId) {
  const chapter = await api.manualChapter(chapterId)
  elements.manualChapterId.value = chapter.id
  elements.manualTitle.value = chapter.title
  elements.manualHtml.value = chapter.body_html
  elements.manualCancel.hidden = false
  elements.manualTitle.focus()
}

function resetManualForm() {
  elements.manualForm.reset()
  elements.manualChapterId.value = ''
  elements.manualCancel.hidden = true
}

/**
 * Say what sanitizing removed. Silence here is what makes an upload look
 * broken: the administrator's formatting is gone and nothing said so.
 *
 * @param {Record<string, number> | undefined} removed
 */
function renderSanitizeReport(removed) {
  const entries = Object.entries(removed || {})
  if (!entries.length) {
    elements.manualReport.textContent = '清洗后没有内容被移除。'
    return
  }
  /** @type {Record<string, string>} */
  const labels = {
    'script': '脚本',
    'style': '样式表',
    'style attribute': '内联样式',
    'event handler': '事件处理器',
    'unsafe link': '不安全链接',
    'remote image': '外链图片',
    'iframe': '内嵌框架',
    'form': '表单',
    'input': '输入控件',
    'object': '嵌入对象',
    'noscript': 'noscript',
    'template': 'template',
    'link': 'link',
    'base': 'base',
    'svg': 'svg',
  }
  const summary = document.createElement('p')
  summary.className = 'muted'
  summary.textContent = `已移除：${entries
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => `${labels[name] || name} ×${count}`)
    .join('、')}。手册外观由平台统一控制。`
  elements.manualReport.replaceChildren(summary)
}

async function loadManualChapters() {
  elements.manualTable.textContent = '正在读取章节…'
  try {
    const { data } = await api.listManualChapters(elements.manualTrack.value)
    if (!data.length) {
      elements.manualTable.textContent = '这个实验类别还没有章节。'
      return
    }
    const table = document.createElement('table')
    table.innerHTML = '<thead><tr><th>#</th><th>标题</th><th>状态</th><th>操作</th></tr></thead>'
    const body = document.createElement('tbody')
    for (const [index, chapter] of data.entries()) {
      const row = document.createElement('tr')
      const published = chapter.status === 'published'
      row.innerHTML = `
        <td>${chapter.position}</td>
        <td>${escapeHtml(chapter.title)}</td>
        <td><span class="badge ${published ? 'active' : 'suspended'}">${published ? '已发布' : '草稿'}</span></td>
        <td>
          <button class="secondary compact" type="button" data-manual-action="edit" data-chapter="${escapeHtml(chapter.id)}">编辑</button>
          <button class="secondary compact" type="button" data-manual-action="${published ? 'unpublish' : 'publish'}" data-chapter="${escapeHtml(chapter.id)}">${published ? '撤回' : '发布'}</button>
          <button class="secondary compact" type="button" data-manual-action="up" data-chapter="${escapeHtml(chapter.id)}" data-position="${chapter.position - 1}" ${index === 0 ? 'disabled' : ''}>上移</button>
          <button class="secondary compact" type="button" data-manual-action="down" data-chapter="${escapeHtml(chapter.id)}" data-position="${chapter.position + 1}" ${index === data.length - 1 ? 'disabled' : ''}>下移</button>
          <button class="secondary compact danger" type="button" data-manual-action="delete" data-chapter="${escapeHtml(chapter.id)}">删除</button>
        </td>`
      body.append(row)
    }
    table.append(body)
    elements.manualTable.replaceChildren(table)
  }
  catch (error) {
    elements.manualTable.textContent = messageFor(error)
  }
}
