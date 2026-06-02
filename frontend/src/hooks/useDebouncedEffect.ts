import { useEffect } from 'react';

export function useDebouncedEffect(
  effect: () => void | (() => void),
  delayMs: number,
  dependencies: React.DependencyList
) {
  useEffect(() => {
    const timer = window.setTimeout(() => {
      effect();
    }, delayMs);

    return () => {
      window.clearTimeout(timer);
    };
  }, dependencies); // eslint-disable-line react-hooks/exhaustive-deps
}
