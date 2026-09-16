import type { ReactNode } from 'react'
import type { Mock } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { usePathname } from '@/next/navigation'
import { CONSOLE_RAIL_ID, ConsoleRail } from '../console-rail'

// The console rail is a column on a wide viewport and an overlay drawer below
// `lg`. jsdom has no layout, so the viewport is stubbed and the assertions are
// about the state the component puts into the DOM: which attributes the rail
// carries, and whether the trigger and the backdrop are wired to them.
const COMPACT_CONSOLE_QUERY = '(max-width: 1023px)'

let isCompactConsole = false

function stubViewport({ compact }: { compact: boolean }) {
  isCompactConsole = compact
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches: query === COMPACT_CONSOLE_QUERY && isCompactConsole,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
}

vi.mock('@/next/navigation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/next/navigation')>()
  return {
    ...actual,
    usePathname: vi.fn(),
  }
})

const renderRail = (rail: ReactNode = <span>rail content</span>) =>
  render(<ConsoleRail rail={rail} title="Campus" />)

const rail = () => document.getElementById(CONSOLE_RAIL_ID)

describe('ConsoleRail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ;(usePathname as Mock).mockReturnValue('/apps')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('keeps the rail off-screen and unfocusable while the drawer is closed', () => {
    stubViewport({ compact: true })

    renderRail()

    expect(screen.getByRole('button', { name: /navigation\.toggleMainNavigation/ })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(screen.getByRole('button', { name: /navigation\.toggleMainNavigation/ })).toHaveAttribute(
      'aria-controls',
      CONSOLE_RAIL_ID,
    )
    expect(rail()).toHaveClass('-translate-x-full')
    expect(rail()).toHaveAttribute('inert')
  })

  it('opens the drawer from the trigger and stops at nothing else', () => {
    stubViewport({ compact: true })

    renderRail()

    fireEvent.click(screen.getByRole('button', { name: /navigation\.toggleMainNavigation/ }))

    expect(screen.getByRole('button', { name: /navigation\.closeMainNavigation/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(rail()).toHaveClass('translate-x-0')
    expect(rail()).not.toHaveAttribute('inert')
    expect(screen.getByText('rail content')).toBeInTheDocument()
  })

  it('closes the drawer when the backdrop is clicked', () => {
    stubViewport({ compact: true })

    const { container } = renderRail()
    fireEvent.click(screen.getByRole('button', { name: /navigation\.toggleMainNavigation/ }))

    const backdrop = container.querySelector('.bg-background-overlay')

    expect(backdrop).not.toBeNull()
    fireEvent.click(backdrop as Element)

    expect(rail()).toHaveAttribute('inert')
  })

  it('closes the drawer on Escape', () => {
    stubViewport({ compact: true })

    renderRail()
    fireEvent.click(screen.getByRole('button', { name: /navigation\.toggleMainNavigation/ }))
    fireEvent.keyDown(window, { key: 'Escape' })

    expect(rail()).toHaveAttribute('inert')
  })

  it('closes the drawer after a link navigates', () => {
    stubViewport({ compact: true })

    const { rerender } = renderRail()
    fireEvent.click(screen.getByRole('button', { name: /navigation\.toggleMainNavigation/ }))

    expect(rail()).not.toHaveAttribute('inert')

    ;(usePathname as Mock).mockReturnValue('/datasets')
    rerender(<ConsoleRail rail={<span>rail content</span>} title="Campus" />)

    expect(rail()).toHaveAttribute('inert')
  })

  it('leaves the rail as a plain column on a wide viewport', () => {
    stubViewport({ compact: false })

    renderRail()

    expect(rail()).toHaveClass('lg:static')
    expect(rail()).not.toHaveAttribute('inert')
    expect(document.querySelector('.bg-background-overlay')).toBeNull()
  })
})
