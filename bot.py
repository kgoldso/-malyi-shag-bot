# bot.py
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
)
from telegram.error import BadRequest

import config
from database import Database

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name

    # Регистрация пользователя
    db.add_user(user_id, username)

    welcome_text = """Привет! 👋 Я бот 'Малый Шаг' 🌱

Помогу тебе формировать полезные привычки через маленькие ежедневные действия.

Каждый день я предложу тебе простой челлендж на 5-20 минут.

Выбери категорию:"""

    keyboard = get_category_keyboard()

    # НЕ удаляем сообщение /start от пользователя
    # Отправляем новое сообщение
    sent_message = await update.message.reply_text(
        welcome_text,
        reply_markup=keyboard
    )

    # Сохраняем ID нового сообщения
    context.user_data['last_bot_message_id'] = sent_message.message_id


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
        user_data = db.get_user(user_id)
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

    user_achievements = user['achievements']
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


def main():
    """Запуск бота"""
    # Создание приложения
    application = Application.builder().token(config.BOT_TOKEN).build()

    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("achievements", achievements_command))

    # Регистрация обработчиков callback
    application.add_handler(CallbackQueryHandler(category_handler, pattern='^cat_'))
    application.add_handler(CallbackQueryHandler(complete_handler, pattern='^complete$'))
    application.add_handler(CallbackQueryHandler(another_challenge_handler, pattern='^another$'))
    application.add_handler(CallbackQueryHandler(stats_handler, pattern='^stats$'))
    application.add_handler(CallbackQueryHandler(achievements_handler, pattern='^achievements$'))
    application.add_handler(CallbackQueryHandler(back_to_categories_handler, pattern='^back_to_categories$'))

    # Обработчик ошибок
    application.add_error_handler(error_handler)

    # Настройка ежедневных напоминаний в 9:00 по московскому времени
    job_queue = application.job_queue
    job_queue.run_daily(
        send_daily_reminder,
        time=config.REMINDER_TIME,
        days=(0, 1, 2, 3, 4, 5, 6)
    )

    # Запуск бота
    logger.info("Бот 'Малый Шаг' запущен!")
    logger.info(f"Напоминания настроены на {config.REMINDER_TIME.strftime('%H:%M')} {config.TIMEZONE}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
