"""Permit attachment-reader provenance in durable completion metadata."""
from alembic import op

revision = '0006'
down_revision = '0005'


def upgrade():
    op.execute("""
ALTER TABLE backend.agent_runs DROP CONSTRAINT agent_run_tools_check;
ALTER TABLE backend.agent_runs ADD CONSTRAINT agent_run_tools_check CHECK(
 tools_used <@ ARRAY['query_deanery','propose_sql_change','search_regulations','read_attachment_text']::text[]
 AND cardinality(tools_used)<=300);
""")


def downgrade():
    # Removing a now-used provenance value would require discarding historical metadata.
    raise RuntimeError('Attachment tool metadata is durable; use a forward fix or a version-compatible application')
