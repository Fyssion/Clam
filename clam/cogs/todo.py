import asyncpg
import discord
from discord.ext import commands, menus


from clam.utils import colors, db, humantime
from clam.utils.menus import MenuPages


INCOMPLETE_EMOJI = "\N{BLACK LARGE SQUARE}"
COMPLETE_EMOJI = "\N{BALLOT BOX WITH CHECK}"


class TodoTaskSource(menus.ListPageSource):
    def __init__(self, data, ctx, list_type):
        super().__init__(data, per_page=10)
        self.ctx = ctx
        self.list_type = list_type

    def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        all_todos = []
        if self.list_type == "all":
            for i, (todo_id, name, created_at, completed_at) in enumerate(entries, start=offset):
                if completed_at:
                    created = human_friendly = humantime.timedelta(created_at, brief=True, accuracy=1)
                    completed = humantime.timedelta(completed_at, brief=True, accuracy=1)
                    all_todos.append(
                        f"{COMPLETE_EMOJI} ~~{name}~~ - {created} ({completed}) `(ID: {todo_id})`"
                    )
                else:
                    human_friendly = humantime.timedelta(created_at, brief=True, accuracy=1)
                    all_todos.append(f"{INCOMPLETE_EMOJI} {name} - {human_friendly} `(ID: {todo_id})`")
        else:
            for i, (todo_id, name, created_at) in enumerate(entries, start=offset):
                human_friendly = humantime.timedelta(created_at, brief=True, accuracy=1)
                all_todos.append(f"{INCOMPLETE_EMOJI} {name} - {human_friendly} `(ID: {todo_id})`")

        # "created/completed at" or "created at"
        friendly = "created at" + " (completed at)" if self.list_type == "all" else ""

        description = (
            f"Total tasks: **{len(self.entries)}**\nKey: name - {friendly} `(ID: id)`\n\n"
            + "\n".join(all_todos)
        )

        em = discord.Embed(
            title="Your Todo List", description=description, color=colors.PRIMARY,
        )
        em.set_author(name=str(self.ctx.author), icon_url=self.ctx.author.display_avatar.url)
        em.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")

        return em


class Todos(db.Table):
    id = db.PrimaryKeyColumn()
    name = db.Column(db.String(length=512), index=True)
    author_id = db.Column(db.Integer(big=True), index=True)
    created_at = db.Column(
        db.Datetime(), default="now() at time zone 'utc'", index=True
    )
    completed_at = db.Column(db.Datetime(), index=True)

    @classmethod
    def create_table(cls, *, exists_ok=True):
        statement = super().create_table(exists_ok=exists_ok)
        sql = "CREATE UNIQUE INDEX IF NOT EXISTS todos_uniq_idx ON todos (LOWER(name), author_id);"
        return statement + "\n" + sql


class TodoTaskConverter(commands.Converter):
    async def convert(self, ctx, argument):
        try:
            argument = int(argument)
            query = """SELECT id, name, created_at, completed_at
                       FROM todos
                       WHERE id=$1 AND author_id=$2;
                    """
        except ValueError:
            query = """SELECT id, name, created_at, completed_at
                       FROM todos
                       WHERE name=$1 AND author_id=$2;
                    """

        result = await ctx.db.fetchrow(query, argument, ctx.author.id)

        if not result:
            raise commands.BadArgument("Task was not found.")

        return result


class Todo(commands.Cog):
    """Commands for managing your todo list."""

    def __init__(self, bot):
        self.bot = bot
        self.log = bot.log
        self.emoji = f"{COMPLETE_EMOJI}"

    @commands.group(invoke_without_command=True)
    async def todo(self, ctx):
        """Shows your todo list.

        This command houses a series of subcommands used for
        managing a todo list. Each item on your todo list
        is referred to as a task.

        You can create tasks, view your todo list, check
        off tasks, and more. View the subcommands below for
        more info.
        """
        await ctx.invoke(self.todo_list)

    @todo.command(name="add", aliases=["create", "new"])
    async def todo_add(self, ctx, *, name):
        """Adds an task to your todo list."""

        if len(name) > 512:
            raise commands.BadArgument(
                f"That name is too long. Must be 512 characters or less ({len(name)}/512)."
            )

        query = """INSERT INTO todos (name, author_id)
                   VALUES ($1, $2);
                """

        async with ctx.db.acquire() as con:
            tr = con.transaction()
            await tr.start()

            try:
                await ctx.db.execute(query, name, ctx.author.id)
            except asyncpg.UniqueViolationError:
                await tr.rollback()
                await ctx.send(
                    f"{ctx.tick(False)} You already have a task with this name."
                )
            except:
                await tr.rollback()
                await ctx.send(f"{ctx.tick(False)} Could not create task.")
            else:
                await tr.commit()
                await ctx.send(
                    f":page_facing_up: Added **`{discord.utils.escape_mentions(name)}`** to your todo list."
                )

    @todo.command(name="done", aliases=["check", "complete"])
    async def todo_done(self, ctx, *, task):
        """Marks a task on your todo list as done."""

        try:
            task = int(task)
            sql = """UPDATE todos
                     SET completed_at=NOW() AT TIME ZONE 'UTC'
                     WHERE author_id=$1 AND id=$2;
                  """
        except ValueError:
            task = task
            sql = """UPDATE todos
                     SET completed_at=NOW() AT TIME ZONE 'UTC'
                     WHERE author_id=$1 AND name=$2;
                  """

        result = await ctx.db.execute(sql, ctx.author.id, task)
        if result.split(" ")[1] == "0":
            raise commands.BadArgument("Task was not found.")

        await ctx.send(f"{COMPLETE_EMOJI} Task marked as done")

    @todo.command(name="undone", aliases=["uncheck"])
    async def todo_undone(self, ctx, *, task):
        """Marks a task from your todo list as not done."""

        try:
            task = int(task)
            sql = """UPDATE todos
                     SET completed_at=NULL
                     WHERE author_id=$1 AND id=$2;
                  """
        except ValueError:
            task = task
            sql = """UPDATE todos
                     SET completed_at=NULL
                     WHERE author_id=$1 AND name=$2;
                  """

        result = await ctx.db.execute(sql, ctx.author.id, task)
        if result.split(" ")[1] == "0":
            raise commands.BadArgument("Task was not found.")

        await ctx.send(f"{INCOMPLETE_EMOJI} Task marked as not done")

    @todo.command(name="delete", aliases=["remove"])
    async def todo_delete(self, ctx, *, task):
        """Deletes a task from your todo list."""

        try:
            task = int(task)
            query = "DELETE FROM todos WHERE id=$1 AND author_id=$2;"
        except ValueError:
            task = task
            query = "DELETE FROM todos WHERE name=$1 AND author_id=$2;"

        result = await ctx.db.execute(query, task, ctx.author.id)
        if result.split(" ")[1] == "0":
            raise commands.BadArgument("Task was not found.")

        await ctx.send(":wastebasket: Task deleted.")

    @todo.command(name="info", aliases=["show"])
    async def todo_info(self, ctx, *, task: TodoTaskConverter):
        """Shows info about a task on your todo list."""

        todo_id, name, created_at, completed_at = task

        if completed_at:
            description = f"{COMPLETE_EMOJI} ~~{name}~~ `(ID: {todo_id})`"
            description += f"\nCreated {humantime.fulltime(created_at, accuracy=1)}."
            description += f"\nCompleted {humantime.fulltime(completed_at, accuracy=1)}."
        else:
            description = f"{INCOMPLETE_EMOJI} {name} `(ID: {todo_id})`"
            description += f"\nCreated {humantime.fulltime(created_at, accuracy=1)}."

        em = discord.Embed(
            title="Task Info",
            description=description,
            color=colors.PRIMARY,
            timestamp=created_at,
        )

        em.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
        em.set_footer(text="Task created")

        await ctx.send(embed=em)

    @todo.command(name="list")
    async def todo_list(self, ctx):
        """Shows incomplete tasks on your todo list."""

        query = """SELECT id, name, created_at
                   FROM todos
                   WHERE author_id=$1 AND completed_at IS NULL
                   ORDER BY created_at DESC
                """

        records = await ctx.db.fetch(query, ctx.author.id)

        if not records:
            return await ctx.send("You have nothing on your todo list.")

        pages = MenuPages(TodoTaskSource(records, ctx, "list"), ctx=ctx)
        await pages.start()

    @todo.command(name="all")
    async def todo_all(self, ctx):
        """Shows all tasks on your todo list, regardless of completion."""

        query = """SELECT id, name, created_at, completed_at
                   FROM todos
                   WHERE author_id=$1
                   ORDER BY created_at DESC
                """

        records = await ctx.db.fetch(query, ctx.author.id)

        if not records:
            return await ctx.send("You have no tasks on your todo list.")

        pages = MenuPages(TodoTaskSource(records, ctx, "all"), ctx=ctx)
        await pages.start()


def setup(bot):
    bot.add_cog(Todo(bot))
