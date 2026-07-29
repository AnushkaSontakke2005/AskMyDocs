import json
from time import perf_counter
from typing import List, Optional, TypedDict

from anyio import sleep
from fastapi import APIRouter, Depends, HTTPException
from peewee import SQL

from backend.schemas import ChatAskRequest, ChatAskResponse, ChatMessageResponse, QuestionQuotaResponse
from backend.security import get_current_api_user
from backend.services.constants import RESPOND_TO_MESSAGE_SYSTEM_PROMPT
from backend.services.cost_tracking import record_llm_usage
from backend.db import ChatMessages, DocumentInformationChunks, Documents, Users, initialize_database
from backend.services.embeddings import get_embedding, rerank_chunks
from backend.services.evaluation import evaluate_groundedness
from backend.services.openai_client import openai_client
from backend.services.rate_limit import DAILY_QUESTION_LIMIT, get_remaining_questions, increment_question_count


router = APIRouter(prefix="/chat", tags=["Chat"])


class RetrievedReference(TypedDict):
    citation: int
    text: str
    page_number: Optional[int]
    chunk_index: Optional[int]


def serialize_message(row: ChatMessages) -> ChatMessageResponse:
    return ChatMessageResponse(
        role=row.role,
        content=row.content,
        references=json.loads(row.references) if row.references else None,
        groundedness_label=row.groundedness_label,
        groundedness_score=row.groundedness_score,
        groundedness_reason=row.groundedness_reason,
    )


def format_reference(reference: RetrievedReference) -> str:
    location_parts = []
    if reference["page_number"] is not None:
        location_parts.append(f"page {reference['page_number']}")
    if reference["chunk_index"] is not None:
        location_parts.append(f"chunk {reference['chunk_index']}")
    location = f" ({', '.join(location_parts)})" if location_parts else ""
    return f"[{reference['citation']}]{location} {reference['text']}"


def get_related_chunks(user_id: int, question: str, document_id: Optional[int]) -> List[RetrievedReference]:
    initialize_database(require_vector=True)
    query_embedding = get_embedding(question)
    base_query = (
        DocumentInformationChunks.select(
            DocumentInformationChunks.chunk,
            DocumentInformationChunks.page_number,
            DocumentInformationChunks.chunk_index,
        )
        .join(Documents)
        .where(Documents.user_id == user_id)
    )

    if document_id is not None:
        document = Documents.get_or_none(
            (Documents.id == document_id)
            & (Documents.user_id == user_id)
        )
        if not document:
            raise HTTPException(status_code=404, detail="Document not found.")
        base_query = base_query.where(DocumentInformationChunks.document_id == document_id)

    result = (
        base_query.order_by(SQL("embedding <-> %s::vector", (str(query_embedding),)))
        .limit(20)
        .execute()
    )
    reranked_rows = rerank_chunks(question, list(result), top_n=5)
    return [
        {
            "citation": index + 1,
            "text": row.chunk,
            "page_number": row.page_number,
            "chunk_index": row.chunk_index,
        }
        for index, row in enumerate(reranked_rows)
    ]


@router.post("/ask", response_model=ChatAskResponse)
async def ask_question(payload: ChatAskRequest, current_user: Users = Depends(get_current_api_user)):
    remaining_questions = get_remaining_questions(current_user.id)
    if remaining_questions <= 0:
        raise HTTPException(
            status_code=429,
            detail=f"You have reached the daily limit of {DAILY_QUESTION_LIMIT} questions.",
        )

    selected_document_name = payload.selected_document_name
    if payload.document_id is not None:
        document = Documents.get_or_none(
            (Documents.id == payload.document_id)
            & (Documents.user_id == current_user.id)
        )
        if not document:
            raise HTTPException(status_code=404, detail="Document not found.")
        selected_document_name = document.name

    increment_question_count(current_user.id)
    related_chunks = get_related_chunks(current_user.id, payload.question, payload.document_id)
    formatted_references = [format_reference(reference) for reference in related_chunks]

    ChatMessages.create(
        user_id=current_user.id,
        document_id=payload.document_id,
        selected_document_name=selected_document_name,
        role="user",
        content=payload.question,
    )

    history = (
        ChatMessages.select()
        .where(
            (ChatMessages.user_id == current_user.id)
            & (ChatMessages.selected_document_name == selected_document_name)
        )
        .order_by(ChatMessages.created_at.asc())
    )

    total_retries = 0
    model = "llama-3.3-70b-versatile"
    while True:
        try:
            started_at = perf_counter()
            output = await openai_client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": RESPOND_TO_MESSAGE_SYSTEM_PROMPT.replace(
                            "{{knowledge}}",
                            "\n\n".join(formatted_references),
                        ),
                    },
                    *[
                        {"role": row.role, "content": row.content}
                        for row in history
                    ],
                ],
                temperature=0.1,
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0,
            )
            latency_ms = int((perf_counter() - started_at) * 1000)
            record_llm_usage(
                user_id=current_user.id,
                operation="answer_generation",
                model=model,
                response=output,
                latency_ms=latency_ms,
            )
            answer = output.choices[0].message.content or ""
            groundedness = evaluate_groundedness(payload.question, answer, formatted_references)

            ChatMessages.create(
                user_id=current_user.id,
                document_id=payload.document_id,
                selected_document_name=selected_document_name,
                role="assistant",
                content=answer,
                references=json.dumps(formatted_references) if formatted_references else None,
                groundedness_label=groundedness["label"],
                groundedness_score=groundedness["score"],
                groundedness_reason=groundedness["reason"],
            )

            return ChatAskResponse(
                answer=answer,
                groundedness_label=groundedness["label"],
                groundedness_score=groundedness["score"],
                groundedness_reason=groundedness["reason"],
                references=formatted_references,
                remaining_questions=get_remaining_questions(current_user.id),
            )
        except HTTPException:
            raise
        except Exception as exc:
            total_retries += 1
            if total_retries > 5:
                raise HTTPException(status_code=500, detail=str(exc))
            await sleep(1)


@router.get("/history", response_model=List[ChatMessageResponse])
def chat_history(
    selected_document_name: str = "All Documents",
    current_user: Users = Depends(get_current_api_user),
):
    rows = (
        ChatMessages.select()
        .where(
            (ChatMessages.user_id == current_user.id)
            & (ChatMessages.selected_document_name == selected_document_name)
        )
        .order_by(ChatMessages.created_at.asc())
    )
    return [serialize_message(row) for row in rows]


@router.get("/quota", response_model=QuestionQuotaResponse)
def question_quota(current_user: Users = Depends(get_current_api_user)):
    return QuestionQuotaResponse(
        daily_limit=DAILY_QUESTION_LIMIT,
        remaining_questions=get_remaining_questions(current_user.id),
    )
