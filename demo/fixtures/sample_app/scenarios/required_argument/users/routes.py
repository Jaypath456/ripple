from fastapi import APIRouter, Depends

from users.service import delete_user, list_users
from settings import get_session

router = APIRouter()


@router.get("/users")
def index(session=Depends(get_session)):
    return list_users(session)


@router.delete("/users/{user_id}", status_code=204)
def remove_user(user_id: int, session=Depends(get_session)) -> None:
    delete_user(session, user_id, actor="api")
