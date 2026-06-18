import os

import uvicorn


def main() -> None:
    host = os.getenv("DYMON_HOST", "0.0.0.0")
    port = int(os.getenv("DYMON_PORT", "8900"))
    uvicorn.run("douyin_user_monitor.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
