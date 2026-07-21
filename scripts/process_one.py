"""Process a single text file and add to RAG. Exits completely after done."""
import sys, time, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

from src.config import load_settings
from src.rag.chunker import chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

txt_path = Path(sys.argv[1])
source = txt_path.stem
category = sys.argv[2]

settings = load_settings()
settings.vectordb_dir.mkdir(parents=True, exist_ok=True)
embedder = Embedder(model_name=settings.embedding_model)
retriever = Retriever(str(settings.vectordb_dir), embedder)

text = txt_path.read_text(encoding="utf-8").strip()
if len(text) < 100:
    print(f"SKIP: {source} ({len(text)} chars)")
    sys.exit(0)

chunks = chunk_text(text, source=source, author="", category=category, chunk_size=800, overlap=80)
if not chunks:
    print(f"NOCHUNKS: {source}")
    sys.exit(0)

t0 = time.time()
retriever.add_chunks(chunks)
elapsed = time.time() - t0
print(f"OK: {source} ({len(chunks)} chunks, {len(text)} chars) in {elapsed:.1f}s")
