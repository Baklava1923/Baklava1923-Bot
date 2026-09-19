import discord
from discord.ext import commands
import os
from keep_alive import keep_alive

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} olarak giriş yapıldı!")

@bot.command()
async def merhaba(ctx):
    await ctx.send("Merhaba! Bot çalışıyor 🎉")

keep_alive()
bot.run(os.environ["DISCORD_TOKEN"])
