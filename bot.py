import discord
from discord.ext import commands
import os
from keep_alive import keep_alive
import requests

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

current_mode = "normal"

mode_prompts = {
    "normal": "Sen yardımsever ve dengeli bir Discord botusun. Türkçe konuş.",
    "komik": "Sen çok esprili, şakacı ve takılan bir Discord botusun. Cevaplarını komik yap. Türkçe konuş.",
    "ciddi": "Sen çok resmi, kısa ve öz cevaplar veren ciddi bir Discord botusun. Türkçe konuş.",
    "korkutucu": "Sen gizemli ve ürkütücü bir atmosferle konuşan bir Discord botusun. Hikaye anlatır gibi ürkütücü bir üslup kullan ama gerçek tehdit içerme. Türkçe konuş.",
    "tartışmacı": "Sen her konuda karşı görüş sunan, meydan okuyan ve itiraz eden bir Discord botusun. Saygılı ama ısrarcı bir üslupla tartış. Türkçe konuş."
}

def get_ai_response(user_message):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "meta-llama/llama-3.1-8b-instruct:free",
        "messages": [
            {"role": "system", "content": mode_prompts[current_mode]},
            {"role": "user", "content": user_message}
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
    else:
        print(f"API HATASI: {result}")
        return f"Bir hata oluştu: {result.get('error', {}).get('message', 'Bilinmeyen hata')}"

@bot.event
async def on_ready():
    print(f"{bot.user} olarak giriş yapıldı!")

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
        await ctx.send("Geçerli modlar: normal, komik, ciddi, korkutucu, tartışmacı")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    await bot.process_commands(message)

    is_mentioned = bot.user in message.mentions
    is_reply_to_bot = False
    if message.reference:
        try:
            replied_msg = await message.channel.fetch_message(message.reference.message_id)
            if replied_msg.author == bot.user:
                is_reply_to_bot = True
        except:
            pass

    if is_mentioned or is_reply_to_bot:
        if not message.content.startswith("!"):
            clean_content = message.content.replace(f"<@{bot.user.id}>", "").strip()
            if clean_content:
                async with message.channel.typing():
                    cevap = get_ai_response(clean_content)
                    await message.reply(cevap)

keep_alive()
bot.run(os.environ["DISCORD_TOKEN"])
