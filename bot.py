import logging
import random
from datetime import datetime, date
import asyncio
from typing import Dict, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from telegram.error import BadRequest
import config
from database import Database
from functools import wraps


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


def get_user_level(total_completed: int) -> str:
    """Определение уровня пользователя"""
    level = "🌱 Новичок"
    for threshold, level_name in sorted(config.LEVELS.items(), reverse=True):
        if total_completed >= threshold:
            level = level_name
            break
    return level


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


def check_achievements(user_id: int, user_data: Dict) -> List[Dict]:
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


async def delete_old_bot_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Удаление старого сообщения бота"""
    if 'last_bot_message_id' in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=context.user_data['last_bot_message_id']
            )
        except BadRequest:
            pass  # Сообщение уже удалено или недоступно


@ensure_user
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user

    # Добавляем/обновляем пользователя
    db.add_user(
        user_id=user.id,
        username=user.username or user.first_name,
        first_name=user.first_name,
        language_code=user.language_code or 'ru'
    )

    welcome_text = f"""👋 Привет, *{user.first_name}*!

🌱 Добро пожаловать в бот "Малый Шаг"!

Этот бот поможет тебе выработать полезные привычки через маленькие ежедневные задания.

🎯 *Каждый день:*
• Выполняй простое задание
• Получай монеты 🪙
• Увеличивай streak 🔥
• Открывай достижения 🏆

📊 Начни с кнопки ниже чтобы получить свой первый челлендж!"""

    keyboard = [
        [InlineKeyboardButton("🎯 Получить челлендж", callback_data='back_to_categories')],
        [InlineKeyboardButton("📊 Моя статистика", callback_data='stats')],
        [InlineKeyboardButton("🏆 Достижения", callback_data='achievements')],
    ]

    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@ensure_user
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
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_challenge_keyboard(can_complete: bool = True):
    """Создание клавиатуры для челленджа"""
    keyboard = []

    if can_complete:
        keyboard.append([InlineKeyboardButton("✅ Выполнил", callback_data='complete')])
        keyboard.append([InlineKeyboardButton("⏭️ Другой челлендж", callback_data='another')])

    keyboard.append([
        InlineKeyboardButton("📊 Моя статистика", callback_data='stats'),
        InlineKeyboardButton("◀️ Назад к категориям", callback_data='back_to_categories')
    ])

    return InlineKeyboardMarkup(keyboard)


async def category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора категории"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    category = query.data.replace('cat_', '')

    # Проверка, выполнил ли уже сегодня
    user = db.get_user(user_id)

    # ИСПРАВЛЕНИЕ: Проверяем что пользователь существует
    if not user:
        # Если пользователя нет, регистрируем
        username = query.from_user.username or query.from_user.first_name
        db.add_user(
            user_id=user_id,
            username=username,
            first_name=query.from_user.first_name,
            language_code=query.from_user.language_code or 'ru'
        )
        user = db.get_user(user_id)

    today = date.today().isoformat()
    can_complete = user['last_completed_date'] != today

    if not can_complete:
        # Если уже выполнил сегодня, показываем сообщение без челленджа
        emoji = config.CATEGORIES[category]['emoji']
        cat_name = config.CATEGORIES[category]['name']

        message_text = f"""{emoji} *Категория: {cat_name}*

✅ *Отличная работа!*

Ты уже выполнил челлендж сегодня! 

🌟 Возвращайся завтра за новым заданием.
💪 Продолжай развивать свою дисциплину!"""

        keyboard = [
            [InlineKeyboardButton("📊 Моя статистика", callback_data='stats')],
            [InlineKeyboardButton("◀️ Назад к категориям", callback_data='back_to_categories')]
        ]

        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return

    # Выбор случайного челленджа
    challenges = config.CATEGORIES[category]['challenges']
    challenge = random.choice(challenges)

    # Сохранение челленджа
    db.update_challenge(user_id, challenge, category)

    emoji = config.CATEGORIES[category]['emoji']
    cat_name = config.CATEGORIES[category]['name']

    message_text = f"""{emoji} *Категория: {cat_name}*

🎯 *Твой челлендж:*
{challenge}

✨ Выполни задание и нажми кнопку!"""

    keyboard = get_challenge_keyboard(can_complete)

    await query.edit_message_text(
        message_text,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )


async def another_challenge_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Другой челлендж'"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user = db.get_user(user_id)

    if not user or not user['current_category']:
        # Если нет текущей категории, показываем выбор
        text = "Сначала выбери категорию челленджа:"
        keyboard = get_category_keyboard()
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    # Проверка, не выполнил ли уже сегодня
    today = date.today().isoformat()
    can_complete = user['last_completed_date'] != today

    if not can_complete:
        # Если уже выполнил, не даем менять челлендж
        await query.answer("❌ Ты уже выполнил челлендж сегодня! Приходи завтра 😊", show_alert=True)
        return

    category = user['current_category']

    # Новый случайный челлендж
    challenges = config.CATEGORIES[category]['challenges']
    challenge = random.choice(challenges)

    db.update_challenge(user_id, challenge, category)

    emoji = config.CATEGORIES[category]['emoji']
    cat_name = config.CATEGORIES[category]['name']

    message_text = f"""{emoji} *Категория: {cat_name}*

🎯 *Твой новый челлендж:*
{challenge}

✨ Выполни задание и нажми кнопку!"""

    keyboard = get_challenge_keyboard(can_complete)

    await query.edit_message_text(
        message_text,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )


async def complete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Выполнил'"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    try:
        result = db.complete_challenge(user_id)
    except Exception as e:
        logger.error(f"Ошибка при выполнении челленджа: {e}")
        keyboard = [[InlineKeyboardButton("◀️ Назад к категориям", callback_data='back_to_categories')]]
        await query.edit_message_text(
            "❌ Произошла ошибка. Попробуйте еще раз.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if not result.get('success', False):
        keyboard = [[InlineKeyboardButton("◀️ Назад к категориям", callback_data='back_to_categories')]]
        await query.edit_message_text(
            result.get('message', 'Ошибка'),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Безопасное получение значений с преобразованием типов
    streak = int(result.get('streak', 1))
    total = int(result.get('total', 1))
    coins_earned = int(result.get('coins_earned', 5))
    total_coins = int(result.get('total_coins', 5))

    # Проверка достижений
    try:
        user_data = db.get_stats(user_id)  # ← Используем get_stats!
        new_achievements = check_achievements(user_id, user_data)
    except Exception as e:
        logger.error(f"Ошибка при проверке достижений: {e}")
        new_achievements = []

    # Проверка milestone
    milestone_messages = check_milestones(streak, total)

    # Сообщения в зависимости от streak
    if streak == 1:
        streak_msg = "🌱 Отличное начало! Первый шаг сделан!"
    elif streak < 7:
        streak_msg = f"🔥 Streak: {streak} дней! Продолжай в том же духе!"
    elif streak < 30:
        streak_msg = f"🔥🔥 Streak: {streak} дней! Ты на верном пути!"
    else:
        streak_msg = f"🔥🔥🔥 Невероятно! Streak: {streak} дней! Ты чемпион!"

    # Определение уровня
    level = get_user_level(total)

    # Формируем сообщение БЕЗ звездочек около чисел
    message_text = (
        "✅ *Поздравляю! Челлендж выполнен!*\n\n"
        f"{streak_msg}\n"
        f"📈 Всего выполнено: {total} челленджей\n"
        f"⭐ Уровень: {level}\n"
        f"💰 Получено: +{coins_earned} монет (всего: {total_coins})\n\n"
        "💪 Увидимся завтра! Возвращайся за новым заданием."
    )

    # Добавление новых достижений
    if new_achievements:
        message_text += "\n\n🎉 *НОВЫЕ ДОСТИЖЕНИЯ:*\n"
        for ach in new_achievements:
            ach_name = str(ach.get('name', 'Достижение'))
            ach_emoji = str(ach.get('emoji', '🏆'))
            ach_reward = int(ach.get('reward', 0))
            message_text += f"\n{ach_emoji} {ach_name}"
            message_text += f"\n💰 +{ach_reward} монет!"

    # Добавление milestone сообщений
    if milestone_messages:
        message_text += "\n\n" + "\n\n".join(milestone_messages)

    keyboard = [
        [InlineKeyboardButton("🏆 Мои достижения", callback_data='achievements')],
        [InlineKeyboardButton("🔄 Выбрать другую категорию", callback_data='back_to_categories')],
        [InlineKeyboardButton("📊 Моя статистика", callback_data='stats')]
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
    """Обработчик статистики"""
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id

    stats = db.get_stats(user_id)

    if not stats:
        text = "У тебя пока нет статистики. Начни выполнять челленджи!"
        keyboard = [[InlineKeyboardButton("◀️ Назад к категориям", callback_data='back_to_categories')]]
        if query:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Определение уровня
    level = get_user_level(stats['total_completed'])

    # Форматирование даты последнего выполнения
    last_date_formatted = ""
    if stats['last_completed_date']:
        try:
            date_obj = datetime.fromisoformat(stats['last_completed_date'])
            last_date_formatted = date_obj.strftime("%d.%m.%Y")
        except:
            last_date_formatted = stats['last_completed_date']

    # Формирование статистики по категориям
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

*Выполнено по категориям:*
{category_text}
{"Последний челлендж: *" + last_date_formatted + "*" if last_date_formatted else ""}

Продолжай в том же духе! 💪"""

    keyboard = [
        [InlineKeyboardButton("🏆 Мои достижения", callback_data='achievements')],
        [InlineKeyboardButton("🔄 Новый челлендж", callback_data='back_to_categories')],
    ]

    if query:
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stats"""
    await stats_handler(update, context)


async def achievements_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик просмотра достижений"""
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id

    user = db.get_user(user_id)
    if not user:
        return

    # ИСПРАВЛЕНИЕ: Парсим JSON в список
    import json
    user_achievements = json.loads(user['achievements']) if user['achievements'] else []
    coins = user['coins']

    # Формирование списка достижений
    earned_text = ""
    locked_text = ""

    for ach_id, ach in config.ACHIEVEMENTS.items():
        if ach_id in user_achievements:
            earned_text += f"{ach['emoji']} *{ach['name']}*\n"
            earned_text += f"   _{ach['description']}_\n"
            earned_text += f"   💰 +{ach['reward']} монет\n\n"
        else:
            locked_text += f"🔒 {ach['name']}\n"
            locked_text += f"   _{ach['description']}_\n"
            locked_text += f"   💰 {ach['reward']} монет\n\n"

    if not earned_text:
        earned_text = "_Пока нет достижений. Выполняй челленджи!_\n\n"

    message_text = f"""🏆 *Твои достижения*

💰 *Всего монет:* {coins}
🎖️ *Получено:* {len(user_achievements)}/{len(config.ACHIEVEMENTS)}

*✅ Открытые:*
{earned_text}
*🔒 Заблокированные:*
{locked_text[:800]}{"..." if len(locked_text) > 800 else ""}"""

    keyboard = [
        [InlineKeyboardButton("🔄 Новый челлендж", callback_data='back_to_categories')],
        [InlineKeyboardButton("📊 Статистика", callback_data='stats')]
    ]

    if query:
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )


async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /achievements"""
    await achievements_handler(update, context)


async def back_to_categories_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат к выбору категорий"""
    query = update.callback_query
    await query.answer()

    text = "Выбери категорию челленджа:"
    keyboard = get_category_keyboard()

    await query.edit_message_text(text, reply_markup=keyboard)


async def send_daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправка ежедневных напоминаний в 9:00 утра"""
    users = db.get_all_users()

    reminder_text = "Доброе утро! 🌅 Готов к новому челленджу?\n\nВыбери категорию:"
    keyboard = get_category_keyboard()

    for user_id in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=reminder_text,
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Не удалось отправить напоминание пользователю {user_id}: {e}")

        # Задержка, чтобы не превысить лимиты Telegram
        await asyncio.sleep(0.1)

    logger.info(f"Отправлено напоминаний: {len(users)}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Update {update} caused error {context.error}")


# bot.py
# ... существующие импорты ...

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
    today = date.today().isoformat()

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
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


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
        "📢 *Рассылка всем пользователям*\n\n"
        "Отправьте текст сообщения:",
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
        "Формат: `USER_ID текст сообщения`\n\n"
        "Например: `123456789 Привет!`",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

    context.user_data['awaiting_broadcast'] = 'one'


async def admin_broadcast_multiple_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылка нескольким"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='admin_broadcast_menu')]]

    await query.edit_message_text(
        "👥 *Отправить нескольким пользователям*\n\n"
        "Формат (ID через пробел, потом текст):\n"
        "`ID1 ID2 ID3 | текст сообщения`\n\n"
        "Например:\n`123 456 789 | Привет всем!`",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

    context.user_data['awaiting_broadcast'] = 'multiple'


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
    """Список жалоб"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    reports = db.get_pending_reports()

    if not reports:
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='admin_back')]]
        await query.edit_message_text(
            "✅ Новых жалоб нет!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    keyboard = []
    for report in reports[:10]:
        report_id = report['id']
        user_id = report['userid']  # БЕЗ подчеркивания!
        username = report['username']
        message_text = report['message']
        created_at = report['createdat']  # БЕЗ подчеркивания!

        short_msg = message_text[:30] + "..." if len(message_text) > 30 else message_text
        keyboard.append([
            InlineKeyboardButton(
                f"@{username or 'Без имени'}: {short_msg}",
                callback_data=f'admin_report_{report_id}'
            )
        ])

    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data='admin_back')])

    await query.edit_message_text(
        f"⚠️ *Жалобы пользователей* ({len(reports)})\n\nВыберите жалобу:",
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
        SELECT userid, username, message, createdat
        FROM reports WHERE id = %s
    ''', (report_id,))

    report = cursor.fetchone()
    conn.close()

    if not report:
        await query.edit_message_text("❌ Жалоба не найдена.")
        return

    user_id, username, message, created_at = report

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
            try:
                await context.bot.send_message(chat_id=target_user_id, text=text)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                failed += 1

        await update.message.reply_text(
            f"✅ Рассылка завершена!\n\n📤 Отправлено: {sent}\n❌ Ошибок: {failed}"
        )
        return

    # Рассылка одному
    if context.user_data.get('awaiting_broadcast') == 'one':
        context.user_data['awaiting_broadcast'] = None

        try:
            parts = text.split(' ', 1)
            target_user_id = int(parts[0])
            message = parts[1]

            await context.bot.send_message(chat_id=target_user_id, text=message)
            await update.message.reply_text(f"✅ Сообщение отправлено пользователю {target_user_id}")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    # Рассылка нескольким
    if context.user_data.get('awaiting_broadcast') == 'multiple':
        context.user_data['awaiting_broadcast'] = None

        try:
            ids_part, message = text.split('|', 1)
            ids = [int(x.strip()) for x in ids_part.strip().split()]
            message = message.strip()

            sent = 0
            for target_user_id in ids:
                try:
                    await context.bot.send_message(chat_id=target_user_id, text=message)
                    sent += 1
                    await asyncio.sleep(0.05)
                except:
                    pass

            await update.message.reply_text(f"✅ Отправлено {sent} из {len(ids)} пользователям")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}\n\nФормат: `ID1 ID2 | текст`", parse_mode='Markdown')
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

    # ============= СОЗДАНИЕ ПРИЛОЖЕНИЯ =============
    application = Application.builder().token(config.BOT_TOKEN).build()

    # Установка команд меню
    async def post_init(app: Application):
        from telegram import BotCommand
        commands = [
            BotCommand("start", "🌱 Начать работу"),
            BotCommand("stats", "📊 Моя статистика"),
            BotCommand("achievements", "🏆 Достижения"),
            BotCommand("report", "📝 Сообщить об ошибке"),
        ]
        await app.bot.set_my_commands(commands)
        logger.info("✅ Команды меню установлены")

    application.post_init = post_init

    application = Application.builder().token(config.BOT_TOKEN).build()

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("achievements", achievements_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler('challenge', challenge_command))

    # Обычные callback
    application.add_handler(CallbackQueryHandler(category_handler, pattern='^cat_'))
    application.add_handler(CallbackQueryHandler(complete_handler, pattern='^complete$'))
    application.add_handler(CallbackQueryHandler(another_challenge_handler, pattern='^another$'))
    application.add_handler(CallbackQueryHandler(stats_handler, pattern='^stats$'))
    application.add_handler(CallbackQueryHandler(achievements_handler, pattern='^achievements$'))
    application.add_handler(CallbackQueryHandler(back_to_categories_handler, pattern='^back_to_categories$'))

    # Админ callback
    application.add_handler(CallbackQueryHandler(admin_stats_handler, pattern='^admin_stats$'))
    application.add_handler(CallbackQueryHandler(admin_users_handler, pattern='^admin_users$'))
    application.add_handler(CallbackQueryHandler(admin_broadcast_menu_handler, pattern='^admin_broadcast_menu$'))
    application.add_handler(CallbackQueryHandler(admin_broadcast_all_handler, pattern='^admin_broadcast_all$'))
    application.add_handler(CallbackQueryHandler(admin_broadcast_one_handler, pattern='^admin_broadcast_one$'))
    application.add_handler(
        CallbackQueryHandler(admin_broadcast_multiple_handler, pattern='^admin_broadcast_multiple$'))
    application.add_handler(CallbackQueryHandler(admin_delete_menu_handler, pattern='^admin_delete_menu$'))
    application.add_handler(CallbackQueryHandler(admin_give_coins_handler, pattern='^admin_give_coins$'))
    application.add_handler(CallbackQueryHandler(admin_reports_handler, pattern='^admin_reports$'))
    application.add_handler(CallbackQueryHandler(admin_back_handler, pattern='^admin_back$'))
    application.add_handler(CallbackQueryHandler(cancel_report_handler, pattern='^cancel_report$'))

    # Паттерны с параметрами
    application.add_handler(CallbackQueryHandler(admin_report_detail_handler, pattern='^admin_report_\d+$'))
    application.add_handler(CallbackQueryHandler(admin_reply_report_handler, pattern='^admin_reply_'))
    application.add_handler(CallbackQueryHandler(admin_approve_report_handler, pattern='^admin_approve_'))
    application.add_handler(CallbackQueryHandler(admin_reject_report_handler, pattern='^admin_reject_'))
    application.add_handler(CallbackQueryHandler(admin_warn_report_handler, pattern='^admin_warn_'))

    # Обработчик текстовых сообщений (ПОСЛЕДНИМ!)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_message_handler))

    # Обработчик ошибок
    application.add_error_handler(error_handler)

    # Напоминания
    job_queue = application.job_queue
    job_queue.run_daily(
        send_daily_reminder,
        time=config.REMINDER_TIME,
        days=(0, 1, 2, 3, 4, 5, 6)
    )

    logger.info("Бот 'Малый Шаг' запущен!")
    logger.info(f"Напоминания настроены на {config.REMINDER_TIME.strftime('%H:%M')} {config.TIMEZONE}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

