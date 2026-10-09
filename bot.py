import discord
from discord.ext import commands, tasks
import os
import random
import sqlite3
import time
import io
import math
from keep_alive import keep_alive
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

intents = discord.Intents.default()
intents.message_content = True
intents.presences = True
intents.members = True

bot = commands.Bot(
    command_prefix=("!", "B!", "b!"),
    intents=intents,
    case_insensitive=True
)

bot.remove_command("help")

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

OWNER_ID = 1358430002508726276

current_mode = "normal"

mode_prompts = {
    "normal": "Sen yardımsever ve dengeli bir Discord botusun. Türkçe konuş.",
    "komik": "Sen çok esprili, şakacı ve takılan bir Discord botusun. Cevaplarını komik yap. Türkçe konuş.",
    "ciddi": "Sen çok resmi, kısa ve öz cevaplar veren ciddi bir Discord botusun. Türkçe konuş.",
    "korkutucu": "Sen gizemli ve ürkütücü bir atmosferle konuşan bir Discord botusun. Hikaye anlatır gibi ürkütücü bir üslup kullan ama gerçek tehdit içerme. Türkçe konuş.",
    "tartışmacı": "Sen sert ve meydan okuyan bir Discord botusun. Karşı görüşlere sert şekilde itiraz et. Argo kullanabilirsin.Küçük harfle yaz ve noktalama işaretlerini sakın kullanma. Türkçe konuş."
}


def get_ai_response(user_message):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": mode_prompts[current_mode]},
            {"role": "user", "content": user_message}
        ]
    }

    try:
        response = requests.post(
            GROQ_URL,
            headers=headers,
            json=data,
            timeout=25  # Discord'un bağlantısını kilitlememesi için üst sınır
        )
    except requests.exceptions.RequestException as e:
        print(f"API İSTEK HATASI: {e}")
        return "Şu an cevap veremiyorum, Groq'a ulaşılamadı. Birazdan tekrar dene."

    try:
        result = response.json()
    except ValueError:
        print(f"API JSON HATASI: {response.text[:300]}")
        return "Şu an cevap veremiyorum, API'den beklenmedik bir yanıt geldi."

    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        print(f"API HATASI: {result}")
        hata = result.get("error", {}).get("message", "Bilinmeyen hata")
        return f"Bir hata oluştu: {hata}"


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

cursor.execute("""
CREATE TABLE IF NOT EXISTS welcome_config (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER
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
            "INSERT INTO activity VALUES (?, ?, ?, ?)",
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
            results.append((member, seconds))

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
                f"**{index}.** {member.display_name} — "
                f"{format_time(seconds)}\n"
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

    @discord.ui.button(label="7 Gün", style=discord.ButtonStyle.primary)
    async def seven_days(self, interaction, button):
        await interaction.response.defer()
        await show_activity(interaction.channel, 7)
        await interaction.message.delete()

    @discord.ui.button(label="30 Gün", style=discord.ButtonStyle.primary)
    async def thirty_days(self, interaction, button):
        await interaction.response.defer()
        await show_activity(interaction.channel, 30)
        await interaction.message.delete()

    @discord.ui.button(label="60 Gün", style=discord.ButtonStyle.primary)
    async def sixty_days(self, interaction, button):
        await interaction.response.defer()
        await show_activity(interaction.channel, 60)
        await interaction.message.delete()

    @discord.ui.button(label="90 Gün", style=discord.ButtonStyle.primary)
    async def ninety_days(self, interaction, button):
        await interaction.response.defer()
        await show_activity(interaction.channel, 90)
        await interaction.message.delete()


@bot.command()
async def aktiflik(ctx):
    embed = discord.Embed(
        title="Aktiflik Süresi",
        description="Hangi zaman aralığındaki aktifliği görmek istiyorsun\n\nAşağıdan bir süre seç",
        color=discord.Color.blue()
    )

    view = ActivityView(ctx.author.id)

    await ctx.send(
        embed=embed,
        view=view
    )


# NORMAL KOMUTLAR

@bot.command()
async def merhaba(ctx):
    await ctx.send("Merhaba! Bot çalışıyor 🎉")


@bot.command()
async def mod(ctx, secim: str):
    global current_mode

    secim = secim.lower()

    if secim in mode_prompts:
        current_mode = secim
        await ctx.send(f"Mod değiştirildi: **{secim}**")
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
        "he doğru nerden bildin oic.",
        "abov.",
        "cevap veremem purnaciyim.",
        "he kesin.",
        "şuanda bir adamı sömürüyom.",
        "yarrak.",
        "purna.",
        "yo doğru değil sen calismamissin 0 aldin.",
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


@bot.command(name="öneri")
async def oneri(ctx, *, oneri_metni: str = None):
    if oneri_metni is None:
        await ctx.send(
            "Bir öneri yazmalısın! Örnek: `!öneri bence şu eklensin`"
        )
        return

    try:
        owner = await bot.fetch_user(OWNER_ID)

        embed = discord.Embed(
            title="📩 Yeni Öneri",
            description=oneri_metni,
            color=discord.Color.gold()
        )

        embed.add_field(
            name="Gönderen",
            value=f"{ctx.author} ({ctx.author.id})",
            inline=False
        )

        embed.add_field(
            name="Sunucu",
            value=ctx.guild.name if ctx.guild else "DM",
            inline=False
        )

        await owner.send(embed=embed)
        await ctx.send("Öneriniz iletildi, teşekkürler! ✅")

    except discord.Forbidden:
        await ctx.send("Öneri iletilemedi, bir hata oluştu.")


# TAŞ KAĞIT MAKAS

class TKMGameView(discord.ui.View):
    def __init__(self, player1, player2):
        super().__init__(timeout=120)

        self.player1 = player1
        self.player2 = player2
        self.choices = {}

    async def interaction_check(self, interaction):
        if interaction.user.id not in (
            self.player1.id,
            self.player2.id
        ):
            await interaction.response.send_message(
                "Bu oyunda değilsin",
                ephemeral=True
            )
            return False

        return True

    async def choose(self, interaction, choice):
        if interaction.user.id in self.choices:
            await interaction.response.send_message(
                "Zaten seçim yaptın",
                ephemeral=True
            )
            return

        self.choices[interaction.user.id] = choice

        await interaction.response.send_message(
            f"Seçimin kaydedildi: **{choice}**",
            ephemeral=True
        )

        if len(self.choices) == 2:
            p1_choice = self.choices[self.player1.id]
            p2_choice = self.choices[self.player2.id]

            if p1_choice == p2_choice:
                result = "Berabere"

            elif (
                (p1_choice == "Taş" and p2_choice == "Makas")
                or
                (p1_choice == "Kağıt" and p2_choice == "Taş")
                or
                (p1_choice == "Makas" and p2_choice == "Kağıt")
            ):
                result = f"{self.player1.mention} kazandı"

            else:
                result = f"{self.player2.mention} kazandı"

            for button in self.children:
                button.disabled = True

            await interaction.message.edit(
                content=(
                    "**Taş Kağıt Makas Sonucu**\n\n"
                    f"{self.player1.mention}: **{p1_choice}**\n"
                    f"{self.player2.mention}: **{p2_choice}**\n\n"
                    f"**{result}**"
                ),
                view=self
            )

            self.stop()

    @discord.ui.button(label="Taş", style=discord.ButtonStyle.primary)
    async def rock(self, interaction, button):
        await self.choose(interaction, "Taş")

    @discord.ui.button(label="Kağıt", style=discord.ButtonStyle.success)
    async def paper(self, interaction, button):
        await self.choose(interaction, "Kağıt")

    @discord.ui.button(label="Makas", style=discord.ButtonStyle.danger)
    async def scissors(self, interaction, button):
        await self.choose(interaction, "Makas")


class TKMInviteView(discord.ui.View):
    def __init__(self, challenger, opponent):
        super().__init__(timeout=60)

        self.challenger = challenger
        self.opponent = opponent

    @discord.ui.button(
        label="Kabul Et",
        style=discord.ButtonStyle.success
    )
    async def accept(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "Bu daveti sadece etiketlenen oyuncu kabul edebilir",
                ephemeral=True
            )
            return

        for item in self.children:
            item.disabled = True

        game_view = TKMGameView(
            self.challenger,
            self.opponent
        )

        await interaction.response.edit_message(
            content=(
                f"{self.challenger.mention} ve {self.opponent.mention}\n\n"
                "İkiniz de aşağıdan seçiminizi yapın"
            ),
            view=game_view
        )

        self.stop()

    @discord.ui.button(
        label="Reddet",
        style=discord.ButtonStyle.danger
    )
    async def decline(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "Bu daveti sadece etiketlenen oyuncu reddedebilir",
                ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content=f"{self.opponent.mention} oyunu reddetti",
            view=None
        )

        self.stop()


@bot.command(name="tkm")
async def tkm(ctx, oyuncu: discord.Member = None):
    if oyuncu is None:
        await ctx.send(
            "Bir oyuncuyu etiketlemelisin. Örnek: `B!tkm @oyuncu`"
        )
        return

    if oyuncu.bot:
        await ctx.send("Botlarla taş kağıt makas oynayamazsın")
        return

    if oyuncu.id == ctx.author.id:
        await ctx.send("Kendinle taş kağıt makas oynayamazsın")
        return

    view = TKMInviteView(
        ctx.author,
        oyuncu
    )

    await ctx.send(
        f"{oyuncu.mention}\n\n"
        f"{ctx.author.mention} seninle Taş Kağıt Makas oynamak istiyor\n"
        "Kabul ediyor musun",
        view=view
    )


# 1vs1 DÜELLO SİSTEMİ

DUEL_MAX_HP = 500
DUEL_ULTRA_COST = 100

active_duels = {}  # channel_id -> DuelGameView


def make_hp_bar(hp, max_hp=DUEL_MAX_HP):
    filled = round((hp / max_hp) * 10)
    filled = max(0, min(10, filled))
    return "🟩" * filled + "⬛" * (10 - filled)


class DuelGameView(discord.ui.View):
    def __init__(self, player1, player2, channel_id):
        super().__init__(timeout=300)

        self.channel_id = channel_id
        self.order = [player1.id, player2.id]
        self.turn_index = 0
        self.players = {
            player1.id: {"member": player1, "hp": DUEL_MAX_HP, "power": 0, "shield": False},
            player2.id: {"member": player2, "hp": DUEL_MAX_HP, "power": 0, "shield": False},
        }

    @property
    def current_player_id(self):
        return self.order[self.turn_index]

    def other_id(self, user_id):
        return self.order[1] if user_id == self.order[0] else self.order[0]

    async def interaction_check(self, interaction):
        if interaction.user.id not in self.players:
            await interaction.response.send_message(
                "Bu düelloda değilsin", ephemeral=True
            )
            return False

        if interaction.user.id != self.current_player_id:
            await interaction.response.send_message(
                "Sıra sende değil", ephemeral=True
            )
            return False

        return True

    def build_embed(self, log_line=""):
        embed = discord.Embed(
            title="⚔️ 1vs1 Düello",
            color=discord.Color.red()
        )

        if log_line:
            embed.description = log_line

        for user_id in self.order:
            p = self.players[user_id]
            value = (
                f"❤️ {p['hp']}/{DUEL_MAX_HP}\n"
                f"{make_hp_bar(p['hp'])}\n"
                f"⚡ Güç: {p['power']}/{DUEL_ULTRA_COST}"
            )

            if p["shield"]:
                value += "\n🧱 Çin Seddi aktif"

            embed.add_field(
                name=p["member"].display_name,
                value=value,
                inline=True
            )

        embed.set_footer(
            text=f"Sıra: {self.players[self.current_player_id]['member'].display_name}"
        )

        return embed

    async def end_duel(self, interaction, winner_id, loser_id, reason):
        active_duels.pop(self.channel_id, None)

        for item in self.children:
            item.disabled = True

        embed = discord.Embed(
            title="🏆 Düello Bitti",
            description=reason,
            color=discord.Color.gold()
        )

        embed.add_field(
            name="Kazanan",
            value=self.players[winner_id]["member"].mention
        )

        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def resolve(self, interaction, action):
        actor_id = interaction.user.id
        target_id = self.other_id(actor_id)
        actor = self.players[actor_id]
        target = self.players[target_id]
        log = ""

        if action == "terlik":
            dmg = random.randint(50, 99)
            blocked = ""

            if target["shield"]:
                dmg = round(dmg * 0.4)
                target["shield"] = False
                blocked = " (Çin Seddi hasarı azalttı!)"

            target["hp"] = max(0, target["hp"] - dmg)
            log = (
                f"👡 {actor['member'].mention}, {target['member'].mention}'e "
                f"anne terliğiyle **{dmg}** hasar verdi!{blocked}"
            )

        elif action == "topla":
            gain = random.randint(30, 60)
            actor["power"] = min(DUEL_ULTRA_COST, actor["power"] + gain)
            log = f"🔋 {actor['member'].mention} güç topladı! (+{gain} güç)"

        elif action == "ultra":
            if actor["power"] < DUEL_ULTRA_COST:
                log = f"❌ {actor['member'].mention}, yeterli gücün yok, git topla."
            else:
                dmg = random.randint(200, 300)
                blocked = ""

                if target["shield"]:
                    dmg = round(dmg * 0.4)
                    target["shield"] = False
                    blocked = " (Çin Seddi hasarı azalttı!)"

                actor["power"] = 0
       
