from dotenv import load_dotenv
load_dotenv()
"""
BackTester - Python Edition
Main Entry Point
"""
import os
import sys
import threading
import time
import webbrowser
from app import app

PORT = int(os.environ.get("PORT", 5000))
HOST = os.environ.get("HOST", "127.0.0.1")

def open_browser():
    time.sleep(1.2)
    url = f"http://{HOST}:{PORT}"
    print(f"[*] Opening browser to {url}...")
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"[!] Could not open browser automatically: {e}")

def run_server():
    print("=" * 60)
    print("  BackTester - Python Desktop & Web Quantitative Trading Suite")
    print(f"  Running on: http://{HOST}:{PORT}")
    print("=" * 60)
    
    # Try running with waitress if installed for production grade threading
    try:
        from waitress import serve
        serve(app, host=HOST, port=PORT, threads=8)
    except ImportError:
        app.run(host=HOST, port=PORT, debug=False, threaded=True)

if __name__ == "__main__":
    # Launch browser thread
    threading.Thread(target=open_browser, daemon=True).start()
    run_server()
