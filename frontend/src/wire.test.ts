// The cross-language check of the Protocol Buffers tool chain (API-04, QA-05): the fixture was
// written by the Python code (backend/tools/generate.py); this test decodes it with the
// generated TypeScript code against values stated here by hand, re-encodes it and compares
// bytes and canonical JSON.
import { create, fromBinary, fromJson, type JsonValue, toBinary, toJson } from '@bufbuild/protobuf'
import { describe, expect, it } from 'vitest'
import fixture from '../../proto/fixtures/wirecheck.json'
import { CheckKind, WireCheckSchema } from './gen/s3t/wire/v1/check_pb'
import { roundTrip, sameWireCheck } from './lib/wirecheck'

const expected = create(WireCheckSchema, {
  timeUs: -(2n ** 53n) - 1n,
  value: Number.NaN,
  column: [0, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, 5e-324],
  text: 'Δ öäü €',
  kind: CheckKind.SECOND,
})

function fromHex(hex: string): Uint8Array {
  return Uint8Array.from(hex.match(/../g) ?? [], (b) => Number.parseInt(b, 16))
}

function toHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

describe('WireCheck fixture written by Python', () => {
  it('decodes to the stated values', () => {
    const decoded = fromBinary(WireCheckSchema, fromHex(fixture.binary_hex))
    expect(sameWireCheck(decoded, expected)).toBe(true)
    expect(decoded.maybe).toBeUndefined()
    expect(decoded.timeUs).toBe(-9007199254740993n)
  })

  it('encodes to the same bytes', () => {
    expect(toHex(toBinary(WireCheckSchema, expected))).toBe(fixture.binary_hex)
  })

  it('has the same canonical JSON', () => {
    expect(toJson(WireCheckSchema, expected)).toEqual(fixture.json)
    const parsed = fromJson(WireCheckSchema, fixture.json as JsonValue)
    expect(sameWireCheck(parsed, expected)).toBe(true)
  })
})

describe('WireCheck in this engine', () => {
  it('survives a binary and a JSON round trip', () => {
    expect(roundTrip()).toBe('wire ok')
  })

  it('keeps the presence of an optional field', () => {
    const zero = create(WireCheckSchema, { maybe: 0 })
    expect(fromBinary(WireCheckSchema, toBinary(WireCheckSchema, zero)).maybe).toBe(0)
    expect(fromBinary(WireCheckSchema, toBinary(WireCheckSchema, expected)).maybe).toBeUndefined()
  })
})
