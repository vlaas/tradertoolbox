// The address of a screen (UI-01 base).
import { describe, expect, it } from 'vitest'
import { hashOf, screenOf } from './route'

const known = new Set(['status', 'workbench'])

describe('the address', () => {
  it('names a known screen', () => {
    expect(screenOf('#/status', known)).toBe('status')
    expect(screenOf('#/workbench/SPY', known)).toBe('workbench')
    expect(screenOf('#/status?x=1', known)).toBe('status')
    expect(screenOf('#status', known)).toBe('status')
  })

  it('names none when it is empty or unknown', () => {
    expect(screenOf('', known)).toBeNull()
    expect(screenOf('#/', known)).toBeNull()
    expect(screenOf('#/nosuch', known)).toBeNull()
    expect(screenOf('#/login', known)).toBeNull() // the login is no address
  })

  it('is written back the same way', () => {
    expect(hashOf('status')).toBe('#/status')
    expect(screenOf(hashOf('workbench'), known)).toBe('workbench')
  })
})
