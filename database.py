import os
import json
from datetime import datetime, date
from typing import Optional, Dict, Any, List
import config

# Определяем тип БД
USE_POSTGRES = os.getenv('DATABASE_URL') is not None

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import Json
else:
    import sqlite3


class Database:
    def __init__(self):
        self.use_postgres = USE_POSTGRES

        if self.use_postgres:
            self.db_url = os.getenv('DATABASE_URL')
            # Railway иногда даёт postgres://, но psycopg2 требует postgresql://
            if self.db_url.startswith('postgres://'):
                self.db_url = self.db_url.replace('postgres://', 'postgresql://', 1)
        else:
            self.db_name = config.DATABASE_NAME

        self.init_db()

    def get_connection(self):
        """Получить подключение к БД"""
        if self.use_postgres:
            return psycopg2.connect(self.db_url)
        else:
            conn = sqlite3.connect(self.db_name)
            conn.row_factory = sqlite3.Row
            return conn

    def init_db(self):
        """Инициализация базы данных"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if self.use_postgres:
            # PostgreSQL синтаксис
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    language_code TEXT DEFAULT 'ru',
                    streak INTEGER DEFAULT 0,
                    longest_streak INTEGER DEFAULT 0,
                    total_completed INTEGER DEFAULT 0,
                    coins INTEGER DEFAULT 0,
                    last_completed_date TEXT,
                    purchased_items TEXT DEFAULT '[]',
                    achievements TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    warnings INTEGER DEFAULT 0
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS reports (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    username TEXT,
                    message TEXT,
                    status TEXT DEFAULT 'pending',
                    admin_response TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
        else:
            # SQLite синтаксис
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    language_code TEXT DEFAULT 'ru',
                    streak INTEGER DEFAULT 0,
                    longest_streak INTEGER DEFAULT 0,
                    total_completed INTEGER DEFAULT 0,
                    coins INTEGER DEFAULT 0,
                    last_completed_date TEXT,
                    purchased_items TEXT DEFAULT '[]',
                    achievements TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    warnings INTEGER DEFAULT 0
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    message TEXT,
                    status TEXT DEFAULT 'pending',
                    admin_response TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')

            # Таблица истории челленджей
            if self.use_postgres:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS history (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        category TEXT,
                        challenge TEXT,
                        completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                    )
                ''')
            else:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        category TEXT,
                        challenge TEXT,
                        completed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                    )
                ''')

        # Добавляем колонки для текущего челленджа, если их нет
        try:
            if self.use_postgres:
                cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_challenge TEXT")
                cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_category TEXT")
            else:
                cursor.execute("PRAGMA table_info(users)")
                columns = [col[1] for col in cursor.fetchall()]
                if 'current_challenge' not in columns:
                    cursor.execute('ALTER TABLE users ADD COLUMN current_challenge TEXT')
                if 'current_category' not in columns:
                    cursor.execute('ALTER TABLE users ADD COLUMN current_category TEXT')
        except Exception as e:
            pass  # Колонки уже существуют

        conn.commit()
        conn.close()

    def add_user(self, user_id: int, username: str, first_name: str, language_code: str = 'ru'):
        """Добавить пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO users (user_id, username, first_name, language_code)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id) DO NOTHING
                ''', (user_id, username, first_name, language_code))
            else:
                cursor.execute('''
                    INSERT OR IGNORE INTO users (user_id, username, first_name, language_code)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, username, first_name, language_code))

            conn.commit()
        finally:
            conn.close()

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получить данные пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'SELECT * FROM users WHERE user_id = {param}', (user_id,))
            row = cursor.fetchone()

            if row:
                if self.use_postgres:
                    columns = [desc[0] for desc in cursor.description]
                    return dict(zip(columns, row))
                else:
                    return dict(row)
            return None
        finally:
            conn.close()

    def get_stats(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получить статистику пользователя"""
        user = self.get_user(user_id)
        if not user:
            return None

        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            # Считаем статистику по категориям из истории
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'''
                SELECT category, COUNT(*) as count
                FROM history
                WHERE user_id = {param}
                GROUP BY category
            ''', (user_id,))

            category_stats = {}
            for row in cursor.fetchall():
                category_stats[row[0]] = row[1]

            # Получаем историю для check_achievements
            cursor.execute(f'''
                SELECT category, challenge, completed_at
                FROM history
                WHERE user_id = {param}
                ORDER BY completed_at DESC
            ''', (user_id,))

            history = []
            for row in cursor.fetchall():
                history.append({
                    'category': row[0],
                    'challenge': row[1],
                    'completed_at': row[2]
                })

            # Базовая статистика из таблицы users
            stats = {
                'user_id': user['user_id'],
                'username': user['username'],
                'streak': user['streak'],
                'longest_streak': user['longest_streak'],
                'total_completed': user['total_completed'],
                'coins': user['coins'],
                'last_completed_date': user['last_completed_date'],
                'achievements': json.loads(user['achievements']) if user['achievements'] else [],
                'category_stats': category_stats,
                'history': history
            }

            return stats
        finally:
            conn.close()

    def update_streak(self, user_id: int):
        """Обновить streak"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            today = date.today().isoformat()
            param = '%s' if self.use_postgres else '?'

            cursor.execute(f'''
                UPDATE users 
                SET streak = streak + 1,
                    longest_streak = GREATEST(longest_streak, streak + 1),
                    total_completed = total_completed + 1,
                    last_completed_date = {param}
                WHERE user_id = {param}
            ''', (today, user_id))

            conn.commit()
        finally:
            conn.close()

    def reset_streak(self, user_id: int):
        """Сбросить streak"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'''
                UPDATE users 
                SET streak = 0
                WHERE user_id = {param}
            ''', (user_id,))

            conn.commit()
        finally:
            conn.close()

    def add_coins(self, user_id: int, amount: int):
        """Добавить монеты"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'''
                UPDATE users 
                SET coins = coins + {param}
                WHERE user_id = {param}
            ''', (amount, user_id))

            conn.commit()
        finally:
            conn.close()

    def get_coins(self, user_id: int) -> int:
        """Получить количество монет"""
        user = self.get_user(user_id)
        return user['coins'] if user else 0

    def purchase_item(self, user_id: int, item_id: str, cost: int) -> bool:
        """Купить предмет"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'SELECT coins, purchased_items FROM users WHERE user_id = {param}', (user_id,))
            row = cursor.fetchone()

            if not row:
                return False

            coins = row[0]
            purchased_items = json.loads(row[1]) if row[1] else []

            if coins < cost or item_id in purchased_items:
                return False

            purchased_items.append(item_id)
            new_coins = coins - cost

            cursor.execute(f'''
                UPDATE users 
                SET coins = {param}, purchased_items = {param}
                WHERE user_id = {param}
            ''', (new_coins, json.dumps(purchased_items), user_id))

            conn.commit()
            return True
        finally:
            conn.close()

    def get_purchased_items(self, user_id: int) -> list:
        """Получить купленные предметы"""
        user = self.get_user(user_id)
        if user and user['purchased_items']:
            return json.loads(user['purchased_items'])
        return []

    def add_achievement(self, user_id: int, achievement_id: str) -> bool:
        """Добавить достижение"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'SELECT achievements FROM users WHERE user_id = {param}', (user_id,))
            row = cursor.fetchone()

            if row:
                achievements = json.loads(row[0]) if row[0] else []

                # Проверяем, нет ли уже такого достижения
                if achievement_id in achievements:
                    return False

                achievements.append(achievement_id)
                cursor.execute(f'''
                    UPDATE users
                    SET achievements = {param}
                    WHERE user_id = {param}
                ''', (json.dumps(achievements), user_id))
                conn.commit()
                return True
            return False
        finally:
            conn.close()

    def get_achievements(self, user_id: int) -> list:
        """Получить достижения"""
        user = self.get_user(user_id)
        if user and user['achievements']:
            return json.loads(user['achievements'])
        return []

    def add_report(self, user_id: int, username: str, message: str):
        """Добавить жалобу"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO reports (user_id, username, message)
                    VALUES (%s, %s, %s)
                ''', (user_id, username, message))
            else:
                cursor.execute('''
                    INSERT INTO reports (user_id, username, message)
                    VALUES (?, ?, ?)
                ''', (user_id, username, message))

            conn.commit()
        finally:
            conn.close()

    def getpendingreports(self) -> list:
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM reports WHERE status = 'pending' ORDER BY createdat DESC")
            rows = cursor.fetchall()

            if self.use_postgres:
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
            else:
                return [dict(row) for row in rows]
        finally:
            conn.close()

    def update_report_status(self, report_id: int, status: str, admin_response: str = None):
        """Обновить статус жалобы"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'''
                UPDATE reports 
                SET status = {param}, admin_response = {param}
                WHERE id = {param}
            ''', (status, admin_response, report_id))

            conn.commit()
        finally:
            conn.close()

    def add_warning(self, user_id: int):
        """Добавить предупреждение"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'''
                UPDATE users 
                SET warnings = warnings + 1
                WHERE user_id = {param}
            ''', (user_id,))

            conn.commit()
        finally:
            conn.close()

    def delete_user_data(self, user_id: int):
        """Удалить данные пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'DELETE FROM reports WHERE user_id = {param}', (user_id,))
            cursor.execute(f'DELETE FROM users WHERE user_id = {param}', (user_id,))
            conn.commit()
        finally:
            conn.close()

    def get_all_users(self) -> list:
        """Получить всех пользователей"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT user_id FROM users')
            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_last_report_time(self, user_id: int):
        """Получить время последней жалобы"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'''
                SELECT created_at 
                FROM reports 
                WHERE user_id = {param}
                ORDER BY created_at DESC 
                LIMIT 1
            ''', (user_id,))

            result = cursor.fetchone()
            return result[0] if result else None
        finally:
            conn.close()

    def count_user_reports_today(self, user_id: int) -> int:
        """Подсчитать жалобы за сегодня"""
        today = date.today().isoformat()
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'''
                SELECT COUNT(*) 
                FROM reports 
                WHERE user_id = {param} AND DATE(created_at) = {param}
            ''', (user_id, today))

            return cursor.fetchone()[0]
        finally:
            conn.close()

    def is_user_banned(self, user_id: int) -> bool:
        """Проверка на бан"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'SELECT warnings FROM users WHERE user_id = {param}', (user_id,))
            result = cursor.fetchone()

            return result and result[0] >= 3
        finally:
            conn.close()

    def update_challenge(self, user_id: int, challenge: str, category: str):
        """Обновить текущий челлендж пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            param = '%s' if self.use_postgres else '?'
            cursor.execute(f'''
                UPDATE users
                SET current_challenge = {param}, current_category = {param}
                WHERE user_id = {param}
            ''', (challenge, category, user_id))
            conn.commit()
        finally:
            conn.close()

    def complete_challenge(self, user_id: int) -> Dict[str, Any]:
        """Завершить челлендж"""
        user = self.get_user(user_id)
        if not user:
            return {'success': False, 'message': 'Пользователь не найден'}

        today = date.today().isoformat()

        # Проверка, не завершал ли уже сегодня
        if user.get('last_completed_date') == today:
            return {
                'success': False,
                'message': 'Ты уже выполнил челлендж сегодня!'
            }

        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            param = '%s' if self.use_postgres else '?'

            # Получаем текущий челлендж и категорию
            current_challenge = user.get('current_challenge', '')
            current_category = user.get('current_category', 'unknown')

            # Обновляем streak и статистику
            cursor.execute(f'''
                UPDATE users
                SET streak = streak + 1,
                    longest_streak = CASE 
                        WHEN streak + 1 > longest_streak THEN streak + 1 
                        ELSE longest_streak 
                    END,
                    total_completed = total_completed + 1,
                    coins = coins + 5,
                    last_completed_date = {param}
                WHERE user_id = {param}
            ''', (today, user_id))

            # Добавляем в историю
            if self.use_postgres:
                cursor.execute('''
                    INSERT INTO history (user_id, category, challenge)
                    VALUES (%s, %s, %s)
                ''', (user_id, current_category, current_challenge))
            else:
                cursor.execute('''
                    INSERT INTO history (user_id, category, challenge)
                    VALUES (?, ?, ?)
                ''', (user_id, current_category, current_challenge))

            conn.commit()
            print(f"DEBUG: Completed challenge for user {user_id}, added 5 coins")

            # Получаем обновленные данные
            cursor.execute(f'SELECT * FROM users WHERE user_id = {param}', (user_id,))
            row = cursor.fetchone()

            if self.use_postgres:
                columns = [desc[0] for desc in cursor.description]
                updated_user = dict(zip(columns, row))
            else:
                updated_user = dict(row)

            return {
                'success': True,
                'streak': updated_user['streak'],
                'total': updated_user['total_completed'],
                'coins_earned': 5,
                'total_coins': updated_user['coins']
            }
        except Exception as e:
            conn.rollback()
            return {'success': False, 'message': f'Ошибка: {str(e)}'}
        finally:
            conn.close()



