from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.auth import DUMMY_PASSWORD_HASH, TeamTokens, Viewer, verify_password
from app.models import AuditLog, TeamUser

router = APIRouter(prefix="/api/auth", tags=["team auth"])


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class UserView(BaseModel):
    id: str
    email: str
    name: str
    role: str

    @classmethod
    def of(cls, user: TeamUser) -> "UserView":
        return cls(id=str(user.id), email=user.email, name=user.name, role=user.role)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth token type, not a secret
    user: UserView


@router.post("/login")
async def login(body: LoginBody, request: Request) -> LoginResponse:
    async with request.app.state.sessionmaker() as db:
        user: TeamUser | None = await db.scalar(
            select(TeamUser).where(func.lower(TeamUser.email) == body.email.strip().lower())
        )
        valid = verify_password(body.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
        if user is None or not valid or user.disabled:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
        db.add(AuditLog(actor="team", event="team.login", payload={"user_id": str(user.id)}))
        await db.commit()
    tokens: TeamTokens = request.app.state.team_tokens
    return LoginResponse(access_token=tokens.issue(user), user=UserView.of(user))


@router.get("/me")
async def me(user: Viewer) -> UserView:
    return UserView.of(user)
