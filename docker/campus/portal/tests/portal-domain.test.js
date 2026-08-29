import assert from 'node:assert/strict'
import test from 'node:test'

import {
  addCampusDays,
  campusDay,
  canCancelReservation,
  createCampusClock,
  detectPromotions,
  formatCountdown,
  isCurrentAccessSlot,
  launchWorkspace,
  reservationTiming,
  visibleSlots,
} from '../assets/portal-domain.js'

test('booking window follows UTC+8 even when the instant is still the previous UTC day', () => {
  const now = new Date('2026-08-11T16:30:00Z')

  assert.equal(campusDay(now), '2026-08-12')
  assert.equal(addCampusDays(now, 6), '2026-08-18')
})

test('an unfinished reservation can be cancelled until its slot ends', () => {
  const now = new Date('2026-08-12T02:30:00Z')
  const slot = { starts_at: '2026-08-12T02:00:00Z', ends_at: '2026-08-12T04:00:00Z' }

  assert.equal(canCancelReservation({ status: 'confirmed', ...slot }, now), true)
  assert.equal(canCancelReservation({ status: 'waitlisted', ...slot }, now), true)
  assert.equal(canCancelReservation({ status: 'completed', ...slot }, now), false)
  assert.equal(canCancelReservation({ status: 'confirmed', ...slot }, new Date('2026-08-12T04:00:00Z')), false)
})

test('current access slot is active only before its fixed end', () => {
  const slot = { starts_at: '2026-08-12T02:00:00Z', ends_at: '2026-08-12T04:00:00Z' }

  assert.equal(isCurrentAccessSlot(slot, new Date('2026-08-12T02:30:00Z')), true)
  assert.equal(isCurrentAccessSlot(slot, new Date('2026-08-12T01:59:59Z')), false)
  assert.equal(isCurrentAccessSlot(slot, new Date('2026-08-12T04:00:00Z')), false)
})

test('campus clock corrects a skewed browser clock toward the server instant', () => {
  const clientNowMs = Date.parse('2026-08-12T02:10:00Z')
  const clock = createCampusClock('2026-08-12T02:00:00Z', clientNowMs)

  assert.equal(clock(clientNowMs).toISOString(), '2026-08-12T02:00:00.000Z')
  assert.equal(clock(clientNowMs + 60_000).toISOString(), '2026-08-12T02:01:00.000Z')
})

test('countdown renders minutes first and switches to seconds inside the final minute', () => {
  assert.equal(formatCountdown(2 * 3_600_000 + 5 * 60_000), '2 小时 5 分钟')
  assert.equal(formatCountdown(9 * 60_000 + 30_000), '10 分钟')
  assert.equal(formatCountdown(59_000), '59 秒')
  assert.equal(formatCountdown(0), '0 秒')
})

test('reservation timing reports the wait before start and the remainder during the slot', () => {
  const reservation = { starts_at: '2026-08-12T02:00:00Z', ends_at: '2026-08-12T04:00:00Z' }

  assert.deepEqual(reservationTiming(reservation, new Date('2026-08-12T01:30:00Z')), {
    phase: 'before',
    remainingMs: 30 * 60_000,
  })
  assert.deepEqual(reservationTiming(reservation, new Date('2026-08-12T03:00:00Z')), {
    phase: 'during',
    remainingMs: 60 * 60_000,
  })
  assert.equal(reservationTiming(reservation, new Date('2026-08-12T04:00:00Z')), null)
})

test('a waitlisted reservation confirmed since the previous refresh is detected as a promotion', () => {
  const previous = [
    { id: 'a', status: 'waitlisted' },
    { id: 'b', status: 'confirmed' },
  ]
  const current = [
    { id: 'a', status: 'confirmed' },
    { id: 'b', status: 'confirmed' },
  ]

  assert.deepEqual(detectPromotions(previous, current).map(item => item.id), ['a'])
  assert.deepEqual(detectPromotions([], current), [])
})

test('ended slots are hidden from the booking list', () => {
  const slots = [
    { starts_at: '2026-08-12T00:00:00Z', ends_at: '2026-08-12T02:00:00Z' },
    { starts_at: '2026-08-12T02:00:00Z', ends_at: '2026-08-12T04:00:00Z' },
  ]

  assert.deepEqual(visibleSlots(slots, new Date('2026-08-12T02:30:00Z')), [slots[1]])
  assert.deepEqual(visibleSlots(slots, new Date('2026-08-12T04:00:00Z')), [])
})

test('successful session launch enters the guarded Dify apps page', async () => {
  const events = []

  await launchWorkspace(
    async () => events.push('session-issued'),
    path => events.push(`navigate:${path}`),
  )

  assert.deepEqual(events, ['session-issued', 'navigate:/apps'])
})
