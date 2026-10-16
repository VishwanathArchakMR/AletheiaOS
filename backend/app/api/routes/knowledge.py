from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from redis.exceptions import RedisError

from app.demo_data import load_demo_data
from app.graph.ingestion import IngestionFormatError
from app.graph.repository import RevisionConflictError

router = APIRouter(tags=["knowledge"])


class IngestSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=512)
    kind: Literal["document", "ticket", "chat", "pull_request", "decision"]
    file_format: Literal["md", "markdown", "txt", "text", "json", "csv"]
    content: str = Field(min_length=1, max_length=1_000_000)
    canonical_url: HttpUrl | None = None


class EvidenceSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=20)


class GraphToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, min_length=1, max_length=200)
    entity_id: str | None = Field(default=None, min_length=1, max_length=512)
    skill_id: str | None = Field(default=None, min_length=1, max_length=512)
    decision_id: str | None = Field(default=None, min_length=1, max_length=512)
    from_id: str | None = Field(default=None, min_length=1, max_length=512)
    to_id: str | None = Field(default=None, min_length=1, max_length=512)
    as_of: str | None = Field(default=None, min_length=10, max_length=40)
    direction: Literal["older", "newer"] | None = None
    limit: int = Field(default=10, ge=1, le=20)


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)


@router.post("/sources", status_code=status.HTTP_201_CREATED)
def ingest_source(payload: IngestSourceRequest, request: Request) -> dict[str, object]:
    repository = request.app.state.graph_repository
    try:
        repository.initialize()
        receipt = repository.ingest_source(
            source_id=payload.source_id,
            kind=payload.kind,
            canonical_url=str(payload.canonical_url) if payload.canonical_url else None,
            file_format=payload.file_format,
            content=payload.content,
        )
    except IngestionFormatError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except RevisionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except (RedisError, TimeoutError, OSError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FalkorDB is unavailable",
        ) from error
    return {
        "source_id": receipt.source_id,
        "revision_id": receipt.revision_id,
        "chunks_processed": receipt.chunks_processed,
        "status": receipt.status,
    }


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: str, request: Request) -> None:
    if not source_id.strip() or len(source_id) > 512:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid source ID",
        )
    try:
        deleted = request.app.state.graph_repository.delete_source(source_id)
    except (RedisError, TimeoutError, OSError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FalkorDB is unavailable",
        ) from error
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return None


@router.post("/evidence/search")
def search_evidence(payload: EvidenceSearchRequest, request: Request) -> dict[str, object]:
    try:
        matches = request.app.state.graph_repository.search_evidence(payload.query, payload.limit)
    except (RedisError, TimeoutError, OSError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FalkorDB is unavailable",
        ) from error
    return {"matches": matches}


@router.post("/tools/{tool_name}")
def run_graph_tool(
    tool_name: str, payload: GraphToolRequest, request: Request
) -> dict[str, object]:
    if tool_name not in request.app.state.graph_repository.tool_names:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown graph tool")
    try:
        results = request.app.state.graph_repository.run_tool(
            tool_name, payload.model_dump(exclude_none=True)
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except (RedisError, TimeoutError, OSError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FalkorDB is unavailable",
        ) from error
    return {"tool": tool_name, "results": results}


@router.post("/answers", response_model=None)
def answer_question(payload: QuestionRequest, request: Request) -> dict[str, object] | JSONResponse:
    try:
        return request.app.state.graph_repository.answer_question(payload.question)
    except (RedisError, TimeoutError, OSError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "temporarily_unavailable",
                "answer": "FalkorDB is temporarily unavailable.",
                "claims": [],
            },
        )


@router.post("/demo/load")
def load_demo(request: Request) -> dict[str, object]:
    repository = request.app.state.graph_repository
    settings = request.app.state.settings
    try:
        repository.initialize()
        receipts = load_demo_data(repository, settings.demo_data_directory)
    except (IngestionFormatError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error
    except (RedisError, TimeoutError, OSError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FalkorDB is unavailable",
        ) from error
    return {
        "sources_loaded": len(receipts),
        "sources": [
            {
                "source_id": receipt.source_id,
                "revision_id": receipt.revision_id,
                "chunks_processed": receipt.chunks_processed,
                "status": receipt.status,
            }
            for receipt in receipts
        ],
    }
