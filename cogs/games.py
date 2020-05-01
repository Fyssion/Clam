from datetime import datetime, timedelta

import discord
from discord.ext import commands, menus


class Piece:

    def __init__(self, name, emoji_name, custom=False, id=None, animated=False):
        self.name = name
        self.emoji_name = emoji_name
        self.id = id

        if not custom:
            self.emoji = f":emoji_name:"
        else:
            if not animated:
                self.emoji = f"<:{emoji_name}:{id}>"
            else:
                self.emoji = f"<a:{emoji_name}:{id}>"


class SinglePlayerGame(menus.Menu):
    pass


class TenSeconds(SinglePlayerGame):
    def __init__(self):
        super().__init__(timeout=30.0)

    async def send_initial_message(self, ctx, channel):
        em = discord.Embed(description="Click the reaction after 10 seconds!",
                           color=discord.Color.blurple())
        msg = await channel.send(embed=em)
        self.ten_seconds = datetime.utcnow() + timedelta(seconds=10)
        return msg

    @menus.button("⏲️")
    async def on_time(self, payload):
        tm = datetime.utcnow()
        end_time = tm - timedelta(microseconds=tm.microsecond % 100000)
        tm = self.ten_seconds
        ten_seconds = tm - timedelta(microseconds=tm.microsecond % 10000)
        if ten_seconds == end_time:
            msg = ":tada: You did it!"
        elif ten_seconds < end_time:
            time = end_time - ten_seconds
            result = str(float(f"{time.seconds}.{time.microseconds}"))
            msg = f"You were fast by `{result}` seconds."
        elif ten_seconds > end_time:
            time = ten_seconds - end_time
            result = str(float(f"{time.seconds}.{time.microseconds}"))
            msg = f"You were slow by `{result}` seconds."
        em = self.message.embeds[0]
        em.description = msg
        await self.message.edit(embed=em)
        self.stop()


class MultiPlayerGame:

    def __init__(self, players):
        self.players = players


# 1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣
class Connect4(MultiPlayerGame):
    async def send_initial_message(self, ctx, channel):
        return await channel.send(f"Initial message")

    @menus.button("1️⃣")
    async def one(self, payload):
        await self.play_piece("one")

    @menus.button("2️⃣")
    async def two(self, payload):
        await self.play_piece("two")

    @menus.button("3️⃣")
    async def three(self, payload):
        await self.play_piece("three")


class Games(commands.Cog, name=":video_game: Games"):
    """Games to play with friends"""

    def __init__(self, bot):
        self.bot = bot

        # guild_id: Connect4
        self.connect4_games = {}

    @commands.group(name="game", description="Play a game")
    async def _game(self, ctx):
        pass

    @_game.command(desciption="Start a Connect 4 game")
    async def connect4(self, ctx):
        pass

    @commands.command(name="10s", description="Start a ten seconds game. Timer starts as soon as my message is sent.")
    async def ten_seconds(self, ctx):
        m = TenSeconds()
        await m.start(ctx)


def setup(bot):
    bot.add_cog(Games(bot))
