from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def main_menu(role="user"):
    buttons = [
        [KeyboardButton(text="🚀 Купить VPN"), KeyboardButton(text="👤 Мой профиль")],
        [KeyboardButton(text="📖 Инструкция"), KeyboardButton(text="🆘 Поддержка")],
        [KeyboardButton(text="👥 Рефералы")]
    ]
    if role == "admin":
        buttons.append([KeyboardButton(text="⚙️ Админка")])
    elif role == "dealer":
        buttons.append([KeyboardButton(text="💼 Панель Дилера")])
    
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🖥 Серверы", callback_data="manage_servers")],
        [InlineKeyboardButton(text="🤝 Дилеры", callback_data="manage_dealers")]
    ])

def download_links_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 iOS (iPhone/iPad)", url="https://apps.apple.com/us/app/openvpn-connect/id590379981")],
        [InlineKeyboardButton(text="🤖 Android", url="https://play.google.com/store/apps/details?id=net.openvpn.openvpn")],
        [InlineKeyboardButton(text="💻 Windows", url="https://openvpn.net/client-connect-vpn-for-windows/")]
    ])


def server_manage_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data="add_server")],
        [InlineKeyboardButton(text="❌ Удалить по ID", callback_data="del_server_start")],
        [InlineKeyboardButton(text="📋 Список", callback_data="list_servers")]
    ])

def dealer_manage_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Назначить", callback_data="add_dealer")],
        [InlineKeyboardButton(text="❌ Снять", callback_data="remove_dealer")],
        [InlineKeyboardButton(text="📋 Список дилеров", callback_data="list_dealers")],
        [InlineKeyboardButton(text="💰 Пополнить дилера", callback_data="admin_pay_dealer")]
    ])


def get_tariff_keyboard(prices):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"Standard ({prices['standard']} к.)", callback_data="buy_standard"))
    builder.row(InlineKeyboardButton(text=f"VIP ({prices['vip']} к.)", callback_data="buy_vip"))
    return builder.as_markup()

def dealer_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пополнить баланс клиента", callback_data="dealer_pay")]
    ])
