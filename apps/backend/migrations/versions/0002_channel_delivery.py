"""Allow channel destinations without inventing a user account."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    table = 'notification_deliveries'
    op.alter_column(table, 'receiver_id', existing_type=sa.String(36), nullable=True)
    # 0001 uses current metadata on fresh installations.
    inspector = sa.inspect(op.get_bind())
    if 'uq_delivery_chat' not in {c['name'] for c in inspector.get_unique_constraints(table)}:
        op.create_unique_constraint('uq_delivery_chat', table, ['notification_id', 'telegram_chat_id'])
    if 'ck_channel_delivery' not in {c['name'] for c in inspector.get_check_constraints(table)}:
        op.create_check_constraint('ck_channel_delivery', table, 'receiver_id IS NOT NULL OR telegram_chat_id < 0')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM notification_deliveries WHERE receiver_id IS NULL')):
        raise RuntimeError('Channel delivery history must be archived before downgrading; no records were deleted.')
    op.drop_constraint('ck_channel_delivery', 'notification_deliveries', type_='check')
    op.drop_constraint('uq_delivery_chat', 'notification_deliveries', type_='unique')
    op.alter_column('notification_deliveries', 'receiver_id', existing_type=sa.String(36), nullable=False)
