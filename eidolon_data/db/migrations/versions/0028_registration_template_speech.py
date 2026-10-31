"""A registration template's voiced speech is its whole registration's.

The identity calibration (design §3.3) weighs a template by the voiced speech
it was built from: for a registration, the sum of its three samples (capped
at the contract's 30 s). Templates stored before that rule carried only the
first sample's. Downgrade keeps the corrected value; older code reads it.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0028_registration_template_speech"
down_revision = "0027_owner_voices"
branch_labels = None
depends_on = None

MAX_SPEECH_MS = 30000


def upgrade():
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT p.profile_id, p.template_json, e.samples_json FROM speaker_profiles p "
        "JOIN voice_enrollments e ON e.enrollment_id = p.enrollment_id "
        "WHERE p.origin = 'enrollment' AND e.state = 'ready'"
    )).fetchall()
    for profile_id, template_json, samples_json in rows:
        template = json.loads(template_json) if isinstance(template_json, str) else template_json
        samples = json.loads(samples_json) if isinstance(samples_json, str) else samples_json
        registration = (samples or {}).get("registration") or {}
        if len(registration) != 3:
            continue
        speech_ms = min(sum(int(s["speech_ms"]) for s in registration.values()), MAX_SPEECH_MS)
        if template.get("speech_ms") != speech_ms:
            template["speech_ms"] = speech_ms
            bind.execute(sa.text("UPDATE speaker_profiles SET template_json = :t WHERE profile_id = :p"),
                         {"t": json.dumps(template), "p": profile_id})


def downgrade():
    pass
