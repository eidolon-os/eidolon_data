"""A voiceprint has no name of its own: it belongs to a Person, who has one.

``speaker_profiles.display_name`` was a copy typed at registration; renaming
the Person left it stale. The stored registration request carried the same
copy, so it is removed from ``voice_enrollments.request_json`` as well: a
registration begun before this upgrade then completes, and repeats its create
call, exactly like one begun after it.

Downgrade restores both from the Person's listed name, the only name there is
left to restore. The column comes back with an empty server default, which the
older schema did not have and older code never relies on: SQLite cannot add a
NOT NULL column without one, and rebuilding the table to remove it would drop
the table's CHECK constraint.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0033_drop_voiceprint_name"
down_revision = "0032_face_recognition_consent"
branch_labels = None
depends_on = None

NAME_LENGTH = 80


def _rewrite_requests(bind, change):
    rows = bind.execute(sa.text(
        "SELECT e.enrollment_id, e.request_json, p.display_name FROM voice_enrollments e "
        "LEFT JOIN persons p ON p.owner_id = e.owner_id AND p.person_id = e.person_id"
    )).fetchall()
    for enrollment_id, request_json, person_name in rows:
        request = json.loads(request_json) if isinstance(request_json, str) else request_json
        if not isinstance(request, dict):
            continue
        before = dict(request)
        change(request, person_name)
        if request != before:
            bind.execute(sa.text("UPDATE voice_enrollments SET request_json = :r WHERE enrollment_id = :e"),
                         {"r": json.dumps(request), "e": enrollment_id})


def upgrade():
    _rewrite_requests(op.get_bind(), lambda request, _: request.pop("display_name", None))
    op.drop_column("speaker_profiles", "display_name")


def downgrade():
    op.add_column("speaker_profiles",
                  sa.Column("display_name", sa.String(NAME_LENGTH), nullable=False, server_default=""))
    op.execute(
        "UPDATE speaker_profiles SET display_name = COALESCE((SELECT substr(p.display_name, 1, "
        f"{NAME_LENGTH}) FROM persons p WHERE p.owner_id = speaker_profiles.owner_id "
        "AND p.person_id = speaker_profiles.person_id), '')"
    )

    def restore(request, person_name):
        # Only a real registration request names a person; test or legacy rows
        # that stored something else are left as they were.
        if "person_id" in request:
            request["display_name"] = (person_name or "")[:NAME_LENGTH]

    _rewrite_requests(op.get_bind(), restore)
