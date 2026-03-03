/**
 * React Query hooks wrapping the editor API.
 *
 * - Queries are used for read operations (resolve-doc, page-info, markdown)
 * - Mutations are used for write/long-running operations (VLM, save, refine, judge)
 */
import { useMutation, useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';

import { editorApi, type VisionRefineRequest } from '@/services/api';
import { useEditorStore } from '@/stores/editor-store';

// ── Queries ───────────────────────────────────────────────────────

export function useResolveDoc(docId: string | undefined) {
  return useQuery({
    queryKey: ['editor', 'resolve-doc', docId],
    queryFn: () => editorApi.resolveDoc(docId!),
    enabled: !!docId,
  });
}

export function usePageInfo(runId: number | null) {
  return useQuery({
    queryKey: ['editor', 'page-info', runId],
    queryFn: () => editorApi.pageInfo(runId!),
    enabled: runId != null,
  });
}

export function useMarkdown(runId: number | null) {
  return useQuery({
    queryKey: ['editor', 'markdown', runId],
    queryFn: () => editorApi.markdown(runId!),
    enabled: runId != null,
  });
}

// ── Mutations ─────────────────────────────────────────────────────

export function useVisionRefine() {
  const setStatus = useEditorStore((s) => s.setStatus);
  const setLastModel = useEditorStore((s) => s.setLastModel);

  return useMutation({
    mutationFn: (req: VisionRefineRequest) => editorApi.visionRefine(req),
    onMutate: () => setStatus('busy', 'VLM vision-refine…'),
    onSuccess: (data) => {
      setLastModel(data.model);
      setStatus('idle', 'VLM fix applied');
      toast.success(`VLM corrected page ${data.page_num + 1} via ${data.model}`);
    },
    onError: (err: Error) => {
      setStatus('error', 'VLM fix failed');
      toast.error(err.message);
    },
  });
}

export function useSaveMarkdown() {
  const setStatus = useEditorStore((s) => s.setStatus);

  return useMutation({
    mutationFn: (req: { run_id: number; markdown: string }) =>
      editorApi.saveMarkdown(req),
    onMutate: () => setStatus('busy', 'Saving…'),
    onSuccess: (data) => {
      setStatus('idle', 'Saved');
      toast.success(`Saved ${data.chars.toLocaleString()} chars → ${data.path}`);
    },
    onError: (err: Error) => {
      setStatus('error', 'Save failed');
      toast.error(err.message);
    },
  });
}

export function useLlmRefine() {
  const setStatus = useEditorStore((s) => s.setStatus);
  const setLastModel = useEditorStore((s) => s.setLastModel);

  return useMutation({
    mutationFn: (req: { run_id: number; markdown: string }) =>
      editorApi.llmRefine(req),
    onMutate: () => setStatus('busy', 'LLM Refine running…'),
    onSuccess: (data) => {
      setLastModel(data.model);
      if (data.success) {
        const impList = data.improvements.length
          ? `: ${data.improvements.join(', ')}`
          : '';
        setStatus('idle', 'Refined');
        toast.success(`Refined by ${data.model}${impList}`);
      } else {
        setStatus('idle', 'Refine returned unchanged');
        toast.warning('Refine returned unchanged (guardrail triggered)');
      }
    },
    onError: (err: Error) => {
      setStatus('error', 'Refine error');
      toast.error(err.message);
    },
  });
}

export function useReJudge() {
  const setStatus = useEditorStore((s) => s.setStatus);
  const setLastModel = useEditorStore((s) => s.setLastModel);

  return useMutation({
    mutationFn: (req: { run_id: number; markdown: string }) =>
      editorApi.reJudge(req),
    onMutate: () => setStatus('busy', 'Re-Judge running…'),
    onSuccess: (data) => {
      setLastModel(data.model);
      setStatus('idle', `Judge: ${data.score}/5`);
      const level = data.needs_refinement ? 'warning' : 'success';
      toast[level](
        `Score: ${data.score}/5 — ${data.needs_refinement ? 'needs refinement' : 'passes quality gate'}`,
      );
    },
    onError: (err: Error) => {
      setStatus('error', 'Judge error');
      toast.error(err.message);
    },
  });
}
