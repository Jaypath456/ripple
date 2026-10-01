from fastapi import APIRouter

from app.schemas import UserResponse
from app.service import delete_user

router = APIRouter()


@router.delete("/users/{user_id}", response_model=UserResponse)
def remove_user(user_id: int) -> None:
    delete_user(user_id)  # type: ignore[arg-type]
