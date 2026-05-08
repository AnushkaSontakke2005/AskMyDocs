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
from db import Users, initialize_database


router = APIRouter(prefix="/auth", tags=["Auth"])


def serialize_user(user: Users) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, is_admin=bool(user.is_admin))


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest):
    initialize_database()
    email = normalize_email(payload.email)
    if Users.get_or_none(Users.email == email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    try:
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

    return TokenResponse(access_token=create_access_token(user))


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    user = authenticate_user(payload.email, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(user))


@router.get("/me", response_model=UserResponse)
def me(current_user: Users = Depends(get_current_api_user)):
    return serialize_user(current_user)
