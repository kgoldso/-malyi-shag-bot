# migrate_db.py
import sqlite3


def migrate():
    conn = sqlite3.connect('habits_bot.db')
    cursor = conn.cursor()

    # Добавляем колонку coins если её нет
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN coins INTEGER DEFAULT 0')
        print("✅ Добавлена колонка coins")
    except Exception as e:
        print(f"⚠️ Колонка coins: {e}")

    # Добавляем колонку achievements если её нет
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN achievements TEXT DEFAULT '[]'")
        print("✅ Добавлена колонка achievements")
    except Exception as e:
        print(f"⚠️ Колонка achievements: {e}")

    # Обновляем все NULL значения на дефолтные
    cursor.execute('UPDATE users SET coins = 0 WHERE coins IS NULL')
    cursor.execute("UPDATE users SET achievements = '[]' WHERE achievements IS NULL")

    conn.commit()
    conn.close()
    print("✅ Миграция завершена!")


if __name__ == '__main__':
    migrate()
