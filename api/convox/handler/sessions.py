from fastapi import APIRouter

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


@router.get("")
async def list_sessions() -> dict:
    return {"sessions": [], "total": 0}


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict:
    return {"message": "not implemented yet", "session_id": session_id}
