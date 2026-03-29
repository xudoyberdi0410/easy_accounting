import asyncio
import logging

from app.bot.create import create_bot, create_dispatcher


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = create_bot()
    dp = create_dispatcher()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
