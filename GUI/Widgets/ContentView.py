from PySide6.QtWidgets import QSplitter, QLabel, QVBoxLayout, QWidget, QSizePolicy
from PySide6.QtCore import Qt

from GUI.Widgets.SubtitleView import SubtitleView

class ContentView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.subtitle_view = SubtitleView(self)
        self.translation_view = SubtitleView(self)

        layout = QVBoxLayout()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.subtitle_view)
        splitter.addWidget(self.translation_view)
        splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(splitter)

        metadata_context = QLabel("Metadata & Context Information")
        metadata_context.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout.addWidget(metadata_context)

        self.setLayout(layout)

    def show_subtitles(self, subtitles):
        self.subtitle_view.show_subtitles(subtitles)
    
    def show_translations(self, translations):
        self.translation_view.show_subtitles(translations)

    def clear(self):
        self.subtitle_view.show_subtitles([])
        self.translation_view.show_subtitles([])