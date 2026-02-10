# 2 задание

```
1. Конфигурация
   ├── BOT_TOKEN и CHAT_ID из .env файла или переменных окружения
   └── Валидация: проверить, что оба значения заданы

2. Чтение файла
   ├── Принять путь к .txt через CLI-аргумент
   └── Прочитать содержимое с обработкой ошибок

3. Подготовка сообщения
   ├── Telegram лимит — 4096 символов
   └── Если текст длиннее — разбить на части (chunks)

4. Отправка через Telegram Bot API
   ├── POST на https://api.telegram.org/bot<TOKEN>/sendMessage
   ├── Поддержка parse_mode (plain / Markdown / HTML)
   └── Обработка ответов и ошибок API

5. CLI-интерфейс
   └── argparse: путь к файлу, опционально parse_mode, --preview
```

# Инструкция
## 1. Создание бота и получение CHAT_ID
### Шаг 1 — Создать бота

1. Открой Telegram → найди ```@BotFather```
2. Отправь:  ```/newbot```
3. Введи имя и username бота
4. Скопируй токен:  123456789:ABCdefGHI-jklMNOpqrSTUvwxYZ

### Шаг 2 — Узнать свой Chat ID

1. Найди бота ```@userinfobot``` (или @getmyid_bot)
2. Напиши ему /start
3. Он ответит твой ID: ```123456789```
   
### Шаг 3 — Активировать бота

Найди СВОЕГО бота в Telegram → нажми /start
(без этого бот не сможет тебе писать!)

## 2. Настройка
Отредактируй файл .env рядом со скриптом:
```
TG_BOT_TOKEN=123456789:ABCdefGHI-jklMNOpqrSTUvwxYZ
TG_CHAT_ID=987654321
```

## 3. Установка зависимостей

```pip install requests python-dotenv```

##4. Запуск

### Проверить подключение бота

```python tg_sender.py --check message.txt```

### Превью (без отправки)

```python tg_sender.py message.txt --preview```

### Отправить plain text

```python tg_sender.py message.txt```

### Отправить с HTML-форматированием

```python tg_sender.py message.txt --parse-mode HTML```
