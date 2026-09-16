import assert from 'node:assert/strict'
import test from 'node:test'

import { AdminApi, AdminApiError } from '../assets/admin-api.js'

function response(body, { status = 200 } = {}) {
  return new Response(body === undefined ? undefined : JSON.stringify(body), {
    headers: { 'content-type': 'application/json' },
    status,
  })
}

test('admin requests echo the csrf cookie and stay same-origin', async () => {
  const requests = []
  const api = new AdminApi(async (url, options = {}) => {
    requests.push({ url, options })
    return response({ data: [], server_now: '2026-08-28T02:00:00Z' })
  }, () => 'csrf_token=tok-1')

  await api.listSlots('2026-08-28')
  await api.setSlotCapacity('2026-08-28T04:00:00Z', 120)

  assert.deepEqual(requests.map(({ url }) => url), [
    '/console/api/campus/admin/slots?day=2026-08-28',
    '/console/api/campus/admin/slots',
  ])
  assert.ok(requests.every(({ options }) => options.credentials === 'same-origin'))
  assert.ok(requests.every(({ options }) => options.headers.get('X-CSRF-Token') === 'tok-1'))
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    starts_at: '2026-08-28T04:00:00Z',
    capacity: 120,
  })
})

test('authentication and authorization failures get administrator-facing messages', async () => {
  const unauthorized = new AdminApi(async () => response({}, { status: 401 }), () => '')
  const forbidden = new AdminApi(async () => response({}, { status: 403 }), () => '')

  await assert.rejects(unauthorized.listAdministrators(), error =>
    error instanceof AdminApiError && error.status === 401 && /登录/.test(error.message))
  await assert.rejects(forbidden.listAdministrators(), error =>
    error instanceof AdminApiError && error.status === 403 && /管理员/.test(error.message))
})

test('backend validation messages are surfaced to the administrator', async () => {
  const api = new AdminApi(async () =>
    response({ code: 'bad_request', message: 'new students require a password: 20260009' }, { status: 400 }), () => '')

  await assert.rejects(api.syncRoster({ students: [] }), error =>
    error instanceof AdminApiError && /20260009/.test(error.message))
})

test('a multipart upload keeps the browser boundary instead of a json content type', async () => {
  // Forcing application/json on FormData drops the multipart boundary, and the
  // upload fails at the server with a parse error that names nothing useful.
  let seen
  const api = new AdminApi(async (url, options) => {
    seen = options
    return new Response(JSON.stringify({ id: 'i1', url: '/console/api/campus/lab-manuals/images/i1' }), {
      headers: { 'content-type': 'application/json' },
    })
  }, () => 'csrf_token=tok')

  const file = new File([new Uint8Array([1, 2, 3])], 'figure.png', { type: 'image/png' })
  const result = await api.uploadManualImage('deep-learning', file)

  assert.equal(result.url, '/console/api/campus/lab-manuals/images/i1')
  assert.ok(seen.body instanceof FormData)
  assert.equal(seen.headers.get('content-type'), null)
  assert.equal(seen.headers.get('X-CSRF-Token'), 'tok')
})

test('learning documents are sent as original files without text decoding', async () => {
  let seen
  const api = new AdminApi(async (url, options) => {
    seen = { url, options }
    return response({
      id: 'd1',
      original_filename: '实验.html',
      size_bytes: 4,
      content_url: 'http://127.0.0.1:18083/document',
    })
  }, () => 'csrf_token=tok')
  const original = new Uint8Array([0xff, 0xfe, 0x00, 0x61])
  const file = new File([original], '实验.html', { type: 'text/html' })

  await api.createManualChapter('agent', file)

  assert.equal(seen.url, '/console/api/campus/admin/lab-manuals/agent/chapters')
  assert.ok(seen.options.body instanceof FormData)
  assert.deepEqual(new Uint8Array(await seen.options.body.get('file').arrayBuffer()), original)
  assert.equal(seen.options.headers.get('content-type'), null)
})

test('the unified slot capacity endpoints share the administrator route', async () => {
  const requests = []
  const api = new AdminApi(async (url, options = {}) => {
    requests.push({ url, options })
    return response({
      capacity: 120,
      platform_default: 500,
      configured_capacity: 120,
      is_default: false,
      previous_configured_capacity: null,
      scanned_slots: 2,
      changed_slots: 1,
      promoted_waiters: 0,
    })
  }, () => 'csrf_token=tok-2')

  await api.slotCapacitySetting()
  await api.setSlotCapacityDefault(120)
  await api.restoreSlotCapacityDefault()

  assert.deepEqual(requests.map(({ url }) => url), [
    '/console/api/campus/admin/slot-capacity',
    '/console/api/campus/admin/slot-capacity',
    '/console/api/campus/admin/slot-capacity',
  ])
  assert.deepEqual(requests.map(({ options }) => options.method || 'GET'), ['GET', 'PUT', 'DELETE'])
  assert.deepEqual(JSON.parse(requests[1].options.body), { capacity: 120 })
  assert.ok(requests.every(({ options }) => options.credentials === 'same-origin'))
  assert.ok(requests.every(({ options }) => options.headers.get('X-CSRF-Token') === 'tok-2'))
})

test('the knowledge limit endpoints share the administrator route', async () => {
  const requests = []
  const api = new AdminApi(async (url, options = {}) => {
    requests.push({ url, options })
    return response({
      max_datasets_per_workspace: 1,
      max_documents_per_dataset: 3,
      platform_max_datasets_per_workspace: 1,
      platform_max_documents_per_dataset: 3,
      configured_max_datasets_per_workspace: null,
      configured_max_documents_per_dataset: null,
      is_default: true,
    })
  }, () => 'csrf_token=tok-3')

  await api.knowledgeLimitSetting()
  await api.setKnowledgeLimit({ max_datasets_per_workspace: 1, max_documents_per_dataset: 3 })
  await api.restoreKnowledgeLimit()

  assert.deepEqual(requests.map(({ url }) => url), [
    '/console/api/campus/admin/knowledge-limits',
    '/console/api/campus/admin/knowledge-limits',
    '/console/api/campus/admin/knowledge-limits',
  ])
  assert.deepEqual(requests.map(({ options }) => options.method || 'GET'), ['GET', 'PUT', 'DELETE'])
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    max_datasets_per_workspace: 1,
    max_documents_per_dataset: 3,
  })
  assert.ok(requests.every(({ options }) => options.credentials === 'same-origin'))
  assert.ok(requests.every(({ options }) => options.headers.get('X-CSRF-Token') === 'tok-3'))
})

test('the default allowance endpoints share the administrator route', async () => {
  const requests = []
  const api = new AdminApi(async (url, options = {}) => {
    requests.push({ url, options })
    return response({
      default_allowance_usd: '15.0000',
      platform_default_usd: '10.0000',
      configured_default_allowance_usd: '15.0000',
      is_default: false,
      previous_configured_default_allowance_usd: null,
      scanned_students: 3,
      changed_students: 1,
    })
  }, () => 'csrf_token=tok-4')

  await api.defaultAllowanceSetting()
  await api.setDefaultAllowance(15)
  await api.restoreDefaultAllowance()

  assert.deepEqual(requests.map(({ url }) => url), [
    '/console/api/campus/admin/default-allowance',
    '/console/api/campus/admin/default-allowance',
    '/console/api/campus/admin/default-allowance',
  ])
  assert.deepEqual(requests.map(({ options }) => options.method || 'GET'), ['GET', 'PUT', 'DELETE'])
  assert.deepEqual(JSON.parse(requests[1].options.body), { default_allowance_usd: 15 })
  assert.ok(requests.every(({ options }) => options.credentials === 'same-origin'))
  assert.ok(requests.every(({ options }) => options.headers.get('X-CSRF-Token') === 'tok-4'))
})

test('resetting a password sends no password: the server derives the initial one', async () => {
  const requests = []
  const api = new AdminApi(async (url, options = {}) => {
    requests.push({ url, options })
    return response({ student_number: '20260001', password: 'zhang0001' })
  }, () => 'csrf_token=tok-5')

  const reset = await api.resetStudentPassword('20260001')

  assert.deepEqual(requests.map(({ url }) => url), [
    '/console/api/campus/admin/students/20260001/password',
  ])
  assert.deepEqual(requests.map(({ options }) => options.method || 'GET'), ['PUT'])
  assert.equal(requests[0].options.body, undefined)
  assert.equal(reset.password, 'zhang0001')
})

test('the student list sends the search term and leaves it out when blank', async () => {
  const requests = []
  const api = new AdminApi(async (url, options = {}) => {
    requests.push({ url, options })
    return response({ data: [] })
  }, () => 'csrf_token=tok-1')

  await api.listStudents(50, 0)
  await api.listStudents(50, 50, '  张  ')
  await api.listStudents(50, 0, '2026%')

  assert.equal(requests[0].url, '/console/api/campus/admin/students?limit=50&offset=0')
  // Trimmed, and encoded so a `%` stays a literal the backend can escape.
  assert.equal(requests[1].url, '/console/api/campus/admin/students?limit=50&offset=50&keyword=%E5%BC%A0')
  assert.equal(requests[2].url, '/console/api/campus/admin/students?limit=50&offset=0&keyword=2026%25')
})

test('the rename body is JSON, not a stringified object literal', async () => {
  const requests = []
  const api = new AdminApi(async (url, options = {}) => {
    requests.push({ url, options })
    return response({ id: 's1', student_number: '20260001', display_name: '新名字' })
  }, () => 'csrf_token=tok-1')

  await api.renameStudent('20260001', '新名字')

  assert.equal(requests[0].options.method, 'PUT')
  // A plain object would be coerced to the literal "[object Object]", which the
  // server cannot parse as JSON and rejects with a bare 400.
  assert.deepEqual(JSON.parse(String(requests[0].options.body)), { display_name: '新名字' })
  assert.equal(requests[0].options.headers.get('content-type'), 'application/json')
})

test('the usage report page is fetched as a document, csrf header included', async () => {
  const requests = []
  const api = new AdminApi(async (url, options = {}) => {
    requests.push({ url, options })
    return new Response('<!doctype html><h1>token 使用与计费报告</h1>', {
      headers: { 'content-type': 'text/html; charset=utf-8' },
    })
  }, () => 'csrf_token=tok-1')

  const { blob } = await api.usageReportPage('month')

  assert.equal(requests[0].url, '/console/api/campus/admin/usage-report?granularity=month')
  // A top-level navigation cannot set this header, which is exactly why the
  // page has to be fetched and opened as a blob instead.
  assert.equal(requests[0].options.headers.get('X-CSRF-Token'), 'tok-1')
  assert.equal(requests[0].options.credentials, 'same-origin')
  assert.match(blob.type, /text\/html/)
  assert.match(await blob.text(), /token 使用与计费报告/)
})

test('the workbook download keeps the filename the server chose', async () => {
  const api = new AdminApi(async () => new Response(new Uint8Array([0x50, 0x4b]), {
    headers: {
      'content-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'content-disposition': 'attachment; filename="token-usage-day.xlsx"',
    },
  }), () => 'csrf_token=tok-1')

  const { blob, filename } = await api.usageReportWorkbook('day')

  assert.equal(filename, 'token-usage-day.xlsx')
  assert.equal(blob.size, 2)
})

test('a rejected report fetch is reported like any other admin failure', async () => {
  const api = new AdminApi(async () => response({}, { status: 401 }), () => '')

  await assert.rejects(api.usageReportPage('year'), error =>
    error instanceof AdminApiError && error.status === 401 && /登录/.test(error.message))
})
