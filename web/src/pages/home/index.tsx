import { useCallback, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { FileText } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

export function HomePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [docId, setDocId] = useState(
    () => searchParams.get('doc_id') || '',
  );

  const handleLoad = useCallback(() => {
    const id = docId.trim();
    if (!id) return;
    // Support "run:123" shorthand → route to /run/123
    if (/^run:\s*(\d+)$/i.test(id)) {
      const runId = id.match(/^run:\s*(\d+)$/i)![1];
      navigate(`/run/${runId}`);
    } else {
      navigate(`/doc/${encodeURIComponent(id)}`);
    }
  }, [docId, navigate]);

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6">
      <div className="flex flex-col items-center gap-3">
        <FileText className="size-12 text-accent opacity-60" />
        <h1 className="text-lg font-bold text-text-primary">
          Atlas Document Editor
        </h1>
        <p className="max-w-sm text-center text-sm text-text-muted">
          Enter a Document ID (filename or doc hash) to load and edit its
          pipeline-generated markdown alongside the source PDF.
        </p>
      </div>

      <div className="flex w-full max-w-md gap-2">
        <Input
          placeholder="e.g. my-file.pdf  or  run:42"
          value={docId}
          onChange={(e) => setDocId(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleLoad()}
          autoFocus
          className="flex-1"
        />
        <Button onClick={handleLoad} disabled={!docId.trim()}>
          Load
        </Button>
      </div>
    </div>
  );
}
