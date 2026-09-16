import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const page = await readFile(new URL('../index.html', import.meta.url), 'utf8')
const script = await readFile(new URL('../assets/admin.js', import.meta.url), 'utf8')

test('every element admin.js requires exists in index.html', () => {
  const required = [...script.matchAll(/requiredElement\('#([a-z-]+)'/g)].map(match => match[1])

  assert.ok(required.length > 0, 'admin.js must declare its required elements')
  const missing = required.filter(id => !page.includes(`id="${id}"`))
  assert.deepEqual(missing, [], `index.html is missing required elements: ${missing.join(', ')}`)
})

test('the auth recovery control is a button, not a bare sign-in link that can bounce', () => {
  assert.match(page, /<button id="auth-action"/)
  assert.doesNotMatch(page, /<a[^>]+class="primary-link"[^>]+href="\/signin"/)
})

test('every tab button has the panel the switcher will look for', () => {
  const tabs = [...page.matchAll(/data-tab="([a-z-]+)"/g)].map(match => match[1])

  assert.ok(tabs.length > 0, 'index.html must declare tab buttons')
  const missing = tabs.filter(name => !page.includes(`id="tab-${name}"`))
  assert.deepEqual(missing, [], `index.html is missing tab panels: ${missing.join(', ')}`)
})

test('the model gateway tab keeps the pricing guardrail in front of the administrator', () => {
  // Adding a model without a ratio makes the gateway bill at its fallback,
  // roughly five hundred times the real price. The tab that sends an
  // administrator to the gateway must say so.
  const panel = page.slice(page.indexOf('id="tab-gateway"'))

  assert.match(panel, /倍率/)
  assert.match(panel, /manage\.sh verify/)
})

test('the learning-document tab offers all tracks in the required order', () => {
  const panel = page.slice(page.indexOf('id="tab-manuals"'), page.indexOf('id="tab-gateway"'))
  const options = [...panel.matchAll(/<option value="([a-z-]+)"/g)].map(match => match[1])

  assert.deepEqual(options, ['large-model', 'agent', 'deep-learning', 'reference'])
})

test('the learning-document tab explains byte preservation and origin isolation', () => {
  const panel = page.slice(page.indexOf('id="tab-manuals"'), page.indexOf('id="tab-gateway"'))

  assert.match(panel, /原始字节/)
  assert.match(panel, /不解析、不改写/)
  assert.match(panel, /不限制页面自身/)
  assert.match(panel, /独立手册来源/)
})

test('a saved document reports the original filename and byte size', () => {
  const submit = script.slice(script.indexOf("elements.manualForm.addEventListener('submit'"))
  const handler = submit.slice(0, submit.indexOf('\n})'))

  assert.match(handler, /saved\.original_filename/)
  assert.match(handler, /saved\.size_bytes/)
})

test('replacing a document never reads or edits its HTML body', () => {
  const editor = script.slice(script.indexOf('function startEditingChapter('))
  const body = editor.slice(0, editor.indexOf('\n}'))
  const submit = script.slice(script.indexOf("elements.manualForm.addEventListener('submit'"), script.indexOf("elements.manualTable.addEventListener('click'"))

  assert.match(body, /manualChapterId\.value = chapterId/)
  assert.doesNotMatch(submit, /file\.text\(\)/)
  assert.doesNotMatch(script, /body_html|manualHtml/)
})

test('chapter titles reach the table escaped', () => {
  // Titles are administrator input rendered into an innerHTML template.
  const loader = script.slice(script.indexOf('async function loadManualChapters('))
  const body = loader.slice(0, loader.indexOf('\n}\n'))

  assert.match(body, /escapeHtml\(chapter\.title\)/)
  assert.doesNotMatch(body, /\$\{chapter\.title\}/)
})

test('the raw-file form does not offer content rewriting helpers', () => {
  const panel = page.slice(page.indexOf('id="tab-manuals"'), page.indexOf('id="tab-gateway"'))

  assert.match(panel, /id="manual-file"/)
  assert.doesNotMatch(panel, /manual-html|manual-image/)
})

test('the slot tab says a unified capacity only rewrites slots that have not started', () => {
  // The unified value overwrites per-slot exceptions, so the panel has to name
  // the slots it touches and the ones it leaves alone before an administrator
  // presses save.
  const panel = page.slice(page.indexOf('id="tab-slots"'), page.indexOf('id="tab-students"'))

  assert.match(panel, /id="slot-capacity-input"/)
  assert.match(panel, /id="slot-capacity-restore"/)
  assert.match(panel, /尚未开始/)
  assert.match(panel, /覆盖/)
  assert.match(panel, /进行中与已结束的时段不受影响/)
})

test('the default allowance panel names who a save reaches', () => {
  // Lowering the default must not look like it takes quota away from students
  // who already hold a model account, so the panel says so before the save.
  const panel = page.slice(page.indexOf('id="tab-students"'), page.indexOf('id="tab-knowledge"'))

  assert.match(panel, /id="default-allowance-input"/)
  assert.match(panel, /id="default-allowance-save"/)
  assert.match(panel, /id="default-allowance-restore"/)
  assert.match(panel, /尚未开通模型账号/)
  assert.match(panel, /已经开通的用户额度保持不变/)
})

test('allowance edits in a row panel while a password reset just confirms', () => {
  const scriptSource = script.slice(script.indexOf("else if (action === 'reset-password')"), script.indexOf("else if (action === 'rename')"))

  assert.match(scriptSource, /openRowPanel\(row, allowancePanel\(studentNumber\)\)/)
  // A reset has nothing to ask for: the platform derives the student's initial
  // password, so the row confirms and then reports what the password became.
  assert.match(scriptSource, /window\.confirm\(/)
  assert.match(scriptSource, /api\.resetStudentPassword\(studentNumber\)/)
  assert.match(scriptSource, /reset\.password/)
  assert.doesNotMatch(scriptSource, /passwordPanel/)
  assert.doesNotMatch(scriptSource, /window\.prompt/)
})

test('a confirmed panel reloads the roster so the row shows the new state', () => {
  const submit = script.slice(script.indexOf('async function submitPanel('))
  const body = submit.slice(0, submit.indexOf('\n}\n'))

  assert.match(body, /await action\(\)/)
  // The reload is what writes the new balance or password state back into the row.
  assert.match(body, /await loadStudents\(\)/)
  // A rejected submit keeps the panel open with the server's reason.
  assert.match(body, /catch \(error\) \{[\s\S]{0,120}?showMessage\(messageFor\(error\), true\)/)
})

test('the row panels carry the fields the API needs', () => {
  const allowance = script.slice(script.indexOf('function allowancePanel('), script.indexOf('function panelForm('))

  assert.match(allowance, /name="delta"/)
  assert.match(allowance, /name="reason"/)
  assert.match(allowance, /delta_usd: delta/)
  // No panel asks for a password any more: the reset derives it server-side.
  assert.doesNotMatch(script, /passwordPanel/)
  // The panel names its own cancel, and nothing opens a second panel at once.
  const panelForm = script.slice(script.indexOf('function panelForm('))
  assert.match(panelForm, /data-panel-cancel/)
  assert.match(script, /function openRowPanel\(sourceRow, content\) \{[\s\S]{0,80}?closeRowPanel\(\)/)
})

test('the gateway tab offers a usage report at every granularity, plus Excel', () => {
  const panel = page.slice(page.indexOf('id="tab-gateway"'))

  for (const granularity of ['day', 'month', 'year']) {
    assert.match(panel, new RegExp(`data-report-page="${granularity}"`))
    assert.match(panel, new RegExp(`data-report-xlsx="${granularity}"`))
  }
  assert.match(panel, /三张表/)
  // A plain link cannot carry the console CSRF header, so the tab must not
  // offer one: the buttons fetch the report and open the blob instead.
  assert.doesNotMatch(panel, /href="\/console\/api\//)
  assert.match(script, /api\.usageReportPage\(/)
  assert.match(script, /api\.usageReportWorkbook\(/)
  assert.match(script, /function openBlob\(/)
})

test('every element the script touches is declared in its table', () => {
  // A deleted view used to leave one `elements.someView.hidden = true` behind,
  // and the swallowed error left a signed-in student on a blank page. The table
  // is the single place a view may be referenced from.
  const tableStart = script.indexOf('const elements = {')
  const table = script.slice(tableStart, script.indexOf('\n}\n', tableStart))
  const declared = new Set([...table.matchAll(/^\s{2}([a-zA-Z]+):/gm)].map(match => match[1]))
  const used = new Set([...script.matchAll(/elements\.([a-zA-Z]+)/g)].map(match => match[1]))
  const undeclared = [...used].filter(name => !declared.has(name))

  assert.ok(declared.size > 0, 'the script must declare its element table')
  assert.deepEqual(undeclared, [], `undeclared element references: ${undeclared.join(', ')}`)
})

test('a roster delete confirms twice without making the operator retype the number', () => {
  const source = script.slice(script.indexOf("else if (action === 'delete')"), script.indexOf("else if (action === 'restore')"))

  // The transcribed student number was friction rather than a decision: two
  // confirms are what stop a mis-click, and neither of them asks for typing.
  assert.doesNotMatch(source, /window\.prompt/)
  assert.equal([...source.matchAll(/window\.confirm\(/g)].length, 2)
  assert.match(source, /api\.deleteStudent\(studentNumber\)/)
  assert.match(source, /await loadStudents\(\)/)
})

test('the batch delete drives the audited per-student endpoint and names its failures', () => {
  const start = script.indexOf("elements.studentsBatchDelete.addEventListener('click'")
  const handler = script.slice(start, script.indexOf('\n})\n', start))

  // A bulk route would have to reimplement the audit event and the session
  // revocation, so the batch loops the endpoint a single delete already uses.
  assert.match(handler, /for \(const studentNumber of studentNumbers\)/)
  assert.match(handler, /api\.deleteStudent\(studentNumber\)/)
  assert.match(handler, /failed\.push\(studentNumber\)/)
  assert.match(handler, /failed\.join/)
  assert.equal([...handler.matchAll(/window\.confirm\(/g)].length, 2)
  assert.match(handler, /await loadStudents\(\)/)
})

test('a row selection cannot outlive the page it was made on', () => {
  const loader = script.slice(script.indexOf('async function loadStudents()'))
  const body = loader.slice(0, loader.indexOf('\n}\n'))

  // The list is server-paged, so a selection carried across pages could name
  // rows the operator can no longer see; every load starts from none.
  assert.match(body, /studentSelection\.clear\(\)/)
  assert.match(body, /updateStudentSelection\(\)/)
  assert.match(body, /data-select-student/)
  assert.match(body, /students-select-all/)
  assert.match(body, /\$\{selectCell\}/)
  // Only active rows are selectable: the deleted view is for restoring.
  assert.match(body, /const selectCell = deleted/)
})

test('the batch bar is the only place the selection count is written', () => {
  const updater = script.slice(script.indexOf('function updateStudentSelection()'), script.indexOf('function setStudentSelection('))

  assert.match(updater, /elements\.studentsBatchCount\.textContent/)
  assert.match(updater, /elements\.studentsBatch\.hidden = studentSelection\.size === 0/)
})

test('the row checkbox state is read from its data attribute, not from input.value', () => {
  const updater = script.slice(script.indexOf('function updateStudentSelection()'), script.indexOf('/**\n * @param {string} studentNumber'))

  // A checkbox with no `value` attribute reports "on", so reading `box.value`
  // unchecked every box the selection had just recorded: the batch deleted the
  // right rows while the operator could not see which ones they were.
  assert.match(updater, /studentSelection\.has\(box\.getAttribute\('data-select-student'\)/)
  assert.doesNotMatch(updater, /studentSelection\.has\(box\.value/)
})

test('selecting every row captures the state before refreshing the bar', () => {
  const handler = script.slice(script.indexOf("elements.studentTable.addEventListener('change'"))
  const body = handler.slice(0, handler.indexOf('\n})\n'))

  // The refresh rewrites the select-all checkbox itself, so reading
  // `target.checked` inside the loop stopped the selection after one row.
  assert.match(body, /const selected = target\.checked/)
  assert.match(body, /, selected\)/)

  // The mutator stays a mutator: the bar is refreshed once, after the loop.
  const setter = script.slice(script.indexOf('function setStudentSelection('), script.indexOf("elements.studentTable.addEventListener('change'"))
  assert.doesNotMatch(setter, /updateStudentSelection\(\)/)
})
