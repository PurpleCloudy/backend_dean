"""Pin immutable file versions to the already admitted canonical user request."""
from alembic import op

revision = '0003'
down_revision = '0002'


def upgrade():
    op.execute("""
CREATE TABLE backend.agent_run_attachments (
 run_id uuid NOT NULL REFERENCES backend.agent_runs(id),
 file_id uuid NOT NULL,
 version_id uuid NOT NULL,
 ordinal smallint NOT NULL CHECK(ordinal BETWEEN 0 AND 4),
 PRIMARY KEY(run_id,version_id),
 UNIQUE(run_id,ordinal),
 FOREIGN KEY(file_id,version_id) REFERENCES backend.file_versions(file_id,id)
);
ALTER TABLE backend.agent_run_attachments ENABLE ROW LEVEL SECURITY;
CREATE POLICY owned_read ON backend.agent_run_attachments FOR SELECT TO deanery_runtime
 USING(EXISTS(SELECT 1 FROM backend.agent_runs r WHERE r.id=run_id AND r.user_id=backend.actor()));
CREATE POLICY owned_insert ON backend.agent_run_attachments FOR INSERT TO deanery_runtime
 WITH CHECK(EXISTS(SELECT 1 FROM backend.agent_runs r WHERE r.id=run_id AND r.user_id=backend.actor() AND r.status='running'));
REVOKE ALL ON backend.agent_run_attachments FROM PUBLIC,deanery_reader,deanery_jobs,deanery_upstream;
GRANT SELECT,INSERT ON backend.agent_run_attachments TO deanery_runtime;
-- Processing changes lifecycle/extraction metadata, never the original identity.
REVOKE UPDATE ON backend.file_versions FROM deanery_runtime,deanery_jobs;
GRANT UPDATE(state,error_code,quality) ON backend.file_versions TO deanery_runtime,deanery_jobs;
""")


def downgrade():
    op.execute("""
DROP TABLE backend.agent_run_attachments;
GRANT UPDATE ON backend.file_versions TO deanery_runtime,deanery_jobs;
""")
