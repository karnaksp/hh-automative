"""Data utils"""

import os


def read_text_file(relative_path, file_name, encoding="utf-8"):
    file_path = os.path.join(relative_path, file_name)
    with open(file_path, "r", encoding=encoding) as f:
        return f.read()
