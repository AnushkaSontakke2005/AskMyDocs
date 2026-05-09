import traceback

from fastapi import APIRouter, Depends, HTTPException, status
from peewee import IntegrityError

from backend.schemas import LoginRequest, SignupRequest, TokenResponse, UserResponse
from backend.security import (
    authenticate_user,
    create_access_token,
    get_current_api_user,
    hash_password,
    normalize_email,
)
from backend.db import Users, initialize_database


router = APIRouter(prefix="/auth", tags=["Auth"])


def serialize_user(user: Users) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, is_admin=bool(user.is_admin))


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest):
    try:
        initialize_database()
        email = normalize_email(payload.email)
        if Users.get_or_none(Users.email == email):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists.",
            )

        should_be_admin = Users.select().count() == 0
        user = Users.create(
            email=email,
            password_hash=hash_password(payload.password),
            is_admin=should_be_admin,
        )
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Auth signup failed: {type(exc).__name__}: {exc}",
        )

    return TokenResponse(access_token=create_access_token(user))


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    try:
        user = authenticate_user(payload.email, payload.password)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return TokenResponse(access_token=create_access_token(user))
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Auth login failed: {type(exc).__name__}: {exc}",
        )


@router.get("/me", response_model=UserResponse)
def me(current_user: Users = Depends(get_current_api_user)):
    return serialize_user(current_user)
