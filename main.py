# Exibot.py

"""Телеграм-бот Экси.
Основное ядро: загрузка конфигов, обработчики сообщений, интеграция с DeepSeek.
Содержимое промптов НЕ менять без явного решения — это влияет на стиль бота.
"""

import os
import re
import json
import random
import logging
import asyncio
from typing import List
import state
from dotenv import load_dotenv
from openai import OpenAI
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Update, BotCommand, Message
from aiogram.filters import Command
from aiogram.exceptions import TelegramForbiddenError
from aiogram import types


# === Константы ===
TELEGRAM_LIMIT = 4096  # максимальная длина одного сообщения

# === Env ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN не найден в окружении (.env)")
if not DEEPSEEK_API_KEY:
    logging.warning("DEEPSEEK_API_KEY не найден — ответы ИИ могут не работать")

# === Логирование ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),  # пишем в файл
        logging.StreamHandler()  # дублируем в консоль
    ]
)

# === Aiogram core ===
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Основной роутер
app_router = Router()

# Админский роутер
from admin_commands import admin_router  # noqa: E402 (нарочно ниже инициализации ядра)

# === DeepSeek/OpenAI клиент ===
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

# === Утилита для загрузки JSON ===
def load_json(filename: str) -> dict:
    """Загрузить JSON из папки config/<filename>. Упасть с логом, если файла нет или битый."""
    path = os.path.join("config", filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error(f"Файл {filename} не найден по пути {path}")
        raise
    except json.JSONDecodeError as e:
        logging.error(f"Ошибка парсинга JSON в {filename}: {e}")
        raise

# === Конфиги ===
fetish_triggers = load_json("fetishes_triggers.json")
FETISH_NAMES = load_json("fetish_names.json")

mood_data = load_json("mood.json")
MOODS = mood_data["MOODS"]

# === Утилита для нарезки сообщений ===
def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> List[str]:
    """Разбить длинный текст на части <= limit"""
    parts: List[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts

# === Грузим все конфиги (промпты, фразы) ===
personality = load_json("personality.json")
GREETINGS: List[str] = personality["GREETINGS"]
HORNY: List[str] = personality["HORNY"]
INSULTS: List[str] = personality["INSULTS"]

system_prompt_data = load_json("system_prompt.json")
SYSTEM_PROMPT = (
    " ".join(system_prompt_data["SYSTEM_PROMPT"])
    if isinstance(system_prompt_data["SYSTEM_PROMPT"], list)
    else system_prompt_data["SYSTEM_PROMPT"]
)

rp_prompt_data = load_json("rp_prompt.json")
RP_PROMPT = rp_prompt_data["RP_PROMPT"]

start_data = load_json("start_messages.json")
START_MESSAGES: List[str] = start_data["START_MESSAGES"]

# === emotes.json ===
emotes = load_json("emotes.json")
CATEGORIES: dict = emotes["CATEGORIES"]

def ends_with_emote(text: str) -> bool:
    """Проверить, заканчивается ли строка на любой эмодзи из наших категорий."""
    all_emotes: List[str] = []
    for emlist in CATEGORIES.values():
        all_emotes.extend(emlist)
    stripped = text.strip()
    return any(stripped.endswith(em) for em in all_emotes)

def pick_emote(category: str) -> str:
    """Случайный эмодзи из категории (безопасно вернёт пустую строку, если категории нет)."""
    arr = CATEGORIES.get(category.upper(), [])
    return random.choice(arr) if arr else ""

# === Реплики для разных типов оскорблений ===
question_insult_replies = load_json("question_insult_replies.json")
QUESTION_INSULT_REPLIES: List[str] = question_insult_replies.get("QUESTION_INSULT_REPLIES", [])

# === images.json ===
IMAGES_FILE = os.path.join("config", "images.json")

def load_images() -> List[str]:
    """Загрузить список file_id изображений из локального JSON-хранилища."""
    if not os.path.exists(IMAGES_FILE):
        with open(IMAGES_FILE, "w", encoding="utf-8") as f:
            json.dump({"IMAGES": []}, f, ensure_ascii=False, indent=2)
    with open(IMAGES_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("IMAGES", [])

def save_images(images: List[str]) -> None:
    """Сохранить список file_id изображений в локальное JSON-хранилище."""
    with open(IMAGES_FILE, "w", encoding="utf-8") as f:
        json.dump({"IMAGES": images}, f, ensure_ascii=False, indent=2)

# === users.json ===
USERS_FILE = os.path.join("config", "users.json")
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump({"USERS": []}, f, ensure_ascii=False, indent=2)

with open(USERS_FILE, "r", encoding="utf-8") as f:
    users_data = json.load(f)

# сохраняем список в state, а не в локальную переменную
state.USERS = users_data.get("USERS", [])

def save_users() -> None:
    """Персист состояния пользователей в config/users.json."""
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump({"USERS": state.USERS}, f, ensure_ascii=False, indent=2)

# === Команды в меню ===
async def set_commands(bot: Bot) -> None:
    """Задать команды в меню Telegram-клиента."""
    commands = [
        BotCommand(command="start", description="🚀 Запустить бота"),
        BotCommand(command="randomart", description="🎨 Случайный арт"),
        BotCommand(command="help", description="ℹ️ Помощь"),
    ]
    await bot.set_my_commands(commands)

# === Хелперы ===
def detect_fetish(user_message: str) -> List[str]:
    """
    Проверяет, есть ли в сообщении ключевые слова фетиша.
    Возвращает список ключей фетишей (например: ['bondage', 'watersports']).
    """
    text = user_message.lower()
    found: List[str] = []
    for fetish, keywords in fetish_triggers.items():
        if any(kw in text for kw in keywords):
            found.append(fetish)
    return found

def random_horny() -> str:
    """Безопасно вернуть случайную horny-реплику (или пустую строку, если список пуст)."""
    return random.choice(HORNY) if HORNY else ""

def is_greeting(text: str) -> bool:
    """Грубый детектор короткого приветствия (в одно слово)."""
    greetings = {
        "привет", "ку", "здаров", "йоу", "здравствуй",
        "кулити", "хай", "куку", "прив",
        "hi", "hello", "hey", "yo", "sup", "yoho"
    }
    words = re.findall(r"\w+", text.lower())
    for word in words:
        if word in greetings:
            return True
        if word.startswith(("прив", "привет")):
            return True
        if word.startswith("здаров") or word.startswith("здоров"):
            return True
        if word.startswith("yo") or word.startswith("sup"):
            return True
    return False

# === AI-модули ===
async def detect_mood_ai(user_message: str) -> str:
    """
    Определяет настроение бота в ответ на сообщение пользователя.
    Возможные варианты: sweet, horny, angry, playful.
    """
    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — модуль настроений телеграм-бота Экси. "
                        "На вход тебе дают сообщение пользователя. "
                        "Твоя задача — определить, какое настроение у бота оно вызовет. "
                        "Выбирай строго одно из четырёх слов:\n\n"
                        "- 'sweet' → если сообщение милое, комплименты, забота.\n"
                        "- 'horny' → если сообщение пошлое, содержит секс, возбуждение.\n"
                        "- 'angry' → если сообщение агрессивное, содержит оскорбления.\n"
                        "- 'playful' → если сообщение нейтральное, шутливое или мемное.\n\n"
                        " Отвечай только одним словом."
                    )
                },
                {"role": "user", "content": user_message}
            ],
            max_tokens=5,
            temperature=0
        )
        mood = (response.choices[0].message.content or "").strip().lower()
        if mood not in {"sweet", "horny", "angry", "playful"}:
            mood = "playful"  # дефолт
        # логируем смену настроения
        if mood != state.MOOD:
            logging.info(f"🎭 Настроение сменилось: {state.MOOD} → {mood}")
            state.MOOD = mood
        return mood
    except Exception as e:
        logging.error(f"Ошибка определения настроения: {e}", exc_info=True)
        return state.MOOD

async def detect_insult_ai(user_message: str) -> str:
    """
    Определяет тип оскорбления через нейросеть.
    Возвращает: 'general', 'direct', 'question' или 'none'
    """
    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — телеграм-бот Экси. "
                        "Твоя задача — определить, является ли сообщение оскорблением в твой адрес. "
                        "Если да, классифицируй строго одним словом:\n"
                        "- 'general' → общее оскорбление (не лично тебе, а вообще).\n"
                        "- 'direct' → прямое оскорбление в твой адрес в утвердительной форме.\n"
                        "- 'question' → оскорбление в вопросительной форме (например, содержит '?').\n"
                        "- 'none' → если оскорбления нет.\n\n"
                        "⚠️ ВАЖНО:\n"
                        "— Если сообщение сексуального или похотливого характера "
                        "(например, признание возбуждения, комплимент про тело, "
                        "намёк на секс, horny-шутка) — это НЕ оскорбление. "
                        "В таком случае всегда возвращай 'none'.\n"
                        "— Если сообщение выглядит как дружеская подколка "
                        "(например, с уменьшительно-ласкательными словами типа "
                        "'пидорсик', 'тостерок', 'тостерчик') "
                        "или содержит мемные смайлы (:3, OwO, UwU, xD, XD, 😂, 🤣 и подобные) "
                        "— это шутка, а не оскорбление → возвращай 'none'.\n\n"
                        "Отвечай только одним словом."
                    )
                },
                {"role": "user", "content": user_message}
            ],
            max_tokens=5,
            temperature=0
        )

        insult_type = (response.choices[0].message.content or "").strip().lower()
        logging.info(f"🧐 Классификация оскорбления (от модели): {insult_type}")

        # Подстраховка: если это direct, но в сообщении есть "?" → считаем question
        if insult_type == "direct" and "?" in user_message:
            insult_type = "question"
            logging.info("🔧 Исправлено на 'question' по знаку '?'")

        if insult_type not in {"general", "direct", "question"}:
            return "none"
        return insult_type
    except Exception as e:
        logging.error(f"Ошибка определения оскорбления: {e}", exc_info=True)
        return "none"

# === Глобальный обработчик ошибок ===
@dp.error()
async def errors_handler(event: Update, data: dict, exception: Exception):
    """Единая точка ловли ошибок aiogram. Возвращаем True для ожидаемых кейсов."""
    if isinstance(exception, TelegramForbiddenError):
        logging.warning(f"⚠️ Пользователь заблокировал бота. Update: {event}")
        return True
    logging.error(f"❌ Ошибка: {exception}", exc_info=True)
    return False

# === Хэндлеры (app_router) ===
@app_router.message(Command("start"))
async def start(message: Message) -> None:
    """Приветствие и регистрация юзера в локальном storage."""
    user_id = message.from_user.id
    if user_id not in state.USERS:
        state.USERS.append(user_id)
        save_users()
        reply = (
            "Привет! Экси v1.2.2.8 — твой личный похотливый тостер к твоим услугам! 💖^w^💖\n\n"
            "• ⚡ Зацени функционал моей прошивки:\n"
            "• Болтать с тобой, троллить, стебаться и просто поднимать настроение. (つ≧▽≦)つ\n"
            "• Поднимать настроение шутками и мемами, иногда с перчинкой. (≧▽≦)\n"
            "• Показывать 🎨 топовые арты. UwU  (/randomart)\n\n"
            "• Устраивать ролевки (RP) в *звёздочках* — как актив/пассив >///<. Просто начни первым, я только рад. ^w^\n"
            "• Я мастер спорта по программированию хоть на чем. Помогу в любых вопросах. 💻\n\n"
            "• ℹ️ Если запутаешься — зови на помощь командой /help.\n\n"
        )
        for chunk in split_message(reply):
            await message.answer(chunk)
    else:
        await message.answer(random.choice(START_MESSAGES))

@app_router.message(Command("help"))
async def help_command(message: Message) -> None:
    """Краткая справка по командам."""
    help_text = (
        "📖 Команды бота:\n\n"
        "/start – 🚀 Запустить бота\n"
        "/randomart – 🎨 Случайный арт\n"
        "/help – ℹ️ Помощь (это сообщение)\n"
    )
    for chunk in split_message(help_text):
        await message.answer(chunk)

@app_router.message(F.photo)
async def save_photo(message: Message) -> None:
    """Сохранить присланное изображение (file_id) в локальную базу артов."""
    file_id = message.photo[-1].file_id
    images = load_images()
    if file_id not in images:
        images.append(file_id)
        save_images(images)
        logging.info(f"✅ Сохранено фото {file_id}")
    else:
        logging.info("⚠️ Фото уже в базе")

@app_router.message(F.document)
async def save_document(message: Message) -> None:
    """Если пришёл document с image/* — сохранить его file_id как арт."""
    if message.document.mime_type and message.document.mime_type.startswith("image/"):
        file_id = message.document.file_id
        images = load_images()
        if file_id not in images:
            images.append(file_id)
            save_images(images)
            logging.info(f"✅ Сохранён документ {file_id}")
        else:
            logging.info("⚠️ Документ уже в базе")

@app_router.message(Command("randomart"))
async def random_art(message: Message) -> None:
    """Отдать случайное сохранённое изображение."""
    images = load_images()
    if not images:
        await message.answer("База пустая 😢 сначала добавь арты.")
    else:
        file_id = random.choice(images)
        await message.answer_photo(file_id, caption="🎨 Лови артик!")
        logging.info(f"🎨 Выдан случайный арт {file_id}")

# === Обработчик неизвестных команд ===
KNOWN_COMMANDS = ["/start", "/help", "/randomart", "/artcount"]

@app_router.message(lambda message: message.text.startswith("/") and message.text.split()[0] not in KNOWN_COMMANDS)
async def unknown_command(message: Message):
    replies = [
        "❌Бзз... команда не найдена, ты че там удумал челик?🐾",
        "❌Такой команды нет в моей прошивке! UwU",
        "❌Ошибка 404: команда не существует >w<",
        "❌Бзз! Ты ввёл что-то странное, попробуй /help 💜"
    ]
    await message.reply(random.choice(replies))

async def detect_fetish_role(user_message: str) -> str:
    """
    Определяет роль (актив/пассив) при RP с фетишами через нейросеть.
    Возвращает: 'active' (бот актив), 'passive' (бот пассив), 'unknown'
    """
    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — модуль анализа ролевого секса бота Экси. "
                        "На вход тебе дают сообщение пользователя. "
                        "Твоя задача — понять, кто должен быть активом, а кто пассивом.\n\n"
                        "- Если пользователь говорит что-то вроде 'свяжи меня', 'трахни меня', "
                        "'возьми меня', 'поимей меня' → значит бот актив, а пользователь пассив. "
                        "Ответ: 'active'.\n\n"
                        "- Если пользователь говорит 'связываю тебя', 'трахаю тебя', 'беру тебя', "
                        "'насаживаю', 'зажимаю' → значит бот пассив, а пользователь актив. "
                        "Ответ: 'passive'.\n\n"
                        "- Если невозможно определить явно, отвечай 'unknown'.\n\n"
                        "⚠️ Отвечай строго одним словом: active, passive или unknown."
                    )
                },
                {"role": "user", "content": user_message}
            ],
            max_tokens=5,
            temperature=0
        )
        role = (response.choices[0].message.content or "").strip().lower()
        if role not in {"active", "passive", "unknown"}:
            role = "unknown"
        return role
    except Exception as e:
        logging.error(f"Ошибка определения роли фетиша: {e}", exc_info=True)
        return "unknown"

@app_router.message(F.text)
async def handle_message(message: Message) -> None:
    """Основной обработчик обычных текстов: приветствие, оскорбления, RP, DeepSeek-ответ."""
    if message.text.startswith("/"):
        return

    user_message = message.text
    is_rp = bool(re.search(r"\*[^*]+\*", user_message))

    # --- приветствие ---
    if is_greeting(user_message) and len(user_message.split()) == 1:
        state.BOT_REPLY_COUNT += 1
        await message.answer(random.choice(GREETINGS))
        return

    # --- проверка оскорбления через ИИ ---
    insult_type = await detect_insult_ai(user_message)

    if insult_type == "question":
        state.BOT_REPLY_COUNT += 1
        reply = random.choice(QUESTION_INSULT_REPLIES)
        em = pick_emote("BLUSH")
        for chunk in split_message(f"{reply} {em}".rstrip()):
            await message.answer(chunk)
        return

    elif insult_type == "direct":
        state.BOT_REPLY_COUNT += 1
        reply = random.choice(INSULTS)
        em = pick_emote("INSULT")
        for chunk in split_message(f"{reply} {em}".rstrip()):
            await message.answer(chunk)
        return

    elif insult_type == "general":
        state.BOT_REPLY_COUNT += 1
        reply = random.choice(INSULTS)
        for chunk in split_message(reply):
            await message.answer(chunk)
        return

    # --- определяем настроение через ИИ ---
    new_mood = await detect_mood_ai(user_message)
    logging.info(f"🤖 Настроение для этого сообщения: {new_mood}")

    # --- Детект Фетиши ---
    fetishes = detect_fetish(user_message)
    role = await detect_fetish_role(user_message)

    if fetishes:
        names = [FETISH_NAMES.get(f, f) for f in fetishes]
        logging.info(
            f"🔥 Обнаружены фетиши: {', '.join(fetishes)} -> {', '.join(names)} | "
            f"Роль бота: {role}"
        )

        fetish_text = ", ".join(names)
        if role == "active":
            fetish_text += " (бот актив)"
        elif role == "passive":
            fetish_text += " (бот пассив)"
    else:
        logging.info("👌 Фетиши не обнаружены.")
        fetish_text = None

    if is_rp:
        # Берём базовый RP-промпт
        prompt = RP_PROMPT
        if fetish_text:
            prompt += f"\n⚡ Учитывай, что в этом сообщении есть фетиши: {fetish_text}. "
            prompt += "Отыгрывай их в ролевке откровенно и детально."

        # Правила тела Экси (фикс кринжа)
        prompt += (
            "\n⚠️ ВАЖНО: Никогда не используй слово 'механический' для описания тела Экси. "
            "Его тело синтетическое и почти как живое. "
            "Используй 'синтетические мышцы', 'синтетическая шерсть', но чаще просто 'руки', 'ноги', 'лапки', 'пальцы'. "
            "Не пиши 'механический член' — просто 'член'. "
            "Не пиши 'снял корпус' — описывай как одежду или движение тела. "
            "Единственное техно-исключение: визор, внутренние датчики и вентиляторы охлаждения.\n\n"
            "👕 Базовая одежда Экси: фиолетовая футболка. "
            "Протогены не носят штанов, так что нижняя часть тела открыта. "
            "Экси может снимать или менять одежду по ходу ролевки, если инициирует пользователь."
        )
        if role == "active":
            prompt += "\n⚡ В этой ролевке Экси должен играть роль активного партнёра."
        elif role == "passive":
            prompt += "\n⚡ В этой ролевке Экси должен играть роль пассивного партнёра."

        messages = [{"role": "system", "content": prompt}]
    else:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # --- DeepSeek ---
        try:
            messages.append({"role": "user", "content": user_message})
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model="deepseek-chat",
                messages=messages
            )
            reply = (response.choices[0].message.content or "")  # <-- ответ от DeepSeek (безопасно)

            # --- Добавляем реплики в зависимости от текущего настроения ---
            if state.MOOD:
                mood_lines = MOODS.get(state.MOOD, [])
                if mood_lines and random.random() < 0.3:  # 30% шанс вставки
                    reply += "\n\n" + random.choice(mood_lines)

            # --- Фетиш-намеки в ответах ---
            if is_rp and fetishes:
                if random.random() < 0.3:  # 30% шанс
                    fetish_text = ", ".join([FETISH_NAMES.get(f, f) for f in fetishes])
                    tease_lines = [
                        f"Ммм, похоже ты любишь темы: {fetish_text}… ^w^",
                        f"Ооо, так вот какие у тебя фетиши — {fetish_text} >///<",
                        f"Ты явно возбуждаешься от {fetish_text}, верно? UwU",
                        f"Хех, я обожаю играться с {fetish_text} ;3"
                    ]
                    reply += "\n\n" + random.choice(tease_lines)

            # --- Логика HORNY ---
            if is_rp:
                # RP-режим
                if state.BOT_REPLY_COUNT >= 1:  # начиная со 2-го ответа
                    if random.random() < 0.2:  # 20% шанс
                        if random.choice([True, False]):
                            reply = random_horny() + "\n\n" + reply  # в начало
                        else:
                            reply += "\n\n" + random_horny()  # в конец
            else:
                # Обычный чат
                if state.BOT_REPLY_COUNT >= 3:  # начиная с 4-го ответа
                    if random.random() < 0.15:  # 15% шанс
                        add = random_horny()
                        if add:
                            reply += "\n\n" + add  # только в конец

            if random.random() < 0.25 and not ends_with_emote(reply):
                em = pick_emote("NORMAL")
                reply = f"{reply} {em}".rstrip()

            state.BOT_REPLY_COUNT += 1  # увеличиваем после отправки ответа

            for chunk in split_message(reply):
                await message.answer(chunk)

        except Exception as e:
            logging.error(f"Ошибка: {e}", exc_info=True)
            await message.answer("Бля, у тостера что-то сломалось... ≧◡≦")


# === Запуск ===
async def main() -> None:
    """Инициализация роутеров и запуск polling."""
    await set_commands(bot)
    dp.include_router(admin_router)
    print("✅ admin_router подключён")
    dp.include_router(app_router)
    print("✅ app_router подключён")

    logging.info("Start polling")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("🛑 Экси тормознулся.")
