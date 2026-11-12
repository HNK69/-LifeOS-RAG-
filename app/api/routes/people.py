"""
people.py

People and identity management endpoints for LifeOS Phase 11.
Provides face tagging and identity confirmation without ever exposing
raw face vectors or sensitive internal embeddings.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import require_auth
from app.api.schemas import (
    PeopleListResponse,
    PersonConfirmRequest,
    PersonConfirmResponse,
    PersonSummary,
)
from app.knowledge.people import (
    confirm_person_identity,
    get_all_people,
    get_person,
)

router = APIRouter(prefix="/api/people", tags=["People"])


@router.get(
    "",
    response_model=PeopleListResponse,
    summary="List Recognized People",
    description="Returns summaries of recognized individuals. Raw face vectors are strictly excluded.",
)
async def list_people(_user=Depends(require_auth)):
    records = get_all_people()
    people_summaries = [
        PersonSummary(
            person_id=str(p.get("person_id")),
            label=p.get("label"),
            status=p.get("status", "unknown"),
            is_user=bool(p.get("is_user")),
            created_at=p.get("created_at", ""),
        )
        for p in records
    ]
    return PeopleListResponse(people=people_summaries, count=len(people_summaries))


@router.post(
    "/{person_id}/confirm",
    response_model=PersonConfirmResponse,
    summary="Confirm Person Identity",
    description="Assigns a user-confirmed label to an identified person record.",
)
async def confirm_person(
    person_id: str,
    body: PersonConfirmRequest,
    _user=Depends(require_auth),
):
    person = get_person(person_id)
    if not person:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Person with ID '{person_id}' not found",
        )

    try:
        confirm_person_identity(person_id=person_id, label=body.name)
        return PersonConfirmResponse(
            person_id=person_id,
            label=body.name,
            status="confirmed",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to confirm person identity: {exc}",
        )
