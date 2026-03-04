/**
 * AuthGate — shown on pages that require admin authentication.
 * Provides a friendly message and links to the connection settings.
 */
import { Lock, Settings } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface AuthGateProps {
  message?: string;
}

export function AuthGate({
  message = 'This page requires admin access. Enter your admin token in the sidebar to continue.',
}: AuthGateProps) {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="max-w-md space-y-4 text-center">
        <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-bg-card">
          <Lock className="size-8 text-text-muted" />
        </div>
        <h2 className="text-lg font-semibold text-text-primary">Admin Access Required</h2>
        <p className="text-sm text-text-secondary">{message}</p>
        <div className="flex justify-center">
          <Button variant="outline" size="sm" className="gap-1.5">
            <Settings className="size-3.5" />
            Open connection settings
          </Button>
        </div>
      </div>
    </div>
  );
}
