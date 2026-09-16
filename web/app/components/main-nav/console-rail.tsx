'use client'

import type { ReactNode } from 'react'
import { cn } from '@langgenius/dify-ui/cn'
import { useEffect, useState, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import { usePathname } from '@/next/navigation'

export const CONSOLE_RAIL_ID = 'console-rail'

// Below this width the rail stops being a column and becomes an overlay drawer.
//
// The console rail is a fixed 248px column with no narrow layout of its own, so
// on a 390px phone it left the page roughly 140px and every page scrolled
// sideways inside it. Upstream adapts the pages that need it individually
// (`useBreakpoints` in chat and dataset creation); the shell never was, which is
// why the whole console looked unadapted on a phone or a tablet. This query and
// the `lg:` classes below have to stay in step.
const COMPACT_CONSOLE_QUERY = '(max-width: 1023px)'

function subscribeToCompactConsole(onStoreChange: () => void) {
  const mediaQueryList = window.matchMedia(COMPACT_CONSOLE_QUERY)
  mediaQueryList.addEventListener('change', onStoreChange)
  return () => mediaQueryList.removeEventListener('change', onStoreChange)
}

const getCompactConsoleSnapshot = () => window.matchMedia(COMPACT_CONSOLE_QUERY).matches
// The server cannot know the viewport. Reporting a wide one keeps the server and
// the first client render identical: the drawer's closed state is carried by
// classes, not by attributes, so nothing flashes when a narrow viewport hydrates.
const getServerCompactConsoleSnapshot = () => false

function useIsCompactConsole() {
  return useSyncExternalStore(
    subscribeToCompactConsole,
    getCompactConsoleSnapshot,
    getServerCompactConsoleSnapshot,
  )
}

type ConsoleRailProps = {
  /** The rail itself: the workspace navigation or the detail sidebar. */
  rail: ReactNode
  /** Branding title, shown next to the drawer trigger on a compact viewport. */
  title: string
}

/**
 * Hosts the console rail: a column beside the page on a wide viewport, and an
 * overlay drawer behind a trigger in a top bar on a narrow one.
 */
export function ConsoleRail({ rail, title }: ConsoleRailProps) {
  const { t } = useTranslation()
  const pathname = usePathname()
  const isCompactConsole = useIsCompactConsole()
  const [isDrawerOpen, setIsDrawerOpen] = useState(false)

  // Reveal the page a tapped link opened instead of leaving the drawer over it.
  useEffect(() => {
    setIsDrawerOpen(false)
  }, [pathname])

  useEffect(() => {
    if (!isDrawerOpen) return

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsDrawerOpen(false)
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isDrawerOpen])

  const isDrawerHidden = isCompactConsole && !isDrawerOpen

  return (
    <>
      <div className="flex h-12 shrink-0 items-center gap-1 border-b border-divider-subtle pr-2 pl-[max(0.5rem,env(safe-area-inset-left))] lg:hidden">
        <button
          type="button"
          aria-label={t(
            ($) =>
              isDrawerOpen
                ? $['navigation.closeMainNavigation']
                : $['navigation.toggleMainNavigation'],
            { ns: 'common' },
          )}
          aria-expanded={isDrawerOpen}
          aria-controls={CONSOLE_RAIL_ID}
          onClick={() => setIsDrawerOpen((open) => !open)}
          className="flex size-8 shrink-0 items-center justify-center rounded-lg text-text-secondary outline-hidden transition-colors hover:bg-state-base-hover hover:text-text-primary focus-visible:ring-2 focus-visible:ring-state-accent-solid"
        >
          <span aria-hidden className={cn('size-5', isDrawerOpen ? 'i-ri-close-line' : 'i-ri-menu-line')} />
        </button>
        <span className="min-w-0 truncate system-md-semibold text-text-primary" title={title}>
          {title}
        </span>
      </div>
      {isCompactConsole && isDrawerOpen && (
        <div
          aria-hidden="true"
          onClick={() => setIsDrawerOpen(false)}
          className="fixed top-12 right-0 bottom-0 left-0 z-40 bg-background-overlay lg:hidden"
        />
      )}
      <div
        id={CONSOLE_RAIL_ID}
        // Off-screen content must not be reachable by keyboard. `inert` is only
        // ever set on a compact viewport, where the drawer is an overlay.
        inert={isDrawerHidden}
        className={cn(
          // Bounded below the trigger bar, so the trigger keeps working as the
          // drawer's close control instead of disappearing behind the rail.
          'fixed top-12 bottom-0 left-0 z-50 flex transition-transform duration-200 motion-reduce:transition-none',
          'lg:static lg:z-auto lg:translate-x-0',
          isDrawerHidden ? '-translate-x-full' : 'translate-x-0',
        )}
      >
        {rail}
      </div>
    </>
  )
}
