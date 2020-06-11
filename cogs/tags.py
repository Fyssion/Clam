import discord
from discord.ext import commands

import asyncpg

from .utils import db, checks, colors


# Note that this was heavily inspired by Rapptz/RoboDanny
# https://github.com/Rapptz/RoboDanny/blob/65b13cad81317768b21cd1e1e05e6efc414cceda/cogs/tags.py


class Tags(db.Table):
    id = db.PrimaryKeyColumn()
    name = db.Column(db.String(length=60), index=True)
    content = db.Column(db.String(length=2000))
    owner_id = db.Column(db.Integer(big=True), index=True)
    guild_id = db.Column(db.Integer(big=True), index=True)
    created_at = db.Column(db.Datetime(), default="now() at time zone 'utc'")
    uses = db.Column(db.Integer, default=0)

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = (
            "CREATE UNIQUE INDEX IF NOT EXISTS tags_uniq_idx ON tags (LOWER(name), guild_id);\n"
            "CREATE INDEX IF NOT EXISTS tags_name_trgm_idx ON tags USING GIN (name gin_trgm_ops);"
        )
        return statement + "\n" + sql


class TagAliases(db.Table, table_name="tag_aliases"):
    id = db.PrimaryKeyColumn()
    name = db.Column(db.String(length=60), index=True)
    owner_id = db.Column(db.Integer(big=True))
    guild_id = db.Column(db.Integer(big=True), index=True)
    created_at = db.Column(db.Datetime, default="now() at time zone 'utc'")
    tag_id = db.Column(db.ForeignKey("tags", "id"))

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = (
            "CREATE UNIQUE INDEX IF NOT EXISTS tag_aliases_uniq_idx ON tag_aliases (LOWER(name), guild_id);\n"
            "CREATE INDEX IF NOT EXISTS tags_alias_name_trgm_idx ON tag_aliases USING GIN (name gin_trgm_ops);"
        )
        return statement + "\n" + sql


class Tag:
    @classmethod
    def from_record(cls, record):
        self = cls()

        self.id = record["id"]
        self.name = record["name"]
        self.content = record["content"]
        self.owner_id = record["owner_id"]
        self.guild_id = record["guild_id"]
        self.created_at = record["created_at"]
        self.uses = record["uses"]

        return self

    @classmethod
    def from_partial(cls, partial):
        self = cls()

        self.name = partial["name"]
        self.content = partial["content"]

        return self


class TagConverter(commands.Converter):
    async def convert(self, ctx, arg):
        arg = arg.lower()
        query = """SELECT tags.name, tags.content
                   FROM tag_aliases
                   INNER JOIN tags ON tags.id = tag_aliases.tag_id
                   WHERE tag_aliases.guild_id=$1 AND LOWER(tag_aliases.name)=$2;
                """

        row = await ctx.db.fetchrow(query, ctx.guild.id, arg)

        if not row:
            query = """SELECT     tag_aliases.name
                       FROM       tag_aliases
                       WHERE      tag_aliases.guild_id=$1 AND tag_aliases.name % $2
                       ORDER BY   similarity(tag_aliases.name, $2) DESC
                       LIMIT 3;
                    """

            rows = await ctx.db.fetch(query, ctx.guild.id, arg)

            if not rows:
                raise commands.BadArgument("Could not find tag.")

            similar = "\n".join(r["name"] for r in rows)
            raise commands.BadArgument(
                f"Could not find tag. Did you mean...\n{similar}"
            )

        return Tag.from_partial(row)


class TagFullConverter(commands.Converter):
    async def convert(self, ctx, arg):
        query = """SELECT *
                   FROM tags
                   WHERE guild_id=$1 AND name=$2;
                """

        record = await ctx.db.fetchrow(query, ctx.guild.id, arg.lower())

        if not record:
            raise commands.BadArgument(
                "I couldn't find that tag. Make sure you aren't specifying a tag alias."
            )

        return Tag.from_record(record)


class TagNameConverter(commands.Converter):
    async def convert(self, ctx, arg):
        name = arg.lower().strip()

        if len(name) > 60:
            raise commands.BadArgument(
                "Tag name is too long. Must be 60 characters or shorter."
            )

        first_word = name.split(" ")[0]
        root = ctx.bot.get_command("tag")
        if first_word in root.all_commands:
            raise commands.BadArgument("Tag name starts with a reserved word.")

        return name


class TagContentConverter(commands.clean_content):
    async def convert(self, ctx, arg):
        content = await super().convert(ctx, arg)

        if len(content) > 2000:
            raise commands.BadArgument(
                "Tag content is too long. Must be 2000 characters or shorter."
            )

        return content


class Tags(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":bookmark:"
        self.log = self.bot.log

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))
            ctx.handled = True

    @commands.group(usage="<tag>", invoke_without_command=True)
    async def tag(self, ctx, name=None):
        """Tag stuff and retrieve it later

        You can create, edit, delete, and alias
        tags. Note that server moderators can delete
        any tags.
        """
        if not name:
            return await ctx.send_help(ctx.command)

        tag = await TagConverter().convert(ctx, name)
        await ctx.send(tag.content)

        query = "UPDATE tags SET uses = uses + 1 WHERE name=$1 AND guild_id=$2;"
        await ctx.db.execute(query, tag.name, ctx.guild.id)

    @tag.command(
        name="create",
        description="Create a new tag",
        aliases=["new"],
        usage="[name] [content]",
    )
    async def tag_create(
        self, ctx, name: TagNameConverter, *, content: TagContentConverter
    ):
        # https://github.com/Rapptz/RoboDanny/blob/65b13cad81317768b21cd1e1e05e6efc414cceda/cogs/tags.py#L253-L283
        query = """WITH tag_insert AS (
                        INSERT INTO tags (name, content, owner_id, guild_id)
                        VALUES ($1, $2, $3, $4)
                        RETURNING id
                    )
                    INSERT INTO tag_aliases (name, owner_id, guild_id, tag_id)
                    VALUES ($1, $3, $4, (SELECT id FROM tag_insert));
                """

        async with ctx.db.acquire() as con:
            tr = con.transaction()
            await tr.start()

            try:
                await ctx.db.execute(query, name, content, ctx.author.id, ctx.guild.id)
            except asyncpg.UniqueViolationError:
                await tr.rollback()
                await ctx.send("There is already a tag with this name.")
            except:
                await tr.rollback()
                await ctx.send("Could not create tag. Sorry.")
            else:
                await tr.commit()
                await ctx.send(f"Successfully created tag **`{name}`**.")

    @tag.command(
        name="delete",
        description="Delete a tag you own",
        usage="[tag name]",
        aliases=["remove"],
    )
    async def delete(self, ctx, name):
        # https://github.com/Rapptz/RoboDanny/blob/65b13cad81317768b21cd1e1e05e6efc414cceda/cogs/tags.py#L644-L669
        bypass_owner_check = (
            ctx.author.id == self.bot.owner_id
            or ctx.author.guild_permissions.manage_messages
        )
        clause = "LOWER(name)=$1 AND guild_id=$2"

        if bypass_owner_check:
            args = [name, ctx.guild.id]
        else:
            args = [name, ctx.guild.id, ctx.author.id]
            clause = f"{clause} AND owner_id=$3"

        query = f"DELETE FROM tag_aliases WHERE {clause} RETURNING tag_id;"
        deleted = await ctx.db.fetchrow(query, *args)

        if deleted is None:
            await ctx.send(
                "Could not delete tag. Either it does not exist or you do not have permissions to do so."
            )
            return

        args.append(deleted[0])
        query = f"DELETE FROM tags WHERE id=${len(args)} AND {clause};"
        status = await ctx.db.execute(query, *args)

        # the status returns DELETE <count>, similar to UPDATE above
        if status[-1] == "0":
            # this is based on the previous delete above
            await ctx.send("Tag alias successfully deleted.")
        else:
            await ctx.send("Tag and corresponding aliases successfully deleted.")

    @tag.command(
        name="edit",
        description="Edit a tag you own",
        usage="[name] [new content]",
        aliases=["update"],
    )
    async def tag_edit(
        self, ctx, tag: TagNameConverter, *, content: TagContentConverter
    ):
        query = """UPDATE tags
                   SET content=$1
                   WHERE LOWER(name)=$2 AND guild_id=$3 AND owner_id=$4;
                """

        status = await ctx.db.execute(query, content, tag, ctx.guild.id, ctx.author.id)

        if status[-1] == "0":
            return await ctx.send(
                "Tag edit failed. Either the tag doesn't exist or you don't own it."
            )

        await ctx.send("Successfully edited tag.")

    @tag.command(
        name="alias",
        description="Set an alias for a tag",
        usage="[original name] [alias name]",
    )
    async def tag_alias(self, ctx, original: TagNameConverter, alias: TagNameConverter):
        query = """INSERT INTO tag_aliases (name, owner_id, guild_id, tag_id)
                   SELECT $1, $4, guild_id, tag_id
                   FROM tag_aliases
                   WHERE guild_id=$3 AND LOWER(name)=$2;
                """

        try:
            status = await ctx.db.execute(
                query, alias, original.lower(), ctx.guild.id, ctx.author.id
            )
        except asyncpg.UniqueViolationError:
            await ctx.send("A tag with this name already exists.")
        else:
            # The status returns INSERT N M, where M is the number of rows inserted.
            if status[-1] == "0":
                await ctx.send(f"Tag **`{original}`** doesn't exist.")
            else:
                await ctx.send(
                    f"An aliases for **`{original}`** called **`{alias}`** has been created."
                )

    def _owner_kwargs(self, guild, owner_id):
        member = guild.get_member(owner_id)
        if not member:
            return {"name": owner_id}
        return {"name": str(member), "icon_url": member.avatar_url}

    @tag.command(
        name="info",
        description="Get info about a tag",
        aliases=["about"],
        usage="[tag]",
    )
    async def tag_info(self, ctx, name):
        query = """SELECT
                       tag_aliases.name <> tags.name AS "Alias",
                       tag_aliases.name AS alias_name,
                       tag_aliases.created_at AS alias_created_at,
                       tag_aliases.owner_id AS alias_owner_id,
                       tags.*
                   FROM tag_aliases
                   INNER JOIN tags ON tag_aliases.tag_id = tags.id
                   WHERE LOWER(tag_aliases.name)=$1 AND tag_aliases.guild_id=$2
                """

        record = await ctx.db.fetchrow(query, name, ctx.guild.id)

        if record is None:
            return await ctx.send("Tag not found.")

        if record["Alias"]:
            em = discord.Embed(
                title="Tag Alias Info",
                description=f"Alias name: **`{record['alias_name']}`**\nPoints to **`{record['name']}`**",
                color=colors.PRIMARY,
                timestamp=record["alias_created_at"],
            )
            em.set_author(**self._owner_kwargs(ctx.guild, record["alias_owner_id"]))
            em.set_footer(text="Created")

        else:
            em = discord.Embed(
                title="Tag Info",
                description=f"Tag name: **`{record['name']}`**\nUses: **{record['uses']}**",
                color=colors.PRIMARY,
                timestamp=record["created_at"],
            )
            em.set_author(**self._owner_kwargs(ctx.guild, record["owner_id"]))
            em.set_footer(text=f"ID: {record['id']} | Created")

        await ctx.send(embed=em)

    @tag.command(
        name="raw",
        description="Get a tag without markdown (for copy/pasting)",
        usage="[tag name]",
    )
    async def tag_raw(self, ctx, tag: TagConverter):
        await ctx.send(discord.utils.escape_markdown(tag.content))

        query = "UPDATE tags SET uses = uses + 1 WHERE name=$1 AND guild_id=$2;"
        await ctx.db.execute(query, tag.name, ctx.guild.id)

    @tag.command(
        name="all", description="List all tags for this server", aliases=["list"]
    )
    async def tag_all(self, ctx):
        query = """SELECT name, uses
                   FROM tags
                   WHERE guild_id=$1
                   LIMIT 10;
                """

        results = await ctx.db.fetch(query, ctx.guild.id)

        if not results:
            return await ctx.send("This server has no tags.")

        em = discord.Embed(title="All Tags", color=colors.PRIMARY)

        desc = "\n".join(f"**{r[0]}** ({r[1]} uses)" for r in results)

        if len(results) == 10:
            desc += "\n Only showing first ten tags."

        em.description = desc

        await ctx.send(embed=em)

    @tag.command(
        name="top",
        description="List top tags by number of uses for this server",
        aliases=["ranks"],
    )
    async def tag_rank(self, ctx):
        query = """SELECT name, uses
                   FROM tags
                   WHERE guild_id=$1
                   ORDER BY uses DESC
                   LIMIT 10;
                """

        results = await ctx.db.fetch(query, ctx.guild.id)

        if not results:
            return await ctx.send("This server has no tags.")

        em = discord.Embed(title="Top Tags", color=colors.PRIMARY)

        desc = "\n".join(
            f"`{i+1}.` **{n}** ({r} uses)" for i, (n, r) in enumerate(results)
        )

        if len(results) == 10:
            desc += "\n Only showing top ten tags."

        em.description = desc

        await ctx.send(embed=em)


def setup(bot):
    bot.add_cog(Tags(bot))
