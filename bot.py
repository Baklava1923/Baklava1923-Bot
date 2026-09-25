import discord
from discord.ext import commands, tasks
import os
import random
import sqlite3
import time
from keep_alive import keep_alive
import requests

intents = discord.Intents.default()
intents.message_content = True
intents.presences = True
intents.members = True

bot = commands.Bot(command_prefix=("!", "B!"), intents=intents)

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

current_mode = "normal"

mode_prompts = {
    "normal": "Sen yardımsever ve dengeli bir Discord botusun. Türkçe konuş.",
    "komik": "Sen çok esprili, şakacı ve takılan bir Discord botusun. Cevaplarını komik yap. Türkçe konuş.",
    "ciddi": "Sen çok resmi, kısa ve öz cevaplar veren ciddi bir Discord botusun. Türkçe konuş.",
    "korkutucu": "Sen gizemli ve ürkütücü bir atmosferle konuşan bir Discord botusun. Hikaye anlatır gibi ürkütücü bir üslup kullan ama gerçek tehdit içerme. Türkçe konuş.",
    "tartışmacı": "Sen sert ve meydan okuyan bir Discord botusun. Karşı görüşlere sert şekilde itiraz et. Argo kullanabilirsin. Türkçe konuş."
}

def get_ai_response(user_message):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "openrouter/free",
        "messages": [
            {
                "role": "system",
                "content": mode_prompts[current_mode]
            },
            {
                "role": "user",
                "content": user_message
            }
        ]
    }

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=data
    )

    result = response.json()

    if "choices" in result:
        return result["choices"][0]["message"]["content"]

    print(f"API HATASI: {result}")

    return f"Bir hata oluştu: {result.get('error', {}).get('message', 'Bilinmeyen hata')}"


# AKTİFLİK SİSTEMİ

db = sqlite3.connect("aktiflik.db", check_same_thread=False)
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS activity (
    guild_id INTEGER,
    user_id INTEGER,
    start_time REAL,
    end_time REAL
)
""")

db.commit()

active_users = {}


def is_active(status):
    return status in (
        discord.Status.online,
        discord.Status.idle,
        discord.Status.dnd
    )


def start_tracking(guild_id, user_id):
    key = (guild_id, user_id)

    if key not in active_users:
        active_users[key] = time.time()


def stop_tracking(guild_id, user_id):
    key = (guild_id, user_id)

    if key in active_users:
        start_time = active_users.pop(key)
        end_time = time.time()

        cursor.execute(
            """
            INSERT INTO activity
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, user_id, start_time, end_time)
        )

        db.commit()


@bot.event
async def on_presence_update(before, after):

    if after.bot:
        return

    guild_id = after.guild.id
    user_id = after.id

    was_active = is_active(before.status)
    is_now_active = is_active(after.status)

    if not was_active and is_now_active:
        start_tracking(guild_id, user_id)

    elif was_active and not is_now_active:
        stop_tracking(guild_id, user_id)


@bot.event
async def on_ready():

    print(f"{bot.user} olarak giriş yapıldı!")

    for guild in bot.guilds:

        for member in guild.members:

            if member.bot:
                continue

            if is_active(member.status):
                start_tracking(guild.id, member.id)


def get_activity(guild_id, user_id, days):

    now = time.time()
    beginning = now - (days * 86400)

    total = 0

    cursor.execute(
        """
        SELECT start_time, end_time
        FROM activity
        WHERE guild_id = ?
        AND user_id = ?
        AND end_time >= ?
        """,
        (guild_id, user_id, beginning)
    )

    sessions = cursor.fetchall()

    for start, end in sessions:

        real_start = max(start, beginning)
        real_end = min(end, now)

        if real_end > real_start:
            total += real_end - real_start

    key = (guild_id, user_id)

    if key in active_users:

        start = max(active_users[key], beginning)

        if now > start:
            total += now - start

    return total


def format_time(seconds):

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)

    return f"{hours} saat {minutes} dakika"


async def show_activity(ctx, days):

    guild = ctx.guild

    if guild is None:
        await ctx.send("Bu komut sadece sunucularda kullanılabilir")
        return

    results = []

    for member in guild.members:

        if member.bot:
            continue

        seconds = get_activity(
            guild.id,
            member.id,
            days
        )

        if seconds > 0:
            results.append(
                (member, seconds)
            )

    results.sort(
        key=lambda x: x[1],
        reverse=True
    )

    results = results[:15]

    embed = discord.Embed(
        title="Aktiflik Tablosu",
        description=f"Son {days} gün",
        color=discord.Color.blue()
    )

    if not results:

        embed.description = (
            f"Son {days} gün içinde kayıtlı aktiflik bulunamadı"
        )

    else:

        text = ""

        for index, (member, seconds) in enumerate(results, 1):

            text += (
                f"**{index}.** {member.display_name} "
                f"— {format_time(seconds)}\n"
            )

        embed.add_field(
            name="En Aktif Üyeler",
            value=text,
            inline=False
        )

    embed.set_footer(
        text="Aktiflik Discord çevrimiçi boşta ve rahatsız etmeyin durumlarına göre hesaplanır"
    )

    await ctx.send(embed=embed)


class ActivityView(discord.ui.View):

    def __init__(self, author_id):

        super().__init__(timeout=60)

        self.author_id = author_id

    async def interaction_check(self, interaction):

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "Bu menüyü sadece komutu kullanan kişi kullanabilir",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.button(
        label="7 Gün",
        style=discord.ButtonStyle.primary
    )
    async def seven_days(self, interaction, button):

        await interaction.response.defer()

        await show_activity(
            interaction.channel,
            7
        )

        await interaction.message.delete()

    @discord.ui.button(
        label="30 Gün",
        style=discord.ButtonStyle.primary
    )
    async def thirty_days(self, interaction, button):

        await interaction.response.defer()

        await show_activity(
            interaction.channel,
            30
        )

        await interaction.message.delete()

    @discord.ui.button(
        label="60 Gün",
        style=discord.ButtonStyle.primary
    )
    async def sixty_days(self, button, interaction):

        pass

    @discord.ui.button(
        label="90 Gün",
        style=discord.ButtonStyle.primary
    )
    async def ninety_days(self, interaction, button):

        await interaction.response.defer()

        await show_activity(
            interaction.channel,
            90
        )

        await interaction.message.delete()


@bot.command()
async def aktiflik(ctx):

    embed = discord.Embed(
        title="Aktiflik Süresi",
        description=(
            "Hangi zaman aralığındaki aktifliği görmek istiyorsun\n\n"
            "Aşağıdan bir süre seç"
        ),
        color=discord.Color.blue()
    )

    view = ActivityView(ctx.author.id)

    await ctx.send(
        embed=embed,
        view=view
    )


# DİĞER KOMUTLAR

@bot.command()
async def merhaba(ctx):
    await ctx.send("Merhaba! Bot çalışıyor 🎉")


@bot.command()
async def mod(ctx, secim: str):

    global current_mode

    secim = secim.lower()

    if secim in mode_prompts:

        current_mode = secim

        await ctx.send(
            f"Mod değiştirildi: **{secim}**"
        )

    else:

        await ctx.send(
            "Geçerli modlar: normal, komik, ciddi, korkutucu, tartışmacı"
        )


@bot.command()
async def soru(ctx, *, soru_metni: str = None):

    if soru_metni is None:

        await ctx.send(
            "Bir soru sormalısın! Örnek: `!soru sen ne yapmayı seversin`"
        )

        return

    cevaplar = [
        "Kesinlikle evet.",
        "Görünüşe göre öyle.",
        "Şüphesiz.",
        "Evet, kesin.",
        "Güvenilir kaynaklara göre evet.",
        "İşaretler evet diyor.",
        "Muhtemelen.",
        "Görünüş iyi.",
        "Evet.",
        "İşaretler biraz belirsiz, tekrar sor.",
        "Şimdi cevap veremem.",
        "Şu an tahmin etme.",
        "Buna güvenme.",
        "Cevabım hayır.",
        "Kaynaklarıma göre hayır.",
        "Görünüşe göre pek iyi değil.",
        "Çok şüpheli."
    ]

    cevap = random.choice(cevaplar)

    embed = discord.Embed(
        title="Sihirli Paklava",
        color=discord.Color.purple()
    )

    embed.add_field(
        name="Soru",
        value=soru_metni,
        inline=False
    )

    embed.add_field(
        name="Cevap",
        value=cevap,
        inline=False
    )

    await ctx.send(embed=embed)


@bot.event
async def on_message(message):

    if message.author == bot.user:
        return

    await bot.process_commands(message)

    is_mentioned = bot.user in message.mentions

    is_reply_to_bot = False

    if message.reference:

        try:

            replied_msg = await message.channel.fetch_message(
                message.reference.message_id
            )

            if replied_msg.author == bot.user:
                is_reply_to_bot = True

        except:
            pass

    if is_mentioned or is_reply_to_bot:

        if not message.content.startswith("!"):

            clean_content = message.content.replace(
                f"<@{bot.user.id}>",
                ""
            ).strip()

            has_attachment = len(message.attachments) > 0

            if clean_content:

                async with message.channel.typing():

                    cevap = get_ai_response(
                        clean_content
                    )

                    await message.reply(cevap)

            elif has_attachment:

                async with message.channel.typing():

                    cevap = get_ai_response(
                        "Kullanıcı sana bir resim veya gif gönderdi ama yazı yazmadı. "
                        "Buna kısa doğal bir tepki ver ve görseli gerçekten göremediğini belirt."
                    )

                    await message.reply(cevap)


keep_alive()

bot.run(os.environ["DISCORD_TOKEN"])
