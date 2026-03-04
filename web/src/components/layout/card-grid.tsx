/**
 * CardGrid — Responsive grid container for card layouts.
 *
 * Automatically adapts column count based on available width.
 * Matches the pattern used in RAGFlow's CardContainer.
 *
 * Usage:
 *   <CardGrid>
 *     {items.map(item => <Card key={item.id}>…</Card>)}
 *   </CardGrid>
 */
import * as React from 'react';
import { cn } from '@/lib/utils';

interface CardGridProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Minimum card width for auto-fit columns */
  minCardWidth?: string;
  /** Gap between cards */
  gap?: string;
}

export function CardGrid({
  minCardWidth = '280px',
  gap = '1rem',
  className,
  style,
  children,
  ...props
}: CardGridProps) {
  return (
    <div
      className={cn('grid w-full', className)}
      style={{
        gridTemplateColumns: `repeat(auto-fill, minmax(${minCardWidth}, 1fr))`,
        gap,
        ...style,
      }}
      {...props}
    >
      {children}
    </div>
  );
}
