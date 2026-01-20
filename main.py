import asyncio, logging, random, string, paramiko
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from sqlalchemy import select, delete
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import init_db, get_user, async_session, User, VPNServer
from sqlalchemy import func
import keyboards as kb
from keyboards import admin_menu
import config
from database import VPNKey

logging.basicConfig(level=logging.INFO)
bot = Bot(token=config.API_TOKEN)
dp = Dispatcher()

class AdminStates(StatesGroup):
    wait_server_data = State()
    wait_server_id_del = State()
    wait_add_dealer = State()
    wait_remove_dealer = State()
    wait_dealer_pay_id = State()    # ID дилера для пополнения
    wait_dealer_pay_amount = State() # Сумма для пополнения
    wait_broadcast_text = State()

class DealerStates(StatesGroup):
    wait_user_id = State()
    wait_amount = State()


class SupportStates(StatesGroup):
    wait_for_question = State()


async def create_single_user_on_server(server: VPNServer, user: User):
    """Создает ОДНОГО конкретного пользователя на ОДНОМ сервере"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(server.ip, username=server.ssh_user, password=server.ssh_password, timeout=10)
        
        cmds = [
            f"/usr/local/openvpn_as/scripts/sacli --user {user.vpn_login} --key \"type\" --value \"user\" UserPropPut",
            f"/usr/local/openvpn_as/scripts/sacli --user {user.vpn_login} --new_pass \"{user.vpn_password}\" SetLocalPassword",
            f"/usr/local/openvpn_as/scripts/sacli --user {user.vpn_login} --key \"prop_autologin\" --value \"true\" UserPropPut",
            "/usr/local/openvpn_as/scripts/sacli ConfigQuery"
        ]
        
        for cmd in cmds:
            stdin, stdout, stderr = ssh.exec_command(cmd)
            stdout.channel.recv_exit_status() # Ждем выполнения
        
        ssh.close()
        return True
    except Exception as e:
        logging.error(f"Ошибка на {server.ip} для {user.vpn_login}: {e}")
        return False


async def delete_user_from_all_servers(key: VPNKey):
    """Удаляет конкретный VPN-ключ со всех серверов по SSH"""
    async with async_session() as session:
        res = await session.execute(select(VPNServer))
        servers = res.scalars().all()

    for s in servers:
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(s.ip, username=s.ssh_user, password=s.ssh_password, timeout=10)

            # Используем key.vpn_login вместо user.vpn_login
            cmds = [
                f"/usr/local/openvpn_as/scripts/sacli --user {key.vpn_login} UserPropDelAll",
                "/usr/local/openvpn_as/scripts/sacli ConfigQuery"
            ]

            for cmd in cmds:
                stdin, stdout, stderr = ssh.exec_command(cmd)
                stdout.channel.recv_exit_status() # Ждем завершения

            ssh.close()
            logging.info(f"Ключ {key.vpn_login} удален с сервера {s.ip}")
        except Exception as e:
            logging.error(f"Ошибка удаления ключа {key.vpn_login} на {s.ip}: {e}")



async def check_expired_subscriptions():
    """Фоновая задача: проверяет сроки и уведомляет за 3 дня"""
    while True:
        logging.info("Запуск проверки сроков ключей...")
        async with async_session() as session:
            now = datetime.now()
            three_days_later = now + timedelta(days=3)

            # 1. ПРОВЕРКА НАПОМИНАНИЙ (за 3 дня до конца)
            # Ищем ключи, срок которых < чем (сейчас + 3 дня) И уведомление еще не отправлялось
            warning_res = await session.execute(
                select(VPNKey).where(
                    VPNKey.expiry_date <= three_days_later,
                    VPNKey.expiry_date > now, # Еще не просрочен
                    VPNKey.warning_sent == False
                )
            )
            keys_to_warn = warning_res.scalars().all()

            for k in keys_to_warn:
                try:
                    await bot.send_message(
                        k.user_id,
                        f"🔔 <b>Внимание! Подписка скоро истечет</b>\n\n"
                        f"Срок действия ключа <code>{k.vpn_login}</code> заканчивается через 3 дня.\n"
                        f"Пожалуйста, пополните баланс в профиле, чтобы не потерять доступ.",
                        parse_mode="HTML"
                    )
                    k.warning_sent = True # Ставим отметку, чтобы не слать повторно
                    logging.info(f"Отправлено предупреждение для ключа {k.vpn_login}")
                except Exception as e:
                    logging.error(f"Ошибка уведомления за 3 дня: {e}")

            # 2. УДАЛЕНИЕ ПРОСРОЧЕННЫХ (твоя логика)
            expired_res = await session.execute(
                select(VPNKey).where(VPNKey.expiry_date < now)
            )
            expired_keys = expired_res.scalars().all()

            for k in expired_keys:
                await delete_user_from_all_servers(k)
                
                try:
                    await bot.send_message(
                        k.user_id,
                        f"⚠️ Срок действия вашего VPN-ключа <code>{k.vpn_login}</code> истек. Он был удален с серверов.",
                        parse_mode="HTML"
                    )
                except:
                    pass

                await session.delete(k)
                logging.info(f"Удален просроченный ключ {k.vpn_login}")

            await session.commit()

        await asyncio.sleep(3600) # Проверка каждый час



# --- Обработчики ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Разделяем сообщение на части, чтобы найти ID пригласившего (реферальный код)
    args = message.text.split()
    referrer_id = None

    welcome_photo = "https://img.freepik.com/free-photo/vpn-cybersecurity-illustration-woman-with-laptop-protecting-privacy_23-2151997024.jpg" 
    
    welcome_text = (
        "<b>Добро пожаловать в мир без границ!</b> 🌍\n\n"
        "🔒 Мы обеспечиваем безопасное и быстрое соединение.\n"
        "⚡️ Безлимитный трафик и высокая скорость.\n"
        "👥 Приглашайте друзей и получайте бонусы!"
    )


    
    # Если в ссылке есть ID (например, /start 1234567)
    if len(args) > 1:
        try:
            referrer_id = int(args[1])
            # Нельзя пригласить самого себя
            if referrer_id == message.from_user.id:
                referrer_id = None
        except ValueError:
            referrer_id = None

    # Получаем или создаем пользователя
    user = await get_user(message.from_user.id, message.from_user.username)
    
    # Если пользователь новый и пришел по рефералке — записываем это
    async with async_session() as session:
        db_user = await session.get(User, message.from_user.id)
        
        # Если у пользователя еще не указано, кто его пригласил, и у нас есть referrer_id
        if db_user and not db_user.referred_by and referrer_id:
            db_user.referred_by = referrer_id
            await session.commit()
            try:
                await bot.send_message(referrer_id, "🤝 По вашей ссылке зарегистрировался новый пользователь!")
            except:
                pass

        # Проверка прав админа (твой существующий код)
        if user.user_id == config.ADMIN_ID:
            db_user.role = "admin"
            user.role = "admin"
            await session.commit()



            
    await message.answer_photo(
        photo=welcome_photo,
        caption=welcome_text,
        reply_markup=kb.main_menu(user.role),
        parse_mode="HTML"
    )



@dp.message(F.text == "👤 Мой профиль")
async def show_profile(message: types.Message):
    async with async_session() as session:
        # 1. Получаем данные пользователя
        user = await session.get(User, message.from_user.id)
        
        # 2. Получаем все ключи пользователя
        res_keys = await session.execute(
            select(VPNKey).where(VPNKey.user_id == message.from_user.id)
        )
        keys = res_keys.scalars().all()
        
        # 3. Получаем все доступные серверы, чтобы распределить их по тарифам
        res_servers = await session.execute(select(VPNServer))
        all_servers = res_servers.scalars().all()

    text = (
        f"👤 <b>Ваш профиль</b>\n"
        f"ID: <code>{user.user_id}</code>\n"
        f"Баланс: {user.balance} кред.\n"
        f"Роль: {user.role}\n\n"
    )

    if not keys:
        text += "У вас пока нет активных VPN ключей."
    else:
        text += "<b>🔑 Ваши ключи и серверы:</b>\n"
        for k in keys:
            status = "✅" if k.expiry_date > datetime.now() else "❌ Истек"
            
            # Фильтруем серверы, которые подходят под тариф этого ключа
            suitable_servers = [s for s in all_servers if s.tariff_type == k.tariff.lower()]
            server_links = "\n".join([f"  📍 {s.name}: <code>{s.ip}</code>" for s in suitable_servers])
            
            text += (
                f"--------------------------\n"
                f"{status} <b>Тариф: {k.tariff.upper()}</b>\n"
                f"👤 Логин: <code>{k.vpn_login}</code>\n"
                f"🔑 Пасс: <code>{k.vpn_password}</code>\n"
                f"📅 До: {k.expiry_date.strftime('%d.%m.%Y')}\n"
                f"🌐 <b>Серверы для подключения:</b>\n"
                f"{server_links if server_links else '  ⚠️ Серверы временно недоступны'}\n"
            )
    
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "🚀 Купить VPN")
async def show_shop(message: types.Message):
    await message.answer("Выберите тариф:", reply_markup=kb.get_tariff_keyboard(config.PRICES))


@dp.message(F.text == "📖 Инструкция")
async def show_help(message: types.Message):
    guide_text = (
        "🚀 <b>Как подключиться к VPN за 3 шага:</b>\n\n"
        "1️⃣ <b>Скачайте приложение:</b>\n"
        "Установите <b>OpenVPN Connect</b> на ваше устройство (ссылки ниже).\n\n"
        "2️⃣ <b>Получите данные:</b>\n"
        "Зайдите в раздел 👤 <b>Мой профиль</b> и скопируйте:\n"
        "   • IP сервера\n"
        "   • Логин (uXXXXX_XXX)\n"
        "   • Пароль\n\n"
        "3️⃣ <b>Настройте подключение:</b>\n"
        "   • Откройте приложение.\n"
        "   • Выберите вкладку <b>URL</b> (или Import Profile -> URL).\n"
        "   • Введите IP-адрес сервера из профиля.\n"
        "   • Введите ваш Логин и Пароль, когда приложение их запросит.\n"
        "   • Нажмите <b>Connect</b>.\n\n"
        "💡 <i>Если один сервер работает медленно, просто попробуйте другой IP из вашего списка в профиле!</i>"
    )
    
    from keyboards import download_links_menu # убедись, что импорт есть
    await message.answer(guide_text, reply_markup=download_links_menu(), parse_mode="HTML")


# --- ПОЛЬЗОВАТЕЛЬ: ОТПРАВКА ВОПРОСА ---
@dp.message(F.text == "🆘 Поддержка")
async def support_start(message: types.Message, state: FSMContext):
    await message.answer("💬 Опишите вашу проблему или задайте вопрос. Админ ответит вам в ближайшее время.")
    await state.set_state(SupportStates.wait_for_question)

@dp.message(SupportStates.wait_for_question)
async def support_send_to_admin(message: types.Message, state: FSMContext):
    # Отправляем сообщение админу
    admin_text = (
        f"📩 <b>Новое обращение в поддержку!</b>\n"
        f"От: @{message.from_user.username or 'без юзернейма'}\n"
        f"ID: <code>{message.from_user.id}</code>\n\n"
        f"Текст: {message.text}\n\n"
        f"<i>Чтобы ответить, просто используйте функцию 'Reply' (Ответить) на это сообщение.</i>"
    )
    await bot.send_message(config.ADMIN_ID, admin_text, parse_mode="HTML")
    await message.answer("✅ Ваше сообщение отправлено поддержке.")
    await state.clear()

# --- АДМИН: ОТВЕТ НА СООБЩЕНИЕ ---
@dp.message(lambda message: message.reply_to_message and message.from_user.id == config.ADMIN_ID)
async def support_answer(message: types.Message):
    # Пытаемся вытащить ID пользователя из текста пересланного сообщения
    try:
        # Ищем ID в тексте сообщения, на которое отвечает админ
        original_text = message.reply_to_message.text
        # Достаем ID (он у нас между 'ID: ' и новой строкой)
        target_id = int(original_text.split("ID: ")[1].split("\n")[0])
        
        answer_text = (
            f"✉️ <b>Ответ от техподдержки:</b>\n\n"
            f"{message.text}"
        )
        await bot.send_message(target_id, answer_text, parse_mode="HTML")
        await message.answer(f"✅ Ответ отправлен пользователю {target_id}")
    except Exception as e:
        await message.answer(f"❌ Не удалось определить ID пользователя для ответа. {e}")




# --- НАЧАЛО РАССЫЛКИ ---
@dp.callback_query(F.data == "admin_broadcast")
async def broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_ID: return
    await callback.message.answer("📝 Введите текст для рассылки всем пользователям:")
    await state.set_state(AdminStates.wait_broadcast_text)
    await callback.answer()

# --- ПРОЦЕСС РАССЫЛКИ ---
@dp.message(AdminStates.wait_broadcast_text)
async def broadcast_process(message: types.Message, state: FSMContext):
    async with async_session() as session:
        # Получаем ID всех пользователей из базы
        result = await session.execute(select(User.user_id))
        users = result.scalars().all()

    await message.answer(f"🚀 Начинаю рассылку на {len(users)} пользователей...")
    
    count = 0
    errors = 0
    
    for uid in users:
        try:
            # Копируем сообщение пользователя (поддерживает текст, фото, видео)
            await message.copy_to(uid)
            count += 1
            # Небольшая пауза, чтобы Telegram не заблокировал за спам
            await asyncio.sleep(0.05) 
        except Exception:
            errors += 1
    
    await message.answer(f"🏁 Рассылка завершена!\n✅ Успешно: {count}\n❌ Заблокировали бота: {errors}")
    await state.clear()






@dp.callback_query(F.data == "admin_stats")
async def show_admin_stats(callback: types.CallbackQuery):
    # Проверка на админа
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("У вас нет доступа!")
        return

    async with async_session() as session:
        # 1. Общее количество пользователей
        total_users = await session.execute(select(func.count(User.user_id)))
        total_users = total_users.scalar()

        # 2. Количество активных ключей (срок которых не истек)
        active_keys = await session.execute(
            select(func.count(VPNKey.id)).where(VPNKey.expiry_date > datetime.now())
        )
        active_keys = active_keys.scalar()

        # 3. Общая сумма балансов всех пользователей (кредиты в обороте)
        total_balance = await session.execute(select(func.sum(User.balance)))
        total_balance = total_balance.scalar() or 0.0

        # 4. Количество серверов
        total_servers = await session.execute(select(func.count(VPNServer.id)))
        total_servers = total_servers.scalar()

    stats_text = (
        "📊 <b>Статистика сервиса:</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"🔑 Активных подписок: <b>{active_keys}</b>\n"
        f"🖥 Всего серверов: <b>{total_servers}</b>\n"
        f"💰 Кредитов на балансах: <b>{total_balance:.2f}</b>\n\n"
        "<i>Данные обновлены в реальном времени.</i>"
    )

    await callback.message.edit_text(stats_text, reply_markup=admin_menu(), parse_mode="HTML")
    await callback.answer()





# --- СПИСОК ДИЛЕРОВ ---
@dp.callback_query(F.data == "list_dealers")
async def list_dealers(callback: types.CallbackQuery):
    async with async_session() as session:
        # Ищем всех пользователей с ролью dealer
        res = await session.execute(select(User).where(User.role == "dealer"))
        dealers = res.scalars().all()
    
    if not dealers:
        await callback.message.answer("Дилеров пока нет.")
        return

    text = "<b>Список дилеров:</b>\n\n"
    for d in dealers:
        text += f"👤 ID: <code>{d.user_id}</code> | Баланс: {d.balance} кред. | @{d.username or 'нет'}\n"
    
    await callback.message.answer(text, parse_mode="HTML")

# --- ПОПОЛНЕНИЕ ДИЛЕРА АДМИНОМ ---
@dp.callback_query(F.data == "admin_pay_dealer")
async def admin_pay_dealer_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_ID: return
    await callback.message.answer("Введите Telegram ID дилера, которому хотите начислить баланс:")
    await state.set_state(AdminStates.wait_dealer_pay_id)

@dp.message(AdminStates.wait_dealer_pay_id)
async def admin_pay_dealer_id(message: types.Message, state: FSMContext):
    try:
        target_id = int(message.text)
        await state.update_data(target_id=target_id)
        await message.answer("Сколько кредитов начислить?")
        await state.set_state(AdminStates.wait_dealer_pay_amount)
    except:
        await message.answer("ID должен быть числом.")
        await state.clear()

@dp.message(AdminStates.wait_dealer_pay_amount)
async def admin_pay_dealer_finish(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        data = await state.get_data()
        target_id = data['target_id']

        async with async_session() as session:
            user = await session.get(User, target_id)
            if user:
                user.balance += amount
                await session.commit()
                await message.answer(f"✅ Баланс дилера {target_id} пополнен на {amount}!")
                # Уведомляем дилера
                try:
                    await bot.send_message(target_id, f"🎁 Админ пополнил ваш баланс на {amount} кредитов!")
                except: pass
            else:
                await message.answer("❌ Пользователь не найден в базе данных.")
    except:
        await message.answer("Ошибка! Введите число.")
    await state.clear()



@dp.message(F.text == "👥 Рефералы")
async def show_referral_info(message: types.Message):
    # Получаем имя бота, чтобы создать ссылку
    bot_info = await bot.get_me()
    # Ссылка формата: https://t.me/имя_бота?start=твой_id
    ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    
    async with async_session() as session:
        # Считаем, сколько пользователей пригласил текущий юзер
        res = await session.execute(
            select(func.count(User.user_id)).where(User.referred_by == message.from_user.id)
        )
        total_referrals = res.scalar()

    text = (
        "🤝 <b>Реферальная программа</b>\n\n"
        "Зарабатывайте кредиты, приглашая друзей!\n\n"
        "💰 <b>Ваш бонус:</b> 2 кредитов на баланс за <u>первую</u> покупку каждого друга.\n\n"
        f"👥 Вы пригласили: <b>{total_referrals}</b> чел.\n\n"
        f"🔗 <b>Ваша ссылка для приглашения:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "<i>Просто отправьте эту ссылку другу, и когда он совершит покупку, вы получите бонус автоматически.</i>"
    )
    
    await message.answer(text, parse_mode="HTML")



# --- Админка: Серверы ---
@dp.message(F.text == "⚙️ Админка")
async def admin_panel(message: types.Message):
    if message.from_user.id != config.ADMIN_ID: return
    await message.answer("Управление:", reply_markup=kb.admin_menu())

@dp.callback_query(F.data == "manage_servers")
async def m_servers(callback: types.CallbackQuery):
    await callback.message.edit_text("Серверы:", reply_markup=kb.server_manage_menu())

@dp.callback_query(F.data == "add_server")
async def add_server_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите: `IP Имя Тип(standard/vip) SSH_Логин SSH_Пароль`")
    await state.set_state(AdminStates.wait_server_data)



@dp.message(AdminStates.wait_server_data)
async def save_server(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split()
        if len(parts) < 5:
            await message.answer("❌ Ошибка! Формат: IP Имя Тип Логин Пароль")
            return

        ip, name, t_type, s_user, s_pass = parts

        async with async_session() as session:
            # 1. Сохраняем сервер
            new_s = VPNServer(
                ip=ip, name=name, 
                tariff_type=t_type.lower(), 
                ssh_user=s_user, ssh_password=s_pass
            )
            session.add(new_s)
            await session.commit()
            # Обновляем объект, чтобы подтянулся ID
            await session.refresh(new_s) 

            await message.answer(f"⏳ Сервер добавлен. Начинаю перенос пользователей...")
            
            # 2. Запускаем синхронизацию
            count = await sync_all_active_users_to_server(new_s)

        await message.answer(
            f"✅ Сервер <b>{name}</b> настроен!\n"
            f"👥 Аккаунтов создано: <b>{count}</b>", 
            parse_mode="HTML"
        )
        await state.clear()
    except Exception as e:
        logging.error(f"Ошибка в save_server: {e}")
        await message.answer(f"❌ Ошибка: {e}")
        await state.clear()

@dp.callback_query(F.data == "del_server_start")
async def del_server_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите ID сервера для удаления (узнать в списке):")
    await state.set_state(AdminStates.wait_server_id_del)

@dp.message(AdminStates.wait_server_id_del)
async def del_server_fin(message: types.Message, state: FSMContext):
    try:
        sid = int(message.text)
        async with async_session() as session:
            await session.execute(delete(VPNServer).where(VPNServer.id == sid))
            await session.commit()
        await message.answer(f"✅ Сервер {sid} удален.")
    except: await message.answer("Ошибка!")
    await state.clear()



async def sync_all_active_users_to_server(server: VPNServer):
    count = 0
    async with async_session() as session:
        # Берем ВСЕ активные КЛЮЧИ из таблицы VPNKey
        result = await session.execute(
            select(VPNKey).where(VPNKey.expiry_date > datetime.now())
        )
        active_keys = result.scalars().all()

    if not active_keys:
        logging.info("Синхронизация: Активных ключей не найдено.")
        return 0

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(server.ip, username=server.ssh_user, password=server.ssh_password, timeout=15)

        for k in active_keys:
            # Используем k.vpn_login и k.vpn_password из таблицы VPNKey
            logging.info(f"Синхронизация ключа {k.vpn_login} на сервер {server.ip}")

            cmds = [
                f"/usr/local/openvpn_as/scripts/sacli --user {k.vpn_login} --key \"type\" --value \"user\" UserPropPut",
                f"/usr/local/openvpn_as/scripts/sacli --user {k.vpn_login} --new_pass \"{k.vpn_password}\" SetLocalPassword",
                f"/usr/local/openvpn_as/scripts/sacli --user {k.vpn_login} --key \"prop_autologin\" --value \"true\" UserPropPut",
                "/usr/local/openvpn_as/scripts/sacli ConfigQuery"
            ]

            for cmd in cmds:
                stdin, stdout, stderr = ssh.exec_command(cmd)
                stdout.channel.recv_exit_status()

            count += 1

            # Отправляем уведомление владельцу ключа
            try:
                msg_text = (
                    f"🌐 <b>Добавлен новый сервер!</b>\n\n"
                    f"Локация: <b>{server.name}</b>\n"
                    f"IP: <code>{server.ip}</code>\n\n"
                    f"Ваш ключ <code>{k.vpn_login}</code> активен на этом сервере. Данные в разделе <b>👤 Мой профиль</b>."
                )
                await bot.send_message(k.user_id, msg_text, parse_mode="HTML")
            except:
                pass

            await asyncio.sleep(0.3)

        ssh.close()
    except Exception as e:
        logging.error(f"Ошибка синхронизации: {e}")

    return count


@dp.callback_query(F.data == "list_servers")
async def list_servers(callback: types.CallbackQuery):
    async with async_session() as session:
        res = await session.execute(select(VPNServer)); ss = res.scalars().all()
    text = "Список серверов:\n" + "\n".join([f"ID:{s.id} | {s.name} | {s.ip}" for s in ss])
    await callback.message.answer(text if ss else "Пусто.")

# --- Админка: Дилеры ---
@dp.callback_query(F.data == "manage_dealers")
async def m_dealers(callback: types.CallbackQuery):
    await callback.message.edit_text("Дилеры:", reply_markup=kb.dealer_manage_menu())

@dp.callback_query(F.data == "add_dealer")
async def add_dealer_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите ID пользователя:")
    await state.set_state(AdminStates.wait_add_dealer)

@dp.message(AdminStates.wait_add_dealer)
async def save_dealer(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text)
        async with async_session() as session:
            u = await session.get(User, uid)
            if u: u.role = "dealer"; await session.commit(); await message.answer("✅ Готово.")
    except: pass
    await state.clear()

@dp.callback_query(F.data == "remove_dealer")
async def rem_dealer_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите ID:")
    await state.set_state(AdminStates.wait_remove_dealer)

@dp.message(AdminStates.wait_remove_dealer)
async def rem_dealer_fin(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text)
        async with async_session() as session:
            u = await session.get(User, uid)
            if u: u.role = "user"; await session.commit(); await message.answer("❌ Снят.")
    except: pass
    await state.clear()

# --- Дилерская панель ---
@dp.message(F.text.contains("Панель Дилера"))
async def d_panel(message: types.Message):
    u = await get_user(message.from_user.id)
    if u.role not in ["admin", "dealer"]: 
        return
    # Мы вызываем функцию kb.dealer_panel_kb(), которую создали выше
    await message.answer(f"💼 Баланс: {u.balance} кред.", reply_markup=kb.dealer_panel_kb())


@dp.callback_query(F.data == "dealer_pay")
async def d_pay_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите ID клиента:")
    await state.set_state(DealerStates.wait_user_id)

@dp.message(DealerStates.wait_user_id)
async def d_id_rec(message: types.Message, state: FSMContext):
    await state.update_data(tid=int(message.text))
    await message.answer("Сумма:")
    await state.set_state(DealerStates.wait_amount)

@dp.message(DealerStates.wait_amount)
async def d_amount_rec(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text)
        data = await state.get_data()
        async with async_session() as session:
            dlr = await session.get(User, message.from_user.id)
            trg = await session.get(User, data['tid'])
            if trg and (dlr.role == "admin" or dlr.balance >= amt):
                if dlr.role != "admin": dlr.balance -= amt
                trg.balance += amt; await session.commit()
                await message.answer("✅ Готово!"); await bot.send_message(data['tid'], f"💰 Пополнение: {amt}")
    except: pass
    await state.clear()

# --- Покупка ---
@dp.callback_query(F.data.startswith("buy_"))
async def buy_vpn(callback: types.CallbackQuery):
    await callback.answer("⏳ Генерирую новый ключ...")

    tariff = callback.data.split("_")[1]
    price = config.PRICES[tariff]
    user_id = callback.from_user.id

    async with async_session() as session:
        user = await session.get(User, user_id)
        if user.balance < price:
            await callback.message.answer("❌ Недостаточно кредитов на балансе!")
            return

        # ПРОВЕРКА ДЛЯ РЕФЕРАЛЬНОЙ СИСТЕМЫ (до совершения покупки)
        # Считаем, есть ли у пользователя уже купленные ключи
        res_keys = await session.execute(select(VPNKey).where(VPNKey.user_id == user_id))
        is_first_purchase = len(res_keys.scalars().all()) == 0

        # Генерируем данные ключа
        new_login = f"u{user_id}_{random.randint(100, 999)}"
        new_password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
        expiry = datetime.now() + timedelta(days=30)

        new_key = VPNKey(
            user_id=user_id,
            vpn_login=new_login,
            vpn_password=new_password,
            tariff=tariff,
            expiry_date=expiry
        )

        user.balance -= price
        session.add(new_key)

        # НАЧИСЛЕНИЕ БОНУСА ПРИГЛАСИВШЕМУ
        if is_first_purchase and user.referred_by:
            referrer = await session.get(User, user.referred_by)
            if referrer:
                referrer.balance += 2.0
                try:
                    await bot.send_message(
                        referrer.user_id,
                        f"💰 <b>Бонус за реферала!</b>\nПриглашенный вами пользователь совершил первую покупку. Вам начислено 20 кредитов!",
                        parse_mode="HTML"
                    )
                except:
                    pass

        await session.commit()
        await session.refresh(new_key)

        # Устанавливаем ключ на серверы
        res_s = await session.execute(select(VPNServer).where(VPNServer.tariff_type == tariff))
        servers = res_s.scalars().all()

        done_count = 0
        for s in servers:
            if await create_single_user_on_server(s, new_key):
                done_count += 1

    await callback.message.answer(
        f"✅ <b>Новый ключ создан!</b>\n\n"
        f"👤 Логин: <code>{new_login}</code>\n"
        f"🔑 Пароль: <code>{new_password}</code>\n"
        f"📅 Срок: 30 дней\n"
        f"🌐 Активен на серверах: {done_count}\n\n"
        f"Все ваши ключи доступны в разделе <b>👤 Мой профиль</b>.",
        parse_mode="HTML"
    )



async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
	# Запускаем проверку в фоновом режиме
    asyncio.create_task(check_expired_subscriptions())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
