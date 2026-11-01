import { createRoot } from 'react-dom/client';
import './styles/fonts.css';
import './styles/tokens.css';
import './styles/base.css';
import './styles/app.css';
import './styles/transcript.css';
import './styles/panels.css';
import './styles/dispatch.css';
import './styles/motion.css';
import { App } from './components/App';
import { initApp, applyTheme } from './lib/store';
import { installTooltips } from './lib/tooltips';

// Keys typed while an input method is composing (注音, 倉頡, 拼音…) belong to the IME: Enter picks a
// candidate and Esc cancels it. Stop them here, before any shortcut or form handler can act on them.
window.addEventListener(
  'keydown',
  (e) => {
    if (e.isComposing || e.keyCode === 229) e.stopImmediatePropagation();
  },
  true,
);

// Windows draws text with ClearType and no subpixel smoothing on light weights; nudge the UI face
// a little heavier and the smallest sizes a hair larger there so it renders as crisply as on macOS.
try {
  const p = (navigator.userAgentData?.platform || navigator.platform || '').toLowerCase();
  document.documentElement.dataset.os = p.startsWith('win') ? 'win' : p.startsWith('mac') ? 'mac' : 'linux';
} catch {
  /* ignore */
}

applyTheme();
installTooltips();
initApp();
createRoot(document.getElementById('root')!).render(<App />);
