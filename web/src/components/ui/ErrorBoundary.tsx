import { Component, type ErrorInfo, type ReactNode } from 'react';
import { CircleAlert, RotateCcw } from 'lucide-react';

/**
 * A fence around one part of the page. React unmounts the whole tree when a render throws; a
 * boundary keeps the failure to the row, card or panel it happened in, says so in place, and offers
 * to try that part again. The rest of the window keeps working.
 */
export class ErrorBoundary extends Component<{ children: ReactNode; label?: string; className?: string; fallback?: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[pilcrow] ${this.props.label || 'a part of the page'} failed to render`, error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback !== undefined) return this.props.fallback;
    return (
      <div className={`render-fault ${this.props.className || ''}`} role="alert">
        <CircleAlert size={14} aria-hidden="true" />
        <span className="render-fault-text">
          {this.props.label ? `${this.props.label} could not be drawn` : 'This part could not be drawn'}
          {error.message ? <span className="render-fault-why"> · {error.message}</span> : null}
        </span>
        <button className="link render-fault-retry" onClick={() => this.setState({ error: null })}>
          <RotateCcw size={12} aria-hidden="true" /> Try again
        </button>
      </div>
    );
  }
}
