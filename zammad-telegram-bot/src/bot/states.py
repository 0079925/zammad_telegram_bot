"""FSM states for the Telegram bot."""
from aiogram.fsm.state import State, StatesGroup


class UserFlow(StatesGroup):
    # User has started but hasn't shared their phone yet
    awaiting_phone = State()
    # User is at the main menu (phone collected, no active ticket context)
    main_menu = State()
    # User has selected a queue and has an active ticket; all messages go there
    in_ticket = State()
