import { create, fromBinary, fromJsonString, toBinary, toJsonString } from '@bufbuild/protobuf'
import { CheckKind, type WireCheck, WireCheckSchema } from '../gen/s3t/wire/v1/check_pb'

/** A message with the awkward values: an int64 beyond 2^53, NaN, infinities, an unset
 * optional field, non-ASCII text. */
export function sample(): WireCheck {
  return create(WireCheckSchema, {
    timeUs: -9007199254740993n,
    value: Number.NaN,
    column: [0, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, 5e-324],
    text: 'Δ öäü €',
    kind: CheckKind.SECOND,
  })
}

/** Field-by-field equality with NaN equal to NaN and the presence of `maybe` compared. */
export function sameWireCheck(a: WireCheck, b: WireCheck): boolean {
  const same = (x: number, y: number) => Object.is(x, y)
  return (
    a.timeUs === b.timeUs &&
    same(a.value, b.value) &&
    a.maybe === b.maybe &&
    a.column.length === b.column.length &&
    a.column.every((x, i) => same(x, b.column[i])) &&
    a.text === b.text &&
    a.kind === b.kind
  )
}

/** Binary and JSON round trip in the running engine; "wire ok" or what failed. */
export function roundTrip(): string {
  const m = sample()
  const binary = fromBinary(WireCheckSchema, toBinary(WireCheckSchema, m))
  if (!sameWireCheck(m, binary)) return 'wire mismatch: binary'
  const json = fromJsonString(WireCheckSchema, toJsonString(WireCheckSchema, m))
  if (!sameWireCheck(m, json)) return 'wire mismatch: json'
  return 'wire ok'
}
