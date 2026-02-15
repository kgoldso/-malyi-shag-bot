import sqlite3

conn = sqlite3.connect('habits_bot.db')
cursor = conn.cursor()

print("=" * 60)
print("ПРОВЕРКА БАЗЫ ДАННЫХ")
print("=" * 60)

# Проверяем структуру users
try:
    cursor.execute("PRAGMA table_info(users)")
    cols = cursor.fetchall()
    print("\n📋 Колонки в таблице users:")
    for col in cols:
        print(f"  - {col[1]} ({col[2]})")
except Exception as e:
    print(f"ОШИБКА таблицы users: {e}")

# Проверяем данные
try:
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    print(f"\n👥 Пользователей в базе: {count}")

    if count > 0:
        cursor.execute("SELECT userid, username, totalcompleted, streak FROM users LIMIT 3")
        users = cursor.fetchall()
        print("\n📊 Примеры данных:")
        for u in users:
            print(f"  ID: {u[0]}, Name: {u[1]}, Total: {u[2]}, Streak: {u[3]}")
except Exception as e:
    print(f"ОШИБКА чтения данных: {e}")

# Проверяем структуру reports
try:
    cursor.execute("PRAGMA table_info(reports)")
    cols = cursor.fetchall()
    print("\n📋 Колонки в таблице reports:")
    for col in cols:
        print(f"  - {col[1]} ({col[2]})")
except Exception as e:
    print(f"ОШИБКА таблицы reports: {e}")

conn.close()
print("\n" + "=" * 60)