// Tiny split-pane resize hook for the editor's left/right divider (PRD §7.2).

import { useCallback, useEffect, useRef, useState } from 'react';

export function useSplitPane({ initial = 0.5, min = 480, minRightPx = 320 } = {}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [leftPct, setLeftPct] = useState(initial * 100);
  const draggingRef = useRef(false);
  const previousBodyTouchActionRef = useRef('');

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    previousBodyTouchActionRef.current = document.body.style.touchAction;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.body.style.touchAction = 'none';
  }, []);

  useEffect(() => {
    const resetBody = () => {
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.body.style.touchAction = previousBodyTouchActionRef.current;
    };
    const onMove = (e: PointerEvent) => {
      if (!draggingRef.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const total = rect.width;
      const leftPx = Math.max(min, Math.min(total - minRightPx, x));
      setLeftPct((leftPx / total) * 100);
    };
    const onUp = () => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      resetBody();
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
      if (draggingRef.current) {
        draggingRef.current = false;
        resetBody();
      }
    };
  }, [min, minRightPx]);

  return { containerRef, leftPct, onPointerDown };
}
