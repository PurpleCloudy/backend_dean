"""Exercise the actual upstream invocation with an in-memory persistence seam."""
import importlib
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv('DELEGATION_SECRET','attachment-adapter-unit-secret-not-production')
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://test:test@127.0.0.1/test')
    monkeypatch.setenv('OPENAI_MODEL','fixture-test-model')
    module = importlib.import_module('integrations.dean_agent_adapter.app')
    monkeypatch.delenv('AGENT_VISION_MODEL',raising=False)
    return module


def test_real_upstream_persists_only_original_user_message_and_next_turn_has_no_raw_marker(adapter,monkeypatch):
    actor, session = 'user:1',uuid4()
    persisted, invocations = [], []
    class Database:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def get(self,*args): return SimpleNamespace(id=session,actor_id=actor)
        def scalars(self,statement):
            model=statement.column_descriptions[0]['entity']
            rows=list(reversed(persisted)) if model is adapter.original_agent.ChatMessage else []
            return SimpleNamespace(all=lambda:rows)
        def add_all(self,rows): persisted.extend(rows)
    class Sessions:
        def __call__(self): return Database()
        def begin(self): return Database()
    monkeypatch.setattr(adapter.original_agent,'SessionLocal',Sessions())
    monkeypatch.setattr(adapter.original_agent,'build_tools',lambda *a:[])
    monkeypatch.setattr(adapter.original_agent,'ChatOpenAI',lambda **kw:object())
    def invoke(value,config=None):
        invocations.append(value)
        return {'messages':[SimpleNamespace(text='Answer with no copied source',tool_calls=[])]}
    monkeypatch.setattr(adapter,'original_create_agent',lambda **kw:SimpleNamespace(invoke=invoke))
    ref={'file_id':str(uuid4()),'version_id':str(uuid4())}
    marker='RAW_ATTACHMENT_ONLY_98147'
    item={**ref,'filename':'secret.txt','byte_size':5,'text':marker,'blocks':[]}
    token=adapter.trusted.set({'attachments':[ref]})
    try:
        adapter.original_ask('Read attached',actor,session,attachments=[item])
    finally:
        adapter.trusted.reset(token)
    assert marker in str(invocations[0])
    assert persisted[0].content == 'Read attached'
    assert all(marker not in row.content for row in persisted)
    adapter.original_ask('Next turn',actor,session)
    assert marker not in str(invocations[1]) and 'secret.txt' not in str(invocations[1])
    assert invocations[1]['messages'][-1] == {'role':'user','content':'Next turn'}


def test_plain_invocation_preserves_graph_and_visual_requires_exact_model_identity(adapter,monkeypatch):
    graph=object()
    monkeypatch.setattr(adapter,'original_create_agent',lambda **kw:graph)
    assert adapter.create_scoped_agent() is graph
    assert not adapter.vision_supported()
    monkeypatch.setenv('AGENT_VISION_MODEL','different-model')
    assert not adapter.vision_supported()
    monkeypatch.setenv('AGENT_VISION_MODEL',adapter.original_agent.settings.openai_model)
    assert adapter.vision_supported()


def test_attachment_scoped_wrapper_bounds_only_old_pairs_and_retains_config(adapter,monkeypatch):
    observed=[]
    monkeypatch.setattr(adapter,'original_create_agent',lambda **kw:SimpleNamespace(invoke=lambda v,config=None:observed.append((v,config))))
    current={'role':'user','content':[{'type':'text','text':'current source'}]}
    value={'messages':[{'role':'user','content':'x'*4000},{'role':'assistant','content':'x'*4000},current]}
    config={'recursion_limit':30}
    token=adapter.trusted.set({'attachments':[{'version_id':str(uuid4())}]})
    try:
        adapter.create_scoped_agent().invoke(value,config=config)
    finally:
        adapter.trusted.reset(token)
    assert observed[0][0]['messages'] == [current]
    assert observed[0][0]['messages'][0] is current and observed[0][1] is config


def test_two_context_threads_keep_claims_and_attachment_readers_isolated(adapter,monkeypatch):
    barrier=threading.Barrier(2)
    results, failures = [], []
    class Reader:
        def __init__(self,http,backend,secret,claims,vision=False): self.claims=claims
        def prepare(self,refs): return refs
    monkeypatch.setattr(adapter,'AttachmentReader',Reader)
    monkeypatch.setattr(adapter,'sync_http',SimpleNamespace(post=lambda *a,**kw:SimpleNamespace(status_code=200)))
    def invoke(message,actor,session,trace_callback=None,attachments=None):
        barrier.wait(timeout=5)
        reader=adapter.attachment_reader.get()
        assert reader.claims['run'] == message == adapter.trusted.get()['run']
        assert attachments == reader.claims['attachments']
        return {'session_id':str(session),'run_id':str(uuid4()),'answer':message,'tools_used':[],'proposals':[]}
    monkeypatch.setattr(adapter,'original_ask',invoke)
    def run(claims):
        try:
            results.append(adapter.scoped_ask(claims['run'],'user:1',claims['session']))
            assert adapter.attachment_reader.get() is None
        except Exception as exc: failures.append(exc)
    threads=[]
    for _ in range(2):
        claims={'user':1,'session':str(uuid4()),'run':str(uuid4()),'attachments':[{'file_id':str(uuid4()),'version_id':str(uuid4())}]}
        mark=adapter.trusted.set(claims)
        try: threads.append(adapter.ContextThread(target=run,args=(claims,)))
        finally: adapter.trusted.reset(mark)
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=10)
    assert not failures and len(results) == 2 and results[0]['answer'] != results[1]['answer']
    assert adapter.trusted.get() is None and adapter.attachment_reader.get() is None
