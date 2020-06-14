import yaml


# NOTE: This is not a config file
# This is only a helper class for the actual
# config file


class Config:
    """config.yml helper class"""

    def __init__(self, file_path):
        self._file_path = file_path

        with open(file_path, "r") as config:
            self._data = yaml.safe_load(config)

        # Required config stuff
        self.bot_token = self._data["bot-token"]  # Bot token
        self.console = self._data["console"]  # Console channel ID
        self.reddit_id = self._data["reddit-id"]  # Reddit app ID
        self.reddit_secret = self._data["reddit-secret"]  # Reddit app secret
        self.database_uri = self._data["database-uri"]  # Postgres database URI

        # Optional config stuff
        # Run the bot in debug mode or not
        self.debug = self._data if "debug" in self._data.keys() else False
