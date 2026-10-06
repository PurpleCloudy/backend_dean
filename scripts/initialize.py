"""Explicit additive install; synthetic sample loading is opt-in and empty-DB only."""
import argparse
import asyncio
import os
from pathlib import Path
import subprocess
import sys

import psycopg
from psycopg import sql
from aiobotocore.session import get_session
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT=Path(__file__).resolve().parents[1]


def migrate(revision):
    subprocess.run([sys.executable,'-m','alembic','-c','migrations/alembic.ini','upgrade',revision],cwd=ROOT,check=True)


async def bucket():
    async with get_session().create_client('s3',endpoint_url=os.environ['S3_ENDPOINT_URL'],region_name='us-east-1',
        aws_access_key_id=os.environ['S3_ADMIN_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_ADMIN_SECRET_KEY'],
        config=Config(s3={'addressing_style':'path'},connect_timeout=5,read_timeout=30)) as client:
        name=os.environ.get('S3_BUCKET','deanery-private')
        try:
            await client.head_bucket(Bucket=name)
        except ClientError as exc:
            if exc.response['ResponseMetadata']['HTTPStatusCode']!=404:
                raise
            await client.create_bucket(Bucket=name)
        print('Private S3 bucket available; anonymous access is checked separately.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--synthetic',action='store_true',help='Load the supplied fictional sample on an empty canonical schema')
    parser.add_argument('--storage-only',action='store_true')
    args=parser.parse_args()
    if not args.storage_only:
        if args.synthetic:
            migrate('0001')
            with psycopg.connect(os.environ['MIGRATION_DATABASE_URL']) as conn:
                tables=conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='deanery'").fetchall()
                if any(conn.execute(sql.SQL('SELECT EXISTS(SELECT 1 FROM deanery.{})').format(sql.Identifier(name))).fetchone()[0] for name, in tables):
                    raise SystemExit('Synthetic seed requires an empty schema; existing data were left untouched')
                conn.execute((ROOT/'sources/deanery_db/03_seed.sql').read_text(encoding='utf-8'))
        migrate('head')
        subprocess.run([sys.executable,'migrations/bootstrap.py','--login','admin'],cwd=ROOT,check=True)
        if args.synthetic:
            subprocess.run([sys.executable,'migrations/schedule_seed.py'],cwd=ROOT,check=True)
    asyncio.run(bucket())
