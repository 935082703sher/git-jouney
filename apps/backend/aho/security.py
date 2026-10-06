from datetime import timedelta
import secrets
import jwt
from fastapi import Depends, Request, HTTPException
from pwdlib import PasswordHash
from .config import settings
from .db import get_db, now
from .models import User

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(24))

def hash_password(value): return passwords.hash(value)
def verify_password(value, hashed):
    return passwords.verify(value, hashed or DUMMY_HASH) and bool(hashed)

def token_for(user):
    csrf = secrets.token_urlsafe(24)
    token = jwt.encode({'sub':user.id, 'exp':now()+timedelta(hours=8), 'csrf':csrf}, settings().jwt_secret, algorithm='HS256')
    return token, csrf

def current_user(request: Request, db=Depends(get_db)):
    try:
        data = jwt.decode(request.cookies.get('aho_session',''), settings().jwt_secret, algorithms=['HS256'])
        if request.method not in ('GET','HEAD','OPTIONS'):
            if not secrets.compare_digest(request.headers.get('X-CSRF-Token',''), data['csrf']):
                raise HTTPException(403, 'Обновите страницу и повторите действие')
        user = db.get(User, data['sub'])
        if not user or user.status != 'ACTIVE': raise HTTPException(403, 'Учетная запись не активна')
        request.state.user_id = user.id
        return user
    except jwt.PyJWTError:
        raise HTTPException(401, 'Войдите в систему')

def has_role(user, *roles): return bool(set(user.role_names) & set(roles))
def staff(user): return has_role(user, 'AHO_SPECIALIST','AHO_MANAGER','ADMIN')
def manager(user): return has_role(user, 'AHO_MANAGER','ADMIN')
def require(user, *roles):
    if not has_role(user, *roles): raise HTTPException(403, 'Недостаточно прав')
