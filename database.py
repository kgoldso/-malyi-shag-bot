# database.py
import sqlite3
import json
from datetime import datetime, date
from typing import Optional, Dict, Any, List
import config


class Database:
    """Класс для работы с базой данных SQLite"""

    def __init__(self, db_name: str = config.DATABASE_NAME):
        self.db_name = db_name
        self.init_db()

    def get_connection(self):
        """Создание подключения к БД"""
        return sqlite3.connect(self.db_name)

    def init_db(self):
        """Инициализация базы данных"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                streak INTEGER DEFAULT 0,
                total_completed INTEGER DEFAULT 0,
                last_completed_date TEXT,
                current_challenge TEXT,
                current_category TEXT,
                history TEXT DEFAULT '[]',
                coins INTEGER DEFAULT 0,
                achievements TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()

    def add_user(self, user_id: int, username: str) -> bool:
        """Добавление нового пользователя"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                INSERT OR IGNORE INTO users (user_id, username)
                VALUES (?, ?)
            ''', (user_id, username))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Ошибка добавления пользователя: {e}")
            return False

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получение данных пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Явно указываем какие колонки нам нужны
        cursor.execute('''
            SELECT user_id, username, streak, total_completed, last_completed_date,
                   current_challenge, current_category, history, 
                   COALESCE(coins, 0) as coins, 
                   COALESCE(achievements, '[]') as achievements,
                   created_at
            FROM users 
            WHERE user_id = ?
        ''', (user_id,))

        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                'user_id': row[0],
                'username': row[1],
                'streak': row[2] or 0,
                'total_completed': row[3] or 0,
                'last_completed_date': row[4],
                'current_challenge': row[5],
                'current_category': row[6],
                'history': json.loads(row[7]) if row[7] else [],
                'coins': row[8] or 0,
                'achievements': json.loads(row[9]) if row[9] and row[9] != '[]' else [],
                'created_at': row[10] if len(row) > 10 else None
            }
        return None

    def update_challenge(self, user_id: int, challenge: str, category: str):
        """Обновление текущего челленджа"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE users 
            SET current_challenge = ?, current_category = ?
            WHERE user_id = ?
        ''', (challenge, category, user_id))

        conn.commit()
        conn.close()

    def complete_challenge(self, user_id: int) -> Dict[str, Any]:
        """Отметка челленджа как выполненного"""
        user = self.get_user(user_id)
        if not user:
            return {'success': False, 'message': 'Пользователь не найден'}

        today = date.today().isoformat()
        last_date = user['last_completed_date']

        # Проверка, что уже выполнил сегодня
        if last_date == today:
            return {
                'success': False,
                'message': '❌ Ты уже выполнил челлендж сегодня! Приходи завтра 😊'
            }

        # Проверка streak
        if last_date:
            last_date_obj = datetime.fromisoformat(last_date).date()
            from datetime import timedelta
            yesterday = datetime.now().date() - timedelta(days=1)

            if last_date_obj == yesterday:
                new_streak = user['streak'] + 1
            else:
                new_streak = 1
        else:
            new_streak = 1

        # Награда: 5 монет за каждый челлендж
        coins_earned = 5
        current_coins = user.get('coins') or 0  # Защита от None
        new_coins = int(current_coins) + int(coins_earned)

        # Обновление истории
        history = user['history']
        history.append({
            'date': today,
            'challenge': user['current_challenge'],
            'category': user['current_category']
        })

        # Обновление БД
        conn = self.get_connection()
        cursor = conn.cursor()

        new_total = user['total_completed'] + 1

        cursor.execute('''
            UPDATE users 
            SET streak = ?,
                total_completed = ?,
                last_completed_date = ?,
                history = ?,
                coins = ?
            WHERE user_id = ?
        ''', (int(new_streak), int(new_total), today, json.dumps(history, ensure_ascii=False), int(new_coins), user_id))

        conn.commit()
        conn.close()

        # Возвращаем все значения как int
        return {
            'success': True,
            'streak': int(new_streak),
            'total': int(new_total),
            'coins_earned': int(coins_earned),
            'total_coins': int(new_coins)
        }

    def add_coins(self, user_id: int, amount: int):
        """Добавление монет пользователю"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE users 
            SET coins = coins + ?
            WHERE user_id = ?
        ''', (amount, user_id))

        conn.commit()
        conn.close()

    def add_achievement(self, user_id: int, achievement_id: str) -> bool:
        """Добавление достижения пользователю"""
        user = self.get_user(user_id)
        if not user:
            return False

        achievements = user['achievements']
        if achievement_id in achievements:
            return False  # Уже есть

        achievements.append(achievement_id)

        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE users 
            SET achievements = ?
            WHERE user_id = ?
        ''', (json.dumps(achievements), user_id))

        conn.commit()
        conn.close()

        return True

    # database.py
    # В класс Database добавьте эти методы:

    def add_report(self, user_id: int, username: str, message: str):
        """Добавить жалобу/отчет от пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO reports (user_id, username, message)
            VALUES (?, ?, ?)
        ''', (user_id, username, message))

        conn.commit()
        conn.close()

    def get_pending_reports(self):
        """Получить все необработанные жалобы"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, user_id, username, message, created_at
            FROM reports
            WHERE status = 'pending'
            ORDER BY created_at DESC
        ''')

        reports = cursor.fetchall()
        conn.close()
        return reports

    def get_user_reports(self, user_id: int):
        """Получить все жалобы конкретного пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, message, status, admin_response, created_at
            FROM reports
            WHERE user_id = ?
            ORDER BY created_at DESC
        ''', (user_id,))

        reports = cursor.fetchall()
        conn.close()
        return reports

    def update_report_status(self, report_id: int, status: str, admin_response: str = None):
        """Обновить статус жалобы"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE reports
            SET status = ?, admin_response = ?
            WHERE id = ?
        ''', (status, admin_response, report_id))

        conn.commit()
        conn.close()

    def add_warning(self, user_id: int):
        """Добавить предупреждение пользователю"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE users
            SET warnings = warnings + 1
            WHERE user_id = ?
        ''', (user_id,))

        conn.commit()
        conn.close()

    def delete_user_data(self, user_id: int):
        """Удалить пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM reports WHERE user_id = ?', (user_id,))

        conn.commit()
        conn.close()

    def add_coins(self, user_id: int, amount: int):
        """Добавить монеты пользователю"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE users
            SET coins = coins + ?
            WHERE user_id = ?
        ''', (amount, user_id))

        conn.commit()
        conn.close()

    def get_stats(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получение статистики пользователя"""
        user = self.get_user(user_id)
        if not user:
            return None

        # Подсчет по категориям
        history = user['history']
        category_stats = {}

        for entry in history:
            cat = entry.get('category', 'unknown')
            category_stats[cat] = category_stats.get(cat, 0) + 1

        return {
            'streak': user['streak'],
            'total_completed': user['total_completed'],
            'category_stats': category_stats,
            'last_completed_date': user['last_completed_date'],
            'coins': user['coins'],
            'achievements': user['achievements']
        }

    def get_all_users(self) -> list:
        """Получение всех пользователей (для рассылки напоминаний)"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT user_id FROM users')
        users = [row[0] for row in cursor.fetchall()]

        conn.close()
        return users


def complete_challenge(self, user_id: int) -> Dict[str, Any]:
    """Отметка челленджа как выполненного"""
    user = self.get_user(user_id)
    if not user:
        return {'success': False, 'message': 'Пользователь не найден'}

    today = date.today().isoformat()
    last_date = user['last_completed_date']

    # Проверка, что уже выполнил сегодня
    if last_date == today:
        return {
            'success': False,
            'message': '❌ Ты уже выполнил челлендж сегодня! Приходи завтра 😊'
        }

    # Проверка streak
    if last_date:
        last_date_obj = datetime.fromisoformat(last_date).date()
        yesterday = datetime.now().date()
        from datetime import timedelta
        yesterday = yesterday - timedelta(days=1)

        if last_date_obj == yesterday:
            new_streak = user['streak'] + 1
        else:
            new_streak = 1
    else:
        new_streak = 1

    # Награда: 5 монет за каждый челлендж
    coins_earned = 5
    new_coins = int(user.get('coins', 0)) + coins_earned  # Явное преобразование

    # Обновление истории
    history = user['history']
    history.append({
        'date': today,
        'challenge': user['current_challenge'],
        'category': user['current_category']
    })

    # Обновление БД
    conn = self.get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE users 
        SET streak = ?,
            total_completed = total_completed + 1,
            last_completed_date = ?,
            history = ?,
            coins = ?
        WHERE user_id = ?
    ''', (new_streak, today, json.dumps(history, ensure_ascii=False), new_coins, user_id))

    conn.commit()
    conn.close()

    # Возвращаем все значения как int
    return {
        'success': True,
        'streak': int(new_streak),
        'total': int(user['total_completed'] + 1),
        'coins_earned': int(coins_earned),
        'total_coins': int(new_coins)
    }


def init_db(self):
    """Инициализация базы данных"""
    conn = self.get_connection()
    cursor = conn.cursor()

    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            streak INTEGER DEFAULT 0,
            total_completed INTEGER DEFAULT 0,
            last_completed_date TEXT,
            current_challenge TEXT,
            current_category TEXT,
            history TEXT DEFAULT '[]',
            coins INTEGER DEFAULT 0,
            achievements TEXT DEFAULT '[]',
            warnings INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # НОВАЯ ТАБЛИЦА: Жалобы пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            message TEXT,
            status TEXT DEFAULT 'pending',
            admin_response TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    conn.commit()
    conn.close()


# Добавьте новые методы в класс Database:

def add_report(self, user_id: int, username: str, message: str):
    """Добавить жалобу/отчет от пользователя"""
    conn = self.get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO reports (user_id, username, message)
        VALUES (?, ?, ?)
    ''', (user_id, username, message))

    conn.commit()
    conn.close()


def get_pending_reports(self):
    """Получить все необработанные жалобы"""
    conn = self.get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, user_id, username, message, created_at
        FROM reports
        WHERE status = 'pending'
        ORDER BY created_at DESC
    ''')

    reports = cursor.fetchall()
    conn.close()
    return reports


def get_user_reports(self, user_id: int):
    """Получить все жалобы конкретного пользователя"""
    conn = self.get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, message, status, admin_response, created_at
        FROM reports
        WHERE user_id = ?
        ORDER BY created_at DESC
    ''', (user_id,))

    reports = cursor.fetchall()
    conn.close()
    return reports


def update_report_status(self, report_id: int, status: str, admin_response: str = None):
    """Обновить статус жалобы"""
    conn = self.get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE reports
        SET status = ?, admin_response = ?
        WHERE id = ?
    ''', (status, admin_response, report_id))

    conn.commit()
    conn.close()


def add_warning(self, user_id: int):
    """Добавить предупреждение пользователю"""
    conn = self.get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE users
        SET warnings = warnings + 1
        WHERE user_id = ?
    ''', (user_id,))

    conn.commit()
    conn.close()


def delete_user_data(self, user_id: int):
    """Удалить пользователя"""
    conn = self.get_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM reports WHERE user_id = ?', (user_id,))

    conn.commit()
    conn.close()