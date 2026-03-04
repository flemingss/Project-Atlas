/**
 * useMobile — Responsive breakpoint hook.
 *
 * Returns true when the viewport is below the mobile breakpoint (default 768px).
 * Uses matchMedia for efficient, event-driven updates.
 */
import { useEffect, useState } from 'react';

const MOBILE_BREAKPOINT = 768;

export function useMobile(breakpoint = MOBILE_BREAKPOINT): boolean {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${breakpoint - 1}px)`);
    const onChange = () => setIsMobile(mql.matches);

    // Set initial value
    setIsMobile(mql.matches);

    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [breakpoint]);

  return isMobile;
}
