from aiogram.fsm.state import State, StatesGroup


class AddAccount(StatesGroup):
    name = State()
    currency = State()
    account_type = State()
    balance = State()


class AddCategory(StatesGroup):
    category_type = State()
    name = State()
    icon = State()


class AddTransaction(StatesGroup):
    account = State()
    amount = State()
    category = State()
    note = State()


class AddTransfer(StatesGroup):
    from_account = State()
    to_account = State()
    amount = State()
    note = State()


class AddBudget(StatesGroup):
    category = State()
    amount = State()
    period = State()


class RenameAccount(StatesGroup):
    name = State()
