import asyncio
import logging

from app.bot.create import create_bot, create_dispatcher
from app.db.seed import seed_categories


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    count = await seed_categories()
    if count:
        logging.info("Seeded %d default categories", count)
    bot = create_bot()
    dp = create_dispatcher()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
