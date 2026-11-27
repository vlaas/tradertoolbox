// Which time the screens show (UI-12): exchange time by default, local, UTC or a named zone.
import { describe, expect, it } from 'vitest'
import { dateTime, hm, time } from './format'
import { choiceOf, isZone, localZone, MARKET_ZONE, zoneName, zoneOf } from './zones'

const T = Date.UTC(2026, 10, 20, 15, 0, 0) * 1000 // 10:00 New York, 09:00 Chicago, 17:00 Tallinn

describe('the choice', () => {
  it('resolves to the exchange zone of the instrument by default', () => {
    expect(zoneOf('exchange')).toBe(MARKET_ZONE)
    expect(zoneOf('exchange', 'America/Chicago')).toBe('America/Chicago')
  })

  it('resolves to local time, UTC, or a zone by its name', () => {
    expect(zoneOf('local', 'America/Chicago')).toBe(localZone())
    expect(zoneOf('UTC', 'America/Chicago')).toBe('UTC')
    expect(zoneOf('Europe/Tallinn')).toBe('Europe/Tallinn')
  })

  it('falls back to exchange time for a saved name that is no zone', () => {
    expect(isZone('Europe/Tallinn')).toBe(true)
    expect(isZone('Mars/Base')).toBe(false)
    expect(isZone('')).toBe(false)
    expect(choiceOf('Mars/Base')).toBe('exchange')
    expect(choiceOf(null)).toBe('exchange')
    expect(choiceOf('local')).toBe('local')
    expect(choiceOf('UTC')).toBe('UTC')
    expect(zoneOf('Mars/Base')).toBe(MARKET_ZONE)
  })
})

describe('a time', () => {
  it('is written in the zone, and says which', () => {
    expect(time(T)).toBe('10:00:00')
    expect(time(T, 'America/Chicago')).toBe('09:00:00')
    expect(hm(T, 'UTC')).toBe('15:00')
    expect(dateTime(T, 'UTC')).toBe('2026-11-20 15:00:00 UTC')
    expect(dateTime(T, 'America/Chicago')).toBe('2026-11-20 09:00:00 CT')
    expect(dateTime(T, 'Europe/Tallinn')).toMatch(/^2026-11-20 17:00:00 (EET|GMT\+2)$/)
  })

  it('names the two exchange zones ET and CT, whatever the season', () => {
    expect(zoneName('America/New_York', new Date(T))).toBe('ET')
    expect(zoneName('America/Chicago', new Date(Date.UTC(2026, 6, 1)))).toBe('CT')
    expect(zoneName('UTC')).toBe('UTC')
  })
})
