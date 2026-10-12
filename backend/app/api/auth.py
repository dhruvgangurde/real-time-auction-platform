"""Identity endpoints (FR-1 to FR-8). Routers hold no business logic."""

from fastapi import APIRouter, Cookie, Depends, Header, Response, status

from app.api.deps import client_ip, current_user, get_auth_service
from app.core.config import settings
from app.core.errors import AppError
from app.schemas.auth import (
    EmailRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenRequest,
    TokenResponse,
    UserProfile,
)
from app.services.auth import AuthService, CurrentUser, Tokens

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
COOKIE_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, tokens: Tokens) -> TokenResponse:
    # HttpOnly + Secure + SameSite=Strict, scoped to the auth routes (FR-5, NFR-17, NFR-21)
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=tokens.refresh_token,
        max_age=settings.refresh_token_ttl_s,
        path=COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.expires_in)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserProfile)
async def register(
    body: RegisterRequest,
    ip: str = Depends(client_ip),
    auth: AuthService = Depends(get_auth_service),
) -> UserProfile:
    profile = await auth.register(str(body.email), body.password, body.full_name, ip)
    return UserProfile.model_validate(profile)


@router.post("/verify-email")
async def verify_email(
    body: TokenRequest,
    ip: str = Depends(client_ip),
    auth: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    await auth.verify_email(body.token, ip)
    return {"status": "verified"}


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    body: EmailRequest,
    ip: str = Depends(client_ip),
    auth: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    await auth.resend_verification(str(body.email), ip)
    return {"status": "accepted"}


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    response: Response,
    ip: str = Depends(client_ip),
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    tokens = await auth.login(str(body.email), body.password, ip)
    return _set_refresh_cookie(response, tokens)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    x_requested_with: str | None = Header(default=None),
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    # a custom header is something a cross-site form post cannot add (NFR-21)
    if not x_requested_with:
        raise AppError("FORBIDDEN", "X-Requested-With header required", 403)
    tokens = await auth.refresh(refresh_token)
    return _set_refresh_cookie(response, tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    user: CurrentUser = Depends(current_user),
    auth: AuthService = Depends(get_auth_service),
) -> None:
    await auth.logout(user)
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    body: EmailRequest,
    ip: str = Depends(client_ip),
    auth: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    await auth.forgot_password(str(body.email), ip)
    return {"status": "accepted"}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    ip: str = Depends(client_ip),
    auth: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    await auth.reset_password(body.token, body.password, ip)
    return {"status": "reset"}
