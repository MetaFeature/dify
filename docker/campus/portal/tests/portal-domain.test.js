import assert from 'node:assert/strict'
import test from 'node:test'

import {
  addCampusDays,
  campusDay,
  canCancelReservation,
  isCurrentAccessSlot,
  launchWorkspace,
} from '../assets/portal-domain.js'

test('booking window follows UTC+8 even when the instant is still the previous UTC day', () => {
  const now = new Date('2026-08-11T16:30:00Z')

  assert.equal(campusDay(now), '2026-08-12')
  assert.equal(addCampusDays(now, 6), '2026-08-18')
})

test('only an unfinished reservation before its start can be cancelled', () => {
  const now = new Date('2026-08-12T02:00:00Z')

  assert.equal(canCancelReservation({ status: 'confirmed', starts_at: '2026-08-12T02:01:00Z' }, now), true)
  assert.equal(canCancelReservation({ status: 'waitlisted', starts_at: '2026-08-12T02:00:00Z' }, now), false)
  assert.equal(canCancelReservation({ status: 'completed', starts_at: '2026-08-12T03:00:00Z' }, now), false)
})

test('current access slot is active only before its fixed end', () => {
  const slot = { starts_at: '2026-08-12T02:00:00Z', ends_at: '2026-08-12T04:00:00Z' }

  assert.equal(isCurrentAccessSlot(slot, new Date('2026-08-12T02:30:00Z')), true)
  assert.equal(isCurrentAccessSlot(slot, new Date('2026-08-12T01:59:59Z')), false)
  assert.equal(isCurrentAccessSlot(slot, new Date('2026-08-12T04:00:00Z')), false)
})

test('successful session launch enters the guarded Dify apps page', async () => {
  const events = []

  await launchWorkspace(
    async () => events.push('session-issued'),
    path => events.push(`navigate:${path}`),
  )

  assert.deepEqual(events, ['session-issued', 'navigate:/apps'])
})
