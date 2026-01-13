# Author: Sergey Akulov
# GitHub: https://github.com/serg-akulov

import asyncio
import logging
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto

import config
import database

# Настройка логов
logging.basicConfig(level=logging.INFO)

# Инициализация
bot = Bot(token=config.8252283540:AAENRCiE0Pza0_HNDSrL3UcFb6-CLgtBEfU)
dp = Dispatcher()

# --- Машина состояний ---
class OrderForm(StatesGroup):
    photo = State()
    work_type = State()
    dimensions = State()
    conditions = State()
    urgency = State()
    extra_q = State()
    comment = State()

# --- ПОМОЩНИКИ ---
def get_text(key):
    """
    Берет текст ТОЛЬКО из базы.
    Если ключа нет - вернет заглушку [NO_DB_TEXT: key], чтобы мы видели ошибку.
    """
    cfg = database.get_bot_config()
    val = cfg.get(key)
    if val: return val
    return f"[NO_DB_TEXT: {key}]" # Если видишь это в боте - значит в базе нет этой строки

def get_config_bool(key):
    cfg = database.get_bot_config()
    return str(cfg.get(key, '0')) == '1'

def safe_text(message: types.Message):
    if message.text: return message.text
    if message.caption: return message.caption
    if message.sticker: return "[Стикер]"
    if message.photo: return "[Фото]"
    return "[Неизвестно]"

async def forward_message_to_admin(message: types.Message, order_id):
    try:
        cfg = database.get_bot_config()
        admin_id = cfg.get("admin_chat_id", "0")
        if admin_id and admin_id != '0':
            header = f"📩 <b>Клиент (Заказ №{order_id}):</b>\n"
            if message.text:
                await bot.send_message(admin_id, header + message.text, parse_mode="HTML")
            else:
                await message.copy_to(admin_id)
                await bot.send_message(admin_id, f"👆 К заказу №{order_id}", parse_mode="HTML")
        else:
            # Текст ошибки админа
            await message.answer(get_text('err_admin_not_set'))
    except Exception as e:
        logging.error(f"Deliver error: {e}")

# --- КЛАВИАТУРЫ ---
def kb_photo_step():
    buttons = [[KeyboardButton(text="✅ Все фото отправлены")]]
    if not get_config_bool('is_photo_required'):
        buttons.append([KeyboardButton(text=get_text('btn_skip_photo'))])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)

def kb_work_type():
    buttons = [
        [InlineKeyboardButton(text=get_text('btn_type_repair'), callback_data="type_repair")],
        [InlineKeyboardButton(text=get_text('btn_type_copy'), callback_data="type_copy")],
        [InlineKeyboardButton(text=get_text('btn_type_drawing'), callback_data="type_drawing")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_urgency():
    buttons = [
        [InlineKeyboardButton(text=get_text('btn_urgency_high'), callback_data="urgency_high")],
        [InlineKeyboardButton(text=get_text('btn_urgency_med'), callback_data="urgency_med")],
        [InlineKeyboardButton(text=get_text('btn_urgency_low'), callback_data="urgency_low")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- ЛОГИКА ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    user_id = message.from_user.id
    # Очищаем старые черновики
    database.cancel_old_filling_orders(user_id)
    
    username = message.from_user.username or "NoNick"
    full_name = message.from_user.full_name
    order_id = database.create_order(user_id, username, full_name)
    
    await state.update_data(order_id=order_id, photo_ids=[])
    
    # СТРОГО ИЗ БАЗЫ
    welcome = get_text('welcome_msg')
    await message.answer(f"{welcome}\n\n🆕 <b>Заказ №{order_id}</b>", parse_mode="HTML")
    
    await message.answer(get_text('step_photo_text'), reply_markup=kb_photo_step(), parse_mode="Markdown")
    await state.set_state(OrderForm.photo)

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    database.cancel_old_filling_orders(user_id)
    await message.answer(get_text('msg_order_canceled'))

# 1. ФОТО
@dp.message(OrderForm.photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    p_ids = data.get('photo_ids', [])
    p_ids.append(message.photo[-1].file_id)
    await state.update_data(photo_ids=p_ids)
    await message.answer(f"📸 Фото {len(p_ids)} принято.", reply_markup=kb_photo_step())

@dp.message(OrderForm.photo)
async def process_photo_done(message: types.Message, state: FSMContext):
    txt = safe_text(message)
    data = await state.get_data()
    p_ids = data.get('photo_ids', [])
    skip_btn = get_text('btn_skip_photo')

    # 1. Завершить фото
    if txt == "✅ Все фото отправлены":
        if not p_ids:
            await message.answer("⚠️ Вы не загрузили ни одного фото.")
            return
        database.update_order_field(data['order_id'], 'photo_file_id', ",".join(p_ids))
        await message.answer("👍 Фото приняты.", reply_markup=types.ReplyKeyboardRemove())
        await ask_work_type(message, state)

    # 2. Пропустить
    elif txt == skip_btn:
        if get_config_bool('is_photo_required'):
             await message.answer(get_text('err_photo_required'))
        else:
            await message.answer("👍 Ок, без фото.", reply_markup=types.ReplyKeyboardRemove())
            await ask_work_type(message, state)
            
    # 3. Левый текст или завис
    else:
        # Проверяем, есть ли обязаловка фото
        if get_config_bool('is_photo_required') and not p_ids:
            await message.answer(get_text('err_photo_required'))
            return
            
        # Пробуем восстановить состояние
        await check_lost_state(message, state)

async def ask_work_type(message, state):
    await message.answer(get_text('step_type_text'), reply_markup=kb_work_type(), parse_mode="Markdown")
    await state.set_state(OrderForm.work_type)

# 2. ТИП
@dp.callback_query(OrderForm.work_type)
async def process_work_type(callback: types.CallbackQuery, state: FSMContext):
    map_types = {'type_repair': 'btn_type_repair', 'type_copy': 'btn_type_copy', 'type_drawing': 'btn_type_drawing'}
    # Получаем ключ
    key = map_types.get(callback.data)
    # Получаем текст из базы
    human = get_text(key)
    
    database.update_order_field((await state.get_data())['order_id'], 'work_type', human)
    
    await callback.message.edit_text(f"✅ {human}")
    await callback.message.answer(get_text('step_dim_text'), parse_mode="Markdown")
    await state.set_state(OrderForm.dimensions)

# 3. РАЗМЕРЫ
@dp.message(OrderForm.dimensions)
async def process_dimensions(message: types.Message, state: FSMContext):
    txt = safe_text(message)
    database.update_order_field((await state.get_data())['order_id'], 'dimensions_info', txt)
    
    btns = [
        [InlineKeyboardButton(text=get_text('btn_cond_rotation'), callback_data="cond_rotation")],
        [InlineKeyboardButton(text=get_text('btn_cond_static'), callback_data="cond_static")],
        [InlineKeyboardButton(text=get_text('btn_cond_impact'), callback_data="cond_impact")],
        [InlineKeyboardButton(text=get_text('btn_cond_unknown'), callback_data="cond_unknown")]
    ]
    await message.answer(get_text('step_cond_text'), reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="Markdown")
    await state.set_state(OrderForm.conditions)

# 4. УСЛОВИЯ
@dp.callback_query(OrderForm.conditions)
async def process_conditions(callback: types.CallbackQuery, state: FSMContext):
    map_cond = {'cond_rotation': 'btn_cond_rotation', 'cond_static': 'btn_cond_static', 'cond_impact': 'btn_cond_impact', 'cond_unknown': 'btn_cond_unknown'}
    human = get_text(map_cond.get(callback.data))
    
    database.update_order_field((await state.get_data())['order_id'], 'conditions', human)
    
    await callback.message.edit_text(f"✅ {human}")
    await callback.message.answer(get_text('step_urgency_text'), reply_markup=kb_urgency(), parse_mode="Markdown")
    await state.set_state(OrderForm.urgency)

# 5. СРОЧНОСТЬ
@dp.callback_query(OrderForm.urgency)
async def process_urgency(callback: types.CallbackQuery, state: FSMContext):
    map_urg = {'urgency_high': 'btn_urgency_high', 'urgency_med': 'btn_urgency_med', 'urgency_low': 'btn_urgency_low'}
    human = get_text(map_urg.get(callback.data))
    
    database.update_order_field((await state.get_data())['order_id'], 'urgency', human)
    await callback.message.edit_text(f"✅ {human}")
    
    if get_config_bool('step_extra_enabled'):
        await callback.message.answer(get_text('step_extra_text'), parse_mode="Markdown")
        await state.set_state(OrderForm.extra_q)
    else:
        await ask_final(callback.message, state)

@dp.message(OrderForm.extra_q)
async def process_extra(message: types.Message, state: FSMContext):
    txt = safe_text(message)
    await state.update_data(temp_comment=f"Доп: {txt}\n")
    await ask_final(message, state)

async def ask_final(message, state):
    await message.answer(get_text('step_final_text'), parse_mode="Markdown")
    await state.set_state(OrderForm.comment)

# 6. ФИНАЛ
@dp.message(OrderForm.comment)
async def process_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    comm = safe_text(message)
    final_comm = data.get('temp_comment', '') + comm
    await finalize_order(message, data['order_id'], final_comm)
    await state.clear()

async def finalize_order(message, order_id, comment_text):
    database.update_order_field(order_id, 'comment', comment_text)
    database.finish_order_creation(order_id)
    await message.answer(get_text('msg_done'), parse_mode="Markdown")
    await notify_admin(order_id)

async def notify_admin(order_id):
    cfg = database.get_bot_config()
    aid = cfg.get("admin_chat_id", "0")
    if not aid or aid == '0': return 

    order = database.get_order(order_id)
    text = (f"🔔 <b>НОВЫЙ ЗАКАЗ №{order['id']}</b>\n"
            f"👤: {order['full_name']} (@{order['username']})\n"
            f"🛠: {order['work_type']}\n"
            f"📏: {order['dimensions_info']}\n"
            f"⚙️: {order['conditions']}\n"
            f"⏳: {order['urgency']}\n"
            f"📝: {order['comment']}\n\n"
            f"<i>Reply для ответа.</i>")
    try:
        p_ids = order['photo_file_id'].split(',') if order['photo_file_id'] else []
        if len(p_ids) > 1:
            mg = [InputMediaPhoto(media=pid) for pid in p_ids]
            await bot.send_media_group(aid, media=mg)
            await bot.send_message(aid, text, parse_mode="HTML")
        elif len(p_ids) == 1:
            await bot.send_photo(aid, p_ids[0], caption=text, parse_mode="HTML")
        else:
            await bot.send_message(aid, text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Err admin: {e}")

# --- АДМИНКА ---
@dp.message(Command("iamadmin"))
async def cmd_admin_auth(message: types.Message):
    args = message.text.split()
    if len(args) > 1 and args[1] == config.BOT_ADMIN_PASSWORD:
        database.update_setting("admin_chat_id", str(message.chat.id))
        await message.answer("✅ Админ авторизован.")
    else:
        await message.answer("❌ Неверный пароль.")

@dp.message(F.reply_to_message)
async def admin_reply_handler(message: types.Message):
    try:
        cfg = database.get_bot_config()
        aid = str(cfg.get("admin_chat_id", "0"))
        if str(message.chat.id) != aid: return 

        orig = message.reply_to_message.caption or message.reply_to_message.text
        if not orig:
            await message.answer("⚠️ Нет текста для ответа.")
            return
        
        match = re.search(r"(?:№|No|Num|Заказ)\s*[:#]?\s*(\d+)", orig, re.IGNORECASE)
        if not match:
            await message.answer(f"⚠️ Не вижу номер заказа.")
            return
            
        oid = int(match.group(1))
        order = database.get_order(oid)
        if not order:
            await message.answer(f"❌ Заказ №{oid} не найден.")
            return

        try:
            if message.text:
                await bot.send_message(order['user_id'], f"👨‍🔧 <b>Мастер:</b>\n{message.text}", parse_mode="HTML")
            else:
                await message.copy_to(order['user_id'])
            await message.react([types.ReactionTypeEmoji(emoji="👍")])
        except Exception as e:
            await message.answer(f"❌ Ошибка отправки:\n{e}")
    except Exception as e:
        await message.answer(f"💀 Err: {e}")

# --- УМНЫЙ ПЕРЕХВАТЧИК ---
@dp.message()
async def user_chat_handler(message: types.Message):
    await check_lost_state(message, None)

async def check_lost_state(message, state):
    filling_id = database.get_active_order_id(message.from_user.id)
    
    if filling_id:
        order = database.get_order(filling_id)
        has_photos = order['photo_file_id'] is not None and len(str(order['photo_file_id'])) > 5
        
        if not has_photos:
            if get_config_bool('is_photo_required'):
                await message.answer(get_text('err_photo_required'))
                return
            
            if state: 
                await state.update_data(order_id=filling_id)
                await state.set_state(OrderForm.photo)
            await process_photo_done(message, state or FSMContext(storage=dp.storage, key=types.StorageKey(bot.id, message.chat.id, message.from_user.id), parent=None))
            return

        if not order['work_type']:
            await message.answer("⚠️ Восстанавливаю... Выберите ТИП:", reply_markup=kb_work_type())
            if state: await state.set_state(OrderForm.work_type)
            return

        if not order['dimensions_info']:
            database.update_order_field(filling_id, 'dimensions_info', safe_text(message))
            btns = [[InlineKeyboardButton(text=get_text('btn_cond_rotation'), callback_data="cond_rotation")], [InlineKeyboardButton(text=get_text('btn_cond_static'), callback_data="cond_static")], [InlineKeyboardButton(text=get_text('btn_cond_unknown'), callback_data="cond_unknown")]]
            await message.answer(f"✅ Размеры записал ({safe_text(message)}). Условия?", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
            if state: await state.set_state(OrderForm.conditions)
            return

        await finalize_order(message, filling_id, safe_text(message))
        return

    order_id = database.get_user_last_active_order(message.from_user.id)
    if order_id:
        await forward_message_to_admin(message, order_id)
    else:
        await message.answer(get_text('err_no_active_order'))

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
