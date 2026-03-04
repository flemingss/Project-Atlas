/**
 * Review page — HITL review inbox with task detail / editing workflow.
 */
import { useState, useEffect } from 'react';
import {
  CheckCircle2,
  ChevronRight,
  Loader2,
  Play,
  RefreshCw,
  SkipForward,
  XCircle,
} from 'lucide-react';
import { PageShell } from '@/components/layout';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useConnectionStore } from '@/stores/connection-store';
import { useScopeStore } from '@/stores/scope-store';
import { AuthGate } from '@/components/auth-gate';
import { hitlApi, type HitlTask } from '@/services/hitl-api';
import { toast } from 'sonner';

type StatusFilter = 'all' | 'pending' | 'completed' | 'skipped' | 'rejected';

const statusColors: Record<string, string> = {
  pending: 'bg-state-warning/10 text-state-warning',
  completed: 'bg-state-success/10 text-state-success',
  skipped: 'bg-accent/10 text-accent',
  rejected: 'bg-state-error/10 text-state-error',
};

export function ReviewPage() {
  const { isAdmin } = useConnectionStore();
  const { workspace } = useScopeStore();

  const [tasks, setTasks] = useState<HitlTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [activeTask, setActiveTask] = useState<HitlTask | null>(null);

  // Edit state
  const [editedMd, setEditedMd] = useState('');
  const [reason, setReason] = useState('');
  const [actionInProgress, setActionInProgress] = useState(false);

  const loadTasks = async () => {
    setLoading(true);
    try {
      const data = await hitlApi.listTasks({
        status: filter === 'all' ? undefined : filter,
        tenant_id: workspace || undefined,
        limit: 100,
      });
      setTasks(data);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load tasks');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isAdmin) loadTasks();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, workspace, isAdmin]);

  if (!isAdmin) return <AuthGate />;

  const openTask = (t: HitlTask) => {
    setActiveTask(t);
    setEditedMd(t.after_md ?? t.before_md ?? '');
    setReason('');
  };

  const handleClaimNext = async () => {
    try {
      const t = await hitlApi.nextTask({ tenant_id: workspace || undefined });
      openTask(t);
      toast.success(`Claimed task #${t.task_id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'No pending tasks');
    }
  };

  const handleComplete = async () => {
    if (!activeTask) return;
    setActionInProgress(true);
    try {
      await hitlApi.completeTask(activeTask.task_id, {
        after_md: editedMd,
        reason: reason || undefined,
      });
      toast.success(`Task #${activeTask.task_id} completed`);
      setActiveTask(null);
      loadTasks();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Complete failed');
    } finally {
      setActionInProgress(false);
    }
  };

  const handleSkip = async () => {
    if (!activeTask) return;
    setActionInProgress(true);
    try {
      await hitlApi.skipTask(activeTask.task_id, { reason: reason || 'Skipped' });
      toast.success(`Task #${activeTask.task_id} skipped`);
      setActiveTask(null);
      loadTasks();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Skip failed');
    } finally {
      setActionInProgress(false);
    }
  };

  const handleReject = async () => {
    if (!activeTask) return;
    setActionInProgress(true);
    try {
      await hitlApi.rejectTask(activeTask.task_id, { reason: reason || undefined });
      toast.success(`Task #${activeTask.task_id} rejected`);
      setActiveTask(null);
      loadTasks();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Reject failed');
    } finally {
      setActionInProgress(false);
    }
  };

  const handleResume = async () => {
    if (!activeTask) return;
    setActionInProgress(true);
    try {
      await hitlApi.resumeTask(activeTask.task_id);
      toast.success(`Pipeline resumed for task #${activeTask.task_id}`);
      setActiveTask(null);
      loadTasks();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Resume failed');
    } finally {
      setActionInProgress(false);
    }
  };

  const pendingCount = tasks.filter((t) => t.status === 'pending').length;

  // ─── Detail view ──────────────────────────────────────────────
  if (activeTask) {
    return (
      <PageShell className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => setActiveTask(null)}>
          ← Back to queue
        </Button>

        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold text-text-primary">Task #{activeTask.task_id}</h1>
          <Badge className={statusColors[activeTask.status] ?? ''}>
            {activeTask.status}
          </Badge>
        </div>

        {/* Meta info */}
        <div className="flex flex-wrap gap-4 text-xs text-text-secondary">
          <span><strong>Doc&nbsp;ID:</strong> {activeTask.doc_id}</span>
          <span><strong>Version:</strong> {activeTask.doc_version}</span>
          {activeTask.chunk_index != null && (
            <span><strong>Chunk:</strong> {activeTask.chunk_index}</span>
          )}
          {activeTask.meta?.judge_score != null && (
            <span><strong>Score:</strong> {(activeTask.meta.judge_score * 100).toFixed(0)}%</span>
          )}
          {activeTask.meta?.refine_attempts != null && (
            <span>
              <strong>Refine:</strong> {activeTask.meta.refine_attempts}
              {activeTask.meta.max_refine_attempts != null && `/${activeTask.meta.max_refine_attempts}`}
            </span>
          )}
        </div>

        {/* Judge rationale */}
        {activeTask.meta?.judge_rationale && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-xs">Judge rationale</CardTitle>
            </CardHeader>
            <CardContent className="text-xs leading-relaxed text-text-secondary">
              {activeTask.meta.judge_rationale}
            </CardContent>
          </Card>
        )}

        {/* Sub-scores */}
        {activeTask.meta?.judge_sub_scores && Object.keys(activeTask.meta.judge_sub_scores).length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-xs">Sub-scores</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-2">
                {Object.entries(activeTask.meta.judge_sub_scores).map(([k, v]) => (
                  <Badge key={k} variant="outline" className="text-[11px]">
                    {k}: {((v as number) * 100).toFixed(0)}%
                  </Badge>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Split pane: Before | After (editable) */}
        <div className="grid gap-4 lg:grid-cols-2">
          {/* Before */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-xs">Before (original)</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="max-h-[500px] overflow-auto whitespace-pre-wrap rounded-md bg-bg-card p-3 font-mono text-xs leading-relaxed">
                {activeTask.before_md ?? '(empty)'}
              </pre>
            </CardContent>
          </Card>

          {/* After (editable) */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-xs">After (editable)</CardTitle>
            </CardHeader>
            <CardContent>
              {activeTask.status === 'pending' ? (
                <Textarea
                  value={editedMd}
                  onChange={(e) => setEditedMd(e.target.value)}
                  rows={20}
                  className="font-mono text-xs leading-relaxed"
                />
              ) : (
                <pre className="max-h-[500px] overflow-auto whitespace-pre-wrap rounded-md bg-bg-card p-3 font-mono text-xs leading-relaxed">
                  {activeTask.after_md ?? '(empty)'}
                </pre>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Reason */}
        {activeTask.status === 'pending' && (
          <div className="space-y-1">
            <Label className="text-xs">Reason / notes (optional)</Label>
            <Input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why this decision?"
              className="h-8 text-xs"
            />
          </div>
        )}

        {/* Actions */}
        <div className="flex flex-wrap gap-2">
          {activeTask.status === 'pending' && (
            <>
              <Button onClick={handleComplete} disabled={actionInProgress}>
                {actionInProgress ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <CheckCircle2 className="mr-1.5 size-3.5" />}
                Approve & complete
              </Button>
              <Button variant="outline" onClick={handleSkip} disabled={actionInProgress}>
                <SkipForward className="mr-1.5 size-3.5" />
                Skip
              </Button>
              <Button variant="destructive" onClick={handleReject} disabled={actionInProgress}>
                <XCircle className="mr-1.5 size-3.5" />
                Reject
              </Button>
            </>
          )}
          {activeTask.status === 'completed' && (
            <Button variant="outline" onClick={handleResume} disabled={actionInProgress}>
              {actionInProgress ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <Play className="mr-1.5 size-3.5" />}
              Resume pipeline
            </Button>
          )}
        </div>
      </PageShell>
    );
  }

  // ─── List view ────────────────────────────────────────────────
  return (
    <PageShell className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Review queue</h1>
          <p className="text-sm text-text-secondary">
            {pendingCount} pending task{pendingCount !== 1 ? 's' : ''} awaiting review
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadTasks} disabled={loading}>
            {loading ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 size-3.5" />}
            Refresh
          </Button>
          <Button size="sm" onClick={handleClaimNext}>
            <Play className="mr-1.5 size-3.5" />
            Next task
          </Button>
        </div>
      </div>

      {/* Status filter */}
      <div className="flex gap-2">
        {(['all', 'pending', 'completed', 'skipped', 'rejected'] as StatusFilter[]).map((s) => (
          <Button
            key={s}
            variant={filter === s ? 'default' : 'outline'}
            size="sm"
            onClick={() => setFilter(s)}
            className="text-xs capitalize"
          >
            {s}
          </Button>
        ))}
      </div>

      {/* Tasks table */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">ID</TableHead>
                <TableHead className="text-xs">Document</TableHead>
                <TableHead className="text-xs">Chunk</TableHead>
                <TableHead className="text-xs">Score</TableHead>
                <TableHead className="text-xs">Status</TableHead>
                <TableHead className="text-xs">Updated</TableHead>
                <TableHead className="w-8" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {tasks.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-8 text-center text-sm text-text-muted">
                    {loading ? 'Loading…' : 'No tasks found'}
                  </TableCell>
                </TableRow>
              ) : (
                tasks.map((t) => (
                  <TableRow
                    key={t.task_id}
                    className="cursor-pointer"
                    onClick={() => openTask(t)}
                  >
                    <TableCell className="font-mono text-xs">#{t.task_id}</TableCell>
                    <TableCell className="max-w-[150px] truncate font-mono text-xs">{t.doc_id}</TableCell>
                    <TableCell className="text-xs">
                      {t.chunk_index != null ? `#${t.chunk_index}` : '—'}
                    </TableCell>
                    <TableCell className="text-xs">
                      {t.meta?.judge_score != null
                        ? `${(t.meta.judge_score * 100).toFixed(0)}%`
                        : '—'}
                    </TableCell>
                    <TableCell>
                      <Badge className={`text-[11px] ${statusColors[t.status] ?? ''}`}>
                        {t.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-text-muted">
                      {t.updated_at ? new Date(t.updated_at).toLocaleDateString() : '—'}
                    </TableCell>
                    <TableCell>
                      <ChevronRight className="size-3.5 text-text-muted" />
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </PageShell>
  );
}
