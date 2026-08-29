import asyncio
from datetime import datetime, timedelta

import discord
from discord import ui, Interaction, app_commands
from discord.ext import commands


# ================= НАСТРОЙКИ =================

import os

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("Не указан токен бота. Проверь переменную окружения DISCORD_TOKEN.")

# Канал, куда бот отправляет уведомления и карточки стрел
CHANNEL_ID = 1543007392315474080

# Каналы, где любой пользователь сервера может использовать /strels.
# Добавь сюда ID других разрешённых каналов.
ALLOWED_STRELS_CHANNEL_IDS = [
    1537428377428955218,
]

# Администраторы могут использовать:
# /slot, /dstrel, /dslot, /addslot
ADMIN_IDS = [
    1416863224430596107,
    1466099103288135836,
    1076786393658970214,
    541552528677011457,
    1326336317071818804,
]

# За сколько минут до начала стрелы публиковать @everyone и карточку
NOTIFICATION_BEFORE_MINUTES = 20

# ===============================================


intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

# Активные, уже опубликованные стрелы:
# {message_id: {"data": data, "view": view, "message": message}}
active_raids = {}

# Последняя опубликованная стрела
last_raid_message_id = None

# Запланированные стрелы:
# {номер: {"data": ..., "start_time": ..., "publish_time": ..., "task": ...}}
scheduled_raids = {}

# Номер следующей запланированной стрелы
next_schedule_id = 1


# ================= ВАРИАНТЫ ВЫБОРА =================


SERVER_CHOICES = [
    app_commands.Choice(name="04 Chandler", value="04 Chandler"),
    app_commands.Choice(name="12 Glendale", value="12 Glendale"),
    app_commands.Choice(name="15 Payson", value="15 Payson"),
    app_commands.Choice(name="16 Gilbert", value="16 Gilbert"),
    app_commands.Choice(name="23 Holiday", value="23 Holiday"),
]

FORMAT_CHOICES = [
    app_commands.Choice(name="2x2", value="2x2"),
    app_commands.Choice(name="3x3", value="3x3"),
    app_commands.Choice(name="4x4", value="4x4"),
    app_commands.Choice(name="5x5", value="5x5"),
]

# НОВОЕ: выбор дня для стрелы
DAY_CHOICES = [
    app_commands.Choice(name="Сегодня", value="today"),
    app_commands.Choice(name="Завтра", value="tomorrow"),
]

TIME_CHOICES = [
    app_commands.Choice(name="16:00", value="16:00"),
    app_commands.Choice(name="16:20", value="16:20"),
    app_commands.Choice(name="16:40", value="16:40"),
    app_commands.Choice(name="17:00", value="17:00"),
    app_commands.Choice(name="17:20", value="17:20"),
    app_commands.Choice(name="17:40", value="17:40"),
    app_commands.Choice(name="18:00", value="18:00"),
    app_commands.Choice(name="18:20", value="18:20"),
    app_commands.Choice(name="18:40", value="18:40"),
    app_commands.Choice(name="19:00", value="19:00"),
    app_commands.Choice(name="19:20", value="19:20"),
    app_commands.Choice(name="19:40", value="19:40"),
    app_commands.Choice(name="20:00", value="20:00"),
    app_commands.Choice(name="20:20", value="20:20"),
    app_commands.Choice(name="20:40", value="20:40"),
    app_commands.Choice(name="21:00", value="21:00"),
    app_commands.Choice(name="21:20", value="21:20"),
    app_commands.Choice(name="21:40", value="21:40"),
]


# ===================================================


def is_admin(user: discord.abc.User) -> bool:
    return user.id in ADMIN_IDS


def parse_format(format_text: str) -> int:
    formats = {
        "2x2": 2,
        "3x3": 3,
        "4x4": 4,
        "5x5": 5,
    }

    cleaned = format_text.lower().replace(" ", "").replace("х", "x")

    if cleaned not in formats:
        raise ValueError(
            "Формат должен быть: 2x2, 3x3, 4x4 или 5x5."
        )

    return formats[cleaned]


# ИЗМЕНЕНО: теперь явно выбираются сегодня или завтра
def get_start_datetime(time_text: str, day: str) -> datetime:
    """
    Создаёт дату и время стрелы на сегодня или завтра.

    Если выбран сегодняшний день, нельзя назначить
    стрелу на уже прошедшее время.
    """
    hours_text, minutes_text = time_text.split(":")

    hours = int(hours_text)
    minutes = int(minutes_text)

    now = datetime.now()

    if day == "tomorrow":
        target_date = now + timedelta(days=1)
    else:
        target_date = now

    start_datetime = target_date.replace(
        hour=hours,
        minute=minutes,
        second=0,
        microsecond=0
    )

    if day == "today" and start_datetime <= now:
        raise ValueError(
            "Нельзя создать стрелу на сегодня в уже прошедшее время. "
            "Выбери «Завтра»."
        )

    return start_datetime


def build_embed(data: dict) -> discord.Embed:
    main_list = "\n".join(
        f"{number}. <@{user_id}>"
        for number, user_id in enumerate(data["main"], start=1)
    ) or "—"

    reserve_list = "\n".join(
        f"{number}. <@{user_id}>"
        for number, user_id in enumerate(data["reserve"], start=1)
    ) or "—"

    embed = discord.Embed(
        title=f"🏹 Стрела | {data['server']}",
        color=discord.Color.green()
    )

    embed.add_field(
        name="Формат",
        value=data["format"],
        inline=True
    )

    embed.add_field(
        name="Время начала",
        value=data["time"],
        inline=True
    )

    embed.add_field(
        name="Создал",
        value=f"<@{data['creator_id']}>",
        inline=True
    )

    embed.add_field(
        name=f"Основные слоты ({len(data['main'])}/{data['slots_total']})",
        value=main_list,
        inline=False
    )

    embed.add_field(
        name=f"Резерв ({len(data['reserve'])}/3)",
        value=reserve_list,
        inline=False
    )

    embed.set_footer(
        text="Нажми кнопку, чтобы записаться или покинуть слот."
    )

    return embed


class RaidView(ui.View):
    def __init__(self, data: dict):
        super().__init__(timeout=None)

        self.data = data
        self.message = None
        self.lock = asyncio.Lock()

    async def update_message(self):
        if self.message is None:
            return

        await self.message.edit(
            embed=build_embed(self.data),
            view=self
        )

    async def try_pull_from_reserve(self):
        """
        Если в основе есть место и в резерве есть игроки,
        первый из резерва автоматически переводится в основу.
        """
        if len(self.data["main"]) >= self.data["slots_total"]:
            return

        if not self.data["reserve"]:
            return

        user_id = self.data["reserve"].pop(0)
        self.data["main"].append(user_id)

        await self.update_message()

        if self.message is not None:
            await self.message.channel.send(
                f"<@{user_id}> автоматически переведён в основной состав."
            )

    @ui.button(
        label="Взять основной слот",
        style=discord.ButtonStyle.green,
        custom_id="main_slot"
    )
    async def main_button(
        self,
        interaction: Interaction,
        button: ui.Button
    ):
        user_id = interaction.user.id

        async with self.lock:
            if user_id in self.data["main"]:
                await interaction.response.send_message(
                    "Ты уже записан в основные слоты.",
                    ephemeral=True
                )
                return

            if user_id in self.data["reserve"]:
                if len(self.data["main"]) >= self.data["slots_total"]:
                    await interaction.response.send_message(
                        "Основные слоты заполнены. "
                        "Оставайся в резерве, пока кто‑то не освободит место.",
                        ephemeral=True
                    )
                    return

                self.data["reserve"].remove(user_id)
                self.data["main"].append(user_id)

                await interaction.response.defer()
                await self.update_message()
                return

            if len(self.data["main"]) >= self.data["slots_total"]:
                await interaction.response.send_message(
                    "Основные слоты заполнены. "
                    "Можешь записаться в резерв.",
                    ephemeral=True
                )
                return

            self.data["main"].append(user_id)

            await interaction.response.defer()
            await self.update_message()

    @ui.button(
        label="Взять резерв",
        style=discord.ButtonStyle.secondary,
        custom_id="reserve_slot"
    )
    async def reserve_button(
        self,
        interaction: Interaction,
        button: ui.Button
    ):
        user_id = interaction.user.id

        async with self.lock:
            if user_id in self.data["reserve"]:
                await interaction.response.send_message(
                    "Ты уже записан в резерв.",
                    ephemeral=True
                )
                return

            if user_id in self.data["main"]:
                await interaction.response.send_message(
                    "Ты уже записан в основные слоты.",
                    ephemeral=True
                )
                return

            if len(self.data["reserve"]) >= 3:
                await interaction.response.send_message(
                    "Резерв заполнен. Максимум: 3 игрока.",
                    ephemeral=True
                )
                return

            self.data["reserve"].append(user_id)

            await interaction.response.defer()
            await self.update_message()

    @ui.button(
        label="Покинуть слот",
        style=discord.ButtonStyle.danger,
        custom_id="leave_slot"
    )
    async def leave_button(
        self,
        interaction: Interaction,
        button: ui.Button
    ):
        user_id = interaction.user.id

        async with self.lock:
            removed_from_main = False
            removed_from_reserve = False

            if user_id in self.data["main"]:
                self.data["main"].remove(user_id)
                removed_from_main = True

            if user_id in self.data["reserve"]:
                self.data["reserve"].remove(user_id)
                removed_from_reserve = True

            if not removed_from_main and not removed_from_reserve:
                await interaction.response.send_message(
                    "Ты не записан в эту стрелу.",
                    ephemeral=True
                )
                return

            await interaction.response.defer()
            await self.update_message()

            if removed_from_main:
                await self.try_pull_from_reserve()


async def get_target_channel():
    channel = bot.get_channel(CHANNEL_ID)

    if channel is None:
        try:
            channel = await bot.fetch_channel(CHANNEL_ID)
        except discord.DiscordException:
            return None

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return None

    return channel


async def publish_raid(schedule_id: int):
    global last_raid_message_id

    raid = scheduled_raids.get(schedule_id)

    if raid is None:
        return

    publish_time = raid["publish_time"]
    seconds_to_wait = (publish_time - datetime.now()).total_seconds()

    if seconds_to_wait > 0:
        await asyncio.sleep(seconds_to_wait)

    raid = scheduled_raids.get(schedule_id)

    if raid is None:
        return

    data = raid["data"]
    channel = await get_target_channel()

    if channel is None:
        print(
            f"Не удалось отправить стрелу #{schedule_id}: "
            "канал недоступен или указан неверный CHANNEL_ID."
        )
        scheduled_raids.pop(schedule_id, None)
        return

    try:
        await channel.send(
            f"@everyone 🏹 Через {NOTIFICATION_BEFORE_MINUTES} минут стрела!\n"
            f"Сервер: **{data['server']}**\n"
            f"Формат: **{data['format']}**\n"
            f"Время начала: **{data['time']}**",

            allowed_mentions=discord.AllowedMentions(
                everyone=True,
                users=False,
                roles=False
            )
        )

        view = RaidView(data)

        message = await channel.send(
            embed=build_embed(data),
            view=view
        )

        view.message = message

        active_raids[message.id] = {
            "data": data,
            "view": view,
            "message": message
        }

        last_raid_message_id = message.id

        print(
            f"Стрела #{schedule_id} опубликована: "
            f"{data['server']} | {data['format']} | {data['time']}"
        )

    except discord.Forbidden:
        print(
            "У бота нет прав на отправку сообщений "
            "или упоминание @everyone."
        )

    except discord.HTTPException as error:
        print(f"Ошибка Discord: {error}")

    finally:
        scheduled_raids.pop(schedule_id, None)


# ----------------- /slot -----------------


@bot.tree.command(
    name="slot",
    description="Запланировать новую стрелу"
)
@app_commands.describe(
    server="Выбери сервер",
    format="Выбери формат",
    day="Выбери день проведения стрелы",
    time_str="Выбери время начала стрелы"
)
@app_commands.choices(
    server=SERVER_CHOICES,
    format=FORMAT_CHOICES,
    day=DAY_CHOICES,
    time_str=TIME_CHOICES
)
async def slot(
    interaction: Interaction,
    server: app_commands.Choice[str],
    format: app_commands.Choice[str],
    day: app_commands.Choice[str],
    time_str: app_commands.Choice[str]
):
    global next_schedule_id

    if interaction.guild is not None:
        await interaction.response.send_message(
            "Команду /slot используй в личных сообщениях с ботом.",
            ephemeral=True
        )
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message(
            "У тебя нет прав для создания стрел.",
            ephemeral=True
        )
        return

    try:
        slots_total = parse_format(format.value)

        start_time = get_start_datetime(
            time_text=time_str.value,
            day=day.value
        )

    except ValueError as error:
        await interaction.response.send_message(
            f"Ошибка: {error}",
            ephemeral=True
        )
        return

    publish_time = start_time - timedelta(
        minutes=NOTIFICATION_BEFORE_MINUTES
    )

    # Если стрела на сегодня начинается меньше чем через 20 минут,
    # уведомление отправится сразу.
    if publish_time < datetime.now():
        publish_time = datetime.now()

    schedule_id = next_schedule_id
    next_schedule_id += 1

    data = {
        "server": server.value,
        "format": format.value,
        "slots_total": slots_total,
        "time": time_str.value,
        "creator_id": interaction.user.id,
        "main": [],
        "reserve": []
    }

    scheduled_raids[schedule_id] = {
        "data": data,
        "start_time": start_time,
        "publish_time": publish_time,
        "task": None
    }

    task = asyncio.create_task(
        publish_raid(schedule_id)
    )

    scheduled_raids[schedule_id]["task"] = task

    start_text = start_time.strftime("%d.%m.%Y %H:%M")
    publish_text = publish_time.strftime("%d.%m.%Y %H:%M")

    if day.value == "today":
        day_text = "Сегодня"
    else:
        day_text = "Завтра"

    await interaction.response.send_message(
        f"✅ Стрела #{schedule_id} запланирована.\n\n"
        f"День: **{day_text}**\n"
        f"Сервер: **{server.value}**\n"
        f"Формат: **{format.value}**\n"
        f"Начало: **{start_text}**\n"
        f"Уведомление и слоты: **{publish_text}**\n"
        f"Канал отправки: <#{CHANNEL_ID}>\n\n"
        f"Посмотреть расписание: `/strels`\n"
        f"Отменить стрелу: `/dstrel номер:{schedule_id}`",
        ephemeral=True
    )


# ----------------- /strels -----------------


@bot.tree.command(
    name="strels",
    description="Показать все запланированные стрелы"
)
async def strels(interaction: Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Команду /strels нельзя использовать в личных сообщениях.",
            ephemeral=True
        )
        return

    if interaction.channel_id not in ALLOWED_STRELS_CHANNEL_IDS:
        await interaction.response.send_message(
            "Команду /strels можно использовать только в разрешённых каналах.",
            ephemeral=True
        )
        return

    if not scheduled_raids:
        await interaction.response.send_message(
            "📋 Сейчас нет запланированных стрел.",
            ephemeral=False
        )
        return

    lines = []

    for schedule_id, raid in sorted(scheduled_raids.items()):
        data = raid["data"]
        start_time = raid["start_time"]
        publish_time = raid["publish_time"]

        lines.append(
            f"**#{schedule_id}** — "
            f"**{data['server']}** | "
            f"**{data['format']}** | "
            f"начало: **{start_time:%d.%m %H:%M}** | "
            f"слоты откроются: **{publish_time:%d.%m %H:%M}**"
        )

    await interaction.response.send_message(
        "📋 **Запланированные стрелы:**\n\n" + "\n".join(lines),
        ephemeral=False
    )


# ----------------- /dstrel -----------------


@bot.tree.command(
    name="dstrel",
    description="Отменить запланированную стрелу"
)
@app_commands.describe(
    number="Номер стрелы из команды /strels"
)
async def dstrel(
    interaction: Interaction,
    number: int
):
    if interaction.guild is not None:
        await interaction.response.send_message(
            "Команду /dstrel используй в личных сообщениях с ботом.",
            ephemeral=True
        )
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message(
            "У тебя нет прав для отмены стрел.",
            ephemeral=True
        )
        return

    raid = scheduled_raids.get(number)

    if raid is None:
        await interaction.response.send_message(
            f"Стрела #{number} не найдена или уже опубликована.",
            ephemeral=True
        )
        return

    task = raid["task"]

    if task is not None and not task.done():
        task.cancel()

    data = raid["data"]

    scheduled_raids.pop(number, None)

    await interaction.response.send_message(
        f"❌ Стрела #{number} отменена.\n"
        f"Сервер: **{data['server']}**\n"
        f"Формат: **{data['format']}**\n"
        f"Время: **{data['time']}**",
        ephemeral=True
    )


# ----------------- /dslot -----------------


@bot.tree.command(
    name="dslot",
    description="Удалить игрока из последней активной стрелы"
)
@app_commands.describe(
    user="Игрок, которого нужно удалить"
)
async def dslot(
    interaction: Interaction,
    user: discord.Member
):
    global last_raid_message_id

    if interaction.guild is None:
        await interaction.response.send_message(
            "Команду /dslot нельзя использовать в личных сообщениях.",
            ephemeral=True
        )
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message(
            "У тебя нет прав для использования этой команды.",
            ephemeral=True
        )
        return

    if last_raid_message_id is None:
        await interaction.response.send_message(
            "Сейчас нет активной опубликованной стрелы.",
            ephemeral=True
        )
        return

    raid = active_raids.get(last_raid_message_id)

    if raid is None:
        await interaction.response.send_message(
            "Активная стрела не найдена.",
            ephemeral=True
        )
        return

    data = raid["data"]
    view = raid["view"]
    user_id = user.id

    async with view.lock:
        removed_from_main = False
        removed_from_reserve = False

        if user_id in data["main"]:
            data["main"].remove(user_id)
            removed_from_main = True

        if user_id in data["reserve"]:
            data["reserve"].remove(user_id)
            removed_from_reserve = True

        if not removed_from_main and not removed_from_reserve:
            await interaction.response.send_message(
                "Этот игрок не записан в слоты.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        await view.update_message()

        if removed_from_main:
            await view.try_pull_from_reserve()

    await interaction.followup.send(
        f"{user.mention} удалён из слотов.",
        ephemeral=True
    )


# ----------------- /addslot -----------------


@bot.tree.command(
    name="addslot",
    description="Добавить игрока в основные слоты"
)
@app_commands.describe(
    user="Игрок, которого нужно добавить"
)
async def addslot(
    interaction: Interaction,
    user: discord.Member
):
    global last_raid_message_id

    if interaction.guild is None:
        await interaction.response.send_message(
            "Команду /addslot нельзя использовать в личных сообщениях.",
            ephemeral=True
        )
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message(
            "У тебя нет прав для использования этой команды.",
            ephemeral=True
        )
        return

    if last_raid_message_id is None:
        await interaction.response.send_message(
            "Сейчас нет активной опубликованной стрелы.",
            ephemeral=True
        )
        return

    raid = active_raids.get(last_raid_message_id)

    if raid is None:
        await interaction.response.send_message(
            "Активная стрела не найдена.",
            ephemeral=True
        )
        return

    data = raid["data"]
    view = raid["view"]
    user_id = user.id

    async with view.lock:
        if user_id in data["main"]:
            await interaction.response.send_message(
                "Этот игрок уже находится в основных слотах.",
                ephemeral=True
            )
            return

        if len(data["main"]) >= data["slots_total"]:
            await interaction.response.send_message(
                "Основные слоты заполнены. "
                "Сначала освободи место через /dslot.",
                ephemeral=True
            )
            return

        if user_id in data["reserve"]:
            data["reserve"].remove(user_id)

        data["main"].append(user_id)

        await interaction.response.defer(ephemeral=True)
        await view.update_message()

    await interaction.followup.send(
        f"{user.mention} добавлен в основные слоты.",
        ephemeral=True
    )


# ----------------- События -----------------


@bot.event
async def on_ready():
    print(f"Бот готов: {bot.user}")

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(
            name="Пикни слот на стрелу, чудище!",
            type=discord.ActivityType.playing
        )
    )

    synced = await bot.tree.sync()

    print(f"Синхронизировано slash-команд: {len(synced)}")


@bot.event
async def on_app_command_error(
    interaction: Interaction,
    error: app_commands.AppCommandError
):
    print(f"Ошибка slash-команды: {error}")

    if interaction.response.is_done():
        await interaction.followup.send(
            "При выполнении команды произошла ошибка.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "При выполнении команды произошла ошибка.",
            ephemeral=True
        )


if __name__ == "__main__":
    bot.run(TOKEN)
