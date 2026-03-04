/**
 * Admin Cleanup page — Cleanup rules management, feedback, and rule suggestion.
 */
import { useState, useEffect } from 'react';
import {
  Download,
  FileWarning,
  Loader2,
  MessageSquare,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  Upload,
} from 'lucide-react';
import { PageShell } from '@/components/layout';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Separator } from '@/components/ui/separator';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ConfirmDialog } from '@/components/confirm-dialog';
import {
  adminApi,
  type CleanupFeedback,
  type CleanupFeedbackSummary,
  type CleanupRuleSuggestion,
} from '@/services/admin-api';
import { toast } from 'sonner';

export function AdminCleanupPage() {
  // ── State ─────────────────────────────────────────────────────
  const [feedbackList, setFeedbackList] = useState<CleanupFeedback[]>([]);
  const [feedbackSummary, setFeedbackSummary] = useState<CleanupFeedbackSummary | null>(null);
  const [loading, setLoading] = useState(false);

  // Submit feedback form
  const [fbDocId, setFbDocId] = useState('');
  const [fbCategory, setFbCategory] = useState('formatting');
  const [fbComment, setFbComment] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Rule suggestion
  const [sampleMd, setSampleMd] = useState('');
  const [observedIssues, setObservedIssues] = useState('');
  const [suggestion, setSuggestion] = useState<CleanupRuleSuggestion | null>(null);
  const [suggesting, setSuggesting] = useState(false);

  // Rule apply
  const [ruleYaml, setRuleYaml] = useState('');
  const [applying, setApplying] = useState(false);

  // Rule remove
  const [removeRuleName, setRemoveRuleName] = useState('');

  const loadFeedback = async () => {
    setLoading(true);
    try {
      const [list, summary] = await Promise.all([
        adminApi.listFeedback(),
        adminApi.feedbackCategories(),
      ]);
      setFeedbackList(list);
      setFeedbackSummary(summary);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load feedback');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFeedback();
  }, []);

  // ── Handlers ────────────────────────────────────────────────

  const handleSubmitFeedback = async () => {
    if (!fbDocId) return;
    setSubmitting(true);
    try {
      await adminApi.submitFeedback({
        doc_id: fbDocId,
        category: fbCategory,
        comment: fbComment || undefined,
      });
      toast.success('Feedback submitted');
      setFbDocId('');
      setFbComment('');
      loadFeedback();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Submit failed');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteFeedback = async (id: number) => {
    try {
      await adminApi.deleteFeedback(id);
      toast.success('Feedback deleted');
      loadFeedback();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const handleSuggestRule = async () => {
    if (!sampleMd.trim()) return;
    setSuggesting(true);
    setSuggestion(null);
    try {
      const s = await adminApi.suggestRule({
        sample_markdown: sampleMd,
        observed_issues: observedIssues,
      });
      setSuggestion(s);
      setRuleYaml(s.yaml);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Suggestion failed');
    } finally {
      setSuggesting(false);
    }
  };

  const handleApplyRule = async () => {
    if (!ruleYaml.trim()) return;
    setApplying(true);
    try {
      await adminApi.applyRule({ yaml: ruleYaml });
      toast.success('Rule applied');
      setRuleYaml('');
      setSuggestion(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Apply failed');
    } finally {
      setApplying(false);
    }
  };

  const handleRemoveRule = async () => {
    if (!removeRuleName) return;
    try {
      await adminApi.removeRule(removeRuleName);
      toast.success(`Rule "${removeRuleName}" removed`);
      setRemoveRuleName('');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Remove failed');
    }
  };

  const handleExportRules = async () => {
    try {
      const data = await adminApi.exportRules();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'cleanup-rules-export.json';
      a.click();
      URL.revokeObjectURL(url);
      toast.success('Rules exported');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Export failed');
    }
  };

  const handleImportRules = async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    try {
      await adminApi.importRules(fd);
      toast.success('Rules imported');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Import failed');
    }
  };

  return (
    <PageShell className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Cleanup & feedback</h1>
          <p className="text-sm text-text-secondary">
            Manage cleanup rules, submit feedback, and get AI-powered suggestions
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadFeedback} disabled={loading}>
          {loading ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 size-3.5" />}
          Refresh
        </Button>
      </div>

      {/* Feedback summary */}
      {feedbackSummary && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Feedback summary</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline" className="text-xs">
                Total: {feedbackSummary.total ?? 0}
              </Badge>
              {Object.entries(feedbackSummary.categories ?? {}).map(([cat, count]) => (
                <Badge key={cat} variant="secondary" className="text-xs">
                  {cat}: {count}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Submit feedback */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <MessageSquare className="size-4" />
            Submit feedback
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label className="text-[11px]">Document ID</Label>
              <Input value={fbDocId} onChange={(e) => setFbDocId(e.target.value)} className="h-8 text-xs" />
            </div>
            <div className="space-y-1">
              <Label className="text-[11px]">Category</Label>
              <Select value={fbCategory} onValueChange={setFbCategory}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="formatting">Formatting</SelectItem>
                  <SelectItem value="content">Content quality</SelectItem>
                  <SelectItem value="structure">Structure</SelectItem>
                  <SelectItem value="tables">Tables</SelectItem>
                  <SelectItem value="headers">Headers</SelectItem>
                  <SelectItem value="artifacts">Artifacts</SelectItem>
                  <SelectItem value="other">Other</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-[11px]">Comment (optional)</Label>
            <Textarea value={fbComment} onChange={(e) => setFbComment(e.target.value)} rows={2} className="text-xs" />
          </div>
          <Button size="sm" onClick={handleSubmitFeedback} disabled={submitting || !fbDocId}>
            {submitting ? <Loader2 className="mr-1.5 size-3 animate-spin" /> : <Plus className="mr-1.5 size-3" />}
            Submit
          </Button>
        </CardContent>
      </Card>

      {/* Feedback list */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Feedback history</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">ID</TableHead>
                <TableHead className="text-xs">Doc ID</TableHead>
                <TableHead className="text-xs">Category</TableHead>
                <TableHead className="text-xs">Comment</TableHead>
                <TableHead className="text-xs">Created</TableHead>
                <TableHead className="w-8" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {feedbackList.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-6 text-center text-sm text-text-muted">
                    No feedback submitted yet
                  </TableCell>
                </TableRow>
              ) : (
                feedbackList.map((fb) => (
                  <TableRow key={fb.id}>
                    <TableCell className="font-mono text-xs">{fb.id}</TableCell>
                    <TableCell className="max-w-[120px] truncate font-mono text-xs">{fb.doc_id}</TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="text-[11px]">{fb.category}</Badge>
                    </TableCell>
                    <TableCell className="max-w-[200px] truncate text-xs">{fb.description ?? '—'}</TableCell>
                    <TableCell className="text-xs text-text-muted">
                      {fb.created_at ? new Date(fb.created_at).toLocaleDateString() : '—'}
                    </TableCell>
                    <TableCell>
                      <ConfirmDialog
                        title="Delete feedback"
                        description="Remove this feedback entry?"
                        confirmLabel="Delete"
                        variant="destructive"
                        onConfirm={() => handleDeleteFeedback(fb.id)}
                      >
                        <Button variant="ghost" size="icon" className="size-6">
                          <Trash2 className="size-3" />
                        </Button>
                      </ConfirmDialog>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Separator />

      {/* Rule suggestion */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Sparkles className="size-4" />
            AI rule suggestion
          </CardTitle>
          <CardDescription className="text-xs">
            Paste problematic markdown and describe issues to get an AI-generated cleanup rule
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <Label className="text-[11px]">Sample markdown</Label>
            <Textarea value={sampleMd} onChange={(e) => setSampleMd(e.target.value)} rows={4} className="font-mono text-xs" />
          </div>
          <div className="space-y-1">
            <Label className="text-[11px]">Observed issues</Label>
            <Input value={observedIssues} onChange={(e) => setObservedIssues(e.target.value)} className="h-8 text-xs" />
          </div>
          <Button size="sm" onClick={handleSuggestRule} disabled={suggesting || !sampleMd.trim()}>
            {suggesting ? <Loader2 className="mr-1.5 size-3 animate-spin" /> : <Sparkles className="mr-1.5 size-3" />}
            Get suggestion
          </Button>

          {suggestion && (
            <div className="space-y-2 rounded-md border border-border p-3">
              <p className="text-xs font-medium text-text-primary">Rationale</p>
              <p className="text-xs text-text-secondary">{suggestion.rationale}</p>
              {suggestion.warnings && suggestion.warnings.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {suggestion.warnings.map((w, i) => (
                    <Badge key={i} variant="outline" className="text-[10px] text-state-warning">
                      {w}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Apply rule */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Apply cleanup rule</CardTitle>
          <CardDescription className="text-xs">
            Paste YAML rule or use the suggested output above
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            value={ruleYaml}
            onChange={(e) => setRuleYaml(e.target.value)}
            rows={6}
            placeholder="rule_name:&#10;  pattern: ..."
            className="font-mono text-xs"
          />
          <Button size="sm" onClick={handleApplyRule} disabled={applying || !ruleYaml.trim()}>
            {applying ? <Loader2 className="mr-1.5 size-3 animate-spin" /> : <Plus className="mr-1.5 size-3" />}
            Apply rule
          </Button>
        </CardContent>
      </Card>

      {/* Remove rule */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Remove rule</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2">
            <Input
              value={removeRuleName}
              onChange={(e) => setRemoveRuleName(e.target.value)}
              placeholder="Rule name to remove"
              className="h-8 text-xs"
            />
            <ConfirmDialog
              title="Remove rule"
              description={`Remove cleanup rule "${removeRuleName}"?`}
              confirmLabel="Remove"
              variant="destructive"
              onConfirm={handleRemoveRule}
            >
              <Button variant="destructive" size="sm" disabled={!removeRuleName}>
                <Trash2 className="mr-1.5 size-3" />
                Remove
              </Button>
            </ConfirmDialog>
          </div>
        </CardContent>
      </Card>

      <Separator />

      {/* Import / Export */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <FileWarning className="size-4" />
            Rules import / export
          </CardTitle>
        </CardHeader>
        <CardContent className="flex gap-2">
          <Button variant="outline" size="sm" onClick={handleExportRules}>
            <Download className="mr-1.5 size-3" />
            Export rules
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const input = document.createElement('input');
              input.type = 'file';
              input.accept = '.json,.yaml,.yml';
              input.onchange = (e) => {
                const file = (e.target as HTMLInputElement).files?.[0];
                if (file) handleImportRules(file);
              };
              input.click();
            }}
          >
            <Upload className="mr-1.5 size-3" />
            Import rules
          </Button>
        </CardContent>
      </Card>
    </PageShell>
  );
}
