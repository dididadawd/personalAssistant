import chromadb
import google.generativeai as genai
import uuid
import time
import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Literal
from dataclasses import dataclass, asdict
import hashlib
import math
import tempfile
import threading
import contextlib
from datetime import datetime, timezone
import random
from collections import OrderedDict


import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.config import Config

# ------------------- Configuration & Defaults -------------------
DEFAULT_EMBEDDING_MODEL = Config.EMBEDDING_MODEL
DEFAULT_RERANKING_MODEL = Config.GEMINI_MODEL_NAME
DEFAULT_CACHE_NAME = "embedding_cache.json"
DEFAULT_DEDUP_SIMILARITY = Config.MEMORY_DEDUP_SIM
DEFAULT_MAX_CACHE_ITEMS = Config.MEMORY_MAX_CACHE_ITEMS
DEFAULT_MAX_SUMMARY_CHARS = Config.MEMORY_MAX_SUMMARY_CHARS
DEFAULT_RERANK_TOP_K = Config.MEMORY_RERANK_TOP_K

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ensure user provided API key
GOOGLE_GEMINI_API_KEY = Config.GOOGLE_GEMINI_API_KEY
if not GOOGLE_GEMINI_API_KEY:
    raise ValueError("FATAL: GOOGLE_GEMINI_API_KEY environment variable not set.")

genai.configure(api_key=GOOGLE_GEMINI_API_KEY)


# ------------------- Utility Helpers -------------------
class SimpleFileLock:
    """A tiny cross-platform file-based advisory lock using atomic create.

    Notes:
    - Not perfect (lockfile might remain if process is killed), but simple and works
      for most single-server use cases where processes play nice.
    - Timeout will raise TimeoutError if lock cannot be acquired.
    """

    def __init__(self, lock_path: Path, timeout: float = 10.0, poll_interval: float = 0.08):
        self.lockfile = Path(str(lock_path) + ".lock")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fd = None

    def acquire(self):
        start = time.time()
        while True:
            try:
                # O_EXCL ensures atomic creation; fails if file exists
                fd = os.open(self.lockfile, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                self._fd = fd
                return True
            except FileExistsError:
                if time.time() - start > self.timeout:
                    raise TimeoutError(f"Timeout acquiring lock {self.lockfile}")
                time.sleep(self.poll_interval)

    def release(self):
        try:
            if self._fd:
                os.close(self._fd)
                self._fd = None
            if self.lockfile.exists():
                os.remove(self.lockfile)
        except Exception:
            pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


def atomic_write_json(path: Path, obj: Any):
    """Safely write JSON to path using atomic replace.

    Keeps file consistent across crashes.
    """
    tmp = None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tf:
            json.dump(obj, tf, ensure_ascii=False, indent=None)
            tmp = Path(tf.name)
        os.replace(str(tmp), str(path))
    finally:
        if tmp and tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two dense vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom == 0:
        return 0.0
    return dot / denom


# ------------------- Main VectorMemory Class -------------------
SearchStrategy = Literal["vector", "llm_rerank"]


@dataclass
class MemoryMetrics:
    embed_calls: int = 0
    embed_cache_hits: int = 0
    embed_cache_misses: int = 0
    rerank_calls: int = 0
    memory_adds: int = 0
    memory_updates: int = 0
    searches: int = 0


class VectorMemory:
    """Robust, efficient vector memory for AI agents.

    Major features:
    - Persistent embedding cache (file-based) with safe atomic writes + lightweight file-locking
    - In-process LRU cache for fast repeated lookups
    - Deduplication / merge by cosine similarity when adding similar memories
    - Optional LLM-based re-ranking with JSON output parsing
    - Metrics for observability
    - Safe, resilient API with retries + jitter/backoff
    """

    def __init__(
        self,
        agent_name: str,
        api_key_manager: 'ApiKeyRotator',
        base_path: str = "personas",
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        reranking_model: str = DEFAULT_RERANKING_MODEL,
        dedup_similarity: float = DEFAULT_DEDUP_SIMILARITY,
        max_cache_items: int = DEFAULT_MAX_CACHE_ITEMS,
        max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS,
        rerank_top_k: int = DEFAULT_RERANK_TOP_K,
        enable_rerank: bool = True,
    ):
        self.api_key_manager = api_key_manager # <-- שמירת המנהל כמשתנה של האובייקט
        self.agent_name = agent_name
        self.base_path = Path(base_path)
        self.memory_path = self.base_path / self.agent_name / "memory"
        self.memory_path.mkdir(parents=True, exist_ok=True)

        self._cache_path = self.memory_path / DEFAULT_CACHE_NAME
        self._cache_lock = SimpleFileLock(self._cache_path)

        self.embedding_model = embedding_model
        self.reranking_model_name = reranking_model
        self.dedup_similarity = dedup_similarity
        self.max_cache_items = max_cache_items
        self.max_summary_chars = max_summary_chars
        self.rerank_top_k = rerank_top_k
        self.enable_rerank = enable_rerank

        # In-process LRU cache keyed by hash
        self._lru_cache: "OrderedDict[str, List[float]]" = OrderedDict()
        self._lru_capacity = min(1024, max(128, int(self.max_cache_items / 10)))

        # Persistent cache loaded from disk: structure {hash: embedding_list}
        self._persistent_cache: Dict[str, List[float]] = self._load_embedding_cache()

        # Metrics
        self.metrics = MemoryMetrics()

        # Initialize chroma
        try:
            self.client = chromadb.PersistentClient(path=str(self.memory_path))
            collection_name = f"{self.agent_name}_memory_v3"
            self.collection = self.client.get_or_create_collection(name=collection_name)
        except Exception as e:
            logger.error("Failed to initialize ChromaDB for '%s': %s", self.agent_name, e, exc_info=True)
            raise

        # LLM model wrapper for reranking; instantiate lazily
        self._rerank_model = None 
        if self.enable_rerank and not self.reranking_model_name:
            logger.warning("Reranking is enabled but no model name was provided. Disabling.")
            self.enable_rerank = False

        logger.info("Advanced VectorMemory initialized for agent '%s' (rerank=%s).", self.agent_name, self.enable_rerank)

    # ------------------- Embedding cache persistence -------------------
    def _load_embedding_cache(self) -> Dict[str, List[float]]:
        if not self._cache_path.exists():
            return {}
        try:
            with SimpleFileLock(self._cache_path):
                with open(self._cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Newer persistent formats might store extra fields; just return the mapping
                    if isinstance(data, dict):
                        return data
        except Exception as e:
            logger.warning("Could not load embedding cache (will start fresh): %s", e)
        return {}

    def _save_embedding_cache(self):
        # Write atomically while holding the lock
        try:
            with SimpleFileLock(self._cache_path):
                atomic_write_json(self._cache_path, self._persistent_cache)
        except Exception as e:
            logger.error("Failed to write embedding cache: %s", e, exc_info=True)

    def _prune_persistent_cache_if_needed(self):
        if len(self._persistent_cache) > self.max_cache_items:
            # remove oldest entries (dictionary order is insertion order in CPython 3.7+)
            to_remove = len(self._persistent_cache) - self.max_cache_items
            keys = list(self._persistent_cache.keys())[:to_remove]
            for k in keys:
                self._persistent_cache.pop(k, None)

    # ------------------- In-memory LRU helpers -------------------
    def _lru_get(self, key: str) -> Optional[List[float]]:
        v = self._lru_cache.get(key)
        if v is not None:
            # move to end -> most recently used
            try:
                self._lru_cache.move_to_end(key)
            except Exception:
                pass
        return v

    def _lru_set(self, key: str, value: List[float]):
        self._lru_cache[key] = value
        try:
            self._lru_cache.move_to_end(key)
        except Exception:
            pass
        if len(self._lru_cache) > self._lru_capacity:
            try:
                self._lru_cache.popitem(last=False)
            except Exception:
                pass

    # ------------------- Embedding API with caching & retries -------------------
    def _get_embedding(self, text: str, retries: int = 3, base_delay: float = 0.8) -> Optional[List[float]]:
        """
        Return embedding vector for text. Uses LRU in-memory cache then persistent file cache; if missing, calls API.
        Stores embedding in both caches. Keyed by SHA256(text) to keep the persistent JSON compact and stable.
        *** גרסה מתוקנת ועמידה בפני שינויים בפורמט התשובה של ה-API ***
        """
        if not text or not isinstance(text, str):
            return None

        self.metrics.embed_calls += 1
        h = sha256_hex(text)

        # In-memory LRU
        emb = self._lru_get(h)
        if emb is not None:
            self.metrics.embed_cache_hits += 1
            return emb

        # Persistent cache
        emb = self._persistent_cache.get(h)
        if emb is not None:
            self._lru_set(h, emb)
            self.metrics.embed_cache_hits += 1
            return emb

        self.metrics.embed_cache_misses += 1

        # Not found: call the API with robust retry/backoff
        for attempt in range(retries):
            try:
                result = self.api_key_manager.embed_content(model_name=self.embedding_model, contents=text)
                
                # --- >>> התחלת התיקון הקריטי <<< ---
                embedding = None
                if isinstance(result, dict) and 'embedding' in result:
                    # זה המקרה הצפוי והתקין
                    embedding = result['embedding']
                elif isinstance(result, list):
                    # זה המקרה הלא צפוי שגרם לשגיאה. התוצאה היא בעצמה הווקטור.
                    logger.warning("API returned a raw list for embedding, not a dictionary. Adapting.")
                    embedding = result
                elif hasattr(result, 'embeddings') and result.embeddings:
                    embedding = result.embeddings[0].values
                else:
                    # אם קיבלנו משהו אחר, זו שגיאה אמיתית
                    raise RuntimeError(f"Unexpected embedding result type: {type(result)}")
                # --- >>> סוף התיקון הקריטי <<< ---

                if not isinstance(embedding, list):
                    raise RuntimeError("Processed embedding is not a list, which is invalid.")
                
                # Store in both caches
                self._persistent_cache[h] = embedding
                self._prune_persistent_cache_if_needed()
                try:
                    self._save_embedding_cache()
                except Exception:
                    logger.debug("Failed to persist embedding cache immediately; continuing.")
                self._lru_set(h, embedding)
                return embedding
            except Exception as e:
                msg = str(e).lower()
                if '429' in msg or 'rate' in msg or 'quota' in msg:
                    wait = base_delay * (2 ** attempt) + random.random() * 0.3
                    logger.warning("Embedding rate-limited or quota issue; retrying in %.2fs (attempt %d): %s", wait, attempt + 1, e)
                    time.sleep(wait)
                    continue
                else:
                    logger.error("Embedding failed irrecoverably: %s", e, exc_info=True)
                    return None
        logger.error("Failed to get embedding after %d retries.", retries)
        return None

    # ------------------- Summarization helper -------------------
    def _create_searchable_summary(self, user_prompt: str, final_answer: str, max_chars: Optional[int] = None) -> str:
        """Use the LLM to create a short, searchable summary. Fall back to simple concatenation.

        We try to keep the summary short and meaningful. max_chars may be used to truncate prompts.
        """
        if max_chars is None:
            max_chars = self.max_summary_chars

        try:
            up = (user_prompt or '').strip()
            fa = (final_answer or '').strip()
            # Light pre-truncation to avoid extremely large prompts
            if len(up) > max_chars:
                up = up[:max_chars] + '...'
            if len(fa) > max_chars:
                fa = fa[:max_chars] + '...'

            prompt = (
                "You are a concise summarizer. Produce a one-sentence, action-focused summary suitable for semantic search. "
                "Do NOT hallucinate. Keep it neutral and factual.\n\n"
                f"User Request: \"{up}\"\n"
                f"AI Answer: \"{fa}\"\n\n"
                "One-sentence summary for search:" 
            )

            # If rerank model is available, use it for summarization because it's fast for short prompts
            if self._rerank_model:
                resp = self.api_key_manager.generate_content(prompt, model_name=self.reranking_model_name)
                summary = resp.text.strip()
                # Defensive: if LLM returns empty or too long, fallback
                if not summary:
                    raise RuntimeError("Empty summary from LLM")
                if len(summary) > max_chars:
                    summary = summary[:max_chars].rstrip() + '...'
                return summary
            else:
                # No LLM: fallback simple heuristics
                s = f"User asked: {up[:120]} | AI answered: {fa[:120]}"
                return s
        except Exception as e:
            logger.warning("Summarization failed; falling back to heuristic: %s", e)
            return f"User asked: {user_prompt[:120]} | AI answered: {final_answer[:120]}"

    # ------------------- Deduplication helpers -------------------
    def _find_most_similar(self, embedding: List[float], top_k: int = 6) -> Optional[Dict[str, Any]]:
        """Query the vector DB for top_k candidates and compute cosine similarity against their stored embeddings in metadata.

        Returns the best candidate metadata dict with the fields: {'id', 'similarity', 'metadata', 'document'} or None.
        """
        try:
            if not embedding:
                return None
            count = max(1, min(top_k, max(1, int(self.collection.count()))))
            results = self.collection.query(
                query_embeddings=[embedding],
                n_results=count,
                include=["metadatas", "documents"],
            )
            docs = results.get('documents', [[]])[0]
            metas = results.get('metadatas', [[]])[0]
            ids = results.get('ids', [[]])[0]
            best = None
            for i, meta in enumerate(metas):
                if not meta:
                    continue
                cand_emb_json = meta.get('summary_embedding_json')
                if not cand_emb_json:
                    continue # Skip if the data isn't there

                try:
                    cand_emb = json.loads(cand_emb_json)
                    if not cand_emb:
                        continue
                    sim = cosine_similarity(embedding, cand_emb)
                except (json.JSONDecodeError, TypeError):
                    # If the data is corrupted or not a valid list, just skip this memory item
                    continue
                if best is None or sim > best['similarity']:
                    best = {
                        'id': ids[i] if i < len(ids) else None,
                        'similarity': sim,
                        'metadata': meta,
                        'document': docs[i] if i < len(docs) else None,
                    }
            return best
        except Exception as e:
            logger.warning("_find_most_similar failed: %s", e)
            return None

    # ------------------- Public API: add_memory -------------------
    def add_memory(self, user_prompt: str, final_answer: str, metadata: Optional[Dict[str, Any]] = None):
        """Add a memory: summarize, embed, deduplicate/merge or add.

        If a similar memory exists above the dedup threshold, we update that memory's metadata
        (e.g. append an alias or update timestamps) instead of inserting a duplicate.
        """
        if not user_prompt and not final_answer:
            return
        if len((user_prompt or '') + (final_answer or '')) < 30:
            # skip tiny trivial memories
            return

        summary = self._create_searchable_summary(user_prompt, final_answer)
        # Ensure summary is not empty
        if not summary:
            summary = (user_prompt or '')[:120] + ' | ' + (final_answer or '')[:120]

        embedding = self._get_embedding(summary)
        if not embedding:
            logger.warning("Skipping memory addition because embedding generation failed.")
            return

        # Deduplicate by similarity
        try:
            candidate = self._find_most_similar(embedding, top_k=self.rerank_top_k)
            if candidate and candidate.get('similarity', 0.0) >= self.dedup_similarity:
                # Merge/update existing memory
                cid = candidate.get('id')
                existing_meta = candidate.get('metadata', {})
                # Merge metadata fields conservatively
                new_meta = existing_meta.copy()
                new_meta.setdefault('aliases', [])
                new_alias = {
                    'added_at': datetime.now(timezone.utc).isoformat(),
                    'user_prompt': user_prompt,
                }
                new_meta['aliases'].append(new_alias)
                # Keep the most recent full answers in a short list
                new_meta.setdefault('full_texts', [])
                new_meta['full_texts'].append({'text': final_answer, 'ts': datetime.now(timezone.utc).isoformat()})
                new_meta['last_updated'] = datetime.now(timezone.utc).isoformat()
                # Also keep the stored summary up-to-date
                new_doc = summary
                try:
                    # Try to update in-place; not all Chroma versions support update - be defensive
                    if hasattr(self.collection, 'update'):
                        self.collection.update(ids=[cid], metadatas=[new_meta], documents=[new_doc])
                        self.metrics.memory_updates += 1
                        logger.info("Merged memory %s (sim=%.3f)", cid, candidate['similarity'])
                        return
                    else:
                        # Fallback: add as a new memory
                        logger.info("Chroma collection lacks 'update'; adding merged memory as new entry.")
                except Exception as e:
                    logger.warning("Failed to update existing memory %s: %s", cid, e)

        except Exception as e:
            logger.warning("Deduplication step failed: %s", e)

        # If we get here: no merge happened -> add new memory
        memory_id = str(uuid.uuid4())
        final_metadata = metadata.copy() if metadata else {}
        final_metadata['timestamp'] = datetime.now(timezone.utc).isoformat()
        final_metadata['user_prompt'] = user_prompt
        final_metadata['full_text'] = final_answer
        # Store the summary embedding inside metadata too, to enable efficient similarity checks later
        final_metadata['summary_embedding_json'] = json.dumps(embedding)

        try:
            self.collection.add(
                embeddings=[embedding],
                documents=[summary],
                metadatas=[final_metadata],
                ids=[memory_id]
            )
            self.metrics.memory_adds += 1
            logger.info("Added memory %s (summary='%s'...)", memory_id, summary[:60])
        except Exception as e:
            logger.error("Failed to add memory to ChromaDB: %s", e, exc_info=True)

    # ------------------- Re-ranking -------------------
    def _rerank_results(self, query: str, results: Dict[str, Any]) -> Optional[str]:
        """Use the LLM to pick the best candidate. Expects results to contain documents and metadatas lists.

        Output format requested from LLM: JSON like {"selected_index": 2, "reason": "short text"}
        """
        if not self._rerank_model:
            return None

        docs = results.get('documents', [[]])[0]
        metas = results.get('metadatas', [[]])[0]
        if not docs or not metas:
            return None

        # Build a compact prompt asking for JSON output
        prompt = "You are a re-ranking assistant. Given the user's current query and a numbered list of candidate memories (each with a short summary and context), choose the single most relevant memory. Respond ONLY with JSON: {\"selected_index\": <1-based index>, \"reason\": \"short reason\"}. Do not include anything else.\n\n"
        prompt += f"User Query: \"{query}\"\n\nCandidates:\n"
        for i, (doc, meta) in enumerate(zip(docs, metas), start=1):
            up = meta.get('user_prompt', '')
            ts = meta.get('timestamp', '')
            prompt += f"{i}. Summary: {doc}\n   Original User Prompt: {up} | ts={ts}\n"

        # Limit prompt size by truncating if absurdly long
        if len(prompt) > 8000:
            prompt = prompt[:7600] + "\n...truncated...\n"

        # Try generate with retries
        for attempt in range(3):
            try:
                self.metrics.rerank_calls += 1
                resp = self.api_key_manager.generate_content(prompt, model_name=self.reranking_model_name)
                txt = resp.text.strip()
                # Try to parse JSON out of the text (be robust)
                try:
                    # Find first { ... }
                    start = txt.find('{')
                    end = txt.rfind('}')
                    if start != -1 and end != -1:
                        candidate_json = txt[start:end+1]
                        parsed = json.loads(candidate_json)
                        sel = parsed.get('selected_index')
                        if isinstance(sel, int) and 1 <= sel <= len(docs):
                            chosen_meta = metas[sel-1]
                            return chosen_meta.get('full_text') or docs[sel-1]
                except Exception:
                    # fallback to extract integer
                    digits = ''.join(ch for ch in txt if ch.isdigit())
                    if digits:
                        idx = int(digits)
                        if 1 <= idx <= len(docs):
                            chosen_meta = metas[idx-1]
                            return chosen_meta.get('full_text') or docs[idx-1]
                # If we didn't parse, log and break to fallback
                logger.warning("Reranker returned unparsable response; falling back to vector result. Response was: %s", txt)
                break
            except Exception as e:
                logger.warning("Rerank attempt %d failed: %s", attempt + 1, e)
                time.sleep(0.6 * (2 ** attempt))
                continue
        # fallback to top vector result
        logger.info("Reranking failed; using top vector result as fallback.")
        return metas[0].get('full_text') if metas and metas[0] else None

    # ------------------- Search -------------------
    def search_memory(self, query_text: str, n_results: int = 5, strategy: SearchStrategy = "llm_rerank") -> List[str]:
        """Search stored memories and return the most relevant full text(s).

        strategy: 'llm_rerank' for best-quality (requires LLM), or 'vector' for speed.
        """
        self.metrics.searches += 1
        if not query_text:
            return []

        query_embedding = self._get_embedding(query_text)
        if not query_embedding:
            return []

        try:
            n = min(n_results, max(1, int(self.collection.count())))
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=n,
                include=['metadatas', 'documents']
            )
            if not results or not results.get('ids', [[]])[0]:
                return []

            if strategy == 'llm_rerank' and self.enable_rerank:
                # Limit candidates to rerank_top_k to avoid huge prompts
                # Build a trimmed results object for reranker
                docs = results.get('documents', [[]])[0][:self.rerank_top_k]
                metas = results.get('metadatas', [[]])[0][:self.rerank_top_k]
                trimmed = {'documents': [docs], 'metadatas': [metas]}
                best = self._rerank_results(query_text, trimmed)
                return [best] if best else []
            else:
                # Vector strategy: return full_text fields from metadata in order
                metas = results.get('metadatas', [[]])[0]
                out = []
                for meta in metas:
                    if not meta:
                        continue
                    ft = meta.get('full_text') or meta.get('full_texts')
                    if isinstance(ft, list):
                        # return the last appended
                        out.append(ft[-1].get('text') if ft and isinstance(ft[-1], dict) else '')
                    else:
                        out.append(ft or '')
                return out

        except Exception as e:
            logger.error("Memory search failed: %s", e, exc_info=True)
            return []

    # ------------------- Utilities -------------------
    def export_memory(self, out_path: str):
        """Export all stored documents + metadata to a JSON file for backup.

        Note: In very large DBs this will be slow / memory heavy.
        """
        try:
            all_docs = []
            # Use collection.get or iterate via query - specifics depend on Chroma version
            if hasattr(self.collection, 'get'):
                # Some chroma versions provide a get() method
                res = self.collection.get(include=['documents', 'metadatas', 'ids'])
                docs = res.get('documents', [])
                metas = res.get('metadatas', [])
                ids = res.get('ids', [])
                for i in range(len(ids)):
                    all_docs.append({'id': ids[i], 'doc': docs[i], 'meta': metas[i]})
            else:
                # Fallback: try to query by using an empty vector if possible
                # We'll query small slices to avoid sleeping the DB
                res = self.collection.query(query_embeddings=[[0]*1], n_results=1)
                # If no suitable generic API, raise
                raise NotImplementedError("Export not implemented for this chroma client version")

            atomic_write_json(Path(out_path), all_docs)
            logger.info("Exported memory snapshot to %s", out_path)
        except Exception as e:
            logger.error("Failed to export memory: %s", e, exc_info=True)

    def health_check(self) -> Dict[str, Any]:
        """Return simple health metrics that can be used for monitoring."""
        try:
            count = int(self.collection.count())
        except Exception:
            count = -1
        return {
            'agent': self.agent_name,
            'memory_count': count,
            'embed_cache_size': len(self._persistent_cache),
            'metrics': asdict(self.metrics),
            'rerank_enabled': bool(self._rerank_model),
        }


# ------------------- End of File -------------------
# Usage note: instantiate VectorMemory and then use add_memory/search_memory.
# Example (not part of the module):
# vm = VectorMemory('alice')
# vm.add_memory('How do I make coffee?', 'Use hot water and coffee grounds...')
# vm.search_memory('How to brew coffee?')
