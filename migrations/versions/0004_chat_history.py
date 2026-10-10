"""Expose durable owned chat history without copying prompts or completions."""
from alembic import op

revision = '0004'
down_revision = '0003'


def upgrade():
    op.execute("""
ALTER TABLE public.chat_sessions ADD COLUMN title varchar(160), ADD COLUMN deleted_at timestamptz;
ALTER TABLE public.chat_sessions ADD CONSTRAINT chat_title_nonblank CHECK(title IS NULL OR btrim(title)<>'');
CREATE INDEX chat_sessions_actor_idx ON public.chat_sessions(actor_id,created_at,id) WHERE deleted_at IS NULL;
CREATE INDEX agent_runs_session_history_idx ON backend.agent_runs(session_id,created_at,id);
ALTER TABLE backend.agent_runs ADD COLUMN tools_used text[];
ALTER TABLE backend.agent_runs ADD CONSTRAINT agent_run_tools_check CHECK(
 tools_used <@ ARRAY['query_deanery','propose_sql_change','search_regulations']::text[] AND cardinality(tools_used)<=300);
ALTER TABLE public.chat_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY owned_session ON public.chat_sessions TO deanery_runtime,deanery_jobs
 USING(actor_id='user:'||backend.actor()) WITH CHECK(actor_id='user:'||backend.actor());
-- The internal agent validates signed user/session delegation before opening its DB session.
CREATE POLICY upstream_session ON public.chat_sessions FOR SELECT TO deanery_upstream USING(true);
ALTER TABLE public.chat_messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY owned_message ON public.chat_messages TO deanery_runtime,deanery_jobs
 USING(EXISTS(SELECT 1 FROM public.chat_sessions s WHERE s.id=session_id AND s.actor_id='user:'||backend.actor()))
 WITH CHECK(EXISTS(SELECT 1 FROM public.chat_sessions s WHERE s.id=session_id AND s.actor_id='user:'||backend.actor()));
CREATE POLICY upstream_message_read ON public.chat_messages FOR SELECT TO deanery_upstream USING(true);
CREATE POLICY upstream_message_insert ON public.chat_messages FOR INSERT TO deanery_upstream WITH CHECK(true);
REVOKE DELETE ON public.chat_sessions,public.chat_messages FROM deanery_runtime;
""")


def downgrade():
    # Dropping tombstones would resurrect removed conversations; roll back application code instead.
    raise RuntimeError('Chat history metadata is durable; restore a backup only with an explicit retention decision')
