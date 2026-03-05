# Build Variants — Dockerfile vs Dockerfile.slim

Project Atlas offers two Docker build configurations to match different deployment scenarios.

## Quick Comparison

| Aspect | Full  (`Dockerfile`) | Slim (`Dockerfile.slim`) |
|--------|---|---|
| **Image Size** | ~13.8 GB | ~1.5–2.0 GB |
| **Build Time** | 30–40 min | 5–10 min |
| **Memory (runtime)** | ~6 GB | ~1–2 GB |
| **Docling Support** | ✓ Full PDF parsing + OCR + layout analysis | ✗ Removed |
| **VLM Support** | ✓ Full | ✓ Full |
| **LLM Serving** | ✓ Can bundle LLMs (with extras) | ✓ Must be external |
| **Ideal For** | Enterprise, multi-method ingest | VLM-only, edge deployments, dev |

## Full Dockerfile

**Use when:**
- Ingesting PDFs via **both** Docling and VLM methods
- Automatic layout analysis required (tables, reading order, figure detection)
- Supporting legacy workflows
- E2E testing with all pipelines

**What's included:**
- Docling with onnxruntime, torch, layout models (~7 GB of deps)
- Full OpenCV with GPU support (optional)
- All OCR/ML models auto-downloaded on first run

**Build:**
```bash
docker compose build
```

## Dockerfile.slim

**Use when:**
- **VLM-only ingestion** — all PDFs processed via vision model
- Minimizing container footprint (edge, serverless, CI/CD caching)
- LLMs served externally (LM Studio, OpenAI API, Ollama)
- Quick prototyping without heavy dependency downloads

**What's removed:**
- ✗ `docling` — not needed for VLM path
- ✗ `onnxruntime` — ML inference runtime (~5 GB)
- ✗ `torch`, transformers — deep learning frameworks (~3 GB)
- ✗ Layout/OCR models — auto-downloaded by Docling
- ✗ OpenCV — computer vision library (slim base already has Python)

**What's kept:**
- FastAPI, Uvicorn — web server
- SQLAlchemy, Psycopg — database ORM & driver
- Qdrant client — vector store
- PyMuPDF, pdfplumber — lightweight PDF utilities (metadata, tables)
- All pipeline nodes — Judge, Refine, Cleanup, Chunking
- VLM ingest — page rendering, stitching
- RAG endpoints — search, retrieval, QA

**Build:**
```bash
# Option 1: Use the slim compose file
docker compose -f docker-compose.slim.yml build

# Option 2: Override dockerfile path
docker compose build --file Dockerfile.slim attr
```

## Migration Guide

### From Full to Slim

✓ **Safe to switch if:**
- Using only VLM ingestion (not Docling)
- LLMs are served externally
- No layout analysis features in use

⚠ **Requires code changes if:**
- Code explicitly imports Docling modules (`from docling import ...`)
- Pipeline configured to use Docling node

**Steps:**
1. Ensure all PDFs use VLM ingest method
2. Update `docker-compose.yml` to use `Dockerfile.slim`
3. Rebuild: `docker compose build --no-cache`
4. Restart: `docker compose up -d`

### From Slim to Full

1. Revert `docker-compose.yml` to use `Dockerfile`
2. Rebuild: `docker compose build --no-cache`
3. Restart: `docker compose up -d`
4. Docling path is now available

## Implementation Details

### Dockerfile.slim dependency pinning

Explicit version pins in slim variant ensure reproducibility:
```dockerfile
pip install \
    fastapi==0.110.0 \
    uvicorn[standard]==0.27.0 \
    sqlalchemy==2.0.25 \
    'psycopg[binary]==3.1.18' \
    qdrant-client>=1.9.0 \
    PyMuPDF==1.24.0 \
    ...
```

Full `Dockerfile` uses `pyproject.toml` with looser version constraints for flexibility.

### OS Libraries Reduction

**Full:** Installs X11/OpenCV dependencies (~200 MB)
```dockerfile
RUN apt-get install libgl1 libglib2.0-0 libx11-6 libxcb1 ...
```

**Slim:** Only essential PostgreSQL client
```dockerfile
RUN apt-get install libpq5
```

## Performance Notes

### Build Performance

| Variant | First Build | Cached Build | Pull Time |
|---------|---|---|---|
| Full | 30–40 min | 2–3 min (cpp recompile) | 10 min |
| Slim | 5–10 min | 30 sec | 1 min |

Slim variant is 7x faster to build and pull due to:
- No torch/CUDA compilation
- No model auto-downloads
- Smaller layer cache hits

### Runtime Performance

No performance difference in request handling. All computation patterns are identical. Image size savings translate to:
- Faster cold starts
- Lower K8s scheduling latency
- Reduced storage cost

## Documentation References

- **VLM Ingest**: [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md#vlm-ingestion)
- **Pipeline Architecture**: [ARCHITECTURE.md](ARCHITECTURE.md)
- **Configuration**: [config/pipeline.yaml.example](config/pipeline.yaml.example)

## When to Use Each

### Use **Full (Dockerfile)**:
```
Company has multi-source PDF warehouse
├─ Some PDFs → Docling (legacy system integration)
├─ Some PDFs → VLM (high-fidelity requirement)
└─ All PDFs → Judge/Refine/RAG (pipeline is unified)
```

### Use **Slim (Dockerfile.slim)**:
```
New system, VLM-first architecture
├─ All PDFs → VLM (consistent quality)
├─ LLMs → External service (LM Studio, OpenAI)
└─ Build once, deploy everywhere (edge-friendly)
```

---

**Last Updated:** 2026-03-05  
**Maintainer Note:** These variants should be kept in sync. When adding dependencies to `pyproject.toml`, update both Dockerfiles. When removing Docling or adding lightweight alternatives, document the rationale here.
