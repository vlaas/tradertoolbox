// Numbers with fixed decimals and a dash for what is missing; instants in New York time.
import { describe, expect, it } from 'vitest'
import { count, dateTime, decimal, micros, MISSING, seconds, time } from './format'

describe('numbers', () => {
  it('have fixed decimals and groups, and a dash when missing, never zero', () => {
    expect(decimal(111.1)).toBe('111.10')
    expect(decimal(6000.25, 2)).toBe('6,000.25')
    expect(decimal(0)).toBe('0.00')
    expect(decimal(null)).toBe(MISSING)
    expect(decimal(Number.NaN)).toBe(MISSING)
    expect(count(25000)).toBe('25,000')
    expect(count(undefined)).toBe(MISSING)
  })
})

describe('instants', () => {
  it('are New York time, on both sides of the clock change', () => {
    // 2026-11-20 15:00 UTC is 10:00 EST; 2026-07-01 15:00 UTC is 11:00 EDT.
    expect(time(Date.UTC(2026, 10, 20, 15, 0, 0) * 1000)).toBe('10:00:00')
    expect(time(Date.UTC(2026, 6, 1, 15, 0, 0) * 1000)).toBe('11:00:00')
    expect(dateTime(Date.UTC(2026, 10, 21, 4, 30, 5) * 1000)).toBe('2026-11-20 23:30:05 ET')
    expect(time(Date.UTC(2026, 10, 20, 5, 0, 0) * 1000)).toBe('00:00:00') // not 24:00:00
    expect(time(null)).toBe(MISSING)
    expect(time(Number.NaN)).toBe(MISSING)
  })

  it('of the REST answers become microseconds', () => {
    expect(micros('2026-11-20T15:00:00.5Z')).toBe(Date.UTC(2026, 10, 20, 15, 0, 0, 500) * 1000)
    expect(micros(null)).toBeNull()
    expect(micros('never')).toBeNull()
  })

  it('a wait is whole seconds, at least one', () => {
    expect(seconds(2100)).toBe('3 s')
    expect(seconds(0)).toBe('1 s')
  })
})
