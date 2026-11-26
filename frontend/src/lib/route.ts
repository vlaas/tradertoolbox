/** The address of a screen (UI-01 base): `#/<screen>`. The login is no address: it stands in for
 * the screen of the address until there is a session, so that the address survives it. */

/** The screen an address names, or null when it names none of `known`. */
export function screenOf(hash: string, known: ReadonlySet<string>): string | null {
  const id = hash.replace(/^#\/?/, '').split(/[/?]/, 1)[0]
  return known.has(id) ? id : null
}

export function hashOf(screen: string): string {
  return `#/${screen}`
}
