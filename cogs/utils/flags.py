from discord.ext import commands, flags


class NoUsageFlagCommand(flags.FlagCommand):
    @property
    def signature(self):
        return commands.Command.signature.fget(self)


class NoUsageFlagGroup(flags.FlagGroup, NoUsageFlagCommand):
    pass
