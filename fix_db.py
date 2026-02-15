import sqlite3
import shutil
from datetime import datetime

# Бэкап на всякий случай
shutil.copy('habits_bot.db', f'habits_bot_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db')
print("✅ Создан бэкап базы данных")

conn = sqlite3.connect('habits_bot.db')
cursor = conn.cursor()

try:
    print("\n🔄 Начинаю миграцию...")

    # Создаём новую таблицу с правильными именами (БЕЗ подчеркиваний)
    cursor.execute('''
        CREATE TABLE users_new (
            userid INTEGER PRIMARY KEY,
            username TEXT,
            firstname TEXT,
            languagecode TEXT DEFAULT 'ru',
            streak INTEGER DEFAULT 0,
            longeststreak INTEGER DEFAULT 0,
            totalcompleted INTEGER DEFAULT 0,
            coins INTEGER DEFAULT 0,
            lastcompleteddate TEXT,
            purchaseditems TEXT DEFAULT '[]',
            achievements TEXT DEFAULT '[]',
            createdat TEXT DEFAULT CURRENT_TIMESTAMP,
            warnings INTEGER DEFAULT 0,
            currentchallenge TEXT,
            currentcategory TEXT
        )
    ''')

    # Копируем данные из старой таблицы
    cursor.execute('''
        INSERT INTO users_new 
        (userid, username, streak, totalcompleted, lastcompleteddate, 
         coins, achievements, createdat, currentchallenge, currentcategory)
        SELECT user_id, username, streak, total_completed, last_completed_date,
               COALESCE(coins, 0), COALESCE(achievements, '[]'), created_at, 
               current_challenge, current_category
        FROM users
    ''')

    # Создаём таблицу history если её нет
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            userid INTEGER,
            category TEXT,
            challenge TEXT,
            completedat TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (userid) REFERENCES users(userid)
        )
    ''')

    # Создаём таблицу reports
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            userid INTEGER,
            username TEXT,
            message TEXT,
            status TEXT DEFAULT 'pending',
            adminresponse TEXT,
            createdat TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (userid) REFERENCES users(userid)
        )
    ''')

    # Удаляем старую и переименовываем новую
    cursor.execute('DROP TABLE users')
    cursor.execute('ALTER TABLE users_new RENAME TO users')

    conn.commit()
    print("✅ Миграция успешно завершена!")
    print("\n📊 Проверка данных:")

    # Проверяем что всё ок
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    print(f"   Пользователей в базе: {count}")

    if count > 0:
        cursor.execute("SELECT userid, username, totalcompleted FROM users LIMIT 3")
        users = cursor.fetchall()
        for u in users:
            print(f"   ID: {u[0]}, Name: {u[1]}, Completed: {u[2]}")

except Exception as e:
    print(f"❌ ОШИБКА: {e}")
    conn.rollback()
    print("\n⚠️ Откат изменений... Данные не изменены")

conn.close()
print("\n✅ Готово! Теперь можно запускать бота")
