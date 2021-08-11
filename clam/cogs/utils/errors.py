from discord.ext import commands


class PrivateCog(commands.CommandError):
    pass


class Blacklisted(commands.CommandError):
    pass
