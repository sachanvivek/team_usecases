import uvicorn
from config_loader import load_config

if __name__ == "__main__":
    cfg = load_config()
    host = cfg.get("server", "host", fallback="0.0.0.0")
    port = cfg.getint("server", "port", fallback=5000)
    debug = cfg.getboolean("server", "debug", fallback=False)
    uvicorn.run("app:app", host=host, port=port, reload=debug)
