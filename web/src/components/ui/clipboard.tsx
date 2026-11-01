// The clipboard: copying text, and the copy button that confirms in place.
import { useState } from 'react';
import { Copy, Check } from 'lucide-react';

/** Copy to the clipboard. Falls back to the legacy path when the async API is unavailable or refused. */
export async function copyText(text: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    /* fall through */
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
  const prev = document.activeElement as HTMLElement | null;
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand('copy');
  ta.remove();
  prev?.focus?.();
  if (!ok) throw new Error('Copy was blocked');
}

/** Copy with a check-mark confirmation in place, instead of a toast. */
export function CopyButton({ text, title = 'Copy', size = 13 }: { text: string; title?: string; size?: number }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className={`icon-btn sm copy-btn${done ? ' done' : ''}`}
      title={done ? 'Copied' : title} aria-label={done ? 'Copied' : title}
      onClick={() =>
        copyText(text).then(
          () => {
            setDone(true);
            setTimeout(() => setDone(false), 1300);
          },
          () => {},
        )
      }
    >
      {done ? <Check size={size} /> : <Copy size={size} />}
    </button>
  );
}
