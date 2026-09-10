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
