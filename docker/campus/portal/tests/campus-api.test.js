import assert from 'node:assert/strict'
import test from 'node:test'

import { CampusApi, CampusApiError } from '../assets/campus-api.js'

function response(body, { status = 200 } = {}) {
  return new Response(body === undefined ? undefined : JSON.stringify(body), {
    headers: { 'content-type': 'application/json' },
    status,
  })
}

test('student workflow uses only same-origin Campus API requests with cookie credentials', async () => {
  const requests = []
  const fetcher = async (url, options = {}) => {
    requests.push({ url, options })
    if (url.endsWith('/auth/virtual'))
      return response({ student_id: 'student-1', expires_at: '2026-08-12T12:00:00Z' })
    if (url.endsWith('/slots?day=2026-08-12'))
      return response({ data: [] })
    if (url.endsWith('/reservations') && options.method === 'POST')
      return response({ id: 'reservation-1', status: 'confirmed' }, { status: 201 })
    if (url.endsWith('/reservations/reservation-1'))
      return response(undefined, { status: 204 })
    if (url.endsWith('/session/launch'))
      return response({ result: 'success' })
    throw new Error(`Unexpected request: ${url}`)
  }
  const api = new CampusApi(fetcher)

  await api.login('20260001', 'login-code')
  await api.listSlots('2026-08-12')
  await api.reserve('2026-08-12T02:00:00+08:00')
  await api.cancelReservation('reservation-1')
  await api.launchSession()

  assert.deepEqual(requests.map(({ url }) => url), [
    '/console/api/campus/auth/virtual',
    '/console/api/campus/slots?day=2026-08-12',
    '/console/api/campus/reservations',
    '/console/api/campus/reservations/reservation-1',
    '/console/api/campus/session/launch',
  ])
  assert.ok(requests.every(({ options }) => options.credentials === 'same-origin'))
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    subject: '20260001',
    credential: 'login-code',
  })
  assert.deepEqual(JSON.parse(requests[2].options.body), {
    starts_at: '2026-08-12T02:00:00+08:00',
  })
})

test('dashboard data is loaded from the existing Campus backend contracts', async () => {
  const requested = []
  const fetcher = async (url, options = {}) => {
    requested.push({ url, options })
    if (url.endsWith('/access'))
      return response({ allowed: false, reservation_id: null, ends_at: null })
    if (url.endsWith('/reservations'))
      return response({ data: [] })
    if (url.endsWith('/allowance'))
      return response({ remaining_yuan: '20', used_yuan: '0', total_yuan: '20', model_calls_enabled: true, by_model: [] })
    throw new Error(`Unexpected request: ${url}`)
  }
  const api = new CampusApi(fetcher)

  const result = await api.dashboard()

  assert.equal(result.access.allowed, false)
  assert.deepEqual(result.reservations, [])
  assert.equal(result.allowance.remaining_yuan, '20')
  assert.ok(requested.every(({ options }) => options.credentials === 'same-origin'))
})

test('API failures expose a safe normalized message without leaking response bodies', async () => {
  const api = new CampusApi(async () => response({ message: 'internal database detail' }, { status: 500 }))

  await assert.rejects(
    api.dashboard(),
    error => error instanceof CampusApiError && error.status === 500 && error.code === 'unavailable',
  )
})

test('current-slot load rejection has a dedicated safe error code', async () => {
  const api = new CampusApi(async () => response({ message: 'private load detail' }, { status: 429 }))

  await assert.rejects(
    api.reserve('2026-08-12T02:00:00+08:00'),
    error => error instanceof CampusApiError && error.status === 429 && error.code === 'busy',
  )
})
