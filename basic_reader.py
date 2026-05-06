from src.mt5_copy.app import run_once


if __name__ == "__main__":
    events_count = run_once()
    print(f"Scan complete. Detected events: {events_count}")
