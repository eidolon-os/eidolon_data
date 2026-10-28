"""Make retained private realms from historical Person deletion manageable."""

import sqlalchemy as sa
from alembic import op

revision = "0016_retained_person_archives"
down_revision = "0015_person_archive"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT r.person_id, r.owner_id FROM memory_realms r "
            "LEFT JOIN persons p ON p.person_id = r.person_id "
            "WHERE r.person_id IS NOT NULL AND p.person_id IS NULL"
        )
    ).all()
    if len({row.person_id for row in rows}) != len(rows):
        raise RuntimeError("retained Person identity has ambiguous Owner ownership")
    for row in rows:
        values = {"person_id": row.person_id, "owner_id": row.owner_id}
        connection.execute(
            sa.text(
                "INSERT INTO persons(person_id, owner_id, display_name, status, revision, profile_json, deleted) "
                "VALUES (:person_id, :owner_id, '已删除用户（历史档案）', 'inactive', 1, '{}', true)"
            ),
            values,
        )
        connection.execute(
            sa.text(
                "UPDATE memory_realms SET status = 'inactive' "
                "WHERE person_id = :person_id AND owner_id = :owner_id"
            ),
            values,
        )


def downgrade():
    # These are data records, not a schema extension. Keep them and any later
    # owner edits; 0015 already knows their lifecycle and refuses unsafe removal.
    pass
