# check_db.py
import sqlite3

conn = sqlite3.connect('habits_bot.db')
cursor = conn.cursor()

# Проверяем структуру таблицы
cursor.execute("PRAGMA table_info(users)")
columns = cursor.fetchall()

print("📋 Структура таблицы users:")
print("="*60)
for col in columns:
    print(f"{col[1]:<20} {col[2]:<15} Default: {col[4]}")
print("="*60)

# Проверяем данные пользователей
cursor.execute("SELECT user_id, username, coins, achievements FROM users")
users = cursor.fetchall()

print("\n👥 Пользователи:")
print("="*60)
for user in users:
    print(f"ID: {user[0]}, Name: {user[1]}, Coins: {user[2]}, Achievements: {user[3]}")
print("="*60)

conn.close()
