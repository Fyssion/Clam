import yaml


# NOTE: This is not a config file
# This is only a helper class for the actual
# config file


class DebugMode:
    def __init__(self, mode):
        if type(mode) is not int:
            raise TypeError("Debug mode must be an int.")

        if not 0 <= mode <= 2:
            raise ValueError("Debug mode must be between 0 and 2.")

        self.mode = mode

    def __bool__(self):
        return bool(self.mode)

    def __int__(self):
        return self.mode

    def __str__(self):
        mode_map = {0: "off", 1: "partial", 2: "full"}
        return mode_map[self.mode]

    @property
    def off(self):
        return self.mode == 0

    @property
    def partial(self):
        return self.mode == 1

    @property
    def full(self):
        return self.mode == 2


class Config:
    """config.yml helper class"""

    def __init__(self, file_path):
        self._file_path = file_path

        with open(file_path, "r") as config:
            self._data = yaml.safe_load(config)

        # Required config stuff
        self.bot_token = self._data["bot-token"]  # Bot token
        self.console = self._data["console"]  # Console channel ID
        self.google_api_key = self._data["google-api-key"]  # Google api key
        self.database_uri = self._data["database-uri"]  # Postgres database URI
        self.cleverbot_api_key = self._data["cleverbot-api-key"]  # Cleverbot API key
        self.wolfram_api_key = self._data["wolfram-api-key"]  # wolframalpha api key

        # Optional config stuff
        # Run the bot in debug mode or not
        # 0: Off | 1: Test acc | 2: Same acc
        self.debug = DebugMode(self._data.get("debug", 0))
        # Webhook for status messages
        self.status_hook = self._data.get("status-hook")
