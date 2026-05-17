from aiogram.fsm.state import State, StatesGroup


class TopUpStates(StatesGroup):
    waiting_amount = State()


class ConvertStates(StatesGroup):
    waiting_money = State()
    waiting_stars = State()
