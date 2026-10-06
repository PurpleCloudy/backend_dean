import os
from alembic import context
from sqlalchemy import create_engine

url=os.environ['MIGRATION_DATABASE_URL'].replace('postgresql://','postgresql+psycopg://',1)
engine=create_engine(url)
with engine.connect() as connection:
    connection=connection.execution_options(no_parameters=True)
    context.configure(connection=connection,transactional_ddl=True)
    with context.begin_transaction(): context.run_migrations()
