/**
 * PanelLayout — Resizable split layout for tool pages (e.g. editor, ingest wizard).
 *
 * Provides a standard two-panel or three-panel layout using react-resizable-panels.
 * Panels auto-collapse at their minimum size and can be toggled via keyboard shortcut.
 *
 * Usage:
 *   <PanelLayout
 *     left={<SidebarContent />}
 *     leftSize={{ default: 25, min: 15, max: 40 }}
 *   >
 *     <MainContent />
 *   </PanelLayout>
 */
import * as React from 'react';
import { cn } from '@/lib/utils';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from '@/components/ui/resizable';

interface PanelSize {
  default: number;
  min?: number;
  max?: number;
}

interface PanelLayoutProps {
  /** Left panel content (sidebar, nav, etc.) */
  left?: React.ReactNode;
  /** Left panel sizing in percentage */
  leftSize?: PanelSize;
  /** Right panel content (properties, preview, etc.) */
  right?: React.ReactNode;
  /** Right panel sizing in percentage */
  rightSize?: PanelSize;
  /** Direction of the split */
  direction?: 'horizontal' | 'vertical';
  /** Show drag handle dots */
  withHandle?: boolean;
  /** Extra class on the outer container */
  className?: string;
  /** Main (center) content */
  children: React.ReactNode;
}

export function PanelLayout({
  left,
  leftSize = { default: 25, min: 15, max: 40 },
  right,
  rightSize = { default: 30, min: 15, max: 50 },
  direction = 'horizontal',
  withHandle = true,
  className,
  children,
}: PanelLayoutProps) {
  return (
    <ResizablePanelGroup
      direction={direction}
      className={cn('flex-1 overflow-hidden', className)}
    >
      {/* ── Left panel ── */}
      {left && (
        <>
          <ResizablePanel
            defaultSize={leftSize.default}
            minSize={leftSize.min}
            maxSize={leftSize.max}
            collapsible
            className="flex flex-col overflow-hidden"
          >
            {left}
          </ResizablePanel>
          <ResizableHandle withHandle={withHandle} />
        </>
      )}

      {/* ── Center (main) panel ── */}
      <ResizablePanel
        defaultSize={
          100 -
          (left ? leftSize.default : 0) -
          (right ? rightSize.default : 0)
        }
        minSize={30}
        className="flex flex-col overflow-hidden"
      >
        {children}
      </ResizablePanel>

      {/* ── Right panel ── */}
      {right && (
        <>
          <ResizableHandle withHandle={withHandle} />
          <ResizablePanel
            defaultSize={rightSize.default}
            minSize={rightSize.min}
            maxSize={rightSize.max}
            collapsible
            className="flex flex-col overflow-hidden"
          >
            {right}
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  );
}
