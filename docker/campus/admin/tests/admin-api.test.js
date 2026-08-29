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
