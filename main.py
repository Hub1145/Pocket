import os
import sys

# Ensure the app can find the bot module if needed
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    print("Starting Trusted Spots Dashboard...")
    print("Use Docker or run 'python app.py' to start the system.")
    os.system("python app.py")
