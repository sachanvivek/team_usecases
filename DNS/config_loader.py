import configparser
import os

_config = None

def load_config(path: str = None) -> configparser.ConfigParser:
    global _config
    if _config is not None:
        return _config
    _config = configparser.ConfigParser()
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "Config.ini")
    _config.read(path)
    return _config

def get_config() -> configparser.ConfigParser:
    if _config is None:
        return load_config()
    return _config
