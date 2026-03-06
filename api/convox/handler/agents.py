from fastapi import APIRouter

router = APIRouter(prefix="/v1/agents", tags=["agents"])


@router.get("")
async def list_agents() -> dict:
    return {"agents": [], "total": 0}


@router.post("")
async def create_agent() -> dict:
    return {"message": "not implemented yet"}


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    return {"message": "not implemented yet", "agent_id": agent_id}
