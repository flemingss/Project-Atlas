/**
 * React Query hooks for VLM Ingest workflow.
 *
 * Queries for read operations, mutations for write/long-running operations.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import {
  vlmIngestApi,
  type StartSessionRequest,
  type UpdateConfigRequest,
  type ProcessPageRequest,
  type CommitRequest,
  type ThumbnailEntry,
  type SessionSummary,
} from '@/services/vlm-ingest-api';
import { useVlmIngestStore } from '@/stores/vlm-ingest-store';

export function isSessionNotFoundError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message.toLowerCase() : String(err).toLowerCase();
  return msg.includes('404') && (msg.includes('session') || msg.includes('not found'));
}

// ── Query keys ────────────────────────────────────────────────────

const KEYS = {
  sessions: ['vlm-ingest', 'sessions'] as const,
  session: (sid: string) => ['vlm-ingest', 'session', sid] as const,
  thumbnails: (sid: string) => ['vlm-ingest', 'thumbnails', sid] as const,
};

// ── Queries ───────────────────────────────────────────────────────

export function useVlmSession(sid: string | null) {
  return useQuery<SessionSummary>({
    queryKey: sid ? KEYS.session(sid) : ['vlm-ingest', 'session', 'none'],
    queryFn: () => vlmIngestApi.getSession(sid!),
    enabled: !!sid,
    refetchInterval: (query) => (query.state.error ? false : 5_000),
    retry: false,
  });
}

export function useVlmThumbnails(sid: string | null) {
  return useQuery<ThumbnailEntry[]>({
    queryKey: sid ? KEYS.thumbnails(sid) : ['vlm-ingest', 'thumbnails', 'none'],
    queryFn: () => vlmIngestApi.getThumbnails(sid!),
    enabled: !!sid,
    staleTime: 60_000,
    retry: false,
  });
}

// ── Mutations ─────────────────────────────────────────────────────

export function useStartSession() {
  const setSession = useVlmIngestStore((s) => s.setSession);
  const setStatus = useVlmIngestStore((s) => s.setStatus);
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (req: StartSessionRequest) => vlmIngestApi.startSession(req),
    onMutate: () => setStatus('busy', 'Creating session…'),
    onSuccess: (data, variables) => {
      // Build a SessionSummary-like object for the store
      setSession({
        session_id: data.session_id,
        status: data.status,
        source_filename: data.source_filename,
        run_id: variables.run_id,
        page_count: data.page_count,
        headless: data.headless,
        progress: {},
        config: {},
      });
      setStatus('idle', `Session started — ${data.page_count} pages`);
      toast.success(`Session created: ${data.source_filename} (${data.page_count} pages)`);
      qc.invalidateQueries({ queryKey: KEYS.sessions });
    },
    onError: (err: Error) => {
      setStatus('error', 'Failed to create session');
      toast.error(err.message);
    },
  });
}

export function useStartSessionUpload() {
  const setSession = useVlmIngestStore((s) => s.setSession);
  const setStatus = useVlmIngestStore((s) => s.setStatus);
  const qc = useQueryClient();

  return useMutation({
    mutationFn: ({ file, config, headless }: { file: File; config?: Record<string, unknown>; headless?: boolean }) =>
      vlmIngestApi.startSessionUpload(file, config, headless),
    onMutate: () => setStatus('busy', 'Uploading PDF…'),
    onSuccess: (data) => {
      setSession({
        session_id: data.session_id,
        status: data.status,
        source_filename: data.source_filename,
        run_id: null,
        page_count: data.page_count,
        headless: data.headless,
        progress: {},
        config: {},
      });
      setStatus('idle', `Uploaded — ${data.page_count} pages`);
      toast.success(`Uploaded ${data.source_filename} (${data.page_count} pages)`);
      qc.invalidateQueries({ queryKey: KEYS.sessions });
    },
    onError: (err: Error) => {
      setStatus('error', 'Upload failed');
      toast.error(err.message);
    },
  });
}

export function useUpdateConfig() {
  const setStatus = useVlmIngestStore((s) => s.setStatus);
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);
  const qc = useQueryClient();

  return useMutation({
    mutationFn: ({ sid, req }: { sid: string; req: UpdateConfigRequest }) =>
      vlmIngestApi.updateConfig(sid, req),
    onMutate: () => setStatus('busy', 'Updating config…'),
    onSuccess: (data) => {
      setStatus('idle', 'Config updated');
      toast.success('Configuration saved');
      qc.invalidateQueries({ queryKey: KEYS.session(data.session_id) });
    },
    onError: (err: Error) => {
      if (isSessionNotFoundError(err)) {
        markSessionExpired('The backend session was lost while saving config.');
      }
      setStatus('error', 'Config update failed');
      toast.error(err.message);
    },
  });
}

export function useProcessPage() {
  const setPageResult = useVlmIngestStore((s) => s.setPageResult);
  const setPageError = useVlmIngestStore((s) => s.setPageError);
  const setProcessing = useVlmIngestStore((s) => s.setProcessing);
  const setStatus = useVlmIngestStore((s) => s.setStatus);
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);

  return useMutation({
    mutationFn: ({ sid, req }: { sid: string; req: ProcessPageRequest }) =>
      vlmIngestApi.processPage(sid, req),
    onMutate: (vars) => {
      const pageNum = vars.req.page_num;
      setProcessing(true, pageNum ?? null);
      // Mark page as 'processing' so processNext won't re-pick it
      if (pageNum != null) {
        useVlmIngestStore.getState().setPageResult(pageNum, '', '', 'processing');
      }
      setStatus('busy', `Processing page ${pageNum != null ? pageNum + 1 : '(next)'}…`);
    },
    onSuccess: (data) => {
      if (data.status === 'done') {
        setPageResult(data.page_num, data.markdown, data.model, 'done');
        toast.success(`Page ${data.page_num + 1} processed via ${data.model}`);
      } else if (data.status === 'skipped') {
        setPageResult(data.page_num, '', '', 'skipped');
      } else if (data.status === 'error') {
        setPageError(data.page_num, data.finish_reason || 'Unknown error');
        toast.error(`Page ${data.page_num + 1} failed: ${data.finish_reason}`);
      }
      setProcessing(false);
      setStatus('idle', `Page ${data.page_num + 1}: ${data.status}`);
    },
    onError: (err: Error) => {
      if (isSessionNotFoundError(err)) {
        markSessionExpired('The backend session was lost while processing pages.');
      }
      setProcessing(false);
      setStatus('error', 'Processing failed');
      toast.error(err.message);
    },
  });
}

export function useStitch() {
  const setStitchResult = useVlmIngestStore((s) => s.setStitchResult);
  const setStatus = useVlmIngestStore((s) => s.setStatus);
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);

  return useMutation({
    mutationFn: (sid: string) => vlmIngestApi.stitch(sid),
    onMutate: () => setStatus('busy', 'Stitching pages…'),
    onSuccess: (data) => {
      setStitchResult(data);
      setStatus('idle', `Stitched ${data.pages_processed} pages → ${data.markdown.length.toLocaleString()} chars`);
      toast.success(`Stitched ${data.pages_processed} pages (${data.duplicate_lines_removed} dupes removed, ${data.tables_merged} tables merged)`);
    },
    onError: (err: Error) => {
      if (isSessionNotFoundError(err)) {
        markSessionExpired('The backend session was lost before stitching.');
      }
      setStatus('error', 'Stitch failed');
      toast.error(err.message);
    },
  });
}

export function useCommit() {
  const setStatus = useVlmIngestStore((s) => s.setStatus);
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);
  const qc = useQueryClient();

  return useMutation({
    mutationFn: ({ sid, req }: { sid: string; req: CommitRequest }) =>
      vlmIngestApi.commit(sid, req),
    onMutate: () => setStatus('busy', 'Committing…'),
    onSuccess: (data) => {
      if (data.run_id != null) useVlmIngestStore.setState({ runId: data.run_id });
      setStatus('idle', `Committed: ${data.chars.toLocaleString()} chars`);
      toast.success(`Committed to ${data.path} (${data.chars.toLocaleString()} chars)`);
      qc.invalidateQueries({ queryKey: KEYS.sessions });
    },
    onError: (err: Error) => {
      if (isSessionNotFoundError(err)) {
        markSessionExpired('The backend session was lost before commit.');
      }
      setStatus('error', 'Commit failed');
      toast.error(err.message);
    },
  });
}

export function useExportConfig() {
  const setStatus = useVlmIngestStore((s) => s.setStatus);
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);

  return useMutation({
    mutationFn: (sid: string) => vlmIngestApi.exportConfig(sid),
    onMutate: () => setStatus('busy', 'Exporting config…'),
    onSuccess: (data) => {
      // Download as JSON file
      const blob = new Blob([JSON.stringify(data.config, null, 2)], {
        type: 'application/json',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `vlm-ingest-config-${data.source_filename}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setStatus('idle', 'Config exported');
      toast.success('Config downloaded');
    },
    onError: (err: Error) => {
      if (isSessionNotFoundError(err)) {
        markSessionExpired('The backend session was lost before config export.');
      }
      setStatus('error', 'Export failed');
      toast.error(err.message);
    },
  });
}

export function useDeleteSession() {
  const reset = useVlmIngestStore((s) => s.reset);
  const setStatus = useVlmIngestStore((s) => s.setStatus);
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (sid: string) => vlmIngestApi.deleteSession(sid),
    onSuccess: () => {
      reset();
      setStatus('idle', 'Session discarded');
      toast.success('Session discarded');
      qc.invalidateQueries({ queryKey: KEYS.sessions });
    },
    onError: (err: Error) => {
      setStatus('error', 'Delete failed');
      toast.error(err.message);
    },
  });
}

export function useUpdatePageResult() {
  const setPageMarkdown = useVlmIngestStore((s) => s.setPageMarkdown);
  const setStatus = useVlmIngestStore((s) => s.setStatus);
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);

  return useMutation({
    mutationFn: ({ sid, pageNum, markdown }: { sid: string; pageNum: number; markdown: string }) =>
      vlmIngestApi.updatePageResult(sid, pageNum, markdown),
    onMutate: () => setStatus('busy', 'Saving correction…'),
    onSuccess: (data, variables) => {
      setPageMarkdown(data.page_num, variables.markdown);
      setStatus('idle', `Page ${data.page_num + 1} updated`);
      toast.success(`Page ${data.page_num + 1} correction saved`);
    },
    onError: (err: Error) => {
      if (isSessionNotFoundError(err)) {
        markSessionExpired('The backend session was lost while saving a page correction.');
      }
      setStatus('error', 'Save failed');
      toast.error(err.message);
    },
  });
}
