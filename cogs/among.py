from discord.ext import commands
import discord

import datetime

from .utils import human_time


class AmongUsGame:
    pass


class AmongUsCode:
    def __init__(self, code, region, author):
        self.code = code
        self.region = region
        self.author = author
        self.set_at = datetime.datetime.utcnow()

    def __str__(self):
        return self.code


class AmongUs(commands.Cog, name="Among Us"):
    """Commands that make Among Us more fun"""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "<:among:755230659030679573>"

        if not hasattr(bot, "among_games"):
            # guild_id: AmongUsGame
            bot.among_games = {}

        self.among_games = bot.among_games

        if not hasattr(bot, "among_codes"):
            # guild_id: AmongUsCode
            bot.among_codes = {}

        self.among_codes = bot.among_codes

    def get_code(self, guild_id):
        return self.among_codes.get(guild_id)

    def get_game(self, guild_id):
        return self.among_games.get(guild_id)

    async def before_invoke(self, ctx):
        ctx.game = self.get_game(ctx.guild.id)

    @commands.group(
        description="A set of commands that make Among Us more fun",
        aliases=["amongus"],
        invoke_without_command=True,
    )
    async def among(self, ctx):
        await ctx.send_help(ctx.command)

    @among.group(name="code", invoke_without_command=True)
    async def among_code(self, ctx):
        """View the current Among Us code for this server

        To set a code, use `among code set`
        """
        code = self.get_code(ctx.guild.id)

        if not code:
            return await ctx.send("A code has not been set for this server.")

        await ctx.send(f"Among Us code: **`{code}`** (region: `{code.region}`)\n"f"Set by `{code.author}` {human_time.human_timedelta(code.set_at)}.")

    @among_code.command(name="set", description="Set the current code for Among Us", aliases=["create"])
    async def among_code_set(self, ctx, code, *, region="North America"):
        old_code = self.get_code(ctx.guild.id)

        if old_code:
            result = await ctx.confirm(
                f"There is already a code set: `{old_code}` (region: `{old_code.region}`). Do you want to overwrite it?\n"
                f"This code was set by `{old_code.author}` {human_time.human_timedelta(old_code.set_at)}."
            )

            if not result:
                return await ctx.send("Aborted.")

        self.among_codes[ctx.guild.id] = AmongUsCode(code.upper(), region, ctx.author)

        await ctx.send(ctx.tick(True, f"Among Us code set to **`{code}`** (region: `{region}`)"))

    @among_code.command(name="clear", description="Clear the current code for Among Us", aliases=["reset"])
    async def among_code_clear(self, ctx):
        old_code = self.get_code(ctx.guild.id)

        if not old_code:
            return await ctx.send("A code has not been set for this server.")

        result = await ctx.confirm(
            f"The current code is `{old_code}` (region: `{old_code.region}`). Do you want to clear it?\n"
            f"This code was set by `{old_code.author}` {human_time.human_timedelta(old_code.set_at)}."
        )

        if not result:
            return await ctx.send("Aborted.")

        self.among_codes.pop(ctx.guild.id)

        await ctx.send(ctx.tick(True, "Cleared code."))

    @among.command(
        name="start",
        description="Start the Among Us game and mute everyone in the channel",
    )
    async def among_start(self, ctx):
        result = await ctx.confirm(
            "Start the Among Us game?\n**This will mute `num` members in the voice channel.**"
        )
        if not result:
            return await ctx.send("Aborted")

    @among.command(
        name="stop",
        description="Stop the Among Us game and unmute everyone",
        aliases=["end"],
    )
    @commands.is_owner()
    async def among_stop(self, ctx):
        pass

    @among.command(name="mute", description="Mute all members in the voice channel")
    @commands.is_owner()
    async def among_mute(self, ctx):
        pass

    @among.command(
        name="unmute",
        description="Umute all undead members in the voice channel (discussion)",
        aliases=["discuss"],
    )
    @commands.is_owner()
    async def among_unmute(self, ctx):
        pass

    @among.command(name="dead")
    @commands.is_owner()
    async def among_dead(self, ctx, *, member: discord.Member = None):
        """Mark yourself or someone else as dead (mutes you/them)

        This mutes you until the game is over or until you leave the game
        """
        pass

    @among.command(
        name="leave", description="Leave the current Among Us game (unmutes you)"
    )
    @commands.is_owner()
    async def among_leave(self, ctx):
        pass


def setup(bot):
    bot.add_cog(AmongUs(bot))
