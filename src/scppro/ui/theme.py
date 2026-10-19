from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


def apply_theme(application: QApplication, dark: bool | None = None) -> None:
    application.setStyle("Fusion")
    if dark is None:
        dark = application.styleHints().colorScheme() == Qt.ColorScheme.Dark
    colors = (
        dict(background="#161c27", panel="#1e2634", sidebar="#19212e", text="#edf2fa", muted="#a8b4c7", border="#303c50", alternate="#222c3d", accent="#78a9ff", selection="#294675", input="#252f40", primary="#346bd1")
        if dark else
        dict(background="#f3f5f9", panel="#ffffff", sidebar="#edf1f7", text="#202c40", muted="#738198", border="#e1e7ef", alternate="#f9fbfd", accent="#356ed3", selection="#e5eeff", input="#ffffff", primary="#356ed3")
    )
    c = colors
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, c["background"]),
        (QPalette.ColorRole.WindowText, c["text"]),
        (QPalette.ColorRole.Base, c["panel"]),
        (QPalette.ColorRole.AlternateBase, c["alternate"]),
        (QPalette.ColorRole.Text, c["text"]),
        (QPalette.ColorRole.Button, c["panel"]),
        (QPalette.ColorRole.ButtonText, c["text"]),
        (QPalette.ColorRole.Highlight, c["selection"]),
        (QPalette.ColorRole.HighlightedText, c["text"]),
        (QPalette.ColorRole.ToolTipBase, c["panel"]),
        (QPalette.ColorRole.ToolTipText, c["text"]),
        (QPalette.ColorRole.Link, c["accent"]),
        (QPalette.ColorRole.PlaceholderText, c["muted"]),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["muted"]))
    application.setPalette(palette)
    application.setFont(QFont("Helvetica Neue", 12))
    application.setStyleSheet(f"""
        QWidget {{ color: {c['text']}; font-size: 12px; }}
        QMainWindow, QDialog {{ background: {c['background']}; }}
        QWidget#sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
        QFrame#filePane, QFrame#queuePane {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 10px; }}
        QLabel#brand {{ font-size: 23px; font-weight: 700; color: {c['accent']}; }}
        QLabel#mainTitle {{ font-size: 23px; font-weight: 600; }}
        QLabel#paneTitle {{ font-size: 14px; font-weight: 600; }}
        QLabel#dialogTitle {{ font-size: 17px; font-weight: 600; }}
        QLabel#muted {{ color: {c['muted']}; }}
        QLabel#badge {{ color: {c['accent']}; background: {c['selection']}; border-radius: 5px; padding: 3px 7px; font-size: 10px; }}
        QLabel#connectionState {{ color: {c['muted']}; padding: 4px 8px; }}
        QPushButton {{ border: 1px solid {c['border']}; border-radius: 6px; background: {c['panel']}; padding: 6px 10px; min-height: 17px; }}
        QPushButton:hover {{ border-color: {c['accent']}; background: {c['selection']}; }}
        QPushButton:pressed {{ background: {c['selection']}; }}
        QPushButton:disabled {{ color: {c['muted']}; border-color: {c['border']}; background: {c['background']}; }}
        QPushButton#primary {{ color: #ffffff; background: {c['primary']}; border-color: {c['primary']}; font-weight: 600; }}
        QPushButton#primary:hover {{ background: #285bbb; }}
        QPushButton#primary:disabled {{ color: {c['muted']}; background: {c['background']}; border-color: {c['border']}; }}
        QLineEdit, QSpinBox, QComboBox {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 6px; min-height: 17px; selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
        QTableWidget {{ background: {c['panel']}; alternate-background-color: {c['alternate']}; border: none; selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
        QTableWidget::item {{ border-bottom: 1px solid {c['border']}; padding: 4px; }}
        QTableWidget::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
        QHeaderView::section {{ background: {c['panel']}; color: {c['muted']}; border: none; border-bottom: 1px solid {c['border']}; padding: 8px 6px; font-size: 11px; font-weight: 500; }}
        QListWidget {{ background: transparent; border: none; outline: none; }}
        QListWidget::item {{ padding: 9px 6px; margin: 3px 0; border-radius: 6px; }}
        QListWidget::item:selected {{ background: {c['selection']}; color: {c['accent']}; }}
        QListWidget::item:hover {{ background: {c['selection']}; }}
        QProgressBar {{ background: {c['background']}; border: 1px solid {c['border']}; border-radius: 4px; text-align: center; font-size: 10px; }}
        QProgressBar::chunk {{ background: {c['selection']}; border-radius: 3px; }}
        QStatusBar {{ background: {c['background']}; color: {c['muted']}; border-top: 1px solid {c['border']}; }}
        QSplitter::handle {{ background: transparent; }}
        QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 5px; }}
    """)
