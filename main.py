import asyncio
import os
from datetime import datetime, timedelta

import discord
from discord import ui, Interaction, app_commands
from discord.ext import commands, tasks


TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("Не указан токен бота. Проверь DISCORD_TOKEN.")

CHANNEL_ID = 1543007392315474080

ALLOWED_STRELS_CHANNEL_IDS = [
    1537428377428955218,
]

ADMIN_IDS = [
    1416863224430596107,
]

NOTIFICATION_BEFORE_MINUTES = 20
SCHEDULER_CHECK_SECONDS = 3


intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

active_raids = {}
last_raid_message_id = None

scheduled_raids = {}
next_schedule_id = 1

publishing_raids = set()
retry_after = {}


SERVER_CHOICES = [
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
        raise ValueError("Формат: 2x2, 3x3, 4x4 или 5x5.")

    return formats[cleaned]


def get_start_datetime(time_text: str) -> datetime:
    hours, minutes = map(int, time_text.split(":"))

    now = datetime.now()

    start_time = now.replace(
        hour=hours,
        minute=minutes,
        second=0,
        microsecond=0
    )

    if start_time <= now:
        start_time += timedelta(days=1)

    return start_time


def get_sorted_raids():
    return sorted(
        scheduled_raids.items(),
        key=lambda item: (
            item[1]["publish_time"],
            item[1]["start_time"],
            item[0]
        )
    )


def get_raid_by_display_number(number: int):
    raids = get_sorted_raids()

    if number < 1 or number > len(raids):
        return None, None

    return raids[number - 1]


def get_display_number_by_internal_id(internal_id: int):
    for number, (raid_id, _) in enumerate(
        get_sorted_raids(),
        start=1
    ):
        if raid_id == internal_id:
            return number

    return None


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
        if self.message is not None:
            await self.message.edit(
                embed=build_embed(self.data),
                view=self
            )

    async def try_pull_from_reserve(self):
        if len(self.data["main"]) >= self.data["slots_total"]:
            return

        if not self.data["reserve"]:
            return

        user_id = self.data["reserve"].pop(0)
        self.data["main"].append(user_id)

        await self.update_message()

        if self.message is not None:
            await self.message.channel.send(
                f"<@{user_id}> автоматически переведён "
                "в основной состав."
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

            if len(self.data["main"]) >= self.data["slots_total"]:
                await interaction.response.send_message(
                    "Основные слоты заполнены. "
                    "Можешь записаться в резерв.",
                    ephemeral=True
                )
                return

            if user_id in self.data["reserve"]:
                self.data["reserve"].remove(user_id)

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
            if user_id in self.data["main"]:
                await interaction.response.send_message(
                    "Ты уже записан в основные слоты.",
                    ephemeral=True
                )
                return

            if user_id in self.data["reserve"]:
                await interaction.response.send_message(
                    "Ты уже записан в резерв.",
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

    if schedule_id in publishing_raids:
        return

    raid = scheduled_raids.get(schedule_id)

    if raid is None:
        return

    publishing_raids.add(schedule_id)

    try:
        data = raid["data"]
        channel = await get_target_channel()

        if channel is None:
            retry_after[schedule_id] = (
                datetime.now() + timedelta(minutes=1)
            )
            return

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

        scheduled_raids.pop(schedule_id, None)
        retry_after.pop(schedule_id, None)

        print(
            f"Стрела опубликована: "
            f"{data['server']} | {data['format']} | {data['time']}"
        )

    except Exception as error:
        retry_time = datetime.now() + timedelta(minutes=1)

        retry_after[schedule_id] = retry_time

        print(
            f"Ошибка отправки стрелы: {error}. "
            f"Повтор в {retry_time:%H:%M:%S}"
        )

    finally:
        publishing_raids.discard(schedule_id)


@tasks.loop(seconds=SCHEDULER_CHECK_SECONDS)
async def raid_scheduler():
    now = datetime.now()

    for schedule_id, raid in get_sorted_raids():
        if raid["publish_time"] > now:
            continue

        if schedule_id in publishing_raids:
            continue

        next_retry_time = retry_after.get(schedule_id)

        if next_retry_time is not None and now < next_retry_time:
            continue

        asyncio.create_task(
            publish_raid(schedule_id)
        )


@raid_scheduler.before_loop
async def before_raid_scheduler():
    await bot.wait_until_ready()


@bot.tree.command(
    name="slot",
    description="Запланировать новую стрелу"
)
@app_commands.choices(
    server=SERVER_CHOICES,
    format=FORMAT_CHOICES,
    time_str=TIME_CHOICES
)
async def slot(
    interaction: Interaction,
    server: app_commands.Choice[str],
    format: app_commands.Choice[str],
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

    slots_total = parse_format(format.value)
    start_time = get_start_datetime(time_str.value)

    publish_time = start_time - timedelta(
        minutes=NOTIFICATION_BEFORE_MINUTES
    )

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
        "publish_time": publish_time
    }

    display_number = get_display_number_by_internal_id(
        schedule_id
    )

    await interaction.response.send_message(
        f"✅ Стрела #{display_number} запланирована.\n\n"
        f"Сервер: **{server.value}**\n"
        f"Формат: **{format.value}**\n"
        f"Начало: **{start_time:%d.%m.%Y %H:%M}**\n"
        f"Уведомление и слоты: **{publish_time:%d.%m.%Y %H:%M}**\n"
        f"Канал отправки: <#{CHANNEL_ID}>\n\n"
        f"Посмотреть расписание: `/bizwar`\n"
        f"Отменить стрелу: `/dstrel номер:{display_number}`",
        ephemeral=True
    )


@bot.tree.command(
    name="bizwar",
    description="Показать все запланированные стрелы"
)
async def bizwar(interaction: Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Команду /bizwar нельзя использовать в личных сообщениях.",
            ephemeral=True
        )
        return

    if interaction.channel_id not in ALLOWED_STRELS_CHANNEL_IDS:
        await interaction.response.send_message(
            "Команду /bizwar можно использовать только "
            "в разрешённых каналах.",
            ephemeral=True
        )
        return

    if not scheduled_raids:
        await interaction.response.send_message(
            "📋 Сейчас нет запланированных стрел."
        )
        return

    lines = []

    for number, (_, raid) in enumerate(
        get_sorted_raids(),
        start=1
    ):
        data = raid["data"]

        lines.append(
            f"**#{number}** — "
            f"**{data['server']}** | "
            f"**{data['format']}** | "
            f"начало: **{raid['start_time']:%d.%m %H:%M}** | "
            f"слоты откроются: **{raid['publish_time']:%d.%m %H:%M}**"
        )

    await interaction.response.send_message(
        "📋 **Запланированные стрелы (по времени):**\n\n"
        + "\n".join(lines)
    )


@bot.tree.command(
    name="dstrel",
    description="Отменить запланированную стрелу"
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

    internal_id, raid = get_raid_by_display_number(number)

    if raid is None:
        await interaction.response.send_message(
            f"Стрела #{number} не найдена.",
            ephemeral=True
        )
        return

    data = raid["data"]

    scheduled_raids.pop(internal_id, None)
    retry_after.pop(internal_id, None)
    publishing_raids.discard(internal_id)

    await interaction.response.send_message(
        f"❌ Стрела #{number} отменена.\n"
        f"Сервер: **{data['server']}**\n"
        f"Формат: **{data['format']}**\n"
        f"Время: **{data['time']}**",
        ephemeral=True
    )


@bot.tree.command(
    name="dslot",
    description="Удалить игрока из последней активной стрелы"
)
async def dslot(
    interaction: Interaction,
    user: discord.Member
):
    global last_raid_message_id

    if interaction.guild is None or not is_admin(interaction.user):
        await interaction.response.send_message(
            "Команда доступна администратору на сервере.",
            ephemeral=True
        )
        return

    raid = active_raids.get(last_raid_message_id)

    if raid is None:
        await interaction.response.send_message(
            "Сейчас нет активной опубликованной стрелы.",
            ephemeral=True
        )
        return

    data = raid["data"]
    view = raid["view"]

    async with view.lock:
        main = user.id in data["main"]
        reserve = user.id in data["reserve"]

        if not main and not reserve:
            await interaction.response.send_message(
                "Этот игрок не записан в слоты.",
                ephemeral=True
            )
            return

        if main:
            data["main"].remove(user.id)

        if reserve:
            data["reserve"].remove(user.id)

        await interaction.response.defer(ephemeral=True)
        await view.update_message()

        if main:
            await view.try_pull_from_reserve()

    await interaction.followup.send(
        f"{user.mention} удалён из слотов.",
        ephemeral=True
    )


@bot.tree.command(
    name="addslot",
    description="Добавить игрока в основные слоты"
)
async def addslot(
    interaction: Interaction,
    user: discord.Member
):
    global last_raid_message_id

    if interaction.guild is None or not is_admin(interaction.user):
        await interaction.response.send_message(
            "Команда доступна администратору на сервере.",
            ephemeral=True
        )
        return

    raid = active_raids.get(last_raid_message_id)

    if raid is None:
        await interaction.response.send_message(
            "Сейчас нет активной опубликованной стрелы.",
            ephemeral=True
        )
        return

    data = raid["data"]
    view = raid["view"]

    async with view.lock:
        if user.id in data["main"]:
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

        if user.id in data["reserve"]:
            data["reserve"].remove(user.id)

        data["main"].append(user.id)

        await interaction.response.defer(ephemeral=True)
        await view.update_message()

    await interaction.followup.send(
        f"{user.mention} добавлен в основные слоты.",
        ephemeral=True
    )


@bot.event
async def on_ready():
    print(f"Бот готов: {bot.user}")

    if not raid_scheduler.is_running():
        raid_scheduler.start()

    await bot.change_presence(
        activity=discord.Game(
            "Пикни слот на стрелу, чудище!"
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
