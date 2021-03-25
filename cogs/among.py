import asyncpg
import discord
from discord.ext import commands

from .utils import cache, db, humantime


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

    async def set_among_code(self, ctx, code, region="North America"):
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
                            f"`{formatted}` {humantime.timedelta(code.set_at, accuracy=1)}.\n"
                            f"You can clear the current code with `{ctx.guild_prefix}among code clear`"
                        ),
                    )

        self.get_game.invalidate(self, ctx.guild.id)

        await ctx.send(
            ctx.tick(
                True, f"Among Us code set to **`{code.upper()}`** (region: `{region}`)"
            )
        )

    @commands.group(
        description="Retrieves or saves an Among Us game code.",
        aliases=["amongus", "amogus"],
        invoke_without_command=True,
    )
    async def among(self, ctx, code=None, *, region="North America"):
        """View or set the current Among Us code for this server.

        To set a code, use `{prefix}among <code> [region]`
        """
        if code:
            return await self.set_among_code(ctx, code, region)

        game = await self.get_game(ctx.guild.id)

        if not game or not game.code:
            return await ctx.send("A code has not been set for this server.")

        code = game.code
        author = self.bot.get_user(code.author)
        formatted = str(author) if author else "[unknown user]"

        await ctx.send(
            f"Among Us code: `{code}` (region: `{code.region}`)\n"
            f"Set by `{formatted}` {humantime.timedelta(code.set_at, accuracy=1)}."
        )

    @among.command(name="us")
    async def among_us(self, ctx, code=None, *, region="North America"):
        """Alias for `among`."""
        await ctx.invoke(self.among, code=code, region=region)

    @among.command(
        name="clear",
        description="Clears the current Among Us code.",
        aliases=["reset"],
    )
    async def among_clear(self, ctx):
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


def setup(bot):
    bot.add_cog(AmongUs(bot))
