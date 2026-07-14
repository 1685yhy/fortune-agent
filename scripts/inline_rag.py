"""Simple text-by-text RAG builder with explicit progress tracking."""
import sys, time, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

from src.config import load_settings
from src.rag.chunker import chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

settings = load_settings()
settings.vectordb_dir.mkdir(parents=True, exist_ok=True)
log.info("Loading embedder...")
embedder = Embedder(model_name=settings.embedding_model)
retriever = Retriever(str(settings.vectordb_dir), embedder)

books_dir = Path("/mnt/d/fortune-data/books")
all_files = []
for cat_dir in sorted(books_dir.iterdir()):
    if not cat_dir.is_dir(): continue
    for txt in sorted(cat_dir.glob("*.txt")):
        all_files.append((cat_dir.name, txt))

log.info("Total texts: %d", len(all_files))
total_chunks = 0
t_start = time.time()

for i, (category, txt_path) in enumerate(all_files):
    source = txt_path.stem
    text = txt_path.read_text(encoding="utf-8").strip()
    if len(text) < 100:
        continue
    chunks = chunk_text(text, source=source, author="", category=category, chunk_size=800, overlap=80)
    if not chunks:
        continue
    t0 = time.time()
    try:
        retriever.add_chunks(chunks)
        elapsed = time.time() - t0
        total_chunks += len(chunks)
        log.info("[%d/%d] %s: %d chunks (%d chars) in %.1fs", i+1, len(all_files), source, len(chunks), len(text), elapsed)
    except Exception as e:
        log.error("[%d/%d] %s FAILED: %s", i+1, len(all_files), source, e)
        break

log.info("Done! %d chunks in %.1fs. Collection size: %d", total_chunks, time.time()-t_start, retriever.count())
