/**
 * Search page — RAG query interface with results display.
 */
import { useState } from 'react';
import { Search as SearchIcon, Loader2, Sparkles, FileText } from 'lucide-react';
import { PageShell } from '@/components/layout';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useScopeStore } from '@/stores/scope-store';
import { ragApi, type SearchHit, type SearchResponse } from '@/services/rag-api';
import { toast } from 'sonner';

export function SearchPage() {
  const { workspace, project, collection } = useScopeStore();

  const [query, setQuery] = useState('');
  const [maxResults, setMaxResults] = useState('5');
  const [fidelity, setFidelity] = useState('verified+partial');
  const [searching, setSearching] = useState(false);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);

  const handleSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setResponse(null);
    const t0 = performance.now();
    try {
      const res = await ragApi.search({
        query: query.trim(),
        top_k: parseInt(maxResults, 10) || 5,
        fidelity_mode: fidelity as 'verified' | 'verified+partial' | 'all',
        tenant_id: workspace || undefined,
        project_id: project || undefined,
        corpus_id: collection || undefined,
      });
      setElapsed(Math.round(performance.now() - t0));
      setResponse(res);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Search failed');
    } finally {
      setSearching(false);
    }
  };

  return (
    <PageShell className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text-primary">Search</h1>
        <p className="text-sm text-text-secondary">
          Query your indexed documents using semantic search
        </p>
      </div>

      {/* Query form */}
      <Card>
        <CardContent className="space-y-4 pt-5">
          <div className="space-y-1.5">
            <Label className="text-xs">Query</Label>
            <div className="relative">
              <SearchIcon className="absolute left-3 top-2.5 size-4 text-text-muted" />
              <Input
                placeholder="Ask a question or enter keywords…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                className="h-10 pl-10 text-sm"
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-4">
            <div className="w-32 space-y-1.5">
              <Label className="text-[11px]">Max results</Label>
              <Select value={maxResults} onValueChange={setMaxResults}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {['3', '5', '10', '20', '50'].map((n) => (
                    <SelectItem key={n} value={n}>
                      {n}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="w-36 space-y-1.5">
              <Label className="text-[11px]">Fidelity</Label>
              <Select value={fidelity} onValueChange={setFidelity}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="verified">Verified only</SelectItem>
                  <SelectItem value="verified+partial">Verified + partial</SelectItem>
                  <SelectItem value="all">All</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-end">
              <Button onClick={handleSearch} disabled={searching || !query.trim()}>
                {searching ? (
                  <Loader2 className="mr-1.5 size-4 animate-spin" />
                ) : (
                  <Sparkles className="mr-1.5 size-4" />
                )}
                Search
              </Button>
            </div>
          </div>

          {/* Scope context */}
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            <span className="text-text-muted">Scope:</span>
            {workspace && <Badge variant="outline" className="text-[10px]">{workspace}</Badge>}
            {project && <Badge variant="outline" className="text-[10px]">{project}</Badge>}
            {collection && <Badge variant="outline" className="text-[10px]">{collection}</Badge>}
            {!workspace && !project && !collection && (
              <span className="text-text-muted italic">Global (all documents)</span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Results */}
      {response && (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-semibold text-text-primary">
              {response.hits.length} result{response.hits.length !== 1 ? 's' : ''}
            </h2>
            {elapsed !== null && (
              <span className="text-[11px] text-text-muted">{elapsed}ms</span>
            )}
          </div>

          {response.hits.length === 0 ? (
            <Card>
              <CardContent className="py-8 text-center text-sm text-text-muted">
                No results found. Try adjusting your query or broadening the scope.
              </CardContent>
            </Card>
          ) : (
            response.hits.map((hit: SearchHit, i: number) => <HitCard key={i} hit={hit} rank={i + 1} />)
          )}
        </div>
      )}
    </PageShell>
  );
}

function HitCard({ hit, rank }: { hit: SearchHit; rank: number }) {
  const [expanded, setExpanded] = useState(false);

  const score = typeof hit.score === 'number' ? hit.score : null;
  const scoreColor =
    score === null ? '' : score >= 0.8 ? 'text-state-success' : score >= 0.5 ? 'text-state-warning' : 'text-state-error';

  return (
    <Card className="overflow-hidden">
      <CardContent className="space-y-2 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="flex size-6 items-center justify-center rounded-full bg-accent/10 text-xs font-bold text-accent">
              {rank}
            </span>
            <FileText className="size-3.5 text-text-muted" />
            <span className="font-mono text-xs text-text-secondary">
              {hit.doc_id ?? 'unknown'}
            </span>
          </div>
          {score !== null && (
            <Badge variant="outline" className={`text-[11px] ${scoreColor}`}>
              {(score * 100).toFixed(1)}%
            </Badge>
          )}
        </div>

        <p className={`text-sm leading-relaxed text-text-primary ${expanded ? '' : 'line-clamp-4'}`}>
          {hit.text}
        </p>

        {hit.text.length > 300 && (
          <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => setExpanded(!expanded)}>
            {expanded ? 'Show less' : 'Show more'}
          </Button>
        )}

        {/* Metadata row */}
        <div className="flex flex-wrap gap-1.5 text-[10px]">
          {hit.source && <Badge variant="secondary" className="text-[10px]">{hit.source}</Badge>}
          {hit.chunk_index != null && (
            <Badge variant="secondary" className="text-[10px]">Chunk {hit.chunk_index}</Badge>
          )}
          {hit.doc_version != null && (
            <Badge variant="secondary" className="text-[10px]">v{hit.doc_version}</Badge>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
