#admin_commands

"""Админские команды бота Экси.
Здесь лежат команды управления медиа и статусом: арты, аптайм, статистика, пинг.
"""

import time
import state
import asyncio
import json
import os
from typing import List
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

print("✅ admin_commands.py загружен")

# === Константы и глобальные ===
START_TIME = time.time()
IMAGES_FILE = os.path.join("config", "images.json")

# --- Загружаем список админов ---
with open(os.path.join("config", "owner.json"), "r", encoding="utf-8") as f:
    owner_data = json.load(f)

ADMINS: set[int] = {int(x) for x in owner_data.get("ADMINS", [])}

# --- Router для админских команд ---
admin_router = Router()


# === Утилиты ===
def is_admin(user_id: int) -> bool:
    """Проверка: является ли user_id админом."""
    return user_id in ADMINS


def load_images() -> List[str]:
    """Загрузить список картинок (file_id) из локальной базы."""
    if not os.path.exists(IMAGES_FILE):
        with open(IMAGES_FILE, "w", encoding="utf-8") as f:
            json.dump({"IMAGES": []}, f, ensure_ascii=False, indent=2)
    with open(IMAGES_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("IMAGES", [])


def save_images(images: List[str]) -> None:
    """Сохранить список картинок (file_id) в локальную базу."""
    with open(IMAGES_FILE, "w", encoding="utf-8") as f:
        json.dump({"IMAGES": images}, f, ensure_ascii=False, indent=2)


# === Хэндлеры админских команд ===
@admin_router.message(Command("listimages"))
async def list_images(message: Message) -> None:
    """Показать последние N артов (по умолчанию 1, макс 200)."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У тебя нет доступа к этой команде.")
        return

    images = load_images()
    if not images:
        await message.answer("📂 База артов пуста.")
        return

    parts = message.text.strip().split(maxsplit=1)
    count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    count = max(1, min(count, 200))  # ограничение

    last_images = images[-count:][::-1]
    await message.answer(f"📂 Показываю последние {count} артов:")

    for file_id in last_images:
        try:
            await message.answer_photo(
                file_id, caption=f"<code>{file_id}</code>", parse_mode="HTML"
            )
            await asyncio.sleep(1)  # flood control
        except Exception as e:
            await message.answer(f"⚠️ Ошибка с {file_id}: {e}")


@admin_router.message(Command("removeimage"))
async def remove_image(message: Message) -> None:
    """Удалить арты по ID (через запятую)."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У тебя нет доступа к этой команде.")
        return

    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "⚠️ Укажи ID артов через запятую.\nПример: `/removeimage id1,id2,id3`",
            parse_mode="Markdown",
        )
        return

    ids_to_remove = [x.strip() for x in parts[1].split(",")]
    images = load_images()
    removed, not_found = [], []

    for file_id in ids_to_remove:
        if file_id in images:
            images.remove(file_id)
            removed.append(file_id)
        else:
            not_found.append(file_id)

    save_images(images)

    reply = []
    if removed:
        reply.append(f"✅ Удалено: {len(removed)} артов")
    if not_found:
        reply.append(f"⚠️ Не найдено: {len(not_found)} артов")

    await message.answer("\n".join(reply) if reply else "⚠️ Ничего не удалено.")


@admin_router.message(Command("artcount"))
async def art_count(message: Message) -> None:
    """Показать количество артов в базе."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У тебя нет доступа к этой команде.")
        return

    images = load_images()
    if not images:
        await message.answer("📂 База артов пуста.")
    else:
        await message.answer(f"📂 В базе {len(images)} артов.")


@admin_router.message(Command("status"))
async def status_cmd(message: Message) -> None:
    """Показать статус бота: аптайм, юзеры, ответы, последние логи."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У тебя нет доступа к этой команде.")
        return

    uptime = int(time.time() - state.START_TIME)
    h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60

    reply = (
        "🧠 Статус бота:\n"
        f"• Uptime: {h:02d}:{m:02d}:{s:02d}\n"
        f"• Пользователей: {len(state.USERS)}\n"
        f"• Ответов отправлено: {state.BOT_REPLY_COUNT}"
    )

    log_file = "bot.log"
    if os.path.exists(log_file):
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        last_logs = "".join(lines[-20:])
        reply += f"\n\n📝 Последние логи:\n<pre>{last_logs}</pre>"
    else:
        reply += "\n\n⚠️ Лог-файл не найден."

    await message.answer(reply, parse_mode="HTML")


@admin_router.message(Command("ownhelp"))
async def own_help(message: Message) -> None:
    """Показать список всех админских команд."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У тебя нет доступа к этой команде.")
        return

    help_text = (
        "📖 Админские команды:\n\n"
        "/listimages <N> – 📂 Показать последние N артов (по умолчанию 1)\n"
        "/removeimage <id1,id2,...> – 🗑 Удалить арты по ID\n"
        "/artcount – 🔢 Показать количество артов\n"
        "/status – 📊 Показать статус бота (аптайм, пользователи, ответы, последние логи)\n"
        "/ping – 🏓 Проверка доступности\n"
        "/ownhelp – 👑 Список админских команд (ты тут)\n"
    )
    await message.answer(help_text)


@admin_router.message(Command("ping"))
async def ping(message: Message) -> None:
    """Простейшая проверка доступности роутера."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У тебя нет доступа к этой команде.")
        return
    await message.answer("🏓 Pong от админского роутера!")
