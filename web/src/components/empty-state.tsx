/**
 * EmptyState — Consistent empty/no-data placeholder for pages and panels.
 *
 * Usage:
 *   <EmptyState
 *     icon={FileText}
 *     title="No documents"
 *     description="Upload a PDF to get started."
 *     action={<Button>Upload</Button>}
 *   />
 */
import { type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

interface EmptyStateProps {
  /** Icon component from lucide-react */
  icon?: LucideIcon;
  /** Primary heading */
  title: string;
  /** Supporting text */
  description?: string;
  /** Action button or link */
  action?: React.ReactNode;
  /** Extra className */
  className?: string;
}

export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-1 flex-col items-center justify-center gap-4 py-16', className)}>
      {Icon && (
        <div className="rounded-full bg-bg-surface p-4">
          <Icon className="size-8 text-text-muted" />
        </div>
      )}
      <div className="text-center">
        <h3 className="text-sm font-medium text-text-primary">{title}</h3>
        {description && <p className="mt-1 text-sm text-text-secondary">{description}</p>}
      </div>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
