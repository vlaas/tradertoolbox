// The live rule (UI-05): received at live_since or later, on an open connection, or not live.
import { describe, expect, it } from 'vitest'
import { isLive, NOTHING_LIVE, rowState } from './live'

const open = { open: true, liveSinceUs: 1000 }

describe('a value', () => {
  it('is live when received at live_since or later on an open connection', () => {
    expect(isLive(1000, open)).toBe(true)
    expect(isLive(5000, open)).toBe(true)
    expect(isLive(999, open)).toBe(false)
  })

  it('is not live without a connection, whatever the last status said', () => {
    expect(isLive(5000, { ...open, open: false })).toBe(false)
    expect(isLive(5000, NOTHING_LIVE)).toBe(false)
  })

  it('is not live when the status has no live_since, or the value was never received', () => {
    expect(isLive(5000, { open: true, liveSinceUs: null })).toBe(false)
    expect(isLive(null, open)).toBe(false)
  })
})

describe('a row', () => {
  it('is live when every received value is, stale when one is not, none without values', () => {
    expect(rowState([2000, 3000], open)).toBe('live')
    expect(rowState([2000, null], open)).toBe('live') // an index has no quote
    expect(rowState([2000, 999], open)).toBe('stale')
    expect(rowState([null, null], open)).toBe('none')
    expect(rowState([2000], NOTHING_LIVE)).toBe('stale')
  })
})
