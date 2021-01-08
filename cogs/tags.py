import asyncio
import re

import asyncpg
import discord
from discord.ext import commands, menus


from .utils import checks, colors, db
from .utils.menus import MenuPages


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

    faq = db.Column(db.Boolean, index=True)

    # embed stuff
    embed = db.Column(db.Boolean)
    embed_title = db.Column(db.String(length=256))
    embed_description = db.Column(db.String(length=2048))
    # embed_fields = db.Column(db.Array(db.Array(db.String(length=1024))))
    embed_thumbnail = db.Column(db.String(length=2000))
    embed_image = db.Column(db.String(length=2000))

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


def faq_only():
    async def predicate(ctx):
        try:
            await checks.has_permissions(manage_guild=True).predicate(ctx)
            return True
        except commands.MissingPermissions as missing_perms:
            try:
                await commands.has_any_role("faq", "FAQ").predicate(ctx)
                return True
            except commands.MissingAnyRole:
                if ctx.author.id == ctx.bot.owner_id:
                    return True
                raise missing_perms

    return commands.check(predicate)


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


class FAQ(Tag):
    @classmethod
    def from_record(cls, record):
        self = super().from_record(record)

        self.embed = record["embed"]
        self.embed_title = record["embed_title"]
        self.embed_description = record["embed_description"]
        # self.embed_fields = record["embed_fields"]
        self.embed_thumbnail = record["embed_thumbnail"]
        self.embed_image = record["embed_image"]

        return self

    @classmethod
    def from_partial(cls, partial):
        self = super().from_partial(partial)

        self.embed = partial["embed"]
        self.embed_title = partial["embed_title"]
        self.embed_description = partial["embed_description"]
        # self.embed_fields = partial["embed_fields"]
        self.embed_thumbnail = partial["embed_thumbnail"]
        self.embed_image = partial["embed_image"]

        return self


class TagConverter(commands.Converter):
    def __init__(self, faq=False, owner=False):
        super().__init__()
        self.faq = faq
        if owner:
            self.owner = ", tags.owner_id"
        else:
            self.owner = ""

    async def get_faq(self, ctx, arg):
        arg = arg.lower().strip()
        query = f"""SELECT tags.name, tags.content, tags.faq, tags.embed, tags.embed_title,
                   tags.embed_description, tags.embed_thumbnail, tags.embed_image{self.owner}
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
                raise commands.BadArgument("Could not find FAQ tag. Sorry.")

            similar = "\n".join(r["name"] for r in rows)
            raise commands.BadArgument(
                f"Could not find FAQ tag. Sorry.\nSimilar tags:\n{similar}"
            )

        if not row["faq"]:
            raise commands.BadArgument(
                "This tag isn't a FAQ tag. Please use the normal tag command to display this."
            )

        partial = FAQ.from_partial(row)
        if self.owner:
            partial.owner_id = row["owner_id"]
        return partial

    async def convert(self, ctx, arg):
        if self.faq:
            return await self.get_faq(ctx, arg)

        arg = arg.lower().strip()
        query = """SELECT tags.name, tags.content, tags.faq
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
                raise commands.BadArgument("Could not find tag. Sorry.")

            similar = "\n".join(r["name"] for r in rows)
            raise commands.BadArgument(
                f"Could not find tag. Sorry.\nSimilar tags:\n{similar}"
            )

        if row["faq"]:
            raise commands.BadArgument(
                "This is an FAQ tag. Please use the faq command to display this."
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


class CreateEmbedMenu(menus.Menu):
    def __init__(self, embed=None):
        super().__init__(timeout=180.0)
        self.embed = embed or discord.Embed(color=discord.Color.blurple())
        self.finished = False

    async def send_initial_message(self, ctx, channel):
        em = discord.Embed(
            title=":regional_indicator_t: Set Title",
            description=":regional_indicator_d: Set Description",
            color=discord.Color.blurple(),
        )
        # em.add_field(
        #     name=":regional_indicator_f: Add Field",
        #     value=(
        #         "Field values are set when adding a field. "
        #         "You can have up to six fields."
        #     ),
        # )

        em.set_image(
            url="https://raw.githubusercontent.com/Fyssion/Clam/main/assets/embed-image.png"
        )
        em.set_thumbnail(
            url="https://raw.githubusercontent.com/Fyssion/Clam/main/assets/embed-thumbnail.png"
        )

        return await ctx.send(
            (
                "Read and choose the options below to create an embed.\n"
                "View your embed with :regional_indicator_v:\n"
                "**When you are finished, react with :ok:**"
            ),
            embed=em,
        )

    async def create_embed(self, ctx):
        await self.start(ctx, wait=True)
        await self.message.delete()

        if not self.finished:
            await self.ctx.send(f"{ctx.tick(False)} You timed out. Aborting.")
            return None

        return self.embed

    async def prompt(self, message, *, timeout=180.0, check=None, bool_response=False):
        ctx = self.ctx

        def default_check(ms):
            return ms.author == ctx.author and ms.channel == ctx.channel

        check = check or default_check

        bot_message = await ctx.send(message)

        response = await ctx.bot.wait_for("message", timeout=timeout, check=check)

        if bool_response:
            sw = response.content.lower().startswith
            if sw("y"):
                return True
            elif sw("n"):
                return False
            else:
                raise commands.BadArgument(
                    f"{ctx.tick(False)} You must input y or n. Aborting."
                )

        await bot_message.delete()
        await response.delete()

        return response.content

    @menus.button("\N{REGIONAL INDICATOR SYMBOL LETTER T}")
    async def set_title(self, payload):
        title = await self.prompt("What would you like to set the title to?")

        if len(title) > 256:
            return await self.ctx.send(
                "Title must be no longer than 256 characters.", delete_after=5.0
            )

        self.embed.title = title

    @menus.button("\N{REGIONAL INDICATOR SYMBOL LETTER D}")
    async def set_description(self, payload):
        description = await self.prompt(
            "What would you like to set the description to?"
        )

        if len(description) > 2048:
            return await self.ctx.send(
                f"{self.ctx.tick(False)} Description must be no longer than 2048 characters.",
                delete_after=5.0,
            )

        self.embed.description = description

    # @menus.button("\N{REGIONAL INDICATOR SYMBOL LETTER F}")
    # async def add_field(self, payload):
    #     if len(self.embed.fields) == 6:
    #         return await self.ctx.send("You can't add more than six fields.")

    #     name = await self.prompt("What would you like the field's name to be?")

    #     if len(name) > 256:
    #         return await self.ctx.send(
    #             "Field name must be no longer than 256 characters.", delete_after=5.0
    #         )

    #     value = await self.prompt("What would you like the field's value to be?")

    #     if len(name) > 1048:
    #         return await self.ctx.send(
    #             "Field value must be no longer than 1048 characters.", delete_after=5.0
    #         )

    #     self.embed.add_field(name=name, value=value)

    @menus.button("\N{REGIONAL INDICATOR SYMBOL LETTER B}")
    async def set_thumbnail(self, payload):
        thumbnail = await self.prompt(
            "What would you like to set the thumbnail to?\n"
            "**Note: thumbnail must be a direct URL to an image.**"
        )

        url = r"https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)"

        if not re.match(url, thumbnail):
            return await self.ctx.send(
                f"{self.ctx.tick(False)} That is not a vaild URL.", delete_after=5.0
            )

        self.embed.set_thumbnail(url=thumbnail)

    @menus.button("\N{REGIONAL INDICATOR SYMBOL LETTER I}")
    async def set_image(self, payload):
        image = await self.prompt(
            "What would you like to set the image to?\n"
            "**Note: image must be a direct URL to an image.**"
        )

        url = r"https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)"

        if not re.match(url, image):
            return await self.ctx.send(
                f"{self.ctx.tick(False)} That is not a vaild URL.", delete_after=5.0
            )

        self.embed.set_image(url=image)

    async def display_embed(self):
        message = await self.ctx.send(embed=self.embed)
        await message.add_reaction("\N{CROSS MARK}")

        def check(reaction, user):
            return (
                reaction.message.id == message.id
                and user == self.ctx.author
                and str(reaction.emoji) == "\N{CROSS MARK}"
            )

        try:
            await self.bot.wait_for("reaction_add", check=check, timeout=120.0)
            await message.delete()
        except asyncio.TimeoutError:
            await message.delete()

    @menus.button("\N{REGIONAL INDICATOR SYMBOL LETTER V}")
    async def view_embed(self, payload):
        self.ctx.bot.loop.create_task(self.display_embed())

    @menus.button("\N{SQUARED OK}")
    async def finish_embed(self, payload):
        self.finished = True
        self.stop()


class TagPageSource(menus.ListPageSource):
    def __init__(self, entries, title="All Tags"):
        super().__init__(entries, per_page=10)
        self.title = title

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        em = discord.Embed(
            title=self.title,
            description=f"Total tags: **{len(self.entries)}**\n\nTags:\n",
            color=colors.PRIMARY,
        )
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")

        tags = []
        for i, (tag_id, name, uses, faq) in enumerate(entries, start=offset):
            tag_line = f"`{i+1}.` **{name}** - {uses} uses `(ID: {tag_id})`"
            if faq:
                tag_line += " [FAQ]"
            tags.append(tag_line)

        em.description += "\n".join(tags)

        return em


class Tags(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":bookmark:"
        self.display_over_commands = True
        self.log = self.bot.log

        # guild_id: List[tag_name]
        self._in_progress_tags = {}

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))
            ctx.handled = True

    @commands.group(usage="[tag]", invoke_without_command=True)
    async def tag(self, ctx, *, name=None):
        """Tag stuff and retrieve it later

        You can create, edit, delete, and alias
        tags. Note that server moderators can delete
        any tags.
        """
        if not name:
            return await ctx.send_help(ctx.command)

        tag = await TagConverter().convert(ctx, name)

        ref = ctx.message.reference
        reference = None
        if ref and isinstance(ref.resolved, discord.Message):
            reference = ref.resolved.to_reference()

        await ctx.send(tag.content, reference=reference)

        query = "UPDATE tags SET uses = uses + 1 WHERE name=$1 AND guild_id=$2;"
        await ctx.db.execute(query, tag.name, ctx.guild.id)

    async def create_tag(self, ctx, name, content):
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
                await ctx.send(
                    f"{ctx.tick(False)} There is already a tag with this name."
                )
            except:
                await tr.rollback()
                await ctx.send(f"{ctx.tick(False)} Could not create tag. Sorry.")
            else:
                await tr.commit()
                await ctx.send(
                    f"{ctx.tick(True)} Successfully created tag **`{name}`**."
                )

    @tag.command(
        name="create", description="Create a new tag", aliases=["new"],
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
                await ctx.send(
                    f"{ctx.tick(False)} There is already a tag with this name."
                )
            except:
                await tr.rollback()
                await ctx.send(f"{ctx.tick(False)} Could not create tag. Sorry.")
            else:
                await tr.commit()
                await ctx.send(
                    f"{ctx.tick(True)} Successfully created tag **`{name}`**."
                )

    @tag.command(name="make", description="Make a tag with an interactive session")
    async def tag_make(self, ctx):
        await ctx.send("What would you like to name your tag?")

        def check(ms):
            return ms.author == ctx.author and ms.channel == ctx.channel

        try:
            message = await self.bot.wait_for(
                "message", check=check, timeout=180.0
            )  # 3 minutes
        except asyncio.TimeoutError:
            return await ctx.send(f"{ctx.tick(False)} You timed out. Aborting.")

        name = await TagNameConverter().convert(ctx, message.content)

        if ctx.guild.id not in self._in_progress_tags.keys():
            self._in_progress_tags[ctx.guild.id] = []

        if name in self._in_progress_tags[ctx.guild.id]:
            return await ctx.send("A tag with that name is being made right now.")

        query = """SELECT id
                   FROM tag_aliases
                   WHERE name=$1 AND guild_id=$2;
                """

        result = await ctx.db.fetchrow(query, name, ctx.guild.id)

        if result:
            return await ctx.send(f"{ctx.tick(False)} That tag name is already taken.")

        self._in_progress_tags[ctx.guild.id].append(name)

        await ctx.send(
            f"What would you like the content of your tag to be? (Type `{ctx.prefix}abort` to abort)"
        )

        try:
            message = await self.bot.wait_for(
                "message", check=check, timeout=180.0  # 3 minutes
            )
        except asyncio.TimeoutError:
            return await ctx.send(f"{ctx.tick(False)} You timed out. Aborting.")

        if message.content == f"{ctx.prefix}abort":
            await ctx.send("Aborting tag creation.")
            ipt = self._in_progress_tags[ctx.guild.id]
            ipt.pop(ipt.index(name))
            return

        content = await TagContentConverter().convert(ctx, message.content)

        if not content and not message.attachments:
            raise commands.BadArgument("You must specify text or an image to put in the tag.")

        if message.attachments:
            url = message.attachments[0].url
            if not content:
                content = url
            else:
                content += f"\n{url}"

        await self.create_tag(ctx, name, content)

        ipt = self._in_progress_tags[ctx.guild.id]
        ipt.pop(ipt.index(name))

    @tag.command(
        name="delete",
        description="Delete a tag you own (unless you are a mod)",
        aliases=["remove"],
    )
    async def tag_delete(self, ctx, *, name):
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
                f"{ctx.tick(False)} Could not delete tag. "
                "Either it does not exist or you do not have permissions to do so."
            )
            return

        args.append(deleted[0])
        query = f"DELETE FROM tags WHERE id=${len(args)} AND {clause};"
        status = await ctx.db.execute(query, *args)

        # the status returns DELETE <count>, similar to UPDATE above
        if status[-1] == "0":
            # this is based on the previous delete above
            await ctx.send(f"{ctx.tick(True)} Tag alias successfully deleted.")
        else:
            await ctx.send(
                f"{ctx.tick(True)} Tag and corresponding aliases successfully deleted."
            )

    @tag.command(
        name="edit", description="Edit a tag you own", aliases=["update"],
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
                f"{ctx.tick(False)} Tag edit failed. "
                "Either the tag doesn't exist or you don't own it."
            )

        await ctx.send(f"{ctx.tick(True)} Successfully edited tag.")

    @tag.command(
        name="alias",
        description="Set an alias for a tag",
        usage="<original name> <alias name>",
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
            await ctx.send(f"{ctx.tick(False)} A tag with this name already exists.")
        else:
            # The status returns INSERT N M, where M is the number of rows inserted.
            if status[-1] == "0":
                await ctx.send(f"{ctx.tick(False)} Tag **`{original}`** doesn't exist.")
            else:
                await ctx.send(
                    f"{ctx.tick(True)} An aliases for **`{original}`** called **`{alias}`** has been created."
                )

    @tag.command(
        name="transfer", description="Transfer a tag to another member",
    )
    async def tag_transfer(self, ctx, tag, *, member: discord.Member):
        confirm = await ctx.confirm(
            f"Are you sure you want to transfer tag `{tag}` to `{member}`?"
        )

        if not confirm:
            return await ctx.send("Aborted tag transfer.")

        bypass_owner_check = (
            ctx.author.id == self.bot.owner_id
            or ctx.author.guild_permissions.manage_messages
        )
        clause = "LOWER(name)=$2 AND guild_id=$3"

        if bypass_owner_check:
            args = [member.id, tag, ctx.guild.id]
        else:
            args = [member.id, tag, ctx.guild.id, ctx.author.id]
            clause = f"{clause} AND owner_id=$4"

        query = f"""UPDATE tags
                    SET owner_id=$1
                    WHERE {clause}
                    RETURNING id;
                """

        transferred = await ctx.db.fetchrow(query, *args)

        if not transferred:
            raise commands.BadArgument(
                "Could not complete the transfer. "
                "\nEither:\n- that tag doesn't exist\n- you do not own that tag or have manage messages."
            )

        await ctx.send(ctx.tick(True, f"Transfered tag `{tag}` to `{member}`."))

        em = discord.Embed(
            title=f"Tag `{tag}` transferred to you.", color=discord.Color.green()
        )
        em.description = f"Tag `{tag}` was just transferred to you.\nThis message was sent to notify you of this action."

        em.add_field(name="Server", value=str(ctx.guild))
        em.add_field(name="Transferred by", value=str(ctx.author))

        try:
            await member.send("Tag transfer notification", embed=em)

        except discord.Forbidden:
            pass

    @tag.command(name="claim")
    async def tag_claim(self, ctx, *, tag):
        """Claim a tag

        To claim a tag, **one** of the following requirements must be met:
        - the original tag owner is no longer in the server
        - you have manage messages
        """
        query = "SELECT id, owner_id FROM tags WHERE guild_id=$1;"
        record = await ctx.db.fetchrow(query, ctx.guild.id)

        if not record:
            raise commands.BadArgument("Tag not found.")

        print(record)
        tag_id, owner_id = record

        bypass_owner_check = (
            ctx.author.id == self.bot.owner_id
            or ctx.author.guild_permissions.manage_messages
        )

        query = """UPDATE tags SET owner_id=$1 WHERE LOWER(name)=$2 AND guild_id=$3 RETURNING id;"""
        args = [ctx.author.id, tag, ctx.guild.id]

        member = ctx.guild.get_member(owner_id)
        if member and not bypass_owner_check:
            raise commands.BadArgument("Tag owner is in the server.")

        transferred = await ctx.db.fetchrow(query, *args)

        if not transferred:
            raise commands.BadArgument(
                (
                    "Tag claim failed.\n"
                    "To claim a tag, **one** of the following requirements must be met:\n"
                    "- the original tag owner is no longer in the server\n"
                    "- you have manage messages"
                )
            )

        await ctx.send(ctx.tick(True, f"Claimed tag `{tag}`."))

    def _owner_kwargs(self, guild, owner_id):
        if not owner_id:
            return {"name": "Unclaimed tag (no owner)"}

        member = guild.get_member(owner_id)
        if not member:
            return {"name": owner_id}
        return {"name": str(member), "icon_url": member.avatar_url}

    @tag.command(
        name="info",
        description="Get info about a tag",
        aliases=["about"],
        usage="<tag>",
    )
    async def tag_info(self, ctx, *, name):
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
            return await ctx.send(f"{ctx.tick(False)} Tag not found.")

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

            if record["faq"]:
                em.description += "\nThis is an FAQ tag."

        await ctx.send(embed=em)

    @tag.command(
        name="raw", description="Get a tag without markdown (for copy/pasting)",
    )
    async def tag_raw(self, ctx, *, tag: TagConverter):
        await ctx.send(discord.utils.escape_markdown(tag.content))

        query = "UPDATE tags SET uses = uses + 1 WHERE name=$1 AND guild_id=$2;"
        await ctx.db.execute(query, tag.name, ctx.guild.id)

    @tag.command(
        name="all", description="List all tags for this server", aliases=["list"]
    )
    async def tag_all(self, ctx):
        query = """SELECT id, name, uses, faq
                   FROM tags
                   WHERE guild_id=$1;
                """

        results = await ctx.db.fetch(query, ctx.guild.id)

        if not results:
            return await ctx.send("This server has no tags.")

        pages = MenuPages(source=TagPageSource(results), clear_reactions_after=True,)
        await pages.start(ctx)

    @commands.command(
        description="[Alias for `tag all`] List all tags for this server",
    )
    async def tags(self, ctx):
        await ctx.invoke(self.tag_all)

    @tag.command(
        name="top",
        description="List top tags by number of uses for this server",
        aliases=["ranks"],
    )
    async def tag_top(self, ctx):
        query = """SELECT id, name, uses, faq
                   FROM tags
                   WHERE guild_id=$1
                   ORDER BY uses DESC
                   LIMIT 10;
                """

        results = await ctx.db.fetch(query, ctx.guild.id)

        if not results:
            return await ctx.send("This server has no tags.")

        em = discord.Embed(title="Top Tags", color=colors.PRIMARY)

        tags = []
        for i, (tag_id, name, uses, faq) in enumerate(results):
            tag_line = f"`{i+1}.` **{name}** - {uses} uses `(ID: {tag_id})`"
            if faq:
                tag_line += " [FAQ]"
            tags.append(tag_line)

        desc = "\n".join(tags)

        if len(results) == 10:
            desc += "\nOnly showing top ten tags."

        em.description = desc

        await ctx.send(embed=em)

    @tag.command(
        name="member", description="Get top ten tags for a member", aliases=["user"],
    )
    async def tag_member(self, ctx, *, member: discord.Member):
        query = """SELECT id, name, uses, faq
                   FROM tags
                   WHERE guild_id=$1 AND owner_id=$2
                   ORDER BY uses DESC
                   LIMIT 10;
                """

        results = await ctx.db.fetch(query, ctx.guild.id, member.id)

        if not results:
            return await ctx.send("This member has no tags.")

        em = discord.Embed(
            title=f"Top Tags For {member.display_name}", color=colors.PRIMARY
        )

        em.set_author(name=str(member), icon_url=member.avatar_url)

        tags = []
        for i, (tag_id, name, uses, faq) in enumerate(results):
            tag_line = f"`{i+1}.` **{name}** - {uses} uses `(ID: {tag_id})`"
            if faq:
                tag_line += " [FAQ]"
            tags.append(tag_line)

        desc = "\n".join(tags)

        if len(results) == 10:
            desc += "\n Only showing top ten tags."

        em.description = desc

        await ctx.send(embed=em)

    @tag.command(
        name="search", description="Search for a tag", usage="<tag>", aliases=["find"],
    )
    async def tag_search(self, ctx, name):
        query = """SELECT     tag_aliases.name
                   FROM       tag_aliases
                   WHERE      tag_aliases.guild_id=$1 AND tag_aliases.name % $2
                   ORDER BY   similarity(tag_aliases.name, $2) DESC
                   LIMIT 10;
                """

        results = await ctx.db.fetch(query, ctx.guild.id, name)

        if not results:
            return await ctx.send("I couldn't find any similar tags. Sorry.")

        em = discord.Embed(
            title=f"Results for '{name}'",
            description="\n".join(r["name"] for r in results),
            color=colors.PRIMARY,
        )

        if len(results) == 10:
            em.description += "\nOnly showing first ten results."

        await ctx.send(embed=em)

    def generate_faq_embed(self, tag):
        em = discord.Embed(
            title=tag.embed_title or "",
            description=tag.embed_description or "",
            color=discord.Color.blurple(),
        )
        if tag.embed_thumbnail:
            em.set_thumbnail(url=tag.embed_thumbnail)

        # if tag.embed_fields:
        #     for name, value in tag.embed_fields:
        #         em.add_field(name=name, value=value)

        if tag.embed_image:
            em.set_image(url=tag.embed_image)

        return em

    @commands.group(invoke_without_command=True, usage="<FAQ tag>")
    async def faq(self, ctx, *, name=None):
        """Varient of tags for server admins

        FAQ tags are similar to regular tags, except FAQ tags
        are embeds, and they can only be created by admins
        and members with a role called "faq"

        Since tags and FAQ tags use the same system, you can
        still use most of the tag subcommands for FAQ tags.
        Exceptions to this are the subcommands below, unless
        specified otherwise.
        """
        if not name:
            return await ctx.send_help(ctx.command)

        tag = await TagConverter(faq=True).convert(ctx, name)

        if not tag.embed:
            await ctx.send(tag.content)

        else:
            await ctx.send(embed=self.generate_faq_embed(tag))

        query = "UPDATE tags SET uses = uses + 1 WHERE name=$1 AND guild_id=$2;"
        await ctx.db.execute(query, tag.name, ctx.guild.id)

    async def send_prompt(
        self, ctx, message, *, timeout=180.0, check=None, bool_response=False
    ):
        def default_check(ms):
            return ms.author == ctx.author and ms.channel == ctx.channel

        check = check or default_check

        bot_message = await ctx.send(message)

        response = self.bot.wait_for("message", timeout=timeout, check=check)

        if bool_response:
            sw = response.content.lower().startswith
            if sw("y"):
                return True
            elif sw("n"):
                return False
            else:
                raise commands.BadArgument(
                    f"{ctx.tick(False)} You must input y or n. Aborting."
                )

        await bot_message.delete()
        await response.delete()

        return response.content

    @faq.command(
        name="create", description="Create a new faq tag", aliases=["new"],
    )
    @faq_only()
    async def faq_create(self, ctx, *, name: TagNameConverter):
        # try:
        #     embed = await self.send_prompt(
        #         "Would you like this FAQ to be an embed? (y/n)", bool_response=True
        #     )
        # except asyncio.TimeoutError:
        #     return await ctx.send("You timed out. Aborting.")

        # if embed:
        #     return await self.create_faq_embed(ctx):

        if ctx.guild.id not in self._in_progress_tags.keys():
            self._in_progress_tags[ctx.guild.id] = []

        if name in self._in_progress_tags[ctx.guild.id]:
            return await ctx.send(
                f"{ctx.tick(False)} A tag with that name is being made right now."
            )

        query = """SELECT id
                   FROM tag_aliases
                   WHERE name=$1 AND guild_id=$2;
                """

        result = await ctx.db.fetchrow(query, name, ctx.guild.id)

        if result:
            raise commands.BadArgument(
                f"{ctx.tick(False)} There is already a tag with that name."
            )

        self._in_progress_tags[ctx.guild.id].append(name)

        embed = await CreateEmbedMenu().create_embed(ctx)

        if embed is None:
            ipt = self._in_progress_tags[ctx.guild.id]
            ipt.pop(ipt.index(name))
            return

        title = embed.title or None
        description = embed.description or None
        thumbnail = embed.thumbnail.url if embed.thumbnail else None
        image = embed.image.url if embed.image else None

        # fields = [[f.name, f.value] for f in embed.fields]

        query = """WITH tag_insert AS (
                        INSERT INTO tags (name, owner_id, guild_id, faq, embed, embed_title,
                        embed_description, embed_thumbnail, embed_image)
                        VALUES ($1, $2, $3, true, true, $4, $5, $6, $7)
                        RETURNING id
                    )
                    INSERT INTO tag_aliases (name, owner_id, guild_id, tag_id)
                    VALUES ($1, $2, $3, (SELECT id FROM tag_insert));
                """

        async with ctx.db.acquire() as con:
            tr = con.transaction()
            await tr.start()

            try:
                await ctx.db.execute(
                    query,
                    name,
                    ctx.author.id,
                    ctx.guild.id,
                    title,
                    description,
                    thumbnail,
                    image,
                )
            except asyncpg.UniqueViolationError:
                await tr.rollback()
                await ctx.send(
                    f"{ctx.tick(False)} There is already a tag with this name."
                )
            except:
                await tr.rollback()
                await ctx.send(f"{ctx.tick(False)} Could not create FAQ tag. Sorry.")
                raise
            else:
                await tr.commit()
                await ctx.send(
                    f"{ctx.tick(True)} Successfully created FAQ tag **`{name}`**."
                )

        ipt = self._in_progress_tags[ctx.guild.id]
        ipt.pop(ipt.index(name))

    @faq.command(name="edit", description="Edit a FAQ tag you own")
    async def faq_edit(self, ctx, *, tag: TagConverter(faq=True, owner=True)):
        if tag.owner_id != ctx.author.id:
            raise commands.BadArgument("You do not have permission to edit that tag.")

        embed = await CreateEmbedMenu(self.generate_faq_embed(tag)).create_embed(ctx)

        if not embed:
            return

        title = embed.title or None
        description = embed.description or None
        thumbnail = embed.thumbnail.url if embed.thumbnail else None
        image = embed.image.url if embed.image else None

        query = """UPDATE tags
                   SET embed_title=$1, embed_description=$2, embed_thumbnail=$3, embed_image=$4
                   WHERE name=$5 AND guild_id=$6;
                """

        await ctx.db.execute(
            query, title, description, thumbnail, image, tag.name, ctx.guild.id
        )

        await ctx.send(f"{ctx.tick(True)} Successfully edited tag.")

    @faq.command(name="delete", description="Alias for tag delete", usage="[FAQ tag]")
    async def faq_delete(self, ctx, *, name):
        await ctx.invoke(self.tag_delete, name=name)

    @faq.command(
        name="alias",
        description="Alias for tag alias",
        usage="<original name> <alias name>",
    )
    async def faq_alias(self, ctx, original: TagNameConverter, alias: TagNameConverter):
        await ctx.invoke(self.tag_alias, original=original, alias=alias)


def setup(bot):
    bot.add_cog(Tags(bot))
