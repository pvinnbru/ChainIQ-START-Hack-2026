from fastapi import Cookie, HTTPException, Depends
from sqlalchemy.orm import Session
from database import get_db
import models

COOKIE_NAME = "demo_user_id"


def get_current_user(
    demo_user_id: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> models.User:
    if not demo_user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(models.User).filter(models.User.id == demo_user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
