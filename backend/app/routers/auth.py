from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from .. import db
from ..security import (
    PUBLIC_USER, audit, clear_throttle, client_ip, create_session, current_user, destroy_session, fail,
    hash_password, password_problem, throttle, verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(max_length=190)
    password: str = Field(max_length=200)


class PasswordIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(min_length=1, max_length=200)


class PrefsIn(BaseModel):
    lang: str | None = Field(default=None, pattern="^(en|ar)$")
    name: str | None = Field(default=None, min_length=1, max_length=120)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    email = body.email.strip().lower()
    key = f"{client_ip(request)}|{email}"
    throttle(key)
    row = db.fetch_one("SELECT id, password_hash, is_active FROM users WHERE email=:e", e=email)
    if not row or not verify_password(body.password, row["password_hash"]):
        audit(request, None, "auth.login_failed", "user", None, {"email": email})
        raise fail(401, "bad_credentials", "Email or password is incorrect.")
    if not row["is_active"]:
        raise fail(403, "account_disabled", "This account has been disabled.")
    clear_throttle(key)
    create_session(response, request, row["id"])
    db.execute("UPDATE users SET last_login_at=NOW() WHERE id=:id", id=row["id"])
    user = db.fetch_one(f"SELECT {PUBLIC_USER} FROM users WHERE id=:id", id=row["id"])
    audit(request, user, "auth.login", "user", user["id"])
    return {"user": user}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    destroy_session(request, response)
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    return {"user": user}


@router.patch("/me")
def update_me(body: PrefsIn, request: Request, user: dict = Depends(current_user)) -> dict:
    if body.lang:
        db.execute("UPDATE users SET lang=:l WHERE id=:id", l=body.lang, id=user["id"])
    if body.name:
        db.execute("UPDATE users SET name=:n WHERE id=:id", n=body.name.strip(), id=user["id"])
    return {"user": db.fetch_one(f"SELECT {PUBLIC_USER} FROM users WHERE id=:id", id=user["id"])}


@router.post("/password")
def change_password(body: PasswordIn, request: Request, user: dict = Depends(current_user)) -> dict:
    row = db.fetch_one("SELECT password_hash FROM users WHERE id=:id", id=user["id"])
    if not verify_password(body.current, row["password_hash"]):
        raise fail(400, "bad_current_password", "The current password is incorrect.")
    problem = password_problem(body.new)
    if problem:
        raise fail(400, problem, "Use at least 8 characters with letters and numbers.")
    db.execute("UPDATE users SET password_hash=:h, must_change_password=0 WHERE id=:id",
               h=hash_password(body.new), id=user["id"])
    audit(request, user, "auth.password_changed", "user", user["id"])
    return {"user": db.fetch_one(f"SELECT {PUBLIC_USER} FROM users WHERE id=:id", id=user["id"])}
