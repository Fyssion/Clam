from discord.ext import commands
import discord

import asyncpg

from .utils import db, human_time, cache


class AmongGameTable(db.Table, table_name="among_games"):
    id = db.Column(db.Integer(big=True), primary_key=True)

    code = db.Column(db.String)
    code_region = db.Column(db.String)
    code_author = db.Column(db.Integer(big=True))
    code_set_at = db.Column(db.Datetime, default="now() at time zone 'utc'")

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = "CREATE UNIQUE INDEX IF NOT EXISTS among_uniq_idx ON among_games (UPPER(code), UPPER(code_region));"
        return statement + "\n" + sql


class AmongCode:
    @classmethod
    def from_record(cls, record):
        self = cls()

        self.code = record["code"]
        self.region = record["code_region"]
        self.author = record["code_author"]
        self.set_at = record["code_set_at"]

        return self

    def __str__(self):
        return self.code


class AmongGame:
    @classmethod
    def from_record(cls, record):
        self = cls()

        self.id = record["id"]
        if record["code"] is not None:
            self.code = AmongCode.from_record(record)
        else:
            self.code = None

        return self


class AmongUs(commands.Cog, name="Among Us"):
    """Commands that make Among Us easier to play"""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = "<:among:755230659030679573>"

        if not hasattr(bot, "among_games"):
            # guild_id: AmongUsGame
            bot.among_games = {}

        self.among_games = bot.among_games

    @cache.cache()
    async def get_game(self, guild_id):
        query = """SELECT * FROM among_games
                   WHERE id=$1;
                """

        record = await self.bot.pool.fetchrow(query, guild_id)

        if not record:
            return None

        return AmongGame.from_record(record)

    @commands.group(
        description="A set of commands that make Among Us easier to play",
        aliases=["amongus"],
        invoke_without_command=True,
    )
    async def among(self, ctx):
        await ctx.invoke(self.among_code)

    @among.group(name="code", invoke_without_command=True)
    async def among_code(self, ctx):
        """View the current Among Us code for this server

        To set a code, use `among code set`
        """
        game = await self.get_game(ctx.guild.id)

        if not game or not game.code:
            return await ctx.send("A code has not been set for this server.")

        code = game.code
        author = self.bot.get_user(code.author)
        formatted = str(author) if author else "[unknown user]"

        await ctx.send(
            f"Among Us code: `{code}` (region: `{code.region}`)\n"
            f"Set by `{formatted}` {human_time.human_timedelta(code.set_at, accuracy=1)}."
        )

    @among_code.command(
        name="set", description="Set the current code for Among Us", aliases=["create"]
    )
    async def among_code_set(self, ctx, code, *, region="North America"):
        query = """INSERT INTO among_games (id, code, code_region, code_author, code_set_at)
                   VALUES ($1, $2, $3, $4, (now() at time zone 'utc')) ON CONFLICT (id) DO UPDATE SET
                        code=EXCLUDED.code,
                        code_region=EXCLUDED.code_region,
                        code_author=EXCLUDED.code_author,
                        code_set_at=EXCLUDED.code_set_at;
                """

        async with ctx.db.acquire() as conn:
            async with conn.transaction():
                try:
                    await ctx.db.execute(query, ctx.guild.id, code.upper(), region, ctx.author.id)
                except asyncpg.UniqueViolationError:
                    game = await self.get_game(ctx.guild.id)
                    code = game.code
                    author = self.bot.get_user(code.author)
                    formatted = str(author) if author else "[unknown user]"
                    return await ctx.send(
                        (
                            f"{ctx.tick(False)} That code and region have already been set by "
                            f"`{formatted}` {human_time.human_timedelta(code.set_at, accuracy=1)}.\n"
                            f"You can clear the current code with `{ctx.guild_prefix}among code clear`"
                        ),
                    )

        self.get_game.invalidate(self, ctx.guild.id)

        await ctx.send(
            ctx.tick(
                True, f"Among Us code set to **`{code.upper()}`** (region: `{region}`)"
            )
        )

    @among_code.command(
        name="clear",
        description="Clear the current code for Among Us",
        aliases=["reset"],
    )
    async def among_code_clear(self, ctx):
        query = """INSERT INTO among_games (id, code, code_region, code_author, code_set_at)
                   VALUES ($1, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO UPDATE SET
                        code=EXCLUDED.code,
                        code_region=EXCLUDED.code_region,
                        code_author=EXCLUDED.code_author,
                        code_set_at=EXCLUDED.code_set_at;
                """

        async with ctx.db.acquire() as conn:
            async with conn.transaction():
                try:
                    await ctx.db.execute(query, ctx.guild.id)
                except asyncpg.UniqueViolationError:
                    return await ctx.send(ctx.tick(False, "A code has not been set for this server."))

        self.get_game.invalidate(self, ctx.guild.id)

        await ctx.send(ctx.tick(True, "Cleared code."))

    @among.command(
        name="start",
        description="Start the Among Us game and mute everyone in the channel",
    )
    @commands.is_owner()
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
