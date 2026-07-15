from functools import lru_cache
from typing import Sequence


EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
RERANKER_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
EMBEDDING_DIMENSIONS = 384


@lru_cache(maxsize=1)
def get_embedding_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL_NAME)


@lru_cache(maxsize=1)
def get_reranker_model():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(RERANKER_MODEL_NAME)


def get_embedding(text: str) -> list[float]:
    embedding = get_embedding_model().encode(
        text,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return embedding.astype(float).tolist()


def get_embeddings(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    embeddings = get_embedding_model().encode(
        list(texts),
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return [embedding.astype(float).tolist() for embedding in embeddings]


def rerank_chunks(question: str, chunks: Sequence, top_n: int = 5) -> list:
    if not chunks:
        return []

    pairs = [(question, chunk.chunk) for chunk in chunks]
    scores = get_reranker_model().predict(pairs)
    ranked = sorted(zip(chunks, scores), key=lambda item: float(item[1]), reverse=True)
    return [chunk for chunk, _ in ranked[:top_n]]
