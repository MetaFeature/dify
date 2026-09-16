import { AdminApi, AdminApiError } from './admin-api.js'
import {
  allowanceAmount,
  authFailureView,
  campusDay,
  defaultAllowancePayload,
  knowledgeLimitPayload,
  parseRosterCsv,
  rosterPayload,
  slotCapacityPayload,
  slotEnded,
} from './admin-domain.js'

const PAGE_SIZE = 50

const api = new AdminApi()
const elements = {
  authView: requiredElement('#auth-view', HTMLElement),
  authMessage: requiredElement('#auth-message', HTMLElement),
  authAction: requiredElement('#auth-action', HTMLButtonElement),
  logout: requiredElement('#admin-logout', HTMLButtonElement),
  adminView: requiredElement('#admin-view', HTMLElement),
  message: requiredElement('#admin-message', HTMLElement),
  tabs: [...document.querySelectorAll('.tab')].filter(tab => tab instanceof HTMLButtonElement),
  slotDay: requiredElement('#slot-day', HTMLInputElement),
  slotTable: requiredElement('#slot-table', HTMLElement),
  loginPageForm: requiredElement('#login-page-form', HTMLFormElement),
  loginPageFile: requiredElement('#login-page-file', HTMLInputElement),
  loginPageSave: requiredElement('#login-page-save', HTMLButtonElement),
  loginPageRestore: requiredElement('#login-page-restore', HTMLButtonElement),
  loginPageStatus: requiredElement('#login-page-status', HTMLElement),
  loginPageReport: requiredElement('#login-page-report', HTMLElement),
  loginPageList: requiredElement('#login-page-list', HTMLElement),
  knowledgeLimitForm: requiredElement('#knowledge-limit-form', HTMLFormElement),
  knowledgeDatasetsInput: requiredElement('#knowledge-datasets-input', HTMLInputElement),
  knowledgeDocumentsInput: requiredElement('#knowledge-documents-input', HTMLInputElement),
  knowledgeLimitSave: requiredElement('#knowledge-limit-save', HTMLButtonElement),
  knowledgeLimitRestore: requiredElement('#knowledge-limit-restore', HTMLButtonElement),
  knowledgeLimitStatus: requiredElement('#knowledge-limit-status', HTMLElement),
  defaultAllowanceForm: requiredElement('#default-allowance-form', HTMLFormElement),
  defaultAllowanceInput: requiredElement('#default-allowance-input', HTMLInputElement),
  defaultAllowanceSave: requiredElement('#default-allowance-save', HTMLButtonElement),
  defaultAllowanceRestore: requiredElement('#default-allowance-restore', HTMLButtonElement),
  defaultAllowanceStatus: requiredElement('#default-allowance-status', HTMLElement),
  slotCapacityForm: requiredElement('#slot-capacity-form', HTMLFormElement),
  slotCapacityInput: requiredElement('#slot-capacity-input', HTMLInputElement),
  slotCapacitySave: requiredElement('#slot-capacity-save', HTMLButtonElement),
  slotCapacityRestore: requiredElement('#slot-capacity-restore', HTMLButtonElement),
  slotCapacityStatus: requiredElement('#slot-capacity-status', HTMLElement),
  studentTable: requiredElement('#student-table', HTMLElement),
  studentSearchForm: requiredElement('#student-search-form', HTMLFormElement),
  studentSearchInput: requiredElement('#student-search-input', HTMLInputElement),
  studentSearchClear: requiredElement('#student-search-clear', HTMLButtonElement),
  studentsShowDeleted: requiredElement('#students-show-deleted', HTMLInputElement),
  studentsPurge: requiredElement('#students-purge', HTMLButtonElement),
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
  presentationForm: requiredElement('#presentation-form', HTMLFormElement),
  presentationLoginHtml: requiredElement('#presentation-login-html', HTMLTextAreaElement),
  presentationPublish: requiredElement('#presentation-publish', HTMLButtonElement),
  presentationRestore: requiredElement('#presentation-restore', HTMLButtonElement),
  manualTrack: requiredElement('#manual-track', HTMLSelectElement),
  manualForm: requiredElement('#manual-form', HTMLFormElement),
  manualChapterId: requiredElement('#manual-chapter-id', HTMLInputElement),
  manualFile: requiredElement('#manual-file', HTMLInputElement),
  manualCancel: requiredElement('#manual-cancel', HTMLButtonElement),
  manualReport: requiredElement('#manual-report', HTMLElement),
  manualTable: requiredElement('#manual-table', HTMLElement),
  reportStatus: requiredElement('#report-status', HTMLElement),
  reportButtons: [...document.querySelectorAll('[data-report-page], [data-report-xlsx]')]
    .filter(button => button instanceof HTMLButtonElement),
}

let studentOffset = 0
// The list is server-paged, so the filter has to travel to the API: filtering
// in the browser would only ever search the fifty rows already on screen.
let studentKeyword = ''
// The roster hides soft-deleted students unless the operator opts in.
let studentIncludeDeleted = false
//: The one expanded row panel: either a student's detail or a form.
/** @type {HTMLTableRowElement | null} */
let openPanelRow = null
/** @type {HTMLTableRowElement | null} */
let openPanelSource = null
/** @type {import('./admin-domain.js').RosterRow[] | null} */
let pendingRoster = null
/** @type {import('./admin-domain.js').RosterRow[] | null} */
let parsedWorkbookRoster = null

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

elements.slotCapacityForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  const payload = slotCapacityPayload(elements.slotCapacityInput.value)
  if ('error' in payload) {
    showMessage(payload.error, true)
    return
  }
  elements.slotCapacitySave.disabled = true
  try {
    const change = await api.setSlotCapacityDefault(payload.capacity)
    showMessage(
      `统一容量已设为 ${change.capacity}，更新了 ${change.changed_slots} 个尚未开始的时段`
      + (change.promoted_waiters > 0 ? `，候补转为已确认 ${change.promoted_waiters} 人。` : '。'),
      false,
    )
    renderSlotCapacity(change)
    await loadSlots()
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    elements.slotCapacitySave.disabled = false
  }
})

elements.slotCapacityRestore.addEventListener('click', async () => {
  if (!window.confirm('恢复平台默认容量？尚未开始时段的容量会被重置，逐时段的单独设置也会被覆盖。'))
    return
  elements.slotCapacityRestore.disabled = true
  try {
    const change = await api.restoreSlotCapacityDefault()
    showMessage(`已恢复平台默认容量 ${change.capacity}，更新了 ${change.changed_slots} 个尚未开始的时段。`, false)
    renderSlotCapacity(change)
    await loadSlots()
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    elements.slotCapacityRestore.disabled = false
  }
})

elements.knowledgeLimitForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  const payload = knowledgeLimitPayload(
    elements.knowledgeDatasetsInput.value,
    elements.knowledgeDocumentsInput.value,
  )
  if ('error' in payload) {
    showMessage(payload.error, true)
    return
  }
  elements.knowledgeLimitSave.disabled = true
  try {
    renderKnowledgeLimit(await api.setKnowledgeLimit(payload))
    showMessage('知识库限制已保存并立即生效。', false)
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    elements.knowledgeLimitSave.disabled = false
  }
})

elements.knowledgeLimitRestore.addEventListener('click', async () => {
  if (!window.confirm('恢复平台默认的知识库限制？'))
    return
  elements.knowledgeLimitRestore.disabled = true
  try {
    const setting = await api.restoreKnowledgeLimit()
    renderKnowledgeLimit(setting)
    showMessage('已恢复平台默认的知识库限制。', false)
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    elements.knowledgeLimitRestore.disabled = false
  }
})

elements.defaultAllowanceForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  const payload = defaultAllowancePayload(elements.defaultAllowanceInput.value)
  if ('error' in payload) {
    showMessage(payload.error, true)
    return
  }
  elements.defaultAllowanceSave.disabled = true
  try {
    const change = await api.setDefaultAllowance(payload.default_allowance_usd)
    renderDefaultAllowance(change)
    showMessage(defaultAllowanceSavedMessage('默认额度已保存', change), false)
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    elements.defaultAllowanceSave.disabled = false
  }
})

elements.defaultAllowanceRestore.addEventListener('click', async () => {
  if (!window.confirm('恢复平台默认的额度？已开通账号的用户额度仍不会改变。'))
    return
  elements.defaultAllowanceRestore.disabled = true
  try {
    const change = await api.restoreDefaultAllowance()
    renderDefaultAllowance(change)
    showMessage(defaultAllowanceSavedMessage('已恢复平台默认的额度', change), false)
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    elements.defaultAllowanceRestore.disabled = false
  }
})

elements.loginPageForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  const file = elements.loginPageFile.files?.[0]
  if (!file) {
    showMessage('请选择要上传的登录页 HTML 文件。', true)
    return
  }
  elements.loginPageSave.disabled = true
  try {
    renderPortalLogin(await api.uploadPortalLoginPage(file))
    showMessage('登录页已通过审核并入库。确认无误后点击“启用”。', false)
  }
  catch (error) {
    renderPortalLoginMessage(messageFor(error))
  }
  finally {
    elements.loginPageSave.disabled = false
  }
})

elements.loginPageRestore.addEventListener('click', async () => {
  if (!window.confirm('恢复内置登录页？当前启用的自建登录页会立即下线。'))
    return
  elements.loginPageRestore.disabled = true
  try {
    renderPortalLogin(await api.restoreBuiltInPortalLogin())
    showMessage('已恢复内置登录页。', false)
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    elements.loginPageRestore.disabled = false
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
    showMessage('用户已添加。', false)
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

elements.studentSearchForm.addEventListener('submit', (event) => {
  event.preventDefault()
  studentKeyword = elements.studentSearchInput.value.trim()
  studentOffset = 0
  loadStudents()
})

elements.studentSearchClear.addEventListener('click', () => {
  elements.studentSearchInput.value = ''
  studentKeyword = ''
  studentOffset = 0
  loadStudents()
})

elements.studentsShowDeleted.addEventListener('change', () => {
  studentIncludeDeleted = elements.studentsShowDeleted.checked
  studentOffset = 0
  loadStudents()
})

elements.studentsPurge.addEventListener('click', async () => {
  const typed = window.prompt(
    `清除已软删除超过四年的用户？\n\n这会永久删除他们在 Dify 的账号、工作区、`
    + `应用、知识库与文件，不可恢复。\n\n再次确认：请输入「清除」以继续。`,
  )
  if (typed === null || typed.trim() !== '清除')
    return
  elements.studentsPurge.disabled = true
  try {
    const result = await api.purgeExpiredStudents()
    const failed = result.failed.length ? `，${result.failed.length} 个失败` : ''
    showMessage(`已清除 ${result.purged.length} 个用户的全部数据${failed}。`, false)
    await loadStudents()
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    elements.studentsPurge.disabled = false
  }
})

elements.studentTable.addEventListener('click', async (event) => {
  const target = event.target instanceof Element ? event.target : null
  if (target?.closest('[data-panel-cancel]')) {
    closeRowPanel()
    return
  }
  const button = target ? target.closest('[data-action]') : null
  if (!(button instanceof HTMLButtonElement))
    return
  const studentNumber = button.getAttribute('data-student') || ''
  const action = button.getAttribute('data-action')
  button.disabled = true
  try {
    if (action === 'suspend' || action === 'activate') {
      const suspend = action === 'suspend'
      if (suspend && !window.confirm(`确定停用用户 ${studentNumber} 吗？停用后无法登录，其工作区与数据保留。`))
        return
      await api.setStudentStatus(studentNumber, suspend ? 'suspended' : 'active')
      showMessage(suspend ? '用户已停用。' : '用户已启用。', false)
      await loadStudents()
    }
    else if (action === 'reset-password') {
      const row = button.closest('tr')
      if (!(row instanceof HTMLTableRowElement))
        return
      if (openPanelSource === row)
        closeRowPanel()
      else
        openRowPanel(row, passwordPanel(studentNumber))
    }
    else if (action === 'adjust-allowance') {
      const row = button.closest('tr')
      if (!(row instanceof HTMLTableRowElement))
        return
      if (openPanelSource === row)
        closeRowPanel()
      else
        openRowPanel(row, allowancePanel(studentNumber))
    }
    else if (action === 'rename') {
      const displayName = window.prompt(`输入用户 ${studentNumber} 的新姓名：`)
      if (!displayName)
        return
      await api.renameStudent(studentNumber, displayName.trim())
      showMessage('姓名已更新。', false)
      await loadStudents()
    }
    else if (action === 'delete') {
      // Two steps: a confirm, then typing the student number back. A mis-click
      // cannot get past the second one.
      const typed = window.prompt(
        `删除用户 ${studentNumber} ？\n\n删除后该用户将从名单中隐藏并退出登录，`
        + `但其数据会保留，可用「显示已删除」恢复。\n\n再次确认：请输入该学号以继续。`,
      )
      if (typed === null || typed.trim() !== studentNumber)
        return
      await api.deleteStudent(studentNumber)
      showMessage('用户已删除，可在「显示已删除」中恢复。', false)
      await loadStudents()
    }
    else if (action === 'restore') {
      await api.restoreStudent(studentNumber)
      showMessage('用户已恢复，可以重新登录。', false)
      await loadStudents()
    }
    else if (action === 'detail') {
      const row = button.closest('tr')
      if (!(row instanceof HTMLTableRowElement))
        return
      if (openPanelSource === row) {
        closeRowPanel()
        return
      }
      const detail = await api.studentDetail(studentNumber)
      closeRowPanel()
      openStudentDetail(row, detail)
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
  parsedWorkbookRoster = null
  pendingRoster = null
  elements.rosterConfirm.hidden = true
  if (!file)
    return
  if (file.name.toLowerCase().endsWith('.xlsx')) {
    elements.rosterFile.disabled = true
    try {
      const parsed = await api.parseRosterWorkbook(file)
      parsedWorkbookRoster = parsed.data
      elements.rosterText.value = ''
      elements.rosterText.placeholder = `已读取 ${parsed.data.length} 行 XLSX 名单；点击“解析并预览”继续。`
      elements.rosterReport.textContent = `已读取 ${parsed.data.length} 名用户。`
    }
    catch (error) {
      renderRosterErrors([messageFor(error)])
    }
    finally {
      elements.rosterFile.disabled = false
    }
    return
  }
  elements.rosterText.placeholder = '也可以直接粘贴 CSV 内容'
  elements.rosterText.value = await file.text()
})

elements.rosterPreview.addEventListener('click', async () => {
  pendingRoster = null
  elements.rosterConfirm.hidden = true
  const { rows, errors } = parsedWorkbookRoster
    ? { rows: parsedWorkbookRoster, errors: [] }
    : parseRosterCsv(elements.rosterText.value)
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
    summary.innerHTML = `将新增 <b>${preview.created}</b> 名用户，更新 <b>${preview.updated}</b> 名用户，`
      + `其中 <b>${preview.password_resets}</b> 名用户的密码将被重置，`
      + `<b>${preview.default_passwords}</b> 名新用户将使用「姓名首字拼音 + 学号后四位」初始密码并须首次登录修改。请确认后导入。`
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
    parsedWorkbookRoster = null
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
    await api.createAdministrator({
      name: String(form.get('name')).trim(),
      email: String(form.get('email')).trim(),
      password: String(form.get('password')),
    })
    elements.adminAddForm.reset()
    showMessage('管理员账号已创建并授权。', false)
    await loadAdministrators()
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
})

elements.logout.addEventListener('click', async () => {
  elements.logout.disabled = true
  try {
    await api.logout()
  }
  finally {
    window.location.assign('/signin')
  }
})

elements.presentationForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  try {
    await api.savePresentationDraft(presentationPayload())
    showMessage('页面设置草稿已保存；发布前用户端不会变化。', false)
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
})

elements.presentationPublish.addEventListener('click', async () => {
  elements.presentationPublish.disabled = true
  try {
    await api.savePresentationDraft(presentationPayload())
    await api.publishPresentation()
    showMessage('页面设置已发布到用户端。', false)
  }
  catch (error) {
    showMessage(messageFor(error), true)
  }
  finally {
    elements.presentationPublish.disabled = false
  }
})

elements.presentationRestore.addEventListener('click', async () => {
  if (!window.confirm('确定恢复平台默认页面设置吗？'))
    return
  const restored = await api.restorePresentation()
  renderPresentation(restored)
  showMessage('已恢复平台默认页面设置。', false)
})

for (const button of elements.reportButtons) {
  button.addEventListener('click', async () => {
    // One request at a time: the report walks the whole gateway log, so a
    // double click would only queue a second identical query.
    for (const other of elements.reportButtons)
      other.disabled = true
    try {
      if (button.dataset.reportPage)
        await openUsageReport(button.dataset.reportPage)
      else
        await downloadUsageReport(button.dataset.reportXlsx || 'day')
    }
    finally {
      for (const other of elements.reportButtons)
        other.disabled = false
    }
  })
}

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

async function loadSlotCapacity() {
  elements.slotCapacityStatus.textContent = '正在读取统一容量…'
  try {
    renderSlotCapacity(await api.slotCapacitySetting())
  }
  catch (error) {
    elements.slotCapacityStatus.textContent = messageFor(error)
  }
}

/** @param {import('./admin-api.js').SlotCapacitySetting} setting */
function renderSlotCapacity(setting) {
  elements.slotCapacityInput.value = String(setting.capacity)
  elements.slotCapacityStatus.textContent = setting.is_default
    ? `当前使用平台默认容量 ${setting.capacity}。`
    : `当前使用统一容量 ${setting.capacity}（平台默认为 ${setting.platform_default}）。`
}

async function loadKnowledgeLimit() {
  elements.knowledgeLimitStatus.textContent = '正在读取知识库限制…'
  try {
    renderKnowledgeLimit(await api.knowledgeLimitSetting())
  }
  catch (error) {
    elements.knowledgeLimitStatus.textContent = messageFor(error)
  }
}

/** @param {import('./admin-api.js').KnowledgeLimitSetting} setting */
function renderKnowledgeLimit(setting) {
  elements.knowledgeDatasetsInput.value = String(setting.max_datasets_per_workspace)
  elements.knowledgeDocumentsInput.value = String(setting.max_documents_per_dataset)
  elements.knowledgeLimitStatus.textContent = setting.is_default
    ? `当前使用平台默认：每工作区 ${setting.max_datasets_per_workspace} 个知识库，单库 ${setting.max_documents_per_dataset} 个文件。`
    : `当前限制：每工作区 ${setting.max_datasets_per_workspace} 个知识库，单库 ${setting.max_documents_per_dataset} 个文件`
      + `（平台默认为 ${setting.platform_max_datasets_per_workspace} / ${setting.platform_max_documents_per_dataset}）。`
}

async function loadDefaultAllowance() {
  elements.defaultAllowanceStatus.textContent = '正在读取默认额度…'
  try {
    renderDefaultAllowance(await api.defaultAllowanceSetting())
  }
  catch (error) {
    elements.defaultAllowanceStatus.textContent = messageFor(error)
  }
}

/** @param {import('./admin-api.js').DefaultAllowanceSetting} setting */
function renderDefaultAllowance(setting) {
  elements.defaultAllowanceInput.value = allowanceAmount(setting.default_allowance_usd)
  elements.defaultAllowanceStatus.textContent = setting.is_default
    ? `当前使用平台默认：${allowanceAmount(setting.default_allowance_usd)} 元。`
    : `当前默认额度：${allowanceAmount(setting.default_allowance_usd)} 元`
      + `（平台默认为 ${allowanceAmount(setting.platform_default_usd)} 元）。`
}

/**
 * Say which students a save actually reached, because the answer is not "all of them".
 *
 * @param {string} headline
 * @param {import('./admin-api.js').DefaultAllowanceSettingChange} change
 */
function defaultAllowanceSavedMessage(headline, change) {
  const applied = change.changed_students > 0
    ? `已同步 ${change.changed_students} 个尚未开通模型账号的用户`
    : '没有尚未开通模型账号的用户需要同步'
  return `${headline}：${applied}；已开通账号的用户额度不变。`
}

async function loadPortalLogin() {
  elements.loginPageStatus.textContent = '正在读取登录界面状态…'
  try {
    renderPortalLogin(await api.portalLoginState())
  }
  catch (error) {
    elements.loginPageStatus.textContent = messageFor(error)
  }
}

/** @param {{ active_id: string | null, active_filename: string | null, pages: Array<object> }} state */
function renderPortalLogin(state) {
  elements.loginPageStatus.textContent = state.active_id
    ? `当前启用：${state.active_filename}。`
    : '当前使用内置登录页。'
  elements.loginPageReport.innerHTML = ''
  elements.loginPageList.innerHTML = ''
  if (!state.pages?.length)
    return
  state.pages.forEach((page) => {
    const card = document.createElement('div')
    card.className = 'preview-summary'
    const title = document.createElement('p')
    title.innerHTML = `<b>${escapeHtml(page.filename)}</b> · ${Math.round(page.size_bytes / 1024)} KB`
      + (page.is_active ? ' · <b>已启用</b>' : '')
    card.append(title)
    if (page.checks?.length) {
      const checks = document.createElement('p')
      checks.className = 'muted'
      checks.textContent = `审核通过项：${page.checks.join('；')}`
      card.append(checks)
    }
    if (page.warnings?.length) {
      const warnings = document.createElement('p')
      warnings.className = 'muted'
      warnings.textContent = `提醒：${page.warnings.join('；')}`
      card.append(warnings)
    }
    const actions = document.createElement('div')
    actions.className = 'row-actions'
    if (!page.is_active) {
      const enable = document.createElement('button')
      enable.type = 'button'
      enable.className = 'secondary compact'
      enable.textContent = '启用'
      enable.addEventListener('click', async () => {
        try {
          renderPortalLogin(await api.activatePortalLoginPage(page.id))
          showMessage(`已启用 ${page.filename}。`, false)
        }
        catch (error) {
          showMessage(messageFor(error), true)
        }
      })
      const remove = document.createElement('button')
      remove.type = 'button'
      remove.className = 'secondary compact danger'
      remove.textContent = '删除'
      remove.addEventListener('click', async () => {
        if (!window.confirm(`删除 ${page.filename}？`))
          return
        try {
          renderPortalLogin(await api.deletePortalLoginPage(page.id))
          showMessage('已删除。', false)
        }
        catch (error) {
          showMessage(messageFor(error), true)
        }
      })
      actions.append(enable, remove)
    }
    card.append(actions)
    elements.loginPageList.append(card)
  })
}

function renderPortalLoginMessage(message) {
  elements.loginPageReport.innerHTML = ''
  const item = document.createElement('p')
  item.className = 'message error'
  item.textContent = message
  elements.loginPageReport.append(item)
}

async function loadStudents() {
  closeRowPanel()
  elements.studentTable.textContent = '正在读取用户列表…'
  try {
    const { data } = await api.listStudents(PAGE_SIZE, studentOffset, studentKeyword, studentIncludeDeleted)
    elements.studentsPage.textContent = `第 ${Math.floor(studentOffset / PAGE_SIZE) + 1} 页`
    elements.studentsPrev.disabled = studentOffset === 0
    elements.studentsNext.disabled = data.length < PAGE_SIZE
    if (!data.length) {
      elements.studentTable.textContent = studentKeyword
        ? `没有匹配「${studentKeyword}」的用户。`
        : '本页没有用户。'
      return
    }
    const table = document.createElement('table')
    table.innerHTML = '<thead><tr><th>学号</th><th>姓名</th><th>班级</th><th>状态</th><th>额度</th><th>凭据</th><th>操作</th></tr></thead>'
    const body = document.createElement('tbody')
    for (const student of data) {
      const row = document.createElement('tr')
      const active = student.status === 'active'
      const deleted = !!student.deleted_at
      const credentialBadges = [
        student.has_credential ? '<span class="badge credential">平台密码</span>' : '',
        student.virtual_identity ? '<span class="badge virtual">虚拟</span>' : '',
      ].join(' ')
      const allowance = student.allowance
      // A missing allowance means the gateway did not answer — show that as
      // unknown rather than as a balance of zero.
      const remaining = allowance ? `${allowance.remaining_usd} 元` : '—'
      const exhausted = allowance && !allowance.model_calls_enabled
      row.innerHTML = `
        <td>${escapeHtml(student.student_number)}</td>
        <td>${escapeHtml(student.display_name)}</td>
        <td>${escapeHtml(student.cohort || '—')}</td>
        <td><span class="badge ${deleted ? 'deleted' : active ? 'active' : 'suspended'}">${deleted ? '已删除' : active ? '启用' : '已停用'}</span></td>
        <td>${remaining}${exhausted ? ' <span class="badge exhausted">已用尽</span>' : ''}</td>
        <td>${credentialBadges || '—'}</td>
        <td>
          ${deleted
            ? `<button class="secondary compact" type="button" data-action="restore" data-student="${escapeHtml(student.student_number)}">恢复</button>`
            : `<button class="secondary compact" type="button" data-action="rename" data-student="${escapeHtml(student.student_number)}">更名</button>
               <button class="secondary compact" type="button" data-action="${active ? 'suspend' : 'activate'}" data-student="${escapeHtml(student.student_number)}">${active ? '停用' : '启用'}</button>
               <button class="secondary compact" type="button" data-action="reset-password" data-student="${escapeHtml(student.student_number)}">重置密码</button>
               <button class="secondary compact" type="button" data-action="adjust-allowance" data-student="${escapeHtml(student.student_number)}">额度调整</button>
               <button class="secondary compact" type="button" data-action="delete" data-student="${escapeHtml(student.student_number)}">删除</button>`}
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

// The allowance numbers are dollar-denominated on the gateway (`*_usd`), but the
// campus operators and the student portal both read them as 元 — a display label
// choice, with no exchange-rate conversion anywhere.
/**
 * Render the detail into a row of its own, directly beneath the student it
 * describes, so the operator never has to match a panel at the page bottom
 * back to a row in the middle of the table.
 * @param {HTMLTableRowElement} row @param {Record<string, unknown>} detail
 */
/** Timestamps arrive as ISO strings; show them in the browser's own zone. */
function formatStamp(value) {
  if (!value)
    return '未知'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString()
}

function openStudentDetail(row, detail) {
  const allowance = /** @type {{ remaining_usd?: string, used_usd?: string, total_usd?: string } | null} */ (detail.allowance)
  const body = document.createElement('div')
  body.innerHTML = `
    <b>${escapeHtml(String(detail.display_name))}</b>（${escapeHtml(String(detail.student_number))}）<br>
    工作区：${escapeHtml(String(detail.workspace_id || '未开通'))}<br>
    额度：${allowance ? `剩余 ${allowance.remaining_usd} 元 / 总额 ${allowance.total_usd} 元（已用 ${allowance.used_usd} 元）` : '未开通'}<br>
    <span class="detail-stamp">创建时间：${escapeHtml(formatStamp(detail.created_at))}${detail.deleted_at ? `　·　删除时间：${escapeHtml(formatStamp(detail.deleted_at))}` : ''}</span>`
  openRowPanel(row, body)
}

/**
 * The inline form for adjusting one student's allowance.
 *
 * Lives under the student's own row, and reloading the list afterwards is what
 * syncs the new balance back into that row — no dialog, no stale cell.
 *
 * @param {string} studentNumber
 */
function allowancePanel(studentNumber) {
  const form = panelForm(
    `调整「${escapeHtml(studentNumber)}」的额度`,
    `<label>调整金额（元，可为负数）<input name="delta" type="number" step="0.0001" required></label>
     <label>调整原因<input name="reason" maxlength="500" required></label>`,
    '确认调整',
  )
  form.addEventListener('submit', async (event) => {
    event.preventDefault()
    const values = new FormData(form)
    const delta = String(values.get('delta') ?? '').trim()
    const reason = String(values.get('reason') ?? '').trim()
    if (!delta || !reason)
      return
    await submitPanel(form, async () => {
      // Entered in 元; the API field is still `delta_usd` and no conversion happens.
      await api.adjustAllowance(studentNumber, {
        delta_usd: delta,
        reason,
        request_id: crypto.randomUUID(),
      })
      showMessage('额度已调整。', false)
    })
  })
  return form
}

/** The inline form for resetting one student's password. */
function passwordPanel(studentNumber) {
  const form = panelForm(
    `重置「${escapeHtml(studentNumber)}」的密码`,
    '<label>新密码<input name="password" maxlength="128" autocomplete="off" required></label>',
    '确认重置',
  )
  form.addEventListener('submit', async (event) => {
    event.preventDefault()
    const password = String(new FormData(form).get('password') ?? '').trim()
    if (!password)
      return
    await submitPanel(form, async () => {
      await api.resetStudentPassword(studentNumber, password)
      showMessage('密码已重置，该用户的登录会话已全部退出。', false)
    })
  })
  return form
}

/** @param {string} title @param {string} fields @param {string} confirmLabel */
function panelForm(title, fields, confirmLabel) {
  const form = document.createElement('form')
  form.className = 'row-form'
  form.innerHTML = `
    <p class="row-form-title">${title}</p>
    ${fields}
    <div class="row-actions">
      <button class="primary" type="submit">${confirmLabel}</button>
      <button class="secondary" type="button" data-panel-cancel>取消</button>
    </div>`
  return form
}

/**
 * Run a panel's action, then reload the roster so the row shows the new state.
 *
 * On failure the panel stays open with the server's message, so the operator
 * can correct the input instead of starting over.
 *
 * @param {HTMLFormElement} form @param {() => Promise<void>} action
 */
async function submitPanel(form, action) {
  const submit = form.querySelector('button[type=submit]')
  if (submit instanceof HTMLButtonElement)
    submit.disabled = true
  try {
    await action()
    await loadStudents()
  }
  catch (error) {
    showMessage(messageFor(error), true)
    if (submit instanceof HTMLButtonElement)
      submit.disabled = false
  }
}

/**
 * Expand one row into a panel directly beneath it.
 *
 * Only one panel is ever open, and re-opening the same row replaces it, so
 * the table never accumulates stale panels after a reload.
 *
 * @param {HTMLTableRowElement} sourceRow @param {HTMLElement} content
 */
function openRowPanel(sourceRow, content) {
  closeRowPanel()
  const cell = document.createElement('td')
  cell.colSpan = sourceRow.children.length
  cell.append(content)
  const panelRow = document.createElement('tr')
  panelRow.className = 'student-detail-row'
  panelRow.append(cell)
  sourceRow.after(panelRow)
  openPanelRow = panelRow
  openPanelSource = sourceRow
}

function closeRowPanel() {
  if (openPanelRow)
    openPanelRow.remove()
  openPanelRow = null
  openPanelSource = null
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

async function loadPresentation() {
  renderPresentation(await api.presentationDraft())
}

function renderPresentation(presentation) {
  elements.presentationLoginHtml.value = presentation.login_html
  const byTrack = new Map(presentation.tracks.map(item => [item.track, item]))
  for (const row of document.querySelectorAll('[data-presentation-track]')) {
    if (!(row instanceof HTMLTableRowElement))
      continue
    const item = byTrack.get(row.dataset.presentationTrack || '')
    if (!item)
      continue
    row.querySelector('[name="title"]').value = item.title
    row.querySelector('[name="description"]').value = item.description
    row.querySelector('[name="position"]').value = String(item.position)
  }
}

function presentationPayload() {
  const tracks = []
  for (const row of document.querySelectorAll('[data-presentation-track]')) {
    if (!(row instanceof HTMLTableRowElement))
      continue
    tracks.push({
      track: row.dataset.presentationTrack,
      title: row.querySelector('[name="title"]').value.trim(),
      description: row.querySelector('[name="description"]').value.trim(),
      position: Number.parseInt(row.querySelector('[name="position"]').value, 10),
    })
  }
  return { login_html: elements.presentationLoginHtml.value, tracks }
}

const GRANULARITY_LABELS = { day: '按日', month: '按月', year: '按年' }

/**
 * Open one usage report in a new tab.
 *
 * The page is fetched and handed over as a blob instead of being linked to:
 * the Dify console only reads the CSRF token from the X-CSRF-Token request
 * header, so a top-level navigation to the endpoint can never authenticate
 * and always comes back 401 (see CODEBUDDY §31).
 *
 * @param {string} granularity
 */
async function openUsageReport(granularity) {
  setReportStatus('正在生成报告…')
  try {
    const { blob } = await api.usageReportPage(granularity)
    const page = blob.type ? blob : new Blob([blob], { type: 'text/html; charset=utf-8' })
    const url = URL.createObjectURL(page)
    openBlob(url, { target: '_blank' })
    // The new tab reads the blob as it opens; hold it long enough for slow
    // tab starts, then release it.
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
    setReportStatus(`${GRANULARITY_LABELS[granularity] || ''}报告已在新标签页打开。`)
  }
  catch (error) {
    setReportStatus(messageFor(error), true)
  }
}

/**
 * Save one usage report as a workbook.
 *
 * @param {string} granularity
 */
async function downloadUsageReport(granularity) {
  setReportStatus('正在导出 Excel…')
  try {
    const { blob, filename } = await api.usageReportWorkbook(granularity)
    const url = URL.createObjectURL(blob)
    openBlob(url, { download: filename || `token-usage-${granularity}.xlsx` })
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
    setReportStatus(`${GRANULARITY_LABELS[granularity] || ''} Excel 已开始下载。`)
  }
  catch (error) {
    setReportStatus(messageFor(error), true)
  }
}

/**
 * Click a link the markup does not need to carry, so a fetched blob can be
 * opened or saved without tripping the popup blocker.
 *
 * @param {string} url
 * @param {{ target?: string, download?: string }} options
 */
function openBlob(url, options) {
  const anchor = document.createElement('a')
  anchor.href = url
  if (options.download)
    anchor.download = options.download
  if (options.target) {
    anchor.target = options.target
    anchor.rel = 'noopener'
  }
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
}

/** @param {string} text @param {boolean} [error] */
function setReportStatus(text, error = false) {
  elements.reportStatus.textContent = text
  elements.reportStatus.classList.toggle('error', error)
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
  await Promise.all([
    loadDefaultAllowance(),
    loadKnowledgeLimit(),
    loadPortalLogin(),
    loadSlotCapacity(),
    loadSlots(),
    loadStudents(),
    loadAdministrators(),
    loadPresentation(),
    loadManualChapters(),
  ])
}

bootstrap()

elements.manualTrack.addEventListener('change', () => {
  resetManualForm()
  void loadManualChapters()
})

elements.manualCancel.addEventListener('click', resetManualForm)

elements.manualForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  const file = elements.manualFile.files && elements.manualFile.files[0]
  if (!file) {
    showMessage('请选择 HTML 文件。', true)
    return
  }
  const chapterId = elements.manualChapterId.value
  elements.manualForm.querySelectorAll('button').forEach((button) => { button.disabled = true })
  try {
    const saved = chapterId
      ? await api.updateManualChapter(chapterId, file)
      : await api.createManualChapter(elements.manualTrack.value, file)
    elements.manualReport.textContent = `已按原始字节保存 ${saved.original_filename}（${saved.size_bytes} 字节）。`
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
      showMessage(action === 'publish' ? '章节已发布，用户现在能看到。' : '章节已撤回为草稿。', false)
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
      startEditingChapter(chapterId)
      return
    }
    else if (action === 'preview') {
      const contentUrl = button.getAttribute('data-content-url') || ''
      window.open(contentUrl, '_blank', 'noopener')
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
 * Mark one document for replacement. The administrator must choose a new
 * original file; no editable copy of its contents is created.
 *
 * @param {string} chapterId
 */
function startEditingChapter(chapterId) {
  elements.manualChapterId.value = chapterId
  elements.manualCancel.hidden = false
  elements.manualFile.focus()
}

function resetManualForm() {
  elements.manualForm.reset()
  elements.manualChapterId.value = ''
  elements.manualCancel.hidden = true
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
          <button class="secondary compact" type="button" data-manual-action="preview" data-chapter="${escapeHtml(chapter.id)}" data-content-url="${escapeHtml(chapter.content_url)}">预览</button>
          <button class="secondary compact" type="button" data-manual-action="edit" data-chapter="${escapeHtml(chapter.id)}">替换文件</button>
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
