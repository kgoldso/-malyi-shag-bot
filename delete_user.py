# delete_user.py
import sqlite3
import config


def search_users(search_term):
    """Поиск пользователей по имени или ID"""
    conn = sqlite3.connect(config.DATABASE_NAME)
    cursor = conn.cursor()

    # Поиск по username или user_id
    cursor.execute('''
        SELECT user_id, username, streak, total_completed, last_completed_date 
        FROM users 
        WHERE username LIKE ? OR CAST(user_id AS TEXT) LIKE ?
    ''', (f'%{search_term}%', f'%{search_term}%'))

    results = cursor.fetchall()
    conn.close()

    return results


def show_all_users():
    """Показать всех пользователей"""
    conn = sqlite3.connect(config.DATABASE_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT user_id, username, streak, total_completed, last_completed_date 
        FROM users 
        ORDER BY total_completed DESC
    ''')

    results = cursor.fetchall()
    conn.close()

    return results


def delete_user(user_id):
    """Удаление конкретного пользователя"""
    conn = sqlite3.connect(config.DATABASE_NAME)
    cursor = conn.cursor()

    # Проверяем, существует ли пользователь
    cursor.execute('SELECT username FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user:
        print(f"❌ Пользователь с ID {user_id} не найден!")
        conn.close()
        return False

    cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))

    conn.commit()
    conn.close()
    print(f"✅ Пользователь {user[0]} (ID: {user_id}) удален!")
    return True


def print_users(users):
    """Красивый вывод списка пользователей"""
    if not users:
        print("❌ Пользователи не найдены")
        return

    print("\n" + "=" * 80)
    print(f"{'№':<4} {'User ID':<15} {'Username':<20} {'Streak':<8} {'Total':<8} {'Last Date':<12}")
    print("=" * 80)

    for idx, user in enumerate(users, 1):
        user_id, username, streak, total, last_date = user
        username = username or "Без имени"
        last_date = last_date or "Никогда"
        print(f"{idx:<4} {user_id:<15} {username:<20} {streak:<8} {total:<8} {last_date:<12}")

    print("=" * 80 + "\n")


def main():
    """Главное меню"""
    print("🗑️  Управление пользователями бота 'Малый Шаг'\n")

    while True:
        print("Выберите действие:")
        print("1. Показать всех пользователей")
        print("2. Найти пользователя")
        print("3. Удалить пользователя по ID")
        print("4. Выход")

        choice = input("\nВаш выбор (1-4): ").strip()

        if choice == '1':
            print("\n📋 Все пользователи:")
            users = show_all_users()
            print_users(users)

        elif choice == '2':
            search_term = input("\n🔍 Введите имя пользователя или часть ID: ").strip()
            if search_term:
                print(f"\n🔍 Результаты поиска по '{search_term}':")
                users = search_users(search_term)
                print_users(users)

                if users:
                    delete_choice = input("\nХотите удалить кого-то из списка? (yes/no): ").strip().lower()
                    if delete_choice == 'yes':
                        try:
                            user_id = int(input("Введите User ID для удаления: ").strip())
                            confirm = input(
                                f"⚠️  Вы уверены, что хотите удалить пользователя {user_id}? (yes/no): ").strip().lower()
                            if confirm == 'yes':
                                delete_user(user_id)
                        except ValueError:
                            print("❌ Неверный формат ID!")

        elif choice == '3':
            try:
                user_id = int(input("\n🗑️  Введите User ID для удаления: ").strip())
                confirm = input(
                    f"⚠️  Вы уверены, что хотите удалить пользователя {user_id}? (yes/no): ").strip().lower()
                if confirm == 'yes':
                    delete_user(user_id)
            except ValueError:
                print("❌ Неверный формат ID! Введите число.")

        elif choice == '4':
            print("👋 До свидания!")
            break

        else:
            print("❌ Неверный выбор! Попробуйте снова.")

        print()


if __name__ == '__main__':
    main()
