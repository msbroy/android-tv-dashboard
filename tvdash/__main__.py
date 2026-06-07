import sys

from PySide6.QtWidgets import QApplication

from .app import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("TV Dashboard")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
