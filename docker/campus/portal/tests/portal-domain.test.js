import assert from 'node:assert/strict'
import test from 'node:test'

import { addCampusDays, campusDay, canCancelReservation, createCampusClock, detectPromotions, experimentTrackCards, formatCountdown, isCurrentAccessSlot, launchWorkspace, paginateReservations, reservationPageSize, reservationTiming, visibleSlots } from '../assets/portal-domain.js'

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

test('reservation history page length adapts to the viewport without expanding unbounded', () => {
  assert.equal(reservationPageSize(720), 2)
  assert.equal(reservationPageSize(1080), 3)
  assert.equal(reservationPageSize(1293), 4)
  assert.equal(reservationPageSize(1600), 6)
})

test('reservation history exposes one bounded page and clamps a stale page after refresh', () => {
  const reservations = Array.from({ length: 7 }, (_, index) => ({ id: `reservation-${index + 1}` }))

  assert.deepEqual(paginateReservations(reservations, 1, 1080), {
    items: reservations.slice(3, 6),
    page: 1,
    pageCount: 3,
    pageSize: 3,
  })
  assert.deepEqual(paginateReservations(reservations.slice(0, 2), 2, 1080), {
    items: reservations.slice(0, 2),
    page: 0,
    pageCount: 1,
    pageSize: 3,
  })
})

test('successful session launch enters the guarded Dify apps page', async () => {
  const events = []

  await launchWorkspace(
    async () => events.push('session-issued'),
    path => events.push(`navigate:${path}`),
  )

  assert.deepEqual(events, ['session-issued', 'navigate:/apps'])
})

test('experiment tracks are presented with the platform label and the right destination', () => {
  const cards = experimentTrackCards([
    { track: 'large-model', kind: 'dify', chapters: 0 },
    { track: 'deep-learning', kind: 'manual', chapters: 3 },
    { track: 'agent', kind: 'manual', chapters: 0 },
  ])

  assert.deepEqual(cards, [
    {
      track: 'large-model',
      title: '大模型实验',
      detail: '在 Dify 工作区完成，需预约时段',
      chapters: [],
      manualAvailable: false,
      reservationsAvailable: true,
    },
    {
      track: 'deep-learning',
      title: '深度学习实验',
      detail: '在自己的电脑上完成 · 3 份课件',
      chapters: [],
      manualAvailable: true,
      reservationsAvailable: false,
    },
    {
      track: 'agent',
      title: '智能体实验',
      detail: '在自己的电脑上完成',
      chapters: [],
      manualAvailable: false,
      reservationsAvailable: false,
    },
  ])
})

test('an unknown track is dropped rather than shown without a name', () => {
  // The backend enum is the authority; a card with no label would be a dead end.
  assert.deepEqual(experimentTrackCards([{ track: 'quantum', kind: 'manual', chapters: 1 }]), [])
})

test('a manual with no published chapter is not presented as ready', () => {
  const [card] = experimentTrackCards([{ track: 'deep-learning', kind: 'manual', chapters: 0 }])

  assert.equal(card.manualAvailable, false)
})

test('published presentation controls labels and experiment order', () => {
  const cards = experimentTrackCards(
    [
      { track: 'large-model', kind: 'dify', chapters: 1 },
      { track: 'deep-learning', kind: 'manual', chapters: 1 },
      { track: 'agent', kind: 'manual', chapters: 1 },
    ],
    [
      { track: 'large-model', title: '实验一', description: '平台完成', position: 1 },
      { track: 'agent', title: '实验二', description: '本机完成', position: 2 },
      { track: 'deep-learning', title: '实验三', description: '本机完成', position: 3 },
    ],
  )

  assert.deepEqual(cards.map(card => card.title), ['实验一', '实验二', '实验三'])
})

test('the administrator chapter list reaches the card in its own order', () => {
  // The chooser renders exactly what the backend published, so the titles and
  // their reading order are the administrator's, not a client-side guess.
  const [card] = experimentTrackCards([
    {
      track: 'reference',
      kind: 'manual',
      chapters: 2,
      chapter_list: [
        { id: 'c1', title: '课程大纲', view_url: 'http://manual:18083/c1/view' },
        { id: 'c2', title: '参考文献', view_url: 'http://manual:18083/c2/view' },
      ],
    },
  ])

  assert.equal(card.title, '参考资料')
  assert.equal(card.manualAvailable, true)
  assert.deepEqual(card.chapters.map(chapter => chapter.title), ['课程大纲', '参考文献'])
  assert.equal(card.chapters[0].view_url, 'http://manual:18083/c1/view')
})

test('a track without a chapter list still reports its published count', () => {
  const [card] = experimentTrackCards([{ track: 'agent', kind: 'manual', chapters: 4 }])

  assert.equal(card.manualAvailable, true)
  assert.deepEqual(card.chapters, [])
})
