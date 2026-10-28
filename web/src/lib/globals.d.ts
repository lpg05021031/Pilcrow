// Browser surfaces the app relies on that the DOM typings do not (yet) describe, and the bridge the
// desktop shell puts on the window.

/** What the Electron preload (or the Windows/GTK host) exposes to the page; absent in a plain browser. */
export interface PilcrowNative {
  shell: string;
  pickFolder?: (initial?: string) => Promise<string | null>;
  focus?: () => void;
  attention?: () => void;
  onMenu?: (cb: (command: string) => void) => () => void;
}

declare global {
  interface Window {
    pilcrowNative?: PilcrowNative;
    /** For screenshots and quick checks from the console. */
    __pilcrowToast?: (text: string, level?: 'info' | 'success' | 'warning' | 'error') => void;
  }
  interface Navigator {
    userAgentData?: { platform?: string };
  }
  interface Document {
    /** Page Visibility in older WebKit. */
    webkitHidden?: boolean;
  }
}
