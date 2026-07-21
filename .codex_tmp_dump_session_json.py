import asyncio
import json
from app.core.database import database_manager
from app.models.interview import InterviewSessionEntity

SESSION_ID = "40480b5f-f0eb-40b8-8725-c28a54ce1b5d"

async def main():
    await database_manager.connect()
    session_factory = database_manager.get_session_factory()
    async with session_factory() as session:
        row = await session.get(InterviewSessionEntity, SESSION_ID)
        if row is None:
            print("SESSION_NOT_FOUND")
            return
        print("=== QUESTIONS_JSON ===")
        print(json.dumps(row.questions_json, ensure_ascii=False, indent=2))
        print("=== SESSION_CONTEXT_JSON ===")
        print(json.dumps(row.session_context_json, ensure_ascii=False, indent=2))
    await database_manager.close()

asyncio.run(main())
