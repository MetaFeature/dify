import assert from 'node:assert/strict'
import test from 'node:test'

import { addCampusDays, campusDay, canCancelReservation } from '../assets/portal-domain.js'

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
