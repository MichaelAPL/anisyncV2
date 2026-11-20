import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => {
  cleanup()
  scrolled.length = 0
  localStorage.clear()
  vi.unstubAllGlobals()
})

// jsdom has no matchMedia; phone layout is off and the OS scheme is light.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }),
})

// jsdom doesn't implement scrolling; record the calls so tests can check what was brought into view.
export const scrolled: Element[] = []
Element.prototype.scrollIntoView = function (this: Element) {
  scrolled.push(this)
}
