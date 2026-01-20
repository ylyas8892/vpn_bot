#!/bin/bash

# --- КОНФИГУРАЦИЯ ---
PROJECT_NAME="vpn_bot"
PROJECT_DIR="/root/$PROJECT_NAME"

echo "🎯 Начинаю развертывание бота..."

# 1. Установка системных пакетов
apt update && apt install -y python3 python3-pip python3-venv sqlite3

# 2. Создание папки проекта (если её нет)
mkdir -p $PROJECT_DIR
cd $PROJECT_DIR

# 3. Настройка виртуального окружения
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ Виртуальное окружение создано."
fi

# 4. Установка зависимостей
source venv/bin/activate
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    echo "✅ Все библиотеки установлены."
else
    echo "❌ Ошибка: requirements.txt не найден!"
    exit 1
fi

# 5. Инициализация базы данных (создание таблиц)
# Мы просто запускаем бота на секунду, чтобы сработал init_db, или доверяем это main.py
echo "🗄 Настройка базы данных..."

# 6. Создание службы Systemd (автозапуск 24/7)
echo "⚙️ Настройка Systemd..."
cat <<EOF > /etc/systemd/system/vpnbot.service
[Unit]
Description=VPN Telegram Bot
After=network.target

[Service]
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/python3 main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 7. Запуск
systemctl daemon-reload
systemctl enable vpnbot
systemctl restart vpnbot

echo "🚀 БОТ ЗАПУЩЕН!"
systemctl status vpnbot