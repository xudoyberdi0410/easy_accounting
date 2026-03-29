from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="+ Income"), KeyboardButton(text="- Expense")],
        [KeyboardButton(text="Accounts"), KeyboardButton(text="Categories")],
        [KeyboardButton(text="History"), KeyboardButton(text="Budgets")],
        [KeyboardButton(text="Settings")],
    ],
    resize_keyboard=True,
)
