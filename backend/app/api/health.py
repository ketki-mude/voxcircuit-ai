from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()


@router.get("/api/health")
async def health(request: Request) -> dict[str, str]:
    async with request.app.state.sessionmaker() as db:
        await db.execute(text("SELECT 1"))
    return {"status": "ok"}
