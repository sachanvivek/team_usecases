"""
Configuration manager - reads settings from config.ini
"""
import configparser
import os


class Config:
    _instance = None
    _config = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._config = configparser.ConfigParser()
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'config.ini'
            )
            cls._config.read(config_path)
        return cls._instance

    def get(self, section, key, fallback=None):
        return self._config.get(section, key, fallback=fallback)

    def getint(self, section, key, fallback=None):
        return self._config.getint(section, key, fallback=fallback)

    def getfloat(self, section, key, fallback=None):
        return self._config.getfloat(section, key, fallback=fallback)

    def getboolean(self, section, key, fallback=None):
        return self._config.getboolean(section, key, fallback=fallback)

    def getlist(self, section, key, fallback=None):
        value = self._config.get(section, key, fallback=fallback)
        if value:
            return [item.strip() for item in value.split(',')]
        return fallback or []


config = Config()
