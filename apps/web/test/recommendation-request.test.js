import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatElapsedDuration,
  requestedResultCount,
} from '../src/services/recommendation-request.js'

test('defaults an unspecified recommendation request to three results', () => {
  assert.equal(requestedResultCount('我明天去看中国古画展，怎么穿'), 3)
})

test('preserves an explicitly requested result count', () => {
  assert.equal(requestedResultCount('只要一套通勤搭配'), 1)
  assert.equal(requestedResultCount('给我两套方案'), 2)
  assert.equal(requestedResultCount('给我 3 套不同搭配'), 3)
  assert.equal(requestedResultCount('给我几套选择'), 3)
})

test('formats completed request duration for the chat result', () => {
  assert.equal(formatElapsedDuration(240), '< 1 秒')
  assert.equal(formatElapsedDuration(12_345), '12.3 秒')
  assert.equal(formatElapsedDuration(68_900), '1 分 8 秒')
  assert.equal(formatElapsedDuration(undefined), '')
})
