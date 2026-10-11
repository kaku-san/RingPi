import argparse
import uvicorn
from .app import create_app


def main():
    parser = argparse.ArgumentParser(description="Local-first Agent Doorbell")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8877, type=int)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
