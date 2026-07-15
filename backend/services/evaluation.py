import re
from typing import Literal, TypedDict

from backend.services.embeddings import get_embeddings


class GroundednessResult(TypedDict):
    label: Literal["Grounded", "Hallucination Risk"]
    score: float
    reason: str


def split_sentences(text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if sentence.strip()
    ]


def extract_citation_numbers(answer: str) -> set[int]:
    return {int(match) for match in re.findall(r"\[(\d+)\]", answer)}


def strip_reference_prefix(reference: str) -> str:
    return re.sub(r"^\[\d+\](?:\s*\([^)]*\))?\s*", "", reference).strip()


def dot_product(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def evaluate_groundedness(
    question: str,
    answer: str,
    references: list[str],
) -> GroundednessResult:
    if not references:
        return {
            "label": "Hallucination Risk",
            "score": 0.0,
            "reason": "No document references were retrieved for this answer.",
        }

    valid_citation_numbers = set(range(1, len(references) + 1))
    answer_sentences = split_sentences(re.sub(r"\[\d+\]", "", answer))
    reference_texts = [strip_reference_prefix(reference) for reference in references]
    if not answer_sentences or not reference_texts:
        return {
            "label": "Hallucination Risk",
            "score": 0.0,
            "reason": "The answer or retrieved references were empty.",
        }

    original_sentences = split_sentences(answer)
    sentence_citation_hits = 0
    invalid_citations = set()
    for sentence in original_sentences:
        sentence_citations = extract_citation_numbers(sentence)
        invalid_citations.update(sentence_citations - valid_citation_numbers)
        if sentence_citations & valid_citation_numbers:
            sentence_citation_hits += 1

    citation_coverage = sentence_citation_hits / max(len(original_sentences), 1)
    sentence_embeddings = get_embeddings(answer_sentences)
    reference_embeddings = get_embeddings(reference_texts)
    best_sentence_scores = [
        max(dot_product(sentence_embedding, reference_embedding) for reference_embedding in reference_embeddings)
        for sentence_embedding in sentence_embeddings
    ]
    semantic_support = sum(best_sentence_scores) / len(best_sentence_scores)
    score = max(0.0, min(1.0, (0.75 * semantic_support) + (0.25 * citation_coverage)))

    if invalid_citations:
        reason = (
            f"Semantic support {semantic_support:.0%}; citation coverage {citation_coverage:.0%}. "
            f"Invalid citation numbers used: {sorted(invalid_citations)}."
        )
    else:
        reason = f"Semantic support {semantic_support:.0%}; citation coverage {citation_coverage:.0%}."

    return {
        "label": "Grounded" if score >= 0.75 else "Hallucination Risk",
        "score": score,
        "reason": reason,
    }
