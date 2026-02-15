# migrate_admin.py
import sqlite3


def migrate():
    """Миграция для админ-панели"""
    conn = sqlite3.connect('habits_bot.db')
    cursor = conn.cursor()

    # Добавляем колонку warnings если её нет
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN warnings INTEGER DEFAULT 0')
        print("✅ Добавлена колонка warnings")
    except Exception as e:
        print(f"⚠️ Колонка warnings: {e}")

    # Создаём таблицу reports
    try:
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
        print("✅ Создана таблица reports")
    except Exception as e:
        print(f"⚠️ Таблица reports: {e}")

    conn.commit()
    conn.close()
    print("✅ Миграция завершена!")


if __name__ == '__main__':
    migrate()
