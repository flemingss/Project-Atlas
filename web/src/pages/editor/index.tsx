/**
 * Editor page — split pane with PDF viewer (left) and markdown editor (right),
 * status bar at bottom, and toolbar above the editor.
 *
 * Routes:
 *   /editor/doc/:docId   → resolve doc → get runId
 *   /editor/run/:runId   → use runId directly
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from 'react-resizable-panels';

import {
  EditorToolbar,
  MarkdownEditor,
  PdfViewer,
  StatusBar,
  type EditorHandle,
} from '@/components/editor';
import { useEditorStore } from '@/stores/editor-store';
import { editorApi } from '@/services/api';
import { toast } from 'sonner';

export function EditorPage() {
  const params = useParams<{ docId?: string; runId?: string }>();
  const editorRef = useRef<EditorHandle>(null);
  const [charCount, setCharCount] = useState(0);
  const [lineCount, setLineCount] = useState(0);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);

  const {
    setDocument,
    setStatus,
  } = useEditorStore();

  // ── Load document on mount / param change ──
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        let resolvedRunId: number;
        let docId: string;
        let filename: string;
        let docVersion: string;
        let totalPages: number;

        if (params.runId) {
          // Direct run ID route
          resolvedRunId = parseInt(params.runId, 10);
          docId = `run:${resolvedRunId}`;

          setStatus('busy', `Loading run ${resolvedRunId}…`);

          const info = await editorApi.pageInfo(resolvedRunId);
          filename = info.source_filename;
          docVersion = '';
          totalPages = info.page_count;
        } else if (params.docId) {
          // Document ID route — resolve first
          docId = decodeURIComponent(params.docId);
          setStatus('busy', `Resolving '${docId}'…`);

          const resolved = await editorApi.resolveDoc(docId);
          resolvedRunId = resolved.run_id;
          docVersion = resolved.doc_version;

          setStatus('busy', `Loading run ${resolvedRunId}…`);

          const info = await editorApi.pageInfo(resolvedRunId);
          filename = info.source_filename;
          totalPages = info.page_count;
        } else {
          return;
        }

        if (cancelled) return;

        // Set store state
        setDocument({
          runId: resolvedRunId,
          docId,
          filename,
          docVersion,
          totalPages,
        });

        // PDF URL
        setPdfUrl(editorApi.sourcePdfUrl(resolvedRunId));

        // Load markdown
        const mdResp = await editorApi.markdown(resolvedRunId);
        if (cancelled) return;

        // Set editor content (allow a tick for mount)
        requestAnimationFrame(() => {
          editorRef.current?.setContent(mdResp.markdown);
        });

        toast.success(
          `Loaded: ${filename} (${totalPages} pages, run #${resolvedRunId})`,
        );
      } catch (err) {
        if (!cancelled) {
          setStatus('error', `Failed to load document`);
          toast.error(String(err));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.docId, params.runId]);

  // ── Editor change callback ──
  const handleEditorUpdate = useCallback((content: string) => {
    const lines = content.split('\n').length;
    setCharCount(content.length);
    setLineCount(lines);
  }, []);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <PanelGroup direction="horizontal" className="flex-1">
        {/* ── Left: PDF ── */}
        <Panel defaultSize={40} minSize={20}>
          <PdfViewer pdfUrl={pdfUrl} />
        </Panel>

        <PanelResizeHandle className="w-1.5 bg-bg-secondary hover:bg-accent/30 transition-colors" />

        {/* ── Right: Editor ── */}
        <Panel defaultSize={60} minSize={25}>
          <div className="flex h-full flex-col">
            <EditorToolbar editorRef={editorRef} />
            <MarkdownEditor
              ref={editorRef}
              onUpdate={handleEditorUpdate}
            />
          </div>
        </Panel>
      </PanelGroup>

      <StatusBar charCount={charCount} lineCount={lineCount} />
    </div>
  );
}
