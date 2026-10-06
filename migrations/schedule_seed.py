"""Queue native pending demo orders after explicit synthetic initialization."""
import asyncio
import sys
from deanery_api.auth import load_principal
from deanery_api.db import open_pool,close_pool,get_pool,transaction
from deanery_api.jobs import start_services,stop_services
from deanery_api.workflows import schedule_seed_orders


async def main():
    await open_pool()
    await start_services()
    try:
        async with get_pool().connection()as conn:
            row=await(await conn.execute("SELECT user_id FROM backend.login_record('admin')")).fetchone()
            if not row: raise RuntimeError('Provision the explicit bootstrap administrator first')
            principal=await load_principal(conn,row['user_id'])
        async with transaction(principal,'synthetic-initialization')as conn:
            scheduled=await schedule_seed_orders(conn,principal)
        print('Native demo orders scheduled:',len(scheduled))
    finally:
        await stop_services()
        await close_pool()


if __name__=='__main__':
    asyncio.run(main(),**({'loop_factory':asyncio.SelectorEventLoop} if sys.platform=='win32'else {}))
