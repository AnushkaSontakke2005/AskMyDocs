import asyncio
import re
from io import BytesIO
from time import perf_counter
from typing import Callable, Optional, TypedDict

import pdftotext
from anyio import sleep
from pydantic import BaseModel

from backend.services.constants import GET_MATCHING_TAGS_SYSTEM_PROMPT
from backend.services.cost_tracking import record_llm_usage
from backend.db import DocumentInformationChunks, DocumentTags, Documents, Tags, db, initialize_database
from backend.services.embeddings import get_embeddings
from backend.services.openai_client import openai_client
from backend.services.utils import find


CHUNK_TOKEN_LENGTH = 700
CHUNK_TOKEN_OVERLAP = 120


class GeneratedMatchingTags(BaseModel):
    tags: list[str]


class PreparedChunk(TypedDict):
    text: str
    page_number: int
    chunk_index: int


def get_retry_delay(error: Exception) -> float:
    retry_match = re.search(r"try again in ([\d.]+)s", str(error), re.IGNORECASE)
    if retry_match:
        return float(retry_match.group(1)) + 1
    return 5


async def get_matching_tags(
    user_id: int,
    pdf_text: str,
    document_processing_job_id: Optional[int] = None,
):
    tags_result = list(Tags.select().where(Tags.user_id == user_id))
    tags = [tag.name.lower() for tag in tags_result]
    if not tags:
        return []

    total_retries = 0
    while True:
        try:
            model = "llama-3.3-70b-versatile"
            started_at = perf_counter()
            output = await openai_client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": GET_MATCHING_TAGS_SYSTEM_PROMPT.replace("{{tags_to_match_with}}", str(tags)),
                    },
                    {"role": "user", "content": pdf_text},
                ],
                temperature=0.1,
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0,
                response_format={"type": "json_object"},
            )
            latency_ms = int((perf_counter() - started_at) * 1000)
            record_llm_usage(
                user_id=user_id,
                operation="document_tag_matching",
                model=model,
                response=output,
                latency_ms=latency_ms,
                document_processing_job_id=document_processing_job_id,
            )
            if not output.choices[0].message.content:
                raise Exception("Empty response for generating matching tags.")
            matching_tag_names = GeneratedMatchingTags.model_validate_json(output.choices[0].message.content).tags
            matching_tag_ids: list[int] = []
            for tag_name in matching_tag_names:
                matching_tag = find(lambda tag: tag.name.lower() == tag_name.lower(), tags_result)
                if matching_tag:
                    matching_tag_ids.append(matching_tag.id)
            print(f"Generated matching tags {str(matching_tag_names)} for pdf text.")
            return matching_tag_ids
        except Exception as e:
            total_retries += 1
            if total_retries > 5:
                raise e
            retry_delay = get_retry_delay(e)
            await sleep(retry_delay)
            print(f"Failed to generate matching tags for pdf with this err: {e}. Retrying in {retry_delay}s...")


async def process_pdf_text(
    user_id: int,
    pdf_text: str,
    progress_callback: Callable[[int, str], None],
    document_processing_job_id: Optional[int] = None,
):
    progress_callback(75, "Matching tags")
    matching_tag_ids = await get_matching_tags(user_id, pdf_text[0:5000], document_processing_job_id)
    return matching_tag_ids


def tokenize_text(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def prepare_token_chunks(pages: list[str]) -> list[PreparedChunk]:
    chunks: list[PreparedChunk] = []
    chunk_index = 0
    step = CHUNK_TOKEN_LENGTH - CHUNK_TOKEN_OVERLAP

    for page_number, page_text in enumerate(pages, start=1):
        tokens = tokenize_text(page_text)
        if not tokens:
            continue

        for start in range(0, len(tokens), step):
            window = tokens[start : start + CHUNK_TOKEN_LENGTH]
            if not window:
                continue
            chunks.append(
                {
                    "text": " ".join(window),
                    "page_number": page_number,
                    "chunk_index": chunk_index,
                }
            )
            chunk_index += 1
            if start + CHUNK_TOKEN_LENGTH >= len(tokens):
                break

    return chunks


def upload_document_sync(
    user_id: int,
    name: str,
    pdf_file: bytes,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    document_processing_job_id: Optional[int] = None,
):
    initialize_database(require_vector=True)
    progress_callback = progress_callback or (lambda progress, message: None)
    progress_callback(5, "Parsing PDF")
    parsed_pdf = pdftotext.PDF(BytesIO(pdf_file))
    pages = list(parsed_pdf)
    pdf_text = "\n\n".join(pages)
    prepared_chunks = prepare_token_chunks(pages)

    progress_callback(10, f"Prepared {len(prepared_chunks)} token chunks")
    event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(event_loop)
    try:
        matching_tag_ids = event_loop.run_until_complete(
            process_pdf_text(user_id, pdf_text, progress_callback, document_processing_job_id)
        )
    finally:
        event_loop.close()

    progress_callback(85, "Saving document")
    with db.atomic() as transaction:
        document_id = Documents.insert(name=name, user_id=user_id).execute()
        if prepared_chunks:
            embeddings = get_embeddings([chunk["text"] for chunk in prepared_chunks])
            DocumentInformationChunks.insert_many(
                [
                    {
                        "document_id": document_id,
                        "chunk": chunk["text"],
                        "embedding": embedding,
                        "page_number": chunk["page_number"],
                        "chunk_index": chunk["chunk_index"],
                    }
                    for chunk, embedding in zip(prepared_chunks, embeddings)
                ]
            ).execute()
        if matching_tag_ids:
            DocumentTags.insert_many(
                [
                    {"document_id": document_id, "tag_id": tag_id}
                    for tag_id in matching_tag_ids
                ]
            ).execute()
        transaction.commit()

    progress_callback(100, "Completed")
    print(
        f"Inserted {len(prepared_chunks)} chunks for pdf {name} "
        f"with document id {document_id} and {len(matching_tag_ids)} matching tags."
    )
    return document_id
