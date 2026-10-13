"""
BackTester - Native Desktop Window Wrapper (PySide6 / PyQt6 / PyWebView)
"""
import os
import sys
import threading
import time
from app import app

PORT = 5002
HOST = "127.0.0.1"

def start_backend():
    try:
        from waitress import serve
        serve(app, host=HOST, port=PORT, threads=6)
    except ImportError:
        app.run(host=HOST, port=PORT, debug=False, threaded=True)

def main():
    backend_thread = threading.Thread(target=start_backend, daemon=True)
    backend_thread.start()
    time.sleep(1.0)
    url = f"http://{HOST}:{PORT}"

    # Try PySide6 / PyQt6 WebEngine
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtCore import QUrl

        qt_app = QApplication(sys.argv)
        qt_app.setApplicationName("BackTester - Quantitative Backtester")
        
        view = QWebEngineView()
        view.setWindowTitle("BackTester - Python Edition")
        view.resize(1500, 950)
        view.load(QUrl(url))
        view.show()
        sys.exit(qt_app.exec())
    except Exception:
        pass

    # Try pywebview
    try:
        import webview
        webview.create_window("BackTester - Quantitative Backtester", url, width=1500, height=950)
        webview.start()
        return
    except Exception:
        pass

    # Fallback to browser
    import webbrowser
    print(f"Launching web view at {url}")
    webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == "__main__":
    main()
