/** The screens and their addresses (UI-01 base). Each is `#/<id>`. Only the screens that exist
 * are listed; the workbench and the chart come with R1 and R2. */
import type { Component } from 'svelte'
import Status from './Status.svelte'

export interface Screen {
  id: string
  title: string
  component: Component
}

export const SCREENS: readonly Screen[] = [{ id: 'status', title: 'Status', component: Status }]

export const DEFAULT_SCREEN = 'status'
