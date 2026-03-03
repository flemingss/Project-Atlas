/**
 * Editor toolbar — VLM Fix, LLM Refine, Re-Judge, Strip, Save, Undo.
 */
import type { EditorHandle } from './markdown-editor';
import {
  Eraser,
  Gavel,
  RotateCcw,
  Save,
  Sparkles,
  Wand2,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { useEditorStore } from '@/stores/editor-store';
import {
  useLlmRefine,
  useReJudge,
  useSaveMarkdown,
  useVisionRefine,
} from '@/hooks/use-editor-api';
import { editorApi } from '@/services/api';
import { VlmSettingsPopover } from './vlm-settings';

interface EditorToolbarProps {
  editorRef: React.RefObject<EditorHandle | null>;
}

export function EditorToolbar({ editorRef }: EditorToolbarProps) {
  const { runId, currentPage, vlm, status } = useEditorStore();
  const busy = status === 'busy';
  const disabled = !runId || busy;

  const visionRefine = useVisionRefine();
  const saveMarkdown = useSaveMarkdown();
  const llmRefine = useLlmRefine();
  const reJudge = useReJudge();

  // ── VLM Fix ──
  const handleVlmFix = async () => {
    if (!runId || !editorRef.current) return;
    const editor = editorRef.current;

    // Check selection first
    const sel = editor.getSelection();
    let sendMarkdown: string;
    let hasSelection = false;
    let selFrom = 0;
    let selTo = 0;

    if (sel) {
      sendMarkdown = sel.text;
      hasSelection = true;
      selFrom = sel.from;
      selTo = sel.to;
    } else {
      // Try per-page markdown
      try {
        const pageMd = await editorApi.pageMarkdown(runId, currentPage);
        sendMarkdown = pageMd.markdown;
      } catch {
        // Fallback to full content
        sendMarkdown = editor.getContent();
      }
    }

    visionRefine.mutate(
      {
        run_id: runId,
        page_num: currentPage,
        current_markdown: sendMarkdown,
        dpi: vlm.dpi,
        crop_top: vlm.cropTop,
        crop_bottom: vlm.cropBottom,
      },
      {
        onSuccess: (data) => {
          if (hasSelection) {
            // Replace selection only
            const view = editor.getView();
            if (view) {
              view.dispatch({
                changes: {
                  from: selFrom,
                  to: selTo,
                  insert: data.corrected_markdown,
                },
              });
            }
          } else {
            editor.setContent(data.corrected_markdown);
          }
        },
      },
    );
  };

  // ── LLM Refine ──
  const handleLlmRefine = () => {
    if (!runId || !editorRef.current) return;
    const md = editorRef.current.getContent();
    llmRefine.mutate(
      { run_id: runId, markdown: md },
      {
        onSuccess: (data) => {
          if (data.success) editorRef.current?.setContent(data.refined_markdown);
        },
      },
    );
  };

  // ── Strip Artifacts ──
  const handleStrip = () => {
    if (!editorRef.current) return;
    let md = editorRef.current.getContent();
    const origLen = md.length;

    md = md.replace(/^```(?:markdown)?\s*\n/i, '');
    md = md.replace(/\n```\s*$/i, '');
    md = md.replace(
      /^(?:Here is|Below is|Sure,?|I've (?:made|cleaned|updated|improved)).*?\n+/i,
      '',
    );
    md = md.replace(
      /\n+(?:Let me know|I hope|Feel free|Is there anything).*?$/i,
      '',
    );
    md = md.replace(
      /\n+## (?:Summary of Changes|Improvements Made|Changes Made)[\s\S]*?(?=\n## |\n*$)/gi,
      '',
    );
    md = md.replace(/<think>[\s\S]*?<\/think>/g, '');
    md = md.replace(/<think>[\s\S]*$/g, '');
    md = md.trim();

    if (md.length < origLen) {
      editorRef.current.setContent(md);
    }
  };

  // ── Save ──
  const handleSave = () => {
    if (!runId || !editorRef.current) return;
    saveMarkdown.mutate({
      run_id: runId,
      markdown: editorRef.current.getContent(),
    });
  };

  // ── Re-Judge ──
  const handleReJudge = () => {
    if (!runId || !editorRef.current) return;
    reJudge.mutate({
      run_id: runId,
      markdown: editorRef.current.getContent(),
    });
  };

  // ── Undo ──
  const handleUndo = () => editorRef.current?.undo();

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-border bg-bg-secondary px-3 py-1.5">
      {/* VLM group */}
      <div className="flex items-center gap-1">
        <Button
          size="sm"
          className="bg-accent text-black hover:bg-accent-hover"
          disabled={disabled}
          onClick={handleVlmFix}
          title="Send current page + markdown to VLM for correction"
        >
          <Wand2 className="mr-1 size-3.5" />
          VLM Fix
        </Button>
        <VlmSettingsPopover />
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={handleLlmRefine}
          title="Send markdown to refine model"
        >
          <Sparkles className="mr-1 size-3.5" />
          LLM Refine
        </Button>
      </div>

      <Separator orientation="vertical" className="mx-1 h-5" />

      {/* Quality group */}
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={handleStrip}
          title="Remove common LLM artifacts"
        >
          <Eraser className="mr-1 size-3.5" />
          Strip
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={handleReJudge}
          title="Re-run quality judge on current markdown"
        >
          <Gavel className="mr-1 size-3.5" />
          Re-Judge
        </Button>
      </div>

      <Separator orientation="vertical" className="mx-1 h-5" />

      {/* Save group */}
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={handleSave}
          title="Save markdown back to artifacts"
        >
          <Save className="mr-1 size-3.5" />
          Save
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={handleUndo}
          title="Undo last change"
        >
          <RotateCcw className="mr-1 size-3.5" />
          Undo
        </Button>
      </div>
    </div>
  );
}
