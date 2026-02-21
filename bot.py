import logging
import random
import pytz
from datetime import datetime, timedelta, date
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)
import config
from database import Database
from functools import wraps

MINSK_TZ = pytz.timezone('Europe/Minsk')

def _today_minsk():
    """Текущая дата по минскому времени (UTC+3)."""
    return datetime.now(MINSK_TZ).date()


def escape_markdown(text):
    """Экранирует специальные символы Markdown"""
    if not text:
        return text
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text


def ensure_user(func):
    """Декоратор для автоматической регистрации пользователя"""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user

        # Проверяем существует ли пользователь
        if not db.get_user(user.id):
            db.add_user(
                user_id=user.id,
                username=user.username or user.first_name,
                first_name=user.first_name,
                language_code=user.language_code or 'ru'
            )

        return await func(update, context)

    return wrapper


# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация базы данных
db = Database()


async def check_and_reset_streaks(bot):
    today_dt = _today_minsk()
    today = today_dt.isoformat()
    yesterday = (today_dt - timedelta(days=1)).isoformat()
    users = db.get_all_users()

    for user_id in users:
        user = db.get_user(user_id)
        last = user.get('last_completed_date')

        if last != today and last != yesterday:
            freeze_until = user.get('streak_freeze_until')
            if freeze_until and date.fromisoformat(freeze_until) >= today_dt:
                continue
            if user.get('streak', 0) > 0:
                db.reset_streak(user_id)
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text="💔 Твой стрик сброшен — вчера не было выполнено задание.\n\n"
                             "Но это не конец! Начни заново сегодня 💪"
                    )
                except Exception:
                    pass


async def send_evening_reminder(bot):
    today = _today_minsk().isoformat()
    users = db.get_all_users()

    for user_id in users:
        user = db.get_user(user_id)
        if user.get('last_completed_date') == today:
            continue  # уже выполнил — не беспокоим

        freeze_until = user.get('streak_freeze_until')
        if freeze_until and date.fromisoformat(freeze_until) >= _today_minsk():
            continue  # заморозка активна — не беспокоим

        try:
            await bot.send_message(
                chat_id=user_id,
                text="⏰ Эй, ты ещё не выполнил челлендж сегодня!\n\n"
                     "Осталось несколько часов — успей сохранить стрик 🔥"
            )
        except Exception:
            pass


def get_user_level(total_completed: int) -> str:
    """Определение уровня пользователя"""
    level = "🌱 Новичок"
    for threshold, level_name in sorted(config.LEVELS.items(), reverse=True):
        if total_completed >= threshold:
            level = level_name
            break
    return level


def get_progress_bar(total_completed: int) -> str:
    """Прогресс-бар до следующего уровня"""
    levels = sorted(config.LEVELS.items())

    current_threshold = 0
    current_name = levels[0][1]
    next_threshold = None
    next_name = None

    for threshold, name in levels:
        if total_completed >= threshold:
            current_threshold = threshold
            current_name = name
        else:
            next_threshold = threshold
            next_name = name
            break

    if next_threshold is None:
        return f"{current_name} 🏆 Максимальный уровень!"

    progress = total_completed - current_threshold
    total = next_threshold - current_threshold
    filled = int((progress / total) * 10)
    empty = 10 - filled

    bar = "▓" * filled + "░" * empty

    # Берём только эмодзи из названия уровня (первый символ)
    current_emoji = current_name.split()[0]
    next_emoji = next_name.split()[0]

    return f"{current_emoji} {bar} {next_emoji}"


def check_milestones(streak: int, total: int) -> list:
    """Проверка достижения milestone"""
    messages = []

    # Проверка streak milestone
    if streak in config.MILESTONES['streak']:
        messages.append(config.MILESTONES['streak'][streak])

    # Проверка total milestone
    if total in config.MILESTONES['total']:
        messages.append(config.MILESTONES['total'][total])

    return messages


def check_achievements(user_id: int, user_data: dict) -> list[dict]:
    """Проверка новых достижений"""
    new_achievements = []
    user_achievements = user_data.get('achievements', [])

    streak = user_data['streak']
    total = user_data['total_completed']

    # Подсчет по категориям
    category_counts = {}
    for entry in user_data['history']:
        cat = entry.get('category', 'unknown')
        category_counts[cat] = category_counts.get(cat, 0) + 1

    for achievement_id, achievement in config.ACHIEVEMENTS.items():
        # Пропускаем уже полученные
        if achievement_id in user_achievements:
            continue

        condition = achievement['condition']
        value = achievement['value']
        earned = False

        if condition == 'streak':
            earned = streak >= value
        elif condition == 'total_completed':
            earned = total >= value
        elif condition == 'all_categories':
            earned = len(category_counts) >= value
        elif condition.startswith('category_'):
            cat = condition.replace('category_', '')
            earned = category_counts.get(cat, 0) >= value

        if earned:
            # Добавляем достижение
            if db.add_achievement(user_id, achievement_id):
                db.add_coins(user_id, achievement['reward'])
                new_achievements.append({
                    'id': achievement_id,
                    'name': achievement['name'],
                    'reward': achievement['reward'],
                    'emoji': achievement['emoji']
                })

    return new_achievements


@ensure_user
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"⚡ START вызван! update_id={update.update_id}, user={update.effective_user.id}")
    user = update.effective_user
    welcome_text = f"""👋 Привет, *{user.first_name}*!

🌱 Добро пожаловать в бот \"Малый Шаг\"!

Этот бот поможет тебе выработать полезные привычки через маленькие ежедневные задания.

🎯 *Каждый день:*
• Выполняй простое задание
• Получай монеты 🪙
• Увеличивай streak 🔥
• Открывай достижения 🏆"""

    keyboard = [
        [InlineKeyboardButton("🎯 Получить челлендж", callback_data='back_to_categories')],
        [InlineKeyboardButton("👤 Профиль", callback_data='profile')],
        [InlineKeyboardButton("🛒 Магазин", callback_data='shop')],
    ]
    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Профиль: статистика + достижения + топ"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    stats = db.get_stats(user_id)

    if not stats:
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='back_to_main')]]
        await query.edit_message_text(
            "У тебя пока нет данных. Начни выполнять челленджи!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    import json
    user = db.get_user(user_id)
    user_achievements = json.loads(user['achievements']) if user['achievements'] else []
    level = get_user_level(stats['total_completed'])
    progress_bar = get_progress_bar(stats['total_completed'])
    coins = stats.get('coins', 0)
    today = _today_minsk().isoformat()

    streak = stats['streak']
    longest_streak = user.get('longest_streak', 0)

    # Статус стрика
    if user.get('last_completed_date') == today:
        streak_status = f"🔥 Streak: *{streak} дней* ✅"
    else:
        freeze_until = user.get('streak_freeze_until')
        if freeze_until and date.fromisoformat(freeze_until) >= _today_minsk():
            streak_status = f"🔥 Streak: *{streak} дней* 🛡️"
        else:
            streak_status = f"🔥 Streak: *{streak} дней* ⚠️"

    # Рекорд только если текущий стрик меньше максимального
    record_line = ""
    if longest_streak > streak:
        record_line = f"🏅 Рекорд: *{longest_streak} дней*\n"

    # Статистика по категориям
    category_text = ""
    for cat_key, count in stats['category_stats'].items():
        if cat_key in config.CATEGORIES:
            emoji = config.CATEGORIES[cat_key]['emoji']
            name = config.CATEGORIES[cat_key]['name']
            category_text += f"\n{emoji} {name}: *{count}*"

    text = (
        f"👤 *Профиль*\n\n"
        f"{streak_status}\n"
        f"{record_line}"
        f"✅ Выполнено: *{stats['total_completed']}* челленджей\n"
        f"⭐ Уровень: *{level}*\n"
        f"`{progress_bar}`\n"
        f"💰 Монет: *{coins}*\n"
        f"🏆 Достижений: *{len(user_achievements)}/{len(config.ACHIEVEMENTS)}*"
    )

    if category_text:
        text += f"\n\n*По категориям:*{category_text}"

    keyboard = [
        [InlineKeyboardButton("🏆 Достижения", callback_data='achievements'),
         InlineKeyboardButton("🥇 Топ игроков", callback_data='leaderboard')],
        [InlineKeyboardButton("◀️ Назад", callback_data='back_to_main')],
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def challenge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /challenge - показывает категории"""
    text = "🎯 *Выбери категорию челленджа!*\n\nВыбери категорию, чтобы получить задание на день:"
    keyboard = get_category_keyboard()
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown')


def get_category_keyboard():
    """Создание клавиатуры с категориями"""
    keyboard = [
        [
            InlineKeyboardButton(config.CATEGORIES['sport']['name'], callback_data='cat_sport'),
            InlineKeyboardButton(config.CATEGORIES['thinking']['name'], callback_data='cat_thinking'),
        ],
        [
            InlineKeyboardButton(config.CATEGORIES['creative']['name'], callback_data='cat_creative'),
            InlineKeyboardButton(config.CATEGORIES['communication']['name'], callback_data='cat_communication'),
        ],
        [InlineKeyboardButton("◀️ Назад", callback_data='back_to_main')],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_challenge_keyboard(can_complete: bool = True):
    keyboard = []
    if can_complete:
        keyboard.append([InlineKeyboardButton("✅ Выполнил", callback_data='complete')])
        keyboard.append([InlineKeyboardButton("⏭️ Другой челлендж", callback_data='another')])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data='back_to_categories')])
    return InlineKeyboardMarkup(keyboard)


async def category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    category = query.data.replace('cat_', '')

    user = db.get_user(user_id)
    if not user:
        username = query.from_user.username or query.from_user.first_name
        db.add_user(
            user_id=user_id,
            username=username,
            first_name=query.from_user.first_name,
            language_code=query.from_user.language_code or 'ru'
        )
        user = db.get_user(user_id)

    today = _today_minsk().isoformat()
    can_complete = user['last_completed_date'] != today

    if user.get('challenge_date') != today:
        db.update_challenge(user_id, None, None)
        user = db.get_user(user_id)

    emoji = config.CATEGORIES[category]['emoji']
    cat_name = config.CATEGORIES[category]['name']

    if not can_complete:
        message_text = f"""{emoji} *Категория: {cat_name}*

✅ *Отличная работа!*

Ты уже выполнил челлендж сегодня!

🌟 Возвращайся завтра за новым заданием.

💪 Продолжай развивать свою дисциплину!"""
        keyboard = [
            [InlineKeyboardButton("◀️ Назад к категориям", callback_data='back_to_categories')],
        ]
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return

    # Антидубль — берём историю выданных для этой категории
    history_key = f'recent_challenges_{category}'
    recent = context.user_data.get(history_key, [])

    challenges = config.CATEGORIES[category]['challenges']
    # Исключаем недавние, если есть из чего выбирать
    available = [c for c in challenges if c not in recent]
    if not available:
        available = challenges
        recent = []

    challenge = random.choice(available)

    # Обновляем историю (храним последние 5)
    recent.append(challenge)
    if len(recent) > 5:
        recent.pop(0)
    context.user_data[history_key] = recent

    db.update_challenge(user_id, challenge, category)

    message_text = f"""{emoji} *Категория: {cat_name}*

🎯 *Твой челлендж:*

{challenge}

✨ Выполни задание и нажми кнопку!"""

    await query.edit_message_text(
        message_text,
        reply_markup=get_challenge_keyboard(can_complete),
        parse_mode='Markdown'
    )


async def another_challenge_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Другой челлендж'"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user = db.get_user(user_id)

    if not user or not user['current_category']:
        text = "Сначала выбери категорию челленджа:"
        keyboard = get_category_keyboard()
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    today = _today_minsk().isoformat()
    can_complete = user['last_completed_date'] != today

    if not can_complete:
        await query.answer("❌ Ты уже выполнил челлендж сегодня! Приходи завтра 😊", show_alert=True)
        return

    category = user['current_category']

    # Антидубль — та же логика что в category_handler
    history_key = f'recent_challenges_{category}'
    recent = context.user_data.get(history_key, [])

    challenges = config.CATEGORIES[category]['challenges']
    available = [c for c in challenges if c not in recent]
    if not available:
        available = challenges
        recent = []

    challenge = random.choice(available)

    recent.append(challenge)
    if len(recent) > 5:
        recent.pop(0)
    context.user_data[history_key] = recent

    db.update_challenge(user_id, challenge, category)

    emoji = config.CATEGORIES[category]['emoji']
    cat_name = config.CATEGORIES[category]['name']

    message_text = f"""{emoji} *Категория: {cat_name}*

🎯 *Твой новый челлендж:*
{challenge}

✨ Выполни задание и нажми кнопку!"""

    await query.edit_message_text(
        message_text,
        reply_markup=get_challenge_keyboard(can_complete),
        parse_mode='Markdown'
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Помощь по боту 'Малый Шаг'*\n\n"
        "🎯 *Как это работает:*\n"
        "Каждый день выбирай категорию и выполняй маленькое задание. "
        "Не пропускай дни — копи стрик и зарабатывай монеты!\n\n"
        "*Команды:*\n"
        "/start — главное меню\n"
        "/help — это сообщение\n"
        "/report — сообщить об ошибке\n\n"
        "*Категории:*\n"
        "💪 Спорт — физические активности\n"
        "🧠 Мышление — саморазвитие и учёба\n"
        "🎨 Креатив — творческие задания\n"
        "🤝 Общение — социальные челленджи\n\n"
        "*Магазин:*\n"
        "🛡️ Заморозка стрика — защита от сброса\n"
        "⚡ x2 монеты — двойная награда 7 дней\n\n"
        "*Стрик сбрасывается в 00:00 если не выполнил задание за день.*"
    )
    keyboard = [[InlineKeyboardButton("🎯 Начать", callback_data='back_to_categories')]]
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def complete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    try:
        result = db.complete_challenge(user_id)
    except Exception as e:
        logger.error(f"Ошибка при выполнении челленджа: {e}")
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='back_to_main')]]
        await query.edit_message_text("❌ Произошла ошибка. Попробуйте еще раз.",
                                      reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if not result.get('success', False):
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='back_to_main')]]
        await query.edit_message_text(result.get('message', 'Ошибка'),
                                      reply_markup=InlineKeyboardMarkup(keyboard))
        return

    streak = int(result.get('streak', 1))
    total = int(result.get('total', 1))
    coins_earned = int(result.get('coins_earned', 5))
    total_coins = int(result.get('total_coins', 5))

    try:
        user_data = db.get_stats(user_id)
        new_achievements = check_achievements(user_id, user_data)
    except Exception as e:
        logger.error(f"Ошибка при проверке достижений: {e}")
        new_achievements = []

    milestone_messages = check_milestones(streak, total)

    if streak == 1:
        streak_msg = "🌱 Отличное начало! Первый шаг сделан!"
    elif streak < 7:
        streak_msg = f"🔥 Streak: {streak} дней! Продолжай в том же духе!"
    elif streak < 30:
        streak_msg = f"🔥🔥 Streak: {streak} дней! Ты на верном пути!"
    else:
        streak_msg = f"🔥🔥🔥 Невероятно! Streak: {streak} дней! Ты чемпион!"

    level = get_user_level(total)

    message_text = (
        "✅ *Поздравляю! Челлендж выполнен!*\n\n"
        f"{streak_msg}\n"
        f"📈 Всего выполнено: {total} челленджей\n"
        f"⭐ Уровень: {level}\n"
        f"💰 Получено: +{coins_earned} монет (всего: {total_coins})\n\n"
        "💪 Увидимся завтра!"
    )

    if new_achievements:
        message_text += "\n\n🎉 *НОВЫЕ ДОСТИЖЕНИЯ:*\n"
        for ach in new_achievements:
            message_text += f"\n{ach.get('emoji','🏆')} {ach.get('name','')}"
            message_text += f"\n💰 +{ach.get('reward', 0)} монет!"

    if milestone_messages:
        message_text += "\n\n" + "\n\n".join(milestone_messages)

    keyboard = [
        [InlineKeyboardButton("🔄 Ещё челлендж", callback_data='back_to_categories')],
        [InlineKeyboardButton("👤 Профиль", callback_data='profile')],
    ]

    try:
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения: {e}")


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id

    stats = db.get_stats(user_id)

    if not stats:
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='back_to_main')]]
        if query:
            await query.edit_message_text("Нет данных. Начни выполнять челленджи!",
                                          reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text("Нет данных. Начни выполнять челленджи!",
                                            reply_markup=InlineKeyboardMarkup(keyboard))
        return

    level = get_user_level(stats['total_completed'])

    last_date_formatted = ""
    if stats['last_completed_date']:
        try:
            date_obj = datetime.fromisoformat(stats['last_completed_date'])
            last_date_formatted = date_obj.strftime("%d.%m.%Y")
        except:
            last_date_formatted = stats['last_completed_date']

    category_text = ""
    for cat_key, count in stats['category_stats'].items():
        if cat_key in config.CATEGORIES:
            emoji = config.CATEGORIES[cat_key]['emoji']
            name = config.CATEGORIES[cat_key]['name']
            category_text += f"{emoji} {name}: *{count}*\n"
    if not category_text:
        category_text = "_Пока нет данных_"

    coins = stats.get('coins', 0)
    achievements_count = len(stats.get('achievements', []))

    message_text = f"""📊 *Твоя статистика:*

🔥 Streak: *{stats['streak']} дней* подряд
✅ Всего выполнено: *{stats['total_completed']}* челленджей
⭐ Уровень: *{level}*
💰 Монет: *{coins}*
🏆 Достижений: *{achievements_count}/{len(config.ACHIEVEMENTS)}*

*По категориям:*
{category_text}
{"Последний: *" + last_date_formatted + "*" if last_date_formatted else ""}"""

    keyboard = [
        [InlineKeyboardButton("◀️ Назад в профиль", callback_data='profile')],
    ]

    if query:
        await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard),
                                      parse_mode='Markdown')
    else:
        await update.message.reply_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard),
                                        parse_mode='Markdown')


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stats"""
    await stats_handler(update, context)


async def achievements_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id

    user = db.get_user(user_id)
    if not user:
        return

    import json
    user_achievements = json.loads(user['achievements']) if user['achievements'] else []
    coins = user['coins']

    lines = [f"🏆 *Достижения* ({len(user_achievements)}/{len(config.ACHIEVEMENTS)})\n"]

    for ach_id, ach in config.ACHIEVEMENTS.items():
        if ach_id in user_achievements:
            lines.append(f"{ach['emoji']} *{ach['name']}* ✅\n_{ach['description']}_ — 💰 {ach['reward']} монет")
        else:
            lines.append(f"🔒 *{ach['name']}*\n_{ach['description']}_ — 💰 {ach['reward']} монет")

    lines.append(f"\n💰 Монет: *{coins}*")
    text = "\n\n".join(lines)

    keyboard = [
        [InlineKeyboardButton("◀️ Назад в профиль", callback_data='profile')],
    ]

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                      parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                        parse_mode='Markdown')


async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /achievements"""
    await achievements_handler(update, context)


async def back_to_categories_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат к выбору категорий"""
    query = update.callback_query
    await query.answer()

    text = "🎯 *Выбери категорию челленджа!*\n\nВыбери категорию, чтобы получить задание на день:"
    keyboard = get_category_keyboard()

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Update {update} caused error {context.error}")

# ============= АДМИН ПАНЕЛЬ =============

def is_admin(user_id: int) -> bool:
    """Проверка является ли пользователь админом"""
    return user_id == config.ADMIN_ID


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ-панель"""
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return

    keyboard = [
        [InlineKeyboardButton("📊 Статистика бота", callback_data='admin_stats')],
        [InlineKeyboardButton("👥 Список пользователей", callback_data='admin_users')],
        [InlineKeyboardButton("📢 Рассылка", callback_data='admin_broadcast_menu')],
        [InlineKeyboardButton("🗑️ Удалить пользователя", callback_data='admin_delete_menu')],
        [InlineKeyboardButton("💰 Выдать монеты", callback_data='admin_give_coins')],
        [InlineKeyboardButton("⚠️ Жалобы пользователей", callback_data='admin_reports')],
    ]

    await update.message.reply_text(
        "🔐 *Админ-панель*\n\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def admin_stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика бота для админа"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Доступ запрещен.")
        return

    users = db.get_all_users()
    total_users = len(users)
    today = _today_minsk().isoformat()

    conn = db.get_connection()
    cursor = conn.cursor()

    # ПРАВИЛЬНЫЕ НАЗВАНИЯ С ПОДЧЕРКИВАНИЕМ!
    cursor.execute('SELECT SUM(total_completed) FROM users', ())
    total_challenges = cursor.fetchone()[0] or 0

    cursor.execute('SELECT COUNT(*) FROM users WHERE last_completed_date = %s', (today,))
    total_active_today = cursor.fetchone()[0] or 0

    cursor.execute('SELECT AVG(streak) FROM users')
    avg_streak = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM reports WHERE status = 'pending'")
    pending_reports = cursor.fetchone()[0] or 0

    cursor.execute('SELECT COUNT(*) FROM reports WHERE DATE(created_at) = %s', (today,))
    reports_today = cursor.fetchone()[0] or 0

    cursor.execute('SELECT COUNT(*) FROM users WHERE warnings >= 3')
    banned_users = cursor.fetchone()[0] or 0

    conn.close()

    message = f"""📊 *Статистика бота 'Малый Шаг'*

👥 Всего пользователей: *{total_users}*
✅ Активных сегодня: *{total_active_today}*
🎯 Всего челленджей: *{total_challenges}*
🔥 Средний streak: *{avg_streak:.1f}* дней
⚠️ Новых жалоб: *{pending_reports}*
📋 Жалоб за сегодня: *{reports_today}*
🚫 Забаненных: *{banned_users}*

📈 Показатели растут! 🚀"""

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='admin_back')]]
    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def admin_users_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список пользователей"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    conn = db.get_connection()
    cursor = conn.cursor()

    # ПРАВИЛЬНЫЕ НАЗВАНИЯ С ПОДЧЕРКИВАНИЕМ!
    cursor.execute('''
        SELECT user_id, username, total_completed, streak, coins, warnings
        FROM users
        ORDER BY total_completed DESC
        LIMIT 15
    ''')
    users = cursor.fetchall()
    conn.close()

    if not users:
        message = "📋 Пользователей пока нет."
    else:
        message = "👥 *Топ-15 пользователей:*\n\n"
        for idx, user in enumerate(users, 1):
            user_id, username, total, streak, coins, warnings = user
            username = username or "Без имени"
            warn_text = f" ⚠️{warnings}" if warnings > 0 else ""
            message += f"{idx}. @{username}{warn_text}\n"
            message += f" ID: `{user_id}`\n"
            message += f" ✅ {total} | 🔥 {streak} | 💰 {coins}\n\n"

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='admin_back')]]
    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def send_any_message(bot, chat_id: int, source_msg) -> bool:
    """
    Копирует сообщение любого типа через copy_message.
    Не показывает пометку Переслано. Возвращает True при успехе.
    """
    try:
        await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=source_msg.chat_id,
            message_id=source_msg.message_id,
        )
        return True
    except Exception:
        return False


# ============= РАССЫЛКА =============

async def admin_broadcast_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню рассылки"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    keyboard = [
        [InlineKeyboardButton("📢 Всем пользователям", callback_data='admin_broadcast_all')],
        [InlineKeyboardButton("👤 Одному пользователю", callback_data='admin_broadcast_one')],
        [InlineKeyboardButton("👥 Нескольким пользователям", callback_data='admin_broadcast_multiple')],
        [InlineKeyboardButton("◀️ Назад", callback_data='admin_back')],
    ]

    await query.edit_message_text(
        "📢 *Рассылка сообщений*\n\nВыберите тип рассылки:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def admin_broadcast_all_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылка всем"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='admin_broadcast_menu')]]

    await query.edit_message_text(
        "📢 *Рассылка всем пользователями*\n\n"
        "Отправьте сообщение любого типа:\n"
        "\(текст, фото, видео, голосовое, документ, стикер и т\.д\.\)",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

    context.user_data['awaiting_broadcast'] = 'all'


async def admin_broadcast_one_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылка одному"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='admin_broadcast_menu')]]

    await query.edit_message_text(
        "👤 *Отправить сообщение пользователю*\n\n"
        "Шаг 1: отправьте *только USER\_ID* получателя\n"
        "Например: `123456789`",
        parse_mode='Markdown'
    )

    context.user_data['awaiting_broadcast'] = 'one_waiting_id'


async def admin_broadcast_multiple_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылка нескольким"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='admin_broadcast_menu')]]

    await query.edit_message_text(
        "👥 *Отправить нескольким пользователям*\n\n"
        "Шаг 1: отправьте ID через пробел\n"
        "Например: `123456789 987654321 111222333`",
        parse_mode='Markdown'
    )

    context.user_data['awaiting_broadcast'] = 'multiple_waiting_ids'


# ============= УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЯ =============

async def admin_delete_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню удаления пользователя"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='admin_back')]]

    await query.edit_message_text(
        "🗑️ *Удаление пользователя*\n\n"
        "Отправьте ID пользователя для удаления:\n"
        "`123456789`",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

    context.user_data['awaiting_delete_user'] = True


# ============= ВЫДАТЬ МОНЕТЫ =============

async def admin_give_coins_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выдать монеты"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='admin_back')]]

    await query.edit_message_text(
        "💰 *Выдать монеты*\n\n"
        "Формат: `USER_ID КОЛИЧЕСТВО`\n\n"
        "Например: `123456789 100`",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

    context.user_data['awaiting_give_coins'] = True


# ============= ЖАЛОБЫ ПОЛЬЗОВАТЕЛЕЙ =============

async def admin_reports_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    reports = db.getpendingreports()

    if not reports:
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='admin_back')]]
        await query.edit_message_text("📭 Нет ожидающих жалоб!", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    keyboard = []
    for report in reports[:10]:
        report_id = report['id']
        userid = report['user_id']  # БЕЗ подчёркивания
        username = report['username']
        message_text = report['message']

        short_msg = message_text[:30] + '...' if len(message_text) > 30 else message_text
        keyboard.append(
            [InlineKeyboardButton(f"{username or userid}: {short_msg}", callback_data=f'admin_report_{report_id}')])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='admin_back')])

    await query.edit_message_text(
        f"⚠️ Жалобы пользователей ({len(reports)}):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def admin_report_detail_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Детали жалобы"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    report_id = int(query.data.replace('admin_report_', ''))

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, message, created_at
        FROM reports WHERE id = %s
    ''', (report_id,))

    report = cursor.fetchone()
    conn.close()

    if not report:
        await query.edit_message_text("❌ Жалоба не найдена.")
        return

    user_id, username, message, created_at = report

    username = escape_markdown(username) if username else None
    message = escape_markdown(message)

    text = f"""⚠️ *Жалоба #{report_id}*

👤 От: @{username or 'Без имени'}
🆔 ID: `{user_id}`
📅 Дата: {created_at}

📝 *Сообщение:*
{message}"""

    keyboard = [
        [InlineKeyboardButton("✉️ Ответить", callback_data=f'admin_reply_{report_id}_{user_id}')],
        [InlineKeyboardButton("✅ Одобрить", callback_data=f'admin_approve_{report_id}_{user_id}')],
        [InlineKeyboardButton("❌ Отклонить", callback_data=f'admin_reject_{report_id}_{user_id}')],
        [InlineKeyboardButton("⚠️ Выдать предупреждение", callback_data=f'admin_warn_{report_id}_{user_id}')],
        [InlineKeyboardButton("◀️ К списку жалоб", callback_data='admin_reports')],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def admin_reply_report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ответить на жалобу"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    parts = query.data.split('_')
    report_id = int(parts[2])
    user_id = int(parts[3])

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data=f'admin_report_{report_id}')]]

    await query.edit_message_text(
        "✉️ *Ответ пользователю*\n\nОтправьте текст ответа:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

    context.user_data['awaiting_reply'] = {
        'report_id': report_id,
        'user_id': user_id
    }


async def admin_approve_report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Одобрить жалобу"""
    query = update.callback_query
    await query.answer("✅ Жалоба одобрена")

    if not is_admin(query.from_user.id):
        return

    parts = query.data.split('_')
    report_id = int(parts[2])
    user_id = int(parts[3])

    db.update_report_status(report_id, 'approved', 'Жалоба рассмотрена положительно')

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ *Ответ от Администрации*\n\nВаша жалоба рассмотрена и принята к сведению. Спасибо за обратную связь!",
            parse_mode='Markdown'
        )
    except:
        pass

    await admin_reports_handler(update, context)


async def admin_reject_report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отклонить жалобу"""
    query = update.callback_query
    await query.answer("❌ Жалоба отклонена")

    if not is_admin(query.from_user.id):
        return

    parts = query.data.split('_')
    report_id = int(parts[2])
    user_id = int(parts[3])

    db.update_report_status(report_id, 'rejected', 'Жалоба отклонена')

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ *Ответ от Администрации*\n\nВаша жалоба была рассмотрена и отклонена.",
            parse_mode='Markdown'
        )
    except:
        pass

    await admin_reports_handler(update, context)


async def admin_warn_report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выдать предупреждение"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    parts = query.data.split('_')
    report_id = int(parts[2])
    user_id = int(parts[3])

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data=f'admin_report_{report_id}')]]

    await query.edit_message_text(
        "⚠️ *Предупреждение пользователю*\n\nОтправьте текст предупреждения:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

    context.user_data['awaiting_warning'] = {
        'report_id': report_id,
        'user_id': user_id
    }


# ============= ОБРАБОТЧИК СООБЩЕНИЙ ДЛЯ АДМИНА =============

async def admin_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user_id = update.effective_user.id
    text = update.message.text

    # ========== ДЛЯ ОБЫЧНЫХ ПОЛЬЗОВАТЕЛЕЙ ==========
    if context.user_data.get('awaiting_report'):
        context.user_data['awaiting_report'] = False

        # Дополнительная проверка перед отправкой
        if db.is_user_banned(user_id):
            await update.message.reply_text("⛔ Доступ заблокирован.")
            return

        reports_today = db.count_user_reports_today(user_id)
        if reports_today >= 5:
            await update.message.reply_text("⚠️ Лимит жалоб исчерпан на сегодня.")
            return

        # Проверка длины сообщения
        if len(text) < 10:
            await update.message.reply_text(
                "❌ Сообщение слишком короткое.\n"
                "Минимум 10 символов. Попробуйте еще раз: /report"
            )
            return

        if len(text) > 1000:
            await update.message.reply_text(
                "❌ Сообщение слишком длинное.\n"
                "Максимум 1000 символов."
            )
            return

        username = update.effective_user.username or update.effective_user.first_name

        try:
            db.add_report(user_id, username, text)

            remaining = 5 - reports_today - 1

            await update.message.reply_text(
                f"✅ *Ваше сообщение отправлено администрации!*\n\n"
                f"Мы рассмотрим его в ближайшее время.\n\n"
                f"Осталось жалоб сегодня: *{remaining}/5*",
                parse_mode='Markdown'
            )

            # Уведомляем админа
            try:
                await context.bot.send_message(
                    chat_id=config.ADMIN_ID,
                    text=f"⚠️ *Новая жалоба #{reports_today + 1}*\n\n"
                         f"От: @{username}\n"
                         f"ID: `{user_id}`\n"
                         f"Жалоб сегодня: {reports_today + 1}/5\n\n"
                         f"{text}",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить админа: {e}")
        except Exception as e:
            logger.error(f"Ошибка добавления жалобы: {e}")
            await update.message.reply_text("❌ Произошла ошибка. Попробуйте позже.")

        return

    # ========== ТОЛЬКО ДЛЯ АДМИНА ДАЛЬШЕ ==========
    if not is_admin(user_id):
        return

    # Рассылка всем
    if context.user_data.get('awaiting_broadcast') == 'all':
        context.user_data['awaiting_broadcast'] = None
        users = db.get_all_users()
        sent = 0
        failed = 0
        await update.message.reply_text(f"📤 Начинаю рассылку для {len(users)} пользователей...")
        for target_user_id in users:
            ok = await send_any_message(context.bot, target_user_id, update.message)
            if ok:
                sent += 1
            else:
                failed += 1
            import asyncio
            await asyncio.sleep(0.05)
        await update.message.reply_text(f"✅ Рассылка завершена!\n\n📤 Отправлено: {sent}\n❌ Ошибок: {failed}")
        return

    # Рассылка одному
    if context.user_data.get('awaiting_broadcast') == 'one_waiting_id':
        msg_text = getattr(update.message, 'text', None)
        if msg_text is None or not msg_text.strip().isdigit():
            await update.message.reply_text('❌ Введите корректный числовой USER_ID')
            return
        context.user_data['broadcast_one_target'] = int(msg_text.strip())
        context.user_data['awaiting_broadcast'] = 'one_waiting_msg'
        await update.message.reply_text(
            f'✅ ID `{msg_text.strip()}` принят.\n'
            'Шаг 2: теперь отправьте сообщение любого типа для этого пользователя.',
            parse_mode='Markdown'
        )
        return

    elif context.user_data.get('awaiting_broadcast') == 'one_waiting_msg':
        target_user_id = context.user_data.pop('broadcast_one_target', None)
        context.user_data['awaiting_broadcast'] = None
        if target_user_id is None:
            await update.message.reply_text('❌ ID не найден, начните заново.')
            return
        ok = await send_any_message(context.bot, target_user_id, update.message)
        if ok:
            await update.message.reply_text(f'✅ Сообщение отправлено пользователю {target_user_id}')
        else:
            await update.message.reply_text(f'❌ Ошибка отправки пользователю {target_user_id}')
        return

    # Рассылка нескольким
    if context.user_data.get('awaiting_broadcast') == 'multiple_waiting_ids':
        msg_text = getattr(update.message, 'text', '')
        if not msg_text:
            await update.message.reply_text('❌ Введите ID через пробел')
            return
        ids = [int(x) for x in msg_text.strip().split() if x.strip().isdigit()]
        if not ids:
            await update.message.reply_text('❌ Не найдено ни одного корректного ID')
            return
        context.user_data['broadcast_multiple_targets'] = ids
        context.user_data['awaiting_broadcast'] = 'multiple_waiting_msg'
        await update.message.reply_text(
            f'✅ Принято {len(ids)} ID.\n'
            'Шаг 2: отправьте сообщение любого типа для рассылки.'
        )
        return

    elif context.user_data.get('awaiting_broadcast') == 'multiple_waiting_msg':
        ids = context.user_data.pop('broadcast_multiple_targets', [])
        context.user_data['awaiting_broadcast'] = None
        sent = 0
        for target_user_id in ids:
            ok = await send_any_message(context.bot, target_user_id, update.message)
            if ok:
                sent += 1
            import asyncio
            await asyncio.sleep(0.05)
        await update.message.reply_text(f'✅ Отправлено {sent} из {len(ids)} пользователям')
        return

    # Удаление пользователя
    if context.user_data.get('awaiting_delete_user'):
        context.user_data['awaiting_delete_user'] = False

        try:
            target_user_id = int(text.strip())
            db.delete_user_data(target_user_id)
            await update.message.reply_text(f"✅ Пользователь {target_user_id} удален")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    # Выдача монет
    if context.user_data.get('awaiting_give_coins'):
        context.user_data['awaiting_give_coins'] = False

        try:
            parts = text.split()
            target_user_id = int(parts[0])
            amount = int(parts[1])

            db.add_coins(target_user_id, amount)
            await update.message.reply_text(f"✅ Выдано {amount} монет пользователю {target_user_id}")

            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"🎁 Вам начислено {amount} монет от администрации!"
                )
            except:
                pass
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    # Ответ на жалобу
    if context.user_data.get('awaiting_reply'):
        data = context.user_data['awaiting_reply']
        context.user_data['awaiting_reply'] = None

        report_id = data['report_id']
        target_user_id = data['user_id']

        db.update_report_status(report_id, 'answered', text)

        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"✉️ *Ответ от Администрации:*\n\n{text}",
                parse_mode='Markdown'
            )
            await update.message.reply_text("✅ Ответ отправлен пользователю")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка отправки: {e}")
        return

    # Предупреждение
    if context.user_data.get('awaiting_warning'):
        data = context.user_data['awaiting_warning']
        context.user_data['awaiting_warning'] = None

        report_id = data['report_id']
        target_user_id = data['user_id']

        db.add_warning(target_user_id)
        db.update_report_status(report_id, 'warned', text)

        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"⚠️ *ПРЕДУПРЕЖДЕНИЕ от Администрации:*\n\n{text}",
                parse_mode='Markdown'
            )
            await update.message.reply_text("✅ Предупреждение выдано")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
        return


# ============= ЖАЛОБА ДЛЯ ОБЫЧНЫХ ПОЛЬЗОВАТЕЛЕЙ =============

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подать жалобу/сообщение об ошибке"""
    user_id = update.effective_user.id

    # Проверка на бан (3+ предупреждений)
    if db.is_user_banned(user_id):
        await update.message.reply_text(
            "⛔ *Доступ к отправке жалоб заблокирован*\n\n"
            "У вас 3 или более предупреждений.\n"
            "Обратитесь к администратору.",
            parse_mode='Markdown'
        )
        return

    # Проверка лимита жалоб за день (макс 5)
    reports_today = db.count_user_reports_today(user_id)
    if reports_today >= 5:
        await update.message.reply_text(
            "⚠️ *Лимит жалоб исчерпан*\n\n"
            "Вы можете отправить максимум 5 жалоб в день.\n"
            "Попробуйте завтра.",
            parse_mode='Markdown'
        )
        return

    # Проверка на спам (минимум 1 минута между жалобами)
    last_report = db.get_last_report_time(user_id)
    if last_report:
        from datetime import datetime, timedelta
        try:
            last_time = datetime.fromisoformat(last_report)
            now = datetime.now()
            time_diff = (now - last_time).total_seconds()

            if time_diff < 60:  # Меньше 1 минуты
                wait_time = int(60 - time_diff)
                await update.message.reply_text(
                    f"⏳ *Подождите {wait_time} секунд*\n\n"
                    "Между отправкой жалоб должно пройти минимум 1 минута.",
                    parse_mode='Markdown'
                )
                return
        except:
            pass

    # Показываем оставшиеся жалобы
    remaining = 5 - reports_today
    keyboard = [[InlineKeyboardButton("❌ Отменить", callback_data='cancel_report')]]
    await update.message.reply_text(
        f"📝 *Сообщить об ошибке/проблеме*\n\n"
        f"Напишите ваше сообщение следующим сообщением.\n\n"
        f"Осталось жалоб сегодня: *{remaining}/5*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    context.user_data['awaiting_report'] = True


async def cancel_report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена подачи жалобы"""
    query = update.callback_query
    await query.answer()

    context.user_data.clear()
    await query.edit_message_text("❌ Отменено.")


# ============= МАГАЗИН =============

async def shop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Магазин"""
    query = update.callback_query
    if query:
        await query.answer()
    user_id = query.from_user.id if query else update.effective_user.id
    user = db.get_user(user_id)
    coins = user['coins'] if user else 0
    today = _today_minsk()

    freeze_until = user.get('streak_freeze_until') if user else None
    double_until = user.get('double_coins_until') if user else None
    last_coinflip = user.get('lastcoinflipdate') if user else None

    freeze_status = ""
    if freeze_until and date.fromisoformat(freeze_until) >= today:
        freeze_status = f" ✅ _(до {freeze_until})_"

    double_status = ""
    if double_until and date.fromisoformat(double_until) >= today:
        double_status = f" ✅ _(до {double_until})_"

    coinflip_status = ""
    if last_coinflip == today.isoformat():
        coinflip_status = " ✅ _(сыграно сегодня)_"

    text = (
        f"🛒 *Магазин*\n\n"
        f"💰 Твой баланс: *{coins} монет*\n\n"
        f"*Доступные товары:*\n\n"
        f"🛡️ *Заморозка стрика на 1 день* — 50 🪙{freeze_status}\n"
        f"_Один пропуск не сбросит твой стрик_\n\n"
        f"❄️ *Заморозка стрика на 3 дня* — 120 🪙{freeze_status}\n"
        f"_Три дня пропусков без потери стрика_\n\n"
        f"⚡ *x2 монеты на 7 дней* — 50 🪙{double_status}\n"
        f"_Получай 10 монет вместо 5 за каждый челлендж_\n\n"
        f"🎲 *Коинфлип* — угадай кубик!{coinflip_status}\n"
        f"_Ставь 5–20 монет, угадай исход — 1 раз в день_"
    )

    keyboard = [
        [InlineKeyboardButton("🛡️ Заморозка 1 день — 50 🪙", callback_data='buy_freeze_1')],
        [InlineKeyboardButton("❄️ Заморозка 3 дня — 120 🪙", callback_data='buy_freeze_3')],
        [InlineKeyboardButton("⚡ x2 монеты 7 дней — 50 🪙", callback_data='buy_double')],
        [InlineKeyboardButton("🎲 Коинфлип", callback_data='coinflip')],
        [InlineKeyboardButton("◀️ Назад", callback_data='back_to_main')],
    ]

    if query:
        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )


async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /shop"""
    await shop_handler(update, context)


async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик покупок в магазине"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    action = query.data

    if action == 'buy_freeze_1':
        result = db.buy_streak_freeze(user_id, days=1, cost=50)
        if result['success']:
            text = (
                "✅ *Заморозка куплена!*\n\n"
                f"Стрик защищён до: `{result['freeze_until']}`\n\n"
                "Один пропуск не засчитается! 🛡️"
            )
        else:
            text = f"❌ {result['message']}"

    elif action == 'buy_freeze_3':
        result = db.buy_streak_freeze(user_id, days=3, cost=120)
        if result['success']:
            text = (
                "✅ *Заморозка куплена!*\n\n"
                f"Стрик защищён до: `{result['freeze_until']}`\n\n"
                "Три пропуска не засчитаются! ❄️"
            )
        else:
            text = f"❌ {result['message']}"

    elif action == 'buy_double':
        result = db.buy_double_coins(user_id, cost=50)
        if result['success']:
            text = (
                "✅ *x2 монеты активированы!*\n\n"
                f"Активно до: `{result['double_until']}`\n\n"
                "Теперь получаешь 10 монет за каждый челлендж! ⚡"
            )
        else:
            text = f"❌ {result['message']}"

    else:
        text = "❌ Неизвестный товар"

    keyboard = [[InlineKeyboardButton("◀️ Назад в магазин", callback_data='shop')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


# ============= КОИНФЛИП =============

# In-memory set: защита от double-click в момент ожидания анимации кубика.
# Работает в рамках одного процесса (Railway — один инстанс).
_coinflip_in_progress: set = set()


async def coinflip_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открыть меню коинфлипа из магазина"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = db.get_user(user_id)

    if not user:
        keyboard = [[InlineKeyboardButton("◀️ Назад в магазин", callback_data='shop')]]
        await query.edit_message_text(
            "❌ Пользователь не найден.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    today = _today_minsk().isoformat()
    last_coinflip = user.get('lastcoinflipdate')
    coins = user['coins']

    # Уже играл сегодня — показываем сообщение БЕЗ кнопок ставки
    if last_coinflip == today:
        text = (
            "🎲 *Коинфлип*\n\n"
            "❌ Ты уже играл в коинфлип сегодня.\n\n"
            "Попробуй снова завтра — попытка обновляется каждый день в 00:00 🕐"
        )
        keyboard = [[InlineKeyboardButton("◀️ Назад в магазин", callback_data='shop')]]
        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
        return

    # Не хватает монет даже на минимальную ставку
    if coins < 5:
        text = (
            "🎲 *Коинфлип*\n\n"
            f"❌ У тебя только *{coins} монет* — недостаточно для игры.\n"
            "Минимальная ставка: *5 монет* 🪙\n\n"
            "Выполняй челленджи, чтобы заработать монеты! 💪"
        )
        keyboard = [[InlineKeyboardButton("◀️ Назад в магазин", callback_data='shop')]]
        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
        return

    text = (
        "🎲 *Коинфлип — игра с кубиком*\n\n"
        f"💰 Твой баланс: *{coins} монет*\n\n"
        "📋 *Правила:*\n"
        "• Выбери ставку (5 / 10 / 15 / 20 монет)\n"
        "• Предскажи исход кубика\n"
        "• Угадал → получаешь ставку ×2 💰\n"
        "• Не угадал → теряешь ставку 💸\n\n"
        "⚠️ Одна попытка в сутки\n\n"
        "👇 Выбери ставку:"
    )

    # Показываем только те ставки, на которые хватает монет
    bet_row = []
    for bet_amount in [5, 10, 15, 20]:
        if coins >= bet_amount:
            bet_row.append(
                InlineKeyboardButton(f"🪙 {bet_amount}", callback_data=f'coinflip_bet_{bet_amount}')
            )

    keyboard = [
        bet_row,
        [InlineKeyboardButton("◀️ Назад в магазин", callback_data='shop')],
    ]
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
    )


async def coinflip_bet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь выбрал ставку — показываем выбор исхода"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    bet = int(query.data.replace('coinflip_bet_', ''))

    # Повторная проверка баланса и даты (мог пройти некоторый период)
    user = db.get_user(user_id)
    if not user:
        keyboard = [[InlineKeyboardButton("◀️ Назад в магазин", callback_data='shop')]]
        await query.edit_message_text(
            "❌ Ошибка. Попробуй снова.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    today = _today_minsk().isoformat()
    if user.get('lastcoinflipdate') == today:
        keyboard = [[InlineKeyboardButton("◀️ Назад в магазин", callback_data='shop')]]
        await query.edit_message_text(
            "🎲 *Коинфлип*\n\n❌ Ты уже играл сегодня. Приходи завтра!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return

    if user['coins'] < bet:
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='coinflip')]]
        await query.edit_message_text(
            f"❌ Недостаточно монет для ставки *{bet}* 🪙\n"
            f"Твой баланс: *{user['coins']}* монет",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return

    # Сохраняем ставку — монеты ещё НЕ списаны
    context.user_data['coinflip_bet'] = bet
    logger.info(f"[COINFLIP] User {user_id} selected bet={bet}")

    text = (
        f"🎲 *Коинфлип*\n\n"
        f"💰 Ставка: *{bet} монет*\n"
        f"🏆 Выигрыш при угадывании: *{bet * 2} монет*\n\n"
        f"Выбери исход кубика (🎲 1–6):\n\n"
        f"🔼 *Больше 3* — выпадет 4, 5 или 6\n"
        f"🔽 *3 или меньше* — выпадет 1, 2 или 3"
    )

    keyboard = [
        [
            InlineKeyboardButton("🔼 Больше 3", callback_data='coinflip_high'),
            InlineKeyboardButton("🔽 3 или меньше", callback_data='coinflip_low'),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data='coinflip_cancel')],
    ]
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
    )


async def coinflip_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена — возвращаемся в магазин БЕЗ изменения баланса"""
    query = update.callback_query
    user_id = query.from_user.id
    context.user_data.pop('coinflip_bet', None)
    logger.info(f"[COINFLIP] User {user_id} cancelled (no coins changed)")
    # Передаём управление shop_handler (он сам сделает query.answer())
    await shop_handler(update, context)


async def coinflip_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь выбрал исход (high/low) — бросаем кубик и подводим итог"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # --- Anti-double-click: проверяем in-progress ---
    if user_id in _coinflip_in_progress:
        await query.answer("⏳ Кубик уже брошен, подожди результата!", show_alert=True)
        return

    bet = context.user_data.get('coinflip_bet')
    if bet is None:
        # Ставка не найдена — устаревшее состояние (напр. после перезапуска бота)
        keyboard = [[InlineKeyboardButton("◀️ Назад в магазин", callback_data='shop')]]
        await query.edit_message_text(
            "❌ Ставка не найдена. Начни игру заново.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    choice = query.data  # 'coinflip_high' или 'coinflip_low'
    choice_text = "🔼 Больше 3" if choice == 'coinflip_high' else "🔽 3 или меньше"

    # Блокируем повторные нажатия на время анимации
    _coinflip_in_progress.add(user_id)

    try:
        # 1. Убираем кнопки немедленно (UI-защита от двойного клика)
        await query.edit_message_text(
            f"🎲 *Коинфлип*\n\n"
            f"💰 Ставка: *{bet} монет*\n"
            f"Твой выбор: *{choice_text}*\n\n"
            f"⏳ Бросаю кубик...",
            parse_mode='Markdown'
            # Без reply_markup — кнопки убраны
        )

        # 2. Атомарная проверка в БД + фиксация даты (lock против повторной игры)
        start_result = db.coinflip_start(user_id, bet)
        if not start_result['success']:
            logger.warning(
                f"[COINFLIP] User {user_id} coinflip_start rejected: {start_result['message']}"
            )
            keyboard = [[InlineKeyboardButton("◀️ Назад в магазин", callback_data='shop')]]
            await query.edit_message_text(
                f"❌ {start_result['message']}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return

        logger.info(f"[COINFLIP] User {user_id} game started: bet={bet}, choice={choice}")

        # 3. Бросаем кубик — sendDice отправляет анимацию отдельным сообщением
        dice_msg = await context.bot.send_dice(
            chat_id=query.message.chat_id,
            emoji='🎲'
        )
        dice_value = dice_msg.dice.value

        logger.info(f"[COINFLIP] User {user_id} dice rolled: value={dice_value}")

        # 4. Ждём окончания анимации кубика (~4 сек)
        await asyncio.sleep(4)

        # 5. Определяем победителя
        if choice == 'coinflip_high':
            won = dice_value > 3
        else:  # coinflip_low
            won = dice_value <= 3

        # 6. Применяем изменение монет в БД
        finish_result = db.coinflip_finish(user_id, bet, won)

        if not finish_result['success']:
            logger.error(
                f"[COINFLIP] User {user_id} coinflip_finish FAILED: {finish_result['message']}"
            )
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    "⚠️ Кубик брошен, но произошла ошибка при обновлении баланса.\n"
                    "Свяжись с поддержкой — твои монеты в безопасности. 🙏"
                ),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◀️ В магазин", callback_data='shop')]]
                )
            )
            return

        new_coins = finish_result['new_coins']

        # 7. Формируем результат
        if won:
            result_emoji = "🎉"
            result_header = "Ты угадал! Победа!"
            coins_line = f"Выигрыш: *+{bet} монет* 💰"
        else:
            result_emoji = "😔"
            result_header = "Не угадал. Удачи в следующий раз!"
            coins_line = f"Проигрыш: *−{bet} монет* 💸"

        result_text = (
            f"🎲 Выпало: *{dice_value}*\n\n"
            f"{result_emoji} *{result_header}*\n\n"
            f"Твой выбор: *{choice_text}*\n"
            f"{coins_line}\n"
            f"Баланс: *{new_coins} монет* 🪙"
        )

        logger.info(
            f"[COINFLIP] User {user_id} RESULT: dice={dice_value}, choice={choice}, "
            f"won={won}, bet={bet}, new_coins={new_coins}"
        )

        keyboard = [[InlineKeyboardButton("◀️ Назад в магазин", callback_data='shop')]]
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=result_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"[COINFLIP] User {user_id} unexpected error: {e}", exc_info=True)
        try:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="❌ Произошла ошибка во время игры. Попробуй позже.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◀️ В магазин", callback_data='shop')]]
                )
            )
        except Exception:
            pass

    finally:
        # Всегда снимаем блокировку и чистим ставку
        _coinflip_in_progress.discard(user_id)
        context.user_data.pop('coinflip_bet', None)


async def back_to_main_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    keyboard = [
        [InlineKeyboardButton("🎯 Получить челлендж", callback_data='back_to_categories')],
        [InlineKeyboardButton("👤 Профиль", callback_data='profile')],
        [InlineKeyboardButton("🛒 Магазин", callback_data='shop')],
    ]
    await query.edit_message_text(
        f"👋 Привет, *{user.first_name}*!\n\nВыбери действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def leaderboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Топ игроков"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    top = db.get_leaderboard()

    if not top:
        text = "🥇 *Топ игроков*\n\nПока никто не выполнил ни одного челленджа. Будь первым!"
    else:
        medals = ['🥇', '🥈', '🥉']
        lines = ["🏆 *Топ игроков по стрику*\n"]

        for i, user in enumerate(top):
            medal = medals[i] if i < 3 else f"{i + 1}."
            name = user['first_name'] or user['username'] or 'Игрок'
            streak = user['streak']
            total = user['total_completed']

            # Подсвечиваем текущего пользователя
            current_user = db.get_user(user_id)
            is_me = (
                current_user and
                current_user['first_name'] == user['first_name'] and
                current_user['streak'] == streak
            )
            marker = " ← ты" if is_me else ""

            lines.append(f"{medal} *{name}* — 🔥 {streak} дней | ✅ {total}{marker}")

        text = "\n".join(lines)

    keyboard = [
        [InlineKeyboardButton("◀️ Назад в профиль", callback_data='profile')],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def admin_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в админ-панель"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    keyboard = [
        [InlineKeyboardButton("📊 Статистика бота", callback_data='admin_stats')],
        [InlineKeyboardButton("👥 Список пользователей", callback_data='admin_users')],
        [InlineKeyboardButton("📢 Рассылка", callback_data='admin_broadcast_menu')],
        [InlineKeyboardButton("🗑️ Удалить пользователя", callback_data='admin_delete_menu')],
        [InlineKeyboardButton("💰 Выдать монеты", callback_data='admin_give_coins')],
        [InlineKeyboardButton("⚠️ Жалобы пользователей", callback_data='admin_reports')],
    ]

    await query.edit_message_text(
        "🔐 *Админ-панель*\n\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


def main():
    """Запуск бота"""

    application = Application.builder().token(config.BOT_TOKEN).build()
    # При старте бота:
    minsk_tz = pytz.timezone("Europe/Minsk")

    scheduler = AsyncIOScheduler(timezone=minsk_tz)
    scheduler.add_job(
        check_and_reset_streaks,
        'cron',
        hour=0,
        minute=0,
        args=[application.bot]
    )
    scheduler.add_job(
        send_evening_reminder,
        'cron',
        hour=20,
        minute=0,
        args=[application.bot]
    )
    scheduler.start()

    # Установка команд меню
    async def post_init(app: Application):
        await application.bot.set_my_commands([
            ("start", "Главное меню"),
            ("help", "Помощь"),
            ("report", "Сообщить об ошибке"),
        ])
        logger.info("✅ Команды меню установлены")

    application.post_init = post_init

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("achievements", achievements_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("challenge", challenge_command))
    application.add_handler(CommandHandler("shop", shop_command))
    application.add_handler(CommandHandler("help", help_command))

    # Обычные callback
    application.add_handler(CallbackQueryHandler(category_handler, pattern='^cat_'))
    application.add_handler(CallbackQueryHandler(complete_handler, pattern='^complete$'))
    application.add_handler(CallbackQueryHandler(another_challenge_handler, pattern='^another$'))
    application.add_handler(CallbackQueryHandler(stats_handler, pattern='^stats$'))
    application.add_handler(CallbackQueryHandler(achievements_handler, pattern='^achievements$'))
    application.add_handler(CallbackQueryHandler(back_to_categories_handler, pattern='^back_to_categories$'))
    application.add_handler(CallbackQueryHandler(shop_handler, pattern='^shop$'))
    application.add_handler(CallbackQueryHandler(buy_handler, pattern='^buy_'))
    application.add_handler(CallbackQueryHandler(back_to_main_handler, pattern='^back_to_main$'))
    application.add_handler(CallbackQueryHandler(profile_handler, pattern='^profile$'))
    application.add_handler(CallbackQueryHandler(leaderboard_handler, pattern='^leaderboard$'))
    application.add_handler(CallbackQueryHandler(coinflip_menu_handler, pattern='^coinflip$'))
    application.add_handler(CallbackQueryHandler(coinflip_bet_handler, pattern='^coinflip_bet_'))
    application.add_handler(CallbackQueryHandler(coinflip_choice_handler, pattern='^coinflip_(high|low)$'))
    application.add_handler(CallbackQueryHandler(coinflip_cancel_handler, pattern='^coinflip_cancel$'))

    # Админ callback
    application.add_handler(CallbackQueryHandler(admin_stats_handler, pattern='^admin_stats$'))
    application.add_handler(CallbackQueryHandler(admin_users_handler, pattern='^admin_users$'))
    application.add_handler(CallbackQueryHandler(admin_broadcast_menu_handler, pattern='^admin_broadcast_menu$'))
    application.add_handler(CallbackQueryHandler(admin_broadcast_all_handler, pattern='^admin_broadcast_all$'))
    application.add_handler(CallbackQueryHandler(admin_broadcast_one_handler, pattern='^admin_broadcast_one$'))
    application.add_handler(
        CallbackQueryHandler(admin_broadcast_multiple_handler, pattern='^admin_broadcast_multiple$')
    )
    application.add_handler(CallbackQueryHandler(admin_delete_menu_handler, pattern='^admin_delete_menu$'))
    application.add_handler(CallbackQueryHandler(admin_give_coins_handler, pattern='^admin_give_coins$'))
    application.add_handler(CallbackQueryHandler(admin_reports_handler, pattern='^admin_reports$'))
    application.add_handler(CallbackQueryHandler(admin_back_handler, pattern='^admin_back$'))
    application.add_handler(CallbackQueryHandler(cancel_report_handler, pattern='^cancel_report$'))

    # Паттерны с параметрами
    application.add_handler(CallbackQueryHandler(admin_report_detail_handler, pattern='^admin_report_\\d+$'))
    application.add_handler(CallbackQueryHandler(admin_reply_report_handler, pattern='^admin_reply_'))
    application.add_handler(CallbackQueryHandler(admin_approve_report_handler, pattern='^admin_approve_'))
    application.add_handler(CallbackQueryHandler(admin_reject_report_handler, pattern='^admin_reject_'))
    application.add_handler(CallbackQueryHandler(admin_warn_report_handler, pattern='^admin_warn_'))

    # Обработчик текстовых сообщений (ПОСЛЕДНИМ!)
    # Расширенный фильтр: текст + медиа для рассылки
    _broadcast_filter = (
        filters.TEXT | filters.PHOTO | filters.VIDEO | filters.VOICE |
        filters.DOCUMENT | filters.STICKER | filters.AUDIO | filters.VIDEO_NOTE
    )
    application.add_handler(MessageHandler(_broadcast_filter & ~filters.COMMAND, admin_message_handler))

    # Обработчик ошибок
    application.add_error_handler(error_handler)

    logger.info("Бот 'Малый Шаг' запущен!")
    logger.info(f"Напоминания настроены на {config.REMINDER_TIME.strftime('%H:%M')} {config.TIMEZONE}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

