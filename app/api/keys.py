from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import require_admin
from app.db import create_api_key, list_keys, revoke_key

router = APIRouter(prefix="/keys", tags=["keys"])


class CreateKeyRequest(BaseModel):
    name: str
    team_member: Optional[str] = None
    is_admin: bool = False


class CreateKeyResponse(BaseModel):
    key_id: str
    key: str
    name: str
    message: str = "Store this key securely — it will not be shown again."


@router.post("", response_model=CreateKeyResponse)
async def create_key(
    body: CreateKeyRequest,
    _admin: dict = Depends(require_admin),
) -> CreateKeyResponse:
    key_id, full_key = await create_api_key(
        name=body.name,
        team_member=body.team_member,
        is_admin=body.is_admin,
    )
    return CreateKeyResponse(key_id=key_id, key=full_key, name=body.name)


@router.get("")
async def get_keys(_admin: dict = Depends(require_admin)) -> list[dict]:
    return await list_keys()


@router.delete("/{key_id}")
async def delete_key(
    key_id: str,
    _admin: dict = Depends(require_admin),
) -> dict:
    await revoke_key(key_id)
    return {"status": "revoked", "key_id": key_id}
