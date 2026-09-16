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
