"""Explicit operator provisioning; private /start proves the Telegram identity."""
import argparse
from sqlalchemy import select
from .db import Session
from .models import Category, Department, Receiver, Role, User
from .services import audit, set_categories


def provision(db, telegram_user_id, first_name, last_name, username, all_categories):
    if telegram_user_id <= 0:
        raise ValueError('A positive Telegram user ID is required')
    user = db.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    if user is None:
        department = db.scalar(select(Department).where(Department.name == 'АХО'))
        user = User(first_name=first_name, last_name=last_name,
                    department_id=department.id if department else None,
                    telegram_user_id=telegram_user_id, status='ACTIVE')
        db.add(user)
        db.flush()
    elif user.status == 'DISABLED':
        raise ValueError('Reactivate the disabled employee explicitly in the admin dashboard first')
    user.status = 'ACTIVE'
    for name in ('EMPLOYEE', 'AHO_SPECIALIST'):
        role = db.get(Role, name)
        if role is None:
            raise ValueError('Run migrations and initial seed before provisioning')
        if name not in user.role_names:
            user.roles.append(role)
    receiver = db.get(Receiver, user.id)
    if receiver is None:
        receiver = Receiver(id=user.id, connection_status='NOT_CONNECTED')
        db.add(receiver)
        db.flush()
    receiver.receiver_status = 'ACTIVE'
    receiver.receive_requests = True
    receiver.availability = 'AVAILABLE'
    receiver.expected_username = username.lstrip('@') if username else None
    # Never fabricate chat_id, a last interaction, or a CONNECTED status.
    if not user.telegram_chat_id:
        receiver.connection_status = 'NOT_CONNECTED'
    if all_categories:
        ids = list(db.scalars(select(Category.id).where(Category.active == True)))
        set_categories(db, receiver, ids)
    audit(db, None, 'RECEIVER_PREAPPROVED', 'receiver', user.id,
          new={'telegram_user_id': telegram_user_id, 'all_categories': all_categories})
    return receiver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--telegram-user-id', required=True, type=int)
    parser.add_argument('--first-name', required=True)
    parser.add_argument('--last-name', required=True)
    parser.add_argument('--username', default='')
    parser.add_argument('--all-categories', action='store_true')
    args = parser.parse_args()
    with Session.begin() as db:
        receiver = provision(db, **vars(args))
        print(f'Receiver preapproved; connection={receiver.connection_status}; '
              f'categories={len(receiver.categories)}. Private /start is required.')


if __name__ == '__main__':
    main()
