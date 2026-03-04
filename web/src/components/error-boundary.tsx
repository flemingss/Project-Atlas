/**
 * ErrorBoundary — catches React render crashes and shows a recovery UI
 * instead of a blank page.  Also logs the error to the console with
 * component-stack information so issues surface immediately during dev.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface Props {
  /** Fallback shown while the boundary is in error state. If omitted, the default crash card is used. */
  fallback?: ReactNode;
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    this.setState({ errorInfo });

    // Surface the crash prominently in the dev console
    console.group('%c🔥 React Error Boundary caught a crash', 'color:#ef4444;font-weight:bold');
    console.error(error);
    if (errorInfo?.componentStack) {
      console.log('%cComponent stack:', 'color:#f59e0b;font-weight:bold');
      console.log(errorInfo.componentStack);
    }
    console.groupEnd();
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;

      const { error, errorInfo } = this.state;

      return (
        <div className="flex flex-1 items-center justify-center p-8">
          <div className="w-full max-w-lg space-y-4 rounded-lg border border-state-error/30 bg-state-error/5 p-6">
            <div className="flex items-center gap-3">
              <AlertTriangle className="size-6 text-state-error" />
              <h2 className="text-lg font-semibold text-text-primary">Something went wrong</h2>
            </div>

            <p className="text-sm text-text-secondary">
              This page encountered an unexpected error. You can try recovering below, or refresh the browser.
            </p>

            {error && (
              <details className="rounded-md border border-border bg-bg-base p-3" open>
                <summary className="cursor-pointer text-xs font-medium text-state-error">
                  {error.name}: {error.message}
                </summary>
                <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-[11px] text-text-muted">
                  {error.stack}
                </pre>
                {errorInfo?.componentStack && (
                  <>
                    <p className="mt-2 text-[11px] font-medium text-text-secondary">Component stack:</p>
                    <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] text-text-muted">
                      {errorInfo.componentStack}
                    </pre>
                  </>
                )}
              </details>
            )}

            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={this.handleReset}>
                <RefreshCw className="mr-1.5 size-3.5" />
                Try again
              </Button>
              <Button variant="outline" size="sm" onClick={() => window.location.reload()}>
                Reload page
              </Button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
